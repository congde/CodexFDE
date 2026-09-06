"""Orchestration fixtures verify gates; they do not claim a real Codex delivery."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from workbench.course_delivery import submit_course
from workbench.task_store import TaskStore
from workbench.bootstrap_source import control_source_snapshot


class CourseDeliveryTests(unittest.TestCase):
    def run_fixture(self, *, pre_red=True, changed=True, lesson=13, failed_cases=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        isolated = root / 'isolated'
        isolated.mkdir()
        calls = []
        parent_id = None
        if lesson == 4:
            for directory in ('workbench', 'eval'):
                (root / directory).mkdir()
                (root / directory / '__init__.py').write_text('# fixture')
            store = TaskStore(root / 'runtime' / 'workbench.db')
            parent_id = store.create('fixture workbench acceptance', requirement_id='WB-L04-BOOTSTRAP', actor='builder')['id']
            for state in ('spec_ready', 'executing', 'evaluating'):
                store.transition(parent_id, state, actor='peer', evidence={'execution_mode':'verify'})
            store.transition(parent_id, 'review', actor='peer', result={'summary':{'decision':'pass','blocking_failed':0}, 'bootstrap_source':control_source_snapshot(root)})
            store.review(parent_id, 'peer', 'approve', 'fixture, not student evidence')

        def eval_factory(workspace, runtime, task_id, cases, phase):
            self.assertEqual(isolated, workspace)
            def run(*args, **kwargs):
                calls.append(phase)
                failed = phase == 'pre' and pre_red
                results = [{'name': name, 'level': 'blocking', 'passed': not (failed and (failed_cases is None or name in failed_cases))} for name in cases]
                failed_count = sum(not item['passed'] for item in results)
                return {'schema_version': '1.0', 'suite': 'blocking', 'requested_cases': list(cases),
                        'results': results,
                        'summary': {'total': len(cases), 'passed': len(cases) - failed_count,
                                    'blocking_failed': failed_count, 'observing_failed': 0,
                                    'decision': 'block' if failed_count else 'pass'},
                        'runner': {'validated': True, 'workspace': str(workspace), 'attempt_id': phase,
                                   'process_returncode': 1 if failed_count else 0}}
            return run

        def executor_factory(workspace, runtime):
            self.assertEqual(isolated, workspace)
            def execute(task):
                calls.append('execute')
                return {'success': True, 'mode': 'codex_exec', 'out_of_scope_files': [],
                        'changed_files': ['flowerp/service.py'] if changed else []}
            return execute

        with ExitStack() as stack:
            stack.enter_context(patch('workbench.course_delivery.lesson_baseline_status', return_value={'baseline_commit': 'fixture'}))
            manager = stack.enter_context(patch('workbench.course_delivery.CourseWorktreeManager'))
            manager.return_value.prepare.return_value = {'path': str(isolated)}
            stack.enter_context(patch('workbench.course_delivery.LessonSubprocessEvalRunner', side_effect=eval_factory))
            stack.enter_context(patch('workbench.course_delivery.CodexExecutionRunner', side_effect=executor_factory))
            if lesson >= 15:
                stack.enter_context(patch('workbench.course_delivery.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='fixture')))
            output = submit_course(repository_root=root, runtime_dir=root / 'runtime', lesson_number=lesson,
                                   actor='maintainer-fixture', execute_code=True, bootstrap_task_id=parent_id,
                                   eval_cases=('new_requirement_fixture',) if lesson >= 15 else (),
                                   session_baseline_ref='fixture' if lesson >= 15 else None,
                                   requirement_spec_text=('## 来源\n测试夹具\n## 目标\n验证新增需求执行\n'
                                     '## 非目标\n不模拟学生成果\n## 约束\n遵守写集\n'
                                     '## 验收用例\nnew_requirement_fixture\n## 完成定义\n保留证据并人审\n') if lesson >= 15 else None)
        if parent_id:
            self.assertEqual(output['task']['id'], store.get(parent_id)['events'][-1]['evidence']['task_id'])
        return output, calls

    def test_green_start_never_calls_executor(self):
        output, calls = self.run_fixture(pre_red=False)
        self.assertEqual(['pre'], calls)
        self.assertEqual('failed', output['task']['status'])
        self.assertFalse(output['implementation_evidence'])

    def test_red_changed_green_stops_for_human_and_returns_final_events(self):
        output, calls = self.run_fixture()
        self.assertEqual(['pre', 'execute', 'post'], calls)
        self.assertEqual('review', output['task']['status'])
        self.assertTrue(output['implementation_evidence'])
        self.assertIsNone(output['task']['reviewed_by'])
        self.assertEqual('课程红绿差分判定已完成', output['task']['events'][-1]['detail'])

    def test_green_without_diff_is_rework(self):
        output, calls = self.run_fixture(changed=False)
        self.assertEqual(['pre', 'execute', 'post'], calls)
        self.assertEqual('rework', output['task']['status'])
        self.assertFalse(output['implementation_evidence'])

    def test_l04_links_independent_workbench_before_erp_red_green_delivery(self):
        output, calls = self.run_fixture(lesson=4)
        self.assertEqual(['pre', 'execute', 'post'], calls)
        self.assertEqual('review', output['task']['status'])
        self.assertIsNone(output['task']['reviewed_by'])
        links = [event for event in output['task']['events'] if event['detail'] == 'L04 已关联工作台独立验收']
        self.assertEqual(1, len(links))
        self.assertEqual('peer', links[0]['evidence']['reviewed_by'])

    def test_new_requirement_cannot_hide_old_regressions(self):
        for lesson in (15,16):
            with self.subTest(lesson=lesson):
                output, calls = self.run_fixture(lesson=lesson)
                self.assertEqual(['pre'], calls)
                self.assertEqual('failed', output['task']['status'])
                self.assertFalse(output['implementation_evidence'])

    def test_new_requirement_red_then_green_still_requires_human_review(self):
        for lesson in (15,16):
            with self.subTest(lesson=lesson):
                output, calls = self.run_fixture(lesson=lesson, failed_cases={'new_requirement_fixture'})
                self.assertEqual(['pre','execute','post'], calls)
                self.assertEqual('review', output['task']['status'])
                self.assertTrue(output['implementation_evidence'])
                self.assertIsNone(output['task']['reviewed_by'])

    def test_authorization_must_be_boolean(self):
        with self.assertRaises(ValueError):
            submit_course(repository_root='.', runtime_dir='.runtime', lesson_number=13,
                          actor='fixture', execute_code='false')


if __name__ == '__main__':
    unittest.main()
