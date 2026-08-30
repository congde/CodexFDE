from __future__ import annotations

import argparse
import cmd
import json
import shlex
import sys

from .harness_terminal import (
    HarnessTerminal,
    format_composition,
    format_headless,
    format_session,
    format_status,
    format_table,
    format_task,
)
from .platform_bootstrap import bootstrap_platform
from .session_graph import format_session_graph


def _emit(payload: object, *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif isinstance(payload, str):
        print(payload)
    elif isinstance(payload, dict) and payload.get("_render") == "status":
        print(format_status(payload))
    elif isinstance(payload, dict) and payload.get("_render") == "task":
        print(format_task(payload["task"]))
    elif isinstance(payload, dict) and payload.get("_render") == "composition":
        print(format_composition(payload["composition"]))
    elif isinstance(payload, dict) and payload.get("_render") == "headless":
        # dsh-headless style: final answer on stdout, status metadata on stderr
        answer = str(payload.get("final_answer") or "").strip()
        if answer:
            print(answer)
        print(format_headless(payload), file=sys.stderr)
    elif isinstance(payload, dict) and payload.get("_render") == "session":
        print(format_session(payload["session"]))
    elif isinstance(payload, dict) and payload.get("_render") == "graph":
        print(format_session_graph(payload["graph"]))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


class HarnessShell(cmd.Cmd):
    intro = "Harness Workbench · 终端控制面。输入 help 查看命令，quit 退出。"
    prompt = "harness> "

    def __init__(self, terminal: HarnessTerminal, json_mode: bool = False) -> None:
        super().__init__()
        self.terminal = terminal
        self.json_mode = json_mode

    def do_status(self, arg: str) -> None:
        """显示平台、项目、工具与运行时状态。"""
        payload = self.terminal.status()
        if self.json_mode:
            _emit(payload, json_mode=True)
        else:
            _emit(payload | {"_render": "status"}, json_mode=False)

    def do_bootstrap(self, arg: str) -> None:
        """注册当前仓库为默认目标项目 PROJECT-FLOWERP。"""
        _emit(self.terminal.bootstrap(), json_mode=self.json_mode)

    def do_projects(self, arg: str) -> None:
        """列出已注册目标项目。"""
        items = self.terminal.list_projects()
        if self.json_mode:
            _emit({"items": items}, json_mode=True)
            return
        if not items:
            print("尚无项目。运行 bootstrap 或 register。")
            return
        rows = [[item["id"], item["name"], item["root_path"]] for item in items]
        print(format_table(["ID", "NAME", "ROOT"], rows))

    def do_register(self, arg: str) -> None:
        """register <name> <root_path> [project_id]"""
        parts = shlex.split(arg)
        if len(parts) < 2:
            print("用法: register <name> <root_path> [project_id]")
            return
        name, root_path = parts[0], parts[1]
        project_id = parts[2] if len(parts) > 2 else ""
        result = self.terminal.register_project(name, root_path, project_id=project_id)
        _emit(result, json_mode=self.json_mode)

    def do_tasks(self, arg: str) -> None:
        """列出最近交付任务。"""
        limit = 20
        parts = shlex.split(arg)
        if parts:
            limit = int(parts[0])
        items = self.terminal.list_tasks(limit)
        if self.json_mode:
            _emit({"items": items}, json_mode=True)
            return
        if not items:
            print("暂无任务。")
            return
        rows = [
            [item["id"], item["status"], item.get("requirement_id") or "—", item.get("updated_at", "")]
            for item in items
        ]
        print(format_table(["TASK", "STATUS", "REQ", "UPDATED"], rows))

    def do_task(self, arg: str) -> None:
        """task <task_id>"""
        task_id = arg.strip()
        if not task_id:
            print("用法: task <task_id>")
            return
        task = self.terminal.show_task(task_id)
        if self.json_mode:
            _emit(task, json_mode=True)
        else:
            _emit({"_render": "task", "task": task}, json_mode=False)

    def do_composition(self, arg: str) -> None:
        """composition [profile_id] — 显示 Profile 插件组合。"""
        profile_id = arg.strip() or "PROFILE-DEFAULT"
        payload = self.terminal.composition(profile_id)
        if self.json_mode:
            _emit(payload, json_mode=True)
        else:
            _emit({"_render": "composition", "composition": payload}, json_mode=False)

    def do_dump_config(self, arg: str) -> None:
        """dump-config [profile_id] — 导出完整 Harness 配置。"""
        profile_id = arg.strip() or "PROFILE-DEFAULT"
        _emit(self.terminal.dump_config(profile_id), json_mode=self.json_mode)

    def do_profiles(self, arg: str) -> None:
        """列出可用 Profile。"""
        items = self.terminal.list_profiles()
        if self.json_mode:
            _emit({"items": items}, json_mode=True)
            return
        rows = [[item["id"], item["name"], "yes" if item.get("is_default") else "no"] for item in items]
        print(format_table(["ID", "NAME", "DEFAULT"], rows))

    def do_export(self, arg: str) -> None:
        """export <session_id> [output.json]"""
        parts = shlex.split(arg)
        if not parts:
            print("用法: export <session_id> [output.json]")
            return
        session_id = parts[0]
        output = parts[1] if len(parts) > 1 else None
        result = self.terminal.export_session(session_id, output)
        if self.json_mode:
            _emit(result if output else result.get("bundle", result), json_mode=True)
        elif output:
            print(f"exported: {result['export_path']}")
        else:
            print(json.dumps(result.get("bundle", result), ensure_ascii=False, indent=2))

    def do_watch(self, arg: str) -> None:
        """watch <session_id> [-v] — 实时 tail Session 事件。"""
        parts = shlex.split(arg)
        if not parts:
            print("用法: watch <session_id> [-v]")
            return
        session_id = parts[0]
        verbose = "-v" in parts or "--verbose" in parts
        self.terminal.watch_session(session_id, verbose=verbose)

    def do_derive(self, arg: str) -> None:
        """derive <session_id> — 从 Session 日志投影模型可见消息。"""
        session_id = arg.strip()
        if not session_id:
            print("用法: derive <session_id>")
            return
        messages = self.terminal.derive_session_messages(session_id)
        _emit({"session_id": session_id, "messages": messages}, json_mode=self.json_mode)

    def do_run(self, arg: str) -> None:
        """headless 一次性执行：run --req REQ-001 [--codex] <需求描述>"""
        self.do_submit(arg)

    def do_session(self, arg: str) -> None:
        """session <session_id>"""
        session_id = arg.strip()
        if not session_id:
            print("用法: session <session_id>")
            return
        session = self.terminal.show_session(session_id)
        if self.json_mode:
            _emit(session, json_mode=True)
        else:
            _emit({"_render": "session", "session": session}, json_mode=False)

    def do_plugins(self, arg: str) -> None:
        """列出全部插件（含可切换 Provider）。"""
        items = self.terminal.list_plugins()
        if self.json_mode:
            _emit({"items": items}, json_mode=True)
            return
        rows = [
            [item["id"], item["seam"], item["provider"], "yes" if item["enabled"] else "no"]
            for item in items
        ]
        print(format_table(["ID", "SEAM", "PROVIDER", "ENABLED"], rows))

    def do_activate(self, arg: str) -> None:
        """activate <plugin_id> — 切换 Profile 中某个 seam 的 Provider。"""
        plugin_id = arg.strip()
        if not plugin_id:
            print("用法: activate <plugin_id>  例如 activate eval.local")
            return
        payload = self.terminal.activate_plugin(plugin_id)
        if self.json_mode:
            _emit(payload, json_mode=True)
        else:
            _emit({"_render": "composition", "composition": payload}, json_mode=False)

    def do_mcp(self, arg: str) -> None:
        """mcp | mcp list | mcp call <name> ['{\"k\":1}'] — 经 MCP seam list/call。"""
        parts = shlex.split(arg)
        if not parts or parts[0] == "status":
            _emit(self.terminal.mcp_capabilities(), json_mode=self.json_mode)
            return
        action = parts[0]
        if action == "list":
            _emit(self.terminal.mcp_list(), json_mode=self.json_mode)
            return
        if action == "call" and len(parts) >= 2:
            arguments = json.loads(parts[2]) if len(parts) >= 3 else {}
            _emit(self.terminal.mcp_call(parts[1], arguments), json_mode=self.json_mode)
            return
        print("用法: mcp | mcp list | mcp call <name> ['{\"k\":1}']")

    def do_tools(self, arg: str) -> None:
        """列出 Tool Registry 中的内置工具。"""
        items = self.terminal.list_tools()
        if self.json_mode:
            _emit({"items": items}, json_mode=True)
            return
        rows = [[item["id"], ", ".join(item["permissions"]), item["description"]] for item in items]
        print(format_table(["TOOL", "PERMISSIONS", "DESCRIPTION"], rows))

    def do_agent(self, arg: str) -> None:
        """agent <session_id>"""
        session_id = arg.strip()
        if not session_id:
            print("用法: agent <session_id>")
            return
        _emit(self.terminal.agent_status(session_id), json_mode=self.json_mode)

    def do_graph(self, arg: str) -> None:
        """graph <session_id> — 投影交付状态图。"""
        session_id = arg.strip()
        if not session_id:
            print("用法: graph <session_id>")
            return
        graph = self.terminal.session_graph(session_id)
        if self.json_mode:
            _emit(graph, json_mode=True)
        else:
            _emit({"_render": "graph", "graph": graph}, json_mode=False)

    def do_submit(self, arg: str) -> None:
        """submit --req REQ-001 --scope flowerp,tests [--codex] <需求描述>"""
        parts = shlex.split(arg)
        requirement_id = ""
        write_scope: list[str] = []
        execute_code = False
        verbose = False
        positional: list[str] = []
        index = 0
        while index < len(parts):
            token = parts[index]
            if token == "--req" and index + 1 < len(parts):
                requirement_id = parts[index + 1]
                index += 2
                continue
            if token == "--scope" and index + 1 < len(parts):
                write_scope = [part.strip() for part in parts[index + 1].split(",") if part.strip()]
                index += 2
                continue
            if token == "--codex":
                execute_code = True
                index += 1
                continue
            if token in {"--verbose", "-v"}:
                verbose = True
                index += 1
                continue
            positional.append(token)
            index += 1
        request = " ".join(positional).strip()
        if not request:
            print("用法: submit --req REQ-001 [--scope flowerp,tests] [--codex] [-v] <需求描述>")
            return
        print(f"提交任务 actor={self.terminal.actor} mode={'codex' if execute_code else 'verify'} …")
        result = self.terminal.submit(
            request,
            requirement_id=requirement_id,
            execute_code=execute_code,
            write_scope=write_scope,
            verbose=verbose,
        )
        if self.json_mode:
            _emit(result, json_mode=True)
        else:
            print(format_task(result))
            print(f"session_id: {result.get('session_id', '—')}")

    def do_wait(self, arg: str) -> None:
        """wait <task_id> [timeout_seconds]"""
        parts = shlex.split(arg)
        if not parts:
            print("用法: wait <task_id> [timeout_seconds]")
            return
        timeout = float(parts[1]) if len(parts) > 1 else 300.0
        result = self.terminal.wait(parts[0], timeout)
        if self.json_mode:
            _emit(result, json_mode=True)
        else:
            print(format_task(result))

    def do_review(self, arg: str) -> None:
        """review <task_id> approve|reject <note>"""
        parts = shlex.split(arg)
        if len(parts) < 3:
            print("用法: review <task_id> approve|reject <note>")
            return
        task_id, decision = parts[0], parts[1]
        note = " ".join(parts[2:])
        result = self.terminal.review(task_id, decision, note)
        _emit(result, json_mode=self.json_mode)

    def do_actor(self, arg: str) -> None:
        """actor <name> — 设置具名操作者。"""
        name = arg.strip()
        if not name:
            print(f"当前 actor: {self.terminal.actor}")
            return
        self.terminal.actor = name
        print(f"actor 已设置为 {name}")

    def do_quit(self, arg: str) -> bool:
        """退出终端工作台。"""
        print("再见。")
        return True

    do_exit = do_quit
    do_EOF = do_quit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Harness Workbench — 终端控制面（DeepSeek Harness 风格，Python 标准库实现）",
    )
    parser.add_argument("--runtime-dir", default=".harness-runtime")
    parser.add_argument("--repository-root")
    parser.add_argument("--actor", default="terminal-operator", help="具名操作者，写入 Session 与审核记录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是人类可读文本")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("repl", help="进入交互式终端（默认）")
    sub.add_parser("bootstrap", help="注册当前仓库为 PROJECT-FLOWERP")
    sub.add_parser("status", help="显示平台状态")

    composition_cmd = sub.add_parser("composition", help="显示 Profile 插件组合（对标 dsh --dump-config）")
    composition_cmd.add_argument("--profile", default="PROFILE-DEFAULT")
    dump_config_cmd = sub.add_parser("dump-config", help="导出完整 Harness 配置（对标 dsh --dump-config）")
    dump_config_cmd.add_argument("--profile", default="PROFILE-DEFAULT")

    export_cmd = sub.add_parser("export", help="导出 Session 证据包（JSON）")
    export_cmd.add_argument("session_id")
    export_cmd.add_argument("--output", "-o", help="写入文件路径；省略则输出到 stdout")

    profiles_cmd = sub.add_parser("profiles", help="列出可用 Profile")

    run_cmd = sub.add_parser("run", help="headless 一次性执行并退出（对标 dsh --profile headless）")
    run_cmd.add_argument("--request", required=True)
    run_cmd.add_argument("--requirement-id", default="")
    run_cmd.add_argument("--project-id", default="PROJECT-FLOWERP")
    run_cmd.add_argument("--execute-code", action="store_true")
    run_cmd.add_argument("--write-scope", default="flowerp,tests")
    run_cmd.add_argument("--timeout", type=float, default=300.0)
    run_cmd.add_argument("--profile", default="PROFILE-HEADLESS", help="Profile ID（默认 headless）")
    run_cmd.add_argument("--verbose", "-v", action="store_true")

    derive_cmd = sub.add_parser("derive", help="从 Session 事件日志投影模型可见消息")
    derive_cmd.add_argument("session_id")

    watch_cmd = sub.add_parser("watch", help="实时 tail Session 事件（对标 dsh 流式输出）")
    watch_cmd.add_argument("session_id")
    watch_cmd.add_argument("--verbose", "-v", action="store_true")

    submit_cmd = sub.add_parser("submit", help="提交交付任务并等待 Agent Loop 完成")
    submit_cmd.add_argument("--request", required=True)
    submit_cmd.add_argument("--requirement-id", default="")
    submit_cmd.add_argument("--project-id", default="PROJECT-FLOWERP")
    submit_cmd.add_argument("--execute-code", action="store_true")
    submit_cmd.add_argument("--write-scope", default="flowerp,tests")
    submit_cmd.add_argument("--no-wait", action="store_true")
    submit_cmd.add_argument("--timeout", type=float, default=300.0)
    submit_cmd.add_argument("--profile", default="PROFILE-DEFAULT")
    submit_cmd.add_argument("--verbose", "-v", action="store_true")

    task_show = sub.add_parser("task-show", help="查看任务详情")
    task_show.add_argument("task_id")

    session_show = sub.add_parser("session-show", help="查看 Session 事件链")
    session_show.add_argument("session_id")

    sub.add_parser("tasks", help="列出任务")
    sub.add_parser("projects", help="列出项目")
    sub.add_parser("tools", help="列出 Tool Registry")

    sub.add_parser("plugins", help="列出全部插件")
    activate_cmd = sub.add_parser("activate", help="切换 Profile 中某个 seam 的 Provider")
    activate_cmd.add_argument("plugin_id")

    mcp_cmd = sub.add_parser("mcp", help="经 MCP seam 列出或调用工具（对标独立 mcp provider）")
    mcp_sub = mcp_cmd.add_subparsers(dest="mcp_action")
    mcp_sub.add_parser("list", help="列出 MCP Provider 工具")
    mcp_call = mcp_sub.add_parser("call", help="调用 MCP 工具")
    mcp_call.add_argument("name")
    mcp_call.add_argument("--args", default="{}", help="JSON 参数对象")
    mcp_sub.add_parser("status", help="查看 MCP Provider 能力")

    agent_cmd = sub.add_parser("agent-status", help="查看 Session 上的 Agent Loop 状态")
    agent_cmd.add_argument("session_id")

    graph_cmd = sub.add_parser("graph", help="投影 Session 交付状态图")
    graph_cmd.add_argument("session_id")

    review_cmd = sub.add_parser("review", help="具名审核任务")
    review_cmd.add_argument("task_id")
    review_cmd.add_argument("--decision", choices=("approve", "reject"), required=True)
    review_cmd.add_argument("--note", required=True)

    serve_cmd = sub.add_parser("serve-web", help="可选：启动 legacy Web 面板（8010）")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8010)
    serve_cmd.add_argument("--bootstrap", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "repl"

    if command == "serve-web":
        from .platform_server import serve

        serve(args.host, args.port, args.runtime_dir, args.repository_root, args.bootstrap)
        return 0

    terminal = HarnessTerminal(args.runtime_dir, args.repository_root, args.actor)

    if command == "repl":
        bootstrap = terminal.bootstrap()
        if not args.json:
            action = bootstrap.get("action", "exists")
            project = bootstrap.get("project", {})
            print(f"bootstrap: {action} · {project.get('id', '—')} · {project.get('root_path', '—')}")
        shell = HarnessShell(terminal, json_mode=args.json)
        try:
            shell.cmdloop()
        except KeyboardInterrupt:
            print("\n再见。")
        return 0

    if command == "bootstrap":
        payload = bootstrap_platform(args.runtime_dir, args.repository_root)
        payload["interface"] = "terminal"
        _emit(payload, json_mode=args.json)
        return 0

    if command == "status":
        payload = terminal.status()
        if args.json:
            _emit(payload, json_mode=True)
        else:
            _emit(payload | {"_render": "status"}, json_mode=False)
        return 0

    if command == "composition":
        profile_id = getattr(args, "profile", "PROFILE-DEFAULT")
        payload = terminal.composition(profile_id)
        if args.json:
            _emit(payload, json_mode=True)
        else:
            _emit({"_render": "composition", "composition": payload}, json_mode=False)
        return 0 if payload.get("ready") else 1

    if command == "dump-config":
        payload = terminal.dump_config(getattr(args, "profile", "PROFILE-DEFAULT"))
        _emit(payload, json_mode=args.json)
        return 0 if payload.get("composition", {}).get("ready") else 1

    if command == "profiles":
        _emit({"items": terminal.list_profiles()}, json_mode=args.json)
        return 0

    if command == "export":
        result = terminal.export_session(args.session_id, getattr(args, "output", None))
        if getattr(args, "output", None):
            if args.json:
                _emit({"session_id": result["session_id"], "export_path": result["export_path"]}, json_mode=True)
            else:
                print(f"exported: {result['export_path']}")
        else:
            _emit(result.get("bundle", result), json_mode=args.json)
        return 0

    if command == "derive":
        messages = terminal.derive_session_messages(args.session_id)
        _emit({"session_id": args.session_id, "messages": messages}, json_mode=args.json)
        return 0

    if command == "watch":
        terminal.watch_session(args.session_id, verbose=args.verbose)
        return 0

    if command == "run":
        scopes = [part.strip() for part in args.write_scope.split(",") if part.strip()]
        result = terminal.headless(
            args.request,
            project_id=args.project_id,
            requirement_id=args.requirement_id,
            execute_code=args.execute_code,
            write_scope=scopes,
            wait_timeout=args.timeout,
            verbose=args.verbose,
            profile_id=args.profile,
        )
        if args.json:
            _emit(result, json_mode=True)
        else:
            _emit({"_render": "headless", **result}, json_mode=False)
        return int(result.get("exit_code", 1))

    if command == "plugins":
        _emit({"items": terminal.list_plugins()}, json_mode=args.json)
        return 0

    if command == "mcp":
        action = getattr(args, "mcp_action", None) or "status"
        if action == "list":
            _emit(terminal.mcp_list(), json_mode=args.json)
            return 0
        if action == "call":
            arguments = json.loads(getattr(args, "args", "{}") or "{}")
            if not isinstance(arguments, dict):
                raise SystemExit("--args 必须是 JSON 对象")
            _emit(terminal.mcp_call(args.name, arguments), json_mode=True)
            return 0
        _emit(terminal.mcp_capabilities(), json_mode=args.json)
        return 0

    if command == "activate":
        payload = terminal.activate_plugin(args.plugin_id)
        if args.json:
            _emit(payload, json_mode=True)
        else:
            _emit({"_render": "composition", "composition": payload}, json_mode=False)
        return 0 if payload.get("ready") else 1

    if command == "projects":
        _emit({"items": terminal.list_projects()}, json_mode=args.json)
        return 0

    if command == "tasks":
        _emit({"items": terminal.list_tasks()}, json_mode=args.json)
        return 0

    if command == "tools":
        _emit({"items": terminal.list_tools()}, json_mode=args.json)
        return 0

    if command == "task-show":
        task = terminal.show_task(args.task_id)
        if args.json:
            _emit(task, json_mode=True)
        else:
            _emit({"_render": "task", "task": task}, json_mode=False)
        return 0 if task.get("status") not in {"failed", "dead_letter"} else 1

    if command == "session-show":
        session = terminal.show_session(args.session_id)
        if args.json:
            _emit(session, json_mode=True)
        else:
            _emit({"_render": "session", "session": session}, json_mode=False)
        return 0

    if command == "agent-status":
        _emit(terminal.agent_status(args.session_id), json_mode=args.json)
        return 0

    if command == "graph":
        graph = terminal.session_graph(args.session_id)
        if args.json:
            _emit(graph, json_mode=True)
        else:
            _emit({"_render": "graph", "graph": graph}, json_mode=False)
        return 0

    if command == "submit":
        scopes = [part.strip() for part in args.write_scope.split(",") if part.strip()]
        result = terminal.submit(
            args.request,
            project_id=args.project_id,
            requirement_id=args.requirement_id,
            execute_code=args.execute_code,
            write_scope=scopes,
            wait=not args.no_wait,
            wait_timeout=args.timeout,
            verbose=args.verbose,
            profile_id=args.profile,
        )
        if args.json:
            _emit(result, json_mode=True)
        else:
            print(format_task(result))
            if result.get("session_id"):
                print(f"session_id: {result['session_id']}")
        return 0 if result.get("status") == "review" else 1

    if command == "review":
        result = terminal.review(args.task_id, args.decision, args.note)
        _emit(result, json_mode=args.json)
        return 0 if result.get("status") == "completed" else 1

    parser.error(f"未知命令: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
