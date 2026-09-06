"""Synthetic plans exercise binding; these tests never run Codex or approve a product."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from workbench.course_mainline import create_lesson_task, lesson_contract
from workbench.initiative import InitiativeStore
from workbench.task_store import TaskStore
from workbench.web_execution import WebExecution
from workbench.workflow import prepare_task


class CourseInitiativeBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tasks = TaskStore(self.root / 'workbench.db')
        self.items = InitiativeStore(self.tasks.path)
        item = self.items.create({'title': '夹具：库存导出', 'raw_signal': '需要可用数量',
            'source': '合成测试', 'problem_statement': '缺少导出', 'goal': '导出可用库存',
            'non_goals': ['不改库存数量'], 'constraints': ['仅增加导出'], 'project_id': 'FlowERP',
            'acceptance': ['导出数量等于可用量'], 'evidence': ['合成观察']}, 'fixture-human')
        self.item = self.items.decide(item['id'], 'build', 'fixture-human', '测试决定', 1)
        self.calls = []

    def service(self):
        def submitter(**kwargs):
            self.calls.append(kwargs)
            task = create_lesson_task(self.tasks, kwargs['lesson_number'], self.root,
                execution_mode='codex', additional_eval_cases=kwargs['eval_cases'],
                requirement_spec_text=kwargs['requirement_spec_text'])
            kwargs['on_task_created'](task)
            return {'task': task}
        return WebExecution(self.root, self.root, self.tasks, enabled=True, submitter=submitter)

    def plan(self, service, lesson=15):
        with ExitStack() as stack:
            stack.enter_context(patch('workbench.web_execution.lesson_baseline_status', return_value={'baseline_commit':'base'}))
            stack.enter_context(patch('workbench.web_execution.CodexExecutionRunner.capabilities', return_value={'codex_available':True}))
            stack.enter_context(patch('workbench.web_execution.subprocess.run', return_value=SimpleNamespace(returncode=0,stdout='commit')))
            return service.prepare(lesson, 'fixture-human', ['new_export_fixture'], 'HEAD',
                                   initiative_id=self.item['id'], initiative_version=self.item['version'])

    def run_plan(self, service, plan):
        with patch('workbench.web_execution.threading.Thread.start'):
            service.authorize(plan['plan_id'], plan['confirmation'], 'fixture-human')
        service._run(plan['plan_id'])

    def test_specific_goal_reaches_frozen_task_and_links_both_records(self):
        for lesson in (15, 16):
            if lesson == 16:
                # A new fixture represents a separate new requirement, not a second task for one decision.
                item = self.items.create({'title':'第二夹具','raw_signal':'新反馈','source':'夹具','goal':'导出可用库存',
                    'problem_statement':'缺少导出','project_id':'FlowERP','acceptance':['导出可用量'],'evidence':['观察']},'fixture-human')
                self.item = self.items.decide(item['id'],'build','fixture-human','夹具',1)
            service = self.service()
            plan = self.plan(service, lesson)
            self.assertEqual(plan['request'], '导出可用库存')
            self.assertEqual(plan['write_scope'], list(lesson_contract(lesson).write_scope))
            self.assertEqual(plan['initiative']['decision_by'], 'fixture-human')
            self.run_plan(service, plan)
            result = service.get(plan['plan_id'])
            self.assertEqual(result['state'], 'finished', result)
            task = self.tasks.get(result['task_id'])
            self.assertEqual(self.items.get(self.item['id'])['linked_task_id'], task['id'])
            self.assertTrue(any(e['detail'] == '已关联具名决定的事项与冻结合同' for e in task['events']))
            prepared = prepare_task(self.tasks, task['id'])
            self.assertEqual(prepared['spec']['goal'], '导出可用库存')
            self.assertIn('new_export_fixture', prepared['spec']['acceptance'])
            self.assertIsNone(prepared['reviewed_by'])

    def test_second_prepared_plan_cannot_execute_same_decided_item(self):
        service = self.service()
        first, second = self.plan(service), self.plan(service)
        self.run_plan(service, first)
        with self.assertRaisesRegex(ValueError, '尚未关联'):
            service.authorize(second['plan_id'], second['confirmation'], 'fixture-human')
        self.assertEqual(len(self.calls), 1)

    def test_fixed_lesson_cannot_claim_to_execute_arbitrary_initiative(self):
        with self.assertRaisesRegex(ValueError, '固定课程合同'):
            self.plan(self.service(), 13)
        self.assertEqual(self.tasks.list(), [])

    def test_changed_spec_file_rejected_before_execution(self):
        service = self.service()
        plan = self.plan(service)
        self.run_plan(service, plan)
        task = self.tasks.get(service.get(plan['plan_id'])['task_id'])
        with Path(task['spec_path']).open('a', encoding='utf-8') as stream:
            stream.write('\n额外修改')
        with self.assertRaisesRegex(ValueError, 'Spec 已变化'):
            prepare_task(self.tasks, task['id'])
        self.assertEqual(self.tasks.get(task['id'])['status'], 'queued')

    def test_missing_specific_requirement_fails_before_task_creation(self):
        with self.assertRaisesRegex(ValueError, '六段式'):
            create_lesson_task(self.tasks, 15, self.root, execution_mode='codex')
        self.assertEqual(self.tasks.list(), [])

    def test_mismatched_task_contract_fails_before_execution_and_preserves_failure(self):
        service = self.service()
        plan = self.plan(service)
        def wrong_submitter(**kwargs):
            task = create_lesson_task(self.tasks, 15, self.root, execution_mode='codex',
                additional_eval_cases=kwargs['eval_cases'],
                requirement_spec_text=kwargs['requirement_spec_text'].replace('导出可用库存', '另一个目标'))
            kwargs['on_task_created'](task)
            self.fail('Contract mismatch must stop the submitter before execution')
        service.submitter = wrong_submitter
        self.run_plan(service, plan)
        result = service.get(plan['plan_id'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(self.tasks.get(result['task_id'])['status'], 'failed')
        self.assertIsNone(self.items.get(self.item['id'])['linked_task_id'])
