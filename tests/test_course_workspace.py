from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import time
from pathlib import Path

from workbench.course_workspace import CourseWorktreeManager, LessonSubprocessEvalRunner, differential_evidence


class CourseWorkspaceTests(unittest.TestCase):
    @staticmethod
    def report(passed=True, name="case-a"):
        return {
            "schema_version": "1.0", "suite": "blocking", "requested_cases": [name],
            "summary": {"total": 1, "passed": int(passed), "blocking_failed": int(not passed),
                        "observing_failed": 0, "decision": "pass" if passed else "block"},
            "results": [{"name": name, "level": "blocking", "passed": passed, "evidence": "test fixture"}],
        }

    def test_subprocess_eval_never_reuses_previous_green(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "worktree"; workspace.mkdir()
            old = root / "runtime/reports/TASK-1234567890-post.json"
            old.parent.mkdir(parents=True)
            old.write_text(json.dumps(self.report()), encoding="utf-8")
            def no_report(command, **kwargs):
                return subprocess.CompletedProcess(command, 2, stdout="", stderr="could not start")
            runner = LessonSubprocessEvalRunner(workspace, root / "runtime", "TASK-1234567890", ("case-a",), "post",
                                               process_runner=no_report)
            with self.assertRaisesRegex(RuntimeError, "未生成报告"):
                runner()
            self.assertTrue(old.is_file(), "旧报告应保留为失败调查证据")

    def test_subprocess_rejects_inconsistent_or_incomplete_reports(self) -> None:
        invalid = [
            (self.report(), 1, "退出码"),
            (self.report(False), 0, "退出码"),
            ({**self.report(), "results": []}, 0, "用例"),
            (self.report(name="other"), 0, "用例"),
            ({**self.report(), "summary": {"decision": "pass"}}, 0, "汇总"),
            ({**self.report(), "results": [{"name": "case-a", "level": "blocking", "passed": "false"}]}, 0, "布尔"),
        ]
        for payload, returncode, message in invalid:
            with self.subTest(message=message, payload=payload), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "worktree"; workspace.mkdir()
                def fake_run(command, **kwargs):
                    report = Path(command[command.index("--report-path") + 1])
                    report.parent.mkdir(parents=True, exist_ok=True)
                    report.write_text(json.dumps(payload), encoding="utf-8")
                    return subprocess.CompletedProcess(command, returncode, stdout="", stderr="")
                with self.assertRaisesRegex(RuntimeError, message):
                    LessonSubprocessEvalRunner(workspace, root / "runtime", "TASK-1234567890", ("case-a",), "post",
                                               process_runner=fake_run)()

    def test_each_eval_attempt_keeps_a_separate_report_and_process_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); workspace = root / "worktree"; workspace.mkdir()
            paths = []
            def fake_run(command, **kwargs):
                report = Path(command[command.index("--report-path") + 1]); paths.append(report)
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps(self.report()), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="actual stdout", stderr="actual stderr")
            runner = LessonSubprocessEvalRunner(workspace, root / "runtime", "TASK-1234567890", ("case-a",), "post",
                                               process_runner=fake_run)
            first, second = runner(), runner()
            self.assertNotEqual(paths[0], paths[1])
            self.assertTrue(all(path.is_file() for path in paths))
            self.assertNotEqual(first["runner"]["attempt_id"], second["runner"]["attempt_id"])
            receipt_path = Path(second["runner"]["receipt_path"])
            # Windows scanners can briefly hold a just-closed temporary file.
            for attempt in range(5):
                try:
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
            self.assertEqual("actual stderr", receipt["stderr"])
            self.assertEqual("actual stdout", receipt["stdout"])
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

    def test_prepare_strips_this_lesson_increment(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            runtime = root / ".runtime"

            def fake_runner(command, **kwargs):
                if command[1:4] == ["worktree", "add", "--detach"]:
                    dest = Path(command[4])
                    dest.mkdir(parents=True)
                    (dest / "flowerp").mkdir()
                    (dest / "flowerp" / "service.py").write_text(
                        (source / "flowerp" / "service.py").read_text(encoding="utf-8"), encoding="utf-8",
                    )
                    (dest / "docs" / "courses" / "labs" / "baselines").mkdir(parents=True)
                    (dest / "docs" / "courses" / "labs" / "baselines" / "PROGRESSION.json").write_text("{}", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, stdout="prepared", stderr="")
                return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")

            result = CourseWorktreeManager(root, runtime, process_runner=fake_runner).prepare(
                "TASK-1234567890", "course/l05-start", lesson_number=5,
            )
            start = result["student_start"]
            self.assertTrue(start["removed_progression_gate"])
            self.assertIn("flowerp/service.py", start["applied_overlays"])
            text = (runtime / "course-worktrees/TASK-1234567890/flowerp/service.py").read_text(encoding="utf-8")
            self.assertIn("if False and exists:", text)

    def test_prepare_for_lesson_falls_back_to_head_when_tag_missing(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            runtime = root / ".runtime"
            revisions: list[str] = []

            def fake_runner(command, **kwargs):
                if command[1] == "rev-parse" and "refs/tags/course/l03-start" in command[-1]:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing")
                if command[1:4] == ["worktree", "add", "--detach"]:
                    revisions.append(command[-1])
                    dest = Path(command[4])
                    dest.mkdir(parents=True)
                    (dest / "workbench").mkdir()
                    (dest / "workbench" / "spec.py").write_text(
                        (source / "workbench" / "spec.py").read_text(encoding="utf-8"), encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(command, 0, stdout="prepared", stderr="")
                return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")

            result = CourseWorktreeManager(root, runtime, process_runner=fake_runner).prepare_for_lesson(3)
            self.assertEqual("progression_gate", result["baseline_semantics"])
            self.assertIn("缺少 course/l03-start", result["warning"])
            self.assertEqual(["HEAD"], revisions)
            self.assertIn("workbench/spec.py", result["student_start"]["applied_overlays"])

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
                report_path.write_text(json.dumps(self.report(False)), encoding="utf-8")
                self.assertEqual(Path(kwargs["cwd"]), workspace.resolve())
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

            report = LessonSubprocessEvalRunner(
                workspace, runtime, "TASK-1234567890", ("case-a",), "pre",
                process_runner=fake_runner,
            )()
            self.assertEqual(report["summary"]["decision"], "block")
            self.assertEqual(report["runner"]["workspace"], str(workspace.resolve()))

    def test_differential_requires_red_change_green_and_real_execution(self) -> None:
        pre = self.report(False)
        post = self.report()
        pre["runner"] = {"workspace": "candidate", "process_returncode": 1, "validated": True, "attempt_id": "pre-run"}
        post["runner"] = {"workspace": "candidate", "process_returncode": 0, "validated": True, "attempt_id": "post-run"}
        task = {"events": [{
            "detail": "受控执行阶段完成",
            "evidence": {"success": True, "mode": "codex_exec", "changed_files": ["flowerp/service.py"], "out_of_scope_files": []},
        }]}
        self.assertTrue(differential_evidence(pre, post, task)["accepted"])
        task["events"][0]["evidence"]["success"] = False
        self.assertFalse(differential_evidence(pre, post, task)["accepted"])
        task["events"][0]["evidence"]["success"] = True
        task["events"][0]["evidence"]["changed_files"] = []
        self.assertFalse(differential_evidence(pre, post, task)["accepted"])

    def test_differential_rejects_different_cases_workspaces_and_reused_attempt(self) -> None:
        task = {"events": [{"detail": "受控执行阶段完成", "evidence": {
            "mode": "codex_exec", "changed_files": ["flowerp/service.py"], "out_of_scope_files": []}}]}
        pre = self.report(False)
        pre["runner"] = {"workspace": "candidate", "process_returncode": 1, "validated": True, "attempt_id": "before"}
        for case, workspace, attempt in (("other", "candidate", "after"), ("case-a", "controller", "after"),
                                          ("case-a", "candidate", "before")):
            post = self.report(name=case)
            post["runner"] = {"workspace": workspace, "process_returncode": 0, "validated": True, "attempt_id": attempt}
            with self.subTest(case=case, workspace=workspace, attempt=attempt):
                self.assertFalse(differential_evidence(pre, post, task)["accepted"])

    def test_differential_cannot_accept_empty_summary_only_reports(self) -> None:
        pre = {"summary": {"decision": "block", "blocking_failed": 1}}
        post = {"summary": {"decision": "pass", "blocking_failed": 0}}
        task = {"events": [{"detail": "受控执行阶段完成", "evidence": {
            "mode": "codex_exec", "changed_files": ["flowerp/service.py"], "out_of_scope_files": []}}]}
        self.assertFalse(differential_evidence(pre, post, task)["accepted"])


if __name__ == "__main__":
    unittest.main()
