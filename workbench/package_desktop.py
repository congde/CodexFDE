"""Build a local source distribution with its pinned course material bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from .course_snapshot import source_paths
from .course_versions import default_session_version


def build(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f'输出文件已存在，请使用新的版本文件名：{output}')
    refs = [f'refs/tags/course/l{n:02d}-start' for n in range(1, 17)]
    for number in range(4, 15):
        item = default_session_version(root, number)
        if item is None:
            raise ValueError(f'L{number:02d} 配套课程材料尚未准备好')
        refs.append('refs/tags/' + item['ref'])
    files = source_paths(root, root / '.runtime')
    # Never include generated reports or temporary build trees from source folders.
    files = [p for p in files if p.relative_to(root).as_posix() != 'eval/report.json'
             and not any(part in {'reports', '_build', '.cache'} for part in p.relative_to(root).parts)]
    required = {'首次使用.cmd', '打开工作台.cmd', 'workbench/setup_desktop.py',
                'workbench_web/index.html', 'web/index.html', 'docs/courses/session-versions.json'}
    if missing := required - {p.relative_to(root).as_posix() for p in files}:
        raise ValueError('缺少安装文件：' + ', '.join(sorted(missing)))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='workbench-package-') as temp:
        bundle = Path(temp) / 'course-materials.bundle'
        result = subprocess.run(['git', 'bundle', 'create', str(bundle), *refs], cwd=root,
                                capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('课程材料打包失败：' + result.stderr)
        payload = {p.relative_to(root).as_posix(): p.read_bytes() for p in files}
        payload['course-materials.bundle'] = bundle.read_bytes()
        payload['先读我.md'] = (
            '# 课程工作台\n\n'
            '请先把整个文件夹解压到自己的课程目录，不要在压缩包内直接运行。\n\n'
            '1. 按 L00 安装 Python 3.10 或更新版本和 Git。\n'
            '2. 双击“首次使用.cmd”，等待出现“准备完成”。首次安装需要联网。\n'
            '3. 双击“打开工作台.cmd”，在网页填写自己的姓名或课堂昵称。\n\n'
            'L01～L03 按讲义亲手搭建；L04 起通过工作台交付。使用 Codex 写代码前，'
            '还需完成 L00 的 Codex 安装登录，并核对、授权本次修改。\n\n'
            '以后直接打开工作台。原任务保存在本目录的 .runtime，不要删除或与别人的目录混用。'
            '安装失败时保留窗口提示，把日志位置交给老师。\n\n'
            '本包包含当前参考源码与固定课程起点，不包含任何学员作业、运行数据库或登录信息。'
            '参考源码可运行不等于学生已完成课程；正式成品验收状态见 docs/reference/工作台成品验收.md。\n'
        ).encode('utf-8')
        manifest = {'schema_version': 1, 'files': {
            name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())}}
        payload['package-manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
        # Exclusive creation preserves existing exports, even if another build races us.
        with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payload.items()):
                archive.writestr('课程工作台/' + name, data)
    return {'path': str(output), 'files': len(payload), 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description='生成带配套材料的课程工作台安装包')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parent.parent, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
