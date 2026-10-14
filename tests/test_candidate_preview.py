import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from workbench.task_store import TaskStore
from workbench.candidate_preview import CandidatePreviews
from workbench.process_guard import spawn as owned_spawn
from workbench.managed_process import ManagedProcess
from workbench.generic_preview import ProjectPreviews
from workbench.migration import prepare_migration, restore_migration
from workbench.project_configuration import ProjectConfiguration
from workbench.project_delivery import CandidateProjectEval
from workbench.project_store import ProjectStore
from workbench.reference_paths import reference_mapping_scope, unload_reference_mappings


PREVIEW_SERVICE = r'''
import hashlib,json,os,sys,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
data=Path(sys.argv[2]);status=sys.argv[3]
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        (data/'health-requested').write_text('fixture request',encoding='utf-8')
        if status=='stalled':time.sleep(30)
        body=json.dumps({'service':'flowerp','status':status,'runtime_id':hashlib.sha256(os.path.normcase(str(data.resolve())).encode()).hexdigest()}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
'''


class CandidatePreviewTests(unittest.TestCase):
    def fixture(self, directory):
        root=Path(directory);store=TaskStore(root/'workbench.db')
        task=store.create('isolated legacy preview fixture',execution_mode='codex',write_scope=['web'])
        for state in ['spec_ready','executing','evaluating','review']:store.transition(task['id'],state)
        store.append_event(task['id'],'课程红绿差分判定已完成',evidence={'accepted':True})
        store.append_event(task['id'],'受控执行阶段完成',evidence={'change_manifest':[
            {'path':'web/index.html','after_sha256':hashlib.sha256(b'checked').hexdigest()}]})
        web=root/'course-worktrees'/task['id']/'web';web.mkdir(parents=True);(web/'index.html').write_bytes(b'checked')
        return root,store,task

    def fixture_spawn(self, status, captured):
        def launch(command, workspace):
            port=command[command.index('--port')+1];data=command[command.index('--runtime-dir')+1]
            process,owner,prefix=owned_spawn([sys.executable,'-X','utf8','-u','-c',PREVIEW_SERVICE,port,data,status],workspace)
            captured.append((process,owner,int(port)))
            return process,owner,prefix
        return launch

    def wait_until(self, predicate, seconds=5):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.02)
        self.fail('isolated process fixture did not reach the expected state')

    def cleanup_processes(self, captured):
        for process,owner,_ in captured:
            if owner:owner.close()
            if process.poll() is None:process.kill()
            process.wait(timeout=5)

    def migrated_daily_fixture(self, directory):
        root = Path(directory).resolve()
        original = root / 'original-runtime'
        store = TaskStore(original / 'workbench.db')
        source = root / 'original-project'
        source.mkdir()
        subprocess.run(['git', 'init', '-q'], cwd=source, check=True, capture_output=True)
        script = ("import json\nprint(json.dumps({'summary':{'decision':'pass','total':1,'passed':1,'blocking_failed':0},"
                  "'results':[{'name':'maintenance-fixture','level':'blocking','passed':True}]}))\n")
        (source / 'fixture_eval.py').write_text(script, encoding='utf-8')
        projects = ProjectStore(store.path)
        command = [sys.executable, '-B', 'fixture_eval.py']
        projects.create('isolated preview migration fixture', source, command)
        spec = original / 'specs' / 'preview.md'
        spec.parent.mkdir()
        spec.write_text('Isolated historical preview fixture; no student or customer acceptance.', encoding='utf-8')
        task = store.create('isolated migrated daily preview', requirement_id='LEGACY-PREVIEW-FIXTURE',
                            spec_path=str(spec), execution_mode='codex', write_scope=['web'])
        workspace = original / 'daily-delivery' / task['id'] / 'workspace'
        (workspace / 'web').mkdir(parents=True)
        (workspace / 'web/index.html').write_bytes(b'checked preview fixture')
        (workspace / 'fixture_eval.py').write_text(script, encoding='utf-8')
        subprocess.run(['git', 'init', '-q'], cwd=workspace, check=True, capture_output=True)
        for status in ('spec_ready', 'executing', 'evaluating'):
            store.transition(task['id'], status)
        report = CandidateProjectEval(workspace, original, task['id'], command, 'migration-fixture')()
        store.transition(task['id'], 'review', result=report)
        store.append_event(task['id'], '受控执行阶段完成', evidence={'change_manifest':[
            {'path':'web/index.html', 'after_sha256':hashlib.sha256(b'checked preview fixture').hexdigest()}]})
        store.append_event(task['id'], '日常研发交付包已保存', evidence={
            'status':'review', 'workspace':str(workspace)})
        with store.connect() as db:
            original_result = db.execute('SELECT result_json FROM tasks WHERE id=?', (task['id'],)).fetchone()[0]
            original_events = [tuple(row) for row in db.execute(
                'SELECT id,evidence_json FROM task_events WHERE task_id=? ORDER BY id', (task['id'],))]
        evidence_hashes = {path.relative_to(original).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (Path(report['runner']['report_path']), Path(report['runner']['process_path']))}
        backup = prepare_migration(original, projects, 'fixture-owner')
        target = root / 'migrated-runtime'
        migrated_source = root / 'migrated-project'
        shutil.copytree(source, migrated_source)
        restored = restore_migration(backup['archive_path'], target, {str(source):str(migrated_source)})
        self.assertEqual('ready', restored['execution_environment']['status'])
        # Remove the historical candidate location without deleting its bytes.
        retained = workspace.parent / 'retained-original-workspace'
        self.assertTrue(workspace.resolve().is_relative_to(root))
        self.assertTrue(retained.resolve().is_relative_to(root))
        workspace.rename(retained)
        self.assertFalse(workspace.exists())
        migrated_store = TaskStore(target / 'workbench.db')
        migrated_projects = ProjectStore(migrated_store.path)
        previews = ProjectPreviews(target, migrated_store, migrated_projects, ProjectConfiguration(migrated_projects))
        self.addCleanup(unload_reference_mappings, target)
        self.addCleanup(unload_reference_mappings, original)
        return {'runtime':target, 'store':migrated_store, 'task':task, 'previews':previews,
                'historical_workspace':workspace, 'workspace':target / workspace.relative_to(original),
                'original_result':original_result, 'original_events':original_events, 'evidence_hashes':evidence_hashes}

    def assert_migrated_history_unchanged(self, fixture):
        with fixture['store'].connect() as db:
            result = db.execute('SELECT result_json FROM tasks WHERE id=?', (fixture['task']['id'],)).fetchone()[0]
            events = [tuple(row) for row in db.execute(
                'SELECT id,evidence_json FROM task_events WHERE task_id=? AND id<=? ORDER BY id',
                (fixture['task']['id'], fixture['original_events'][-1][0]))]
        self.assertEqual(fixture['original_result'], result)
        self.assertEqual(fixture['original_events'], events)
        for relative, expected in fixture['evidence_hashes'].items():
            self.assertEqual(expected, hashlib.sha256((fixture['runtime'] / relative).read_bytes()).hexdigest())

    def test_migrated_daily_preview_plan_and_start_share_effective_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.migrated_daily_fixture(directory)
            previews = fixture['previews']; captured = []
            plan = previews.plan(fixture['task']['id'])
            self.assertTrue(plan['available']); self.assertTrue(plan['legacy'])
            with patch('workbench.candidate_preview.spawn', side_effect=self.fixture_spawn('ok', captured)) as launch:
                try:
                    opened = previews.start(fixture['task']['id'], 'fixture-owner')
                    self.assertEqual(str(fixture['workspace']), opened['workspace'])
                    self.assertEqual(fixture['workspace'], launch.call_args.args[1])
                    self.assertEqual(1, launch.call_count)
                    self.assertIsNone(captured[0][0].poll())
                    self.assert_migrated_history_unchanged(fixture)
                    previews.close()
                    self.assertIsNotNone(captured[0][0].poll()); self.assertFalse(previews.busy())
                finally:
                    previews.close(); self.cleanup_processes(captured)

    def test_migrated_daily_preview_rejects_effective_workspace_outside_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.migrated_daily_fixture(directory)
            outside = Path(directory) / 'outside-candidate'
            shutil.copytree(fixture['workspace'], outside)
            mapping = {str(fixture['historical_workspace']):str(outside)}
            try:
                with reference_mapping_scope(fixture['runtime'], mapping), patch('workbench.candidate_preview.spawn') as launch:
                    with self.assertRaisesRegex(ValueError, '不在原运行目录'):
                        CandidatePreviews(fixture['runtime'], fixture['store']).start(fixture['task']['id'], 'fixture-owner')
                    launch.assert_not_called()
                self.assert_migrated_history_unchanged(fixture)
            finally:
                fixture['previews'].close()

    def test_migrated_daily_preview_rejects_candidate_changed_after_eval(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.migrated_daily_fixture(directory)
            plan = fixture['previews'].plan(fixture['task']['id'])
            self.assertTrue(plan['available'])
            (fixture['workspace'] / 'web/index.html').write_bytes(b'changed after migration')
            try:
                with patch('workbench.candidate_preview.spawn') as launch:
                    with self.assertRaisesRegex(ValueError, '变化|重新'):
                        fixture['previews'].start(fixture['task']['id'], 'fixture-owner')
                    launch.assert_not_called()
                self.assert_migrated_history_unchanged(fixture)
            finally:
                fixture['previews'].close()

    def test_migrated_daily_preview_rejects_symbolic_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.migrated_daily_fixture(directory)
            workspace = fixture['workspace']
            retained = workspace.parent / 'retained-migrated-workspace'
            self.assertTrue(workspace.resolve().is_relative_to(fixture['runtime']))
            self.assertTrue(retained.resolve().is_relative_to(fixture['runtime']))
            workspace.rename(retained)
            try:
                workspace.symlink_to(retained, target_is_directory=True)
            except OSError as error:
                retained.rename(workspace)
                self.skipTest('directory symlink is unavailable: ' + str(error))
            try:
                with patch('workbench.candidate_preview.spawn') as launch:
                    with self.assertRaisesRegex(ValueError, '不在原运行目录'):
                        CandidatePreviews(fixture['runtime'], fixture['store']).start(fixture['task']['id'], 'fixture-owner')
                    launch.assert_not_called()
                self.assert_migrated_history_unchanged(fixture)
            finally:
                workspace.unlink()
                retained.rename(workspace)
                fixture['previews'].close()

    def test_unverified_candidate_never_launches(self):
        with tempfile.TemporaryDirectory() as directory, patch('workbench.candidate_preview.spawn') as spawn:
            root = Path(directory); store = TaskStore(root/'workbench.db')
            task = store.create('preview fixture', execution_mode='codex', write_scope=['flowerp'])
            with self.assertRaisesRegex(ValueError, '还未完成'):
                CandidatePreviews(root,store).start(task['id'],'maintenance fixture')
            spawn.assert_not_called()

    def test_changed_delivery_file_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as directory, patch('workbench.candidate_preview.spawn') as spawn:
            root = Path(directory); store = TaskStore(root/'workbench.db')
            task = store.create('preview fixture', execution_mode='codex', write_scope=['web'])
            for state in ['spec_ready','executing','evaluating','review']: store.transition(task['id'],state)
            store.append_event(task['id'],'课程红绿差分判定已完成',evidence={'accepted':True})
            store.append_event(task['id'],'受控执行阶段完成',evidence={'change_manifest':[
                {'path':'web/index.html','after_sha256':hashlib.sha256(b'checked').hexdigest()}]})
            candidate = root/'course-worktrees'/task['id']/'web';candidate.mkdir(parents=True)
            (candidate/'index.html').write_bytes(b'changed after eval')
            with self.assertRaisesRegex(ValueError,'又发生变化'):
                CandidatePreviews(root,store).start(task['id'],'maintenance fixture')
            spawn.assert_not_called()

    def test_real_unhealthy_startup_shutdown_is_prompt_and_leaves_no_process_or_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root,store,task=self.fixture(directory);previews=CandidatePreviews(root,store);captured=[];errors=[];close_errors=[]
            def start():
                try:previews.start(task['id'],'isolated fixture operator')
                except Exception as exc:errors.append(exc)
            def close():
                try:previews.close()
                except Exception as exc:close_errors.append(exc)
            starter=threading.Thread(target=start,daemon=True);closer=threading.Thread(target=close,daemon=True)
            with patch('workbench.candidate_preview.spawn',side_effect=self.fixture_spawn('stalled',captured)):
                try:
                    starter.start();self.wait_until(lambda:(root/'candidate-previews'/task['id']/'health-requested').is_file())
                    self.assertIsNone(captured[0][0].poll());started=time.monotonic();closer.start();closer.join(2)
                    elapsed=time.monotonic()-started
                    print(json.dumps({'fixture':'legacy_preview_unhealthy_shutdown','elapsed_seconds':round(elapsed,3),'close_finished':not closer.is_alive()}),flush=True)
                    self.assertFalse(closer.is_alive(),'close must interrupt health startup instead of waiting its 15-second deadline')
                    self.assertLess(elapsed,2);starter.join(2);self.assertFalse(starter.is_alive());self.assertFalse(close_errors)
                    self.assertTrue(errors);self.assertIsInstance(errors[0],ValueError);self.assertIsNotNone(captured[0][0].poll());self.assertFalse(previews.running);self.assertFalse(previews._owned)
                    self.assertFalse(any(e['detail']=='已打开本次候选成果预览' for e in store.get(task['id'])['events']))
                    with socket.socket() as sock:self.assertNotEqual(0,sock.connect_ex(('127.0.0.1',captured[0][2])))
                finally:
                    self.cleanup_processes(captured);starter.join(3)
                    if closer.ident:closer.join(3)
                    previews.close()

    def test_closed_preview_rejects_new_launch_before_any_spawn(self):
        with tempfile.TemporaryDirectory() as directory,patch('workbench.candidate_preview.spawn') as spawn:
            root,store,task=self.fixture(directory);previews=CandidatePreviews(root,store);previews.close()
            with self.assertRaisesRegex(ValueError,'关闭'):previews.start(task['id'],'isolated fixture operator')
            spawn.assert_not_called()

    def test_shutdown_cancels_wait_for_old_runtime_release_before_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            root,store,task=self.fixture(directory);previews=CandidatePreviews(root,store)
            waiting=threading.Event();errors=[]
            def occupied(*args,**kwargs):
                waiting.set();raise RuntimeError('fixture previous runtime still owns data')
            def start():
                try:previews.start(task['id'],'isolated fixture operator')
                except Exception as error:errors.append(error)
            starter=threading.Thread(target=start)
            with patch('workbench.candidate_preview.wait_for_product_release',side_effect=occupied),patch('workbench.candidate_preview.spawn') as spawn:
                try:
                    starter.start();self.assertTrue(waiting.wait(2));began=time.monotonic()
                    previews.close();starter.join(2)
                    self.assertFalse(starter.is_alive());self.assertLess(time.monotonic()-began,2)
                    self.assertTrue(errors);self.assertIsInstance(errors[0],ValueError)
                    spawn.assert_not_called();self.assertFalse(previews._owned)
                finally:previews.close();starter.join(3)

    def test_pending_cleanup_is_busy_and_cannot_be_overwritten_by_a_new_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            root,store,task=self.fixture(directory);previews=CandidatePreviews(root,store)
            logs=root/'pending-logs';logs.mkdir()
            old=ManagedProcess([sys.executable,'-c','import time;time.sleep(30)'],root,log_dir=logs)
            previews._owned[task['id']]=old
            try:
                with patch.object(old,'close',side_effect=RuntimeError('fixture pending cleanup')),patch('workbench.candidate_preview.spawn') as launch:
                    self.assertTrue(previews.busy())
                    with self.assertRaisesRegex(RuntimeError,'pending cleanup'):
                        previews._start_process(task['id'],[sys.executable,'-c','pass'],root,logs,log_mode='a',spawn_factory=launch)
                    launch.assert_not_called();self.assertIs(old,previews._owned[task['id']]);self.assertTrue(previews.busy())
                def launch(command,workspace):
                    self.assertIsNotNone(old.process.poll());self.assertTrue(old.process.stdout.closed)
                    return owned_spawn(command,workspace)
                new=previews._start_process(task['id'],[sys.executable,'-c','import time;time.sleep(30)'],root,logs,log_mode='a',spawn_factory=launch)
                self.assertIsNot(old,new);self.assertIs(new,previews._owned[task['id']]);self.assertTrue(previews.busy())
                previews.close();self.assertFalse(previews.busy());self.assertIsNotNone(new.process.poll())
            finally:old.close();previews.close()

    def test_close_during_real_spawn_owns_process_before_the_launch_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root,store,task=self.fixture(directory);previews=CandidatePreviews(root,store);captured=[];spawned=threading.Event();errors=[]
            launch=self.fixture_spawn('ok',captured)
            def delayed_spawn(command,workspace):
                value=launch(command,workspace);spawned.set()
                if not previews._closed.wait(3):raise RuntimeError('fixture shutdown was not signalled')
                return value
            def start():
                try:previews.start(task['id'],'isolated fixture operator')
                except Exception as error:errors.append(error)
            starter=threading.Thread(target=start,daemon=True);closer=threading.Thread(target=previews.close,daemon=True)
            with patch('workbench.candidate_preview.spawn',side_effect=delayed_spawn):
                try:
                    starter.start();self.assertTrue(spawned.wait(5));started=time.monotonic();closer.start();closer.join(2);starter.join(2)
                    elapsed=time.monotonic()-started
                    print(json.dumps({'fixture':'legacy_preview_spawn_gate_shutdown','elapsed_seconds':round(elapsed,3),'close_finished':not closer.is_alive()}),flush=True)
                    self.assertFalse(closer.is_alive());self.assertFalse(starter.is_alive());self.assertLess(elapsed,2)
                    self.assertTrue(errors);self.assertIsInstance(errors[0],ValueError);self.assertIsNotNone(captured[0][0].poll());self.assertFalse(previews._owned);self.assertFalse(previews.running)
                    self.assertFalse((root/'candidate-previews'/task['id']/'health-requested').exists())
                    self.assertFalse(any(e['detail']=='已打开本次候选成果预览' for e in store.get(task['id'])['events']))
                finally:
                    previews._closed.set();self.cleanup_processes(captured);starter.join(3)
                    if closer.ident:closer.join(3)
                    previews.close()

    def test_real_ready_preview_keeps_legacy_reuse_contract_and_closes_its_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root,store,task=self.fixture(directory);previews=CandidatePreviews(root,store);captured=[]
            with patch('workbench.candidate_preview.spawn',side_effect=self.fixture_spawn('ok',captured)) as spawn:
                try:
                    result=previews.start(task['id'],'isolated fixture operator');reused=previews.start(task['id'],'isolated fixture operator')
                    self.assertEqual(task['id'],result['task_id']);self.assertFalse(result['human_accepted']);self.assertEqual(result['url'],reused['url']);self.assertTrue(reused['reused']);self.assertEqual(1,spawn.call_count)
                    managed = previews._owned[task['id']]
                    self.assertEqual(2, len(managed.readers))
                    previews.close();self.assertIsNotNone(captured[0][0].poll());self.assertFalse(previews.running)
                    self.assertFalse(any(reader.is_alive() for reader in managed.readers))
                    self.assertEqual(1,sum(e['detail']=='已打开本次候选成果预览' for e in store.get(task['id'])['events']))
                finally:self.cleanup_processes(captured);previews.close()
