"""L01's local evidence ledger. It records observations, never executes or approves them."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _required(value: str, label: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{label}不能为空")
    return value.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _time(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("观察时间必须包含时区")
    return stamp


class BootstrapLedger:
    def __init__(self, runtime_dir: str | Path):
        self.path = Path(runtime_dir).resolve() / "workbench.db"

    @contextmanager
    def _connect(self):
        if not self.path.is_file():
            raise ValueError("工作台尚未初始化，请先运行 workbench-init")
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self, owner: str, name: str = "个人 AI 研发工作台") -> dict:
        owner = _required(owner, "工作台所有者")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS bootstrap_workbench (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner TEXT NOT NULL,
                    created_at TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bootstrap_projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                    path TEXT NOT NULL, purpose TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bootstrap_tasks (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES bootstrap_projects(id),
                    title TEXT NOT NULL, spec TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL, requirement_id TEXT NOT NULL, actor TEXT NOT NULL,
                    problem TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bootstrap_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES bootstrap_tasks(id),
                    phase TEXT NOT NULL CHECK(phase IN ('red','diff','green','observation')),
                    command TEXT NOT NULL, output TEXT NOT NULL, output_sha256 TEXT NOT NULL,
                    returncode INTEGER NOT NULL, observed_at TEXT NOT NULL, recorded_at TEXT NOT NULL);
            """)
            previous = connection.execute("SELECT owner FROM bootstrap_workbench WHERE singleton=1").fetchone()
            if previous and previous[0] != owner:
                raise ValueError("工作台已属于另一所有者，不能覆盖")
            connection.execute("INSERT OR IGNORE INTO bootstrap_workbench VALUES (1,?,?,?,?)",
                               (owner, _now(), "WB-" + uuid.uuid4().hex, _required(name, "工作台名称")))
            identity = connection.execute("SELECT id,name FROM bootstrap_workbench WHERE singleton=1").fetchone()
        return {"initialized": True, "workbench_id": identity[0], "name": identity[1], "owner": owner,
                "workbench_version": "V0.1", "flowerp_connected": False}

    def add_project(self, project_id: str, name: str, path: str = ".", purpose: str = "组织人与 Codex 的研发协同与交付") -> dict:
        project_id, name = _required(project_id, "项目编号"), _required(name, "项目名称")
        with self._connect() as connection:
            connection.execute("INSERT INTO bootstrap_projects VALUES (?,?,?,?,?)",
                               (project_id, name, _now(), str(Path(path).resolve()), _required(purpose, "项目用途")))
        return {"project_id": project_id, "name": name}

    def create_task(self, project_id: str, task_id: str, title: str, spec_file: str,
                    actor: str = "student", requirement_id: str | None = None, problem_file: str | None = None) -> dict:
        project_id = _required(project_id, "项目编号")
        task_id, title = _required(task_id, "任务编号"), _required(title, "任务标题")
        spec = Path(spec_file).read_text(encoding="utf-8")
        _required(spec, "需求说明")
        digest = hashlib.sha256(spec.encode()).hexdigest()
        problem = Path(problem_file).read_text(encoding="utf-8") if problem_file else ""
        with self._connect() as connection:
            connection.execute("INSERT INTO bootstrap_tasks VALUES (?,?,?,?,?,?,?,?,?)",
                               (task_id, project_id, title, spec, digest, _now(), requirement_id or task_id,
                                _required(actor, "创建人"), problem))
        return {"task_id": task_id, "project_id": project_id, "spec_sha256": digest,
                "requirement_id": requirement_id or task_id, "actor": actor, "status": "recording"}

    def add_evidence(self, task_id: str, phase: str, command: str, output_file: str,
                     returncode: int, observed_at: str) -> dict:
        _time(observed_at)
        command = _required(command, "原始命令")
        output = Path(output_file).read_text(encoding="utf-8")
        _required(output, "原始输出")
        digest = hashlib.sha256(output.encode()).hexdigest()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO bootstrap_evidence (task_id,phase,command,output,output_sha256,returncode,observed_at,recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?)", (task_id, phase, command, output, digest, returncode, observed_at, _now()))
            evidence_id = cursor.lastrowid
        return {"evidence_id": evidence_id, "task_id": task_id, "output_sha256": digest,
                "provenance": "user_supplied_observation"}

    @staticmethod
    def _chain(evidence: list[dict]) -> bool:
        # Observations can be imported after implementation (first bootstrap), so
        # compare supplied observation times, not insertion IDs. Keep ALL entries.
        checks = [item for item in evidence if item["phase"] in {"red", "diff", "green"}]
        if not checks:
            return False
        latest = max(checks, key=lambda item: (_time(item["observed_at"]), item["id"]))
        if latest["phase"] != "green" or latest["returncode"] != 0:
            return False
        for red in evidence:
            if red["phase"] != "red" or red["returncode"] == 0:
                continue
            for green in [latest]:
                if green["phase"] != "green" or green["returncode"] != 0 or green["command"] != red["command"]:
                    continue
                if any(item["phase"] == "diff" and item["returncode"] == 0
                       and _time(red["observed_at"]) < _time(item["observed_at"]) < _time(green["observed_at"])
                       for item in evidence):
                    return True
        return False

    def status(self, require_project: str | None = None, require_task: str | None = None,
               require_red_green_evidence: bool = False) -> dict:
        with self._connect() as connection:
            owner = connection.execute("SELECT owner,id,name FROM bootstrap_workbench WHERE singleton=1").fetchone()
            projects = [dict(row) for row in connection.execute("SELECT * FROM bootstrap_projects ORDER BY id")]
            tasks = [dict(row) for row in connection.execute("SELECT * FROM bootstrap_tasks ORDER BY id")]
            for task in tasks:
                task["evidence"] = [dict(row) for row in connection.execute(
                    "SELECT * FROM bootstrap_evidence WHERE task_id=? ORDER BY id", (task["id"],))]
        errors = []
        if not owner:
            errors.append("workbench_owner_missing")
        if require_project and not any(item["id"] == require_project for item in projects):
            errors.append("required_project_missing")
        selected = [task for task in tasks if (not require_task or task["id"] == require_task)
                    and (not require_project or task["project_id"] == require_project)]
        if require_task and not selected:
            errors.append("required_task_missing")
        complete = bool(selected) and all(self._chain(task["evidence"]) for task in selected)
        if require_red_green_evidence and not complete:
            errors.append("same_command_red_diff_green_missing")
        if require_red_green_evidence and any(not task["problem"].strip() for task in selected):
            errors.append("original_problem_missing")
        for task in tasks:
            if hashlib.sha256(task["spec"].encode()).hexdigest() != task["spec_sha256"]:
                errors.append("spec_digest_mismatch")
            if any(hashlib.sha256(item["output"].encode()).hexdigest() != item["output_sha256"] for item in task["evidence"]):
                errors.append("output_digest_mismatch")
        return {"ok": not errors, "owner": owner[0] if owner else None,
                "workbench_id": owner[1] if owner else None, "name": owner[2] if owner else None,
                "workbench_version": "V0.1", "projects": projects, "tasks": tasks,
                "evidence_complete": complete and not errors, "flowerp_connected": False,
                "acceptance": "pending_human_review", "errors": errors,
                "limitations": ["用户导入的观察记录，未认证命令执行者和时间",
                                "有效红灯原因、Diff写集与Spec签署仍须人工核验；完整性不等于验收完成"]}


def add_bootstrap_commands(subparsers) -> None:
    for command in ("init", "project-add", "task-create", "evidence-add", "status"):
        parser = subparsers.add_parser("workbench-" + command, help="L01：最小工作台任务与证据账")
        parser.add_argument("--runtime-dir", default=".runtime/course/L01-workbench")
        if command == "init":
            parser.add_argument("--owner", required=True)
            parser.add_argument("--name", default="个人 AI 研发工作台")
        elif command == "project-add":
            parser.add_argument("--project-id", required=True)
            parser.add_argument("--name", required=True)
            parser.add_argument("--path", default=".")
            parser.add_argument("--purpose", default="组织人与 Codex 的研发协同与交付")
        elif command == "task-create":
            parser.add_argument("--project-id", required=True)
            parser.add_argument("--task-id")
            parser.add_argument("--requirement-id")
            parser.add_argument("--title", "--request", dest="title", required=True)
            parser.add_argument("--actor", default="student")
            parser.add_argument("--spec-file", default="docs/courses/L01/WORKBENCH_SPEC.md")
            parser.add_argument("--problem-file")
        elif command == "evidence-add":
            parser.add_argument("--task-id", required=True)
            parser.add_argument("--phase", choices=["red", "diff", "green", "observation"], required=True)
            parser.add_argument("--command-text", required=True)
            parser.add_argument("--output-file", required=True)
            parser.add_argument("--returncode", type=int, required=True)
            parser.add_argument("--observed-at", required=True)
        else:
            parser.add_argument("--require-project")
            parser.add_argument("--require-task")
            parser.add_argument("--require-red-green-evidence", action="store_true")


def run_bootstrap_command(args) -> int:
    ledger = BootstrapLedger(args.runtime_dir)
    try:
        if args.command == "workbench-init":
            result = ledger.initialize(args.owner, args.name)
        elif args.command == "workbench-project-add":
            result = ledger.add_project(args.project_id, args.name, args.path, args.purpose)
        elif args.command == "workbench-task-create":
            result = ledger.create_task(args.project_id, args.task_id or args.requirement_id or "TASK-" + uuid.uuid4().hex,
                                        args.title, args.spec_file, args.actor, args.requirement_id, args.problem_file)
        elif args.command == "workbench-evidence-add":
            result = ledger.add_evidence(args.task_id, args.phase, args.command_text, args.output_file,
                                         args.returncode, args.observed_at)
        else:
            result = ledger.status(args.require_project, args.require_task, args.require_red_green_evidence)
    except (ValueError, OSError, sqlite3.Error) as exc:
        result = {"ok": False, "error": str(exc), "flowerp_connected": False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1
