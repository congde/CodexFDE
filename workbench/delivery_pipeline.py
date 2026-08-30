from __future__ import annotations

"""Human-visible delivery pipeline stages shared by UI and LLM context.

These stages describe one FlowERP *code delivery* task — not ERP business
state machines (inventory / orders / purchase).
"""

# Ordered stages shown to humans and injected into model context.
PIPELINE_STAGES: tuple[dict, ...] = (
    {
        "id": "request",
        "label": "需求",
        "statuses": ("queued",),
        "title": "已接到需求",
        "summary": "自然语言需求已入队，等待生成任务 Spec。",
        "model_hint": "确认需求边界与 AGENTS.md 业务规则，不要跳过 Spec。",
    },
    {
        "id": "spec",
        "label": "规格",
        "statuses": ("spec_ready",),
        "title": "规格已写好",
        "summary": "任务级 Spec 已生成，可进入受控改代码或验证。",
        "model_hint": "先读 Spec，再决定是否改代码；写入必须落在 write_scope。",
    },
    {
        "id": "code",
        "label": "改代码",
        "statuses": ("executing", "rework"),
        "title": "正在改 FlowERP 代码",
        "summary": "在 flowerp/tests 等范围内修改产品代码。",
        "model_hint": "仅修改 write_scope；保持库存非负、入库幂等、订单状态机、采购审批。",
    },
    {
        "id": "eval",
        "label": "验收",
        "statuses": ("evaluating",),
        "title": "正在跑阻断级 Eval",
        "summary": "用 Eval 验证 FlowERP 业务规则未被破坏。",
        "model_hint": "根据失败用例定位修复；不得把失败伪装成成功。",
    },
    {
        "id": "human",
        "label": "老板终审",
        "statuses": ("review", "completed", "failed", "dead_letter"),
        "title": "等待或结束于老板终审",
        "summary": "员工停工；质检员可挑刺，只有老板能通过或驳回。",
        "model_hint": "review 时停止自动改代码；等待老板终审，不要假装已 approve。",
    },
)


_STATUS_TO_STAGE = {
    status: stage
    for stage in PIPELINE_STAGES
    for status in stage["statuses"]
}


def stage_for_status(status: str | None) -> dict | None:
    if not status:
        return None
    return _STATUS_TO_STAGE.get(str(status))


def stage_index(status: str | None) -> int:
    stage = stage_for_status(status)
    if not stage:
        return -1
    for index, item in enumerate(PIPELINE_STAGES):
        if item["id"] == stage["id"]:
            return index
    return -1


def pipeline_payload(task_status: str, *, detail: str = "", evidence: object = None) -> dict:
    stage = stage_for_status(task_status) or {
        "id": "unknown",
        "label": task_status or "—",
        "title": status_title(task_status),
        "summary": "未知流水线状态",
        "model_hint": "报告当前状态并请求人工指引。",
    }
    title = status_title(task_status)
    summary = stage.get("summary") or ""
    if task_status == "review":
        title = "等老板终审"
        summary = "员工已停工；质检员可挑刺，需要老板具名通过或驳回。"
    elif task_status == "completed":
        title = "交付已接受"
        summary = "老板终审已通过，本次 FlowERP 增量完成。"
    elif task_status == "failed":
        title = "交付失败"
        summary = "本轮未能完成，需人工查看失败原因。"
    elif task_status == "dead_letter":
        title = "进入死信"
        summary = "自动重试已耗尽或不可重试，任务需人工介入后另开会话。"
    elif task_status == "rework":
        title = "需要返工"
        summary = "验收未过或被驳回，流水线回到可再执行。"

    payload = {
        "schema": "harness.pipeline.stage/v1",
        "visible_to_model": True,
        "stage_id": stage["id"],
        "label": stage.get("label") or stage["id"],
        "task_status": task_status,
        "title": title,
        "summary": summary,
        "model_hint": stage.get("model_hint") or "",
        "detail": detail or "",
    }
    try:
        from .agent_roster import duty_actor_for_stage, resolve

        duty = duty_actor_for_stage(stage["id"])
        payload["on_duty"] = duty
        payload["on_duty_name"] = resolve(duty).display_name
    except Exception:
        pass
    if evidence is not None:
        payload["evidence"] = evidence
    return payload


def status_title(task_status: str | None) -> str:
    mapping = {
        "queued": "已接到需求",
        "spec_ready": "规格已写好",
        "executing": "正在改 FlowERP 代码",
        "evaluating": "正在跑阻断级 Eval",
        "rework": "需要返工",
        "review": "等老板终审",
        "completed": "交付已接受",
        "failed": "交付失败",
        "dead_letter": "进入死信",
    }
    return mapping.get(str(task_status or ""), str(task_status or "—"))


def format_pipeline_for_llm(
    *,
    task: dict,
    graph: dict | None = None,
    session_id: str | None = None,
    roster_overrides: dict[str, str] | None = None,
) -> str:
    """Compact, model-facing snapshot of the delivery pipeline."""
    from .agent_roster import (
        duty_actor_for_stage,
        format_roster_for_llm,
        next_duty_actor,
        resolve,
    )

    status = str(task.get("status") or "queued")
    current = stage_for_status(status)
    idx = stage_index(status)
    duty = duty_actor_for_stage((current or {}).get("id"), overrides=roster_overrides)
    nxt = next_duty_actor(status, overrides=roster_overrides)
    lines = [
        "这是 FlowERP 代码交付流水线（不是库存/订单业务状态机）。",
        "OPC：老板是超级个体；员工是 Agent（见下方班组）。",
        f"session: {session_id or '—'}",
        f"task: {task.get('id') or '—'}",
        f"requirement_id: {task.get('requirement_id') or '—'}",
        f"execution_mode: {task.get('execution_mode') or 'verify'}",
        f"write_scope: {', '.join(task.get('write_scope') or []) or '—'}",
        f"current_status: {status} ({status_title(status)})",
    ]
    if current:
        lines.append(f"current_stage: {current['id']} / {current['label']}")
        lines.append(f"model_hint: {current.get('model_hint')}")
        try:
            lines.append(f"on_duty: {duty} / {resolve(duty).display_name}")
        except KeyError:
            lines.append(f"on_duty: {duty}")
        if nxt:
            try:
                lines.append(f"next_employee: {nxt} / {resolve(nxt).display_name}")
            except KeyError:
                lines.append(f"next_employee: {nxt}")

    lines.append("stages:")
    for index, stage in enumerate(PIPELINE_STAGES):
        if idx < 0:
            mark = "·"
        elif index < idx:
            mark = "✓"
        elif index == idx:
            mark = "→"
        else:
            mark = "·"
        employee = duty_actor_for_stage(stage["id"], overrides=roster_overrides)
        try:
            name = resolve(employee).display_name
        except KeyError:
            name = employee
        lines.append(
            f"  {mark} {stage['label']} ({stage['id']}) @{name}: {stage['summary']}"
        )

    summary = ((task.get("result") or {}).get("summary") or {}) if isinstance(task.get("result"), dict) else {}
    if summary:
        lines.append(
            "last_eval: "
            f"decision={summary.get('decision') or '—'} "
            f"blocking_failed={summary.get('blocking_failed', '—')} "
            f"blocking_passed={summary.get('blocking_passed', '—')}"
        )
    if task.get("error"):
        lines.append(f"error: {task.get('error')}")
    if graph and graph.get("current"):
        lines.append(f"graph.current: {graph.get('current')}")
    failures = []
    if graph:
        for turn in graph.get("turns") or []:
            failures.extend(turn.get("failures") or [])
    if failures:
        lines.append("recent_failures: " + ", ".join(str(item) for item in failures[-8:]))

    lines.append(format_roster_for_llm(task_status=status, overrides=roster_overrides))
    lines.append(
        "规则: 每一步都要对用户可见；你的计划与工具调用必须对齐当前 stage 与值班员工；"
        "verify 模式不要假装已改代码；dead_letter/failed 时说明老板下一步。"
    )
    return "\n".join(lines)


def pipeline_system_message(
    task: dict,
    *,
    graph: dict | None = None,
    session_id: str | None = None,
    roster_overrides: dict[str, str] | None = None,
) -> dict:
    return {
        "role": "system",
        "content": format_pipeline_for_llm(
            task=task,
            graph=graph,
            session_id=session_id,
            roster_overrides=roster_overrides,
        ),
        "source": "delivery_pipeline",
        "kind": "pipeline/context",
    }
