"""Named, pinned session material; historical lesson tags remain unchanged."""
import json
import re
import subprocess
from pathlib import Path


def default_session_version(repository, lesson):
    root = Path(repository)
    catalog = root / 'docs/courses/session-versions.json'
    if not catalog.is_file():
        return None
    data = json.loads(catalog.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('lessons'), dict):
        raise ValueError('课程版本目录格式不受支持，请更新课程材料')
    item = data.get('lessons', {}).get(str(lesson))
    if item is None:
        return None
    if (not isinstance(item, dict) or not isinstance(item.get('name'), str)
            or not isinstance(item.get('ref'), str) or not isinstance(item.get('commit'), str)
            or not re.fullmatch(r'course/session/[A-Za-z0-9._/-]+', item['ref'])
            or not re.fullmatch(r'[a-f0-9]{40}', item['commit'])):
        raise ValueError('课程版本记录不完整，请更新课程材料')
    result = subprocess.run(['git', 'rev-parse', '--verify', 'refs/tags/' + item['ref'] + '^{commit}'],
                            cwd=root, capture_output=True, text=True, check=False)
    if result.returncode or result.stdout.strip() != item['commit']:
        raise ValueError('本讲配套版本缺失或不一致，请补齐课程版本后再执行；不会退回旧版材料')
    return dict(item)
