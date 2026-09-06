#!/usr/bin/env python3
"""Sync course task cards and detailed lesson headers to the outline contract."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Inline the same extractor used by tests to avoid importing unittest modules slowly.
CONTRACT_LABELS = {
    "核心内容",
    "演示结果",
    "课内增量",
    "通过标准",
    "挑战任务",
    "验收命令",
    "最终验收命令",
}


def outline_contracts() -> dict[int, tuple[str, list[str]]]:
    text = (ROOT / "docs" / "课程大纲-Codex-FDE行动营-个人研发自动化工作台.md").read_text(encoding="utf-8")
    matches = list(re.finditer(r"^#### 第 (\d+) 讲｜(.+)$", text, re.MULTILINE))
    contracts: dict[int, tuple[str, list[str]]] = {}
    for index, match in enumerate(matches):
        lesson = int(match.group(1))
        if not 1 <= lesson <= 16:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end() : end]
        lines = []
        for line in section.splitlines():
            field = re.match(r"^- \*\*(.+?)\*\*：", line)
            if field and field.group(1) in CONTRACT_LABELS:
                lines.append(line)
        contracts[lesson] = (match.group(2), lines)
    return contracts


def replace_header(body: str, title_line: str, contract_lines: list[str]) -> str:
    lines = body.splitlines()
    if not lines:
        return title_line + "\n\n" + "\n".join(contract_lines) + "\n"
    lines[0] = title_line
    # Drop leading contract bullets immediately after title (and blank lines among them).
    i = 1
    while i < len(lines) and (not lines[i].strip() or lines[i].startswith("- **")):
        # Stop if we hit a non-contract bullet that isn't a known label
        if lines[i].startswith("- **"):
            field = re.match(r"^- \*\*(.+?)\*\*：", lines[i])
            if not field or field.group(1) not in CONTRACT_LABELS:
                break
        i += 1
    # Skip one blank after contracts if present
    rest = lines[i:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    return "\n".join([title_line, ""] + contract_lines + [""] + rest) + ("\n" if body.endswith("\n") else "")


def ensure_task_mainline(body: str) -> str:
    if "FlowERP 现场问题" in body:
        return body
    needle = "## 项目主线与评价证据"
    if needle not in body:
        # Insert after contract block
        parts = body.split("\n\n", 1)
        block = (
            f"{needle}\n\n"
            "- **FlowERP 现场问题**：本讲由 FlowERP 真实交付暴露可重复工程问题。\n"
            "- **工作台增量**：见本讲课内增量。\n"
            "- **学生学习证据**：首次判断、失败证据、修订与同伴复验。\n"
            "- **形成性评价**：保留修订前后版本与反馈。\n"
        )
        return parts[0] + "\n\n" + block + ("\n" + parts[1] if len(parts) > 1 else "")
    # Insert marker into existing section
    return body.replace(
        needle,
        needle
        + "\n\n"
        + "- **FlowERP 现场问题**：本讲由 FlowERP 真实交付暴露可重复工程问题，用于触发工作台能力验证。",
        1,
    )


def main() -> None:
    contracts = outline_contracts()
    for relative in (Path("docs/courses"), Path("docs/courses/tasks")):
        directory = ROOT / relative
        paths = [path for path in sorted(directory.glob("L??-*.md")) if not path.name.endswith("-教师备课说明.md")]
        paths.extend(path for number in range(1, 17) if (path := ROOT / f"docs/courses/L{number:02d}" / ("行动卡.md" if directory.name == "tasks" else "阅读讲义.md")).is_file())
        for path in paths:
            lesson = int(path.parent.name[1:]) if path.parent.name in ("L01", "L02") else int(path.name[1:3])
            if lesson not in contracts:
                continue
            title, contract_lines = contracts[lesson]
            title_line = f"# L{lesson:02d}｜{title}"
            body = path.read_text(encoding="utf-8")
            updated = replace_header(body, title_line, contract_lines)
            if relative.as_posix() == "docs/courses/tasks":
                updated = ensure_task_mainline(updated)
            if updated != body:
                path.write_text(updated, encoding="utf-8")
                print(f"updated {path.relative_to(ROOT)}")
            else:
                print(f"ok {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
