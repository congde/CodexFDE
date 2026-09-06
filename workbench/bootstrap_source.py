"""Fingerprint the local control code checked during Workbench V0 acceptance."""
import hashlib
import json
import os
from pathlib import Path

DIRECTORIES = ('workbench', 'eval', 'agent', 'tests', '.codex')
ROOT_FILES = ('AGENTS.md', 'FDE_SPEC.md', 'pyproject.toml', 'main.py')
SUFFIXES = {'.py', '.json', '.toml', '.md', '.yaml', '.yml', '.ps1', '.sh'}


def control_source_snapshot(repository):
    root = Path(repository).resolve()
    paths = []
    for name in DIRECTORIES:
        base = root / name
        if not base.exists():
            continue
        if base.is_symlink() or getattr(base.stat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('工作台验收源码不允许链接目录或文件')
        for current, folders, files in os.walk(base, followlinks=False):
            folders[:] = [f for f in folders if f not in {'__pycache__', '.runtime', '.venv', 'node_modules'}]
            for name in folders + files:
                p = Path(current) / name
                if p.is_symlink() or getattr(p.stat(), 'st_file_attributes', 0) & 0x400:
                    raise ValueError('工作台验收源码不允许链接目录或文件')
            paths.extend(Path(current) / name for name in files
                         if Path(name).suffix.lower() in SUFFIXES and not name.startswith('.env'))
    paths.extend(root / name for name in ROOT_FILES if (root / name).is_file())
    manifest = {}
    for path in sorted(paths):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('工作台验收源码越界')
        manifest[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not any(p.startswith('workbench/') for p in manifest) or not any(p.startswith('eval/') for p in manifest):
        raise ValueError('工作台验收缺少 workbench 或 eval 源码')
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return {'schema':1, 'root':str(root), 'files':manifest, 'sha256':digest}


def verify_control_source(snapshot, repository=None):
    if not isinstance(snapshot, dict) or snapshot.get('schema') != 1 or not snapshot.get('root'):
        raise ValueError('Ticket A 缺少工作台源码指纹，请独立重新复验')
    root = Path(repository or snapshot['root']).resolve()
    if str(root) != snapshot['root']:
        raise ValueError('Ticket A 属于另一工作区，不能沿用其验收')
    current = control_source_snapshot(root)
    if current != snapshot:
        raise ValueError('工作台源码已变化，请独立重新复验 Ticket A')
    return current
