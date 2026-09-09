from __future__ import annotations

"""Project the Harness control shell around one delivery task.

The control surface is deliberately derived from durable task state and events.
It is not a slogan about model strength; it exposes whether the shell around the
model has loop, tools, context and guardrail gates that another reviewer can
inspect.
"""

from typing import Iterable


COMPONENT_ORDER = ("loop", "tools", "context", "guardrails")


def _positive_integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None

_LABELS = {
    "loop": "Loop：有界续跑",
    "tools": "Tools：动作与权限",
    "context": "Context：任务上下文",
    "guardrails": "Guardrails：边界与刹车",
}


def _events(task: dict) -> list[dict]:
    raw = task.get("events") or []
    return [event for event in raw if isinstance(event, dict)]


def _latest_event(task: dict, detail: str) -> dict | None:
    for event in reversed(_events(task)):
        if event.get("detail") == detail:
            return event
    return None


def _latest_evidence(task: dict, detail: str) -> dict:
    event = _latest_event(task, detail)
    evidence = event.get("evidence") if event else None
    return dict(evidence) if isinstance(evidence, dict) else {}


def _has_event(task: dict, details: Iterable[str]) -> bool:
    wanted = set(details)
    return any(event.get("detail") in wanted for event in _events(task))


def _result_summary(task: dict) -> dict:
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return summary


def _result_report(task: dict) -> dict:
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    return {
        "decision": _result_summary(task).get("decision"),
        "report_path": result.get("report_path") or result.get("blocking_report") or "",
        "report_sha256": result.get("report_sha256") or "",
    }


def _component(component: str, *, state: str, summary: str, evidence: dict | None = None,
               issues: list[str] | None = None) -> dict:
    issues = list(issues or [])
    return {
        "id": component,
        "label": _LABELS[component],
        "state": state,
        "armed": state in {"armed", "observed"},
        "summary": summary,
        "evidence": evidence or {},
        "issues": issues,
    }


def build_control_surface(task: dict) -> dict:
    """Return a reviewer-facing Harness control shell for one task."""
    status = str(task.get("status") or "")
    execution_mode = str(task.get("execution_mode") or "verify")
    automation_mode = str(task.get("automation_mode") or "manual")
    write_scope = list(task.get("write_scope") or [])
    timeout = _positive_integer(task.get("execution_timeout_seconds"))
    started = _latest_evidence(task, "开始受控执行")
    auto_start = _latest_evidence(task, "自动流水线开始推进")
    terminal = status in {"review", "completed", "failed", "dead_letter", "rework"}
    active_or_later = status in {"executing", "evaluating", "review", "completed", "failed", "dead_letter", "rework"}

    loop_issues: list[str] = []
    if automation_mode == "automatic":
        attempt = auto_start.get("attempt")
        max_attempts = auto_start.get("max_attempts")
        if active_or_later and not auto_start:
            loop_issues.append("loop_event_missing")
            loop_state = "missing"
            loop_summary = "自动任务缺少有界推进事件，不能确认续跑闸门。"
        elif auto_start and (
            _positive_integer(attempt) is None
            or _positive_integer(max_attempts) is None
            or attempt > max_attempts
        ):
            loop_issues.append("loop_bounds_invalid")
            loop_state = "missing"
            loop_summary = "自动推进轮次或上限无效，不能确认有界续跑。"
        else:
            loop_state = "observed" if auto_start else "armed"
            loop_summary = (
                f"自动流水线有界推进：第 {attempt or 1} 轮 / 最多 {max_attempts or '?'} 轮。"
                if auto_start else "自动任务已登记，等待有界流水线推进。"
            )
    else:
        loop_state = "manual"
        loop_summary = "本任务为人工触发流程；续跑不自动发生，需在事件链中具名重新发起。"

    allowed_actions = list(started.get("allowed_actions") or [])
    forbidden_actions = list(started.get("forbidden_actions") or [])
    tool_issues: list[str] = []
    if active_or_later and not allowed_actions:
        tool_issues.append("tool_permissions_missing")
    if execution_mode == "codex" and "write_code_in_task_scope" not in allowed_actions and active_or_later:
        tool_issues.append("codex_write_permission_not_recorded")
    tool_state = "missing" if tool_issues else ("observed" if allowed_actions else "pending")
    tool_summary = (
        "已记录允许动作与禁止动作，工具调用不能只凭模型自述放行。"
        if allowed_actions else "尚未进入执行阶段，工具权限闸门等待装载。"
    )

    context_issues: list[str] = []
    spec_available = isinstance(task.get("spec"), dict) and bool(task.get("spec"))
    if active_or_later and not spec_available:
        context_issues.append("parsed_spec_missing")
    if not task.get("request"):
        context_issues.append("request_missing")
    if not task.get("spec_path"):
        context_issues.append("spec_path_missing")
    context_state = "missing" if context_issues else ("observed" if spec_available else "armed")
    context_summary = (
        "已解析任务 Spec，并保留业务对象、报告与候选线索。"
        if spec_available else "已登记需求与 Spec 路径，等待结构化校验。"
    )

    guard_issues: list[str] = []
    if execution_mode == "codex" and not write_scope:
        guard_issues.append("write_scope_missing")
    if timeout is None:
        guard_issues.append("execution_timeout_missing")
    if active_or_later and "skip_eval" not in forbidden_actions:
        guard_issues.append("skip_eval_not_forbidden")
    if task.get("status") == "completed" and (
        not task.get("reviewed_by") or task.get("review_decision") != "approve"
    ):
        guard_issues.append("named_review_missing")
    report = _result_report(task)
    if terminal and status in {"review", "completed"} and not report["report_path"]:
        guard_issues.append("blocking_report_missing")
    guard_state = "missing" if guard_issues else ("observed" if active_or_later else "armed")
    guard_summary = (
        "写集、超时、禁止跳过 Eval 与具名验收共同构成刹车。"
        if not guard_issues else "控制面存在缺口，不能把模型输出或绿色摘要直接当成交付完成。"
    )

    components = [
        _component("loop", state=loop_state, summary=loop_summary, evidence={
            "automation_mode": automation_mode,
            "attempt": auto_start.get("attempt"),
            "max_attempts": auto_start.get("max_attempts"),
            "rework_scheduled": _has_event(task, ["自动流水线安排有界重试", "自动流水线为异常中断安排有界重试"]),
        }, issues=loop_issues),
        _component("tools", state=tool_state, summary=tool_summary, evidence={
            "execution_mode": execution_mode,
            "allowed_actions": allowed_actions,
            "forbidden_actions": forbidden_actions,
        }, issues=tool_issues),
        _component("context", state=context_state, summary=context_summary, evidence={
            "request": task.get("request") or "",
            "requirement_id": task.get("requirement_id") or "",
            "business_refs": list(task.get("business_refs") or []),
            "spec_path": task.get("spec_path") or "",
            "spec_available": spec_available,
            **report,
        }, issues=context_issues),
        _component("guardrails", state=guard_state, summary=guard_summary, evidence={
            "write_scope": write_scope,
            "execution_timeout_seconds": timeout,
            "eval_decision": report["decision"],
            "reviewed_by": task.get("reviewed_by"),
            "review_decision": task.get("review_decision"),
        }, issues=guard_issues),
    ]
    issues = [issue for item in components for issue in item["issues"]]
    return {
        "schema": "workbench.control-surface/v1",
        "thesis": "长任务翻车先看 Harness 外壳是否缺闸；瓶颈从表达外移到控制面。",
        "components": components,
        "ready": not issues,
        "issues": issues,
    }
