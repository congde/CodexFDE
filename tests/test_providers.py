from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workbench.harness_terminal import HarnessTerminal
from workbench.platform_api import HarnessPlatformAPI
from workbench.providers import HarnessProviders


class ProviderTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "target"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        return repo

    def test_activate_plugin_switches_eval_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            before = api.runtime.composition()
            self.assertEqual("command", next(p for p in before["plugins"] if p["seam"] == "eval")["provider"])
            updated = api.runtime.activate_plugin("PROFILE-DEFAULT", "eval.local")
            self.assertEqual("local", next(p for p in updated["plugins"] if p["seam"] == "eval")["provider"])
            providers = HarnessProviders(api.runtime, api.projects, api.runtime_dir, api.repository_root)
            self.assertEqual("local", providers.capabilities()["providers"]["eval"])

    def test_llm_seam_emits_assistant_events_during_headless(self) -> None:
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
                "llm seam 验证",
                project_id="PROJECT-TARGET",
                requirement_id="REQ-LLM-001",
            )
            self.assertEqual(0, result["exit_code"])
            session = terminal.show_session(result["session_id"])
            kinds = [event["kind"] for event in session["events"]]
            self.assertIn("turn/start", kinds)
            self.assertIn("assistant/message", kinds)
            self.assertTrue(any(item["role"] == "assistant" for item in result["messages"]))

    def test_fs_and_shell_seams_are_swappable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            (repo / "note.txt").write_text("hello-seam", encoding="utf-8")
            api = HarnessPlatformAPI(root / "harness", repo)
            composition = api.runtime.composition()
            self.assertIn("fs", composition["seams"])
            self.assertIn("shell", composition["seams"])
            self.assertTrue(composition["ready"])
            fs = api.providers.fs_provider()
            listed = fs.list_dir(repo, ".")
            self.assertTrue(any(item["name"] == "note.txt" for item in listed["entries"]))
            read = fs.read_text(repo, "note.txt")
            self.assertEqual("hello-seam", read["content"])
            api.runtime.activate_plugin("PROFILE-DEFAULT", "shell.deny")
            shell = api.providers.shell_provider()
            with self.assertRaises(PermissionError):
                shell.exec(repo, [sys.executable, "-m", "unittest", "discover", "-s", "tests"])

    def test_jsonl_persist_allows_derive_messages_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            session = api.runtime.create_session("PROJECT-X", "persist", "tester")
            api.runtime.append(session["id"], "user/message", "tester", {"request": "hi from jsonl"})
            api.runtime.append(
                session["id"],
                "assistant/message",
                "llm",
                {"turn": 1, "content": "persisted answer"},
            )
            persist = api.providers.persist_jsonl()
            self.assertIsNotNone(persist)
            path = persist.path_for(session["id"])
            self.assertTrue(path.is_file())
            from workbench.session_persist import JsonlSessionPersist

            reloaded = JsonlSessionPersist(path.parent)
            messages = reloaded.derive_messages(session["id"])
            self.assertTrue(any(item.get("role") == "user" for item in messages))
            self.assertTrue(any(
                item.get("role") == "assistant" and "persisted answer" in str(item.get("content"))
                for item in messages
            ))


if __name__ == "__main__":
    unittest.main()
