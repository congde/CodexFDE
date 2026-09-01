from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from workbench.harness_cli import main
from workbench.harness_terminal import HarnessTerminal, format_composition, format_status


class HarnessTerminalTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "target"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        return repo

    def test_status_reports_terminal_interface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            terminal = HarnessTerminal(root / "harness", repo, actor="tester")
            terminal.bootstrap()
            status = terminal.status()
            self.assertEqual("terminal", status["interface"])
            self.assertIn("tools", status)
            rendered = format_status({**status, "_render": "status"})
            self.assertIn("TERMINAL CONTROL PLANE", rendered)

    def test_cli_bootstrap_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(["--json", "--runtime-dir", str(root / "harness"), "--repository-root", str(repo), "bootstrap"])
            self.assertEqual(0, code)
            payload = json.loads(buffer.getvalue())
            self.assertEqual("terminal", payload["interface"])
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(["--json", "--runtime-dir", str(root / "harness"), "--repository-root", str(repo), "status"])
            self.assertEqual(0, code)
            status = json.loads(buffer.getvalue())
            self.assertEqual("Harness Workbench", status["product"])
            self.assertGreaterEqual(status["projects"], 1)


    def test_headless_run_exits_zero_on_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            terminal = HarnessTerminal(root / "harness", repo, actor="tester")
            report = {"summary": {"decision": "pass", "blocking_failed": 0, "blocking_passed": 1}, "results": []}
            script = "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(" + repr(json.dumps(report)) + ",encoding='utf-8')"
            terminal.register_project(
                "Target",
                repo,
                project_id="PROJECT-TARGET",
                eval_command=[sys.executable, "-c", script, "{report_path}"],
            )
            result = terminal.headless(
                "headless 验证",
                project_id="PROJECT-TARGET",
                requirement_id="REQ-HEADLESS-001",
            )
            self.assertEqual(0, result["exit_code"])
            self.assertEqual("review", result["task"]["status"])
            self.assertTrue(result["messages"])
            self.assertTrue(str(result.get("final_answer") or "").strip())

    def test_derive_messages_from_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            terminal = HarnessTerminal(root / "harness", repo)
            terminal.api.runtime.create_session("PROJECT-FLOWERP", "derive", "tester")
            session_id = terminal.api.runtime.sessions(1)[0]["id"]
            terminal.api.runtime.append(session_id, "user/message", "tester", {"request": "hello"})
            messages = terminal.derive_session_messages(session_id)
            self.assertEqual("user", messages[0]["role"])

    def test_headless_profile_uses_verify_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            terminal = HarnessTerminal(root / "harness", repo, actor="tester")
            terminal.bootstrap()
            headless = terminal.composition("PROFILE-HEADLESS")
            self.assertTrue(headless["ready"])
            providers = {item["seam"]: item["provider"] for item in headless["plugins"]}
            self.assertEqual("verify", providers["execution"])
            self.assertEqual("command", providers["eval"])

    def test_dump_config_and_export_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            terminal = HarnessTerminal(root / "harness", repo)
            terminal.bootstrap()
            config = terminal.dump_config()
            self.assertEqual("Harness Workbench", config["product"])
            self.assertTrue(config["composition"]["ready"])
            session = terminal.api.runtime.create_session("PROJECT-FLOWERP", "export", "tester")
            session_id = session["id"]
            terminal.api.runtime.append(session_id, "user/message", "tester", {"request": "export me"})
            exported = terminal.export_session(session_id)
            self.assertEqual("harness.session.export/v1", exported["bundle"]["schema"])
            self.assertGreaterEqual(exported["bundle"]["counts"]["events"], 2)

    def test_builtin_workspace_tools_registered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            terminal = HarnessTerminal(root / "harness", repo)
            terminal.bootstrap()
            tool_ids = {item["id"] for item in terminal.list_tools()}
            self.assertIn("workspace.read", tool_ids)
            self.assertIn("workspace.list", tool_ids)
            self.assertIn("workspace.grep", tool_ids)
            self.assertIn("shell.exec", tool_ids)
            self.assertIn("mcp.call", tool_ids)
            self.assertIn("mcp.list", tool_ids)

    def test_workspace_grep_finds_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            (repo / "sample.py").write_text("def hello_harness():\n    return 1\n", encoding="utf-8")
            terminal = HarnessTerminal(root / "harness", repo)
            terminal.bootstrap()
            spec_path = terminal.api.runtime_dir / "specs" / "TASK-GREPTEST01.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            from workbench.spec import write_delivery_spec

            task = terminal.api.tasks.create(
                "grep test",
                "REQ-GREP",
                ["PROJECT:PROJECT-FLOWERP"],
                str(spec_path),
                "tester",
                task_id="TASK-GREPTEST01",
            )
            write_delivery_spec(spec_path, task["request"], "REQ-GREP", task["business_refs"])
            session = terminal.api.runtime.create_session(
                "PROJECT-FLOWERP", "grep", "tester", task_id=task["id"],
            )
            result = terminal.invoke_tool(
                session["id"],
                "workspace.grep",
                task["id"],
                {"pattern": "hello_harness", "path": "."},
            )
            self.assertTrue(result["matches"])

    def test_composition_reports_ready_default_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            terminal = HarnessTerminal(root / "harness", repo)
            terminal.bootstrap()
            payload = terminal.composition()
            self.assertTrue(payload["ready"])
            rendered = format_composition(payload)
            self.assertIn("Profile PROFILE-DEFAULT", rendered)
            providers = {item["seam"]: item["provider"] for item in payload["plugins"]}
            self.assertEqual("codex", providers["llm"])


if __name__ == "__main__":
    unittest.main()
