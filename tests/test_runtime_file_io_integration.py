"""Read-lock recovery must preserve current evidence and reject real changes."""
from contextlib import contextmanager
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from workbench import ci_evidence, file_io, quality_hook, workbench_backup
from workbench.task_store import TaskStore
from workbench.reference_paths import reference_mapping_scope

REAL_SLEEP = time.sleep


@contextmanager
def transient_read_lock(path, replacement=None):
    original = Path.open
    attempts = []

    def opened(target, *args, **kwargs):
        mode = args[0] if args else kwargs.get('mode', 'r')
        if target == path and mode in {'r', 'rt', 'rb'}:
            attempts.append(mode)
            if len(attempts) == 1:
                if replacement is not None:
                    with original(target, 'wb') as output:
                        output.write(replacement)
                raise PermissionError(errno.EACCES, 'temporary evidence sharing lock')
        return original(target, *args, **kwargs)

    with patch.object(file_io, '_WINDOWS', True), patch.object(Path, 'open', opened), \
            patch.object(file_io.time, 'sleep', wraps=REAL_SLEEP):
        yield attempts


class RuntimeReadIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='runtime-read-integration-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def v0_contract(self):
        spec = self.root / 'SPEC.md'
        text = '\n\n'.join('## ' + title + '\n' + content for title, content in (
            ('来源', '隔离故障测试'), ('目标', '检查当前合同'), ('非目标', '不改客户'),
            ('约束', '只复验'), ('验收用例', '读取成功'), ('完成定义', '仍待真人审核')))
        spec.write_text(text, encoding='utf-8')
        store = TaskStore(self.root / 'runtime' / 'workbench.db')
        return store, spec, text

    def submit_v0(self, store, spec):
        return store.create_v0('隔离 Spec 读取故障', spec_path=str(spec), actor='fixture',
                               workspace_path=str(self.root), execution_timeout_seconds=30)

    def test_v0_freezes_current_spec_after_temporary_read_lock(self):
        store, spec, text = self.v0_contract()
        changed = text.replace('检查当前合同', '检查重读后的合同')
        with transient_read_lock(spec, changed.encode('utf-8')) as attempts:
            task = self.submit_v0(store, spec)
        self.assertEqual(2, len(attempts))
        self.assertEqual(changed, task['spec_text'])
        self.assertEqual(hashlib.sha256(changed.encode('utf-8')).hexdigest(), task['spec_sha256'])
        self.assertEqual('检查重读后的合同', task['spec']['goal'])

    def test_v0_rejects_invalid_spec_after_read_lock_without_creating_task(self):
        store, spec, _ = self.v0_contract()
        with transient_read_lock(spec, b'changed invalid contract') as attempts:
            with self.assertRaisesRegex(ValueError, 'Spec 缺少'):
                self.submit_v0(store, spec)
        self.assertEqual(2, len(attempts))
        self.assertEqual([], store.list())

    def test_v0_persistent_read_denial_is_bounded_and_creates_no_task(self):
        store, spec, _ = self.v0_contract()
        original, attempts = Path.open, []

        def denied(target, mode='r', *args, **kwargs):
            if target == spec and mode in {'r', 'rt', 'rb'}:
                attempts.append(mode)
                error = PermissionError(errno.EACCES, 'persistent Spec sharing denial')
                error.winerror = 32
                raise error
            return original(target, mode, *args, **kwargs)

        with patch.object(file_io, '_WINDOWS', True), patch.object(Path, 'open', denied), \
                patch.object(file_io.time, 'sleep', wraps=REAL_SLEEP):
            with self.assertRaisesRegex(ValueError, 'Spec 无法读取'):
                self.submit_v0(store, spec)
        self.assertEqual(6, len(attempts))
        self.assertEqual([], store.list())

    def test_v0_explicit_spec_is_not_redirected_by_another_runtime_map(self):
        store, spec, text = self.v0_contract()
        other = self.root / 'OTHER.md'
        other.write_text(text.replace('检查当前合同', '无关迁移合同'), encoding='utf-8')
        with reference_mapping_scope(self.root / 'other-runtime', {str(spec): str(other)}):
            task = self.submit_v0(store, spec)
        self.assertEqual(text, task['spec_text'])
        self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(), task['spec_sha256'])

    def stock_module(self):
        source = Path(__file__).resolve().parents[1] / 'docs/courses/L06/examples/stock_practice.py'
        spec = importlib.util.spec_from_file_location('stock_read_integration', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_ci_envelope_hashes_changed_disk_report_after_temporary_lock(self):
        report = self.root / 'report.json'
        report.write_text('{"suite":"blocking","summary":{"decision":"pass"}}', encoding='utf-8')
        changed = b'{"suite":"blocking","summary":{"decision":"block"}}'
        with transient_read_lock(report, changed) as attempts:
            envelope = ci_evidence.build_envelope(report, env={'GITHUB_SHA': 'abc', 'GITHUB_RUN_ID': '1'})
        self.assertEqual(2, len(attempts))
        self.assertEqual('block', envelope['report_decision'])
        self.assertEqual(hashlib.sha256(changed).hexdigest(), envelope['report_sha256'])

    def test_persistent_ci_report_denial_does_not_publish_an_envelope(self):
        report, output = self.root / 'report.json', self.root / 'envelope.json'
        report.write_text('{}', encoding='utf-8')
        original = Path.open

        def denied(target, *args, **kwargs):
            if target == report:
                raise PermissionError(errno.EACCES, 'persistent evidence denial')
            return original(target, *args, **kwargs)

        arguments = ['ci-evidence', '--report', str(report), '--output', str(output)]
        with patch.object(file_io, '_WINDOWS', True), patch.object(Path, 'open', denied), \
                patch.object(file_io.time, 'sleep', wraps=REAL_SLEEP), patch.object(sys, 'argv', arguments), \
                patch.dict(os.environ, {'GITHUB_SHA': 'abc', 'GITHUB_RUN_ID': '1'}):
            with self.assertRaises(PermissionError):
                ci_evidence.main()
        self.assertFalse(output.exists())

    def test_hook_retry_does_not_hide_binding_tampering(self):
        candidate = self.root / 'candidate'
        (candidate / '.git').mkdir(parents=True)
        project = {'id': 'PROJECT-fixture', 'root_path': str(self.root / 'source'),
                   'eval_command': [sys.executable, '-B', 'fixture_eval.py']}
        package = quality_hook.prepare(self.root / 'runtime', candidate, project,
                                       'TASK-fixture', 'INIT-fixture', 'fixture-preparer')
        binding = Path(package['path']) / 'binding.json'
        with transient_read_lock(binding) as attempts:
            view = quality_hook.view(package, candidate, 'TASK-fixture')
        self.assertEqual('prepared', view['status'])
        self.assertGreaterEqual(len(attempts), 2)
        binding.write_text('{}', encoding='utf-8')
        self.assertEqual('unavailable', quality_hook.view(package, candidate, 'TASK-fixture')['status'])

    def test_hook_preparation_rejects_files_changed_during_read_retry(self):
        for filename in ('binding.json', 'quality_gate.py', 'hooks.json'):
            with self.subTest(file=filename):
                candidate = self.root / filename / 'candidate'
                (candidate / '.git').mkdir(parents=True)
                project = {'id': 'PROJECT-fixture', 'root_path': str(self.root / 'source'),
                           'eval_command': [sys.executable, '-B', 'fixture_eval.py']}
                original = Path.open
                altered = []

                def changed(target, *args, **kwargs):
                    mode = args[0] if args else kwargs.get('mode', 'r')
                    if target.name == filename and mode == 'rb' and not altered:
                        altered.append(target)
                        with original(target, 'wb') as output:
                            output.write(b'{}')
                        raise PermissionError(errno.EACCES, 'sharing lock during tampering')
                    return original(target, *args, **kwargs)

                with patch.object(file_io, '_WINDOWS', True), patch.object(Path, 'open', changed), \
                        patch.object(file_io.time, 'sleep', wraps=REAL_SLEEP):
                    with self.assertRaisesRegex(ValueError, '准备期间'):
                        quality_hook.prepare(self.root / filename / 'runtime', candidate, project,
                                             'TASK-fixture', 'INIT-fixture', 'fixture-preparer')
                self.assertEqual(1, len(altered))

    def test_backup_hash_reads_in_bounded_chunks(self):
        evidence = self.root / 'large-evidence.bin'
        content = b'evidence-block' * 180000
        evidence.write_bytes(content)
        original = workbench_backup.open_read
        reads = []
        case = self

        class BoundedReader:
            def __init__(self, source):
                self.source = source

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.source.close()

            def read(self, size):
                case.assertGreater(size, 0)
                case.assertLessEqual(size, 1024 * 1024)
                reads.append(size)
                return self.source.read(size)

        def streamed(path, mode='rb'):
            return BoundedReader(original(path, mode))

        with patch.object(workbench_backup, 'open_read', streamed):
            actual = workbench_backup._hash(evidence)
        self.assertEqual(hashlib.sha256(content).hexdigest(), actual)
        self.assertGreater(len(reads), 2)

    def test_backup_verification_recovers_a_temporary_archive_lock(self):
        runtime = self.root / 'runtime'
        TaskStore(runtime / 'workbench.db')
        report = runtime / 'reports' / 'evidence.json'
        report.parent.mkdir()
        report.write_text('{"source":"isolated fixture"}', encoding='utf-8')
        result = workbench_backup.BackupService(runtime).create('fixture-maintainer')
        archive = Path(result['archive_path'])
        with transient_read_lock(archive) as attempts:
            verified = workbench_backup.verify_backup(archive)
        self.assertTrue(verified['ok'])
        self.assertEqual(2, len(attempts))
        self.assertEqual(2, verified['files_verified'])

    def test_stock_hash_uses_current_bytes_and_preserves_newline_policy(self):
        stock = self.stock_module()
        source = self.root / 'service.py'
        source.write_bytes(b'value = 1\r\n')
        changed = b'value = 2\r\n'
        with transient_read_lock(source, changed) as attempts:
            digest = stock.sha(source)
        self.assertEqual(2, len(attempts))
        self.assertEqual(hashlib.sha256(changed.replace(b'\r\n', b'\n')).hexdigest(), digest)
        self.assertNotEqual(hashlib.sha256(b'value = 1\n').hexdigest(), digest)

    def test_stock_session_read_recovers_lock_and_keeps_bom_support(self):
        stock = self.stock_module()
        session = self.root / 'session.json'
        value = {'candidate': str(self.root / 'candidate'), 'scope': 'isolated fixture'}
        session.write_text(json.dumps(value), encoding='utf-8-sig')
        with transient_read_lock(session) as attempts:
            actual = stock.read(session)
        self.assertEqual(2, len(attempts))
        self.assertEqual(value, actual)


if __name__ == '__main__':
    unittest.main()
