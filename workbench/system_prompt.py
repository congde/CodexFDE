from __future__ import annotations

from dataclasses import dataclass, field

from .agent_roster import format_roster_for_llm
from .delivery_pipeline import format_pipeline_for_llm, pipeline_system_message, stage_for_status, status_title


@dataclass
class PromptSection:
    id: str
    title: str
    body: str


@dataclass
class SystemPromptAssembly:
    """Minimal ctx.systemPrompt seam: sections + tool schemas for one step."""

    sections: list[PromptSection] = field(default_factory=list)
    tool_schemas: list[dict] = field(default_factory=list)

    def as_text(self) -> str:
        chunks: list[str] = []
        for section in self.sections:
            chunks.append(f"## {section.title}\n{section.body}".strip())
        if self.tool_schemas:
            names = ", ".join(item["id"] for item in self.tool_schemas)
            chunks.append(f"## Available tools\n{names}")
        return "\n\n".join(chunks)

    def as_dict(self) -> dict:
        return {
            "sections": [{"id": s.id, "title": s.title, "body": s.body} for s in self.sections],
            "tool_schemas": self.tool_schemas,
            "text": self.as_text(),
        }


def assemble_system_prompt(
    *,
    task: dict,
    tools: list[dict],
    repository_root: str | None = None,
    graph: dict | None = None,
    session_id: str | None = None,
    roster_overrides: dict[str, str] | None = None,
) -> SystemPromptAssembly:
    """Assemble delivery-oriented prompt sections (course V0 stand-in for ctx.systemPrompt)."""
    request = str(task.get("request") or "").strip()
    requirement = str(task.get("requirement_id") or "").strip()
    mode = str(task.get("execution_mode") or "verify")
    scopes = task.get("write_scope") or []
    status = str(task.get("status") or "queued")
    stage = stage_for_status(status)
    sections = [
        PromptSection(
            id="role",
            title="Role",
            body=(
                "你是 OPC 工作台里的值班 Agent 员工（由超级个体老板调度）。"
                "遵守 AGENTS.md 与业务规则。"
                "只通过已注册工具读取工作区、执行受控修改、运行 blocking Eval。"
                "交付流水线每一步都必须对用户可见，并作为你推理的依据。"
                "你不能代替老板终审 approve。"
            ),
        ),
        PromptSection(
            id="roster",
            title="OPC employee roster",
            body=format_roster_for_llm(task_status=status, overrides=roster_overrides),
        ),
        PromptSection(
            id="pipeline",
            title="Delivery pipeline (visible to model)",
            body=format_pipeline_for_llm(
                task=task,
                graph=graph,
                session_id=session_id,
                roster_overrides=roster_overrides,
            ),
        ),
        PromptSection(
            id="task",
            title="Task",
            body=(
                f"requirement_id: {requirement or '—'}\n"
                f"execution_mode: {mode}\n"
                f"write_scope: {', '.join(scopes) if scopes else '—'}\n"
                f"status: {status} ({status_title(status)})\n"
                f"stage: {(stage or {}).get('id', '—')} / {(stage or {}).get('label', '—')}\n"
                f"request: {request}"
            ),
        ),
        PromptSection(
            id="constraints",
            title="Constraints",
            body=(
                "可用库存不得为负；入库幂等；订单状态机；采购须审批；"
                "失败不可伪装成成功；不得写入 .env/密钥/运行库。"
            ),
        ),
    ]
    if repository_root:
        sections.append(PromptSection(
            id="workspace",
            title="Workspace",
            body=f"repository_root: {repository_root}",
        ))
    schemas = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "description": item.get("description"),
            "permissions": item.get("permissions") or [],
        }
        for item in tools
    ]
    return SystemPromptAssembly(sections=sections, tool_schemas=schemas)


@dataclass
class PreStepDecision:
    """Outcome of agent/pre-step (dsh waterfall stand-in)."""

    action: str  # enter | reject
    messages: list[dict] = field(default_factory=list)
    reason: str = ""
    prompt: dict | None = None

    @property
    def entered(self) -> bool:
        return self.action == "enter" and bool(self.messages)


def run_pre_step(
    *,
    task: dict,
    tools: list[dict],
    repository_root: str | None = None,
    reject_reason: str | None = None,
    graph: dict | None = None,
    session_id: str | None = None,
    roster_overrides: dict[str, str] | None = None,
) -> PreStepDecision:
    """Claim next-step input and decide enter vs reject before step/start."""
    request = str(task.get("request") or "").strip()
    if reject_reason:
        return PreStepDecision(action="reject", reason=reject_reason, messages=[])
    if not request:
        return PreStepDecision(action="reject", reason="empty_request", messages=[])
    prompt = assemble_system_prompt(
        task=task,
        tools=tools,
        repository_root=repository_root,
        graph=graph,
        session_id=session_id,
        roster_overrides=roster_overrides,
    )
    return PreStepDecision(
        action="enter",
        messages=[
            pipeline_system_message(
                task,
                graph=graph,
                session_id=session_id,
                roster_overrides=roster_overrides,
            ),
            {"role": "user", "content": request, "source": "queued"},
        ],
        prompt=prompt.as_dict(),
    )
