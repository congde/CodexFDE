from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from .project_store import ProjectStore


class FsProvider(Protocol):
    def read_text(self, root: Path, relative: str) -> dict: ...
    def list_dir(self, root: Path, relative: str) -> dict: ...
    def grep(self, root: Path, relative: str, pattern: str, *, limit: int = 50) -> dict: ...


def safe_relative_path(relative: str) -> str:
    value = str(relative or ".").replace("\\", "/").strip().lstrip("/")
    if ".." in value.split("/"):
        raise ValueError("path 必须是工作区相对安全路径")
    return value or "."


def resolve_under_root(root: Path, relative: str) -> Path:
    rel = safe_relative_path(relative)
    target = (root / rel).resolve() if rel != "." else root.resolve()
    root = root.resolve()
    if root not in target.parents and target != root:
        raise ValueError("path 越界")
    return target


class LocalFsProvider:
    """Local filesystem provider for the fs capability seam."""

    def read_text(self, root: Path, relative: str) -> dict:
        rel = safe_relative_path(relative)
        if not rel or rel == ".":
            raise ValueError("path 不能为空")
        target = resolve_under_root(root, rel)
        if not target.is_file():
            raise FileNotFoundError(rel)
        if target.stat().st_size > 1_000_000:
            raise ValueError("文件过大，拒绝读取")
        return {"path": rel, "content": target.read_text(encoding="utf-8"), "provider": "local"}

    def list_dir(self, root: Path, relative: str) -> dict:
        rel = safe_relative_path(relative or ".")
        target = resolve_under_root(root, rel)
        if not target.is_dir():
            raise NotADirectoryError(rel or ".")
        entries = []
        for child in sorted(target.iterdir(), key=lambda item: item.name.lower())[:200]:
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "path": child.relative_to(root.resolve()).as_posix(),
            })
        return {"path": rel, "entries": entries, "provider": "local"}

    def grep(self, root: Path, relative: str, pattern: str, *, limit: int = 50) -> dict:
        if not pattern.strip():
            raise ValueError("pattern 不能为空")
        if len(pattern) > 200:
            raise ValueError("pattern 过长")
        rel = safe_relative_path(relative or ".")
        target = resolve_under_root(root, rel)
        limit = min(int(limit), 200)
        regex = re.compile(pattern)
        matches: list[dict] = []
        root = root.resolve()
        files = [target] if target.is_file() else sorted(target.rglob("*"))
        for path in files:
            if not path.is_file() or path.is_symlink():
                continue
            if path.stat().st_size > 500_000:
                continue
            try:
                file_rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append({"path": file_rel, "line": line_no, "text": line[:240]})
                    if len(matches) >= limit:
                        return {
                            "pattern": pattern,
                            "path": rel,
                            "matches": matches,
                            "truncated": True,
                            "provider": "local",
                        }
        return {
            "pattern": pattern,
            "path": rel,
            "matches": matches,
            "truncated": False,
            "provider": "local",
        }


def project_root_for_task(projects: ProjectStore, task: dict, fallback: Path | None = None) -> Path:
    reference = next((item for item in task.get("business_refs", []) if str(item).startswith("PROJECT:")), "")
    project_id = reference.partition(":")[2]
    if project_id:
        return Path(projects.get(project_id)["root_path"]).resolve()
    if fallback is not None:
        return Path(fallback).resolve()
    raise ValueError("任务缺少 PROJECT 引用，无法定位工作区")
