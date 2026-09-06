from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from eval.harness import EVALS
from workbench.course_mainline import COURSE_BUILD_THESIS, LESSONS, LESSON_STORY, create_lesson_task, lesson_baseline_status, lesson_contract, render_lesson_spec, validate_mainline, write_lesson_spec
from workbench.spec import parse_spec
from workbench.task_store import TaskStore
from workbench.workflow import prepare_task


class CourseMainlineTests(unittest.TestCase):
    def test_contract_is_a_continuous_16_lesson_chain(self) -> None:
        self.assertEqual([item.number for item in LESSONS], list(range(1, 17)))
        self.assertEqual(LESSONS[0].prerequisites, ())
        for item in LESSONS[1:]:
            self.assertEqual(item.prerequisites, (item.number - 1,))
        self.assertTrue(LESSONS[-1].live_request)
        self.assertFalse(LESSONS[13].dynamic_eval_required)
        self.assertTrue(LESSONS[14].dynamic_eval_required)
        self.assertTrue(LESSONS[15].dynamic_eval_required)

    def test_product_and_workbench_both_advance_from_l04(self) -> None:
        for item in LESSONS[3:]:
            self.assertTrue(item.workbench_increment)
            self.assertTrue(item.erp_increment)
            self.assertTrue(item.write_scope)
            self.assertTrue(item.acceptance)

    def test_codex_fde_story_covers_bootstrap_and_workbench_driven_delivery(self) -> None:
        self.assertEqual(set(range(1, 17)), set(LESSON_STORY))
        for lesson in LESSONS:
            story = lesson.as_dict()
            with self.subTest(lesson=lesson.number):
                self.assertTrue(story["codex_role"])
                self.assertIn("循环", story["fde_loop"])
                self.assertTrue(story["causal_link"])
                self.assertEqual(COURSE_BUILD_THESIS, story["course_build_thesis"])
                self.assertTrue(story["construction_stage"])
        self.assertIn("共同建造者", LESSON_STORY[3]["codex_role"])
        self.assertIn("直接监督 Codex", LESSONS[2].request)
        self.assertIn("协同换挡伙伴", LESSON_STORY[4]["codex_role"])
        self.assertIn("Workbench V0", LESSON_STORY[4]["codex_role"])
        self.assertIn("两张 Ticket", LESSONS[3].request)
        self.assertIn("现场开发伙伴", LESSON_STORY[16]["codex_role"])

    def test_all_declared_eval_cases_exist(self) -> None:
        available = {name for name, _level, _fn in EVALS}
        declared = {name for item in LESSONS for name in item.eval_cases}
        self.assertFalse(declared - available)
        for lesson in LESSONS[3:]:
            self.assertTrue(lesson.eval_cases, f"L{lesson.number:02d} 没有课程级代码 Eval")

    def test_lesson_spec_is_scoped_instead_of_end_state_spec(self) -> None:
        text = render_lesson_spec(5)
        parsed = parse_spec(text)
        self.assertIn("幂等入库", parsed.goal)
        self.assertIn("receiving_is_idempotent", parsed.constraints)
        self.assertIn("Codex 当讲角色", text)
        self.assertIn("FDE 循环", text)
        self.assertIn("因果交接", text)
        self.assertIn("课程建设主线", text)
        self.assertIn("通过个人工作台组织人与 AI 协同开发 FlowERP", text)
        self.assertIn("本讲所在阶段", text)
        self.assertNotIn("采购补货必须经过具名人工审批", text)
        self.assertNotIn("原子预占", parsed.acceptance)

    def test_write_lesson_spec_publishes_parseable_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = write_lesson_spec(8, Path(temporary) / "FDE_SPEC.md")
            self.assertTrue(target.is_file())

    def test_dynamic_eval_is_written_into_live_lesson_spec(self) -> None:
        text = render_lesson_spec(16, ("live_draw_rule_is_enforced",))
        self.assertIn("live_draw_rule_is_enforced", parse_spec(text).constraints)

    def test_status_distinguishes_valid_contract_from_missing_baselines(self) -> None:
        available = [name for name, _level, _fn in EVALS]
        result = validate_mainline(".", ref_checker=lambda _root, _ref: False, eval_names=available)
        self.assertTrue(result["contract_valid"])
        self.assertFalse(result["course_ready"])
        self.assertEqual(len(result["missing_baseline_refs"]), 16)

    def test_status_rejects_many_tags_pointing_to_terminal_commit(self) -> None:
        available = [name for name, _level, _fn in EVALS]
        result = validate_mainline(
            ".", ref_checker=lambda _root, _ref: True, eval_names=available,
            commit_resolver=lambda _root, _ref: "terminal-commit",
            ancestor_checker=lambda _root, _older, _newer: True,
        )
        self.assertFalse(result["course_ready"])
        self.assertTrue(any("同一提交" in error for error in result["baseline_errors"]))

    def test_status_accepts_distinct_linear_baseline_commits(self) -> None:
        available = [name for name, _level, _fn in EVALS]
        commits = {item.baseline_ref: f"commit-{item.number:02d}" for item in LESSONS}
        result = validate_mainline(
            ".", ref_checker=lambda _root, _ref: True, eval_names=available,
            commit_resolver=lambda _root, ref: commits[ref],
            ancestor_checker=lambda _root, _older, _newer: True,
        )
        self.assertTrue(result["course_ready"])
        self.assertFalse(result["baseline_errors"])

    def test_default_status_batches_git_queries_and_caches_unchanged_history(self) -> None:
        available = [name for name, _level, _fn in EVALS]
        commits = [f"fake-course-commit-{number:02d}" for number in range(1, 17)]
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            if command[1] == "cat-file":
                return SimpleNamespace(
                    returncode=0,
                    stdout="".join(f"{commit} commit\n" for commit in commits),
                )
            if command[1] == "rev-list":
                lines = []
                for index in range(15, -1, -1):
                    parent = f" {commits[index - 1]}" if index else ""
                    lines.append(f"{commits[index]}{parent}")
                return SimpleNamespace(returncode=0, stdout="\n".join(lines) + "\n")
            raise AssertionError(command)

        with patch("workbench.course_mainline.subprocess.run", side_effect=fake_run):
            first = validate_mainline(".", eval_names=available)
            second = validate_mainline(".", eval_names=available)

        self.assertTrue(first["course_ready"])
        self.assertEqual(first["baseline_semantics"], "progression_gate")
        self.assertIn("隔离工作区", first["constructibility"])
        self.assertTrue(second["course_ready"])
        self.assertIn("checked_at", second)
        self.assertEqual(2, sum(command[1] == "cat-file" for command in calls))
        self.assertEqual(1, sum(command[1] == "rev-list" for command in calls))

    def test_course_task_consumes_lesson_spec_and_write_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary) / "workbench.db")
            task = create_lesson_task(store, 9, temporary)
            prepared = prepare_task(store, task["id"])
            self.assertEqual(prepared["requirement_id"], "REQ-COURSE-L09")
            self.assertEqual(prepared["write_scope"], [item.rstrip("/") for item in lesson_contract(9).write_scope])
            self.assertIn("取消释放预占", prepared["spec"]["goal"])

    def test_lesson_number_is_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "1 到 16"):
            lesson_contract(17)

    def test_missing_baseline_cannot_look_ready(self) -> None:
        result = lesson_baseline_status(".", 4, revision_resolver=lambda _root, _revision: None)
        self.assertEqual(result["baseline_ref"], "course/l04-start")
        self.assertFalse(result["ready"])


if __name__ == "__main__":
    unittest.main()
