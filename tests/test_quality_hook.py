"""Synthetic Hook fixtures; manual inputs never claim host event evidence."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from workbench import quality_hook as hook
from workbench.file_io import read_text


class QualityHookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.candidate = self.root / 'candidate with spaces'
        self.candidate.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.candidate)], check=True)
        self.runtime = self.root / 'runtime'
        self.project = {'id': 'PROJECT-test', 'root_path': str(self.root / 'product'),
                        'eval_command': [sys.executable, 'fixture_eval.py']}
        self.package = hook.prepare(self.runtime, self.candidate, self.project, 'TASK-test', 'INIT-test', 'tester')
        self.folder = Path(self.package['path'])
        self.binding = json.loads(read_text(self.folder / 'binding.json'))

    def event(self, **kw):
        return dict(hook_event_name='Stop', cwd=str(self.candidate), stop_hook_active=False, **kw)

    def install_fixture(self):
        destination = self.candidate / '.codex/hooks'
        destination.mkdir(parents=True)
        shutil.copyfile(self.folder / 'quality_gate.py', destination / 'quality_gate.py')
        shutil.copyfile(self.folder / 'hooks.json', destination.parent / 'hooks.json')

    def test_preparation_does_not_install_or_change_source_and_is_append_only(self):
        self.assertFalse((self.candidate / '.codex').exists())
        self.assertEqual(self.project['eval_command'], self.binding['command'])
        other = hook.prepare(self.runtime, self.candidate, self.project, 'TASK-test', 'INIT-test', 'tester')
        self.assertNotEqual(self.package['path'], other['path'])
        self.assertTrue((self.folder / 'binding.json').is_file())
        self.assertEqual('prepared', hook.view(self.package, self.candidate, 'TASK-test')['status'])
        with self.assertRaisesRegex(ValueError, '正式源码'):
            hook.prepare(self.runtime, self.candidate, dict(self.project, root_path=str(self.candidate)), 'TASK-test', 'INIT-test', 'tester')

    def test_install_match_does_not_claim_trust_and_preserves_other_rules(self):
        self.install_fixture()
        config_path = self.candidate / '.codex/hooks.json'
        config = json.loads(read_text(config_path))
        config['hooks']['Stop'].append({'hooks': [{'type': 'command', 'command': 'another rule'}]})
        hook.write_json(config_path, config)
        view = hook.view(self.package, self.candidate, 'TASK-test')
        self.assertEqual('installed', view['status'])
        self.assertEqual('unknown', view['trust'])
        self.assertEqual('stale', hook.view(self.package, self.candidate, 'TASK-other')['status'])
        (self.candidate / '.codex/hooks/quality_gate.py').write_text('changed')
        self.assertEqual('different', hook.view(self.package, self.candidate, 'TASK-test')['status'])
        (self.folder / 'binding.json').write_text('{}')
        self.assertEqual('unavailable', hook.view(self.package, self.candidate, 'TASK-test')['status'])

    def test_protocol_branches_reentry_and_wrong_candidate(self):
        for passed in (True, False):
            factory = Mock(return_value=Mock(return_value={'summary': {'decision': 'pass' if passed else 'block'}}))
            response, record = hook.handle(self.binding, self.event(), runner_factory=factory)
            self.assertEqual(passed, response.get('continue', False))
            self.assertFalse(record['host_event_verified'])
            self.assertEqual(self.candidate, factory.call_args.args[0])
            self.assertEqual(100, factory.call_args.kwargs['timeout'])
        factory = Mock()
        response, record = hook.handle(self.binding, dict(self.event(), stop_hook_active=True), runner_factory=factory)
        self.assertTrue(response['continue'])
        self.assertEqual('skipped', record['outcome'])
        factory.assert_not_called()
        for event in (None, [], dict(self.event(), cwd=str(self.root)), dict(self.event(), stop_hook_active='false')):
            response, record = hook.handle(self.binding, event, runner_factory=factory)
            self.assertEqual('block', response['decision'])
            self.assertEqual('unverified', record['outcome'])
        factory.assert_not_called()
        for error in (FileNotFoundError('python missing'), subprocess.TimeoutExpired('check', 1)):
            response, record = hook.handle(self.binding, self.event(), runner_factory=Mock(side_effect=error))
            self.assertEqual('block', response['decision'])
            self.assertEqual('unverified', record['outcome'])

    def write_check(self, passed, exit_code=None):
        report = {'summary': {'total': 1, 'passed': int(passed), 'blocking_failed': int(not passed),
                             'observing_failed': 0, 'decision': 'pass' if passed else 'block'},
                  'results': [{'name': 'independent-product', 'level': 'blocking', 'passed': passed}]}
        (self.candidate / 'fixture_eval.py').write_text(
            'import json,sys\nprint(json.dumps(' + repr(report) + '))\nsys.exit(' + str(int(not passed) if exit_code is None else exit_code) + ')\n', encoding='utf-8')

    def test_real_generated_handler_runs_candidate_and_keeps_red_green_records(self):
        self.install_fixture()
        script = self.candidate / '.codex/hooks/quality_gate.py'
        for passed in (False, True):
            self.write_check(passed)
            result = subprocess.run([sys.executable, '-X', 'utf8', str(script)], input=json.dumps(self.event()),
                text=True, capture_output=True, encoding='utf-8', cwd=self.root, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            response = json.loads(result.stdout)
            self.assertEqual(passed, response.get('continue', False))
        view = hook.view(self.package, self.candidate, 'TASK-test')
        self.assertEqual(['pass', 'block'], [r['outcome'] for r in view['runs']])
        self.assertEqual(['current', 'stale'], [r['freshness'] for r in view['runs']])
        self.assertTrue(all(not r['host_event_verified'] for r in view['runs']))

    def test_false_green_malformed_json_and_binding_tamper_fail_closed(self):
        self.write_check(True, exit_code=1)
        response, record = hook.handle(self.binding, self.event())
        self.assertEqual('unverified', record['outcome'])
        self.assertEqual('block', response['decision'])
        for raw in ('{broken', json.dumps(self.event())):
            if raw.startswith('{"'):
                (self.folder / 'binding.json').write_text('{}')
            output = io.StringIO()
            with patch('sys.stdin', io.StringIO(raw)), patch('sys.stdout', output):
                code = hook.main(self.folder / 'binding.json', self.package['binding_sha256'])
            self.assertEqual(0, code)
            self.assertEqual('block', json.loads(output.getvalue())['decision'])

    def test_real_timeout_returns_unverified(self):
        (self.candidate / 'fixture_eval.py').write_text('import time\ntime.sleep(30)\n')
        binding = dict(self.binding, timeout=0.1)
        response, record = hook.handle(binding, self.event())
        self.assertEqual('block', response['decision'])
        self.assertEqual('unverified', record['outcome'])
        receipts = list((self.runtime / 'project-reports/TASK-test').glob('*/process.json'))
        self.assertEqual(1, len(receipts))
        self.assertNotEqual(0, json.loads(read_text(receipts[0]))['returncode'])
