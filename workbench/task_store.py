from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .execution import normalize_write_scope


VALID_TRANSITIONS = {
    "queued": {"spec_ready", "failed", "dead_letter"},
    "spec_ready": {"executing", "failed", "dead_letter"},
    "executing": {"evaluating", "rework", "failed", "dead_letter"},
    "evaluating": {"review", "rework", "failed", "dead_letter"},
    "review": {"completed", "rework", "failed", "dead_letter"},
    "rework": {"executing", "failed", "dead_letter"},
    "completed": set(),
    "failed": {"dead_letter"},
    "dead_letter": set(),
}


class TaskSubmissionConflict(ValueError):
    """An existing submission key belongs to a different request."""


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
                CREATE TABLE IF NOT EXISTS task_submissions(
                  submission_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                  task_id TEXT NOT NULL REFERENCES tasks(id)
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
                "spec_text": "TEXT",
                "spec_sha256": "TEXT",
                "workspace_path": "TEXT NOT NULL DEFAULT ''",
                "authorization_policy": "TEXT NOT NULL DEFAULT 'legacy'",
                "source_task_id": "TEXT NOT NULL DEFAULT ''",
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
        submission_key: str | None = None,
        frozen_contract: dict | None = None,
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
        fingerprint = hashlib.sha256(json.dumps({
            "request": request.strip(), "requirement": requirement_id.strip(), "refs": refs,
            "actor": actor.strip(), "mode": execution_mode, "scope": scopes,
            "timeout": int(execution_timeout_seconds), "automation": automation_mode,
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        with self.connect() as conn:
            if submission_key is not None:
                if not submission_key.strip() or len(submission_key) > 200:
                    raise ValueError("提交键不能为空且不能超过200字符")
                conn.execute("BEGIN IMMEDIATE")
                previous = conn.execute("SELECT * FROM task_submissions WHERE submission_key=?", (submission_key,)).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise TaskSubmissionConflict("提交键已用于不同需求；请使用新键")
                    return self.get(previous["task_id"])
            conn.execute(
                "INSERT INTO tasks(id,request,status,requirement_id,business_refs_json,spec_path,automation_mode,"
                "execution_mode,write_scope_json,execution_timeout_seconds) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (task_id, request.strip(), "queued", requirement_id.strip(), json.dumps(refs, ensure_ascii=False),
                 spec_path.strip(), automation_mode, execution_mode, json.dumps(scopes, ensure_ascii=False),
                int(execution_timeout_seconds)),
            )
            if frozen_contract:
                conn.execute(
                    "UPDATE tasks SET spec_text=?,spec_sha256=?,spec_json=?,workspace_path=?,"
                    "authorization_policy=?,source_task_id=? WHERE id=?",
                    (frozen_contract['text'], frozen_contract['sha256'],
                     json.dumps(frozen_contract['parsed'], ensure_ascii=False),
                     frozen_contract['workspace_path'], 'v0', frozen_contract.get('source_task_id', ''), task_id))
            conn.execute(
                "INSERT INTO task_events(task_id,to_status,detail,actor,evidence_json) VALUES(?,?,?,?,?)",
                (task_id, "queued", "任务已接收", actor.strip() or "system", json.dumps({
                    "requirement_id": requirement_id.strip(), "business_refs": refs, "spec_path": spec_path.strip(),
                    "automation_mode": automation_mode, "execution_mode": execution_mode,
                    "write_scope": scopes, "execution_timeout_seconds": int(execution_timeout_seconds),
                }, ensure_ascii=False)),
            )
            if submission_key is not None:
                conn.execute("INSERT INTO task_submissions VALUES(?,?,?)", (submission_key, fingerprint, task_id))
        return self.get(task_id)

    def create_v0(self, request: str, *, spec_path: str, actor: str,
                  execution_mode: str = 'verify', workspace_path: str = '',
                  write_scope: list[str] | None = None, execution_timeout_seconds: int = 900,
                  requirement_id: str = '', business_refs: list[str] | None = None,
                  source_task_id: str = '') -> dict:
        """Strict V0 entry; legacy course creators retain their declared contracts."""
        from .spec import parse_spec
        from .execution import validate_v0_authorization
        if not actor.strip():
            raise ValueError('V0 任务需要实际提交者标识')
        path = Path(spec_path).resolve()
        if path.suffix.lower() != '.md':
            raise ValueError('Spec 必须是 Markdown 文件')
        try:
            raw = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            raise ValueError(f'Spec 无法读取：{path}') from exc
        parsed = parse_spec(raw).as_dict()
        workspace = validate_v0_authorization(execution_mode, workspace_path, write_scope,
                                              execution_timeout_seconds)
        if source_task_id:
            with self.connect() as conn:
                has_bootstrap = conn.execute("SELECT 1 FROM sqlite_master WHERE name='bootstrap_tasks'").fetchone()
                previous = conn.execute('SELECT id FROM tasks WHERE id=?', (source_task_id,)).fetchone()
                if not previous and has_bootstrap:
                    previous = conn.execute('SELECT id FROM bootstrap_tasks WHERE id=?', (source_task_id,)).fetchone()
                if not previous:
                    raise ValueError('来源任务不存在；请使用同一运行目录中的真实记录')
        return self.create(request, requirement_id=requirement_id, business_refs=business_refs,
                           spec_path=str(path), actor=actor, execution_mode=execution_mode,
                           write_scope=write_scope, execution_timeout_seconds=execution_timeout_seconds,
                           frozen_contract={'text': raw, 'sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                                            'parsed': parsed, 'workspace_path': workspace,
                                            'source_task_id': source_task_id})

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

    def quarantine_interrupted_web_code_tasks(self) -> list[str]:
        """Call only after acquiring the workbench service runtime lease."""
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = list(conn.execute(
                "SELECT id,status FROM tasks WHERE execution_mode='codex' "
                "AND status IN ('queued','spec_ready','executing','evaluating','review') "
                "AND EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=tasks.id "
                "AND e.detail IN ('网页具名授权课程隔离执行','网页具名授权日常研发'))"
            ))
            quarantined = []
            for row in rows:
                if row['status'] == 'review':
                    package = conn.execute("SELECT evidence_json FROM task_events WHERE task_id=? AND detail='日常研发交付包已保存' ORDER BY id DESC LIMIT 1", (row['id'],)).fetchone()
                    if package:
                        try:
                            daily = json.loads(package['evidence_json'] or '{}')
                            if daily.get('status') == 'review':
                                continue
                        except (ValueError, TypeError, AttributeError):
                            pass
                    gate = conn.execute("SELECT evidence_json FROM task_events WHERE task_id=? AND detail='课程红绿差分判定已完成' ORDER BY id DESC LIMIT 1", (row['id'],)).fetchone()
                    try:
                        evidence = json.loads(gate['evidence_json'] or '{}') if gate else None
                    except (ValueError, TypeError):
                        evidence = None
                    if isinstance(evidence, dict) and evidence.get('accepted') is True:
                        continue
                quarantined.append(row['id'])
                conn.execute("UPDATE tasks SET status='dead_letter',error=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             ('网页代码执行中断，先核对残留进程、隔离副本与证据，禁止自动重写', row['id']))
                conn.execute('INSERT INTO task_events(task_id,from_status,to_status,detail,actor,evidence_json) VALUES(?,?,?,?,?,?)',
                             (row['id'], row['status'], 'dead_letter', '网页代码任务中断，等待人工核对',
                              'workbench-recovery', json.dumps({'safe_replay': False, 'human_review_required': True})))
            return quarantined

    def recover_automatic_tasks(self) -> list[str]:
        """Return durable automatic work that is safe to resume after a process restart."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            interrupted = list(conn.execute(
                "SELECT id,status,execution_mode FROM tasks WHERE automation_mode='automatic' "
                "AND status IN ('executing','evaluating')"
            ))
            replayable = []
            for row in interrupted:
                if row["execution_mode"] == "codex":
                    # A process crash loses the executor's live ownership and may
                    # leave writes without an independent completion snapshot.
                    conn.execute(
                        "UPDATE tasks SET status='dead_letter',error=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        ("代码执行中断，需人工核对残留进程、Diff与证据后另行授权", row["id"]),
                    )
                    conn.execute(
                        "INSERT INTO task_events(task_id,from_status,to_status,detail,actor,evidence_json) VALUES(?,?,?,?,?,?)",
                        (row["id"], row["status"], "dead_letter", "代码任务中断，禁止自动重复写入",
                         "automation-recovery", json.dumps({"safe_replay": False, "human_review_required": True})),
                    )
                    continue
                replayable.append(row["id"])
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
                "AND (status IN ('queued','spec_ready') OR id IN (%s)) ORDER BY created_at,id" % (
                    ",".join("?" for _ in replayable) or "NULL"
                ), tuple(replayable),
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
        from .agent_roster import assert_boss_actor

        reviewer = assert_boss_actor(reviewer)
        decision = decision.strip().lower()
        note = note.strip()
        if decision not in {"approve", "reject"}:
            raise ValueError("审核决定必须是 approve 或 reject")
        if not note:
            raise ValueError("审核理由不能为空")
        target = "completed" if decision == "approve" else "rework"
        task = self.get(task_id)
        if decision == 'approve' and task.get('requirement_id') == 'WB-L04-BOOTSTRAP':
            if task.get('authorization_policy') == 'v0':
                builders = {event['actor'] for event in task['events']
                            if event.get('from_status') is None or
                            (event.get('to_status') == 'executing' and
                             (event.get('evidence') or {}).get('execution_mode') == 'codex')}
                evaluators = [event['actor'] for event in task['events'] if event.get('to_status') == 'evaluating']
                if reviewer in builders or not evaluators or evaluators[-1] in builders or evaluators[-1].startswith('agent:'):
                    raise ValueError('Ticket A 必须由实际非构建者亲自复验并审核；当前仍待独立接受')
            from .bootstrap_source import verify_control_source
            verify_control_source((task.get('result') or {}).get('bootstrap_source'))
        return self.transition(
            task_id, target, f"老板终审：{decision}；{note}", actor=reviewer,
            evidence={"reviewer": reviewer, "decision": decision, "note": note, "opc_final": True},
            reviewer=reviewer, review_decision=decision, review_note=note,
        )
