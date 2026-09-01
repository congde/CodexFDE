from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workbench.platform_api import HarnessPlatformAPI


class HarnessPlatformTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "target"; repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        return repo

    def test_project_registry_is_independent_from_erp_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            headers = {"x-workbench-actor": "operator-a", "idempotency-key": "project-1"}
            payload = {
                "id": "PROJECT-FLOWERP", "name": "FlowERP", "root_path": str(repo),
                "eval_command": [sys.executable, "-c", "print('{}')"],
            }
            created = api.dispatch("POST", "/api/v1/projects", headers, payload)
            replay = api.dispatch("POST", "/api/v1/projects", headers, payload)
            listed = api.dispatch("GET", "/api/v1/projects", {}, {})
            self.assertEqual(201, created.status)
            self.assertEqual(created.body["id"], replay.body["id"])
            self.assertEqual("PROJECT-FLOWERP", listed.body["items"][0]["id"])
            self.assertTrue((root / "harness/platform.db").is_file())
            self.assertFalse((root / "harness/flowerp.db").exists())

    def test_registered_project_drives_eval_and_stops_at_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo = self._repo(root); api = HarnessPlatformAPI(root / "harness", repo)
            report = {"summary": {"decision": "pass", "blocking_failed": 0, "blocking_passed": 1}, "results": []}
            script = "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(" + repr(json.dumps(report)) + ",encoding='utf-8')"
            project_headers = {"x-workbench-actor": "operator-a", "idempotency-key": "project-1"}
            api.dispatch("POST", "/api/v1/projects", project_headers, {
                "id": "PROJECT-TARGET", "name": "Target", "root_path": str(repo),
                "eval_command": [sys.executable, "-c", script, "{report_path}"],
            })
            submitted = api.dispatch("POST", "/api/v1/tasks", {
                "x-workbench-actor": "learner-a", "idempotency-key": "task-1",
            }, {
                "project_id": "PROJECT-TARGET", "request": "验证目标项目交付链路",
                "requirement_id": "REQ-TARGET-001", "execute_code": False,
            })
            self.assertEqual(202, submitted.status)
            finished = api.automation.wait(submitted.body["id"], 5)
            self.assertEqual("review", finished["status"])
            self.assertIn("PROJECT:PROJECT-TARGET", finished["business_refs"])
            self.assertEqual(str(repo.resolve()), finished["result"]["project_runner"]["root_path"])
            session = api.dispatch("GET", f"/api/v1/sessions/{submitted.body['session_id']}", {}, {})
            self.assertEqual(200, session.status)
            self.assertEqual(finished["id"], session.body["task_id"])
            self.assertEqual(
                ["session/start", "user/message", "task/created"],
                [event["kind"] for event in session.body["events"][:3]],
            )
            task_events = [event for event in session.body["events"] if event["kind"].startswith("task/")]
            self.assertTrue(any(event["payload"].get("to_status") == "review" for event in task_events))
            replayed = api.dispatch("GET", f"/api/v1/sessions/{submitted.body['session_id']}", {}, {})
            self.assertEqual(len(session.body["events"]), len(replayed.body["events"]))

    def test_session_can_pause_resume_and_fork_but_closed_is_final(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo = self._repo(root); api = HarnessPlatformAPI(root / "harness", repo)
            api.dispatch("POST", "/api/v1/projects", {
                "x-workbench-actor": "operator-a", "idempotency-key": "project-1",
            }, {
                "id": "PROJECT-TARGET", "name": "Target", "root_path": str(repo),
                "eval_command": [sys.executable, "-c", "print('{}')"],
            })
            created = api.dispatch("POST", "/api/v1/sessions", {
                "x-workbench-actor": "learner-a", "idempotency-key": "session-1",
            }, {"project_id": "PROJECT-TARGET", "title": "Original"})
            session_id = created.body["id"]
            for index, status in enumerate(("paused", "active", "closed"), start=1):
                changed = api.dispatch("POST", f"/api/v1/sessions/{session_id}/status", {
                    "x-workbench-actor": "learner-a", "idempotency-key": f"status-{index}",
                }, {"status": status})
                self.assertEqual(200, changed.status)
            refused = api.dispatch("POST", f"/api/v1/sessions/{session_id}/status", {
                "x-workbench-actor": "learner-a", "idempotency-key": "status-4",
            }, {"status": "active"})
            self.assertEqual(422, refused.status)
            forked = api.dispatch("POST", f"/api/v1/sessions/{session_id}/fork", {
                "x-workbench-actor": "learner-a", "idempotency-key": "fork-1",
            }, {"title": "Follow-up"})
            self.assertEqual(201, forked.status)
            self.assertEqual("active", forked.body["status"])
            self.assertEqual("session/forked", forked.body["events"][-1]["kind"])

    def test_default_profile_composes_required_plugin_seams(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo = self._repo(root); api = HarnessPlatformAPI(root / "harness", repo)
            plugins = api.dispatch("GET", "/api/v1/plugins", {}, {})
            composition = api.dispatch("GET", "/api/v1/profiles/PROFILE-DEFAULT/composition", {}, {})
            self.assertEqual(200, plugins.status)
            self.assertTrue(composition.body["ready"])
            self.assertFalse(composition.body["missing_seams"])
            self.assertEqual(
                {
                    "sessions",
                    "persist",
                    "workspace",
                    "fs",
                    "shell",
                    "tools",
                    "llm",
                    "eval",
                    "execution",
                    "approval",
                    "permission",
                    "mcp",
                },
                set(composition.body["seams"]),
            )
            self.assertTrue(all(item["builtin"] for item in plugins.body["items"]))

    def test_writes_require_named_actor_and_idempotency_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo = self._repo(root); api = HarnessPlatformAPI(root / "harness", repo)
            response = api.dispatch("POST", "/api/v1/projects", {}, {
                "name": "Target", "root_path": str(repo), "eval_command": ["python", "eval.py"],
            })
            self.assertEqual(422, response.status)
            self.assertIn("X-Workbench-Actor", response.body["message"])

    def test_dump_config_api_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            response = api.dispatch("GET", "/api/v1/dump-config", {}, {})
            self.assertEqual(200, response.status)
            self.assertEqual("Harness Workbench", response.body["product"])
            self.assertTrue(response.body["composition"]["ready"])

    def test_session_messages_and_export_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            session = api.runtime.create_session("PROJECT-X", "export route", "tester")
            session_id = session["id"]
            api.runtime.append(session_id, "user/message", "tester", {"request": "hello"})
            messages = api.dispatch("GET", f"/api/v1/sessions/{session_id}/messages", {}, {})
            exported = api.dispatch("GET", f"/api/v1/sessions/{session_id}/export", {}, {})
            graph = api.dispatch("GET", f"/api/v1/sessions/{session_id}/graph", {}, {})
            self.assertEqual(200, messages.status)
            self.assertEqual(200, exported.status)
            self.assertEqual(200, graph.status)
            self.assertEqual("harness.session.export/v1", exported.body["schema"])
            self.assertEqual("harness.session.graph/v1", graph.body["schema"])
            self.assertEqual("user", messages.body["messages"][0]["role"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo = self._repo(root)
            from workbench.platform_bootstrap import bootstrap_default_project

            api = HarnessPlatformAPI(root / "harness", repo)
            first = bootstrap_default_project(api)
            second = bootstrap_default_project(api)
            self.assertEqual("created", first["action"])
            self.assertEqual("exists", second["action"])
            self.assertEqual("PROJECT-FLOWERP", first["project"]["id"])
            health = api.dispatch("GET", "/api/v1/health", {}, {})
            self.assertEqual(str(repo.resolve()), health.body["repository_root"])


    def test_session_prompt_binds_task_and_blocks_while_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            report = {
                "summary": {"decision": "pass", "blocking_failed": 0, "blocking_passed": 1},
                "results": [],
            }
            script = (
                "import pathlib,sys,time;time.sleep(0.8);pathlib.Path(sys.argv[1]).write_text("
                + repr(json.dumps(report))
                + ",encoding='utf-8')"
            )
            api.dispatch("POST", "/api/v1/projects", {
                "x-workbench-actor": "operator-a", "idempotency-key": "project-1",
            }, {
                "id": "PROJECT-TARGET", "name": "Target", "root_path": str(repo),
                "eval_command": [sys.executable, "-c", script, "{report_path}"],
            })
            created = api.dispatch("POST", "/api/v1/sessions", {
                "x-workbench-actor": "learner-a", "idempotency-key": "session-1",
            }, {"project_id": "PROJECT-TARGET", "title": "新会话"})
            session_id = created.body["id"]
            prompted = api.dispatch("POST", f"/api/v1/sessions/{session_id}/prompt", {
                "x-workbench-actor": "learner-a", "idempotency-key": "prompt-1",
            }, {"request": "第一次交付", "execute_code": False})
            self.assertEqual(202, prompted.status)
            self.assertEqual(session_id, prompted.body["session_id"])
            busy = api.dispatch("POST", f"/api/v1/sessions/{session_id}/prompt", {
                "x-workbench-actor": "learner-a", "idempotency-key": "prompt-busy",
            }, {"request": "应被拒绝", "execute_code": False})
            self.assertEqual(422, busy.status)
            finished = api.automation.wait(prompted.body["id"], 8)
            self.assertEqual("review", finished["status"])
            session = api.dispatch("GET", f"/api/v1/sessions/{session_id}", {}, {})
            self.assertEqual(prompted.body["id"], session.body["task_id"])
            self.assertIn("第一次交付", session.body["title"])
            kinds = [event["kind"] for event in session.body["events"]]
            self.assertIn("user/message", kinds)
            self.assertIn("task/bound", kinds)
            self.assertIn("task/created", kinds)
            follow_up = api.dispatch("POST", f"/api/v1/sessions/{session_id}/prompt", {
                "x-workbench-actor": "learner-a", "idempotency-key": "prompt-2",
            }, {"request": "第二次交付", "execute_code": False})
            self.assertEqual(202, follow_up.status)
            follow = api.automation.wait(follow_up.body["id"], 8)
            self.assertEqual("review", follow["status"])
            session2 = api.dispatch("GET", f"/api/v1/sessions/{session_id}", {}, {})
            self.assertEqual(follow_up.body["id"], session2.body["task_id"])

    def test_plugin_enable_toggle_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            plugins = api.dispatch("GET", "/api/v1/plugins", {}, {})
            target = next(item for item in plugins.body["items"] if item["id"] == "persist.jsonl")
            disabled = api.dispatch("POST", f"/api/v1/plugins/{target['id']}/enabled", {
                "x-workbench-actor": "operator-a", "idempotency-key": "plug-1",
            }, {"enabled": False})
            self.assertEqual(200, disabled.status)
            self.assertFalse(disabled.body["enabled"])
            enabled = api.dispatch("POST", f"/api/v1/plugins/{target['id']}/enabled", {
                "x-workbench-actor": "operator-a", "idempotency-key": "plug-2",
            }, {"enabled": True})
            self.assertTrue(enabled.body["enabled"])


if __name__ == "__main__":
    unittest.main()
