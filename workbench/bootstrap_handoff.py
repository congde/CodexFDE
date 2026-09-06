"""L04's explicit handoff from independently reviewed workbench to ERP work."""
import json


def require_bootstrap_ticket(store, task_id, repository=None):
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError('L04 代码执行必须引用已独立接受的 WB-L04-BOOTSTRAP 任务')
    task_id = task_id.strip()
    try:
        task = store.get(task_id)
    except KeyError as error:
        raise ValueError('找不到工作台 Ticket A，请检查任务编号及运行目录') from error
    if task.get('requirement_id') != 'WB-L04-BOOTSTRAP':
        raise ValueError('前置任务必须是 WB-L04-BOOTSTRAP')
    reviewer = task.get('reviewed_by') or ''
    if task['status'] != 'completed' or task.get('review_decision') != 'approve' or not reviewer or reviewer.startswith('agent:'):
        raise ValueError('工作台 Ticket A 尚未被具名接受')
    events = task.get('events', [])
    builders = {event['actor'] for event in events
                if event.get('from_status') is None and event.get('to_status') == 'queued'}
    builders.update(event['actor'] for event in events
                    if event.get('to_status') == 'executing'
                    and (event.get('evidence') or {}).get('execution_mode') == 'codex')
    if not builders or reviewer in builders:
        raise ValueError('Ticket A 必须由非执行者独立验收，不能执行者自签')
    evaluators = [event['actor'] for event in events if event.get('to_status') == 'evaluating']
    if not evaluators or evaluators[-1] in builders or evaluators[-1].startswith('agent:'):
        raise ValueError('Ticket A 必须由非构建者具名重跑复验')
    summary = (task.get('result') or {}).get('summary') or {}
    if summary.get('decision') != 'pass' or summary.get('blocking_failed') != 0:
        raise ValueError('Ticket A 缺少通过的独立复验结果')
    from .bootstrap_source import verify_control_source
    source = verify_control_source(task['result'].get('bootstrap_source'), repository)
    return {'task_id': task_id, 'requirement_id': task['requirement_id'],
            'reviewed_by': reviewer, 'evaluated_by': evaluators[-1],
            'reviewed_at': task.get('reviewed_at'), 'version': task['version'],
            'source_sha256': source['sha256']}


def link_bootstrap_ticket(store, parent, child_id, actor):
    """Record both references together, after rechecking the accepted parent."""
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT status,version FROM tasks WHERE id=?', (parent['task_id'],)).fetchone()
        if not row or row['status'] != 'completed' or row['version'] != parent['version']:
            raise ValueError('前置验收记录已变化，请重新确认')
        child = conn.execute('SELECT status FROM tasks WHERE id=?', (child_id,)).fetchone()
        if not child:
            raise ValueError('后续任务不存在')
        for task_id, status, detail, evidence in (
            (child_id, child['status'], 'L04 已关联工作台独立验收', parent),
            (parent['task_id'], row['status'], 'L04 工作台验收已用于后续交付', {'task_id':child_id}),
        ):
            conn.execute('INSERT INTO task_events(task_id,from_status,to_status,detail,actor,evidence_json) VALUES(?,?,?,?,?,?)',
                         (task_id, status, status, detail, actor, json.dumps(evidence, ensure_ascii=False)))
