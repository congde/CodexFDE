from __future__ import annotations

# 电商能力必须能回指工作台交付，否则只算产品挑战，不计入学员本人成果。
LINEAGE: tuple[dict, ...] = (
    {
        "eval": "inventory_export_is_stable",
        "requirement": "REQ-COURSE-L04",
        "lesson": 4,
        "via_workbench": True,
        "note": "库存导出由 Workbench V0 受控交付",
    },
    {
        "eval": "receiving_is_idempotent",
        "requirement": "REQ-COURSE-L05",
        "lesson": 5,
        "via_workbench": True,
        "note": "幂等入库由失败优先 Eval 逼出",
    },
    {
        "eval": "stock_never_negative",
        "requirement": "REQ-COURSE-L06",
        "lesson": 6,
        "via_workbench": True,
        "note": "可用库存口径由统一 Harness 收口",
    },
    {
        "eval": "order_total_matches_lines",
        "requirement": "REQ-COURSE-L07",
        "lesson": 7,
        "via_workbench": True,
        "note": "销售订单经 Hook 复用 Harness",
    },
    {
        "eval": "sales_credit_and_atomic_reservation",
        "requirement": "REQ-COURSE-L08",
        "lesson": 8,
        "via_workbench": True,
        "note": "原子预占经本地/CI 同一入口复验",
    },
    {
        "eval": "cancellation_releases_reservation",
        "requirement": "REQ-COURSE-L09",
        "lesson": 9,
        "via_workbench": True,
        "note": "取消释放由 Repair Task 授权修复",
    },
    {
        "eval": "illegal_transition_is_blocked",
        "requirement": "REQ-COURSE-L10",
        "lesson": 10,
        "via_workbench": True,
        "note": "状态机由有界 Loop 收敛",
    },
    {
        "eval": "purchase_request_preserves_reason",
        "requirement": "REQ-COURSE-L11",
        "lesson": 11,
        "via_workbench": True,
        "note": "采购申请经写集调度后串行集成",
    },
    {
        "eval": "purchase_requires_approval",
        "requirement": "REQ-COURSE-L12",
        "lesson": 12,
        "via_workbench": True,
        "note": "审批入库经 Graph 具名人审",
    },
    {
        "eval": "ecommerce_channel_order_is_idempotent_and_guarded",
        "requirement": "REQ-ECOM-CHANNEL-001",
        "lesson": 15,
        "via_workbench": False,
        "note": "产品挑战：渠道幂等已在终态，须用工作台再交一次才算学员成果",
    },
    {
        "eval": "channel_callback_lease_is_exclusive_and_bounded",
        "requirement": "REQ-ECOM-CHANNEL-002",
        "lesson": 15,
        "via_workbench": False,
        "note": "产品挑战：回传租约须经反馈任务独立复验",
    },
    {
        "eval": "purchase_invoice_three_way_match",
        "requirement": "REQ-ECOM-FIN-001",
        "lesson": 15,
        "via_workbench": False,
        "note": "产品挑战：三单匹配不是 16 讲基础增量，须走 L15 反馈闭环",
    },
    {
        "eval": "double_entry_fifo_and_subledger_reconciliation",
        "requirement": "REQ-ECOM-FIN-002",
        "lesson": 15,
        "via_workbench": False,
        "note": "产品挑战：总账对账须用工作台再交才计入学员成果",
    },
    {
        "eval": "bank_statement_control_and_reconciliation",
        "requirement": "REQ-ECOM-FIN-003",
        "lesson": 15,
        "via_workbench": False,
        "note": "产品挑战：银行对账须经独立 Task 与新 Eval",
    },
)


def lineage_for(eval_name: str) -> dict:
    for item in LINEAGE:
        if item["eval"] == eval_name:
            return item
    raise KeyError(eval_name)


def undeclared_or_unproven(eval_names: list[str] | tuple[str, ...]) -> list[str]:
    known = {item["eval"] for item in LINEAGE}
    return [name for name in eval_names if name not in known]
