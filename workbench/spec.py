from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path


REQUIRED_SECTIONS = ("来源", "目标", "非目标", "约束", "验收用例", "完成定义")


@dataclass(frozen=True)
class ParsedSpec:
    source: str
    goal: str
    non_goals: str
    constraints: str
    acceptance: str
    done: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "goal": self.goal,
            "non_goals": self.non_goals,
            "constraints": self.constraints,
            "acceptance": self.acceptance,
            "done": self.done,
        }


def parse_spec(text: str) -> ParsedSpec:
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE))
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[name] = text[match.end():end].strip()
    missing = [name for name in REQUIRED_SECTIONS if not sections.get(name)]
    if missing:
        raise ValueError(f"Spec 缺少必要章节：{', '.join(missing)}")
    return ParsedSpec(
        source=sections["来源"], goal=sections["目标"], non_goals=sections["非目标"],
        constraints=sections["约束"], acceptance=sections["验收用例"], done=sections["完成定义"],
    )


def load_spec(path: str | Path = "FDE_SPEC.md") -> ParsedSpec:
    return parse_spec(Path(path).read_text(encoding="utf-8"))


BUSINESS_REF_PATTERN = re.compile(
    r"\b(SKU|CHANNEL|CHANNEL_ORDER|SALES_ORDER|ORDER|PURCHASE|PRODUCT|CUSTOMER|SUPPLIER|REQUIREMENT):"
    r"([A-Za-z0-9][A-Za-z0-9._/-]{1,127})\b",
    flags=re.IGNORECASE,
)


def normalize_requirement_id(value: str = "") -> str:
    candidate = value.strip().upper()
    if candidate:
        if not re.fullmatch(r"REQ-[A-Z0-9][A-Z0-9._-]{2,63}", candidate):
            raise ValueError("需求编号必须使用 REQ- 前缀，且只包含字母、数字、点、下划线或连字符")
        return candidate
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"REQ-AUTO-{day}-{uuid.uuid4().hex[:6].upper()}"


def normalize_business_refs(request: str, values: list[str] | None = None) -> list[str]:
    refs: list[str] = []
    for raw in values or []:
        value = str(raw).strip()
        match = BUSINESS_REF_PATTERN.fullmatch(value)
        if not match:
            raise ValueError(f"业务对象引用格式无效：{value}")
        refs.append(f"{match.group(1).upper()}:{match.group(2)}")
    for match in BUSINESS_REF_PATTERN.finditer(request):
        refs.append(f"{match.group(1).upper()}:{match.group(2)}")
    return list(dict.fromkeys(refs))


def _business_acceptance(request: str, business_refs: list[str]) -> list[str]:
    signal = f"{request} {' '.join(business_refs)}".upper()
    cases: list[str] = []
    if any(token in signal for token in ("库存", "缺货", "预占", "SKU:")):
        cases.extend([
            "给定需求量大于可用库存，当执行订单预占时，那么整单失败，`reserved` 与库存流水均不产生部分写入。",
            "给定并发请求竞争同一 SKU，当事务提交时，那么 `available = on_hand - reserved` 始终不小于 0。",
        ])
    if any(token in signal for token in ("采购", "补货", "PURCHASE:")):
        cases.extend([
            "给定补货建议尚未由具名人员批准，当尝试收货时，那么请求被阻断且库存不变。",
            "给定已批准采购，当相同入库幂等键重放时，那么库存和流水只增加一次。",
        ])
    if any(token in signal for token in ("渠道", "平台", "CHANNEL:", "CHANNEL_ORDER:")):
        cases.extend([
            "给定同一平台订单重复推送，当接入渠道订单时，那么只保留一个业务身份，变化重放必须显式冲突。",
            "给定 SKU 未映射、地址无效或库存不足，当统一审单时，那么异常保持可见且不得进入错误履约。",
        ])
    if any(token in signal for token in ("订单", "履约", "发货", "SALES_ORDER:", "ORDER:")):
        cases.extend([
            "给定订单未完成合法前置状态，当尝试发货时，那么状态迁移被拒绝。",
            "给定已预占订单被取消，当取消完成时，那么全部预占被释放并留下可追溯事件。",
        ])
    if not cases:
        cases.extend([
            "给定需求中声明的业务对象，当执行正常路径时，那么对象状态产生可查询、可追溯的预期变化。",
            "给定任一前置条件不满足，当执行失败路径时，那么返回明确原因且不留下部分副作用。",
        ])
    # Preserve order while avoiding repeated templates triggered by overlapping signals.
    return list(dict.fromkeys(cases))


def build_delivery_spec(request: str, requirement_id: str, business_refs: list[str]) -> str:
    request = request.strip()
    if not request:
        raise ValueError("需求不能为空")
    refs = "、".join(business_refs) if business_refs else f"REQUIREMENT:{requirement_id}"
    business_cases = _business_acceptance(request, business_refs)
    acceptance_lines = [
        *business_cases,
        "给定本需求和关联业务对象，当平台接收请求时，那么生成唯一 Task ID 和当前 Spec 文件。",
        "给定已校验 Spec，当平台执行任务时，那么状态只能按 `queued → spec_ready → executing → evaluating` 迁移。",
        "给定 Blocking Eval 全绿，当评测结束时，那么任务进入 `review`，不得自动进入 `completed`。",
        "给定任一 Blocking Eval 失败，当评测结束时，那么任务进入 `rework`，保存失败项和报告摘要。",
        "给定任务处于 `review`，当具名审核人批准或驳回时，那么分别进入 `completed` 或 `rework`，并保存审核理由。",
    ]
    acceptance = "\n".join(f"{index}. {line}" for index, line in enumerate(acceptance_lines, 1))
    return f"""# {requirement_id}｜自动生成的交付 Spec

> 该文件由 FlowERP 交付工作台从需求入口生成。它是本次任务合同草案，不替代领域服务权限、数据库状态或具名人工审核。

## 来源

- 需求编号：`{requirement_id}`
- 业务对象：{refs}
- 原始需求：{request}

## 目标

{request}

交付结果必须能够通过需求编号、任务编号、业务对象引用和 Eval 报告相互追溯。

## 非目标

- 不直接修改运行数据库、库存余额、审批记录或审计历史。
- 不绕过订单、采购、库存和财务状态机。
- 不通过删除 Eval、降低阻断等级或伪造页面状态获得绿色结果。
- 不修改与本需求无关的模块、依赖和部署配置。

## 约束

- 可用库存不得为负，预占必须原子化。
- 同一个入库幂等键只能生效一次。
- 订单只能按合法状态迁移，取消必须释放预占。
- 采购补货必须经过具名人工审批才能入库。
- 任务、Eval 报告、工具结果和审核决定必须保留可追溯证据。

## 验收用例

{acceptance}

## 完成定义

- 当前 Spec 可被解析且六个必要章节完整。
- Blocking Harness 的 decision、失败数和退出语义一致。
- 状态事件包含 actor、时间、迁移原因和关键证据。
- 全绿后仍完成具名人工验收；失败和剩余风险没有被隐藏。
"""


def write_delivery_spec(path: str | Path, request: str, requirement_id: str,
                        business_refs: list[str]) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(build_delivery_spec(request, requirement_id, business_refs), encoding="utf-8")
    # Parse before publish so an incomplete generated contract never becomes the task's active Spec.
    load_spec(temporary)
    temporary.replace(target)
    return target
