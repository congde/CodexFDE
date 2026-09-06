from __future__ import annotations

import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from eval.report_contract import validate_report


ProcessRunner = Callable[..., subprocess.CompletedProcess]


class CourseWorktreeManager:
    """Prepare an isolated, detached workspace from a reviewed lesson baseline."""

    def __init__(self, repository_root: str | Path, runtime_dir: str | Path, *,
                 process_runner: ProcessRunner | None = None) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.process_runner = process_runner or subprocess.run

    def prepare(self, task_id: str, baseline_ref: str, *, qualified_ref: bool = False,
                lesson_number: int | None = None) -> dict:
        if not task_id.startswith("TASK-") or "/" in task_id or "\\" in task_id:
            raise ValueError("任务编号不能用于隔离工作区路径")
        target = self.runtime_dir / "course-worktrees" / task_id
        if target.exists():
            raise FileExistsError(f"课程隔离工作区已存在：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@{}^~:-]{0,199}", baseline_ref):
            raise ValueError("课程基线引用格式无效")
        revision = baseline_ref if qualified_ref else f"refs/tags/{baseline_ref}"
        completed = self.process_runner(
            ["git", "worktree", "add", "--detach", str(target), revision],
            cwd=self.repository_root, text=True, capture_output=True, check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"创建课程隔离 Worktree 失败：{(completed.stderr or completed.stdout).strip()}")
        baseline_commit = self._revision(self.repository_root, f"{revision}^{{commit}}")
        worktree_commit = self._revision(target, "HEAD")
        if not baseline_commit or baseline_commit != worktree_commit:
            raise RuntimeError("隔离 Worktree 的 HEAD 与课程起始标签不一致")
        evidence = {
            "mode": "git_worktree",
            "path": str(target),
            "baseline_ref": baseline_ref,
            "baseline_commit": baseline_commit,
            "detached": True,
        }
        if lesson_number:
            from .lesson_constructibility import apply_student_start
            evidence["student_start"] = apply_student_start(target, lesson_number)
        metadata = self.runtime_dir / "course-worktrees" / f"{task_id}.json"
        metadata.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return evidence

    def prepare_for_lesson(self, lesson_number: int, *, task_id: str | None = None, source: str = "baseline") -> dict:
        """Create a student sandbox: tagged start if published, otherwise HEAD plus overlays."""
        if source == "working-tree":
            from .course_snapshot import prepare_source_snapshot
            return prepare_source_snapshot(self.repository_root, self.runtime_dir, lesson_number, task_id)
        if source != "baseline":
            raise ValueError("未知课程起点来源")
        if not 1 <= lesson_number <= 16:
            raise ValueError("课次必须在 1 到 16 之间")
        tag = f"course/l{lesson_number:02d}-start"
        tag_commit = self._revision(self.repository_root, f"refs/tags/{tag}^{{commit}}")
        stamp = task_id or f"TASK-PREP-L{lesson_number:02d}"
        if (self.runtime_dir / "course-worktrees" / stamp).exists():
            raise FileExistsError(f"课程隔离工作区已存在：{stamp}。先 course-worktree-clean 或换 --task-id")
        if tag_commit:
            evidence = self.prepare(stamp, tag, lesson_number=lesson_number)
            evidence["baseline_semantics"] = "tagged_start"
            return evidence
        evidence = self.prepare(stamp, "HEAD", qualified_ref=True, lesson_number=lesson_number)
        evidence["baseline_semantics"] = "progression_gate"
        evidence["warning"] = (
            f"缺少 {tag}。本隔离区从当前 HEAD 剥离本讲增量，只能作跟跑预习，"
            "不能声称课程基线已经发布。"
        )
        metadata = self.runtime_dir / "course-worktrees" / f"{stamp}.json"
        metadata.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return evidence

    def _revision(self, cwd: Path, revision: str) -> str | None:
        completed = self.process_runner(
            ["git", "rev-parse", "--verify", revision], cwd=cwd,
            text=True, capture_output=True, check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None


class LessonSubprocessEvalRunner:
    """Run lesson Eval against the isolated worktree, never the controller checkout."""

    def __init__(self, workspace_root: str | Path, runtime_dir: str | Path, task_id: str,
                 case_names: tuple[str, ...], label: str, *,
                 process_runner: ProcessRunner | None = None) -> None:
        if not case_names:
            raise ValueError("课程差分评测至少需要一个 Eval")
        self.workspace_root = Path(workspace_root).resolve()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.task_id = task_id
        self.case_names = tuple(dict.fromkeys(case_names))
        self.label = label
        self.process_runner = process_runner or subprocess.run

    def __call__(self, suite: str = "blocking", write_report: bool = True) -> dict:
        attempt_id = uuid.uuid4().hex
        # Unique directories retain earlier successes AND failures, including a
        # previous attempt that did not produce a report at all.
        attempt_dir = self.runtime_dir / "reports" / f"eval-{attempt_id}"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        report_path = attempt_dir / "report.json"
        receipt_path = attempt_dir / "process.json"
        command = [
            sys.executable, "-X", "utf8", "-m", "eval.harness", "--suite", suite,
            "--report-path", str(report_path),
        ]
        for name in self.case_names:
            command.extend(["--case", name])
        started_at = datetime.now(timezone.utc).isoformat()
        def receipt(returncode, stdout, stderr):
            def as_text(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            receipt_path.write_text(json.dumps({
                "attempt_id": attempt_id, "task_id": self.task_id, "label": self.label,
                "workspace": str(self.workspace_root), "command": command,
                "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
                "returncode": returncode, "stdout": as_text(stdout), "stderr": as_text(stderr),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            completed = self.process_runner(
                command, cwd=self.workspace_root, text=True, encoding="utf-8", capture_output=True,
                check=False, timeout=1800,
            )
        except subprocess.TimeoutExpired as exc:
            receipt(124, exc.stdout, exc.stderr)
            raise RuntimeError(f"隔离 Eval 超时，原始过程保留于 {receipt_path}") from exc
        except OSError as exc:
            receipt(127, "", str(exc))
            raise RuntimeError(f"隔离 Eval 无法启动，过程保留于 {receipt_path}") from exc
        receipt(completed.returncode, completed.stdout, completed.stderr)
        if not report_path.is_file():
            raise RuntimeError(
                f"隔离 Eval 未生成报告（exit={completed.returncode}）：{(completed.stderr or '')[-2000:]}"
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise RuntimeError(f"隔离 Eval 报告不可读：{report_path}") from exc
        validate_report(report, self.case_names, completed.returncode, suite)
        report["runner"] = {
            "workspace": str(self.workspace_root),
            "process_returncode": completed.returncode,
            "label": self.label,
            "attempt_id": attempt_id,
            "report_path": str(report_path),
            "receipt_path": str(receipt_path),
            "validated": True,
        }
        return report


def dynamic_start_evidence(report: dict, static_cases: tuple[str, ...], dynamic_cases: tuple[str, ...]) -> dict:
    """A new requirement must fail its own checks while the existing contract passes."""
    error = None
    runner = report.get('runner') or {}
    try:
        validate_report(report, static_cases + dynamic_cases, runner.get('process_returncode'))
        if runner.get('validated') is not True:
            raise RuntimeError('新增需求的起始报告未经独立执行器验证')
    except (RuntimeError, TypeError, ValueError) as exc:
        error = str(exc)
    failures = {item.get('name') for item in report.get('results', [])
                if isinstance(item, dict) and item.get('passed') is False}
    static_failed = sorted(failures & set(static_cases))
    dynamic_failed = sorted(failures & set(dynamic_cases))
    return {'accepted': error is None and bool(dynamic_failed) and not static_failed,
            'static_failed': static_failed, 'dynamic_failed': dynamic_failed,
            'validation_error': error,
            'requirement': '既有合同通过，至少一个本次新增用例真实失败'}


def differential_evidence(pre_report: dict, post_report: dict, task: dict) -> dict:
    execution = next(
        (event.get("evidence") or {} for event in reversed(task.get("events", []))
         if event.get("detail") == "受控执行阶段完成"),
        {},
    )
    pre_summary = pre_report.get("summary", {})
    post_summary = post_report.get("summary", {})
    changed_files = execution.get("changed_files", [])
    pre_runner, post_runner = pre_report.get("runner", {}), post_report.get("runner", {})
    same_cases = bool(pre_report.get("requested_cases")) and pre_report.get("requested_cases") == post_report.get("requested_cases")
    reports_valid = False
    try:
        validate_report(pre_report, tuple(pre_report.get("requested_cases", ())), pre_runner.get("process_returncode"))
        validate_report(post_report, tuple(pre_report.get("requested_cases", ())), post_runner.get("process_returncode"))
        reports_valid = pre_runner.get("validated") is True and post_runner.get("validated") is True
    except (RuntimeError, TypeError, ValueError):
        pass
    checks = {
        "baseline_was_red": reports_valid and pre_summary.get("decision") == "block" and pre_summary.get("blocking_failed", 0) > 0,
        "candidate_is_green": reports_valid and post_summary.get("decision") == "pass" and post_summary.get("blocking_failed") == 0,
        "candidate_changed": bool(changed_files),
        "no_out_of_scope_writes": not execution.get("out_of_scope_files"),
        "real_code_execution": execution.get("mode") == "codex_exec",
        "execution_succeeded": execution.get("success") is True,
        "reports_are_valid": reports_valid,
        "same_eval_cases": same_cases,
        "same_isolated_workspace": bool(pre_runner.get("workspace")) and pre_runner.get("workspace") == post_runner.get("workspace"),
        "distinct_eval_attempts": bool(pre_runner.get("attempt_id")) and bool(post_runner.get("attempt_id"))
            and pre_runner["attempt_id"] != post_runner["attempt_id"],
    }
    return {
        "accepted": all(checks.values()),
        "checks": checks,
        "changed_files": changed_files,
        "pre_summary": pre_summary,
        "post_summary": post_summary,
    }
