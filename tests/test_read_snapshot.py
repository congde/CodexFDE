"""Request-local reads remain fresh at every authorization boundary."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench.daily_delivery import manifest
from workbench.eval_harness import ReadSnapshot, fingerprint, report_view
from workbench.execution import CodexExecutionRunner
from workbench.initiative import InitiativeStore
from workbench.initiative_workflow import InitiativeWorkflow
from workbench.quality_hook import prepare
from workbench.task_store import TaskStore


class ReadSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.runtime = Path(self.temp.name) / 'runtime'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'value.txt').write_text('original')

    def test_successful_projection_reads_create_no_process_receipts(self):
        first = ReadSnapshot(self.runtime)
        expected = first.files(self.root)
        self.assertEqual(expected, first.files(str(self.root)))
        self.assertEqual(expected, ReadSnapshot(self.runtime).files(self.root))
        self.assertEqual([], list((self.runtime / 'git-processes').glob('*/process.json')))
        self.assertEqual(expected, manifest(self.root, self.runtime))
        self.assertEqual(1, len(list((self.runtime / 'git-processes').glob('*/process.json'))))

    def report(self):
        snapshot = manifest(self.root, self.runtime)
        path = self.runtime / 'report.json'
        path.write_text('projection fixture, not a delivery report')
        return {'summary': {'decision': 'pass'}, 'runner': {'workspace': str(self.root),
            'candidate_sha256': fingerprint(snapshot), 'report_path': str(path)},
            'report_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_next_read_and_acceptance_gate_detect_changes_without_using_old_snapshot(self):
        report = self.report()
        reader = ReadSnapshot(self.runtime)
        self.assertEqual('current', report_view(report, self.root, self.runtime, snapshot=reader)['freshness'])
        (self.root / 'value.txt').write_text('changed')
        self.assertEqual('stale', report_view(report, self.root, self.runtime,
                                            snapshot=ReadSnapshot(self.runtime))['freshness'])
        self.assertEqual('stale', report_view(report, self.root, self.runtime)['freshness'])

    def test_failed_read_is_shared_only_within_the_request(self):
        loader = unittest.mock.Mock(side_effect=OSError('source unavailable'))
        reader = ReadSnapshot(self.runtime, loader=loader)
        for _ in range(3):
            with self.assertRaisesRegex(OSError, 'source unavailable'):
                reader.files(self.root)
        self.assertEqual(1, loader.call_count)
        self.assertEqual({'value.txt'}, set(ReadSnapshot(self.runtime).files(self.root)))

    def test_failed_git_read_retains_diagnostic_receipt(self):
        failure = subprocess.CompletedProcess(['git'], 1, stdout=b'', stderr='git failed fixture')
        with patch.object(CodexExecutionRunner, '_run_codex_streaming', return_value=failure):
            with self.assertRaises(subprocess.CalledProcessError):
                ReadSnapshot(self.runtime).files(self.root)
        receipts = list((self.runtime / 'git-processes').glob('*/process.json'))
        self.assertEqual(1, len(receipts))
        receipt = json.loads(receipts[0].read_text(encoding='utf-8'))
        self.assertFalse(receipt['success'])
        self.assertEqual(1, receipt['returncode'])

    def test_workflow_report_and_hook_history_share_one_candidate_read(self):
        tasks = TaskStore(self.runtime / 'workbench.db')
        items = InitiativeStore(tasks.path)
        item = items.create({'title': 'projection fixture', 'raw_signal': 'projection fixture',
                             'source': 'test', 'project_id': 'FIXTURE'}, 'owner')
        workflow = InitiativeWorkflow(self.root, self.runtime, items, tasks)
        self.addCleanup(workflow.learning_generation.close)
        report = self.report()
        task = tasks.create('projection fixture')
        with tasks.connect() as db:
            db.execute('UPDATE tasks SET result_json=? WHERE id=?', (json.dumps(report), task['id']))
        data = workflow._load(item['id'])
        data.update(stage='confirmed', workspace=str(self.root), active_task_id=task['id'],
                    research_manifest=manifest(self.root, self.runtime),
                    plan={'source_manifest': manifest(self.root, self.runtime)})
        package = prepare(self.runtime, self.root, {'id': 'FIXTURE', 'root_path': str(self.root.parent / 'registered'),
                          'eval_command': [sys.executable, 'fixture_eval.py']},
                          task['id'], item['id'], 'owner')
        for index in range(3):
            (Path(package['path']) / ('run-' + str(index) + '.json')).write_text(json.dumps({'report': report}), encoding='utf-8')
        data['hook_package'] = package
        workflow._save(data)
        before = len(list((self.runtime / 'git-processes').glob('*/process.json')))
        with patch('workbench.initiative_workflow.manifest', wraps=manifest) as source_read:
            for _ in range(2):
                result = workflow.get(item['id'])
                self.assertEqual('current', result['source_check']['status'])
                self.assertEqual('current', result['eval_harness']['freshness'])
                self.assertEqual(['current'] * 3, [run['freshness'] for run in result['quality_hook']['runs']])
            self.assertEqual(2, source_read.call_count)
        self.assertEqual(before, len(list((self.runtime / 'git-processes').glob('*/process.json'))))


if __name__ == '__main__':
    unittest.main()
