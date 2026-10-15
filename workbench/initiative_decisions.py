"""Human delivery decisions over the existing initiative and candidate lifecycle."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import time
from pathlib import Path

from .daily_delivery import manifest, prepare_daily, submit_daily


MODES = {'standard', 'quick', 'course', 'strict'}
REASONS = {'implementation', 'technical', 'requirement', 'environment'}


def course_bound(item, data):
    return bool(data.get('v0') or (data.get('delivery_route') or {}).get('mode') == 'course'
                or re.search(r'(?:^|[-/])(?:COURSE|L\d{2})(?:$|[-/])',
                             item.get('requirement_id', '') + '/' + data.get('linked_requirement_id', ''), re.I))


def policy(item, data, root):
    """Risk recommendations never grant permission or change saved decisions."""
    scope = (data.get('proposal') or {}).get('write_scope') or []
    protected = re.compile(r'(?:inventory|stock|payment|auth|permission|purchase|order|migration|schema|deploy|security|execution|'
                           r'initiative_workflow|initiative_decisions|workflow\.py|task_store|project_delivery|process_guard|'
                           r'runtime_store|tool_registry|http_origin|mutation_receipts|'
                           r'(?:^|/)(?:AGENTS\.md|FDE_SPEC\.md|\.codex|\.github|eval|agent|deploy|harness_web)(?:$|/)|'
                           r'(?:control_surface|delivery_runtime|daily_delivery|repair_loop|spec|hooks?|shell_provider)\.py|'
                           r'库存|支付|权限|采购|订单|部署|护栏)', re.I)
    local_files = bool(scope) and len(scope) <= 3 and all(
        isinstance(p, str) and Path(p).suffix and not (Path(root) / p).is_dir()
        and not protected.search(p.replace('\\', '/')) for p in scope)
    course = course_bound(item, data)
    quick = item.get('risk_lane') == 'quick' and local_files and not course
    selected = (data.get('delivery_route') or {}).get('mode', 'standard')
    mode = 'course' if course else 'strict' if item.get('risk_lane') == 'strict' else selected
    reasons = list(item.get('risk_reasons') or [])
    if not local_files:
        reasons.append('快捷交付只适用于最多三个明确文件，关键业务和执行控制保留分步确认')
    if course:
        reasons.append('课程事项保留分步判断、失败、修订和具名验收证据')
    if mode == 'quick' and not quick:
        mode = 'standard'
        reasons.append('当前范围已不满足快捷条件，需要重新核对分流决定')
    return {'mode': mode, 'quick_eligible': quick, 'course_bound': course,
            'reasons': reasons, 'decision': data.get('delivery_route')}


def confirmation_digest(data):
    plan = data.get('plan') or {}
    frozen = {key: plan.get(key) for key in ('request', 'acceptance', 'write_scope', 'spec_text',
              'project', 'profile', 'document_version', 'learning_binding_id', 'budget_policy')}
    if plan.get('continuation_run_id'):
        frozen['continuation_run_id'] = plan['continuation_run_id']
    frozen.update(document=(data.get('documents') or [None])[-1], proposal=data.get('proposal'),
                  reviewer=data.get('reviewer'), delivery_route=data.get('delivery_route'))
    return hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class InitiativeDecisions:
    def __init__(self, workflow):
        self.workflow = workflow

    def configure(self, item_id, actor, revision, mode, note):
        w = self.workflow
        actor = w.actor(actor)
        if mode not in MODES or not isinstance(note, str) or not note.strip() or len(note) > 3000:
            raise ValueError('请选择交付路径并填写本次判断理由')
        with w.lock:
            data = w._load(item_id)
            w._check(data, revision, {'idle', 'clarifying', 'ready'})
            current = policy(w.initiatives.get(item_id), data, w.repository_for(item_id))
            if current['course_bound'] and mode != 'course':
                raise ValueError('课程事项不能切换为简化交付；请保留课程判断与验收证据')
            if mode == 'quick' and not current['quick_eligible']:
                raise ValueError('当前事项不满足快捷交付条件，请保留分步确认')
            if current['mode'] == 'strict' and mode not in {'strict', 'course'}:
                raise ValueError('关键业务事项必须保留严格的分步确认')
            decision = {'mode': mode, 'actor': actor, 'note': note.strip(), 'at': time.time(),
                        'initiative_version': data['initiative_version'],
                        'document_version': data.get('document_version'), 'write_scope': (data.get('proposal') or {}).get('write_scope', [])}
            data['delivery_route'] = decision
            data.setdefault('route_decisions', []).append(copy.deepcopy(decision))
            w._event(data, 'user', '选择交付路径：' + mode + '\n判断理由：' + note.strip(), actor=actor)
            w._save(data)
        return w.get(item_id)

    def confirm_execute(self, item_id, actor, revision, reviewer, metric, confirmed=False):
        w = self.workflow
        actor, reviewer = w.actor(actor), w.actor(reviewer)
        if confirmed is not True:
            raise ValueError('请明确核对目标、范围与验收条件并授权执行')
        with w.lock:
            data = w._load(item_id)
            w._check(data, revision, {'ready'})
            route = policy(w.initiatives.get(item_id), data, w.repository_for(item_id))
            if route['mode'] != 'quick' or not route['quick_eligible']:
                raise ValueError('仅已具名选择的局部快捷事项可以合并确认与授权')
            source = w.workspace_path(data['workspace']) if data.get('workspace') else w.repository_for(item_id)
            if manifest(source, w.runtime) != data.get('research_manifest'):
                raise ValueError('调研所依据的源码已变化，请重新核对后确认')
            # Each existing confirmation still records its real actor and version.
            result = w.confirm_prd(item_id, actor, revision, metric)
            result = w.confirm(item_id, actor, result['revision'], reviewer)
            return w.execute(item_id, actor, result['revision'])

    def validate_frozen(self, item_id, data, *, check_source=True):
        w = self.workflow
        item, plan = w.initiatives.get(item_id), data.get('plan')
        if not plan or item['version'] != data['initiative_version']:
            raise ValueError('已确认需求发生变化，请重新核对需求与方案')
        if plan.get('confirmation_sha256'):
            if plan['confirmation_sha256'] != confirmation_digest(data):
                raise ValueError('已确认的目标、方案、范围或预算发生变化，请重新确认')
        else:
            # Legacy plans can renew only when their frozen contract still agrees
            # with the original confirmed document, never merely because it exists.
            doc = (data.get('documents') or [{}])[-1]
            proposal = data.get('proposal') or {}
            if (not doc.get('technical_confirmation') or doc.get('status') != 'confirmed'
                    or (w.projects and not doc.get('prd_confirmation'))
                    or plan.get('document_version') != doc.get('version')
                    or plan.get('request') != proposal.get('goal')
                    or plan.get('acceptance') != '\n'.join(proposal.get('acceptance', []))
                    or plan.get('write_scope') != doc.get('technical_plan', {}).get('write_scope')
                    or doc.get('prd', {}).get('goal') != proposal.get('goal')
                    or doc.get('prd', {}).get('acceptance') != proposal.get('acceptance')):
                raise ValueError('历史方案缺少可核对的原确认合同，请重新确认')
        project = w.project(item_id)
        frozen_project = plan.get('project')
        if project and (not frozen_project or any(project[k] != frozen_project.get(k) for k in ('id', 'root_path', 'eval_command'))):
            raise ValueError('项目位置或质量检查命令发生变化，请重新确认技术方案')
        if hasattr(w, 'delivery_runtime'):
            from .delivery_runtime import _profile_fingerprint
            runtime = w.delivery_runtime._load(item_id)
            composition = w.delivery_runtime.sessions.composition(runtime['profile_id'])
            frozen = plan.get('profile') or {}
            if (not composition['ready'] or frozen.get('id') != runtime['profile_id']
                    or frozen.get('sha256') != _profile_fingerprint(composition)):
                raise ValueError('运行配置发生变化或不可用，请重新确认技术方案')
        if plan.get('budget_policy', {}) != (data.get('repair_loop_config') or {}):
            raise ValueError('执行预算发生变化，请重新确认授权')
        source = w.workspace_path(data['workspace']) if data.get('workspace') else w.repository_for(item_id)
        current = manifest(source, w.runtime)
        if check_source and current != plan['source_manifest']:
            raise ValueError('已授权源码发生变化，请重新核对技术方案')
        if plan.get('learning_binding_id'):
            w.learning.validate_binding(plan['learning_binding_id'], source)
        return source, current

    def renew(self, item_id, actor, revision, note, confirmed=False):
        w = self.workflow
        actor = w.actor(actor)
        if confirmed is not True or not isinstance(note, str) or not note.strip() or len(note) > 3000:
            raise ValueError('请填写续期核对依据并明确确认；续期不会启动执行')
        if getattr(w, 'require_preflight', w.submitter is submit_daily):
            w.preflight(item_id)
        if hasattr(w, 'project_plans'):
            w.project_plans.assert_ready(item_id)
        with w.lock:
            data = w._load(item_id)
            w._check(data, revision, {'confirmed'})
            if actor != data['plan']['actor']:
                raise ValueError('请由原授权人核对并续期')
            self.validate_frozen(item_id, data)
            old_expiry = data['plan']['expires_at']
            new_expiry = time.time() + 900
            record = {'actor': actor, 'note': note.strip(), 'at': time.time(), 'plan_id': data['plan']['plan_id'],
                      'document_version': data.get('document_version'), 'previous_expires_at': old_expiry,
                      'expires_at': new_expiry, 'source_sha256': data['plan']['source_sha256'], 'automatic_execution': False}
            data.setdefault('authorization_renewals', []).append(record)
            data['plan']['expires_at'] = new_expiry
            data['plan']['confirmation_sha256'] = confirmation_digest(data)
            w._event(data, 'user', '核对未变化的合同并续期执行授权：' + note.strip(), **record)
            w._save(data)
        return w.get(item_id)

    def rework(self, item_id, actor, revision, reason, note):
        w = self.workflow
        actor = w.actor(actor)
        if reason not in REASONS or not isinstance(note, str) or not note.strip() or len(note) > 10000:
            raise ValueError('请选择返工原因并填写实际失败与修订依据')
        with w.lock:
            data = w._load(item_id)
            w._check(data, revision, {'review', 'rework', 'failed', 'interrupted', 'cancelled'})
            if data.get('v0'):
                raise ValueError('课程 V0 请保留原始合同，使用课程提交入口修订与复验')
            if reason == 'implementation' and (data.get('repair_loop_config') or {}).get('enabled'):
                from .repair_loop import project
                loop = project(data['repair_loop_config'], data.get('iterations', []), w.tasks)
                if (loop['rounds'] >= loop['config']['max_rounds'] or loop['tokens_remaining'] <= 0
                        or loop['elapsed_seconds'] >= loop['config']['time_budget_seconds']):
                    raise ValueError('Loop 已停止：本轮修复超过原授权轮数或预算，请重新核对边界')
                if not loop['can_continue'] and loop['status'] != 'converged':
                    raise ValueError('Loop 已停止：' + loop['reason'])
            source = current = plan = None
            continuation_budget = None
            recheck_only = bool(reason == 'environment' and data.get('active_task_id')
                                and w.tasks.get(data['active_task_id'])['status'] in {'review', 'rework'})
            if reason in {'implementation', 'environment'}:
                source, current = self.validate_frozen(item_id, data, check_source=False)
                if data.get('candidate_manifest') and current != data['candidate_manifest']:
                    raise ValueError('候选尚未核对或已被修改，请先核对原候选与失败记录')
                from .execution import CodexExecutionRunner
                original = data['plan']['source_manifest']
                changed = [p for p in set(original) | set(current) if original.get(p) != current.get(p)]
                if any(not CodexExecutionRunner._allowed(p, data['plan']['write_scope']) for p in changed):
                    raise ValueError('候选存在范围外变化，请先处理越界文件并核对失败证据')
                if reason == 'implementation' and not data.get('workspace'):
                    raise ValueError('尚无实现候选；请先恢复环境并核对原方案')
            if reason == 'implementation' or (reason == 'environment' and not recheck_only):
                original = data['plan']
                if hasattr(w, 'delivery_runtime'):
                    continuation_budget = w.delivery_runtime.continuation_budget(item_id, original['plan_id'])
                plan = prepare_daily(source, w.runtime, actor, data['proposal']['goal'], original['acceptance'],
                                     original['write_scope'], '\n'.join(data['proposal']['non_goals']) or '不扩大本次目标', project=w.project(item_id))
                plan.update(plan_id=secrets.token_hex(18), expires_at=time.time() + 900,
                            document_version=data['document_version'], budget_policy=copy.deepcopy(data.get('repair_loop_config') or {}))
                if continuation_budget is not None:
                    plan['continuation_run_id'] = original['plan_id']
                plan['spec_text'] += '\n\n已确认产品与技术方案：\n' + json.dumps(data['documents'][-1], ensure_ascii=False)
                plan['request'] += '\n本轮' + ('实现修复' if reason == 'implementation' else '环境恢复核对') + '要求：' + note.strip()
                plan['spec_text'] += '\n\n本轮修订依据（沿用原目标与写集，核对后另行授权）：\n' + note.strip()
                if hasattr(w, 'delivery_runtime'):
                    w.delivery_runtime.freeze_profile(item_id, plan)
                binding = w.learning.bind(item_id, plan['plan_id'], data.get('learning_decision'), source, actor)
                if binding:
                    plan['learning_binding_id'] = binding['id']
                    plan['spec_text'] += '\n\n已采用经验：\n' + json.dumps([w.learning.execution_summary(a) for a in binding['assets']], ensure_ascii=False)
            record = {'reason': reason, 'note': note.strip(), 'actor': actor, 'at': time.time(),
                      'task_id': data.get('active_task_id'), 'document_version': data.get('document_version'),
                      'previous_plan_id': (data.get('plan') or {}).get('plan_id'), 'error': data.get('error'),
                      'source_sha256': (data.get('plan') or {}).get('source_sha256')}
            if continuation_budget is not None:
                record['retained_budget'] = continuation_budget
            if data.get('active_task_id'):
                task = w.tasks.get(data['active_task_id'])
                report = task.get('result') or {}
                record['failure_evidence'] = {'task_status': task['status'], 'error': task.get('error'),
                    'summary': report.get('summary'), 'report_path': report.get('report_path'),
                    'report_sha256': report.get('report_sha256')}
            data.setdefault('rework_decisions', []).append(record)
            w._event(data, 'user', '按原因返工：' + reason + '\n' + note.strip(), **record)
            if reason in {'technical', 'requirement'}:
                if reason == 'requirement':
                    data.pop('prd_reuse_base', None)
                data['pending_rework_reason'] = reason
                w._save(data)
                return w.discuss(item_id, actor, data['revision'], note.strip())
            if plan:
                if data['stage'] == 'review':
                    w.tasks.review(data['active_task_id'], actor, 'reject', note.strip())
                    w.learning.finish(data['active_task_id'], note=note.strip())
                data.update(stage='confirmed', plan=plan, error='')
                plan['confirmation_sha256'] = confirmation_digest(data)
                w._save(data)
                return w.get(item_id)
            data['stage'] = 'rework'
            w._save(data)
            return w.run_eval(item_id, actor, data['revision'])
