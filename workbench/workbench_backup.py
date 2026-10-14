"""Local workbench backups: immutable evidence, offline restoration, no ERP data."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import sqlite3
import tempfile
import zipfile

from .maintenance import MaintenanceGate, MaintenanceBusy
from .runtime_lease import WorkbenchRuntimeLease
from .file_io import atomic_write_text, open_read, read_text


SCHEMA = 'workbench.backup/v1'
RESTORE_MARKER = 'workbench-restored.json'
EVIDENCE_DIRS = frozenset({'reports', 'project-reports', 'delivery', 'daily-delivery', 'course',
    'course-worktrees', 'specs', 'v0-contracts', 'initiative-research', 'initiative-integration',
    'ci-evidence', 'hook-packages', 'candidates', 'release-index', 'baseline-audits', 'subagents',
    'learning-generation', 'deployments', 'migration', 'runtime-events', 'harness-sessions', 'sessions_jsonl', 'git-processes'})
SKIP_DIRS = frozenset({'.venv', 'node_modules', '__pycache__', '.cache', '.runtime', '.harness-runtime'})
MAX_BYTES = 8 * 1024 ** 3
MAX_FILES = 100000
BUSY_STAGES = {'researching', 'queued', 'executing', 'checking', 'cancelling', 'integrating'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _begin_restore(target, backup_id):
    """Block service startup before installing any database or evidence."""
    marker = Path(target) / 'workbench-restore-failed.json'
    atomic_write_text(marker, _json({'backup_id': backup_id, 'complete': False,
        'phase': 'installing', 'automatic_replay': False,
        'message': '恢复尚未完成；请保留原目录，不要启动此运行目录'}))
    return marker


def _failed_restore(target, installed, backup_id, error):
    """Try every owned file; the preinstalled startup block survives cleanup errors."""
    cleanup_errors = []
    target = Path(target).resolve()
    for path in reversed(installed):
        try:
            if not Path(path).resolve().is_relative_to(target):
                raise ValueError('恢复清理路径已越界')
            Path(path).unlink(missing_ok=True)
        except (OSError, ValueError) as cleanup_error:
            cleanup_errors.append(str(path) + ': ' + str(cleanup_error))
    try:
        atomic_write_text(target / 'workbench-restore-failed.json', _json({
            'backup_id': backup_id, 'complete': False, 'phase': 'failed',
            'automatic_replay': False, 'error': str(error), 'cleanup_errors': cleanup_errors,
            'message': '恢复失败；保留原目录和备份，核对失败目录后再重试'}))
    except OSError:
        # The installing marker was written before any data. Disk or rename
        # failures must not turn failed cleanup into an apparently usable DB.
        pass
    if cleanup_errors and hasattr(error, 'add_note'):
        error.add_note('恢复清理未完成：' + '; '.join(cleanup_errors))


def _publish_archive(temporary, archive):
    """Same-filesystem atomic publication that cannot replace an earlier backup."""
    # A rename/replace can clobber an existing archive on some hosts. Link fails
    # closed if the target exists or the filesystem cannot support publication.
    os.link(temporary, archive)
    try:
        Path(temporary).unlink()
    except OSError:
        pass  # The published, fully verified archive remains authoritative.


def _hash(path):
    digest = hashlib.sha256()
    with open_read(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def _is_link(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def _safe_member(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('备份文件路径无效')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {'', '.', '..'} for p in path.parts) or str(path) != value:
        raise ValueError('备份文件路径越界')
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {'COM' + str(i) for i in range(1, 10)} | {'LPT' + str(i) for i in range(1, 10)}
    if any(part.endswith((' ', '.')) or any(ord(ch) < 32 for ch in part)
           or part.split('.')[0].upper() in reserved for part in path.parts):
        raise ValueError('备份文件路径含本机不安全名称')
    return path


def _database_file(name):
    lower = name.lower()
    return any(lower.endswith(suffix + trailer) for suffix in ('.db', '.sqlite', '.sqlite3')
               for trailer in ('', '-wal', '-shm', '-journal'))


def _sensitive(name):
    return (name.lower().startswith(('.env', 'auth.', 'credentials.', 'secrets.'))
            or name.endswith(('.pyc', '.pyo')) or _database_file(name))


def _dependency_ownership(relative, kind):
    parts = PurePosixPath(relative).parts
    if parts and parts[0] == 'candidate-previews':
        return 'candidate_preview_data_not_restored'
    if any(part in SKIP_DIRS for part in parts):
        return 'environment_or_isolated_runtime_not_restored'
    if kind == 'file' and relative != 'workbench.db' and _database_file(parts[-1]):
        return 'other_database_not_restored'
    return None


def _paths(runtime):
    files, excluded, dependencies, directories = {}, [], [], set()
    for entry in sorted(runtime.iterdir()):
        if entry.name not in EVIDENCE_DIRS:
            if entry.name not in {'workbench.db', 'workbench.db-wal', 'workbench.db-shm', 'workbench.db-journal',
                                  'workbench-service.lock', 'workbench-maintenance.lock', 'delivery-code.lock',
                                  'backups', RESTORE_MARKER} and not entry.name.startswith('.backup-'):
                excluded.append(entry.name)
            continue
        if _is_link(entry):
            raise ValueError('证据目录不可为链接或重解析点：' + str(entry))
        if not entry.is_dir():
            raise ValueError('证据目录类型不正确：' + str(entry))
        for folder, dirs, names in os.walk(entry, followlinks=False):
            base = Path(folder)
            directories.add(base.relative_to(runtime).as_posix())
            for name in list(dirs):
                child = base / name
                if _is_link(child):
                    raise ValueError('证据中存在链接或重解析点：' + str(child))
                if name in SKIP_DIRS:
                    excluded.append(child.relative_to(runtime).as_posix() + '/')
                    dirs.remove(name)
            for name in sorted(names):
                path = base / name
                if _is_link(path) or not path.resolve().is_relative_to(runtime):
                    raise ValueError('证据文件不可链接到其他位置：' + str(path))
                relative = path.relative_to(runtime).as_posix()
                if _sensitive(name):
                    excluded.append(relative)
                    continue
                files[relative] = {'sha256': _hash(path), 'size': path.stat().st_size}
                if name == '.git' and path.is_file():
                    text = read_text(path, encoding='utf-8').strip()
                    if text.startswith('gitdir:'):
                        external = (path.parent / text[7:].strip()).resolve()
                        dependencies.append({'kind': 'git_worktree_registration', 'path': str(external),
                                             'exists': external.is_dir(), 'workspace': str(path.parent)})
    if len(files) + len(directories) > MAX_FILES or sum(row['size'] for row in files.values()) > MAX_BYTES:
        raise ValueError('备份范围超过本机备份上限（100000 个文件或 8 GiB）')
    return files, excluded, dependencies, sorted(directories)


def _database_state(database, runtime, *, check_busy=True, known_directories=None):
    references, dependencies, counts = [], [], {}
    with closing(_connect(database)) as db:
        db.row_factory = sqlite3.Row
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            counts[table] = db.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
            for row in db.execute('SELECT * FROM ' + quoted):
                item = dict(row)
                identity = str(item.get('id', item.get('task_id', '')))
                if check_busy and table == 'tasks' and item.get('status') in {'queued', 'spec_ready', 'executing', 'evaluating'}:
                    raise MaintenanceBusy('仍有待执行或运行中的任务，请先完成、取消或核对中断任务再备份')
                if check_busy and table == 'learning_generations' and item.get('status') == 'running':
                    raise MaintenanceBusy('学习草稿尚在生成，请先完成或取消再备份')
                for column, raw in item.items():
                    if not isinstance(raw, str):
                        continue
                    try:
                        value = json.loads(raw)
                    except (ValueError, TypeError):
                        value = raw
                    if check_busy and column == 'payload' and isinstance(value, dict):
                        if table in {'deployments', 'delivery_runtime'} and (value.get('status') in {'running', 'rolling_back'} or value.get('state') in {'running', 'pausing', 'cancelling'}):
                            raise MaintenanceBusy('高级执行或部署尚未停止，请先停止并保存现场')
                        if table == 'initiative_workflows' and value.get('stage') in BUSY_STAGES:
                            raise MaintenanceBusy('仍有事项正在调研、排队、执行或集成，请结束后再备份')
                        if table == 'web_execution_plans' and value.get('state') == 'starting':
                            raise MaintenanceBusy('仍有网页执行方案正在启动或执行，请结束后再备份')
                    stack = [(column, value)]
                    hash_bindings = {}
                    while stack:
                        field, nested = stack.pop()
                        if isinstance(nested, dict):
                            for path_key, hash_key in (('report_path', 'report_sha256'),
                                                       ('blocking_report', 'report_sha256'),
                                                       ('process_path', 'process_sha256'),
                                                       ('exit_process_path', 'exit_process_sha256'),
                                                       ('patch_path', 'patch_sha256')):
                                if nested.get(path_key) and nested.get(hash_key) is not None:
                                    hash_bindings[field + '.' + path_key] = (nested[hash_key], field + '.' + hash_key, False)
                            if nested.get('path') and nested.get('sha256') is not None:
                                hash_bindings[field + '.path'] = (nested['sha256'], field + '.sha256', True)
                            artifacts, hashes = nested.get('artifacts'), nested.get('artifact_sha256')
                            if isinstance(artifacts, dict):
                                for name in artifacts:
                                    sha = hashes.get(name) if isinstance(hashes, dict) else None
                                    hash_field = field + '.artifact_sha256.' + str(name)
                                    if name == 'report' and sha is None:
                                        sha = nested.get('report_sha256')
                                        hash_field = field + '.report_sha256'
                                    if sha is not None:
                                        hash_bindings[field + '.artifacts.' + str(name)] = (sha, hash_field, False)
                            stack.extend((field + '.' + str(k), v) for k, v in nested.items())
                        elif isinstance(nested, list):
                            stack.extend((field + '[' + str(i) + ']', v) for i, v in enumerate(nested))
                        elif isinstance(nested, str) and len(nested) < 4096 and '\n' not in nested and '://' not in nested:
                            if field.endswith('.health.path'):
                                continue  # Health request routes are URLs, including POSIX-looking /health.
                            path = Path(nested)
                            if not path.is_absolute() and (not path.parts or path.parts[0] not in EVIDENCE_DIRS):
                                continue
                            if not path.is_absolute() and field.rsplit('.', 1)[-1] in {'kind', 'type', 'status', 'state', 'action', 'role', 'decision', 'provider', 'seam'}:
                                continue  # Event enums such as delivery/iteration are not file references.
                            from .reference_paths import resolve_reference
                            path = resolve_reference(path, runtime).resolve() if path.is_absolute() else (runtime / path).resolve()
                            inside = path.is_relative_to(runtime)
                            relative = path.relative_to(runtime).as_posix() if inside else None
                            kind = ('directory' if path.is_dir() or (known_directories is not None and
                                    (relative == '.' or relative in known_directories)) else 'file')
                            ownership = _dependency_ownership(relative, kind) if inside else None
                            ref = {'table': table, 'row': identity, 'field': field, 'path': str(path),
                                   'inside_runtime': inside and ownership is None, 'exists': path.exists(), 'kind': kind}
                            if inside and ownership is None:
                                ref['relative'] = relative
                                binding = hash_bindings.get(field)
                                if binding and (not binding[2] or ref['kind'] == 'file'):
                                    sha, hash_field, _ = binding
                                    if (not isinstance(sha, str) or len(sha) != 64
                                            or any(ch not in '0123456789abcdefABCDEF' for ch in sha)):
                                        raise ValueError('内部证据的原始哈希无效：' + str(path))
                                    ref.update(expected_sha256=sha.lower(), hash_field=hash_field)
                                references.append(ref)
                            else:
                                if ownership:
                                    ref.update(ownership=ownership, physical_runtime_relative=relative)
                                dependencies.append(ref)
    return counts, references, dependencies


def _validate_references(references, runtime_root, files, directories, *, source_required=False):
    """Internal paths must be restored by this package, never merely warned about."""
    root = Path(runtime_root).resolve()
    for ref in references:
        if not isinstance(ref, dict) or not ref.get('inside_runtime') or not isinstance(ref.get('relative'), str):
            raise ValueError('内部引用记录无效')
        relative = ref['relative']
        path = root if relative == '.' else root.joinpath(*_safe_member(relative).parts)
        if os.path.normcase(str(path)) != os.path.normcase(str(Path(ref.get('path', '')).resolve())):
            raise ValueError('内部引用路径与原始运行目录不一致')
        expected_kind = 'directory' if relative == '.' or relative in directories else 'file' if relative in files else None
        if expected_kind is None or (source_required and (not ref.get('exists') or ref.get('kind') != expected_kind)):
            raise ValueError('内部引用缺失、类型不符或未打包：' + str(path))
        if ref.get('expected_sha256') is not None:
            record = files.get(relative, {})
            if expected_kind != 'file' or record.get('sha256') != ref['expected_sha256']:
                raise ValueError('内部证据文件与数据库原始哈希不一致：' + str(path))


def _validate_database_references(database, manifest):
    counts, derived, _ = _database_state(database, Path(manifest['runtime_root']).resolve(),
                                       check_busy=False, known_directories=set(manifest['directories']))
    if counts != manifest['table_counts']:
        raise ValueError('备份数据库与表记录清单不一致')
    directories = set(manifest['directories'])
    _validate_references(manifest['references'], manifest['runtime_root'], manifest['files'], directories, source_required=True)
    _validate_references(derived, manifest['runtime_root'], manifest['files'], directories)
    signature = lambda ref: (ref['table'], ref['row'], ref['field'], ref['path'], ref['relative'],
                             ref.get('expected_sha256'), ref.get('hash_field'))
    if {signature(ref) for ref in derived} != {signature(ref) for ref in manifest['references']}:
        raise ValueError('数据库内部引用与备份引用清单不一致')


def _checked_archive(archive, destination):
    try:
        from .reference_paths import reference_mapping_scope
        with reference_mapping_scope(destination, {}):
            return _read_checked_archive(archive, destination)
    except (zipfile.BadZipFile, sqlite3.DatabaseError, KeyError, TypeError) as error:
        raise ValueError('备份包或数据库不可读：' + str(error)) from error


def _read_checked_archive(archive, destination):
    archive = Path(archive).resolve()
    with open_read(archive, 'rb') as archive_stream, zipfile.ZipFile(archive_stream) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or 'manifest.json' not in names or len(names) > MAX_FILES + 2:
            raise ValueError('备份目录重复、缺少清单或文件数量过大')
        if sum(item.file_size for item in bundle.infolist()) > MAX_BYTES + 16 * 1024 ** 2:
            raise ValueError('备份展开大小超过上限')
        for name in names:
            _safe_member(name)
        if bundle.getinfo('manifest.json').file_size > 16 * 1024 ** 2:
            raise ValueError('备份清单过大')
        manifest = json.loads(bundle.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA or not isinstance(manifest.get('files'), dict):
            raise ValueError('备份版本或清单无效')
        if (not isinstance(manifest.get('table_counts'), dict) or
                not isinstance(manifest.get('id'), str) or
                any(not isinstance(manifest.get(key), list) for key in ('directories', 'references', 'external_dependencies', 'warnings'))):
            raise ValueError('备份清单字段无效')
        if not Path(manifest.get('runtime_root', '')).is_absolute():
            raise ValueError('备份缺少原始运行目录')
        expected = {'manifest.json'} | {'payload/' + name for name in manifest['files']}
        if set(names) != expected or 'workbench.db' not in manifest['files']:
            raise ValueError('备份文件与清单不一致')
        if len(manifest['directories']) + len(manifest['files']) > MAX_FILES or len(manifest['directories']) != len(set(manifest['directories'])):
            raise ValueError('备份目录清单重复或数量过大')
        for relative in sorted(manifest['directories']):
            member = _safe_member(relative)
            if (member.parts[0] not in EVIDENCE_DIRS or any(part in SKIP_DIRS for part in member.parts)
                    or relative in manifest['files']):
                raise ValueError('备份目录类型或范围无效：' + relative)
            (destination / relative).mkdir(parents=True, exist_ok=True)
        for relative, record in manifest['files'].items():
            member = _safe_member(relative)
            if relative != 'workbench.db' and member.parts[0] not in EVIDENCE_DIRS:
                raise ValueError('备份包含非工作台文件：' + relative)
            if any(part in SKIP_DIRS for part in member.parts) or (relative != 'workbench.db' and _sensitive(member.name)):
                raise ValueError('备份包含被排除的环境或敏感文件：' + relative)
            if not isinstance(record, dict) or bundle.getinfo('payload/' + relative).file_size != record.get('size'):
                raise ValueError('备份文件大小不一致：' + relative)
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open('payload/' + relative) as source, path.open('xb') as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
            if _hash(path) != record.get('sha256'):
                raise ValueError('备份文件校验失败：' + relative)
    with closing(_connect(destination / 'workbench.db')) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('备份数据库完整性检查失败')
    from .reference_paths import backup_reference_scope
    with backup_reference_scope(manifest, destination):
        _validate_database_references(destination / 'workbench.db', manifest)
    return manifest


class BackupService:
    def __init__(self, runtime):
        self.runtime = Path(runtime).resolve()
        self.directory = self.runtime / 'backups'

    def create(self, actor, *, busy_check=None):
        from .reference_paths import load_reference_mappings, reference_mapping_scope
        with reference_mapping_scope(self.runtime, load_reference_mappings(self.runtime)):
            return self._create(actor, busy_check=busy_check)

    def _create(self, actor, *, busy_check=None):
        if not isinstance(actor, str) or not actor.strip() or actor.strip().lower().startswith('agent:') or len(actor) > 80:
            raise ValueError('请填写真实操作人的署名')
        # Do not briefly freeze a known live worker merely to reject its backup.
        if busy_check and busy_check():
            raise MaintenanceBusy('仍有执行、排队或候选预览，请结束后再备份')
        with MaintenanceGate(self.runtime).exclusive():
            if busy_check and busy_check():
                raise MaintenanceBusy('仍有执行、排队或候选预览，请结束后再备份')
            database = self.runtime / 'workbench.db'
            if not database.is_file() or _is_link(database):
                raise ValueError('工作台数据库不存在或为链接')
            counts, references, external = _database_state(database, self.runtime)
            files, excluded, git_deps, directories = _paths(self.runtime)
            _validate_references(references, self.runtime, files | {'workbench.db': {}}, set(directories), source_required=True)
            if self.directory.exists() and _is_link(self.directory):
                raise ValueError('备份输出目录不可为链接或重解析点')
            self.directory.mkdir(parents=True, exist_ok=True)
            identifier = 'WB-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(6)
            temporary = self.directory / ('.' + identifier + '.tmp')
            archive = self.directory / (identifier + '.zip')
            if temporary.exists() or archive.exists():
                raise FileExistsError('备份编号已存在；原备份保留，请重新备份')
            temporary_owned = False
            try:
                with tempfile.TemporaryDirectory(prefix='.backup-', dir=self.runtime) as stage:
                    snapshot = Path(stage) / 'workbench.db'
                    with closing(_connect(database)) as source, closing(sqlite3.connect(snapshot)) as target:
                        version = source.execute('PRAGMA data_version').fetchone()[0]
                        source.backup(target)
                        target.commit()
                        files['workbench.db'] = {'sha256': _hash(snapshot), 'size': snapshot.stat().st_size}
                        warnings = ['缺少需另行保留的外部依赖：' + ref['path'] for ref in external + git_deps if not ref['exists']]
                        manifest = {'schema': SCHEMA, 'id': identifier, 'created_at': datetime.now(timezone.utc).isoformat(),
                            'actor': actor.strip(), 'runtime_root': str(self.runtime), 'files': files, 'directories': directories,
                            'table_counts': counts, 'references': references, 'external_dependencies': external + git_deps,
                            'excluded': excluded, 'warnings': warnings,
                            'boundary': '仅恢复本机原路径的工作台数据与文件证据；外部项目源码、FlowERP 数据、浏览器草稿和 Git 注册需另行保留。'}
                        with temporary.open('xb') as archive_stream:
                            temporary_owned = True
                            with zipfile.ZipFile(archive_stream, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
                                bundle.writestr('manifest.json', _json(manifest))
                                for relative in sorted(files):
                                    bundle.write(snapshot if relative == 'workbench.db' else self.runtime / relative, 'payload/' + relative)
                        again, _, _, again_directories = _paths(self.runtime)
                        if (again != {k: v for k, v in files.items() if k != 'workbench.db'} or again_directories != directories
                                or source.execute('PRAGMA data_version').fetchone()[0] != version):
                            raise MaintenanceBusy('备份期间文件或数据库发生变化，未发布备份；请核对其他 CLI 进程')
                    with tempfile.TemporaryDirectory(prefix='.backup-check-', dir=self.runtime) as check:
                        _checked_archive(temporary, Path(check))
                _publish_archive(temporary, archive)
            except Exception:
                if temporary_owned:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
        return self.get(identifier)

    def archive_path(self, identifier):
        if not isinstance(identifier, str) or not identifier.startswith('WB-') or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-' for c in identifier):
            raise ValueError('备份编号无效')
        path = self.directory / (identifier + '.zip')
        if self.directory.exists() and _is_link(self.directory):
            raise ValueError('备份目录不可为链接或重解析点')
        if not path.is_file() or _is_link(path):
            raise KeyError('备份不存在')
        return path

    def get(self, identifier):
        path = self.archive_path(identifier)
        with open_read(path, 'rb') as archive_stream, zipfile.ZipFile(archive_stream) as bundle:
            manifest = json.loads(bundle.read('manifest.json'))
        return {key: manifest[key] for key in ('id', 'created_at', 'actor', 'runtime_root', 'boundary', 'warnings')} | {
            'file_count': len(manifest['files']), 'archive_bytes': path.stat().st_size,
            'archive_path': str(path), 'download_url': '/api/v1/backups/' + identifier + '/download',
            'external_dependency_count': len(manifest['external_dependencies'])}

    def list(self):
        items = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob('WB-*.zip'), reverse=True):
                try:
                    items.append(self.get(path.stem))
                except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                    items.append({'id': path.stem, 'error': '备份清单不可读，请使用校验命令核对'})
        return {'items': items, 'runtime': str(self.runtime), 'restored': (self.runtime / RESTORE_MARKER).is_file(),
                'boundary': '在线备份需无正在运行或排队的事项；恢复须停服并使用本机原路径的空目录。'}

    def verify(self, archive):
        path = Path(archive) if str(archive).endswith('.zip') else self.archive_path(str(archive))
        return verify_backup(path)


def verify_backup(archive):
    try:
        with tempfile.TemporaryDirectory(prefix='workbench-backup-verify-') as folder:
            manifest = _checked_archive(archive, Path(folder))
            environment = _execution_environment(manifest)
            return {'ok': True, 'id': manifest['id'], 'runtime_root': manifest['runtime_root'],
                    'files_verified': len(manifest['files']), 'table_counts': manifest['table_counts'],
                    'warnings': manifest.get('warnings', []),
                    'missing_external_dependencies': environment['missing_external_dependencies'],
                    'execution_environment': environment,
                    'boundary': '包内文件与数据库校验通过；外部依赖可用性另行报告，不执行或重放任务。'}
    except (zipfile.BadZipFile, sqlite3.DatabaseError, KeyError, TypeError) as error:
        raise ValueError('备份包或数据库不可读：' + str(error)) from error


def _execution_environment(manifest):
    missing = sorted({ref['path'] for ref in manifest.get('external_dependencies', []) if not Path(ref['path']).exists()})
    return {'status': 'missing_dependencies' if missing else 'not_verified',
            'missing_external_dependencies': missing,
            'message': ('仍缺少外部项目、数据或 Git 注册依赖，执行环境尚未恢复。' if missing else
                        '登记的依赖路径目前可访问；解释器、Git 注册和项目运行环境尚未验证。')}


def restore_backup(archive, target_runtime):
    raw_target = Path(target_runtime).absolute()
    if raw_target.exists() and _is_link(raw_target):
        raise ValueError('恢复目标不可为链接或重解析点')
    target = raw_target.resolve()
    with tempfile.TemporaryDirectory(prefix='workbench-backup-restore-') as folder:
        staging = Path(folder)
        manifest = _checked_archive(archive, staging)
        if os.path.normcase(str(target)) != os.path.normcase(str(Path(manifest['runtime_root']).resolve())):
            raise ValueError('首版恢复只支持备份记录的本机原绝对路径')
        with WorkbenchRuntimeLease(target), MaintenanceGate(target).exclusive():
            allowed = {'workbench-service.lock', 'workbench-maintenance.lock'}
            if any(p.name not in allowed for p in target.iterdir()):
                raise ValueError('恢复目标必须为空；请先停服并把现有运行目录保留到其他位置')
            failure_marker = _begin_restore(target, manifest['id'])
            installed = []
            try:
                for relative in manifest['directories']:
                    target.joinpath(*_safe_member(relative).parts).mkdir(parents=True, exist_ok=True)
                # The DB is installed last; no application is started here.
                for relative in sorted(manifest['files'], key=lambda p: p == 'workbench.db'):
                    destination = target / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    installed.append(destination)
                    shutil.copyfile(staging / relative, destination)
                    if _hash(destination) != manifest['files'][relative]['sha256']:
                        raise ValueError('恢复后文件校验失败：' + relative)
                from .reference_paths import backup_reference_scope
                with backup_reference_scope(manifest, target):
                    _validate_database_references(target / 'workbench.db', manifest)
                migrated = (staging / 'migration' / 'reference-map.json').is_file()
                marker = {'schema': 'workbench.migration/v1' if migrated else 'workbench.restore/v1', 'backup_id': manifest['id'],
                          'restored_at': datetime.now(timezone.utc).isoformat(), 'automatic_replay': False,
                          'human_review_required': True, 'external_dependencies': manifest.get('external_dependencies', [])}
                if migrated:
                    marker['execution_environment'] = 'not_verified'
                    marker['restore_kind'] = 'same_path_backup'
                installed.append(target / RESTORE_MARKER)
                atomic_write_text(target / RESTORE_MARKER, _json(marker))
                failure_marker.unlink()
            except Exception as error:
                _failed_restore(target, installed, manifest['id'], error)
                raise
    return {'ok': True, 'id': manifest['id'], 'runtime': str(target), 'automatic_replay': False,
            'evidence_restore': {'status': 'restored', 'files_verified': len(manifest['files']),
                                 'table_counts': manifest['table_counts']},
            'execution_environment': _execution_environment(manifest),
            'message': '已恢复包内工作台数据与文件证据；请核对外部项目及 Git 注册，旧任务不会自动重放。'}
