from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from .course_mainline import lesson_contract
from .course_workspace import LessonSubprocessEvalRunner


ProcessRunner = Callable[..., subprocess.CompletedProcess]
EvalFactory = Callable[[Path, tuple[str, ...], str], dict]


def _safe_revision(value: str) -> str:
    candidate = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", candidate):
        raise ValueError("Git revision 只能包含字母、数字、点、下划线、斜杠和连字符")
    if ".." in candidate or "@{" in candidate or candidate.endswith("/"):
        raise ValueError("Git revision 包含不安全或歧义语法")
    return candidate


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    boundary = root.resolve()
    return resolved == boundary or boundary in resolved.parents


class CourseBaselinePublisher:
    """Audit candidate commits before creating immutable lesson start tags."""

    def __init__(self, repository_root: str | Path, runtime_dir: str | Path, *,
                 process_runner: ProcessRunner | None = None,
                 eval_factory: EvalFactory | None = None) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.process_runner = process_runner or subprocess.run
        self.eval_factory = eval_factory or self._run_eval

    def audit(self, lesson_number: int, candidate_ref: str,
              manual_evidence: str | Path | None = None) -> dict:
        lesson = lesson_contract(lesson_number)
        revision = _safe_revision(candidate_ref)
        if self._revision(f"refs/tags/{lesson.baseline_ref}"):
            raise ValueError(f"课程标签已存在且不可重写：{lesson.baseline_ref}")
        candidate_commit = self._revision(revision)
        if not candidate_commit:
            raise ValueError(f"候选 revision 不存在：{revision}")

        checks: dict[str, bool] = {"candidate_exists": True, "tag_absent": True}
        previous_commit = None
        if lesson_number > 1:
            previous_ref = lesson_contract(lesson_number - 1).baseline_ref
            previous_commit = self._revision(f"refs/tags/{previous_ref}")
            checks["previous_tag_exists"] = bool(previous_commit)
            checks["candidate_differs_from_previous"] = bool(previous_commit and previous_commit != candidate_commit)
            checks["linear_history"] = bool(
                previous_commit and self._is_ancestor(previous_commit, candidate_commit)
            )

        evidence_path = Path(manual_evidence).resolve() if manual_evidence else None
        evidence_hash = None
        if evidence_path:
            if not evidence_path.is_file():
                raise ValueError(f"人工基线证据不存在：{evidence_path}")
            evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        checks["manual_evidence_present"] = bool(evidence_hash)

        reports: dict[str, dict] = {}
        if lesson_number >= 4:
            target = self.runtime_dir / "baseline-audits" / f"L{lesson_number:02d}"
            if target.exists():
                raise FileExistsError(f"基线审计 Worktree 已存在：{target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            self._git(["worktree", "add", "--detach", str(target), candidate_commit])
            try:
                current = self.eval_factory(target, lesson.eval_cases, f"l{lesson_number:02d}-red")
                reports["current_lesson"] = current
                summary = current.get("summary", {})
                if lesson.dynamic_eval_required:
                    checks["static_contract_is_green"] = (
                        summary.get("decision") == "pass"
                        and int(summary.get("blocking_failed", 0)) == 0
                    )
                    checks["dynamic_red_deferred_to_session"] = True
                else:
                    checks["current_lesson_is_red"] = (
                        summary.get("decision") == "block" and int(summary.get("blocking_failed", 0)) > 0
                    )
                if lesson_number > 4:
                    previous = lesson_contract(lesson_number - 1)
                    previous_report = self.eval_factory(
                        target, previous.eval_cases, f"l{lesson_number - 1:02d}-green",
                    )
                    reports["previous_lesson"] = previous_report
                    previous_summary = previous_report.get("summary", {})
                    checks["previous_lesson_is_green"] = (
                        previous_summary.get("decision") == "pass"
                        and int(previous_summary.get("blocking_failed", 0)) == 0
                    )
            finally:
                self._remove_audit_worktree(target)

        accepted = all(checks.values())
        result = {
            "schema_version": "1.0",
            "lesson": lesson_number,
            "baseline_ref": lesson.baseline_ref,
            "candidate_ref": revision,
            "candidate_commit": candidate_commit,
            "previous_commit": previous_commit,
            "accepted": accepted,
            "checks": checks,
            "manual_evidence": str(evidence_path) if evidence_path else None,
            "manual_evidence_sha256": evidence_hash,
            "reports": reports,
        }
        output = self.runtime_dir / "baseline-audits" / f"L{lesson_number:02d}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["report_path"] = str(output)
        return result

    def publish(self, lesson_number: int, candidate_ref: str, manual_evidence: str | Path) -> dict:
        result = self.audit(lesson_number, candidate_ref, manual_evidence)
        if not result["accepted"]:
            raise ValueError("候选基线未通过全部门禁，不得创建课程标签")
        message = (
            f"FlowERP course L{lesson_number:02d} start baseline\n\n"
            f"audit: {result['report_path']}\n"
            f"evidence-sha256: {result['manual_evidence_sha256']}"
        )
        self._git([
            "tag", "-a", result["baseline_ref"], result["candidate_commit"], "-m", message,
        ])
        result["published"] = True
        return result

    def _run_eval(self, workspace: Path, cases: tuple[str, ...], label: str) -> dict:
        return LessonSubprocessEvalRunner(
            workspace, self.runtime_dir, f"BASELINE-{label.upper()}", cases, label,
            process_runner=self.process_runner,
        )()

    def _revision(self, revision: str) -> str | None:
        completed = self.process_runner(
            ["git", "rev-parse", "--verify", revision], cwd=self.repository_root,
            text=True, capture_output=True, check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    def _is_ancestor(self, older: str, newer: str) -> bool:
        completed = self.process_runner(
            ["git", "merge-base", "--is-ancestor", older, newer], cwd=self.repository_root,
            text=True, capture_output=True, check=False,
        )
        return completed.returncode == 0

    def _git(self, arguments: list[str]) -> subprocess.CompletedProcess:
        completed = self.process_runner(
            ["git", *arguments], cwd=self.repository_root,
            text=True, capture_output=True, check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout).strip())
        return completed

    def _remove_audit_worktree(self, target: Path) -> None:
        audit_root = self.runtime_dir / "baseline-audits"
        if not _inside(target, audit_root):
            raise RuntimeError("拒绝清理课程运行目录之外的 Worktree")
        self._git(["worktree", "remove", "--force", str(target)])


class CourseCandidateArtifacts:
    """Export an approved isolated candidate, promote it deliberately, then clean it safely."""

    def __init__(self, repository_root: str | Path, runtime_dir: str | Path, *,
                 process_runner: ProcessRunner | None = None) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.process_runner = process_runner or subprocess.run

    def export(self, task_store, task_id: str) -> dict:
        task = task_store.get(task_id)
        if task.get("status") != "completed" or task.get("review_decision") != "approve":
            raise ValueError("只有具名审核通过并 completed 的课程任务可以导出候选")
        differential = next(
            (event.get("evidence") for event in reversed(task.get("events", []))
             if event.get("detail") == "课程红绿差分判定已完成"), None,
        )
        if not isinstance(differential, dict) or not differential.get("accepted"):
            raise ValueError("任务缺少通过的课程红绿差分证据")
        workspace = self._workspace(task_id)
        baseline = self._metadata(task_id)["baseline_commit"]
        completed = self._run(
            ["git", "-C", str(workspace), "diff", "--binary", "--full-index", baseline, "--", "."],
            cwd=self.repository_root,
        )
        patch = completed.stdout.encode("utf-8")
        if not patch.strip():
            raise ValueError("候选没有可导出的 Git Diff")
        output = self.runtime_dir / "candidates" / f"{task_id}.patch"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(patch)
        manifest = {
            "schema_version": "1.0",
            "task_id": task_id,
            "requirement_id": task.get("requirement_id"),
            "baseline_commit": baseline,
            "patch_path": str(output),
            "patch_sha256": hashlib.sha256(patch).hexdigest(),
            "changed_files": differential.get("changed_files", []),
            "reviewed_by": task.get("reviewed_by"),
            "review_note": task.get("review_note"),
        }
        manifest_path = output.with_suffix(".json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["manifest_path"] = str(manifest_path)
        return manifest

    def promote(self, task_id: str, target_workspace: str | Path) -> dict:
        manifest = self._candidate_manifest(task_id)
        target = Path(target_workspace).resolve()
        if not (target / ".git").exists():
            raise ValueError("提升目标必须是独立 Git 工作区")
        head = self._run(["git", "-C", str(target), "rev-parse", "HEAD"], cwd=target).stdout.strip()
        if head != manifest["baseline_commit"]:
            raise ValueError("提升目标 HEAD 与候选基线不一致")
        status = self._run(["git", "-C", str(target), "status", "--porcelain"], cwd=target).stdout.strip()
        if status:
            raise ValueError("提升目标存在未提交修改")
        patch_path = Path(manifest["patch_path"])
        digest = hashlib.sha256(patch_path.read_bytes()).hexdigest()
        if digest != manifest["patch_sha256"]:
            raise ValueError("候选 Patch 校验和不一致")
        self._run(["git", "-C", str(target), "apply", "--check", str(patch_path)], cwd=target)
        self._run(["git", "-C", str(target), "apply", str(patch_path)], cwd=target)
        return {
            "task_id": task_id, "target_workspace": str(target),
            "baseline_commit": head, "patch_sha256": digest, "applied": True,
            "committed": False,
        }

    def cleanup(self, task_store, task_id: str) -> dict:
        task = task_store.get(task_id)
        if task.get("status") == "completed":
            self._candidate_manifest(task_id)
        elif task.get("status") not in {"failed", "dead_letter"}:
            raise ValueError("只有已导出的 completed 任务或 failed/dead_letter 任务可以清理 Worktree")
        workspace = self._workspace(task_id)
        root = self.runtime_dir / "course-worktrees"
        if not _inside(workspace, root):
            raise RuntimeError("拒绝清理课程运行目录之外的 Worktree")
        self._run(
            ["git", "worktree", "remove", "--force", str(workspace)], cwd=self.repository_root,
        )
        return {"task_id": task_id, "workspace": str(workspace), "removed": True}

    def manifest(self, task_id: str) -> dict:
        return self._candidate_manifest(task_id)

    def _workspace(self, task_id: str) -> Path:
        metadata = self._metadata(task_id)
        workspace = Path(metadata["path"]).resolve()
        if not _inside(workspace, self.runtime_dir / "course-worktrees"):
            raise RuntimeError("课程 Worktree 元数据越过运行目录")
        if not workspace.exists():
            raise FileNotFoundError(workspace)
        return workspace

    def _metadata(self, task_id: str) -> dict:
        path = self.runtime_dir / "course-worktrees" / f"{task_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"课程 Worktree 元数据不存在：{path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _candidate_manifest(self, task_id: str) -> dict:
        path = self.runtime_dir / "candidates" / f"{task_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"候选尚未导出：{path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _run(self, command: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
        completed = self.process_runner(
            command, cwd=cwd, text=True, capture_output=True, check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout).strip())
        return completed
