"""Freeze precise permissions and commands from the personal six-section Spec."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

from . import execution
from .task_store import TaskError


def spec_settings(parsed):
    settings = {}
    for field in ('constraints', 'acceptance'):
        for match in re.finditer(r'```json\s*\n(.*?)\n```', parsed[field], re.S):
            value = json.loads(match[1])
            if isinstance(value, dict):
                for key in ('write_scope', 'eval_commands'):
                    if key in value:
                        if key in settings:
                            raise TaskError('ambiguous_spec', 'Spec 重复定义：' + key)
                        settings[key] = value[key]
    if 'write_scope' not in settings:
        match = re.search(r'允许写集[^：\n]*：([^\n]+)', parsed['constraints'])
        settings['write_scope'] = re.findall(r'`([^`]+)`', match[1]) if match else []
    return settings


def in_scope(name, scope):
    return any(name == item or (item.endswith('/') and name.startswith(item)) for item in scope)


def exact_files(workspace, names, scope):
    root = Path(workspace).resolve()
    if not isinstance(scope, list) or not scope:
        raise TaskError('spec_scope_missing', 'Spec 必须声明允许写集')
    for item in scope:
        if not isinstance(item, str) or not item or execution.sensitive_path(item):
            raise TaskError('invalid_spec_scope', 'Spec 写集包含保留路径')
        path = PurePosixPath(item.rstrip('/'))
        if path.is_absolute() or '..' in path.parts or '\\' in item or ':' in item or path.as_posix() != item.rstrip('/'):
            raise TaskError('invalid_spec_scope', 'Spec 写集格式无效')
    if not names:
        raise TaskError('write_set_missing', '须冻结精确允许写文件')
    result = []
    for name in names:
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name
                or path.as_posix() != name or execution.sensitive_path(name) or not in_scope(name, scope)):
            raise TaskError('invalid_write_set', '冻结写文件越界或为保留路径：' + name)
        target = root / name
        if not target.resolve().is_relative_to(root) or target.is_dir():
            raise TaskError('invalid_write_set', '写集须是候选内精确文件：' + name)
        for component in (target, *target.parents):
            if component == root:
                break
            if os.path.lexists(component) and execution.linked(component):
                raise TaskError('invalid_write_set', '允许写路径不能包含链接：' + name)
        result.append(name)
    return sorted(set(result))


def commands(settings):
    values = settings.get('eval_commands')
    if not isinstance(values, list) or not values:
        raise TaskError('eval_commands_missing', 'Spec 验收用例须冻结 JSON eval_commands，不使用教师 Harness 默认值')
    result = []
    for argv in values:
        if not isinstance(argv, list) or len(argv) < 2 or not all(isinstance(a, str) and a for a in argv):
            raise TaskError('invalid_eval_commands', 'Eval 命令须为非空 argv 数组')
        if argv[0] not in ('python', sys.executable):
            raise TaskError('invalid_eval_commands', '最小 Eval 只执行当前解释器的候选 Python 检查')
        if argv[1] != '-m' and (argv[1].startswith('-') or PurePosixPath(argv[1]).is_absolute() or ':' in argv[1] or '\\' in argv[1] or '..' in PurePosixPath(argv[1]).parts):
            raise TaskError('invalid_eval_commands', '检查入口必须为候选内脚本或模块')
        result.append([sys.executable, '-B', '-S', '-X', 'utf8', *argv[1:]])
    return result


def require_eval_assets(workspace, argv_list):
    root = Path(workspace).resolve()
    for argv in argv_list:
        args = argv[5:]
        modules = []
        if args[:1] == ['-m']:
            if len(args) < 2:
                raise TaskError('eval_asset_missing', 'Eval 模块未声明')
            if args[1] == 'unittest':
                modules = [name for name in args[2:] if not name.startswith('-')]
                if not modules or any(not name.startswith('tests.') for name in modules):
                    raise TaskError('eval_asset_missing', '必须指定本人保存的测试模块，拒绝发现式默认检查')
            else:
                modules = [args[1]]
            paths = [root / (module.replace('.', '/') + '.py') for module in modules]
            if args[1] == 'eval.harness' and '--case' in args:
                paths.append(root / 'eval/cases.py')
        else:
            paths = [root / args[0]]
        for path in paths:
            if not path.is_file() or not path.resolve().is_relative_to(root) or execution.linked(path):
                raise TaskError('eval_asset_missing', '本人候选缺少冻结检查入口：' + str(path))
