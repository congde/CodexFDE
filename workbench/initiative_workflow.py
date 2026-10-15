"""Durable, revision-bound initiative discussion, delivery, rework and integration."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path
from contextlib import nullcontext

from .daily_delivery import manifest, prepare_daily, submit_daily
from .initiative_research import InitiativeResearch
from .learning import LearningStore, canonical
from .initiative_decisions import InitiativeDecisions, confirmation_digest, policy as delivery_policy


BUSY = {'researching', 'queued', 'executing', 'checking', 'cancelling', 'integrating'}
INTEGRATION_LOCK = threading.Lock()


def research_source_check(source, baseline, current, proposal):
    """The registered project root is the boundary, never the workbench root."""
    changes = [{'path': p, 'kind': 'added' if p not in baseline else 'removed' if p not in current else 'modified'}
               for p in sorted(baseline.keys() | current.keys()) if baseline.get(p) != current.get(p)]
    return {'status': 'changed' if changes else 'current', 'files': changes}


class InitiativeWorkflow:
    def __init__(self, repository, runtime, initiatives, tasks, *, enabled=False,
                 researcher=None, submitter=None, projects=None):
        self.repository, self.runtime = Path(repository).resolve(), Path(runtime).resolve()
        self.initiatives, self.tasks = initiatives, tasks
        self.enabled = enabled
        self.projects = projects
        self.cancel_events = {}
        self.closed = False
        self.shutdown_errors = []
        self.researcher = researcher or InitiativeResearch()
        self.submitter = submitter or submit_daily
        self.lock = threading.RLock()
        self.workers = {}
        self.learning = LearningStore(tasks.path)
        self.decisions = InitiativeDecisions(self)
        from .learning_generation import LearningGenerationService
        self.learning_generation = LearningGenerationService(self.runtime, self.tasks, self.repository_for)
        with self.tasks.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS initiative_workflows (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            rows = db.execute('SELECT id,payload FROM initiative_workflows').fetchall()
            for row in rows:
                data = json.loads(row['payload'])
                if data['stage'] in BUSY:
                    data.update(stage='interrupted', error='工作台已重启，本轮停止。请核对保留的候选和记录后再继续。')
                    if data.get('active_task_id'):
                        db.execute("UPDATE tasks SET status='failed',error=? WHERE id=? AND status IN ('queued','spec_ready','executing','evaluating')", (data['error'], data['active_task_id']))
                    data['revision'] += 1
                    data['messages'].append({'role': 'system', 'text': data['error'], 'at': time.time()})
                    db.execute('UPDATE initiative_workflows SET payload=? WHERE id=?',
                               (json.dumps(data, ensure_ascii=False), row['id']))
        for row in rows:
            restored = json.loads(row['payload'])
            if restored['stage'] in BUSY and restored.get('active_task_id'):
                self.learning.finish(restored['active_task_id'], note='工作台重启中断，保留引用链并停用失败流程')

    def project(self, item_id):
        if not self.projects:
            return None
        item = self.initiatives.get(item_id)
        return self.projects.get(item['project_id'])

    def repository_for(self, item_id):
        project = self.project(item_id)
        return Path(project['root_path']) if project else self.repository

    def preflight(self, item_id, *, require_eval=True):
        from .migration import assert_migration_preflight
        project = self.project(item_id)
        assert_migration_preflight(self.runtime, project['id'] if project else None)
        if not self.projects:
            return
        import shutil
        import subprocess
        from .execution import CodexExecutionRunner
        from .codex_options import headless_environment
        project = self.project(item_id)
        root = Path(project['root_path'])
        if not root.is_dir() or not (root / '.git').exists():
            raise ValueError('项目目录或 Git 工作区不可用')
        command = (project['eval_command'] or [''])[0]
        if require_eval and not (Path(command).is_absolute() and Path(command).is_file()):
            raise ValueError('请登记项目运行环境中解释器或测试程序的绝对路径')
        runner = CodexExecutionRunner(root, self.runtime)
        capability = runner.capabilities()
        if not capability['codex_available']:
            raise ValueError(capability['reason'])
        try:
            probe = subprocess.run([shutil.which(runner.executable) or runner.executable, 'login', 'status'],
                capture_output=True, timeout=10, env=headless_environment(),
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError('Codex CLI 认证检查未完成，请核对安装与登录状态') from error
        if probe.returncode:
            raise ValueError('Codex CLI 尚未认证；请在本机完成 codex login 后重试')

    def confirm_prd(self, item_id, actor, revision, success_metric=None):
        actor = self.actor(actor)
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'ready'})
            if self.initiatives.get(item_id)['version'] != data['initiative_version']:
                raise ValueError('事项内容已变化，请重新调研')
            document = data['documents'][-1]
            metric = success_metric if success_metric is not None else document['prd'].get('success_metric', '')
            if not isinstance(metric, str) or not metric.strip() or metric.startswith('待业务负责人') or len(metric) > 1000:
                raise ValueError('请填写 PRD 的效果指标与目标')
            if document.get('prd_confirmation') and document['prd'].get('success_metric') != metric.strip():
                raise ValueError('已确认 PRD 不可覆盖，请重新讨论生成新版本')
            document['prd']['success_metric'] = metric.strip()
            if not document.get('prd_confirmation'):
                document['prd_confirmation'] = {'actor': actor, 'at': time.time(), 'version': document['version']}
            self._event(data, 'user', '确认 PRD 业务目标与效果指标', actor=actor)
            self._save(data)
        return self.get(item_id)

    def cancel(self, item_id, actor, revision, *, run_id=None, session_id=None, control_revision=None):
        actor = self.actor(actor)
        with self.lock:
            data = self._load(item_id)
            binding = any(value is not None for value in (run_id, session_id, control_revision))
            if type(revision) is not int or revision < 0 or (not binding and data['revision'] != revision):
                raise ValueError('事项进展已变化，请刷新后核对最新内容')
            if data['stage'] in {'cancelled', 'cancelling'}:
                if binding:
                    raise ValueError('当前运行已停止，请刷新后核对最新内容')
                return self.get(item_id)
            if data['stage'] not in {'queued', 'executing', 'researching', 'checking'}:
                raise ValueError('当前没有可取消的执行')
            runtime = getattr(self, 'delivery_runtime', None)
            with runtime.lock if runtime is not None else nullcontext():
                active = runtime._load(item_id) if runtime is not None else None
                current_run = bool(active and data['stage'] in {'queued', 'executing'}
                    and active['run_id'] == (data.get('plan') or {}).get('plan_id')
                    and active['state'] in {'running', 'queued', 'awaiting_approval', 'pausing'})
                if binding and not current_run:
                    raise ValueError('运行身份已变化，请刷新后核对最新内容')
                if current_run:
                    runtime.control(item_id, 'cancel', actor, active['revision'],
                                    run_id=run_id, session_id=session_id, control_revision=control_revision)
            self.cancel_events.setdefault(item_id, threading.Event()).set()
            data['stage'] = 'cancelling'
            self._event(data, 'user', '请求取消；正在停止并保留证据', actor=actor)
            self._save(data)
        return self.get(item_id)

    @staticmethod
    def actor(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 80 or value.strip().lower().startswith('agent:'):
            raise ValueError('请填写真实操作人的署名')
        return value.strip()

    def _load(self, item_id):
        item = self.initiatives.get(item_id)
        with self.tasks.connect() as db:
            row = db.execute('SELECT payload FROM initiative_workflows WHERE id=?', (item_id,)).fetchone()
        if row:
            data = json.loads(row['payload'])
            if item.get('linked_task_id'):
                linked = self.tasks.get(item['linked_task_id'])
                data['linked_requirement_id'] = linked.get('requirement_id', '')
            if data.get('v0') and data.get('active_task_id'):
                task = self.tasks.get(data['active_task_id'])
                if task['status'] in {'review', 'completed', 'rework', 'failed', 'dead_letter'}:
                    data['stage'] = task['status']
            return data
        return {
            'id': item_id, 'revision': 0, 'stage': 'idle', 'initiative_version': item['version'],
            'messages': [], 'proposal': None, 'iterations': [], 'active_task_id': None,
            'workspace': None, 'plan': None, 'invocation': None, 'error': '', 'progress': []}

    def workspace_path(self, path):
        from .reference_paths import resolve_reference
        return resolve_reference(path, self.runtime).resolve()

    def _save(self, data, *, connection=None):
        data['revision'] += 1
        with nullcontext(connection) if connection is not None else self.tasks.connect() as db:
            db.execute('INSERT OR REPLACE INTO initiative_workflows VALUES (?,?)',
                       (data['id'], json.dumps(data, ensure_ascii=False)))

    def _documents(self, data, item, proposal):
        versions = data.setdefault('documents', [])
        previous = copy.deepcopy(versions[-1]) if versions else None
        version = len(versions) + 1
        versions.append({'version': version, 'at': time.time(), 'status': 'draft',
            'prd': {'business_problem': item['raw_signal'], 'goal': proposal['goal'],
                    'users': proposal.get('users', []), 'scope': proposal.get('scope', []),
                    'acceptance': proposal['acceptance'], 'non_goals': proposal['non_goals'],
                    'questions': proposal['questions'], 'success_metric': item.get('success_metric') or '待业务负责人在讨论中确定'},
            'technical_plan': {'findings': proposal['findings'], 'sources': proposal['sources'],
                               'write_scope': proposal['write_scope'], 'steps': proposal['steps'],
                               'test_plan': proposal.get('test_plan', []), 'eval_command': (self.project(item['id']) or {}).get('eval_command', [])}})
        data['document_version'] = version
        if data.pop('pending_rework_reason', None) == 'technical' and previous and previous.get('prd_confirmation'):
            data['prd_reuse_base'] = previous
        original = data.get('prd_reuse_base')
        if original:
            prd = original['prd']
            if all(prd.get(key, []) == proposal.get(key, []) for key in ('goal', 'acceptance', 'non_goals', 'users', 'scope')):
                versions[-1]['prd'] = copy.deepcopy(prd)
                versions[-1]['prd_confirmation'] = {**original['prd_confirmation'], 'version': version,
                                                     'carried_from_version': original['version']}
                self._event(data, 'system', '技术方案返工沿用未变化的已确认需求；保留原需求判断与确认来源',
                            previous_document_version=original['version'])

    def record_delivery(self, item_id, actor, revision, kind, fields):
        actor = self.actor(actor)
        required = {'release': ('version', 'environment', 'evidence'),
                    'outcome': ('observation', 'period', 'evidence', 'conclusion')}
        if self.projects:
            required['outcome'] += ('target', 'actual')
        if kind not in required or not isinstance(fields, dict):
            raise ValueError('交付记录格式无效')
        clean = {}
        for key in required[kind]:
            value = fields.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 4000:
                raise ValueError('请完整填写记录与实际证据：' + key)
            clean[key] = value.strip()
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'integrated', 'released', 'observed'})
            if kind == 'outcome' and not data.get('current_release'):
                raise ValueError('请先记录本轮实际发布，再回收线上效果')
            record = {'target': fields.get('target', ''), 'actual': fields.get('actual', fields.get('observation', '')),
                      'id': secrets.token_hex(12), 'kind': kind, 'actor': actor,
                      'at': time.time(), 'task_id': data['active_task_id'],
                      'document_version': data.get('document_version'), **clean}
            if kind == 'release':
                record['integration'] = copy.deepcopy(data.get('integration'))
                data['current_release'] = record['id']
            else:
                record['release_id'] = data['current_release']
            data.setdefault('delivery_records', []).append(record)
            data['stage'] = 'released' if kind == 'release' else 'observed'
            data['observation_status'] = '待观察' if kind == 'release' else '已回收'
            self._event(data, 'user', ('登记实际发布（人工提供证据）' if kind == 'release' else '回收实际效果')
                        + '\n' + '\n'.join(clean.values()), actor=actor, record_id=record['id'])
            self._save(data)
            with self.initiatives.connect() as db:
                db.execute('UPDATE initiatives SET status=? WHERE id=?', (data['stage'], item_id))
        return self.get(item_id)

    def reopen(self, item_id, actor, revision, text):
        actor = self.actor(actor)
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError('请说明下一轮需要改进的业务问题')
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'integrated', 'released', 'observed'})
            data.setdefault('completed_cycles', []).append({k: copy.deepcopy(data.get(k)) for k in
                ('active_task_id', 'workspace', 'integration', 'document_version', 'current_release', 'delivery_records', 'iterations')})
            data.update(stage='idle', workspace=None, active_task_id=None, plan=None, proposal=None,
                        integration=None, origin_manifest=None, candidate_manifest=None, current_release=None, iterations=[], observation_status='尚未发布')
            self._event(data, 'user', '开启下一轮改进：' + text.strip(), actor=actor)
            self._save(data)
        return self.get(item_id)

    def _event(self, data, role, text, **extra):
        data['messages'].append({'role': role, 'text': text, 'at': time.time(), **extra})

    def submit_v0(self, item_id, actor, revision, *, spec_text, execution_mode,
                  workspace_path, write_scope, execution_timeout_seconds, confirmed=False):
        """Consume the user's already-confirmed contract without generating another one."""
        from .spec import parse_spec
        from .execution import validate_v0_authorization
        actor = self.actor(actor)
        if confirmed is not True:
            raise ValueError('请确认本次合同、执行方式和允许文件')
        if execution_mode == 'codex' and not self.enabled:
            raise ValueError('当前服务未启用 Codex 编码')
        if not isinstance(spec_text, str) or len(spec_text) > 24000:
            raise ValueError('合同须为最多 24000 字的 Markdown 文本')
        parse_spec(spec_text)
        validate_v0_authorization(execution_mode, workspace_path, write_scope, execution_timeout_seconds)
        if execution_mode == 'codex' and Path(workspace_path).resolve() == self.repository_for(item_id).resolve():
            raise ValueError('请使用隔离候选目录，不能直接修改登记项目')
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'idle', 'rework', 'failed', 'cancelled', 'interrupted'})
            item = self.initiatives.get(item_id)
            if item.get('decision') in {'defer', 'reject', 'stop'}:
                raise ValueError('事项已暂缓或停止，请先复查决定')
            folder = self.runtime / 'v0-contracts' / secrets.token_hex(16)
            folder.mkdir(parents=True)
            path = folder / 'SPEC.md'
            path.write_text(spec_text, encoding='utf-8')
            task = self.tasks.create_v0(item['title'], spec_path=str(path), actor=actor,
                requirement_id='WB-L04-BOOTSTRAP', execution_mode=execution_mode,
                workspace_path=workspace_path, write_scope=write_scope,
                execution_timeout_seconds=execution_timeout_seconds)
            self.tasks.append_event(task['id'], '已关联具名决定的事项与冻结合同', actor=actor,
                evidence={'id': item_id, 'title': item['title'], 'version': item['version'],
                          'decision_by': actor, 'spec_sha256': task['spec_sha256']})
            with self.initiatives.connect() as db:
                db.execute("UPDATE initiatives SET linked_task_id=?,status='delivering' WHERE id=?", (task['id'], item_id))
                self.initiatives._append(db, item_id, 'delivery/v0', actor, {'task_id': task['id']})
            data.update(active_task_id=task['id'], stage='queued', error='', v0=True)
            data['iterations'].append({'task_id': task['id'], 'plan_id': None})
            self._event(data, 'user', '确认合同并启动：' + execution_mode, actor=actor, task_id=task['id'])
            self._launch(data, self._run_v0, actor)
        return self.get(item_id)

    def _run_v0(self, item_id, actor):
        from .execution import CodexExecutionRunner
        from .workflow import run_task
        from .execution_control import delivery_lock, checkpoint
        while not delivery_lock.acquire(timeout=.1):
            checkpoint()
        try:
            with self.lock:
                data = self._load(item_id)
                data['stage'] = 'executing'
                self._save(data)
            task = self.tasks.get(data['active_task_id'])
            result = run_task(self.tasks, task['id'], actor,
                execution_runner=CodexExecutionRunner(task['workspace_path'], self.runtime))
            with self.lock:
                data = self._load(item_id)
                data.update(stage=result['status'], error=result.get('error') or '')
                self._event(data, 'system', 'V0 执行结束；检查与人工确认请在交付记录查看', task_id=task['id'])
                self._save(data)
        finally:
            delivery_lock.release()

    def _check(self, data, revision, stages):
        if type(revision) is not int or data['revision'] != revision:
            raise ValueError('事项进展已变化，请刷新后核对最新内容')
        if data['stage'] not in stages:
            raise ValueError('当前阶段不能执行此操作：' + data['stage'])
        if self.workers.get(data['id']) and self.workers[data['id']].is_alive():
            raise ValueError('本事项的上一项操作尚未结束')

    def get(self, item_id):
        with self.lock:
            data = copy.deepcopy(self._load(item_id))
        from .eval_harness import ReadSnapshot
        snapshot = ReadSnapshot(self.runtime, loader=lambda source: manifest(source, self.runtime, record_process=False))
        data['enabled'] = self.enabled
        data['project'] = self.project(item_id)
        data['learning'] = self.learning.view(item_id)
        data['learning_sources'] = self.learning.source_options(item_id)
        data['prd_confirmed'] = bool(data.get('documents') and data['documents'][-1].get('prd_confirmation'))
        data['delivery_policy'] = delivery_policy(self.initiatives.get(item_id), data, self.repository_for(item_id))
        data['authorization_expired'] = bool(data.get('plan') and time.time() > data['plan'].get('expires_at', 0))
        # The saved warning describes a past observation, not the current tree.
        # Keep that observation in messages and expose a fresh, inspectable check.
        data['source_check'] = None
        if data.get('research_manifest') is not None and data['stage'] in {'clarifying', 'ready', 'confirmed'}:
            source = self.workspace_path(data['workspace']) if data['workspace'] else self.repository_for(item_id)
            try:
                current = snapshot.files(source)
                baseline = data['plan']['source_manifest'] if data['stage'] == 'confirmed' and data.get('plan') else data['research_manifest']
                data['source_check'] = research_source_check(source, baseline, current, data['proposal'])
                changes = data['source_check']['files']
                data['warning'] = (f'本事项所属项目有 {len(changes)} 项文件变化。'
                                   '已有回答和发现已保留；请重新核对后确认方案。' if changes else '')
            except (OSError, ValueError, subprocess.SubprocessError):
                data['source_check'] = {'status': 'unavailable', 'files': []}
                data['warning'] = '暂时无法核对项目文件，请恢复项目目录后重新核对。'
        data.pop('origin_manifest', None)
        data.pop('research_manifest', None)
        data.pop('candidate_manifest', None)
        if data.get('plan'):
            data['plan'].pop('source_manifest', None)
        if data['active_task_id']:
            task = self.tasks.get(data['active_task_id'])
            execution = next((e.get('evidence') for e in reversed(task['events'])
                              if e['detail'] == '受控执行阶段完成'), {}) or {}
            data['task'] = {'id': task['id'], 'status': task['status'], 'error': task.get('error'),
                'summary': (task.get('result') or {}).get('summary'), 'diff': execution.get('diff', ''),
                'changed_files': execution.get('changed_files', []), 'execution': execution,
                'events': task['events'][-80:]}
            from .eval_harness import report_view
            latest = next((r for r in reversed(data.get('eval_runs', []))
                           if r['task_id'] == task['id']), None)
            report = latest.get('report') if latest else task.get('result')
            data['eval_harness'] = report_view(report, data.get('workspace'), self.runtime, snapshot=snapshot)
            data['eval_harness']['error'] = latest.get('error', '') if latest else ''
            data['eval_harness']['can_run'] = bool(self.enabled and not data.get('v0') and
                data['stage'] in {'review', 'rework'} and data.get('workspace') and
                ((data.get('plan') or {}).get('project') or {}).get('eval_command'))
        from .repair_loop import project as loop_project
        data['repair_loop'] = loop_project(data.get('repair_loop_config'), data.get('iterations', []), self.tasks)
        from .quality_hook import view as hook_view
        data['quality_hook'] = hook_view(data.get('hook_package'), data.get('workspace'), data.get('active_task_id'), snapshot=snapshot)
        data['quality_hook']['can_prepare'] = bool(not data.get('v0') and
            data['stage'] in {'review', 'rework'} and data.get('workspace') and data.get('active_task_id') and
            ((data.get('plan') or {}).get('project') or {}).get('eval_command'))
        from .ci_review import view as ci_view
        data['ci_evidence'] = ci_view(data.get('ci_evidence_runs'))
        data['ci_evidence']['can_record'] = bool(data.get('active_task_id') and
                                                 data['stage'] in {'review', 'rework'})
        data.setdefault('learning', {})['generations'] = self.learning_generation.list(item_id)
        return data

    def configure_loop(self, item_id, actor, revision, fields):
        """Freeze L10 bounds for subsequent rework rounds on this initiative."""
        from .repair_loop import validate_config
        actor = self.actor(actor)
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'idle', 'clarifying', 'ready', 'confirmed', 'review', 'rework',
                                        'failed', 'interrupted', 'cancelled'})
            config = validate_config(fields)
            data['repair_loop_config'] = config
            self._event(data, 'user', '保存有界修复 Loop：最多 {max_rounds} 轮，时间 {time_budget_seconds} 秒，Token {token_budget}'.format(**config), actor=actor)
            self._save(data)
        return self.get(item_id)

    def configure_delivery(self, item_id, actor, revision, mode, note):
        return self.decisions.configure(item_id, actor, revision, mode, note)

    def confirm_execute(self, item_id, actor, revision, reviewer, metric, confirmed=False):
        return self.decisions.confirm_execute(item_id, actor, revision, reviewer, metric, confirmed)

    def renew_authorization(self, item_id, actor, revision, note, confirmed=False):
        return self.decisions.renew(item_id, actor, revision, note, confirmed)

    def rework(self, item_id, actor, revision, reason, note):
        return self.decisions.rework(item_id, actor, revision, reason, note)

    def prepare_hook(self, item_id, actor, revision):
        from .quality_hook import prepare
        actor = self.actor(actor)
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'review', 'rework'})
            project = (data.get('plan') or {}).get('project')
            if data.get('v0') or not project or not data.get('workspace') or not data.get('active_task_id'):
                raise ValueError('请先形成项目候选及已确认的检查命令')
            package = prepare(self.runtime, data['workspace'], project, data['active_task_id'], item_id, actor)
            data['hook_package'] = package
            data.setdefault('hook_packages', []).append(package)
            self._event(data, 'user', '准备候选 Stop Hook 待审文件；尚未安装或信任', actor=actor,
                        task_id=data['active_task_id'], package_path=package['path'])
            self._save(data)
        return self.get(item_id)

    def run_eval(self, item_id, actor, revision):
        """Re-run the confirmed command without invoking Codex or accepting a task."""
        actor = self.actor(actor)
        if not self.enabled:
            raise ValueError('当前服务未开启代码执行')
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'review', 'rework'})
            command = ((data.get('plan') or {}).get('project') or {}).get('eval_command')
            if data.get('v0') or not command or not data.get('workspace') or not data.get('active_task_id'):
                raise ValueError('当前事项没有可复验的项目候选与已确认检查命令')
            prior = data['stage']
            data.update(stage='checking', error='')
            self._event(data, 'user', '运行候选 Eval Harness，保留原报告', actor=actor)
            self._launch(data, self._run_eval, actor, prior, list(command))
        return self.get(item_id)

    def record_ci_evidence(self, item_id, actor, revision, fields):
        """Verify remote CI identity/report bytes and retain a task-linked record."""
        from .ci_review import retain
        actor = self.actor(actor)
        if not isinstance(fields, dict):
            raise ValueError('CI 证据格式无效')
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'review', 'rework'})
            if not data.get('active_task_id'):
                raise ValueError('当前事项没有可绑定的交付候选')
            record = retain(self.runtime, data['active_task_id'], fields.get('report_text'),
                fields.get('envelope'), run_url=fields.get('run_url'),
                job_conclusion=fields.get('job_conclusion'), candidate_sha=fields.get('candidate_sha'), actor=actor)
            data.setdefault('ci_evidence_runs', []).append(record)
            self.tasks.append_event(data['active_task_id'], 'CI 独立复验证据已核验', actor=actor,
                evidence={k: v for k, v in record.items() if k != 'artifacts'})
            self._event(data, 'user', '核验 CI 独立复验：' + record['job_conclusion'], actor=actor,
                        task_id=data['active_task_id'], run_id=record['run_id'])
            self._save(data)
        return self.get(item_id)

    def _run_eval(self, item_id, actor, prior, command):
        from .project_delivery import CandidateProjectEval
        with self.lock:
            data = self._load(item_id)
        with self.tasks.connect() as db:
            bound = db.execute('SELECT id FROM learning_bindings WHERE task_id=?', (data['active_task_id'],)).fetchone()
        binding_id = bound['id'] if bound else None
        binding_v2 = binding_id and any((a['snapshot'].get('recipe') or {}).get('schema_version') == 2
                                       for a in self.learning.binding(binding_id)['assets'])
        contract, contract_error = {'checks': [], 'required_files': []}, ''
        try:
            candidate_changed = binding_id and manifest(data['workspace'], self.runtime) != data.get('candidate_manifest')
            if binding_v2 and candidate_changed:
                raise ValueError('采用流程的候选已变化，请重新确认返工方案与实际Diff')
            report = CandidateProjectEval(data['workspace'], self.runtime, data['active_task_id'],
                                          command, 'manual-recheck')()
            if binding_id and report['summary']['decision'] == 'pass':
                try:
                    contract = self.learning.check_phase(binding_id, 'eval', data['workspace'])
                except (ValueError, OSError) as error:
                    contract_error = str(error)
                if candidate_changed:
                    contract_error = '采用流程的候选已变化，复验绿不能替代重新确认方案与实际Diff'
        except Exception as error:
            with self.lock:
                current = self._load(item_id)
                current.setdefault('eval_runs', []).append({'task_id': data['active_task_id'],
                    'actor': actor, 'at': time.time(), 'error': str(error)})
                if binding_id:
                    self.learning.record_recheck(binding_id, {'passed': False, 'phase_contract': contract,
                        'message': str(error), 'stopped_phase': 'eval', 'recovery': 'retain_candidate_new_plan'})
                self._save(current)
            raise
        with self.lock:
            from .execution_control import checkpoint
            checkpoint()
            current = self._load(item_id)
            current.setdefault('eval_runs', []).append({'task_id': data['active_task_id'],
                'actor': actor, 'at': time.time(), 'report': report, 'phase_contract': contract,
                'phase_error': contract_error})
            passed = report['summary']['decision'] == 'pass' and not contract_error
            current['stage'] = prior if passed else 'rework'
            # Each recheck gets its own immutable report. The task points to the
            # actual latest receipt even when the checks remain green.
            task_report = copy.deepcopy(report)
            report_dir = self.runtime / 'reports'
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / (data['active_task_id'] + '-' + secrets.token_hex(16) + '-manual-recheck.json')
            task_report['report_path'] = (Path('reports') / report_path.name).as_posix()
            task_report.pop('report_sha256', None)
            payload = json.dumps(task_report, ensure_ascii=False, indent=2).encode('utf-8')
            report_path.write_bytes(payload)
            task_report['report_sha256'] = hashlib.sha256(payload).hexdigest()
            task = self.tasks.get(data['active_task_id'])
            if task['status'] not in {'review', 'rework'}:
                raise ValueError('复验期间任务状态已变化，保留报告但不覆盖审核结果')
            if not passed and task['status'] == 'review':
                self.tasks.transition(task['id'], 'rework', '候选复验或流程阶段约束未通过', actor=actor,
                    result=task_report, error=contract_error or '候选Eval阻断失败')
            else:
                with self.tasks.connect() as db:
                    updated = db.execute("UPDATE tasks SET result_json=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
                                         (json.dumps(task_report, ensure_ascii=False), task['id'], task['status']))
                    if updated.rowcount != 1:
                        raise ValueError('复验期间任务状态已变化，拒绝覆盖审核结果')
            if binding_id:
                self.learning.record_recheck(binding_id, {'passed': passed, 'summary': report['summary'],
                    'report_path': task_report['report_path'], 'report_sha256': task_report['report_sha256'],
                    'candidate_manifest': manifest(data['workspace'], self.runtime), 'phase_contract': contract,
                    'message': contract_error, 'stopped_phase': None if passed else 'eval',
                    'recovery': None if passed else 'retain_candidate_new_plan'})
            if not passed:
                self.learning.finish(data['active_task_id'], note=contract_error or '候选 Eval 复验存在阻断失败，停用已采用流程并保留失败证据')
            self.tasks.append_event(data['active_task_id'], '候选 Eval 复验完成', actor=actor,
                                    evidence={'summary': report['summary'], 'runner': report['runner'],
                                        'report_path': task_report['report_path'], 'report_sha256': task_report['report_sha256'],
                                        'phase_contract': contract, 'phase_error': contract_error})
            self._event(current, 'system', '候选复验完成；检查通过仍需人审。' if passed else '候选复验未通过，请返工。')
            self._save(current)

    def learning_action(self, item_id, actor, revision, fields):
        """All governance remains in the initiative's existing home workspace."""
        from .learning import human
        actor = human(actor)
        if not isinstance(fields, dict):
            raise ValueError('经验操作必须是对象')
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'idle', 'clarifying', 'ready', 'confirmed', 'review', 'rework',
                                        'failed', 'interrupted', 'cancelled', 'accepted', 'integrated', 'released', 'observed'})
            action = fields.get('action')
            if action == 'generate':
                self.learning_generation.start(item_id, actor, fields)
                return self.get(item_id)
            if action == 'cancel_generation':
                self.learning_generation.cancel(item_id, fields.get('generation_id'), actor)
                return self.get(item_id)
            try:
                if action == 'create':
                    asset = self.learning.create(item_id, actor, fields.get('candidate'))
                    self._event(data, 'user', '提炼候选：' + asset['title'], actor=actor, asset_id=asset['id'])
                elif action in {'approve', 'publish', 'revoke'}:
                    asset = self.learning.govern(item_id, fields.get('asset_id'), actor, action, fields.get('note'))
                    self._event(data, 'user', '经验治理：' + action, actor=actor, asset_id=asset['id'], note=fields.get('note'))
                elif action == 'recall':
                    if data['stage'] not in {'idle', 'clarifying', 'ready', 'rework', 'failed'}:
                        raise ValueError('请在方案确认前召回或试用经验')
                    item = self.initiatives.get(item_id)
                    query = self._learning_query(item, data)
                    data['learning_recall'] = self.learning.recall(item_id, query, trials=fields.get('trials') is True)
                    data['learning_decision'] = None
                    self._event(data, 'user', '召回适用经验与流程', actor=actor, recall_id=data['learning_recall']['id'])
                elif action == 'decide':
                    if data['stage'] not in {'clarifying', 'ready'}:
                        raise ValueError('请在确认技术方案前记录采用决定')
                    recall = data.get('learning_recall')
                    if not recall or recall['id'] != fields.get('recall_id'):
                        raise ValueError('请使用本轮最新召回记录')
                    data['learning_decision'] = self.learning.decide(item_id, recall['id'], fields.get('choices'), actor)
                    self._event(data, 'user', '已逐项记录经验采用决定', actor=actor, decision=data['learning_decision'])
                else:
                    raise ValueError('未知经验操作')
            except (ValueError, KeyError, OSError) as error:
                self._event(data, 'system', '经验操作被阻断：' + str(error), action=action, actor=actor)
                self._save(data)
                raise
            self._save(data)
        return self.get(item_id)

    @staticmethod
    def _learning_query(item, data):
        return '\n'.join([str(item.get(k, '')) for k in ('title', 'raw_signal', 'goal')] +
                         [m['text'] for m in data['messages'] if m['role'] == 'user'])[-30000:]

    def _launch(self, data, function, *args):
        if self.closed:
            raise ValueError('工作台正在关闭，不能启动新的事项工作')
        self.cancel_events[data['id']] = threading.Event()
        self._save(data)
        thread = threading.Thread(target=self._worker, args=(data['id'], function, args), daemon=True)
        self.workers[data['id']] = thread
        thread.start()

    def _worker(self, item_id, function, args):
        from contextlib import ExitStack
        from .maintenance import MaintenanceBusy, MaintenanceGate
        from .execution_control import local, checkpoint
        previous_event = getattr(local, 'cancel_event', None)
        local.cancel_event = self.cancel_events[item_id]
        try:
            with ExitStack() as ownership:
                while True:
                    checkpoint()
                    try:
                        ownership.enter_context(MaintenanceGate(self.runtime).write())
                        break
                    except MaintenanceBusy:
                        self.cancel_events[item_id].wait(.05)
                self._worker_owned(item_id, function, args)
        except Exception as error:
            try:
                self._worker_failed(item_id, error)
            except Exception as persistence_error:
                message = '事项中断状态未能保存：' + str(persistence_error)
                self.shutdown_errors.append(message)
                logging.getLogger(__name__).warning('%s；原错误：%s', message, error)
        finally:
            local.cancel_event = previous_event

    def _worker_owned(self, item_id, function, args):
        from .execution_control import checkpoint
        checkpoint()
        function(item_id, *args)
        checkpoint()

    def _worker_failed(self, item_id, error):
        with self.lock:
            data = self._load(item_id)
            data.update(stage='cancelled' if self.cancel_events[item_id].is_set() else 'failed', error=f'{type(error).__name__}: {error}')
            self._event(data, 'system', data['error'])
            if data.get('active_task_id'):
                task = self.tasks.get(data['active_task_id'])
                if task['status'] in {'queued', 'spec_ready', 'executing', 'evaluating', 'review', 'rework'}:
                    self.tasks.transition(task['id'], 'failed', '事项执行失败，原记录保留', error=str(error))
                self.learning.finish(task['id'], note=str(error))
            self._save(data)

    def _progress(self, item_id, line):
        invocation = None
        try:
            event = json.loads(line)
            invocation = event.get('invocation') if event.get('type') == 'research.invocation' else None
            item = event.get('item') or {}
            text = item.get('text') or item.get('command') or event.get('type', '')
        except ValueError:
            text = line
        with self.lock:
            data = self._load(item_id)
            if invocation:
                data['invocation'] = invocation
            data['progress'] = (data['progress'] + [str(text)[-3000:]])[-30:]
            self._save(data)

    def discuss(self, item_id, actor, revision, text):
        actor = self.actor(actor)
        if not self.enabled:
            raise ValueError('当前服务未启用 Codex 执行，请用原运行目录开启执行模式')
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError('请填写本次需求、回答或修改意见（最多 10000 字）')
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'idle', 'clarifying', 'ready', 'confirmed', 'review', 'rework', 'failed', 'interrupted', 'cancelled', 'accepted'})
            if data['stage'] == 'rework' and (data.get('repair_loop_config') or {}).get('enabled'):
                from .repair_loop import project as loop_project
                loop = loop_project(data['repair_loop_config'], data.get('iterations', []), self.tasks)
                if not loop['can_continue']:
                    raise ValueError('Loop 已停止：' + loop['reason'] + '。请先按 handoff 核对证据并由负责人决定。')
            item = self.initiatives.get(item_id)
            if item.get('decision') in {'defer', 'reject', 'stop'}:
                raise ValueError('事项已暂缓或停止，请先复查决定')
            if data['stage'] == 'review' and data.get('active_task_id'):
                self.tasks.review(data['active_task_id'], actor, 'reject', text.strip())
                self.learning.finish(data['active_task_id'], note=text.strip())
            data.update(stage='researching', error='', warning='', plan=None, progress=[], initiative_version=item['version'])
            self._event(data, 'user', text.strip(), actor=actor)
            self._launch(data, self._research, actor)
        return self.get(item_id)

    def _research(self, item_id, actor):
        with self.lock:
            data = self._load(item_id)
        if isinstance(self.researcher, InitiativeResearch):
            self.preflight(item_id, require_eval=False)
        source = self.workspace_path(data['workspace']) if data['workspace'] else self.repository_for(item_id)
        folder = self.runtime / 'initiative-research' / item_id / secrets.token_hex(12)
        item = self.initiatives.get(item_id)
        recall = self.learning.recall(item_id, self._learning_query(item, data))
        # Earlier model prose can repeat now-revoked memory. Keep user evidence,
        # but rebuild model conclusions whenever the previous packet is stale.
        old_ids = {m['id'] for m in (data.get('learning_recall') or {}).get('matches', [])}
        stale = old_ids - {m['id'] for m in recall['matches']}
        context = {'initiative': {k: item[k] for k in ('title', 'raw_signal', 'goal', 'non_goals', 'acceptance')},
                   'discussion': data['messages'], 'previous_proposal': data['proposal'], 'project': self.project(item_id),
                   'success_metric': item.get('success_metric', ''), 'previous_documents': data.get('documents', [])[-1:]}
        context['reusable_experience'] = {'matches': recall['matches'], 'conflicts': recall['conflicts'],
                                        'notice': '这些是带来源的建议，不能代替具名采用、授权或质量门；冲突须交给人判断。'}
        if stale:
            context['discussion'] = [m for m in data['messages'] if m['role'] == 'user']
            context['previous_proposal'], context['previous_documents'] = None, []
        if data.get('active_task_id'):
            task = self.tasks.get(data['active_task_id'])
            context['previous_result'] = {'status': task['status'], 'error': task.get('error'),
                                         'eval': task.get('result')}
        result = self.researcher(source, self.runtime, folder, context, lambda line: self._progress(item_id, line))
        with self.lock:
            current = self._load(item_id)
            if self.initiatives.get(item_id)['version'] != data['initiative_version']:
                raise ValueError('调研期间事项内容发生变化，请重新调研最新需求')
            proposal = result['proposal']
            self._documents(current, item, proposal)
            current.update(proposal=proposal, research_manifest=result['source_manifest'],
                           invocation=result['invocation'], stage='clarifying' if proposal['questions'] else 'ready')
            current.update(learning_recall=recall, learning_decision=None)
            current['warning'] = ('调研期间源码有更新。本轮发现和问题已保留；授权执行前需要重新核对最新源码。'
                                  if result.get('changed_sources') else '')
            self._event(current, 'codex', '\n'.join(proposal['findings']), questions=proposal['questions'])
            if current['warning']:
                self._event(current, 'system', current['warning'], changed_sources=result['changed_sources'])
            self._save(current)

    def confirm(self, item_id, actor, revision, reviewer):
        actor, reviewer = self.actor(actor), self.actor(reviewer)
        if reviewer.lower() in {'待确认', '待定', '待指定', '未指定', 'tbd', 'pending', 'ai', 'codex'}:
            raise ValueError('方案确认前请填写真实人工验收人的署名，不能使用待确认或 AI 名称')
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'ready'})
            item = self.initiatives.get(item_id)
            if item['version'] != data['initiative_version']:
                raise ValueError('事项内容已变化，请重新调研')
            proposal = data['proposal']
            recall = data.get('learning_recall')
            if recall and recall['matches'] and not data.get('learning_decision'):
                raise ValueError('请先逐项记录召回经验的采用或不采用理由')
            project = self.project(item_id)
            if project and not project.get('eval_command'):
                raise ValueError('请先在管理项目中配置质量检查命令，再确认技术方案')
            if self.projects and not data['documents'][-1].get('prd_confirmation'):
                raise ValueError('请先分别确认 PRD，再确认技术方案')
            if not data.get('documents'):
                self._documents(data, item, proposal)
            source = self.workspace_path(data['workspace']) if data['workspace'] else self.repository_for(item_id)
            if research_source_check(source, data['research_manifest'], manifest(source, self.runtime), proposal)['files']:
                raise ValueError('调研所依据的源码已变化，请重新调研')
            plan = prepare_daily(source, self.runtime, actor, proposal['goal'], '\n'.join(proposal['acceptance']),
                                 proposal['write_scope'], '\n'.join(proposal['non_goals']) or '不扩大本次目标', project=self.project(item_id))
            if research_source_check(source, data['research_manifest'], plan['source_manifest'], proposal)['files']:
                raise ValueError('确认期间源码已变化，请重新调研')
            plan['spec_text'] += '\n\n产品与技术方案版本：\n\n' + json.dumps(data['documents'][-1], ensure_ascii=False)
            plan.update(plan_id=secrets.token_hex(18), expires_at=time.time() + 900,
                        budget_policy=copy.deepcopy(data.get('repair_loop_config') or {}))
            if hasattr(self, 'delivery_runtime'):
                self.delivery_runtime.freeze_profile(item_id, plan)
            binding = self.learning.bind(item_id, plan['plan_id'], data.get('learning_decision'), source, actor)
            if binding:
                plan['learning_binding_id'] = binding['id']
                # Source trajectories remain in the store. Only adopted bounded
                # conclusions and the executable recipe enter the frozen Spec.
                adopted = [self.learning.execution_summary(a) for a in binding['assets']]
                plan['spec_text'] += '\n\n已具名采用的经验与受控流程（不得扩大写集或绕过审批）：\n' + canonical(adopted)
            plan['document_version'] = data['document_version']
            data['documents'][-1]['technical_confirmation'] = {'actor': actor, 'at': time.time(), 'version': data['document_version']}
            data['documents'][-1]['status'] = 'confirmed'
            data.setdefault('document_confirmations', []).append({
                'version': data['document_version'], 'actor': actor, 'reviewer': reviewer,
                'at': time.time(), 'plan_id': plan['plan_id']})
            with self.initiatives.connect() as db:
                updated = db.execute("UPDATE initiatives SET goal=?,non_goals_json=?,acceptance_json=?,"
                    "reviewer=?,decision='build',decision_by=?,decision_rationale=?,status='approved_for_delivery',"
                    "version=version+1,decided_at=CURRENT_TIMESTAMP WHERE id=? AND version=?",
                    (proposal['goal'], json.dumps(proposal['non_goals'], ensure_ascii=False),
                     json.dumps(proposal['acceptance'], ensure_ascii=False), reviewer, actor,
                     '已核对并确认本期需求与 Codex 调研方案', item_id, item['version']))
                if updated.rowcount != 1:
                    raise ValueError('事项版本已变化')
                self.initiatives._append(db, item_id, 'decision/workflow-confirmed', actor,
                                        {'proposal': proposal, 'plan_id': plan['plan_id'], 'reviewer': reviewer})
            if not data.get('origin_manifest'):
                data['origin_manifest'] = plan['source_manifest']
            data.update(stage='confirmed', plan=plan, reviewer=reviewer, initiative_version=item['version'] + 1)
            data.pop('prd_reuse_base', None)
            plan['confirmation_sha256'] = confirmation_digest(data)
            self._event(data, 'user', '确认本期目标、验收条件与不做范围；验收人：' + reviewer, actor=actor)
            self._save(data)
        return self.get(item_id)

    def execute(self, item_id, actor, revision):
        actor = self.actor(actor)
        if not self.enabled:
            raise ValueError('当前服务未启用代码执行')
        if hasattr(self, 'project_plans'):
            self.project_plans.assert_ready(item_id)
        if getattr(self, 'require_preflight', self.submitter is submit_daily):
            try:
                self.preflight(item_id)
            except (OSError, ValueError, TimeoutError) as error:
                with self.lock:
                    data = self._load(item_id)
                    self._check(data, revision, {'confirmed'})
                    data['error'] = str(error)
                    self._event(data, 'system', '执行前检查失败：' + str(error))
                    self._save(data)
                raise ValueError(str(error)) from error
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'confirmed'})
            if self.projects and not data['plan'].get('project'):
                raise ValueError('历史方案尚未绑定项目，请重新调研并确认')
            if time.time() > data['plan']['expires_at']:
                raise ValueError('执行授权已过期；请核对未变化的合同并续期，发生变化时再重新确认')
            if self.initiatives.get(item_id)['version'] != data['initiative_version']:
                raise ValueError('已确认的事项发生变化，请重新核对')
            if data['plan'].get('confirmation_sha256'):
                self.decisions.validate_frozen(item_id, data)
            if data['plan'].get('learning_binding_id'):
                source = self.workspace_path(data['workspace']) if data['workspace'] else self.repository_for(item_id)
                try:
                    self.learning.validate_binding(data['plan']['learning_binding_id'], source)
                except (ValueError, KeyError, OSError) as error:
                    self._event(data, 'system', '采用版本执行前检查被阻断：' + str(error), actor=actor)
                    self._save(data)
                    raise
            data.update(stage='queued', error='', progress=[])
            self._event(data, 'user', '授权工作台按已确认的方案执行本轮修改', actor=actor)
            self._launch(data, self._execute, actor)
        return self.get(item_id)

    def _execute(self, item_id, actor):
        from .execution_control import delivery_lock, checkpoint
        while not delivery_lock.acquire(timeout=.1):
            checkpoint()
        try:
            checkpoint()
            with self.lock:
                current = self._load(item_id)
                current['stage'] = 'executing'
                self._save(current)
            self._execute_serial(item_id, actor)
        finally:
            delivery_lock.release()

    def _execute_serial(self, item_id, actor):
        data = self._load(item_id)
        source = self.workspace_path(data['workspace']) if data['workspace'] else self.repository_for(item_id)
        def created(task):
            with self.lock:
                current = self._load(item_id)
                current['active_task_id'] = task['id']
                current['iterations'].append({'task_id': task['id'], 'plan_id': data['plan']['plan_id'],
                                              'document_version': data['plan'].get('document_version'),
                                              'at': time.time()})
                self._save(current)
            self.tasks.append_event(task['id'], '网页具名授权日常研发', actor=actor,
                evidence={'initiative_id': item_id, 'plan_id': data['plan']['plan_id']})
            with self.initiatives.connect() as db:
                db.execute("UPDATE initiatives SET linked_task_id=?,status='delivering' WHERE id=?", (task['id'], item_id))
                self.initiatives._append(db, item_id, 'delivery/iteration', actor, {'task_id': task['id']})
        try:
            output = self.submitter(source, self.runtime, self.tasks, data['plan'], created)
        except Exception:
            # A completed source snapshot may contain useful failed work. Retain
            # it as the next research starting point without accepting it.
            candidate = self.runtime / 'daily-delivery' / data['plan']['plan_id'] / 'workspace'
            if (candidate / '.git' / 'HEAD').is_file():
                with self.lock:
                    current = self._load(item_id)
                    current['workspace'] = str(candidate)
                    self._save(current)
            raise
        task = output['task']
        self.learning.finish(task['id'])
        package = next((e['evidence'] for e in reversed(task['events']) if e['detail'] == '日常研发交付包已保存'), None)
        with self.lock:
            current = self._load(item_id)
            if package:
                current['workspace'] = package['workspace']
                current['candidate_manifest'] = manifest(package['workspace'], self.runtime)
            current.update(stage='review' if task['status'] == 'review' and package else 'rework')
            self._event(current, 'system', '本轮修改与检查已结束，等待核对候选。' if current['stage'] == 'review'
                        else '本轮未通过，请查看失败记录并在此事项中提出修订意见。', task_id=task['id'])
            self._save(current)

    def accept(self, item_id, actor, revision, note):
        actor = self.actor(actor)
        if not isinstance(note, str) or not note.strip():
            raise ValueError('请填写验收依据')
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'review'})
            if actor != data['reviewer']:
                raise ValueError('请由已确认的验收负责人署名验收：' + data['reviewer'])
            if manifest(data['workspace'], self.runtime) != data['candidate_manifest']:
                raise ValueError('候选源码在检查后发生变化，请重新复验')
            from .eval_harness import report_view
            latest = next((r for r in reversed(data.get('eval_runs', []))
                           if r['task_id'] == data['active_task_id']), None)
            report = latest['report'] if latest else self.tasks.get(data['active_task_id']).get('result')
            view = report_view(report, data['workspace'], self.runtime)
            if view['freshness'] in {'stale', 'unavailable'}:
                raise ValueError('Eval 报告或候选来源已变化，请重新复验')
            if latest and report['summary']['decision'] != 'pass':
                raise ValueError('最近一次 Eval 未通过，不能接受候选')
            self.learning.check_acceptance(data['active_task_id'])
            # All review guards run before the shared connection starts writing.
            # Task status, named review and initiative acceptance commit together.
            with self.tasks.connect() as db:
                self.tasks.review(data['active_task_id'], actor, 'approve', note.strip(), connection=db)
                data['stage'] = 'accepted'
                self._event(data, 'user', '接受本轮候选：' + note.strip(), actor=actor)
                self._save(data, connection=db)
            self.learning.finish(data['active_task_id'], note=note.strip())
        return self.get(item_id)

    def integrate(self, item_id, actor, revision):
        actor = self.actor(actor)
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'accepted'})
            if actor != data['reviewer']:
                raise ValueError('请由验收负责人确认集成')
            data['stage'] = 'integrating'
            self._event(data, 'user', '确认将已验收候选集成到当前项目源码', actor=actor)
            self._launch(data, self._integrate, actor)
        return self.get(item_id)

    def _integrate(self, item_id, actor):
        with INTEGRATION_LOCK:
            self._apply_integration(item_id, actor)

    def _apply_integration(self, item_id, actor):
        data = self._load(item_id)
        repository = self.repository_for(item_id)
        workspace = self.workspace_path(data['workspace'])
        candidate = manifest(workspace, self.runtime)
        if candidate != data['candidate_manifest']:
            raise ValueError('候选已变化，请重新复验和验收')
        before = manifest(repository, self.runtime)
        if before != data['origin_manifest']:
            raise ValueError('源项目已变化，不能覆盖当前工作；候选与补丁已保留，需要人工解决基线冲突后再集成')
        changed = sorted(p for p in set(before) | set(candidate) if before.get(p) != candidate.get(p))
        if not changed:
            raise ValueError('没有可集成的源码改动')
        scopes = [p for iteration in data['iterations'] for p in self._iteration_scope(iteration['task_id'])]
        from .execution import CodexExecutionRunner
        if any(not CodexExecutionRunner._allowed(p, scopes) for p in changed):
            raise ValueError('累计候选中存在未经授权的改动')
        backups = {p: (repository / p).read_bytes() if p in before else None for p in changed}
        folder = self.runtime / 'initiative-integration' / item_id / secrets.token_hex(12)
        folder.mkdir(parents=True)
        for p, content in backups.items():
            if content is not None:
                destination = folder / 'before' / p
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        receipt = {'files': changed, 'actor': actor, 'task_id': data['active_task_id'], 'state': 'applying'}
        receipt_path = folder / 'receipt.json'
        from .file_io import atomic_write_bytes, atomic_write_text
        def save_receipt():
            atomic_write_text(receipt_path, json.dumps(receipt, ensure_ascii=False))
        save_receipt()
        def checked_target(relative, expected):
            target = repository / relative
            if target.is_symlink() or not target.resolve().is_relative_to(repository):
                raise ValueError('集成目标路径越界')
            actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            if actual != expected:
                raise ValueError('集成期间源文件变化，已停止')
            return target
        written = []
        try:
            for p in changed:
                target = checked_target(p, before.get(p))
                if p in candidate:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    content = (workspace / p).read_bytes()
                    if hashlib.sha256(content).hexdigest() != candidate[p]:
                        raise ValueError('集成期间候选文件变化，已停止')
                    atomic_write_bytes(target, content, before_replace=lambda: checked_target(p, before.get(p)))
                else:
                    target.unlink()
                written.append(p)
            if manifest(repository, self.runtime) != candidate:
                raise ValueError('集成后的源码与已验收候选不一致')
        except Exception as error:
            conflicts = []
            rollback_errors = {}
            for p in reversed(written):
                content = backups[p]
                try:
                    target = checked_target(p, candidate.get(p))
                    if content is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write_bytes(target, content, before_replace=lambda: checked_target(p, candidate.get(p)))
                    checked_target(p, before.get(p))
                except Exception as rollback_error:
                    conflicts.append(p)
                    rollback_errors[p] = str(rollback_error)
            receipt.update(state='rollback_conflict' if conflicts else 'rolled_back', conflicts=conflicts,
                           rollback_errors=rollback_errors, error=str(error))
            try:
                save_receipt()
            except Exception as receipt_error:
                if hasattr(error, 'add_note'):
                    error.add_note('集成失败回执无法保存：' + str(receipt_error))
                logging.getLogger(__name__).error('集成失败回执无法保存：%s', receipt_error)
            raise
        receipt.update(state='integrated', after_manifest=candidate)
        save_receipt()
        with self.lock:
            current = self._load(item_id)
            current.update(stage='integrated', integration={'files': changed, 'receipt': str(receipt_path), 'committed': False})
            with self.initiatives.connect() as db:
                db.execute("UPDATE initiatives SET status='integrated',updated_at=CURRENT_TIMESTAMP WHERE id=?", (item_id,))
                self.initiatives._append(db, item_id, 'delivery/integrated', actor, current['integration'])
            self._event(current, 'system', '已集成到项目源码，保留原文件备份；尚未提交 Git 或部署。')
            self._save(current)

    def _iteration_scope(self, task_id):
        return self.tasks.get(task_id)['write_scope']
