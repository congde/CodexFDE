"""Run frozen personal tasks through the existing adapter and minimal command Eval."""
import hashlib
from pathlib import Path
from uuid import uuid4

from . import execution
from .bootstrap_handoff import current_a
from .bootstrap_source import source_identity
from .task_contract import require_eval_assets
from .task_store import TaskError, required


def run_generic(store, task, *, actor, execute_code=None, workspace_dir=None,
                allowed_write_files=None, execution_timeout=None):
    from eval import harness
    from .workflow import _save_execution
    actor = required(actor, 'actor')
    task_id = task['task_id']
    if task['status'] not in ('spec_ready', 'rework'):
        raise TaskError('invalid_state', '通用任务仅允许 spec_ready/rework 开始')
    # Rejected overrides cannot mutate a valid frozen task.
    if ((execute_code is not None and execute_code != task['execute_code'])
            or (workspace_dir is not None and str(Path(workspace_dir).resolve()) != task['workspace_path'])
            or (allowed_write_files is not None and sorted(set(allowed_write_files)) != task['write_scope'])
            or (execution_timeout is not None and execution_timeout != task['execution_timeout'])):
        raise TaskError('frozen_permission_changed', 'run 不得改变冻结目录、执行模式、写集或超时；另建任务')
    workspace, allowed, timeout = task['workspace_path'], task['write_scope'], task['execution_timeout']
    before = None
    result = report = diff = None
    attempt = store.runtime / 'evidence' / task_id / uuid4().hex
    try:
        current_a(store, task['bootstrap_task_id'], fixture=task.get('fixture', False))
        if task.get('isolation'):
            from .course_workspace import require_source_unchanged
            require_source_unchanged(task['isolation'])
        identity = source_identity(store.control_root)
        require_eval_assets(workspace, task['eval_commands'])
        before = execution.file_hashes(workspace)
        if before != task['frozen_workspace']:
            raise TaskError('candidate_changed', '候选与创建时冻结内容不符；保留原任务，另建任务')
        if execution.sensitive_files(workspace, before):
            raise TaskError('sensitive_candidate', '候选含敏感材料或链接')
        store.update(task_id, detail='按通用冻结合同开始执行', expected_status=('spec_ready', 'rework'),
                     status='running', executor=actor if task['execute_code'] else None,
                     verification_actor=actor if not task['execute_code'] else None,
                     evidence={'workspace': workspace, 'write_scope': allowed, 'timeout': timeout,
                               'commands': task['eval_commands'], 'source_identity': identity})
        if task['execute_code']:
            prompt = ('按以下冻结 Spec 实现最小变更；只改精确允许文件，不改检查/运行数据/源仓库，'
                      '不提交、不签署。失败如实报告。\n允许文件：' + repr(allowed)
                      + '\nSpec SHA256：' + task['spec_sha256'] + '\n需求：' + task['request']
                      + '\nSpec：\n' + task['spec_text'])
            try:
                result = execution.run_codex(workspace=workspace, prompt=prompt, timeout=timeout)
            except Exception as error:
                result = {'exitcode': None, 'stdout': '', 'stderr': '', 'timed_out': False,
                          'error': str(error), 'invocation': {'cwd': workspace, 'timeout': timeout, 'stdin': prompt}}
            result = _save_execution(attempt, result)
            diff = execution.hash_diff(before, execution.file_hashes(workspace), allowed, workspace)
            from .execution_receipt import save
            receipt = save(store, task_id, attempt.name, result, diff)
            store.update(task_id, detail='已保存通用执行结果和模型 Diff', execution=result, diff=diff,
                         usage=result.get('usage'), evidence=receipt)
            if diff['violations'] or diff['sensitive']:
                raise TaskError('write_scope_violation', '通用任务实际变更超出精确冻结文件')
            if result['timed_out']:
                raise TaskError('execution_timeout', '执行超时，进程树终止，输出已保存')
            if result['exitcode'] != 0 or result.get('error'):
                raise TaskError('execution_failed', '执行失败，不能进入 Eval/review')
        eval_before = execution.file_hashes(workspace)
        current_a(store, task['bootstrap_task_id'], fixture=task.get('fixture', False))
        report = harness.evaluate(task['eval_commands'], cwd=workspace, timeout=timeout,
                                  report_path=attempt / 'eval.json')
        loaded = harness.load_report(report['path'])
        if loaded != {k: report[k] for k in ('schema', 'status', 'checks')} or hashlib.sha256(Path(report['path']).read_bytes()).hexdigest() != report['sha256']:
            raise TaskError('eval_report_changed', '实际保存 Eval 身份不符')
        diff = execution.hash_diff(before, execution.file_hashes(workspace), allowed, workspace)
        if diff['violations'] or diff['sensitive'] or diff['after'] != eval_before:
            raise TaskError('eval_side_effect', 'Eval 改变候选或存在越界')
        current_a(store, task['bootstrap_task_id'], fixture=task.get('fixture', False))
        if identity != source_identity(store.control_root):
            raise TaskError('source_changed', '控制源码在执行中变化')
        if task.get('isolation'):
            require_source_unchanged(task['isolation'])
        if report['status'] != 'passed':
            raise TaskError('eval_failed', '冻结最小 Eval 未通过')
        return store.update(task_id, detail='通用最小 Eval 通过，停在待人审', status='review', ok=True,
                            execution=result, eval=report, diff=diff, source_identity=identity,
                            acceptance='pending_human_review', human_review='pending_human_review',
                            evidence={'eval': report, 'diff': diff})
    except Exception as error:
        return store.fail(task_id, getattr(error, 'code', 'generic_execution_failed'), str(error),
                          execution=result, eval=report, diff=diff)
