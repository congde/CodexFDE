"""Durable, revision-bound initiative discussion, delivery, rework and integration."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

from .daily_delivery import manifest, prepare_daily, submit_daily
from .initiative_research import InitiativeResearch


BUSY = {'researching', 'queued', 'executing', 'cancelling', 'integrating'}
INTEGRATION_LOCK = threading.Lock()


class InitiativeWorkflow:
    def __init__(self, repository, runtime, initiatives, tasks, *, enabled=False,
                 researcher=None, submitter=None, projects=None):
        self.repository, self.runtime = Path(repository).resolve(), Path(runtime).resolve()
        self.initiatives, self.tasks = initiatives, tasks
        self.enabled = enabled
        self.projects = projects
        self.cancel_events = {}
        self.researcher = researcher or InitiativeResearch()
        self.submitter = submitter or submit_daily
        self.lock = threading.RLock()
        self.workers = {}
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

    def project(self, item_id):
        if not self.projects:
            return None
        item = self.initiatives.get(item_id)
        return self.projects.get(item['project_id'])

    def repository_for(self, item_id):
        project = self.project(item_id)
        return Path(project['root_path']) if project else self.repository

    def preflight(self, item_id):
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
        command = project['eval_command'][0]
        if not (Path(command).is_absolute() and Path(command).is_file()):
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

    def cancel(self, item_id, actor, revision):
        actor = self.actor(actor)
        with self.lock:
            data = self._load(item_id)
            if data['stage'] in {'cancelled', 'cancelling'}:
                return self.get(item_id)
            if data['stage'] not in {'queued', 'executing', 'researching'}:
                raise ValueError('当前没有可取消的执行')
            self.cancel_events[item_id].set()
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
        return json.loads(row['payload']) if row else {
            'id': item_id, 'revision': 0, 'stage': 'idle', 'initiative_version': item['version'],
            'messages': [], 'proposal': None, 'iterations': [], 'active_task_id': None,
            'workspace': None, 'plan': None, 'invocation': None, 'error': '', 'progress': []}

    def _save(self, data):
        data['revision'] += 1
        with self.tasks.connect() as db:
            db.execute('INSERT OR REPLACE INTO initiative_workflows VALUES (?,?)',
                       (data['id'], json.dumps(data, ensure_ascii=False)))

    def _documents(self, data, item, proposal):
        versions = data.setdefault('documents', [])
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
        data['enabled'] = self.enabled
        data['project'] = self.project(item_id)
        data['prd_confirmed'] = bool(data.get('documents') and data['documents'][-1].get('prd_confirmation'))
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
        return data

    def _launch(self, data, function, *args):
        self.cancel_events[data['id']] = threading.Event()
        self._save(data)
        thread = threading.Thread(target=self._worker, args=(data['id'], function, args), daemon=True)
        self.workers[data['id']] = thread
        thread.start()

    def _worker(self, item_id, function, args):
        from .execution_control import local, checkpoint
        local.cancel_event = self.cancel_events[item_id]
        try:
            checkpoint()
            function(item_id, *args)
            checkpoint()
        except Exception as error:
            with self.lock:
                data = self._load(item_id)
                data.update(stage='cancelled' if self.cancel_events[item_id].is_set() else 'failed', error=f'{type(error).__name__}: {error}')
                self._event(data, 'system', data['error'])
                if data.get('active_task_id'):
                    task = self.tasks.get(data['active_task_id'])
                    if task['status'] in {'queued', 'spec_ready', 'executing', 'evaluating', 'review', 'rework'}:
                        self.tasks.transition(task['id'], 'failed', '事项执行失败，原记录保留', error=str(error))
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
            item = self.initiatives.get(item_id)
            if item.get('decision') in {'defer', 'reject', 'stop'}:
                raise ValueError('事项已暂缓或停止，请先复查决定')
            if data['stage'] == 'review' and data.get('active_task_id'):
                self.tasks.review(data['active_task_id'], actor, 'reject', text.strip())
            data.update(stage='researching', error='', warning='', plan=None, progress=[], initiative_version=item['version'])
            self._event(data, 'user', text.strip(), actor=actor)
            self._launch(data, self._research, actor)
        return self.get(item_id)

    def _research(self, item_id, actor):
        with self.lock:
            data = self._load(item_id)
        if isinstance(self.researcher, InitiativeResearch):
            self.preflight(item_id)
        source = Path(data['workspace']) if data['workspace'] else self.repository_for(item_id)
        folder = self.runtime / 'initiative-research' / item_id / secrets.token_hex(12)
        item = self.initiatives.get(item_id)
        context = {'initiative': {k: item[k] for k in ('title', 'raw_signal', 'goal', 'non_goals', 'acceptance')},
                   'discussion': data['messages'], 'previous_proposal': data['proposal'], 'project': self.project(item_id),
                   'success_metric': item.get('success_metric', ''), 'previous_documents': data.get('documents', [])[-1:]}
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
            current['warning'] = ('调研期间源码有更新。本轮发现和问题已保留；授权执行前需要重新核对最新源码。'
                                  if result.get('changed_sources') else '')
            self._event(current, 'codex', '\n'.join(proposal['findings']), questions=proposal['questions'])
            if current['warning']:
                self._event(current, 'system', current['warning'], changed_sources=result['changed_sources'])
            self._save(current)

    def confirm(self, item_id, actor, revision, reviewer):
        actor, reviewer = self.actor(actor), self.actor(reviewer)
        with self.lock:
            data = self._load(item_id)
            self._check(data, revision, {'ready'})
            item = self.initiatives.get(item_id)
            if item['version'] != data['initiative_version']:
                raise ValueError('事项内容已变化，请重新调研')
            proposal = data['proposal']
            if self.projects and not data['documents'][-1].get('prd_confirmation'):
                raise ValueError('请先分别确认 PRD，再确认技术方案')
            if not data.get('documents'):
                self._documents(data, item, proposal)
            source = Path(data['workspace']) if data['workspace'] else self.repository_for(item_id)
            if manifest(source, self.runtime) != data['research_manifest']:
                raise ValueError('调研所依据的源码已变化，请重新调研')
            plan = prepare_daily(source, self.runtime, actor, proposal['goal'], '\n'.join(proposal['acceptance']),
                                 proposal['write_scope'], '\n'.join(proposal['non_goals']) or '不扩大本次目标', project=self.project(item_id))
            plan['spec_text'] += '\n\n产品与技术方案版本：\n\n' + json.dumps(data['documents'][-1], ensure_ascii=False)
            plan.update(plan_id=secrets.token_hex(18), expires_at=time.time() + 900)
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
            self._event(data, 'user', '确认本期目标、验收条件与不做范围；验收人：' + reviewer, actor=actor)
            self._save(data)
        return self.get(item_id)

    def execute(self, item_id, actor, revision):
        actor = self.actor(actor)
        if not self.enabled:
            raise ValueError('当前服务未启用代码执行')
        if self.submitter is submit_daily:
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
                raise ValueError('执行方案已过期，请重新调研确认')
            if self.initiatives.get(item_id)['version'] != data['initiative_version']:
                raise ValueError('已确认的事项发生变化，请重新核对')
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
        source = Path(data['workspace']) if data['workspace'] else self.repository_for(item_id)
        def created(task):
            with self.lock:
                current = self._load(item_id)
                current['active_task_id'] = task['id']
                current['iterations'].append({'task_id': task['id'], 'plan_id': data['plan']['plan_id'],
                                              'document_version': data['plan'].get('document_version')})
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
            self.tasks.review(data['active_task_id'], actor, 'approve', note.strip())
            data['stage'] = 'accepted'
            self._event(data, 'user', '接受本轮候选：' + note.strip(), actor=actor)
            self._save(data)
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
        workspace = Path(data['workspace'])
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
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding='utf-8')
        written = []
        try:
            for p in changed:
                target = repository / p
                if target.is_symlink() or not target.resolve().is_relative_to(repository):
                    raise ValueError('集成目标路径越界')
                actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
                if actual != before.get(p):
                    raise ValueError('集成期间源文件变化，已停止')
                if p in candidate:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((workspace / p).read_bytes())
                else:
                    target.unlink()
                written.append(p)
            if manifest(repository, self.runtime) != candidate:
                raise ValueError('集成后的源码与已验收候选不一致')
        except Exception:
            conflicts = []
            for p in reversed(written):
                content = backups[p]
                target = repository / p
                if target.is_symlink() or not target.resolve().is_relative_to(repository):
                    conflicts.append(p)
                    continue
                actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
                if actual != candidate.get(p):
                    conflicts.append(p)
                    continue
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
            receipt.update(state='rollback_conflict' if conflicts else 'rolled_back', conflicts=conflicts)
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding='utf-8')
            raise
        receipt.update(state='integrated', after_manifest=candidate)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding='utf-8')
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
