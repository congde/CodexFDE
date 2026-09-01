from __future__ import annotations

from dataclasses import dataclass

from .agent_planner import expand_plan_after_tool, plan_turn_tools
from .agent_roster import (
    build_agent_critique,
    duty_actor_for_status,
    duty_actor_for_tool,
    roster_overrides_from_events,
)
from .codex_events import SessionCodexStreamer
from .llm_adapter import create_llm_adapter
from .providers import HarnessProviders
from .runtime_store import HarnessRuntimeStore
from .session_graph import session_delivery_graph
from .system_prompt import run_pre_step
from .task_store import TaskStore
from .tool_registry import ToolRegistry
from .workflow import ControlledExecutionError, execute_task, prepare_task, start_task


def failure_signature(report: dict | None) -> tuple[str, ...]:
    if not isinstance(report, dict):
        return tuple()
    results = report.get("results") or []
    return tuple(sorted(
        str(item.get("name"))
        for item in results
        if not item.get("passed") and item.get("level") == "blocking"
    ))


@dataclass
class AgentLoopConfig:
    max_rounds: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.max_rounds <= 10:
            raise ValueError("max_rounds 必须在 1..10")


class AgentLoop:
    """Delivery driver aligned with dsh turn/step vocabulary.

    Official semantics (deepseek-harness glossary):
    - step = one model request + the tool executions it triggered
    - turn = zero or more steps that drain admitted input

    Course V0 uses a deterministic planner as the "model request" inside each
    step (llm seam emits assistant/chunk|message), then runs the planned tools
    inside the same step — never one step per tool.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        runtime: HarnessRuntimeStore,
        tasks: TaskStore,
        providers: HarnessProviders,
        config: AgentLoopConfig | None = None,
    ) -> None:
        self.registry = registry
        self.runtime = runtime
        self.tasks = tasks
        self.providers = providers
        self.config = config or AgentLoopConfig()

    def status(self, session_id: str) -> dict:
        session = self.runtime.get_session(session_id)
        agent_events = [
            event for event in session.get("events", [])
            if event["kind"].startswith(("agent/", "turn/", "step/", "assistant/"))
        ]
        latest_stop = next((event for event in reversed(agent_events) if event["kind"] == "agent/stopped"), None)
        latest_turn = next((event for event in reversed(agent_events) if event["kind"] == "turn/end"), None)
        latest_plan = next((event for event in reversed(agent_events) if event["kind"] == "agent/plan"), None)
        steps = [event for event in agent_events if event["kind"] == "step/end"]
        return {
            "session_id": session_id,
            "session_status": session.get("status"),
            "task_id": session.get("task_id"),
            "profile_id": session.get("profile_id"),
            "turns_completed": latest_turn["payload"].get("turn", 0) if latest_turn else 0,
            "steps_completed": len(steps),
            "stopped": latest_stop["payload"] if latest_stop else None,
            "latest_plan": latest_plan["payload"] if latest_plan else None,
            "providers": {
                "llm": self.providers.llm_provider(session.get("profile_id", "PROFILE-DEFAULT")),
            },
            "graph": session_delivery_graph(
                session,
                self.tasks.get(session["task_id"]) if session.get("task_id") else None,
            ),
            "events": agent_events,
        }

    def _context(self, task: dict, session_id: str, actor: str, profile_id: str) -> dict:
        allowed = ["read_spec", "read_workspace", "run_blocking_eval", "run_workspace_shell"]
        if task.get("execution_mode") == "codex":
            allowed.append("write_code_in_task_scope")
        return {
            "task": task,
            "session_id": session_id,
            "actor": actor,
            "profile_id": profile_id,
            "allowed_actions": allowed,
            "projects": self.providers.projects,
            "repository_root": self.providers.repository_root,
            "fs": self.providers.fs_provider(profile_id),
            "shell": self.providers.shell_provider(profile_id),
            "mcp": self.providers.mcp_provider(profile_id),
            "auto_approve": True,
            "approved_tools": ["*"],
        }

    def _roster_overrides(self, session: dict) -> dict[str, str]:
        return roster_overrides_from_events(session.get("events") or [])

    def _emit_agent_critique(self, session_id: str, task: dict, *, turn_no: int) -> None:
        if task.get("status") != "review":
            return
        critique = build_agent_critique(task)
        self.runtime.append(
            session_id,
            "agent/critique",
            critique["reviewer_id"],
            {
                **critique,
                "turn": turn_no,
                "task_id": task.get("id"),
                "visible_to_model": True,
            },
            source_key=f"turn:{turn_no}:agent-critique:{task.get('id')}",
        )

    def _emit_model_step(
        self,
        session_id: str,
        task: dict,
        *,
        turn_no: int,
        step_no: int,
        profile_id: str,
        prompt: dict | None,
        duty_actor: str,
    ) -> list[dict]:
        tool_plan = plan_turn_tools(task, turn=turn_no)
        configured_provider = self.providers.llm_provider(profile_id)
        # Verification-only tasks already have a deterministic tool plan and
        # must stay offline/reproducible. Only explicit Codex execution tasks
        # ask the configured model to produce a delivery plan.
        provider_name = configured_provider if task.get("execution_mode") == "codex" else "template"
        adapter = create_llm_adapter(self.runtime, provider_name, self.providers.repository_root)
        adapter.emit_delivery_plan(session_id, turn=turn_no, task=task, actor=duty_actor)
        self.runtime.append(
            session_id,
            "agent/plan",
            duty_actor,
            {"turn": turn_no, "step": step_no, "tools": tool_plan, "prompt": prompt},
            source_key=f"turn:{turn_no}:step:{step_no}:plan",
        )
        return tool_plan

    def _prepare_for_tool(self, task_id: str, actor: str, tool_id: str, profile_id: str) -> dict:
        task = self.tasks.get(task_id)
        if task["status"] == "queued" and tool_id == "spec.read":
            prepare_task(self.tasks, task_id, actor)
            task = self.tasks.get(task_id)
        if tool_id in {"codex.exec", "eval.blocking"} and task["status"] in {"spec_ready", "rework"}:
            start_task(self.tasks, task_id, actor)
            task = self.tasks.get(task_id)
        if tool_id == "eval.blocking" and task["status"] == "executing" and task.get("execution_mode") != "codex":
            runner = self.providers.execution_runner_for_task(task, profile_id)
            if runner:
                execute_task(self.tasks, task_id, actor, runner)
            task = self.tasks.get(task_id)
        return task

    def _invoke_planned_tool(
        self,
        tool_id: str,
        args: dict,
        *,
        task_id: str,
        session_id: str,
        actor: str,
        profile_id: str,
        turn_no: int,
        step_no: int,
        tool_index: int,
        overrides: dict[str, str] | None = None,
    ) -> tuple[dict, dict]:
        duty = duty_actor_for_tool(tool_id, overrides=overrides)
        task = self._prepare_for_tool(task_id, duty, tool_id, profile_id)
        context = self._context(task, session_id, duty, profile_id)
        if tool_id == "codex.exec":
            context["codex_streamer"] = SessionCodexStreamer(
                self.runtime, session_id, duty, turn=turn_no, provider="codex",
            )
        call_id = f"{task_id}-t{turn_no}-s{step_no}-c{tool_index}-{tool_id.replace('.', '-')}"
        result = self.registry.invoke(
            tool_id, context, args,
            session_id=session_id, actor=duty, call_id=call_id,
        )
        if tool_id == "codex.exec":
            streamer = context.get("codex_streamer")
            if isinstance(streamer, SessionCodexStreamer):
                streamer.finalize()
        return self.tasks.get(task_id), result if isinstance(result, dict) else {}

    def run_turn(self, session_id: str, task_id: str, actor: str, *, turn_no: int) -> dict:
        session = self.runtime.get_session(session_id)
        if session["status"] != "active":
            raise ValueError("Session 非 active，拒绝推进 Agent Loop")
        if session.get("task_id") and session["task_id"] != task_id:
            raise ValueError("Session 与 Task 关联不一致")
        profile_id = str(session.get("profile_id") or "PROFILE-DEFAULT")
        overrides = self._roster_overrides(session)
        boss = actor  # human boss who started the loop

        self.runtime.append(
            session_id,
            "turn/start",
            boss,
            {"turn": turn_no, "task_id": task_id, "profile_id": profile_id, "opc": True},
            source_key=f"turn:{turn_no}:start",
        )

        task = self.tasks.get(task_id)
        duty = duty_actor_for_status(task.get("status"), overrides=overrides)
        graph = session_delivery_graph(session, task)
        decision = run_pre_step(
            task=task,
            tools=self.registry.list_tools(),
            repository_root=str(self.providers.repository_root),
            graph=graph,
            session_id=session_id,
            roster_overrides=overrides,
        )
        self.runtime.append(
            session_id,
            "agent/pre-step",
            duty,
            {
                "turn": turn_no,
                "action": decision.action,
                "reason": decision.reason,
                "messages": decision.messages,
                "prompt": decision.prompt,
                "on_duty": duty,
            },
            source_key=f"turn:{turn_no}:pre-step",
        )

        if not decision.entered:
            self.runtime.append(
                session_id,
                "turn/end",
                duty,
                {"turn": turn_no, "reason": "pre_step_reject", "status": task["status"]},
                source_key=f"turn:{turn_no}:end",
            )
            return task

        for message in decision.messages:
            role = str(message.get("role") or "user")
            source = str(message.get("source") or "queued")
            if role == "system" or source == "delivery_pipeline":
                self.runtime.append(
                    session_id,
                    "pipeline/context",
                    duty,
                    {
                        "content": message.get("content"),
                        "source": source,
                        "turn": turn_no,
                        "visible_to_model": True,
                        "on_duty": duty,
                    },
                    source_key=f"turn:{turn_no}:pipeline-context",
                )
                continue
            self.runtime.append(
                session_id,
                "user/message",
                boss,
                {
                    "request": message.get("content"),
                    "source": source,
                    "turn": turn_no,
                },
                source_key=f"turn:{turn_no}:user:{source}",
            )

        step_no = 1
        self.runtime.append(
            session_id,
            "step/start",
            duty,
            {"turn": turn_no, "step": step_no, "on_duty": duty},
            source_key=f"turn:{turn_no}:step:{step_no}:start",
        )

        tool_plan = self._emit_model_step(
            session_id,
            task,
            turn_no=turn_no,
            step_no=step_no,
            profile_id=profile_id,
            prompt=decision.prompt,
            duty_actor=duty,
        )
        remaining = list(tool_plan)
        tool_index = 0
        tools_run: list[str] = []

        try:
            while remaining:
                item = remaining.pop(0)
                tool_index += 1
                tool_id = str(item["tool_id"])
                args = dict(item.get("args") or {})
                tools_run.append(tool_id)
                tool_duty = duty_actor_for_tool(tool_id, overrides=overrides)
                task, tool_result = self._invoke_planned_tool(
                    tool_id,
                    args,
                    task_id=task_id,
                    session_id=session_id,
                    actor=tool_duty,
                    profile_id=profile_id,
                    turn_no=turn_no,
                    step_no=step_no,
                    tool_index=tool_index,
                    overrides=overrides,
                )
                expanded = expand_plan_after_tool(remaining, tool_id=tool_id, result=tool_result)
                if expanded != remaining:
                    remaining = expanded
                    self.runtime.append(
                        session_id,
                        "agent/plan",
                        tool_duty,
                        {
                            "turn": turn_no,
                            "step": step_no,
                            "adaptive": True,
                            "after": tool_id,
                            "tools": remaining,
                        },
                        source_key=f"turn:{turn_no}:step:{step_no}:plan:adaptive:{tool_index}",
                    )
                if task["status"] in {"review", "completed", "failed", "dead_letter"}:
                    break
        except ControlledExecutionError as exc:
            task = self.tasks.get(task_id)
            if task["status"] == "executing":
                self.tasks.transition(
                    task_id, "rework", "受控执行失败，保留证据并等待有界重试", actor=duty_actor_for_tool("codex.exec"),
                    evidence={"error_type": type(exc).__name__, "execution": exc.evidence},
                    error=str(exc),
                )
                task = self.tasks.get(task_id)

        failures = failure_signature(task.get("result"))
        end_duty = duty_actor_for_status(task.get("status"), overrides=overrides)
        self.runtime.append(
            session_id,
            "step/end",
            end_duty,
            {
                "turn": turn_no,
                "step": step_no,
                "status": task["status"],
                "failures": list(failures),
                "tools": tools_run,
                "on_duty": end_duty,
            },
            source_key=f"turn:{turn_no}:step:{step_no}:end",
        )
        self.runtime.append(
            session_id,
            "turn/end",
            end_duty,
            {"turn": turn_no, "reason": "drained", "status": task["status"], "failures": list(failures)},
            source_key=f"turn:{turn_no}:end",
        )
        if task.get("status") == "review":
            self._emit_agent_critique(session_id, task, turn_no=turn_no)
        return task

    def run_round(self, session_id: str, task_id: str, actor: str, *, round_no: int) -> dict:
        return self.run_turn(session_id, task_id, actor, turn_no=round_no)

    def run(self, session_id: str, task_id: str, actor: str) -> dict:
        previous: tuple[str, ...] | None = None
        task: dict | None = None
        for turn_no in range(1, self.config.max_rounds + 1):
            task = self.run_turn(session_id, task_id, actor, turn_no=turn_no)
            failures = failure_signature(task.get("result"))

            if task["status"] == "review":
                self.runtime.append(
                    session_id,
                    "agent/stopped",
                    "agent:reviewer",
                    {"reason": "converged", "turns": turn_no, "awaiting": "boss_final_review"},
                    source_key=f"turn:{turn_no}:stopped",
                )
                break

            if task["status"] not in {"rework"}:
                self.runtime.append(
                    session_id,
                    "agent/stopped",
                    duty_actor_for_status(task.get("status")),
                    {"reason": "terminal_status", "turns": turn_no, "status": task["status"]},
                    source_key=f"turn:{turn_no}:stopped",
                )
                break

            if failures == previous:
                self.runtime.append(
                    session_id,
                    "agent/stopped",
                    "agent:coder",
                    {"reason": "no_progress", "turns": turn_no, "failures": list(failures)},
                    source_key=f"turn:{turn_no}:stopped",
                )
                break

            if task.get("execution_mode") != "codex":
                self.runtime.append(
                    session_id,
                    "agent/stopped",
                    "agent:coder",
                    {"reason": "verify_only_rework", "turns": turn_no, "failures": list(failures)},
                    source_key=f"turn:{turn_no}:stopped",
                )
                break

            previous = failures

        assert task is not None
        self.runtime.sync_task(session_id, task)
        return task
