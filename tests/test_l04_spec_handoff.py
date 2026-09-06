"""Check actual frozen L04 input, not a model response or student acceptance."""
from pathlib import Path
import tempfile
import unittest

from workbench.course_mainline import create_lesson_task, lesson_contract
from workbench.course_requirement import freeze_requirement_spec
from workbench.spec import load_spec
from workbench.task_store import TaskStore

SPEC = '''## 来源
课堂练习：L03 确认稿 revision-2，非真实客户签字。
## 目标
导出库存，供运营核对可售数量。
## 非目标
不发邮件，不修改库存。
## 约束
available = on_hand - reserved；空库存保留表头。
## 验收用例
在库 8、预占 3 时，CSV 对应行可用量为 5。
## 完成定义
独立复验后由负责人作接受决定。
'''

class L04SpecHandoffTests(unittest.TestCase):
    def test_l03_content_is_the_created_task_input_with_course_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            store = TaskStore(runtime / 'workbench.db')
            task = create_lesson_task(store, 4, runtime, requirement_spec_text=SPEC)
            parsed = load_spec(task['spec_path'])
            self.assertIn('revision-2', parsed.source)
            self.assertIn('空库存保留表头', parsed.constraints)
            self.assertEqual('导出库存，供运营核对可售数量。', task['request'])
            self.assertIn('CSV 对应行可用量为 5', parsed.acceptance)
            self.assertIn('课程执行写集', parsed.constraints)
            self.assertEqual(['flowerp', 'workbench', 'tests'], task['write_scope'])
            self.assertIn('具名人审', parsed.done)
            self.assertIn('本次需求 Spec 已冻结', str(task['events']))

    def test_missing_required_section_is_rejected_before_task_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            store = TaskStore(runtime / 'workbench.db')
            with self.assertRaisesRegex(ValueError, '缺少必要章节'):
                create_lesson_task(store, 4, runtime, requirement_spec_text=SPEC.replace('## 非目标\n不发邮件，不修改库存。\n', ''))
            self.assertEqual([], store.list())

    def test_other_fixed_lessons_do_not_silently_accept_custom_contracts(self):
        with self.assertRaisesRegex(ValueError, '仅用于'):
            freeze_requirement_spec(lesson_contract(5), SPEC, ())
