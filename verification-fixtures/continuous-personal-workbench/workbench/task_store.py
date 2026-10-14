"""L04 task/event storage. Its database is independent of the L01 ledger."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .bootstrap import LedgerError
from .bootstrap_source import CONTROL_ROOT, source_identity
from .spec import parse_spec

TaskError = LedgerError


def required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise TaskError('invalid_argument', name + ' 不能为空')
    return value.strip()


def person_key(value):
    return ' '.join(value.split()).casefold()


def event(detail, evidence=None):
    return {'at': datetime.now(timezone.utc).isoformat(), 'detail': detail, 'evidence': evidence or {}}


class TaskStore:
    def __init__(self, runtime_dir, *, control_root=CONTROL_ROOT):
        from .execution import require_unlinked_path
        require_unlinked_path(runtime_dir)
        require_unlinked_path(control_root)
        self.runtime = Path(runtime_dir).resolve()
        self.control_root = Path(control_root).resolve()
        self.database = self.runtime / 'l04-tasks.sqlite3'

    @contextmanager
    def _connection(self, *, create=False, readonly=False):
        if create:
            self.runtime.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database, timeout=30)
        else:
            if not self.database.is_file():
                raise TaskError('task_missing', 'L04 任务不存在')
            connection = sqlite3.connect(self.database.as_uri() + ('?mode=ro' if readonly else '?mode=rw'),
                                        uri=True, timeout=30)
        try:
            with connection:
                connection.execute('BEGIN' if readonly else 'BEGIN IMMEDIATE')
                if create:
                    connection.execute('CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
                yield connection
        finally:
            connection.close()

    def create(self, *, requirement_id, request, spec_path, actor, kind='A', workspace_dir=None,
               execute_code=None, allowed_write_files=None, execution_timeout=None, source_task_id=None,
               bootstrap_task_id=None, fixture=False):
        requirement_id = required(requirement_id, 'requirement-id')
        request = required(request, 'request')
        actor = required(actor, 'actor')
        generic = any(value is not None for value in (workspace_dir, execute_code, allowed_write_files,
                                                     execution_timeout, source_task_id))
        if generic:
            kind = 'generic'
        if kind not in ('A', 'B', 'generic'):
            raise TaskError('invalid_kind', '仅支持 L04 A/B')
        path = Path(spec_path).resolve()
        raw = path.read_bytes()
        # Normalize display/parser newlines; retain the exact decoded source too.
        text = raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
        parsed = parse_spec(text).as_dict()  # Validate before any runtime/database creation.
        frozen = {}
        if generic:
            from .bootstrap_handoff import current_a
            from . import execution
            from .task_contract import spec_settings, exact_files, commands
            accepted = current_a(self, bootstrap_task_id, fixture=fixture)
            if execute_code is not None and type(execute_code) is not bool:
                raise TaskError('invalid_argument', 'execute_code 必须为布尔许可')
            if source_task_id is not None:
                source_task_id = required(source_task_id, 'source-task-id')
                self.get(source_task_id)  # Same database; validate before mutation.
            if not workspace_dir:
                raise TaskError('workspace_missing', '通用任务必须冻结 workspace')
            workspace = Path(workspace_dir).resolve()
            execution.require_unlinked_path(workspace_dir)
            if (not workspace.is_dir() or not workspace.is_relative_to(self.control_root / '.runtime')
                    or execution.linked(Path(workspace_dir))):
                raise TaskError('invalid_workspace', '通用任务只执行个人 .runtime 内候选')
            if not execution.valid_timeout(execution_timeout):
                raise TaskError('invalid_timeout', '通用任务必须冻结有效超时')
            settings = spec_settings(parsed)
            scope = exact_files(workspace, allowed_write_files, settings['write_scope']) if execute_code else []
            if not execute_code and allowed_write_files:
                raise TaskError('readonly_arguments', '只读任务不能冻结写许可')
            frozen = {'workspace_path': str(workspace), 'write_scope': scope, 'execute_code': bool(execute_code),
                      'execution_timeout': execution_timeout, 'source_task_id': source_task_id,
                      'bootstrap_task_id': accepted['task_id'], 'eval_commands': commands(settings),
                      'frozen_workspace': execution.file_hashes(workspace)}
        task = {'ok': True, 'task_id': 'TASK-' + uuid4().hex.upper(), 'kind': kind,
                'requirement_id': requirement_id, 'request': request, 'actor': actor,
                'executor': None, 'verification_actor': None, 'status': 'spec_ready',
                'spec': {'path': str(path), 'text': text, 'original_text': raw.decode('utf-8'),
                         'sha256': hashlib.sha256(raw).hexdigest(),
                         'parsed': parsed}, 'errors': [], 'execution': None, 'diff': None,
                'eval': None, 'isolation': None, 'source_identity': None, 'accepted_source': None,
                'reviewer': None, 'review_decision': None, 'acceptance': 'pending_human_review',
                'events': [event('已绑定 Spec 创建任务', {'spec_sha256': hashlib.sha256(raw).hexdigest()})]}
        task.update(id=task['task_id'], spec_text=text, spec_sha256=task['spec']['sha256'],
                    workspace_path=None, write_scope=[], source_task_id=source_task_id,
                    fixture=bool(fixture or (not generic and actor.startswith('fixture'))),
                    human_review='pending_human_review', usage=None)
        task.update(frozen)
        with self._connection(create=True) as connection:
            connection.execute('INSERT INTO tasks VALUES (?, ?)', (task['task_id'], json.dumps(task, ensure_ascii=False)))
        return task

    @staticmethod
    def _read(connection, task_id):
        row = connection.execute('SELECT body FROM tasks WHERE task_id = ?', (task_id,)).fetchone()
        if row is None:
            raise TaskError('task_missing', 'L04 任务不存在：' + task_id)
        task = json.loads(row[0])
        task.setdefault('id', task['task_id'])
        task.setdefault('spec_text', task['spec']['text'])
        task.setdefault('spec_sha256', task['spec']['sha256'])
        task.setdefault('workspace_path', (task.get('isolation') or {}).get('path'))
        task.setdefault('write_scope', [])
        task.setdefault('source_task_id', None)
        task.setdefault('human_review', 'pending_human_review')
        task.setdefault('usage', None)
        return task

    def get(self, task_id):
        with self._connection(readonly=True) as connection:
            return self._read(connection, task_id)

    def mutate(self, task_id, operation):
        with self._connection() as connection:
            task = self._read(connection, task_id)
            operation(task)
            connection.execute('UPDATE tasks SET body = ? WHERE task_id = ?',
                               (json.dumps(task, ensure_ascii=False), task_id))
        return task

    def update(self, task_id, *, detail, evidence=None, expected_status=None, **fields):
        def operation(task):
            if expected_status is not None and task['status'] not in expected_status:
                raise TaskError('invalid_state', '任务状态不允许此操作：' + task['status'])
            task.update(fields)
            task['events'].append(event(detail, evidence))
        return self.mutate(task_id, operation)

    def fail(self, task_id, code, message, **fields):
        return self.update(task_id, detail='任务失败，已保留证据', evidence={'code': code, 'message': message},
                           status='failed', ok=False, errors=[code], **fields)

    def review(self, task_id, *, reviewer, decision, note, fixture=False):
        reviewer = required(reviewer, 'reviewer')
        note = required(note, 'note')
        if decision not in ('approve', 'reject'):
            raise TaskError('invalid_decision', '审核决定必须是 approve/reject')
        def operation(task):
            if task['status'] != 'review':
                raise TaskError('invalid_state', '只能审核 review 任务；失败任务保留结果并新建复验')
            people = [task['actor'], task.get('executor')]
            if person_key(reviewer) in {person_key(p) for p in people if p}:
                raise TaskError('self_review', '实现者或代码执行者不能自签')
            if decision == 'approve':
                current = source_identity(self.control_root)
                if task['source_identity'] != current:
                    raise TaskError('source_changed', '复验后控制源码变化，须新建 A 复验')
                if task['kind'] == 'A':
                    verifier = task.get('verification_actor')
                    if not verifier or person_key(verifier) == person_key(task['actor']):
                        raise TaskError('verification_not_independent', 'A 必须由非实现者复验')
                elif task['kind'] == 'B':
                    from .bootstrap_handoff import require_accepted_a
                    from .execution import file_hashes
                    require_accepted_a(self, task['bootstrap_task_id'])
                    if file_hashes(task['isolation']['path']) != task['diff']['after']:
                        raise TaskError('candidate_changed', '复验后候选内容变化，须新建候选复验')
                else:
                    from .bootstrap_handoff import current_a
                    from .execution import file_hashes
                    current_a(self, task['bootstrap_task_id'], fixture=task.get('fixture', False))
                    if file_hashes(task['workspace_path']) != task['diff']['after']:
                        raise TaskError('candidate_changed', '通用候选复验后发生变化')
                task['accepted_source'] = current
            is_fixture = bool(fixture or task.get('fixture') or (task.get('execution') or {}).get('test_double'))
            task.update(status='completed' if decision == 'approve' else 'rework', ok=True,
                        reviewer=reviewer, review_decision=decision,
                        fixture=is_fixture,
                        acceptance=('fixture_approved' if is_fixture else 'human_approved') if decision == 'approve' else 'pending_human_review',
                        human_review='human_approved' if decision == 'approve' and not is_fixture else 'pending_human_review')
            task['events'].append(event('已记录具名审核决定', {'reviewer': reviewer, 'decision': decision,
                                                        'note': note, 'source': task['accepted_source']}))
        return self.mutate(task_id, operation)
