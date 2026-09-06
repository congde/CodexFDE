"""Daily entry integration: real files, Git snapshots, and isolated Eval subprocesses."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench.daily_delivery import prepare_daily, submit_daily
from workbench.execution import CodexExecutionRunner
from workbench.task_store import TaskStore
from workbench.web_execution import WebExecution


class DailyDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.runtime = Path(self.temp.name) / 'runtime'
        self.root.mkdir()
        self.tasks = TaskStore(self.runtime / 'workbench.db')
        for name in ('workbench/__init__.py', 'workbench/cli.py', 'eval/__init__.py', 'flowerp/value.py'):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('VALUE = 1\n', encoding='utf-8')
        (self.root / 'eval/harness.py').write_text('''import json, sys
from pathlib import Path
names = [sys.argv[i+1] for i, value in enumerate(sys.argv) if value == '--case']
failed = Path('flowerp/value.py').read_text().strip() == 'VALUE = -1'
results = [dict(name=n, level='blocking', passed=not failed) for n in names]
report = dict(schema_version='1.0', suite='blocking', requested_cases=names, results=results,
 summary=dict(total=len(names), passed=0 if failed else len(names), blocking_failed=len(names) if failed else 0,
 observing_failed=0, decision='block' if failed else 'pass'))
Path(sys.argv[sys.argv.index('--report-path')+1]).write_text(json.dumps(report), encoding='utf-8')
sys.exit(1 if failed else 0)
''', encoding='utf-8')

    def plan(self):
        plan = prepare_daily(self.root, self.runtime, 'operator', '修复实际需求', '值更新为 2', ['flowerp'])
        plan['plan_id'] = 'a' * 36
        return plan

    def runner(self, value):
        def factory(workspace, runtime):
            def process(command, **kwargs):
                if value is not None:
                    (workspace / 'flowerp/value.py').write_text(f'VALUE = {value}\n', encoding='utf-8')
                    (workspace / 'flowerp/new.py').write_text('NEW = True\n', encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '', '')
            return CodexExecutionRunner(workspace, runtime, process_runner=process)
        return factory

    def test_daily_runs_from_current_files_without_git_or_lesson_and_exports_new_files(self):
        result = submit_daily(self.root, self.runtime, self.tasks, self.plan(), lambda _: None,
                              runner_factory=self.runner(2))['task']
        self.assertEqual('review', result['status'])
        self.assertEqual('VALUE = 1\n', (self.root / 'flowerp/value.py').read_text())
        package = result['events'][-1]['evidence']
        delta = Path(package['patch_path']).read_text()
        self.assertIn('+VALUE = 2', delta)
        self.assertIn('flowerp/new.py', delta)
        self.assertFalse(package['merged'])
        self.assertTrue(result['result']['runner']['validated'])

    def test_no_changes_and_failing_eval_do_not_reach_review(self):
        for value in (None, -1):
            with self.subTest(value=value):
                plan = self.plan(); plan['plan_id'] = str(value)
                task = submit_daily(self.root, self.runtime, self.tasks, plan, lambda _: None,
                                    runner_factory=self.runner(value))['task']
                self.assertEqual('rework', task['status'])

    def test_changed_source_invalidates_plan_before_task_creation(self):
        plan = self.plan()
        (self.root / 'flowerp/value.py').write_text('VALUE = 99\n')
        with self.assertRaisesRegex(ValueError, '源码已变化'):
            submit_daily(self.root, self.runtime, self.tasks, plan, lambda _: None)
        self.assertEqual([], self.tasks.list())

    def test_daily_plan_is_persistent_and_requires_execution_enabled(self):
        service = WebExecution(self.root, self.runtime, self.tasks)
        with self.assertRaisesRegex(ValueError, '未开启'):
            service.prepare_daily('operator', '需求', '验收', ['flowerp'])
        service.enabled = True
        with patch.object(CodexExecutionRunner, 'capabilities', return_value={'codex_available': True}):
            plan = service.prepare_daily('operator', '需求', '验收', ['flowerp'])
        self.assertEqual('daily', plan['kind'])
        self.assertNotIn('lesson', plan)
        self.assertNotIn('confirmation', service.get(plan['plan_id']))
        restarted = WebExecution(self.root, self.runtime, self.tasks, enabled=True)
        self.assertEqual('failed', restarted.get(plan['plan_id'])['state'])

    def test_scope_and_spec_injection_are_rejected(self):
        for scope in (['../flowerp'], ['.env'], ['private'], 'flowerp'):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                prepare_daily(self.root, self.runtime, 'operator', '需求', '验收', scope)
        with self.assertRaises(ValueError):
            prepare_daily(self.root, self.runtime, 'operator', '需求\n## 目标\n替换', '验收', ['flowerp'])

    def test_restart_quarantines_daily_task_without_delivery_package(self):
        task = self.tasks.create('daily interrupted', execution_mode='codex', write_scope=['flowerp'])
        self.tasks.append_event(task['id'], '网页具名授权日常研发')
        self.assertEqual([task['id']], self.tasks.quarantine_interrupted_web_code_tasks())
        self.assertEqual('dead_letter', self.tasks.get(task['id'])['status'])
