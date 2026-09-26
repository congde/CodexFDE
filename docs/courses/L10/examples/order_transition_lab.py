"""Check real order transitions in a temporary database via the unified Harness.

refuse-all deliberately rejects a legal operation to illustrate a bad repair.
No Codex invocation and no mutation of a personal database.
"""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from workbench.course_experiments import ensure_product_process
ensure_product_process(__file__)
from flowerp.models import InvalidTransition, OrderLine
from flowerp.service import ERPService
from flowerp.store import ERPStore
from eval import harness


def check(mode: str, state_path: str | Path | None = None) -> str:
    with tempfile.TemporaryDirectory(prefix="l10-order-") as folder:
        store = ERPStore(Path(folder) / "orders.db")
        service = ERPService(store)
        service.add_product("A", "商品 A", 100)
        service.receive_stock("A", 10, "opening")
        service.create_order("其他客户", [OrderLine("A", 2, 100)], "OTHER")
        service.reserve_order("OTHER")
        other = service.order("OTHER")
        service.create_order("目标客户", [OrderLine("A", 4, 100)], "TARGET")
        if mode != "draft-ship":
            service.reserve_order("TARGET")
        if mode == "cancelled-ship":
            service.cancel_order("TARGET")
        if mode == "double-ship":
            service.ship_order("TARGET")
        def snapshot():
            return {table: store.rows(f"SELECT * FROM {table} ORDER BY 1") for table in
                    ("stock", "sales_orders", "sales_order_lines", "inventory_events")}
        before = snapshot()
        try:
            if mode in {"draft-ship", "cancelled-ship", "double-ship"}:
                try:
                    service.ship_order("TARGET")
                except InvalidTransition:
                    pass
                else:
                    raise AssertionError("illegal transition was accepted")
                assert snapshot() == before, "rejected operation changed business state"
                return f"{mode}: rejected; four tables unchanged; OTHER preserved"
            if mode == "refuse-all":
                # This is a deliberately bad teaching repair. The legal-path check must fail.
                raise InvalidTransition("TEACHING defect: all shipping rejected, including reserved")
            result = service.ship_order("TARGET")
            product = service.product("A")
            assert result["status"] == "shipped"
            assert (product["on_hand"], product["reserved"], product["available"]) == (6, 2, 4)
            assert service.order("OTHER") == other
            events = store.rows("SELECT * FROM inventory_events WHERE reference='TARGET' AND event_type='ship'")
            assert len(events) == 1 and (events[0]["quantity"], events[0]["reserved_delta"]) == (-4, -4)
            return "legal: shipped; on_hand/reserved/available=6/2/4; OTHER preserved; one ship event"
        finally:
            if state_path is not None:
                target = Path(state_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8") as stream:
                    json.dump({"mode": mode, "before": before, "after": snapshot(),
                               "other_before": other, "other_after": service.order("OTHER")},
                              stream, ensure_ascii=False, indent=2)



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["legal", "draft-ship", "cancelled-ship", "double-ship", "refuse-all"])
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--state-path", help="optional new path for actual before/after tables")
    args = parser.parse_args()
    if Path(args.report_path).exists():
        parser.error("use a new path; existing reports must be preserved")
    with patch.object(harness, "EVALS", [("l10_teaching_transition", "blocking", lambda: check(args.mode, args.state_path))]), \
         patch.object(sys, "argv", ["harness", "--suite", "blocking", "--case", "l10_teaching_transition",
                                    "--report-path", args.report_path]):
        raise SystemExit(harness.main())
