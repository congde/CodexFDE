from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from workbench.delivery_view import DeliveryViewService, build_delivery_view
from workbench.evolution import EvolutionStore
from workbench.feedback import add_feedback, review_feedback
from workbench.platform_api import HarnessPlatformAPI
from workbench.task_store import TaskStore


class DeliveryViewTests(unittest.TestCase):
    def test_review_projection_has_one_owner_next_action_and_green_eval(self) -> None:
        task = {
            "id": "TASK-1234567890",
            "request": "交付库存失败证据",
            "requirement_id": "REQ-VIEW-001",
            "business_refs": ["SKU:NOTEBOOK-AI"],
            "status": "review",
            "automation_mode": "automatic",
            "execution_mode": "verify",
            "write_scope": [],
            "execution_timeout_seconds": 900,
            "spec_path": "FDE_SPEC.md",
            "spec": {"goal": "保留失败"},
            "result": {
                "summary": {"decision": "pass", "blocking_failed": 0, "blocking_passed": 1},
                "results": [{"name": "stock_never_negative", "level": "blocking", "passed": True}],
                "report_path": "reports/TASK-1234567890-harness-blocking.json",
                "report_sha256": "a" * 64,
            },
            "events": [
                {
                    "detail": "自动流水线开始推进",
                    "evidence": {
                        "attempt": 1,
                        "max_attempts": 3,
                    },
                },
                {
                    "detail": "开始受控执行",
                    "evidence": {
                        "allowed_actions": ["read_spec", "read_workspace", "run_blocking_eval", "run_workspace_shell"],
                        "forbidden_actions": ["write_runtime_database", "approve_business_document", "skip_eval"],
                    },
                },
            ],
            "reviewed_by": None,
            "review_decision": None,
            "review_note": None,
            "reviewed_at": None,
            "updated_at": "2026-08-31 01:00:00",
        }
        view = build_delivery_view(
            task, now=datetime(2026, 8, 31, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual("workbench.delivery-view/v1", view["schema"])
        self.assertEqual("human:reviewer", view["status"]["owner"]["id"])
        self.assertIn("具名", view["status"]["next_action"])
        self.assertEqual(0, view["eval"]["blocking_failed"])
        self.assertEqual("reports/TASK-1234567890-harness-blocking.json", view["eval"]["report_path"])
        self.assertEqual("a" * 64, view["eval"]["report_sha256"])
        self.assertEqual(
            [{"name": "stock_never_negative", "level": "blocking", "passed": True}],
            view["eval"]["cases"],
        )
        compact = build_delivery_view(
            task,
            include_detail=False,
            now=datetime(2026, 8, 31, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual([], compact["eval"]["results"])
        self.assertEqual(view["eval"]["cases"], compact["eval"]["cases"])
        self.assertEqual(view["eval"]["report_path"], compact["eval"]["report_path"])
        self.assertEqual(view["eval"]["report_sha256"], compact["eval"]["report_sha256"])
        self.assertIn("approve", view["allowed_actions"])
        self.assertTrue(view["integrity"]["truthful"])
        self.assertEqual("workbench.control-surface/v1", view["control_surface"]["schema"])
        self.assertIn("控制面", view["control_surface"]["thesis"])
        self.assertEqual(
            ["loop", "tools", "context", "guardrails"],
            [item["id"] for item in view["control_surface"]["components"]],
        )
        guardrails = next(item for item in view["control_surface"]["components"] if item["id"] == "guardrails")
        self.assertEqual("observed", guardrails["state"])
        self.assertEqual("pass", guardrails["evidence"]["eval_decision"])

    def test_unknown_status_is_explicitly_untrusted(self) -> None:
        view = build_delivery_view({
            "id": "TASK-UNKNOWN000",
            "status": "mystery",
            "updated_at": "2026-08-31 01:00:00",
        }, now=datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc))
        self.assertFalse(view["status"]["known"])
        self.assertEqual("未知状态", view["status"]["label"])
        self.assertTrue(view["status"]["requires_human"])
        self.assertIn("unknown_status", view["integrity"]["issues"])
        self.assertFalse(view["integrity"]["truthful"])

    def test_control_surface_reports_missing_gates_after_execution_started(self) -> None:
        task = {
            "id": "TASK-MISSING00",
            "request": "长任务翻车诊断",
            "status": "executing",
            "automation_mode": "automatic",
            "execution_mode": "codex",
            "write_scope": [],
            "execution_timeout_seconds": 900,
            "spec_path": "FDE_SPEC.md",
            "business_refs": [],
            "events": [],
            "updated_at": "2026-08-31 01:00:00",
        }
        view = build_delivery_view(task, now=datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc))
        self.assertIn("loop_event_missing", view["control_surface"]["issues"])
        self.assertIn("tool_permissions_missing", view["control_surface"]["issues"])
        self.assertIn("write_scope_missing", view["control_surface"]["issues"])
        self.assertFalse(view["control_surface"]["ready"])
        self.assertFalse(view["integrity"]["truthful"])

    def test_service_joins_task_feedback_and_evolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tasks.db"
            tasks = TaskStore(path)
            task = tasks.create("复现页面状态漂移", "REQ-VIEW-002", ["SKU:NOTEBOOK-AI"])
            feedback = add_feedback(task["id"], "web-review", "详情与列表不一致", "固定投影合同", str(path))
            review_feedback(feedback["id"], "reviewer-a", "accept", "已复现", str(path))
            evolutions = EvolutionStore(path)
            evolution = evolutions.create(
                feedback["id"], "delivery/status-drift", "workbench_observability", None, "reviewer-a",
            )
            service = DeliveryViewService(tasks, evolutions)
            view = service.get(task["id"])
            listing = service.list()
            self.assertEqual(1, view["feedback"]["accepted"])
            self.assertEqual(evolution["id"], view["evolution"]["items"][0]["id"])
            self.assertEqual(task["id"], listing["items"][0]["task_id"])
            self.assertEqual(1, listing["summary"]["active"])

    def test_standalone_harness_exposes_the_same_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            api = HarnessPlatformAPI(root / "harness", root)
            task = api.tasks.create("验证统一投影", "REQ-VIEW-003", ["PROJECT:TEST"])
            response = api.dispatch("GET", f"/api/v1/delivery/views/{task['id']}", {}, {})
            self.assertEqual(200, response.status)
            self.assertEqual("workbench.delivery-view/v1", response.body["schema"])
            self.assertEqual(task["id"], response.body["task_id"])


if __name__ == "__main__":
    unittest.main()
