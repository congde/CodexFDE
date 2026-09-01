from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workbench.platform_api import HarnessPlatformAPI
from workbench.session_context import derive_messages
from workbench.tool_registry import ToolRegistry, ToolSpec


class ToolRegistryTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "target"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        return repo

    def test_invoke_emits_dsh_aligned_tool_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            session = api.runtime.create_session("PROJECT-X", "tool test", "tester")
            registry = api.tools

            def handler(_context: dict, _args: dict) -> dict:
                return {"ok": True}

            registry.register(ToolSpec(
                id="demo.echo",
                name="Echo",
                description="test tool",
                permissions=frozenset({"read_spec"}),
                handler=handler,
            ))
            result = registry.invoke(
                "demo.echo",
                {"allowed_actions": ["read_spec"]},
                {},
                session_id=session["id"],
                actor="tester",
                call_id="call-1",
            )
            self.assertTrue(result["ok"])
            kinds = [event["kind"] for event in api.runtime.get_session(session["id"])["events"]]
            self.assertEqual(
                [
                    "session/start",
                    "tool/call",
                    "tools/pre-execute",
                    "tools/execute",
                    "tools/post-execute",
                    "tools/result",
                    "tool/result",
                ],
                kinds,
            )

    def test_ask_approval_denies_without_named_allow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            session = api.runtime.create_session("PROJECT-X", "ask test", "tester")

            def handler(_context: dict, _args: dict) -> dict:
                return {"ok": True}

            api.tools.register(ToolSpec(
                id="demo.risky",
                name="Risky",
                description="needs ask",
                permissions=frozenset({"read_spec"}),
                handler=handler,
                approval="ask",
            ))
            with self.assertRaises(PermissionError):
                api.tools.invoke(
                    "demo.risky",
                    {"allowed_actions": ["read_spec"]},
                    {},
                    session_id=session["id"],
                    actor="tester",
                    call_id="call-ask",
                )
            kinds = [event["kind"] for event in api.runtime.get_session(session["id"])["events"]]
            self.assertIn("approval/ask", kinds)
            self.assertIn("tools/result", kinds)

    def test_ask_approval_allows_with_auto_approve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            session = api.runtime.create_session("PROJECT-X", "ask ok", "tester")

            def handler(_context: dict, _args: dict) -> dict:
                return {"ok": True}

            api.tools.register(ToolSpec(
                id="demo.risky2",
                name="Risky2",
                description="needs ask",
                permissions=frozenset({"read_spec"}),
                handler=handler,
                approval="ask",
            ))
            result = api.tools.invoke(
                "demo.risky2",
                {"allowed_actions": ["read_spec"], "auto_approve": True},
                {},
                session_id=session["id"],
                actor="tester",
                call_id="call-ask-ok",
            )
            self.assertTrue(result["ok"])
            kinds = [event["kind"] for event in api.runtime.get_session(session["id"])["events"]]
            self.assertIn("approval/ask", kinds)

    def test_invoke_denied_emits_tool_result_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            session = api.runtime.create_session("PROJECT-X", "deny test", "tester")

            def handler(_context: dict, _args: dict) -> dict:
                return {"ok": True}

            api.tools.register(ToolSpec(
                id="demo.secure",
                name="Secure",
                description="needs write",
                permissions=frozenset({"write_code_in_task_scope"}),
                handler=handler,
            ))
            with self.assertRaises(PermissionError):
                api.tools.invoke(
                    "demo.secure",
                    {"allowed_actions": ["read_spec"]},
                    {},
                    session_id=session["id"],
                    actor="tester",
                    call_id="call-2",
                )
            session = api.runtime.get_session(session["id"])
            kinds = [event["kind"] for event in session["events"]]
            self.assertIn("tool/call", kinds)
            self.assertIn("tools/pre-execute", kinds)
            self.assertIn("tool/result", kinds)
            result_event = next(event for event in session["events"] if event["kind"] == "tool/result")
            self.assertTrue(result_event["payload"]["isError"])


class AgentLoopTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "target"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        return repo

    def test_agent_loop_converges_to_review_and_emits_turn_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            report = {"summary": {"decision": "pass", "blocking_failed": 0, "blocking_passed": 1}, "results": []}
            script = "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(" + repr(json.dumps(report)) + ",encoding='utf-8')"
            api.projects.create("Target", str(repo), [sys.executable, "-c", script, "{report_path}"], "PROJECT-TARGET")
            spec_path = api.runtime_dir / "specs" / "TASK-A1B2C3D4E5.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            from workbench.spec import write_delivery_spec

            task = api.tasks.create(
                "验证 Agent Loop",
                "REQ-AGENT-001",
                ["PROJECT:PROJECT-TARGET"],
                str(spec_path),
                "tester",
                task_id="TASK-A1B2C3D4E5",
            )
            write_delivery_spec(spec_path, task["request"], "REQ-AGENT-001", task["business_refs"])
            session = api.runtime.create_session("PROJECT-TARGET", "agent loop", "tester", task_id=task["id"])
            finished = api.agent_loop.run(session["id"], task["id"], "tester")
            self.assertEqual("review", finished["status"])
            kinds = [event["kind"] for event in api.runtime.get_session(session["id"])["events"]]
            self.assertIn("turn/start", kinds)
            self.assertIn("turn/end", kinds)
            self.assertIn("step/start", kinds)
            self.assertIn("step/end", kinds)
            self.assertEqual(1, kinds.count("step/start"), "dsh: one model request ⇒ one step")
            self.assertEqual(1, kinds.count("step/end"))
            step_end = next(e for e in api.runtime.get_session(session["id"])["events"] if e["kind"] == "step/end")
            self.assertGreaterEqual(len(step_end["payload"].get("tools") or []), 2)
            self.assertIn("assistant/message", kinds)
            self.assertIn("assistant/chunk", kinds)
            self.assertIn("agent/pre-step", kinds)
            self.assertIn("agent/plan", kinds)
            self.assertIn("agent/stopped", kinds)
            tool_calls = [event["payload"].get("tool_id") for event in api.runtime.get_session(session["id"])["events"] if event["kind"] == "tool/call"]
            self.assertIn("spec.read", tool_calls)
            self.assertIn("eval.blocking", tool_calls)
            # Multiple tool/call events must sit between the single step/start and step/end.
            start_i = kinds.index("step/start")
            end_i = kinds.index("step/end")
            between = kinds[start_i + 1:end_i]
            self.assertIn("tool/call", between)
            self.assertTrue(between.count("tool/call") >= 2)
            messages = derive_messages(api.runtime.get_session(session["id"]))
            self.assertTrue(any(item["role"] == "assistant" for item in messages))

    def test_pre_step_rejects_empty_request(self) -> None:
        from workbench.system_prompt import run_pre_step

        decision = run_pre_step(task={"request": "   "}, tools=[])
        self.assertFalse(decision.entered)
        self.assertEqual("reject", decision.action)
        self.assertEqual("empty_request", decision.reason)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            report = {"summary": {"decision": "pass", "blocking_failed": 0, "blocking_passed": 1}, "results": []}
            script = "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(" + repr(json.dumps(report)) + ",encoding='utf-8')"
            api.projects.create("Target", str(repo), [sys.executable, "-c", script, "{report_path}"], "PROJECT-TARGET")
            from workbench.spec import write_delivery_spec

            spec_path = api.runtime_dir / "specs" / "TASK-EMPTY001XX.md"
            task = api.tasks.create(
                "will clear",
                "REQ-EMPTY-001",
                ["PROJECT:PROJECT-TARGET"],
                str(spec_path),
                "tester",
                task_id="TASK-EMPTY001XX",
            )
            write_delivery_spec(spec_path, "placeholder", "REQ-EMPTY-001", task["business_refs"])
            session = api.runtime.create_session("PROJECT-TARGET", "empty", "tester", task_id=task["id"])
            original_get = api.tasks.get

            def empty_request(task_id: str) -> dict:
                payload = original_get(task_id)
                payload = dict(payload)
                payload["request"] = ""
                return payload

            api.tasks.get = empty_request  # type: ignore[method-assign]
            try:
                api.agent_loop.run_turn(session["id"], task["id"], "tester", turn_no=1)
            finally:
                api.tasks.get = original_get  # type: ignore[method-assign]
            kinds = [event["kind"] for event in api.runtime.get_session(session["id"])["events"]]
            self.assertIn("agent/pre-step", kinds)
            pre = next(e for e in api.runtime.get_session(session["id"])["events"] if e["kind"] == "agent/pre-step")
            self.assertEqual("reject", pre["payload"]["action"])
            self.assertNotIn("step/start", kinds)


if __name__ == "__main__":
    unittest.main()
