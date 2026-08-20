from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flowerp import EcommerceDemo, ERPService, ERPStore
from flowerp.models import ApprovalRequired, InsufficientStock, InvalidTransition, OrderLine
from workbench.server import App


class ERPServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.service = ERPService(ERPStore(Path(self.tmp.name) / "erp.db"))
        self.service.add_product("A", "商品 A", 2500, 2)
        self.service.receive_stock("A", 5, "opening")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reserve_and_ship(self) -> None:
        order = self.service.create_order("C", [OrderLine("A", 3, 2500)], "SO-1")
        self.assertEqual(7500, order["total_cents"])
        self.assertEqual("reserved", self.service.reserve_order("SO-1")["status"])
        self.assertEqual("shipped", self.service.ship_order("SO-1")["status"])
        self.assertEqual(2, self.service.product("A")["on_hand"])

    def test_failed_reservation_is_atomic(self) -> None:
        self.service.create_order("C", [OrderLine("A", 6, 2500)], "SO-2")
        with self.assertRaises(InsufficientStock): self.service.reserve_order("SO-2")
        self.assertEqual(0, self.service.product("A")["reserved"])

    def test_cancel_releases_stock(self) -> None:
        self.service.create_order("C", [OrderLine("A", 4, 2500)], "SO-3")
        self.service.reserve_order("SO-3"); self.service.cancel_order("SO-3")
        self.assertEqual(5, self.service.product("A")["available"])

    def test_receipt_is_idempotent(self) -> None:
        self.service.receive_stock("A", 2, "receipt-1"); replay = self.service.receive_stock("A", 2, "receipt-1")
        self.assertTrue(replay["idempotent_replay"]); self.assertEqual(7, replay["on_hand"])

    def test_purchase_requires_approval(self) -> None:
        request = self.service.propose_purchase("A", 3, "缺货", "PR-1")
        with self.assertRaises(ApprovalRequired): self.service.receive_purchase(request["id"], "pr-1")
        self.service.approve_purchase("PR-1", "Alice"); self.service.receive_purchase("PR-1", "pr-1")
        self.assertEqual(8, self.service.product("A")["on_hand"])

    def test_illegal_shipping_is_blocked(self) -> None:
        self.service.create_order("C", [OrderLine("A", 1, 2500)], "SO-4")
        with self.assertRaises(InvalidTransition): self.service.ship_order("SO-4")

    def test_master_data_can_be_linked_to_sales_and_purchase(self) -> None:
        customer = self.service.add_customer("测试客户", "13800000000")
        supplier = self.service.add_supplier("测试供应商", "张经理", "13900000000")
        order = self.service.create_order(
            customer["name"], [OrderLine("A", 2, 2500)], "SO-LINK",
            customer_id=customer["id"], channel="online", remark="平台订单",
        )
        purchase = self.service.propose_purchase(
            "A", 3, "补充安全库存", "PR-LINK", supplier_id=supplier["id"]
        )
        self.assertEqual(customer["id"], order["customer_id"])
        self.assertEqual(supplier["id"], purchase["supplier_id"])

    def test_inventory_ledger_records_reserve_release_and_ship(self) -> None:
        self.service.create_order("C", [OrderLine("A", 2, 2500)], "SO-LEDGER")
        self.service.reserve_order("SO-LEDGER")
        self.service.cancel_order("SO-LEDGER")
        events = self.service.inventory_events()
        self.assertEqual(["release", "reserve", "receive"], [event["event_type"] for event in events])
        self.assertEqual(-2, events[0]["reserved_delta"])
        self.assertEqual(2, events[1]["reserved_delta"])

    def test_received_purchase_replay_is_idempotent(self) -> None:
        self.service.propose_purchase("A", 3, "缺货", "PR-REPLAY")
        self.service.approve_purchase("PR-REPLAY", "Alice")
        first = self.service.receive_purchase("PR-REPLAY", "receipt:PR-REPLAY")
        replay = self.service.receive_purchase("PR-REPLAY", "receipt:PR-REPLAY")
        self.assertFalse(first["stock"]["idempotent_replay"])
        self.assertTrue(replay["stock"]["idempotent_replay"])
        self.assertEqual(8, replay["stock"]["on_hand"])


class EcommerceDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.demo = EcommerceDemo(ERPService(ERPStore(Path(self.tmp.name) / "demo.db")))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_twenty_unit_order_is_blocked_before_oversell(self) -> None:
        self.demo.advance()  # create one 20-unit order against 8 available
        state = self.demo.advance()
        self.assertEqual("shortage", state["stage_key"])
        self.assertEqual(20, state["metrics"]["order_demand"])
        self.assertEqual(12, state["metrics"]["shortage"])
        self.assertEqual(8, state["metrics"]["available"])
        self.assertEqual("draft", state["orders"][0]["status"])
        self.assertIn("缺口 12", state["failure"])

    def test_demo_requires_approval_then_completes_fulfilment(self) -> None:
        self.demo.advance(); self.demo.advance()
        blocked = self.demo.advance()
        self.assertEqual("blocked", blocked["stage_key"])
        self.assertIn("未审批", blocked["failure"])
        self.assertEqual(8, blocked["metrics"]["available"])
        self.demo.advance(); self.demo.advance()
        completed = self.demo.advance()
        self.assertTrue(completed["is_complete"])
        self.assertEqual(["shipped"], [item["status"] for item in completed["orders"]])
        self.assertEqual(0, completed["metrics"]["available"])


class AppStartupTests(unittest.TestCase):
    def test_web_app_starts_with_empty_business_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = App(tmp)
            self.assertEqual([], app.erp.inventory())
            self.assertEqual([], app.erp.list_orders())
            self.assertEqual([], app.erp.list_customers())
