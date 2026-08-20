from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flowerp.channels import EcommerceChannelService
from flowerp.identity import IdentityService, SYSTEM_PRINCIPAL
from flowerp.inventory import InventoryService
from flowerp.master_data import MasterDataService
from flowerp.models import Conflict
from flowerp.sales import SalesService
from flowerp.store import ERPStore


class EcommerceChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ERPStore(Path(self.tmp.name) / "channel.db")
        IdentityService(self.store).ensure_local_defaults()
        self.principal = SYSTEM_PRINCIPAL
        self.master = MasterDataService(self.store)
        self.inventory = InventoryService(self.store)
        self.sales = SalesService(self.store)
        self.channels = EcommerceChannelService(self.store)
        self.product = self.master.create_product(self.principal, "EC-001", "电商测试商品", 12900, 7000)
        self.customer = self.master.create_customer(self.principal, "PLAT-001", "平台结算客户", credit_limit_cents=5_000_000)
        self.shop = self.channels.create_shop(
            self.principal, "mock", "TMALL-A", "天猫旗舰店", self.customer["id"], "SITE-MAIN", "shop-10001",
            sync_mode="manual",
        )
        self.listing = self.channels.map_listing(
            self.principal, self.shop["id"], "item-1", "sku-red", "测试商品 红色",
            [{"product_id": self.product["id"], "quantity": 1, "revenue_share_basis_points": 10000}],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def payload(**changes) -> dict:
        item = {
            "external_order_id": "T202608140001", "external_status": "paid",
            "order_time": "2026-08-14T09:30:00+08:00", "paid_time": "2026-08-14T09:31:00+08:00",
            "currency": "CNY", "goods_cents": 25800, "discount_cents": 1000,
            "freight_cents": 600, "total_cents": 25400, "buyer_reference": "buyer-opaque-1",
            "recipient": "张三", "phone": "13800138000", "country": "CN", "province": "浙江省",
            "city": "杭州市", "district": "余杭区", "street": "文一西路 969 号", "buyer_note": "工作日送达",
            "lines": [{"external_line_id": "1", "external_product_id": "item-1", "external_sku_id": "sku-red",
                       "title": "测试商品 红色", "quantity": 2, "unit_price_cents": 12900,
                       "discount_cents": 1000, "total_cents": 24800}],
        }
        item.update(changes)
        return item

    def test_paid_order_import_reserves_stock_and_shipment_queues_callback(self) -> None:
        self.inventory.receive(self.principal, self.product["id"], "LOC-MAIN-STOCK", 10, "ec-opening", unit_cost_cents=7000)
        run = self.channels.ingest_orders(self.principal, self.shop["id"], [self.payload()], "mock")
        self.assertEqual("completed", run["status"])
        self.assertEqual("received", run["items"][0]["status"])
        imported = self.channels.review_and_import(self.principal, run["items"][0]["id"])
        self.assertEqual("imported", imported["status"])
        sales = self.sales.order(self.principal, imported["sales_document_id"])
        self.assertEqual("reserved", sales["status"])
        self.assertEqual(25400, sales["total_cents"])
        shipment = self.sales.create_shipment(self.principal, sales["id"], carrier="SF", tracking_number="SF10001")
        self.sales.post_shipment(self.principal, shipment["id"], "ec-shipment-1")
        callbacks = self.channels.list_callbacks(self.principal, "pending")
        self.assertEqual(1, len(callbacks))
        self.assertEqual("shipment", callbacks[0]["task_type"])
        self.assertEqual("SF10001", callbacks[0]["payload"]["tracking_number"])

    def test_ingest_is_idempotent_but_changed_replay_is_blocked(self) -> None:
        first = self.channels.ingest_orders(self.principal, self.shop["id"], [self.payload()])
        replay = self.channels.ingest_orders(self.principal, self.shop["id"], [self.payload()])
        self.assertEqual(first["items"][0]["id"], replay["items"][0]["id"])
        self.assertEqual(1, replay["replay_count"])
        changed = self.payload(total_cents=25399)
        failed = self.channels.ingest_orders(self.principal, self.shop["id"], [changed])
        self.assertEqual("failed", failed["status"])
        self.assertIn("内容不一致", failed["errors"][0])

    def test_unmapped_invalid_address_and_amount_are_explicit_blockers(self) -> None:
        invalid = self.payload(
            recipient="", phone="12", province="", total_cents=1,
            lines=[{"external_line_id": "1", "external_product_id": "missing", "external_sku_id": "missing",
                    "title": "未知商品", "quantity": 1, "unit_price_cents": 100, "total_cents": 100}],
        )
        order = self.channels.ingest_orders(self.principal, self.shop["id"], [invalid])["items"][0]
        self.assertEqual("blocked", order["status"])
        self.assertTrue({"RECIPIENT_MISSING", "PHONE_INVALID", "ADDRESS_INCOMPLETE", "SKU_UNMAPPED", "AMOUNT_MISMATCH"}.issubset(set(order["blocker_codes"])))
        with self.assertRaises(Conflict): self.channels.review_and_import(self.principal, order["id"])
        self.assertIsNone(self.store.scalar("SELECT sales_document_id FROM channel_orders WHERE id=?", (order["id"],)))

    def test_inventory_shortage_keeps_visible_exception_and_sales_document(self) -> None:
        order = self.channels.ingest_orders(self.principal, self.shop["id"], [self.payload()])["items"][0]
        with self.assertRaises(Conflict): self.channels.review_and_import(self.principal, order["id"])
        current = self.channels.order(self.principal, order["id"])
        self.assertEqual("exception", current["status"])
        self.assertIn("INVENTORY_SHORTAGE", current["blocker_codes"])
        self.assertTrue(current["sales_document_id"])
        self.assertEqual("confirmed", self.sales.order(self.principal, current["sales_document_id"])["status"])

    def test_new_mapping_unblocks_previously_received_order(self) -> None:
        payload = self.payload(
            external_order_id="T202608140099", goods_cents=12900, discount_cents=0, freight_cents=0,
            total_cents=12900, lines=[{"external_line_id": "1", "external_product_id": "item-new",
            "external_sku_id": "sku-new", "title": "新上架商品", "quantity": 1,
            "unit_price_cents": 12900, "discount_cents": 0, "total_cents": 12900}],
        )
        blocked = self.channels.ingest_orders(self.principal, self.shop["id"], [payload])["items"][0]
        self.assertIn("SKU_UNMAPPED", blocked["blocker_codes"])
        self.channels.map_listing(self.principal, self.shop["id"], "item-new", "sku-new", "新上架商品",
                                  [{"product_id": self.product["id"], "quantity": 1, "revenue_share_basis_points": 10000}])
        self.inventory.receive(self.principal, self.product["id"], "LOC-MAIN-STOCK", 1, "new-map-stock", unit_cost_cents=7000)
        imported = self.channels.review_and_import(self.principal, blocked["id"])
        self.assertEqual("imported", imported["status"])
        self.assertEqual([], imported["blocker_codes"])

    def test_address_change_updates_unshipped_order_and_queues_callback(self) -> None:
        self.inventory.receive(self.principal, self.product["id"], "LOC-MAIN-STOCK", 5, "ec-address", unit_cost_cents=7000)
        order = self.channels.ingest_orders(self.principal, self.shop["id"], [self.payload()])["items"][0]
        imported = self.channels.review_and_import(self.principal, order["id"])
        changed = self.channels.change_shipping_address(
            self.principal, order["id"], "李四", "13900139000", "上海市", "上海市", "浦东新区", "世纪大道 1 号",
        )
        self.assertEqual("李四", changed["recipient"])
        sales = self.sales.order(self.principal, imported["sales_document_id"])
        self.assertIn("世纪大道 1 号", sales["shipping_address"])
        self.assertEqual("address_change", self.channels.list_callbacks(self.principal, "pending")[0]["task_type"])


if __name__ == "__main__":
    unittest.main()
