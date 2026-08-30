from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .runtime_store import HarnessRuntimeStore


ToolHandler = Callable[[dict, dict], dict]


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    description: str
    permissions: frozenset[str]
    handler: ToolHandler
    # dsh-aligned: none | ask — ask requires named one-shot approval before execute
    approval: str = "none"

    def __post_init__(self) -> None:
        if self.approval not in {"none", "ask"}:
            raise ValueError("ToolSpec.approval 必须是 none 或 ask")


def _summarize(result: dict) -> dict:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    summary: dict[str, object] = {}
    for key in ("status", "mode", "decision", "success", "message"):
        if key in result:
            summary[key] = result[key]
    nested = result.get("result") or result.get("summary")
    if isinstance(nested, dict):
        if "decision" in nested:
            summary["decision"] = nested["decision"]
        if "blocking_failed" in nested:
            summary["blocking_failed"] = nested["blocking_failed"]
    return summary


def _approval_allowed(spec: ToolSpec, context: dict) -> tuple[bool, str]:
    if spec.approval != "ask":
        return True, "not_required"
    if context.get("auto_approve"):
        return True, "auto_approve"
    approved = set(context.get("approved_tools") or [])
    if spec.id in approved or "*" in approved:
        return True, "named_allow_once"
    return False, "approval_required"


class ToolRegistry:
    """Scoped tool registry aligned with dsh tool/call → tools/* → tool/result pipeline."""

    def __init__(self, runtime: HarnessRuntimeStore) -> None:
        self.runtime = runtime
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.id.strip():
            raise ValueError("Tool id 不能为空")
        self._tools[spec.id] = spec

    def get(self, tool_id: str) -> ToolSpec:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(tool_id) from exc

    def list_tools(self) -> list[dict]:
        items = []
        for spec in sorted(self._tools.values(), key=lambda item: item.id):
            items.append({
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "permissions": sorted(spec.permissions),
                "approval": spec.approval,
            })
        return items

    def _append_denied_result(
        self,
        *,
        session_id: str,
        actor: str,
        source_prefix: str,
        tool_id: str,
        call_id: str,
        message: str,
        reason: str,
    ) -> None:
        self.runtime.append(
            session_id,
            "tools/post-execute",
            actor,
            {"tool_id": tool_id, "call_id": call_id, "success": False, "reason": reason},
            source_key=f"{source_prefix}:post",
        )
        self.runtime.append(
            session_id,
            "tools/result",
            actor,
            {"tool_id": tool_id, "call_id": call_id, "isError": True, "frozen": True},
            source_key=f"{source_prefix}:tools-result",
        )
        self.runtime.append(
            session_id,
            "tool/result",
            actor,
            {
                "tool_id": tool_id,
                "call_id": call_id,
                "isError": True,
                "summary": {"message": message},
            },
            source_key=f"{source_prefix}:result",
        )

    def invoke(
        self,
        tool_id: str,
        context: dict,
        args: dict,
        *,
        session_id: str,
        actor: str,
        call_id: str,
    ) -> dict:
        spec = self.get(tool_id)
        allowed = set(context.get("allowed_actions") or [])
        missing = spec.permissions - allowed
        source_prefix = f"tool:{call_id}"
        call_payload = {"tool_id": tool_id, "call_id": call_id, "args": args}

        self.runtime.append(
            session_id,
            "tool/call",
            actor,
            call_payload,
            source_key=f"{source_prefix}:call",
        )

        if missing:
            deny_payload = {
                **call_payload,
                "allowed": False,
                "missing_permissions": sorted(missing),
                "allowed_actions": sorted(allowed),
            }
            self.runtime.append(
                session_id,
                "tools/pre-execute",
                actor,
                deny_payload,
                source_key=f"{source_prefix}:pre",
            )
            self._append_denied_result(
                session_id=session_id,
                actor=actor,
                source_prefix=source_prefix,
                tool_id=tool_id,
                call_id=call_id,
                message=f"缺少权限：{', '.join(sorted(missing))}",
                reason="denied",
            )
            raise PermissionError(f"Tool {tool_id} 缺少权限：{', '.join(sorted(missing))}")

        approved, approval_reason = _approval_allowed(spec, context)
        if not approved:
            self.runtime.append(
                session_id,
                "tools/pre-execute",
                actor,
                {
                    **call_payload,
                    "allowed": False,
                    "approval": "ask",
                    "approval_decision": "denied",
                    "reason": approval_reason,
                },
                source_key=f"{source_prefix}:pre",
            )
            self.runtime.append(
                session_id,
                "approval/ask",
                actor,
                {"tool_id": tool_id, "call_id": call_id, "decision": "denied", "reason": approval_reason},
                source_key=f"{source_prefix}:approval",
            )
            self._append_denied_result(
                session_id=session_id,
                actor=actor,
                source_prefix=source_prefix,
                tool_id=tool_id,
                call_id=call_id,
                message=f"工具 {tool_id} 需要具名一次性审批（approval=ask）",
                reason="approval_denied",
            )
            raise PermissionError(f"Tool {tool_id} 需要具名一次性审批")

        self.runtime.append(
            session_id,
            "tools/pre-execute",
            actor,
            {
                **call_payload,
                "allowed": True,
                "permissions": sorted(spec.permissions),
                "approval": spec.approval,
                "approval_decision": approval_reason,
            },
            source_key=f"{source_prefix}:pre",
        )
        if spec.approval == "ask":
            self.runtime.append(
                session_id,
                "approval/ask",
                actor,
                {"tool_id": tool_id, "call_id": call_id, "decision": "allowed-once", "reason": approval_reason},
                source_key=f"{source_prefix}:approval",
            )

        try:
            self.runtime.append(
                session_id,
                "tools/execute",
                actor,
                {"tool_id": tool_id, "call_id": call_id},
                source_key=f"{source_prefix}:exec",
            )
            result = spec.handler(context, args)
            if not isinstance(result, dict):
                raise ValueError("Tool handler 必须返回 dict 证据")
            summary = _summarize(result)
            self.runtime.append(
                session_id,
                "tools/post-execute",
                actor,
                {"tool_id": tool_id, "call_id": call_id, "success": True, "verified": True},
                source_key=f"{source_prefix}:post",
            )
            self.runtime.append(
                session_id,
                "tools/result",
                actor,
                {"tool_id": tool_id, "call_id": call_id, "isError": False, "frozen": True, "summary": summary},
                source_key=f"{source_prefix}:tools-result",
            )
            self.runtime.append(
                session_id,
                "tool/result",
                actor,
                {"tool_id": tool_id, "call_id": call_id, "isError": False, "summary": summary},
                source_key=f"{source_prefix}:result",
            )
            return result
        except Exception as exc:
            self.runtime.append(
                session_id,
                "tools/post-execute",
                actor,
                {"tool_id": tool_id, "call_id": call_id, "success": False, "error": str(exc)},
                source_key=f"{source_prefix}:post",
            )
            self.runtime.append(
                session_id,
                "tools/result",
                actor,
                {"tool_id": tool_id, "call_id": call_id, "isError": True, "frozen": True},
                source_key=f"{source_prefix}:tools-result",
            )
            self.runtime.append(
                session_id,
                "tool/result",
                actor,
                {
                    "tool_id": tool_id,
                    "call_id": call_id,
                    "isError": True,
                    "summary": {"message": str(exc)},
                },
                source_key=f"{source_prefix}:result",
            )
            raise
