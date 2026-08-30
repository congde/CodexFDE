from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from workbench.course_workspace import CourseWorktreeManager, LessonSubprocessEvalRunner, differential_evidence


class CourseWorkspaceTests(unittest.TestCase):
    def test_worktree_is_created_from_exact_lesson_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"; root.mkdir()
            runtime = root / ".runtime"
            commands: list[list[str]] = []

            def fake_runner(command, **kwargs):
                commands.append(command)
                if command[1:4] == ["worktree", "add", "--detach"]:
                    Path(command[4]).mkdir(parents=True)
                    return subprocess.CompletedProcess(command, 0, stdout="prepared", stderr="")
                return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")

            result = CourseWorktreeManager(root, runtime, process_runner=fake_runner).prepare(
                "TASK-1234567890", "course/l04-start",
            )
            self.assertEqual(result["baseline_commit"], "abc123")
            self.assertTrue(result["detached"])
            self.assertEqual(commands[0][-1], "refs/tags/course/l04-start")
            self.assertTrue((runtime / "course-worktrees/TASK-1234567890.json").is_file())

    def test_existing_worktree_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / ".runtime"
            target = runtime / "course-worktrees/TASK-1234567890"
            target.mkdir(parents=True)
            manager = CourseWorktreeManager(temporary, runtime)
            with self.assertRaises(FileExistsError):
                manager.prepare("TASK-1234567890", "course/l04-start")

    def test_qualified_session_baseline_is_used_without_tag_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"; root.mkdir()
            commands: list[list[str]] = []

            def fake_runner(command, **kwargs):
                commands.append(command)
                if command[1:4] == ["worktree", "add", "--detach"]:
                    Path(command[4]).mkdir(parents=True)
                return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")

            CourseWorktreeManager(root, root / ".runtime", process_runner=fake_runner).prepare(
                "TASK-1234567890", "refs/heads/live-eval^{commit}", qualified_ref=True,
            )
            self.assertEqual("refs/heads/live-eval^{commit}", commands[0][-1])

    def test_subprocess_eval_reads_report_from_isolated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); workspace = root / "worktree"; workspace.mkdir()
            runtime = root / "runtime"

            def fake_runner(command, **kwargs):
                report_path = Path(command[command.index("--report-path") + 1])
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps({
                    "summary": {"decision": "block", "blocking_failed": 1}, "results": [],
                }), encoding="utf-8")
                self.assertEqual(Path(kwargs["cwd"]), workspace.resolve())
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

            report = LessonSubprocessEvalRunner(
                workspace, runtime, "TASK-1234567890", ("case-a",), "pre",
                process_runner=fake_runner,
            )()
            self.assertEqual(report["summary"]["decision"], "block")
            self.assertEqual(report["runner"]["workspace"], str(workspace.resolve()))

    def test_differential_requires_red_change_green_and_real_execution(self) -> None:
        pre = {"summary": {"decision": "block", "blocking_failed": 1}}
        post = {"summary": {"decision": "pass", "blocking_failed": 0}}
        task = {"events": [{
            "detail": "受控执行阶段完成",
            "evidence": {"mode": "codex_exec", "changed_files": ["flowerp/service.py"], "out_of_scope_files": []},
        }]}
        self.assertTrue(differential_evidence(pre, post, task)["accepted"])
        task["events"][0]["evidence"]["changed_files"] = []
        self.assertFalse(differential_evidence(pre, post, task)["accepted"])


if __name__ == "__main__":
    unittest.main()
