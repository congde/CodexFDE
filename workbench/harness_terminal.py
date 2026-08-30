from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from .platform_api import HarnessPlatformAPI
from .platform_bootstrap import bootstrap_default_project, default_eval_command
from .session_context import derive_messages
from .session_watch import tail_session, verbose_event_formatter, default_event_formatter


class HarnessTerminal:
    """In-process terminal control plane for the personal delivery Harness."""

    def __init__(
        self,
        runtime_dir: str | Path = ".harness-runtime",
        repository_root: str | Path | None = None,
        actor: str = "terminal-operator",
    ) -> None:
        self.api = HarnessPlatformAPI(runtime_dir, repository_root)
        self.actor = actor.strip() or "terminal-operator"

    def bootstrap(self) -> dict:
        return bootstrap_default_project(self.api)

    def status(self) -> dict:
        composition = self.api.runtime.composition()
        capabilities = self.api.providers.capabilities()
        return {
            "product": "Harness Workbench",
            "interface": "terminal",
            "runtime_dir": str(self.api.runtime_dir),
            "repository_root": str(self.api.repository_root),
            "actor": self.actor,
            "projects": len(self.api.projects.list()),
            "tasks": len(self.api.tasks.list(1000)),
            "sessions": len(self.api.runtime.sessions(1000)),
            "tools": self.api.tools.list_tools(),
            "composition": composition,
            "capabilities": capabilities,
        }

    def list_projects(self) -> list[dict]:
        return self.api.projects.list()

    def list_tasks(self, limit: int = 20) -> list[dict]:
        return self.api.tasks.list(limit)

    def show_task(self, task_id: str) -> dict:
        return self.api.tasks.get(task_id)

    def show_session(self, session_id: str) -> dict:
        session = self.api.runtime.get_session(session_id)
        if session.get("task_id"):
            session = self.api.runtime.sync_task(session_id, self.api.tasks.get(session["task_id"]))
        return session

    def list_tools(self) -> list[dict]:
        return self.api.tools.list_tools()

    def agent_status(self, session_id: str) -> dict:
        return self.api.agent_loop.status(session_id)

    def session_graph(self, session_id: str) -> dict:
        from .session_graph import session_delivery_graph

        session = self.show_session(session_id)
        task = self.api.tasks.get(session["task_id"]) if session.get("task_id") else None
        return session_delivery_graph(session, task)

    def composition(self, profile_id: str = "PROFILE-DEFAULT") -> dict:
        return self.api.runtime.composition(profile_id)

    def dump_config(self, profile_id: str = "PROFILE-DEFAULT") -> dict:
        return self.api.runtime.dump_config(profile_id)

    def export_session(self, session_id: str, output: str | Path | None = None) -> dict:
        from .session_export import export_session_bundle, write_session_export

        session = self.show_session(session_id)
        task = None
        if session.get("task_id"):
            task = self.api.tasks.get(session["task_id"])
        bundle = export_session_bundle(
            self.api.runtime,
            session_id,
            task=task,
            repository_root=self.api.repository_root,
        )
        if output:
            path = write_session_export(bundle, output)
            return {"session_id": session_id, "export_path": str(path), "bundle": bundle}
        return {"session_id": session_id, "bundle": bundle}

    def list_profiles(self) -> list[dict]:
        return self.api.runtime.profiles()

    def list_plugins(self) -> list[dict]:
        return self.api.runtime.plugins()

    def activate_plugin(self, plugin_id: str, profile_id: str = "PROFILE-DEFAULT") -> dict:
        return self.api.runtime.activate_plugin(profile_id, plugin_id)

    def mcp_capabilities(self, profile_id: str = "PROFILE-DEFAULT") -> dict:
        return self.api.providers.mcp_provider(profile_id).capabilities()

    def mcp_list(self, profile_id: str = "PROFILE-DEFAULT") -> dict:
        provider = self.api.providers.mcp_provider(profile_id)
        caps = provider.capabilities()
        return {
            "provider": caps.get("provider"),
            "available": bool(caps.get("mcp_available")),
            "reason": caps.get("reason"),
            "tools": provider.list_tools(),
        }

    def mcp_call(
        self,
        name: str,
        arguments: dict | None = None,
        *,
        profile_id: str = "PROFILE-DEFAULT",
    ) -> dict:
        provider = self.api.providers.mcp_provider(profile_id)
        return {
            "provider": provider.capabilities().get("provider"),
            "name": name,
            "result": provider.call_tool(name, arguments or {}),
        }

    def derive_session_messages(self, session_id: str) -> list[dict]:
        session = self.show_session(session_id)
        return derive_messages(session)

    def headless(
        self,
        request: str,
        *,
        project_id: str = "PROJECT-FLOWERP",
        requirement_id: str = "",
        execute_code: bool = False,
        write_scope: list[str] | None = None,
        wait_timeout: float = 300.0,
        verbose: bool = False,
        profile_id: str = "PROFILE-HEADLESS",
    ) -> dict:
        """One-shot headless runner aligned with dsh --profile headless."""
        bootstrap = self.bootstrap()
        result = self.submit(
            request,
            project_id=project_id,
            requirement_id=requirement_id,
            execute_code=execute_code,
            write_scope=write_scope,
            wait=True,
            wait_timeout=wait_timeout,
            verbose=verbose,
            profile_id=profile_id,
        )
        session_id = result.get("session_id")
        messages = self.derive_session_messages(session_id) if session_id else []
        composition = self.composition(profile_id)
        final_answer = ""
        for message in reversed(messages):
            if message.get("role") == "assistant" and message.get("kind") == "assistant/message":
                final_answer = str(message.get("content") or "").strip()
                break
        if not final_answer and isinstance(result.get("result"), dict):
            summary = (result.get("result") or {}).get("summary") or {}
            final_answer = (
                f"delivery status={result.get('status')} "
                f"decision={summary.get('decision', '—')}"
            )
        return {
            "profile_id": profile_id,
            "profile": composition.get("profile", {}).get("name", profile_id),
            "bootstrap": bootstrap,
            "task": result,
            "session_id": session_id,
            "messages": messages,
            "final_answer": final_answer,
            "providers": {
                "llm": next((p["provider"] for p in composition.get("plugins", []) if p.get("seam") == "llm"), "—"),
                "eval": next((p["provider"] for p in composition.get("plugins", []) if p.get("seam") == "eval"), "—"),
                "execution": next((p["provider"] for p in composition.get("plugins", []) if p.get("seam") == "execution"), "—"),
            },
            "exit_code": 0 if result.get("status") == "review" else 1,
        }

    def submit(
        self,
        request: str,
        *,
        project_id: str = "PROJECT-FLOWERP",
        requirement_id: str = "",
        execute_code: bool = False,
        write_scope: list[str] | None = None,
        execution_timeout_seconds: int = 900,
        wait: bool = True,
        wait_timeout: float = 300.0,
        verbose: bool = False,
        profile_id: str = "PROFILE-DEFAULT",
    ) -> dict:
        self.api.projects.get(project_id)
        refs = [f"PROJECT:{project_id}"]
        task = self.api.automation.submit(
            request,
            requirement_id,
            refs,
            self.actor,
            "codex" if execute_code else "verify",
            write_scope or [],
            execution_timeout_seconds,
        )
        session = self.api.runtime.create_session(
            project_id,
            request[:120],
            self.actor,
            profile_id,
            task["id"],
        )
        session_id = session["id"]
        self.api.runtime.append(session_id, "user/message", self.actor, {"request": request})
        self.api.runtime.append(
            session_id,
            "task/created",
            "harness",
            {"task_id": task["id"], "status": task["status"]},
        )
        result = {**task, "session_id": session_id}
        if wait:
            if verbose:
                print(f"[session {session_id}] streaming events…", flush=True)
                stop = threading.Event()

                def _waiter() -> None:
                    try:
                        self.api.automation.wait(task["id"], wait_timeout)
                    finally:
                        stop.set()

                worker = threading.Thread(target=_waiter, name=f"wait-{task['id']}", daemon=True)
                worker.start()
                tail_session(
                    self.api.runtime,
                    session_id,
                    stop_when=stop.is_set,
                    formatter=verbose_event_formatter if verbose else default_event_formatter,
                )
                worker.join()
                result = self.api.tasks.get(task["id"])
            else:
                result = self.wait(task["id"], wait_timeout)
            result["session_id"] = session_id
        return result

    def wait(self, task_id: str, timeout: float = 300.0) -> dict:
        return self.api.automation.wait(task_id, timeout)

    def review(self, task_id: str, decision: str, note: str) -> dict:
        return self.api.tasks.review(task_id, self.actor, decision, note)

    def register_project(
        self,
        name: str,
        root_path: str | Path,
        *,
        project_id: str = "",
        eval_command: list[str] | None = None,
    ) -> dict:
        return self.api.projects.create(
            name,
            str(root_path),
            eval_command or default_eval_command(),
            project_id,
        )

    def invoke_tool(
        self,
        session_id: str,
        tool_id: str,
        task_id: str,
        args: dict | None = None,
    ) -> dict:
        task = self.api.tasks.get(task_id)
        context = {
            "task": task,
            "session_id": session_id,
            "actor": self.actor,
            "profile_id": self.api.runtime.get_session(session_id).get("profile_id", "PROFILE-DEFAULT"),
            "projects": self.api.projects,
            "allowed_actions": HarnessPlatformAPI._task_allowed_actions(task),
        }
        call_id = f"cli-{uuid.uuid4().hex[:12]}"
        return self.api.tools.invoke(
            tool_id,
            context,
            args or {},
            session_id=session_id,
            actor=self.actor,
            call_id=call_id,
        )

    def run_agent(self, session_id: str, task_id: str) -> dict:
        return self.api.agent_loop.run(session_id, task_id, self.actor)

    def watch_session(self, session_id: str, *, verbose: bool = False) -> None:
        from .session_watch import default_event_formatter, tail_session, verbose_event_formatter

        formatter = verbose_event_formatter if verbose else default_event_formatter
        print(f"watching session {session_id} … Ctrl+C 退出", flush=True)
        try:
            tail_session(self.api.runtime, session_id, formatter=formatter)
        except KeyboardInterrupt:
            print("\nwatch 结束")


def _short(text: str, width: int = 72) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= width else value[: width - 1] + "…"


def format_status(payload: dict) -> str:
    lines = [
        "Harness Workbench · TERMINAL CONTROL PLANE",
        f"runtime   : {payload['runtime_dir']}",
        f"repo      : {payload['repository_root']}",
        f"actor     : {payload['actor']}",
        f"projects  : {payload['projects']}   tasks: {payload['tasks']}   sessions: {payload['sessions']}",
        f"runtime   : {'ready' if payload['composition']['ready'] else 'incomplete'}",
        f"codex     : {payload['capabilities'].get('codex_available', False)}",
    ]
    providers = payload["capabilities"].get("providers")
    if providers:
        lines.append(
            "providers : "
            f"llm={providers.get('llm')} eval={providers.get('eval')} execution={providers.get('execution')}"
        )
    tools = ", ".join(item["id"] for item in payload.get("tools", []))
    if tools:
        lines.append(f"tools     : {tools}")
    return "\n".join(lines)


def format_task(task: dict) -> str:
    summary = (task.get("result") or {}).get("summary") or {}
    lines = [
        f"{task['id']}  status={task['status']}  mode={task.get('execution_mode', 'verify')}",
        f"request   : {_short(task.get('request', ''))}",
        f"requirement: {task.get('requirement_id') or '—'}",
    ]
    if summary:
        lines.append(
            f"eval      : decision={summary.get('decision', '—')} "
            f"blocking_failed={summary.get('blocking_failed', '—')}"
        )
    if task.get("error"):
        lines.append(f"error     : {task['error']}")
    events = task.get("events") or []
    if events:
        lines.append("events:")
        for event in events[-6:]:
            lines.append(
                f"  · {event.get('created_at', '')} {event.get('to_status', '')}: "
                f"{_short(event.get('detail', ''), 56)}"
            )
    return "\n".join(lines)


def format_session(session: dict) -> str:
    lines = [
        f"{session['id']}  status={session['status']}  task={session.get('task_id') or '—'}",
        f"title     : {_short(session.get('title', ''))}",
        f"project   : {session.get('project_id')}",
    ]
    events = session.get("events") or []
    if events:
        lines.append("event log:")
        for event in events[-12:]:
            kind = event.get("kind", "")
            actor = event.get("actor", "")
            detail = event.get("payload")
            if isinstance(detail, dict):
                if kind.startswith(("tool/", "tools/")):
                    detail_text = json.dumps(detail, ensure_ascii=False)
                elif kind.startswith(("agent/", "turn/", "step/")):
                    detail_text = json.dumps(detail, ensure_ascii=False)
                elif kind.startswith("task/"):
                    detail_text = detail.get("detail") or detail.get("to_status") or ""
                else:
                    detail_text = json.dumps(detail, ensure_ascii=False)
            else:
                detail_text = str(detail or "")
            lines.append(f"  [{event.get('sequence')}] {kind} @{actor} {_short(detail_text, 64)}")
    return "\n".join(lines)


def format_headless(result: dict) -> str:
    task = result.get("task") or {}
    lines = [
        "Harness headless run complete",
        f"profile   : {result.get('profile_id', result.get('profile', '—'))}",
        f"task      : {task.get('id', '—')}  status={task.get('status', '—')}",
        f"session   : {result.get('session_id', '—')}",
        f"exit_code : {result.get('exit_code', 1)}",
    ]
    providers = result.get("providers")
    if providers:
        lines.append(
            f"providers : llm={providers.get('llm')} eval={providers.get('eval')} execution={providers.get('execution')}"
        )
    messages = result.get("messages") or []
    if messages:
        lines.append("derived messages:")
        for index, message in enumerate(messages[-8:], start=max(1, len(messages) - 7)):
            role = message.get("role", "?")
            kind = message.get("kind", "")
            content = message.get("content") or message.get("tool_call") or message.get("summary") or message
            lines.append(f"  {index}. [{role}/{kind}] {_short(json.dumps(content, ensure_ascii=False), 72)}")
    return "\n".join(lines)


def format_composition(payload: dict) -> str:
    profile = payload.get("profile") or {}
    lines = [
        f"Profile {profile.get('id', '—')} · ready={payload.get('ready')}",
        f"seams     : {', '.join(payload.get('seams') or [])}",
    ]
    if payload.get("missing_seams"):
        lines.append(f"missing   : {', '.join(payload['missing_seams'])}")
    lines.append("plugins:")
    for plugin in payload.get("plugins") or []:
        lines.append(f"  · {plugin.get('seam')} → {plugin.get('provider')} ({plugin.get('id')})")
    return "\n".join(lines)


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    header_line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    separator = "  ".join("-" * widths[index] for index in range(len(headers)))
    body = [header_line, separator]
    for row in rows:
        body.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))
    return "\n".join(body)
