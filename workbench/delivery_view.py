from __future__ import annotations

"""Stable, read-only projection of one AI delivery task and its evidence chain."""

from datetime import datetime, timezone
from typing import Iterable

from .cockpit import classify_lane, spec_title
from .control_surface import build_control_surface
from .delivery_pipeline import pipeline_payload, stage_for_status
from .evolution import EvolutionStore
from .feedback import summary as feedback_summary
from .task_store import TaskStore


SCHEMA = "workbench.delivery-view/v1"
ACTIVE_STATUSES = {"queued", "spec_ready", "executing", "evaluating", "review", "rework"}
TERMINAL_STATUSES = {"completed", "failed", "dead_letter"}
STALE_SENSITIVE_STATUSES = {"queued", "spec_ready", "executing", "evaluating", "rework"}

_STATUS_CONTROL = {
    "queued": ("automation", "自动化调度", "生成并校验任务级 Spec"),
    "spec_ready": ("agent:coder", "实现者", "按写入范围执行或直接进入验证"),
    "executing": ("agent:coder", "实现者", "完成受控修改并提交真实执行证据"),
    "evaluating": ("agent:reviewer", "质量验证", "运行统一 blocking Eval 并保存报告"),
    "review": ("human:reviewer", "具名终审人", "具名核对 Spec、Diff 与 Eval 后接受或打回"),
    "rework": ("agent:coder", "返工责任人", "根据保留的阻断证据进行有界修复"),
    "completed": ("human:reviewer", "交付责任人", "采集真实反馈并观察后续业务结果"),
    "failed": ("human:operator", "人工处置人", "核对不可重试错误并决定恢复或停止"),
    "dead_letter": ("human:operator", "人工处置人", "先核对中断原因和实际改动，确认后再创建新任务；原记录会保留"),
}


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness(task: dict, *, now: datetime | None = None) -> dict:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    updated = _timestamp(task.get("updated_at"))
    if updated is None:
        return {"updated_at": task.get("updated_at"), "age_seconds": None, "state": "unknown", "stale": True}
    age = max(0, int((current - updated).total_seconds()))
    code = str(task.get("status") or "")
    # review is a deliberate human wait state and must not become dishonest
    # merely because the reviewer takes longer than an automated stage.
    stale = code in STALE_SENSITIVE_STATUSES and age > 300
    state = "stale" if stale else ("final" if code in TERMINAL_STATUSES else "current")
    return {"updated_at": task.get("updated_at"), "age_seconds": age, "state": state, "stale": stale}


def _status_view(task: dict) -> dict:
    code = str(task.get("status") or "")
    known = code in _STATUS_CONTROL and stage_for_status(code) is not None
    payload = pipeline_payload(code)
    owner_id, owner_label, next_action = _STATUS_CONTROL.get(
        code,
        ("human:operator", "人工处置人", "停止自动推进并核对未知状态、数据库和事件链"),
    )
    if code == "completed" and task.get("reviewed_by"):
        owner_id = str(task["reviewed_by"])
        owner_label = "具名交付审核人"
    return {
        "code": code or "unknown",
        "known": known,
        "stage_id": payload.get("stage_id") if known else "unknown",
        "label": "已停止，待核对" if code == "dead_letter" else payload.get("label") if known else "未知状态",
        "title": payload.get("title") if known else f"未知交付状态：{code or '空值'}",
        "summary": payload.get("summary") if known else "状态不在交付合同中，禁止显示为成功。",
        "owner": {"id": owner_id, "label": owner_label},
        "next_action": next_action,
        "terminal": code in TERMINAL_STATUSES,
        "requires_human": code in {"review", "failed", "dead_letter"} or not known,
    }


def _eval_view(task: dict, *, include_detail: bool) -> dict:
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    results = result.get("results") if isinstance(result.get("results"), list) else []
    blocking = [item for item in results if isinstance(item, dict) and item.get("level") == "blocking"]
    passed = summary.get("blocking_passed")
    failed = summary.get("blocking_failed")
    if passed is None:
        passed = sum(bool(item.get("passed")) for item in blocking)
    if failed is None:
        failed = sum(not bool(item.get("passed")) for item in blocking)
    return {
        "available": bool(result),
        "decision": summary.get("decision"),
        "blocking_passed": int(passed or 0),
        "blocking_failed": int(failed or 0),
        "report_path": result.get("report_path") or result.get("blocking_report") or "",
        "report_sha256": result.get("report_sha256") or "",
        "cases": [
            {
                "name": str(item.get("name") or ""),
                "level": str(item.get("level") or ""),
                "passed": bool(item.get("passed")),
            }
            for item in results if isinstance(item, dict)
        ],
        "summary": summary,
        "results": results if include_detail else [],
        "runner": result.get("runner"),
    }


def _execution_view(task: dict, *, include_detail: bool) -> dict:
    evidence: dict = {}
    for event in reversed(task.get("events") or []):
        candidate = event.get("evidence")
        if event.get("detail") == "受控执行阶段完成" and isinstance(candidate, dict):
            evidence = candidate
            break
    compact = dict(evidence)
    if not include_detail:
        for key in ("diff", "stdout_tail", "stderr_tail", "commands", "change_manifest"):
            compact.pop(key, None)
    return {
        "available": bool(evidence),
        "mode": evidence.get("mode"),
        "success": evidence.get("success") if evidence else None,
        "changed_files": list(evidence.get("changed_files") or []),
        "out_of_scope_files": list(evidence.get("out_of_scope_files") or []),
        "usage": evidence.get("usage") if isinstance(evidence.get("usage"), dict) else {},
        "evidence": compact,
    }


def _allowed_actions(task: dict) -> list[str]:
    status = str(task.get("status") or "")
    actions = ["view_evidence", "add_feedback"]
    if status in {"queued", "spec_ready", "rework"}:
        actions.append("run")
    if status == "review":
        actions.extend(("approve", "reject"))
    if status == "completed" and str(task.get("requirement_id") or "").startswith("REQ-COURSE-L"):
        actions.append("export_candidate")
    return actions


def _event_views(task: dict) -> list[dict]:
    projected = []
    for event in task.get("events") or []:
        item = dict(event)
        item["status"] = _status_view({"status": event.get("to_status")})
        projected.append(item)
    return projected


def build_delivery_view(
    task: dict,
    *,
    feedback_items: Iterable[dict] = (),
    evolution_items: Iterable[dict] = (),
    include_detail: bool = True,
    now: datetime | None = None,
) -> dict:
    """Project raw stores into one truthful API/Web/CLI contract."""
    task_id = str(task.get("id") or "")
    feedback = [dict(item) for item in feedback_items if item.get("task_id") == task_id]
    evolutions = [
        dict(item) for item in evolution_items
        if item.get("source_task_id") == task_id or item.get("candidate_task_id") == task_id
    ]
    status = _status_view(task)
    freshness = _freshness(task, now=now)
    eval_view = _eval_view(task, include_detail=include_detail)
    execution = _execution_view(task, include_detail=include_detail)
    review = {
        "required": str(task.get("status") or "") == "review",
        "reviewed_by": task.get("reviewed_by"),
        "decision": task.get("review_decision"),
        "note": task.get("review_note"),
        "reviewed_at": task.get("reviewed_at"),
    }
    issues: list[str] = []
    if not status["known"]:
        issues.append("unknown_status")
    if freshness["stale"]:
        issues.append("stale_active_task")
    if task.get("status") == "completed" and (not review["reviewed_by"] or review["decision"] != "approve"):
        issues.append("completed_without_named_approval")
    if task.get("status") in {"review", "completed"} and (
        eval_view["decision"] != "pass" or eval_view["blocking_failed"] != 0
    ):
        issues.append("review_without_green_blocking_eval")
    if execution["out_of_scope_files"]:
        issues.append("out_of_scope_writes")
    control_surface = build_control_surface(task)
    for issue in control_surface["issues"]:
        if issue not in issues and issue in {
            "loop_event_missing",
            "loop_bounds_invalid",
            "tool_permissions_missing",
            "codex_write_permission_not_recorded",
            "parsed_spec_missing",
            "request_missing",
            "spec_path_missing",
            "write_scope_missing",
            "execution_timeout_missing",
            "skip_eval_not_forbidden",
            "named_review_missing",
            "blocking_report_missing",
        }:
            issues.append(issue)

    task_projection = dict(task)
    validations = [event.get('evidence', {}).get('validation') for event in task.get('events', [])
                   if isinstance(event.get('evidence'), dict) and event['evidence'].get('validation')]
    risks = []
    if task.get('status') != 'completed':
        risks.append('尚未获得实际审核者接受')
    if task.get('error'):
        risks.append(str(task['error']))
    if task.get('execution_mode') == 'verify':
        risks.append('仅复验已有候选，未调用 Codex 编码')
    if execution['evidence'].get('provenance') == 'injected_process_runner':
        risks.append('编码进程为控制实验替身，不是真实 Codex 调用')
    if task.get('authorization_policy') != 'v0':
        risks.append('历史或课程接口任务；未采用 V0 逐文件授权合同')
    if not any(item.get('command') for item in validations):
        risks.append('尚无独立验证命令记录')
    if not include_detail:
        for key in ("spec", "result", "events"):
            task_projection.pop(key, None)
    return {
        "schema": SCHEMA,
        "task_id": task_id,
        "requirement_id": task.get("requirement_id") or "",
        "request": task.get("request") or "",
        "business_refs": list(task.get("business_refs") or []),
        "task": task_projection,
        "delivery_summary": {
            "changed_files": execution['changed_files'], "validations": validations,
            "result": task.get('status'), "remaining_risks": risks,
            "workspace": task.get('workspace_path') or execution['evidence'].get('invocation', {}).get('workspace'),
        },
        "status": status,
        "freshness": freshness,
        "policy": {
            "automation_mode": task.get("automation_mode") or "manual",
            "execution_mode": task.get("execution_mode") or "verify",
            "write_scope": list(task.get("write_scope") or []),
            "execution_timeout_seconds": task.get("execution_timeout_seconds"),
            "workspace_path": task.get('workspace_path'),
            "authorization_policy": task.get('authorization_policy'),
        },
        "lane": classify_lane(task),
        "spec": {
            "text": task.get('spec_text') if include_detail else None,
            "sha256": task.get('spec_sha256'),
            "available": isinstance(task.get("spec"), dict) and bool(task.get("spec")),
            "path": task.get("spec_path") or "",
            "title": spec_title(task.get("spec")) or str(task.get("request") or "")[:120],
            "goal": str((task.get("spec") or {}).get("goal") or "") if include_detail and isinstance(task.get("spec"), dict) else "",
            "acceptance": str((task.get("spec") or {}).get("acceptance") or "") if include_detail and isinstance(task.get("spec"), dict) else "",
            "content": task.get("spec") if include_detail else None,
        },
        "execution": execution,
        "eval": eval_view,
        "control_surface": control_surface,
        "review": review,
        "feedback": {
            "total": len(feedback),
            "pending_review": sum(item.get("status") == "pending_review" for item in feedback),
            "accepted": sum(item.get("status") == "accepted" for item in feedback),
            "rejected": sum(item.get("status") == "rejected" for item in feedback),
            "items": feedback if include_detail else [],
        },
        "evolution": {
            "total": len(evolutions),
            "verified": sum(item.get("status") == "verified" for item in evolutions),
            "items": evolutions if include_detail else [],
        },
        "events": _event_views(task) if include_detail else [],
        "allowed_actions": _allowed_actions(task),
        "integrity": {"truthful": not issues, "issues": issues},
        "links": {
            "self": f"/api/v1/delivery/views/{task_id}",
            "task": f"/api/v1/tasks/{task_id}",
            "web": "/#delivery",
        },
    }


class DeliveryViewService:
    def __init__(self, tasks: TaskStore, evolutions: EvolutionStore | None = None) -> None:
        self.tasks = tasks
        self.evolutions = evolutions or EvolutionStore(tasks.path)

    def _related(self) -> tuple[list[dict], list[dict]]:
        feedback = feedback_summary(self.tasks.path)["items"]
        evolutions = self.evolutions.list(500)
        return feedback, evolutions

    def get(self, task_id: str) -> dict:
        feedback, evolutions = self._related()
        return build_delivery_view(
            self.tasks.get(task_id), feedback_items=feedback, evolution_items=evolutions,
        )

    def list(self, limit: int = 100) -> dict:
        feedback, evolutions = self._related()
        views = [
            build_delivery_view(
                self.tasks.get(task["id"]), feedback_items=feedback,
                evolution_items=evolutions, include_detail=False,
            )
            for task in self.tasks.list(limit)
        ]
        return {
            "schema": "workbench.delivery-view-list/v1",
            "summary": {
                "total": len(views),
                "active": sum(view["status"]["code"] in ACTIVE_STATUSES for view in views),
                "review": sum(view["status"]["code"] == "review" for view in views),
                "completed": sum(view["status"]["code"] == "completed" for view in views),
                "rework": sum(view["status"]["code"] == "rework" for view in views),
                "failed": sum(view["status"]["code"] in {"failed", "dead_letter"} for view in views),
                "unknown": sum(not view["status"]["known"] for view in views),
                "pending_feedback": sum(view["feedback"]["pending_review"] for view in views),
                "verified_evolutions": sum(view["evolution"]["verified"] for view in views),
            },
            "items": views,
        }
