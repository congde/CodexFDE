from __future__ import annotations

import json
from pathlib import Path

from .session_context import derive_messages


class JsonlSessionPersist:
    """Optional persist seam: mirror Session events to jsonl for restart/rebuild."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in session_id)
        return self.root / f"{safe}.jsonl"

    def append_event(self, session_id: str, event: dict) -> Path:
        target = self.path_for(session_id)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return target

    def load_events(self, session_id: str) -> list[dict]:
        target = self.path_for(session_id)
        if not target.is_file():
            return []
        events: list[dict] = []
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
        return events

    def derive_messages(self, session_id: str) -> list[dict]:
        return derive_messages({"events": self.load_events(session_id)})
