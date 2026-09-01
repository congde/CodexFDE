from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workbench.initiative import InitiativeStore, route_risk
from workbench.platform_api import HarnessPlatformAPI


def complete_payload(**overrides: object) -> dict:
    payload = {
        "title": "库存不足时给出可解释拒单",
        "raw_signal": "客服反馈：库存不足的订单仍显示处理中",
        "source": "客服工单 CS-1042",
        "problem_statement": "用户无法判断订单是否因库存不足而失败",
        "goal": "库存不足时原子拒单并返回明确原因",
        "non_goals": ["不改采购补货策略"],
        "constraints": ["可用库存不得为负"],
        "acceptance": ["库存不足时订单失败且不产生预占", "成功路径保持可追溯"],
        "evidence": [{"type": "user_feedback", "content": "近 7 天出现 6 次", "source": "CS-1042"}],
        "assumptions": ["错误来自库存预占路径"],
        "alternatives": ["先改善错误提示而不改流程"],
        "affected_areas": ["inventory", "order"],
        "dependencies": [],
        "reversibility": "easy",
        "owner": "product-tech-lead",
        "project_id": "PROJECT-TARGET",
        "requirement_id": "REQ-INV-1042",
    }
    payload.update(overrides)
    return payload


class InitiativeStoreTests(unittest.TestCase):
    def test_risk_router_is_deterministic_and_explainable(self) -> None:
        self.assertEqual(("quick", ["范围局部且容易恢复"]), route_risk(["ui"], "easy"))
        lane, reasons = route_risk(["api"], "partial", ["team-order"])
        self.assertEqual("standard", lane)
        self.assertTrue(any("共享" in reason for reason in reasons))
        lane, reasons = route_risk(["inventory"], "easy")
        self.assertEqual("strict", lane)
        self.assertTrue(any("inventory" in reason for reason in reasons))

    def test_build_is_blocked_until_evidence_contract_and_named_review_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = InitiativeStore(Path(temporary) / "platform.db")
            incomplete = store.create({
                "title": "一个意见", "raw_signal": "领导说做一个按钮", "source": "会议",
                "affected_areas": ["inventory"], "reversibility": "easy",
            }, "owner-a")
            self.assertFalse(incomplete["readiness"]["decision_ready"])
            with self.assertRaisesRegex(ValueError, "证据门未通过"):
                store.decide(incomplete["id"], "build", "owner-a", "现在做", incomplete["version"])

            ready = store.create(complete_payload(), "owner-a")
            self.assertEqual("strict", ready["risk_lane"])
            self.assertIn("reviewer", ready["readiness"]["delivery_missing"])
            with self.assertRaisesRegex(ValueError, "reviewer"):
                store.decide(ready["id"], "build", "owner-a", "证据支持投入", ready["version"])
            decided = store.decide(
                ready["id"], "build", "owner-a", "证据支持投入", ready["version"],
                reviewer="reviewer-a",
            )
            self.assertEqual("approved_for_delivery", decided["status"])
            self.assertEqual("build", decided["decision"])
            self.assertEqual(2, decided["version"])
            self.assertEqual("decision/recorded", decided["events"][-1]["kind"])

    def test_non_build_decisions_have_specific_safety_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = InitiativeStore(Path(temporary) / "platform.db")
            item = store.create(complete_payload(affected_areas=["ui"]), "owner-a")
            with self.assertRaisesRegex(ValueError, "success_metric"):
                store.decide(item["id"], "experiment", "owner-a", "先验证", item["version"])
            experiment = store.decide(
                item["id"], "experiment", "owner-a", "先验证", item["version"],
                success_metric="错误工单下降 50%", stop_condition="错误率上升即停止",
            )
            self.assertEqual("experiment", experiment["status"])

    def test_incomplete_item_can_be_revised_without_rewriting_original_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = InitiativeStore(Path(temporary) / "platform.db")
            created = store.create({
                "title": "待调查", "raw_signal": "用户原话必须保留", "source": "访谈",
                "affected_areas": ["ui"], "reversibility": "easy",
            }, "owner-a")
            revised = store.revise(created["id"], {
                "problem_statement": "库存错误缺少解释",
                "goal": "返回可解释结果",
                "evidence": ["3 位用户遇到同一问题"],
                "acceptance": ["失败路径返回明确原因"],
                "affected_areas": ["inventory"],
                "reversibility": "hard",
                "project_id": "PROJECT-TARGET",
                "reviewer": "reviewer-a",
            }, "owner-a", created["version"])
            self.assertEqual("用户原话必须保留", revised["raw_signal"])
            self.assertEqual("strict", revised["risk_lane"])
            self.assertTrue(revised["readiness"]["delivery_ready"])
            self.assertEqual("initiative/revised", revised["events"][-1]["kind"])
            with self.assertRaisesRegex(ValueError, "版本已变化"):
                store.revise(revised["id"], {"goal": "旧版本覆盖"}, "owner-a", created["version"])


class InitiativePlatformAPITests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "target"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        return repo

    def test_decided_initiative_promotes_once_into_existing_delivery_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            api = HarnessPlatformAPI(root / "harness", repo)
            report = {"summary": {"decision": "pass", "blocking_failed": 0}, "results": []}
            script = (
                "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("
                + repr(json.dumps(report)) + ",encoding='utf-8')"
            )
            api.dispatch("POST", "/api/v1/projects", {
                "x-workbench-actor": "owner-a", "idempotency-key": "project-1",
            }, {
                "id": "PROJECT-TARGET", "name": "Target", "root_path": str(repo),
                "eval_command": [sys.executable, "-c", script, "{report_path}"],
            })
            created = api.dispatch("POST", "/api/v1/initiatives", {
                "x-workbench-actor": "owner-a", "idempotency-key": "initiative-1",
            }, complete_payload())
            self.assertEqual(201, created.status)
            self.assertEqual("strict", created.body["risk_lane"])

            decided = api.dispatch("POST", f"/api/v1/initiatives/{created.body['id']}/decision", {
                "x-workbench-actor": "owner-a", "idempotency-key": "decision-1",
            }, {
                "decision": "build", "rationale": "工单与约束证据支持投入",
                "reviewer": "reviewer-a", "expected_version": created.body["version"],
            })
            self.assertEqual(200, decided.status)
            promoted = api.dispatch("POST", f"/api/v1/initiatives/{created.body['id']}/delivery", {
                "x-workbench-actor": "owner-a", "idempotency-key": "delivery-1",
            }, {"expected_version": decided.body["version"], "execute_code": False})
            self.assertEqual(202, promoted.status)
            task_id = promoted.body["task"]["id"]
            self.assertIn(f"INITIATIVE:{created.body['id']}", promoted.body["task"]["business_refs"])
            self.assertEqual(task_id, promoted.body["initiative"]["linked_task_id"])
            self.assertTrue(promoted.body["session_id"])

            repeated = api.dispatch("POST", f"/api/v1/initiatives/{created.body['id']}/delivery", {
                "x-workbench-actor": "owner-a", "idempotency-key": "delivery-2",
            }, {"expected_version": 999, "execute_code": False})
            self.assertEqual(202, repeated.status)
            self.assertEqual(task_id, repeated.body["task"]["id"])
            self.assertEqual(1, len(api.tasks.list()))
            api.automation.wait(task_id, 5)

    def test_stale_decision_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            api = HarnessPlatformAPI(root / "harness", self._repo(root))
            created = api.dispatch("POST", "/api/v1/initiatives", {
                "x-workbench-actor": "owner-a", "idempotency-key": "initiative-1",
            }, complete_payload(project_id="PROJECT-NOT-NEEDED", affected_areas=["ui"]))
            stale = api.dispatch("POST", f"/api/v1/initiatives/{created.body['id']}/decision", {
                "x-workbench-actor": "owner-a", "idempotency-key": "decision-stale",
            }, {"decision": "reject", "rationale": "暂无价值", "expected_version": 99})
            self.assertEqual(422, stale.status)
            self.assertIn("版本已变化", stale.body["message"])


if __name__ == "__main__":
    unittest.main()
