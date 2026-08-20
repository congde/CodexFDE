from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .execution import normalize_write_scope


VALID_TRANSITIONS = {
    "queued": {"spec_ready", "failed"},
    "spec_ready": {"executing", "failed"},
    "executing": {"evaluating", "failed"},
    "evaluating": {"review", "rework", "failed"},
    "review": {"completed", "rework", "failed"},
    "rework": {"executing", "failed"},
    "completed": set(),
    "failed": set(),
}


class TaskStore:
    def __init__(self, path: str | Path = ".runtime/workbench.db") -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks(
                  id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL,
                  spec_json TEXT, result_json TEXT, error TEXT,
                  requirement_id TEXT NOT NULL DEFAULT '',
                  business_refs_json TEXT NOT NULL DEFAULT '[]',
                  spec_path TEXT NOT NULL DEFAULT 'FDE_SPEC.md',
                  reviewed_by TEXT, review_decision TEXT, review_note TEXT, reviewed_at TEXT,
                  automation_mode TEXT NOT NULL DEFAULT 'manual',
                  execution_mode TEXT NOT NULL DEFAULT 'verify',
                  write_scope_json TEXT NOT NULL DEFAULT '[]',
                  execution_timeout_seconds INTEGER NOT NULL DEFAULT 900,
                  version INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS task_events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                  from_status TEXT, to_status TEXT NOT NULL, detail TEXT,
                  actor TEXT NOT NULL DEFAULT 'system', evidence_json TEXT,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self._ensure_columns(conn, "tasks", {
                "requirement_id": "TEXT NOT NULL DEFAULT ''",
                "business_refs_json": "TEXT NOT NULL DEFAULT '[]'",
                "spec_path": "TEXT NOT NULL DEFAULT 'FDE_SPEC.md'",
                "reviewed_by": "TEXT",
                "review_decision": "TEXT",
                "review_note": "TEXT",
                "reviewed_at": "TEXT",
                "automation_mode": "TEXT NOT NULL DEFAULT 'manual'",
                "execution_mode": "TEXT NOT NULL DEFAULT 'verify'",
                "write_scope_json": "TEXT NOT NULL DEFAULT '[]'",
                "execution_timeout_seconds": "INTEGER NOT NULL DEFAULT 900",
                "version": "INTEGER NOT NULL DEFAULT 1",
            })
            self._ensure_columns(conn, "task_events", {
                "actor": "TEXT NOT NULL DEFAULT 'system'",
                "evidence_json": "TEXT",
            })

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create(
        self,
        request: str,
        requirement_id: str = "",
        business_refs: list[str] | None = None,
        spec_path: str = "FDE_SPEC.md",
        actor: str = "system",
        automation_mode: str = "manual",
        task_id: str | None = None,
        execution_mode: str = "verify",
        write_scope: list[str] | None = None,
        execution_timeout_seconds: int = 900,
    ) -> dict:
        if not request.strip():
            raise ValueError("任务需求不能为空")
        refs = [str(value).strip() for value in (business_refs or []) if str(value).strip()]
        if len(refs) != len(set(refs)):
            raise ValueError("业务对象引用不能重复")
        if not spec_path.strip():
            raise ValueError("Spec 路径不能为空")
        if automation_mode not in {"manual", "automatic"}:
            raise ValueError("automation_mode 必须是 manual 或 automatic")
        if execution_mode not in {"verify", "codex"}:
            raise ValueError("execution_mode 必须是 verify 或 codex")
        scopes = normalize_write_scope(write_scope)
        if execution_mode == "codex" and not scopes:
            raise ValueError("Codex 代码执行至少需要一个明确写入范围")
        if not 30 <= int(execution_timeout_seconds) <= 3600:
            raise ValueError("execution_timeout_seconds 必须在 30..3600")
        task_id = task_id or f"TASK-{uuid.uuid4().hex[:10].upper()}"
        if not re.fullmatch(r"TASK-[A-Z0-9]{10}", task_id):
            raise ValueError("任务编号格式无效")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO tasks(id,request,status,requirement_id,business_refs_json,spec_path,automation_mode,"
                "execution_mode,write_scope_json,execution_timeout_seconds) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (task_id, request.strip(), "queued", requirement_id.strip(), json.dumps(refs, ensure_ascii=False),
                 spec_path.strip(), automation_mode, execution_mode, json.dumps(scopes, ensure_ascii=False),
                 int(execution_timeout_seconds)),
            )
            conn.execute(
                "INSERT INTO task_events(task_id,to_status,detail,actor,evidence_json) VALUES(?,?,?,?,?)",
                (task_id, "queued", "任务已接收", actor.strip() or "system", json.dumps({
                    "requirement_id": requirement_id.strip(), "business_refs": refs, "spec_path": spec_path.strip(),
                    "automation_mode": automation_mode, "execution_mode": execution_mode,
                    "write_scope": scopes, "execution_timeout_seconds": int(execution_timeout_seconds),
                }, ensure_ascii=False)),
            )
        return self.get(task_id)

    def get(self, task_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            item = dict(row)
            for field in ("spec_json", "result_json", "business_refs_json", "write_scope_json"):
                output_name = field[:-5] if field.endswith("_json") else field
                raw = item.pop(field)
                item[output_name] = json.loads(raw) if raw else ([] if output_name == "business_refs" else None)
            events = []
            for row in conn.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (task_id,)):
                event = dict(row)
                raw_evidence = event.pop("evidence_json", None)
                event["evidence"] = json.loads(raw_evidence) if raw_evidence else None
                events.append(event)
            item["events"] = events
            return item

    def list(self, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            items = []
            for row in conn.execute(
                "SELECT id,request,status,error,requirement_id,business_refs_json,spec_path,reviewed_by,"
                "review_decision,review_note,reviewed_at,automation_mode,execution_mode,write_scope_json,"
                "execution_timeout_seconds,version,created_at,updated_at "
                "FROM tasks ORDER BY created_at DESC,id DESC LIMIT ?", (limit,),
            ):
                item = dict(row)
                item["business_refs"] = json.loads(item.pop("business_refs_json") or "[]")
                item["write_scope"] = json.loads(item.pop("write_scope_json") or "[]")
                items.append(item)
            return items

    def append_event(self, task_id: str, detail: str, *, actor: str = "system",
                     evidence: object = None) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            conn.execute(
                "INSERT INTO task_events(task_id,from_status,to_status,detail,actor,evidence_json) "
                "VALUES(?,?,?,?,?,?)",
                (task_id, row["status"], row["status"], detail, actor.strip() or "system",
                 json.dumps(evidence, ensure_ascii=False) if evidence is not None else None),
            )
            conn.execute("UPDATE tasks SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
        return self.get(task_id)

    def recover_automatic_tasks(self) -> list[str]:
        """Return durable automatic work that is safe to resume after a process restart."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            interrupted = list(conn.execute(
                "SELECT id,status FROM tasks WHERE automation_mode='automatic' "
                "AND status IN ('executing','evaluating')"
            ))
            for row in interrupted:
                conn.execute(
                    "UPDATE tasks SET status='rework',error=?,version=version+1,updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status=?",
                    (f"服务重启时任务停在 {row['status']}，已转入安全重放", row["id"], row["status"]),
                )
                conn.execute(
                    "INSERT INTO task_events(task_id,from_status,to_status,detail,actor,evidence_json) "
                    "VALUES(?,?,?,?,?,?)",
                    (row["id"], row["status"], "rework", "检测到中断执行，进入安全重放",
                     "automation-recovery", json.dumps({"safe_replay": True}, ensure_ascii=False)),
                )
            rows = conn.execute(
                "SELECT id FROM tasks WHERE automation_mode='automatic' "
                "AND (status='queued' OR id IN (%s)) ORDER BY created_at,id" % (
                    ",".join("?" for _ in interrupted) or "NULL"
                ), tuple(row["id"] for row in interrupted),
            ).fetchall()
        return [row["id"] for row in rows]

    def transition(
        self,
        task_id: str,
        to_status: str,
        detail: str = "",
        *,
        actor: str = "system",
        evidence: object = None,
        **payload: object,
    ) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status,result_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            current = row["status"]
            if to_status not in VALID_TRANSITIONS.get(current, set()):
                raise ValueError(f"非法任务状态迁移：{current} -> {to_status}")
            reviewer = str(payload.get("reviewer", "")).strip()
            decision = str(payload.get("review_decision", "")).strip()
            review_note = str(payload.get("review_note", "")).strip()
            if to_status == "completed":
                if not reviewer or decision != "approve":
                    raise ValueError("完成交付必须由具名审核人明确 approve")
                result = json.loads(row["result_json"]) if row["result_json"] else {}
                summary = result.get("summary", {}) if isinstance(result, dict) else {}
                if summary.get("decision") != "pass" or int(summary.get("blocking_failed", 0)) != 0:
                    raise ValueError("阻断级 Eval 未通过，不能批准完成")
            spec_json = json.dumps(payload.get("spec"), ensure_ascii=False) if "spec" in payload else None
            result_json = json.dumps(payload.get("result"), ensure_ascii=False) if "result" in payload else None
            error = str(payload.get("error")) if payload.get("error") else None
            updated = conn.execute(
                "UPDATE tasks SET status=?,spec_json=COALESCE(?,spec_json),result_json=COALESCE(?,result_json),"
                "error=COALESCE(?,error),reviewed_by=COALESCE(?,reviewed_by),"
                "review_decision=COALESCE(?,review_decision),review_note=COALESCE(?,review_note),"
                "reviewed_at=CASE WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP ELSE reviewed_at END,"
                "version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
                (to_status, spec_json, result_json, error, reviewer or None, decision or None, review_note or None,
                 reviewer or None, task_id, current),
            )
            if updated.rowcount != 1:
                raise ValueError(f"任务状态已变化，拒绝并发迁移：{current} -> {to_status}")
            conn.execute(
                "INSERT INTO task_events(task_id,from_status,to_status,detail,actor,evidence_json) VALUES(?,?,?,?,?,?)",
                (task_id, current, to_status, detail, actor.strip() or "system",
                 json.dumps(evidence, ensure_ascii=False) if evidence is not None else None),
            )
        return self.get(task_id)

    def review(self, task_id: str, reviewer: str, decision: str, note: str) -> dict:
        reviewer = reviewer.strip()
        decision = decision.strip().lower()
        note = note.strip()
        if not reviewer:
            raise ValueError("审核人不能为空")
        if decision not in {"approve", "reject"}:
            raise ValueError("审核决定必须是 approve 或 reject")
        if not note:
            raise ValueError("审核理由不能为空")
        target = "completed" if decision == "approve" else "rework"
        return self.transition(
            task_id, target, f"人工审核：{decision}；{note}", actor=reviewer,
            evidence={"reviewer": reviewer, "decision": decision, "note": note},
            reviewer=reviewer, review_decision=decision, review_note=note,
        )
