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
            (root / 'check.py').write_text("import json\nfrom pathlib import Path\n"
                "assert Path('value.txt').read_text() in ('1', '2')\n"
                "print(json.dumps({'summary': {'decision':'pass','total':1,'passed':1,'blocking_failed':0},"
                "'results':[{'name':'value','level':'blocking','passed':True}]}))\n")
            self.registered.append(self.projects.create(name, root, [sys.executable, 'check.py']))
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
