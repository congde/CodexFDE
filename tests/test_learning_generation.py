"""Isolated generated drafts and v2 contracts; fixture names are not real review."""
import copy
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch

from eval.workbench_contracts import LocalDeliveryFixture
from workbench.learning import LearningStore, REQUIRED_EVIDENCE, RECOVERY, canonical, digest
from workbench.learning_generation import CodexLearningGenerator, LearningGenerationService, collect_input, validate_output
from workbench.execution import CodexExecutionRunner
from workbench.file_io import read_bytes
from workbench.task_store import TaskStore


def v2(recipe, **updates):
    return {**copy.deepcopy(recipe), 'schema_version': 2,
            'stage_checks': {'precheck': [], 'implement': [], 'eval': []},
            'required_files': [], 'required_evidence': list(REQUIRED_EVIDENCE),
            'recovery': RECOVERY, **updates}


@contextmanager
def windows_read_lock(path):
    """Hold a real Windows sharing lock; release only this fixture's handle."""
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                               ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    api.CreateFileW.restype = ctypes.c_void_p
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    handle = api.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    lock = threading.Lock()
    def release():
        nonlocal handle
        with lock:
            if handle is not None:
                if not api.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                handle = None
    try:
        yield release
    finally:
        release()


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.f = LocalDeliveryFixture()
        self.addCleanup(self.f.close)
        self.source, self.task_id = self.f.accepted_source()

    def output(self, packet):
        candidate = self.f.candidate(self.task_id, packet['kind'])
        result = {k: candidate[k] for k in ('title', 'content', 'applies', 'excludes', 'boundary', 'conflict_key')}
        result['recipe'] = v2(candidate['recipe']) if packet['kind'] == 'workflow' else None
        return {'candidate': result, 'evidence_refs': [{'claim': 'A positive value was independently checked', 'evidence_id': 'source'}]}

    def service(self, generator):
        return LearningGenerationService(self.f.runtime, self.f.tasks, lambda item: self.f.source, generator=generator)

    def completed(self, service, fields=None):
        result = service.start(self.source['id'], 'fixture-author', fields or {'kind': 'memory', 'task_id': self.task_id})
        service.workers[result['id']].join(10)
        self.assertFalse(service.workers[result['id']].is_alive())
        return service.get(self.source['id'], result['id'])

    def test_generated_draft_needs_human_save_and_keeps_original_output_and_edits(self):
        before = self.f.tasks.get(self.task_id)
        service = self.service(lambda repository, runtime, folder, packet, event: self.output(packet))
        result = self.completed(service)
        self.assertEqual('succeeded', result['status'], result['error'])
        self.assertEqual([], self.f.learning.view(self.source['id'])['assets'])
        self.assertEqual(before, self.f.tasks.get(self.task_id))
        candidate = result['candidate'] | {'content': 'Human refined the applicable positive value check'}
        asset = self.f.learning.create(self.source['id'], 'fixture-author', candidate)
        self.assertEqual('candidate', asset['state'])
        self.assertIn('content', asset['generation']['human_edited_fields'])
        self.assertEqual(result['output_sha256'], asset['generation']['output_sha256'])
        with self.assertRaisesRegex(ValueError, '独立审核'):
            self.f.learning.govern(self.source['id'], asset['id'], 'fixture-author', 'approve', 'self review forbidden')
        self.assertEqual(before, self.f.tasks.get(self.task_id))

    def test_bad_citation_failure_and_tampered_source_never_change_old_task(self):
        before = self.f.tasks.get(self.task_id)
        def invalid(repository, runtime, folder, packet, event):
            value = self.output(packet)
            value['evidence_refs'][0]['evidence_id'] = 'invented-path'
            return value
        service = self.service(invalid)
        result = self.completed(service)
        self.assertEqual('failed', result['status'])
        self.assertIn('引用不在', result['error'])
        self.assertEqual(before, self.f.tasks.get(self.task_id))
        service.generator = lambda repository, runtime, folder, packet, event: self.output(packet)
        result = self.completed(service)
        self.f.tasks.append_event(self.task_id, 'New real evidence after draft', actor='fixture-user')
        with self.assertRaisesRegex(ValueError, '来源已变化'):
            self.f.learning.create(self.source['id'], 'fixture-author', result['candidate'])

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing lock contract')
    def test_temporary_input_sharing_lock_retries_current_artifact_without_changing_source(self):
        before = self.f.tasks.get(self.task_id)
        denied, timers, held = [], [], {}
        original_open = Path.open
        def tracked_open(target, *args, **kwargs):
            try:
                return original_open(target, *args, **kwargs)
            except PermissionError:
                if target == held.get('path'):
                    denied.append(target)
                    if not timers:
                        timer = threading.Timer(.1, held['release'])
                        timers.append(timer)
                        timer.start()
                raise
        with ExitStack() as locks:
            def generated(repository, runtime, folder, packet, event):
                held['path'] = folder / 'input.json'
                held['release'] = locks.enter_context(windows_read_lock(held['path']))
                return self.output(packet)
            service = self.service(generated)
            self.addCleanup(service.close)
            with patch.object(Path, 'open', tracked_open):
                result = self.completed(service)
            for timer in timers:
                timer.join(3)
                self.assertFalse(timer.is_alive())
        self.assertTrue(denied, 'The fixture must actually deny at least one read')
        self.assertEqual('succeeded', result['status'], result['error'])
        self.assertIsNotNone(result['candidate'])
        artifact = result['artifacts']['input.json']
        self.assertEqual(hashlib.sha256(read_bytes(artifact['path'])).hexdigest(), artifact['sha256'])
        self.assertEqual(before, self.f.tasks.get(self.task_id))

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing lock contract')
    def test_permanent_input_sharing_lock_fails_and_preserves_both_failure_causes(self):
        before = self.f.tasks.get(self.task_id)
        for bad_citation in (False, True):
            with self.subTest(bad_citation=bad_citation), ExitStack() as locks:
                def generated(repository, runtime, folder, packet, event):
                    locks.enter_context(windows_read_lock(folder / 'input.json'))
                    value = self.output(packet)
                    if bad_citation:
                        value['evidence_refs'][0]['evidence_id'] = 'invented-path'
                    return value
                service = self.service(generated)
                self.addCleanup(service.close)
                result = self.completed(service)
                self.assertEqual('failed', result['status'], result['error'])
                self.assertIsNone(result['candidate'])
                self.assertIsNone(result['output_sha256'])
                self.assertIn('生成过程证据无法完整保存', result['error'])
                self.assertIn('Permission denied', result['error'])
                self.assertNotIn('input.json', result['artifacts'])
                if bad_citation:
                    self.assertIn('引用不在', result['error'])
                else:
                    self.assertIn('draft.json', result['artifacts'])
                fields = self.f.candidate(self.task_id, 'memory') | {
                    'generation_id': result['id'], 'evidence_refs': [
                        {'claim': 'Fixture claim', 'evidence_id': 'source'}]}
                with self.assertRaisesRegex(ValueError, '尚未成功'):
                    self.f.learning.create(self.source['id'], 'fixture-author', fields)
                self.assertEqual(before, self.f.tasks.get(self.task_id))
            input_file = self.f.runtime / 'learning-generation' / result['id'] / 'input.json'
            self.assertEqual(result['input_sha256'], hashlib.sha256(read_bytes(input_file)).hexdigest())

    def test_cancel_and_restart_generation_never_replay_or_fail_source(self):
        before = self.f.tasks.get(self.task_id)
        entered, release = threading.Event(), threading.Event()
        def blocked(repository, runtime, folder, packet, event):
            entered.set()
            release.wait(5)
            return self.output(packet)
        service = self.service(blocked)
        result = service.start(self.source['id'], 'fixture-author', {'kind': 'memory', 'task_id': self.task_id})
        self.assertTrue(entered.wait(5))
        service.cancel(self.source['id'], result['id'], 'fixture-author')
        release.set()
        service.workers[result['id']].join(10)
        self.assertEqual('cancelled', service.get(self.source['id'], result['id'])['status'])
        self.assertEqual(before, self.f.tasks.get(self.task_id))
        entered.clear()
        release.clear()
        result = service.start(self.source['id'], 'fixture-author', {'kind': 'memory', 'task_id': self.task_id})
        self.assertTrue(entered.wait(5))
        restored = self.service(blocked)
        self.assertFalse(restored.workers)
        self.assertEqual('interrupted', restored.get(self.source['id'], result['id'])['status'])
        release.set()
        service.workers[result['id']].join(10)
        self.assertEqual('interrupted', restored.get(self.source['id'], result['id'])['status'])
        self.assertEqual(before, self.f.tasks.get(self.task_id))

    def test_output_and_generation_artifact_tampering_are_rejected(self):
        service = self.service(lambda repository, runtime, folder, packet, event: self.output(packet))
        result = self.completed(service)
        artifact = next(iter(result['artifacts'].values()))
        Path(artifact['path']).write_text('replaced', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '生成过程文件'):
            self.f.learning.create(self.source['id'], 'fixture-author', result['candidate'])
        packet = collect_input(self.f.tasks, self.source['id'], {'kind': 'memory', 'task_id': self.task_id})
        with self.assertRaisesRegex(ValueError, '流程执行定义'):
            validate_output(self.output(packet) | {'candidate': self.output(packet)['candidate'] | {'recipe': {}}}, packet)
        workflow = collect_input(self.f.tasks, self.source['id'], {'kind': 'workflow', 'task_id': self.task_id})
        invalid = self.output(workflow)
        invalid['candidate']['recipe']['stop'] = 'Stop for ${undefined}'
        with self.assertRaisesRegex(ValueError, '未定义参数'):
            validate_output(invalid, workflow)


class GenerationLookupTests(unittest.TestCase):
    """Synthetic history lookup only; no generation or human review is invoked."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.tasks = TaskStore(self.runtime / 'workbench.db')
        self.service = LearningGenerationService(self.runtime, self.tasks, lambda item: self.runtime)
        self.addCleanup(self.service.close)
        self.packet = {'kind': 'memory', 'task_id': 'TASK-LOOKUP-FIXTURE',
                       'feedback_id': '', 'supersedes': '', 'evidence': []}
        with self.tasks.connect() as db:
            db.executemany('INSERT INTO learning_generations(id,initiative_id,status,actor,input_payload,input_sha256,created_at,error) '
                           'VALUES(?,?,?,?,?,?,?,?)',
                [('GEN-LOOKUP-' + str(index), 'INIT-LOOKUP', 'failed', 'fixture',
                  canonical(self.packet), digest(self.packet), index, 'synthetic lookup fixture')
                 for index in range(1001)])

    def test_lookup_cost_does_not_scan_recent_history(self):
        statements = []
        original_connect = self.tasks.connect
        @contextmanager
        def traced_connect(**kwargs):
            with original_connect(**kwargs) as db:
                db.set_trace_callback(statements.append)
                yield db
        with patch.object(self.tasks, 'connect', traced_connect):
            result = self.service.get('INIT-LOOKUP', 'GEN-LOOKUP-1000')
        self.assertEqual('GEN-LOOKUP-1000', result['id'])
        self.assertLessEqual(len([sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]), 4,
                             'one lookup must not scan unrelated generation history')

    def test_lookup_preserves_valid_history_outside_latest_thousand(self):
        result = self.service.get('INIT-LOOKUP', 'GEN-LOOKUP-0')
        self.assertEqual('GEN-LOOKUP-0', result['id'])
        self.assertEqual('synthetic lookup fixture', result['error'])
        self.assertIsNone(result['candidate'])

    def test_lookup_isolates_other_corrupt_records_but_rejects_requested_corruption(self):
        with self.tasks.connect() as db:
            db.execute("UPDATE learning_generations SET input_sha256='other-corrupt-fixture' WHERE id='GEN-LOOKUP-999'")
        self.assertEqual('GEN-LOOKUP-1000', self.service.get('INIT-LOOKUP', 'GEN-LOOKUP-1000')['id'])
        with self.assertRaisesRegex(ValueError, '输入快照被修改'):
            self.service.get('INIT-LOOKUP', 'GEN-LOOKUP-999')
        with self.assertRaisesRegex(ValueError, '不属于本事项'):
            self.service.get('INIT-OTHER-FIXTURE', 'GEN-LOOKUP-1000')
        with self.tasks.connect() as db:
            db.execute("UPDATE learning_generations SET output_payload='{}',output_sha256='changed-fixture' WHERE id='GEN-LOOKUP-1000'")
        with self.assertRaisesRegex(ValueError, '输出快照被修改'):
            self.service.get('INIT-LOOKUP', 'GEN-LOOKUP-1000')


class RecipeV2Tests(unittest.TestCase):
    def base(self):
        phases = ['precheck', 'implement', 'eval', 'review']
        return {'parameters': [], 'preconditions': [{'kind': 'file_exists', 'path': 'policy.txt'}],
                'steps': [{'phase': p, 'role': ['harness', 'codex', 'harness', 'human'][i],
                           'depends_on': [] if i == 0 else [phases[i-1]], 'instruction': p} for i, p in enumerate(phases)],
                'authorization': 'confirmed_plan', 'eval_entry': 'project_blocking', 'outputs': ['Actual Diff and Eval'],
                'stop': 'Stop at a failed check', 'rollback': 'Retain candidate and prepare a new plan'}

    def test_v1_serialization_is_unchanged_v2_cannot_remove_evidence_or_auto_rollback(self):
        original = self.base()
        self.assertEqual(original, LearningStore.recipe(original))
        for change in ({'required_evidence': ['project_eval']}, {'recovery': 'git_reset'},
                       {'stage_checks': {'arbitrary_shell': []}}, {'required_files': [{'phase': 'review', 'path': 'x'}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                LearningStore.recipe(v2(original, **change))

    def test_full_recipe_parameters_are_frozen_and_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'policy.txt').write_text('positive', encoding='utf-8')
            recipe = LearningStore.recipe(v2(self.base(), parameters=['policy'],
                outputs=['${policy}'], stop='Stop if ${policy} changes', rollback='Keep ${policy}',
                required_files=[{'phase': 'implement', 'path': '${policy}'}]))
            asset = {'recipe': recipe}
            prepared = LearningStore.preconditions(asset, {'policy': 'policy.txt'}, root)
            self.assertEqual(['policy.txt'], prepared['recipe']['outputs'])
            self.assertEqual('Stop if policy.txt changes', prepared['recipe']['stop'])
            recipe['stage_checks']['precheck'] = [{'kind': 'file_exists', 'path': '../escaped'}]
            with self.assertRaisesRegex(ValueError, '边界'):
                LearningStore.preconditions(asset, {'policy': 'policy.txt'}, root)


class RecipeV2DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f = LocalDeliveryFixture()
        self.addCleanup(self.f.close)
        self.source, self.task_id = self.f.accepted_source()

    def asset(self, **updates):
        candidate = self.f.candidate(self.task_id, 'workflow')
        candidate['recipe'] = v2(candidate['recipe'], **updates)
        asset = self.f.learning.create(self.source['id'], 'fixture-author', candidate)
        return self.f.learning.govern(self.source['id'], asset['id'], 'fixture-peer', 'approve', 'Allow isolated trial')

    def test_v2_trial_records_actual_diff_files_and_eval_before_named_acceptance(self):
        asset = self.asset(stage_checks={'implement': [{'kind': 'file_contains', 'path': 'value.txt', 'text': '3'}]},
                           required_files=[{'phase': 'implement', 'path': 'value.txt'}])
        trial = self.f.item('value v2 independent trial')
        self.f.value = 3
        self.f.prepare(trial, trials=True, choices=[self.f.choice(asset)])
        state = self.f.execute(trial)
        self.assertEqual('review', state['stage'], state.get('error'))
        binding = state['learning']['bindings'][0]
        implementation = next(r['payload'] for r in binding['runs'] if r['phase'] == 'implement')
        self.assertTrue(implementation['diff_artifact']['sha256'])
        self.assertEqual('value.txt', implementation['phase_contract']['required_files'][0]['path'])
        self.f.accept(trial)
        self.assertEqual(1, self.f.learning.view(trial['id'])['metrics']['reuse_passed'])

    def test_implement_gate_stops_before_eval_and_revokes_failed_version(self):
        asset = self.asset(required_files=[{'phase': 'implement', 'path': 'missing-output.txt'}])
        trial = self.f.item('value missing v2 output')
        self.f.value = 3
        self.f.prepare(trial, trials=True, choices=[self.f.choice(asset)])
        state = self.f.execute(trial)
        self.assertEqual('rework', state['stage'])
        task = self.f.tasks.get(state['active_task_id'])
        self.assertFalse(any(e.get('to_status') == 'evaluating' for e in task['events']))
        self.assertEqual('revoked', self.f.learning.get(asset['id'])['state'])
        binding = state['learning']['bindings'][0]
        record = next(r['payload'] for r in binding['runs'] if r['phase'] == 'implement')
        self.assertEqual('implement', record['stopped_phase'])
        self.assertEqual(RECOVERY, record['recovery'])
        self.assertEqual('1', (self.f.source / 'value.txt').read_text())

    def test_eval_gate_failure_keeps_passing_report_but_cannot_accept(self):
        asset = self.asset(stage_checks={'eval': [{'kind': 'file_contains', 'path': 'value.txt', 'text': '99'}]})
        trial = self.f.item('value blocked after v2 Eval')
        self.f.value = 3
        self.f.prepare(trial, trials=True, choices=[self.f.choice(asset)])
        state = self.f.execute(trial)
        self.assertEqual('rework', state['stage'])
        task = self.f.tasks.get(state['active_task_id'])
        self.assertEqual('pass', task['result']['summary']['decision'])
        self.assertEqual('revoked', self.f.learning.get(asset['id'])['state'])
        self.assertEqual(0, state['learning']['metrics']['reuse_passed'])

    def test_manual_recheck_updates_real_report_and_appends_eval_contract_without_rewriting_history(self):
        asset = self.asset(required_files=[{'phase': 'eval', 'path': 'value.txt'}])
        trial = self.f.item('value v2 repeat Eval')
        self.f.value = 3
        self.f.prepare(trial, trials=True, choices=[self.f.choice(asset)])
        state = self.f.execute(trial)
        task_id = state['active_task_id']
        original = self.f.tasks.get(task_id)['result']
        binding = state['learning']['bindings'][0]
        initial_eval = next(r for r in binding['runs'] if r['phase'] == 'eval')
        self.f.call(trial, 'run_eval')
        state = self.f.wait(trial)
        self.assertEqual('review', state['stage'], state.get('error'))
        report = self.f.tasks.get(task_id)['result']
        self.assertNotEqual(original['report_sha256'], report['report_sha256'])
        self.assertNotEqual(original['runner']['process_path'], report['runner']['process_path'])
        self.assertTrue((self.f.runtime / original['report_path']).is_file())
        runs = self.f.learning.view(trial['id'])['bindings'][0]['runs']
        self.assertEqual(initial_eval, next(r for r in runs if r['phase'] == 'eval'))
        latest = next(r['payload'] for r in runs if r['phase'].startswith('eval_recheck/'))
        self.assertEqual(report['report_sha256'], latest['report_sha256'])
        self.assertEqual('value.txt', latest['phase_contract']['required_files'][0]['path'])
        self.f.accept(trial)
        self.assertEqual(1, self.f.learning.view(trial['id'])['metrics']['reuse_passed'])

    def test_manual_recheck_green_eval_with_invalid_output_enters_rework_and_revokes(self):
        # Eval-owned outputs are ignored by Git but still required by the recipe.
        (self.f.source / '.gitignore').write_text('.cache/\n', encoding='utf-8')
        check = self.f.source / 'contract_eval.py'
        check.write_text("from pathlib import Path\nPath('.cache').mkdir(exist_ok=True)\n"
            "Path('.cache/output.txt').write_text('bad' if Path('.cache/break-output').exists() else 'good')\n"
            + check.read_text(encoding='utf-8'), encoding='utf-8')
        asset = self.asset(stage_checks={'eval': [{'kind': 'file_contains', 'path': '.cache/output.txt', 'text': 'good'}]},
                           required_files=[{'phase': 'eval', 'path': '.cache/output.txt'}])
        trial = self.f.item('value ignored output recheck')
        self.f.value = 3
        self.f.prepare(trial, trials=True, choices=[self.f.choice(asset)])
        state = self.f.execute(trial)
        self.assertEqual('review', state['stage'], state.get('error'))
        (Path(state['workspace']) / '.cache/break-output').write_text('fixture trigger', encoding='utf-8')
        self.f.call(trial, 'run_eval')
        state = self.f.wait(trial)
        self.assertEqual('rework', state['stage'], state.get('error'))
        task = self.f.tasks.get(state['active_task_id'])
        self.assertEqual('pass', task['result']['summary']['decision'])
        self.assertEqual('rework', task['status'])
        self.assertEqual('revoked', self.f.learning.get(asset['id'])['state'])
        latest = next(r['payload'] for r in state['learning']['bindings'][0]['runs'] if r['phase'].startswith('eval_recheck/'))
        self.assertFalse(latest['passed'])
        self.assertIn('内容不满足', latest['message'])

    def test_manual_recheck_rejects_changed_learning_candidate_before_evaluation(self):
        asset = self.asset()
        trial = self.f.item('value changed candidate recheck')
        self.f.value = 3
        self.f.prepare(trial, trials=True, choices=[self.f.choice(asset)])
        state = self.f.execute(trial)
        (Path(state['workspace']) / 'value.txt').write_text('4', encoding='utf-8')
        self.f.call(trial, 'run_eval')
        state = self.f.wait(trial)
        self.assertEqual('failed', state['stage'])
        self.assertIn('候选已变化', state['error'])
        self.assertEqual('revoked', self.f.learning.get(asset['id'])['state'])

    def test_legacy_recipe_recheck_cannot_turn_manual_candidate_edits_into_proven_reuse(self):
        fields = self.f.candidate(self.task_id, 'workflow')
        asset = self.f.learning.create(self.source['id'], 'fixture-author', fields)
        asset = self.f.learning.govern(self.source['id'], asset['id'], 'fixture-peer', 'approve', 'Independent trial')
        trial = self.f.item('value v1 changed but positive')
        self.f.value = 3
        self.f.prepare(trial, trials=True, choices=[self.f.choice(asset)])
        state = self.f.execute(trial)
        (Path(state['workspace']) / 'value.txt').write_text('4', encoding='utf-8')
        self.f.call(trial, 'run_eval')
        state = self.f.wait(trial)
        self.assertEqual('rework', state['stage'])
        self.assertEqual('pass', self.f.tasks.get(state['active_task_id'])['result']['summary']['decision'])
        self.assertEqual(0, self.f.learning.view(trial['id'])['metrics']['reuse_passed'])
        self.assertEqual(asset['sha256'], self.f.learning.get(asset['id'])['sha256'])


@unittest.skipUnless(os.name == 'nt', 'Windows Job process-tree contract')
class OwnedGenerationTests(unittest.TestCase):
    def test_real_owned_readonly_process_tree_stops_on_cancel_and_close(self):
        fixture = LocalDeliveryFixture()
        self.addCleanup(fixture.close)
        source, task_id = fixture.accepted_source()
        original_task = fixture.tasks.get(task_id)
        commands = []
        class OwnedRunner(CodexExecutionRunner):
            def capabilities(self):
                return {'codex_available': True}

            def _run_codex_streaming(self, command, prompt, timeout, callback, started):
                commands.append(command)
                output = Path(command[command.index('--output-last-message') + 1])
                pid_file = output.parent / 'fixture-pids.json'
                script = ('import json,os,subprocess,sys,time;from pathlib import Path;'
                    'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);'
                    + 'Path(' + repr(str(pid_file)) + ').write_text(json.dumps({"parent":os.getpid(),"child":child.pid}));'
                    + 'print("fixture process started",flush=True);time.sleep(30)')
                return super()._run_codex_streaming([sys.executable, '-c', script], '', timeout, callback, started)

        def live(pid):
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            api.OpenProcess.restype = ctypes.c_void_p
            api.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            api.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = api.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                status = ctypes.c_ulong()
                return bool(api.GetExitCodeProcess(handle, ctypes.byref(status))) and status.value == 259
            finally:
                api.CloseHandle(handle)

        for action in ('cancel', 'close'):
            with self.subTest(action=action), patch('workbench.execution.CodexExecutionRunner', OwnedRunner):
                service = LearningGenerationService(fixture.runtime, fixture.tasks,
                    lambda item: fixture.source, generator=CodexLearningGenerator())
                self.addCleanup(service.close)
                record = service.start(source['id'], 'fixture-author', {'kind': 'memory', 'task_id': task_id})
                pid_file = fixture.runtime / 'learning-generation' / record['id'] / 'fixture-pids.json'
                deadline = time.monotonic() + 8
                while not pid_file.is_file() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(pid_file.is_file(), service.get(source['id'], record['id']))
                pids = json.loads(read_bytes(pid_file))
                self.assertTrue(all(live(pid) for pid in pids.values()))
                self.assertIn('read-only', commands[-1])
                if action == 'cancel':
                    service.cancel(source['id'], record['id'], 'fixture-author')
                    service.workers[record['id']].join(8)
                    expected = 'cancelled'
                else:
                    self.assertEqual([], service.close()['live_generations'])
                    expected = 'interrupted'
                self.assertFalse(service.busy())
                self.assertTrue(all(not live(pid) for pid in pids.values()))
                result = service.get(source['id'], record['id'])
                self.assertEqual(expected, result['status'])
                self.assertTrue(result['artifacts'])
                self.assertEqual(original_task, fixture.tasks.get(task_id))
                service.close()
                with self.assertRaisesRegex(ValueError, '已停止'):
                    service.start(source['id'], 'fixture-author', {'kind': 'memory', 'task_id': task_id})


if __name__ == '__main__':
    unittest.main()
