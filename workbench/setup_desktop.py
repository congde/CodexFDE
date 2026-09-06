"""Prepare the local course environment; never replace existing course tags."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .course_versions import default_session_version

ROOT = Path(__file__).resolve().parent.parent


def run(command, root, *, timeout=300, env=None):
    result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout, env=env)
    if result.returncode:
        # Keep dependency output local, rather than exposing configured index URLs.
        logs = root / '.runtime' / 'startup-logs'
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / 'environment-setup.log'
        log.write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
        raise RuntimeError(f'准备步骤未成功，原文件已保留。请将此日志交给老师检查：{log}')
    return result.stdout.strip()


def initialize_reference(root):
    """Record only the verified distribution, never the learner's extra files."""
    head = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=root,
                          capture_output=True)
    if head.returncode == 0:
        return
    manifest_path = root / 'package-manifest.json'
    if not manifest_path.is_file():
        raise RuntimeError('源码缺少初始版本和安装清单，请使用完整课程包。')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    files = manifest.get('files') if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or not isinstance(files, dict) or not files:
        raise ValueError('安装清单损坏，请重新获取课程包。')
    for name, expected in files.items():
        if not isinstance(name, str):
            raise ValueError('安装清单包含无效路径，未建立初始版本。')
        path = root / name
        if (not isinstance(name, str) or name.startswith(('/', '\\')) or '\\' in name
                or any(part in {'..', '.git', '.runtime', '.venv'} for part in Path(name).parts)
                or any(ord(char) < 32 for char in name) or path.is_symlink()
                or not path.resolve().is_relative_to(root) or not path.is_file()):
            raise ValueError('安装清单包含无效路径，未建立初始版本。')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'首次安装前文件已经变化：{name}。请保留修改，在新的目录解压课程包。')
    index = Path(run(['git', 'rev-parse', '--git-path', 'index'], root))
    if not index.is_absolute():
        index = root / index
    if index.exists():
        raise RuntimeError('当前目录已有暂存修改，未覆盖。请在新的目录解压课程包。')
    with tempfile.TemporaryDirectory(prefix='course-reference-') as temporary:
        temp = Path(temporary)
        paths = temp / 'paths'
        paths.write_bytes(b'\0'.join(name.encode('utf-8') for name in [*files, 'package-manifest.json']) + b'\0')
        env = dict(os.environ, GIT_INDEX_FILE=str(temp / 'index'), GIT_LITERAL_PATHSPECS='1',
                   GIT_AUTHOR_NAME='Course package reference', GIT_AUTHOR_EMAIL='course-package@localhost',
                   GIT_COMMITTER_NAME='Course package reference', GIT_COMMITTER_EMAIL='course-package@localhost')
        run(['git', 'add', '-f', '--pathspec-from-file=' + str(paths),
             '--pathspec-file-nul'], root, env=env)
        tree = run(['git', 'write-tree'], root, env=env)
        commit = run(['git', '-c', 'commit.gpgsign=false', 'commit-tree', tree, '-m',
                      'Course package reference; not learner work or human approval'], root, env=env)
        # Fail if a concurrent operation has already created this branch.
        run(['git', 'update-ref', 'HEAD', commit, '0' * 40], root)
        # No working files are checked out or changed.
        run(['git', 'read-tree', commit], root)


def prepare_materials(root):
    if not shutil.which('git'):
        raise RuntimeError('还没有找到 Git。请完成 L00 的 Git 安装，重新打开本窗口后再试。')
    bundle = root / 'course-materials.bundle'
    if not (root / '.git').exists():
        if not bundle.is_file():
            raise RuntimeError('课程材料不完整：缺少版本记录。请使用老师提供的完整课程包或克隆仓库。')
        run(['git', 'init', '--quiet'], root)
    if bundle.is_file():
        # Atomic fetch refuses any moved tag; no force, reset, checkout or deletion.
        run(['git', 'fetch', '--atomic', '--no-write-fetch-head', str(bundle),
             'refs/tags/course/*:refs/tags/course/*'], root)
    catalog = root / 'docs/courses/session-versions.json'
    if not catalog.is_file():
        raise RuntimeError('缺少课程版本目录，请重新获取完整课程包。')
    data = json.loads(catalog.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('lessons'), dict):
        raise RuntimeError('课程版本目录损坏，请重新获取完整课程包。')
    for lesson in range(4, 15):
        if default_session_version(root, lesson) is None:
            raise RuntimeError(f'缺少 L{lesson:02d} 配套材料，请重新获取完整课程包。')
    for lesson in range(1, 17):
        run(['git', 'rev-parse', '--verify', f'refs/tags/course/l{lesson:02d}-start^{{commit}}'], root)
    initialize_reference(root)


def prepare(root=ROOT):
    root = Path(root).resolve()
    if sys.version_info < (3, 10):
        raise RuntimeError('需要 Python 3.10 或更新版本，请按 L00 更新后重试。')
    print('1/3 检查并准备课程材料……', flush=True)
    prepare_materials(root)
    python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    print('2/3 准备本项目独立运行环境，首次使用可能需要几分钟……', flush=True)
    if not python.is_file():
        if (root / '.venv').exists():
            raise RuntimeError('已有运行环境不完整。请让老师检查 .venv 目录；不会自动删除其中内容。')
        run([sys.executable, '-X', 'utf8', '-m', 'venv', str(root / '.venv')], root)
    run([str(python), '-X', 'utf8', '-m', 'pip', 'install', '--disable-pip-version-check', '-e', '.'], root)
    print('3/3 检查工作台组件……', flush=True)
    run([str(python), '-X', 'utf8', '-c',
         'import workbench.desktop, workbench.web_execution, flowerp, eval.harness'], root)
    print('准备完成。现在可以双击“打开工作台”。授权 AI 写代码前，还需按 L00 完成 Codex 安装与登录。', flush=True)
    return 0


def main():
    try:
        return prepare()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'暂未准备完成：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
