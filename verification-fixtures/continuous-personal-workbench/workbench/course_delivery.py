"""L04 only: gate B on accepted A, then organize an independent client candidate."""
import os

from .bootstrap_handoff import require_accepted_a
from .course_workspace import create_candidate, prepare_inventory
from .execution import INVENTORY_WRITES
from .task_store import TaskError
from .workflow import run_task


def submit_l04(store, *, bootstrap_task_id, requirement_spec, actor, execution_timeout=900):
    task = store.create(requirement_id='FLOWERP-L04-INVENTORY-EXPORT', request='按绑定 L03 Spec 交付最小库存导出',
                        spec_path=requirement_spec, actor=actor, kind='B')
    task_id = task['task_id']
    store.update(task_id, detail='已绑定前置 A，准备检查接受与源码版本',
                 evidence={'bootstrap_task_id': bootstrap_task_id}, bootstrap_task_id=bootstrap_task_id)
    try:
        accepted = require_accepted_a(store, bootstrap_task_id)
        source = os.environ.get('FLOWERP_PROJECT_ROOT')
        if not source:
            raise TaskError('client_source_missing', '必须通过 FLOWERP_PROJECT_ROOT 指定独立客户源码')
        def on_created(isolation):
            store.update(task_id, detail='已创建课程隔离 Worktree', evidence={'path': isolation['path'],
                         'mode': isolation['mode']}, isolation=isolation)
        isolation = create_candidate(source, store.runtime, control_root=store.control_root, on_created=on_created)
        try:
            prepare_inventory(isolation, store.control_root)
        finally:
            store.update(task_id, detail='已保存来源、教学准备与实现前检查', evidence=isolation,
                         isolation=isolation, eval_commands=isolation.get('eval_commands'))
        store.update(task_id, detail='已核对独立来源与逐文件 SHA256', evidence=isolation,
                     isolation=isolation, bootstrap_source=accepted['accepted_source'])
        require_accepted_a(store, bootstrap_task_id)
        return run_task(store, task_id, actor=actor, execute_code=True, workspace_dir=isolation['path'],
                        allowed_write_files=sorted(INVENTORY_WRITES), execution_timeout=execution_timeout)
    except Exception as error:
        return store.fail(task_id, getattr(error, 'code', 'course_delivery_failed'), str(error))
