"""Maintainer audit of actual missing capabilities, never evidence of student authorship.

Run from the reference repository with its virtual-environment Python. Every
attempt and candidate is retained. Reference repair restores only overlaid files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from workbench.course_mainline import lesson_contract
from workbench.course_snapshot import prepare_source_snapshot
from workbench.course_workspace import LessonSubprocessEvalRunner


def audit_lesson(source: Path, runtime: Path, number: int) -> dict:
    contract = lesson_contract(number)
    result = {"lesson": number, "title": contract.title, "basis": "maintainer_reference_repair",
              "student_achievement": False, "real_codex_execution": False}
    if contract.dynamic_eval_required:
        return {**result, "status": "requires_live_request", "reason": "必须另行建立本次需求与新增Eval，静态参考修复不能证明本讲完成"}
    if not contract.eval_cases:
        return {**result, "status": "requires_observation", "reason": "需本讲具名活动或独立验收，当前没有可运行课程Eval"}
    snapshot = prepare_source_snapshot(source, runtime, number, f"TASK-AUDIT-L{number:02d}")
    candidate = Path(snapshot["path"])
    result.update(workspace=str(candidate), source_tree_sha256=snapshot["source_tree_sha256"],
                  start_commit=snapshot["snapshot_commit"], overlays=snapshot["student_start"]["applied_overlays"])
    pre = LessonSubprocessEvalRunner(candidate, runtime, f"L{number:02d}", contract.eval_cases, "pre")()
    result["pre"] = pre
    if pre["summary"]["decision"] != "block":
        return {**result, "status": "missing_start_gap", "reason": "起点已绿，不能据此声称学生需要构造本讲能力"}
    restored = []
    for relative in sorted(set(result["overlays"])):
        origin, destination = source / relative, candidate / relative
        if not origin.resolve().is_relative_to(source) or not destination.resolve().is_relative_to(candidate):
            raise ValueError("参考恢复路径越界")
        if not origin.is_file():
            continue
        before = destination.read_bytes() if destination.is_file() else None
        content = origin.read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        if before != content:
            restored.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
    diff = subprocess.run(["git", "diff", "--", "."], cwd=candidate, text=True, encoding="utf-8", capture_output=True, check=True)
    diff_path = runtime / f"L{number:02d}-reference.diff"
    diff_path.write_text(diff.stdout, encoding="utf-8")
    post = LessonSubprocessEvalRunner(candidate, runtime, f"L{number:02d}", contract.eval_cases, "post")()
    allowed = [item.rstrip("/") for item in contract.write_scope]
    out_of_scope = [item["path"] for item in restored if not any(item["path"] == scope or item["path"].startswith(scope + "/") for scope in allowed)]
    verified = bool(restored) and not out_of_scope and post["summary"]["decision"] == "pass"
    return {**result, "post": post, "restored": restored, "diff_path": str(diff_path), "out_of_scope": out_of_scope,
            "status": "reference_red_green_verified" if verified else "reference_repair_failed",
            "limitation": "只证明登记的缺口能被本讲Eval发现、参考修复后通过；不证明所有教学能力均已剥离，也不证明学生自主实现"}


def main() -> int:
    parser = argparse.ArgumentParser(description="逐讲隔离起点与参考修复审计，保留全部失败证据")
    parser.add_argument("--lesson", type=int, choices=range(1, 17), action="append")
    parser.add_argument("--runtime-dir", default=".runtime/course-start-audits")
    args = parser.parse_args()
    source = Path.cwd().resolve()
    runtime = Path(args.runtime_dir).resolve() / uuid.uuid4().hex
    runtime.mkdir(parents=True, exist_ok=False)
    results = []
    for number in args.lesson or range(1, 17):
        try:
            result = audit_lesson(source, runtime, number)
        except Exception as exc:
            result = {"lesson": number, "status": "audit_error", "error": f"{type(exc).__name__}: {exc}"}
        results.append(result)
        (runtime / "audit.json").write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"L{number:02d}: {result['status']}", flush=True)
    print(f"完整证据：{runtime / 'audit.json'}")
    return 0 if all(item["status"] == "reference_red_green_verified" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
