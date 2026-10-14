"""Read a six-section contract without approving its business contents."""
from dataclasses import dataclass
from pathlib import Path
import re

TITLES = ('来源', '目标', '非目标', '约束', '验收用例', '完成定义')

@dataclass(frozen=True)
class ParsedSpec:
    source: str
    goal: str
    non_goals: str
    constraints: str
    acceptance: str
    done: str

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

def parse_spec(text):
    headings = []
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker and not (marker[1][0] == '`' and '`' in marker[2]):
            fence = (marker[1][0], len(marker[1]))
        else:
            heading = re.match(r'^##[ \t]+(.+?)\s*$', line)
            if heading:
                headings.append((heading[1], offset, offset + len(line)))
        offset += len(line)
    if fence:
        raise ValueError('代码围栏未闭合')
    names = [item[0] for item in headings]
    unknown = [name for name in names if name not in TITLES]
    if unknown:
        raise ValueError('未知章节：' + '、'.join(unknown))
    duplicate = [name for name in TITLES if names.count(name) > 1]
    if duplicate:
        raise ValueError('重复章节：' + '、'.join(duplicate))
    missing = [name for name in TITLES if name not in names]
    if missing:
        raise ValueError('缺少章节：' + '、'.join(missing))
    if tuple(names) != TITLES:
        raise ValueError('章节顺序错误')
    contents = []
    for index, (name, start, body_start) in enumerate(headings):
        end = headings[index + 1][1] if index + 1 < len(headings) else len(text)
        body = text[body_start:end].strip()
        if not body:
            raise ValueError('空章节：' + name)
        contents.append(body)
    return ParsedSpec(*contents)

def load_spec(path):
    return parse_spec(Path(path).read_text(encoding='utf-8-sig'))
