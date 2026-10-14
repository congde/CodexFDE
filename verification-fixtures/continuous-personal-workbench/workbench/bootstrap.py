"""Persistent L01 engineering evidence; completeness always awaits human review."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4


class LedgerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def failure_result(code: str, message: str) -> dict:
    return {
        "ok": False, "owner": None, "workbench_id": None,
        "projects": [], "tasks": [], "errors": [code], "message": message,
        "evidence_complete": False, "flowerp_connected": False,
        "acceptance": "pending_human_review",
    }


def _nonempty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError("invalid_argument", f"{field} 不能为空")
    return value


def _observed_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing time zone")
    except (TypeError, ValueError) as error:
        raise LedgerError("invalid_observed_at", "observed-at 必须是带有效时区的 ISO 8601 时间") from error
    return parsed


def _snapshot(filename) -> tuple[str, str, str]:
    path = Path(filename).resolve()
    # Reading bytes preserves CRLF, BOM and the exact hash of the source file.
    raw = path.read_bytes()
    return str(path), raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS workbench (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        owner TEXT NOT NULL, name TEXT NOT NULL, workbench_id TEXT NOT NULL UNIQUE
    )""",
    """CREATE TABLE IF NOT EXISTS projects (
        project_id TEXT PRIMARY KEY, name TEXT NOT NULL,
        path TEXT NOT NULL, purpose TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id),
        title TEXT NOT NULL, request TEXT NOT NULL, actor TEXT NOT NULL,
        requirement_id TEXT NOT NULL, spec_file TEXT NOT NULL, spec TEXT NOT NULL,
        spec_sha256 TEXT NOT NULL, problem_file TEXT NOT NULL, problem TEXT NOT NULL,
        problem_sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS evidence (
        evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        phase TEXT NOT NULL CHECK (phase IN ('red', 'diff', 'green', 'observation')),
        command_text TEXT NOT NULL, output_file TEXT NOT NULL, output TEXT NOT NULL,
        output_sha256 TEXT NOT NULL, returncode INTEGER NOT NULL, observed_at TEXT NOT NULL
    )""",
)


def _has_chain(evidence: list[dict]) -> bool:
    """Require append order AND strict observation times, comparing time zones.

    Diff is a separate command. Red and green must match exactly. Any subsequent
    failure restarts this task's chain, including failed green/observation records.
    """
    red = None
    diff = None
    complete = False
    for record in evidence:
        observed = _observed_time(record["observed_at"])
        phase = record["phase"]
        command = record["command_text"]
        if record["returncode"] != 0:
            red = (command, observed) if phase == "red" else None
            diff = None
            complete = False
        elif phase == "diff":
            diff = observed if red and red[1] < observed else None
        elif phase == "green":
            complete = bool(red and diff and command == red[0] and red[1] < diff < observed)
    return complete


class BootstrapLedger:
    def __init__(self, runtime_dir):
        self.runtime = Path(runtime_dir).resolve()
        self.database = self.runtime / "workbench.db"

    @contextmanager
    def _connection(self, *, readonly: bool = False):
        if not self.database.is_file():
            raise LedgerError("workbench_not_initialized", "工作台尚未初始化，请先运行 workbench-init")
        # mode=ro/rw cannot create a missing database, even if removed after is_file.
        connection = sqlite3.connect(
            self.database.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                connection.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
                if connection.execute("SELECT 1 FROM workbench WHERE singleton = 1").fetchone() is None:
                    raise LedgerError("workbench_not_initialized", "工作台尚未初始化，请先运行 workbench-init")
                yield connection
        finally:
            connection.close()

    def init(self, *, owner: str, name: str = "个人 AI 研发工作台") -> dict:
        _nonempty(owner, "owner")
        _nonempty(name, "name")
        self.runtime.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA:
                    connection.execute(statement)
                row = connection.execute("SELECT * FROM workbench WHERE singleton = 1").fetchone()
                if row is not None:
                    if row["owner"] != owner:
                        raise LedgerError("owner_conflict", "工作台已初始化，不能覆盖原 owner")
                else:
                    connection.execute(
                        "INSERT INTO workbench VALUES (1, ?, ?, ?)", (owner, name, str(uuid4()))
                    )
                    row = connection.execute("SELECT * FROM workbench WHERE singleton = 1").fetchone()
                result = dict(row)
                result.pop("singleton")
                return {"ok": True, **result}
        finally:
            connection.close()

    def project_add(self, *, project_id: str, name: str, path=".", purpose: str = "") -> dict:
        _nonempty(project_id, "project-id")
        _nonempty(name, "name")
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone():
                raise LedgerError("project_exists", "项目编号已存在，不能覆盖")
            result = {"project_id": project_id, "name": name,
                      "path": str(Path(path).resolve()), "purpose": purpose}
            connection.execute("INSERT INTO projects VALUES (?, ?, ?, ?)", tuple(result.values()))
            return {"ok": True, **result}

    def task_create(self, *, project_id: str, task_id: str, title: str, spec_file,
                    problem_file=None, actor: str = "", requirement_id: str = "") -> dict:
        _nonempty(task_id, "task-id")
        _nonempty(title, "title/request")
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone() is None:
                raise LedgerError("project_missing", "项目尚未登记")
            if connection.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone():
                raise LedgerError("task_exists", "任务编号已存在，不能覆盖")
            spec_path, spec, spec_hash = _snapshot(spec_file)
            if problem_file is None:
                raise LedgerError("problem_file_missing", "创建任务需要 problem-file 问题快照")
            problem_path, problem, problem_hash = _snapshot(problem_file)
            result = {
                "task_id": task_id, "project_id": project_id, "title": title, "request": title,
                "actor": actor, "requirement_id": requirement_id,
                "spec_file": spec_path, "spec": spec, "spec_sha256": spec_hash,
                "problem_file": problem_path, "problem": problem, "problem_sha256": problem_hash,
            }
            connection.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               tuple(result.values()))
            return {"ok": True, **result, "evidence": []}

    def evidence_add(self, *, task_id: str, phase: str, command_text: str, output_file,
                     returncode: int, observed_at: str) -> dict:
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone() is None:
                raise LedgerError("task_missing", "任务尚未创建")
            if phase not in ("red", "diff", "green", "observation"):
                raise LedgerError("invalid_phase", "phase 必须是 red/diff/green/observation")
            _nonempty(command_text, "command-text")
            if type(returncode) is not int or not -(2 ** 63) <= returncode < 2 ** 63:
                raise LedgerError("invalid_returncode", "returncode 必须是 SQLite 可保存的有符号 64 位整数")
            _observed_time(observed_at)
            output_path, output, output_hash = _snapshot(output_file)
            if not output.strip():
                raise LedgerError("empty_output", "证据输出不能为空")
            result = {
                "task_id": task_id, "phase": phase, "command_text": command_text,
                "output_file": output_path, "output": output, "output_sha256": output_hash,
                "returncode": returncode, "observed_at": observed_at,
            }
            cursor = connection.execute(
                """INSERT INTO evidence
                   (task_id, phase, command_text, output_file, output, output_sha256, returncode, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", tuple(result.values())
            )
            return {"ok": True, "evidence_id": cursor.lastrowid, **result}

    def status(self, *, require_project=(), require_task=(), require_red_green_evidence=False) -> dict:
        required_projects = [require_project] if isinstance(require_project, str) else list(require_project)
        required_tasks = [require_task] if isinstance(require_task, str) else list(require_task)
        with self._connection(readonly=True) as connection:
            metadata = dict(connection.execute("SELECT * FROM workbench WHERE singleton = 1").fetchone())
            projects = [dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY rowid")]
            tasks = [dict(row) for row in connection.execute("SELECT * FROM tasks ORDER BY rowid")]
            task_map = {task["task_id"]: task for task in tasks}
            for task in tasks:
                task["evidence"] = []
            for row in connection.execute("SELECT * FROM evidence ORDER BY evidence_id"):
                record = dict(row)
                task_map[record["task_id"]]["evidence"].append(record)
        errors = []
        project_ids = {project["project_id"] for project in projects}
        if any(project_id not in project_ids for project_id in required_projects):
            errors.append("required_project_missing")
        if any(task_id not in task_map for task_id in required_tasks):
            errors.append("required_task_missing")
        checked_tasks = [task_map[task_id] for task_id in required_tasks if task_id in task_map] if required_tasks else tasks
        complete = bool(checked_tasks) and not errors and all(_has_chain(task["evidence"]) for task in checked_tasks)
        if require_red_green_evidence and not complete:
            errors.append("same_command_red_diff_green_missing")
        return {
            "ok": not errors, "owner": metadata["owner"], "workbench_id": metadata["workbench_id"],
            "projects": projects, "tasks": tasks, "errors": errors,
            "evidence_complete": complete, "flowerp_connected": False,
            "acceptance": "pending_human_review",
        }
