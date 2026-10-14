import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from workbench.project_registration import ProjectRegistration
from workbench.project_store import ProjectStore
from workbench.workbench_server import WorkbenchApp, make_handler


class ProjectRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / 'runtime'
        self.store = ProjectStore(self.runtime / 'registry.db')
        self.service = ProjectRegistration(self.store)

    def body(self, root, **extra):
        return {'name': '本地项目', 'root_path': str(root), 'source_type': 'local', **extra}

    def test_data_directory_roots_are_rejected_before_git_and_registration(self):
        plain = self.runtime / 'plain'
        plain.mkdir()
        original = plain / 'code.txt'
        original.write_bytes(b'keep original fixture source')
        existing = self.runtime / 'existing'
        (existing / '.git').mkdir(parents=True)
        for root in (self.runtime, plain, existing):
            with self.subTest(root=root.name), patch.object(self.service, 'git') as git:
                with self.assertRaisesRegex(ValueError, '工作台数据目录'):
                    self.service.add(self.body(root, initialize_git=True, make_default=True))
                with self.assertRaisesRegex(ValueError, '工作台数据目录'):
                    self.store.create('direct registration', root, [], allow_pending_eval=True)
                git.assert_not_called()
        self.assertFalse((self.runtime / '.git').exists())
        self.assertFalse((plain / '.git').exists())
        self.assertTrue((existing / '.git').is_dir())
        self.assertEqual(b'keep original fixture source', original.read_bytes())
        self.assertEqual([], self.store.list())
        self.assertIsNone(self.store.default())

    def test_internal_clone_destination_is_rejected_without_mkdir_or_git(self):
        destination = self.runtime / 'not-created' / 'clone'
        with patch.object(self.service, 'git') as git:
            with self.assertRaisesRegex(ValueError, '工作台数据目录'):
                self.service.add(self.body(destination, source_type='git',
                    git_url='https://example.org/project.git'))
            git.assert_not_called()
        self.assertFalse(destination.parent.exists())
        self.assertEqual([], self.store.list())

    def test_resolved_link_into_data_directory_is_rejected(self):
        target = self.runtime / 'linked-source'
        target.mkdir()
        original = target / 'code.txt'
        original.write_bytes(b'keep linked fixture source')
        alias = self.root / 'external-link'
        try:
            alias.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest('directory symlink unavailable: ' + str(error))
        self.addCleanup(alias.unlink)
        with patch.object(self.service, 'git') as git:
            with self.assertRaisesRegex(ValueError, '工作台数据目录'):
                self.service.add(self.body(alias, initialize_git=True))
            with self.assertRaisesRegex(ValueError, '工作台数据目录'):
                self.store.create('direct alias', alias, [], allow_pending_eval=True)
            git.assert_not_called()
        self.assertFalse((target / '.git').exists())
        self.assertEqual(b'keep linked fixture source', original.read_bytes())
        self.assertEqual([], self.store.list())

    def test_sibling_and_source_parent_of_data_directory_are_allowed(self):
        sibling = self.root / 'runtime-copy'
        sibling.mkdir()
        original = sibling / 'code.txt'
        original.write_bytes(b'keep external fixture source')
        for root in (sibling, self.root):
            with self.subTest(root=root.name):
                project = self.service.add(self.body(root, initialize_git=True))
                self.assertEqual(str(root.resolve()), project['root_path'])
                self.assertEqual([], project['eval_command'])
        self.assertEqual(b'keep external fixture source', original.read_bytes())
        self.assertEqual(2, len(self.store.list()))
        self.assertIsNone(self.store.default())

    def test_plain_directory_initializes_without_committing_or_changing_files(self):
        folder = self.root / 'local';folder.mkdir()
        file = folder / 'code.txt';file.write_text('keep my work')
        with self.assertRaisesRegex(ValueError, '初始化'):
            self.service.add(self.body(folder))
        result = self.service.add(self.body(folder, initialize_git=True, make_default=True))
        self.assertEqual([], result['eval_command'])
        self.assertEqual(result, self.store.default())
        self.assertEqual('keep my work', file.read_text())
        self.assertNotEqual(0, subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=folder, capture_output=True).returncode)
        with self.assertRaisesRegex(ValueError, '已添加'):
            self.service.add(self.body(folder))
        configured = self.service.configure(result['id'], {'eval_command': [sys.executable, 'fixture_eval.py']})
        self.assertEqual([sys.executable, 'fixture_eval.py'], configured['eval_command'])
        self.assertEqual(result['root_path'], configured['root_path'])

    def test_git_subdirectory_is_not_silently_registered_or_reinitialized(self):
        folder = self.root / 'parent';folder.mkdir()
        subprocess.run(['git', 'init', '-q', str(folder)], check=True)
        nested = folder / 'nested';nested.mkdir()
        with self.assertRaisesRegex(ValueError, '仓库根目录'):
            self.service.add(self.body(nested, initialize_git=True))
        self.assertFalse((nested / '.git').exists())

    def test_git_clone_registers_real_checkout_and_never_overwrites_destination(self):
        source = self.root / 'remote';source.mkdir()
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        (source / 'README.md').write_text('source project')
        subprocess.run(['git', 'add', '.'], cwd=source, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
                        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture'], cwd=source, check=True)
        original = self.service.git
        def git(args, **kwargs):
            if args[0] == 'clone':
                # Replace transport only; exercise an actual Git clone and root check.
                subprocess.run(['git', 'clone', '--quiet', str(source), args[-1]], check=True)
                return ''
            return original(args, **kwargs)
        target = self.root / 'clone'
        with patch.object(self.service, 'git', side_effect=git):
            result = self.service.add(self.body(target, source_type='git', git_url='https://example.org/project.git'))
        self.assertEqual(str(target), result['root_path'])
        self.assertEqual('source project', (target / 'README.md').read_text())
        occupied = self.root / 'occupied';occupied.mkdir()
        with self.assertRaisesRegex(ValueError, '已存在'):
            self.service.add(self.body(occupied, source_type='git', git_url='https://example.org/project.git'))
        self.assertEqual(1, len(self.store.list()))

    def test_invalid_remote_and_failed_clone_do_not_register_a_project(self):
        for remote in ('--upload-pack=evil', 'file:///local', 'ext::run', 'https://token@example.org/repo',
                       'https://example.org/repo?token=secret', 'ssh://root@example.org/repo'):
            with self.assertRaises(ValueError):
                self.service.add(self.body(self.root / 'bad', source_type='git', git_url=remote))
        with patch.object(self.service, 'git', side_effect=ValueError('克隆失败')):
            with self.assertRaisesRegex(ValueError, '克隆失败'):
                self.service.add(self.body(self.root / 'failed', source_type='git', git_url='git@example.org:repo.git'))
        self.assertEqual([], self.store.list())

    def test_http_add_configure_default_and_origin_boundary(self):
        app = WorkbenchApp(self.root / 'runtime')
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
        client = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        self.addCleanup(client.close)
        def request(path, body, origin=None):
            headers = {'Content-Type': 'application/json'}
            if origin: headers['Origin'] = origin
            client.request('POST', path, json.dumps({'actor': 'fixture-user', **body}), headers)
            response = client.getresponse();return response.status, json.loads(response.read())
        folder = self.root / 'api-project';folder.mkdir()
        body = self.body(folder, initialize_git=True, make_default=True)
        status, rejected = request('/api/v1/projects', body, 'https://other.example')
        self.assertEqual(403, status)
        self.assertEqual('cross_origin', rejected['error'])
        self.assertFalse(rejected['result_unknown'])
        self.assertFalse((folder / '.git').exists())
        status, project = request('/api/v1/projects', body)
        self.assertEqual(201, status)
        self.assertEqual(project['id'], app.default_project)
        self.assertEqual([], project['eval_command'])
        status, configured = request('/api/v1/projects/'+project['id']+'/settings', {'eval_command': [sys.executable, 'fixture_eval.py']})
        self.assertEqual(200, status)
        self.assertEqual([sys.executable, 'fixture_eval.py'], configured['eval_command'])
        self.assertEqual(project['id'], WorkbenchApp(app.runtime).default_project)


if __name__ == '__main__':
    unittest.main()
