"""Persist observed execution facts for independently supplied course checks."""
import json
from pathlib import Path


def save(store, task_id, attempt_id, result, diff):
    invocation = result["invocation"]
    changes = diff["changed"]
    receipt = {
        "schema": "personal.execution-receipt/v1",
        "mode": "codex_exec", "task_id": task_id, "attempt_id": attempt_id,
        "invocation": {**invocation, "workspace": invocation["cwd"]},
        "returncode": result["exitcode"],
        "success": result["exitcode"] == 0 and not result["timed_out"] and not result.get("error"),
        "changed_files": changes,
        "change_manifest": [{"path": name, "before_sha256": diff["before"].get(name),
                             "after_sha256": diff["after"].get(name)} for name in changes],
        "violations": diff["violations"], "sensitive": diff["sensitive"],
        "human_review": "pending_human_review",
    }
    path = store.runtime / "delivery" / task_id / attempt_id / "evidence.json"
    path.parent.mkdir(parents=True, exist_ok=False)
    with path.open("x", encoding="utf8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    return {**receipt, "receipt_path": str(path)}
