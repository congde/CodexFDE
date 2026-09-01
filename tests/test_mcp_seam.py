from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from workbench.mcp_provider import ManifestMCPProvider, MCPProviderError, OffMCPProvider, create_mcp_provider
from workbench.platform_api import HarnessPlatformAPI


class MCPSeamTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "target"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        return repo

    def test_off_provider_refuses_call(self) -> None:
        provider = OffMCPProvider()
        self.assertFalse(provider.available())
        with self.assertRaises(MCPProviderError):
            provider.call_tool("anything", {})

    def test_manifest_provider_list_and_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "mcp.json"
            manifest.write_text(
                json.dumps({
                    "tools": [
                        {
                            "name": "echo.demo",
                            "description": "echo args",
                            "response": {"content": [{"type": "text", "text": "pong"}], "isError": False},
                        }
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            provider = ManifestMCPProvider(manifest)
            tools = provider.list_tools()
            self.assertEqual("echo.demo", tools[0]["name"])
            result = provider.call_tool("echo.demo", {"q": 1})
            self.assertEqual("pong", result["content"][0]["text"])

    def test_profile_mcp_seam_switches_to_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            self.assertEqual("off", api.providers.capabilities()["providers"]["mcp"])
            self.assertFalse(api.providers.mcp_provider().available())

            manifest = root / "mcp.json"
            manifest.write_text(
                json.dumps({
                    "tools": [
                        {
                            "name": "demo.ping",
                            "description": "ping",
                            "response": {"ok": True, "message": "pong"},
                        }
                    ]
                }),
                encoding="utf-8",
            )
            previous = os.environ.get("HARNESS_MCP_MANIFEST")
            os.environ["HARNESS_MCP_MANIFEST"] = str(manifest)
            try:
                api.runtime.activate_plugin("PROFILE-DEFAULT", "mcp.manifest")
                caps = api.providers.mcp_provider().capabilities()
                self.assertTrue(caps["mcp_available"])
                self.assertEqual("manifest", caps["provider"])

                session = api.runtime.create_session("PROJECT-X", "mcp", "tester")
                listed = api.tools.invoke(
                    "mcp.list",
                    {
                        "allowed_actions": ["call_mcp"],
                        "mcp": api.providers.mcp_provider(),
                        "auto_approve": True,
                    },
                    {},
                    session_id=session["id"],
                    actor="tester",
                    call_id="mcp-list-1",
                )
                self.assertTrue(listed["available"])
                self.assertEqual("demo.ping", listed["tools"][0]["name"])

                called = api.tools.invoke(
                    "mcp.call",
                    {
                        "allowed_actions": ["call_mcp"],
                        "mcp": api.providers.mcp_provider(),
                        "auto_approve": True,
                    },
                    {"name": "demo.ping", "arguments": {"x": 1}},
                    session_id=session["id"],
                    actor="tester",
                    call_id="mcp-call-1",
                )
                self.assertEqual("pong", called["result"]["message"])
            finally:
                if previous is None:
                    os.environ.pop("HARNESS_MCP_MANIFEST", None)
                else:
                    os.environ["HARNESS_MCP_MANIFEST"] = previous

    def test_create_mcp_provider_factory(self) -> None:
        self.assertFalse(create_mcp_provider("off").available())
        with self.assertRaises(ValueError):
            create_mcp_provider("unknown")


if __name__ == "__main__":
    unittest.main()
