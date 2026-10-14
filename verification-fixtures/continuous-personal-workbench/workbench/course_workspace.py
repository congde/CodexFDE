"""Copy an explicitly selected independent client into a unique runtime candidate."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from .bootstrap_source import CONTROL_ROOT
from .execution import linked, sensitive_path
from .task_store import TaskError
from . import execution

_SOURCE_SUFFIXES = {'.py', '.json', '.toml', '.txt', '.csv', '.md', '.yaml', '.yml', '.sql'}
_NECESSARY_ROOT_FILES = ('pyproject.toml', 'requirements.txt', 'setup.cfg', 'setup.py')
_SKIP_DIRS = {'__pycache__', '.git', '.venv', '.runtime', '.codex', '.agents', '.aws', '.ssh', 'node_modules'}


def create_candidate(source_root, runtime_dir, *, control_root=CONTROL_ROOT, on_created=None):
    execution.require_unlinked_path(source_root)
    execution.require_unlinked_path(runtime_dir)
    if Path(source_root).exists() and linked(Path(source_root)):
        raise TaskError('source_link', '客户来源根目录不得使用链接')
    source = Path(source_root).resolve()
    control = Path(control_root).resolve()
    runtime = Path(runtime_dir).resolve()
    if not runtime.is_relative_to(control / '.runtime'):
        raise TaskError('invalid_runtime', '课程候选必须位于个人项目 .runtime 内')
    if source.is_relative_to(control) or control.is_relative_to(source):
        raise TaskError('invalid_client_source', '必须选择独立客户源码，不能使用控制根目录')
    if not all((source / package).is_dir() for package in ('flowerp', 'eval', 'tests')):
        raise TaskError('client_source_missing', '独立客户源码须具有 flowerp/eval/tests')
    candidate = runtime / 'candidates' / ('l04-' + uuid4().hex)
    candidate.mkdir(parents=True, exist_ok=False)
    isolation = {'path': str(candidate), 'source_root': str(source), 'mode': 'isolated-source-snapshot',
                 'sources': {}, 'omitted': [], 'revision': None}
    if on_created:
        on_created(isolation)
    revision = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, check=False)
    if revision.returncode == 0:
        isolation['revision'] = revision.stdout.strip()
    paths = []
    for package in ('flowerp', 'eval', 'tests'):
        if linked(source / package):
            raise TaskError('source_link', '拒绝链接式客户来源：' + package)
        for directory, dirs, names in os.walk(source / package, followlinks=False):
            base = Path(directory)
            for name in list(dirs):
                path = base / name
                if linked(path):
                    raise TaskError('source_link', '拒绝客户来源中的链接：' + str(path))
                if name in _SKIP_DIRS:
                    dirs.remove(name)
                    isolation['omitted'].append(path.relative_to(source).as_posix() + '/')
            paths.extend(base / name for name in names)
    paths.extend(source / name for name in _NECESSARY_ROOT_FILES if (source / name).is_file())
    for origin in sorted(paths):
        relative = origin.relative_to(source).as_posix()
        if linked(origin):
            raise TaskError('source_link', '拒绝客户来源中的链接：' + relative)
        if sensitive_path(relative) or origin.suffix.lower() not in _SOURCE_SUFFIXES:
            isolation['omitted'].append(relative)
            continue
        raw = origin.read_bytes()
        if execution._SECRET.search(raw):
            isolation['omitted'].append(relative)
            continue
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        isolation['sources'][relative] = {'source': str(origin), 'sha256': hashlib.sha256(raw).hexdigest()}
    require_source_unchanged(isolation)
    return isolation


def inventory_commands(workspace):
    import sys
    root = Path(workspace)
    module = 'tests.test_l04_inventory_delivery' if (root / 'tests/test_l04_inventory_delivery.py').is_file() else 'tests.test_inventory_export'
    if not (root / (module.replace('.', '/') + '.py')).is_file():
        raise TaskError('eval_asset_missing', '候选缺少实际库存检查模块')
    return [[sys.executable, '-B', '-S', '-X', 'utf8', '-m', 'unittest', module, '-v']]


def prepare_inventory(isolation, control_root, *, timeout=90):
    """Explicitly invoke the recorded teaching utility; retain original provenance."""
    import sys
    from eval.harness import evaluate
    root, workspace = Path(control_root), Path(isolation['path'])
    commands = inventory_commands(workspace)
    isolation['eval_commands'] = commands
    if (workspace / 'tests/test_l04_inventory_delivery.py').is_file():
        record = json.loads((root / '.course/learning-resources.json').read_text(encoding='utf-8-sig'))
        reference = Path(record['source_root']).resolve()
        tool = reference / 'docs/courses/L04/scripts/prepare_product_start.py'
        if not tool.is_file() or linked(tool):
            raise TaskError('teaching_tool_missing', 'L01 来源记录中的教学准备工具缺失或为链接')
        before = execution.file_hashes(workspace)
        result = execution.run_command([sys.executable, '-B', '-S', '-X', 'utf8', str(tool),
            '--workspace', str(workspace), '--control-root', str(root), '--reference-root', str(reference)],
            cwd=root, timeout=timeout)
        after = execution.file_hashes(workspace)
        isolation['teaching_preparation'] = {'tool': str(tool), 'sha256': hashlib.sha256(tool.read_bytes()).hexdigest(),
            'result': result, 'diff': execution.hash_diff(before, after,
                ['flowerp/service.py', 'flowerp/import_export.py', '.course/l04-preparation.json', '.course/student-start.json'], workspace),
            'boundary': 'explicit teaching preparation; not model diff or human acceptance'}
        if result['exitcode'] != 0 or result['error'] or result['timed_out']:
            raise TaskError('teaching_preparation_failed', '教学准备失败，保留结果，禁止启动执行器')
        if isolation['teaching_preparation']['diff']['violations']:
            raise TaskError('teaching_scope_violation', '教学准备越界')
    isolation['baseline'] = execution.file_hashes(workspace)
    # Report lives outside candidate; the red command must not create model changes.
    report_path = workspace.parent / (workspace.name + '-initial-eval.json')
    isolation['initial_eval'] = evaluate(commands, cwd=workspace, timeout=timeout, report_path=report_path)
    if execution.file_hashes(workspace) != isolation['baseline']:
        raise TaskError('initial_eval_side_effect', '实现前 Eval 改变候选')
    if isolation['initial_eval']['status'] == 'passed':
        isolation['starting_state'] = 'already_passed_not_red'
    else:
        output = '\n'.join(c['stdout'] + c['stderr'] for c in isolation['initial_eval']['checks'])
        isolation['starting_state'] = 'environment_error' if any(s in output for s in ('ImportError:', 'ModuleNotFoundError:', 'Ran 0 tests')) else 'observed_failure'
        if isolation['starting_state'] == 'environment_error' and (workspace / 'tests/test_l04_inventory_delivery.py').is_file():
            raise TaskError('initial_eval_environment_error', '新版产品起点为环境错误，不能算业务红或启动模型')
    return isolation


def require_source_unchanged(isolation):
    for relative, entry in isolation['sources'].items():
        try:
            matches = hashlib.sha256(Path(entry['source']).read_bytes()).hexdigest() == entry['sha256']
        except OSError:
            matches = False
        if not matches:
            raise TaskError('client_source_changed', '独立客户来源内容变化：' + relative)
