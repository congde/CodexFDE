from __future__ import annotations

"""OPC agent employees — named Agent roles, not multi-user accounts.

One human boss (X-Workbench-Actor) directs multiple Agent employees on the
FlowERP delivery pipeline. Employees never log in; they only appear as
event actors and LLM-visible duty assignments.
"""

from dataclasses import dataclass
from typing import Iterable


AGENT_PREFIX = "agent:"


@dataclass(frozen=True)
class AgentEmployee:
    id: str
    display_name: str
    title: str
    duty: str
    stages: tuple[str, ...]
    tools: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "title": self.title,
            "duty": self.duty,
            "stages": list(self.stages),
            "tools": list(self.tools),
            "kind": "agent",
            "can_final_review": False,
        }


DEFAULT_EMPLOYEES: tuple[AgentEmployee, ...] = (
    AgentEmployee(
        id="agent:spec",
        display_name="规格员",
        title="Spec",
        duty="澄清需求边界并生成任务 Spec",
        stages=("request", "spec"),
        tools=("spec.read",),
    ),
    AgentEmployee(
        id="agent:coder",
        display_name="工程师",
        title="Coder",
        duty="在 write_scope 内受控改代码并跑 Eval",
        stages=("code", "eval"),
        tools=("codex.exec", "eval.blocking", "workspace.read", "workspace.shell"),
    ),
    AgentEmployee(
        id="agent:reviewer",
        display_name="质检员",
        title="Reviewer",
        duty="对照 Eval 与业务规则挑刺；不可代替老板终审",
        stages=("human",),
        tools=(),
    ),
)

_BY_ID = {item.id: item for item in DEFAULT_EMPLOYEES}

# Fixed SoD: coder and reviewer must differ (enforced by roster design).
DUTY_BY_STAGE = {
    "request": "agent:spec",
    "spec": "agent:spec",
    "code": "agent:coder",
    "eval": "agent:coder",
    "human": "agent:reviewer",
}

DUTY_BY_TOOL = {
    "spec.read": "agent:spec",
    "codex.exec": "agent:coder",
    "eval.blocking": "agent:coder",
    "workspace.read": "agent:coder",
    "workspace.shell": "agent:coder",
}


def list_employees() -> list[dict]:
    return [item.as_dict() for item in DEFAULT_EMPLOYEES]


def resolve(agent_id: str) -> AgentEmployee:
    key = str(agent_id or "").strip()
    employee = _BY_ID.get(key)
    if employee is None:
        raise KeyError(f"未知员工：{agent_id}")
    return employee


def is_agent_actor(actor: str | None) -> bool:
    return str(actor or "").strip().startswith(AGENT_PREFIX)


def assert_boss_actor(actor: str) -> str:
    value = str(actor or "").strip()
    if not value:
        raise ValueError("老板身份不能为空")
    if is_agent_actor(value):
        raise ValueError("Agent 员工不能代替老板终审；请用老板身份提交验收")
    return value


def assert_coder_reviewer_sod(coder_id: str, reviewer_id: str) -> None:
    if coder_id == reviewer_id:
        raise ValueError("职责分离：工程师与质检员不能是同一员工")


def duty_actor_for_stage(stage_id: str | None, *, overrides: dict[str, str] | None = None) -> str:
    mapping = {**DUTY_BY_STAGE, **(overrides or {})}
    return mapping.get(str(stage_id or ""), "agent:coder")


def duty_actor_for_status(task_status: str | None, *, overrides: dict[str, str] | None = None) -> str:
    from .delivery_pipeline import stage_for_status

    stage = stage_for_status(task_status)
    stage_id = (stage or {}).get("id")
    return duty_actor_for_stage(stage_id, overrides=overrides)


def duty_actor_for_tool(tool_id: str, *, overrides: dict[str, str] | None = None) -> str:
    _ = overrides
    return DUTY_BY_TOOL.get(str(tool_id or ""), "agent:coder")


def next_duty_actor(task_status: str | None, *, overrides: dict[str, str] | None = None) -> str | None:
    from .delivery_pipeline import PIPELINE_STAGES, stage_index

    idx = stage_index(task_status)
    if idx < 0 or idx >= len(PIPELINE_STAGES) - 1:
        return None
    nxt = PIPELINE_STAGES[idx + 1]["id"]
    return duty_actor_for_stage(nxt, overrides=overrides)


def format_roster_for_llm(
    *,
    task_status: str | None = None,
    overrides: dict[str, str] | None = None,
) -> str:
    current = duty_actor_for_status(task_status, overrides=overrides) if task_status else None
    nxt = next_duty_actor(task_status, overrides=overrides) if task_status else None
    lines = [
        "这是 OPC（一人公司）班组：超级个体是老板；员工都是 Agent，不是多用户账号。",
        "员工名单:",
    ]
    for item in DEFAULT_EMPLOYEES:
        mark = "→" if current == item.id else "·"
        lines.append(f"  {mark} {item.id} / {item.display_name}：{item.duty}")
    if current:
        emp = _BY_ID.get(current)
        lines.append(f"current_duty: {current} ({(emp.display_name if emp else current)})")
    if nxt:
        emp = _BY_ID.get(nxt)
        lines.append(f"next_duty: {nxt} ({(emp.display_name if emp else nxt)})")
    lines.append("规则: 质检员只能挑刺，不能 approve；终审必须由老板（非 agent:*）完成。")
    lines.append("职责分离: agent:coder 与 agent:reviewer 固定不同。")
    return "\n".join(lines)


def build_agent_critique(
    task: dict,
    *,
    reviewer_id: str = "agent:reviewer",
) -> dict:
    """Deterministic quality note before human final review."""
    assert_coder_reviewer_sod("agent:coder", reviewer_id)
    resolve(reviewer_id)
    status = str(task.get("status") or "")
    summary = ((task.get("result") or {}).get("summary") or {}) if isinstance(task.get("result"), dict) else {}
    decision = str(summary.get("decision") or "—")
    failed = summary.get("blocking_failed", "—")
    passed = summary.get("blocking_passed", "—")
    error = task.get("error")
    suggestion = "approve"
    notes = [
        f"任务状态: {status}",
        f"Eval: decision={decision} blocking_failed={failed} blocking_passed={passed}",
    ]
    if error:
        notes.append(f"错误: {error}")
        suggestion = "reject"
    try:
        failed_n = int(failed) if failed not in {"—", None, ""} else 0
    except (TypeError, ValueError):
        failed_n = 0
    if failed_n > 0 or (decision and decision not in {"pass", "passed", "ok", "—"}):
        suggestion = "reject"
        notes.append("阻断级 Eval 未全部通过，建议老板驳回返工。")
    elif status == "review":
        notes.append("自动化已停在 review；建议老板对照 Diff/Eval 具名终审。")
    else:
        notes.append("当前未到老板终审点；仅记录质检意见。")
    return {
        "schema": "harness.agent.critique/v1",
        "reviewer_id": reviewer_id,
        "reviewer_name": resolve(reviewer_id).display_name,
        "suggested_decision": suggestion,
        "note": "\n".join(notes),
        "can_finalize": False,
    }


def roster_overrides_from_events(events: Iterable[dict] | None) -> dict[str, str]:
    """Latest employees/assign event wins."""
    overrides: dict[str, str] = {}
    for event in events or []:
        if event.get("kind") != "employees/assign":
            continue
        payload = event.get("payload") or {}
        mapping = payload.get("duty_by_stage") or payload.get("overrides") or {}
        if isinstance(mapping, dict):
            cleaned = {
                str(stage): str(agent_id)
                for stage, agent_id in mapping.items()
                if str(agent_id) in _BY_ID
            }
            if cleaned:
                coder = cleaned.get("code") or cleaned.get("eval") or "agent:coder"
                reviewer = cleaned.get("human") or "agent:reviewer"
                assert_coder_reviewer_sod(coder, reviewer)
                overrides = cleaned
    return overrides
