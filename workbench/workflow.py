from __future__ import annotations

import json
import hashlib
import uuid
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from eval.harness import run_suite
from .spec import load_spec, parse_spec
from .task_store import TaskStore


class ControlledExecutionError(RuntimeError):
    def __init__(self, message: str, evidence: dict) -> None:
        super().__init__(message)
        self.evidence = evidence


def prepare_task(store: TaskStore, task_id: str, actor: str = "system") -> dict:
    task = store.get(task_id)
    if task["status"] != "queued":
        raise ValueError("只有 queued 任务可以准备 Spec")
    if task.get('spec_text') is not None:
        if hashlib.sha256(task['spec_text'].encode('utf-8')).hexdigest() != task.get('spec_sha256'):
            raise ValueError('任务合同快照校验失败')
        return store.transition(task_id, 'spec_ready', '已校验创建时冻结的 Spec', actor=actor,
                                evidence={'spec_sha256': task['spec_sha256'], 'spec_path': task['spec_path']},
                                spec=parse_spec(task['spec_text']).as_dict())
    spec_path = Path(task.get("spec_path") or "FDE_SPEC.md").resolve()
    workspace = Path.cwd().resolve()
    runtime = Path(store.path).resolve().parent
    allowed = any(spec_path == root or root in spec_path.parents for root in (workspace, runtime))
    if spec_path.suffix.lower() != ".md" or not allowed:
        raise ValueError("Spec 必须是工作区或受控运行目录内的 Markdown 文件")
    binding = next((event.get('evidence') for event in reversed(task.get('events', []))
                    if event.get('detail') == '本次需求 Spec 已冻结'), None)
    if binding:
        raw = spec_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != binding.get('sha256'):
            raise ValueError('本次需求 Spec 已变化，请重新提交并确认')
        spec = parse_spec(raw.decode('utf-8')).as_dict()
    else:
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
    allowed_actions = ["read_spec", "read_workspace", "run_blocking_eval", "run_workspace_shell"]
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
    code_writes = task.get("execution_mode") == "codex"
    if code_writes and execution_runner is None:
        raise ControlledExecutionError("代码任务没有配置执行器，不能退化为仅复验", {
            "success": False, "mode": "not_executed", "changed_files": [],
            "message": "未调用 Codex；请配置执行器后重新发起受控执行",
        })
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
    if code_writes and (evidence.get("success") is not True or evidence.get("mode") != "codex_exec"):
        raise ControlledExecutionError("代码执行证据不完整，不能仅凭复验进入人审", evidence)
    return result


def evaluate_task(store: TaskStore, task_id: str, actor: str = "system", suite_runner=run_suite) -> dict:
    task = store.get(task_id)
    if task["status"] != "executing":
        raise ValueError("只有 executing 任务可以进入评测")
    bootstrap_source = None
    source_root = Path(task.get('workspace_path') or Path.cwd())
    if task.get('requirement_id') == 'WB-L04-BOOTSTRAP':
        from .bootstrap_source import control_source_snapshot
        bootstrap_source = control_source_snapshot(source_root)
    store.transition(task_id, "evaluating", "运行统一阻断级 Eval", actor=actor)
    control_report = None
    if task.get('authorization_policy') == 'v0' and task.get('requirement_id') == 'WB-L04-BOOTSTRAP' and suite_runner is run_suite:
        folder = Path(store.path).parent / 'reports' / ('control-' + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        command = [sys.executable, '-B', '-X', 'utf8', '-m', 'unittest',
                   'tests.test_workbench_v0', 'tests.test_bootstrap_handoff', '-v']
        started = datetime.now(timezone.utc).isoformat()
        try:
            process = subprocess.run(command, cwd=source_root, capture_output=True, text=True,
                                     encoding='utf-8', errors='replace', timeout=180)
        except subprocess.TimeoutExpired as exc:
            from .execution import _text
            process = subprocess.CompletedProcess(command, 124, _text(exc.stdout), _text(exc.stderr))
        except OSError as exc:
            process = subprocess.CompletedProcess(command, 127, '', str(exc))
        receipt = {'task_id': task_id, 'command': command, 'workspace': str(source_root),
                   'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
                   'returncode': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}
        receipt_path = folder / 'process.json'
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        store.append_event(task_id, 'V0 控制检查已保存', actor=actor,
                           evidence={'validation': receipt, 'receipt_path': str(receipt_path)})
        if process.returncode:
            control_report = {'summary': {'total': 1, 'passed': 0, 'decision': 'block', 'blocking_failed': 1},
                              'results': [{'name': 'v0_control_suite', 'level': 'blocking', 'passed': False,
                                           'message': 'V0 控制检查未通过；先核对原始输出'}],
                              'runner': {'workspace': str(source_root), 'process_returncode': process.returncode,
                                         'receipt_path': str(receipt_path)}}
    receipt_before = set()
    if task.get('authorization_policy') == 'v0' and suite_runner is run_suite:
        from eval.harness import EVALS
        from .course_workspace import LessonSubprocessEvalRunner
        suite_runner = LessonSubprocessEvalRunner(source_root, Path(store.path).parent, task_id,
                        tuple(name for name, level, _ in EVALS if level == 'blocking'), 'v0')
        receipt_before = set((Path(store.path).parent / 'reports').glob('eval-*/process.json'))
    try:
        report = control_report or suite_runner("blocking", write_report=True)
    except Exception as exc:
        if task.get('authorization_policy') == 'v0':
            for path in set((Path(store.path).parent / 'reports').glob('eval-*/process.json')) - receipt_before:
                receipt = json.loads(path.read_text(encoding='utf-8'))
                if receipt.get('task_id') == task_id:
                    store.append_event(task_id, '检查失败，原始进程记录已保存', actor=actor,
                                       evidence={'validation': receipt, 'receipt_path': str(path)})
        raise
    if bootstrap_source is not None:
        from .bootstrap_source import verify_control_source
        verify_control_source(bootstrap_source)
        report['bootstrap_source'] = bootstrap_source
    report_dir = Path(store.path).parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{task_id}-{uuid.uuid4().hex}-harness-blocking.json"
    report_ref = (Path("reports") / report_path.name).as_posix()
    report["report_path"] = report_ref
    summary = report.get("summary", {})
    evidence = {
        "decision": summary.get("decision"),
        "blocking_failed": summary.get("blocking_failed"),
        "report_path": report_ref,
    }
    runner = report.get('runner') or {}
    receipt_path = runner.get('receipt_path')
    if receipt_path:
        receipt = json.loads(Path(receipt_path).read_text(encoding='utf-8'))
        evidence['validation'] = receipt
    else:
        evidence['validation'] = {'kind': 'python_callable', 'callable': getattr(suite_runner, '__qualname__', type(suite_runner).__name__),
                                  'command': None, 'returncode': None,
                                  'note': '进程内检查，无独立进程退出码；不得伪装为命令执行'}
    report['validation'] = evidence['validation']
    report_payload = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
    report_path.write_bytes(report_payload)
    report['report_sha256'] = evidence['report_sha256'] = hashlib.sha256(report_payload).hexdigest()
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
    # Reject an invalid request before entering error handling; a second click
    # must not destroy a valid pending review or completed delivery.
    if store.get(task_id)['status'] not in {'queued', 'spec_ready', 'rework'}:
        raise ValueError('当前任务不能重跑；待审核任务须先由人工决定返工或创建关联复验任务')
    try:
        task = store.get(task_id)
        if task["status"] == "queued":
            prepare_task(store, task_id, actor)
        start_task(store, task_id, actor)
        execute_task(store, task_id, actor, execution_runner)
        return evaluate_task(store, task_id, actor, suite_runner)
    except Exception as exc:
        task = store.get(task_id)
        if isinstance(exc, ControlledExecutionError) and task["status"] == "executing":
            return store.transition(
                task_id, "rework", "受控执行失败，保留证据并等待有界重试", actor=actor,
                evidence={"error_type": type(exc).__name__, "execution": exc.evidence},
                error=str(exc),
            )
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
