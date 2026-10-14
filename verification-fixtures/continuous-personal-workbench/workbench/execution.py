"""Real Codex/subprocess adapter and complete candidate file observations."""
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess

INVENTORY_WRITES = frozenset({'flowerp/inventory_export.py', 'flowerp/import_export.py',
                              'tests/test_inventory_export.py', 'tests/test_l04_inventory_delivery.py'})
ENUMERATION_OMISSIONS = frozenset({'.git', '.runtime', '.venv', '__pycache__', '.pytest_cache', '.mypy_cache', 'cache'})
DESKTOP_IPC_KEYS = ('CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID',
                    'CODEX_INTERNAL_ORIGINATOR_OVERRIDE')
_SECRET = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}')


def valid_timeout(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def sensitive_path(name):
    path = PurePosixPath(name.lower())
    return (any(part.startswith('.env') or part in {'.git', '.aws', '.ssh', '.codex', '.agents',
                '.venv', '.runtime', '__pycache__', '.pytest_cache', '.mypy_cache', 'cache', 'node_modules'} for part in path.parts)
            or path.suffix in {'.db', '.sqlite', '.sqlite3', '.pem', '.key', '.p12', '.pfx'}
            or any(token in part for part in path.parts
                   for token in ('credentials', 'auth.json', 'secret', 'token.json')))


def linked(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def require_unlinked_path(path):
    raw = Path(path).absolute()
    for component in (raw, *raw.parents):
        if os.path.lexists(component) and linked(component):
            raise ValueError('路径包含链接或重解析点：' + str(component))


def file_hashes(workspace):
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise ValueError('工作目录不存在')
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in list(dirs):
            path = base / name
            if linked(path):
                files[path.relative_to(root).as_posix()] = 'link:' + hashlib.sha256(str(path.resolve()).encode()).hexdigest()
                dirs.remove(name)
            elif name in ENUMERATION_OMISSIONS:
                dirs.remove(name)
        for name in names:
            path = base / name
            relative = path.relative_to(root).as_posix()
            if name == '.git' and not linked(path):
                continue
            if path.suffix in {'.pyc', '.pyo'} and not linked(path):
                continue
            files[relative] = ('link:' + hashlib.sha256(str(path.resolve()).encode()).hexdigest()
                               if linked(path) else hashlib.sha256(path.read_bytes()).hexdigest())
    return dict(sorted(files.items()))


def sensitive_files(workspace, hashes):
    root = Path(workspace)
    return sorted(name for name, digest in hashes.items()
                  if sensitive_path(name) or digest.startswith('link:')
                  or _SECRET.search((root / name).read_bytes()))


def hash_diff(before, after, allowed, workspace):
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    sensitive = sensitive_files(workspace, after)
    return {'before': before, 'after': after, 'changed': changed,
            'violations': sorted(set(changed) - set(allowed)), 'sensitive': sensitive}


def _kill_tree(process, job=None):
    if job is not None:
        job.terminate()
    elif os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def run_command(argv, *, cwd, timeout, input_text=None, headless_codex=False):
    invocation = {'argv': list(map(str, argv)), 'cwd': str(Path(cwd).resolve()),
                  'timeout': timeout, 'stdin': input_text}
    result = {'invocation': invocation, 'exitcode': None, 'stdout': '', 'stderr': '',
              'timed_out': False, 'error': None}
    process = None
    job = None
    try:
        if not valid_timeout(timeout):
            raise ValueError('必须声明有效超时')
        env = os.environ.copy()  # Existing login is inherited; no credentials are copied or injected.
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env.pop('PYTHONPATH', None)
        if headless_codex:
            for key in DESKTOP_IPC_KEYS:
                env.pop(key, None)
        if os.name == 'nt':
            from .windows_process import WindowsJob
            job = WindowsJob()
        process = subprocess.Popen(invocation['argv'], cwd=invocation['cwd'], env=env,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
            start_new_session=os.name != 'nt',
            creationflags=(subprocess.CREATE_NO_WINDOW | 4) if os.name == 'nt' else 0)
        if job is not None:
            job.assign_and_resume(process)
        try:
            stdout, stderr = process.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            result['timed_out'] = True
            _kill_tree(process, job)
            stdout, stderr = process.communicate(timeout=10)
        result.update(stdout=stdout, stderr=stderr, exitcode=process.returncode)
    except Exception as error:
        if process is not None and process.poll() is None:
            _kill_tree(process, job)
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        if job is not None:
            job.close()
    return result


def run_codex(*, workspace, prompt, timeout):
    executable = shutil.which('codex') or 'codex'
    argv = [executable, '-a', 'never', 'exec', '--json', '--sandbox', 'workspace-write',
            '--skip-git-repo-check', '--ephemeral', '-C', str(workspace), '-']
    return run_command(argv, cwd=workspace, timeout=timeout, input_text=prompt, headless_codex=True)
