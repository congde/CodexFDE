from __future__ import annotations

import json
from pathlib import Path

from .agent_roster import list_employees
from .runtime_store import HarnessRuntimeStore
from .session_context import derive_messages


def export_session_bundle(
    runtime: HarnessRuntimeStore,
    session_id: str,
    *,
    task: dict | None = None,
    delivery_view: dict | None = None,
    repository_root: str | Path | None = None,
) -> dict:
    session = runtime.get_session(session_id)
    events = session.get("events") or []
    messages = derive_messages(session)
    tool_events = [event for event in events if event.get("kind", "").startswith(("tool/", "tools/"))]
    turn_events = [event for event in events if event.get("kind", "").startswith(("turn/", "step/", "agent/"))]
    assistant_events = [event for event in events if event.get("kind", "").startswith("assistant/")]
    agent_actors = sorted({
        str(event.get("actor"))
        for event in events
        if str(event.get("actor") or "").startswith("agent:")
    })
    critiques = [event for event in events if event.get("kind") == "agent/critique"]
    actor_counts: dict[str, int] = {}
    for event in events:
        actor = str(event.get("actor") or "")
        if not actor:
            continue
        actor_counts[actor] = actor_counts.get(actor, 0) + 1
    return {
        "schema": "harness.session.export/v1",
        "session": {
            "id": session.get("id"),
            "status": session.get("status"),
            "profile_id": session.get("profile_id"),
            "project_id": session.get("project_id"),
            "task_id": session.get("task_id"),
            "title": session.get("title"),
        },
        "task": task,
        "delivery_view": delivery_view,
        "repository_root": str(repository_root) if repository_root else None,
        "opc": {
            "mode": "super_individual",
            "multi_user_accounts": False,
            "employees": list_employees(),
            "agent_actors_seen": agent_actors,
            "actor_counts": actor_counts,
            "critiques": [
                {
                    "sequence": event.get("sequence"),
                    "actor": event.get("actor"),
                    "payload": event.get("payload"),
                }
                for event in critiques
            ],
        },
        "messages": messages,
        "counts": {
            "events": len(events),
            "tool_events": len(tool_events),
            "turn_events": len(turn_events),
            "assistant_events": len(assistant_events),
            "agent_actors": len(agent_actors),
            "critiques": len(critiques),
        },
        "events": events,
    }


def write_session_export(bundle: dict, output: str | Path) -> Path:
    target = Path(output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
