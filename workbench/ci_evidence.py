from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def build_envelope(report_path: str | Path, *, env: dict[str, str] | None = None) -> dict:
    report_file = Path(report_path)
    if not report_file.is_file():
        raise FileNotFoundError(f"Harness 报告不存在：{report_file}")
    raw = report_file.read_bytes()
    report = json.loads(raw.decode("utf-8"))
    environ = env if env is not None else os.environ
    sha = str(environ.get("GITHUB_SHA") or "").strip()
    run_id = str(environ.get("GITHUB_RUN_ID") or "").strip()
    if not sha or not run_id:
        raise SystemExit("Evidence Envelope 缺少 GITHUB_SHA 或 GITHUB_RUN_ID")
    summary = report.get("summary") if isinstance(report, dict) else {}
    return {
        "commit_sha": sha,
        "run_id": run_id,
        "workflow": str(environ.get("GITHUB_WORKFLOW") or ""),
        "runner_os": str(environ.get("RUNNER_OS") or ""),
        "python": sys.version.split()[0],
        "suite": report.get("suite") if isinstance(report, dict) else "",
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "report_decision": (summary or {}).get("decision"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind a Harness report to a CI run identity")
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        envelope = build_envelope(args.report)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(envelope, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
