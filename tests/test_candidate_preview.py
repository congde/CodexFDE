import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from workbench.task_store import TaskStore
from workbench.candidate_preview import CandidatePreviews


class CandidatePreviewTests(unittest.TestCase):
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
