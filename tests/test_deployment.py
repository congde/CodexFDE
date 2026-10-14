"""Isolated local releases: real command/probe and guarded failure paths."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from workbench.daily_delivery import manifest
from workbench.deployment import Deployments, probe
from workbench.initiative import InitiativeStore
from workbench.initiative_workflow import InitiativeWorkflow
from workbench.project_configuration import ProjectConfiguration
from workbench.project_delivery import CandidateProjectEval
from workbench.project_store import ProjectStore
from workbench.task_store import TaskStore
from workbench.deployment_process import OwnedCommand
from workbench.workbench_backup import BackupService
from workbench.maintenance import MaintenanceBusy


SERVICE = '''from http.server import BaseHTTPRequestHandler,HTTPServer
import json,sys
from pathlib import Path
Path(sys.argv[2]).mkdir(parents=True,exist_ok=True)
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'status':'ok'}).encode())
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
'''


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'; self.root.mkdir()
        self.runtime = Path(self.temp.name) / 'runtime'
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        (self.root / 'value.txt').write_text('accepted')
        (self.root / 'fixture_eval.py').write_text("import json\nprint(json.dumps({'summary':{'decision':'pass','total':1,'passed':1,'blocking_failed':0},'results':[{'name':'local','level':'blocking','passed':True}]}))")
        self.tasks = TaskStore(self.runtime / 'workbench.db')
        self.projects = ProjectStore(self.tasks.path)
        self.project = self.projects.create('local', self.root, [sys.executable, 'fixture_eval.py'])
        self.items = InitiativeStore(self.tasks.path)
        self.item = self.items.create({'title':'local release', 'raw_signal':'fixture', 'source':'fixture',
                                      'project_id':self.project['id'], 'success_metric':'observable'}, 'owner')
        self.workflow = InitiativeWorkflow(self.root, self.runtime, self.items, self.tasks, projects=self.projects)
        task = self.tasks.create('accepted fixture', business_refs=['PROJECT:'+self.project['id']])
        self.task_id = task['id']
        for status in ('spec_ready', 'executing', 'evaluating'):
            self.tasks.transition(self.task_id, status)
        report = CandidateProjectEval(self.root, self.runtime, self.task_id, self.project['eval_command'], 'fixture')()
        self.tasks.transition(self.task_id, 'review', result=report)
        self.tasks.review(self.task_id, 'reviewer', 'approve', 'actual fixture result')
        work = self.workflow._load(self.item['id'])
        work.update(stage='integrated', active_task_id=self.task_id, reviewer='reviewer',
                    workspace=str(self.root), candidate_manifest=manifest(self.root, self.runtime))
        self.workflow._save(work)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        self.configurations = ProjectConfiguration(self.projects)
        self.config = self.configurations.save(self.project['id'], {'expected_configuration_revision':0, 'deployment_profiles':[
            {'id':'local', 'command':[sys.executable, '-c', "print('deployed')"],
             'rollback_command':[sys.executable, '-c', "print('rolled back')"],
             'url':f'http://127.0.0.1:{self.port}', 'health':{'path':'/health','expected':{'status':'ok'}},
             'timeout_seconds':1}]}, 'owner')
        self.deployments = Deployments(self.runtime, self.tasks, self.projects, self.workflow, self.configurations)
        self.addCleanup(self.deployments.close)
        self.http = None

    def revision(self):
        return self.workflow._load(self.item['id'])['revision']

    def health_server(self, actual=None):
        payload = actual or {'status':'ok'}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(payload).encode())
            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
        thread = threading.Thread(target=self.http.serve_forever, daemon=True); thread.start()
        def close():
            self.http.shutdown(); self.http.server_close(); thread.join(2)
        self.addCleanup(close)

    def prepared(self):
        return self.deployments.prepare(self.item['id'], 'owner', self.revision(), 'local')

    def start(self, plan=None):
        plan = plan or self.prepared()
        value = self.deployments.start(self.item['id'], 'owner', self.revision(), plan['id'], True)
        self.wait(value)
        return self.deployments._get('deployments', value['id'])

    def wait(self, value):
        self.deployments.workers[value['id']].join(10)
        self.assertFalse(self.deployments.workers[value['id']].is_alive())

    def verify(self, value, conclusion='pass', actor='reviewer'):
        return self.deployments.verify(self.item['id'], actor, self.revision(), value['id'], 'actual business fixture', conclusion)

    def fake_success(self, argv, cwd, timeout):
        return subprocess.CompletedProcess(argv, 0, 'command receipt', '')

    def test_actual_command_health_and_named_business_review(self):
        self.health_server()
        self.deployments.executor = lambda argv,cwd,timeout: subprocess.run(argv,cwd=cwd,capture_output=True,text=True,timeout=timeout)
        plan = self.prepared()
        self.assertEqual(manifest(self.root, self.runtime), manifest(plan['workspace'], self.runtime))
        value = self.start(plan)
        self.assertEqual('awaiting_business_review', value['status'], value.get('error'))
        receipt = json.loads(Path(value['process_path']).read_text(encoding='utf-8'))
        self.assertEqual(plan['command'], receipt['command'])
        self.assertEqual(plan['workspace'], receipt['cwd'])
        with self.assertRaisesRegex(ValueError, '指定验收人'):
            self.verify(value, actor='owner')
        result = self.verify(value)
        self.assertEqual('released', result['status'])
        self.assertTrue(result['business_verified'])
        self.assertEqual('released', self.workflow.get(self.item['id'])['stage'])
        self.assertEqual('accepted', (self.root/'value.txt').read_text())

    def test_consumed_configuration_is_rechecked_at_business_review(self):
        self.health_server(); self.deployments.executor = self.fake_success
        value = self.start()
        self.configurations.save(self.project['id'], {'expected_configuration_revision':1, 'deployment_profiles':self.config['deployment_profiles']}, 'owner')
        with self.assertRaisesRegex(ValueError, '配置变化'):
            self.verify(value)
        self.assertIsNone(self.workflow._load(self.item['id']).get('current_release'))

    def test_integrated_source_drift_blocks_business_review(self):
        self.health_server(); self.deployments.executor = self.fake_success
        value = self.start(); (self.root/'value.txt').write_text('drift')
        with self.assertRaisesRegex(ValueError, '集成源码变化'):
            self.verify(value)

    def test_artifact_drift_blocks_business_review_and_rollback(self):
        self.health_server(); self.deployments.executor = self.fake_success
        value = self.start(); (Path(value['workspace'])/'value.txt').write_text('drift')
        with self.assertRaisesRegex(ValueError, '制品变化'):
            self.verify(value)
        with self.assertRaisesRegex(ValueError, '制品变化'):
            self.deployments.rollback(self.item['id'], 'owner', self.revision(), value['id'], True)

    def test_accepted_evidence_and_process_receipt_are_rechecked(self):
        self.health_server(); self.deployments.executor = self.fake_success
        value = self.start(); task = self.tasks.get(self.task_id)
        report_path = Path(task['result']['runner']['report_path']); raw = report_path.read_bytes()
        report_path.write_bytes(raw+b' ')
        with self.assertRaisesRegex(ValueError, '复验'):
            self.verify(value)
        report_path.write_bytes(raw)
        Path(value['process_path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, '回执变化'):
            self.verify(value)

    def test_start_requires_current_accepted_result_explicit_authorization_and_single_consumption(self):
        self.health_server(); self.deployments.executor = self.fake_success
        plan = self.prepared()
        with self.assertRaisesRegex(ValueError, '明确授权'):
            self.deployments.start(self.item['id'], 'owner', self.revision(), plan['id'], False)
        self.start(plan)
        with self.assertRaisesRegex(ValueError, '已消费'):
            self.deployments.start(self.item['id'], 'owner', self.revision(), plan['id'], True)
        with self.tasks.connect() as db:
            db.execute("UPDATE tasks SET review_note='changed acceptance' WHERE id=?", (self.task_id,))
        # A new plan can use the current accepted result, the consumed one cannot.
        with self.assertRaisesRegex(ValueError, '交付证据变化'):
            self.verify(self.deployments.list(self.item['id'])['items'][0])

    def test_health_failure_keeps_receipt_and_needs_explicit_rollback(self):
        self.deployments.executor = self.fake_success
        value = self.start()
        self.assertEqual('failed', value['status'])
        self.assertTrue(Path(value['process_path']).is_file())
        with self.assertRaisesRegex(ValueError, '健康检查'):
            self.verify(value)
        with self.assertRaisesRegex(ValueError, '明确授权'):
            self.deployments.rollback(self.item['id'], 'owner', self.revision(), value['id'], False)
        self.health_server()
        rollback = self.deployments.rollback(self.item['id'], 'owner', self.revision(), value['id'], True)
        self.wait(rollback)
        result = self.deployments._get('deployments', value['id'])
        self.assertEqual('rolled_back', result['status'])
        self.assertIn('rolled back', json.loads(Path(result['process_path']).read_text())['command'][-1])

    def test_business_failure_does_not_record_release_and_explicit_rollback_can_fail(self):
        self.health_server(); self.deployments.executor = self.fake_success
        value = self.verify(self.start(), 'fail')
        self.assertEqual('business_failed', value['status'])
        self.assertIsNone(self.workflow._load(self.item['id']).get('current_release'))
        self.deployments.executor = lambda argv,cwd,timeout: subprocess.CompletedProcess(argv, 2, '', 'rollback failed')
        rollback = self.deployments.rollback(self.item['id'], 'owner', self.revision(), value['id'], True)
        self.wait(rollback)
        result = self.deployments._get('deployments', value['id'])
        self.assertEqual('rollback_failed', result['status'])
        self.assertEqual(2, json.loads(Path(result['process_path']).read_text())['returncode'])

    def test_rollback_retires_current_release_but_preserves_named_history(self):
        self.health_server();self.deployments.executor=self.fake_success
        value=self.verify(self.start())
        original=self.workflow._load(self.item['id'])['current_release']
        self.assertTrue(original)
        rollback=self.deployments.rollback(self.item['id'],'owner',self.revision(),value['id'],True)
        self.wait(rollback)
        state=self.workflow._load(self.item['id'])
        self.assertIsNone(state['current_release'])
        self.assertEqual('integrated',state['stage'])
        self.assertEqual(original,state['delivery_records'][-1]['id'])

    def test_real_foreground_service_is_owned_and_shutdown_stops_process_tree(self):
        profiles = self.config['deployment_profiles']
        profiles[0].update(command=[sys.executable, '-u', '-c', SERVICE, '{port}', '{runtime_dir}'], timeout_seconds=5)
        self.configurations.save(self.project['id'], {'expected_configuration_revision':1,'deployment_profiles':profiles}, 'owner')
        value = self.start()
        self.assertEqual('awaiting_business_review', value['status'], value.get('error'))
        process = self.deployments.processes[value['id']].process
        self.assertTrue(probe(value['url'], profiles[0]['health'])['passed'])
        self.assertTrue(self.deployments.busy())
        self.assertEqual(0, self.deployments.shutdown()['workers_remaining'])
        self.assertIsNotNone(process.poll())
        self.assertFalse(probe(value['url'], profiles[0]['health'], timeout=.1)['passed'])
        self.assertEqual('interrupted', self.deployments._get('deployments', value['id'])['status'])
        restored = Deployments(self.runtime,self.tasks,self.projects,self.workflow,self.configurations)
        self.assertFalse(restored.workers)
        self.assertFalse(restored.processes)

    def test_restart_marks_inflight_deployment_interrupted_without_replay(self):
        self.deployments.executor = self.fake_success
        value = self.prepared(); value.update(id='interrupted-fixture',status='running',deployment_id='interrupted-fixture')
        self.deployments._save('deployments',value)
        with patch('workbench.deployment_process.OwnedCommand') as command:
            restored = Deployments(self.runtime,self.tasks,self.projects,self.workflow,self.configurations)
            command.assert_not_called()
        saved = restored._get('deployments', value['id'])
        self.assertEqual('interrupted',saved['status']); self.assertFalse(saved['automatic_replay'])

    def test_close_failure_retains_owned_service_and_blocks_backup_until_retry(self):
        profiles = self.config['deployment_profiles']
        profiles[0].update(command=[sys.executable, '-u', '-c', SERVICE, '{port}', '{runtime_dir}'], timeout_seconds=5)
        self.configurations.save(self.project['id'], {'expected_configuration_revision':1,'deployment_profiles':profiles}, 'owner')
        value = self.start(); owned = self.deployments.processes[value['id']]
        try:
            with patch.object(owned, 'close', side_effect=RuntimeError('fixture deployment close failure')):
                with self.assertRaisesRegex(RuntimeError, 'deployment close failure'): self.deployments._stop_process(value['id'])
                self.assertIs(owned, self.deployments.processes.get(value['id']))
                self.assertTrue(self.deployments.busy())
                with self.assertRaises(MaintenanceBusy): BackupService(self.runtime).create('fixture-owner', busy_check=self.deployments.busy)
            self.deployments._stop_process(value['id'])
            self.assertFalse(self.deployments.busy()); self.assertIsNotNone(owned.process.poll())
        finally:
            owned.close()
            self.deployments.processes.pop(value['id'], None)

    def test_failed_command_cleanup_failure_keeps_failure_record_and_cleanup_receipt(self):
        profiles = self.config['deployment_profiles']
        profiles[0].update(command=[sys.executable, '-u', '-c', "print('actual failure receipt');raise SystemExit(7)"], timeout_seconds=5)
        self.configurations.save(self.project['id'], {'expected_configuration_revision':1,'deployment_profiles':profiles}, 'owner')
        captured, patches = [], []
        def owned_command(command, cwd):
            owned = OwnedCommand(command, cwd); captured.append(owned)
            failing = patch.object(owned, 'close', side_effect=RuntimeError('fixture failed cleanup'))
            failing.start(); patches.append(failing)
            return owned
        try:
            with patch('workbench.deployment_process.OwnedCommand', side_effect=owned_command): value = self.start()
            self.assertEqual('failed', value['status']); self.assertTrue(value['cleanup_required'])
            self.assertIn('fixture failed cleanup', value['cleanup_error'])
            self.assertEqual(7, json.loads(Path(value['process_path']).read_text())['returncode'])
            cleanup = json.loads(Path(value['cleanup_path']).read_text())
            self.assertEqual('failed', cleanup['status']); self.assertIn('fixture failed cleanup', cleanup['error'])
            self.assertEqual(hashlib.sha256(Path(value['cleanup_path']).read_bytes()).hexdigest(), value['cleanup_sha256'])
            self.assertIs(captured[0], self.deployments.processes[value['id']]); self.assertTrue(self.deployments.busy())
            with self.assertRaises(MaintenanceBusy): BackupService(self.runtime).create('fixture-owner', busy_check=self.deployments.busy)
        finally:
            for failing in patches: failing.stop()
            for owned in captured: owned.close()
            self.deployments.processes.clear()

    def test_missing_database_does_not_prevent_owned_service_shutdown_or_create_replacement(self):
        profiles = self.config['deployment_profiles']
        profiles[0].update(command=[sys.executable, '-u', '-c', SERVICE, '{port}', '{runtime_dir}'], timeout_seconds=5)
        self.configurations.save(self.project['id'], {'expected_configuration_revision':1,'deployment_profiles':profiles}, 'owner')
        value = self.start()
        self.assertEqual('awaiting_business_review', value['status'], value.get('error'))
        process = self.deployments.processes[value['id']].process
        database = Path(self.tasks.path)
        retained = database.with_name('workbench-retained.db')
        self.assertTrue(database.resolve().is_relative_to(Path(self.temp.name)))
        self.assertTrue(retained.resolve().is_relative_to(Path(self.temp.name)))
        database.replace(retained)
        try:
            with self.assertLogs('workbench.deployment', level='WARNING'):
                result = self.deployments.close()
            self.assertEqual(0, result['workers_remaining'])
            self.assertTrue(result['errors'])
            self.assertTrue(self.deployments.closed)
            self.assertIsNotNone(process.poll())
            self.assertFalse(probe(value['url'], profiles[0]['health'], timeout=.1)['passed'])
            self.assertFalse(database.exists())
            self.assertTrue(retained.is_file())
        finally:
            retained.replace(database)

    def test_actual_nonzero_command_cannot_use_healthy_service_to_pass(self):
        self.health_server()
        self.deployments.executor = lambda argv,cwd,timeout: subprocess.run(argv,cwd=cwd,capture_output=True,text=True,timeout=timeout)
        profiles = self.config['deployment_profiles']
        profiles[0]['command'] = [sys.executable, '-c', 'raise SystemExit(4)']
        self.configurations.save(self.project['id'], {'expected_configuration_revision':1,'deployment_profiles':profiles}, 'owner')
        value = self.start()
        self.assertEqual('failed',value['status'])
        self.assertEqual(4,json.loads(Path(value['process_path']).read_text())['returncode'])
        self.assertNotEqual('released',self.workflow.get(self.item['id'])['stage'])

    def test_existing_service_port_cannot_be_mistaken_for_owned_deployment_health(self):
        self.health_server()
        value=self.start()
        self.assertEqual('failed',value['status'])
        self.assertIn('端口',value.get('error',''))
        self.assertFalse(self.deployments.processes)


class DeploymentProbeTests(unittest.TestCase):
    def test_health_redirect_does_not_contact_another_authority(self):
        contacted=[]
        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                contacted.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ok"}')
            def log_message(self,*args):pass
        target=ThreadingHTTPServer(('127.0.0.1',0),Target)
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{target.server_port}/health');self.end_headers()
            def log_message(self,*args):pass
        redirect=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        threads=[]
        for server in (target,redirect):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();threads.append(thread)
        try:
            value=probe(f'http://127.0.0.1:{redirect.server_port}',{'path':'/health','expected':{'status':'ok'}})
            self.assertFalse(value['passed']);self.assertEqual([],contacted)
            self.assertIn('302',value['error'])
        finally:
            for server,thread in zip((target,redirect),threads):
                server.shutdown();server.server_close();thread.join(2)
