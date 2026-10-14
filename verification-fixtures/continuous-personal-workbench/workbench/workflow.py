"""Spec -> actual invocation -> complete Diff -> command Eval -> pending review."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from uuid import uuid4

from . import execution
from .bootstrap_handoff import require_accepted_a
from .bootstrap_source import source_identity
from .course_workspace import require_source_unchanged
from .task_store import TaskError, required


def _check_workspace(store, workspace_dir, allowed, timeout):
    if not workspace_dir:
        raise TaskError('workspace_missing', '代码执行必须声明工作目录')
    workspace = Path(workspace_dir).resolve()
    if not workspace.is_dir() or not workspace.is_relative_to(store.control_root / '.runtime'):
        raise TaskError('invalid_workspace', '只能执行个人 .runtime 内已存在的隔离候选')
    if not allowed:
        raise TaskError('write_set_missing', '代码执行必须声明允许写集')
    for name in allowed:
        path = PurePosixPath(name)
        if (path.is_absolute() or '\\' in name or '..' in path.parts or path.as_posix() != name
                or name not in execution.INVENTORY_WRITES):
            raise TaskError('invalid_write_set', '未确认的允许写文件：' + name)
    if not execution.valid_timeout(timeout):
        raise TaskError('timeout_missing', '代码执行必须声明有效超时')
    hashes = execution.file_hashes(workspace)
    if execution.sensitive_files(workspace, hashes):
        raise TaskError('sensitive_candidate', '候选包含敏感文件或链接，拒绝执行')
    return str(workspace), hashes


def _save_execution(directory, result):
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in ('stdout', 'stderr'):
        path = directory / (name + '.txt')
        path.write_text(result.get(name, ''), encoding='utf-8')
        paths[name] = str(path)
    path = directory / 'execution.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    paths['result'] = str(path)
    return {**result, 'paths': paths}


def run_task(store, task_id, *, actor, execute_code=None, workspace_dir=None,
             allowed_write_files=None, execution_timeout=None):
    from eval import harness
    actor = required(actor, 'actor')
    task = store.get(task_id)
    if task['kind'] == 'generic':
        from .generic_execution import run_generic
        return run_generic(store, task, actor=actor, execute_code=execute_code, workspace_dir=workspace_dir,
                           allowed_write_files=allowed_write_files, execution_timeout=execution_timeout)
    if task['status'] not in ('spec_ready', 'rework'):
        raise TaskError('invalid_state', '只有 spec_ready/rework 可以开始；失败保留并新建任务')
    if not execute_code and task['kind'] == 'B':
        return store.fail(task_id, 'b_execution_required', 'B 必须在候选中明确授权执行代码')
    allowed = list(allowed_write_files or [])
    try:
        identity = source_identity(store.control_root)
        if task['kind'] == 'B':
            require_accepted_a(store, task['bootstrap_task_id'])
            require_source_unchanged(task['isolation'])
            if task['status'] == 'rework':
                raise TaskError('new_candidate_required', 'B 打回后须 course-submit 新建候选，保留旧记录')
        if execute_code:
            workspace, before = _check_workspace(store, workspace_dir, allowed, execution_timeout)
            if task['kind'] == 'B':
                if not task.get('isolation') or workspace != task['isolation']['path']:
                    raise TaskError('candidate_mismatch', 'B 只能使用绑定的同一候选')
                expected = task['isolation'].get('baseline') or {name: entry['sha256'] for name, entry in task['isolation']['sources'].items()}
                if before != expected:
                    raise TaskError('candidate_changed', '执行前候选已偏离已保存的来源基线')
        else:
            if workspace_dir or allowed:
                raise TaskError('readonly_arguments', '仅复验使用控制目录与固定检查，不接收写集/目录')
            workspace = str(store.control_root)
            before = None
            execution_timeout = execution_timeout or 900
            if not execution.valid_timeout(execution_timeout):
                raise TaskError('invalid_timeout', '复验超时无效')
    except (OSError, ValueError) as error:
        return store.fail(task_id, getattr(error, 'code', 'preflight_failed'), str(error))
    attempt = store.runtime / 'evidence' / task_id / uuid4().hex
    store.update(task_id, detail='已开始受控执行' if execute_code else '已开始只读复验',
                 evidence={'workspace': workspace, 'allowed_write_files': allowed,
                           'timeout': execution_timeout, 'source_identity': identity,
                           'before': before, 'actor': actor}, expected_status=('spec_ready', 'rework'),
                 status='running', executor=actor if execute_code else None,
                 workspace_path=workspace, write_scope=allowed,
                 verification_actor=None if execute_code else actor, ok=True, errors=[],
                 execution=None, diff=None, eval=None, source_identity=None, accepted_source=None,
                 reviewer=None, review_decision=None, acceptance='pending_human_review')
    result = None
    diff = None
    report = None
    try:
        if execute_code:
            # Recheck immediately before the real adapter, as well as after it and after Eval.
            if task['kind'] == 'B':
                require_accepted_a(store, task['bootstrap_task_id'])
                require_source_unchanged(task['isolation'])
            prompt = ('依据下列已绑定 Spec 完成当前隔离候选中的最小需求。只允许修改列出的精确文件；'
                      '不改控制工作台、客户原目录、Spec、密钥、运行库；不提交、不签署；'
                      '失败如实报告，不削弱已有断言。需要新增产品测试时只允许已确认的测试文件。\n'
                      + '允许写文件：' + json.dumps(allowed, ensure_ascii=False)
                      + '\n需求：' + task['request'] + '\nSpec SHA256：' + task['spec']['sha256']
                      + '\nSpec：\n' + task['spec']['text'])
            try:
                result = execution.run_codex(workspace=workspace, prompt=prompt, timeout=execution_timeout)
            except Exception as error:
                result = {'exitcode': None, 'stdout': '', 'stderr': '', 'timed_out': False,
                          'error': f'{type(error).__name__}: {error}',
                          'invocation': {'cwd': workspace, 'timeout': execution_timeout, 'stdin': prompt}}
            result = _save_execution(attempt, result)
            diff = execution.hash_diff(before, execution.file_hashes(workspace), allowed, workspace)
            store.update(task_id, detail='已保存执行器真实结果与完整哈希 Diff',
                         evidence={'execution': result, 'diff': diff}, execution=result, diff=diff)
            if task['kind'] == 'B':
                require_accepted_a(store, task['bootstrap_task_id'])
                require_source_unchanged(task['isolation'])
            if diff['violations'] or diff['sensitive']:
                raise TaskError('write_scope_violation', '实际写集越界或包含敏感文件')
            if result['timed_out']:
                raise TaskError('execution_timeout', '执行器超时，已保留输出')
            if result['exitcode'] != 0 or result.get('error'):
                raise TaskError('execution_failed', '执行器失败，已保留真实结果')
            from .course_workspace import inventory_commands
            commands = task.get('eval_commands') or inventory_commands(workspace)
        else:
            commands = [[sys.executable, '-B', '-S', '-X', 'utf8', '-m', 'unittest', 'tests.test_l04_workbench_v0', '-v']]
        store.update(task_id, detail='已登记最小 Eval 命令', evidence={'commands': commands, 'cwd': workspace})
        eval_before = execution.file_hashes(workspace) if execute_code else None
        report = harness.evaluate(commands, cwd=workspace, timeout=execution_timeout, report_path=attempt / 'eval.json')
        # Read and verify a saved, legal report instead of trusting an in-memory green status.
        loaded = harness.load_report(report['path'])
        if loaded != {key: report[key] for key in ('schema', 'status', 'checks')}:
            raise TaskError('eval_report_changed', '保存的 Eval 报告与实际结果不同')
        if hashlib.sha256(Path(report['path']).read_bytes()).hexdigest() != report['sha256']:
            raise TaskError('eval_report_changed', 'Eval 报告内容身份不符')
        if execute_code:
            diff = execution.hash_diff(before, execution.file_hashes(workspace), allowed, workspace)
            if diff['violations'] or diff['sensitive']:
                raise TaskError('write_scope_violation', 'Eval 后实际写集越界或含敏感文件')
            if diff['after'] != eval_before:
                raise TaskError('eval_side_effect', '最小 Eval 改变了候选文件，不能进入 review')
        if task['kind'] == 'B':
            require_accepted_a(store, task['bootstrap_task_id'])
            require_source_unchanged(task['isolation'])
        if source_identity(store.control_root) != identity:
            raise TaskError('source_changed', '执行/复验期间控制源码变化，不能进入 review')
        if report['status'] != 'passed':
            raise TaskError('eval_failed', '最小 Eval 失败，不能进入 review')
        return store.update(task_id, detail='自动检查通过，等待独立具名人审',
            evidence={'eval': report, 'diff': diff, 'source_identity': identity}, expected_status=('running',),
            status='review', ok=True, eval=report, diff=diff, source_identity=identity,
            acceptance='pending_human_review')
    except Exception as error:
        if execute_code and diff is None:
            try:
                diff = execution.hash_diff(before, execution.file_hashes(workspace), allowed, workspace)
            except Exception as observation_error:
                diff = {'before': before, 'after': None, 'observation_error': str(observation_error)}
        return store.fail(task_id, getattr(error, 'code', 'workflow_failed'), str(error),
                          execution=result, diff=diff, eval=report)
