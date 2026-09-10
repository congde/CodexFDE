from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import cases

PROJECT_CASES = frozenset({'illegal_transition_is_blocked', 'purchase_requires_approval', 'stale_stock_count_is_blocked', 'payable_aging_tracks_open_supplier_exposure', 'purchase_request_preserves_reason', 'production_schema_invariants', 'order_total_matches_lines', 'stock_never_negative', 'ecommerce_channel_order_is_idempotent_and_guarded', 'backup_is_restorable', 'bank_statement_control_and_reconciliation', 'channel_callback_lease_is_exclusive_and_bounded', 'multi_location_transfer_conserves_stock', 'inventory_export_is_stable', 'cancellation_releases_reservation', 'receiving_is_idempotent', 'double_entry_fifo_and_subledger_reconciliation', 'purchase_invoice_three_way_match', 'sales_credit_and_atomic_reservation'})


@dataclass
class EvalResult:
    name: str
    level: str
    passed: bool
    duration_ms: int
    evidence: str
    error: dict[str, str] | None = None


EVALS: list[tuple[str, str, Callable[[], str]]] = [
    ("spec_contract_rejects_ambiguity", "blocking", cases.spec_contract_rejects_ambiguity),
    ("isolated_report_contract_is_honest", "blocking", cases.isolated_report_contract_is_honest),
    ("bootstrap_evidence_is_honest", "blocking", cases.bootstrap_evidence_is_honest),
    ("inventory_export_is_stable", "blocking", cases.inventory_export_is_stable),
    ("stock_never_negative", "blocking", cases.stock_never_negative),
    ("receiving_is_idempotent", "blocking", cases.receiving_is_idempotent),
    ("cancellation_releases_reservation", "blocking", cases.cancellation_releases_reservation),
    ("illegal_transition_is_blocked", "blocking", cases.illegal_transition_is_blocked),
    ("purchase_requires_approval", "blocking", cases.purchase_requires_approval),
    ("purchase_request_preserves_reason", "blocking", cases.purchase_request_preserves_reason),
    ("order_total_matches_lines", "blocking", cases.order_total_matches_lines),
    ("ecommerce_channel_order_is_idempotent_and_guarded", "blocking", cases.ecommerce_channel_order_is_idempotent_and_guarded),
    ("channel_callback_lease_is_exclusive_and_bounded", "blocking", cases.channel_callback_lease_is_exclusive_and_bounded),
    ("production_schema_invariants", "blocking", cases.production_schema_invariants),
    ("multi_location_transfer_conserves_stock", "blocking", cases.multi_location_transfer_conserves_stock),
    ("stale_stock_count_is_blocked", "blocking", cases.stale_stock_count_is_blocked),
    ("sales_credit_and_atomic_reservation", "blocking", cases.sales_credit_and_atomic_reservation),
    ("backup_is_restorable", "blocking", cases.backup_is_restorable),
    ("purchase_invoice_three_way_match", "blocking", cases.purchase_invoice_three_way_match),
    ("payable_aging_tracks_open_supplier_exposure", "blocking", cases.payable_aging_tracks_open_supplier_exposure),
    ("double_entry_fifo_and_subledger_reconciliation", "blocking", cases.double_entry_fifo_and_subledger_reconciliation),
    ("bank_statement_control_and_reconciliation", "blocking", cases.bank_statement_control_and_reconciliation),
    ("plugin_lifecycle_is_reversible", "blocking", cases.plugin_lifecycle_is_reversible),
    ("delivery_evidence_and_review_controls", "blocking", cases.delivery_evidence_and_review_controls),
    ("web_api_and_persistence_projection_agree", "blocking", cases.web_api_and_persistence_projection_agree),
    ("ci_evidence_envelope_is_honest", "blocking", cases.ci_evidence_envelope_is_honest),
    ("write_sets_reject_conflict", "blocking", cases.write_sets_reject_conflict),
    ("raw_feedback_cannot_become_blocking", "blocking", cases.raw_feedback_cannot_become_blocking),
    ("no_committed_secrets", "blocking", cases.no_committed_secrets),
    ("course_assets_present", "observing", cases.course_assets_present),
    ("ecommerce_lineage_is_declared", "observing", cases.ecommerce_lineage_is_declared),
]


def run_suite(suite: str = "all", write_report: bool = True,
              case_names: list[str] | tuple[str, ...] | None = None,
              report_path: str | Path | None = None) -> dict:
    if suite not in {"all", "blocking", "observing"}: raise ValueError(f"未知 suite：{suite}")
    available = {name for name, _level, _fn in EVALS}
    unknown = sorted(set(case_names or ()) - available)
    if unknown: raise ValueError(f"未知 Eval：{', '.join(unknown)}")
    selected = [item for item in EVALS if suite == "all" or item[1] == suite]
    if not case_names:
        selected = [item for item in selected if item[0] not in PROJECT_CASES]
    if case_names:
        requested = set(case_names)
        selected = [item for item in selected if item[0] in requested]
        if omitted := requested - {name for name, _level, _fn in selected}:
            raise ValueError("请求的 Eval 不属于当前 suite：" + ", ".join(sorted(omitted)))
    if not selected:
        raise ValueError("当前 suite 没有可执行用例，不能返回空绿灯")
    results: list[EvalResult] = []
    for name, level, fn in selected:
        start = time.perf_counter()
        error = None
        try:
            evidence = fn(); passed = True
        except Exception as exc:
            evidence = f"{type(exc).__name__}: {exc}"; passed = False
            error = {"type": type(exc).__name__, "message": str(exc)}
        results.append(EvalResult(name, level, passed, round((time.perf_counter() - start) * 1000), evidence, error))
    blocking_failed = sum(not r.passed and r.level == "blocking" for r in results)
    observing_failed = sum(not r.passed and r.level == "observing" for r in results)
    report = {
        "schema_version": "1.0",
        "suite": suite,
        "requested_cases": list(dict.fromkeys(case_names or ())),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"total": len(results), "passed": sum(r.passed for r in results), "blocking_failed": blocking_failed, "observing_failed": observing_failed, "decision": "pass" if blocking_failed == 0 else "block"},
        "results": [asdict(r) for r in results],
    }
    if write_report:
        target = Path(report_path) if report_path else Path(".runtime/reports") / f"harness-{suite}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified FlowERP evaluation harness")
    parser.add_argument("--suite", choices=("all", "blocking", "observing"), default="all")
    parser.add_argument("--case", action="append", default=[], help="只运行指定 Eval，可重复")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--report-path", help="将完整 JSON 报告写入指定路径")
    args = parser.parse_args()
    try:
        report = run_suite(args.suite, not args.no_report, args.case or None, args.report_path)
    except ValueError as exc:
        parser.error(str(exc))
    for result in report["results"]:
        mark = "PASS" if result["passed"] else ("BLOCK" if result["level"] == "blocking" else "WARN")
        print(f"[{mark:5}] {result['name']:<36} {result['duration_ms']:>5} ms  {result['evidence']}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 1 if report["summary"]["blocking_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
