"""Personal course handoff interfaces, not a completed sixteen-lesson platform."""
import hashlib
import ast
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from . import execution
from .bootstrap_source import source_identity
from .course_workspace import _SKIP_DIRS, _SOURCE_SUFFIXES, require_source_unchanged
from .spec import parse_spec
from .task_contract import exact_files, spec_settings
from .task_store import TaskError, required

L05_FROZEN = ('flowerp/service.py', 'tests/test_l05_receiving.py', 'tests/test_l05_scope.py', 'eval/l05_scope.py')
L05_COMMANDS = [['python', '-m', 'unittest', 'tests.test_l05_receiving', '-v'],
                ['python', '-m', 'unittest', 'tests.test_l05_scope', '-v'],
                ['python', '-m', 'eval.harness', '--suite', 'blocking', '--case', 'receiving_is_idempotent']]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_record(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)


def source_files(root, packages, root_files=()):
    """Small explicit source inventory; never walk the synchronized course resources."""
    root = Path(root).resolve()
    files, omitted = {}, []
    paths = []
    for package in packages:
        start = root / package
        if not start.exists():
            continue
        if execution.linked(start):
            raise TaskError('source_link', '拒绝链接来源：' + str(start))
        for directory, dirs, names in os.walk(start, followlinks=False):
            base = Path(directory)
            for name in list(dirs):
                path = base / name
                if execution.linked(path):
                    raise TaskError('source_link', '拒绝链接来源：' + str(path))
                if name in _SKIP_DIRS or name in execution.ENUMERATION_OMISSIONS:
                    omitted.append(path.relative_to(root).as_posix() + '/')
                    dirs.remove(name)
            paths.extend(base / name for name in names)
    paths.extend(root / name for name in root_files if (root / name).exists())
    for path in sorted(paths):
        name = path.relative_to(root).as_posix()
        if execution.linked(path):
            raise TaskError('source_link', '拒绝链接来源：' + name)
        raw = path.read_bytes()
        if (execution.sensitive_path(name) or path.suffix.lower() not in _SOURCE_SUFFIXES | {'.html', '.js', '.css', '.cfg'}
                or execution._SECRET.search(raw)):
            omitted.append(name)
            continue
        files[name] = {'source': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
    return files, omitted


class CourseContinuity:
    def __init__(self, store):
        self.store = store
        self.control = store.control_root
        self.registry = store.runtime / 'course-preparations'
        self.specs = self.control / '.runtime/course-specs'

    def contract(self, lesson):
        data = json.loads((self.control / '.course/course-contracts.json').read_text(encoding='utf-8-sig'))
        for item in data['lessons']:
            if item['number'] == lesson:
                return {**item, 'ok': True, 'data_only': True, 'acceptance': 'pending_human_review',
                        'contract_source': str(self.control / '.course/course-contracts.json')}
        raise TaskError('lesson_missing', '合同数据不存在此课次')

    def spec(self, lesson, output):
        contract = self.contract(lesson)
        path = Path(output).absolute()
        if path.exists() or path.is_symlink():
            raise TaskError('spec_exists', 'course-spec 拒绝覆盖已有文件')
        if any(p.exists() and execution.linked(p) for p in (path.parent, *path.parent.parents)):
            raise TaskError('spec_link', 'Spec 输出路径不能包含链接')
        text = contract['spec_template']
        if lesson == 5:
            marker = '\n## 完成定义'
            binding = '\n```json\n' + json.dumps({'eval_commands': L05_COMMANDS}, ensure_ascii=False) + '\n```\n'
            text = text.replace(marker, binding + marker)
        parse_spec(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x+b') as stream:
            stream.write(text.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            stored = stream.read()
        record = {'lesson': lesson, 'path': str(path.resolve()), 'text': text,
                  'sha256': hashlib.sha256(stored).hexdigest(),
                  'template_only': True, 'acceptance': 'pending_human_review'}
        # Every generation gets a separate immutable snapshot; no learner Spec is overwritten.
        write_record(self.specs / (f'l{lesson:02d}-' + uuid4().hex + '.json'), record)
        return {'ok': True, **record}

    def status(self, require_baselines=False):
        data = json.loads((self.control / '.course/course-contracts.json').read_text(encoding='utf-8-sig'))
        reference = Path(data['source_root']).resolve()
        reference_baselines, lessons = [], []
        for item in data['lessons']:
            result = execution.run_command(['git', '-C', str(reference), 'rev-parse', '--verify',
                                            item['baseline_ref'] + '^{commit}'], cwd=self.control, timeout=10)
            present = result['exitcode'] == 0 and not result['error']
            reference_baselines.append({'lesson': item['number'], 'ref': item['baseline_ref'], 'present': present,
                                        'sha': result['stdout'].strip() if present else None, 'source': str(reference)})
            assets = {1: ['workbench/bootstrap.py'], 2: ['AGENTS.md'], 3: ['workbench/spec.py'],
                      4: ['workbench/task_store.py', 'workbench/workflow.py', 'workbench/execution.py', 'eval/harness.py'],
                      5: ['tests/test_l05_receiving.py', 'tests/test_l05_scope.py', 'eval/l05_scope.py', 'eval/cases.py']}.get(item['number'], [])
            missing = [name for name in assets if not (self.control / name).is_file()]
            implementation = 'personal_source_present_requires_review' if item['number'] <= 4 and not missing else 'construction_pending'
            lessons.append({'lesson': item['number'], 'implementation': implementation,
                            'missing_assets': missing, 'acceptance': 'pending_human_review'})
        tasks = []
        if self.store.database.is_file():
            with self.store._connection(readonly=True) as connection:
                tasks = [json.loads(row[0]) for row in connection.execute('SELECT body FROM tasks')]
        return {'ok': not require_baselines or all(r['present'] for r in reference_baselines),
                'reference_baselines': reference_baselines, 'materials_only': True, 'lessons': lessons,
                'personal_source': source_identity(self.control),
                'personal_tasks': [{'id': t['task_id'], 'status': t['status'], 'fixture': t.get('fixture', False),
                                    'acceptance': t['acceptance']} for t in tasks],
                'acceptance': 'pending_human_review', 'sixteen_lessons_complete': False,
                'errors': [] if all(r['present'] for r in reference_baselines) else ['reference_baselines_missing']}

    def _git(self, *args, cwd=None):
        result = execution.run_command(['git', '-C', str(cwd or self.control), *args], cwd=self.control, timeout=30)
        if result['exitcode'] != 0 or result['error'] or result['timed_out']:
            raise TaskError('candidate_git_unavailable', result['stderr'] or result['error'] or 'Git 命令失败')
        return result['stdout'] if '-z' in args else result['stdout'].strip()

    def prepare(self, *, lesson, task_id, source='working-tree', teaching=True):
        self.contract(lesson)
        required(task_id, 'task-id')
        if lesson not in (4, 5, 7, 8):
            raise TaskError('construction_pending', '已建设 L04/L05/L07/L08 prepare；后续能力尚待逐讲建设')
        if source != 'working-tree':
            raise TaskError('unsupported_source', '只使用本人的 working-tree，不使用教师终态标签')
        runtime = self.store.runtime
        if not runtime.is_relative_to(self.control / '.runtime'):
            raise TaskError('invalid_runtime', '组合候选必须位于个人 .runtime')
        raw_source = os.environ.get('FLOWERP_PROJECT_ROOT')
        if not raw_source:
            raise TaskError('client_source_missing', '须声明 FLOWERP_PROJECT_ROOT 独立客户来源')
        origin = Path(raw_source)
        if execution.linked(origin):
            raise TaskError('source_link', '客户来源不能为链接')
        product = origin.resolve()
        if product.is_relative_to(self.control) or self.control.is_relative_to(product):
            raise TaskError('invalid_client_source', '客户来源须独立于工作台')
        if not all((product / name).is_dir() for name in ('flowerp', 'eval', 'tests')):
            raise TaskError('client_source_missing', '产品缺 flowerp/eval/tests')
        control_files, control_omitted = source_files(self.control, ('workbench', 'eval', 'tests', 'agent', 'workbench_web'),
                            ('AGENTS.md', 'pyproject.toml', '.course/course-contracts.json', '.course/learning-resources.json'))
        product_files, product_omitted = source_files(product, ('flowerp', 'eval', 'tests', 'web'))
        mapped = {}
        for name, entry in product_files.items():
            if name == 'tests/__init__.py' and name in control_files and control_files[name]['sha256'] != entry['sha256']:
                body = ast.parse(Path(entry['source']).read_text(encoding='utf-8-sig')).body
                if any(not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))
                       and not isinstance(node, ast.Pass) for node in body):
                    raise TaskError('source_collision', '产品 tests 初始化包含执行代码，不能合并覆盖个人包')
            target = {'eval/cases.py': 'eval/erp_cases.py',
                      'eval/__init__.py': 'product_eval/__init__.py',
                      'tests/__init__.py': 'product_tests/__init__.py'}.get(name, name)
            if target.startswith('eval/') and target in control_files and control_files[target]['sha256'] != entry['sha256']:
                target = 'eval/erp_' + Path(name).name
            if target in control_files and control_files[target]['sha256'] != entry['sha256']:
                raise TaskError('source_collision', '同名不同内容拒绝覆盖：' + target)
            mapped[target] = entry
        path = runtime / 'candidates' / (f'l{lesson:02d}-combined-' + uuid4().hex)
        record = {'lesson': lesson, 'task_id': task_id, 'path': str(path), 'source': source,
                  'mode': 'personal-shared-object-worktree', 'control_root': str(self.control), 'source_root': str(product),
                  'control_sources': control_files, 'product_sources': product_files, 'sources': {**control_files, **mapped},
                  'omitted': {'control': control_omitted, 'product': product_omitted},
                  'namespace_separation': {'product eval/cases.py': 'eval/erp_cases.py',
                    'product tests/__init__.py': 'product_tests/__init__.py (archived initializer; personal tests initializer owns tests)'},
                  'construction_pending': ['L05 personal checks and minimal case'] if lesson == 5 else [],
                  'acceptance': 'pending_human_review', 'teaching_preparation': None}
        record_path = self.registry / (uuid4().hex + '.json')
        try:
            head = self._git('rev-parse', '--verify', 'HEAD^{commit}')
            record['checkpoint'] = {'sha': head, 'personal_checkpoint': True, 'teacher_release': False,
                                    'human_acceptance': False, 'teacher_ref': self.contract(lesson)['baseline_ref']}
            path.parent.mkdir(parents=True, exist_ok=True)
            self._git('worktree', 'add', '--detach', str(path), head)
            # Delete inherited tracked files only within this newly created candidate.
            for name in filter(None, self._git('ls-files', '-z', cwd=path).split('\0')):
                target = path / name
                if not target.resolve().is_relative_to(path) or execution.linked(target):
                    raise TaskError('candidate_link', '初始 Git checkout 包含链接')
                if name not in record['sources'] and target.is_file():
                    target.unlink()
            for name, entry in record['sources'].items():
                target = path / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(Path(entry['source']).read_bytes())
            require_source_unchanged(record)
            before = execution.file_hashes(path)
            if teaching and lesson == 4:
                from .course_workspace import prepare_inventory
                prepare_inventory(record, self.control)
            elif teaching:
                resources = json.loads((self.control / '.course/learning-resources.json').read_text(encoding='utf-8-sig'))
                reference = Path(resources['source_root']).resolve()
                tool = reference / 'workbench/lesson_constructibility.py'
                if not tool.is_file() or execution.linked(tool):
                    raise TaskError('teaching_tool_missing', '记录的教学起点准备器缺失或为链接')
                code = ('import importlib.util,json,sys; p=sys.argv[1]; '
                        's=importlib.util.spec_from_file_location("_explicit_preparer",p); '
                        'm=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); '
                        'print(json.dumps(m.apply_student_start(sys.argv[2],int(sys.argv[3])),ensure_ascii=False))')
                result = execution.run_command([sys.executable, '-B', '-S', '-X', 'utf8', '-c', code,
                                                str(tool), str(path), str(lesson)], cwd=self.control, timeout=30)
                after = execution.file_hashes(path)
                record['teaching_preparation'] = {'source': str(tool), 'sha256': digest(tool), 'result': result,
                    'diff': execution.hash_diff(before, after, ['flowerp/service.py', 'flowerp/import_export.py', 'flowerp/sales.py',
                        '.course/student-start.json'], path), 'boundary': 'teacher preparation only; no workflow import'}
                if result['exitcode'] != 0 or result['error'] or result['timed_out']:
                    raise TaskError('teaching_preparation_failed', '教学工具失败，保留来源/输出')
                payload = json.loads(result['stdout'])
                record['teaching_preparation']['payload'] = payload
                product_unapplied = [e for e in payload.get('unapplied_overlays', []) if str(e.get('path', '')).startswith('flowerp/')]
                if product_unapplied or not payload.get('start_gap_applied'):
                    raise TaskError('product_overlay_anchor_missing', '业务 overlay 缺锚，禁止从参考答案补齐')
                if record['teaching_preparation']['diff']['violations']:
                    raise TaskError('teaching_scope_violation', '教学工具修改了未授权准备文件')
            provenance = path / '.course/product-source.json'
            provenance.parent.mkdir(parents=True, exist_ok=True)
            write_record(provenance, {'product_root': str(product), 'control_root': str(self.control),
                                      'files': product_files, 'scope': 'source preparation; not acceptance'})
            record['baseline'] = execution.file_hashes(path)
            record['ok'] = True
        except Exception as error:
            record.update(ok=False, errors=[getattr(error, 'code', 'prepare_failed')], message=str(error))
            write_record(record_path, record)
            raise
        record['record_path'] = str(record_path)
        write_record(record_path, record)
        return record

    def resolve_session(self, lesson, sha):
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', sha):
            raise TaskError('unknown_session_baseline', '只接受保存候选完整 SHA')
        if not self.registry.exists():
            raise TaskError('unknown_session_baseline', '没有本人保存的准备候选')
        for filename in self.registry.glob('*.json'):
            record = json.loads(filename.read_text(encoding='utf-8'))
            if not record.get('ok') or record['lesson'] != lesson:
                continue
            candidate = Path(record['path'])
            if not candidate.is_dir() or not candidate.resolve().is_relative_to(self.store.runtime / 'candidates') or execution.linked(candidate):
                continue
            if self._git('rev-parse', 'HEAD', cwd=candidate) != sha:
                continue
            common = Path(self._git('rev-parse', '--path-format=absolute', '--git-common-dir', cwd=candidate)).resolve()
            expected_common = Path(self._git('rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
            if common != expected_common:
                raise TaskError('unknown_session_baseline', '候选 Git objects 不属于个人控制仓库')
            self._git('cat-file', '-e', sha + '^{commit}')
            self._git('merge-base', '--is-ancestor', record['checkpoint']['sha'], sha)
            changed = self._git('diff', '--name-only', record['checkpoint']['sha'], sha).splitlines()
            if set(changed) - set(L05_FROZEN):
                raise TaskError('session_scope_violation', '会话提交包含四个冻结文件之外的内容')
            return {**record, 'session_baseline_ref': sha}
        raise TaskError('unknown_session_baseline', 'SHA 不属于本库保存候选的当前会话提交')

    def submit(self, *, lesson, session_baseline_ref, actor, allowed_files, execution_timeout=900,
               bootstrap_task_id=None, fixture=False):
        contract = self.contract(lesson)
        if lesson != 5:
            raise TaskError('construction_pending', '后续课程能力尚未实现，本次只接线 L05')
        prepared = self.resolve_session(lesson, session_baseline_ref)
        candidate = Path(prepared['path'])
        require_source_unchanged(prepared)
        observed = execution.file_hashes(candidate)
        changes = {name for name in observed.keys() | prepared['baseline'].keys()
                   if observed.get(name) != prepared['baseline'].get(name)}
        if changes - set(L05_FROZEN) or execution.sensitive_files(candidate, observed):
            raise TaskError('session_scope_violation', '会话候选变更越出四个冻结文件或含敏感材料')
        snapshots = sorted(self.specs.glob('l05-*.json'), key=lambda p: p.stat().st_mtime_ns) if self.specs.exists() else []
        if not snapshots:
            raise TaskError('personal_spec_missing', '先用 course-spec 保存本人的合同快照')
        spec = json.loads(snapshots[-1].read_text(encoding='utf-8'))
        if not Path(spec['path']).is_file() or digest(spec['path']) != spec['sha256']:
            raise TaskError('personal_spec_changed', '本人 Spec 与保存快照不一致；保留旧记录并重新冻结')
        parsed = parse_spec(spec['text']).as_dict()
        settings = spec_settings(parsed)
        allowed = exact_files(candidate, allowed_files, settings['write_scope'])
        exact_files(candidate, allowed, contract['write_scope'])
        if set(allowed) & set(L05_FROZEN[1:]):
            raise TaskError('frozen_check_write', 'L05 修复阶段不得改冻结检查')
        missing = [name for name in L05_FROZEN if not (candidate / name).is_file()]
        if not (candidate / 'eval/cases.py').is_file():
            missing.append('eval/cases.py:receiving_is_idempotent (personal)')
        if missing:
            return {'ok': False, 'errors': ['construction_pending'], 'construction_pending': missing,
                    'isolation': prepared, 'acceptance': 'pending_human_review', 'task': None,
                    'message': '接口已接线；缺本人 L05 检查资产，未启动执行器'}
        # Frozen checks must be represented in the exact session commit, not unsaved copies.
        for name in L05_FROZEN:
            saved = execution.run_command(['git', '-C', str(candidate), 'show', session_baseline_ref + ':' + name],
                                           cwd=self.control, timeout=10)
            if saved['exitcode'] != 0 or saved['stdout'].replace('\r\n', '\n') != (candidate / name).read_text(encoding='utf-8').replace('\r\n', '\n'):
                raise TaskError('session_checks_unfrozen', '冻结文件缺于 SHA 或已改变：' + name)
        from .workflow import run_task
        task = self.store.create(requirement_id=contract['requirement_id'], request=contract['request'],
                                 spec_path=spec['path'], actor=actor, workspace_dir=str(candidate), execute_code=True,
                                 allowed_write_files=allowed, execution_timeout=execution_timeout,
                                 bootstrap_task_id=bootstrap_task_id, fixture=fixture)
        self.store.update(task['id'], detail='已绑定本人保存的会话 SHA 和双方来源',
                          isolation=prepared, session_baseline_ref=session_baseline_ref,
                          evidence={'session_baseline_ref': session_baseline_ref, 'preparation': prepared['record_path']})
        result = run_task(self.store, task['id'], actor=actor)
        implementation = bool(result['status'] == 'review' and result.get('diff', {}).get('changed')
                              and not result['diff']['violations'] and result.get('execution', {}).get('exitcode') == 0)
        return {'ok': result['ok'], 'task': result, 'task_id': result['task_id'], 'id': result['id'],
                'isolation': prepared, 'implementation_evidence': implementation,
                'acceptance': result['acceptance'], 'errors': result['errors']}
