import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess
from workbench.course_versions import default_session_version


class CourseVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root / 'docs/courses'
        folder.mkdir(parents=True)
        self.item = {'name': '课堂版本', 'ref': 'course/session/l13-test', 'commit': 'a'*40}
        (folder / 'session-versions.json').write_text(json.dumps({'schema_version':1,'lessons':{'13':self.item}}), encoding='utf-8')

    def test_named_version_resolves_to_exact_pinned_commit(self):
        with patch('workbench.course_versions.subprocess.run', return_value=CompletedProcess([],0,'a'*40+'\n','')):
            self.assertEqual(self.item, default_session_version(self.root,13))

    def test_missing_or_moved_version_never_falls_back_to_old_material(self):
        for result in [CompletedProcess([],1,'','missing'), CompletedProcess([],0,'b'*40,'')]:
            with self.subTest(result=result), patch('workbench.course_versions.subprocess.run', return_value=result):
                with self.assertRaisesRegex(ValueError,'不会退回旧版'):
                    default_session_version(self.root,13)

    def test_other_lessons_keep_their_existing_contract(self):
        with patch('workbench.course_versions.subprocess.run') as process:
            self.assertIsNone(default_session_version(self.root,12))
            process.assert_not_called()
