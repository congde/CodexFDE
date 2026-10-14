"""Content identity of the personal controller, including its checks and rules."""
import hashlib
import json
from pathlib import Path

CONTROL_ROOT = Path(__file__).resolve().parents[1]


def source_identity(root=CONTROL_ROOT):
    root = Path(root).resolve()
    paths = {root / name for name in ('AGENTS.md', 'pyproject.toml', 'main.py')}
    for name in ('workbench', 'eval', 'tests', 'agent', 'harness_web', 'workbench_web'):
        directory = root / name
        if directory.is_dir():
            paths.update(path for path in directory.rglob('*') if path.is_file()
                         and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'))
    files = {}
    for path in sorted(paths):
        if path.is_file():
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('控制源码不得通过链接指向外部：' + str(path))
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'sha256': digest, 'files': files}
