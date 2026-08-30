from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..codex_events import SessionCodexStreamer
from ..mcp_provider import mcp_list_handler, mcp_tool_handler
from ..providers import HarnessProviders
from ..spec import load_spec
from ..task_store import TaskStore
from ..tool_registry import ToolRegistry, ToolSpec
from ..tools.workspace import exec_workspace_shell, grep_workspace, list_workspace, read_workspace_file
from ..workflow import evaluate_task, execute_task


def _mcp_call_handler(context: dict, args: dict) -> dict:
    return mcp_tool_handler(context, args)


def _mcp_list_handler(context: dict, args: dict) -> dict:
    return mcp_list_handler(context, args)


def _read_spec_handler(context: dict, _args: dict) -> dict:
    task = context["task"]
    spec_path = Path(str(task.get("spec_path") or "FDE_SPEC.md")).resolve()
    spec = load_spec(spec_path)
    return {"spec_path": str(spec_path), "spec": spec.as_dict()}


def _make_eval_handler(store: TaskStore, providers: HarnessProviders) -> Callable[[dict, dict], dict]:
    def handler(context: dict, _args: dict) -> dict:
        task = context["task"]
        actor = str(context.get("actor") or "automation")
        profile_id = str(context.get("profile_id") or "PROFILE-DEFAULT")
        suite_runner = providers.eval_runner_for_task(task, profile_id)
        result = evaluate_task(store, task["id"], actor, suite_runner)
        context["task"] = store.get(task["id"])
        return result

    return handler


def _make_codex_handler(store: TaskStore, providers: HarnessProviders) -> Callable[[dict, dict], dict]:
    def handler(context: dict, _args: dict) -> dict:
        task = context["task"]
        actor = str(context.get("actor") or "automation")
        profile_id = str(context.get("profile_id") or "PROFILE-DEFAULT")
        execution_runner = providers.execution_runner_for_task(task, profile_id)
        streamer = context.get("codex_streamer")

        def on_codex_line(line: str) -> None:
            if isinstance(streamer, SessionCodexStreamer):
                streamer.ingest_line(line)

        def runner(task_payload: dict) -> dict:
            try:
                return execution_runner(task_payload, on_codex_line=on_codex_line)
            except TypeError:
                return execution_runner(task_payload)

        result = execute_task(store, task["id"], actor, runner)
        if isinstance(streamer, SessionCodexStreamer):
            evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else result
            if isinstance(evidence, dict):
                streamer.ingest_execution_evidence(evidence)
            streamer.finalize()
        context["task"] = store.get(task["id"])
        return result

    return handler


def register_default_tools(
    registry: ToolRegistry,
    store: TaskStore,
    providers: HarnessProviders,
) -> None:
    registry.register(ToolSpec(
        id="spec.read",
        name="Read Delivery Spec",
        description="读取任务绑定的结构化 Spec Markdown",
        permissions=frozenset({"read_spec"}),
        handler=_read_spec_handler,
    ))
    registry.register(ToolSpec(
        id="workspace.read",
        name="Read Workspace File",
        description="读取目标项目工作区内的 UTF-8 文本文件",
        permissions=frozenset({"read_workspace"}),
        handler=read_workspace_file,
    ))
    registry.register(ToolSpec(
        id="workspace.list",
        name="List Workspace",
        description="列出目标项目工作区目录条目",
        permissions=frozenset({"read_workspace"}),
        handler=list_workspace,
    ))
    registry.register(ToolSpec(
        id="workspace.grep",
        name="Grep Workspace",
        description="在目标项目工作区内按正则搜索文本",
        permissions=frozenset({"read_workspace"}),
        handler=grep_workspace,
    ))
    registry.register(ToolSpec(
        id="shell.exec",
        name="Workspace Shell",
        description="在项目工作区运行受控只读/验证命令（unittest、eval.harness、git）",
        permissions=frozenset({"run_workspace_shell"}),
        handler=exec_workspace_shell,
        approval="ask",
    ))
    registry.register(ToolSpec(
        id="eval.blocking",
        name="Blocking Eval",
        description="运行 Profile 选中的 blocking Eval Provider",
        permissions=frozenset({"run_blocking_eval"}),
        handler=_make_eval_handler(store, providers),
    ))
    registry.register(ToolSpec(
        id="codex.exec",
        name="Codex Executor",
        description="运行 Profile 选中的 Execution Provider，并将 Codex 轨迹投影到 Session",
        permissions=frozenset({"write_code_in_task_scope"}),
        handler=_make_codex_handler(store, providers),
        approval="ask",
    ))
    registry.register(ToolSpec(
        id="mcp.list",
        name="MCP Tool List",
        description="列出当前 Profile 选中的 MCP Provider 工具（seam=mcp）",
        permissions=frozenset({"call_mcp"}),
        handler=_mcp_list_handler,
    ))
    registry.register(ToolSpec(
        id="mcp.call",
        name="MCP Tool Call",
        description="通过当前 MCP Provider 调用外部工具（off/http/manifest）",
        permissions=frozenset({"call_mcp"}),
        handler=_mcp_call_handler,
        approval="ask",
    ))
