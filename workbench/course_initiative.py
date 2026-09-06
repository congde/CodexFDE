"""Bind a decided requirement to a specific dynamic course execution plan."""
import hashlib
import json

from .initiative import InitiativeStore


def requirement_from_initiative(tasks, initiative_id, expected_version, cases):
    item = InitiativeStore(tasks.path).get(initiative_id)
    if item['decision'] != 'build' or item['status'] != 'approved_for_delivery' or item['linked_task_id']:
        raise ValueError('事项必须已具名决定进入交付，且尚未关联执行任务')
    if not item['decision_by'] or item['decision_by'].startswith('agent:'):
        raise ValueError('事项缺少具名人的交付决定')
    if item['version'] != expected_version:
        raise ValueError('事项版本已变化，请重新核对需求')
    fields = [('来源', f"事项 {item['id']}，版本 {item['version']}\n{item['source']}\n决定人：{item['decision_by']}"),
              ('目标', item['goal']), ('非目标', '\n'.join(item['non_goals']) or '不扩大本次目标范围'),
              ('约束', '\n'.join(item['constraints']) or '遵守课程写集与仓库规则'),
              ('验收用例', '\n'.join(item['acceptance']) + '\n\n关联新增检查：\n' + '\n'.join(cases)),
              ('完成定义', '以上验收成立，保留真实改动与检查证据，由具名人审核。')]
    text = '\n\n'.join('## ' + heading + '\n\n' + value for heading, value in fields) + '\n'
    snapshot = {key: item[key] for key in ('id', 'version', 'title', 'decision_by', 'decision_rationale',
                'goal', 'non_goals', 'constraints', 'acceptance', 'source', 'evidence')}
    digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    return text, {'id': item['id'], 'version': item['version'], 'title': item['title'],
                  'decision_by': item['decision_by'], 'sha256': digest}


def check_initiative_binding(tasks, binding, cases):
    _, current = requirement_from_initiative(tasks, binding['id'], binding['version'], cases)
    if current != binding:
        raise ValueError('事项内容已变化，请重新查看执行方案')


def link_initiative_task(tasks, binding, task_id, actor, cases, requirement_sha256):
    check_initiative_binding(tasks, binding, cases)
    task = tasks.get(task_id)
    frozen = next((e['evidence'] for e in task['events'] if e.get('detail') == '本次需求 Spec 已冻结'), None)
    if not frozen or frozen.get('sha256') != requirement_sha256 or task['execution_mode'] != 'codex':
        raise ValueError('执行任务的需求合同与已授权事项不一致')
    InitiativeStore(tasks.path).link_delivery(binding['id'], task_id, actor, binding['version'])
    tasks.append_event(task_id, '已关联具名决定的事项与冻结合同', actor=actor,
                       evidence={**binding, 'requirement_sha256': requirement_sha256})
