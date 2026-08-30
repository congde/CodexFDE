from __future__ import annotations

import re


def plan_turn_tools(task: dict, *, turn: int) -> list[dict]:
    """Deterministic per-turn tool schedule aligned with dsh agent planning."""
    planned: list[dict] = [{"tool_id": "spec.read", "args": {}}]
    if turn == 1:
        planned.append({"tool_id": "workspace.list", "args": {"path": "."}})
        requirement = str(task.get("requirement_id") or "").strip()
        request = str(task.get("request") or "").strip()
        pattern = requirement or request[:48]
        if pattern:
            safe = re.escape(pattern[:48])
            planned.append({
                "tool_id": "workspace.grep",
                "args": {"pattern": safe, "path": ".", "limit": 15},
            })
    if task.get("execution_mode") == "codex":
        planned.append({"tool_id": "codex.exec", "args": {}})
    planned.append({"tool_id": "eval.blocking", "args": {}})
    return planned


def paths_from_grep_result(result: dict, *, limit: int = 3) -> list[str]:
    """Pick unique file paths from workspace.grep matches for follow-up reads."""
    matches = result.get("matches") if isinstance(result, dict) else None
    if not isinstance(matches, list):
        return []
    seen: list[str] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").replace("\\", "/").strip().lstrip("/")
        if not path or ".." in path.split("/") or path in seen:
            continue
        if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".db", ".sqlite", ".sqlite3")):
            continue
        seen.append(path)
        if len(seen) >= limit:
            break
    return seen


def expand_plan_after_tool(
    remaining: list[dict],
    *,
    tool_id: str,
    result: dict,
    max_reads: int = 3,
) -> list[dict]:
    """Adaptively insert follow-up tools based on prior tool evidence."""
    if tool_id != "workspace.grep":
        return remaining
    paths = paths_from_grep_result(result, limit=max_reads)
    if not paths:
        return remaining
    already = {
        str((item.get("args") or {}).get("path") or "")
        for item in remaining
        if item.get("tool_id") == "workspace.read"
    }
    inserts = [
        {"tool_id": "workspace.read", "args": {"path": path}, "adaptive": True}
        for path in paths
        if path not in already
    ]
    if not inserts:
        return remaining
    # Insert reads before execution / eval so context is gathered first.
    insert_at = 0
    for index, item in enumerate(remaining):
        if item.get("tool_id") in {"codex.exec", "eval.blocking"}:
            insert_at = index
            break
        insert_at = index + 1
    return remaining[:insert_at] + inserts + remaining[insert_at:]
