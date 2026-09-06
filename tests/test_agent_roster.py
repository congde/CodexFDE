from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workbench.agent_roster import (
    assert_boss_actor,
    assert_coder_reviewer_sod,
    build_agent_critique,
    duty_actor_for_status,
    format_roster_for_llm,
    list_employees,
    resolve,
)
from workbench.platform_api import HarnessPlatformAPI
from workbench.system_prompt import assemble_system_prompt


class AgentRosterTests(unittest.TestCase):
    def test_default_employees_and_sod(self) -> None:
        items = list_employees()
        self.assertEqual(3, len(items))
        self.assertEqual("产品", resolve("agent:spec").display_name)
        self.assertEqual("开发", resolve("agent:coder").display_name)
        self.assertEqual("测试", resolve("agent:reviewer").display_name)
        self.assertEqual("agent:coder", duty_actor_for_status("executing"))
        self.assertEqual("agent:reviewer", duty_actor_for_status("review"))
        assert_coder_reviewer_sod("agent:coder", "agent:reviewer")
        with self.assertRaises(ValueError):
            assert_coder_reviewer_sod("agent:coder", "agent:coder")
        with self.assertRaises(ValueError):
            assert_boss_actor("agent:reviewer")
        self.assertEqual("boss", assert_boss_actor("boss"))

    def test_roster_and_prompt_visible_to_model(self) -> None:
        text = format_roster_for_llm(task_status="evaluating")
        self.assertIn("OPC", text)
        self.assertIn("agent:coder", text)
        self.assertIn("不能 approve", text)
        prompt = assemble_system_prompt(
            task={"id": "T1", "status": "evaluating", "request": "库存不足拒单", "execution_mode": "verify"},
            tools=[],
        )
        self.assertTrue(any(section.id == "roster" for section in prompt.sections))
        self.assertIn("值班", prompt.as_text() + format_roster_for_llm(task_status="evaluating"))

    def test_critique_cannot_finalize_and_boss_review_rejects_agent(self) -> None:
        critique = build_agent_critique({
            "status": "review",
            "result": {"summary": {"decision": "pass", "blocking_failed": 0, "blocking_passed": 1}},
        })
        self.assertFalse(critique["can_finalize"])
        self.assertEqual("approve", critique["suggested_decision"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "target"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            api = HarnessPlatformAPI(root / "harness", repo)
            report = {"summary": {"decision": "pass", "blocking_failed": 0, "blocking_passed": 1}, "results": []}
            script = (
                "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("
                + repr(json.dumps(report))
                + ",encoding='utf-8')"
            )
            api.dispatch("POST", "/api/v1/projects", {
                "x-workbench-actor": "boss", "idempotency-key": "p1",
            }, {
                "id": "PROJECT-TARGET", "name": "Target", "root_path": str(repo),
                "eval_command": [sys.executable, "-c", script, "{report_path}"],
            })
            employees = api.dispatch("GET", "/api/v1/employees", {}, {})
            self.assertEqual(200, employees.status)
            self.assertEqual(3, len(employees.body["items"]))
            submitted = api.dispatch("POST", "/api/v1/tasks", {
                "x-workbench-actor": "boss", "idempotency-key": "t1",
            }, {
                "project_id": "PROJECT-TARGET",
                "request": "验证 OPC 员工协同",
                "execute_code": False,
            })
            self.assertEqual(202, submitted.status)
            finished = api.automation.wait(submitted.body["id"], 8)
            self.assertEqual("review", finished["status"])
            session = api.dispatch("GET", f"/api/v1/sessions/{submitted.body['session_id']}", {}, {})
            actors = {event.get("actor") for event in session.body["events"]}
            self.assertTrue(any(str(a).startswith("agent:") for a in actors if a))
            refused = api.dispatch("POST", f"/api/v1/tasks/{finished['id']}/review", {
                "x-workbench-actor": "agent:reviewer", "idempotency-key": "bad-review",
            }, {"decision": "approve", "note": "员工试图终审"})
            self.assertEqual(422, refused.status)
            critique_resp = api.dispatch("POST", f"/api/v1/tasks/{finished['id']}/agent-critique", {
                "x-workbench-actor": "boss", "idempotency-key": "critique-1",
            }, {})
            self.assertEqual(200, critique_resp.status)
            self.assertFalse(critique_resp.body["critique"]["can_finalize"])
            approved = api.dispatch("POST", f"/api/v1/tasks/{finished['id']}/review", {
                "x-workbench-actor": "boss", "idempotency-key": "ok-review",
            }, {"decision": "approve", "note": "老板终审通过"})
            self.assertEqual(200, approved.status)
            self.assertEqual("completed", approved.body["status"])
            exported = api.dispatch("GET", f"/api/v1/sessions/{submitted.body['session_id']}/export", {}, {})
            self.assertEqual(200, exported.status)
            self.assertEqual("super_individual", exported.body["opc"]["mode"])
            self.assertFalse(exported.body["opc"]["multi_user_accounts"])
            self.assertGreaterEqual(len(exported.body["opc"]["agent_actors_seen"]), 1)
            self.assertIn("employees", exported.body["opc"])


if __name__ == "__main__":
    unittest.main()
