"""Synthetic fixtures only: project isolation and workflow gates, never live evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from workbench.daily_delivery import manifest, submit_daily
from workbench.execution import CodexExecutionRunner
from workbench.execution_control import delivery_lock
from workbench.initiative import InitiativeStore
from workbench.initiative_workflow import InitiativeWorkflow
from workbench.project_store import ProjectStore
from workbench.project_delivery import CandidateProjectEval
from workbench.task_store import TaskStore


class ProjectInitiativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.tasks = TaskStore(self.runtime / 'workbench.db')
        self.items = InitiativeStore(self.tasks.path)
        self.projects = ProjectStore(self.tasks.path)
        self.registered = []
        for name in ('first', 'second'):
            root = self.root / name
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'value.txt').write_text('1')
            (root / 'AGENTS.md').write_text('Only change value.txt.')
            (root / 'fixture_eval.py').write_text("import json\nfrom pathlib import Path\n"
                "assert Path('value.txt').read_text() in ('1', '2')\n"
                "print(json.dumps({'summary': {'decision':'pass','total':1,'passed':1,'blocking_failed':0},"
                "'results':[{'name':'value','level':'blocking','passed':True}]}))\n")
            self.registered.append(self.projects.create(name, root, [sys.executable, 'fixture_eval.py']))
        self.item = self.items.create({'title': 'change value', 'raw_signal': 'change value', 'source': 'test',
            'project_id': self.registered[1]['id'], 'success_metric': 'value is 2'}, 'owner')
        self.sources = []

        def research(source, runtime, folder, context, progress):
            self.sources.append(str(source))
            return {'proposal': {'goal': 'change value', 'questions': [], 'findings': ['value is 1'],
                'acceptance': ['value is 2'], 'non_goals': ['other files'], 'write_scope': ['value.txt'],
                'steps': ['update value', 'run project checks'], 'sources': ['value.txt']},
                'source_manifest': manifest(source, runtime), 'invocation': {'fixture': True}}

        def factory(workspace, runtime):
            def run(command, **kwargs):
                (workspace / 'value.txt').write_text('2')
                return subprocess.CompletedProcess(command, 0, '', '')
            return CodexExecutionRunner(workspace, runtime, process_runner=run)

        def submit(source, runtime, tasks, plan, created):
            return submit_daily(source, runtime, tasks, plan, created, runner_factory=factory)

        self.service = InitiativeWorkflow(self.root / 'first', self.runtime, self.items, self.tasks,
            projects=self.projects, enabled=True, researcher=research, submitter=submit)

    def state(self):
        return self.service.get(self.item['id'])

    def call(self, method, *args, actor='owner'):
        return getattr(self.service, method)(self.item['id'], actor, self.state()['revision'], *args)

    def wait(self):
        self.service.workers[self.item['id']].join(120)
        self.assertFalse(self.service.workers[self.item['id']].is_alive())
        return self.state()

    def ready(self):
        self.call('discuss', 'change value')
        self.assertEqual('ready', self.wait()['stage'])
        self.call('confirm_prd')
        self.call('confirm', 'reviewer')

    def test_second_project_full_cycle_does_not_change_first(self):
        self.ready()
        self.call('execute')
        state = self.wait()
        self.assertEqual('review', state['stage'], state['error'])
        self.assertEqual(str(self.root / 'second'), self.sources[0])
        self.assertIn('PROJECT:' + self.registered[1]['id'], self.tasks.get(state['active_task_id'])['business_refs'])
        self.assertEqual('1', (self.root / 'second/value.txt').read_text())
        self.call('accept', 'checked fixture', actor='reviewer')
        self.call('integrate', actor='reviewer')
        result = self.wait()
        self.assertEqual('integrated', result['stage'], result['error'])
        self.assertEqual('2', (self.root / 'second/value.txt').read_text())
        self.assertEqual('1', (self.root / 'first/value.txt').read_text())
        with self.assertRaises(ValueError):
            self.call('record_delivery', 'outcome', {'target':'2','actual':'2','observation':'2','period':'test','evidence':'fixture','conclusion':'pass'})
        self.call('record_delivery', 'release', {'version':'test-v1','environment':'fixture','evidence':'fixture only'})
        self.assertEqual('待观察', self.state()['observation_status'])
        release = self.state()['current_release']
        self.call('record_delivery', 'outcome', {'target':'2','actual':'2','observation':'2','period':'test','evidence':'fixture','conclusion':'pass'})
        self.assertEqual(release, self.state()['delivery_records'][-1]['release_id'])
        self.call('reopen', 'next fixture change')
        self.assertEqual('idle', self.state()['stage'])
        self.assertEqual(release, self.state()['completed_cycles'][-1]['current_release'])

    def test_workbench_changes_do_not_invalidate_registered_erp_research(self):
        self.call('discuss', 'change value')
        self.wait()
        (self.root / 'first/value.txt').write_text('workbench change')
        state = self.state()
        self.assertEqual('current', state['source_check']['status'])
        self.assertEqual('', state['warning'])
        self.call('confirm_prd')
        self.assertEqual('confirmed', self.call('confirm', 'reviewer')['stage'])
        (self.root / 'second/value.txt').write_text('actual ERP change')
        self.assertEqual('changed', self.state()['source_check']['status'])

    def test_eval_recheck_keeps_review_and_records_fresh_history(self):
        self.ready()
        self.call('execute')
        self.assertEqual('review', self.wait()['stage'])
        self.call('run_eval')
        state = self.wait()
        self.assertEqual('review', state['stage'], state['error'])
        self.assertEqual('current', state['eval_harness']['freshness'])
        self.assertEqual(1, len(state['eval_runs']))
        self.assertEqual('review', self.tasks.get(state['active_task_id'])['status'])
        self.call('run_eval')
        self.assertEqual(2, len(self.wait()['eval_runs']))
        latest = self.state()['eval_runs'][-1]['report']['runner']['report_path']
        Path(latest).write_text('{}')
        self.assertEqual('stale', self.state()['eval_harness']['freshness'])
        with self.assertRaisesRegex(ValueError, '来源已变化'):
            self.call('accept', 'checked', actor='reviewer')

    def test_hook_preparation_binds_confirmed_customer_candidate_without_installing(self):
        self.assertFalse(self.state()['quality_hook']['can_prepare'])
        with self.assertRaises(ValueError):
            self.call('prepare_hook')
        self.ready()
        self.call('execute')
        state = self.wait()
        before = manifest(state['workspace'], self.runtime)
        self.assertTrue(state['quality_hook']['can_prepare'])
        prepared = self.call('prepare_hook')
        self.assertEqual('review', prepared['stage'])
        self.assertEqual('prepared', prepared['quality_hook']['status'])
        folder = Path(prepared['quality_hook']['path'])
        binding = json.loads((folder / 'binding.json').read_text(encoding='utf-8'))
        self.assertEqual(self.registered[1]['id'], binding['project_id'])
        self.assertEqual(self.registered[1]['eval_command'], binding['command'])
        self.assertEqual(state['workspace'], binding['workspace'])
        self.assertEqual(before, manifest(state['workspace'], self.runtime))
        self.assertFalse((Path(state['workspace']) / '.codex/hooks.json').exists())
        with self.assertRaisesRegex(ValueError, '进展已变化'):
            self.service.prepare_hook(self.item['id'], 'owner', state['revision'])
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from workbench.workbench_server import WorkbenchApp, make_handler
        app = WorkbenchApp(self.runtime)
        app.initiative_workflow = self.service
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection('127.0.0.1', server.server_port)
            path = '/api/v1/initiatives/' + self.item['id'] + '/workflow/prepare-hook'
            payload = json.dumps({'actor': 'owner', 'revision': prepared['revision']})
            conn.request('POST', path, payload, {'Content-Type': 'application/json', 'Origin': 'http://foreign.invalid'})
            response = conn.getresponse()
            self.assertEqual(403, response.status)
            response.read()
            conn.close()
            conn = HTTPConnection('127.0.0.1', server.server_port)
            conn.request('POST', path, payload, {'Content-Type': 'application/json'})
            response = conn.getresponse()
            self.assertEqual(200, response.status)
            body = json.loads(response.read())
            self.assertEqual('prepared', body['quality_hook']['status'])
            self.assertIn('quality_gate.py', body['quality_hook']['review_files'])
            self.assertNotEqual(prepared['quality_hook']['path'], body['quality_hook']['path'])
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_eval_recheck_blocks_failed_business_and_does_not_promote_rework(self):
        self.ready()
        self.call('execute')
        state = self.wait()
        workspace = Path(state['workspace'])
        (workspace / 'fixture_eval.py').write_text("import json,sys\nprint(json.dumps({'summary':"
            "{'total':1,'passed':0,'blocking_failed':1,'observing_failed':0,'decision':'block'},"
            "'results':[{'name':'value','level':'blocking','passed':False}]}))\nsys.exit(1)")
        self.call('run_eval')
        state = self.wait()
        self.assertEqual('rework', state['stage'], state['error'])
        self.assertEqual('rework', self.tasks.get(state['active_task_id'])['status'])
        with self.assertRaises(ValueError):
            self.call('accept', 'checked', actor='reviewer')

    def test_invalid_recheck_preserves_error_instead_of_showing_previous_green(self):
        self.ready()
        self.call('execute')
        state = self.wait()
        (Path(state['workspace']) / 'fixture_eval.py').write_text("print('not a JSON report')")
        self.call('run_eval')
        state = self.wait()
        self.assertEqual('failed', state['stage'])
        self.assertFalse(state['eval_harness']['available'])
        self.assertTrue(state['eval_harness']['error'])
        self.assertIn('error', state['eval_runs'][-1])

    def test_added_project_can_discuss_before_eval_configuration_but_cannot_confirm(self):
        project = self.registered[1]
        self.projects.configure(project['id'], [])
        self.call('discuss', 'change value')
        self.assertEqual('ready', self.wait()['stage'])
        self.call('confirm_prd')
        with self.assertRaisesRegex(ValueError, '配置质量检查命令'):
            self.call('confirm', 'reviewer')
        with self.assertRaisesRegex(ValueError, '运行环境'):
            self.service.preflight(self.item['id'])
        self.projects.configure(project['id'], project['eval_command'])
        self.assertEqual('confirmed', self.call('confirm', 'reviewer')['stage'])

    def test_prd_gate_and_new_discussion_invalidate_confirmation(self):
        self.call('discuss', 'change value')
        self.wait()
        with self.assertRaisesRegex(ValueError, 'PRD'):
            self.call('confirm', 'reviewer')
        self.call('confirm_prd')
        old = self.state()['revision']
        self.call('discuss', 'change scope')
        self.wait()
        self.assertFalse(self.state()['prd_confirmed'])
        with self.assertRaises(ValueError):
            self.service.confirm(self.item['id'], 'owner', old, 'reviewer')

    def test_cancel_persisted_queue_never_starts_codex(self):
        self.ready()
        delivery_lock.acquire()
        try:
            self.call('execute')
            self.assertEqual('queued', self.state()['stage'])
            with self.assertRaises(ValueError):
                self.call('execute')
            self.call('cancel')
            self.assertEqual('cancelled', self.wait()['stage'])
            self.assertIsNone(self.state()['active_task_id'])
        finally:
            delivery_lock.release()

    def test_restart_preserves_queue_without_replay(self):
        self.ready()
        data = self.service._load(self.item['id'])
        data['stage'] = 'queued'
        self.service._save(data)
        restored = InitiativeWorkflow(self.root, self.runtime, self.items, self.tasks, projects=self.projects)
        self.assertEqual('interrupted', restored.get(self.item['id'])['stage'])
        self.assertFalse(restored.workers)

    def test_eval_rejects_false_success_and_keeps_process_receipt(self):
        root = self.root / 'second'
        (root / 'lie.py').write_text("import sys,json\nprint(json.dumps({'summary':{'decision':'pass'}}))\nsys.exit(3)\n")
        runner = CandidateProjectEval(root, self.runtime, 'TASK-test', [sys.executable,'lie.py'], 'test')
        with self.assertRaisesRegex(RuntimeError, '退出码'):
            runner()
        self.assertTrue(list((self.runtime / 'project-reports').rglob('process.json')))

    def test_running_process_can_be_cancelled_without_waiting_for_deadline(self):
        import time
        from workbench.execution_control import local
        event = threading.Event()
        local.cancel_event = event
        try:
            runner = CodexExecutionRunner(self.root, self.runtime)
            result = runner._run_codex_streaming([sys.executable, '-u', '-c',
                "import time;print('started',flush=True);time.sleep(60)"], '', 30,
                lambda line: event.set(), time.monotonic())
            self.assertEqual(130, result.returncode)
            self.assertIn('started', result.stdout)
        finally:
            del local.cancel_event

    def test_missing_cli_and_auth_are_blocked(self):
        with patch.object(CodexExecutionRunner, 'capabilities', return_value={'codex_available':False,'reason':'missing CLI'}):
            with self.assertRaisesRegex(ValueError, 'missing CLI'):
                self.service.preflight(self.item['id'])

        with patch.object(CodexExecutionRunner, 'capabilities', return_value={'codex_available':True}), patch('subprocess.run', return_value=subprocess.CompletedProcess([],1)):
            with self.assertRaisesRegex(ValueError, '认证'):
                self.service.preflight(self.item['id'])

    def test_home_api_projects_are_fixed_and_legacy_migration_is_repeatable(self):
        from http.server import ThreadingHTTPServer
        from http.client import HTTPConnection
        from workbench.workbench_server import WorkbenchApp, make_handler
        legacy = self.items.create({'title':'legacy','raw_signal':'legacy','source':'test'}, 'owner')
        app = WorkbenchApp(self.runtime)
        self.assertEqual(app.default_project, app.initiatives.get(legacy['id'])['project_id'])
        again = WorkbenchApp(self.runtime)
        self.assertEqual(app.default_project, again.default_project)
        app.projects.set_default(self.registered[1]['id'])
        configured = WorkbenchApp(self.runtime)
        self.assertEqual(self.registered[1]['id'], configured.default_project)
        self.assertEqual(app.default_project, configured.initiatives.get(legacy['id'])['project_id'])
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            client.request('GET', '/api/v1/projects')
            response = client.getresponse()
            self.assertEqual(200, response.status)
            self.assertEqual(3, len(json.loads(response.read())['items']))
            client.request('POST', '/api/v1/initiatives/' + self.item['id'] + '/revise',
                json.dumps({'actor':'owner','version':1,'data':{'project_id':self.registered[0]['id']}}),
                {'Content-Type':'application/json'})
            response = client.getresponse()
            self.assertEqual(400, response.status)
            self.assertIn('不能切换项目', json.loads(response.read())['message'])
            client.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(5)


if __name__ == '__main__':
    unittest.main()
