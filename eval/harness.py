from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import cases


@dataclass
class EvalResult:
    name: str
    level: str
    passed: bool
    duration_ms: int
    evidence: str


EVALS: list[tuple[str, str, Callable[[], str]]] = [
    ("stock_never_negative", "blocking", cases.stock_never_negative),
    ("receiving_is_idempotent", "blocking", cases.receiving_is_idempotent),
    ("cancellation_releases_reservation", "blocking", cases.cancellation_releases_reservation),
    ("illegal_transition_is_blocked", "blocking", cases.illegal_transition_is_blocked),
    ("purchase_requires_approval", "blocking", cases.purchase_requires_approval),
    ("order_total_matches_lines", "blocking", cases.order_total_matches_lines),
    ("ecommerce_channel_order_is_idempotent_and_guarded", "blocking", cases.ecommerce_channel_order_is_idempotent_and_guarded),
    ("production_schema_invariants", "blocking", cases.production_schema_invariants),
    ("multi_location_transfer_conserves_stock", "blocking", cases.multi_location_transfer_conserves_stock),
    ("stale_stock_count_is_blocked", "blocking", cases.stale_stock_count_is_blocked),
    ("sales_credit_and_atomic_reservation", "blocking", cases.sales_credit_and_atomic_reservation),
    ("backup_is_restorable", "blocking", cases.backup_is_restorable),
    ("purchase_invoice_three_way_match", "blocking", cases.purchase_invoice_three_way_match),
    ("double_entry_fifo_and_subledger_reconciliation", "blocking", cases.double_entry_fifo_and_subledger_reconciliation),
    ("bank_statement_control_and_reconciliation", "blocking", cases.bank_statement_control_and_reconciliation),
    ("delivery_evidence_and_review_controls", "blocking", cases.delivery_evidence_and_review_controls),
    ("no_committed_secrets", "blocking", cases.no_committed_secrets),
    ("course_assets_present", "observing", cases.course_assets_present),
]


def run_suite(suite: str = "all", write_report: bool = True) -> dict:
    if suite not in {"all", "blocking", "observing"}: raise ValueError(f"未知 suite：{suite}")
    selected = [item for item in EVALS if suite == "all" or item[1] == suite]
    results: list[EvalResult] = []
    for name, level, fn in selected:
        start = time.perf_counter()
        try:
            evidence = fn(); passed = True
        except Exception as exc:
            evidence = f"{type(exc).__name__}: {exc}"; passed = False
        results.append(EvalResult(name, level, passed, round((time.perf_counter() - start) * 1000), evidence))
    blocking_failed = sum(not r.passed and r.level == "blocking" for r in results)
    observing_failed = sum(not r.passed and r.level == "observing" for r in results)
    report = {
        "schema_version": "1.0",
        "suite": suite,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"total": len(results), "passed": sum(r.passed for r in results), "blocking_failed": blocking_failed, "observing_failed": observing_failed, "decision": "pass" if blocking_failed == 0 else "block"},
        "results": [asdict(r) for r in results],
    }
    if write_report:
        output = Path(".runtime/reports"); output.mkdir(parents=True, exist_ok=True)
        (output / f"harness-{suite}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified FlowERP evaluation harness")
    parser.add_argument("--suite", choices=("all", "blocking", "observing"), default="all")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(); report = run_suite(args.suite, not args.no_report)
    for result in report["results"]:
        mark = "PASS" if result["passed"] else ("BLOCK" if result["level"] == "blocking" else "WARN")
        print(f"[{mark:5}] {result['name']:<36} {result['duration_ms']:>5} ms  {result['evidence']}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 1 if report["summary"]["blocking_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
