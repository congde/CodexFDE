from __future__ import annotations

import json
import sys
import time
from typing import Callable

from .runtime_store import HarnessRuntimeStore

EventFormatter = Callable[[dict], str]

_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"


def _c(text: str, code: str) -> str:
    if not _USE_COLOR:
        return text
    return f"{code}{text}{_RESET}"


def default_event_formatter(event: dict) -> str:
    kind = event.get("kind", "")
    actor = event.get("actor", "")
    payload = event.get("payload") or {}
    if kind == "assistant/chunk":
        text = payload.get("text", "")
        return f"  chunk @{actor}: {text}"
    if kind in {"tool/call", "tool/result"}:
        body = json.dumps(payload, ensure_ascii=False)
        return f"  {kind} @{actor}: {body[:120]}"
    if kind.startswith(("turn/", "step/", "tools/", "agent/")):
        body = json.dumps(payload, ensure_ascii=False)
        return f"  {kind} @{actor}: {body[:120]}"
    if kind == "user/message":
        return f"  user @{actor}: {str(payload.get('request') or payload)[:120]}"
    return f"  [{event.get('sequence')}] {kind} @{actor}"


def verbose_event_formatter(event: dict) -> str:
    kind = event.get("kind", "")
    actor = event.get("actor", "")
    payload = event.get("payload") or {}
    seq = event.get("sequence", "?")

    if kind == "user/message":
        text = str(payload.get("request") or payload)[:160]
        return f"{_c(f'[{seq}]', _DIM)} {_c('USER', _BOLD)} {_c('@' + actor, _CYAN)} {text}"

    if kind == "assistant/chunk":
        return f"{_c('▸', _GREEN)}{payload.get('text', '')}"

    if kind == "assistant/message":
        content = str(payload.get("content") or "")[:160]
        return f"{_c(f'[{seq}]', _DIM)} {_c('ASSISTANT', _GREEN)} {content}"

    if kind == "agent/plan":
        tools = ", ".join(item.get("tool_id", "?") for item in payload.get("tools") or [])
        return f"{_c(f'[{seq}]', _DIM)} {_c('PLAN', _MAGENTA)} turn={payload.get('turn')} → {tools}"

    if kind == "tool/call":
        return (
            f"{_c(f'[{seq}]', _DIM)} {_c('TOOL', _YELLOW)} "
            f"{payload.get('tool_id')} call={payload.get('call_id')}"
        )

    if kind == "tool/result":
        summary = payload.get("summary") or {}
        status = _c("ERROR", _RED) if payload.get("isError") else _c("OK", _GREEN)
        detail = summary.get("message") or summary.get("decision") or json.dumps(summary, ensure_ascii=False)[:80]
        return f"{_c(f'[{seq}]', _DIM)} {_c('RESULT', _YELLOW)} {status} {detail}"

    if kind.startswith("tools/"):
        return f"{_c(f'[{seq}]', _DIM)} {_c(kind, _DIM)} {payload.get('tool_id', '')}"

    if kind == "turn/start":
        return f"{_c(f'[{seq}]', _DIM)} {_c('TURN', _BLUE)} #{payload.get('turn')} start task={payload.get('task_id')}"

    if kind == "turn/end":
        failures = payload.get("failures") or []
        suffix = f" failures={failures}" if failures else ""
        return f"{_c(f'[{seq}]', _DIM)} {_c('TURN', _BLUE)} #{payload.get('turn')} end status={payload.get('status')}{suffix}"

    if kind == "step/start":
        return f"{_c(f'[{seq}]', _DIM)} {_c('STEP', _CYAN)} #{payload.get('step')} start (model request)"

    if kind == "step/end":
        tools = ", ".join(str(t) for t in (payload.get("tools") or []) if t)
        return (
            f"{_c(f'[{seq}]', _DIM)} {_c('STEP', _CYAN)} #{payload.get('step')} end "
            f"status={payload.get('status', '—')} tools=[{tools}]"
        )

    if kind == "agent/stopped":
        return f"{_c(f'[{seq}]', _DIM)} {_c('STOP', _MAGENTA)} {json.dumps(payload, ensure_ascii=False)[:120]}"

    if kind.startswith("task/"):
        return f"{_c(f'[{seq}]', _DIM)} {kind} {json.dumps(payload, ensure_ascii=False)[:100]}"

    return f"{_c(f'[{seq}]', _DIM)} {kind} @{actor}"


def tail_session(
    runtime: HarnessRuntimeStore,
    session_id: str,
    *,
    stop_when: Callable[[], bool] | None = None,
    poll_seconds: float = 0.2,
    formatter: EventFormatter = default_event_formatter,
    output=sys.stdout,
) -> int:
    """Stream newly appended Session events to the terminal."""
    seen = 0
    while True:
        session = runtime.get_session(session_id)
        events = session.get("events") or []
        while seen < len(events):
            print(formatter(events[seen]), file=output, flush=True)
            seen += 1
        if stop_when and stop_when():
            return seen
        time.sleep(poll_seconds)
