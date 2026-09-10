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
                CREATE TABLE IF NOT EXISTS workbench_settings(
                  key TEXT PRIMARY KEY, value TEXT NOT NULL
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
               eval_command: list[str], project_id: str = "", *, allow_pending_eval=False) -> dict:
        label = name.strip()
        root = Path(root_path).resolve()
        command = [str(part).strip() for part in eval_command if str(part).strip()]
        if not label:
            raise ValueError("项目名称不能为空")
        if not root.is_dir():
            raise ValueError("目标项目目录不存在")
        if not (root / ".git").exists():
            raise ValueError("目标项目必须是独立 Git 工作区")
        if not command and not allow_pending_eval:
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

    def rename(self, project_id: str, name: str) -> dict:
        """Change the display name without replacing identity or linked records."""
        label = name.strip()
        if not label:
            raise ValueError("项目名称不能为空")
        self.get(project_id)
        with self.connect() as conn:
            conn.execute("UPDATE harness_projects SET name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (label, project_id))
        return self.get(project_id)

    def set_default(self, project_id: str) -> dict:
        item = self.get(project_id)
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO workbench_settings VALUES ('default_project', ?)", (project_id,))
        return item

    def configure(self, project_id: str, command: list[str]) -> dict:
        self.get(project_id)
        with self.connect() as conn:
            conn.execute('UPDATE harness_projects SET eval_command_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                         (json.dumps(command, ensure_ascii=False), project_id))
        return self.get(project_id)

    def default(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM workbench_settings WHERE key='default_project'").fetchone()
        return self.get(row['value']) if row else None

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


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="核对或修正已登记本地项目的显示名称")
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--name', help='省略时仅查看；填写时修改此目录对应的项目显示名')
    args = parser.parse_args()
    database = args.runtime_dir / 'workbench.db'
    try:
        if not database.is_file():
            raise ValueError('运行目录没有工作台数据库，请先启动本讲工作台')
        store = ProjectStore(database)
        item = next((p for p in store.list() if Path(p['root_path']).resolve() == args.root.resolve()), None)
        if item is None:
            raise ValueError('源码目录尚未登记，请使用首页的管理项目入口')
        if args.name is not None:
            item = store.rename(item['id'], args.name)
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
