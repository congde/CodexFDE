from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .execution import CodexExecutionRunner, CodexLineCallback


def task_project_id(task: dict) -> str:
    reference = next(
        (item for item in task.get("business_refs", []) if str(item).startswith("PROJECT:")), "",
    )
    project_id = reference.partition(":")[2]
    if not project_id:
        raise ValueError("Harness 任务缺少 PROJECT 项目引用")
    return project_id


class ProjectExecutionRunner:
    def __init__(self, projects, runtime_dir: str | Path) -> None:
        self.projects = projects
        self.runtime_dir = Path(runtime_dir).resolve()

    def __call__(self, task: dict, *, on_codex_line: CodexLineCallback | None = None) -> dict:
        project = self.projects.get(task_project_id(task))
        return CodexExecutionRunner(project["root_path"], self.runtime_dir)(task, on_codex_line=on_codex_line)

    def capabilities(self) -> dict:
        command = os.getenv("FLOWERP_CODEX_COMMAND", "codex")
        resolved = shutil.which(command)
        return {
            "codex_available": bool(resolved),
            "codex_command": resolved or command,
            "sandbox": "project-write-scope",
            "reason": "ready" if resolved else "找不到 Codex CLI",
        }


class ProjectEvalRunner:
    """Resolve and run the registered project's JSON-producing Eval command."""

    def __init__(self, projects, runtime_dir: str | Path) -> None:
        self.projects = projects
        self.runtime_dir = Path(runtime_dir).resolve()

    def for_task(self, task: dict):
        project = self.projects.get(task_project_id(task))

        def run(_suite: str = "blocking", write_report: bool = True) -> dict:
            report_path = self.runtime_dir / "project-reports" / f"{task['id']}.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            command = [part.replace("{report_path}", str(report_path)) for part in project["eval_command"]]
            completed = subprocess.run(
                command, cwd=project["root_path"], text=True,
                capture_output=True, check=False, timeout=1800,
            )
            if report_path.is_file():
                report = json.loads(report_path.read_text(encoding="utf-8"))
            else:
                output = (completed.stdout or "").strip()
                try:
                    report = json.loads(output)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("项目 Eval 命令必须写入 {report_path} 或在 stdout 输出完整 JSON") from exc
            summary = report.get("summary", {})
            if summary.get("decision") not in {"pass", "block"}:
                raise RuntimeError("项目 Eval 报告缺少 pass/block 决策")
            report["project_runner"] = {
                "project_id": project["id"], "root_path": project["root_path"],
                "returncode": completed.returncode,
            }
            return report

        return run
