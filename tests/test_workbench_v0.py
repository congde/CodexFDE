"""V0 control experiments. Injected processes are fixtures, not Codex evidence."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from workbench.task_store import TaskStore
from workbench.execution import CodexExecutionRunner
from workbench.workflow import prepare_task, run_task
from workbench.delivery_view import build_delivery_view


SPEC = '\n\n'.join('## ' + heading + '\n' + value for heading, value in [
    ('来源', '控制实验，非真实业务验收'), ('目标', '修改 value.py'), ('非目标', '不改其他文件'),
    ('约束', '逐文件授权'), ('验收用例', '检查值为 2'), ('完成定义', '检查通过仍待人工审核')])


class WorkbenchV0Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / 'candidate'
        self.workspace.mkdir()
        self.runtime = self.root / 'runtime'
        self.store = TaskStore(self.runtime / 'workbench.db')
        self.spec = self.runtime / 'SPEC.md'
        self.spec.write_text(SPEC, encoding='utf-8')
        (self.workspace / 'value.py').write_text('value = 1\n', encoding='utf-8')

    def task(self, mode='verify', **kwargs):
        options = dict(spec_path=str(self.spec), actor='builder', execution_mode=mode,
                       workspace_path=str(self.workspace), execution_timeout_seconds=30,
                       write_scope=['value.py'] if mode == 'codex' else [])
        options.update(kwargs)
        return self.store.create_v0('V0 控制实验', **options)

    def report(self, passed=True):
        return {'summary': {'decision': 'pass' if passed else 'block',
                            'blocking_failed': 0 if passed else 1}}

    def runner(self, process):
        return CodexExecutionRunner(self.workspace, self.runtime, process_runner=process)

    def test_created_contract_is_frozen_and_queryable_after_source_change(self):
        task = self.task()
        self.spec.write_text('invalid replacement', encoding='utf-8')
        prepared = prepare_task(self.store, task['id'])
        self.assertEqual(SPEC, prepared['spec_text'])
        self.assertEqual(hashlib.sha256(SPEC.encode()).hexdigest(), prepared['spec_sha256'])
        self.assertEqual('修改 value.py', prepared['spec']['goal'])
        self.assertEqual(SPEC, build_delivery_view(prepared)['spec']['text'])

    def test_invalid_contract_creates_no_task(self):
        self.spec.write_text(SPEC.split('## 完成定义')[0], encoding='utf-8')
        with self.assertRaises(ValueError):
            self.task()
        self.assertEqual([], self.store.list())

    def test_missing_workspace_allowlist_and_directory_scope_rejected_before_launch(self):
        for options in ({'workspace_path': ''}, {'write_scope': []},
                        {'write_scope': ['.']}, {'execution_timeout_seconds': 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.task('codex', **options)
        (self.workspace / 'package').mkdir()
        with self.assertRaises(ValueError):
            self.task('codex', write_scope=['package'])

    def test_missing_source_task_is_not_silently_linked(self):
        with self.assertRaises(ValueError):
            self.task(source_task_id='missing')
        self.assertEqual([], self.store.list())

    def test_verify_never_calls_process_and_green_waits_for_review(self):
        task = self.task()
        process = Mock(side_effect=AssertionError('verification cannot call Codex'))
        result = run_task(self.store, task['id'], 'checker', execution_runner=self.runner(process),
                          suite_runner=lambda *a, **k: self.report())
        process.assert_not_called()
        self.assertEqual('review', result['status'])
        self.assertIsNone(result['reviewed_by'])
        view = build_delivery_view(result)
        self.assertEqual('review', view['status']['code'])
        self.assertTrue(view['review']['required'])
        self.assertEqual([], view['delivery_summary']['changed_files'])
        self.assertTrue(view['delivery_summary']['remaining_risks'])

    def test_normal_code_run_records_diff_then_waits(self):
        task = self.task('codex')
        def process(command, **kwargs):
            (self.workspace / 'value.py').write_text('value = 2\n', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, 'fixture output', '')
        result = run_task(self.store, task['id'], 'checker', execution_runner=self.runner(process),
                          suite_runner=lambda *a, **k: self.report())
        self.assertEqual('review', result['status'])
        view = build_delivery_view(result)
        self.assertEqual(['value.py'], view['execution']['changed_files'])
        self.assertIn('+value = 2', view['execution']['evidence']['diff'])
        self.assertEqual(str(self.workspace), result['workspace_path'])

    def test_zero_exit_out_of_scope_stops_before_eval(self):
        task = self.task('codex')
        def process(command, **kwargs):
            (self.workspace / 'outside.py').write_text('unauthorized = True')
            return subprocess.CompletedProcess(command, 0, 'fixture', '')
        evaluate = Mock(side_effect=AssertionError('must stop before Eval'))
        result = run_task(self.store, task['id'], execution_runner=self.runner(process), suite_runner=evaluate)
        evaluate.assert_not_called()
        self.assertEqual('rework', result['status'])
        self.assertIn('outside.py', result['error'])
        self.assertEqual(['outside.py'], build_delivery_view(result)['execution']['out_of_scope_files'])

    def test_protected_runtime_write_is_not_hidden_from_v0_diff(self):
        task = self.task('codex')
        def process(command, **kwargs):
            directory = self.workspace / '.runtime'
            directory.mkdir()
            (directory / 'unauthorized.txt').write_text('not allowed')
            return subprocess.CompletedProcess(command, 0, 'fixture', '')
        evidence = self.runner(process)(task)
        self.assertFalse(evidence['success'])
        self.assertIn('.runtime/unauthorized.txt', evidence['out_of_scope_files'])

    def test_failure_timeout_and_launch_error_preserve_output_and_diff(self):
        for kind in ('exit', 'timeout', 'launch'):
            with self.subTest(kind=kind):
                task = self.task('codex')
                def process(command, **kwargs):
                    (self.workspace / 'value.py').write_text(kind)
                    if kind == 'timeout':
                        raise subprocess.TimeoutExpired(command, 30, output='partial', stderr='timeout fixture')
                    if kind == 'launch':
                        raise OSError('launch fixture')
                    return subprocess.CompletedProcess(command, 7, 'partial', 'failure fixture')
                result = run_task(self.store, task['id'], execution_runner=self.runner(process))
                self.assertEqual('rework', result['status'])
                evidence = build_delivery_view(result)['execution']['evidence']
                self.assertNotEqual(0, evidence['returncode'])
                self.assertIn('value.py', evidence['changed_files'])
                self.assertTrue(Path(evidence['artifacts']['stderr']).is_file())

    def test_complete_output_and_old_attempt_are_retained(self):
        task = self.task('codex')
        output = '中文\n' * 30000
        process = lambda command, **kwargs: subprocess.CompletedProcess(command, 1, output, output)
        runner = self.runner(process)
        first = runner(task)
        old = Path(first['artifacts']['stdout'])
        second = runner(task)
        self.assertNotEqual(first['attempt_id'], second['attempt_id'])
        self.assertEqual(output, old.read_text(encoding='utf-8'))
        self.assertEqual(output, Path(second['artifacts']['stderr']).read_text(encoding='utf-8'))

    def test_wrong_candidate_is_rejected_before_process(self):
        task = self.task('codex')
        process = Mock(side_effect=AssertionError('wrong candidate must not run'))
        runner = CodexExecutionRunner(self.root, self.runtime, process_runner=process)
        with self.assertRaises(ValueError):
            runner(task)
        process.assert_not_called()

    def test_failed_eval_retained_after_second_attempt(self):
        task = self.task()
        first = run_task(self.store, task['id'], suite_runner=lambda *a, **k: self.report(False))
        self.assertEqual('rework', first['status'])
        path = self.runtime / first['result']['report_path']
        original = path.read_bytes()
        second = run_task(self.store, task['id'], suite_runner=lambda *a, **k: self.report())
        self.assertEqual('review', second['status'])
        self.assertNotEqual(first['result']['report_path'], second['result']['report_path'])
        self.assertEqual(original, path.read_bytes())

    def test_review_rerun_is_rejected_without_destroying_pending_review(self):
        task = self.task()
        run_task(self.store, task['id'], suite_runner=lambda *a, **k: self.report())
        with self.assertRaises(ValueError):
            run_task(self.store, task['id'])
        self.assertEqual('review', self.store.get(task['id'])['status'])

    def test_existing_parser_and_unapproved_handoff_guard(self):
        from workbench.spec import parse_spec
        from workbench.bootstrap_handoff import require_bootstrap_ticket
        self.assertEqual('修改 value.py', parse_spec(SPEC).goal)
        existing = self.store.create('fixture, not an acceptance')
        with self.assertRaises(ValueError):
            require_bootstrap_ticket(self.store, existing['id'])

    def test_self_approval_of_v0_bootstrap_is_rejected(self):
        task = self.task(requirement_id='WB-L04-BOOTSTRAP')
        self.store.transition(task['id'], 'spec_ready')
        self.store.transition(task['id'], 'executing', actor='builder')
        self.store.transition(task['id'], 'evaluating', actor='builder')
        self.store.transition(task['id'], 'review', result=self.report())
        with self.assertRaises(ValueError):
            self.store.review(task['id'], 'builder', 'approve', 'self approval forbidden')
        self.assertEqual('review', self.store.get(task['id'])['status'])

    def test_candidate_check_command_runs_in_bound_workspace(self):
        # Real local subprocess, not a Codex call. A tiny Eval module represents
        # the candidate; a controller Eval would return a different result.
        folder = self.workspace / 'eval'
        folder.mkdir()
        (folder / '__init__.py').write_text('')
        (folder / 'harness.py').write_text('import sys; print("candidate marker"); sys.exit(7)')
        task = self.task()
        result = run_task(self.store, task['id'])
        self.assertEqual('failed', result['status'])
        receipts = list((self.runtime / 'reports').glob('eval-*/process.json'))
        self.assertEqual(1, len(receipts))
        receipt = json.loads(receipts[0].read_text(encoding='utf-8'))
        self.assertEqual(7, receipt['returncode'])
        self.assertIn('candidate marker', receipt['stdout'])
        self.assertEqual(str(self.workspace), receipt['workspace'])

    def test_http_reads_same_frozen_contract_and_full_output(self):
        from workbench.workbench_server import WorkbenchApp, make_handler
        from http.server import ThreadingHTTPServer
        import threading
        from urllib.request import build_opener, ProxyHandler
        urlopen = build_opener(ProxyHandler({})).open
        task = self.task('codex')
        evidence = self.runner(lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, 'full output', 'error'))(task)
        self.store.append_event(task['id'], '受控执行阶段完成', evidence=evidence)
        app = WorkbenchApp(self.runtime)
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:' + str(server.server_port)
        with urlopen(base + '/api/v1/delivery/views/' + task['id']) as response:
            view = json.load(response)
        self.assertEqual(SPEC, view['spec']['text'])
        event = view['events'][-1]
        with urlopen(base + '/api/v1/tasks/' + task['id'] + '/artifacts/' + str(event['id']) + '/stdout') as response:
            self.assertEqual('full output', response.read().decode('utf-8'))

    def test_homepage_submission_links_one_real_task_and_requires_confirmation(self):
        from workbench.workbench_server import WorkbenchApp, make_handler
        from http.server import ThreadingHTTPServer
        from http.client import HTTPConnection
        from unittest.mock import patch
        import threading
        app = WorkbenchApp(self.runtime)
        item = app.initiatives.create({'title': 'V0 HTTP 控制实验', 'raw_signal': '接通已有合同',
                                       'source': '自动化测试', 'project_id': app.default_project}, 'builder')
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        payload = dict(actor='builder', revision=0, spec_text=SPEC, execution_mode='verify',
                       workspace_path=str(self.workspace), write_scope=[], execution_timeout_seconds=30)
        def post():
            connection = HTTPConnection('127.0.0.1', server.server_port)
            connection.request('POST', '/api/v1/initiatives/' + item['id'] + '/workflow/v0',
                               json.dumps(payload).encode('utf-8'), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = json.loads(response.read())
            connection.close()
            return response.status, result
        status, _ = post()
        self.assertEqual(400, status)
        self.assertEqual([], app.tasks.list())
        payload['confirmed'] = True
        # This test inspects submission, not real execution or human consent.
        with patch.object(app.initiative_workflow, '_launch', side_effect=lambda data, *a: app.initiative_workflow._save(data)):
            status, result = post()
            self.assertEqual(200, status, result)
            task = self.store.get(result['active_task_id'])
            self.assertEqual(SPEC, task['spec_text'])
            self.assertEqual(task['id'], app.initiatives.get(item['id'])['linked_task_id'])
            self.assertEqual('v0', task['authorization_policy'])
            status, _ = post()
            self.assertEqual(400, status)
            self.assertEqual(1, len(app.tasks.list()))
