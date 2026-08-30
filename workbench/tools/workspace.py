from __future__ import annotations

from pathlib import Path

from ..fs_provider import LocalFsProvider, project_root_for_task
from ..project_store import ProjectStore
from ..shell_provider import LocalShellProvider


def _fs(context: dict):
    return context.get("fs") or LocalFsProvider()


def _shell(context: dict):
    return context.get("shell") or LocalShellProvider()


def _root(context: dict) -> Path:
    projects: ProjectStore = context["projects"]
    task = context["task"]
    fallback = context.get("repository_root")
    return project_root_for_task(projects, task, Path(fallback) if fallback else None)


def read_workspace_file(context: dict, args: dict) -> dict:
    return _fs(context).read_text(_root(context), str(args.get("path") or ""))


def list_workspace(context: dict, args: dict) -> dict:
    return _fs(context).list_dir(_root(context), str(args.get("path") or "."))


def grep_workspace(context: dict, args: dict) -> dict:
    return _fs(context).grep(
        _root(context),
        str(args.get("path") or "."),
        str(args.get("pattern") or ""),
        limit=int(args.get("limit", 50)),
    )


def exec_workspace_shell(context: dict, args: dict) -> dict:
    return _shell(context).exec(
        _root(context),
        list(args.get("command") or []),
        timeout=int(args.get("timeout", 120)),
    )
