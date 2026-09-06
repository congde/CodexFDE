"""Ledger handoff fixtures; never represent real student acceptance."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from workbench.task_store import TaskStore
from workbench.bootstrap_handoff import require_bootstrap_ticket, link_bootstrap_ticket
from workbench.course_delivery import submit_course
from workbench.bootstrap_source import control_source_snapshot

class BootstrapHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = TaskStore(self.root / 'workbench.db')
        for directory in ('workbench', 'eval'):
            (self.root / directory).mkdir()
            (self.root / directory / '__init__.py').write_text('# fixture')

    def ticket(self, reviewer='peer', evaluator='peer', requirement='WB-L04-BOOTSTRAP'):
        task = self.store.create('fixture only', requirement_id=requirement, actor='builder')
        tid = task['id']
        self.store.transition(tid, 'spec_ready', actor='builder')
        self.store.transition(tid, 'executing', actor=evaluator, evidence={'execution_mode':'verify'})
        self.store.transition(tid, 'evaluating', actor=evaluator)
        self.store.transition(tid, 'review', actor=evaluator,
                              result={'summary':{'decision':'pass','blocking_failed':0},
                                      'bootstrap_source':control_source_snapshot(self.root)})
        self.store.review(tid, reviewer, 'approve', 'fixture acceptance, not teaching evidence')
        return tid

    def test_independent_verifier_can_also_review_verification_only_ticket(self):
        tid = self.ticket()
        parent = require_bootstrap_ticket(self.store, tid)
        self.assertEqual('peer', parent['evaluated_by'])
        child = self.store.create('ERP fixture', requirement_id='REQ-COURSE-L04')
        link_bootstrap_ticket(self.store, parent, child['id'], 'builder')
        self.assertEqual(tid, self.store.get(child['id'])['events'][-1]['evidence']['task_id'])
        self.assertEqual(child['id'], self.store.get(tid)['events'][-1]['evidence']['task_id'])

    def test_missing_unaccepted_wrong_requirement_and_self_review_rejected(self):
        for tid in (None, 'TASK-NOTFOUND00', self.store.create('unaccepted')['id'],
                    self.ticket(requirement='OTHER'), self.ticket(reviewer='builder'),
                    self.ticket(evaluator='builder')):
            with self.subTest(tid=tid), self.assertRaises(ValueError):
                require_bootstrap_ticket(self.store, tid)

    def test_stale_parent_records_neither_half_of_link(self):
        tid = self.ticket()
        parent = require_bootstrap_ticket(self.store, tid)
        parent['version'] -= 1
        child = self.store.create('ERP fixture')
        counts = [len(self.store.get(i)['events']) for i in (tid, child['id'])]
        with self.assertRaises(ValueError):
            link_bootstrap_ticket(self.store, parent, child['id'], 'builder')
        self.assertEqual(counts, [len(self.store.get(i)['events']) for i in (tid, child['id'])])

    def test_cli_service_rejects_before_task_creation_or_executor(self):
        with patch('workbench.course_delivery.CodexExecutionRunner') as runner:
            with self.assertRaises(ValueError):
                submit_course(repository_root=self.root, runtime_dir=self.root, lesson_number=4,
                              actor='builder', execute_code=True)
            runner.assert_not_called()
        self.assertEqual([], self.store.list())

    def test_changed_control_code_invalidates_previously_accepted_ticket(self):
        tid = self.ticket()
        (self.root / 'workbench' / '__init__.py').write_text('# changed')
        with self.assertRaisesRegex(ValueError, '源码已变化'):
            require_bootstrap_ticket(self.store, tid, self.root)

    def test_same_code_in_another_workspace_cannot_borrow_acceptance(self):
        tid = self.ticket()
        with self.assertRaisesRegex(ValueError, '另一工作区'):
            require_bootstrap_ticket(self.store, tid, self.root / 'other')

    def test_evaluation_binds_source_and_review_rejects_later_change(self):
        from workbench.workflow import evaluate_task
        tid = self.store.create('fixture', requirement_id='WB-L04-BOOTSTRAP', actor='builder')['id']
        self.store.transition(tid, 'spec_ready')
        self.store.transition(tid, 'executing', actor='peer')
        def runner(*args, **kwargs):
            return {'summary':{'decision':'pass', 'blocking_failed':0}}
        with patch('workbench.workflow.Path.cwd', return_value=self.root):
            result = evaluate_task(self.store, tid, actor='peer', suite_runner=runner)
        self.assertEqual('review', result['status'])
        self.assertEqual(control_source_snapshot(self.root), result['result']['bootstrap_source'])
        (self.root / 'workbench' / 'new.py').write_text('# added after evaluation')
        with self.assertRaisesRegex(ValueError, '源码已变化'):
            self.store.review(tid, 'peer', 'approve', 'must not be accepted')
        self.assertEqual('review', self.store.get(tid)['status'])

    def test_mutation_during_eval_does_not_produce_a_green_report(self):
        from workbench.workflow import evaluate_task
        tid = self.store.create('fixture', requirement_id='WB-L04-BOOTSTRAP', actor='builder')['id']
        self.store.transition(tid, 'spec_ready')
        self.store.transition(tid, 'executing', actor='peer')
        def runner(*args, **kwargs):
            (self.root / 'eval' / '__init__.py').write_text('# changed during check')
            return {'summary':{'decision':'pass', 'blocking_failed':0}}
        with patch('workbench.workflow.Path.cwd', return_value=self.root):
            with self.assertRaisesRegex(ValueError, '源码已变化'):
                evaluate_task(self.store, tid, actor='peer', suite_runner=runner)
        self.assertIsNone(self.store.get(tid)['result'])

    def test_deleted_control_file_invalidates_acceptance_but_runtime_output_does_not(self):
        tid = self.ticket()
        (self.root / 'report.json').write_text('{}')
        require_bootstrap_ticket(self.store, tid, self.root)
        (self.root / 'eval' / '__init__.py').rename(self.root / 'removed-eval.py')
        with self.assertRaises(ValueError):
            require_bootstrap_ticket(self.store, tid, self.root)
