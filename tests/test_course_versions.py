import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess
from workbench.course_versions import REQUIRED_SESSION_LESSONS, default_session_version, session_versions_status


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
        responses = [CompletedProcess([], 1, '', 'missing'), CompletedProcess([], 0, '.git\n', ''),
                     CompletedProcess([], 1, '', '')]
        with patch('workbench.course_versions.subprocess.run', side_effect=responses):
            with self.assertRaisesRegex(ValueError, '本地缺少标签.*git fetch origin --tags.*不会退回旧版') as caught:
                default_session_version(self.root, 13)
        self.assertIn('L13', str(caught.exception))
        self.assertIn(self.item['ref'], str(caught.exception))
        with patch('workbench.course_versions.subprocess.run', return_value=CompletedProcess([], 0, 'b'*40, '')):
            with self.assertRaisesRegex(ValueError, '提交不一致.*不会退回旧版') as caught:
                default_session_version(self.root, 13)
        self.assertIn('期望 ' + 'a'*40, str(caught.exception))
        self.assertIn('实际 ' + 'b'*40, str(caught.exception))
        self.assertIn('不要强制覆盖标签', str(caught.exception))

    def test_other_lessons_keep_their_existing_contract(self):
        with patch('workbench.course_versions.subprocess.run') as process:
            self.assertIsNone(default_session_version(self.root,12))
            process.assert_not_called()

    def test_unavailable_git_has_an_actionable_error(self):
        with patch('workbench.course_versions.subprocess.run', side_effect=FileNotFoundError('git')):
            with self.assertRaisesRegex(ValueError, '确认 Git 已安装'):
                default_session_version(self.root, 13)

    def test_directory_without_repository_does_not_claim_a_tag_is_missing(self):
        with self.assertRaisesRegex(ValueError, '当前目录没有可用的 Git 仓库'):
            default_session_version(self.root, 13)

    def test_bad_catalog_is_reported_without_a_json_traceback(self):
        (self.root / 'docs/courses/session-versions.json').write_text('{', encoding='utf-8')
        result = session_versions_status(self.root, remote='origin')
        self.assertFalse(result['ready'])
        self.assertIn('课程版本目录无法读取', result['errors'][0])
        self.assertEqual('not_checked', result['remote']['status'])

    def test_missing_catalog_and_required_records_are_not_materials_ready(self):
        with patch('workbench.course_versions.subprocess.run', return_value=CompletedProcess([], 0, 'a'*40, '')):
            result = session_versions_status(self.root)
        self.assertFalse(result['ready'])
        self.assertEqual(10, len(result['errors']))
        (self.root / 'docs/courses/session-versions.json').unlink()
        self.assertFalse(session_versions_status(self.root)['ready'])
        self.assertIsNone(default_session_version(self.root, 13))


class CourseVersionGitTests(unittest.TestCase):
    """Exercise tag identity and remote inspection using isolated local Git repos."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repository'
        self.root.mkdir()
        self.git('init', '--quiet')
        (self.root / 'source.txt').write_text('initial\n', encoding='utf-8')
        self.git('add', 'source.txt')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'initial')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.items = {}
        for lesson in REQUIRED_SESSION_LESSONS:
            item = {'name': f'L{lesson:02d} fixture', 'ref': f'course/session/l{lesson:02d}-test', 'commit': self.commit}
            self.items[str(lesson)] = item
            self.git('tag', item['ref'], self.commit)
        catalog = self.root / 'docs/courses/session-versions.json'
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps({'schema_version': 1, 'lessons': self.items}), encoding='utf-8')

    def git(self, *arguments):
        return subprocess.check_output(['git', *arguments], cwd=self.root, encoding='utf-8', stderr=subprocess.PIPE)

    def add_remote(self):
        target = Path(self.tmp.name) / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(target)], check=True)
        self.git('remote', 'add', 'course-source', str(target))

    def test_local_checks_preserve_learner_files_and_refs_without_network(self):
        (self.root / 'source.txt').write_text('learner work\n', encoding='utf-8')
        before_status, before_refs = self.git('status', '--porcelain'), self.git('show-ref')
        result = session_versions_status(self.root)
        self.assertTrue(result['ready'])
        self.assertNotIn('remote', result)
        self.assertEqual(before_status, self.git('status', '--porcelain'))
        self.assertEqual(before_refs, self.git('show-ref'))
        self.assertEqual(self.items['13'], default_session_version(self.root, 13))

    def test_missing_tag_is_distinct_from_moved_tag(self):
        self.git('tag', '-d', self.items['13']['ref'])
        result = session_versions_status(self.root)
        self.assertEqual('missing_tag', next(item for item in result['versions'] if item['lesson'] == 13)['status'])
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '--quiet', '-m', 'second')
        second = self.git('rev-parse', 'HEAD').strip()
        self.git('tag', self.items['13']['ref'], second)
        with self.assertRaisesRegex(ValueError, '期望 ' + self.commit + '，实际 ' + second):
            default_session_version(self.root, 13)

    def test_remote_missing_tags_do_not_make_local_materials_missing(self):
        self.add_remote()
        before_refs = self.git('show-ref')
        result = session_versions_status(self.root, remote='course-source')
        self.assertTrue(result['ready'])
        self.assertFalse(result['remote']['ready'])
        self.assertEqual(11, len(result['remote']['missing_refs']))
        self.assertIn('由课程维护者发布', result['remote']['message'])
        self.assertEqual(before_refs, self.git('show-ref'))

    def test_remote_annotated_tags_use_peeled_commit_and_report_conflicts(self):
        self.add_remote()
        ref = self.items['13']['ref']
        self.git('tag', '-d', ref)
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'tag.gpgsign=false', 'tag', '-a', '-m', 'fixture', ref, self.commit)
        self.git('push', 'course-source', 'refs/tags/course/session/*:refs/tags/course/session/*')
        self.assertTrue(session_versions_status(self.root, remote='course-source')['remote']['ready'])
        self.items['13']['commit'] = 'f'*40
        catalog = self.root / 'docs/courses/session-versions.json'
        catalog.write_text(json.dumps({'schema_version': 1, 'lessons': self.items}), encoding='utf-8')
        remote = session_versions_status(self.root, remote='course-source')['remote']
        self.assertFalse(remote['ready'])
        self.assertFalse(remote['missing_refs'])
        self.assertEqual(self.commit, remote['mismatched_refs'][0]['actual_commit'])

    def test_failed_remote_query_is_not_reported_as_missing_tags(self):
        result = session_versions_status(self.root, remote='not-configured')
        self.assertEqual('remote_unavailable', result['remote']['status'])
        self.assertNotIn('missing_refs', result['remote'])
        self.assertTrue(result['ready'])

    def test_remote_timeout_preserves_local_result_and_does_not_claim_missing_tags(self):
        from workbench.course_versions import _git

        def query(root, *arguments):
            if arguments[0] == 'ls-remote':
                raise subprocess.TimeoutExpired(['git', *arguments], 30)
            return _git(root, *arguments)

        with patch('workbench.course_versions._git', side_effect=query):
            result = session_versions_status(self.root, remote='course-source')
        self.assertTrue(result['ready'])
        self.assertEqual('remote_unavailable', result['remote']['status'])
        self.assertNotIn('missing_refs', result['remote'])

    def test_tag_pointing_to_tree_is_not_reported_as_missing(self):
        self.git('tag', '-d', self.items['13']['ref'])
        self.git('tag', self.items['13']['ref'], self.git('rev-parse', 'HEAD^{tree}').strip())
        with self.assertRaisesRegex(ValueError, '标签存在但无法解析为提交'):
            default_session_version(self.root, 13)
