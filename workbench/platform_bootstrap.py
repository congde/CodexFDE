from __future__ import annotations

import sys
from pathlib import Path

from .platform_api import HarnessPlatformAPI


def default_eval_command() -> list[str]:
    return [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "eval.harness",
        "--suite",
        "blocking",
        "--report-path",
        "{report_path}",
    ]


def bootstrap_default_project(
    api: HarnessPlatformAPI,
    *,
    project_id: str = "PROJECT-FLOWERP",
    name: str = "CodexFDE 工作台",
) -> dict:
    """Register the repository root as the default target project if missing."""
    root = api.repository_root
    try:
        existing = api.projects.get(project_id)
        return {"action": "exists", "project": existing}
    except KeyError:
        pass
    for item in api.projects.list():
        if Path(item["root_path"]).resolve() == root:
            return {"action": "exists", "project": item}
    project = api.projects.create(name, root, default_eval_command(), project_id)
    return {"action": "created", "project": project}


def bootstrap_platform(
    runtime_dir: str | Path = ".harness-runtime",
    repository_root: str | Path | None = None,
) -> dict:
    api = HarnessPlatformAPI(runtime_dir, repository_root)
    result = bootstrap_default_project(api)
    return {
        "runtime_dir": str(api.runtime_dir),
        "repository_root": str(api.repository_root),
        "bootstrap": result,
        "interface": "terminal",
    }
