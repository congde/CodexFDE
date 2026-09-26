"""Observe the real Loop using labelled synthetic reports and an executor substitute.

No Codex calls or business changes. Synthetic usage is not measured model usage.
The wrapper exits zero when the expected observation is confirmed.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from agent import loop

MODES = ["already-green", "converge", "repeated", "changed-reason", "oscillating",
         "last-repair", "token-budget", "missing-usage", "time-budget", "executor-error",
         "empty-report", "dry-run"]


def observe(mode: str) -> dict:
    checks: list[dict] = []
    calls: list[dict] = []
    repaired = False

    def runner(suite, write_report):
        if mode == "empty-report":
            report = {"results": [], "summary": {"decision": "pass"}}
            checks.append(report)
            return report
        passed = mode == "already-green" or (mode in {"converge", "last-repair"} and repaired)
        name = "TEACHING_A" if mode != "oscillating" or len(checks) % 2 == 0 else "TEACHING_B"
        reason = f"synthetic reason {len(checks)}" if mode == "changed-reason" else "synthetic fixed observation"
        report = {"results": [{"name": name, "level": "blocking", "passed": passed, "evidence": reason}],
                  "summary": {"decision": "pass" if passed else "block"}}
        checks.append(report)
        return report

    def executor(task, round_no, timeout):
        nonlocal repaired
        calls.append({"round": round_no, "scope": task["scope"], "timeout": timeout})
        if mode == "executor-error":
            raise subprocess.TimeoutExpired("TEACHING executor substitute", timeout)
        repaired = True
        tokens = 0 if mode == "missing-usage" else 150 if mode == "token-budget" else 10
        return {"returncode": 0, "usage": {"total_tokens": tokens}, "source": "synthetic executor"}

    with tempfile.TemporaryDirectory(prefix="l10-control-") as folder:
        try:
            # Only the time-boundary experiment uses a fake clock; no wall-clock sleep.
            clock = patch.object(loop.time, "monotonic", side_effect=[0.0, 2.0]) if mode == "time-budget" else patch.object(loop, "time", loop.time)
            with clock:
                result = loop.run_loop(max_rounds=1 if mode == "last-repair" else 3,
                    token_budget=100, timeout_seconds=1 if mode == "time-budget" else 90,
                    use_codex=mode != "dry-run", suite_runner=runner, executor=executor,
                    runtime_dir=folder)
        except subprocess.TimeoutExpired as exc:
            assert mode == "executor-error"
            assert len(checks) == 1 and len(calls) == 1
            return {"mode": mode, "source": "synthetic inputs to real Loop", "exception": type(exc).__name__,
                    "structured_loop_result": False, "task_files": len(list(Path(folder).glob('*.json'))),
                    "checks": checks, "executor_calls": calls, "real_codex": False}
        expected = {
            "already-green": ("converged", 1, 0), "converge": ("converged", 2, 1),
            "repeated": ("stopped_no_progress", 2, 1), "changed-reason": ("stopped_no_progress", 2, 1),
            "oscillating": ("stopped_max_rounds", 3, 3), "last-repair": ("stopped_max_rounds", 1, 1),
            "token-budget": ("stopped_token_budget", 1, 1), "missing-usage": ("stopped_token_usage_unavailable", 1, 1),
            "time-budget": ("stopped_time_budget", 1, 0), "empty-report": ("converged", 1, 0),
            "dry-run": ("stopped_no_progress", 2, 0),
        }[mode]
        assert (result["status"], len(checks), len(calls)) == expected
        if mode == "token-budget":
            assert result["tokens_used"] == 150 and result["tokens_remaining"] == 0
        if mode == "last-repair":
            assert repaired and result["remaining_failures"] == ["TEACHING_A"]
        return {"mode": mode, "source": "synthetic inputs to real Loop", "result": result,
                "checks": checks, "executor_calls": calls, "substitute_changed_state": repaired,
                "real_codex": False, "usage_is_synthetic": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES)
    print(json.dumps(observe(parser.parse_args().mode), ensure_ascii=False, indent=2))
