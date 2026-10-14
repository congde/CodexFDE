"""Require a separately verified A and its current content identity for B."""
from .bootstrap_source import source_identity
from .task_store import TaskError, person_key


def require_accepted_a(store, task_id, *, allow_fixture=True):
    try:
        task = store.get(task_id)
    except ValueError as error:
        raise TaskError('bootstrap_not_accepted', '找不到已接受 A') from error
    if (task['kind'] != 'A' or task['requirement_id'] != 'WB-L04-BOOTSTRAP'
            or task['status'] != 'completed' or task['review_decision'] != 'approve'
            or not task.get('reviewer') or not task.get('verification_actor')
            or person_key(task['reviewer']) == person_key(task['actor'])
            or person_key(task['verification_actor']) == person_key(task['actor'])
            or (task.get('executor') and person_key(task['reviewer']) == person_key(task['executor']))):
        raise TaskError('bootstrap_not_accepted', 'A 尚未由非实现者复验并具名接受')
    if task.get('accepted_source') != source_identity(store.control_root):
        raise TaskError('bootstrap_source_changed', 'A 接受后控制源码内容变化，旧接受失效')
    if task.get('fixture') and not allow_fixture:
        raise TaskError('fixture_acceptance', '软件夹具接受不能授权真实执行；维护者夹具须显式 --fixture')
    return task


def current_a(store, task_id=None, *, fixture=False):
    if not task_id:
        with store._connection(readonly=True) as connection:
            rows = connection.execute('SELECT task_id FROM tasks ORDER BY rowid DESC').fetchall()
        for row in rows:
            candidate = store.get(row[0])
            if candidate['kind'] == 'A' and candidate['requirement_id'] == 'WB-L04-BOOTSTRAP' and candidate['status'] == 'completed':
                task_id = row[0]
                break
    if not task_id:
        raise TaskError('bootstrap_not_accepted', '同一任务库没有当前版本已接受 A')
    return require_accepted_a(store, task_id, allow_fixture=fixture)
