"""Persistent business workflow with real snapshots, subprocess Eval and integration.

Only Codex output is a controlled fixture; no simulated business evidence is saved
in the live workbench by these tests.
"""
import unittest
import json
import subprocess
import os
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import test_daily_delivery
from workbench.daily_delivery import manifest, submit_daily
from workbench.initiative import InitiativeStore
from workbench.initiative_workflow import InitiativeWorkflow
from workbench.initiative_research import InitiativeResearch
from workbench.execution import CodexExecutionRunner
from workbench.codex_options import headless_environment
from workbench.research_context import source_context
from workbench.workbench_server import WorkbenchApp, make_handler


class InitiativeWorkflowTests(unittest.TestCase):
    def test_child_cli_does_not_reuse_parent_desktop_tools_pipe(self):
        with patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': 'parent-only', 'CODEX_THREAD_ID': 'parent',
                                     'CODEX_HOME': 'keep-auth-home', 'PATH': 'keep-path'}):
            env = headless_environment()
            self.assertNotIn('CODEX_APP_TOOLS_PIPE_PATH', env)
            self.assertNotIn('CODEX_THREAD_ID', env)
            self.assertEqual('keep-auth-home', env['CODEX_HOME'])
            self.assertEqual('keep-path', env['PATH'])
            self.assertEqual('parent-only', os.environ['CODEX_APP_TOOLS_PIPE_PATH'])

    def setUp(self):
        self.fixture = test_daily_delivery.DailyDeliveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.runtime, self.tasks = self.fixture.root, self.fixture.runtime, self.fixture.tasks
        self.items = InitiativeStore(self.tasks.path)
        self.item = self.items.create({'title': '改进数值', 'raw_signal': '改进数值', 'source': '自动测试'}, 'owner')
        self.questions = ['本期目标是什么？']
        self.value = 2
        self.research_sources = []
        def research(source, runtime, folder, context, progress):
            self.research_sources.append((Path(source), context))
            progress('{"type":"research.started"}')
            return {'proposal': {'findings': ['已有数值实现'], 'questions': list(self.questions),
                'goal': '更新数值', 'acceptance': ['数值按决定更新'], 'non_goals': ['不改其他模块'],
                'write_scope': ['flowerp'], 'steps': ['修改数值并运行检查'], 'sources': ['flowerp/value.py']},
                'source_manifest': manifest(source, runtime), 'invocation': {'test_fixture': True}}
        def submit(source, runtime, tasks, plan, created):
            return submit_daily(source, runtime, tasks, plan, created, runner_factory=self.fixture.runner(self.value))
        self.service = InitiativeWorkflow(self.root, self.runtime, self.items, self.tasks,
                                           enabled=True, researcher=research, submitter=submit)

    def state(self):
        return self.service.get(self.item['id'])

    def call(self, action, *args, actor='owner'):
        return getattr(self.service, action)(self.item['id'], actor, self.state()['revision'], *args)

    def wait(self):
        worker = self.service.workers[self.item['id']]
        worker.join(180)
        self.assertFalse(worker.is_alive())
        return self.state()

    def test_research_packet_contains_real_source_and_detects_drift(self):
        fingerprints = manifest(self.root, self.runtime)
        packet = source_context(self.root, fingerprints, {'initiative': {'goal': 'value'}})
        file = next(f for f in packet['files'] if f['path'] == 'flowerp/value.py')
        self.assertIn('VALUE = 1', file['excerpts'][0]['text'])
        self.assertEqual(fingerprints['flowerp/value.py'], file['sha256'])
        (self.root / 'flowerp/value.py').write_text('VALUE = 9\n')
        with self.assertRaisesRegex(ValueError, '源码变化'):
            source_context(self.root, fingerprints, {'initiative': {'goal': 'value'}})

    def test_source_drift_keeps_discussion_but_blocks_confirmation(self):
        original = self.service.researcher
        def research(*args):
            result = original(*args)
            (self.root / 'flowerp/value.py').write_text('VALUE = 8\n')
            result['changed_sources'] = ['flowerp/value.py']
            return result
        self.service.researcher = research
        self.questions = []
        self.call('discuss', '确定值为 2')
        result = self.wait()
        self.assertEqual('ready', result['stage'])
        self.assertTrue(result['proposal'])
        self.assertIn('源码有更新', result['warning'])
        with self.assertRaisesRegex(ValueError, '源码已变化'):
            self.call('confirm', 'reviewer')

    def ready(self):
        self.questions = []
        self.call('discuss', '本期更新数值')
        self.assertEqual('ready', self.wait()['stage'])
        self.call('confirm', 'reviewer')

    def candidate(self):
        self.ready()
        self.call('execute')
        result = self.wait()
        self.assertEqual('review', result['stage'], result.get('error'))
        return result

    def test_questions_confirm_execution_rework_accept_and_cumulative_integration(self):
        self.call('discuss', '改进数值')
        self.assertEqual('clarifying', self.wait()['stage'])
        with self.assertRaises(ValueError):
            self.call('confirm', 'reviewer')
        first = self.candidate()
        first_workspace = Path(first['workspace'])
        self.value = 3
        self.ready()
        self.assertEqual(first_workspace, self.research_sources[-1][0])
        self.assertIn('previous_result', self.research_sources[-1][1])
        self.call('execute')
        second = self.wait()
        self.assertEqual('review', second['stage'], second.get('error'))
        self.assertEqual(2, len(second['iterations']))
        self.assertEqual('VALUE = 2\n', (first_workspace / 'flowerp/value.py').read_text())
        self.assertEqual('VALUE = 1\n', (self.root / 'flowerp/value.py').read_text())
        with self.assertRaisesRegex(ValueError, '验收负责人'):
            self.call('accept', '已核对')
        self.call('accept', '已核对数值与检查', actor='reviewer')
        self.call('integrate', actor='reviewer')
        final = self.wait()
        self.assertEqual('integrated', final['stage'], final.get('error'))
        self.assertEqual('VALUE = 3\n', (self.root / 'flowerp/value.py').read_text())
        self.assertTrue((self.root / 'flowerp/new.py').exists())
        self.assertTrue(Path(final['integration']['receipt']).is_file())
        restored = InitiativeWorkflow(self.root, self.runtime, self.items, self.tasks, enabled=True)
        self.assertEqual(final['messages'], restored.get(self.item['id'])['messages'])

    def test_revision_source_and_candidate_changes_block_stale_authorizations(self):
        self.ready()
        with self.assertRaisesRegex(ValueError, '进展已变化'):
            self.service.execute(self.item['id'], 'owner', 0)
        self.call('execute')
        self.assertEqual('review', self.wait()['stage'])
        workspace = Path(self.state()['workspace'])
        (workspace / 'flowerp/value.py').write_text('VALUE = 999\n')
        with self.assertRaisesRegex(ValueError, '检查后发生变化'):
            self.call('accept', '通过', actor='reviewer')

    def test_changed_main_source_cannot_be_overwritten(self):
        self.candidate()
        self.call('accept', '通过', actor='reviewer')
        (self.root / 'flowerp/value.py').write_text('VALUE = 88\n')
        self.call('integrate', actor='reviewer')
        result = self.wait()
        self.assertEqual('failed', result['stage'])
        self.assertIn('源项目已变化', result['error'])
        self.assertEqual('VALUE = 88\n', (self.root / 'flowerp/value.py').read_text())

    def test_eval_failure_and_no_change_remain_rework(self):
        for value in (-1, None):
            self.value = value
            self.ready()
            self.call('execute')
            result = self.wait()
            self.assertEqual('rework', result['stage'], result.get('error'))
            with self.assertRaises(ValueError):
                self.call('accept', '不能接受', actor='reviewer')

    def test_research_failure_and_restart_are_preserved(self):
        def broken(*args):
            raise RuntimeError('CLI failed')
        self.service.researcher = broken
        self.call('discuss', '调研')
        self.assertIn('CLI failed', self.wait()['error'])
        data = self.service._load(self.item['id'])
        data['stage'] = 'researching'
        self.service._save(data)
        restored = InitiativeWorkflow(self.root, self.runtime, self.items, self.tasks)
        result = restored.get(self.item['id'])
        self.assertEqual('interrupted', result['stage'])
        self.assertIn('重启', result['messages'][-1]['text'])
        with self.assertRaisesRegex(ValueError, '未启用'):
            restored.discuss(self.item['id'], 'owner', result['revision'], '再试')

    def test_integration_rollback_retains_concurrent_external_change(self):
        self.candidate()
        self.call('accept', '通过', actor='reviewer')
        real_write = Path.write_bytes
        def write(path, content):
            if path == self.root / 'flowerp/value.py':
                (self.root / 'flowerp/new.py').write_text('EXTERNAL = True\n')
                raise OSError('disk write failed')
            return real_write(path, content)
        with patch.object(Path, 'write_bytes', write):
            self.call('integrate', actor='reviewer')
            result = self.wait()
        self.assertEqual('failed', result['stage'])
        self.assertEqual('EXTERNAL = True\n', (self.root / 'flowerp/new.py').read_text())
        self.assertEqual('VALUE = 1\n', (self.root / 'flowerp/value.py').read_text())

    def test_http_routes_revision_checks_and_cross_origin_rejection(self):
        app = WorkbenchApp(self.runtime, enable_code_execution=True)
        app.initiative_workflow = self.service
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, body=None, origin=None):
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                headers = {'Content-Type': 'application/json'}
                if origin:
                    headers['Origin'] = origin
                connection.request(method, path, json.dumps(body) if body else None, headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        base = '/api/v1/initiatives/' + self.item['id'] + '/workflow'
        try:
            self.assertEqual('idle', request('GET', base)[1]['stage'])
            body = {'actor': 'owner', 'revision': 0, 'text': '开始调研'}
            self.assertEqual(403, request('POST', base + '/discuss', body, 'https://other.example')[0])
            self.assertEqual(202, request('POST', base + '/discuss', body)[0])
            self.wait()
            self.assertEqual(400, request('POST', base + '/discuss', body)[0])
            self.assertEqual('clarifying', request('GET', base)[1]['stage'])
        finally:
            server.shutdown(); server.server_close(); thread.join(5)

    def test_research_invokes_read_only_codex_and_requires_valid_source_evidence(self):
        proposal = {'goal': '更新数值', 'findings': ['找到数值实现'], 'questions': ['目标值？'],
            'users': [], 'scope': [], 'test_plan': [],
            'non_goals': [], 'acceptance': [], 'write_scope': [], 'steps': [], 'sources': ['flowerp/value.py']}
        calls = []
        def run(runner, command, prompt, timeout, on_line, started):
            calls.append(command)
            Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps(proposal), encoding='utf-8')
            on_line('{"type":"thread.started"}')
            return subprocess.CompletedProcess(command, 0, '', '')
        with patch.object(CodexExecutionRunner, 'capabilities', return_value={'codex_available': True}), \
             patch.object(CodexExecutionRunner, '_run_codex_streaming', run):
            folder = self.runtime / 'research-test'
            result = InitiativeResearch()(self.root, self.runtime, folder, {'request': '数值'}, lambda _: None)
            self.assertEqual('read-only', calls[0][calls[0].index('--sandbox') + 1])
            self.assertEqual(0, result['invocation']['returncode'])
            self.assertTrue((folder / 'events.jsonl').exists())
            proposal['questions'] = []
            proposal.update(write_scope=['flowerp/value.py'], acceptance=['值等于 2'], steps=['修改数值'])
            with self.assertRaisesRegex(ValueError, '使用者'):
                InitiativeResearch()(self.root, self.runtime, self.runtime / 'incomplete-prd', {}, lambda _: None)
            proposal.update(users=['使用者读取数值'], scope=['将数值更新为 2'],
                            test_plan=['调用读取入口，断言返回 2；检查其他数据不变'])
            ready = InitiativeResearch()(self.root, self.runtime, self.runtime / 'complete-prd', {}, lambda _: None)
            self.assertNotEqual(ready['proposal']['acceptance'], ready['proposal']['test_plan'])
            proposal['sources'] = ['not-real.py']
            with self.assertRaisesRegex(ValueError, '源码依据'):
                InitiativeResearch()(self.root, self.runtime, self.runtime / 'invalid-research', {}, lambda _: None)
