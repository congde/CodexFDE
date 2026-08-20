from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Callable

from eval.harness import run_suite
from .spec import load_spec
from .task_store import TaskStore


class ControlledExecutionError(RuntimeError):
    def __init__(self, message: str, evidence: dict) -> None:
        super().__init__(message)
        self.evidence = evidence


def prepare_task(store: TaskStore, task_id: str, actor: str = "system") -> dict:
    task = store.get(task_id)
    if task["status"] != "queued":
        raise ValueError("只有 queued 任务可以准备 Spec")
    spec_path = Path(task.get("spec_path") or "FDE_SPEC.md").resolve()
    workspace = Path.cwd().resolve()
    runtime = Path(store.path).resolve().parent
    allowed = any(spec_path == root or root in spec_path.parents for root in (workspace, runtime))
    if spec_path.suffix.lower() != ".md" or not allowed:
        raise ValueError("Spec 必须是工作区或受控运行目录内的 Markdown 文件")
    spec = load_spec(spec_path).as_dict()
    return store.transition(
        task_id, "spec_ready", "结构化 Spec 已校验", actor=actor,
        evidence={"spec_path": str(spec_path)}, spec=spec,
    )


def start_task(store: TaskStore, task_id: str, actor: str = "system") -> dict:
    task = store.get(task_id)
    if task["status"] not in {"spec_ready", "rework"}:
        raise ValueError("只有 spec_ready 或 rework 任务可以开始执行")
    code_writes = task.get("execution_mode") == "codex"
    allowed_actions = ["read_spec", "read_workspace", "run_blocking_eval"]
    if code_writes:
        allowed_actions.append("write_code_in_task_scope")
    return store.transition(
        task_id, "executing", "开始受控执行", actor=actor,
        evidence={
            "requirement_id": task.get("requirement_id"),
            "business_refs": task.get("business_refs", []),
            "execution_mode": task.get("execution_mode", "verify"),
            "write_scope": task.get("write_scope", []),
            "execution_timeout_seconds": task.get("execution_timeout_seconds", 900),
            "allowed_actions": allowed_actions,
            "forbidden_actions": ["write_runtime_database", "approve_business_document", "skip_eval"],
        },
    )


def execute_task(store: TaskStore, task_id: str, actor: str = "system",
                 execution_runner: Callable[[dict], dict] | None = None) -> dict:
    task = store.get(task_id)
    if task["status"] != "executing":
        raise ValueError("只有 executing 任务可以执行受控动作")
    evidence = execution_runner(task) if execution_runner else {
        "mode": "verification_only",
        "changed_files": [],
        "message": "未配置代码写入执行器；本轮只验证当前候选，不产生业务数据副作用",
    }
    if not isinstance(evidence, dict):
        raise ValueError("执行器必须返回结构化证据")
    result = store.append_event(task_id, "受控执行阶段完成", actor=actor, evidence=evidence)
    if evidence.get("success") is False:
        raise ControlledExecutionError(str(evidence.get("message") or "受控执行失败"), evidence)
    return result


def evaluate_task(store: TaskStore, task_id: str, actor: str = "system", suite_runner=run_suite) -> dict:
    task = store.get(task_id)
    if task["status"] != "executing":
        raise ValueError("只有 executing 任务可以进入评测")
    store.transition(task_id, "evaluating", "运行统一阻断级 Eval", actor=actor)
    report = suite_runner("blocking", write_report=True)
    report_dir = Path(store.path).parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{task_id}-harness-blocking.json"
    report_ref = (Path("reports") / report_path.name).as_posix()
    report["report_path"] = report_ref
    report_payload = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    report_path.write_bytes(report_payload)
    report["report_sha256"] = hashlib.sha256(report_payload).hexdigest()
    summary = report.get("summary", {})
    evidence = {
        "decision": summary.get("decision"),
        "blocking_failed": summary.get("blocking_failed"),
        "report_path": report_ref,
        "report_sha256": report["report_sha256"],
    }
    if int(summary.get("blocking_failed", 0)) == 0 and summary.get("decision") == "pass":
        return store.transition(
            task_id, "review", "阻断级 Eval 已通过，等待具名人工审核",
            actor=actor, evidence=evidence, result=report,
        )
    return store.transition(
        task_id, "rework", "阻断项未通过，退回最小修复",
        actor=actor, evidence=evidence, result=report,
    )


def run_task(store: TaskStore, task_id: str, actor: str = "system", suite_runner=run_suite,
             execution_runner: Callable[[dict], dict] | None = None) -> dict:
    """Run the deterministic delivery stages and stop at review or rework.

    Codex execution requires an explicit task mode and write scope. A green
    Harness report never becomes ``completed`` without a separate named review.
    """
    try:
        task = store.get(task_id)
        if task["status"] == "queued":
            prepare_task(store, task_id, actor)
        start_task(store, task_id, actor)
        execute_task(store, task_id, actor, execution_runner)
        return evaluate_task(store, task_id, actor, suite_runner)
    except Exception as exc:
        task = store.get(task_id)
        if task["status"] not in {"completed", "failed", "rework"}:
            evidence = {"error_type": type(exc).__name__}
            execution_evidence = getattr(exc, "evidence", None)
            if isinstance(execution_evidence, dict):
                evidence["execution"] = execution_evidence
            return store.transition(
                task_id, "failed", "工作流异常，保留原因", actor=actor,
                evidence=evidence, error=str(exc),
            )
        raise
