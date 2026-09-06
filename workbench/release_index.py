"""Assemble traceable release references without certifying or accepting a delivery."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import uuid

from .task_store import TaskStore


def create_release_index(runtime_dir, task_id, *, cold_start=None, product=None, risks=None):
    runtime = Path(runtime_dir).resolve()
    task = TaskStore(runtime / 'workbench.db').get(task_id)
    output = runtime / 'release-index' / task['id'] / uuid.uuid4().hex
    references = {}
    gaps = []

    def reference(label, candidate):
        if not candidate:
            gaps.append(label)
            return
        path = Path(candidate).resolve()
        if not path.is_relative_to(runtime) or path.suffix.lower() not in {'.json', '.md', '.txt', '.log', '.patch', '.csv'}:
            raise ValueError(f'{label} 必须是本运行目录内的证据文件')
        if not path.is_file():
            gaps.append(label)
            return
        content = path.read_bytes()
        if not content.strip():
            gaps.append(label)
            return
        references[label] = {'path': Path(os.path.relpath(path, output)).as_posix(),
                             'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}

    events = task.get('events') or []
    def evidence(detail):
        return next((e.get('evidence') or {} for e in reversed(events) if e.get('detail') == detail), {})

    pre = evidence('执行前课程 Eval 已完成')
    execution = evidence('受控执行阶段完成')
    differential = evidence('课程红绿差分判定已完成')
    result = task.get('result') or {}
    reference('spec', task.get('spec_path'))
    reference('pre_eval', (pre.get('runner') or {}).get('report_path'))
    reference('post_eval', (result.get('runner') or {}).get('report_path'))
    reference('pre_process', (pre.get('runner') or {}).get('receipt_path'))
    reference('post_process', (result.get('runner') or {}).get('receipt_path'))
    reference('cold_start', cold_start)
    reference('product', product)
    reference('remaining_risks', risks)
    if not execution.get('changed_files') or not execution.get('diff'):
        gaps.append('diff')
    reviewer = task.get('reviewed_by') or ''
    if task.get('status') != 'completed' or not reviewer or reviewer.startswith('agent:') or task.get('review_decision') != 'approve':
        gaps.append('human_acceptance')
    if not differential.get('accepted'):
        gaps.append('verified_implementation')
    if execution.get('out_of_scope_files'):
        gaps.append('out_of_scope_changes')
    summary = result.get('summary') or {}
    if summary.get('decision') != 'pass' or summary.get('blocking_failed') != 0:
        gaps.append('post_eval_not_passing')
    frozen = evidence('本次需求 Spec 已冻结')
    if frozen.get('sha256') and 'spec' in references:
        references['spec']['expected_sha256'] = frozen['sha256']
        if references['spec']['sha256'] != frozen['sha256']:
            gaps.append('spec_changed_after_freeze')
    for label, recorded in (('pre_eval', pre), ('post_eval', result)):
        if label in references:
            try:
                actual = json.loads((output / references[label]['path']).read_text(encoding='utf-8'))
                if actual.get('summary') != recorded.get('summary'):
                    gaps.append(label + '_summary_mismatch')
                if label == 'post_eval' and actual.get('results') != result.get('results'):
                    gaps.append('post_eval_results_mismatch')
            except (ValueError, AttributeError):
                gaps.append(label + '_unreadable')

    payload = {'schema': 'workbench.release-index/v1', 'task_id':task['id'],
        'created_at':datetime.now(timezone.utc).isoformat(), 'index_status':'requires_human_review',
        'request':task['request'], 'requirement_id':task.get('requirement_id'),
        'task_status':task['status'], 'execution_mode':task.get('execution_mode'),
        'references':references, 'evidence_gaps':gaps,
        'pre_eval_summary':pre.get('summary'), 'post_eval_summary':result.get('summary'),
        'changed_files':execution.get('changed_files') or [],
        'out_of_scope_files':execution.get('out_of_scope_files') or [],
        'differential':differential,
        'isolation':evidence('已创建课程隔离 Worktree'),
        'human_review':{'reviewer':reviewer or None, 'decision':task.get('review_decision'),
                        'note':task.get('review_note')},
        'limitations':['文件存在及指纹不证明内容真实或足以验收。',
                       '改动摘要可能被执行器截短，不可作为可应用的完整 Patch。',
                       '本命令不批准任务、不发布版本，也不证明学生结业。']}
    output.mkdir(parents=True, exist_ok=False)
    if execution.get('diff'):
        diff_path = output / 'diff-excerpt.txt'
        diff_path.write_text(execution['diff'], encoding='utf-8', newline='\n')
        reference('diff_excerpt', diff_path)
    path = output / 'index.json'
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return {'path':str(path), 'task_id':task['id'], 'index_status':payload['index_status'], 'evidence_gaps':gaps}
