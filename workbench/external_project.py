"""Process boundary for the independent FlowERP repository."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def flowerp_root(explicit=None):
    value = explicit or os.environ.get('FLOWERP_PROJECT_ROOT')
    if value:
        root = Path(value).resolve()
    else:
        from .project_store import ProjectStore
        from .runtime_paths import service_runtime
        database = service_runtime('workbench', root=ROOT) / 'workbench.db'
        projects = ProjectStore(database).list() if database.is_file() else []
        matches = [Path(p['root_path']).resolve() for p in projects
                   if Path(p['root_path']).resolve() != ROOT and (Path(p['root_path']) / 'flowerp/server.py').is_file()]
        if len(matches) != 1:
            raise ValueError('请在工作台添加独立 FlowERP 仓库，或设置 FLOWERP_PROJECT_ROOT 指向其目录')
        root = matches[0]
    if root == ROOT or not (root / 'flowerp/server.py').is_file():
        raise ValueError('FlowERP 必须指向独立客户仓库，不能指向 CodexFDE 工作台')
    return root


def python_for(root):
    python = Path(root) / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        raise ValueError('请先在 FlowERP 仓库创建 .venv 并执行 pip install -e .')
    return str(python)


def command(arguments, *, root=None):
    root = flowerp_root(root)
    return root, [python_for(root), '-X', 'utf8', '-m', 'flowerp', *arguments]


def run(arguments, *, capture=False):
    root, args = command(arguments)
    options = {'capture_output': True, 'text': True, 'encoding': 'utf-8', 'timeout': 180} if capture else {}
    return subprocess.run(args, cwd=root, **options)


def evaluate_case(name):
    root = Path.cwd().resolve()
    candidate = (root / 'eval/erp_cases.py').is_file() or (root / '.course/product-source.json').is_file()
    if candidate and (not (root / 'flowerp/__init__.py').is_file() or not (root / 'eval/erp_cases.py').is_file()):
        raise AssertionError('课程候选缺少业务源码或检查，不能回退到正式项目')
    if not candidate:
        root = flowerp_root()
    # Load the product checks explicitly: a course snapshot also has controller Eval.
    checks = root / 'eval/erp_cases.py'
    if not checks.is_file():
        checks = root / 'eval/cases.py'
    script = '''import importlib.util, json, pathlib, sys
import flowerp
root = pathlib.Path.cwd().resolve()
assert pathlib.Path(flowerp.__file__).resolve().is_relative_to(root), 'ERP import escaped candidate'
spec = importlib.util.spec_from_file_location('erp_checks', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps({'evidence': getattr(module, sys.argv[2])()}, ensure_ascii=False))
'''
    args = [sys.executable if candidate else python_for(root), '-X', 'utf8', '-c', script, str(checks), name]
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=180)
    if result.returncode:
        raise AssertionError(f'FlowERP 检查失败：{name}（退出码 {result.returncode}）\n{result.stderr[-4000:]}')
    evidence = json.loads(result.stdout.splitlines()[-1])['evidence']
    return f'FlowERP：{root}；{name}；退出码 0；{evidence}'
