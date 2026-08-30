from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ProjectStore:
    """Registry of repositories managed by the standalone Harness platform."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS harness_projects(
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  root_path TEXT NOT NULL UNIQUE,
                  eval_command_json TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS harness_idempotency(
                  operation TEXT NOT NULL,
                  idempotency_key TEXT NOT NULL,
                  request_hash TEXT NOT NULL,
                  response_json TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY(operation,idempotency_key)
                );
                """
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create(self, name: str, root_path: str | Path,
               eval_command: list[str], project_id: str = "") -> dict:
        label = name.strip()
        root = Path(root_path).resolve()
        command = [str(part).strip() for part in eval_command if str(part).strip()]
        if not label:
            raise ValueError("项目名称不能为空")
        if not root.is_dir():
            raise ValueError("目标项目目录不存在")
        if not (root / ".git").exists():
            raise ValueError("目标项目必须是独立 Git 工作区")
        if not command:
            raise ValueError("项目必须声明 Eval 命令")
        identifier = project_id.strip() or f"PROJECT-{uuid.uuid4().hex[:10].upper()}"
        if not identifier.startswith("PROJECT-"):
            raise ValueError("项目编号必须以 PROJECT- 开头")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO harness_projects(id,name,root_path,eval_command_json) VALUES(?,?,?,?)",
                (identifier, label, str(root), json.dumps(command, ensure_ascii=False)),
            )
        return self.get(identifier)

    def get(self, project_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM harness_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError(project_id)
        item = dict(row)
        item["eval_command"] = json.loads(item.pop("eval_command_json"))
        return item

    def list(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM harness_projects ORDER BY created_at,id").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["eval_command"] = json.loads(item.pop("eval_command_json"))
            items.append(item)
        return items

    def idempotent(self, operation: str, key: str, payload: object, producer) -> dict:
        token = key.strip()
        if not token:
            raise ValueError("写操作必须提供 Idempotency-Key")
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT request_hash,response_json FROM harness_idempotency WHERE operation=? AND idempotency_key=?",
                (operation, token),
            ).fetchone()
        if row:
            if row["request_hash"] != digest:
                raise ValueError("同一个 Idempotency-Key 不能用于不同请求")
            return json.loads(row["response_json"])
        result = producer()
        encoded = json.dumps(result, ensure_ascii=False)
        try:
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO harness_idempotency(operation,idempotency_key,request_hash,response_json) VALUES(?,?,?,?)",
                    (operation, token, digest, encoded),
                )
        except sqlite3.IntegrityError:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT request_hash,response_json FROM harness_idempotency WHERE operation=? AND idempotency_key=?",
                    (operation, token),
                ).fetchone()
            if not row or row["request_hash"] != digest:
                raise ValueError("幂等请求发生冲突")
            return json.loads(row["response_json"])
        return result
