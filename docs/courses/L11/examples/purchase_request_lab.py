"""Exercise the real purchase service with temporary data and the unified Harness.

premature-stock deliberately adds stock at application time as a teaching defect.
No agents are launched and no personal database is modified.
"""
from __future__ import annotations
import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from workbench.course_experiments import ensure_product_process
ensure_product_process(__file__)
from flowerp.service import ERPService
from flowerp.store import ERPStore
from flowerp.models import NotFound, OrderLine
from eval import harness

MODES = ("normal", "zero", "negative", "blank-reason", "unknown-sku", "duplicate-id", "premature-stock")

def check(mode):
    with tempfile.TemporaryDirectory(prefix="l11-purchase-") as folder:
        store = ERPStore(Path(folder) / "purchase.db")
        service = ERPService(store)
        service.add_product("A", "商品 A", 100)
        service.receive_stock("A", 10, "opening")
        service.create_order("其他客户", [OrderLine("A", 2, 100)], "OTHER")
        service.reserve_order("OTHER")
        service.propose_purchase("A", 3, "另一项需求", "PR-OTHER")
        tables = ("purchase_requests", "stock", "inventory_events", "sales_orders", "sales_order_lines")
        def snapshot():
            return {t: store.rows(f"SELECT * FROM {t} ORDER BY 1") for t in tables}
        if mode == "duplicate-id":
            service.propose_purchase("A", 7, "低于补货点", "PR-TARGET")
        before = snapshot()
        quantity = {"zero": 0, "negative": -1}.get(mode, 7)
        reason = "   " if mode == "blank-reason" else "  低于补货点  "
        sku = "UNKNOWN" if mode == "unknown-sku" else "a"
        expected_error = {"zero": ValueError, "negative": ValueError, "blank-reason": ValueError,
                          "unknown-sku": NotFound, "duplicate-id": sqlite3.IntegrityError}.get(mode)
        if expected_error:
            try:
                service.propose_purchase(sku, quantity, reason, "PR-TARGET")
            except expected_error:
                pass
            else:
                raise AssertionError("invalid request was accepted")
            assert snapshot() == before, "rejection changed persisted state"
            return f"{mode}: {expected_error.__name__}; five tables unchanged"
        result = service.propose_purchase(sku, quantity, reason, "PR-TARGET")
        if mode == "premature-stock":
            service.receive_stock("A", 7, "teaching-premature-receipt")
        assert (result["id"], result["sku"], result["quantity"], result["reason"], result["status"]) == (
            "PR-TARGET", "A", 7, "低于补货点", "proposed")
        assert result["approved_by"] is None
        after = snapshot()
        assert all(after[t] == before[t] for t in tables if t != "purchase_requests"), "application changed stock, events, or orders"
        assert [p for p in after["purchase_requests"] if p["id"] != "PR-TARGET"] == before["purchase_requests"]
        assert len(after["purchase_requests"]) == len(before["purchase_requests"]) + 1
        assert service.purchase("PR-TARGET") == result
        product = service.product("A")
        assert (product["on_hand"], product["reserved"], product["available"]) == (10, 2, 8)
        return "normal: PR-TARGET proposed; reason trimmed; one request added; 10/2/8 and OTHER unchanged"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--report-path", required=True)
    args = parser.parse_args()
    if Path(args.report_path).exists():
        parser.error("use a new report path; preserve previous evidence")
    with patch.object(harness, "EVALS", [("l11_teaching_purchase", "blocking", lambda: check(args.mode))]), \
         patch.object(sys, "argv", ["harness", "--suite", "blocking", "--case", "l11_teaching_purchase", "--report-path", args.report_path]):
        raise SystemExit(harness.main())
