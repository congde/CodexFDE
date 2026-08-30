from __future__ import annotations

import json
from typing import Any


def extract_codex_text_events(raw: str) -> list[str]:
    """Extract human-readable assistant fragments from Codex --json stdout."""
    parts: list[str] = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if isinstance(item, dict):
            if item.get("type") == "agent_message":
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    parts.append(text)
            elif item.get("type") == "reasoning":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        message = event.get("message")
        if isinstance(message, str) and message.strip():
            parts.append(message.strip())
        delta = event.get("delta")
        if isinstance(delta, str) and delta:
            parts.append(delta)
    merged: list[str] = []
    for part in parts:
        if merged and merged[-1] == part:
            continue
        merged.append(part)
    return merged


class SessionCodexStreamer:
    """Project Codex stdout/final evidence into assistant/chunk + assistant/message."""

    def __init__(self, runtime, session_id: str, actor: str, *, turn: int, provider: str = "codex") -> None:
        self.runtime = runtime
        self.session_id = session_id
        self.actor = actor
        self.turn = turn
        self.provider = provider
        self._chunk_index = 0
        self._pending_parts: list[str] = []
        self._finalized = False
        self._streamed = False

    def ingest_line(self, line: str) -> None:
        """Incrementally project one Codex --json stdout line into assistant/chunk events."""
        stripped = line.strip()
        if not stripped:
            return
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        delta = event.get("delta")
        if isinstance(delta, str) and delta:
            self._emit_chunk_text(delta)
            return
        item = event.get("item")
        if isinstance(item, dict):
            if item.get("type") in {"agent_message", "reasoning"}:
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    self._emit_chunk_text(text)
        message = event.get("message")
        if isinstance(message, str) and message.strip():
            self._emit_chunk_text(message.strip())

    def _emit_chunk_text(self, text: str, *, chunk_size: int = 64) -> None:
        content = text.strip()
        if not content:
            return
        chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
        for chunk in chunks:
            self.runtime.append(
                self.session_id,
                "assistant/chunk",
                self.actor,
                {"turn": self.turn, "index": self._chunk_index, "text": chunk, "provider": self.provider},
                source_key=f"turn:{self.turn}:assistant:chunk:{self._chunk_index}",
            )
            self._chunk_index += 1
        self._pending_parts.append(content)
        self._streamed = True

    def finalize(self) -> None:
        if self._finalized or not self._pending_parts:
            return
        content = "\n\n".join(self._pending_parts)
        self.runtime.append(
            self.session_id,
            "assistant/message",
            self.actor,
            {"turn": self.turn, "content": content, "provider": self.provider},
            source_key=f"turn:{self.turn}:assistant:message:codex",
        )
        self._finalized = True

    def emit_text(self, text: str, *, chunk_size: int = 64) -> None:
        content = text.strip()
        if not content:
            return
        self._emit_chunk_text(content, chunk_size=chunk_size)
        self.finalize()

    def ingest_stdout(self, raw: str) -> None:
        parts = extract_codex_text_events(raw)
        if parts:
            self._streamed = True
            for part in parts:
                self._emit_chunk_text(part)
            self.finalize()

    def ingest_execution_evidence(self, evidence: dict[str, Any]) -> None:
        if not self._streamed:
            stdout = str(evidence.get("stdout_tail") or "")
            if stdout:
                self.ingest_stdout(stdout)
        if not self._finalized:
            final = evidence.get("final")
            if isinstance(final, dict):
                summary = final.get("summary") or final.get("message") or final.get("content")
                if isinstance(summary, str) and summary.strip():
                    self._emit_chunk_text(summary)
            self.finalize()
