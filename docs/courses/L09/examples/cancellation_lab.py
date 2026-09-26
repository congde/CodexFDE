"""Exercise ERPService cancellation in a temporary database through real Harness.

leak is an explicitly injected teaching defect, not a discovered product incident.
No Codex, remote service, or personal runtime database is used.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
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


def check(mode: str, state_path: Path | None = None) -> str:
    with tempfile.TemporaryDirectory(prefix="l09-cancel-") as folder:
        store = ERPStore(Path(folder) / "orders.db")
        service = ERPService(store)
        for sku, quantity in [("A", 10), ("B", 8)]:
            service.add_product(sku, sku, 100)
            service.receive_stock(sku, quantity, "opening-" + sku)
        service.create_order("目标客户", [OrderLine("A", 4, 100), OrderLine("B", 3, 100)], "TARGET")
        service.create_order("其他客户", [OrderLine("A", 2, 100), OrderLine("B", 1, 100)], "OTHER")
        service.reserve_order("OTHER")
        if mode != "draft":
            service.reserve_order("TARGET")

        def snapshot():
            return {table: store.rows(f"SELECT * FROM {table} ORDER BY 1") for table in
                    ("stock", "sales_orders", "sales_order_lines", "inventory_events")}

        if mode == "shipped":
            service.ship_order("TARGET")
        before = snapshot()
        other = service.order("OTHER")
        def record_state():
            if state_path is None:
                return
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with state_path.open("x", encoding="utf-8") as stream:
                json.dump({"mode": mode, "before": before, "after": snapshot(),
                           "other_before": other, "other_after": service.order("OTHER"),
                           "teaching_defect": mode == "leak"}, stream, ensure_ascii=False, indent=2)
        if mode == "write-error":
            with store.connect() as conn:
                conn.execute("""CREATE TRIGGER teaching_fail_release BEFORE INSERT ON inventory_events
                    WHEN NEW.event_type='release' AND NEW.sku='B'
                    BEGIN SELECT RAISE(ABORT, 'L09 teaching second release failure'); END""")
        if mode in {"shipped", "write-error"}:
            expected_error = InvalidTransition if mode == "shipped" else sqlite3.IntegrityError
            try:
                service.cancel_order("TARGET")
            except expected_error:
                pass
            else:
                raise AssertionError("expected cancellation rejection")
            record_state()
            assert snapshot() == before, "failed cancellation left partial business state"
            return f"{mode}: rejection and all four tables unchanged; real temp database"
        if mode == "leak":
            # Deliberately incomplete cancellation, confined to this temporary database.
            with store.connect() as conn:
                conn.execute("UPDATE sales_orders SET status='cancelled' WHERE id='TARGET'")
        else:
            service.cancel_order("TARGET")
        actual = [(row["sku"], row["on_hand"], row["reserved"])
                  for row in store.rows("SELECT * FROM stock ORDER BY sku")]
        record_state()
        assert actual == [("A", 10, 2), ("B", 8, 1)], f"expected only OTHER reservations; observed {actual}"
        assert service.order("TARGET")["status"] == "cancelled"
        assert service.order("OTHER") == other
        releases = store.rows("SELECT * FROM inventory_events WHERE event_type='release' AND reference='TARGET' ORDER BY sku")
        expected = [] if mode == "draft" else [("A", 0, -4), ("B", 0, -3)]
        assert [(r["sku"], r["quantity"], r["reserved_delta"]) for r in releases] == expected
        if mode == "repeat":
            once = snapshot()
            try:
                service.cancel_order("TARGET")
            except InvalidTransition:
                pass
            else:
                raise AssertionError("reference contract rejects repeated cancellation")
            assert snapshot() == once
        return f"{mode}: cancelled; on_hand A/B=10/8, reserved=2/1, available=8/7; OTHER unchanged"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["normal", "draft", "repeat", "shipped", "write-error", "leak"])
    parser.add_argument("--report-path", required=True, help="Use a new report path for each observation")
    parser.add_argument("--state-path", type=Path, help="Optional new path for full before/after states")
    args = parser.parse_args()
    if Path(args.report_path).exists():
        parser.error("report path already exists; preserve it and choose a new path")
    if args.state_path and args.state_path.exists():
        parser.error("state path already exists; preserve it and choose a new path")
    with patch.object(harness, "EVALS", [("l09_teaching_cancellation", "blocking", lambda: check(args.mode, args.state_path))]), \
         patch.object(sys, "argv", ["harness", "--suite", "blocking", "--case", "l09_teaching_cancellation",
                                   "--report-path", args.report_path]):
        result = harness.main()
    print(json.dumps({"mode": args.mode, "teaching_defect": args.mode == "leak",
                      "real_codex_execution": False, "harness_exit": result}, ensure_ascii=False))
    raise SystemExit(result)
