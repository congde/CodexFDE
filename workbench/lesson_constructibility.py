from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class LessonGap:
    number: int
    student_must_construct: str
    honest_failure: str
    overlay_files: tuple[str, ...]
    already_answered_on_head: bool = True


# 诊断：HEAD 已是终态答案。隔离工作区必须剥掉本讲增量，学员才能亲手构造。
GAPS: tuple[LessonGap, ...] = (
    LessonGap(1, "签署 Spec、最小任务证据账与首次自举", "五个工作台命令缺失或缺证据仍报通过", ("workbench/bootstrap.py", "tests/test_l01_workbench_bootstrap.py", "docs/courses/L01/WORKBENCH_SPEC.md")),
    LessonGap(2, "AGENTS 规则补丁与越界判决", "越界请求被放行", ()),
    LessonGap(3, "六段 Spec 解析器", "缺字段仍能解析", ("workbench/spec.py",)),
    LessonGap(4, "库存导出与受控执行摘要", "导出字段/排序与权威库存不一致", ("flowerp/service.py",)),
    LessonGap(5, "幂等入库 Eval 与实现", "同一入库键再次增加库存", ("flowerp/service.py",)),
    LessonGap(6, "统一 Harness 与可用库存口径", "预占后 available 为负仍成功", ("flowerp/service.py",)),
    LessonGap(7, "订单总额守卫与 Codex Hook", "明细合计与总额不一致", ("flowerp/service.py", ".codex/hooks.json")),
    LessonGap(8, "原子预占与 CI 证据信封", "缺货留下部分预占或假绿信封", ("flowerp/service.py", "workbench/ci_evidence.py")),
    LessonGap(9, "取消释放与 Repair Task", "取消已预占订单不释放库存", ("flowerp/service.py",)),
    LessonGap(10, "合法状态迁移与有界 Loop", "未预占订单可直接发货", ("flowerp/service.py",)),
    LessonGap(11, "采购申请与写集冲突判决", "共享写集被标为可并行或申请丢失原因", ("flowerp/service.py", "agent/schedule.py")),
    LessonGap(12, "具名审批后入库与 Graph", "未审批采购改变库存", ("flowerp/service.py",)),
    LessonGap(13, "Task API 停在人审", "提交路由缺失或202被当成完成", ("workbench/workbench_server.py",)),
    LessonGap(14, "工作台面板与 ERP 四方对账", "投影视图写死旧状态", ("workbench/delivery_view.py",)),
    LessonGap(15, "反馈治理与独立候选 Eval", "未审核反馈改写 blocking", ()),
    LessonGap(16, "现场未知需求证据链", "用静态 Eval 冒充抽题", ()),
)


@dataclass(frozen=True)
class Overlay:
    path: str
    old: str
    new: str


def lesson_gap(number: int) -> LessonGap:
    for item in GAPS:
        if item.number == number:
            return item
    raise ValueError("课次必须在 1 到 16 之间")


def diagnose() -> dict:
    return {
        "schema_version": "1.0",
        "finding": "讲义在教构建，HEAD 在提供答案。隔离工作区必须缺本讲产物。",
        "lessons": [asdict(item) for item in GAPS],
    }


def _overlays(number: int) -> tuple[Overlay, ...]:
    export_fn = '''    def export_inventory(self) -> str:
        rows = self.inventory()
        lines = ["sku,name,site,location,lot_id,on_hand,reserved,available"]
        for row in rows:
            lines.append(
                f"{row['sku']},{row['name']},MAIN,STOCK,,{row['on_hand']},{row['reserved']},{row['available']}"
            )
        return "\\n".join(lines)
'''
    broken_export = '''    def export_inventory(self) -> str:
        rows = list(reversed(self.inventory()))
        lines = ["sku,name,available"]
        for row in rows:
            lines.append(f"{row['sku']},{row['name']},{row['available']}")
        return "\\n".join(lines)
'''
    mapping: dict[int, tuple[Overlay, ...]] = {
        3: (
            Overlay(
                "workbench/spec.py",
                "    missing = [name for name in REQUIRED_SECTIONS if name not in names]\n"
                "    if missing:\n"
                "        raise ValueError(f\"Spec 缺少必要章节：{', '.join(missing)}\")\n"
                "    if tuple(names) != REQUIRED_SECTIONS:\n"
                "        expected = \" → \".join(REQUIRED_SECTIONS)\n"
                "        actual = \" → \".join(names)\n"
                "        raise ValueError(f\"Spec 章节顺序错误；应为：{expected}；实际为：{actual}\")",
                "    missing = [name for name in REQUIRED_SECTIONS if name not in names]\n"
                "    if False and missing:\n"
                "        raise ValueError(f\"Spec 缺少必要章节：{', '.join(missing)}\")\n"
                "    if False and tuple(names) != REQUIRED_SECTIONS:\n"
                "        expected = \" → \".join(REQUIRED_SECTIONS)\n"
                "        actual = \" → \".join(names)\n"
                "        raise ValueError(f\"Spec 章节顺序错误；应为：{expected}；实际为：{actual}\")",
            ),
            Overlay(
                "workbench/spec.py",
                "    empty = [name for name in REQUIRED_SECTIONS if not sections[name]]",
                "    for name in REQUIRED_SECTIONS:\n"
                "        sections.setdefault(name, \"未校验\")\n"
                "    empty = [name for name in REQUIRED_SECTIONS if not sections[name]]",
            ),
        ),
        4: (Overlay("flowerp/service.py", export_fn, broken_export),),
        5: (Overlay(
            "flowerp/service.py",
            "            exists = conn.execute(\"SELECT 1 FROM inventory_events WHERE event_key=?\", (event_key,)).fetchone()\n            if exists:",
            "            exists = conn.execute(\"SELECT 1 FROM inventory_events WHERE event_key=?\", (event_key,)).fetchone()\n            if False and exists:",
        ),),
        6: (Overlay(
            "flowerp/service.py",
            "                if line[\"available\"] < line[\"quantity\"]:\n                    raise InsufficientStock(line[\"sku\"], line[\"quantity\"], line[\"available\"])",
            "                if False and line[\"available\"] < line[\"quantity\"]:\n                    raise InsufficientStock(line[\"sku\"], line[\"quantity\"], line[\"available\"])",
        ),),
        7: (Overlay(
            "flowerp/service.py",
            "        total = sum(line.line_total_cents for line in prepared)",
            "        total = 0",
        ),),
        8: (
            Overlay(
                "flowerp/service.py",
                "                if line[\"available\"] < line[\"quantity\"]:\n                    raise InsufficientStock(line[\"sku\"], line[\"quantity\"], line[\"available\"])\n            for line in lines:",
                "                if line[\"available\"] < line[\"quantity\"]:\n                    continue\n            for line in lines:\n                if line[\"available\"] < line[\"quantity\"]:\n                    continue",
            ),
            Overlay(
                "workbench/ci_evidence.py",
                "    if not sha or not run_id:\n        raise SystemExit(\"Evidence Envelope 缺少 GITHUB_SHA 或 GITHUB_RUN_ID\")",
                "    sha = sha or \"unsigned\"\n    run_id = run_id or \"local\"",
            ),
        ),
        9: (Overlay(
            "flowerp/service.py",
            "            if status == OrderStatus.RESERVED:\n                lines = conn.execute(\"SELECT sku,quantity FROM sales_order_lines WHERE order_id=?\", (order_id,)).fetchall()\n                for line in lines:",
            "            if False and status == OrderStatus.RESERVED:\n                lines = conn.execute(\"SELECT sku,quantity FROM sales_order_lines WHERE order_id=?\", (order_id,)).fetchall()\n                for line in lines:",
        ),),
        10: (Overlay(
            "flowerp/service.py",
            "            if order[\"status\"] != OrderStatus.RESERVED:\n                raise InvalidTransition(f\"只有 reserved 订单可发货，当前为 {order['status']}\")",
            "            if False and order[\"status\"] != OrderStatus.RESERVED:\n                raise InvalidTransition(f\"只有 reserved 订单可发货，当前为 {order['status']}\")",
        ),),
        11: (
            Overlay(
                "flowerp/service.py",
                "        if quantity <= 0 or not reason.strip():\n            raise ValueError(\"采购数量和原因不能为空\")",
                "        if quantity <= 0:\n            raise ValueError(\"采购数量不能为空\")\n        reason = reason or \"\"",
            ),
            Overlay(
                "agent/schedule.py",
                '    return left == right or left == "." or right == "." or left.startswith(right + "/") or right.startswith(left + "/")',
                '    return False  # L11 起点：尚未实现读写范围相交判定',
            ),
        ),
        12: (Overlay(
            "flowerp/service.py",
            "        if item[\"status\"] != PurchaseStatus.APPROVED:\n            raise ApprovalRequired(f\"采购 {request_id} 未审批，不允许入库\")",
            "        if False and item[\"status\"] != PurchaseStatus.APPROVED:\n            raise ApprovalRequired(f\"采购 {request_id} 未审批，不允许入库\")",
        ),),
    }
    mapping[13] = (Overlay(
        "workbench/workbench_server.py",
        '    def accept_course_task(self, lesson: int, request: str, actor: str, key: str) -> dict:\n',
        '    def accept_course_task(self, lesson: int, request: str, actor: str, key: str) -> dict:\n'
        '        raise NotImplementedError("L13 工作台异步任务受理尚未实现")\n',
    ),)
    mapping[14] = (Overlay(
        "workbench/delivery_view.py",
        '    status = _status_view(task)',
        '    status = _status_view({"status": "queued"})  # L14 起点：尚未投影真实状态',
    ),)
    return mapping.get(number, ())


def apply_student_start(workspace: str | Path, number: int) -> dict:
    """Make the isolated worktree miss this lesson's increment so pre-eval is honestly red."""
    root = Path(workspace).resolve()
    lesson_gap(number)
    if root == Path(__file__).resolve().parents[1]:
        raise ValueError("只能在隔离工作区剥离增量，不能修改控制仓库")
    applied: list[str] = []
    existing: list[str] = []
    unapplied: list[dict] = []
    if number == 1:
        bootstrap = root / "workbench" / "bootstrap.py"
        if bootstrap.is_file():
            bootstrap.write_text(
                '"""L01 起点：学生从签署的 Spec 构造命令与证据账。"""\n'
                'def add_bootstrap_commands(subparsers):\n    pass\n\n'
                'def run_bootstrap_command(args):\n    raise NotImplementedError("L01 工作台尚未实现")\n',
                encoding="utf-8",
            )
            applied.append("workbench/bootstrap.py")
        for relative in ("tests/test_l01_workbench_bootstrap.py", "docs/courses/L01/WORKBENCH_SPEC.md"):
            target = root / relative
            if target.is_file():
                target.unlink()
                applied.append(relative)
    removed_gates = []
    for relative in ("docs/courses/labs/baselines/PROGRESSION.json", "course/baselines/PROGRESSION.json"):
        progression = (root / relative).resolve()
        if root not in progression.parents:
            raise ValueError("课程门槛文件指向隔离工作区之外")
        if progression.is_file():
            content = progression.read_bytes()
            removed_gates.append({"path": relative, "sha256": __import__('hashlib').sha256(content).hexdigest(),
                                  "content": content.decode('utf-8')})
            progression.unlink()
    pending: dict[str, str] = {}
    for overlay in _overlays(number):
        target = root / overlay.path
        if not target.is_file():
            unapplied.append({"path": overlay.path, "reason": "source_missing"})
            continue
        text = pending[overlay.path] if overlay.path in pending else target.read_text(encoding="utf-8")
        if overlay.new in text or (number == 13 and overlay.path == 'workbench/workbench_server.py'
                                   and 'raise NotImplementedError("L13 工作台异步任务受理尚未实现")' in text):
            existing.append(overlay.path)
            continue
        if overlay.old not in text:
            if number == 13 and overlay.path == 'workbench/workbench_server.py':
                import re
                anchors = re.findall(r'^    def accept_course_task\([^\n]+\) -> dict:\n', text, re.MULTILINE)
                if len(anchors) == 1:
                    pending[overlay.path] = text.replace(anchors[0], anchors[0] +
                        '        raise NotImplementedError("L13 工作台异步任务受理尚未实现")\n', 1)
                    applied.append(overlay.path)
                    continue
            if number == 4 and overlay.path == 'flowerp/service.py':
                # Older tagged baselines export inventory through the generic
                # exporter, before ERPService acquired its export facade.
                alternate = root / 'flowerp/import_export.py'
                if alternate.is_file():
                    legacy = alternate.read_text(encoding='utf-8')
                    lines = legacy.splitlines(keepends=True)
                    inventory = [line for line in lines if line.lstrip().startswith('"inventory":("SELECT p.sku,p.name,')]
                    if len(inventory) == 1 and 'def export_csv(' in legacy:
                        pending['flowerp/import_export.py'] = legacy.replace(inventory[0], '', 1)
                        applied.append('flowerp/import_export.py')
                        continue
            unapplied.append({"path": overlay.path, "reason": "anchor_missing"})
            continue
        pending[overlay.path] = text.replace(overlay.old, overlay.new, 1)
        applied.append(overlay.path)
    # Multiple edits to one source are composed in memory, then written once.
    for relative, text in pending.items():
        (root / relative).write_text(text, encoding="utf-8")
    marker = root / ".course" / "student-start.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "lesson": number,
        "removed_progression_gate": bool(removed_gates),
        "removed_progression_gates": removed_gates,
        "applied_overlays": applied,
        "existing_overlays": existing,
        "unapplied_overlays": unapplied,
        "start_gap_applied": bool(applied or existing) and not unapplied,
        "gap": asdict(lesson_gap(number)),
        "note": "剥离记录只说明已应用的缺口；须执行本讲 Eval 证明起点为红。改 PROGRESSION.json 不能代替实现。",
    }
    marker.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
