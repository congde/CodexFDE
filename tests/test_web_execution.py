"""Authorization tests use a controlled submitter and never invoke Codex."""
from pathlib import Path
import tempfile
import threading
import time
import json
from http.server import ThreadingHTTPServer
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
import unittest
from unittest.mock import patch

from workbench.task_store import TaskStore
from workbench.web_execution import WebExecution


class WebExecutionTests(unittest.TestCase):
    def test_execution_failure_remains_queryable_when_task_update_also_fails(self):
        def submitter(**kwargs):
            task = self.store.create('failure fixture', execution_mode='codex', write_scope=['flowerp'])
            kwargs['on_task_created'](task)
            raise RuntimeError('original execution failure')
        service = WebExecution(self.root, self.root, self.store, enabled=True, submitter=submitter)
        plan = self.prepare(service)
        with patch.object(self.store, 'transition', side_effect=ValueError('task update refused')):
            service._run(plan['plan_id'])
        failed = service.get(plan['plan_id'])
        self.assertEqual('failed', failed['state'])
        self.assertIn('original execution failure', failed['error'])
        self.assertIn('task update refused', failed['task_record_error'])
        self.assertIsNotNone(failed['task_id'])
        restarted = WebExecution(self.root, self.root, self.store, enabled=True)
        self.assertEqual(failed, restarted.get(plan['plan_id']))

    def wait_finished(self, service, plan_id):
        deadline = time.monotonic() + 5
        while service.get(plan_id)['state'] == 'starting' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual('finished', service.get(plan_id)['state'])
    def test_restart_keeps_plan_but_never_replays_interrupted_execution(self):
        service = WebExecution(self.root, self.root, self.store, enabled=True)
        plan = self.prepare(service)
        service.plans[plan['plan_id']].update(state='starting', task_id='TASK-RECOVERY-FIXTURE')
        service._save(service.plans[plan['plan_id']])
        calls = []
        restarted = WebExecution(self.root, self.root, self.store, enabled=True, submitter=lambda **kw: calls.append(kw))
        recovered = restarted.get(plan['plan_id'])
        self.assertEqual('failed', recovered['state'])
        self.assertEqual('TASK-RECOVERY-FIXTURE', recovered['task_id'])
        self.assertNotIn('confirmation', recovered)
        restarted.authorize(plan['plan_id'], plan['confirmation'], plan['actor'])
        self.assertEqual([], calls)

    def test_restart_invalidates_unapproved_plan(self):
        service = WebExecution(self.root, self.root, self.store, enabled=True)
        plan = self.prepare(service)
        restarted = WebExecution(self.root, self.root, self.store, enabled=False)
        self.assertEqual('failed', restarted.get(plan['plan_id'])['state'])
        with self.assertRaisesRegex(ValueError, '未开启'):
            restarted.authorize(plan['plan_id'], plan['confirmation'], plan['actor'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = TaskStore(self.root / 'workbench.db')

    def prepare(self, service):
        with patch('workbench.web_execution.lesson_baseline_status', return_value={'baseline_commit': 'abc'}), \
             patch('workbench.web_execution.CodexExecutionRunner.capabilities', return_value={'codex_available': True}):
            return service.prepare(13, 'maintainer')

    def test_disabled_is_not_implicit_authorization(self):
        with self.assertRaises(ValueError):
            self.prepare(WebExecution(self.root, self.root, self.store))

    def test_readiness_distinguishes_switch_and_cli_without_starting_work(self):
        service = WebExecution(self.root, self.root, self.store)
        with patch('workbench.web_execution.CodexExecutionRunner.capabilities') as probe:
            self.assertEqual('disabled', service.readiness()['state'])
            probe.assert_not_called()
        service.enabled = True
        with patch('workbench.web_execution.CodexExecutionRunner.capabilities',
                   return_value={'codex_available': False, 'reason': 'local diagnostic'}):
            result = service.readiness()
            self.assertFalse(result['ready'])
            self.assertEqual('cli_unavailable', result['state'])
        with patch('workbench.web_execution.CodexExecutionRunner.capabilities',
                   return_value={'codex_available': True}):
            self.assertTrue(service.readiness()['ready'])
        self.assertEqual({}, service.plans)
        self.assertEqual([], self.store.list())

    def test_plan_does_not_execute_and_replayed_authorization_runs_once(self):
        finished = threading.Event()
        calls = []
        def submitter(**kwargs):
            calls.append(kwargs)
            task = self.store.create('fixture', execution_mode='codex', write_scope=['flowerp'])
            kwargs['on_task_created'](task)
            finished.set()
            return {'task': task}
        service = WebExecution(self.root, self.root, self.store, enabled=True, submitter=submitter)
        plan = self.prepare(service)
        self.assertEqual([], calls)
        self.assertNotIn('confirmation', service.get(plan['plan_id']))
        with self.assertRaises(ValueError):
            service.authorize(plan['plan_id'], 'wrong', 'maintainer')
        with self.assertRaises(ValueError):
            service.authorize(plan['plan_id'], plan['confirmation'], 'someone-else')
        service.authorize(plan['plan_id'], plan['confirmation'], 'maintainer')
        service.authorize(plan['plan_id'], plan['confirmation'], 'maintainer')
        self.assertTrue(finished.wait(5))
        self.wait_finished(service, plan['plan_id'])
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0]['execute_code'])
        self.assertEqual('abc', calls[0]['expected_baseline_commit'])
        task = self.store.get(self.store.list()[0]['id'])
        self.assertEqual('网页具名授权课程隔离执行', task['events'][-1]['detail'])

    def test_expired_plan_requires_new_confirmation(self):
        service = WebExecution(self.root, self.root, self.store, enabled=True)
        plan = self.prepare(service)
        service.plans[plan['plan_id']]['expires_at'] = 0
        with self.assertRaises(ValueError):
            service.authorize(plan['plan_id'], plan['confirmation'], 'maintainer')

    def test_dynamic_lesson_requires_new_cases_and_baseline(self):
        service = WebExecution(self.root, self.root, self.store, enabled=True)
        with self.assertRaises(ValueError):
            service.prepare(16, 'maintainer')

    def test_l04_requires_parent_before_creating_a_plan(self):
        service = WebExecution(self.root, self.root, self.store, enabled=True)
        with self.assertRaisesRegex(ValueError, 'WB-L04-BOOTSTRAP'):
            service.prepare(4, 'builder')
        self.assertEqual({}, service.plans)

    def test_l04_authorization_preserves_reviewed_parent_for_shared_service(self):
        calls = []
        done = threading.Event()
        def submitter(**kwargs):
            calls.append(kwargs)
            task = self.store.create('fixture only', execution_mode='codex', write_scope=['flowerp'])
            kwargs['on_task_created'](task)
            done.set()
            return {'task': task}
        service = WebExecution(self.root, self.root, self.store, enabled=True, submitter=submitter)
        parent = {'task_id':'TASK-FIXTURE000', 'reviewed_by':'peer', 'evaluated_by':'peer'}
        with patch('workbench.web_execution.lesson_baseline_status', return_value={'baseline_commit':'abc'}), \
             patch('workbench.web_execution.CodexExecutionRunner.capabilities', return_value={'codex_available':True}), \
             patch('workbench.web_execution.require_bootstrap_ticket', return_value=parent) as guard:
            plan = service.prepare(4, 'builder', bootstrap_task_id=parent['task_id'])
            guard.assert_called_once_with(self.store, parent['task_id'], self.root)
        self.assertEqual(parent, plan['bootstrap'])
        service.authorize(plan['plan_id'], plan['confirmation'], 'builder')
        self.assertTrue(done.wait(5))
        self.wait_finished(service, plan['plan_id'])
        self.assertEqual(parent['task_id'], calls[0]['bootstrap_task_id'])

    def test_http_plan_and_authorization_are_separate_and_cross_origin_is_rejected(self):
        from workbench.workbench_server import WorkbenchApp, make_handler
        app = WorkbenchApp(self.root, enable_code_execution=True)
        done = threading.Event()
        def submitter(**kwargs):
            task = app.tasks.create('http-fixture', execution_mode='codex', write_scope=['flowerp'])
            kwargs['on_task_created'](task)
            done.set()
            return {'task': task}
        app.code.submitter = submitter
        app.code.repository = self.root  # Keep the synthetic abc baseline independent of real release tags.
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = build_opener(ProxyHandler({}))
        def post(path, data, origin=None):
            headers = {'Content-Type': 'application/json'}
            if origin: headers['Origin'] = origin
            request = Request(f'http://127.0.0.1:{server.server_port}' + path,
                              data=json.dumps(data).encode(), headers=headers)
            try:
                with opener.open(request, timeout=5) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)
        try:
            with patch('workbench.web_execution.lesson_baseline_status', return_value={'baseline_commit':'abc'}), \
                 patch('workbench.web_execution.CodexExecutionRunner.capabilities', return_value={'codex_available':True}):
                status, plan = post('/api/v1/execution/plans', {'lesson':13, 'actor':'maintainer'})
            self.assertEqual(201, status)
            self.assertEqual([], app.tasks.list())
            endpoint = '/api/v1/execution/plans/' + plan['plan_id'] + '/authorize'
            body = {'actor':'maintainer', 'confirmation':plan['confirmation']}
            self.assertEqual(403, post(endpoint, body, 'http://unrelated.test')[0])
            self.assertEqual(202, post(endpoint, body)[0])
            self.assertTrue(done.wait(5))
            self.assertEqual(202, post(endpoint, body)[0])
            self.assertEqual(1, len(app.tasks.list()))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == '__main__':
    unittest.main()
