"""Explicit local execution plans, separate from ordinary verification submissions."""
from __future__ import annotations

import copy
import json
import secrets
import subprocess
import threading
import time
from pathlib import Path

from .course_delivery import submit_course
from .course_mainline import lesson_baseline_status, lesson_contract
from .execution import CodexExecutionRunner
from .bootstrap_handoff import require_bootstrap_ticket
from .course_requirement import freeze_requirement_spec
from .course_initiative import requirement_from_initiative, check_initiative_binding, link_initiative_task
from .course_versions import default_session_version


class WebExecution:
    def __init__(self, repository, runtime, tasks, *, enabled=False, submitter=None):
        self.repository = Path(repository).resolve()
        self.runtime = Path(runtime).resolve()
        self.tasks = tasks
        self.enabled = enabled
        self.submitter = submitter or submit_course
        self.plans = {}
        self.lock = threading.Lock()
        with self.tasks.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS web_execution_plans (plan_id TEXT PRIMARY KEY, repository TEXT NOT NULL, payload TEXT NOT NULL)')
            rows = connection.execute('SELECT payload FROM web_execution_plans WHERE repository=?', (str(self.repository),)).fetchall()
        for row in rows:
            plan = json.loads(row[0])
            if plan['state'] in {'prepared', 'starting'}:
                plan.update(state='failed', error='工作台已重启。原方案不再执行；请先核对已有任务与失败记录，再准备新的方案。')
                self._save(plan)
            self.plans[plan['plan_id']] = plan

    def _save(self, plan):
        with self.tasks.connect() as connection:
            connection.execute('INSERT OR REPLACE INTO web_execution_plans(plan_id,repository,payload) VALUES (?,?,?)',
                               (plan['plan_id'], str(self.repository), json.dumps(plan, ensure_ascii=False)))

    def readiness(self):
        """Read-only entry diagnostics; never creates a plan or an execution task."""
        if not self.enabled:
            return {'state': 'disabled', 'ready': False,
                    'message': '当前服务只开放复验。需要以网页执行模式重新启动工作台，再核对并授权具体任务。'}
        capability = CodexExecutionRunner(self.repository, self.runtime).capabilities()
        if not capability['codex_available']:
            return {'state': 'cli_unavailable', 'ready': False,
                    'message': '网页执行已开启，但本机 Codex CLI 尚不可用。请在启动工作台的环境中检查 CLI。'}
        return {'state': 'plan_available', 'ready': True,
                'message': '可以准备执行方案。讲次基线、具体范围与验收条件将在下一步检查；确认方案后才开始执行。'}

    def prepare(self, lesson, actor, cases=(), session_ref=None, bootstrap_task_id=None, requirement_spec_text=None,
                initiative_id=None, initiative_version=None):
        if not self.enabled:
            raise ValueError("本服务未开启网页代码执行；请使用 --enable-code-execution 启动工作台")
        if not isinstance(actor, str) or not actor.strip() or actor.strip().startswith('agent:') or len(actor) > 80:
            raise ValueError("请填写本次授权人的署名")
        if not 4 <= lesson <= 16:
            raise ValueError("请选择 L04-L16")
        if not isinstance(cases, (list, tuple)) or any(not isinstance(case, str) or not case.strip() for case in cases):
            raise ValueError("新增检查须为用例名列表")
        contract = lesson_contract(lesson)
        bootstrap = require_bootstrap_ticket(self.tasks, bootstrap_task_id, self.repository) if lesson == 4 else None
        cases = tuple(dict.fromkeys(cases))
        if contract.dynamic_eval_required and (not cases or not session_ref):
            raise ValueError("本讲须提供新增检查用例与本次需求起始基线")
        if set(cases) & set(contract.eval_cases):
            raise ValueError("新增检查不能重复课程已有用例")
        initiative = None
        if initiative_id:
            if not contract.dynamic_eval_required:
                raise ValueError('事项的具体需求执行须选择 L15/L16；其他讲次采用固定课程合同')
            if requirement_spec_text:
                raise ValueError('已关联事项时直接使用保存的需求，不同时接受另一份 Spec')
            requirement_spec_text, initiative = requirement_from_initiative(self.tasks, initiative_id, initiative_version, cases)
        specific = freeze_requirement_spec(contract, requirement_spec_text, cases, required=contract.dynamic_eval_required)
        baseline = lesson_baseline_status(self.repository, lesson)
        commit = baseline.get('baseline_commit')
        if not commit:
            raise ValueError("缺少本讲课程起始基线")
        selected = commit
        version = default_session_version(self.repository, lesson) if not session_ref else None
        if version:
            session_ref = version['commit']
        if session_ref:
            if not isinstance(session_ref, str) or session_ref.startswith('-'):
                raise ValueError("起始基线格式无效")
            resolved = subprocess.run(['git', 'rev-parse', '--verify', f'{session_ref}^{{commit}}'],
                                      cwd=self.repository, capture_output=True, text=True, check=False)
            if resolved.returncode:
                raise ValueError("无法解析本次需求基线")
            selected = resolved.stdout.strip()
            ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, selected],
                                      cwd=self.repository, capture_output=True, check=False)
            if ancestor.returncode:
                raise ValueError("本次需求基线必须位于课程起始基线之后")
        capability = CodexExecutionRunner(self.repository, self.runtime).capabilities()
        if not capability['codex_available']:
            raise ValueError(capability['reason'])
        plan_id = secrets.token_urlsafe(18)
        plan = {'plan_id': plan_id, 'confirmation': secrets.token_urlsafe(32), 'state': 'prepared',
                'lesson': lesson, 'lesson_title': contract.title, 'actor': actor.strip(),
                'request': specific['goal'] if specific else contract.request,
                'acceptance': [specific['acceptance'], *contract.acceptance] if specific else list(contract.acceptance),
                'requirement_spec_text': requirement_spec_text,
                'requirement_sha256': specific['sha256'] if specific else None,
                'non_goals': specific['non_goals'] if specific else None,
                'write_scope': list(contract.write_scope), 'eval_cases': list(contract.eval_cases + cases),
                'additional_eval_cases': list(cases), 'baseline_commit': commit, 'session_commit': selected,
                'material_version': version,
                'bootstrap': bootstrap,
                'initiative': initiative,
                'expires_at': time.time() + 900, 'task_id': None,
                'boundary': '只修改本次隔离副本。先确认检查失败，再修改并复验；检查通过后由你验收，不自动合入源仓库。'}
        with self.lock:
            self._save(plan)
            self.plans[plan_id] = plan
        return copy.deepcopy(plan)

    def get(self, plan_id):
        with self.lock:
            plan = copy.deepcopy(self.plans[plan_id])
        plan.pop('confirmation', None)
        return plan

    def prepare_daily(self, actor, request, acceptance, write_scope, non_goals='不扩大本次需求范围'):
        if not self.enabled:
            raise ValueError('本服务未开启网页代码执行；请使用 --enable-code-execution 启动工作台')
        from .daily_delivery import prepare_daily
        capability = CodexExecutionRunner(self.repository, self.runtime).capabilities()
        if not capability['codex_available']:
            raise ValueError(capability['reason'])
        plan = prepare_daily(self.repository, self.runtime, actor, request, acceptance, write_scope, non_goals)
        plan.update(plan_id=secrets.token_hex(18), confirmation=secrets.token_urlsafe(32),
                    state='prepared', expires_at=time.time() + 900, task_id=None)
        with self.lock:
            self._save(plan)
            self.plans[plan['plan_id']] = plan
        return copy.deepcopy(plan)

    def authorize(self, plan_id, confirmation, actor):
        if not self.enabled:
            raise ValueError('当前服务未开启网页代码执行，请先核对启动模式')
        with self.lock:
            plan = self.plans[plan_id]
            if not isinstance(confirmation, str) or not secrets.compare_digest(plan['confirmation'], confirmation) or actor != plan['actor']:
                raise ValueError("授权与已展示方案不一致")
            if plan['state'] != 'prepared':
                return {'plan_id': plan_id, 'state': plan['state'], 'task_id': plan['task_id']}
            if time.time() > plan['expires_at']:
                raise ValueError("执行方案已过期，请重新查看")
            if plan.get('initiative'):
                check_initiative_binding(self.tasks, plan['initiative'], plan['additional_eval_cases'])
            plan['state'] = 'starting'
            self._save(plan)
            thread = threading.Thread(target=self._run, args=(plan_id,), daemon=True)
            thread.start()
        return {'plan_id': plan_id, 'state': 'starting', 'task_id': None}

    def _run(self, plan_id):
        plan = self.get(plan_id)
        def created(task):
            with self.lock:
                self.plans[plan_id]['task_id'] = task['id']
                self._save(self.plans[plan_id])
            if plan.get('initiative'):
                link_initiative_task(self.tasks, plan['initiative'], task['id'], plan['actor'],
                                     plan['additional_eval_cases'], plan['requirement_sha256'])
            self.tasks.append_event(task['id'], '网页具名授权日常研发' if plan.get('kind') == 'daily' else '网页具名授权课程隔离执行', actor=plan['actor'], evidence=plan)
        try:
            if plan.get('kind') == 'daily':
                from .daily_delivery import submit_daily
                output = submit_daily(self.repository, self.runtime, self.tasks, plan, created)
            else:
                output = self.submitter(repository_root=self.repository, runtime_dir=self.runtime,
                                    lesson_number=plan['lesson'], actor=plan['actor'], execute_code=True,
                                    eval_cases=tuple(plan['additional_eval_cases']),
                                    session_baseline_ref=plan['session_commit'],
                                    bootstrap_task_id=(plan.get('bootstrap') or {}).get('task_id'),
                                    requirement_spec_text=plan.get('requirement_spec_text'),
                                    expected_baseline_commit=plan['baseline_commit'], on_task_created=created)
            with self.lock:
                self.plans[plan_id].update(state='finished', task_id=output['task']['id'])
                self._save(self.plans[plan_id])
        except Exception as error:
            # Publish the execution failure before attempting a secondary task update.
            # A stale/deleted task or a rejected transition must not leave the plan running.
            with self.lock:
                self.plans[plan_id].update(state='failed', error=f'{type(error).__name__}: {error}')
                self._save(self.plans[plan_id])
            current = self.get(plan_id)
            try:
                if current.get('task_id'):
                    task = self.tasks.get(current['task_id'])
                    if task['status'] not in {'failed', 'completed', 'rework', 'dead_letter'}:
                        self.tasks.transition(task['id'], 'failed', '网页课程执行异常，保留失败原因',
                                              actor=plan['actor'], error=str(error))
            except Exception as record_error:
                with self.lock:
                    self.plans[plan_id]['task_record_error'] = (
                        '任务状态尚未同步，请保留原任务并核对记录。' +
                        f'{type(record_error).__name__}: {record_error}')
                    self._save(self.plans[plan_id])
