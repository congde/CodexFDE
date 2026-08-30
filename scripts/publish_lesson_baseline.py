#!/usr/bin/env python3
"""Wrap course-baseline-audit / course-baseline-publish for one lesson."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit then optionally publish one course baseline tag")
    parser.add_argument("--lesson", type=int, choices=range(1, 17), required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--runtime-dir", default=".runtime")
    parser.add_argument("--publish", action="store_true", help="Run publish after a successful audit")
    parser.add_argument("--confirm", action="store_true", help="Required with --publish to create the tag")
    args = parser.parse_args(argv)

    root = Path.cwd()
    evidence = Path(args.evidence)
    if not evidence.is_file():
        print(f"evidence missing: {evidence}", file=sys.stderr)
        return 2

    audit_cmd = [
        sys.executable, "-X", "utf8", "-m", "workbench.cli", "course-baseline-audit",
        "--lesson", str(args.lesson),
        "--candidate-ref", args.candidate_ref,
        "--evidence", str(evidence),
        "--runtime-dir", args.runtime_dir,
    ]
    completed = subprocess.run(audit_cmd, cwd=root, check=False)
    report = root / args.runtime_dir / "baseline-audits" / f"L{args.lesson:02d}.json"
    if report.is_file():
        payload = json.loads(report.read_text(encoding="utf-8"))
        print(json.dumps({"accepted": payload.get("accepted"), "checks": payload.get("checks")},
                         ensure_ascii=False, indent=2))
        if not payload.get("accepted"):
            return 1
    if completed.returncode != 0:
        return completed.returncode
    if not args.publish:
        return 0
    if not args.confirm:
        print("--publish requires --confirm", file=sys.stderr)
        return 2
    publish_cmd = [
        sys.executable, "-X", "utf8", "-m", "workbench.cli", "course-baseline-publish",
        "--lesson", str(args.lesson),
        "--candidate-ref", args.candidate_ref,
        "--evidence", str(evidence),
        "--runtime-dir", args.runtime_dir,
        "--confirm",
    ]
    return subprocess.run(publish_cmd, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
