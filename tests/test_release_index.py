import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from workbench.release_index import create_release_index
from workbench.task_store import TaskStore


class ReleaseIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'workbench.db')
        self.spec = self.root / 'spec.md'
        self.spec.write_text('本次测试需求', encoding='utf-8')
        self.task = self.store.create('仅用于索引测试', spec_path=str(self.spec))

    def read(self, result):
        # Windows can briefly deny a newly closed temporary file. Never retry
        # malformed JSON or suppress a persistent permissions failure.
        for attempt in range(30):
            try:
                return json.loads(Path(result['path']).read_text(encoding='utf-8'))
            except PermissionError:
                if attempt == 29:
                    raise
                time.sleep(.1)

    def test_draft_lists_gaps_without_accepting_or_overwriting(self):
        first = create_release_index(self.root, self.task['id'])
        second = create_release_index(self.root, self.task['id'])
        self.assertNotEqual(first['path'], second['path'])
        value = self.read(first)
        self.assertIn('human_acceptance', value['evidence_gaps'])
        self.assertIn('cold_start', value['evidence_gaps'])
        self.assertEqual(value['index_status'], 'requires_human_review')
        self.assertEqual(self.store.get(self.task['id'])['status'], 'queued')
        spec = value['references']['spec']
        self.assertEqual((Path(first['path']).parent / spec['path']).resolve(), self.spec)
        self.assertEqual(spec['sha256'], hashlib.sha256(self.spec.read_bytes()).hexdigest())

    def test_tampered_spec_and_report_are_visible_not_silent_success(self):
        self.store.append_event(self.task['id'], '本次需求 Spec 已冻结', evidence={'sha256':'0'*64})
        report = self.root / 'pre.json'
        report.write_text(json.dumps({'summary':{'decision':'pass'}}),encoding='utf-8')
        self.store.append_event(self.task['id'], '执行前课程 Eval 已完成',
            evidence={'summary':{'decision':'block'},'runner':{'report_path':str(report)}})
        value = self.read(create_release_index(self.root, self.task['id']))
        self.assertIn('spec_changed_after_freeze', value['evidence_gaps'])
        self.assertIn('pre_eval_summary_mismatch', value['evidence_gaps'])

    def test_external_attachment_is_not_read_or_exported(self):
        with tempfile.TemporaryDirectory() as external:
            path = Path(external) / 'external.md'
            path.write_text('不属于这次交付的材料', encoding='utf-8')
            with self.assertRaises(ValueError):
                create_release_index(self.root, self.task['id'], risks=path)
        self.assertFalse((self.root / 'release-index').exists())

    def test_diff_is_labelled_excerpt_and_does_not_imply_implementation(self):
        self.store.append_event(self.task['id'], '受控执行阶段完成',
            evidence={'changed_files':['flowerp/example.py'],'diff':'a partial recorded diff'})
        result = create_release_index(self.root, self.task['id'])
        value = self.read(result)
        ref = value['references']['diff_excerpt']
        self.assertEqual(ref['path'], 'diff-excerpt.txt')
        self.assertIn('verified_implementation', value['evidence_gaps'])
