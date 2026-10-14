"""Compatibility requests share the initiative runtime; fixtures never call a model."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import threading
import time
import tempfile
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from tests import test_delivery_runtime as runtime_fixtures
from workbench.web_execution import WebExecution
from workbench.task_store import TaskStore


class DailyCompatibilityAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tasks = TaskStore(self.root / 'workbench.db')
        (self.root / 'workbench').mkdir()
        (self.root / 'workbench/__init__.py').write_text('', encoding='utf-8')
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)

    def test_unbound_daily_request_creates_no_second_plan_or_task(self):
        service = WebExecution(self.root, self.root, self.tasks, enabled=True)
        with patch('workbench.web_execution.CodexExecutionRunner.capabilities', return_value={'codex_available': True}) as capability:
            with self.assertRaisesRegex(ValueError, '事项'):
                service.prepare_daily('owner', 'request', 'acceptance', ['workbench'])
            capability.assert_not_called()
        self.assertEqual({}, service.plans)
        self.assertEqual([], self.tasks.list())

    def test_unbound_historical_daily_plan_is_readable_but_never_executed(self):
        service = WebExecution(self.root, self.root, self.tasks, enabled=True)
        old = {'plan_id': 'historical-daily', 'kind': 'daily', 'state': 'prepared',
               'actor': 'owner', 'confirmation': 'old-token', 'expires_at': 9999999999,
               'task_id': None, 'request': 'historical request'}
        service.plans[old['plan_id']] = copy.deepcopy(old)
        service._save(old)
        with patch('workbench.web_execution.threading.Thread.start') as start:
            with self.assertRaisesRegex(ValueError, '事项'):
                service.authorize(old['plan_id'], old['confirmation'], 'owner')
            start.assert_not_called()
        restarted = WebExecution(self.root, self.root, self.tasks, enabled=True)
        self.assertEqual('historical request', restarted.get(old['plan_id'])['request'])
        self.assertEqual([], self.tasks.list())


class DailyRuntimeCompatibilityTests(unittest.TestCase):
    setUp = runtime_fixtures.DeliveryRuntimeTests.setUp

    def service(self):
        return WebExecution(self.repository, self.runtime_dir, self.tasks, enabled=True,
                            daily_workflow=self.workflow, delivery_runtime=self.runtime)

    def prepare(self, service):
        return service.prepare_daily('owner', initiative_id=self.item['id'],
                                     expected_revision=self.workflow.get(self.item['id'])['revision'])

    def wait(self):
        thread = self.workflow.workers.get(self.item['id'])
        if thread:
            thread.join(15)
            self.assertFalse(thread.is_alive())
        return self.runtime.view(self.item['id'])

    def test_alias_authorization_runs_once_through_existing_runtime_and_retains_human_review(self):
        service = self.service()
        frozen = copy.deepcopy(self.workflow._load(self.item['id'])['plan'])
        plan = self.prepare(service)
        self.assertEqual(frozen['plan_id'], plan['plan_id'])
        self.assertEqual(frozen, self.workflow._load(self.item['id'])['plan'])
        self.assertEqual([], self.tasks.list())
        self.assertEqual(f'/api/v1/initiatives/{self.item["id"]}/workflow/execute', plan['execution_path'])
        with patch.object(service, '_run') as legacy_worker, patch('workbench.daily_delivery.submit_daily') as legacy_submit:
            first = service.authorize(plan['plan_id'], plan['confirmation'], 'owner')
            replay = service.authorize(plan['plan_id'], plan['confirmation'], 'owner')
            result = self.wait()
            legacy_worker.assert_not_called()
            legacy_submit.assert_not_called()
        self.assertEqual(first['plan_id'], replay['plan_id'])
        self.assertEqual('review', result['state'], result['error'])
        self.assertEqual(20, result['budget']['tokens_used'])
        self.assertEqual(1, len(self.tasks.list()))
        self.assertEqual('1', (self.repository / 'a.txt').read_text())
        self.assertEqual('2', (Path(result['workspace']) / 'a.txt').read_text())
        self.assertEqual(result['task_id'], service.get(plan['plan_id'])['task_id'])
        self.assertEqual('review', self.tasks.get(result['task_id'])['status'])
        self.assertEqual(frozen, self.workflow._load(self.item['id'])['plan'])
        changed = self.workflow._load(self.item['id'])
        changed['plan'] = {**changed['plan'], 'plan_id': 'new-confirmed-plan'}
        changed['active_task_id'] = 'a-different-later-task'
        self.workflow._save(changed)
        history = service.get(plan['plan_id'])
        self.assertEqual(result['task_id'], history['task_id'])
        self.assertEqual('review', history['state'])

    def test_only_confirmed_frozen_current_plan_can_be_aliased(self):
        service = self.service()
        revision = self.workflow.get(self.item['id'])['revision']
        with self.assertRaisesRegex(ValueError, '版本'):
            service.prepare_daily('owner', initiative_id=self.item['id'], expected_revision=revision - 1)
        with self.assertRaisesRegex(ValueError, '确认'):
            service.prepare_daily('owner', 'different request', initiative_id=self.item['id'], expected_revision=revision)
        state = self.workflow._load(self.item['id'])
        state['plan'].pop('profile')
        self.workflow._save(state)
        with self.assertRaisesRegex(ValueError, 'Profile'):
            self.prepare(service)
        state['stage'] = 'ready'
        self.workflow._save(state)
        with self.assertRaisesRegex(ValueError, '确认'):
            self.prepare(service)
        self.assertEqual({}, service.plans)
        self.assertEqual([], self.tasks.list())

    def test_changed_canonical_revision_blocks_alias_without_reconfirming(self):
        service = self.service()
        plan = self.prepare(service)
        state = self.workflow._load(self.item['id'])
        self.workflow._save(state)
        with self.assertRaisesRegex(ValueError, '版本'):
            service.authorize(plan['plan_id'], plan['confirmation'], 'owner')
        self.assertEqual('confirmed', self.workflow.get(self.item['id'])['stage'])
        self.assertEqual([], self.tasks.list())
        self.assertFalse(self.workflow.workers)

    def test_concurrent_authorization_requests_share_one_canonical_launch(self):
        service = self.service()
        plan = self.prepare(service)
        barrier = threading.Barrier(2)
        def authorize():
            barrier.wait(timeout=5)
            return service.authorize(plan['plan_id'], plan['confirmation'], 'owner')
        with patch.object(self.workflow, 'execute', wraps=self.workflow.execute) as execute:
            with ThreadPoolExecutor(max_workers=2) as requests:
                results = [future.result(timeout=10) for future in [requests.submit(authorize), requests.submit(authorize)]]
            self.assertEqual(1, execute.call_count)
            result = self.wait()
        self.assertEqual([plan['plan_id'], plan['plan_id']], [r['plan_id'] for r in results])
        self.assertEqual('review', result['state'], result['error'])
        self.assertEqual(1, len(self.tasks.list()))

    def test_lost_authorization_reply_keeps_original_task_visible_but_never_replays(self):
        service = self.service()
        plan = self.prepare(service)
        save = service._save
        def fail_after_launch(current):
            if current['state'] == 'delegated':
                raise OSError('fixture lost reply after canonical admission')
            return save(current)
        with patch.object(service, '_save', side_effect=fail_after_launch):
            with self.assertRaisesRegex(OSError, 'lost reply'):
                service.authorize(plan['plan_id'], plan['confirmation'], 'owner')
        result = self.wait()
        restarted = self.service()
        receipt = restarted.get(plan['plan_id'])
        self.assertEqual('delegating', receipt['authorization_state'])
        self.assertEqual(result['task_id'], receipt['task_id'])
        self.assertEqual('review', receipt['state'])
        with self.assertRaisesRegex(ValueError, '重放'):
            restarted.authorize(plan['plan_id'], plan['confirmation'], 'owner')
        self.assertEqual(1, len(self.tasks.list()))

    def test_pending_alias_restart_never_replays_or_rewrites_canonical_plan(self):
        service = self.service()
        plan = self.prepare(service)
        frozen = copy.deepcopy(self.workflow._load(self.item['id'])['plan'])
        service.plans[plan['plan_id']]['state'] = 'delegating'
        service._save(service.plans[plan['plan_id']])
        restarted = self.service()
        with self.assertRaisesRegex(ValueError, '重放|中断|核对'):
            restarted.authorize(plan['plan_id'], plan['confirmation'], 'owner')
        self.assertEqual(frozen, self.workflow._load(self.item['id'])['plan'])
        self.assertEqual([], self.tasks.list())
        self.assertFalse(self.workflow.workers)

    def test_profile_drift_blocks_the_delegated_runtime_before_any_cli_or_task(self):
        service = self.service()
        plan = self.prepare(service)
        self.runtime.sessions.set_plugin_enabled('shell.local', False)
        with patch.object(runtime_fixtures.LocalWorker, '_run_codex_streaming') as process:
            service.authorize(plan['plan_id'], plan['confirmation'], 'owner')
            self.wait()
            process.assert_not_called()
        self.assertIn('Profile', self.workflow.get(self.item['id'])['error'])
        self.assertEqual([], self.tasks.list())

    def test_alias_uses_runtime_pause_to_terminate_the_owned_cli_process(self):
        service = self.service()
        plan = self.prepare(service)
        with runtime_fixtures.LocalWorker.ready_lock:
            runtime_fixtures.LocalWorker.ready.clear()
        with patch.object(runtime_fixtures.LocalWorker,'completion_gate',self.root/'alias-pause-process-gate'):
            service.authorize(plan['plan_id'], plan['confirmation'], 'owner')
            runtime_fixtures.wait_for_worker_usage(self)
            current = self.runtime.view(self.item['id'])
            self.runtime.control(self.item['id'], 'pause', 'owner', current['revision'],
                                 run_id=current['run_id'], session_id=current['session_id'],
                                 control_revision=current['control_revision'])
            result = self.wait()
        self.assertEqual('paused', result['state'])
        self.assertEqual('1', (self.repository / 'a.txt').read_text())
        self.assertEqual(0, runtime_fixtures.LocalWorker.active)
        self.assertEqual(result['task_id'], service.get(plan['plan_id'])['task_id'])

    def test_http_daily_alias_uses_the_same_runtime_and_origin_boundary(self):
        from workbench.workbench_server import WorkbenchApp, make_handler
        app = WorkbenchApp(self.runtime_dir, enable_code_execution=True)
        app.delivery_runtime.runner_factory = runtime_fixtures.LocalWorker
        app.initiative_workflow.require_preflight = False
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = build_opener(ProxyHandler({}))
        def request(path, body, origin=None):
            headers = {'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            req = Request(f'http://127.0.0.1:{server.server_port}' + path,
                          data=json.dumps(body).encode('utf-8'), headers=headers)
            try:
                with opener.open(req, timeout=5) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                with error:
                    return error.code, json.load(error)
        try:
            self.assertIs(app.code.daily_workflow, app.initiative_workflow)
            status, missing = request('/api/v1/execution/daily-plans', {'actor': 'owner'})
            self.assertEqual(400, status)
            self.assertIn('事项', missing['message'])
            status, plan = request('/api/v1/execution/daily-plans', {
                'actor': 'owner', 'initiative_id': self.item['id'],
                'expected_revision': app.initiative_workflow.get(self.item['id'])['revision']})
            self.assertEqual(201, status)
            path = f'/api/v1/execution/plans/{plan["plan_id"]}/authorize'
            body = {'actor': 'owner', 'confirmation': plan['confirmation']}
            self.assertEqual(403, request(path, body, 'http://unrelated.test')[0])
            self.assertEqual([], app.tasks.list())
            self.assertEqual(202, request(path, body)[0])
            self.assertEqual(202, request(path, body)[0])
            app.initiative_workflow.workers[self.item['id']].join(15)
            result = app.delivery_runtime.view(self.item['id'])
            self.assertEqual('review', result['state'], result['error'])
            self.assertEqual(20, result['budget']['tokens_used'])
            self.assertEqual(1, len(app.tasks.list()))
            self.assertEqual('1', (self.repository / 'a.txt').read_text())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.delivery_runtime.close()


class SharedHarnessConstructionTests(unittest.TestCase):
    def test_shared_harness_does_not_construct_legacy_automation_but_keeps_agent_status(self):
        from workbench.workbench_server import WorkbenchApp
        with tempfile.TemporaryDirectory() as temporary:
            app = None
            try:
                with patch('workbench.platform_api.DeliveryAutomation') as automation:
                    app = WorkbenchApp(temporary, enable_advanced_runtime=True)
                    automation.assert_not_called()
                self.assertIsNone(app.harness_api.automation)
                session = app.harness_api.runtime.create_session(app.default_project, 'status', 'owner')
                response = app.harness_api.dispatch('GET', f'/api/v1/sessions/{session["id"]}/agent', {}, {})
                self.assertEqual(200, response.status)
                headers = {'x-workbench-actor': 'owner', 'idempotency-key': 'denied-create'}
                denied = app.harness_api.dispatch('POST', '/api/v1/tasks', headers, {'project_id': app.default_project})
                self.assertEqual(422, denied.status)
                self.assertIn('首页事项', denied.body['message'])
                self.assertEqual([], app.tasks.list())
            finally:
                if app:
                    app.delivery_runtime.close()
                    app.harness_api.shutdown()


if __name__ == '__main__':
    unittest.main()
