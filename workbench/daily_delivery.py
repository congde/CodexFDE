"""Daily development on a frozen copy of this project's current source."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eval.harness import EVALS
from .course_snapshot import source_paths, _git
from .course_workspace import LessonSubprocessEvalRunner
from .execution import CodexExecutionRunner, normalize_write_scope
from .spec import parse_spec
from .workflow import run_task


def manifest(repository, runtime):
    root = Path(repository).resolve()
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_paths(root, Path(runtime).resolve())}


def prepare_daily(repository, runtime, actor, request, acceptance, write_scope, non_goals='不扩大本次需求范围'):
    if not isinstance(actor, str) or not actor.strip() or actor.strip().startswith('agent:') or len(actor) > 80:
        raise ValueError('请填写本次授权人的署名')
    for value in (request, acceptance, non_goals):
        if not isinstance(value, str) or not value.strip() or len(value) > 10000:
            raise ValueError('需求、验收条件和范围说明必须是非空文本，最多 10000 字')
    if not isinstance(write_scope, list) or any(not isinstance(p, str) for p in write_scope):
        raise ValueError('修改范围须为路径列表')
    scopes = normalize_write_scope(write_scope)
    if not scopes:
        raise ValueError('请指定本次允许修改的文件或目录')
    # This first daily lane supports the repository source; no lesson slicing.
    source = manifest(repository, runtime)
    from .course_snapshot import SOURCE_DIRS, ROOT_FILES
    if any(p.split('/')[0] not in SOURCE_DIRS and p not in ROOT_FILES for p in scopes):
        raise ValueError('当前日常研发支持本项目源码目录；请填写项目内的源文件范围')
    spec = '\n\n'.join('## ' + title + '\n\n' + value for title, value in [
        ('来源', '日常研发需求，提交人：' + actor.strip()), ('目标', request.strip()),
        ('非目标', non_goals.strip()), ('约束', '遵守 AGENTS.md；修改范围：' + ', '.join(scopes)),
        ('验收用例', acceptance.strip()),
        ('完成定义', '实现本次需求并补充相关测试，阻断级 Eval 通过；交付真实 Diff，由具名人验收。')])
    parse_spec(spec)
    return {'kind': 'daily', 'actor': actor.strip(), 'request': request.strip(),
            'acceptance': acceptance.strip(), 'write_scope': scopes, 'spec_text': spec,
            'source_manifest': source, 'source_sha256': hashlib.sha256(
                json.dumps(source, sort_keys=True).encode()).hexdigest(),
            'eval_cases': [name for name, level, _ in EVALS if level == 'blocking'],
            'boundary': '以当前项目源码（含未提交修改）为起点，在隔离副本执行；保留前后检查与完整补丁，验收不会自动合入源项目。'}


def submit_daily(repository, runtime, tasks, plan, on_task_created, *, runner_factory=CodexExecutionRunner,
                 eval_factory=LessonSubprocessEvalRunner):
    runtime = Path(runtime).resolve()
    if manifest(repository, runtime) != plan['source_manifest']:
        raise ValueError('项目源码已变化，请重新准备方案，避免从旧版本开始工作')
    folder = runtime / 'daily-delivery' / plan['plan_id']
    folder.mkdir(parents=True, exist_ok=False)
    spec_path = folder / 'SPEC.md'
    spec_path.write_text(plan['spec_text'], encoding='utf-8')
    task = tasks.create(plan['request'], requirement_id='REQ-DAILY-' + plan['plan_id'][:10].upper(),
                        spec_path=str(spec_path), actor=plan['actor'], execution_mode='codex',
                        write_scope=plan['write_scope'])
    on_task_created(task)
    tasks.append_event(task['id'], '本次需求 Spec 已冻结', actor=plan['actor'],
                       evidence={'sha256': hashlib.sha256(spec_path.read_bytes()).hexdigest()})
    workspace = folder / 'workspace'
    workspace.mkdir()
    for relative, digest in plan['source_manifest'].items():
        content = (Path(repository) / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError('复制期间源码变化，请重新准备方案')
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if manifest(repository, runtime) != plan['source_manifest']:
        raise ValueError('复制期间源码变化，请重新准备方案')
    _git(workspace, 'init', '--quiet')
    _git(workspace, 'add', '--all')
    _git(workspace, '-c', 'user.name=Workbench snapshot', '-c', 'user.email=workbench@localhost',
         '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Daily development source snapshot')
    tasks.append_event(task['id'], '已创建日常研发隔离副本', actor=plan['actor'], evidence={
        'path': str(workspace), 'source_sha256': plan['source_sha256'],
        'baseline_commit': _git(workspace, 'rev-parse', 'HEAD')})
    cases = tuple(plan['eval_cases'])
    before = eval_factory(workspace, runtime, task['id'], cases, 'daily-before')()
    tasks.append_event(task['id'], '日常研发执行前检查', actor=plan['actor'], evidence=before)
    # Existing green checks are normal for new development; do not manufacture red.
    runner = runner_factory(workspace, runtime)
    def execute(current):
        def progress(line):
            tasks.append_event(task['id'], '日常研发执行输出', actor='agent:codex',
                               evidence={'line': line[-8000:]})
        evidence = runner(current, on_codex_line=progress)
        if evidence.get('success') and not evidence.get('changed_files'):
            evidence = {**evidence, 'success': False, 'message': '没有实际文件改动，不能认定需求已实现'}
        return evidence
    result = run_task(tasks, task['id'], plan['actor'], execution_runner=execute,
                      suite_runner=eval_factory(workspace, runtime, task['id'], cases, 'daily-after'))
    # Include new files in the patch without committing or touching the source index.
    _git(workspace, 'add', '--intent-to-add', '--all', '--', *plan['write_scope'])
    patch_path = folder / 'changes.patch'
    import subprocess
    diff = subprocess.run(['git', 'diff', '--binary', 'HEAD'], cwd=workspace, capture_output=True, check=True)
    patch_path.write_bytes(diff.stdout)
    tasks.append_event(task['id'], '日常研发交付包已保存', actor=plan['actor'], evidence={
        'workspace': str(workspace), 'patch_path': str(patch_path),
        'patch_sha256': hashlib.sha256(diff.stdout).hexdigest(), 'source_sha256': plan['source_sha256'],
        'status': result['status'], 'merged': False})
    return {'task': tasks.get(task['id'])}
