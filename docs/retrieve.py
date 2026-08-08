"""按 Tag 检索课程内容（配合《课程内容知识库.md》使用）。

学员/智能体通过 tag 获取对应讲次的完整内容。
运行示例：
    python retrieve.py lesson-05
    python retrieve.py --list
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KB_PATH = Path(__file__).resolve().parent / "课程内容库.md"


def parse_kb(path: Path = KB_PATH) -> dict[str, str]:
    """把课程内容库解析为 {tag(课程名): 小节内容}。兼容 `## tag:` 与 `### tag：` 两种写法。"""
    text = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    pattern = re.compile(r"^#{2,3}\s*tag[：:]\s*(\S+)\n(.*?)(?=^#{1,3}\s*tag[：:]|\Z)", re.M | re.S)
    for m in pattern.finditer(text):
        tag, body = m.group(1), m.group(2).rstrip()
        sections[tag] = body
    return sections


def main() -> int:
    parser = argparse.ArgumentParser(description="按 Tag 检索课程内容")
    parser.add_argument("tag", nargs="?", help="课程 Tag，如 lesson-05")
    parser.add_argument("--list", action="store_true", help="列出所有可用 Tag")
    args = parser.parse_args()

    sections = parse_kb()
    if args.list:
        print("可用 Tag：")
        for tag in sections:
            first = sections[tag].splitlines()[0] if sections[tag] else ""
            print(f"  {tag:12s} {first}")
        return 0

    if not args.tag:
        parser.print_help()
        return 2

    body = sections.get(args.tag)
    if body is None:
        print(f"未找到 Tag: {args.tag}。可用：{', '.join(sections)}")
        return 1
    print(f"## tag: {args.tag}\n{body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())