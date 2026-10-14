from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workbench.migration import (FAILED_MARKER, assert_migration_preflight,
    fresh_migration_preflight, prepare_migration, restore_migration, validate_migration_environment)
from workbench.file_io import open_read
from workbench.project_store import ProjectStore
from workbench.reference_paths import (load_reference_mappings, reference_mapping_scope,
    resolve_reference, unload_reference_mappings, validate_reference_mappings)
from workbench.task_store import TaskStore
from workbench.workbench_backup import BackupService, RESTORE_MARKER, restore_backup, verify_backup


class WorkbenchMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / 'runtime-a'
        self.runtimes = [self.runtime]
        self.tasks = TaskStore(self.runtime / 'workbench.db')
        task = self.tasks.create('retain actual fixture failure', task_id='TASK-ABCD123456')
        self.tasks.transition(task['id'], 'failed', 'fixture failure retained', actor='fixture-user')
        self.report = self.runtime / 'reports' / 'failure.json'
        self.report.parent.mkdir()
        self.report.write_bytes(b'{"decision":"fail","note":"original evidence"}')
        self.workspace = self.runtime / 'daily-delivery' / 'candidate' / 'workspace'
        self.workspace.mkdir(parents=True)
        (self.workspace / 'value.py').write_bytes(b'value = 1\n')
        self.tasks.append_event(task['id'], 'original absolute references', evidence={
            'report_path': str(self.report), 'report_sha256': hashlib.sha256(self.report.read_bytes()).hexdigest(),
            'workspace': str(self.workspace)})
        self.project = self.root / 'project-a'
        self.project.mkdir()
        subprocess.run(['git', 'init', '--quiet', str(self.project)], check=True, capture_output=True)
        (self.project / 'value.py').write_bytes(b'value = 1\n')
        (self.project / 'fixture_eval.py').write_text('print("fixture only")\n', encoding='utf-8')
        self.projects = ProjectStore(self.runtime / 'workbench.db')
        self.project_id = self.projects.create('migration fixture', self.project,
            [sys.executable, '-B', 'fixture_eval.py'], project_id='PROJECT-MIGRATION')['id']

    def tearDown(self):
        for runtime in self.runtimes:
            unload_reference_mappings(runtime)
        self.temp.cleanup()

    def archive(self, runtime=None, projects=None):
        result = prepare_migration(runtime or self.runtime, projects or self.projects, 'fixture-user')
        archive = self.root / (result['id'] + '.zip')
        shutil.copyfile(result['archive_path'], archive)
        self.assertEqual('/api/v1/backups/' + result['id'] + '/download', result['download_url'])
        return archive

    def migrated(self, *, command=None):
        if command:
            self.projects.configure(self.project_id, command)
        archive = self.archive()
        project = self.root / 'project-b'
        shutil.copytree(self.project, project)
        runtime = self.root / 'runtime-b'
        self.runtimes.append(runtime)
        result = restore_migration(archive, runtime, {str(self.project): str(project)})
        return archive, runtime, project, result

    def test_relocation_preserves_archive_database_and_evidence_bytes(self):
        archive, runtime, project, result = self.migrated()
        with open_read(archive, 'rb') as stream, zipfile.ZipFile(stream) as bundle:
            self.assertEqual(bundle.read('payload/workbench.db'), (runtime / 'workbench.db').read_bytes())
            self.assertEqual(bundle.read('payload/reports/failure.json'), (runtime / 'reports/failure.json').read_bytes())
            self.assertEqual(bundle.read('payload/daily-delivery/candidate/workspace/value.py'),
                             (runtime / 'daily-delivery/candidate/workspace/value.py').read_bytes())
        with closing(sqlite3.connect((runtime / 'workbench.db').as_uri() + '?mode=ro', uri=True)) as db:
            self.assertEqual('failed', db.execute('SELECT status FROM tasks').fetchone()[0])
            original = db.execute("SELECT evidence_json FROM task_events WHERE detail='original absolute references'").fetchone()[0]
            self.assertEqual(str(self.report), json.loads(original)['report_path'])
            self.assertEqual(str(self.project), db.execute('SELECT root_path FROM harness_projects').fetchone()[0])
        self.assertEqual(runtime / 'reports/failure.json', resolve_reference(self.report, runtime))
        self.assertEqual(project, resolve_reference(self.project, runtime))
        self.assertTrue(result['ok'])
        self.assertEqual('verified', result['evidence_restore'])
        self.assertEqual('ready', result['execution_environment']['status'])
        self.assertFalse(result['automatic_replay'])
        self.assertTrue((runtime / RESTORE_MARKER).is_file())

    def test_second_migration_uses_package_map_without_live_mapping_or_original(self):
        _, runtime_b, project_b, _ = self.migrated()
        projects_b = ProjectStore(runtime_b / 'workbench.db')
        archive_b = self.archive(runtime_b, projects_b)
        with open_read(archive_b, 'rb') as stream, zipfile.ZipFile(stream) as bundle:
            archived_db = bundle.read('payload/workbench.db')
            original_map = bundle.read('payload/migration/reference-map.json')
            manifest = json.loads(bundle.read('manifest.json'))
        # A third machine need not have the original runtime or the in-process map.
        unload_reference_mappings(runtime_b)
        self.runtime.rename(self.root / 'retained-a')
        self.assertTrue(verify_backup(archive_b)['ok'])
        runtime_c, project_c = self.root / 'runtime-c', self.root / 'project-c'
        self.runtimes.append(runtime_c)
        shutil.copytree(project_b, project_c)
        result = restore_migration(archive_b, runtime_c, {str(project_b): str(project_c)})
        self.assertEqual('ready', result['execution_environment']['status'])
        self.assertEqual(archived_db, (runtime_c / 'workbench.db').read_bytes())
        self.assertEqual(runtime_c / 'reports/failure.json', resolve_reference(self.report, runtime_c))
        self.assertEqual(project_c, resolve_reference(self.project, runtime_c))
        historical = runtime_c / 'migration/history' / manifest['id'] / 'reference-map.json'
        self.assertEqual(original_map, historical.read_bytes())
        self.assertEqual(historical, resolve_reference(runtime_b / 'migration/reference-map.json', runtime_c))
        self.assertTrue(BackupService(runtime_c).create('fixture-user')['id'])

    def test_initial_verify_rejects_drift_but_fresh_work_accepts_later_source_changes(self):
        _, runtime, project, result = self.migrated()
        self.assertEqual('ready', result['execution_environment']['status'])
        (project / 'value.py').write_text('value = 2\n', encoding='utf-8')
        fresh = fresh_migration_preflight(runtime, self.project_id)
        self.assertEqual('ready', fresh['status'])
        self.assertFalse(fresh['source_match_required'])
        # Explicitly repeating the initial source verification still compares the package.
        initial = validate_migration_environment(runtime)
        self.assertEqual('missing_dependencies', initial['status'])
        self.assertIn('源码哈希不匹配', initial['projects'][0]['errors'])
        with self.assertRaisesRegex(ValueError, '首次源码'):
            assert_migration_preflight(runtime, self.project_id)
        (project / 'value.py').write_bytes(b'value = 1\n')
        self.assertEqual('ready', validate_migration_environment(runtime)['status'])
        self.assertEqual('ready', assert_migration_preflight(runtime, self.project_id)['status'])

    def test_initial_proof_is_bound_to_physical_reference_map_and_requires_reverification(self):
        _, runtime, project, restored = self.migrated()
        self.assertEqual('ready', restored['execution_environment']['status'])
        database_bytes = (runtime / 'workbench.db').read_bytes()
        map_file = runtime / 'migration/reference-map.json'
        proof_file = runtime / 'migration/environment-result.json'
        original_proof = proof_file.read_bytes()
        proof = json.loads(original_proof)
        self.assertEqual(hashlib.sha256(map_file.read_bytes()).hexdigest(), proof['reference_map_sha256'])
        proof.pop('reference_map_sha256')
        proof_file.write_text(json.dumps(proof), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '引用映射核验凭据缺失'):
            assert_migration_preflight(runtime, self.project_id)
        proof_file.write_bytes(original_proof)

        different = self.root / 'different-git-project'
        shutil.copytree(project, different)
        (different / 'value.py').write_bytes(b'value = 73\n')
        mapping = json.loads(map_file.read_text(encoding='utf-8'))
        mapping['mappings'][str(self.project)] = str(different)
        map_file.write_text(json.dumps(mapping), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '映射已变化，请重新核验'):
            assert_migration_preflight(runtime, self.project_id)
        # A valid Git repository and actual registered venv do not authorize a
        # replacement source through a proof belonging to the previous map.
        checked = validate_migration_environment(runtime)
        self.assertEqual('missing_dependencies', checked['status'])
        self.assertEqual('python', checked['projects'][0]['executable_kind'])
        self.assertEqual(['源码哈希不匹配'], checked['projects'][0]['errors'])
        self.assertEqual(hashlib.sha256(map_file.read_bytes()).hexdigest(), checked['reference_map_sha256'])
        with self.assertRaisesRegex(ValueError, '首次源码一致性核验'):
            assert_migration_preflight(runtime, self.project_id)

        mapping['mappings'][str(self.project)] = str(project)
        map_file.write_text(json.dumps(mapping, sort_keys=True), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '映射已变化，请重新核验'):
            assert_migration_preflight(runtime, self.project_id)
        checked = validate_migration_environment(runtime)
        self.assertEqual('ready', checked['status'])
        self.assertEqual(hashlib.sha256(map_file.read_bytes()).hexdigest(), checked['reference_map_sha256'])
        self.assertEqual('ready', assert_migration_preflight(runtime, self.project_id)['status'])
        self.assertEqual(database_bytes, (runtime / 'workbench.db').read_bytes())

    def test_missing_external_environment_blocks_fresh_work_without_replay(self):
        _, runtime, project, _ = self.migrated(command=[str(self.root / 'missing/python.exe'), '-B', 'fixture_eval.py'])
        result = validate_migration_environment(runtime)
        self.assertEqual('missing_dependencies', result['status'])
        self.assertTrue(any('可执行程序缺失' in e for e in result['projects'][0]['errors']))
        with self.assertRaisesRegex(ValueError, '未就绪'):
            assert_migration_preflight(runtime, self.project_id)
        with closing(sqlite3.connect((runtime / 'workbench.db').as_uri() + '?mode=ro', uri=True)) as db:
            self.assertEqual('failed', db.execute('SELECT status FROM tasks').fetchone()[0])
        self.assertTrue((project / '.git').is_dir())

    def test_git_registration_and_python_venv_are_actually_probed(self):
        _, runtime, project, _ = self.migrated()
        shutil.rmtree(project / '.git')
        result = validate_migration_environment(runtime)
        self.assertEqual('missing_dependencies', result['status'])
        self.assertTrue(any('Git登记' in error for error in result['projects'][0]['errors']))
        subprocess.run(['git', 'init', '--quiet', str(project)], check=True, capture_output=True)
        self.projects.configure(self.project_id, [getattr(sys, '_base_executable', sys.executable), 'fixture_eval.py'])
        archive = self.archive()
        runtime_c = self.root / 'runtime-c'; self.runtimes.append(runtime_c)
        result = restore_migration(archive, runtime_c, {})
        self.assertEqual('missing_dependencies', result['execution_environment']['status'])
        self.assertTrue(any('Python虚拟环境未验证' in e for e in result['execution_environment']['projects'][0]['errors']))

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable on this test host')
    def test_non_python_registration_uses_real_version_probe(self):
        _, runtime, _, result = self.migrated(command=[shutil.which('node'), 'check.js'])
        self.assertEqual('ready', result['execution_environment']['status'])
        self.assertEqual('non_python', result['execution_environment']['projects'][0]['executable_kind'])
        self.assertEqual('ready', fresh_migration_preflight(runtime, self.project_id)['status'])

    def test_nonempty_destination_is_preserved_and_corrupt_archive_leaves_failure_marker(self):
        archive = self.archive()
        target = self.root / 'nonempty'; target.mkdir()
        keep = target / 'keep.txt'; keep.write_bytes(b'preserve me')
        with self.assertRaisesRegex(ValueError, '必须为空'):
            restore_migration(archive, target, {})
        self.assertEqual(b'preserve me', keep.read_bytes())
        self.assertFalse((target / FAILED_MARKER).exists())
        with open_read(archive, 'rb') as stream, zipfile.ZipFile(stream) as source:
            contents = {name: source.read(name) for name in source.namelist()}
        contents['payload/reports/failure.json'] = b'changed after archive'
        corrupt = self.root / 'corrupt.zip'
        with zipfile.ZipFile(corrupt, 'w') as bundle:
            for name, data in contents.items(): bundle.writestr(name, data)
        target = self.root / 'corrupt-target'; self.runtimes.append(target)
        with self.assertRaisesRegex(ValueError, '校验失败|大小不一致'):
            restore_migration(corrupt, target, {})
        self.assertFalse((target / 'workbench.db').exists())
        self.assertFalse(json.loads((target / FAILED_MARKER).read_text(encoding='utf-8'))['complete'])

    def test_copy_failure_removes_installed_files_and_retains_failure_record(self):
        archive = self.archive()
        target = self.root / 'failed-copy'; self.runtimes.append(target)
        original = shutil.copyfile
        def damaged(source, destination, *args, **kwargs):
            result = original(source, destination, *args, **kwargs)
            if str(destination).endswith('failure.json'):
                Path(destination).write_bytes(b'copy damaged')
            return result
        with patch('workbench.migration.shutil.copyfile', damaged):
            with self.assertRaisesRegex(ValueError, '哈希变化'):
                restore_migration(archive, target, {})
        self.assertFalse((target / 'reports/failure.json').exists())
        self.assertFalse((target / 'workbench.db').exists())
        self.assertTrue((target / FAILED_MARKER).is_file())
        self.assertEqual(self.report, resolve_reference(self.report, target))

    def test_plan_registry_mismatch_and_changed_plan_do_not_grant_initial_proof(self):
        _, runtime, _, _ = self.migrated()
        plan_path = runtime / 'migration/environment-plan.json'
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        plan['projects'][0]['eval_command'] = [sys.executable, 'different-fixture_eval.py']
        plan_path.write_text(json.dumps(plan), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '计划已变化'):
            assert_migration_preflight(runtime, self.project_id)
        result = validate_migration_environment(runtime)
        self.assertEqual('missing_dependencies', result['status'])
        self.assertTrue(any('登记不一致' in error for error in result['projects'][0]['errors']))

    def test_invalid_mapping_cycles_expansion_and_path_escape_are_rejected(self):
        a, b = self.root / 'old', self.root / 'new'
        for mapping in ({str(a): str(a)}, {str(a): str(a / 'child')},
                        {str(a): str(b), str(b): str(a)},
                        {'relative': str(b)}, {str(a): str(b / '..' / 'escape')}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                validate_reference_mappings(mapping)
        with reference_mapping_scope(self.runtime, {str(a): str(b)}):
            self.assertEqual(b / 'evidence.json', resolve_reference(a / 'evidence.json', self.runtime))
            with self.assertRaisesRegex(ValueError, '越界'):
                resolve_reference(a / '..' / 'escape', self.runtime)
        self.assertEqual(a / 'evidence.json', resolve_reference(a / 'evidence.json', self.runtime))

    def test_removing_map_file_unloads_stale_process_mapping(self):
        _, runtime, _, _ = self.migrated()
        (runtime / 'migration/reference-map.json').unlink()
        self.assertEqual({}, load_reference_mappings(runtime))
        self.assertEqual(self.report, resolve_reference(self.report, runtime))
        with self.assertRaisesRegex(ValueError, '映射缺失'):
            assert_migration_preflight(runtime, self.project_id)

    def test_backup_physical_source_is_not_redirected_by_another_loaded_runtime(self):
        _, runtime, _, _ = self.migrated()
        self.assertEqual(runtime / 'reports/failure.json', resolve_reference(self.report))
        # Both directories still exist; A's physical snapshot must stay in A.
        archive = self.archive()
        with open_read(archive, 'rb') as stream, zipfile.ZipFile(stream) as bundle:
            manifest = json.loads(bundle.read('manifest.json'))
        self.assertEqual(str(self.runtime), manifest['runtime_root'])
        self.assertTrue(verify_backup(archive)['ok'])

    def test_missing_marker_cannot_turn_migrated_runtime_into_plain_runtime(self):
        _, runtime, _, _ = self.migrated()
        (runtime / RESTORE_MARKER).unlink()
        with self.assertRaisesRegex(ValueError, '恢复标记缺失'):
            assert_migration_preflight(runtime, self.project_id)

    def test_fresh_preflight_rechecks_external_dependencies_after_initial_proof(self):
        dependency = self.root / 'customer-data'; dependency.mkdir()
        self.tasks.append_event('TASK-ABCD123456', 'external dependency fixture', evidence={'data': str(dependency)})
        _, runtime, _, result = self.migrated()
        self.assertEqual('ready', result['execution_environment']['status'])
        dependency.rmdir()
        fresh = fresh_migration_preflight(runtime, self.project_id)
        self.assertEqual('missing_dependencies', fresh['status'])
        self.assertIn(str(dependency), fresh['missing_external_dependencies'])
        with self.assertRaisesRegex(ValueError, '外部依赖缺失'):
            assert_migration_preflight(runtime, self.project_id)
        dependency.mkdir()
        self.assertEqual('ready', assert_migration_preflight(runtime, self.project_id)['status'])

    def test_standard_restore_preserves_migration_identity_and_initial_proof(self):
        _, runtime, project, result = self.migrated()
        self.assertEqual('ready', result['execution_environment']['status'])
        # A normal backup must preserve the proof; preparing a new migration would
        # intentionally replace the plan and require a new initial verification.
        backup = BackupService(runtime).create('fixture-user')
        archive = self.root / (backup['id'] + '-same-path.zip')
        shutil.copyfile(runtime / 'backups' / (backup['id'] + '.zip'), archive)
        with open_read(archive, 'rb') as stream, zipfile.ZipFile(stream) as bundle:
            originals = {relative: bundle.read('payload/' + relative) for relative in (
                'workbench.db', 'reports/failure.json', 'migration/reference-map.json',
                'migration/environment-plan.json', 'migration/environment-result.json')}
        unload_reference_mappings(runtime)
        runtime.rename(self.root / 'retained-b')
        self.assertTrue(restore_backup(archive, runtime)['ok'])
        marker = json.loads((runtime / RESTORE_MARKER).read_text(encoding='utf-8'))
        self.assertEqual('workbench.migration/v1', marker['schema'])
        self.assertEqual('same_path_backup', marker['restore_kind'])
        self.assertFalse(marker['automatic_replay'])
        for relative, original in originals.items():
            self.assertEqual(original, (runtime / relative).read_bytes(), relative)
        self.assertEqual('ready', validate_migration_environment(runtime)['status'])
        self.assertEqual('ready', assert_migration_preflight(runtime, self.project_id)['status'])
        self.assertEqual(runtime / 'reports/failure.json', resolve_reference(self.report, runtime))
        # Restored proof does not excuse a missing live environment, and repairing
        # that environment can regain readiness without rewriting historical DB.
        shutil.rmtree(project / '.git')
        with self.assertRaisesRegex(ValueError, '未就绪'):
            assert_migration_preflight(runtime, self.project_id)
        subprocess.run(['git', 'init', '--quiet', str(project)], check=True, capture_output=True)
        self.assertEqual('ready', validate_migration_environment(runtime)['status'])
        self.assertEqual('ready', assert_migration_preflight(runtime, self.project_id)['status'])
        self.assertEqual(originals['workbench.db'], (runtime / 'workbench.db').read_bytes())

    def test_process_receipt_hash_pairs_reject_source_and_archive_tampering(self):
        pairs = (('process_path', 'process_sha256'),
                 ('exit_process_path', 'exit_process_sha256'))
        for index, (path_key, hash_key) in enumerate(pairs):
            receipt = self.runtime / 'reports' / ('process-' + str(index) + '.json')
            original = b'{"returncode":0,"fixture":"actual file"}'
            receipt.write_bytes(original)
            self.tasks.append_event('TASK-ABCD123456', 'process receipt ' + path_key,
                                    evidence={path_key: str(receipt),
                                              hash_key: hashlib.sha256(original).hexdigest()})
        for index, (path_key, _) in enumerate(pairs):
            with self.subTest(pair=path_key):
                receipt = self.runtime / 'reports' / ('process-' + str(index) + '.json')
                original = receipt.read_bytes()
                altered = b'{"returncode":1,"fixture":"receipt changed"}'
                receipt.write_bytes(altered)
                with self.assertRaisesRegex(ValueError, '原始哈希不一致'):
                    BackupService(self.runtime).create('fixture-user')
                receipt.write_bytes(original)
                backup = BackupService(self.runtime).create('fixture-user')
                archive = self.runtime / 'backups' / (backup['id'] + '.zip')
                self.assertTrue(verify_backup(archive)['ok'])
                with open_read(archive, 'rb') as stream, zipfile.ZipFile(stream) as bundle:
                    contents = {name: bundle.read(name) for name in bundle.namelist()}
                relative = receipt.relative_to(self.runtime).as_posix()
                contents['payload/' + relative] = altered
                manifest = json.loads(contents['manifest.json'])
                forged_hash = hashlib.sha256(altered).hexdigest()
                manifest['files'][relative] = {'sha256': forged_hash, 'size': len(altered)}
                # Even an attacker updating the package manifest must not replace
                # the original receipt digest bound in the unchanged database.
                for reference in manifest['references']:
                    if reference.get('relative') == relative and 'expected_sha256' in reference:
                        reference['expected_sha256'] = forged_hash
                contents['manifest.json'] = json.dumps(manifest).encode('utf-8')
                tampered = self.root / ('tampered-' + path_key + '.zip')
                with zipfile.ZipFile(tampered, 'w') as bundle:
                    for name, data in contents.items():
                        bundle.writestr(name, data)
                with self.assertRaisesRegex(ValueError, '原始哈希不一致'):
                    verify_backup(tampered)
                with self.assertRaisesRegex(ValueError, '原始哈希不一致'):
                    restore_backup(tampered, self.runtime)
                self.assertEqual(original, receipt.read_bytes())

    def test_historical_evidence_and_commands_use_own_runtime_after_original_is_gone(self):
        from eval.workbench_contracts import LocalDeliveryFixture
        from workbench.daily_delivery import manifest, submit_daily
        from workbench.eval_harness import report_view
        from workbench.evidence_gate import assert_current_evidence
        from workbench.execution import CodexExecutionRunner
        from workbench.project_configuration import ProjectConfiguration

        with LocalDeliveryFixture() as fixture:
            old_source, old_runtime = fixture.source, fixture.runtime
            def factory(workspace, runtime):
                def process(command, **kwargs):
                    (workspace / 'value.txt').write_text(str(fixture.value), encoding='utf-8')
                    output = Path(command[command.index('--output-last-message') + 1])
                    output.write_text(json.dumps(dict(summary='Injected migration contract fixture',
                        tests=[], risks=['Not a live model or human acceptance'], next_step='Run actual project Eval')),
                        encoding='utf-8')
                    return subprocess.CompletedProcess(command, 0, '', '')
                return CodexExecutionRunner(workspace, runtime, process_runner=process)
            fixture.service.submitter = lambda source, runtime, tasks, plan, created: submit_daily(
                source, runtime, tasks, plan, created, runner_factory=factory)
            # The actual fixture Eval ignores the extra argument, but binding it
            # exercises read-time relocation of absolute registered arguments.
            fixture.projects.configure(fixture.project['id'],
                [sys.executable, '-B', 'contract_eval.py', str(old_source / 'policy.txt')])
            config = ProjectConfiguration(fixture.projects)
            runtime_command = [sys.executable, '-B', str(old_source / 'contract_eval.py'), '{workspace}']
            config.save(fixture.project['id'], dict(expected_configuration_revision=0,
                preview_config=dict(command=runtime_command, health={'path': '/health'}),
                deployment_profiles=[dict(id='local', command=runtime_command,
                    rollback_command=runtime_command, url='http://127.0.0.1:19317',
                    health={'path': '/health'})]), 'fixture-owner')
            _, task_id = fixture.accepted_source()
            historical = fixture.tasks.get(task_id)
            assert_current_evidence(historical, old_runtime)
            old_workspace = Path(historical['result']['runner']['workspace'])
            original_source = manifest(old_source, old_runtime)
            self.assertFalse((old_runtime / 'delivery' / 'iteration').exists())
            with closing(sqlite3.connect((old_runtime / 'workbench.db').as_uri() + '?mode=ro', uri=True)) as db:
                self.assertTrue(db.execute("SELECT 1 FROM initiative_events WHERE kind='delivery/iteration'").fetchone())
            prepared = prepare_migration(old_runtime, fixture.projects, 'fixture-user')
            archive = self.root / (prepared['id'] + '-isolated-runtimes.zip')
            shutil.copyfile(prepared['archive_path'], archive)
            with open_read(archive, 'rb') as stream, zipfile.ZipFile(stream) as bundle:
                archived_database = bundle.read('payload/workbench.db')
                backup_manifest = json.loads(bundle.read('manifest.json'))
                self.assertFalse(any(ref.get('relative') == 'delivery/iteration'
                                     for ref in backup_manifest['references']))

            copies = []
            for suffix in ('b', 'c'):
                runtime, project = self.root / ('isolated-runtime-' + suffix), self.root / ('isolated-project-' + suffix)
                self.runtimes.append(runtime)
                shutil.copytree(old_source, project)
                restored = restore_migration(archive, runtime, {str(old_source): str(project)})
                self.assertEqual('ready', restored['execution_environment']['status'], restored['execution_environment'])
                copies.append((runtime, project))
            # A previous host's directories really cannot satisfy any is_file or
            # is_dir check here; retained copies have different absolute paths.
            old_runtime.rename(fixture.root / 'retained-runtime')
            old_source.rename(fixture.root / 'retained-project')
            self.assertFalse(old_runtime.exists())
            self.assertFalse(old_source.exists())
            self.assertFalse(old_workspace.exists())
            runtime_b, project_b = copies[0]
            runtime_c, project_c = copies[1]
            workspace_b = resolve_reference(old_workspace, runtime_b)
            workspace_c = resolve_reference(old_workspace, runtime_c)
            (project_c / 'value.txt').write_text('17', encoding='utf-8')
            (workspace_c / 'value.txt').write_text('19', encoding='utf-8')
            for original_path in (historical['spec_path'], historical['result']['runner']['report_path'],
                                  historical['result']['runner']['process_path']):
                changed = resolve_reference(original_path, runtime_c)
                changed.write_bytes(changed.read_bytes() + b' ')

            # The unrelated C map is newest. Every B operation must explicitly
            # use B, while C must still detect its own damaged historical proof.
            load_reference_mappings(runtime_b)
            load_reference_mappings(runtime_c)
            self.assertEqual(workspace_c, resolve_reference(old_workspace))
            task_b = TaskStore(runtime_b / 'workbench.db').get(task_id)
            task_c = TaskStore(runtime_c / 'workbench.db').get(task_id)
            self.assertEqual(original_source, manifest(old_source, runtime_b))
            assert_current_evidence(task_b, runtime_b)
            self.assertEqual('current', report_view(task_b['result'], old_workspace, runtime_b)['freshness'])
            self.assertEqual(original_source, manifest(old_source, runtime_b))
            self.assertNotEqual(original_source, manifest(old_source, runtime_c))
            self.assertEqual(hashlib.sha256(b'17').hexdigest(), manifest(old_source, runtime_c)['value.txt'])
            self.assertEqual('stale', report_view(task_c['result'], old_workspace, runtime_c)['freshness'])
            with self.assertRaisesRegex(ValueError, '所属任务、合同或配置绑定已变化'):
                assert_current_evidence(task_c, runtime_c)

            load_reference_mappings(runtime_b)
            self.assertEqual(workspace_b, resolve_reference(old_workspace))
            self.assertEqual('stale', report_view(task_c['result'], old_workspace, runtime_c)['freshness'])
            for runtime, project in copies:
                projects = ProjectStore(runtime / 'workbench.db')
                projected = projects.get(fixture.project['id'])
                self.assertEqual(str(project), projected['root_path'])
                self.assertEqual(str(project / 'policy.txt'), projected['eval_command'][3])
                executed = subprocess.run(projected['eval_command'], cwd=resolve_reference(old_workspace, runtime),
                                          check=True, capture_output=True, text=True)
                self.assertEqual('pass', json.loads(executed.stdout)['summary']['decision'])
                projected_config = ProjectConfiguration(projects).get(fixture.project['id'])
                commands = [projected_config['preview_config']['command'],
                    projected_config['deployment_profiles'][0]['command'],
                    projected_config['deployment_profiles'][0]['rollback_command']]
                for command in commands:
                    self.assertEqual(sys.executable, command[0])
                    self.assertEqual(str(project / 'contract_eval.py'), command[2])
                    self.assertTrue(Path(command[2]).is_file())
                    self.assertEqual('{workspace}', command[3])
                self.assertEqual(archived_database, (runtime / 'workbench.db').read_bytes())
                with closing(sqlite3.connect((runtime / 'workbench.db').as_uri() + '?mode=ro', uri=True)) as db:
                    registered = db.execute('SELECT root_path,eval_command_json FROM harness_projects WHERE id=?',
                                            (fixture.project['id'],)).fetchone()
                    raw_config = json.loads(db.execute('SELECT payload FROM project_configurations WHERE project_id=?',
                                                      (fixture.project['id'],)).fetchone()[0])
                    self.assertEqual(str(old_source), registered[0])
                    self.assertEqual(str(old_source / 'policy.txt'), json.loads(registered[1])[3])
                    self.assertEqual(str(old_source / 'contract_eval.py'), raw_config['preview_config']['command'][2])


if __name__ == '__main__':
    unittest.main()
