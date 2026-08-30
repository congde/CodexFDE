from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable


ProcessRunner = Callable[..., subprocess.CompletedProcess]


class CourseWorktreeManager:
    """Prepare an isolated, detached workspace from a reviewed lesson baseline."""

    def __init__(self, repository_root: str | Path, runtime_dir: str | Path, *,
                 process_runner: ProcessRunner | None = None) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.process_runner = process_runner or subprocess.run

    def prepare(self, task_id: str, baseline_ref: str, *, qualified_ref: bool = False) -> dict:
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
        metadata = self.runtime_dir / "course-worktrees" / f"{task_id}.json"
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
        self.case_names = case_names
        self.label = label
        self.process_runner = process_runner or subprocess.run

    def __call__(self, suite: str = "blocking", write_report: bool = True) -> dict:
        report_path = self.runtime_dir / "reports" / f"{self.task_id}-{self.label}.json"
        command = [
            sys.executable, "-X", "utf8", "-m", "eval.harness", "--suite", suite,
            "--report-path", str(report_path),
        ]
        for name in self.case_names:
            command.extend(["--case", name])
        completed = self.process_runner(
            command, cwd=self.workspace_root, text=True, capture_output=True,
            check=False, timeout=1800,
        )
        if not report_path.is_file():
            raise RuntimeError(
                f"隔离 Eval 未生成报告（exit={completed.returncode}）：{completed.stderr[-2000:]}"
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["runner"] = {
            "workspace": str(self.workspace_root),
            "process_returncode": completed.returncode,
            "label": self.label,
        }
        return report


def differential_evidence(pre_report: dict, post_report: dict, task: dict) -> dict:
    execution = next(
        (event.get("evidence") or {} for event in task.get("events", [])
         if event.get("detail") == "受控执行阶段完成"),
        {},
    )
    pre_summary = pre_report.get("summary", {})
    post_summary = post_report.get("summary", {})
    changed_files = execution.get("changed_files", [])
    checks = {
        "baseline_was_red": pre_summary.get("decision") == "block" and int(pre_summary.get("blocking_failed", 0)) > 0,
        "candidate_is_green": post_summary.get("decision") == "pass" and int(post_summary.get("blocking_failed", 0)) == 0,
        "candidate_changed": bool(changed_files),
        "no_out_of_scope_writes": not execution.get("out_of_scope_files"),
        "real_code_execution": execution.get("mode") == "codex_exec",
    }
    return {
        "accepted": all(checks.values()),
        "checks": checks,
        "changed_files": changed_files,
        "pre_summary": pre_summary,
        "post_summary": post_summary,
    }
