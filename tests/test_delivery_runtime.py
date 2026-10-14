"""Local process fixtures, isolated repositories; no model or customer data."""
import json
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from workbench.daily_delivery import manifest, prepare_daily
from workbench.delivery_runtime import DeliveryRuntime
from workbench.execution import CodexExecutionRunner
from workbench.initiative import InitiativeStore
from workbench.initiative_workflow import InitiativeWorkflow
from workbench.platform_api import HarnessPlatformAPI
from workbench.project_store import ProjectStore
from workbench.project_runner import ProjectExecutionRunner, ProjectEvalRunner
from workbench.task_store import TaskStore
from workbench.tool_registry import ToolSpec


class LocalWorker(CodexExecutionRunner):
    """Use the production streaming/process ownership path with local Python."""
    delay = .25
    ready = set()
    ready_lock = threading.Lock()
    active = 0
    peak = 0
    completion_gate = None
    cli_executable = None
    def _run_codex_streaming(self, command, prompt, timeout, callback, started):
        matched = re.search(r'相对路径：([a-z]\.txt)',prompt)
        filename = matched.group(1) if matched else 'a.txt'
        completion_gate = self.completion_gate
        wait_for_completion = 'time.sleep(' + str(self.delay) + ')\n'
        if completion_gate is not None:
            wait_for_completion = (
                'gate=Path(' + repr(str(completion_gate)) + ')\n'
                'deadline=time.monotonic()+30\n'
                'while not gate.is_file():\n'
                "    if time.monotonic() >= deadline: raise TimeoutError('four CLI completion gate did not open')\n"
                '    time.sleep(.01)\n')
        script = ('import time,json\nfrom pathlib import Path\n'
                  + "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}}),flush=True)\n"
                  + wait_for_completion
                  + ("Path(" + repr(filename) + ").write_text('2')\n" if 'read-only' not in command else '')
                  + "print('fixture finished')")
        observed_usage = False
        def observed(line):
            nonlocal observed_usage
            callback(line)
            if 'turn.completed' in line:
                with self.ready_lock:
                    self.ready.add(filename)
                    observed_usage = True
                    LocalWorker.active += 1
                    LocalWorker.peak = max(LocalWorker.peak, LocalWorker.active)
                    if completion_gate is not None and LocalWorker.active == 4 and len(self.ready) == 4:
                        completion_gate.write_text('four real CLI usage callbacks observed')
        try:
            return super()._run_codex_streaming([self.cli_executable or sys.executable, '-c', script], '', timeout, observed, started)
        finally:
            if observed_usage:
                with self.ready_lock:LocalWorker.active -= 1


def wait_for_worker_usage(case, expected=None, timeout=30):
    """Wait for actual owned-process output after bounded snapshot preparation."""
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        with LocalWorker.ready_lock:
            ready=set(LocalWorker.ready)
        if ready and (expected is None or ready==expected):
            return
        worker=case.runtime.workers.get(case.item['id']) or case.workflow.workers.get(case.item['id'])
        if worker is not None and not worker.is_alive():
            case.fail('owned fixture ended before its process marker: '+repr(case.runtime._load(case.item['id'])))
        time.sleep(.02)
    case.fail('owned fixture did not produce its actual process marker before the startup deadline')


class DeliveryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Compatibility tests compose this setUp without inheriting the class.
        self.addCleanup(DeliveryRuntimeTests._cleanup_runtime_fixture, self)
        self.root = Path(self.temp.name)
        self.repository = self.root / 'project'
        self.repository.mkdir()
        subprocess.run(['git', 'init', '-q'], cwd=self.repository, check=True)
        for name in ('a.txt', 'b.txt'):
            (self.repository / name).write_text('1')
        (self.repository / 'delivery_contract_eval.py').write_text("import json;from pathlib import Path\n"
            "ok=all(Path(n).read_text() in ('1','2') for n in ('a.txt','b.txt'))\n"
            "print(json.dumps({'summary':{'decision':'pass' if ok else 'block','total':1,'passed':int(ok),'blocking_failed':int(not ok)},'results':[{'name':'value','level':'blocking','passed':ok}]}))\n"
            "raise SystemExit(0 if ok else 1)\n")
        self.runtime_dir = self.root / 'runtime'
        self.tasks = TaskStore(self.runtime_dir / 'workbench.db')
        self.projects = ProjectStore(self.tasks.path)
        self.project = self.projects.create('test', self.repository, [sys.executable, 'delivery_contract_eval.py'])
        self.items = InitiativeStore(self.tasks.path)
        self.item = self.items.create({'title':'change two files', 'raw_signal':'change two files', 'source':'fixture',
                                      'project_id':self.project['id'], 'success_metric':'both values are 2'}, 'owner')
        self.workflow = InitiativeWorkflow(self.repository, self.runtime_dir, self.items, self.tasks,
                                           projects=self.projects, enabled=True)
        self.runtime = DeliveryRuntime(self.repository, self.runtime_dir, self.tasks, self.projects,
                                       self.items, self.workflow, runner_factory=LocalWorker)
        self.workflow.submitter = self.runtime.submit_daily
        self.plan = prepare_daily(self.repository, self.runtime_dir, 'owner', 'update-a and update-b',
                                  'values are 2', ['a.txt', 'b.txt'], project=self.project)
        self.plan.update(plan_id='plan-fixture', expires_at=time.time()+900)
        self.runtime.freeze_profile(self.item['id'], self.plan)
        work = self.workflow._load(self.item['id'])
        work.update(stage='confirmed', plan=self.plan, reviewer='reviewer', origin_manifest=self.plan['source_manifest'])
        self.workflow._save(work)

    def _cleanup_runtime_fixture(self):
        try:
            runtime = getattr(self, 'runtime', None)
            if runtime is not None:
                receipt = runtime.close()
                workers = [*runtime.workers.copy().values(), *runtime.tool_workers.copy(),
                           *runtime.workflow.workers.copy().values()]
                pending = [worker.name for worker in set(workers) if worker.is_alive()]
                if receipt['workers_still_stopping'] or pending:
                    raise RuntimeError('Runtime fixture shutdown incomplete; directory retained: '
                                       + str(self.root) + '; workers: ' + ', '.join(pending))
            self.temp.cleanup()
        except BaseException:
            self.temp._finalizer.detach()
            raise

    def view(self):
        return self.runtime.view(self.item['id'])

    def prepare(self, second=None, *, max_workers=2):
        return self.runtime.prepare_subtasks(self.item['id'], 'owner', self.view()['revision'], [
            {'name':'a', 'prompt':'update-a', 'write_set':['a.txt']},
            second or {'name':'b', 'prompt':'update-b', 'write_set':['b.txt']}], max_workers=max_workers)

    def start(self):
        view = self.prepare()
        return self.runtime.start_subtasks(self.item['id'], 'owner', view['revision'], 'start-key', view['subtask_plan_id'])

    def wait(self):
        workers = [self.runtime.workers.get(self.item['id']), self.workflow.workers.get(self.item['id'])]
        for worker in workers:
            if worker is not None:
                worker.join(30)
                self.assertFalse(worker.is_alive())
        return self.view()

    def test_parallel_cli_workers_merge_and_eval_without_source_writes(self):
        self.start()
        result = self.wait()
        self.assertEqual('review', result['state'], result['error'])
        self.assertTrue(all(c['status']=='completed' for c in result['subtasks']))
        self.assertEqual('1', (self.repository / 'a.txt').read_text())
        self.assertEqual('2', (Path(result['workspace']) / 'a.txt').read_text())
        task = self.tasks.get(result['task_id'])
        evidence = next(e['evidence'] for e in task['events'] if e['detail']=='受控执行阶段完成')
        self.assertTrue(evidence['overlap_proved'])
        self.assertEqual(40, result['budget']['tokens_used'])
        accepted = self.workflow.accept(self.item['id'], 'reviewer', self.workflow.get(self.item['id'])['revision'], 'fixture independent acceptance')
        self.assertEqual('accepted', accepted['stage'])

    def test_conflicting_or_parent_scope_expansion_is_rejected_before_start(self):
        with self.assertRaisesRegex(ValueError, '不得并行'):
            self.prepare({'name':'b', 'prompt':'change a', 'read_set':['a.txt'], 'write_set':['b.txt']})
        with self.assertRaisesRegex(ValueError, '父事项'):
            self.prepare({'name':'b', 'prompt':'change checks', 'write_set':['delivery_contract_eval.py']})
        self.assertFalse(self.runtime.workers)

    def test_shutdown_refuses_new_delivery_and_prepared_workers(self):
        prepared = self.prepare()
        self.runtime.close()
        with patch.object(LocalWorker, '_run_codex_streaming') as process:
            with self.assertRaisesRegex(ValueError, '关闭'):
                self.runtime._begin(self.item['id'], self.plan)
            with self.assertRaisesRegex(ValueError, '关闭'):
                self.runtime.start_subtasks(self.item['id'], 'owner', prepared['revision'],
                                            'closed-start', prepared['subtask_plan_id'])
            process.assert_not_called()
        self.assertFalse(self.runtime.workers)

    def test_shutdown_and_approved_worker_handoff_never_join_an_unstarted_thread(self):
        self.runtime._begin(self.item['id'], self.plan)
        with self.runtime.lock:
            data = self.runtime._load(self.item['id'])
            data.update(workspace=str(self.repository), task_id='TASK-CONTEXT')
            self.runtime._save(data)
        spec = ToolSpec('shell.exec', 'shell', 'fixture', frozenset(), lambda c, a: {}, approval='ask')
        self.runtime.authorize_tool({'initiative_id': self.item['id'], 'task': {'id': 'TASK-CONTEXT'}},
                                    spec, {'command': ['git', 'status']}, 'handoff', 'owner')
        view = self.view()
        self.runtime.tool_executor = lambda *args: {'success': True, 'fixture': True}
        handoff, release = threading.Event(), threading.Event()
        errors = []
        original_start = threading.Thread.start
        def held_start(thread):
            if getattr(thread._target, '__name__', '') == '_execute_approved_tool':
                handoff.set()
                if not release.wait(5):
                    raise RuntimeError('fixture handoff timed out')
            return original_start(thread)
        def capture(call):
            try:
                call()
            except Exception as error:
                errors.append(error)
        with patch.object(threading.Thread, 'start', held_start):
            deciding = threading.Thread(target=capture, args=(lambda: self.runtime.decide_approval(
                self.item['id'], view['approvals'][0]['id'], 'allow', 'owner', view['revision'], 'fixture handoff'),))
            deciding.start()
            closing = None
            try:
                self.assertTrue(handoff.wait(3))
                closing = threading.Thread(target=capture, args=(self.runtime.close,))
                closing.start()
                closing.join(.2)
            finally:
                release.set()
                deciding.join(5)
                if closing:
                    closing.join(5)
        self.assertFalse(deciding.is_alive())
        self.assertFalse(closing.is_alive())
        self.assertEqual([], errors)
        closed = self.view()
        self.assertEqual('revoked', closed['approvals'][0]['status'])
        self.assertEqual((False, 'runtime_closed'), self.runtime.authorize_tool(
            {'initiative_id': self.item['id'], 'task': {'id': 'TASK-CONTEXT'}},
            spec, {'command': ['git', 'status']}, 'handoff', 'owner'))

    def test_subtask_worker_count_is_strict_and_defaults_to_two(self):
        for value in (1, 5, True, 2.0, '4', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError,'2至4'):
                self.prepare(max_workers=value)
        prepared=self.prepare()
        self.assertEqual(2,prepared['max_workers'])
        self.assertEqual(2,prepared['budget']['max_workers'])
        with self.runtime.lock:
            queued=self.runtime._load(self.item['id']);queued['state']='queued';self.runtime._save(queued)
        with self.assertRaisesRegex(ValueError,'不能重新准备'):
            self.prepare(max_workers=4)
        self.assertEqual(2,self.view()['budget']['max_workers'])

    def test_four_frozen_workers_really_overlap_in_independent_cli_candidates(self):
        for name in ('c.txt','d.txt'):(self.repository/name).write_text('1')
        (self.repository/'delivery_contract_eval.py').write_text("import json;from pathlib import Path\n"
            "ok=all(Path(n).read_text() in ('1','2') for n in ('a.txt','b.txt','c.txt','d.txt'))\n"
            "print(json.dumps({'summary':{'decision':'pass' if ok else 'block','total':1,'passed':int(ok),'blocking_failed':int(not ok)},'results':[{'name':'four-values','level':'blocking','passed':ok}]}))\n"
            "raise SystemExit(0 if ok else 1)")
        names=['a.txt','b.txt','c.txt','d.txt']
        self.plan=prepare_daily(self.repository,self.runtime_dir,'owner','update four independent files','four values are 2',names,project=self.project)
        self.plan.update(plan_id='four-worker-fixture',expires_at=time.time()+900)
        self.runtime.freeze_profile(self.item['id'],self.plan)
        work=self.workflow._load(self.item['id']);work.update(plan=self.plan,origin_manifest=self.plan['source_manifest']);self.workflow._save(work)
        prepared=self.runtime.prepare_subtasks(self.item['id'],'owner',self.view()['revision'],
            [{'name':name,'prompt':'update '+name,'write_set':[name]} for name in names],max_workers=4)
        self.assertEqual(4,prepared['max_workers']);self.assertEqual(4,prepared['budget']['max_workers'])
        with LocalWorker.ready_lock:LocalWorker.peak=0;LocalWorker.active=0;LocalWorker.ready.clear()
        # Parent-owned synchronization lives outside every candidate/source;
        # real CLI processes stay alive until all four usage callbacks arrive.
        completion_gate = self.root / 'four-worker-completion-gate'
        # This stdlib-only fake CLI models a single executable. Project Eval
        # and all other LocalWorker fixtures keep their original venv command.
        with patch.object(LocalWorker,'completion_gate',completion_gate), \
                patch.object(LocalWorker,'cli_executable',sys._base_executable):
            self.runtime.start_subtasks(self.item['id'],'owner',prepared['revision'],'four-key',prepared['subtask_plan_id'])
            result=self.wait()
        self.assertEqual('review',result['state'],result.get('error'))
        self.assertTrue(completion_gate.is_file())
        self.assertEqual(4,LocalWorker.peak)
        self.assertEqual(4,result['budget']['max_workers'])
        self.assertEqual(80,result['budget']['tokens_used'])
        for name in names:
            self.assertEqual('1',(self.repository/name).read_text())
            self.assertEqual('2',(Path(result['workspace'])/name).read_text())

    def test_explicit_resume_preserves_frozen_worker_count_and_measured_budget(self):
        with self.runtime.lock:
            data=self.runtime._load(self.item['id']);data['max_workers']=4;self.runtime._save(data)
        self.runtime._begin(self.item['id'],self.plan)
        with self.runtime.lock:
            data=self.runtime._load(self.item['id']);data.update(state='paused',workspace=str(self.repository))
            data['budget'].update(tokens_used=123,elapsed_seconds=10);self.runtime._save(data)
        current=self.view()
        with patch.object(self.workflow,'execute'):
            self.runtime.control(self.item['id'],'resume','owner',current['revision'],current['candidate_sha256'])
        plan=self.workflow._load(self.item['id'])['plan']
        self.runtime._begin(self.item['id'],plan)
        budget=self.view()['budget']
        self.assertEqual(4,budget['max_workers']);self.assertEqual(123,budget['tokens_used'])
        self.assertGreaterEqual(budget['elapsed_seconds'],10)

    def relocated_candidate(self):
        from workbench.reference_paths import load_reference_mappings, unload_reference_mappings
        old=self.root/'old-candidate';target=self.root/'migrated-candidate'
        self.assertTrue(old.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(target.resolve().is_relative_to(self.root.resolve()))
        shutil.copytree(self.repository,old)
        self.runtime._begin(self.item['id'],self.plan)
        with self.runtime.lock:
            data=self.runtime._load(self.item['id'])
            data.update(state='paused',workspace=str(old),task_id='MIGRATED-PARENT',
                        subtasks=[{'task_id':'MIGRATED-CHILD','workspace':str(old),'status':'planned'}])
            self.runtime._save(data)
        work=self.workflow._load(self.item['id']);work['workspace']=str(old);self.workflow._save(work)
        expected=self.view()['candidate_sha256']
        shutil.move(str(old),str(target))
        folder=self.runtime_dir/'migration';folder.mkdir()
        (folder/'reference-map.json').write_text(json.dumps({'schema':'workbench.reference-map/v1',
            'runtime_root':str(self.runtime_dir.resolve()),'mappings':{str(old):str(target)}}))
        load_reference_mappings(self.runtime_dir)
        self.addCleanup(unload_reference_mappings,self.runtime_dir)
        return old,target,expected

    def test_migrated_candidate_view_and_tool_context_resolve_without_rewriting_history(self):
        old,target,expected=self.relocated_candidate()
        with self.tasks.connect() as db:
            raw=db.execute('SELECT payload FROM delivery_runtime WHERE initiative_id=?',(self.item['id'],)).fetchone()[0]
        self.assertFalse(old.exists())
        self.assertEqual(expected,self.view()['candidate_sha256'])
        self.assertEqual(str(target),self.runtime.task_context('MIGRATED-PARENT')['workspace'])
        self.assertEqual(str(target),self.runtime.task_context('MIGRATED-CHILD')['workspace'])
        with self.tasks.connect() as db:
            self.assertEqual(raw,db.execute('SELECT payload FROM delivery_runtime WHERE initiative_id=?',(self.item['id'],)).fetchone()[0])
        self.assertEqual(str(old),self.workflow._load(self.item['id'])['workspace'])

    def test_migrated_candidate_resume_prepares_new_authorization_from_effective_path(self):
        old,target,expected=self.relocated_candidate()
        view=self.view()
        with patch.object(self.workflow,'execute') as execute:
            self.runtime.control(self.item['id'],'resume','owner',view['revision'],expected)
            execute.assert_called_once()
        work=self.workflow._load(self.item['id'])
        self.assertEqual(str(target),work['workspace'])
        self.assertEqual(manifest(target,self.runtime_dir),work['plan']['source_manifest'])
        self.assertEqual(str(old),self.runtime._load(self.item['id'])['workspace'])
        self.assertIn(expected,work['plan']['spec_text'])

    def test_migrated_candidate_is_actual_source_for_independent_workers(self):
        old,target,expected=self.relocated_candidate()
        self.start();result=self.wait()
        self.assertEqual('review',result['state'],result.get('error'))
        self.assertFalse(old.exists())
        self.assertEqual('1',(target/'a.txt').read_text())
        self.assertEqual('2',(Path(result['workspace'])/'a.txt').read_text())

    def test_read_only_worker_uses_invocation_local_headless_options(self):
        prepared=self.prepare({'name':'inspect','prompt':'inspect project output','write_set':[]})
        commands=[];original=LocalWorker._run_codex_streaming
        def captured(runner,command,*args):
            commands.append(command);return original(runner,command,*args)
        with patch('workbench.codex_options.headless_options',return_value=['--ignore-user-config']), patch.object(LocalWorker,'_run_codex_streaming',captured):
            self.runtime.start_subtasks(self.item['id'],'owner',prepared['revision'],'readonly-options',prepared['subtask_plan_id'])
            result=self.wait()
        self.assertEqual('review',result['state'],result.get('error'))
        readonly=next(command for command in commands if 'read-only' in command)
        self.assertIn('--ignore-user-config',readonly)

    def test_subtask_failure_retains_outputs_and_prevents_acceptance(self):
        (self.repository / 'delivery_contract_eval.py').write_text("import json\nprint(json.dumps({'summary':{'decision':'block','total':1,'passed':0,'blocking_failed':1},'results':[{'name':'bad','level':'blocking','passed':False}]}))\nraise SystemExit(1)")
        self.plan['source_manifest'] = manifest(self.repository, self.runtime_dir)
        work = self.workflow._load(self.item['id']); work['plan'] = self.plan; self.workflow._save(work)
        self.start()
        result = self.wait()
        self.assertEqual('failed', result['state'])
        self.assertTrue(any(c['status']=='failed' for c in result['subtasks']))
        self.assertEqual('1', (self.repository/'a.txt').read_text())
        self.assertNotEqual('review', self.tasks.get(result['task_id'])['status'])

    def test_pause_ends_owned_process_and_explicit_resume_checks_hash(self):
        view = self.prepare()
        with LocalWorker.ready_lock:LocalWorker.ready.clear()
        with patch.object(LocalWorker,'completion_gate',self.root/'pause-process-gate'):
            self.runtime.start_subtasks(self.item['id'], 'owner', view['revision'], 'pause-key', view['subtask_plan_id'])
            wait_for_worker_usage(self,{'a.txt','b.txt'})
            self.assertEqual({'a.txt','b.txt'},LocalWorker.ready)
            current = self.view()
            self.runtime.control(self.item['id'], 'pause', 'owner', current['revision'],
                                 run_id=current['run_id'], session_id=current['session_id'],
                                 control_revision=current['control_revision'])
            result = self.wait()
        self.assertEqual('paused', result['state'], result['error'])
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.runtime.control(self.item['id'], 'resume', 'owner', result['revision'], 'wrong')
        self.runtime.control(self.item['id'], 'resume', 'owner', result['revision'], result['candidate_sha256'])
        resumed=self.wait()
        self.assertEqual('review', resumed['state'], resumed.get('error'))

    def test_resume_refuses_unmeasured_interrupted_call_without_resetting_budget(self):
        self.runtime._begin(self.item['id'],self.plan)
        with self.runtime.lock:
            data=self.runtime._load(self.item['id'])
            data.update(state='paused',workspace=str(self.repository))
            data['budget']['usage_unavailable']=True
            self.runtime._save(data)
        view=self.view()
        self.assertFalse(view['can_resume'])
        with self.assertRaisesRegex(ValueError,'可核验Token'):
            self.runtime.control(self.item['id'],'resume','owner',view['revision'],view['candidate_sha256'])
        self.assertTrue(self.view()['budget']['usage_unavailable'])
        self.assertFalse(self.workflow.workers)

    def test_required_app_uses_same_delivery_owner_and_explicit_recovery_never_replays(self):
        from workbench.workbench_server import WorkbenchApp
        runtime=self.root/'required-app-runtime'
        with patch.object(CodexExecutionRunner,'_run_codex_streaming') as process:
            app=WorkbenchApp(runtime,enable_advanced_runtime=False)
            self.assertIs(app.initiative_workflow.submitter.__self__,app.delivery_runtime)
            self.assertIsNone(app.harness_api)
            item=app.initiatives.create({'title':'interrupted required item','raw_signal':'fixture','source':'fixture',
                                        'project_id':app.default_project,'success_metric':'fixture'},'owner')
            data=app.delivery_runtime._load(item['id'])
            data.update(state='running',run_id='interrupted-required-run')
            app.delivery_runtime._save(data)
            # Constructor has no execution/recovery effects; only the lease owner recovers.
            restored=WorkbenchApp(runtime,enable_advanced_runtime=False)
            self.assertEqual('running',restored.delivery_runtime.view(item['id'])['state'])
            self.assertEqual([item['id']],restored.delivery_runtime.recover())
            self.assertEqual('interrupted',restored.delivery_runtime.view(item['id'])['state'])
            self.assertFalse(restored.delivery_runtime.workers)
            process.assert_not_called()
            for host in (restored,app):
                host.delivery_runtime.close();host.deployments.close();host.previews.close();host.initiative_workflow.learning_generation.close()

    def test_approval_is_candidate_bound_and_consumed_once(self):
        self.runtime._begin(self.item['id'], self.plan)
        with self.runtime.lock:
            data = self.runtime._load(self.item['id']);data.update(workspace=str(self.repository),task_id='TASK-CONTEXT');self.runtime._save(data)
        spec = ToolSpec('shell.exec','shell','test',frozenset(),lambda c,a:{}, approval='ask')
        context = {'initiative_id':self.item['id'], 'task':{'id':'TASK-CONTEXT'}}
        allowed, _ = self.runtime.authorize_tool(context,spec,{'command':['git','status']},'call-1','owner')
        self.assertFalse(allowed)
        view=self.view(); approval=view['approvals'][0]
        self.runtime.decide_approval(self.item['id'],approval['id'],'allow','owner',view['revision'],'read current status')
        self.assertTrue(self.runtime.authorize_tool(context,spec,{'command':['git','status']},'call-1','owner')[0])
        self.assertFalse(self.runtime.authorize_tool(context,spec,{'command':['git','status']},'call-1','owner')[0])

    def test_approval_rejects_candidate_drift(self):
        self.runtime._begin(self.item['id'], self.plan)
        with self.runtime.lock:
            data=self.runtime._load(self.item['id']);data.update(workspace=str(self.repository),task_id='TASK-CONTEXT');self.runtime._save(data)
        spec=ToolSpec('shell.exec','shell','test',frozenset(),lambda c,a:{},approval='ask')
        self.runtime.authorize_tool({'initiative_id':self.item['id'],'task':{'id':'TASK-CONTEXT'}},spec,{},'call','owner')
        view=self.view();(self.repository/'a.txt').write_text('2')
        with self.assertRaisesRegex(ValueError,'候选已变化'):
            self.runtime.decide_approval(self.item['id'],view['approvals'][0]['id'],'allow','owner',view['revision'],'inspect')

    def test_recovery_only_marks_interrupted_without_launching_workers(self):
        self.runtime._begin(self.item['id'], self.plan)
        restored=DeliveryRuntime(self.repository,self.runtime_dir,self.tasks,self.projects,self.items,self.workflow,runner_factory=LocalWorker)
        self.assertFalse(restored.workers)
        self.assertEqual([self.item['id']],restored.recover())
        self.assertEqual('interrupted',restored.view(self.item['id'])['state'])
        self.assertFalse(restored.workers)

    def test_recovery_accounts_utc_downtime_and_blocks_unfinished_calls_until_new_plan(self):
        self.runtime._begin(self.item['id'], self.plan)
        with self.runtime.lock:
            data = self.runtime._load(self.item['id'])
            data.update(workspace=str(self.repository), started_at=1800000000)
            data['budget'].update(elapsed_seconds=7, tokens_used=17)
            self.runtime._save(data)
            self.runtime._event(data, 'tool/call', 'agent:coder', {'tool_id': 'codex.exec', 'task_id': 'TASK-A', 'call_id': 'finished'})
            self.runtime._event(data, 'tool/result', 'agent:coder', {'tool_id': 'codex.exec', 'task_id': 'TASK-A', 'call_id': 'finished', 'usage': {'total_tokens':17}})
            self.runtime._event(data, 'tool/call', 'agent:coder', {'tool_id': 'codex.exec', 'task_id': 'TASK-A', 'call_id': 'unfinished'})
            self.runtime._event(data, 'tool/call', 'agent:coder', {'tool_id': 'codex.exec', 'task_id': 'TASK-B'})
            self.runtime._event(data, 'tool/result', 'agent:coder', {'tool_id': 'codex.exec', 'task_id': 'TASK-B'})
        restored = DeliveryRuntime(self.repository, self.runtime_dir, self.tasks, self.projects,
                                   self.items, self.workflow, runner_factory=LocalWorker)
        self.addCleanup(restored.close)
        with patch('workbench.delivery_runtime.time.time', return_value=1800000100.25):
            self.assertEqual([self.item['id']], restored.recover())
        view = restored.view(self.item['id'])
        self.assertEqual(101, view['budget']['elapsed_seconds'])
        self.assertEqual(17, view['budget']['tokens_used'])
        self.assertTrue(view['budget']['usage_unavailable'])
        self.assertFalse(view['can_resume'])
        accounting = view['recovery_accounting']
        self.assertEqual(1800000000, accounting['recorded_start_utc_epoch_seconds'])
        self.assertEqual(1800000100.25, accounting['observed_recovery_utc_epoch_seconds'])
        self.assertEqual(['unfinished'], [c['call_id'] for c in accounting['pending_calls']])
        with self.assertRaisesRegex(ValueError, '可核验Token'):
            restored.control(self.item['id'], 'resume', 'owner', view['revision'], view['candidate_sha256'])
        with self.assertRaisesRegex(ValueError, '新的执行方案'):
            restored._begin(self.item['id'], self.plan)
        with self.assertRaisesRegex(ValueError, '新的执行方案'):
            restored.prepare_subtasks(self.item['id'], 'owner', view['revision'], [
                {'name':'a','prompt':'update-a','write_set':['a.txt']},
                {'name':'b','prompt':'update-b','write_set':['b.txt']}])
        self.assertFalse(restored.workers)
        fresh = dict(self.plan, plan_id='new-explicitly-confirmed-plan')
        begun = restored._begin(self.item['id'], fresh)
        self.assertEqual(0, begun['budget']['tokens_used'])
        self.assertNotIn('usage_unavailable', begun['budget'])
        self.assertNotIn('recovery_accounting', begun)

    def test_recovery_of_known_calls_preserves_time_and_resume_budget(self):
        self.runtime._begin(self.item['id'], self.plan)
        with self.runtime.lock:
            data = self.runtime._load(self.item['id'])
            data.update(workspace=str(self.repository), started_at=1800000000)
            data['budget'].update(elapsed_seconds=23, tokens_used=123, max_workers=4)
            self.runtime._save(data)
            self.runtime._event(data, 'tool/call', 'agent:coder', {'tool_id':'codex.exec','task_id':'TASK-KNOWN'})
            self.runtime._event(data, 'tool/result', 'agent:coder', {'tool_id':'codex.exec','task_id':'TASK-KNOWN'})
        with patch('workbench.delivery_runtime.time.time', return_value=1800000022.5):
            self.runtime.recover()
        view = self.view()
        self.assertEqual(23, view['budget']['elapsed_seconds'])
        self.assertTrue(view['can_resume'])
        with patch.object(self.workflow, 'execute') as execute:
            self.runtime.control(self.item['id'], 'resume', 'owner', view['revision'], view['candidate_sha256'])
            execute.assert_called_once()
        plan = self.workflow._load(self.item['id'])['plan']
        begun = self.runtime._begin(self.item['id'], plan)
        self.assertEqual(23, begun['budget']['elapsed_seconds'])
        self.assertEqual(123, begun['budget']['tokens_used'])
        self.assertEqual(4, begun['budget']['max_workers'])

    def test_recovery_with_backward_utc_clock_exhausts_unverifiable_time(self):
        self.runtime._begin(self.item['id'], self.plan)
        with self.runtime.lock:
            data = self.runtime._load(self.item['id'])
            data['started_at'] = 1800000000
            self.runtime._save(data)
        with patch('workbench.delivery_runtime.time.time', return_value=1799999990):
            self.runtime.recover()
        view = self.view()
        self.assertEqual(view['budget']['time_budget_seconds'], view['budget']['elapsed_seconds'])
        self.assertTrue(view['recovery_accounting']['time_accounting_unavailable'])
        self.assertFalse(view['can_resume'])

    def _assert_shutdown_with_unavailable_database(self, corrupt=False):
        database = Path(self.tasks.path)
        retained = database.with_name('workbench-retained.db')
        self.assertTrue(database.resolve().is_relative_to(self.root))
        self.assertTrue(retained.resolve().is_relative_to(self.root))
        self.runtime._begin(self.item['id'], self.plan)
        with self.runtime.lock:
            data = self.runtime._load(self.item['id'])
            data.update(workspace=str(self.repository), task_id='TASK-SHUTDOWN')
            self.runtime._save(data)
        event, ready = self.runtime.events[self.item['id']], threading.Event()
        result = []
        runner = CodexExecutionRunner(self.repository, self.runtime_dir)
        runner.control_event = event
        def executor(*args):
            process = runner._run_codex_streaming(
                [sys.executable, '-u', '-c', "import time;print('ready',flush=True);time.sleep(30)"],
                '', 30, lambda line: ready.set(), time.monotonic())
            result.append(process)
            return {'success': process.returncode == 0, 'returncode': process.returncode}
        self.runtime.tool_executor = executor
        spec = ToolSpec('fixture.process', 'shell', 'fixture', frozenset(), lambda c, a: {}, approval='ask')
        self.runtime.authorize_tool({'initiative_id':self.item['id'], 'task':{'id':'TASK-SHUTDOWN'}},
                                    spec, {}, 'shutdown-call', 'owner')
        view = self.view()
        self.runtime.decide_approval(self.item['id'], view['approvals'][0]['id'], 'allow',
                                    'owner', view['revision'], 'isolated shutdown fault fixture')
        thread = next(iter(self.runtime.tool_workers))
        try:
            self.assertTrue(ready.wait(3))
            database.replace(retained)
            if corrupt:
                database.write_bytes(b'not a SQLite database')
            with self.assertLogs('workbench.delivery_runtime', level='WARNING'):
                stopped = self.runtime.close()
            self.assertEqual(0, stopped['workers_still_stopping'])
            self.assertTrue(stopped['errors'])
            self.assertTrue(self.runtime.closed)
            self.assertEqual(130, result[0].returncode)
            if corrupt:
                self.assertEqual(b'not a SQLite database', database.read_bytes())
            else:
                self.assertFalse(database.exists(), 'shutdown recreated a missing database')
            self.assertTrue(retained.is_file())
        finally:
            event.set();thread.join(5)
            self.runtime.tool_workers.discard(thread)
            if retained.exists():
                if database.exists():
                    database.unlink()
                retained.replace(database)

    def test_shutdown_stops_real_process_when_database_is_missing_without_recreating_it(self):
        self._assert_shutdown_with_unavailable_database()

    def test_shutdown_stops_real_process_when_database_is_corrupt_without_overwriting_it(self):
        self._assert_shutdown_with_unavailable_database(corrupt=True)

    def test_shared_harness_uses_existing_ids_and_constructor_does_not_recover(self):
        with patch('workbench.automation.DeliveryAutomation.recover') as recover:
            api=HarnessPlatformAPI(self.runtime_dir,self.repository,tasks=self.tasks,projects=self.projects,
                                   initiatives=self.items,runtime_store=self.runtime.sessions,delivery_runtime=self.runtime)
            recover.assert_not_called()
        self.assertIs(api.tasks,self.tasks)
        self.assertFalse((self.runtime_dir/'platform.db').exists())
        api.shutdown()

    def test_old_report_cannot_green_a_new_project_eval_invocation(self):
        task={'id':'TASK-REPORT','execution_mode':'verify','business_refs':['PROJECT:'+self.project['id']]}
        old=self.runtime_dir/'project-reports'/('TASK-REPORT.json');old.parent.mkdir();old.write_text(json.dumps({'summary':{'decision':'pass'}}))
        self.projects.configure(self.project['id'],[sys.executable,'-c',"print('{}')"])
        with self.assertRaisesRegex(RuntimeError,'pass/block'):
            ProjectEvalRunner(self.projects,self.runtime_dir).for_task(task)()

    def test_legacy_project_execution_creates_candidate_and_never_writes_source(self):
        task={'id':'TASK-CANDIDATE','execution_mode':'codex','write_scope':['a.txt'],
              'execution_timeout_seconds':30,'business_refs':['PROJECT:'+self.project['id']]}
        with patch('workbench.project_runner.CodexExecutionRunner',LocalWorker):
            result=ProjectExecutionRunner(self.projects,self.runtime_dir)(task)
        self.assertTrue(result['success'])
        self.assertEqual('1',(self.repository/'a.txt').read_text())
        receipt=json.loads((self.runtime_dir/'candidates/TASK-CANDIDATE/project.json').read_text())
        self.assertEqual('2',(Path(receipt['workspace'])/'a.txt').read_text())
        report=ProjectEvalRunner(self.projects,self.runtime_dir).for_task(task)()
        self.assertEqual(receipt['workspace'],report['runner']['workspace'])

    def test_profile_configuration_is_frozen_in_the_technical_plan(self):
        self.runtime.freeze_profile(self.item['id'],self.plan)
        self.assertIn('已确认运行 Profile',self.plan['spec_text'])
        self.runtime.sessions.set_plugin_enabled('shell.local',False)
        with self.assertRaisesRegex(ValueError,'Profile'):
            self.runtime._begin(self.item['id'],self.plan)

    def test_total_budget_prevents_additional_worker_calls(self):
        work=self.workflow._load(self.item['id'])
        work['repair_loop_config']={'enabled':True,'max_rounds':3,'token_budget':1,'time_budget_seconds':900}
        self.workflow._save(work)
        self.runtime._begin(self.item['id'],self.plan)
        with self.runtime.lock:
            data=self.runtime._load(self.item['id']);data['budget']['tokens_used']=1;self.runtime._save(data)
        with self.assertRaisesRegex(RuntimeError,'Token'):
            self.runtime._budget(self.item['id'])

    def test_approval_from_shared_api_executes_exact_candidate_shell_once(self):
        self.start();result=self.wait()
        api=HarnessPlatformAPI(self.runtime_dir,self.repository,tasks=self.tasks,projects=self.projects,
                               initiatives=self.items,runtime_store=self.runtime.sessions,delivery_runtime=self.runtime)
        self.addCleanup(api.shutdown)
        path='/api/v1/sessions/'+result['session_id']+'/tools/shell.exec'
        args={'task_id':result['task_id'],'call_id':'status-call','expected_revision':result['revision'],
              'args':{'command':['git','status','--porcelain']}}
        first=api.dispatch('POST',path,{'x-workbench-actor':'owner','idempotency-key':'request-shell'},args)
        self.assertEqual(403,first.status)
        view=self.view();pending=next(a for a in view['approvals'] if a['status']=='pending')
        self.runtime.decide_approval(self.item['id'],pending['id'],'allow','owner',view['revision'],'inspect this candidate')
        deadline=time.time()+5
        while not self.view()['approvals'][0].get('result') and time.time()<deadline:time.sleep(.01)
        approval=self.view()['approvals'][0]
        self.assertEqual('consumed',approval['status'])
        self.assertEqual(0,approval['result']['returncode'])
        args['expected_revision']=self.view()['revision']
        second=api.dispatch('POST',path,{'x-workbench-actor':'owner','idempotency-key':'repeat-shell'},args)
        self.assertEqual(403,second.status)

    def test_service_close_terminates_actual_owned_cli_workers(self):
        with patch.object(LocalWorker,'delay',10):
            self.start()
            deadline=time.time()+5
            while not any(c['status']=='running' for c in self.view()['subtasks']) and time.time()<deadline:time.sleep(.01)
            started=time.monotonic()
            receipt=self.runtime.close()
        self.assertLess(time.monotonic()-started,5)
        self.assertEqual(0,receipt['workers_still_stopping'])
        self.assertEqual('cancelled',self.wait()['state'])
        self.assertEqual('1',(self.repository/'a.txt').read_text())

    def test_close_signal_finishes_worker_before_cancel_intent_is_persisted(self):
        with patch.object(LocalWorker,'delay',10):
            self.start()
            deadline=time.monotonic()+5
            while not any(c['status']=='running' for c in self.view()['subtasks']) and time.monotonic()<deadline:
                time.sleep(.01)
            event=self.runtime.events[self.item['id']]
            original=event.set
            worker=self.runtime.workers[self.item['id']]
            closer=threading.current_thread()
            def finish_on_signal():
                original()
                # close() signals before it takes state locks. Make that actual
                # owned worker finish in this window instead of relying on timing.
                if threading.current_thread() is closer:
                    worker.join(3)
                    self.assertFalse(worker.is_alive(),'fixture did not reach the pre-persistence shutdown window')
            with patch.object(event,'set',side_effect=finish_on_signal):
                receipt=self.runtime.close()
        self.assertEqual(0,receipt['workers_still_stopping'])
        result=self.wait()
        self.assertEqual('cancelled',result['state'],result)
        self.assertEqual('cancel',result['requested_control'])
        self.assertEqual('1',(self.repository/'a.txt').read_text())

    @contextmanager
    def parent_eval_fixture(self, phase):
        """Keep a real parent Eval alive; retain and reclaim only this fixture's jobs."""
        marker = self.root / 'parent-eval-ready.json'
        check = (
            "import json,time;from pathlib import Path\n"
            "values=[Path(n).read_text() for n in ('a.txt','b.txt')]\n"
            "parent='subtasks' not in Path.cwd().parts\n"
            f"waiting=parent and values=={['1','1'] if phase == 'before' else ['2','2']!r}\n"
            "if waiting:\n"
            f"    Path({str(marker)!r}).write_text(json.dumps({{'phase':{phase!r},'workspace':str(Path.cwd())}}))\n"
            "    print('parent Eval ready',flush=True)\n"
            "    time.sleep(30)\n"
            "ok=all(v in ('1','2') for v in values)\n"
            "print(json.dumps({'summary':{'decision':'pass' if ok else 'block','total':1,'passed':int(ok),'blocking_failed':int(not ok)},'results':[{'name':'value','level':'blocking','passed':ok}]}))\n"
            "raise SystemExit(0 if ok else 1)\n"
        )
        (self.repository / 'delivery_contract_eval.py').write_text(check)
        self.plan = prepare_daily(self.repository, self.runtime_dir, 'owner', 'update-a and update-b',
                                  'values are 2', ['a.txt', 'b.txt'], project=self.project)
        self.plan.update(plan_id='plan-fixture', expires_at=time.time()+900)
        self.runtime.freeze_profile(self.item['id'], self.plan)
        work = self.workflow._load(self.item['id'])
        work.update(stage='confirmed', plan=self.plan, origin_manifest=self.plan['source_manifest'])
        self.workflow._save(work)
        from workbench import execution
        from workbench.project_delivery import CandidateProjectEval
        original_spawn = execution.spawn_owned_process
        original_eval = CandidateProjectEval.__call__
        target_phase_started = threading.Event()
        target_label = 'subtasks-before' if phase == 'before' else 'subtasks-final'
        parent_workspace = (self.runtime_dir / 'daily-delivery/plan-fixture/workspace').resolve()
        processes = []
        def spawn(command, cwd, **kwargs):
            process, owner, prefix = original_spawn(command, cwd, **kwargs)
            processes.append((process, owner))
            return process, owner, prefix
        def evaluate(evaluator, *args, **kwargs):
            if evaluator.label == target_label and evaluator.workspace.resolve() == parent_workspace:
                target_phase_started.set()
            return original_eval(evaluator, *args, **kwargs)
        try:
            with patch('workbench.execution.spawn_owned_process', side_effect=spawn), \
                    patch.object(self.runtime, 'runner_factory', self.parent_fixture_child_runner), \
                    patch.object(CandidateProjectEval, '__call__', evaluate):
                self.start()
                # Measure readiness of the selected parent Eval, separately
                # from the unchanged 30-second delivery completion tests.
                self.assertTrue(target_phase_started.wait(30), 'target parent Eval phase did not start within 30 seconds')
                deadline = time.monotonic()+10
                while not marker.is_file() and time.monotonic() < deadline:
                    time.sleep(.01)
                if not marker.is_file():
                    import faulthandler
                    for workspace in (self.runtime_dir / 'daily-delivery/plan-fixture').rglob('workspace'):
                        print('Parent Eval fixture timeout:', workspace,
                              {name: (workspace / name).read_text() for name in ('a.txt', 'b.txt')
                               if (workspace / name).is_file()}, flush=True)
                    faulthandler.dump_traceback()
                self.assertTrue(marker.is_file(), 'real parent Eval did not reach readiness within 10 seconds')
                ready = json.loads(marker.read_text())
                self.assertEqual(phase, ready['phase'])
                self.assertEqual(self.runtime_dir / 'daily-delivery/plan-fixture/workspace', Path(ready['workspace']))
                self.assertTrue(any(process.poll() is None for process, _ in processes))
                yield processes
        finally:
            # Red tests must not leave a 30-second Eval holding the temporary tree.
            event = self.runtime.events.get(self.item['id'])
            if event:
                event.set()
            for process, owner in processes:
                if process.poll() is None:
                    if owner:
                        owner.close()
                    else:
                        process.kill()
                    process.wait(timeout=5)
            worker = self.runtime.workers.get(self.item['id'])
            if worker:
                worker.join(5)
                self.assertFalse(worker.is_alive(), 'fixture owner still alive after controlled cleanup')

    @staticmethod
    def parent_fixture_child_runner(workspace, runtime):
        """Controlled synthetic child execution; parent/child Eval remain real processes."""
        def execute(task, **kwargs):
            changed = list(task['write_scope'])
            for path in changed:
                (Path(workspace) / path).write_text('2')
            return {'success': True, 'mode': 'codex_exec', 'changed_files': changed,
                    'usage': {'total_tokens': 20}, 'fixture': 'controlled child; no model or CLI execution'}
        return execute

    def assert_parent_eval_stopped(self, processes, state):
        worker = self.runtime.workers[self.item['id']]
        worker.join(5)
        self.assertFalse(worker.is_alive(), 'control did not stop the actual parent Eval within 5 seconds')
        result = self.view()
        self.assertEqual(state, result['state'], result.get('error'))
        self.assertEqual('failed', self.tasks.get(result['task_id'])['status'])
        for process, owner in processes:
            self.assertIsNotNone(process.poll())
            if owner:
                self.assertIsNone(owner.handle)
        receipts = list((self.runtime_dir / 'project-reports' / result['task_id']).glob('*/process.json'))
        self.assertTrue(receipts)
        recorded = [json.loads(path.read_text()) for path in receipts]
        cancelled = [receipt for receipt in recorded if receipt['returncode'] == 130]
        self.assertTrue(cancelled, 'no cancelled parent Eval process receipt was retained')
        self.assertTrue(all(Path(receipt['cwd']) == self.runtime_dir / 'daily-delivery/plan-fixture/workspace'
                            for receipt in cancelled))
        self.assertEqual('1', (self.repository / 'a.txt').read_text())
        self.assertEqual('1', (self.repository / 'b.txt').read_text())
        self.assertFalse(self.runtime.busy())
        return result

    def test_close_during_parent_before_eval_stops_real_process(self):
        with self.parent_eval_fixture('before') as processes:
            started = time.monotonic()
            receipt = self.runtime.close()
            self.assertLess(time.monotonic()-started,5)
            self.assertEqual(0, receipt['workers_still_stopping'])
            self.assert_parent_eval_stopped(processes, 'cancelled')
            self.assertFalse(any(c['task_id'] for c in self.view()['subtasks']))

    def test_cancel_during_parent_before_eval_stops_real_process(self):
        from workbench.execution_control import local
        previous = threading.Event()
        restored = []
        original = self.runtime._run_subtasks_owned
        def owned(*args):
            local.cancel_event = previous
            try:
                return original(*args)
            finally:
                restored.append(getattr(local, 'cancel_event', None) is previous)
                local.cancel_event = None
        with patch.object(self.runtime, '_run_subtasks_owned', side_effect=owned):
            with self.parent_eval_fixture('before') as processes:
                current = self.view()
                self.runtime.control(self.item['id'], 'cancel', 'owner', current['revision'])
                self.assert_parent_eval_stopped(processes, 'cancelled')
            self.assertEqual([True], restored)

    def test_pause_during_parent_final_eval_preserves_paused_state(self):
        with self.parent_eval_fixture('final') as processes:
            current = self.view()
            self.runtime.control(self.item['id'], 'pause', 'owner', current['revision'])
            result = self.assert_parent_eval_stopped(processes, 'paused')
            self.assertEqual('interrupted', self.workflow._load(self.item['id'])['stage'])
            self.assertTrue(result['candidate_sha256'])
            self.assertTrue(list((self.runtime_dir / 'daily-delivery/plan-fixture/subtasks').glob('*/changes.patch')))

    def test_time_budget_during_parent_final_eval_stops_real_process(self):
        with self.parent_eval_fixture('final') as processes:
            current = self.view()
            with self.runtime.lock:
                data = self.runtime._load(self.item['id'])
                data['started_at'] = time.time() - data['budget']['time_budget_seconds'] + .1
                self.runtime._save(data)
                self.runtime.timers[self.item['id']].cancel()
                timer = threading.Timer(.1, self.runtime._time_exhausted, args=(self.item['id'], current['run_id']))
                self.runtime.timers[self.item['id']] = timer
                timer.start()
            result = self.assert_parent_eval_stopped(processes, 'failed')
            self.assertTrue(result['budget_exhausted'])
            events = self.runtime.sessions.get_session(result['session_id'])['events']
            self.assertTrue(any(e['kind']=='runtime/budget-stopped' for e in events))

    def test_pause_after_parent_eval_return_cannot_publish_review(self):
        original_append = self.tasks.append_event
        controlled = []
        def append(task_id, detail, **kwargs):
            result = original_append(task_id, detail, **kwargs)
            if detail == '日常研发交付包已保存':
                current = self.view()
                controlled.append(self.runtime.control(self.item['id'], 'pause', 'owner', current['revision']))
            return result
        with patch.object(self.tasks, 'append_event', side_effect=append), \
                patch.object(self.runtime, 'runner_factory', self.parent_fixture_child_runner):
            self.start()
            result = self.wait()
        self.assertEqual(1, len(controlled))
        self.assertEqual('paused', result['state'], result.get('error'))
        self.assertEqual('interrupted', self.workflow._load(self.item['id'])['stage'])
        self.assertEqual('failed', self.tasks.get(result['task_id'])['status'])
        self.assertTrue((self.runtime_dir / 'daily-delivery/plan-fixture/changes.patch').is_file())
        self.assertEqual('1', (self.repository / 'a.txt').read_text())

    def assert_bound_worker_stop_response(self, action):
        from workbench.execution_control import local
        self.runtime._begin(self.item['id'], self.plan)
        with self.runtime.lock:
            current = self.runtime._load(self.item['id'])
            current['workspace'] = str(self.repository)
            self.runtime._save(current)
        event = self.runtime.events[self.item['id']]
        previous = getattr(local, 'cancel_event', None)
        local.cancel_event = event
        try:
            with patch('workbench.delivery_runtime.manifest', side_effect=AssertionError('stop response must not start Git')):
                response = self.runtime.control(self.item['id'], action, 'owner', current['revision'])
            self.assertIs(local.cancel_event, event)
            self.assertTrue(event.is_set())
            self.assertEqual(action, response['requested_control'])
            self.assertEqual('', response['candidate_sha256'])
            self.assertEqual('pending', response['candidate_verification'])
            self.assertFalse(response['can_resume'])
        finally:
            local.cancel_event = previous
        # Only a subsequent ordinary GET may supply a freshly checked hash.
        with self.runtime.lock:
            settled = self.runtime._load(self.item['id'])
            settled['state'] = 'paused' if action == 'pause' else 'cancelled'
            self.runtime._save(settled)
        with patch('workbench.delivery_runtime.manifest', wraps=manifest) as actual_manifest:
            refreshed = self.view()
        actual_manifest.assert_called_once_with(self.repository, self.runtime_dir, record_process=False)
        self.assertTrue(refreshed['candidate_sha256'])
        self.assertEqual('verified', refreshed['candidate_verification'])
        self.assertEqual(action == 'pause', refreshed['can_resume'])
        if action == 'pause':
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                self.runtime.control(self.item['id'], 'resume', 'owner', refreshed['revision'], 'wrong')
        self.assertTrue(event.is_set())

    def test_pause_response_from_bound_worker_does_not_start_git(self):
        self.assert_bound_worker_stop_response('pause')

    def test_cancel_response_from_bound_worker_does_not_start_git(self):
        self.assert_bound_worker_stop_response('cancel')

    def shared_api(self):
        api=HarnessPlatformAPI(self.runtime_dir,self.repository,tasks=self.tasks,projects=self.projects,
                               initiatives=self.items,runtime_store=self.runtime.sessions,delivery_runtime=self.runtime)
        self.addCleanup(api.shutdown)
        return api

    def test_shared_config_requires_version_and_body_identity_is_supported(self):
        api=self.shared_api()
        body={'actor':'owner','submission_key':'no-version','enabled':False}
        bad=api.dispatch('POST','/api/v1/plugins/shell.local/enabled',{},body)
        self.assertEqual(422,bad.status)
        configuration=api.dispatch('GET','/api/v1/plugins',{},{}).body['configuration_sha256']
        body.update(submission_key='disable',expected_configuration_sha256=configuration)
        changed=api.dispatch('POST','/api/v1/plugins/shell.local/enabled',{},body)
        self.assertEqual(200,changed.status,changed.body)
        self.assertFalse(changed.body['enabled'])
        stale=api.dispatch('POST','/api/v1/plugins/shell.local/enabled',{},body|{'submission_key':'stale','enabled':True})
        self.assertEqual(422,stale.status)

    def test_shared_advanced_writes_reserve_key_before_effect_and_do_not_replay_pending(self):
        api=self.shared_api(); entered=threading.Event(); release=threading.Event(); calls=[]
        def producer():
            calls.append('actual');entered.set();release.wait(5);return {'done':True}
        thread=threading.Thread(target=lambda:api._idempotent('fixture','same',{'actor':'owner'},producer))
        thread.start();self.assertTrue(entered.wait(5))
        from workbench.mutation_receipts import MutationPending
        with self.assertRaises(MutationPending):
            api._idempotent('fixture','same',{'actor':'owner'},producer)
        release.set();thread.join(5)
        self.assertEqual(['actual'],calls)
        self.assertEqual({'done':True},api._idempotent('fixture','same',{'actor':'owner'},producer))

    def test_shared_tools_reject_cross_session_and_direct_task_acceptance(self):
        self.start(); result=self.wait(); api=self.shared_api()
        other=api.runtime.create_session(self.project['id'],'other','owner',result['profile_id'],result['task_id'])
        body={'actor':'owner','submission_key':'wrong-session','task_id':result['task_id'],
              'expected_revision':result['revision'],'args':{'command':['git','status']}}
        rejected=api.dispatch('POST','/api/v1/sessions/'+other['id']+'/tools/shell.exec',{},body)
        self.assertEqual(422,rejected.status,rejected.body)
        self.assertFalse(self.view()['approvals'])
        review=api.dispatch('POST','/api/v1/tasks/'+result['task_id']+'/review',{},
                            {'actor':'another','submission_key':'accept-bypass','decision':'approve','note':'not assigned'})
        self.assertEqual(422,review.status)
        self.assertEqual('review',self.tasks.get(result['task_id'])['status'])

    def test_shared_session_pause_controls_the_actual_cli_run(self):
        api=self.shared_api()
        with LocalWorker.ready_lock:LocalWorker.ready.clear()
        with patch.object(LocalWorker,'completion_gate',self.root/'session-pause-process-gate'):
            self.start()
            wait_for_worker_usage(self,{'a.txt','b.txt'})
            self.assertEqual({'a.txt','b.txt'},LocalWorker.ready)
            current=self.view()
            with self.runtime.lock:
                progress=self.runtime._load(self.item['id'])
                progress['subtasks'][0]['progress']='explicit automated Session-route progress'
                self.runtime._save(progress)
            self.assertGreater(self.view()['revision'],current['revision'])
            path='/api/v1/sessions/'+current['session_id']+'/status'
            legacy=api.dispatch('POST',path,
                {'X-Workbench-Actor':'owner','Idempotency-Key':'shared-stale-legacy'},
                {'status':'paused','expected_revision':current['revision']})
            self.assertEqual(422,legacy.status,legacy.body)
            self.assertFalse(self.runtime.events[self.item['id']].is_set())
            self.assertGreater(LocalWorker.active,0)
            body={'status':'paused','expected_revision':current['revision'],
                  'run_id':current['run_id'],'session_id':current['session_id'],
                  'control_revision':current['control_revision']}
            wrong=api.dispatch('POST',path,
                {'X-Workbench-Actor':'owner','Idempotency-Key':'shared-wrong-attempt'},
                {**body,'run_id':'another-run'})
            self.assertEqual(422,wrong.status,wrong.body)
            self.assertFalse(self.runtime.events[self.item['id']].is_set())
            self.assertGreater(LocalWorker.active,0)
            response=api.dispatch('POST',path,
                {'X-Workbench-Actor':'owner','Idempotency-Key':'shared-pause'},
                body)
            self.assertEqual(200,response.status,response.body)
            result=self.wait()
            self.assertEqual('paused',result['state'])
            self.assertEqual(current['run_id'],result['run_id'])
            self.assertEqual(current['session_id'],result['session_id'])
            self.assertEqual(current['control_revision']+1,result['control_revision'])
            self.assertEqual(0,LocalWorker.active)

    def v2_trial(self, required_file='a.txt'):
        from eval.workbench_contracts import LocalDeliveryFixture
        self.start(); source=self.wait()
        self.workflow.accept(self.item['id'],'reviewer',self.workflow.get(self.item['id'])['revision'],'source fixture accepted')
        candidate=LocalDeliveryFixture.candidate(source['task_id'])
        candidate.update(applies=['two files'],excludes=[])
        candidate['recipe'].update(schema_version=2,preconditions=[{'kind':'file_exists','path':'a.txt'}],
            stage_checks={'precheck':[], 'implement':[], 'eval':[]}, required_files=[{'phase':'implement','path':required_file}],
            required_evidence=['precheck','implementation_diff','project_eval','human_review'],recovery='retain_candidate_new_plan')
        learning=self.workflow.learning
        asset=learning.create(self.item['id'],'author',candidate)
        asset=learning.govern(self.item['id'],asset['id'],'peer','approve','independent source evidence')
        self.item=self.items.create({'title':'two files trial', 'raw_signal':'two files trial', 'source':'fixture',
                                    'project_id':self.project['id'], 'success_metric':'both values are 2'},'owner')
        recall=learning.recall(self.item['id'],'two files',trials=True)
        decision=learning.decide(self.item['id'],recall['id'],[LocalDeliveryFixture.choice(asset)],'owner')
        self.plan=prepare_daily(self.repository,self.runtime_dir,'owner','update two files','both values are 2',['a.txt','b.txt'],project=self.project)
        self.plan.update(plan_id='v2-parallel-plan',expires_at=time.time()+900)
        self.runtime.freeze_profile(self.item['id'],self.plan)
        binding=learning.bind(self.item['id'],self.plan['plan_id'],decision,self.repository,'owner')
        self.plan['learning_binding_id']=binding['id']
        work=self.workflow._load(self.item['id'])
        work.update(stage='confirmed',plan=self.plan,reviewer='reviewer',origin_manifest=self.plan['source_manifest'],learning_decision=decision)
        self.workflow._save(work)
        self.start(); result=self.wait()
        return result, learning

    def test_parallel_delivery_satisfies_real_v2_learning_phase_evidence_and_named_acceptance(self):
        result,learning=self.v2_trial()
        self.assertEqual('review',result['state'],result.get('error'))
        history=learning.view(self.item['id'])['bindings'][0]['runs']
        phases={r['phase']:r['payload'] for r in history}
        for phase in ('precheck','implement','eval'):
            self.assertTrue(phases[phase]['passed'])
            self.assertIn('phase_contract',phases[phase])
        self.assertTrue(Path(phases['implement']['diff_artifact']['path']).is_file())
        accepted=self.workflow.accept(self.item['id'],'reviewer',self.workflow.get(self.item['id'])['revision'],'v2 phase evidence accepted')
        self.assertEqual('accepted',accepted['stage'])

    def test_parallel_v2_missing_output_stops_before_parent_eval_and_retains_diff(self):
        result,learning=self.v2_trial('missing-output.txt')
        self.assertEqual('failed',result['state'],result.get('error'))
        history=learning.view(self.item['id'])['bindings'][0]['runs']
        failure=next(r['payload'] for r in history if r['phase']=='implement')
        self.assertFalse(failure['passed'])
        self.assertEqual('implement',failure['stopped_phase'])
        self.assertTrue(Path(failure['diff_artifact']['path']).is_file())
        self.assertFalse(any(r['phase']=='eval' for r in history))
        self.assertEqual('failed',self.tasks.get(result['task_id'])['status'])

    def test_cancel_queued_plan_never_starts_any_cli_worker(self):
        admitted=threading.Event(); release=threading.Event()
        original=self.runtime._run_subtasks
        def held(item_id,actor):
            admitted.set();release.wait(5);original(item_id,actor)
        with patch.object(self.runtime,'_run_subtasks',held):
            self.start();self.assertTrue(admitted.wait(5))
            current=self.view()
            self.runtime.control(self.item['id'],'cancel','owner',current['revision'])
            release.set();result=self.wait()
        self.assertEqual('cancelled',result['state'])
        self.assertFalse((self.runtime_dir/'daily-delivery').exists())


if __name__=='__main__':
    unittest.main()
