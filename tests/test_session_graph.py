from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench.mcp_provider import MCPClient, MCPProviderError, mcp_tool_handler
from workbench.runtime_store import HarnessRuntimeStore
from workbench.session_graph import format_session_graph, session_delivery_graph


class SessionGraphTests(unittest.TestCase):
    def test_projects_task_transitions_into_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = HarnessRuntimeStore(Path(temporary) / "platform.db")
            session = runtime.create_session("PROJECT-X", "graph", "tester")
            session_id = session["id"]
            runtime.append(session_id, "task/status", "system", {"to_status": "queued"})
            runtime.append(session_id, "task/status", "system", {"to_status": "spec_ready"})
            runtime.append(session_id, "task/status", "system", {"to_status": "executing"})
            runtime.append(session_id, "turn/start", "agent", {"turn": 1})
            runtime.append(session_id, "step/start", "agent", {"turn": 1, "step": 1, "tool": "spec.read"})
            runtime.append(session_id, "turn/end", "agent", {"turn": 1, "status": "review"})
            runtime.append(session_id, "task/status", "system", {"to_status": "review"})
            runtime.append(session_id, "agent/stopped", "agent", {"reason": "converged"})
            graph = session_delivery_graph(runtime.get_session(session_id), {"id": "TASK-1", "status": "review"})
            self.assertEqual("harness.session.graph/v1", graph["schema"])
            self.assertEqual("review", graph["current"])
            visited = {node["id"] for node in graph["nodes"] if node["visited"]}
            self.assertIn("spec_ready", visited)
            self.assertIn("review", visited)
            rendered = format_session_graph(graph)
            self.assertIn("current   : review", rendered)


class MCPProviderTests(unittest.TestCase):
    def test_unavailable_without_url(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            client = MCPClient(base_url="")
            caps = client.capabilities()
            self.assertFalse(caps["mcp_available"])
            with self.assertRaises(MCPProviderError):
                mcp_tool_handler({"mcp": client}, {"name": "demo"})


if __name__ == "__main__":
    unittest.main()
