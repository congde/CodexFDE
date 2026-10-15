"""Named, pinned session material; historical lesson tags remain unchanged."""
import json
import re
import subprocess
from pathlib import Path


REQUIRED_SESSION_LESSONS = tuple(range(4, 15))


def _catalog(root):
    catalog = root / 'docs/courses/session-versions.json'
    if not catalog.is_file():
        return None
    try:
        data = json.loads(catalog.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('课程版本目录无法读取，请重新获取完整课程包：docs/courses/session-versions.json') from error
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('lessons'), dict):
        raise ValueError('课程版本目录格式不受支持，请重新获取完整课程包：docs/courses/session-versions.json')
    return data


def _record(data, lesson):
    item = data.get('lessons', {}).get(str(lesson))
    if item is None:
        return None
    if (not isinstance(item, dict) or not isinstance(item.get('name'), str)
            or not isinstance(item.get('ref'), str) or not isinstance(item.get('commit'), str)
            or not re.fullmatch(r'course/session/[A-Za-z0-9._/-]+', item['ref'])
            or not re.fullmatch(r'[a-f0-9]{40}', item['commit'])):
        raise ValueError(f'L{lesson:02d} 课程版本记录不完整，请核对 docs/courses/session-versions.json 或重新获取完整课程包')
    return item


def _git(root, *arguments):
    return subprocess.run(['git', *arguments], cwd=root, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', check=False, timeout=30)


def _check_version(root, lesson, item):
    tag = 'refs/tags/' + item['ref']
    status, actual = 'ready', None
    try:
        result = _git(root, 'rev-parse', '--verify', tag + '^{commit}')
        if result.returncode:
            if _git(root, 'rev-parse', '--git-dir').returncode:
                status = 'repository_missing'
            elif _git(root, 'show-ref', '--verify', '--quiet', tag).returncode:
                status = 'missing_tag'
            else:
                status = 'invalid_tag'
        else:
            actual = result.stdout.strip()
            if actual != item['commit']:
                status = 'commit_mismatch'
    except (OSError, subprocess.TimeoutExpired):
        status = 'git_unavailable'
    prefix = f'L{lesson:02d} 配套版本「{item["name"]}」，标签 {item["ref"]}：'
    messages = {
        'ready': prefix + '与课程版本清单一致。',
        'missing_tag': prefix + '本地缺少标签。先用 git remote -v 核对仓库来源，确认后执行 '
                       'git fetch origin --tags 并重试；若远端也缺少标签，由课程维护者补发标签或提供完整课程包。'
                       '不会退回旧版材料。',
        'commit_mismatch': prefix + f'提交不一致，期望 {item["commit"]}，实际 {actual}。'
                           '请保留当前练习，核对课程清单与仓库来源，在新目录获取匹配的课程版本；'
                           '不要强制覆盖标签或修改清单绕过检查。不会退回旧版材料。',
        'repository_missing': prefix + '当前目录没有可用的 Git 仓库。请进入课程仓库，'
                              '或获取包含 course-materials.bundle 的完整课程包并运行首次使用脚本。',
        'invalid_tag': prefix + '标签存在但无法解析为提交，请由课程维护者核对版本记录；不会退回旧版材料。',
        'git_unavailable': prefix + '无法完成 Git 检查，请确认 Git 已安装、当前目录可访问，并重试。',
    }
    return {'lesson': lesson, 'name': item['name'], 'ref': item['ref'],
            'expected_commit': item['commit'], 'actual_commit': actual,
            'status': status, 'ready': status == 'ready', 'message': messages[status]}


def default_session_version(repository, lesson):
    root = Path(repository)
    data = _catalog(root)
    item = _record(data, lesson) if data is not None else None
    if item is None:
        return None
    diagnostic = _check_version(root, lesson, item)
    if not diagnostic['ready']:
        raise ValueError(diagnostic['message'])
    return dict(item)


def _remote_status(root, remote, items):
    # Only named remotes are accepted; inspection never fetches or moves refs.
    if not isinstance(remote, str) or remote.startswith('-') or not re.fullmatch(r'[A-Za-z0-9._/-]+', remote):
        return {'name': remote, 'ready': False, 'status': 'invalid_remote',
                'message': '请使用 git remote -v 中的远端名称，例如 origin。'}
    try:
        result = _git(root, 'ls-remote', '--tags', remote, 'refs/tags/course/session/*')
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode:
        return {'name': remote, 'ready': False, 'status': 'remote_unavailable',
                'message': '远端查询未完成，不能据此判断标签缺失。请检查网络和 git remote -v 后重试。'}
    refs = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2:
            refs[fields[1]] = fields[0]
    missing, mismatched = [], []
    for item in items:
        ref = 'refs/tags/' + item['ref']
        actual = refs.get(ref + '^{}', refs.get(ref))
        if actual is None:
            missing.append(item['ref'])
        elif actual != item['commit']:
            mismatched.append({'ref': item['ref'], 'expected_commit': item['commit'], 'actual_commit': actual})
    ready = not missing and not mismatched
    message = '远端配套标签与课程版本清单一致。' if ready else (
        f'远端缺少 {len(missing)} 个配套标签，另有 {len(mismatched)} 个提交不一致。'
        '缺少标签须由课程维护者发布匹配版本或提供完整课程包；仅在学生端 fetch 不能补齐远端缺失。'
        '提交不一致须先核对版本来源，不要强制覆盖标签。'
    )
    return {'name': remote, 'ready': ready, 'status': 'ready' if ready else 'incomplete',
            'missing_refs': missing, 'mismatched_refs': mismatched, 'message': message}


def session_versions_status(repository, *, remote=None):
    """Inspect required classroom materials locally; remote inspection is opt-in."""
    root = Path(repository)
    versions, errors, items = [], [], []
    try:
        data = _catalog(root)
        if data is None:
            raise ValueError('缺少课程版本目录 docs/courses/session-versions.json，请重新获取完整课程包。')
        for lesson in REQUIRED_SESSION_LESSONS:
            try:
                item = _record(data, lesson)
                if item is None:
                    raise ValueError(f'L{lesson:02d} 缺少配套版本记录，请重新获取完整课程包。')
            except ValueError as error:
                errors.append(str(error))
                versions.append({'lesson': lesson, 'status': 'invalid_record', 'ready': False, 'message': str(error)})
                continue
            items.append(item)
            diagnostic = _check_version(root, lesson, item)
            versions.append(diagnostic)
            if not diagnostic['ready']:
                errors.append(diagnostic['message'])
    except ValueError as error:
        errors.append(str(error))
    result = {'ready': not errors, 'required_lessons': list(REQUIRED_SESSION_LESSONS),
              'versions': versions, 'errors': errors}
    if remote is not None:
        result['remote'] = _remote_status(root, remote, items) if len(items) == len(REQUIRED_SESSION_LESSONS) else {
            'name': remote, 'ready': False, 'status': 'not_checked',
            'message': '课程版本清单不完整，尚未核对远端。请先获取完整课程清单。'}
    return result
