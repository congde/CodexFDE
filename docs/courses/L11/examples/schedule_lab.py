"""Observe the real declaration checker; does not launch agents or enforce writes."""
from __future__ import annotations
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from agent.schedule import Subtask, assert_parallel_safe, conflict_pairs

SCENARIOS = {
    "independent": (Subtask("tests", ("tests/test_l11_purchase.py",), ("flowerp/service.py",)),
                    Subtask("risk", (), ("flowerp/service.py", "flowerp/store.py"))),
    "same-write": (Subtask("a", ("flowerp/service.py",)), Subtask("b", ("flowerp/service.py",))),
    "read-write": (Subtask("implementation", ("flowerp/service.py",)),
                   Subtask("tests", ("tests/test_l11_purchase.py",), ("flowerp/service.py",))),
    "directory": (Subtask("implementation", ("flowerp/",)), Subtask("risk", (), ("flowerp/service.py",))),
    "path-alias": (Subtask("a", ("./flowerp/service.py",)), Subtask("b", ("FLOWERP\\SERVICE.PY",))),
    "shared-report": (Subtask("a", ("tests/test_a.py", ".runtime/eval.json")),
                      Subtask("b", ("tests/test_b.py", ".runtime/eval.json"))),
    "undeclared-read": (Subtask("implementation", ("flowerp/service.py",)),
                        Subtask("tests", ("tests/test_l11_purchase.py",))),
    "read-only": (Subtask("coverage", (), ("flowerp/service.py",)),
                  Subtask("risk", (), ("flowerp/service.py",))),
    "invalid-path": (Subtask("a", ("../outside.py",)),),
    "duplicate-name": (Subtask("same", ()), Subtask("same", ())),
}
EXPECTED = {"independent": "allowed", "read-only": "allowed", "undeclared-read": "allowed"}

def observe(mode):
    tasks = SCENARIOS[mode]
    result = {"mode": mode, "tasks": [asdict(t) for t in tasks],
              "boundary": "Declared paths only; no agent execution, file lock, or semantic proof."}
    try:
        result["pairs"] = conflict_pairs(tasks)
        result["decision"] = assert_parallel_safe(tasks)
        result["observed"] = "allowed"
    except ValueError as exc:
        result["observed"] = "rejected"
        result["error"] = str(exc)
    assert result["observed"] == EXPECTED.get(mode, "rejected"), result
    if mode == "undeclared-read":
        result["judgment"] = "False independence: the test's actual service read was omitted. Correct the declaration."
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=SCENARIOS)
    args = parser.parse_args()
    print(json.dumps(observe(args.mode), ensure_ascii=False, indent=2))
