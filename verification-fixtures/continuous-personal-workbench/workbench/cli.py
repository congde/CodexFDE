"""Keep the same command entry point as the course project."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from .bootstrap import BootstrapLedger, LedgerError, failure_result
from .runtime_paths import service_runtime


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LedgerError("invalid_arguments", message)


def _bootstrap_commands(commands) -> None:
    command = commands.add_parser("workbench-init", help="初始化工作台账本")
    command.add_argument("--runtime-dir")
    command.add_argument("--owner", required=True)
    command.add_argument("--name", default="个人 AI 研发工作台")
    command = commands.add_parser("workbench-project-add", help="登记项目")
    command.add_argument("--runtime-dir")
    command.add_argument("--project-id", required=True)
    command.add_argument("--name", required=True)
    command.add_argument("--path", default=".")
    command.add_argument("--purpose", default="")
    command = commands.add_parser("workbench-task-create", help="创建任务并保存需求和问题快照")
    command.add_argument("--runtime-dir")
    command.add_argument("--project-id", required=True)
    command.add_argument("--task-id", required=True)
    command.add_argument("--title", "--request", dest="title", required=True)
    command.add_argument("--spec-file", required=True)
    command.add_argument("--problem-file")
    command.add_argument("--actor", default="")
    command.add_argument("--requirement-id", default="")
    command = commands.add_parser("workbench-evidence-add", help="追加真实输出快照")
    command.add_argument("--runtime-dir")
    command.add_argument("--task-id", required=True)
    command.add_argument("--phase", choices=("red", "diff", "green", "observation"), required=True)
    command.add_argument("--command-text", required=True)
    command.add_argument("--output-file", required=True)
    command.add_argument("--returncode", type=int, required=True)
    command.add_argument("--observed-at", required=True)
    command = commands.add_parser("workbench-status", help="只读查询账本和证据完整性")
    command.add_argument("--runtime-dir")
    command.add_argument("--require-project", action="append", default=[])
    command.add_argument("--require-task", action="append", default=[])
    command.add_argument("--require-red-green-evidence", action="store_true")


def _l04_commands(commands):
    command = commands.add_parser('task-create', help='绑定六段 Spec 创建独立 L04 任务')
    command.add_argument('--runtime-dir')
    command.add_argument('--requirement-id', required=True)
    command.add_argument('--request', required=True)
    command.add_argument('--spec-path', required=True)
    command.add_argument('--actor', required=True)
    command.add_argument('--workspace-dir', '--workspace', dest='workspace_dir')
    command.add_argument('--execute-code', action='store_true', default=None)
    command.add_argument('--allowed-write-file', '--allowed-file', '--write-scope', dest='allowed_write_files', action='append')
    command.add_argument('--execution-timeout', type=float)
    command.add_argument('--source-task-id')
    command.add_argument('--bootstrap-task-id')
    command.add_argument('--fixture', action='store_true')
    command = commands.add_parser('task-run', help='默认仅复验；明确授权后执行隔离候选')
    command.add_argument('task_id')
    command.add_argument('--runtime-dir')
    command.add_argument('--actor', required=True)
    command.add_argument('--execute-code', action='store_true', default=None)
    command.add_argument('--workspace-dir')
    command.add_argument('--allowed-write-file', '--allowed-file', dest='allowed_write_files', action='append')
    command.add_argument('--execution-timeout', type=float)
    command = commands.add_parser('task-show', help='只读返回任务与事件')
    command.add_argument('task_id')
    command.add_argument('--runtime-dir')
    command = commands.add_parser('task-review', help='记录真实非执行者的批准或打回')
    command.add_argument('task_id')
    command.add_argument('--runtime-dir')
    command.add_argument('--reviewer', required=True)
    command.add_argument('--decision', choices=('approve', 'reject'), required=True)
    command.add_argument('--note', required=True)
    command.add_argument('--fixture', action='store_true')
    command = commands.add_parser('course-submit', help='仅 L04：通过有效 A 门闩后组织隔离 B')
    command.add_argument('--lesson', type=int, required=True)
    command.add_argument('--execute-code', action='store_true', required=True)
    command.add_argument('--bootstrap-task-id')
    command.add_argument('--requirement-spec')
    command.add_argument('--session-baseline-ref')
    command.add_argument('--allowed-file', dest='allowed_files', action='append', default=[])
    command.add_argument('--fixture', action='store_true')
    command.add_argument('--actor', required=True)
    command.add_argument('--runtime-dir')
    command.add_argument('--execution-timeout', type=float, default=900)


def _continuity_commands(commands):
    for name in ('course-contract', 'course-spec', 'course-prepare', 'course-status'):
        command = commands.add_parser(name)
        command.add_argument('--runtime-dir')
        if name != 'course-status':
            command.add_argument('--lesson', type=int, required=True)
        if name == 'course-spec':
            command.add_argument('--output', required=True)
        if name == 'course-prepare':
            command.add_argument('--task-id', required=True)
            command.add_argument('--source', choices=('working-tree',), default='working-tree')
        if name == 'course-status':
            command.add_argument('--require-baselines', action='store_true')
    command = commands.add_parser('initiative-create')
    command.add_argument('--runtime-dir')
    for name in ('actor', 'requirement', 'scope'):
        command.add_argument('--' + name, required=True)
    command.add_argument('--evidence', default='')
    for name in ('initiative-show', 'initiative-link'):
        command = commands.add_parser(name)
        command.add_argument('initiative_id')
        command.add_argument('--runtime-dir')
        if name == 'initiative-link':
            command.add_argument('--task-id', required=True)


def _l04_dispatch(args):
    from .task_store import TaskStore
    store = TaskStore(service_runtime('workbench', args.runtime_dir))
    arguments = vars(args).copy()
    name = arguments.pop('command')
    arguments.pop('runtime_dir')
    if name == 'task-run':
        from .workflow import run_task
        return run_task(store, **arguments)
    if name == 'course-submit':
        lesson = arguments.pop('lesson')
        arguments.pop('execute_code')
        if lesson == 4:
            from .course_delivery import submit_l04
            arguments.pop('session_baseline_ref')
            arguments.pop('allowed_files')
            arguments.pop('fixture')
            if not arguments['bootstrap_task_id'] or not arguments['requirement_spec']:
                raise LedgerError('invalid_arguments', 'L04 必须声明 bootstrap-task-id 和 requirement-spec')
            return submit_l04(store, **arguments)
        from .course_continuity import CourseContinuity
        if arguments.pop('requirement_spec') is not None:
            raise LedgerError('invalid_arguments', '后续接续采用 course-spec 快照，不接收 L04 requirement-spec')
        return CourseContinuity(store).submit(lesson=lesson, **arguments)
    return getattr(store, {'task-create': 'create', 'task-show': 'get', 'task-review': 'review'}[name])(**arguments)


def _continuity_dispatch(args):
    from .task_store import TaskStore
    runtime = service_runtime('workbench', args.runtime_dir)
    arguments = vars(args).copy()
    name = arguments.pop('command')
    arguments.pop('runtime_dir')
    if name.startswith('initiative-'):
        from .initiatives import InitiativeLedger
        operation = {'initiative-create': 'create', 'initiative-show': 'get', 'initiative-link': 'link'}[name]
        return getattr(InitiativeLedger(runtime), operation)(**arguments)
    from .course_continuity import CourseContinuity
    operation = name.removeprefix('course-')
    return getattr(CourseContinuity(TaskStore(runtime)), operation)(**arguments)


def main(argv=None) -> int:
    parser = _Parser(description="个人 AI 研发工作台")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("serve-workbench", help="启动工作台页面壳（默认 :8001）")
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=8001)
    command.add_argument("--runtime-dir")
    _bootstrap_commands(commands)
    _l04_commands(commands)
    _continuity_commands(commands)
    command = commands.add_parser('spec', help='只读检查六段 Spec 结构')
    command.add_argument('path')
    try:
        args = parser.parse_args(argv)
        if args.command == 'spec':
            from .spec import load_spec
            print(json.dumps(load_spec(args.path).as_dict(), ensure_ascii=False))
            return 0
        if args.command in ('task-create', 'task-run', 'task-show', 'task-review', 'course-submit'):
            result = _l04_dispatch(args)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if args.command == 'task-show' or result['ok'] else 1
        if args.command.startswith(('course-', 'initiative-')):
            result = _continuity_dispatch(args)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['ok'] else 1
        if args.command != "serve-workbench":
            ledger = BootstrapLedger(service_runtime("workbench", args.runtime_dir))
            arguments = vars(args).copy()
            operation = arguments.pop("command").removeprefix("workbench-").replace("-", "_")
            arguments.pop("runtime_dir")
            result = getattr(ledger, operation)(**arguments)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["ok"] else 1
    except LedgerError as error:
        result = failure_result(error.code, str(error))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as error:
        result = failure_result("operation_failed", str(error))
    else:
        return _serve(args)
    print(json.dumps(result, ensure_ascii=False))
    return 1


def _serve(args) -> int:
    from .workbench_server import serve
    try:
        serve(args.host, args.port, str(service_runtime("workbench", args.runtime_dir)))
    except (OSError, ValueError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
