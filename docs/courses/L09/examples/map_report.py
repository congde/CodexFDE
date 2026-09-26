"""Produce a reference draft from an explicitly selected Harness report.

This teaching helper adds input checks and provenance to the reference mapper.
It neither authorizes file changes nor invokes an executor.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from agent.repair import build_repair_task
from eval.report_contract import validate_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--case", action="append", required=True)
    parser.add_argument("--observed-exit", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("output already exists; choose a new path")
        raw = args.report.read_bytes()
        report = json.loads(raw)
        validate_report(report, tuple(args.case), args.observed_exit)
        for row in report["results"]:
            if not isinstance(row.get("evidence"), str):
                raise ValueError("evidence must be present and a string")
        if report["summary"]["blocking_failed"] == 0:
            print(json.dumps({"status": "no_blocking_repair", "task_written": False}))
            return 0
        # Use exclusive creation to preserve prior evidence even if the path appears meanwhile.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="l09-map-") as folder:
            task = build_repair_task(report, Path(folder) / "draft.json")
        task["source"] = {"report": str(args.report.resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
                          "observed_exit_supplied_by_caller": args.observed_exit,
                          "expected_cases_supplied_by_caller": args.case}
        task["draft_status"] = "reference example; file authorization and candidate identity require review"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(task, stream, ensure_ascii=False, indent=2)
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
