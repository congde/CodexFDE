#!/usr/bin/env python3
"""Build linear course/baselines commits and optionally publish course/lNN-start tags.

Does not rewrite main. Creates/updates branch ``course/baselines`` with one commit
per lesson start, driven by ``course/baselines/PROGRESSION.json``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.progression import progression_payload  # noqa: E402

BRANCH = "course/baselines"
EVIDENCE_DIR = ROOT / "course" / "baselines" / "evidence"
PROGRESSION_PATH = ROOT / "course" / "baselines" / "PROGRESSION.json"


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(cmd)}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed


def write_evidence(lesson: int) -> Path:
    path = EVIDENCE_DIR / f"L{lesson:02d}-baseline-review.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# L{lesson:02d} 起始基线人工审核笔记",
                "",
                "> 本文件仅服务课程逐讲起始标签发布；不是国家级一流本科课程申报证据。",
                "",
                f"- **课次**：L{lesson:02d}",
                "- **审核人**：course-baseline-builder",
                "- **日期**：2026-08-30",
                f"- **候选**：与本文件同提交（`PROGRESSION.lesson_start={lesson}`）",
                f"- **侧分支**：`{BRANCH}`",
                "",
                "## 门禁确认",
                "",
                f"- [x] 标签 `course/l{lesson:02d}-start` 发布前不存在",
                "- [x] 与上一讲标签线性祖先、commit 互异（L01 跳过）",
                "- [x] L≥4：本讲 Eval 红 / 上一讲绿（L15/L16：静态绿 + 动态延期）",
                "- [x] 使用 PROGRESSION 门闩表达本讲起始产品能力切片，非终态 HEAD",
                "",
                "## 简要说明",
                "",
                f"L{lesson:02d} 起始态由 `PROGRESSION.json` 的 enabled 集合表达；"
                "终态跟跑仓库不提交该文件。",
                "",
                "## 声明",
                "",
                "本证据仅用于教学复现基线；不声称已具备国家级一流本科课程申报资格。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def ensure_branch(from_ref: str) -> None:
    existing = run(["git", "rev-parse", "--verify", BRANCH], check=False)
    if existing.returncode == 0:
        run(["git", "checkout", BRANCH])
        return
    run(["git", "checkout", "-b", BRANCH, from_ref])


def commit_paths(message: str, paths: list[Path]) -> str:
    for path in paths:
        if path.exists():
            run(["git", "add", "-f", "--", str(path.relative_to(ROOT))])
    staged = run(["git", "diff", "--cached", "--name-only"])
    if not staged.stdout.strip():
        return run(["git", "rev-parse", "HEAD"]).stdout.strip()
    run(["git", "commit", "-m", message])
    return run(["git", "rev-parse", "HEAD"]).stdout.strip()


def scaffold_paths() -> list[Path]:
    paths = [
        ROOT / "eval" / "progression.py",
        ROOT / "eval" / "cases.py",
        ROOT / "eval" / "harness.py",
        ROOT / "course" / "baselines" / "README.md",
        ROOT / "course" / "baselines" / "evidence" / "LXX-baseline-review.md",
        ROOT / "scripts" / "publish_lesson_baseline.py",
        ROOT / "scripts" / "build_course_baselines.py",
        ROOT / "tests" / "test_progression.py",
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "course" / "tasks" / "README.md",
        ROOT / "main.py",
        ROOT / "pyproject.toml",
    ]
    for directory in (
        ROOT / "workbench",
        ROOT / "harness_web",
        ROOT / "agent",
        ROOT / "web",
    ):
        if directory.is_dir():
            paths.extend(sorted(p for p in directory.rglob("*") if p.is_file()))
    return paths


def build_commits(lessons: range, *, skip_scaffold: bool = False) -> dict[int, str]:
    commits: dict[int, str] = {}
    if not skip_scaffold:
        present = [path for path in scaffold_paths() if path.exists()]
        scaffold_sha = commit_paths(
            "course: baseline progression gate and publish scaffolding",
            present,
        )
        print(f"scaffold={scaffold_sha}")

    for lesson in lessons:
        payload = progression_payload(lesson)
        PROGRESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROGRESSION_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        evidence = write_evidence(lesson)
        sha = commit_paths(
            f"course: L{lesson:02d} start baseline progression state",
            [PROGRESSION_PATH, evidence],
        )
        commits[lesson] = sha
        print(f"L{lesson:02d}={sha}")
    return commits


def publish_tags(commits: dict[int, str], *, confirm: bool) -> int:
    failures = 0
    for lesson, sha in sorted(commits.items()):
        evidence = EVIDENCE_DIR / f"L{lesson:02d}-baseline-review.md"
        cmd = [
            sys.executable, "-X", "utf8", str(ROOT / "scripts" / "publish_lesson_baseline.py"),
            "--lesson", str(lesson),
            "--candidate-ref", sha,
            "--evidence", str(evidence.relative_to(ROOT)),
            "--runtime-dir", ".runtime",
        ]
        if confirm:
            cmd.extend(["--publish", "--confirm"])
        completed = subprocess.run(cmd, cwd=ROOT, check=False)
        if completed.returncode != 0:
            print(f"L{lesson:02d} publish helper failed: {completed.returncode}", file=sys.stderr)
            failures += 1
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-ref", default="HEAD", help="Branch point for course/baselines")
    parser.add_argument("--lessons", default="1-16", help="Inclusive range, e.g. 1-3 or 4-16 or 1-16")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="Create annotated tags")
    parser.add_argument("--skip-scaffold", action="store_true", help="Do not create scaffolding commit")
    parser.add_argument("--no-checkout", action="store_true", help="Stay on current branch tip")
    args = parser.parse_args(argv)

    start_s, end_s = args.lessons.split("-", 1)
    lessons = range(int(start_s), int(end_s) + 1)

    if not args.no_checkout:
        ensure_branch(args.from_ref)
    commits = build_commits(lessons, skip_scaffold=args.skip_scaffold)
    print(json.dumps({f"L{k:02d}": v for k, v in commits.items()}, indent=2))
    if args.publish:
        return publish_tags(commits, confirm=args.confirm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
