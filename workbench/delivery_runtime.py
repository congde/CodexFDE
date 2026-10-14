"""One local delivery owner, durable control and isolated CLI worker coordination.

Session events describe execution; the existing initiative/task chain still owns
delivery acceptance. No constructor resumes processes or invokes a model.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
import hashlib
import json
import logging
import math
from pathlib import Path
import secrets
import subprocess
import threading
import time

from agent.schedule import Subtask, assert_parallel_safe
from .daily_delivery import manifest, prepare_daily, submit_daily
from .execution import CodexExecutionRunner, normalize_write_scope
from .project_delivery import CandidateProjectEval
from .runtime_store import HarnessRuntimeStore
from .reference_paths import resolve_reference
from .workflow import run_task


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _profile_fingerprint(composition):
    return _digest({'profile_id': composition['profile']['id'], 'plugins': [
        {k: p[k] for k in ('id', 'seam', 'provider', 'config', 'enabled') if k in p}
        for p in composition['plugins']]})


class CandidateProjectRegistry:
    """Tool-only project view; never changes persisted project registration."""
    def __init__(self, projects, task, workspace):
        self.projects, self.workspace = projects, str(Path(workspace).resolve())
        self.project_id = next((r.partition(':')[2] for r in task.get('business_refs', []) if r.startswith('PROJECT:')), '')

    def get(self, project_id):
        if project_id != self.project_id:
            raise ValueError('工具不能跨项目读取其他候选')
        return self.projects.get(project_id) | {'root_path': self.workspace}


class RuntimeShellProvider:
    """Run admitted shell commands with the same cancellable process owner."""
    def __init__(self, event):
        self.event = event

    def exec(self, root, command, *, timeout=120):
        from .shell_provider import execution_command, normalize_command
        normalized = normalize_command(command)
        admitted = execution_command(normalized)
        runner = CodexExecutionRunner(root, Path(root).parent)
        runner.control_event = self.event
        result = runner._run_codex_streaming(admitted, '', min(max(1, int(timeout)), 600), lambda line: None, time.monotonic())
        return {'command': normalized, 'executed_command': admitted, 'returncode': result.returncode, 'stdout': result.stdout[-20000:],
                'stderr': result.stderr[-10000:], 'provider': 'local-controlled', 'success': result.returncode == 0}


class DeliveryRuntime:
    def __init__(self, repository, runtime, tasks, projects, initiatives, workflow,
                 runtime_store=None, *, runner_factory=CodexExecutionRunner):
        self.repository, self.runtime = Path(repository).resolve(), Path(runtime).resolve()
        self.tasks, self.projects, self.initiatives, self.workflow = tasks, projects, initiatives, workflow
        self.sessions = runtime_store or HarnessRuntimeStore(tasks.path)
        self.runner_factory = runner_factory
        self.lock = threading.RLock()
        self.closed = False
        self.shutdown_errors = []
        self.events, self.workers = {}, {}
        self.tool_workers = set()
        self.timers = {}
        self.tool_executor = None
        with self.tasks.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS delivery_runtime (
                    initiative_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS delivery_tool_approvals (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS delivery_approval_fingerprint
                    ON delivery_tool_approvals(run_id,fingerprint);
            ''')

    @staticmethod
    def _actor(actor):
        if not isinstance(actor, str) or not actor.strip() or actor.strip().startswith('agent:'):
            raise ValueError('运行操作需要具名人工操作人')
        return actor.strip()

    def _load(self, item_id):
        self.initiatives.get(item_id)
        with self.tasks.connect(create=False) as db:
            row = db.execute('SELECT revision,payload FROM delivery_runtime WHERE initiative_id=?', (item_id,)).fetchone()
        if row:
            return {'control_revision': 0} | json.loads(row['payload']) | {'revision': row['revision']}
        return {'initiative_id': item_id, 'run_id': None, 'revision': 0, 'state': 'idle',
                'control_revision': 0,
                'profile_id': 'PROFILE-DEFAULT', 'task_id': None, 'session_id': None,
                'workspace': None, 'subtasks': [], 'subtask_plan_id': None, 'max_workers': 2, 'error': '', 'evidence': [],
                'budget': {'token_budget': 30000, 'tokens_used': 0, 'time_budget_seconds': 900,
                           'elapsed_seconds': 0, 'max_workers': 2}, 'started_at': None}

    def _save(self, data, expected=None):
        with self.tasks.connect(create=False) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM delivery_runtime WHERE initiative_id=?', (data['initiative_id'],)).fetchone()
            current = row['revision'] if row else 0
            if expected is not None and current != expected:
                raise ValueError('运行版本已变化，请刷新后重试')
            data['revision'] = current + 1
            db.execute('INSERT OR REPLACE INTO delivery_runtime VALUES (?,?,?)',
                       (data['initiative_id'], data['revision'], _canonical(data)))

    def _event(self, data, kind, actor, payload):
        if data.get('session_id'):
            self.sessions.append(data['session_id'], kind, actor, payload)

    def _candidate_hash(self, data, *, record_process=True):
        workspace = data.get('workspace')
        target = resolve_reference(workspace, self.runtime) if workspace else None
        return _digest(manifest(target, self.runtime, record_process=record_process)) if target and target.is_dir() else ''

    def view(self, item_id, *, include_candidate=True):
        with self.lock:
            data = self._load(item_id)
            if data.get('started_at') and data['state'] in {'running', 'queued', 'pausing', 'cancelling', 'awaiting_approval'}:
                data['budget']['elapsed_seconds'] = int(max(0, time.time() - data['started_at']))
            data['candidate_sha256'] = self._candidate_hash(data, record_process=False) if include_candidate else ''
            data['candidate_verification'] = ('verified' if data['candidate_sha256'] else 'unavailable') if include_candidate else 'pending'
            with self.tasks.connect() as db:
                approvals = db.execute('SELECT id,status,payload FROM delivery_tool_approvals WHERE run_id=? ORDER BY rowid', (data['run_id'],)).fetchall()
            data['approvals'] = [json.loads(row['payload']) | {'id': row['id'], 'status': row['status']} for row in approvals]
            data['profiles'] = [{'id': p['id'], 'name': p['name']} for p in self.sessions.profiles()]
            data['can_pause'] = data['state'] in {'running', 'queued', 'awaiting_approval'}
            data['can_resume'] = (bool(data['candidate_sha256']) and data['state'] in {'paused', 'interrupted'} and not data['budget'].get('usage_unavailable')
                and data['budget']['tokens_used'] < data['budget']['token_budget']
                and data['budget']['elapsed_seconds'] < data['budget']['time_budget_seconds'])
            data['can_cancel'] = data['state'] in {'running', 'queued', 'pausing', 'paused', 'interrupted', 'awaiting_approval', 'prepared'}
            return data

    def busy(self):
        with self.lock:
            if any(t.is_alive() for t in [*self.workers.values(), *self.tool_workers]):
                return True
            with self.tasks.connect() as db:
                rows = db.execute('SELECT payload FROM delivery_runtime').fetchall()
            return any(json.loads(row['payload'])['state'] in {'running', 'queued', 'pausing', 'cancelling'} for row in rows)

    def close(self, timeout=5):
        """Stop owned work without replay, with a bounded shutdown wait."""
        deadline = time.monotonic() + max(0, timeout)
        self.closed = self.workflow.closed = True

        def signal():
            for event in [*self.events.copy().values(), *self.workflow.cancel_events.copy().values()]:
                event.set()
            for timer in self.timers.copy().values():
                timer.cancel()
            return [*self.workers.copy().values(), *self.tool_workers.copy(), *self.workflow.workers.copy().values()]

        # A busy lock or an unavailable database must never delay stopping owned
        # processes. Repeat under the locks to cover an in-progress handoff.
        workers = signal()
        try:
            with ExitStack() as locks:
                for lock in (self.workflow.lock, self.lock):
                    if not lock.acquire(timeout=max(0, deadline - time.monotonic())):
                        raise RuntimeError('关闭期间状态锁未能释放，中断记录尚未保存')
                    locks.callback(lock.release)
                workers.extend(signal())
                # The database may have been lost or damaged. Shutdown must
                # never create an empty replacement or lose process cleanup.
                with self.tasks.connect(create=False, timeout=0) as db:
                    rows = db.execute('SELECT initiative_id,revision,payload FROM delivery_runtime').fetchall()
                    db.execute("UPDATE delivery_tool_approvals SET status='revoked' WHERE status IN ('pending','allowed')")
                    for row in rows:
                        data = json.loads(row['payload'])
                        if data['state'] in {'running', 'queued', 'pausing', 'cancelling', 'awaiting_approval', 'resuming'}:
                            data.update(state='cancelling', requested_control='cancel', revision=row['revision'] + 1)
                            self.events.setdefault(row['initiative_id'], threading.Event()).set()
                            db.execute('UPDATE delivery_runtime SET revision=?,payload=? WHERE initiative_id=?',
                                       (data['revision'], _canonical(data), row['initiative_id']))
        except Exception as error:
            self.shutdown_errors.append(str(error))
            logging.getLogger(__name__).warning('Runtime shutdown could not persist state: %s', error)
        for worker in set(workers):
            if worker is not threading.current_thread() and worker.ident is not None:
                try:
                    worker.join(max(0, deadline - time.monotonic()))
                except RuntimeError as error:
                    self.shutdown_errors.append(str(error))
        return {'automatic_replay': False, 'workers_still_stopping': sum(t.is_alive() for t in set(workers)),
                'errors': list(self.shutdown_errors) + list(self.workflow.shutdown_errors)}

    def _require_open(self):
        if self.closed:
            raise ValueError('工作台正在关闭，不能启动或授权新的运行')

    @staticmethod
    def _require_fresh_recovery_plan(data, plan):
        if data.get('run_id') == plan['plan_id'] and (data.get('recovery_accounting') or data['budget'].get('usage_unavailable')):
            raise ValueError('中断方案不能再次启动；请核对候选并确认新的执行方案与预算')

    def select_profile(self, item_id, profile_id, actor, expected_revision):
        actor = self._actor(actor)
        if not self.sessions.composition(profile_id)['ready']:
            raise ValueError('Profile 缺少必要运行能力')
        with self.lock:
            data = self._load(item_id)
            workflow = self.workflow._load(item_id)
            if workflow['stage'] not in {'idle', 'clarifying', 'ready', 'rework', 'failed', 'interrupted', 'cancelled'}:
                raise ValueError('技术方案确认后不能更换 Profile；请重新讨论确认')
            if data['state'] in {'running', 'queued', 'pausing', 'cancelling'}:
                raise ValueError('运行期间不能更换 Profile')
            data['profile_id'] = profile_id
            data['profile_sha256'] = _profile_fingerprint(self.sessions.composition(profile_id))
            self._save(data, expected_revision)
        return self.view(item_id)

    def freeze_profile(self, item_id, plan):
        """Call during technical confirmation, before saving the frozen plan."""
        with self.lock:
            data = self._load(item_id)
            composition = self.sessions.composition(data['profile_id'])
            if not composition['ready']:
                raise ValueError('所选 Profile 尚未就绪，不能确认技术方案')
            sha = _profile_fingerprint(composition)
            frozen = {'id': data['profile_id'], 'sha256': sha,
                      'providers': {p['seam']: p['provider'] for p in composition['plugins']}}
            plan['profile'] = frozen
            plan['spec_text'] += '\n\n已确认运行 Profile：\n' + _canonical(frozen)
            data['profile_sha256'] = sha
            self._save(data)
            return plan

    def recover(self):
        """Called once by the process holding the service lease; never replay writes."""
        with self.lock, self.tasks.connect() as db:
            rows = db.execute('SELECT initiative_id FROM delivery_runtime').fetchall()
        recovered = []
        for row in rows:
            with self.lock:
                data = self._load(row['initiative_id'])
                if data['state'] in {'running', 'queued', 'pausing', 'cancelling', 'awaiting_approval', 'resuming'}:
                    self._recover_budget(data, time.time())
                    data.update(state='interrupted', error='服务中断；候选与记录保留，必须核对后重新授权')
                    self._save(data)
                    self._event(data, 'runtime/interrupted', 'harness', {'automatic_replay': False})
                    recovered.append(row['initiative_id'])
                    for child in data.get('subtasks', []):
                        if child.get('task_id'):
                            task = self.tasks.get(child['task_id'])
                            if task['status'] in {'queued', 'spec_ready', 'executing', 'evaluating'}:
                                self.tasks.transition(task['id'], 'failed', '服务中断；子任务禁止自动重放',
                                                      evidence={'automatic_replay': False})
        return recovered

    def _recover_budget(self, data, recovered_at):
        budget = data['budget']
        start = data.get('started_at')
        valid_start = (type(start) in (int, float) and math.isfinite(start)
                       and 0 < start <= recovered_at)
        # Wall-clock UTC survives process death. Include downtime and round up;
        # a missing start or backward clock cannot establish remaining time.
        elapsed = math.ceil(recovered_at - start) if valid_start else budget['time_budget_seconds']
        budget['elapsed_seconds'] = max(budget.get('elapsed_seconds', 0), elapsed)
        pending = {}
        event_error = None
        if data.get('session_id'):
            try:
                events = self.sessions.get_session(data['session_id'])['events']
                for event in events:
                    payload = event.get('payload') or {}
                    if event['kind'] not in {'tool/call', 'tool/result'}:
                        continue
                    key = (payload.get('tool_id'), payload.get('task_id'), payload.get('call_id'))
                    if event['kind'] == 'tool/call':
                        pending.setdefault(key, []).append(dict(payload, sequence=event['sequence']))
                    elif pending.get(key):
                        pending[key].pop(0)
            except Exception as error:
                event_error = str(error)
        elif data.get('task_id') or any(c.get('status') == 'running' for c in data.get('subtasks', [])):
            event_error = '缺少已执行任务的Session调用账目'
        unmatched = [call for calls in pending.values() for call in calls]
        if unmatched or event_error:
            budget['usage_unavailable'] = True
        data['recovery_accounting'] = {
            'recorded_start_utc_epoch_seconds': start,
            'observed_recovery_utc_epoch_seconds': recovered_at,
            'elapsed_seconds': budget['elapsed_seconds'],
            'method': 'conservative_utc_elapsed_including_downtime',
            'time_accounting_unavailable': not valid_start,
            'pending_calls': unmatched, 'event_error': event_error,
            'automatic_replay': False,
        }
        if data.get('resume_budget'):
            data['resume_budget'] = dict(budget)

    def _item_for_plan(self, plan_id):
        with self.tasks.connect() as db:
            rows = db.execute('SELECT id,payload FROM initiative_workflows').fetchall()
        for row in rows:
            if (json.loads(row['payload']).get('plan') or {}).get('plan_id') == plan_id:
                return row['id']
        raise ValueError('执行方案没有关联事项')

    def _begin(self, item_id, plan, *, state='running'):
        with self.lock:
            self._require_open()
            data = self._load(item_id)
            self._require_fresh_recovery_plan(data, plan)
            if data['state'] in {'running', 'queued', 'pausing', 'cancelling'}:
                raise ValueError('这个事项已有活动运行')
            composition = self.sessions.composition(data['profile_id'])
            profile_sha = _profile_fingerprint(composition)
            execution = next((p['provider'] for p in composition['plugins'] if p['seam'] == 'execution'), None)
            if not composition['ready'] or execution != 'codex':
                raise ValueError('所选 Profile 不允许编码交付，请选择 Codex 执行能力')
            if data.get('profile_sha256') and data['profile_sha256'] != profile_sha:
                raise ValueError('确认的 Profile 配置已变化，请重新选择并确认')
            frozen = plan.get('profile')
            if not frozen:
                raise ValueError('历史方案尚未冻结运行 Profile，请重新讨论确认')
            if frozen and (frozen['id'] != data['profile_id'] or frozen['sha256'] != profile_sha):
                raise ValueError('冻结方案的 Profile 已变化，请重新确认')
            project = plan.get('project')
            if project and self.projects.get(project['id'])['eval_command'] != project['eval_command']:
                raise ValueError('项目质量命令已变化，请重新确认执行方案')
            bounds = self.workflow._load(item_id).get('repair_loop_config') or {}
            budget = {'token_budget': bounds.get('token_budget', 30000), 'tokens_used': 0,
                      'time_budget_seconds': bounds.get('time_budget_seconds', 900),
                      'elapsed_seconds': 0, 'max_workers': data.get('max_workers', 2)}
            previous_budget = data.pop('resume_budget', None)
            if previous_budget:
                budget.update(previous_budget)
                if budget.get('usage_unavailable') or budget['tokens_used'] >= budget['token_budget'] or budget['elapsed_seconds'] >= budget['time_budget_seconds']:
                    raise ValueError('旧预算耗尽或存在不可核验调用，请确认新的执行方案与预算')
            if type(budget['max_workers']) is not int or not 2 <= budget['max_workers'] <= 4:
                raise ValueError('冻结预算的并发worker数量必须是2至4的整数')
            item = self.initiatives.get(item_id)
            session = self.sessions.create_session(item['project_id'], item.get('goal') or item.get('title') or item_id,
                                                   plan['actor'], data['profile_id'])
            self._require_open()
            data.update(run_id=plan['plan_id'], state=state, session_id=session['id'], task_id=None,
                        workspace=None, started_at=time.time() - budget['elapsed_seconds'], error='', evidence=[], budget=budget,
                        profile_sha256=profile_sha, control_revision=data['control_revision'] + 1)
            data.pop('requested_control', None)
            data.pop('recovery_accounting', None)
            self.events[item_id] = threading.Event()
            old_timer = self.timers.pop(item_id, None)
            if old_timer:
                old_timer.cancel()
            timer = threading.Timer(max(.01, budget['time_budget_seconds'] - budget['elapsed_seconds']), self._time_exhausted, args=(item_id, plan['plan_id']))
            timer.daemon = True
            self.timers[item_id] = timer
            timer.start()
            self._save(data)
            self._event(data, 'runtime/start', plan['actor'], {'run_id': data['run_id'], 'profile_id': data['profile_id'], 'profile_sha256': profile_sha})
            return data

    def _time_exhausted(self, item_id, run_id):
        with self.lock:
            data = self._load(item_id)
            if data['run_id'] == run_id and data['state'] in {'running', 'queued', 'awaiting_approval'}:
                data.update(error='事项总时间预算已耗尽', budget_exhausted=True)
                self.events[item_id].set()
                self._save(data)
                self._event(data, 'runtime/budget-stopped', 'harness', {'reason': 'time_budget', 'automatic_replay': False})

    def _budget(self, item_id):
        with self.lock:
            data = self._load(item_id)
            budget = data['budget']
            if time.time() - data['started_at'] >= budget['time_budget_seconds']:
                raise RuntimeError('事项总时间预算已耗尽')
            if budget['tokens_used'] >= budget['token_budget']:
                raise RuntimeError('事项总 Token 预算已耗尽，不启动下一次调用')
            if budget.get('usage_unavailable'):
                raise RuntimeError('上一模型调用缺少可核验 Token 用量，不再启动后续模型调用')
            if self.events[item_id].is_set():
                raise RuntimeError('运行已被暂停或取消，候选与证据保留')
            return max(1, int(budget['time_budget_seconds'] - (time.time() - data['started_at'])))

    def _runner(self, item_id, workspace, runtime):
        runner = self.runner_factory(workspace, runtime)
        runner.control_event = self.events[item_id]
        def execute(task, **kwargs):
            remaining = self._budget(item_id)
            bounded = dict(task, execution_timeout_seconds=min(task.get('execution_timeout_seconds', 900), max(30, remaining)),
                           _runtime_timeout_seconds=remaining)
            with self.lock:
                data = self._load(item_id)
                call_id = secrets.token_hex(12)
                self._event(data, 'tool/call', 'agent:coder', {'tool_id': 'codex.exec', 'task_id': task['id'], 'call_id': call_id, 'scope': task.get('write_scope'), 'authorization': 'frozen_task_scope'})
            result = runner(bounded, **kwargs)
            with self.lock:
                data = self._load(item_id)
                usage = (result.get('usage') or {}).get('total_tokens')
                if isinstance(usage, int) and usage > 0:
                    data['budget']['tokens_used'] += usage
                else:
                    data['budget']['usage_unavailable'] = True
                self._save(data)
                self._event(data, 'tool/result', 'agent:coder', {'tool_id': 'codex.exec', 'task_id': task['id'], 'call_id': call_id, 'success': result.get('success'), 'usage': result.get('usage'), 'artifacts': result.get('artifacts')})
            return result
        return execute

    def submit_daily(self, repository, runtime, tasks, plan, on_task_created):
        from .execution_control import local
        item_id = self._item_for_plan(plan['plan_id'])
        self._begin(item_id, plan)
        repository = resolve_reference(repository, self.runtime)
        previous_event = getattr(local, 'cancel_event', None)
        local.cancel_event = self.events[item_id]
        def created(task):
            with self.lock:
                data = self._load(item_id)
                data.update(task_id=task['id'], workspace=str(self.runtime / 'daily-delivery' / plan['plan_id'] / 'workspace'))
                self.sessions.bind_task(data['session_id'], task['id'], plan['actor'])
                self._save(data)
            on_task_created(task)
        try:
            output = submit_daily(repository, runtime, tasks, plan, created,
                                  runner_factory=lambda w, r: self._runner(item_id, w, r))
            with self.lock:
                data = self._load(item_id)
                requested = 'cancel' if self.closed else data.get('requested_control')
                if requested:
                    data['requested_control'] = requested
                data['budget']['elapsed_seconds'] = int(time.time() - data['started_at'])
                data['state'] = 'paused' if requested == 'pause' else 'cancelled' if requested == 'cancel' else output['task']['status']
                data['error'] = output['task'].get('error') or ''
                self.sessions.sync_task(data['session_id'], output['task'])
                self._save(data)
                self._event(data, 'runtime/end', 'harness', {'state': data['state'], 'task_id': data['task_id']})
            return output
        except Exception as exc:
            with self.lock:
                data = self._load(item_id)
                requested = 'cancel' if self.closed else data.get('requested_control')
                if requested:
                    data['requested_control'] = requested
                data['budget']['elapsed_seconds'] = int(time.time() - data['started_at'])
                data.update(state='paused' if requested == 'pause' else 'cancelled' if requested == 'cancel' else 'failed', error=str(exc))
                self._save(data)
                self._event(data, 'runtime/end', 'harness', {'state': data['state'], 'error': str(exc)})
            raise
        finally:
            local.cancel_event = previous_event
            timer = self.timers.pop(item_id, None)
            if timer:
                timer.cancel()

    def control(self, item_id, action, actor, expected_revision, candidate_sha256='', *,
                run_id=None, session_id=None, control_revision=None):
        actor = self._actor(actor)
        if action not in {'pause', 'resume', 'cancel'}:
            raise ValueError('运行操作必须是 pause、resume 或 cancel')
        with self.workflow.lock, self.lock:
            self._require_open()
            data = self._load(item_id)
            if type(expected_revision) is not int or expected_revision < 0:
                raise ValueError('运行版本必须是非负整数')
            bound_stop = action in {'pause', 'cancel'} and any(
                value is not None for value in (run_id, session_id, control_revision))
            if bound_stop:
                if (not isinstance(run_id, str) or not run_id or run_id != data['run_id']
                        or not isinstance(session_id, str) or not session_id or session_id != data.get('session_id')
                        or type(control_revision) is not int or control_revision < 0
                        or control_revision != data['control_revision']):
                    raise ValueError('运行控制版本或身份已变化，请刷新后核对本次运行')
            elif data['revision'] != expected_revision:
                raise ValueError('运行版本已变化，请刷新后重试')
            if action == 'resume':
                if data['state'] not in {'paused', 'interrupted'}:
                    raise ValueError('当前运行不能恢复')
                if data['budget'].get('usage_unavailable'):
                    raise ValueError('中断调用缺少可核验Token用量，不能恢复旧预算；候选已保留，请重新讨论并确认新的执行预算')
                if data['budget']['tokens_used'] >= data['budget']['token_budget'] or data['budget']['elapsed_seconds'] >= data['budget']['time_budget_seconds']:
                    raise ValueError('原执行预算已耗尽；候选已保留，请重新讨论并确认新的执行预算')
                current_hash = self._candidate_hash(data)
                if not current_hash or candidate_sha256 != current_hash:
                    raise ValueError('恢复前必须核对当前候选 SHA-256')
                # Resume is explicit fresh authorization from the preserved candidate.
                with self.workflow.lock:
                    work = self.workflow._load(item_id)
                    old = work.get('plan')
                    if not old:
                        raise ValueError('缺少冻结方案，请重新讨论确认')
                    source = resolve_reference(data['workspace'], self.runtime)
                    plan = prepare_daily(source, self.runtime, actor, old['request'], old['acceptance'], old['write_scope'], project=old.get('project'))
                    plan.update(plan_id=secrets.token_hex(18), expires_at=time.time() + 900,
                                document_version=old.get('document_version'))
                    plan['spec_text'] = old['spec_text'] + '\n\n本次恢复授权：' + actor + '；保留候选 SHA-256：' + current_hash
                    if old.get('profile'):
                        plan['profile'] = old['profile']
                    if old.get('learning_binding_id'):
                        binding = self.workflow.learning.bind(item_id, plan['plan_id'], work.get('learning_decision'), source, actor)
                        if binding:
                            plan['learning_binding_id'] = binding['id']
                    work.update(stage='confirmed', workspace=str(source), plan=plan)
                    self.workflow._save(work)
                    workflow_revision = work['revision']
                resume_budget = dict(data['budget'])
                data.update(state='resuming', requested_control=None, resume_budget=resume_budget)
                self._save(data)
                self._event(data, 'runtime/resume-authorized', actor, {'candidate_sha256': current_hash, 'new_plan_id': plan['plan_id'], 'previous_run_id': data['run_id']})
            else:
                if data['state'] not in {'running', 'queued', 'awaiting_approval', 'pausing', 'paused', 'interrupted', 'prepared'}:
                    raise ValueError('当前运行不能暂停或取消')
                active = data['state'] in {'running', 'queued', 'pausing'}
                data.update(state=('pausing' if action == 'pause' else 'cancelling') if active else ('paused' if action == 'pause' else 'cancelled'), requested_control=action,
                            control_revision=data['control_revision'] + 1)
                self.events.setdefault(item_id, threading.Event()).set()
                with self.tasks.connect() as db:
                    db.execute("UPDATE delivery_tool_approvals SET status='revoked' WHERE run_id=? AND status IN ('pending','allowed')", (data['run_id'],))
                self._save(data)
                self._event(data, 'runtime/control', actor, {'action': action})
        if action == 'resume':
            self.workflow.execute(item_id, actor, workflow_revision)
        # Stop has already committed and signalled its worker. Its response must
        # not start Git in that same cancelled context or advertise an old hash.
        return self.view(item_id, include_candidate=action == 'resume')

    def authorize_tool(self, context, spec, args, call_id, actor):
        item_id = context.get('initiative_id')
        if not item_id:
            return False, 'missing_delivery_binding'
        with self.lock:
            if self.closed:
                return False, 'runtime_closed'
            data = self._load(item_id)
            valid_tasks = {data.get('task_id')} | {c.get('task_id') for c in data.get('subtasks', [])}
            if not data.get('run_id') or context['task']['id'] not in valid_tasks or data['state'] in {'cancelled', 'cancelling', 'interrupted'}:
                return False, 'invalid_delivery_binding'
            if data['state'] in {'paused', 'pausing'} or context['task'].get('status') == 'completed':
                return False, 'delivery_stopped_or_accepted'
            profile_sha = _profile_fingerprint(self.sessions.composition(data['profile_id']))
            if profile_sha != data.get('profile_sha256'):
                return False, 'frozen_profile_changed'
            binding = self.task_context(context['task']['id'])
            candidate_sha = _digest(manifest(binding['workspace'], self.runtime)) if binding.get('workspace') else ''
            if not candidate_sha:
                return False, 'missing_candidate_binding'
            payload = {'tool_id': spec.id, 'args': args, 'args_sha256': _digest(args),
                       'candidate_sha256': candidate_sha, 'profile_sha256': profile_sha,
                       'task_id': context['task']['id'], 'call_id': call_id}
            fingerprint = _digest({k: payload[k] for k in ('tool_id', 'args_sha256', 'candidate_sha256', 'profile_sha256', 'task_id', 'call_id')})
            with self.tasks.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT id,status FROM delivery_tool_approvals WHERE run_id=? AND fingerprint=?', (data['run_id'], fingerprint)).fetchone()
                if row and row['status'] == 'allowed':
                    updated = db.execute("UPDATE delivery_tool_approvals SET status='consumed' WHERE id=? AND status='allowed'", (row['id'],))
                    return updated.rowcount == 1, 'named_allow_once'
                if row:
                    return False, 'approval_' + row['status']
                identifier = 'APPROVAL-' + secrets.token_hex(8).upper()
                db.execute('INSERT INTO delivery_tool_approvals VALUES (?,?,?,?,?)', (identifier, data['run_id'], fingerprint, 'pending', _canonical(payload)))
            self._save(data)
            self._event(data, 'approval/requested', actor, payload | {'id': identifier})
            return False, 'approval_required'

    def task_context(self, task_id):
        with self.tasks.connect() as db:
            rows = db.execute('SELECT initiative_id,payload FROM delivery_runtime').fetchall()
        for row in rows:
            data = json.loads(row['payload'])
            if data.get('task_id') == task_id:
                workspace = data.get('workspace')
                return {'initiative_id': row['initiative_id'], 'workspace': str(resolve_reference(workspace, self.runtime)) if workspace else None}
            child = next((c for c in data.get('subtasks', []) if c.get('task_id') == task_id), None)
            if child:
                workspace = child.get('workspace')
                return {'initiative_id': row['initiative_id'], 'workspace': str(resolve_reference(workspace, self.runtime)) if workspace else None}
        raise ValueError('任务没有绑定工作台受控运行')

    def decide_approval(self, item_id, approval_id, decision, actor, expected_revision, note=''):
        actor = self._actor(actor)
        if decision not in {'allow', 'deny'} or not isinstance(note, str) or not note.strip():
            raise ValueError('请选择 allow/deny 并填写授权理由')
        with self.lock:
            self._require_open()
            data = self._load(item_id)
            if data['revision'] != expected_revision:
                raise ValueError('运行版本已变化')
            with self.tasks.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM delivery_tool_approvals WHERE id=? AND run_id=?', (approval_id, data['run_id'])).fetchone()
                if not row or row['status'] != 'pending':
                    raise ValueError('授权请求不存在或已处理')
                payload = json.loads(row['payload'])
                binding = self.task_context(payload['task_id'])
                candidate_sha = _digest(manifest(binding['workspace'], self.runtime)) if binding.get('workspace') else ''
                if payload['candidate_sha256'] != candidate_sha:
                    raise ValueError('候选已变化，旧授权不可放行')
                if payload.get('profile_sha256') != _profile_fingerprint(self.sessions.composition(data['profile_id'])):
                    raise ValueError('冻结Profile已变化，旧授权不可放行')
                payload.update(actor=actor, note=note.strip())
                db.execute('UPDATE delivery_tool_approvals SET status=?,payload=? WHERE id=?', ('allowed' if decision == 'allow' else 'denied', _canonical(payload), approval_id))
            self._save(data)
            self._event(data, 'approval/decided', actor, payload | {'decision': decision})
            if decision == 'allow' and self.tool_executor is not None:
                # Register and start while holding the shutdown admission lock.
                # close() must never observe an unstarted worker to join.
                thread = threading.Thread(target=self._execute_approved_tool,
                                          args=(item_id, approval_id, payload, actor), daemon=True)
                self.tool_workers.add(thread)
                try:
                    thread.start()
                except BaseException:
                    self.tool_workers.discard(thread)
                    with self.tasks.connect() as db:
                        db.execute("UPDATE delivery_tool_approvals SET status='revoked' WHERE id=?", (approval_id,))
                    raise
        return self.view(item_id)

    def _execute_approved_tool(self, item_id, approval_id, payload, actor):
        from .maintenance import MaintenanceGate
        try:
            with self.lock:
                self._require_open()
            with MaintenanceGate(self.runtime).write():
                result = self.tool_executor(item_id, payload, actor)
        except Exception as exc:
            result = {'success': False, 'error': str(exc)}
        with self.lock:
            try:
                with self.tasks.connect(create=False) as db:
                    row = db.execute('SELECT payload FROM delivery_tool_approvals WHERE id=?', (approval_id,)).fetchone()
                    stored = json.loads(row['payload'])
                    stored['result'] = result
                    db.execute('UPDATE delivery_tool_approvals SET payload=? WHERE id=?', (_canonical(stored), approval_id))
            except Exception as error:
                self.shutdown_errors.append(str(error))
                logging.getLogger(__name__).warning('Approved tool result could not be persisted: %s', error)
            finally:
                self.tool_workers.discard(threading.current_thread())

    def prepare_subtasks(self, item_id, actor, expected_revision, subtasks, *, max_workers=2):
        actor = self._actor(actor)
        if type(max_workers) is not int or not 2 <= max_workers <= 4:
            raise ValueError('并发worker数量必须是2至4的整数')
        with self.workflow.lock, self.lock:
            self._require_open()
            data = self._load(item_id)
            if data['revision'] != expected_revision:
                raise ValueError('运行版本已变化')
            if data['state'] in {'running', 'queued', 'pausing', 'cancelling', 'resuming'} or (self.workers.get(item_id) and self.workers[item_id].is_alive()):
                raise ValueError('分工已授权或正在运行，不能重新准备并发数量或子任务')
            work = self.workflow._load(item_id)
            if work['stage'] != 'confirmed' or not work.get('plan'):
                raise ValueError('请先确认技术方案，再准备分工')
            self._require_fresh_recovery_plan(data, work['plan'])
            if not isinstance(subtasks, list) or not 2 <= len(subtasks) <= 4:
                raise ValueError('每次分工须为 2..4 个子任务')
            source = resolve_reference(work.get('workspace') or self.workflow.repository_for(item_id), self.runtime)
            baseline = manifest(source, self.runtime)
            if baseline != work['plan']['source_manifest']:
                raise ValueError('分工依据的源码已变化，请重新确认')
            contracts, scheduled = [], []
            for raw in subtasks:
                if not isinstance(raw, dict) or not isinstance(raw.get('prompt'), str) or not raw['prompt'].strip() or len(raw['prompt']) > 10000:
                    raise ValueError('每个子任务须有名称和具体任务')
                for field in ('read_set', 'write_set', 'resource_set'):
                    if not isinstance(raw.get(field, []), list) or any(not isinstance(v, str) for v in raw.get(field, [])):
                        raise ValueError('子任务读写集与资源须为路径或标识字符串数组')
                writes = normalize_write_scope(raw.get('write_set', []))
                if any(not CodexExecutionRunner._allowed(path, work['plan']['write_scope']) for path in writes):
                    raise ValueError('子任务写集超出父事项授权')
                item = Subtask(str(raw.get('name') or '').strip(), tuple(writes), tuple(raw.get('read_set', [])), tuple(raw.get('resource_set', [])), _digest(baseline))
                scheduled.append(item)
                contracts.append({'id': 'SUBTASK-' + secrets.token_hex(6).upper(), 'name': item.name,
                                  'prompt': raw['prompt'].strip(), 'read_set': list(item.read_set), 'write_set': writes,
                                  'resource_set': list(item.resource_set), 'status': 'planned', 'task_id': None,
                                  'workspace': None, 'evidence': [], 'error': ''})
            assert_parallel_safe(scheduled)
            data.update(state='prepared', subtasks=contracts, subtask_plan_id='SUBAGENT-' + secrets.token_hex(6).upper(),
                        prepared_plan_id=work['plan']['plan_id'], source_sha256=_digest(baseline),
                        profile_sha256=_profile_fingerprint(self.sessions.composition(data['profile_id'])),
                        max_workers=max_workers, managed_execution=True, native_execution=False)
            data['budget']['max_workers'] = max_workers
            self._save(data)
        return self.view(item_id)

    def start_subtasks(self, item_id, actor, expected_revision, submission_key, plan_id=None):
        actor = self._actor(actor)
        if not submission_key or len(submission_key) > 200:
            raise ValueError('启动分工须提供 submission_key')
        with self.lock:
            self._require_open()
            data = self._load(item_id)
            if data.get('submission_key') == submission_key and data.get('subtask_plan_id') == plan_id:
                return self.view(item_id)
            if data['revision'] != expected_revision or data['state'] != 'prepared':
                raise ValueError('分工计划已变化或不能启动')
            if plan_id and plan_id != data['subtask_plan_id']:
                raise ValueError('分工计划编号不一致')
            if not self.workflow.enabled:
                raise ValueError('当前工作台未启用代码执行')
            work = self.workflow._load(item_id)
            if (work.get('plan') or {}).get('plan_id') != data['prepared_plan_id'] or work['stage'] != 'confirmed':
                raise ValueError('本事项技术方案已变化')
            self._require_fresh_recovery_plan(data, work['plan'])
            data.update(submission_key=submission_key, state='queued')
            self._save(data)
            thread = threading.Thread(target=self._run_subtasks, args=(item_id, actor), daemon=True)
            self.workers[item_id] = thread
            thread.start()
        return self.view(item_id)

    def _run_subtasks(self, item_id, actor):
        from .maintenance import MaintenanceGate
        try:
            with MaintenanceGate(self.runtime).write():
                self._run_subtasks_owned(item_id, actor)
        finally:
            timer = self.timers.pop(item_id, None)
            if timer:
                timer.cancel()

    def _run_subtasks_owned(self, item_id, actor):
        work = self.workflow._load(item_id)
        plan = work['plan']
        source = resolve_reference(work.get('workspace') or self.workflow.repository_for(item_id), self.runtime)
        with self.lock:
            prior = self._load(item_id)
            if prior['state'] in {'cancelled', 'paused', 'cancelling', 'pausing'}:
                if prior['state'] in {'cancelling', 'pausing'}:
                    prior['state'] = 'cancelled' if prior.get('requested_control') == 'cancel' else 'paused'
                    self._save(prior)
                return
            contracts = prior['subtasks']
            # _begin refuses an actually running owner, but queued here is ours.
            prior['state'] = 'prepared'
            self._save(prior)
            data = self._begin(item_id, plan)
        from .execution_control import local, checkpoint
        previous_event = getattr(local, 'cancel_event', None)
        local.cancel_event = self.events[item_id]
        folder = self.runtime / 'daily-delivery' / plan['plan_id']
        try:
            checkpoint()
            if manifest(source, self.runtime) != plan['source_manifest']:
                raise ValueError('启动分工时源码已变化')
            parent = folder / 'workspace'
            self._snapshot(source, parent, plan['source_manifest'],
                git_runner=lambda command, **options: self._git(item_id, 'parent.snapshot', command, **options))
            spec = folder / 'SPEC.md'
            spec.write_text(plan['spec_text'], encoding='utf-8')
            task = self.tasks.create(plan['request'], actor=actor, spec_path=str(spec), execution_mode='codex',
                                     write_scope=plan['write_scope'], business_refs=['PROJECT:' + plan['project']['id']])
            self.tasks.append_event(task['id'], '本次需求 Spec 已冻结', actor=actor,
                                    evidence={'sha256': hashlib.sha256(spec.read_bytes()).hexdigest()})
            self.tasks.append_event(task['id'], '网页具名授权日常研发', actor=actor,
                                    evidence={'initiative_id': item_id, 'plan_id': plan['plan_id'], 'managed_subtasks': True})
            with self.workflow.lock, self.lock:
                data = self._load(item_id)
                data.update(task_id=task['id'], workspace=str(parent))
                self.sessions.bind_task(data['session_id'], task['id'], actor)
                self._save(data)
                current = self.workflow._load(item_id)
                current.update(stage='executing', active_task_id=task['id'])
                current.setdefault('iterations', []).append({'task_id': task['id'], 'plan_id': plan['plan_id'], 'at': time.time()})
                self.workflow._save(current)
            binding_id = plan.get('learning_binding_id')
            if binding_id:
                learning = self.workflow.learning
                binding = learning.validate_binding(binding_id, parent)
                learning.attach(binding_id, task['id'])
                try:
                    contract = learning.check_phase(binding_id, 'precheck', parent)
                except (ValueError, OSError) as exc:
                    learning.run_event(binding_id, 'precheck', {'passed': False, 'workspace': str(parent),
                        'error': str(exc), 'stopped_phase': 'precheck', 'recovery': 'retain_candidate_new_plan'})
                    raise
                learning.run_event(binding_id, 'precheck', {'passed': True, 'workspace': str(parent), 'baseline': plan['source_sha256'],
                    'checks': [a['prepared'] for a in binding['assets'] if a['prepared']], 'phase_contract': contract})
            before = CandidateProjectEval(parent, self.runtime, task['id'], plan['project']['eval_command'],
                                          'subtasks-before', timeout=self._budget(item_id))()
            self.tasks.append_event(task['id'], '日常研发执行前检查', actor=actor, evidence=before)
            intervals = []
            def child(contract):
                from .execution_control import local
                local.cancel_event = self.events[item_id]
                self._budget(item_id)
                workspace = folder / 'subtasks' / contract['id'] / 'workspace'
                self._snapshot(source, workspace, plan['source_manifest'],
                    git_runner=lambda command, **options:
                        self._git(item_id, 'subtask.' + contract['id'] + '.snapshot', command, **options))
                child_spec = workspace.parent / 'SPEC.md'
                child_spec.write_text(plan['spec_text'] + '\n\n子任务：' + contract['prompt'], encoding='utf-8')
                # Read-only workers still use a real CLI process and independent output.
                child_task = self.tasks.create(contract['prompt'], actor=actor, spec_path=str(child_spec),
                    execution_mode='codex' if contract['write_set'] else 'verify', write_scope=contract['write_set'],
                    business_refs=['PROJECT:' + plan['project']['id']])
                self.tasks.append_event(child_task['id'], '本次需求 Spec 已冻结', actor=actor,
                                        evidence={'sha256': hashlib.sha256(child_spec.read_bytes()).hexdigest()})
                started = time.time()
                self._update_child(item_id, contract['id'], status='running', task_id=child_task['id'], workspace=str(workspace), started_at=started)
                runner = self._runner(item_id, workspace, self.runtime)
                if contract['write_set']:
                    outcome = run_task(self.tasks, child_task['id'], actor, execution_runner=runner,
                        suite_runner=CandidateProjectEval(workspace, self.runtime, child_task['id'], plan['project']['eval_command'], 'subtask', timeout=self._budget(item_id)))
                else:
                    readonly = self.runner_factory(workspace, self.runtime)
                    readonly.control_event = self.events[item_id]
                    from .codex_options import headless_options
                    command = [readonly.executable, 'exec', *headless_options(), '--json', '--sandbox', 'read-only', '--ephemeral', '--cd', str(workspace), '-']
                    before = manifest(workspace, self.runtime)
                    call_id = secrets.token_hex(12)
                    with self.lock:
                        current = self._load(item_id)
                        self._event(current, 'tool/call', 'agent:inspector', {'tool_id': 'codex.exec',
                            'task_id': child_task['id'], 'call_id': call_id, 'scope': [], 'authorization': 'frozen_read_only_subtask'})
                    process = readonly._run_codex_streaming(command, contract['prompt'], self._budget(item_id), lambda line: None, time.monotonic())
                    receipt = workspace.parent / 'process.json'
                    receipt.write_text(_canonical({'command': command, 'returncode': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}), encoding='utf-8')
                    usage = readonly._parse_events(process.stdout).get('usage') or {}
                    unchanged = before == manifest(workspace, self.runtime)
                    with self.lock:
                        current = self._load(item_id)
                        measured = usage.get('total_tokens')
                        if isinstance(measured, int) and measured > 0:
                            current['budget']['tokens_used'] += measured
                        else:
                            current['budget']['usage_unavailable'] = True
                        self._save(current)
                        self._event(current, 'tool/result', 'agent:inspector', {'tool_id': 'codex.exec',
                            'task_id': child_task['id'], 'call_id': call_id, 'usage': usage,
                            'success': process.returncode == 0 and unchanged, 'artifacts': {'process': str(receipt)}})
                    if process.returncode or not unchanged:
                        raise RuntimeError('只读子任务执行失败或修改了候选')
                    outcome = run_task(self.tasks, child_task['id'], actor,
                        execution_runner=lambda _: {'success': True, 'mode': 'verification_only', 'receipt_path': str(receipt), 'usage': usage},
                        suite_runner=CandidateProjectEval(workspace, self.runtime, child_task['id'], plan['project']['eval_command'], 'subtask-inspection', timeout=self._budget(item_id)))
                ended = time.time()
                if outcome['status'] != 'review':
                    raise RuntimeError('子任务检查未通过：' + contract['name'])
                patch = self._patch(workspace, contract['write_set'],
                    git_runner=lambda command, **options:
                        self._git(item_id, 'subtask.' + contract['id'] + '.patch', command, **options))
                patch_path = workspace.parent / 'changes.patch'
                patch_path.write_bytes(patch)
                self._update_child(item_id, contract['id'], status='completed', ended_at=ended,
                                   evidence=[{'path': str(patch_path), 'sha256': hashlib.sha256(patch).hexdigest()}])
                return contract, patch, started, ended
            results = []
            with ThreadPoolExecutor(max_workers=data['budget']['max_workers'], thread_name_prefix='delivery-subtask') as pool:
                futures = {pool.submit(child, c): c for c in contracts}
                for future in as_completed(futures):
                    contract = futures[future]
                    try:
                        result = future.result()
                        results.append(result)
                        intervals.append(result[2:])
                    except Exception as exc:
                        self._update_child(item_id, contract['id'], status='failed', ended_at=time.time(), error=str(exc))
                        self.events[item_id].set()
                        raise
            self._budget(item_id)
            for contract, patch, _, _ in sorted(results, key=lambda r: r[0]['name']):
                if patch:
                    patch_input = folder / 'subtasks' / contract['id'] / 'changes.patch'
                    for phase, flags in (('parent.apply-check', ['--check']), ('parent.apply', [])):
                        if patch_input.read_bytes() != patch:
                            raise ValueError('子任务补丁文件已变化，未汇总到父候选')
                        self._git(item_id, phase, ['git', 'apply', *flags, str(patch_input)], cwd=parent)
            postmerge = manifest(parent, self.runtime)
            changed = sorted(p for p in set(plan['source_manifest']) | set(postmerge)
                             if plan['source_manifest'].get(p) != postmerge.get(p))
            if not changed or any(not CodexExecutionRunner._allowed(p, plan['write_scope']) for p in changed):
                raise ValueError('汇总候选无实际改动或超出授权')
            evidence = {'success': True, 'mode': 'codex_exec', 'changed_files': changed, 'managed_subtasks': True,
                        'overlap_proved': any(max(a[0], b[0]) < min(a[1], b[1]) for i, a in enumerate(intervals) for b in intervals[i+1:])}
            patch_path = folder / 'changes.patch'
            patch_path.write_bytes(self._patch(parent, plan['write_scope'],
                git_runner=lambda command, **options: self._git(item_id, 'parent.patch', command, **options)))
            if binding_id:
                actual_diff = {'path': str(patch_path), 'sha256': hashlib.sha256(patch_path.read_bytes()).hexdigest()}
                try:
                    contract = learning.check_phase(binding_id, 'implement', parent)
                except (ValueError, OSError) as exc:
                    learning.run_event(binding_id, 'implement', {'passed': False, 'changed_files': changed,
                        'binding_sha256': binding['sha256'], 'diff_artifact': actual_diff, 'error': str(exc),
                        'stopped_phase': 'implement', 'recovery': 'retain_candidate_new_plan'})
                    raise
                learning.run_event(binding_id, 'implement', {'passed': True, 'changed_files': changed, 'binding_sha256': binding['sha256'],
                    'phase_contract': contract, 'diff_artifact': actual_diff})
            result = run_task(self.tasks, task['id'], actor, execution_runner=lambda _: evidence,
                              suite_runner=CandidateProjectEval(parent, self.runtime, task['id'], plan['project']['eval_command'], 'subtasks-final', timeout=self._budget(item_id)))
            # run_task returns a failed task when its Eval was cancelled. Keep
            # that evidence, but let the runtime preserve the requested control.
            self._budget(item_id)
            if binding_id:
                contract = None
                if result['status'] == 'review':
                    try:
                        contract = learning.check_phase(binding_id, 'eval', parent)
                    except (ValueError, OSError) as exc:
                        self.tasks.transition(task['id'], 'rework', '汇总候选未满足采用流程的Eval阶段合同', actor=actor, error=str(exc))
                        learning.run_event(binding_id, 'eval', {'passed': False, 'error': str(exc),
                            'stopped_phase': 'eval', 'recovery': 'retain_candidate_new_plan'})
                        raise
                report = result.get('result') or {}
                learning.run_event(binding_id, 'eval', {'passed': result['status'] == 'review', 'summary': report.get('summary'),
                    'report_path': report.get('runner', {}).get('report_path') or report.get('report_path'),
                    'report_sha256': report.get('report_sha256'), 'candidate_manifest': manifest(parent, self.runtime), 'phase_contract': contract})
                learning.finish(task['id'])
            self.tasks.append_event(task['id'], '日常研发交付包已保存', actor=actor, evidence={'workspace': str(parent), 'patch_path': str(patch_path),
                'patch_sha256': hashlib.sha256(patch_path.read_bytes()).hexdigest(), 'source_sha256': plan['source_sha256'], 'status': result['status'], 'merged': False})
            with self.workflow.lock, self.lock:
                # Control and close take these locks too; they cannot be
                # overwritten by a late successful Eval publishing review.
                self._budget(item_id)
                data = self._load(item_id)
                data['budget']['elapsed_seconds'] = int(time.time() - data['started_at'])
                data.update(state=result['status'], evidence=[{'path': str(patch_path), 'sha256': hashlib.sha256(patch_path.read_bytes()).hexdigest()}])
                self._save(data)
                current = self.workflow._load(item_id)
                current.update(stage='review' if result['status'] == 'review' else 'rework', workspace=str(parent), candidate_manifest=manifest(parent, self.runtime))
                self.workflow._save(current)
                self.sessions.sync_task(data['session_id'], result)
        except Exception as exc:
            with self.workflow.lock, self.lock:
                data = self._load(item_id)
                requested = 'cancel' if self.closed else data.get('requested_control')
                if requested:
                    data['requested_control'] = requested
                data['budget']['elapsed_seconds'] = int(time.time() - data['started_at']) if data.get('started_at') else 0
                data.update(state='paused' if requested == 'pause' else 'cancelled' if requested == 'cancel' else 'failed', error=str(exc))
                self._save(data)
                current = self.workflow._load(item_id)
                current.update(stage='interrupted' if requested == 'pause' else 'cancelled' if requested == 'cancel' else 'failed', error=str(exc))
                if (folder / 'workspace/.git/HEAD').is_file():
                    current['workspace'] = str(folder / 'workspace')
                self.workflow._save(current)
                if data.get('task_id'):
                    task = self.tasks.get(data['task_id'])
                    if task['status'] in {'queued', 'spec_ready', 'executing', 'evaluating', 'review', 'rework'}:
                        self.tasks.transition(task['id'], 'failed', '分工运行中断，证据保留', error=str(exc))
                    self.workflow.learning.finish(task['id'], note=str(exc))
                self._event(data, 'runtime/end', 'harness', {'state': data['state'], 'error': str(exc)})
        finally:
            local.cancel_event = previous_event

    def _git(self, item_id, phase, command, *, cwd, capture_output=True, check=True):
        """Bound each internal Git step by this run's remaining time, not tokens."""
        with self.lock:
            self._require_open()
            data = self._load(item_id)
            event = self.events[item_id]
            if event.is_set():
                raise RuntimeError('运行已被暂停或取消，候选与证据保留')
            remaining = data['budget']['time_budget_seconds'] - (time.time() - data['started_at'])
            if remaining <= 0:
                raise RuntimeError('事项总时间预算已耗尽')
            step_started = time.monotonic()
            call_id = secrets.token_hex(12)
            context = {'call_id': call_id, 'initiative_id': item_id, 'run_id': data['run_id'],
                       'task_id': data.get('task_id'), 'phase': phase}
            self._event(data, 'runtime/git-start', 'harness', context | {'command': list(command)})
        folder = self.runtime / 'daily-delivery' / context['run_id'] / 'git-processes' / call_id
        failure = None
        try:
            return self._git_process(command, cwd=cwd, timeout=remaining - (time.monotonic() - step_started), control_event=event,
                receipt_dir=folder, context=context, capture_output=capture_output, check=check)
        except BaseException as error:
            failure = error
            raise
        finally:
            # These are process events, not model calls: recovery must not count
            # an interrupted Git step as an unmeasured token-consuming call.
            try:
                from .file_io import read_bytes
                receipt_path = folder / 'process.json'
                receipt_bytes = read_bytes(receipt_path)
                receipt = json.loads(receipt_bytes.decode('utf-8'))
                evidence = {key: receipt[key] for key in ('command', 'returncode', 'interrupted', 'success')}
                evidence.update(context, path=str(receipt_path), sha256=hashlib.sha256(receipt_bytes).hexdigest())
                with self.lock:
                    self._event(self._load(item_id), 'runtime/git-result', 'harness', evidence)
            except Exception as persistence_error:
                if failure is None:
                    raise
                failure.add_note('Git进程证据保存失败：' + str(persistence_error))

    @staticmethod
    def _git_process(command, *, cwd, timeout=120, control_event=None, receipt_dir=None,
                     context=None, capture_output=True, check=True):
        """Use the existing stream owner; binary patch bytes never use its text pipes."""
        from .execution_control import local, checkpoint
        from .file_io import atomic_write_text, read_bytes
        command = list(command)
        control_event = control_event or getattr(local, 'cancel_event', None)
        if timeout <= 0:
            raise RuntimeError('Git步骤剩余时间预算已耗尽')
        checkpoint()
        if control_event is not None and control_event.is_set():
            raise RuntimeError('运行已被暂停或取消，候选与证据保留')
        started = time.monotonic()
        folder = Path(receipt_dir) if receipt_dir else Path(cwd).parent / 'git-processes' / secrets.token_hex(12)
        folder.mkdir(parents=True, exist_ok=False)
        receipt_path = folder / 'process.json'
        receipt = dict(context or {}, command=command, executed_command=command,
            cwd=str(Path(cwd).resolve()), requested_at=time.time(), timeout_seconds=timeout,
            status='started', returncode=None, interrupted=False, success=False, error='')
        template = next((arg.partition('=')[2] for arg in command if arg.startswith('--template=')), None)
        if template:
            template_config = Path(template) / 'config'
            receipt['template_config'] = {'path':str(template_config),
                'sha256':hashlib.sha256(read_bytes(template_config)).hexdigest()}
        if 'apply' in command:
            patch_input = Path(command[-1])
            receipt['input_patch'] = {'path':str(patch_input), 'sha256':hashlib.sha256(read_bytes(patch_input)).hexdigest()}
        atomic_write_text(receipt_path, _canonical(receipt))
        result = failure = None
        try:
            if time.monotonic() - started >= timeout:
                raise RuntimeError('Git步骤剩余时间预算已耗尽')
            # Never invoke the coding runner factory or parse model/token events.
            runner = CodexExecutionRunner(cwd, folder)
            runner.control_event = control_event
            result = runner._run_codex_streaming(command, '', timeout, lambda line: None, started)
            if check and result.returncode:
                raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
            if template and result.returncode == 0:
                import configparser
                if hashlib.sha256(read_bytes(template_config)).hexdigest() != receipt['template_config']['sha256']:
                    raise ValueError('候选Git模板配置已变化')
                candidate_config = Path(cwd) / '.git' / 'config'
                if candidate_config.is_symlink():
                    raise ValueError('候选Git配置不得为符号链接')
                config_bytes = read_bytes(candidate_config)
                config = configparser.ConfigParser()
                config.read_string(config_bytes.decode('utf-8'))
                if config.get('core', 'autocrlf', fallback='').strip().lower() != 'false':
                    raise ValueError('候选Git配置未采用冻结的autocrlf=false')
                receipt['candidate_config'] = {'path':str(candidate_config),
                    'sha256':hashlib.sha256(config_bytes).hexdigest(), 'core.autocrlf':'false'}
            checkpoint()
            if control_event is not None and control_event.is_set():
                raise RuntimeError('运行已被暂停或取消，候选与证据保留')
            return result
        except BaseException as error:
            failure = error
            raise
        finally:
            receipt.update(finished_at=time.time(), returncode=result.returncode if result else None,
                stdout=result.stdout if result else '', stderr=result.stderr if result else '',
                success=bool(result and result.returncode == 0 and failure is None),
                interrupted=bool(control_event and control_event.is_set()),
                status='failed' if failure else 'completed', error=str(failure) if failure else '')
            try:
                output = next((arg.partition('=')[2] for arg in command if arg.startswith('--output=')), None)
                if output:
                    patch_output = Path(output)
                    if patch_output.is_file():
                        receipt['output_patch'] = {'path':str(patch_output),
                            'sha256':hashlib.sha256(read_bytes(patch_output)).hexdigest()}
                atomic_write_text(receipt_path, _canonical(receipt))
            except Exception as persistence_error:
                if failure is None:
                    raise
                failure.add_note('Git进程证据保存失败：' + str(persistence_error))

    def _update_child(self, item_id, child_id, **fields):
        with self.lock:
            data = self._load(item_id)
            child = next(c for c in data['subtasks'] if c['id'] == child_id)
            child.update(fields)
            self._save(data)
            self._event(data, 'subtask/status', 'harness', {'subtask_id': child_id, **fields})

    @staticmethod
    def _snapshot(source, workspace, files, *, git_runner=None):
        execute = git_runner or DeliveryRuntime._git_process
        workspace.mkdir(parents=True, exist_ok=False)
        for relative, digest in files.items():
            content = (source / relative).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError('复制时共同输入已变化')
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        # Machine-wide autocrlf must not silently change an isolated candidate's
        # bytes. A controlled template also avoids a separate config process;
        # explicit project .gitattributes still define their own policy.
        template = workspace.parent / 'git-templates' / secrets.token_hex(12)
        template.mkdir(parents=True, exist_ok=False)
        from .file_io import atomic_write_text
        atomic_write_text(template / 'config', '[core]\n\tautocrlf = false\n')
        execute(['git', 'init', '-q', '--template=' + str(template)], cwd=workspace, check=True, capture_output=True)
        execute(['git', 'add', '-f', '--all'], cwd=workspace, check=True, capture_output=True)
        execute(['git', '-c', 'user.name=Workbench snapshot', '-c', 'user.email=workbench@localhost', '-c', 'commit.gpgsign=false',
                        'commit', '--allow-empty', '-q', '-m', 'Frozen delivery source'], cwd=workspace, check=True, capture_output=True)

    @staticmethod
    def _patch(workspace, scopes, *, git_runner=None):
        execute = git_runner or DeliveryRuntime._git_process
        for scope in scopes:
            if (workspace / scope).exists():
                execute(['git', 'add', '-f', '-N', '--all', '--', scope], cwd=workspace, check=True, capture_output=True)
        patch_folder = workspace.parent / 'git-patches'
        patch_folder.mkdir(parents=True, exist_ok=True)
        patch_output = patch_folder / (secrets.token_hex(12) + '.patch')
        execute(['git', 'diff', '--binary', '--no-ext-diff', '--no-textconv', '--output=' + str(patch_output), 'HEAD'],
                cwd=workspace, check=True, capture_output=True)
        from .file_io import read_bytes
        return read_bytes(patch_output)
