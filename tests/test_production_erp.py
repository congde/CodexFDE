from __future__ import annotations

import tempfile
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from flowerp.audit import AuditService
from flowerp.config import load_settings
from flowerp.finance import FinanceService
from flowerp.identity import IdentityService, Principal, SYSTEM_PRINCIPAL
from flowerp.inventory import InventoryService
from flowerp.master_data import MasterDataService
from flowerp.models import ApprovalRequired, Conflict, CreditLimitExceeded, InsufficientStock, InvalidTransition, PermissionDenied, ValidationError
from flowerp.operations import BackupService, HealthService, OutboxService, RuntimeCoordinator
from flowerp.pricing import PricingService
from flowerp.serials import SerialNumberService
from flowerp.import_export import ImportExportService
from flowerp.reconciliation import ReconciliationService
from flowerp.cash_management import CashManagementService
from flowerp.partners import PartnerDetailService
from flowerp.alerts import AlertService
from flowerp.accounting import LedgerService
from flowerp.purchasing import PurchasingService
from flowerp.reports import ReportService
from flowerp.sales import SalesService
from flowerp.store import ERPStore
from workbench.api_v2 import APIRouter


class ProductionFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tmp.name)
        self.store = ERPStore(self.runtime / "flowerp.db")
        self.identity = IdentityService(self.store)
        self.identity.ensure_local_defaults()
        self.master = MasterDataService(self.store)
        self.inventory = InventoryService(self.store)
        self.sales = SalesService(self.store)
        self.purchasing = PurchasingService(self.store)
        self.finance = FinanceService(self.store)
        self.cash = CashManagementService(self.store)
        self.reports = ReportService(self.store)
        self.admin = SYSTEM_PRINCIPAL
        self.buyer = Principal("buyer", "ORG-DEFAULT", "buyer", "采购员", SYSTEM_PRINCIPAL.permissions)
        self.approver = Principal("approver", "ORG-DEFAULT", "approver", "审批人", SYSTEM_PRINCIPAL.permissions)
        self.product = self.master.create_product(self.admin, "SKU-001", "标准商品", 10000, 6000, min_stock=3, max_stock=20)
        self.customer = self.master.create_customer(self.admin, "C-001", "标准客户", credit_limit_cents=1000000,
                                                    payment_terms_days=30, shipping_address="上海市")
        self.supplier = self.master.create_supplier(self.admin, "S-001", "标准供应商", payment_terms_days=30)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def receive(self, quantity: int = 20, key: str = "opening") -> dict:
        return self.inventory.receive(self.admin, self.product["id"], "LOC-MAIN-STOCK", quantity, key, unit_cost_cents=6000)

    def shipped_order(self, quantity: int = 2) -> dict:
        self.receive()
        order = self.sales.create_order(self.admin, self.customer["id"], [{"product_id": self.product["id"], "quantity": quantity}])
        self.sales.confirm(self.admin, order["id"])
        self.sales.reserve(self.admin, order["id"])
        shipment = self.sales.create_shipment(self.admin, order["id"])
        self.sales.post_shipment(self.admin, shipment["id"], "ship-once")
        return self.sales.order(self.admin, order["id"])


class SchemaAndIdentityTests(ProductionFixture):
    def test_schema_v2_and_integrity(self) -> None:
        self.assertEqual(17, self.store.scalar("SELECT MAX(version) FROM schema_migrations"))
        self.assertTrue(self.store.integrity_check()["ok"])
        self.assertGreaterEqual(self.store.scalar("SELECT COUNT(*) FROM permissions"), 20)

    def test_bootstrap_login_lockout_and_logout(self) -> None:
        other = ERPStore(self.runtime / "identity.db")
        identity = IdentityService(other)
        identity.ensure_local_defaults()
        user = identity.bootstrap("测试组织", "admin", "Correct-Horse-2026")
        self.assertIn("admin", user["roles"])
        token, principal = identity.login("DEFAULT", "admin", "Correct-Horse-2026")
        self.assertIn("users.manage", principal.permissions)
        self.assertEqual(principal.user_id, identity.authenticate(token).user_id)
        identity.logout(principal)
        with self.assertRaises(Exception): identity.authenticate(token)

    def test_user_role_enforces_permissions(self) -> None:
        limited = Principal("u", "ORG-DEFAULT", "viewer", "只读", frozenset({"master.read"}))
        with self.assertRaises(PermissionDenied): self.master.create_product(limited, "NO", "禁止", 1)
        self.assertEqual(self.product["id"], self.master.product(limited, self.product["id"])["id"])

    def test_optimistic_lock_blocks_lost_update(self) -> None:
        updated = self.master.update_product(self.admin, self.product["id"], self.product["version"], name="新名称")
        self.assertEqual("新名称", updated["name"])
        with self.assertRaises(Conflict):
            self.master.update_product(self.admin, self.product["id"], self.product["version"], name="覆盖")

    def test_password_policy(self) -> None:
        other = ERPStore(self.runtime / "short.db"); identity = IdentityService(other); identity.ensure_local_defaults()
        with self.assertRaises(ValidationError): identity.bootstrap("组织", "admin", "short")

    def test_partner_and_user_maintenance_use_optimistic_locking(self) -> None:
        customer = self.master.update_partner(
            self.admin, "customer", self.customer["id"], self.customer["version"],
            contact_name="新联系人", credit_limit_cents=2_000_000,
        )
        self.assertEqual("新联系人", customer["contact_name"])
        with self.assertRaises(Conflict):
            self.master.update_partner(self.admin, "customer", self.customer["id"], self.customer["version"], name="旧版本覆盖")
        user = self.identity.create_user(self.admin, "operator", "业务员", "Secure-Pass-2026", ["sales"])
        changed = self.identity.update_user(self.admin, user["id"], user["version"], role_codes=["sales", "warehouse"], status="disabled")
        self.assertEqual("disabled", changed["status"])
        self.assertEqual(["sales", "warehouse"], changed["roles"])


class InventoryTests(ProductionFixture):
    def test_receive_is_idempotent_and_ledger_is_immutable(self) -> None:
        first = self.receive(10, "receipt-1")
        replay = self.receive(10, "receipt-1")
        self.assertFalse(first["idempotent_replay"]); self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(10, replay["on_hand"])
        self.assertEqual(1, len(self.inventory.ledger(self.admin)))

    def test_atomic_reservation_blocks_negative_availability(self) -> None:
        self.receive(2)
        with self.assertRaises(InsufficientStock):
            self.inventory.reserve(self.admin, self.product["id"], "LOC-MAIN-STOCK", 3, "test", "T-1")
        self.assertEqual(0, self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")["reserved"])

    def test_transfer_between_locations_preserves_total(self) -> None:
        self.receive(10)
        target = self.master.create_location(self.admin, "SITE-MAIN", "BIN-B", "B 库位")
        self.inventory.transfer(self.admin, self.product["id"], "LOC-MAIN-STOCK", target["id"], 4, "transfer-1")
        self.assertEqual(6, self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")["on_hand"])
        self.assertEqual(4, self.inventory.balance(self.admin, self.product["id"], target["id"])["on_hand"])
        self.assertEqual(10, sum(item["on_hand"] for item in self.inventory.list_balances(self.admin)))

    def test_count_list_exposes_pending_document_for_follow_up(self) -> None:
        self.receive(5)
        count = self.inventory.create_count(self.admin, "LOC-MAIN-STOCK", "2026-08-14", [
            {"product_id": self.product["id"], "counted_quantity": 4},
        ], "cycle count")
        items = self.inventory.list_counts(self.admin, "pending_approval")
        self.assertEqual(count["id"], items[0]["id"])
        self.assertEqual(1, items[0]["variance_quantity"])

    def test_count_rejects_stale_snapshot(self) -> None:
        self.receive(10)
        count = self.inventory.create_count(self.admin, "LOC-MAIN-STOCK", "2026-08-13",
                                            [{"product_id": self.product["id"], "counted_quantity": 9}], "盘亏")
        self.inventory.receive(self.admin, self.product["id"], "LOC-MAIN-STOCK", 1, "during-count")
        with self.assertRaises(Conflict): self.inventory.post_count(self.admin, count["id"])

    def test_count_posts_adjustment_and_keeps_reservations_valid(self) -> None:
        self.receive(10)
        count = self.inventory.create_count(self.admin, "LOC-MAIN-STOCK", "2026-08-13",
                                            [{"product_id": self.product["id"], "counted_quantity": 8}], "盘亏")
        posted = self.inventory.post_count(self.admin, count["id"])
        self.assertEqual("posted", posted["status"])
        self.assertEqual(8, self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")["on_hand"])

    def test_lot_tracking_requires_lot_and_blocks_expired(self) -> None:
        lot_product = self.master.create_product(self.admin, "LOT-1", "批次商品", 500, 200, tracking="lot")
        with self.assertRaises(ValidationError):
            self.inventory.receive(self.admin, lot_product["id"], "LOC-MAIN-STOCK", 2, "lot-no-number")
        expired = self.inventory.create_lot(self.admin, lot_product["id"], "2025-A", "2025-01-01", "2025-12-31")
        with self.assertRaises(ValidationError):
            self.inventory.receive(self.admin, lot_product["id"], "LOC-MAIN-STOCK", 2, "expired", expired["id"])

    def test_database_trigger_rejects_negative_stock(self) -> None:
        self.receive(1)
        with self.assertRaises(Exception):
            self.store.execute("UPDATE stock_balance SET on_hand=-1 WHERE product_id=?", (self.product["id"],))


class SalesTests(ProductionFixture):
    def test_order_total_tax_reserve_and_partial_shipment(self) -> None:
        self.receive(20)
        order = self.sales.create_order(self.admin, self.customer["id"],
                                        [{"product_id": self.product["id"], "quantity": 2, "discount_basis_points": 1000}])
        self.assertEqual(20340, order["total_cents"])
        self.sales.confirm(self.admin, order["id"])
        reserved = self.sales.reserve(self.admin, order["id"])
        self.assertEqual("reserved", reserved["status"])
        shipment = self.sales.create_shipment(self.admin, order["id"])
        shipped = self.sales.post_shipment(self.admin, shipment["id"], "shipping-key")
        self.assertEqual("shipped", shipped["status"])
        self.assertEqual("shipped", self.sales.order(self.admin, order["id"])["status"])
        self.assertEqual(18, self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")["on_hand"])

    def test_partial_shipment_preserves_residual_reservation(self) -> None:
        self.receive(10)
        order = self.sales.create_order(
            self.admin, self.customer["id"], [{"product_id": self.product["id"], "quantity": 6}]
        )
        self.sales.confirm(self.admin, order["id"])
        self.sales.reserve(self.admin, order["id"])
        reservation_view = self.sales.order(self.admin, order["id"])["reservation_allocations"]
        self.assertEqual(6, reservation_view[0]["quantity"])
        reservation = self.store.row(
            "SELECT * FROM stock_reservations WHERE reference_id=? AND status='active'", (order["id"],)
        )
        first = self.sales.create_shipment(
            self.admin, order["id"], [{"reservation_id": reservation["id"], "quantity": 2}]
        )
        with self.assertRaises(Conflict):
            self.sales.create_shipment(
                self.admin, order["id"], [{"reservation_id": reservation["id"], "quantity": 1}]
            )
        self.sales.post_shipment(self.admin, first["id"], "partial-shipment-1")
        current = self.sales.order(self.admin, order["id"])
        self.assertEqual("partially_shipped", current["status"])
        self.assertEqual(2, current["lines"][0]["shipped_quantity"])
        self.assertEqual(4, current["lines"][0]["reserved_quantity"])
        balance = self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")
        self.assertEqual((8, 4, 4), (balance["on_hand"], balance["reserved"], balance["outgoing"]))
        residual = self.store.row(
            "SELECT * FROM stock_reservations WHERE reference_id=? AND status='active'", (order["id"],)
        )
        self.assertEqual(4, residual["quantity"])
        self.assertEqual(4, self.sales.order(self.admin, order["id"])["reservation_allocations"][0]["quantity"])
        second = self.sales.create_shipment(self.admin, order["id"])
        self.sales.post_shipment(self.admin, second["id"], "partial-shipment-2")
        self.assertEqual("shipped", self.sales.order(self.admin, order["id"])["status"])
        balance = self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")
        self.assertEqual((4, 0, 0), (balance["on_hand"], balance["reserved"], balance["outgoing"]))

    def test_cancelling_draft_shipment_releases_claim(self) -> None:
        self.receive(3)
        order = self.sales.create_order(
            self.admin, self.customer["id"], [{"product_id": self.product["id"], "quantity": 3}]
        )
        self.sales.confirm(self.admin, order["id"])
        self.sales.reserve(self.admin, order["id"])
        shipment = self.sales.create_shipment(self.admin, order["id"])
        with self.assertRaises(Conflict):
            self.sales.cancel(self.admin, order["id"], "customer request")
        cancelled = self.sales.cancel_shipment(self.admin, shipment["id"], "re-pick")
        self.assertEqual("cancelled", cancelled["status"])
        replacement = self.sales.create_shipment(self.admin, order["id"])
        self.assertEqual("draft", replacement["status"])

    def test_concurrent_orders_cannot_oversell(self) -> None:
        self.receive(5)
        orders = [
            self.sales.create_order(
                self.admin, self.customer["id"], [{"product_id": self.product["id"], "quantity": 4}]
            )
            for _ in range(2)
        ]
        for order in orders:
            self.sales.confirm(self.admin, order["id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.sales.reserve, self.admin, order["id"]) for order in orders]
        successes = 0
        failures = 0
        for future in futures:
            try:
                future.result(); successes += 1
            except (InsufficientStock, Conflict):
                failures += 1
        self.assertEqual((1, 1), (successes, failures))
        balance = self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")
        self.assertEqual(4, balance["reserved"])
        self.assertGreaterEqual(balance["available"], 0)

    def test_cancellation_releases_every_allocation(self) -> None:
        self.receive(10)
        order = self.sales.create_order(self.admin, self.customer["id"], [{"product_id": self.product["id"], "quantity": 4}])
        self.sales.confirm(self.admin, order["id"]); self.sales.reserve(self.admin, order["id"])
        self.assertEqual(4, self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")["outgoing"])
        cancelled = self.sales.cancel(self.admin, order["id"], "客户撤单")
        self.assertEqual("cancelled", cancelled["status"])
        self.assertEqual(0, self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")["reserved"])
        self.assertEqual(0, self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")["outgoing"])

    def test_credit_limit_blocks_confirmation(self) -> None:
        customer = self.master.create_customer(self.admin, "C-LOW", "低额度客户", credit_limit_cents=500)
        order = self.sales.create_order(self.admin, customer["id"], [{"product_id": self.product["id"], "quantity": 1}])
        with self.assertRaises(CreditLimitExceeded): self.sales.confirm(self.admin, order["id"])

    def test_duplicate_channel_reference_is_blocked(self) -> None:
        self.sales.create_order(self.admin, self.customer["id"], [{"product_id": self.product["id"], "quantity": 1}], channel="tmall", external_reference="TM-1")
        with self.assertRaises(ValidationError):
            self.sales.create_order(self.admin, self.customer["id"], [{"product_id": self.product["id"], "quantity": 1}], channel="tmall", external_reference="TM-1")

    def test_return_cannot_exceed_shipped_and_requires_approval(self) -> None:
        order = self.shipped_order(2)
        line = order["lines"][0]
        with self.assertRaises(ValidationError):
            self.sales.create_return(self.admin, order["id"], "quality", [{"sales_line_id": line["id"], "quantity": 3}])
        ret = self.sales.create_return(self.buyer, order["id"], "quality", [{"sales_line_id": line["id"], "quantity": 1}])
        self.assertEqual(ret["id"], self.sales.list_returns(self.admin, "draft")[0]["id"])
        with self.assertRaises(InvalidTransition):
            self.sales.receive_return(self.admin, ret["id"], "LOC-MAIN-STOCK", "return-1")
        with self.assertRaises(ApprovalRequired):
            self.sales.authorize_return(self.buyer, ret["id"])
        self.sales.authorize_return(self.approver, ret["id"])
        received = self.sales.receive_return(self.admin, ret["id"], "LOC-MAIN-STOCK", "return-1")
        self.assertEqual("received", received["status"])


class PurchasingAndFinanceTests(ProductionFixture):
    def make_purchase(self, quantity: int = 5) -> dict:
        order = self.purchasing.create_order(self.buyer, self.supplier["id"], "SITE-MAIN",
                                             [{"product_id": self.product["id"], "quantity": quantity, "unit_price_cents": 6000}])
        self.purchasing.submit(self.buyer, order["id"])
        return order

    def received_purchase(self, quantity: int = 5) -> dict:
        order = self.make_purchase(quantity)
        self.purchasing.approve(self.approver, order["id"])
        current = self.purchasing.order(self.admin, order["id"])
        receipt = self.purchasing.create_receipt(self.admin, order["id"], "LOC-MAIN-STOCK",
                                                 [{"purchase_line_id": current["lines"][0]["id"], "accepted_quantity": quantity}])
        self.purchasing.post_receipt(self.admin, receipt["id"], "goods-1")
        return self.purchasing.order(self.admin, order["id"])

    def test_purchase_draft_update_is_versioned_and_recalculates_totals(self) -> None:
        order = self.purchasing.create_order(
            self.buyer, self.supplier["id"], "SITE-MAIN",
            [{"product_id": self.product["id"], "quantity": 2, "unit_price_cents": 6000}],
        )
        updated = self.purchasing.update_draft(
            self.buyer, order["id"], order["version"],
            lines=[{"product_id": self.product["id"], "quantity": 3, "unit_price_cents": 6500}],
            freight_cents=500, expected_date="2026-08-20",
        )
        self.assertEqual(3, updated["lines"][0]["ordered_quantity"])
        self.assertEqual(500, updated["freight_cents"])
        self.assertGreater(updated["total_cents"], order["total_cents"])
        with self.assertRaises(Conflict):
            self.purchasing.update_draft(self.buyer, order["id"], order["version"], notes="stale")

    def test_four_eyes_purchase_approval(self) -> None:
        order = self.make_purchase()
        with self.assertRaises(ApprovalRequired): self.purchasing.approve(self.buyer, order["id"])
        approved = self.purchasing.approve(self.approver, order["id"])
        self.assertEqual("approved", approved["status"])
        self.assertEqual(5, self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")["incoming"])

    def test_partial_receipt_and_quality_rejection(self) -> None:
        order = self.make_purchase(10); self.purchasing.approve(self.approver, order["id"])
        current = self.purchasing.order(self.admin, order["id"]); line_id = current["lines"][0]["id"]
        receipt = self.purchasing.create_receipt(self.admin, order["id"], "LOC-MAIN-STOCK",
                                                 [{"purchase_line_id": line_id, "accepted_quantity": 6, "rejected_quantity": 1, "rejection_reason": "破损"}])
        self.assertEqual(receipt["id"], self.purchasing.list_receipts(self.admin, "draft")[0]["id"])
        self.purchasing.post_receipt(self.admin, receipt["id"], "partial")
        result = self.purchasing.order(self.admin, order["id"])
        self.assertEqual("partially_received", result["status"])
        self.assertEqual(6, result["lines"][0]["received_quantity"])
        self.assertEqual(3, self.inventory.balance(self.admin, self.product["id"], "LOC-MAIN-STOCK")["incoming"])

    def test_receivable_invoice_payment_and_statement(self) -> None:
        order = self.shipped_order(2)
        invoice = self.finance.create_invoice_from_sales(self.admin, order["id"])
        payment = self.finance.record_payment(self.admin, "receipt", "customer", self.customer["id"], invoice["total_cents"],
                                              external_reference="BANK-1", allocations=[{"invoice_id": invoice["id"], "amount_cents": invoice["total_cents"]}])
        self.assertEqual(payment["id"], self.finance.list_payments(self.admin, "receipt")[0]["id"])
        self.assertEqual(0, payment["unallocated_cents"])
        self.assertEqual("paid", self.finance.invoice(self.admin, invoice["id"])["status"])
        self.assertEqual(0, self.finance.partner_statement(self.admin, "customer", self.customer["id"])["outstanding_cents"])

    def test_payment_void_restores_invoice_balance(self) -> None:
        order = self.shipped_order(2)
        invoice = self.finance.create_invoice_from_sales(self.admin, order["id"])
        amount = invoice["total_cents"] // 2
        payment = self.finance.record_payment(
            self.admin, "receipt", "customer", self.customer["id"], amount,
            allocations=[{"invoice_id": invoice["id"], "amount_cents": amount}],
        )
        self.assertEqual("partially_paid", self.finance.invoice(self.admin, invoice["id"])["status"])
        voided = self.finance.void_payment(self.admin, payment["id"], "bank reversal")
        self.assertEqual("void", voided["status"])
        restored = self.finance.invoice(self.admin, invoice["id"])
        self.assertEqual("issued", restored["status"])
        self.assertEqual(0, restored["paid_cents"])
        self.assertEqual(restored["total_cents"], restored["outstanding_cents"])

    def test_partial_sales_invoices_only_bill_shipped_quantity(self) -> None:
        self.receive(10)
        order = self.sales.create_order(
            self.admin, self.customer["id"],
            [{"product_id": self.product["id"], "quantity": 3, "discount_basis_points": 3333}]
        )
        self.sales.confirm(self.admin, order["id"]); self.sales.reserve(self.admin, order["id"])
        reservation = self.store.row(
            "SELECT * FROM stock_reservations WHERE reference_id=? AND status='active'", (order["id"],)
        )
        first_shipment = self.sales.create_shipment(
            self.admin, order["id"], [{"reservation_id": reservation["id"], "quantity": 1}]
        )
        self.sales.post_shipment(self.admin, first_shipment["id"], "invoice-partial-1")
        first_invoice = self.finance.create_invoice_from_sales(self.admin, order["id"])
        self.assertEqual(1, first_invoice["lines"][0]["quantity"])
        with self.assertRaises(Conflict):
            self.finance.create_invoice_from_sales(self.admin, order["id"])
        second_shipment = self.sales.create_shipment(self.admin, order["id"])
        self.sales.post_shipment(self.admin, second_shipment["id"], "invoice-partial-2")
        second_invoice = self.finance.create_invoice_from_sales(self.admin, order["id"])
        self.assertEqual(2, second_invoice["lines"][0]["quantity"])
        self.assertEqual(order["total_cents"], first_invoice["total_cents"] + second_invoice["total_cents"])

    def test_payable_invoice_after_receipt(self) -> None:
        order = self.received_purchase()
        invoice = self.finance.create_invoice_from_purchase(self.admin, order["id"])
        self.assertEqual("payable", invoice["invoice_type"])
        self.assertGreater(invoice["outstanding_cents"], 0)

    def test_purchase_invoice_requires_three_way_quantity_and_price_match(self) -> None:
        order = self.received_purchase(5)
        source_line_id = self.purchasing.order(self.admin, order["id"])["lines"][0]["id"]
        with self.assertRaises(Conflict):
            self.finance.create_invoice_from_purchase(
                self.admin, order["id"], supplier_invoice_number="SUP-INV-001",
                supplier_lines=[{"source_line_id": source_line_id, "quantity": 6, "unit_price_cents": 6000}],
            )
        with self.assertRaises(Conflict):
            self.finance.create_invoice_from_purchase(
                self.admin, order["id"], supplier_invoice_number="SUP-INV-001",
                supplier_lines=[{"source_line_id": source_line_id, "quantity": 2, "unit_price_cents": 6301}],
                price_tolerance_basis_points=500,
            )
        invoice = self.finance.create_invoice_from_purchase(
            self.admin, order["id"], supplier_invoice_number="SUP-INV-001",
            supplier_lines=[{"source_line_id": source_line_id, "quantity": 2, "unit_price_cents": 6300}],
            price_tolerance_basis_points=500, supplier_total_cents=14238,
        )
        self.assertEqual("matched", invoice["match_status"])
        self.assertEqual("three_way", invoice["match_details"]["policy"])
        self.assertEqual(2, invoice["lines"][0]["quantity"])
        with self.assertRaises(Conflict):
            self.finance.create_invoice_from_purchase(
                self.admin, order["id"], supplier_invoice_number="SUP-INV-001",
                supplier_lines=[{"source_line_id": source_line_id, "quantity": 1, "unit_price_cents": 6000}],
            )

    def test_closed_period_blocks_backdated_invoice(self) -> None:
        order = self.shipped_order()
        self.finance.close_period(self.admin, 2026, 7)
        with self.assertRaises(Exception):
            self.finance.create_invoice_from_sales(self.admin, order["id"], "2026-07-20")


class CashManagementTests(ProductionFixture):
    def _account_and_payment(self, external_reference: str = "BANK-TX-001") -> tuple[dict, dict]:
        account = self.cash.create_account(
            self.admin, "ICBC-CNY", "工商银行基本户", "中国工商银行", "1002", "CNY", "6222021234567890",
        )
        payment = self.finance.record_payment(
            self.admin, "receipt", "customer", self.customer["id"], 12_345,
            "2026-08-14", "CNY", "bank_transfer", external_reference, [], "银行回款", account["id"],
        )
        return account, payment

    def test_statement_import_auto_match_and_reconcile(self) -> None:
        account, payment = self._account_and_payment()
        statement = self.cash.import_statement(
            self.admin, account["id"], "2026-08", "2026-08-01", "2026-08-31", 0, 12_345,
            [{"external_transaction_id": "BANK-LINE-001", "transaction_date": "2026-08-14",
              "signed_amount_cents": 12_345, "counterparty_name": self.customer["name"],
              "reference": "BANK-TX-001", "description": "客户回款"}],
        )
        self.assertFalse(statement["idempotent_replay"])
        replay = self.cash.import_statement(
            self.admin, account["id"], "2026-08", "2026-08-01", "2026-08-31", 0, 12_345,
            [{"external_transaction_id": "BANK-LINE-001", "transaction_date": "2026-08-14",
              "signed_amount_cents": 12_345, "counterparty_name": self.customer["name"],
              "reference": "BANK-TX-001", "description": "客户回款"}],
        )
        self.assertTrue(replay["idempotent_replay"])
        matched = self.cash.auto_match(self.admin, statement["id"])
        self.assertEqual(1, matched["auto_match"]["matched_count"])
        self.assertEqual(payment["id"], matched["lines"][0]["payment_id"])
        reconciled = self.cash.reconcile(self.admin, statement["id"])
        self.assertEqual("reconciled", reconciled["status"])
        self.assertEqual(12_345, self.cash.account(self.admin, account["id"])["book_balance_cents"])

    def test_statement_rejects_unbalanced_duplicate_and_wrong_match(self) -> None:
        account, payment = self._account_and_payment("BANK-TX-FAIL")
        with self.assertRaises(ValidationError):
            self.cash.import_statement(
                self.admin, account["id"], "BAD-BALANCE", "2026-08-01", "2026-08-31", 0, 999,
                [{"external_transaction_id": "BAD-1", "transaction_date": "2026-08-14",
                  "signed_amount_cents": 1_000}],
            )
        statement = self.cash.import_statement(
            self.admin, account["id"], "WRONG-DIRECTION", "2026-08-01", "2026-08-31", 0, -12_345,
            [{"external_transaction_id": "BANK-LINE-FAIL", "transaction_date": "2026-08-14",
              "signed_amount_cents": -12_345}],
        )
        with self.assertRaises(ValidationError):
            self.cash.confirm_match(self.admin, statement["lines"][0]["id"], payment["id"])
        with self.assertRaises(Conflict):
            self.cash.reconcile(self.admin, statement["id"])
        with self.assertRaises(Conflict):
            self.cash.import_statement(
                self.admin, account["id"], "DUPLICATE-LINE", "2026-08-01", "2026-08-31", 0, -12_345,
                [{"external_transaction_id": "BANK-LINE-FAIL", "transaction_date": "2026-08-14",
                  "signed_amount_cents": -12_345}],
            )

    def test_unreconciled_statement_blocks_period_close(self) -> None:
        account, _ = self._account_and_payment("BANK-CLOSE-001")
        self.cash.import_statement(
            self.admin, account["id"], "CLOSE-BLOCK", "2026-08-01", "2026-08-31", 0, 12_345,
            [{"external_transaction_id": "BANK-CLOSE-LINE", "transaction_date": "2026-08-14",
              "signed_amount_cents": 12_345}],
        )
        with self.assertRaises(Conflict):
            self.finance.close_period(self.admin, 2026, 8)


class AccountingAndValuationTests(ProductionFixture):
    def setUp(self) -> None:
        super().setUp(); self.ledger_service = LedgerService(self.store)

    def test_fifo_valuation_sales_cost_and_receivable_reconcile(self) -> None:
        self.inventory.receive(self.admin, self.product["id"], "LOC-MAIN-STOCK", 2, "fifo-a", unit_cost_cents=5000)
        self.inventory.receive(self.admin, self.product["id"], "LOC-MAIN-STOCK", 3, "fifo-b", unit_cost_cents=7000)
        order = self.sales.create_order(self.admin, self.customer["id"],
                                        [{"product_id": self.product["id"], "quantity": 4}])
        self.sales.confirm(self.admin, order["id"]); self.sales.reserve(self.admin, order["id"])
        shipment = self.sales.create_shipment(self.admin, order["id"])
        self.sales.post_shipment(self.admin, shipment["id"], "fifo-ship")
        shipment_move = self.store.row("SELECT * FROM stock_moves WHERE reference_id=?", (shipment["id"],))
        self.assertEqual(24000, shipment_move["total_cost_cents"])
        self.assertEqual(1, self.store.scalar("SELECT SUM(remaining_quantity) FROM inventory_valuation_layers"))
        self.assertEqual(7000, self.store.scalar("SELECT SUM(remaining_value_cents) FROM inventory_valuation_layers"))
        valuation = self.reports.inventory_valuation(self.admin)
        self.assertEqual(7000, valuation[0]["value_cents"])
        self.assertEqual(1, valuation[0]["on_hand"])
        invoice = self.finance.create_invoice_from_sales(self.admin, order["id"])
        payment = self.finance.record_payment(
            self.admin, "receipt", "customer", self.customer["id"], invoice["total_cents"],
            external_reference="ACCOUNTING-AR", allocations=[{"invoice_id": invoice["id"], "amount_cents": invoice["total_cents"]}],
        )
        self.assertEqual("posted", payment["status"])
        trial = self.ledger_service.trial_balance(self.admin)
        self.assertTrue(trial["balanced"]); self.assertEqual(trial["debit_cents"], trial["credit_cents"])
        self.assertTrue(self.ledger_service.reconcile_subledgers(self.admin)["ok"])
        cogs = self.ledger_service.account_statement(self.admin, "6001")
        self.assertEqual(24000, cogs["closing_balance_cents"])
        statements = self.ledger_service.financial_statements(self.admin)
        self.assertTrue(statements["balance_sheet_balanced"])
        self.assertEqual(16000, statements["current_profit_cents"])

    def test_purchase_grni_price_variance_payable_and_payment_reversal(self) -> None:
        purchase = self.purchasing.create_order(
            self.buyer, self.supplier["id"], "SITE-MAIN",
            [{"product_id": self.product["id"], "quantity": 5, "unit_price_cents": 6000}],
        )
        self.purchasing.submit(self.buyer, purchase["id"]); self.purchasing.approve(self.approver, purchase["id"])
        line_id = self.purchasing.order(self.admin, purchase["id"])["lines"][0]["id"]
        receipt = self.purchasing.create_receipt(self.admin, purchase["id"], "LOC-MAIN-STOCK",
                                                 [{"purchase_line_id": line_id, "accepted_quantity": 5}])
        self.purchasing.post_receipt(self.admin, receipt["id"], "ACCOUNTING-GR")
        self.assertEqual(-30000, self._account_net("2203"))
        invoice = self.finance.create_invoice_from_purchase(
            self.admin, purchase["id"], supplier_invoice_number="PV-001",
            supplier_lines=[{"source_line_id": line_id, "quantity": 5, "unit_price_cents": 6300}],
            price_tolerance_basis_points=500, supplier_total_cents=35595,
        )
        self.assertEqual(1500, self._account_net("6002"))
        self.assertEqual(0, self._account_net("2203"))
        self.assertEqual(-invoice["total_cents"], self._account_net("2202"))
        payment = self.finance.record_payment(
            self.admin, "disbursement", "supplier", self.supplier["id"], invoice["total_cents"],
            external_reference="ACCOUNTING-AP", allocations=[{"invoice_id": invoice["id"], "amount_cents": invoice["total_cents"]}],
        )
        self.assertEqual(0, self._account_net("2202"))
        self.finance.void_payment(self.admin, payment["id"], "银行退票")
        self.assertEqual(-invoice["total_cents"], self._account_net("2202"))
        self.assertEqual(1, self.store.scalar("SELECT COUNT(*) FROM journal_entries WHERE reversal_of_id IS NOT NULL"))
        self.assertTrue(self.ledger_service.reconcile_subledgers(self.admin)["ok"])

    def test_posted_journal_is_immutable_and_unbalanced_entry_is_rejected(self) -> None:
        self.receive(1)
        entry_id = self.store.scalar("SELECT id FROM journal_entries LIMIT 1")
        line_id = self.store.scalar("SELECT id FROM journal_lines WHERE journal_entry_id=? LIMIT 1", (entry_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.execute("UPDATE journal_lines SET debit_cents=debit_cents+1 WHERE id=?", (line_id,))
        with self.assertRaises(Conflict):
            with self.store.connect() as conn:
                self.ledger_service.post(conn, self.admin, "general", "2026-08-14", "test", "bad", "bad-entry",
                                         "不平衡测试", [{"account_code": "1002", "debit_cents": 100},
                                                        {"account_code": "5001", "credit_cents": 99}])

    def test_transfer_preserves_fifo_value_without_general_ledger_movement(self) -> None:
        location = self.master.create_location(self.admin, "SITE-MAIN", "VALUED", "估值库位")
        self.inventory.receive(self.admin, self.product["id"], "LOC-MAIN-STOCK", 2, "valued-opening-a", unit_cost_cents=5000)
        self.inventory.receive(self.admin, self.product["id"], "LOC-MAIN-STOCK", 2, "valued-opening-b", unit_cost_cents=7000)
        entries_before = self.store.scalar("SELECT COUNT(*) FROM journal_entries")
        self.inventory.transfer(self.admin, self.product["id"], "LOC-MAIN-STOCK", location["id"], 3, "valued-transfer")
        self.assertEqual(entries_before, self.store.scalar("SELECT COUNT(*) FROM journal_entries"))
        self.assertEqual(24000, self.store.scalar("SELECT SUM(remaining_value_cents) FROM inventory_valuation_layers"))
        transferred = self.store.row(
            "SELECT original_quantity,original_value_cents,unit_cost_cents FROM inventory_valuation_layers WHERE location_id=?",
            (location["id"],),
        )
        self.assertEqual({"original_quantity": 3, "original_value_cents": 17000, "unit_cost_cents": 5666}, transferred)
        self.assertTrue(self.ledger_service.reconcile_subledgers(self.admin)["ok"])

    def test_period_close_blocks_subledger_general_ledger_difference(self) -> None:
        self.receive(1)
        with self.store.connect() as conn:
            self.ledger_service.post(
                conn, self.admin, "general", "2026-08-14", "manual", "mismatch", "mismatch-close",
                "制造库存科目差异", [{"account_code": "1405", "debit_cents": 1},
                                  {"account_code": "3101", "credit_cents": 1}],
            )
        with self.assertRaises(Conflict):
            self.finance.close_period(self.admin, 2026, 8)

    def test_period_close_accepts_reconciled_goods_received_not_invoiced(self) -> None:
        purchase = self.purchasing.create_order(
            self.buyer, self.supplier["id"], "SITE-MAIN",
            [{"product_id": self.product["id"], "quantity": 2, "unit_price_cents": 6400}],
            order_date="2026-08-10",
        )
        self.purchasing.submit(self.buyer, purchase["id"]); self.purchasing.approve(self.approver, purchase["id"])
        line_id = self.purchasing.order(self.admin, purchase["id"])["lines"][0]["id"]
        receipt = self.purchasing.create_receipt(
            self.admin, purchase["id"], "LOC-MAIN-STOCK", [{"purchase_line_id": line_id, "accepted_quantity": 2}],
            receipt_date="2026-08-11",
        )
        self.purchasing.post_receipt(self.admin, receipt["id"], "close-grni")
        period = self.finance.close_period(self.admin, 2026, 8)
        self.assertEqual("closed", period["status"])

    def test_finance_api_exposes_journals_trial_balance_and_reconciliation(self) -> None:
        self.receive(1)
        api = APIRouter(self.store, load_settings(self.runtime))
        entries = api.dispatch("GET", "/api/v1/finance/journal-entries", {}, {})
        trial = api.dispatch("GET", "/api/v1/finance/trial-balance", {}, {})
        reconciliation = api.dispatch("GET", "/api/v1/finance/subledger-reconciliation", {}, {})
        accounts = api.dispatch("GET", "/api/v1/finance/accounts", {}, {})
        statement = api.dispatch("GET", "/api/v1/finance/statements", {}, {})
        self.assertEqual(200, entries.status); self.assertEqual(1, len(entries.body["items"]))
        detail = api.dispatch("GET", f"/api/v1/finance/journal-entries/{entries.body['items'][0]['id']}", {}, {})
        self.assertEqual(200, detail.status); self.assertEqual(2, len(detail.body["lines"]))
        self.assertEqual(200, trial.status); self.assertTrue(trial.body["balanced"])
        self.assertEqual(200, reconciliation.status); self.assertTrue(reconciliation.body["ok"])
        self.assertEqual(200, accounts.status); self.assertGreaterEqual(len(accounts.body["items"]), 17)
        self.assertEqual(200, statement.status); self.assertTrue(statement.body["balance_sheet_balanced"])

    def _account_net(self, code: str) -> int:
        return int(self.store.scalar(
            "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_lines l "
            "JOIN ledger_accounts a ON a.id=l.account_id JOIN journal_entries e ON e.id=l.journal_entry_id "
            "WHERE a.code=? AND e.status='posted'", (code,),
        ) or 0)


class OperationsAndAPITests(ProductionFixture):
    def test_lifecycle_and_governance_collections_are_exposed_by_api(self) -> None:
        api = APIRouter(self.store, load_settings(self.runtime))
        paths = (
            "/api/v1/inventory/counts", "/api/v1/inventory/serials",
            "/api/v1/sales/returns", "/api/v1/purchases/receipts",
            "/api/v1/finance/payments", "/api/v1/finance/periods",
            "/api/v1/finance/bank-accounts", "/api/v1/finance/bank-statements",
            "/api/v1/pricing/lists", "/api/v1/reconciliations", "/api/v1/alerts",
        )
        for path in paths:
            response = api.dispatch("GET", path, {}, {})
            self.assertEqual(200, response.status, path)
            self.assertIn("items", response.body, path)
        accounting = api.dispatch("POST", "/api/v1/reconciliations/run", {}, {"type": "accounting"})
        self.assertEqual(201, accounting.status)
        self.assertEqual("accounting", accounting.body["reconciliation_type"])

    def test_backup_roundtrip_and_health(self) -> None:
        self.receive(3)
        backup = BackupService(self.store, self.runtime / "backups")
        created = backup.create("test")
        self.assertTrue(backup.verify(created["backup"])["ok"])
        restored = self.runtime / "restored.db"
        self.assertTrue(backup.restore(created["backup"], restored)["ok"])
        self.assertTrue(ERPStore(restored).integrity_check()["ok"])
        ok, _ = HealthService(self.store, self.runtime).ready(); self.assertTrue(ok)

    def test_outbox_claim_and_acknowledge(self) -> None:
        self.receive(2)
        outbox = OutboxService(self.store); events = outbox.claim()
        self.assertEqual(1, len(events)); outbox.acknowledge(events[0]["id"])
        self.assertEqual("published", self.store.scalar("SELECT status FROM outbox_events WHERE id=?", (events[0]["id"],)))

    def test_outbox_lease_prevents_concurrent_duplicate_delivery(self) -> None:
        self.receive(1)
        outbox = OutboxService(self.store)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda owner: outbox.claim(1, owner, 30), ("worker-a", "worker-b")))
        claimed = first + second
        self.assertEqual(1, len(claimed))
        event = claimed[0]
        with self.assertRaises(ValueError):
            outbox.acknowledge(event["id"], "wrong-worker")
        outbox.acknowledge(event["id"], event["processing_owner"])

    def test_runtime_maintenance_gate_and_fenced_writer_lease(self) -> None:
        coordinator = RuntimeCoordinator(self.store)
        first = coordinator.acquire("writer", "instance-a", 30)
        with self.assertRaises(RuntimeError):
            coordinator.acquire("writer", "instance-b", 30)
        self.store.execute("UPDATE instance_leases SET expires_at=datetime('now','-1 second') WHERE lease_name='writer'")
        second = coordinator.acquire("writer", "instance-b", 30)
        self.assertGreater(second["fencing_token"], first["fencing_token"])
        self.assertTrue(coordinator.release("writer", "instance-b"))
        third = coordinator.acquire("writer", "instance-c", 30)
        self.assertGreater(third["fencing_token"], second["fencing_token"])
        coordinator.set_maintenance(True, "database migration", self.admin.user_id)
        settings = load_settings(self.runtime)
        api = APIRouter(self.store, settings)
        blocked = api.dispatch("POST", "/api/v1/products", {"idempotency-key": "maintenance-product"},
                               {"sku": "BLOCKED", "name": "Blocked"})
        self.assertEqual(503, blocked.status)
        ready = api.dispatch("GET", "/api/v1/health/ready", {}, {})
        self.assertEqual(503, ready.status)
        resumed = api.dispatch("POST", "/api/v1/operations/maintenance", {}, {"enabled": False})
        self.assertEqual(200, resumed.status)
        self.assertFalse(resumed.body["maintenance_mode"])

    def test_load_shedding_and_per_source_rate_limit(self) -> None:
        settings = replace(load_settings(self.runtime), max_concurrent_requests=1, request_rate_per_minute=1)
        api = APIRouter(self.store, settings)
        first = api.dispatch("GET", "/api/v1/products", {}, {}, "10.0.0.1")
        second = api.dispatch("GET", "/api/v1/products", {}, {}, "10.0.0.1")
        self.assertEqual(200, first.status)
        self.assertEqual(429, second.status)
        self.assertEqual("60", second.headers["Retry-After"])
        self.assertTrue(api._capacity.acquire(blocking=False))
        try:
            overloaded = api.dispatch("GET", "/api/v1/products", {}, {}, "10.0.0.2")
        finally:
            api._capacity.release()
        self.assertEqual(503, overloaded.status)
        self.assertEqual("2", overloaded.headers["Retry-After"])

    def test_readiness_can_require_a_recent_verified_backup(self) -> None:
        health = HealthService(self.store, self.runtime, minimum_free_mb=32,
                               backup_max_age_hours=24, require_recent_backup=True)
        ok, before = health.ready()
        self.assertFalse(ok)
        self.assertTrue(before["checks"]["backup_freshness"]["warning"])
        BackupService(self.store, self.runtime / "backups").create("readiness")
        ok, after = health.ready()
        self.assertTrue(ok)
        self.assertFalse(after["checks"]["backup_freshness"]["warning"])

    def test_changed_backup_is_removed_from_ready_recovery_points(self) -> None:
        backup = BackupService(self.store, self.runtime / "backups")
        created = backup.create("tamper")
        path = Path(created["backup"])
        payload = bytearray(path.read_bytes()); payload[-1] ^= 1; path.write_bytes(payload)
        health = HealthService(self.store, self.runtime, minimum_free_mb=32,
                               backup_max_age_hours=24, require_recent_backup=True)
        ok, result = health.ready()
        self.assertFalse(ok)
        self.assertFalse(result["checks"]["backup_freshness"]["unchanged_since_verification"])
        self.assertFalse(backup.verify(path)["ok"])
        self.assertEqual("failed", self.store.scalar(
            "SELECT status FROM backup_catalog WHERE backup_path=?", (str(path.resolve()),)
        ))

    def test_dashboard_and_reorder_report(self) -> None:
        dashboard = self.reports.dashboard(self.admin)
        self.assertIn("inventory_value_cents", dashboard)
        suggestions = self.reports.reorder_suggestions(self.admin)
        self.assertEqual(self.product["id"], suggestions[0]["product_id"])
        self.sales.create_order(
            self.admin, self.customer["id"], [{"product_id": self.product["id"], "quantity": 2}],
            order_date="2026-06-10",
        )
        trends = self.reports.dashboard_trends(self.admin, 4, "2026-08-14")
        self.assertEqual(["2026-05", "2026-06", "2026-07", "2026-08"], trends["months"])
        self.assertGreater(trends["series"]["sales"][1], 0)
        self.assertEqual(4, len(trends["series"]["inventory"]))
        with self.assertRaises(ValidationError):
            self.reports.dashboard_trends(self.admin, 2, "2026-08-14")

    def test_api_health_and_idempotent_product_flow(self) -> None:
        settings = load_settings(self.runtime)
        api = APIRouter(self.store, settings)
        live = api.dispatch("GET", "/api/v1/health/live", {}, {})
        self.assertEqual(200, live.status)
        response = api.dispatch("GET", "/api/v1/products", {}, {})
        self.assertEqual(200, response.status)
        self.assertGreaterEqual(len(response.body["items"]), 1)

    def test_audit_log_contains_business_writes(self) -> None:
        self.receive(1)
        entries = AuditService(self.store).search(self.admin, entity_type="stock_move")
        self.assertEqual("inventory.receive", entries[0]["action"])


class ExtendedERPTests(ProductionFixture):
    def test_customer_and_channel_price_priority_with_quantity_break(self) -> None:
        pricing = PricingService(self.store)
        generic = pricing.create_price_list(self.admin, "PUBLIC", "公开阶梯价", channel="direct", priority=100)
        pricing.add_rule(self.admin, generic["id"], self.product["id"], 10, 9000)
        customer_price = pricing.create_price_list(
            self.admin, "VIP", "客户专属价", customer_id=self.customer["id"], channel="direct", priority=10
        )
        pricing.add_rule(self.admin, customer_price["id"], self.product["id"], 5, 8000, 500)
        one = pricing.resolve(self.admin, self.product["id"], 2, self.customer["id"], "direct")
        bulk = pricing.resolve(self.admin, self.product["id"], 6, self.customer["id"], "direct")
        public_bulk = pricing.resolve(self.admin, self.product["id"], 10, None, "direct")
        self.assertEqual(10000, one["unit_price_cents"])
        self.assertEqual(8000, bulk["unit_price_cents"])
        self.assertEqual(500, bulk["discount_basis_points"])
        self.assertEqual(9000, public_bulk["unit_price_cents"])

    def test_serial_number_lifecycle_and_global_uniqueness(self) -> None:
        serial_product = self.master.create_product(
            self.admin, "SERIAL-1", "序列号商品", 20000, 10000, tracking="serial"
        )
        lot = self.inventory.create_lot(self.admin, serial_product["id"], "SERIAL-LOT")
        self.inventory.receive(self.admin, serial_product["id"], "LOC-MAIN-STOCK", 1, "serial-receive-1", lot["id"])
        self.inventory.receive(self.admin, serial_product["id"], "LOC-MAIN-STOCK", 1, "serial-receive-2", lot["id"])
        serials = SerialNumberService(self.store)
        registered = serials.register(
            self.admin, serial_product["id"], ["SN-001", "SN-002"], "LOC-MAIN-STOCK", lot["id"]
        )
        self.assertEqual(2, len(registered))
        with self.assertRaises(Conflict):
            serials.register(self.admin, serial_product["id"], ["SN-001"], "LOC-MAIN-STOCK")
        reserved = serials.transition(self.admin, registered[0]["id"], "reserved")
        self.assertEqual("reserved", reserved["status"])
        shipped = serials.transition(self.admin, registered[0]["id"], "shipped")
        self.assertIsNone(shipped["current_location_id"])
        returned = serials.transition(self.admin, registered[0]["id"], "returned", "LOC-MAIN-STOCK")
        self.assertEqual("returned", returned["status"])
        with self.assertRaises(InvalidTransition):
            serials.transition(self.admin, registered[1]["id"], "shipped")

    def test_serial_numbers_are_bound_to_shipment(self) -> None:
        serial_product = self.master.create_product(
            self.admin, "SERIAL-SHIP", "Serialized shipment", 20000, 10000, tracking="serial"
        )
        lot = self.inventory.create_lot(self.admin, serial_product["id"], "SHIP-LOT")
        self.inventory.receive(self.admin, serial_product["id"], "LOC-MAIN-STOCK", 1, "serial-stock-1", lot["id"])
        self.inventory.receive(self.admin, serial_product["id"], "LOC-MAIN-STOCK", 1, "serial-stock-2", lot["id"])
        serial_service = SerialNumberService(self.store)
        serials = serial_service.register(
            self.admin, serial_product["id"], ["SHIP-001", "SHIP-002"], "LOC-MAIN-STOCK", lot["id"]
        )
        order = self.sales.create_order(
            self.admin, self.customer["id"], [{"product_id": serial_product["id"], "quantity": 2}]
        )
        self.sales.confirm(self.admin, order["id"]); self.sales.reserve(self.admin, order["id"])
        reservation = self.store.row(
            "SELECT * FROM stock_reservations WHERE reference_id=? AND status='active'", (order["id"],)
        )
        with self.assertRaises(ValidationError):
            self.sales.create_shipment(self.admin, order["id"])
        shipment = self.sales.create_shipment(
            self.admin, order["id"], [{"reservation_id": reservation["id"], "quantity": 2,
                                       "serial_ids": [serials[0]["id"], serials[1]["id"]]}]
        )
        self.assertEqual(2, len(shipment["lines"][0]["serials"]))
        self.assertEqual("reserved", serial_service.get(self.admin, serials[0]["id"])["status"])
        self.sales.cancel_shipment(self.admin, shipment["id"], "packing error")
        self.assertEqual("available", serial_service.get(self.admin, serials[0]["id"])["status"])
        replacement = self.sales.create_shipment(
            self.admin, order["id"], [{"reservation_id": reservation["id"], "quantity": 2,
                                       "serial_ids": [serials[0]["id"], serials[1]["id"]]}]
        )
        self.sales.post_shipment(self.admin, replacement["id"], "serialized-shipment")
        shipped = serial_service.get(self.admin, serials[0]["id"])
        self.assertEqual("shipped", shipped["status"])
        self.assertIsNone(shipped["current_location_id"])
        current_order = self.sales.order(self.admin, order["id"])
        sales_line = current_order["lines"][0]
        returned = self.sales.create_return(
            self.admin, order["id"], "customer_return",
            [{"sales_line_id": sales_line["id"], "quantity": 1, "lot_id": lot["id"],
              "serial_ids": [serials[0]["id"]]}],
        )
        self.sales.authorize_return(self.approver, returned["id"])
        self.sales.receive_return(self.admin, returned["id"], "LOC-MAIN-STOCK", "serialized-return")
        self.assertEqual("returned", serial_service.get(self.admin, serials[0]["id"])["status"])

    def test_csv_import_is_two_phase_and_rejects_duplicate_rows(self) -> None:
        service = ImportExportService(self.store)
        invalid = service.validate_csv(
            self.admin, "products",
            "sku,name,sales_price_cents\nCSV-1,导入商品,1000\nCSV-1,重复商品,1200\n",
            "invalid.csv",
        )
        self.assertEqual("failed", invalid["status"])
        self.assertEqual(1, invalid["invalid_rows"])
        with self.assertRaises(Conflict):
            service.commit(self.admin, invalid["id"])
        valid = service.validate_csv(
            self.admin, "products",
            "sku,name,sales_price_cents,standard_cost_cents,min_stock,max_stock\nCSV-2,正确商品,1000,500,2,20\n",
            "valid.csv",
        )
        committed = service.commit(self.admin, valid["id"])
        self.assertEqual("completed", committed["status"])
        self.assertEqual("正确商品", self.master.product(self.admin, "CSV-2")["name"])
        self.assertTrue(service.export_csv(self.admin, "products").startswith("\ufeff"))

    def test_inventory_sales_and_finance_reconciliation_pass(self) -> None:
        self.shipped_order(2)
        service = ReconciliationService(self.store)
        result = service.run_all(self.admin)
        self.assertEqual("passed", result["inventory"]["status"])
        self.assertEqual("passed", result["sales"]["status"])
        self.assertEqual("passed", result["finance"]["status"])

    def test_reconciliation_detects_tampered_denormalized_total(self) -> None:
        order = self.shipped_order(1)
        self.store.execute("UPDATE sales_documents SET total_cents=total_cents+1 WHERE id=?", (order["id"],))
        result = ReconciliationService(self.store).run_sales(self.admin)
        self.assertEqual("failed", result["status"])
        self.assertEqual("sales_total_mismatch", result["result"]["discrepancies"][0]["type"])

    def test_multiple_partner_contacts_and_default_addresses(self) -> None:
        details = PartnerDetailService(self.store)
        first = details.add_contact(self.admin, "customer", self.customer["id"], "张三", phone="13800000000", is_primary=True)
        second = details.add_contact(self.admin, "customer", self.customer["id"], "李四", email="li@example.com", is_primary=True)
        contacts = details.contacts(self.admin, "customer", self.customer["id"])
        self.assertFalse(next(item for item in contacts if item["id"] == first["id"])["is_primary"])
        self.assertTrue(next(item for item in contacts if item["id"] == second["id"])["is_primary"])
        address = details.add_address(
            self.admin, "customer", self.customer["id"], "shipping", "李四", "13900000000",
            province="上海市", city="上海市", district="浦东新区", street="世纪大道 1 号", is_default=True
        )
        self.assertIn("世纪大道", address["formatted"])

    def test_alert_refresh_is_idempotent_and_resolves_recovered_stock(self) -> None:
        alerts = AlertService(self.store)
        first = alerts.refresh(self.admin)
        second = alerts.refresh(self.admin)
        self.assertEqual(1, first["generated"])
        self.assertEqual(0, second["generated"])
        current = alerts.list(self.admin, status="open")
        self.assertEqual("low_stock", current[0]["alert_type"])
        alerts.acknowledge(self.admin, current[0]["id"])
        self.receive(10)
        result = alerts.refresh(self.admin)
        self.assertEqual(1, result["resolved"])


if __name__ == "__main__":
    unittest.main()
