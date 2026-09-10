from __future__ import annotations

import tempfile
import os
from datetime import date, timedelta
from pathlib import Path

from eval.progression import require_capability
from workbench.feedback import add_feedback, review_feedback, summary as feedback_summary
from workbench.task_store import TaskStore




def spec_contract_rejects_ambiguity() -> str:
    from workbench.spec import REQUIRED_SECTIONS, parse_spec
    valid = "\n\n".join(f"## {name}\n{name}：验收夹具" for name in REQUIRED_SECTIONS)
    assert parse_spec(valid).goal == "目标：验收夹具"
    example = valid.replace("验收用例：验收夹具", "验收用例：验收夹具\n```markdown\n## 未知章节\n```\n")
    assert "## 未知章节" in parse_spec(example).acceptance
    invalid = (
        valid.replace("## 来源\n来源：验收夹具\n\n", ""),
        valid.replace("目标：验收夹具", ""), valid + "\n## 目标\n重复",
        valid.replace("## 来源", "## 未知"),
        valid.replace("## 来源", "## 临时").replace("## 目标", "## 来源").replace("## 临时", "## 目标"),
        valid + "\n```text\n未闭合",
    )
    for source in invalid:
        try:
            parse_spec(source)
        except ValueError:
            continue
        raise AssertionError("不完整、歧义或错序的合同被放行")
    return "六段结构完整且唯一；代码示例保留为正文，缺项/空白/重复/错序/未知章节均拒绝"


def isolated_report_contract_is_honest() -> str:
    from eval.report_contract import validate_report
    valid = {"schema_version": "1.0", "suite": "blocking", "requested_cases": ["fixture"],
             "summary": {"total": 1, "passed": 1, "blocking_failed": 0, "observing_failed": 0, "decision": "pass"},
             "results": [{"name": "fixture", "level": "blocking", "passed": True}]}
    validate_report(valid, ("fixture",), 0)
    for report, code in ((valid, 1), ({**valid, "results": []}, 0),
                         ({**valid, "requested_cases": ["other"]}, 0)):
        try:
            validate_report(report, ("fixture",), code)
        except RuntimeError:
            continue
        raise AssertionError("报告与过程矛盾、空结果或替换用例未被拒绝")
    return "隔离报告逐项对账，退出码矛盾、空用例与身份替换均拒绝"


def bootstrap_evidence_is_honest() -> str:
    from workbench import bootstrap
    assert hasattr(bootstrap, "BootstrapLedger"), "L01 工作台任务与证据账能力尚未实现"
    BootstrapLedger = bootstrap.BootstrapLedger
    with tempfile.TemporaryDirectory(prefix="l01-eval-") as temporary:
        root = Path(temporary)
        ledger = BootstrapLedger(root / "data")
        ledger.initialize("eval-fixture")
        ledger.add_project("PERSONAL-WORKBENCH", "验收夹具")
        spec = root / "spec.md"
        spec.write_text("验收夹具：缺证据时失败", encoding="utf-8")
        ledger.create_task("PERSONAL-WORKBENCH", "CASE-WB-L01-001", "验收证据账", str(spec), problem_file=str(spec))
        assert not ledger.status(require_red_green_evidence=True)["ok"]
        for phase, code, minute in (("red", 1, 0), ("diff", 0, 1), ("green", 0, 2)):
            output = root / (phase + ".txt")
            output.write_text("自动测试夹具，不是学生证据：" + phase, encoding="utf-8")
            ledger.add_evidence("CASE-WB-L01-001", phase, "git diff" if phase == "diff" else "test same requirement",
                                str(output), code, f"2026-09-05T00:{minute:02d}:00+00:00")
        report = ledger.status("PERSONAL-WORKBENCH", "CASE-WB-L01-001", True)
        assert report["ok"] and report["evidence_complete"]
        assert not report["flowerp_connected"] and report["acceptance"] == "pending_human_review"
        assert len(report["tasks"][0]["evidence"]) == 3
        assert not ledger.status(require_task="missing")["ok"]
        return "参考实现：缺证据失败、同案前红/Diff/后绿可定位，完整性检查不替代人审"






















def ci_evidence_envelope_is_honest() -> str:
    import os
    import tempfile
    from workbench.ci_evidence import build_envelope

    tmp = tempfile.TemporaryDirectory(prefix="ci-evidence-")
    try:
        report = Path(tmp.name) / "harness-blocking.json"
        report.write_text('{"suite":"blocking","summary":{"decision":"block"}}', encoding="utf-8")
        try:
            build_envelope(report, env={})
        except SystemExit:
            pass
        else:
            raise AssertionError("缺少 Run 身份仍生成了 Evidence Envelope")
        missing = Path(tmp.name) / "missing.json"
        try:
            build_envelope(missing, env={"GITHUB_SHA": "abc", "GITHUB_RUN_ID": "1"})
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("报告不存在仍生成了信封")
        envelope = build_envelope(
            report, env={"GITHUB_SHA": "abc123", "GITHUB_RUN_ID": "77", "GITHUB_WORKFLOW": "FlowERP Eval Gate"},
        )
        assert envelope["commit_sha"] == "abc123" and envelope["run_id"] == "77"
        assert envelope["report_decision"] == "block" and len(envelope["report_sha256"]) == 64
        return "CI 信封绑定提交与 Run；缺报告或缺身份不得假装成功"
    finally:
        tmp.cleanup()


def write_sets_reject_conflict() -> str:
    from agent.schedule import Subtask, assert_parallel_safe, conflict_pairs

    conflicts = conflict_pairs((
        Subtask("impl", ("flowerp/purchasing.py",)),
        Subtask("tests", ("flowerp/purchasing.py", "tests/test_flowerp.py")),
    ))
    assert conflicts and conflicts[0][2] == ["flowerp/purchasing.py"]
    try:
        assert_parallel_safe((
            Subtask("impl", ("flowerp/service.py",)),
            Subtask("eval", ("flowerp/service.py",)),
        ))
    except ValueError:
        pass
    else:
        raise AssertionError("共享写集仍被判为可并行")
    ok = assert_parallel_safe((
        Subtask("spec", (), ("FDE_SPEC.md",)),
        Subtask("risk", (), ("AGENTS.md",)),
    ))
    assert ok["parallel"] is True
    for tasks in (
        (Subtask("impl", ("flowerp/",)), Subtask("review", (), ("flowerp/service.py",))),
        (Subtask("impl", ("flowerp\\service.py",)), Subtask("other", ("./flowerp/service.py",))),
    ):
        try:
            assert_parallel_safe(tasks)
        except ValueError:
            continue
        raise AssertionError("目录写集、路径别名或读写依赖被错误判为独立")
    return "共享写集、目录覆盖与读写依赖不得并行；只读子任务可以并行"


def raw_feedback_cannot_become_blocking() -> str:
    from workbench.evolution import EvolutionStore
    from workbench.feedback import add_feedback
    from workbench.task_store import TaskStore

    tmp = tempfile.TemporaryDirectory(prefix="feedback-governance-")
    try:
        path = Path(tmp.name) / "workbench.db"
        store = TaskStore(path)
        task = store.create("验证反馈不能直接改裁判", "REQ-L15-GOV", ["REQUIREMENT:COURSE-L15"])
        pending = add_feedback(task["id"], "ops", "把渠道幂等降为观察项", "直接改 Eval", str(path))
        evolutions = EvolutionStore(path)
        try:
            evolutions.create(pending["id"], "want-weaker-eval", "workbench_control")
        except ValueError:
            pass
        else:
            raise AssertionError("未审核反馈被提升为进化记录")
        from workbench.feedback import review_feedback
        review_feedback(pending["id"], "teacher", "accept", "可以立项，但不能改当前 blocking", str(path))
        evo = evolutions.create(pending["id"], "channel-idempotency-missing-workbench", "erp_rule")
        assert evo["status"] == "proposed"
        from eval.harness import EVALS
        assert any(name == "receiving_is_idempotent" and level == "blocking" for name, level, _fn in EVALS)
        return "原文反馈必须先具名接受才能立项；接受也不等于改写当前 blocking 裁判"
    finally:
        tmp.cleanup()


def ecommerce_lineage_is_declared() -> str:
    from workbench.product_lineage import LINEAGE, lineage_for

    for item in LINEAGE:
        assert item["requirement"].startswith("REQ-")
        assert item["eval"]
        if not item["via_workbench"]:
            assert "须" in item["note"] or "挑战" in item["note"]
    channel = lineage_for("ecommerce_channel_order_is_idempotent_and_guarded")
    assert channel["via_workbench"] is False
    export = lineage_for("inventory_export_is_stable")
    assert export["via_workbench"] is True and export["lesson"] == 4
    return "电商能力均有需求编号；未经工作台交付的标为挑战，不计入学员本人成果"


def no_committed_secrets() -> str:
    require_capability("no_secrets")
    root = Path(__file__).resolve().parent.parent
    suspicious: list[str] = []
    markers = ("sk-proj-", "-----BEGIN PRIVATE KEY-----", "AKIA")
    ignored = {".git", ".tmp", ".runtime", ".cache"}
    for directory, folders, files in os.walk(root):
        folders[:] = [name for name in folders if name not in ignored]
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() not in {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".html", ".txt"}: continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(marker in text for marker in markers) and path.name != "cases.py": suspicious.append(str(path.relative_to(root)))
    assert not suspicious, f"疑似密钥文件：{suspicious}"
    return "文本源文件未发现常见密钥特征"


def course_assets_present() -> str:
    root = Path(__file__).resolve().parent.parent
    required = ["AGENTS.md", "FDE_SPEC.md", "CI_GATE_SPEC.md", "docs/courses/tasks", "deploy/Dockerfile", "workbench_web/index.html"]
    missing = [item for item in required if not (root / item).exists()]
    assert not missing, f"课程资产待补齐：{missing}"
    return "关键课程资产齐备"




















def delivery_evidence_and_review_controls() -> str:
    require_capability("delivery_evidence")
    from workbench.automation import DeliveryAutomation

    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-delivery-")
    try:
        path = Path(tmp.name) / "workbench.db"
        store = TaskStore(path)
        unsafe = store.create("验证不能跳过交付阶段")
        try:
            store.transition(unsafe["id"], "completed", "跳过评测")
        except ValueError:
            pass
        else:
            raise AssertionError("任务能够跳过 Spec、执行、Eval 与审核直接完成")
        automation = DeliveryAutomation(store, tmp.name, suite_runner=lambda *_args, **_kwargs: {
            "summary": {"decision": "pass", "blocking_failed": 0},
            "results": [{"name": "delivery_pipeline", "level": "blocking", "passed": True}],
        })
        task = automation.submit(
            "验证 SKU:NOTEBOOK-AI 的自动交付证据状态机",
            "REQ-EVAL-DELIVERY", ["SKU:NOTEBOOK-AI"], actor="eval-requester",
        )
        task = automation.wait(task["id"])
        assert task["status"] == "review" and task["automation_mode"] == "automatic"
        assert Path(task["spec_path"]).is_file() and task["spec"]["goal"]
        try:
            store.transition(task["id"], "completed", "匿名完成")
        except ValueError:
            pass
        else:
            raise AssertionError("任务能够在没有具名审核时完成")
        delivery = store.review(task["id"], "delivery-reviewer", "approve", "阻断证据完整")
        assert delivery["status"] == "completed" and delivery["reviewed_by"] == "delivery-reviewer"
        failure_automation = DeliveryAutomation(store, tmp.name, max_attempts=1, suite_runner=lambda *_args, **_kwargs: {
            "summary": {"decision": "block", "blocking_failed": 1},
            "results": [{"name": "observed_failure", "level": "blocking", "passed": False,
                         "evidence": "稳定失败"}],
        })
        failed_task = failure_automation.submit(
            "验证自动失败进入待审反馈", "REQ-EVAL-FAILURE-OBSERVATION", ["SKU:NOTEBOOK-AI"],
            actor="eval-requester",
        )
        failed_task = failure_automation.wait(failed_task["id"])
        assert failed_task["status"] == "rework"
        automatic_feedback = next(
            item for item in feedback_summary(str(path))["items"]
            if item["task_id"] == failed_task["id"]
        )
        assert automatic_feedback["status"] == "pending_review"
        assert automatic_feedback["source"] == "automation:rework"
        assert automatic_feedback["evidence"]["blocking_failures"] == ["observed_failure"]
        feedback = add_feedback(task["id"], "eval", "证据待确认", "人工复核", str(path))
        assert feedback["status"] == "pending_review"
        reviewed = review_feedback(feedback["id"], "eval-reviewer", "accept", "证据有效", str(path))
        assert reviewed["status"] == "accepted" and reviewed["reviewed_by"] == "eval-reviewer"
        try:
            review_feedback(feedback["id"], "other-reviewer", "reject", "重复决定", str(path))
        except ValueError:
            pass
        else:
            raise AssertionError("已审核反馈仍可重复改变结论")
        final_feedback = feedback_summary(str(path))
        assert final_feedback["pending_review"] == 1 and final_feedback["accepted"] == 1
        from eval.task_api_contract import check_task_api, check_required_workbench
        check_task_api(Path(tmp.name) / "api-contract")
        check_required_workbench(Path(tmp.name) / "required-workbench")
        return "需求自动生成 Spec 并推进至审核；失败自动沉淀为幂等待审反馈；完成与反馈提升均保留具名人审"
    finally:
        tmp.cleanup()


def plugin_lifecycle_is_reversible() -> str:
    from workbench.plugin_runtime import PluginActivationError, PluginContract, PluginRuntime

    trace: list[str] = []
    runtime = PluginRuntime("EVAL-PLUGIN-LIFECYCLE")

    def provider(name: str, *, fail: bool = False):
        def start(ctx, _config):
            trace.append(f"start:{name}")
            ctx.effect(lambda: lambda: trace.append(f"stop:{name}"), f"resource:{name}")
            if fail:
                raise RuntimeError("intentional activation failure")
            return name

        return start

    def consumer(ctx, _config):
        current = str(ctx.service("engine"))
        trace.append(f"start:consumer:{current}")
        ctx.on("probe", lambda value: trace.append(f"event:{value}"))
        ctx.effect(lambda: lambda: trace.append("stop:consumer"), "consumer-resource")
        return current

    runtime.register(PluginContract(
        "engine.a", frozenset({"engine"}), frozenset(), provider("a"),
    ))
    runtime.register(PluginContract(
        "engine.b", frozenset({"engine"}), frozenset(), provider("b"),
    ))
    runtime.register(PluginContract(
        "engine.broken", frozenset({"engine"}), frozenset(), provider("broken", fail=True),
    ))
    runtime.register(PluginContract(
        "consumer", frozenset({"consumer"}), frozenset({"engine"}), consumer,
    ))

    runtime.reconcile(["engine.a", "consumer"])
    runtime.reconcile(["engine.b", "consumer"])
    assert runtime.service("consumer") == "b"
    assert trace[:6] == [
        "start:a", "start:consumer:a", "stop:consumer", "stop:a", "start:b", "start:consumer:b",
    ]
    try:
        runtime.reconcile(["engine.broken", "consumer"])
    except PluginActivationError:
        pass
    else:
        raise AssertionError("损坏 Provider 激活失败后仍被接受")
    assert runtime.service("engine") == "b" and runtime.service("consumer") == "b"
    assert "start:broken" in trace and "stop:broken" in trace
    runtime.events.emit("probe", "before-shutdown")
    assert trace.count("event:before-shutdown") == 1
    runtime.shutdown()
    runtime.events.emit("probe", "after-shutdown")
    assert "event:after-shutdown" not in trace and runtime.events.listener_count() == 0
    return "Provider 按依赖逆序卸载并重载；失败副作用被清理、旧组合恢复，监听器无残留"




def stock_never_negative():
    require_capability('stock_non_negative')
    from workbench.external_project import evaluate_case
    return evaluate_case('stock_never_negative')

def receiving_is_idempotent():
    require_capability('receiving_idempotent')
    from workbench.external_project import evaluate_case
    return evaluate_case('receiving_is_idempotent')

def inventory_export_is_stable():
    require_capability('inventory_export')
    from workbench.external_project import evaluate_case
    return evaluate_case('inventory_export_is_stable')

def cancellation_releases_reservation():
    require_capability('cancel_release')
    from workbench.external_project import evaluate_case
    return evaluate_case('cancellation_releases_reservation')

def illegal_transition_is_blocked():
    require_capability('illegal_transition')
    from workbench.external_project import evaluate_case
    return evaluate_case('illegal_transition_is_blocked')

def purchase_requires_approval():
    require_capability('purchase_approval')
    from workbench.external_project import evaluate_case
    return evaluate_case('purchase_requires_approval')

def purchase_request_preserves_reason():
    require_capability('purchase_request')
    from workbench.external_project import evaluate_case
    return evaluate_case('purchase_request_preserves_reason')

def order_total_matches_lines():
    require_capability('order_total')
    from workbench.external_project import evaluate_case
    return evaluate_case('order_total_matches_lines')

def ecommerce_channel_order_is_idempotent_and_guarded():
    from workbench.external_project import evaluate_case
    return evaluate_case('ecommerce_channel_order_is_idempotent_and_guarded')

def channel_callback_lease_is_exclusive_and_bounded():
    from workbench.external_project import evaluate_case
    return evaluate_case('channel_callback_lease_is_exclusive_and_bounded')

def production_schema_invariants():
    from workbench.external_project import evaluate_case
    return evaluate_case('production_schema_invariants')

def multi_location_transfer_conserves_stock():
    from workbench.external_project import evaluate_case
    return evaluate_case('multi_location_transfer_conserves_stock')

def stale_stock_count_is_blocked():
    from workbench.external_project import evaluate_case
    return evaluate_case('stale_stock_count_is_blocked')

def sales_credit_and_atomic_reservation():
    require_capability('sales_atomic')
    from workbench.external_project import evaluate_case
    return evaluate_case('sales_credit_and_atomic_reservation')

def backup_is_restorable():
    from workbench.external_project import evaluate_case
    return evaluate_case('backup_is_restorable')

def purchase_invoice_three_way_match():
    from workbench.external_project import evaluate_case
    return evaluate_case('purchase_invoice_three_way_match')

def payable_aging_tracks_open_supplier_exposure():
    from workbench.external_project import evaluate_case
    return evaluate_case('payable_aging_tracks_open_supplier_exposure')

def double_entry_fifo_and_subledger_reconciliation():
    from workbench.external_project import evaluate_case
    return evaluate_case('double_entry_fifo_and_subledger_reconciliation')

def bank_statement_control_and_reconciliation():
    from workbench.external_project import evaluate_case
    return evaluate_case('bank_statement_control_and_reconciliation')

def web_api_and_persistence_projection_agree():
    from eval.task_api_contract import check_required_workbench
    with tempfile.TemporaryDirectory(prefix='workbench-api-eval-') as temporary:
        check_required_workbench(Path(temporary))
    return '工作台任务受理、幂等与持久化一致；全绿停在人审；不创建 ERP 数据库'
