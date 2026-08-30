from __future__ import annotations


NODE_ORDER = (
    "queued",
    "spec_ready",
    "executing",
    "evaluating",
    "review",
    "rework",
    "completed",
    "failed",
    "dead_letter",
)


def session_delivery_graph(session: dict, task: dict | None = None) -> dict:
    """Project a delivery state graph from Session event log + optional Task."""
    events = session.get("events") or []
    nodes: dict[str, dict] = {
        name: {"id": name, "visited": False, "count": 0, "last_at": None}
        for name in NODE_ORDER
    }
    edges: list[dict] = []
    turns: list[dict] = []
    tools: list[dict] = []
    current = "queued"
    if task and task.get("status"):
        current = str(task["status"])

    previous_status = None
    for event in events:
        kind = str(event.get("kind") or "")
        payload = event.get("payload") or {}
        created = event.get("created_at")

        if kind == "task/status" or (kind == "task/event" and payload.get("to_status")):
            status = str(payload.get("to_status") or "")
            if status in nodes:
                nodes[status]["visited"] = True
                nodes[status]["count"] += 1
                nodes[status]["last_at"] = created
                if previous_status and previous_status != status:
                    edges.append({
                        "from": previous_status,
                        "to": status,
                        "at": created,
                        "actor": event.get("actor"),
                    })
                previous_status = status
                current = status

        if kind == "turn/start":
            turns.append({
                "turn": payload.get("turn"),
                "started_at": created,
                "status": None,
                "tools": [],
            })
        elif kind == "turn/end" and turns:
            turns[-1]["status"] = payload.get("status")
            turns[-1]["ended_at"] = created
            turns[-1]["failures"] = payload.get("failures") or []
        elif kind == "step/start" and turns:
            turns[-1]["tools"].append(payload.get("tool") or payload.get("tool_id"))
        elif kind == "tool/call":
            tools.append({
                "tool_id": payload.get("tool_id"),
                "call_id": payload.get("call_id"),
                "at": created,
            })
        elif kind == "agent/stopped":
            nodes.setdefault("stopped", {"id": "stopped", "visited": True, "count": 1, "last_at": created})
            nodes["stopped"]["visited"] = True

    if current in nodes:
        nodes[current]["visited"] = True

    stop_event = next((e for e in reversed(events) if e.get("kind") == "agent/stopped"), None)
    return {
        "schema": "harness.session.graph/v1",
        "session_id": session.get("id"),
        "task_id": session.get("task_id") or (task or {}).get("id"),
        "current": current,
        "nodes": [nodes[name] for name in NODE_ORDER if name in nodes] + (
            [nodes["stopped"]] if "stopped" in nodes else []
        ),
        "edges": edges,
        "turns": turns,
        "tools": tools[-30:],
        "stopped": stop_event.get("payload") if stop_event else None,
    }


def format_session_graph(graph: dict) -> str:
    lines = [
        f"Session Graph · {graph.get('session_id', '—')}",
        f"task      : {graph.get('task_id') or '—'}",
        f"current   : {graph.get('current')}",
    ]
    if graph.get("stopped"):
        lines.append(f"stopped   : {graph['stopped']}")
    lines.append("nodes:")
    for node in graph.get("nodes") or []:
        mark = "●" if node.get("visited") else "○"
        current = " ←" if node.get("id") == graph.get("current") else ""
        lines.append(f"  {mark} {node['id']} (n={node.get('count', 0)}){current}")
    edges = graph.get("edges") or []
    if edges:
        lines.append("edges:")
        for edge in edges[-12:]:
            lines.append(f"  {edge.get('from')} → {edge.get('to')}")
    turns = graph.get("turns") or []
    if turns:
        lines.append("turns:")
        for turn in turns:
            tools = ", ".join(str(t) for t in (turn.get("tools") or []) if t)
            lines.append(
                f"  #{turn.get('turn')} status={turn.get('status') or '—'} tools=[{tools}]"
            )
    return "\n".join(lines)
