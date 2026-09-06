"""Explicit, local-only teaching snapshots of the current source tree.

Snapshots are not published course tags or proof of learning. They solve the
development case where reviewed classroom source has not reached Git HEAD yet.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .lesson_constructibility import apply_student_start


SOURCE_DIRS = {".codex", ".github", "agent", "deploy", "docs", "eval", "flowerp", "harness_web",
               "scripts", "tests", "web", "workbench", "workbench_web"}
ROOT_FILES = {"AGENTS.md", "FDE_SPEC.md", "CI_GATE_SPEC.md", "README.md", "pyproject.toml", "main.py", ".gitignore", "首次使用.cmd", "打开工作台.cmd"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".yaml", ".yml", ".js", ".mjs", ".html", ".css",
                 ".txt", ".sh", ".ps1", ".code-workspace", ".svg", ".drawio"}
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".runtime", ".harness-runtime", ".course"}


def _git(target: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=target, text=True, encoding="utf-8", capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"教学快照 Git 操作失败：{result.stderr.strip()}")
    return result.stdout.strip()


def source_paths(source: Path, runtime: Path) -> list[Path]:
    # Enumerate only course source, before creating the destination. No environment,
    # runtime database, credentials, binary slide output or private build directory.
    paths = [source / name for name in ROOT_FILES if (source / name).is_file()]
    for directory in sorted(SOURCE_DIRS):
        base = source / directory
        if not base.is_dir():
            continue
        for current, folders, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            folders[:] = [name for name in folders if name not in EXCLUDED_DIRS
                          and not (current_path / name).is_symlink()
                          and not (getattr((current_path / name).stat(), "st_file_attributes", 0) & 0x400)
                          and not (current_path / name).resolve().is_relative_to(runtime)]
            for name in files:
                path = current_path / name
                if path.suffix.lower() not in TEXT_SUFFIXES or name.lower().startswith((".env", "auth.", "credentials.", "secrets.")):
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(source):
                    raise ValueError(f"课程源文件不可链接到其他位置：{path.relative_to(source)}")
                paths.append(path)
    required = {"workbench/__init__.py", "workbench/cli.py", "eval/__init__.py", "eval/harness.py"}
    present = {path.relative_to(source).as_posix() for path in paths}
    if missing := required - present:
        raise ValueError("课程源文件不完整：" + ", ".join(sorted(missing)))
    return sorted(paths)


def prepare_source_snapshot(repository_root: str | Path, runtime_dir: str | Path, lesson_number: int,
                            task_id: str | None = None) -> dict:
    if not 1 <= lesson_number <= 16:
        raise ValueError("课次必须在 1 到 16 之间")
    stamp = task_id or f"TASK-PREP-L{lesson_number:02d}"
    if not re.fullmatch(r"TASK-[A-Za-z0-9][A-Za-z0-9_-]{0,100}", stamp):
        raise ValueError("任务编号不能用于隔离工作区路径")
    source, runtime = Path(repository_root).resolve(), Path(runtime_dir).resolve()
    parent = runtime / "course-worktrees"
    target = parent / stamp
    if target.exists():
        raise FileExistsError(f"课程隔离工作区已存在：{target}")
    if target.resolve().parent != parent.resolve():
        raise ValueError("教学快照路径越界")
    paths = source_paths(source, runtime)
    target.mkdir(parents=True)
    manifest = {}
    for path in sorted(paths):
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("源文件路径越界")
        relative = path.relative_to(source)
        content = path.read_bytes()
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        manifest[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    student_start = apply_student_start(target, lesson_number)
    _git(target, "init", "--quiet")
    # Runtime evidence remains outside the baseline commit.
    (target / ".git/info/exclude").write_text(".course/\n.runtime/\n", encoding="utf-8")
    _git(target, "add", "--all")
    _git(target, "-c", "user.name=Course source snapshot", "-c", "user.email=course-snapshot@localhost",
         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Local lesson start snapshot; not a published course baseline")
    commit = _git(target, "rev-parse", "HEAD")
    payload = {
        "mode": "local_source_snapshot", "path": str(target), "source_root": str(source),
        "source_manifest": manifest,
        "source_tree_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        "snapshot_commit": commit, "baseline_commit": commit, "baseline_ref": None,
        "baseline_semantics": "working_tree_snapshot", "detached": False,
        "created_at": datetime.now(timezone.utc).isoformat(), "student_start": student_start,
        "warning": "本地源代码快照，含未提交修改。不是已发布课程标签，不包含二进制课件；须先运行本讲前置验收。",
    }
    (parent / f"{stamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
