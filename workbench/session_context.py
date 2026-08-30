from __future__ import annotations


def derive_messages(session: dict) -> list[dict]:
    """Project model-visible history from the append-only Session event log."""
    messages: list[dict] = []
    chunk_buffer: dict[int, list[str]] = {}
    for event in session.get("events") or []:
        kind = str(event.get("kind") or "")
        payload = event.get("payload") or {}
        if kind == "user/message":
            messages.append({"role": "user", "kind": kind, "content": payload.get("request") or payload})
        elif kind == "assistant/chunk":
            turn = int(payload.get("turn", 0))
            chunk_buffer.setdefault(turn, [])
            chunk_buffer[turn].append(str(payload.get("text") or ""))
        elif kind == "assistant/message":
            turn = int(payload.get("turn", 0))
            content = payload.get("content") or "".join(chunk_buffer.get(turn, []))
            messages.append({"role": "assistant", "kind": kind, "turn": turn, "content": content})
        elif kind == "tool/call":
            messages.append({
                "role": "assistant",
                "kind": kind,
                "tool_call": {
                    "id": payload.get("call_id"),
                    "name": payload.get("tool_id"),
                    "arguments": payload.get("args") or {},
                },
            })
        elif kind == "tool/result":
            messages.append({
                "role": "tool",
                "kind": kind,
                "tool_call_id": payload.get("call_id"),
                "is_error": bool(payload.get("isError")),
                "content": payload.get("summary") or payload,
            })
        elif kind == "agent/plan":
            messages.append({
                "role": "system",
                "kind": kind,
                "turn": payload.get("turn"),
                "step": payload.get("step"),
                "tools": payload.get("tools"),
                "content": _plan_content(payload),
            })
        elif kind == "pipeline/stage":
            messages.append({
                "role": "system",
                "kind": kind,
                "stage_id": payload.get("stage_id"),
                "task_status": payload.get("task_status"),
                "title": payload.get("title"),
                "content": _pipeline_content(payload),
                "visible_to_model": True,
            })
        elif kind == "pipeline/context":
            messages.append({
                "role": "system",
                "kind": kind,
                "content": payload.get("content") or payload,
                "visible_to_model": True,
            })
        elif kind == "agent/pre-step":
            prompt = payload.get("prompt") or {}
            text = prompt.get("text") if isinstance(prompt, dict) else None
            if text:
                messages.append({
                    "role": "system",
                    "kind": kind,
                    "action": payload.get("action"),
                    "content": text,
                    "visible_to_model": True,
                })
        elif kind == "agent/critique":
            messages.append({
                "role": "system",
                "kind": kind,
                "content": _critique_content(payload),
                "suggested_decision": payload.get("suggested_decision"),
                "reviewer_id": payload.get("reviewer_id"),
                "visible_to_model": True,
            })
        elif kind in {"turn/start", "turn/end", "step/start", "step/end"}:
            messages.append({"role": "system", "kind": kind, **payload})
        elif kind == "agent/stopped":
            messages.append({
                "role": "system",
                "kind": kind,
                "content": _stopped_content(payload),
                **payload,
            })
    return messages


def _pipeline_content(payload: dict) -> str:
    title = payload.get("title") or payload.get("task_status") or "pipeline"
    summary = payload.get("summary") or ""
    hint = payload.get("model_hint") or ""
    detail = payload.get("detail") or ""
    parts = [f"[流水线 · {title}] {summary}".strip()]
    if hint:
        parts.append(f"给模型的提示: {hint}")
    if detail:
        parts.append(str(detail))
    return "\n".join(parts)


def _plan_content(payload: dict) -> str:
    tools = payload.get("tools") or []
    names = ", ".join(
        str(item.get("tool_id") or item) if isinstance(item, dict) else str(item)
        for item in tools
    )
    return f"Turn {payload.get('turn')} plan tools: [{names}]"


def _stopped_content(payload: dict) -> str:
    reason = payload.get("reason") or "stopped"
    failures = payload.get("failures") or []
    status = payload.get("status")
    parts = [f"Agent 停止: {reason}"]
    if status:
        parts.append(f"status={status}")
    if failures:
        parts.append("failures=" + ", ".join(str(item) for item in failures))
    return " · ".join(parts)


def _critique_content(payload: dict) -> str:
    name = payload.get("reviewer_name") or payload.get("reviewer_id") or "质检员"
    suggestion = payload.get("suggested_decision") or "—"
    note = payload.get("note") or ""
    return f"[质检员 · {name}] 建议={suggestion}\n{note}".strip()

