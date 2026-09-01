from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workbench.codex_events import SessionCodexStreamer
from workbench.runtime_store import HarnessRuntimeStore


class CodexStreamingTests(unittest.TestCase):
    def test_streamer_ingests_json_lines_incrementally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = HarnessRuntimeStore(Path(temporary) / "platform.db")
            session = runtime.create_session("PROJECT-X", "stream", "tester")
            streamer = SessionCodexStreamer(runtime, session["id"], "codex", turn=1, provider="codex")
            streamer.ingest_line(json.dumps({"delta": "hello "}))
            streamer.ingest_line(json.dumps({"delta": "world"}))
            streamer.finalize()
            kinds = [event["kind"] for event in runtime.get_session(session["id"])["events"]]
            self.assertIn("assistant/chunk", kinds)
            self.assertIn("assistant/message", kinds)
            message = next(event for event in runtime.get_session(session["id"])["events"] if event["kind"] == "assistant/message")
            self.assertIn("hello", message["payload"]["content"])


if __name__ == "__main__":
    unittest.main()
