from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from workbench.cli import main as cli_main
from workbench.cockpit import classify_lane, current_course
from workbench.evolution import EvolutionStore
from workbench.feedback import add_feedback, review_feedback
from workbench.task_store import TaskStore
from workbench.workbench_server import WorkbenchApp


class WorkbenchServerTests(unittest.TestCase):
    def test_health_separates_workbench_from_erp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(temporary, port=8001)
            health = app.health()
            self.assertEqual(health["surface"], "workbench")
            self.assertIn("8000", health["erp_url"])
            self.assertTrue(str(health["database"]).endswith("workbench.db"))
            self.assertIsNone(health["erp_database"])
            self.assertEqual(health["thesis"]["workbench"], "研发工作台是写代码的主体")
            self.assertIn("客户项目", health["message"])
            store = TaskStore(Path(temporary) / "workbench.db")
            store.create("工作台身份检查", "REQ-WB-1", ["REQUIREMENT:COURSE-L01"])
            listing = app.views.list(10)
            self.assertGreaterEqual(listing["summary"]["total"], 1)

    def test_isolated_customer_address_does_not_point_to_default_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(temporary, port=8132, erp_url='http://127.0.0.1:8131')
            self.assertEqual('http://127.0.0.1:8131', app.health()['erp_url'])
            for url in ('javascript:alert(1)', 'http://example.com', 'http://127.0.0.1:8132',
                        'http://name:password@127.0.0.1:8131', 'http://127.0.0.1:8131/#other'):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    WorkbenchApp(temporary, port=8132, erp_url=url)

    def test_course_current_uses_geekbang_theme_and_honest_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(temporary, port=8001)
            current = app.course_current(1)
            self.assertEqual(current["title"], "以终为始：一次可验证的 AI 交付怎样完成？")
            self.assertEqual(current["stage_band"], "造工作台")
            self.assertTrue(current["honest_empty"])
            self.assertEqual(current["bootstrap_state"], "not_constructed")
            self.assertFalse(current["optional_harness_required"])
            self.assertEqual(current["optional_harness_port"], 8010)
            self.assertIn("Codex 是底座", current["thesis"]["codex"])
            self.assertIn("eval_cases", current)
            self.assertTrue(app.course_current(14)["eval_cases"])

    def test_tasks_split_workbench_and_erp_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(temporary, port=8001)
            store = app.tasks
            store.create("自举工作台", "REQ-WB-L01", ["PERSONAL-WORKBENCH"])
            store.create("交付库存导出", "REQ-COURSE-L04", ["SKU:COURSE-DEMO"])
            items = {item["requirement_id"]: item["lane"] for item in app.views.list(10)["items"]}
            self.assertEqual(items["REQ-WB-L01"], "workbench")
            self.assertEqual(items["REQ-COURSE-L04"], "erp")
            self.assertEqual("bootstrapped", app.course_current()["bootstrap_state"])

    def test_upgrade_projects_latest_workbench_evolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(temporary, port=8001)
            empty = app.upgrade()
            self.assertFalse(empty["available"])
            task = app.tasks.create("从失败升级护栏", "REQ-COURSE-L07", ["ORDER:COURSE-DEMO"])
            feedback = add_feedback(
                task["id"], "eval", "Hook 漏跑", "把同一 Harness 接进 Hook",
                app.tasks.path,
            )
            review_feedback(feedback["id"], "reviewer", "accept", "采用", app.tasks.path)
            EvolutionStore(app.tasks.path).create(
                feedback["id"], "hook-missed-harness", "workbench_control",
                ["ORDER:COURSE-DEMO"], actor="reviewer",
            )
            upgrade = app.upgrade()
            self.assertTrue(upgrade["available"])
            self.assertEqual(upgrade["classification"], "workbench_control")

    def test_web_submission_creates_a_traceable_verify_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(temporary, port=8001)
            created = app.create_course_task(
                14,
                "断开 API 后页面必须显示不可用，不能保留绿色假状态",
                "student-qa",
            )
            self.assertEqual(created["lane"], "erp")
            self.assertEqual(created["requirement_id"], "REQ-COURSE-L14")
            self.assertEqual(created["policy"]["execution_mode"], "verify")
            self.assertTrue(created["policy"]["write_scope"])
            restored = app.tasks.get(created["task_id"])
            self.assertEqual(restored["status"], "queued")
            self.assertEqual(restored["events"][-1]["actor"], "student-qa")
            self.assertIn("尚未授权 Codex", restored["events"][-1]["detail"])

    def test_submit_and_verify_runs_lesson_eval_and_stops_for_humans(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(temporary, port=8001)
            view = app.submit_and_verify(
                14,
                "断开 API 后页面必须显示不可用，不能保留绿色假状态",
                "student-qa",
            )
            self.assertIn(view["status"]["code"], {"review", "rework"})
            self.assertTrue(view["spec"]["available"])
            self.assertTrue(view["spec"]["goal"])
            self.assertTrue(view["eval"]["available"])
            self.assertTrue(view["eval"]["cases"])
            self.assertTrue(view["status"]["next_action"])
            names = [item["name"] for item in view["eval"]["cases"]]
            self.assertIn("no_committed_secrets", names)
            if view["status"]["code"] == "review":
                self.assertIn("approve", view["allowed_actions"])
                rejected = app.review_task(view["task_id"], "teacher-li", "reject", "先核对手动操作页再批")
                self.assertEqual("rework", rejected["status"]["code"])
                rerun = app.verify_task(rejected["task_id"], "student-qa")
                self.assertIn(rerun["status"]["code"], {"review", "rework"})
                if rerun["status"]["code"] == "review":
                    done = app.review_task(rerun["task_id"], "teacher-li", "approve", "Spec 与 Eval 一致")
                    self.assertEqual("completed", done["status"]["code"])
                    self.assertEqual("approve", done["review"]["decision"])
            else:
                rerun = app.verify_task(view["task_id"], "student-qa")
                self.assertIn(rerun["status"]["code"], {"review", "rework"})

    def test_web_submission_rejects_uncontrolled_or_anonymous_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(temporary, port=8001)
            for lesson, request, actor in ((3, "越过换挡", "student"), (14, "", "student"), (14, "状态页", "")):
                with self.subTest(lesson=lesson, request=request, actor=actor):
                    with self.assertRaises(ValueError):
                        app.create_course_task(lesson, request, actor)

    def test_cli_help_groups_workbench_and_optional_platform(self) -> None:
        with patch("sys.stdout", new_callable=StringIO) as stdout:
            with self.assertRaises(SystemExit) as raised:
                with patch("sys.argv", ["workbench", "-h"]):
                    cli_main()
        self.assertEqual(0, raised.exception.code)
        text = stdout.getvalue()
        self.assertIn("工作台:", text)
        self.assertIn("客户项目:", text)
        self.assertIn("非大纲通过项", text)
        self.assertIn("serve-workbench", text)


class CockpitProjectionTests(unittest.TestCase):
    def test_classify_lane_and_default_lesson(self) -> None:
        self.assertEqual("workbench", classify_lane({
            "requirement_id": "REQ-COURSE-L01", "business_refs": [],
        }))
        self.assertEqual("erp", classify_lane({
            "requirement_id": "REQ-COURSE-L05", "business_refs": ["SKU:COURSE-DEMO"],
        }))
        current = current_course(4, tasks=[{"requirement_id": "REQ-COURSE-L04"}])
        self.assertEqual("换挡", current["stage_band"])


if __name__ == "__main__":
    unittest.main()
