from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


BUILTIN_PLUGINS = (
    ("session.sqlite", "SQLite Session Log", "sessions", "sqlite", {}),
    ("persist.jsonl", "JSONL Session Persist", "persist", "jsonl", {"dir": "sessions_jsonl"}),
    ("workspace.local", "Local Workspace (legacy alias)", "workspace", "local", {}),
    ("fs.local", "Local Filesystem", "fs", "local", {}),
    ("shell.local", "Local Controlled Shell", "shell", "local", {}),
    ("shell.deny", "Deny-All Shell", "shell", "deny", {}),
    ("tools.local", "Local Tool Registry", "tools", "local", {}),
    ("llm.template", "Template LLM Adapter", "llm", "template", {}),
    ("llm.codex", "Codex LLM Adapter", "llm", "codex", {}),
    ("eval.command", "Project Eval Command", "eval", "command", {"report_placeholder": "{report_path}"}),
    ("eval.local", "Repository Blocking Eval", "eval", "local", {"suite": "blocking"}),
    ("execution.codex", "Codex Executor", "execution", "codex", {"sandbox": "project-write-scope"}),
    ("execution.verify", "Verify-Only Executor", "execution", "verify", {}),
    ("approval.named", "Named Human Approval", "approval", "named", {"required": True}),
    ("permission.local", "Local Permission Policy", "permission", "local", {"bind": "127.0.0.1"}),
    ("mcp.off", "MCP Disabled", "mcp", "off", {}),
    ("mcp.http", "Optional MCP HTTP Bridge", "mcp", "http", {"env": "HARNESS_MCP_URL"}),
    ("mcp.manifest", "Local MCP Manifest", "mcp", "manifest", {"env": "HARNESS_MCP_MANIFEST"}),
)

DEFAULT_PROFILE_PLUGINS = (
    "session.sqlite",
    "persist.jsonl",
    "workspace.local",
    "fs.local",
    "shell.local",
    "tools.local",
    "llm.codex",
    "eval.command",
    "execution.codex",
    "approval.named",
    "permission.local",
    "mcp.off",
)

HEADLESS_PROFILE_PLUGINS = (
    "session.sqlite",
    "persist.jsonl",
    "workspace.local",
    "fs.local",
    "shell.local",
    "tools.local",
    "llm.codex",
    "eval.command",
    "execution.verify",
    "approval.named",
    "permission.local",
    "mcp.off",
)


class HarnessRuntimeStore:
    """Plugin composition and append-only session facts for the standalone Harness."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._after_append: Callable[[str, dict], None] | None = None
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS harness_plugins(
                  id TEXT PRIMARY KEY,name TEXT NOT NULL,seam TEXT NOT NULL,provider TEXT NOT NULL,
                  config_json TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,builtin INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS harness_profiles(
                  id TEXT PRIMARY KEY,name TEXT NOT NULL,plugin_ids_json TEXT NOT NULL,
                  is_default INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS harness_sessions(
                  id TEXT PRIMARY KEY,project_id TEXT NOT NULL,profile_id TEXT NOT NULL,task_id TEXT,
                  title TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS harness_session_events(
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,kind TEXT NOT NULL,
                  actor TEXT NOT NULL,payload_json TEXT NOT NULL,source_key TEXT,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_harness_session_events ON harness_session_events(session_id,sequence);
                """
            )
            event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(harness_session_events)")}
            if "source_key" not in event_columns:
                conn.execute("ALTER TABLE harness_session_events ADD COLUMN source_key TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_harness_session_event_source "
                "ON harness_session_events(session_id,source_key) WHERE source_key IS NOT NULL"
            )
            for plugin_id, name, seam, provider, config in BUILTIN_PLUGINS:
                conn.execute(
                    "INSERT OR IGNORE INTO harness_plugins(id,name,seam,provider,config_json,enabled,builtin) VALUES(?,?,?,?,?,1,1)",
                    (plugin_id, name, seam, provider, json.dumps(config, ensure_ascii=False)),
                )
            conn.execute(
                "INSERT OR IGNORE INTO harness_profiles(id,name,plugin_ids_json,is_default) VALUES('PROFILE-DEFAULT','Delivery Default',?,1)",
                (json.dumps(list(DEFAULT_PROFILE_PLUGINS)),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO harness_profiles(id,name,plugin_ids_json,is_default) VALUES('PROFILE-HEADLESS','Headless Runner',?,0)",
                (json.dumps(list(HEADLESS_PROFILE_PLUGINS)),),
            )
            self._ensure_profile_seams(conn, "PROFILE-DEFAULT", DEFAULT_PROFILE_PLUGINS)
            self._ensure_profile_seams(conn, "PROFILE-HEADLESS", HEADLESS_PROFILE_PLUGINS)

    @staticmethod
    def _ensure_profile_seams(conn: sqlite3.Connection, profile_id: str, defaults: tuple[str, ...]) -> None:
        row = conn.execute(
            "SELECT plugin_ids_json FROM harness_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()
        if not row:
            return
        plugin_ids = json.loads(row["plugin_ids_json"])
        seam_to_default = {
            seam: plugin_id
            for plugin_id, _name, seam, _provider, _config in BUILTIN_PLUGINS
            if plugin_id in defaults
        }
        present_seams = {
            seam
            for plugin_id, _name, seam, _provider, _config in BUILTIN_PLUGINS
            if plugin_id in plugin_ids
        }
        changed = False
        for seam, default_id in seam_to_default.items():
            if seam not in present_seams:
                plugin_ids.append(default_id)
                changed = True
        if changed:
            conn.execute(
                "UPDATE harness_profiles SET plugin_ids_json=? WHERE id=?",
                (json.dumps(plugin_ids), profile_id),
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

    def set_after_append(self, hook: Callable[[str, dict], None] | None) -> None:
        """Optional hook(session_id, event_dict) for persist seam mirroring."""
        self._after_append = hook

    @staticmethod
    def _plugin(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["config"] = json.loads(item.pop("config_json"))
        item["enabled"] = bool(item["enabled"])
        item["builtin"] = bool(item["builtin"])
        return item

    def plugins(self) -> list[dict]:
        with self.connect() as conn:
            return [self._plugin(row) for row in conn.execute("SELECT * FROM harness_plugins ORDER BY seam,id")]

    def profiles(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM harness_profiles ORDER BY is_default DESC,id").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["plugin_ids"] = json.loads(item.pop("plugin_ids_json"))
            item["is_default"] = bool(item["is_default"])
            items.append(item)
        return items

    def composition(self, profile_id: str = "PROFILE-DEFAULT") -> dict:
        profile = next((item for item in self.profiles() if item["id"] == profile_id), None)
        if not profile:
            raise KeyError(profile_id)
        catalog = {item["id"]: item for item in self.plugins()}
        selected = [catalog[item] for item in profile["plugin_ids"] if item in catalog and catalog[item]["enabled"]]
        required = {"sessions", "workspace", "fs", "shell", "tools", "llm", "eval", "execution", "approval", "permission"}
        seams = {item["seam"] for item in selected}
        return {
            "profile": profile,
            "plugins": selected,
            "seams": sorted(seams),
            "missing_seams": sorted(required - seams),
            "ready": required <= seams,
        }

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> dict:
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM harness_plugins WHERE id=?", (plugin_id,)).fetchone():
                raise KeyError(plugin_id)
            conn.execute("UPDATE harness_plugins SET enabled=? WHERE id=?", (1 if enabled else 0, plugin_id))
        return self.get_plugin(plugin_id)

    def get_plugin(self, plugin_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM harness_plugins WHERE id=?", (plugin_id,)).fetchone()
            if not row:
                raise KeyError(plugin_id)
            return self._plugin(row)

    def activate_plugin(self, profile_id: str, plugin_id: str) -> dict:
        catalog = {item["id"]: item for item in self.plugins()}
        if plugin_id not in catalog:
            raise KeyError(plugin_id)
        target = catalog[plugin_id]
        profile = next((item for item in self.profiles() if item["id"] == profile_id), None)
        if not profile:
            raise KeyError(profile_id)
        plugin_ids: list[str] = []
        replaced = False
        for existing_id in profile["plugin_ids"]:
            existing = catalog.get(existing_id)
            if existing and existing["seam"] == target["seam"]:
                if not replaced:
                    plugin_ids.append(plugin_id)
                    replaced = True
                continue
            plugin_ids.append(existing_id)
        if not replaced:
            plugin_ids.append(plugin_id)
        with self.connect() as conn:
            conn.execute(
                "UPDATE harness_profiles SET plugin_ids_json=? WHERE id=?",
                (json.dumps(plugin_ids), profile_id),
            )
        if not target["enabled"]:
            self.set_plugin_enabled(plugin_id, True)
        return self.composition(profile_id)

    def dump_config(self, profile_id: str = "PROFILE-DEFAULT") -> dict:
        composition = self.composition(profile_id)
        return {
            "product": "Harness Workbench",
            "interface": "terminal",
            "profile": composition.get("profile"),
            "plugins": self.plugins(),
            "composition": composition,
        }

    def create_session(
        self,
        project_id: str,
        title: str,
        actor: str,
        profile_id: str = "PROFILE-DEFAULT",
        task_id: str | None = None,
    ) -> dict:
        if not self.composition(profile_id)["ready"]:
            raise ValueError("Profile 缺少必要能力 seam")
        session_id = f"SESSION-{uuid.uuid4().hex[:12].upper()}"
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO harness_sessions(id,project_id,profile_id,task_id,title) VALUES(?,?,?,?,?)",
                (session_id, project_id, profile_id, task_id, title.strip() or "Untitled Session"),
            )
        self.append(
            session_id,
            "session/start",
            actor,
            {"project_id": project_id, "profile_id": profile_id, "task_id": task_id},
        )
        return self.get_session(session_id)

    def append(
        self,
        session_id: str,
        kind: str,
        actor: str,
        payload: object,
        source_key: str | None = None,
    ) -> dict:
        if not kind.strip() or "/" not in kind:
            raise ValueError("Session 事件类型必须使用 domain/name")
        inserted_event = None
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM harness_sessions WHERE id=?", (session_id,)).fetchone():
                raise KeyError(session_id)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO harness_session_events(session_id,kind,actor,payload_json,source_key) "
                "VALUES(?,?,?,?,?)",
                (
                    session_id,
                    kind.strip(),
                    actor.strip() or "system",
                    json.dumps(payload, ensure_ascii=False),
                    source_key.strip() if source_key else None,
                ),
            )
            conn.execute("UPDATE harness_sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (session_id,))
            if cursor.rowcount:
                row = conn.execute(
                    "SELECT * FROM harness_session_events WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                if row:
                    inserted_event = {**dict(row), "payload": json.loads(row["payload_json"])}
                    inserted_event.pop("payload_json", None)
        if inserted_event is not None and self._after_append is not None:
            self._after_append(session_id, inserted_event)
        return self.get_session(session_id)

    def sync_task(self, session_id: str, task: dict) -> dict:
        """Project durable task facts into the model-visible Session event log once."""
        from .delivery_pipeline import pipeline_payload

        session = self.get_session(session_id)
        if session.get("task_id") != task.get("id"):
            raise ValueError("Session 与 Task 关联不一致")
        for event in task.get("events", []):
            event_id = event.get("id")
            self.append(
                session_id,
                "task/status" if event.get("from_status") != event.get("to_status") else "task/event",
                str(event.get("actor") or "harness"),
                {
                    "task_id": task["id"],
                    "task_event_id": event_id,
                    "from_status": event.get("from_status"),
                    "to_status": event.get("to_status"),
                    "detail": event.get("detail"),
                    "evidence": event.get("evidence"),
                },
                source_key=f"task-event:{event_id}",
            )
            to_status = event.get("to_status")
            from_status = event.get("from_status")
            if to_status and to_status != from_status:
                payload = pipeline_payload(
                    str(to_status),
                    detail=str(event.get("detail") or ""),
                    evidence=event.get("evidence"),
                )
                payload["task_id"] = task["id"]
                payload["from_status"] = from_status
                self.append(
                    session_id,
                    "pipeline/stage",
                    str(event.get("actor") or "harness"),
                    payload,
                    source_key=f"pipeline-stage:{event_id}",
                )
        return self.get_session(session_id)

    def set_status(self, session_id: str, status: str, actor: str) -> dict:
        if status not in {"active", "paused", "closed"}:
            raise ValueError("Session 状态必须是 active、paused 或 closed")
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM harness_sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise KeyError(session_id)
            previous = row["status"]
            if previous == "closed" and status != "closed":
                raise ValueError("已关闭 Session 不可重新打开；请 fork 新 Session")
            conn.execute(
                "UPDATE harness_sessions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, session_id),
            )
        return self.append(session_id, "session/status", actor, {"from": previous, "to": status})

    def bind_task(
        self,
        session_id: str,
        task_id: str,
        actor: str,
        *,
        title: str | None = None,
    ) -> dict:
        """Attach or replace the Session's primary Task binding."""
        task_id = str(task_id or "").strip()
        if not task_id:
            raise ValueError("bind_task 需要 task_id")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM harness_sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise KeyError(session_id)
            if row["status"] == "closed":
                raise ValueError("已关闭 Session 不可绑定新任务；请 fork")
            previous = row["task_id"]
            if title and title.strip():
                conn.execute(
                    "UPDATE harness_sessions SET task_id=?, title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (task_id, title.strip()[:120], session_id),
                )
            else:
                conn.execute(
                    "UPDATE harness_sessions SET task_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (task_id, session_id),
                )
        return self.append(
            session_id,
            "task/bound",
            actor,
            {"task_id": task_id, "previous_task_id": previous},
        )

    def fork(self, session_id: str, title: str, actor: str) -> dict:
        source = self.get_session(session_id)
        forked = self.create_session(
            source["project_id"],
            title.strip() or f"Fork of {source['title']}",
            actor,
            source["profile_id"],
        )
        return self.append(
            forked["id"],
            "session/forked",
            actor,
            {"source_session_id": session_id, "source_sequence": source["events"][-1]["sequence"]},
        )

    def get_session(self, session_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM harness_sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise KeyError(session_id)
            events = conn.execute(
                "SELECT * FROM harness_session_events WHERE session_id=? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        item = dict(row)
        item["events"] = [{**dict(event), "payload": json.loads(event["payload_json"])} for event in events]
        for event in item["events"]:
            event.pop("payload_json", None)
        return item

    def sessions(self, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM harness_sessions ORDER BY updated_at DESC,id DESC LIMIT ?",
                    (limit,),
                )
            ]

    def session_for_task(self, task_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM harness_sessions WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None
