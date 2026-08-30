from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from .spec import parse_spec


@dataclass(frozen=True)
class LessonContract:
    number: int
    title: str
    phase: str
    workbench_increment: str
    erp_increment: str
    request: str
    business_refs: tuple[str, ...]
    write_scope: tuple[str, ...]
    acceptance: tuple[str, ...]
    eval_cases: tuple[str, ...]
    prerequisites: tuple[int, ...]
    baseline_ref: str
    live_request: bool = False
    dynamic_eval_required: bool = False

    @property
    def requirement_id(self) -> str:
        return f"REQ-COURSE-L{self.number:02d}"

    def as_dict(self) -> dict:
        result = asdict(self)
        result["requirement_id"] = self.requirement_id
        return result


def _lesson(number: int, title: str, phase: str, workbench: str, erp: str, request: str,
            *, refs: tuple[str, ...] = (), scope: tuple[str, ...] = (),
            acceptance: tuple[str, ...], evals: tuple[str, ...] = (),
            live_request: bool = False, dynamic_eval_required: bool = False) -> LessonContract:
    return LessonContract(
        number=number,
        title=title,
        phase=phase,
        workbench_increment=workbench,
        erp_increment=erp,
        request=request,
        business_refs=refs,
        write_scope=scope,
        acceptance=acceptance,
        eval_cases=evals,
        prerequisites=() if number == 1 else (number - 1,),
        baseline_ref=f"course/l{number:02d}-start",
        live_request=live_request,
        dynamic_eval_required=dynamic_eval_required,
    )


LESSONS: tuple[LessonContract, ...] = (
    _lesson(1, "终局与冷启动", "bootstrap", "终局地图、接手协议与环境证据", "冻结 ERP 起始快照",
            "复验参考终局并提交不冒充个人成果的接手报告。",
            scope=("course/", "docs/courses/"),
            acceptance=("接手报告记录环境、命令、实际结果与未验证项。", "参考终局与学生起始基线被明确区分。")),
    _lesson(2, "AGENTS 项目约束", "design", "仓库规则、写入边界与完成定义", "固化库存、订单、采购和追溯边界",
            "把 FlowERP 不可破坏规则和工作台完成定义写成可审查的仓库约束。",
            scope=("AGENTS.md", "tests/"),
            acceptance=("越界请求能够被规则明确判为拒绝。", "规则同时给出正常路径和失败后不变状态。")),
    _lesson(3, "Spec 驱动", "design", "六段式 Spec Schema 与解析器", "签字确认库存导出合同",
            "为 SKU 库存导出编写可解析、可验收且不提前扩展订单或采购功能的 Spec。",
            refs=("SKU:COURSE-DEMO",), scope=("FDE_SPEC.md", "workbench/spec.py", "tests/"),
            acceptance=("Spec 的六个必要章节可被解析。", "库存导出的列、排序、空结果与错误输入均有明确预期。")),
    _lesson(4, "Codex 最小变更", "build", "受控执行、Diff 摘要与最小 Eval", "交付库存导出",
            "让工作台在限定写集内实现 SKU 库存导出，并保存 Diff、命令和正反路径证据。",
            refs=("SKU:COURSE-DEMO",), scope=("flowerp/", "workbench/", "tests/"),
            acceptance=("库存导出结果字段与排序满足 L03 合同。", "导出值与 ERP 权威库存一致。", "执行结果列出实际写集与复验命令。"),
            evals=("inventory_export_is_stable",)),
    _lesson(5, "失败优先 Eval", "build", "失败优先的单例 Eval", "交付幂等入库",
            "先构造重复入库会失败的 Eval，再实现同一个幂等键只生效一次。",
            refs=("SKU:COURSE-DEMO",), scope=("flowerp/", "eval/", "tests/"),
            acceptance=("首次入库增加库存。", "同一幂等键重放不再次增加库存。", "保留修复前红灯和修复后绿灯。"),
            evals=("receiving_is_idempotent",)),
    _lesson(6, "Harness 证据汇总", "build", "统一 Harness、判决与退出码", "交付可用库存口径",
            "用统一 Harness 验证 available = on_hand - reserved 且永不为负。",
            refs=("SKU:COURSE-DEMO",), scope=("flowerp/", "eval/", "tests/"),
            acceptance=("可用库存按在手减预占计算。", "阻断失败、报告 decision 和进程退出码一致。"),
            evals=("stock_never_negative",)),
    _lesson(7, "Codex Hook", "build", "提交前本地护栏", "交付销售订单创建",
            "通过工作台交付销售订单创建，并让本地护栏复验订单金额和明细一致。",
            refs=("ORDER:COURSE-DEMO",), scope=("flowerp/", "eval/", "tests/", ".githooks/"),
            acceptance=("合法明细生成草稿订单和稳定身份。", "非法数量被拒绝且不留下订单。", "订单总额等于明细合计。"),
            evals=("order_total_matches_lines",)),
    _lesson(8, "CI 远程复验", "build", "远程复验与证据信封", "交付原子预占",
            "在同一套 Eval 的本地与 CI 复验下实现销售订单原子预占，缺货时整单回滚。",
            refs=("ORDER:COURSE-DEMO", "SKU:COURSE-DEMO"), scope=("flowerp/", "eval/", "tests/", ".github/workflows/"),
            acceptance=("库存充足时订单预占成功。", "任一行缺货时整单失败且无部分预占。", "本地与 CI 使用同一 Eval 身份。"),
            evals=("stock_never_negative", "sales_credit_and_atomic_reservation")),
    _lesson(9, "失败转修复任务", "repair", "报告到 Repair Task 的确定性映射", "交付取消释放预占",
            "把取消订单未释放预占的失败报告转成有界修复任务并完成修复。",
            refs=("ORDER:COURSE-DEMO",), scope=("flowerp/", "agent/", "eval/", "tests/"),
            acceptance=("取消已预占订单会完整释放预占。", "修复任务保存失败项、允许写集和复验命令。"),
            evals=("cancellation_releases_reservation",)),
    _lesson(10, "有界 Loop", "repair", "带预算与停止条件的修复 Loop", "交付合法订单状态迁移",
            "用最多三轮的修复 Loop 阻断订单跳过前置状态直接发货。",
            refs=("ORDER:COURSE-DEMO",), scope=("flowerp/", "agent/", "eval/", "tests/"),
            acceptance=("合法状态迁移成功。", "非法迁移被阻断且订单状态不变。", "预算耗尽被记录为未收敛而非成功。"),
            evals=("illegal_transition_is_blocked",)),
    _lesson(11, "Subagents 并行", "build", "独立写集调度与串行集成", "交付采购申请",
            "将采购申请拆成互不冲突的实现与反证任务，串行集成后统一复验。",
            refs=("PURCHASE:COURSE-DEMO",), scope=("flowerp/", "agent/", "eval/", "tests/"),
            acceptance=("采购申请保存 SKU、数量、原因和稳定身份。", "写集冲突的子任务不得并行。", "非法数量不产生采购申请。"),
            evals=("purchase_request_preserves_reason",)),
    _lesson(12, "Graph 与人工审核", "build", "显式状态图、回退边和具名人审", "交付审批后入库",
            "用显式 Graph 交付采购审批与入库，未经具名审批不得改变库存。",
            refs=("PURCHASE:COURSE-DEMO", "SKU:COURSE-DEMO"), scope=("flowerp/", "agent/", "eval/", "tests/"),
            acceptance=("未审批采购入库被阻断且库存不变。", "具名审批后允许一次幂等入库。", "Graph 状态与 ERP 权威状态可对账。"),
            evals=("purchase_requires_approval", "receiving_is_idempotent")),
    _lesson(13, "任务 API", "operate", "可追溯 Task API 与异步状态", "通过 API 交付补货需求",
            "从 Task API 提交补货需求，生成 Spec、执行 Eval，并停在具名人工审核。",
            refs=("PURCHASE:COURSE-DEMO",), scope=("workbench/", "flowerp/", "eval/", "tests/"),
            acceptance=("API 接受请求后返回 Task ID 而非伪称完成。", "任务事件可追溯到需求、业务对象和 Eval。", "全绿后仍停在人工审核。"),
            evals=("delivery_evidence_and_review_controls", "purchase_requires_approval")),
    _lesson(14, "Web 状态面板", "operate", "ERP/交付统一驾驶舱与证据下钻", "交付 ERP 操作页",
            "在无密钥 Web 面板展示 ERP 权威状态和交付证据，并能下钻到任务事件。",
            refs=("REQUIREMENT:COURSE-L14",), scope=("web/", "workbench/", "tests/"),
            acceptance=("页面数据来自 API 而非静态假数据。", "DOM、API、SQLite 与 ERP 状态可对账。", "页面不包含凭据。"),
            evals=("delivery_evidence_and_review_controls", "web_api_and_persistence_projection_agree", "no_committed_secrets")),
    _lesson(15, "摘要与反馈", "operate", "交付摘要、反馈审核与演进记录", "交付反馈驱动的 ERP 小改进",
            "将一条真实采用反馈审核为改进任务，通过工作台交付并保留升级前后证据。",
            refs=("REQUIREMENT:COURSE-L15",), scope=("workbench/", "flowerp/", "eval/", "tests/"),
            acceptance=("原始反馈先审核再进入执行合同。", "改进前后的失败、修订和采用结果均可追溯。", "反馈不能直接改写阻断裁判。"),
            evals=("delivery_evidence_and_review_controls",), dynamic_eval_required=True),
    _lesson(16, "冷启动答辩", "transfer", "冷启动、发布证据索引与迁移答辩", "现场交付此前未实现的小需求",
            "现场抽取一个此前未实现的 FlowERP 小需求，使用工作台完成 Spec、受控执行、Eval、人审和发布证据。",
            refs=("REQUIREMENT:LIVE-DRAW",), scope=("flowerp/", "workbench/", "eval/", "web/", "tests/"),
            acceptance=("需求在答辩现场抽取且仓库基线中尚未实现。", "正常、失败和失败后不变状态均有新证据。", "发布索引能追溯需求、Diff、Eval、人审与剩余风险。"),
            evals=("delivery_evidence_and_review_controls",), live_request=True, dynamic_eval_required=True),
)


def lesson_contract(number: int) -> LessonContract:
    if not 1 <= number <= len(LESSONS):
        raise ValueError("课次必须在 1 到 16 之间")
    return LESSONS[number - 1]


def render_lesson_spec(number: int, additional_eval_cases: tuple[str, ...] = ()) -> str:
    lesson = lesson_contract(number)
    refs = "、".join(f"`{item}`" for item in lesson.business_refs) or f"`REQUIREMENT:{lesson.requirement_id}`"
    selected_evals = lesson.eval_cases + tuple(additional_eval_cases)
    evals = "、".join(f"`{item}`" for item in selected_evals) or "本讲合同中的正反路径"
    acceptance = "\n".join(f"{index}. {item}" for index, item in enumerate(lesson.acceptance, 1))
    scope = "、".join(f"`{item}`" for item in lesson.write_scope)
    text = f"""# {lesson.requirement_id}｜L{number:02d} {lesson.title}

## 来源

- 课程主线合同：L{number:02d}
- 前置课次：{', '.join(f'L{item:02d}' for item in lesson.prerequisites) or '课程起点'}
- 业务对象：{refs}
- 起始基线：`{lesson.baseline_ref}`

## 目标

{lesson.request}

工作台增量：{lesson.workbench_increment}。  
ERP 产品增量：{lesson.erp_increment}。

## 非目标

- 不提前实现后续课次的 ERP 产品增量。
- 不修改本讲允许写集之外的文件。
- 不删除失败证据、降低 Eval 等级或绕过具名人工审核。

## 约束

- 允许写集：{scope}
- 本讲复用 Eval：{evals}
- 库存、订单、采购和任务状态必须遵守 `AGENTS.md` 的不可破坏规则。
- 执行结果必须保存需求、Diff、命令、Eval 和人工决定之间的稳定引用。

## 验收用例

{acceptance}

## 完成定义

- ERP 产品增量与工作台增量均有可复现证据，且能够说明二者因果。
- 正常路径、失败路径和失败后不变状态均已验证。
- 学生保存首次判断、失败、修订和复验结果；参考仓库终态不计作学生成果。
- 若 `course-status --require-baselines` 未通过，不得声称完成了渐进式课程复现。
"""
    parse_spec(text)
    return text


def write_lesson_spec(number: int, path: str | Path,
                      additional_eval_cases: tuple[str, ...] = ()) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(render_lesson_spec(number, additional_eval_cases), encoding="utf-8")
    parse_spec(temporary.read_text(encoding="utf-8"))
    temporary.replace(target)
    return target


def create_lesson_task(store, number: int, runtime_dir: str | Path, *, actor: str = "course-learner",
                       execution_mode: str = "verify", execution_timeout_seconds: int = 900,
                       additional_eval_cases: tuple[str, ...] = ()) -> dict:
    """Create a task that consumes the lesson contract instead of the end-state root Spec."""
    if number < 4:
        raise ValueError("L01-L03 是接手与设计阶段；课程交付任务从 L04 开始")
    lesson = lesson_contract(number)
    spec_path = Path(runtime_dir).resolve() / "course" / f"L{number:02d}" / "FDE_SPEC.md"
    write_lesson_spec(number, spec_path, additional_eval_cases)
    return store.create(
        request=lesson.request,
        requirement_id=lesson.requirement_id,
        business_refs=list(lesson.business_refs),
        spec_path=str(spec_path),
        actor=actor,
        execution_mode=execution_mode,
        write_scope=list(lesson.write_scope),
        execution_timeout_seconds=execution_timeout_seconds,
    )


def _git_ref_exists(root: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/tags/{ref}"],
        cwd=root, check=False, capture_output=True,
    )
    return result.returncode == 0


def _git_ref_commit(root: Path, ref: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{ref}^{{commit}}"],
        cwd=root, check=False, capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _git_is_ancestor(root: Path, older: str, newer: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        cwd=root, check=False, capture_output=True,
    )
    return result.returncode == 0


def lesson_baseline_status(root: str | Path, number: int, *,
                           revision_resolver: Callable[[Path, str], str | None] | None = None) -> dict:
    root_path = Path(root).resolve()
    lesson = lesson_contract(number)

    def resolve(revision: str) -> str | None:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", revision], cwd=root_path,
            check=False, capture_output=True, text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    resolver = revision_resolver or (lambda _root, revision: resolve(revision))
    baseline_commit = resolver(root_path, f"refs/tags/{lesson.baseline_ref}^{{commit}}")
    head_commit = resolver(root_path, "HEAD")
    return {
        "lesson": number,
        "baseline_ref": lesson.baseline_ref,
        "baseline_commit": baseline_commit,
        "head_commit": head_commit,
        "ready": bool(baseline_commit and head_commit and baseline_commit == head_commit),
    }


def validate_mainline(root: str | Path = ".", *,
                      ref_checker: Callable[[Path, str], bool] | None = None,
                      eval_names: Iterable[str] | None = None,
                      commit_resolver: Callable[[Path, str], str | None] | None = None,
                      ancestor_checker: Callable[[Path, str, str], bool] | None = None) -> dict:
    root_path = Path(root).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if [item.number for item in LESSONS] != list(range(1, 17)):
        errors.append("课次必须连续覆盖 L01-L16")
    for item in LESSONS:
        expected = () if item.number == 1 else (item.number - 1,)
        if item.prerequisites != expected:
            errors.append(f"L{item.number:02d} 前置关系不是上一讲")
        if not item.acceptance:
            errors.append(f"L{item.number:02d} 缺少验收用例")
        if item.number >= 4 and not item.write_scope:
            errors.append(f"L{item.number:02d} 缺少受控写集")
    if not LESSONS[-1].live_request:
        errors.append("L16 必须标记为现场未知需求")
    for item in LESSONS:
        if item.dynamic_eval_required != (item.number in {15, 16}):
            errors.append(f"L{item.number:02d} 动态 Eval 要求与课程阶段不一致")

    if eval_names is None:
        from eval.harness import EVALS
        available_evals = {name for name, _level, _fn in EVALS}
    else:
        available_evals = set(eval_names)
    for item in LESSONS:
        missing = sorted(set(item.eval_cases) - available_evals)
        if missing:
            errors.append(f"L{item.number:02d} 引用了不存在的 Eval：{', '.join(missing)}")

    checker = ref_checker or _git_ref_exists
    missing_refs = [item.baseline_ref for item in LESSONS if not checker(root_path, item.baseline_ref)]
    baseline_errors: list[str] = []
    baseline_commits: dict[str, str] = {}
    if not missing_refs:
        resolver = commit_resolver or _git_ref_commit
        for item in LESSONS:
            commit = resolver(root_path, item.baseline_ref)
            if not commit:
                baseline_errors.append(f"{item.baseline_ref} 无法解析为提交")
            else:
                baseline_commits[item.baseline_ref] = commit
        reverse: dict[str, list[str]] = {}
        for ref, commit in baseline_commits.items():
            reverse.setdefault(commit, []).append(ref)
        duplicate_groups = [refs for refs in reverse.values() if len(refs) > 1]
        for refs in duplicate_groups:
            baseline_errors.append(f"多个课次标签指向同一提交：{', '.join(refs)}")
        if not baseline_errors:
            is_ancestor = ancestor_checker or _git_is_ancestor
            for previous, current in zip(LESSONS, LESSONS[1:]):
                older = baseline_commits[previous.baseline_ref]
                newer = baseline_commits[current.baseline_ref]
                if not is_ancestor(root_path, older, newer):
                    baseline_errors.append(
                        f"基线历史不连续：{previous.baseline_ref} 不是 {current.baseline_ref} 的祖先"
                    )
    if missing_refs:
        warnings.append(
            f"缺少 {len(missing_refs)} 个逐讲起始基线；当前终态代码不能替代渐进式学习证据"
        )
    if baseline_errors:
        warnings.append("逐讲标签存在重复提交或非线性历史，不能证明产品状态逐讲推进")
    return {
        "schema_version": "1.0",
        "contract_valid": not errors,
        "course_ready": not errors and not missing_refs and not baseline_errors,
        "lesson_count": len(LESSONS),
        "errors": errors,
        "warnings": warnings,
        "missing_baseline_refs": missing_refs,
        "baseline_errors": baseline_errors,
        "baseline_commits": baseline_commits,
    }
