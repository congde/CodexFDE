from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

from .agent_roster import (
    assert_boss_actor,
    build_agent_critique,
    list_employees,
    resolve,
)
from .automation import DeliveryAutomation
from .agent_loop import AgentLoop, AgentLoopConfig
from .course_mainline import LESSONS, lesson_contract, validate_mainline
from .evolution import EvolutionStore
from .feedback import add_feedback, review_feedback, summary as feedback_summary
from .project_runner import ProjectEvalRunner, ProjectExecutionRunner
from .project_store import ProjectStore
from .providers import HarnessProviders
from .runtime_store import HarnessRuntimeStore
from .task_store import TaskStore
from .tool_registry import ToolRegistry
from .session_context import derive_messages
from .session_export import export_session_bundle
from .session_graph import session_delivery_graph
from .tools import register_default_tools


@dataclass
class PlatformResponse:
    status: int
    body: object
    headers: dict[str, str] = field(default_factory=dict)


class HarnessPlatformAPI:
    """Standalone, project-neutral API for the personal delivery Harness."""

    def __init__(self, runtime_dir: str | Path = ".harness-runtime",
                 repository_root: str | Path | None = None) -> None:
        self.runtime_dir = Path(runtime_dir).resolve()
        self.repository_root = Path(repository_root or Path.cwd()).resolve()
        self.projects = ProjectStore(self.runtime_dir / "platform.db")
        self.runtime = HarnessRuntimeStore(self.runtime_dir / "platform.db")
        self.tasks = TaskStore(self.runtime_dir / "tasks.db")
        self.evolutions = EvolutionStore(self.tasks.path)
        self.providers = HarnessProviders(self.runtime, self.projects, self.runtime_dir, self.repository_root)
        self.tools = ToolRegistry(self.runtime)
        register_default_tools(self.tools, self.tasks, self.providers)
        self.agent_loop = AgentLoop(self.tools, self.runtime, self.tasks, self.providers, AgentLoopConfig())
        self.automation = DeliveryAutomation(
            self.tasks, self.runtime_dir,
            suite_runner=self.providers._eval_runner,
            execution_runner=ProjectExecutionRunner(self.projects, self.runtime_dir),
            agent_runner=self._run_agent_loop,
        )
        self.automation.recover()
        self._install_persist_hook()

    def _install_persist_hook(self) -> None:
        def after_append(session_id: str, event: dict) -> None:
            session = self.runtime.get_session(session_id)
            profile_id = str(session.get("profile_id") or "PROFILE-DEFAULT")
            persist = self.providers.persist_jsonl(profile_id)
            if persist is not None:
                persist.append_event(session_id, event)

        self.runtime.set_after_append(after_append)
    def _run_agent_loop(self, task_id: str, actor: str) -> dict:
        session = self.runtime.session_for_task(task_id)
        if not session:
            from .workflow import run_task
            suite_runner = ProjectEvalRunner(self.projects, self.runtime_dir)
            return run_task(
                self.tasks, task_id, actor=actor,
                suite_runner=suite_runner.for_task(self.tasks.get(task_id)),
                execution_runner=ProjectExecutionRunner(self.projects, self.runtime_dir),
            )
        return self.agent_loop.run(session["id"], task_id, actor)

    def dispatch(self, method: str, raw_path: str, headers: dict[str, str], body: object) -> PlatformResponse:
        path, _, query_string = raw_path.partition("?")
        path = path.rstrip("/") or "/"
        query = {key: values[-1] for key, values in parse_qs(query_string).items()}
        data = body if isinstance(body, dict) else {}
        try:
            if path == "/api/v1/health" and method == "GET":
                return PlatformResponse(200, {
                    "status": "ok", "product": "Harness Workbench",
                    "runtime_dir": str(self.runtime_dir),
                    "repository_root": str(self.repository_root),
                    "project_count": len(self.projects.list()),
                })
            if path == "/api/v1/capabilities" and method == "GET":
                return PlatformResponse(200, {
                    **self.providers.capabilities(),
                    "composition": self.runtime.composition(),
                    "tools": self.tools.list_tools(),
                    "opc": {
                        "mode": "super_individual",
                        "employees": list_employees(),
                        "multi_user_accounts": False,
                    },
                })
            if path == "/api/v1/employees" and method == "GET":
                return PlatformResponse(200, {
                    "items": list_employees(),
                    "mode": "opc",
                    "boss_role": "human_operator",
                    "note": "员工是 Agent，不是多用户账号；老板用 X-Workbench-Actor 具名终审。",
                })
            if path == "/api/v1/tools" and method == "GET":
                return PlatformResponse(200, {"items": self.tools.list_tools()})
            if path.startswith("/api/v1/tools/") and method == "GET":
                tool_id = path.rsplit("/", 1)[-1]
                spec = self.tools.get(tool_id)
                return PlatformResponse(200, {
                    "id": spec.id,
                    "name": spec.name,
                    "description": spec.description,
                    "permissions": sorted(spec.permissions),
                })
            if path == "/api/v1/plugins" and method == "GET":
                return PlatformResponse(200, {"items": self.runtime.plugins()})
            if path == "/api/v1/profiles" and method == "GET":
                return PlatformResponse(200, {"items": self.runtime.profiles()})
            if path.startswith("/api/v1/profiles/") and path.endswith("/composition") and method == "GET":
                return PlatformResponse(200, self.runtime.composition(path.split("/")[4]))
            if path.startswith("/api/v1/profiles/") and path.endswith("/activate") and method == "POST":
                parts = path.split("/")
                profile_id = parts[4]
                actor, key = self._write_identity(headers)
                plugin_id = str(data.get("plugin_id", ""))
                payload = {**data, "profile_id": profile_id, "plugin_id": plugin_id, "actor": actor}
                result = self.projects.idempotent(
                    "profile-activate", key, payload,
                    lambda: self.runtime.activate_plugin(profile_id, plugin_id),
                )
                return PlatformResponse(200, result)
            if path.startswith("/api/v1/plugins/") and path.endswith("/enabled") and method == "POST":
                plugin_id = path.split("/")[4]
                actor, key = self._write_identity(headers)
                enabled = bool(data.get("enabled", True))
                payload = {**data, "plugin_id": plugin_id, "actor": actor}
                result = self.projects.idempotent(
                    "plugin-enabled", key, payload,
                    lambda: self.runtime.set_plugin_enabled(plugin_id, enabled),
                )
                return PlatformResponse(200, result)
            if path == "/api/v1/sessions":
                if method == "GET": return PlatformResponse(200, {"items": self.runtime.sessions(int(query.get("limit", "100")))})
                if method == "POST":
                    actor, key = self._write_identity(headers)
                    self.projects.get(str(data.get("project_id", "")))
                    result = self.projects.idempotent("session-create", key, data, lambda: self.runtime.create_session(
                        str(data.get("project_id", "")), str(data.get("title", "")), actor,
                        str(data.get("profile_id", "PROFILE-DEFAULT")),
                    ))
                    return PlatformResponse(HTTPStatus.CREATED, result)
            if path == "/api/v1/dump-config" and method == "GET":
                profile_id = query.get("profile_id", "PROFILE-DEFAULT")
                return PlatformResponse(200, self.runtime.dump_config(profile_id))
            if path.startswith("/api/v1/sessions/") and method == "GET":
                parts = path.split("/")
                session_id = parts[4] if len(parts) > 4 else ""
                if len(parts) == 6 and parts[5] == "messages":
                    session = self.runtime.get_session(session_id)
                    return PlatformResponse(200, {
                        "session_id": session_id,
                        "messages": derive_messages(session),
                    })
                if len(parts) == 6 and parts[5] == "export":
                    session = self.runtime.get_session(session_id)
                    task = self.tasks.get(session["task_id"]) if session.get("task_id") else None
                    return PlatformResponse(200, export_session_bundle(
                        self.runtime, session_id, task=task, repository_root=self.repository_root,
                    ))
                if len(parts) == 6 and parts[5] == "graph":
                    session = self.runtime.get_session(session_id)
                    task = self.tasks.get(session["task_id"]) if session.get("task_id") else None
                    return PlatformResponse(200, session_delivery_graph(session, task))
                if len(parts) == 6 and parts[5] == "agent":
                    return PlatformResponse(200, self.agent_loop.status(session_id))
                session = self.runtime.get_session(session_id)
                if session.get("task_id"):
                    session = self.runtime.sync_task(session["id"], self.tasks.get(session["task_id"]))
                return PlatformResponse(200, session)
            if path.startswith("/api/v1/sessions/") and method == "POST":
                parts = path.split("/")
                session_id = parts[4] if len(parts) > 4 else ""
                actor, key = self._write_identity(headers)
                payload = {**data, "session_id": session_id, "actor": actor}
                if len(parts) == 6 and parts[5] == "fork":
                    result = self.projects.idempotent(
                        "session-fork", key, payload,
                        lambda: self.runtime.fork(session_id, str(data.get("title", "")), actor),
                    )
                    return PlatformResponse(HTTPStatus.CREATED, result)
                if len(parts) == 6 and parts[5] == "status":
                    result = self.projects.idempotent(
                        "session-status", key, payload,
                        lambda: self.runtime.set_status(session_id, str(data.get("status", "")), actor),
                    )
                    return PlatformResponse(200, result)
                if len(parts) == 6 and parts[5] == "prompt":
                    result = self.projects.idempotent(
                        "session-prompt", key, payload,
                        lambda: self._session_prompt(session_id, actor, data),
                    )
                    return PlatformResponse(HTTPStatus.ACCEPTED, result)
                if len(parts) == 7 and parts[5] == "employees" and parts[6] == "assign":
                    result = self.projects.idempotent(
                        "employees-assign", key, payload,
                        lambda: self._assign_employees(session_id, actor, data),
                    )
                    return PlatformResponse(200, result)
                if len(parts) == 7 and parts[5] == "agent" and parts[6] == "start":
                    task_id = str(data.get("task_id") or self.runtime.get_session(session_id).get("task_id") or "")
                    if not task_id:
                        raise ValueError("agent/start 需要 task_id 或 Session 已绑定 Task")
                    result = self.projects.idempotent(
                        "agent-start", key, payload,
                        lambda: self.agent_loop.run(session_id, task_id, actor),
                    )
                    return PlatformResponse(200, result)
                if len(parts) == 7 and parts[5] == "agent" and parts[6] == "step":
                    task_id = str(data.get("task_id") or self.runtime.get_session(session_id).get("task_id") or "")
                    if not task_id:
                        raise ValueError("agent/step 需要 task_id 或 Session 已绑定 Task")
                    round_no = int(data.get("round", 0)) or (
                        self.agent_loop.status(session_id)["turns_completed"] + 1
                    )
                    result = self.projects.idempotent(
                        "agent-step", key, {**payload, "round": round_no},
                        lambda: self.agent_loop.run_round(session_id, task_id, actor, round_no=round_no),
                    )
                    return PlatformResponse(200, result)
                if len(parts) == 7 and parts[5] == "tools" and method == "POST":
                    tool_id = parts[6]
                    task_id = str(data.get("task_id") or self.runtime.get_session(session_id).get("task_id") or "")
                    if not task_id:
                        raise ValueError("tool invoke 需要 task_id 或 Session 已绑定 Task")
                    task = self.tasks.get(task_id)
                    call_id = str(data.get("call_id") or key)
                    session = self.runtime.get_session(session_id)
                    profile_id = str(session.get("profile_id") or "PROFILE-DEFAULT")
                    context = {
                        "task": task,
                        "session_id": session_id,
                        "actor": actor,
                        "profile_id": profile_id,
                        "projects": self.projects,
                        "repository_root": self.repository_root,
                        "fs": self.providers.fs_provider(profile_id),
                        "shell": self.providers.shell_provider(profile_id),
                        "mcp": self.providers.mcp_provider(profile_id),
                        "allowed_actions": list(data.get("allowed_actions") or self._task_allowed_actions(task)),
                        "auto_approve": bool(data.get("auto_approve", True)),
                        "approved_tools": list(data.get("approved_tools") or ["*"]),
                    }
                    result = self.projects.idempotent(
                        f"tool-{tool_id}", key, payload,
                        lambda: self.tools.invoke(
                            tool_id, context, dict(data.get("args") or {}),
                            session_id=session_id, actor=actor, call_id=call_id,
                        ),
                    )
                    return PlatformResponse(200, result)
            if path == "/api/v1/projects":
                if method == "GET": return PlatformResponse(200, {"items": self.projects.list()})
                if method == "POST":
                    actor, key = self._write_identity(headers)
                    result = self.projects.idempotent("project-create", key, data, lambda: self.projects.create(
                        str(data.get("name", "")), str(data.get("root_path", "")),
                        list(data.get("eval_command") or []), str(data.get("id", "")),
                    ))
                    result["registered_by"] = actor
                    return PlatformResponse(HTTPStatus.CREATED, result)
            if path.startswith("/api/v1/projects/") and method == "GET":
                return PlatformResponse(200, self.projects.get(path.rsplit("/", 1)[-1]))
            if path == "/api/v1/tasks":
                if method == "GET":
                    return PlatformResponse(200, {"items": self.tasks.list(int(query.get("limit", "100")))})
                if method == "POST":
                    actor, key = self._write_identity(headers)
                    project_id = str(data.get("project_id", ""))
                    self.projects.get(project_id)
                    refs = [f"PROJECT:{project_id}", *list(data.get("business_refs") or [])]
                    def submit_task() -> dict:
                        task = self.automation.submit(
                            str(data.get("request", "")), str(data.get("requirement_id", "")), refs, actor,
                            "codex" if data.get("execute_code") else "verify",
                            list(data.get("write_scope") or []), int(data.get("execution_timeout_seconds", 900)),
                        )
                        session = self.runtime.create_session(
                            project_id, str(data.get("request", ""))[:120], actor,
                            str(data.get("profile_id", "PROFILE-DEFAULT")), task["id"],
                        )
                        self.runtime.append(session["id"], "user/message", actor, {"request": data.get("request")})
                        self.runtime.append(session["id"], "task/created", "harness", {"task_id": task["id"], "status": task["status"]})
                        from .delivery_pipeline import pipeline_payload
                        self.runtime.append(
                            session["id"],
                            "pipeline/stage",
                            "harness",
                            {**pipeline_payload(str(task.get("status") or "queued")), "task_id": task["id"]},
                            source_key=f"pipeline-stage:created:{task['id']}",
                        )
                        return {**task, "session_id": session["id"]}
                    result = self.projects.idempotent("task-submit", key, data, submit_task)
                    return PlatformResponse(HTTPStatus.ACCEPTED, result)
            if path.startswith("/api/v1/tasks/"):
                parts = path.split("/")
                task_id = parts[4] if len(parts) > 4 else ""
                if len(parts) == 5 and method == "GET":
                    return PlatformResponse(200, self.tasks.get(task_id))
                if len(parts) == 6 and parts[5] == "review" and method == "POST":
                    actor, key = self._write_identity(headers)
                    boss = assert_boss_actor(actor)
                    payload = {**data, "task_id": task_id, "actor": boss}
                    result = self.projects.idempotent("task-review", key, payload, lambda: self.tasks.review(
                        task_id, boss, str(data.get("decision", "")), str(data.get("note", "")),
                    ))
                    return PlatformResponse(200, result)
                if len(parts) == 6 and parts[5] == "agent-critique" and method == "POST":
                    actor, key = self._write_identity(headers)
                    payload = {**data, "task_id": task_id, "actor": actor}
                    result = self.projects.idempotent(
                        "task-agent-critique", key, payload,
                        lambda: self._record_agent_critique(task_id, actor, data),
                    )
                    return PlatformResponse(200, result)
            if path == "/api/v1/feedback":
                if method == "GET": return PlatformResponse(200, feedback_summary(self.tasks.path))
                if method == "POST":
                    actor, key = self._write_identity(headers)
                    result = self.projects.idempotent("feedback-create", key, data, lambda: add_feedback(
                        str(data.get("task_id", "")), str(data.get("source", "")),
                        str(data.get("conclusion", "")), str(data.get("next_step", "")), self.tasks.path,
                    ))
                    result["recorded_by"] = actor
                    return PlatformResponse(HTTPStatus.CREATED, result)
            if path.startswith("/api/v1/feedback/"):
                parts = path.split("/"); feedback_id = parts[4] if len(parts) > 4 else ""
                if len(parts) == 6 and parts[5] == "review" and method == "POST":
                    actor, key = self._write_identity(headers)
                    payload = {**data, "feedback_id": feedback_id, "actor": actor}
                    result = self.projects.idempotent("feedback-review", key, payload, lambda: review_feedback(
                        feedback_id, actor, str(data.get("decision", "")),
                        str(data.get("note", "")), self.tasks.path,
                    ))
                    return PlatformResponse(200, result)
            if path == "/api/v1/evolutions":
                if method == "GET": return PlatformResponse(200, self.evolutions.summary(int(query.get("limit", "100"))))
                if method == "POST":
                    actor, key = self._write_identity(headers)
                    result = self.projects.idempotent("evolution-create", key, data, lambda: self.evolutions.create(
                        str(data.get("feedback_id", "")), str(data.get("failure_signature", "")),
                        str(data.get("classification", "")), list(data.get("business_refs") or []) or None, actor,
                    ))
                    return PlatformResponse(HTTPStatus.CREATED, result)
            if path.startswith("/api/v1/evolutions/"):
                parts = path.split("/"); evolution_id = parts[4] if len(parts) > 4 else ""
                if len(parts) == 5 and method == "GET": return PlatformResponse(200, self.evolutions.get(evolution_id))
                if len(parts) == 6 and method == "POST":
                    actor, key = self._write_identity(headers)
                    operation = parts[5]; payload = {**data, "evolution_id": evolution_id, "actor": actor}
                    if operation == "review": producer = lambda: self.evolutions.review(evolution_id, actor, str(data.get("decision", "")), str(data.get("note", "")))
                    elif operation == "assets": producer = lambda: self.evolutions.record_assets(evolution_id, list(data.get("asset_changes") or []), actor)
                    elif operation == "verify": producer = lambda: self.evolutions.verify(evolution_id, str(data.get("candidate_task_id", "")), str(data.get("blocking_report", "")), actor)
                    else: return PlatformResponse(404, {"error": "not_found"})
                    return PlatformResponse(200, self.projects.idempotent(f"evolution-{operation}", key, payload, producer))
            if path == "/api/v1/course/status" and method == "GET":
                return PlatformResponse(200, validate_mainline(self.repository_root))
            if path == "/api/v1/course/lessons" and method == "GET":
                return PlatformResponse(200, {"items": [item.as_dict() for item in LESSONS]})
            if path.startswith("/api/v1/course/lessons/") and method == "GET":
                return PlatformResponse(200, lesson_contract(int(path.rsplit("/", 1)[-1])).as_dict())
            return PlatformResponse(404, {"error": "not_found", "message": "Harness API 路径不存在"})
        except KeyError as exc:
            return PlatformResponse(404, {"error": "not_found", "message": str(exc)})
        except (TypeError, ValueError) as exc:
            return PlatformResponse(422, {"error": "invalid_request", "message": str(exc)})

    _BUSY_TASK_STATUSES = frozenset({
        "queued", "spec_ready", "executing", "evaluating", "rework",
    })

    def _assign_employees(self, session_id: str, actor: str, data: dict) -> dict:
        assert_boss_actor(actor)
        session = self.runtime.get_session(session_id)
        mapping = data.get("duty_by_stage") or data.get("overrides") or {}
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("需要 duty_by_stage 映射")
        cleaned = {}
        for stage, agent_id in mapping.items():
            employee = resolve(str(agent_id))
            cleaned[str(stage)] = employee.id
        coder = cleaned.get("code") or cleaned.get("eval") or "agent:coder"
        reviewer = cleaned.get("human") or "agent:reviewer"
        from .agent_roster import assert_coder_reviewer_sod
        assert_coder_reviewer_sod(coder, reviewer)
        event = self.runtime.append(
            session_id,
            "employees/assign",
            actor,
            {
                "duty_by_stage": cleaned,
                "employees": list_employees(),
                "note": "本会话值班覆盖（仍禁止 coder=reviewer）",
            },
        )
        return {
            "session_id": session_id,
            "duty_by_stage": cleaned,
            "event": event,
            "effective": {**{"request": "agent:spec", "spec": "agent:spec", "code": "agent:coder",
                             "eval": "agent:coder", "human": "agent:reviewer"}, **cleaned},
        }

    def _record_agent_critique(self, task_id: str, actor: str, data: dict) -> dict:
        task = self.tasks.get(task_id)
        reviewer_id = str(data.get("reviewer_id") or "agent:reviewer")
        # Allow boss to trigger critique, or the reviewer agent id itself.
        if actor != reviewer_id and not str(actor).startswith("agent:"):
            # human boss triggering is fine
            pass
        elif str(actor).startswith("agent:") and actor != reviewer_id:
            raise ValueError("只有质检员本人或老板可记录质检意见")
        critique = build_agent_critique(task, reviewer_id=reviewer_id)
        if data.get("note"):
            critique["note"] = str(data.get("note")).strip() or critique["note"]
        if data.get("suggested_decision") in {"approve", "reject"}:
            critique["suggested_decision"] = str(data.get("suggested_decision"))
        session = self.runtime.session_for_task(task_id)
        event = None
        if session:
            event = self.runtime.append(
                session["id"],
                "agent/critique",
                reviewer_id,
                {**critique, "task_id": task_id, "visible_to_model": True, "triggered_by": actor},
                source_key=f"agent-critique:{task_id}:{actor}",
            )
        return {"task_id": task_id, "critique": critique, "event": event, "session_id": (session or {}).get("id")}

    def _session_prompt(self, session_id: str, actor: str, data: dict) -> dict:
        """Append a user message and start (or continue) delivery inside one Session."""
        session = self.runtime.get_session(session_id)
        if session.get("status") == "closed":
            raise ValueError("已关闭 Session 不可发送；请 fork 新会话")
        if session.get("status") == "paused":
            raise ValueError("已暂停 Session 不可发送；请先恢复为 active")
        request = str(data.get("request", "")).strip()
        if not request:
            raise ValueError("prompt 需要非空 request")
        project_id = str(session.get("project_id") or "")
        self.projects.get(project_id)
        if session.get("task_id"):
            existing = self.tasks.get(session["task_id"])
            if existing.get("status") in self._BUSY_TASK_STATUSES:
                raise ValueError(f"Session 当前任务仍在进行：{existing['status']}")
        refs = [f"PROJECT:{project_id}", *list(data.get("business_refs") or [])]
        task = self.automation.submit(
            request,
            str(data.get("requirement_id", "")),
            refs,
            actor,
            "codex" if data.get("execute_code") else "verify",
            list(data.get("write_scope") or []),
            int(data.get("execution_timeout_seconds", 900)),
        )
        title = request[:120]
        if not session.get("task_id") or session.get("title") in {"", "新会话", "Untitled Session", "New Session"}:
            self.runtime.bind_task(session_id, task["id"], actor, title=title)
        else:
            self.runtime.bind_task(session_id, task["id"], actor)
        self.runtime.append(session_id, "user/message", actor, {"request": request})
        self.runtime.append(
            session_id,
            "task/created",
            "harness",
            {"task_id": task["id"], "status": task["status"]},
        )
        from .delivery_pipeline import pipeline_payload
        self.runtime.append(
            session_id,
            "pipeline/stage",
            "harness",
            {**pipeline_payload(str(task.get("status") or "queued")), "task_id": task["id"]},
            source_key=f"pipeline-stage:created:{task['id']}",
        )
        return {**task, "session_id": session_id}

    @staticmethod
    def _task_allowed_actions(task: dict) -> list[str]:
        allowed = ["read_spec", "read_workspace", "run_blocking_eval", "run_workspace_shell"]
        if task.get("execution_mode") == "codex":
            allowed.append("write_code_in_task_scope")
        return allowed

    @staticmethod
    def _write_identity(headers: dict[str, str]) -> tuple[str, str]:
        actor = headers.get("x-workbench-actor", "").strip()
        key = headers.get("idempotency-key", "").strip()
        if not actor:
            raise ValueError("写操作必须提供 X-Workbench-Actor 具名操作者")
        if not key:
            raise ValueError("写操作必须提供 Idempotency-Key")
        return actor, key
