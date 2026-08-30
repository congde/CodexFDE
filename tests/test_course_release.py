from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from workbench.course_release import CourseBaselinePublisher, CourseCandidateArtifacts
from workbench.task_store import TaskStore


class CourseReleaseTests(unittest.TestCase):
    def test_baseline_audit_requires_previous_green_and_current_red(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "review.md"; evidence.write_text("reviewer: teacher-a", encoding="utf-8")
            commands: list[list[str]] = []

            def git_runner(command, **_kwargs):
                commands.append(command)
                if command[1:4] == ["rev-parse", "--verify", "refs/tags/course/l05-start"]:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
                if command[1:4] == ["rev-parse", "--verify", "refs/tags/course/l04-start"]:
                    return subprocess.CompletedProcess(command, 0, stdout="previous\n", stderr="")
                if command[1:3] == ["rev-parse", "--verify"]:
                    return subprocess.CompletedProcess(command, 0, stdout="candidate\n", stderr="")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            def eval_factory(_workspace, _cases, label):
                if label == "l05-red":
                    return {"summary": {"decision": "block", "blocking_failed": 1}}
                return {"summary": {"decision": "pass", "blocking_failed": 0}}

            result = CourseBaselinePublisher(
                root, root / ".runtime", process_runner=git_runner, eval_factory=eval_factory,
            ).audit(5, "candidate-ref", evidence)
            self.assertTrue(result["accepted"])
            self.assertTrue(result["checks"]["previous_lesson_is_green"])
            self.assertTrue(result["checks"]["current_lesson_is_red"])
            self.assertTrue(any(command[1:4] == ["worktree", "remove", "--force"] for command in commands))

    def test_baseline_audit_rejects_already_green_lesson(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "review.md"; evidence.write_text("review", encoding="utf-8")

            def git_runner(command, **_kwargs):
                if command[1:4] == ["rev-parse", "--verify", "refs/tags/course/l04-start"]:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
                if command[1:4] == ["rev-parse", "--verify", "refs/tags/course/l03-start"]:
                    return subprocess.CompletedProcess(command, 0, stdout="previous\n", stderr="")
                if command[1:3] == ["rev-parse", "--verify"]:
                    return subprocess.CompletedProcess(command, 0, stdout="candidate\n", stderr="")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = CourseBaselinePublisher(
                root, root / ".runtime", process_runner=git_runner,
                eval_factory=lambda *_args: {"summary": {"decision": "pass", "blocking_failed": 0}},
            ).audit(4, "candidate-ref", evidence)
            self.assertFalse(result["accepted"])
            self.assertFalse(result["checks"]["current_lesson_is_red"])

    def test_dynamic_lesson_defers_new_red_eval_to_session_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "review.md"; evidence.write_text("review", encoding="utf-8")

            def git_runner(command, **_kwargs):
                revision = command[3] if command[1:3] == ["rev-parse", "--verify"] else ""
                if revision == "refs/tags/course/l15-start":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
                if command[1:3] == ["rev-parse", "--verify"]:
                    return subprocess.CompletedProcess(command, 0, stdout=("previous\n" if "l14" in revision else "candidate\n"), stderr="")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = CourseBaselinePublisher(
                root, root / ".runtime", process_runner=git_runner,
                eval_factory=lambda *_args: {"summary": {"decision": "pass", "blocking_failed": 0}},
            ).audit(15, "candidate-ref", evidence)
            self.assertTrue(result["accepted"])
            self.assertTrue(result["checks"]["static_contract_is_green"])
            self.assertTrue(result["checks"]["dynamic_red_deferred_to_session"])
            self.assertNotIn("current_lesson_is_red", result["checks"])

    def _approved_task(self, store: TaskStore) -> dict:
        task = store.create("候选交付", "REQ-COURSE-EXPORT", ["SKU:COURSE-DEMO"])
        store.transition(task["id"], "spec_ready", "spec", spec={"goal": "candidate"})
        store.transition(task["id"], "executing", "execute")
        store.transition(task["id"], "evaluating", "evaluate")
        store.transition(task["id"], "review", "green", result={
            "summary": {"decision": "pass", "blocking_failed": 0},
        })
        store.append_event(task["id"], "课程红绿差分判定已完成", evidence={
            "accepted": True, "changed_files": ["flowerp/service.py"],
        })
        return store.review(task["id"], "reviewer-a", "approve", "红绿差分和业务状态均通过")

    def test_approved_candidate_can_export_a_hashed_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); runtime = root / ".runtime"; store = TaskStore(runtime / "workbench.db")
            task = self._approved_task(store)
            workspace = runtime / "course-worktrees" / task["id"]; workspace.mkdir(parents=True)
            (runtime / "course-worktrees" / f"{task['id']}.json").write_text(json.dumps({
                "path": str(workspace), "baseline_commit": "abc123",
            }), encoding="utf-8")

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(
                    command, 0, stdout="diff --git a/flowerp/service.py b/flowerp/service.py\n+fixed\n", stderr="",
                )

            manifest = CourseCandidateArtifacts(root, runtime, process_runner=runner).export(store, task["id"])
            self.assertEqual(manifest["reviewed_by"], "reviewer-a")
            self.assertTrue(Path(manifest["patch_path"]).is_file())
            self.assertEqual(64, len(manifest["patch_sha256"]))

    def test_unapproved_candidate_cannot_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); runtime = root / ".runtime"; store = TaskStore(runtime / "workbench.db")
            task = store.create("未审核候选")
            with self.assertRaisesRegex(ValueError, "具名审核"):
                CourseCandidateArtifacts(root, runtime).export(store, task["id"])


if __name__ == "__main__":
    unittest.main()
