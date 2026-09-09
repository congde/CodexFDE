from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from flowerp import EcommerceDemo, ERPService, ERPStore
from flowerp.config import load_settings
from flowerp.identity import IdentityService
from flowerp.mock_data import load_mock_data, verify_mock_data
from flowerp.operations import BackupService, HealthService, RuntimeCoordinator
from .automation import DeliveryAutomation
from .course_mainline import LESSONS, create_lesson_task, lesson_baseline_status, lesson_contract, validate_mainline, write_lesson_spec
from .course_workspace import CourseWorktreeManager
from .course_release import CourseBaselinePublisher, CourseCandidateArtifacts
from .execution import CodexExecutionRunner
from .feedback import add_feedback
from .http_bind import ServerBindError, report_bind_error
from .platform_bootstrap import bootstrap_platform
from .platform_server import serve as serve_harness
from .server import serve
from .spec import load_spec
from .task_store import TaskStore
from .workflow import run_task
from .bootstrap import add_bootstrap_commands, run_bootstrap_command


def demo(runtime_dir: str | None = None) -> dict:
    owned_tmp = tempfile.TemporaryDirectory(prefix="flowerp-demo-") if runtime_dir is None else None
    runtime = Path(runtime_dir or owned_tmp.name)
    service = ERPService(ERPStore(runtime / "flowerp.db"))
    ecommerce = EcommerceDemo(service)
    ecommerce.reset()
    while not ecommerce.state()["is_complete"]:
        ecommerce.advance()
    scenario = ecommerce.state()
    task_store = TaskStore(runtime / "workbench.db")
    task = task_store.create(
        "验证电商笔记本订单不超卖、补货需审批且履约状态可追踪",
        "REQ-ECOM-001", ["CHANNEL:MOCK-TMALL-A/EC-20260817-1001", "SKU:NOTEBOOK-AI"],
    )
    task = run_task(task_store, task["id"])
    if task["status"] == "review":
        task = task_store.review(task["id"], "demo-reviewer", "approve", "阻断级 Eval 全绿，接受演示交付")
    add_feedback(task["id"], "demo-day", "审批边界与幂等规则已验证", "下一轮增加多仓隔离 Eval", str(runtime / "workbench.db"))
    result = {
        "scenario": "FlowERP 电商笔记本订单从库存缺口到补货履约",
        "ecommerce": scenario,
        "inventory": service.inventory(),
        "delivery_task": {"id": task["id"], "status": task["status"], "events": len(task["events"])},
    }
    if owned_tmp: owned_tmp.cleanup()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="个人研发工作台 CLI。工作台组织任务、授权与复验；Codex 是开发伙伴；FlowERP 是持续交付的客户产品。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "命令分组\n"
            "  工作台: serve-workbench, course-*, task-*, spec, feedback\n"
            "  客户项目: serve, demo, mock-data, verify-mock-data\n"
            "  可选平台（非大纲通过项）: harness-serve, harness-bootstrap\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add_bootstrap_commands(sub)
    demo_cmd = sub.add_parser("demo", help="客户项目：跑通一条 FlowERP 演示账本")
    demo_cmd.add_argument("--runtime-dir")
    mock_cmd = sub.add_parser("mock-data", help="客户项目：生成幂等的完整 ERP 验收账套")
    mock_cmd.add_argument("--runtime-dir", default=".runtime")
    verify_mock_cmd = sub.add_parser("verify-mock-data", help="客户项目：验证完整 ERP 验收账套")
    verify_mock_cmd.add_argument("--runtime-dir", default=".runtime")
    serve_cmd = sub.add_parser("serve", help="客户项目：启动 FlowERP（默认 :8000）")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8000)
    serve_cmd.add_argument("--runtime-dir", help="覆盖本机 services.json 中保存的数据目录")
    workbench_serve_cmd = sub.add_parser("serve-workbench", help="工作台：启动跟跑必做驾驶舱（默认 :8001）")
    workbench_serve_cmd.add_argument("--host", default="127.0.0.1")
    workbench_serve_cmd.add_argument("--port", type=int, default=8001)
    workbench_serve_cmd.add_argument("--runtime-dir", help="覆盖本机 services.json 中保存的数据目录")
    workbench_serve_cmd.add_argument('--erp-url', default='http://127.0.0.1:8000', help='客户项目的本机地址')
    workbench_serve_cmd.add_argument("--enable-code-execution", action="store_true", help="允许在网页确认课程方案后授权隔离代码执行")
    harness_serve_cmd = sub.add_parser("harness-serve", help="可选平台：完整 Harness（:8010，非大纲通过项）")
    harness_serve_cmd.add_argument("--host", default="127.0.0.1")
    harness_serve_cmd.add_argument("--port", type=int, default=8010)
    harness_serve_cmd.add_argument("--runtime-dir", default=".harness-runtime")
    harness_serve_cmd.add_argument("--repository-root")
    harness_serve_cmd.add_argument("--bootstrap", action="store_true",
                                   help="启动时自动注册当前仓库为 PROJECT-FLOWERP")
    harness_bootstrap_cmd = sub.add_parser("harness-bootstrap", help="可选平台：初始化 Harness（非大纲通过项）")
    harness_bootstrap_cmd.add_argument("--runtime-dir", default=".harness-runtime")
    harness_bootstrap_cmd.add_argument("--repository-root")
    spec_cmd = sub.add_parser("spec", help="工作台：解析并校验一份 Spec")
    spec_cmd.add_argument("path", nargs="?", default="FDE_SPEC.md")
    feedback_cmd = sub.add_parser("feedback", help="工作台：查看结构化反馈摘要")
    feedback_cmd.add_argument("--runtime-dir", default=".runtime")
    course_contract_cmd = sub.add_parser("course-contract", help="查看某讲的可执行课程合同")
    course_contract_cmd.add_argument("--lesson", type=int, choices=range(1, 17))
    course_spec_cmd = sub.add_parser("course-spec", help="生成只包含本讲增量的交付 Spec")
    course_spec_cmd.add_argument("--lesson", type=int, choices=range(1, 17), required=True)
    course_spec_cmd.add_argument("--output", help="默认写入 .runtime/course/LNN/FDE_SPEC.md")
    course_spec_cmd.add_argument("--eval-case", action="append", default=[], help="L15/L16 本次需求新增 Eval，可重复")
    course_status_cmd = sub.add_parser("course-status", help="检查 16 讲合同、Eval 映射和逐讲基线")
    course_status_cmd.add_argument("--require-baselines", action="store_true", help="缺少逐讲 Git 起始标签时返回失败")
    course_prepare_cmd = sub.add_parser("course-prepare", help="为本讲创建剥离增量后的隔离工作区")
    course_prepare_cmd.add_argument("--lesson", type=int, choices=range(1, 17), required=True)
    course_prepare_cmd.add_argument("--runtime-dir", default=".runtime")
    course_prepare_cmd.add_argument("--task-id", help="默认 TASK-PREP-LNN")
    course_prepare_cmd.add_argument("--source", choices=("baseline", "working-tree"), default="baseline",
                                    help="默认课程标签；working-tree 明确使用当前源代码的本地快照（不发布标签）")
    course_eval_cmd = sub.add_parser("course-eval", help="只运行某讲合同声明的阻断 Eval")
    course_eval_cmd.add_argument("--lesson", type=int, choices=range(1, 17), required=True)
    course_eval_cmd.add_argument("--no-report", action="store_true")
    course_eval_cmd.add_argument("--case", action="append", default=[], help="本次需求新增 Eval，可重复")
    course_submit_cmd = sub.add_parser("course-submit", help="按课次合同创建、执行并评测真实交付任务")
    course_submit_cmd.add_argument("--lesson", type=int, choices=range(4, 17), required=True)
    course_submit_cmd.add_argument("--runtime-dir", default=".runtime")
    course_submit_cmd.add_argument("--actor", default="course-learner")
    course_submit_mode = course_submit_cmd.add_mutually_exclusive_group(required=True)
    course_submit_mode.add_argument("--execute-code", action="store_true", help="授权 Codex 在本讲写集内修改代码")
    course_submit_mode.add_argument("--verify-only", action="store_true", help="只复验已有候选，不得作为本讲实现证据")
    course_submit_cmd.add_argument("--execution-timeout", type=int, default=900)
    course_submit_cmd.add_argument("--eval-case", action="append", default=[], help="L15/L16 本次需求新增 Eval，可重复")
    course_submit_cmd.add_argument("--session-baseline-ref", help="含本次红灯 Eval、但尚未实现需求的 Git ref/提交")
    course_submit_cmd.add_argument('--allowed-file', action='append', help='收窄本次写入范围到指定相对文件，可重复；不得超出课程合同')
    course_submit_cmd.add_argument("--bootstrap-task-id", help="L04 已由非执行者接受的 WB-L04-BOOTSTRAP 任务编号")
    course_submit_cmd.add_argument('--requirement-spec', type=Path, help='L04 使用 L03 确认的 Spec；L15/L16 使用本次六段式需求 Markdown 文件')
    baseline_audit_cmd = sub.add_parser("course-baseline-audit", help="审计某提交能否作为逐讲起始基线")
    baseline_audit_cmd.add_argument("--lesson", type=int, choices=range(1, 17), required=True)
    baseline_audit_cmd.add_argument("--candidate-ref", required=True)
    baseline_audit_cmd.add_argument("--evidence", required=True, help="具名人工复验记录文件")
    baseline_audit_cmd.add_argument("--runtime-dir", default=".runtime")
    baseline_publish_cmd = sub.add_parser("course-baseline-publish", help="审计通过后创建不可重写的课程标签")
    baseline_publish_cmd.add_argument("--lesson", type=int, choices=range(1, 17), required=True)
    baseline_publish_cmd.add_argument("--candidate-ref", required=True)
    baseline_publish_cmd.add_argument("--evidence", required=True)
    baseline_publish_cmd.add_argument("--runtime-dir", default=".runtime")
    baseline_publish_cmd.add_argument("--confirm", action="store_true", help="确认创建本地 annotated tag")
    candidate_export_cmd = sub.add_parser("course-candidate-export", help="导出具名审核通过的隔离候选 Patch")
    candidate_export_cmd.add_argument("task_id"); candidate_export_cmd.add_argument("--runtime-dir", default=".runtime")
    release_index_cmd = sub.add_parser('course-release-index', help='汇集发布证据引用，列出缺项；不代替人审')
    release_index_cmd.add_argument('task_id')
    release_index_cmd.add_argument('--runtime-dir', default='.runtime')
    release_index_cmd.add_argument('--cold-start-evidence')
    release_index_cmd.add_argument('--product-evidence')
    release_index_cmd.add_argument('--risks-file')
    candidate_promote_cmd = sub.add_parser("course-candidate-promote", help="校验并应用候选 Patch 到干净目标工作区")
    candidate_promote_cmd.add_argument("task_id"); candidate_promote_cmd.add_argument("--runtime-dir", default=".runtime")
    candidate_promote_cmd.add_argument("--target-workspace", required=True)
    candidate_cleanup_cmd = sub.add_parser("course-worktree-clean", help="安全清理已导出或失败任务的隔离 Worktree")
    candidate_cleanup_cmd.add_argument("task_id"); candidate_cleanup_cmd.add_argument("--runtime-dir", default=".runtime")
    init_cmd = sub.add_parser("init", help="初始化组织和管理员")
    init_cmd.add_argument("--runtime-dir", default=".runtime"); init_cmd.add_argument("--organization", default="FlowERP")
    init_cmd.add_argument("--username", default="admin")
    backup_cmd = sub.add_parser("backup", help="创建一致性数据库备份")
    backup_cmd.add_argument("--runtime-dir", default=".runtime"); backup_cmd.add_argument("--output-dir", default=".runtime/backups")
    backup_cmd.add_argument("--label", default="manual")
    verify_cmd = sub.add_parser("verify-backup", help="校验备份可恢复性")
    verify_cmd.add_argument("path"); verify_cmd.add_argument("--runtime-dir", default=".runtime")
    check_cmd = sub.add_parser("doctor", help="检查数据库、Schema 和运行目录")
    check_cmd.add_argument("--runtime-dir", default=".runtime")
    status_cmd = sub.add_parser("runtime-status", help="查看维护状态与实例租约")
    status_cmd.add_argument("--runtime-dir", default=".runtime")
    maintenance_cmd = sub.add_parser("maintenance", help="启用或关闭业务写入维护模式")
    maintenance_cmd.add_argument("mode", choices=("on", "off")); maintenance_cmd.add_argument("--runtime-dir", default=".runtime")
    maintenance_cmd.add_argument("--reason", default=""); maintenance_cmd.add_argument("--actor", default="cli-operator")
    task_create_cmd = sub.add_parser("task-create", help="创建可追溯的 ERP 交付任务")
    task_create_cmd.add_argument("--runtime-dir", default=".runtime"); task_create_cmd.add_argument("--request", required=True)
    task_create_cmd.add_argument("--requirement-id", default=""); task_create_cmd.add_argument("--business-ref", action="append", default=[])
    task_create_cmd.add_argument("--spec-path", default="FDE_SPEC.md"); task_create_cmd.add_argument("--actor", default="cli-operator")
    task_create_cmd.add_argument("--execute-code", action="store_true", help="授权 Codex 在明确范围内修改代码")
    task_create_cmd.add_argument("--write-scope", action="append", default=[], help="允许写入的工作区相对路径，可重复")
    task_create_cmd.add_argument("--execution-timeout", type=int, default=900)
    task_submit_cmd = sub.add_parser("task-submit", help="提交需求并自动生成 Spec、执行与评测")
    task_submit_cmd.add_argument("--runtime-dir", default=".runtime"); task_submit_cmd.add_argument("--request", required=True)
    task_submit_cmd.add_argument("--requirement-id", default=""); task_submit_cmd.add_argument("--business-ref", action="append", default=[])
    task_submit_cmd.add_argument("--actor", default="cli-operator"); task_submit_cmd.add_argument("--timeout", type=float, default=120.0)
    task_submit_cmd.add_argument("--execute-code", action="store_true", help="授权 Codex 在明确范围内修改代码")
    task_submit_cmd.add_argument("--write-scope", action="append", default=[], help="允许写入的工作区相对路径，可重复")
    task_submit_cmd.add_argument("--execution-timeout", type=int, default=900)
    task_run_cmd = sub.add_parser("task-run", help="准备 Spec、执行并评测，停在人工审核或返工")
    task_run_cmd.add_argument("task_id"); task_run_cmd.add_argument("--runtime-dir", default=".runtime"); task_run_cmd.add_argument("--actor", default="cli-operator")
    task_review_cmd = sub.add_parser("task-review", help="具名接受或打回交付任务")
    task_review_cmd.add_argument("task_id"); task_review_cmd.add_argument("--runtime-dir", default=".runtime")
    task_review_cmd.add_argument("--reviewer", required=True); task_review_cmd.add_argument("--decision", choices=("approve", "reject"), required=True)
    task_review_cmd.add_argument("--note", required=True)
    task_show_cmd = sub.add_parser("task-show", help="查看任务、业务引用、证据和事件链")
    task_show_cmd.add_argument("task_id"); task_show_cmd.add_argument("--runtime-dir", default=".runtime")
    task_list_cmd = sub.add_parser("task-list", help="列出交付任务")
    task_list_cmd.add_argument("--runtime-dir", default=".runtime"); task_list_cmd.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if args.command in {"serve", "serve-workbench"}:
        from .runtime_paths import service_runtime
        try:
            args.runtime_dir = str(service_runtime(
                "flowerp" if args.command == "serve" else "workbench", args.runtime_dir))
        except ValueError as error:
            parser.error(str(error))
    if args.command in {"workbench-init", "workbench-project-add", "workbench-task-create", "workbench-evidence-add", "workbench-status"}:
        return run_bootstrap_command(args)
    if args.command == "feedback":
        from .feedback import summary as feedback_summary

        print(json.dumps(feedback_summary(str(Path(args.runtime_dir) / "workbench.db")), ensure_ascii=False, indent=2))
        return 0
    if args.command == "course-contract":
        result = lesson_contract(args.lesson).as_dict() if args.lesson else {
            "schema_version": "1.0", "lessons": [item.as_dict() for item in LESSONS],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.command == "course-prepare":
        isolation = CourseWorktreeManager(Path.cwd(), Path(args.runtime_dir)).prepare_for_lesson(
            args.lesson, task_id=args.task_id, source=args.source,
        )
        print(json.dumps(isolation, ensure_ascii=False, indent=2))
        return 0
    if args.command == "course-spec":
        output = args.output or f".runtime/course/L{args.lesson:02d}/FDE_SPEC.md"
        lesson = lesson_contract(args.lesson)
        dynamic_cases = tuple(dict.fromkeys(args.eval_case))
        if lesson.dynamic_eval_required and not dynamic_cases:
            parser.error(f"L{args.lesson:02d} 必须用 --eval-case 声明本次需求新增的 Eval")
        target = write_lesson_spec(args.lesson, output, dynamic_cases)
        print(json.dumps({"lesson": args.lesson, "spec_path": str(target)}, ensure_ascii=False, indent=2)); return 0
    if args.command == "course-status":
        result = validate_mainline(Path.cwd())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if (not result["contract_valid"] or (args.require_baselines and not result["course_ready"])) else 0
    if args.command == "course-eval":
        lesson = lesson_contract(args.lesson)
        dynamic_cases = tuple(dict.fromkeys(args.case))
        if lesson.dynamic_eval_required and not dynamic_cases:
            parser.error(f"L{args.lesson:02d} 必须用 --case 运行本次需求新增的 Eval")
        selected_cases = lesson.eval_cases + dynamic_cases
        if not selected_cases:
            parser.error(f"L{args.lesson:02d} 没有可复用的代码 Eval，请执行课程合同中的验收活动")
        from eval.harness import run_suite
        result = run_suite("blocking", not args.no_report, selected_cases)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["summary"]["blocking_failed"] else 0
    if args.command == "course-submit":
        from .course_delivery import submit_course
        try:
            output = submit_course(
                repository_root=Path.cwd(), runtime_dir=args.runtime_dir,
                lesson_number=args.lesson, actor=args.actor, execute_code=args.execute_code,
                execution_timeout=args.execution_timeout, eval_cases=tuple(args.eval_case),
                session_baseline_ref=args.session_baseline_ref,
                write_scope=tuple(args.allowed_file) if args.allowed_file is not None else None,
                bootstrap_task_id=args.bootstrap_task_id,
                requirement_spec_text=args.requirement_spec.read_text(encoding='utf-8') if args.requirement_spec else None,
            )
        except (ValueError, OSError) as error:
            parser.error(str(error))
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if output["task"].get("status") == "review" else 1
    if args.command == 'course-release-index':
        from .release_index import create_release_index
        try:
            output = create_release_index(args.runtime_dir, args.task_id, cold_start=args.cold_start_evidence,
                                          product=args.product_evidence, risks=args.risks_file)
        except (ValueError, OSError, KeyError) as error:
            parser.error(str(error))
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"course-baseline-audit", "course-baseline-publish"}:
        publisher = CourseBaselinePublisher(Path.cwd(), args.runtime_dir)
        if args.command == "course-baseline-publish":
            if not args.confirm:
                parser.error("创建课程标签必须显式传入 --confirm")
            result = publisher.publish(args.lesson, args.candidate_ref, args.evidence)
        else:
            result = publisher.audit(args.lesson, args.candidate_ref, args.evidence)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("accepted") else 1
    if args.command in {"course-candidate-export", "course-candidate-promote", "course-worktree-clean"}:
        runtime = Path(args.runtime_dir)
        artifacts = CourseCandidateArtifacts(Path.cwd(), runtime)
        if args.command == "course-candidate-export":
            result = artifacts.export(TaskStore(runtime / "workbench.db"), args.task_id)
        elif args.command == "course-candidate-promote":
            result = artifacts.promote(args.task_id, args.target_workspace)
        else:
            result = artifacts.cleanup(TaskStore(runtime / "workbench.db"), args.task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.command == "serve":
        try:
            serve(args.host, args.port, args.runtime_dir)
        except ServerBindError as error:
            return report_bind_error(error)
        return 0
    if args.command == "serve-workbench":
        from .workbench_server import serve as serve_workbench
        from .runtime_lease import WorkbenchRuntimeInUse

        try:
            serve_workbench(args.host, args.port, args.runtime_dir, enable_code_execution=args.enable_code_execution,
                            erp_url=args.erp_url)
        except ServerBindError as error:
            return report_bind_error(error)
        except WorkbenchRuntimeInUse as error:
            print(str(error))
            return 2
        return 0
    if args.command == "harness-serve":
        try:
            serve_harness(args.host, args.port, args.runtime_dir, args.repository_root, args.bootstrap)
        except ServerBindError as error:
            return report_bind_error(error)
        return 0
    if args.command == "harness-bootstrap":
        print(json.dumps(bootstrap_platform(args.runtime_dir, args.repository_root), ensure_ascii=False, indent=2))
        return 0
    if args.command in {"mock-data", "verify-mock-data"}:
        runtime = Path(args.runtime_dir); store = ERPStore(runtime / "flowerp.db")
        result = load_mock_data(store) if args.command == "mock-data" else verify_mock_data(store)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        complete = result["verification"]["complete"] if args.command == "mock-data" else result["complete"]
        return 0 if complete else 1
    if args.command == "init":
        runtime = Path(args.runtime_dir); store = ERPStore(runtime / "flowerp.db")
        identity = IdentityService(store); identity.ensure_local_defaults()
        first = getpass.getpass("管理员密码（至少 10 位）: "); second = getpass.getpass("再次输入密码: ")
        if first != second: parser.error("两次输入的密码不一致")
        print(json.dumps(identity.bootstrap(args.organization, args.username, first), ensure_ascii=False, indent=2)); return 0
    if args.command == "backup":
        runtime = Path(args.runtime_dir); service = BackupService(ERPStore(runtime / "flowerp.db"), args.output_dir)
        print(json.dumps(service.create(args.label), ensure_ascii=False, indent=2)); return 0
    if args.command == "verify-backup":
        runtime = Path(args.runtime_dir); service = BackupService(ERPStore(runtime / "flowerp.db"), Path(args.path).parent)
        result = service.verify(args.path); print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["ok"] else 1
    if args.command == "doctor":
        settings = load_settings(args.runtime_dir); runtime = settings.runtime_dir
        ok, result = HealthService(ERPStore(runtime / "flowerp.db", settings.database_busy_timeout_ms), runtime,
                                           settings.minimum_free_disk_mb, settings.backup_max_age_hours,
                                           settings.require_recent_backup).ready()
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if ok else 1
    if args.command == "runtime-status":
        runtime = Path(args.runtime_dir); store = ERPStore(runtime / "flowerp.db")
        print(json.dumps({"runtime": RuntimeCoordinator(store).status(), "leases": store.rows(
            "SELECT lease_name,owner_id,fencing_token,heartbeat_at,expires_at,expires_at>CURRENT_TIMESTAMP AS active "
            "FROM instance_leases ORDER BY lease_name")}, ensure_ascii=False, indent=2)); return 0
    if args.command == "maintenance":
        runtime = Path(args.runtime_dir); result = RuntimeCoordinator(ERPStore(runtime / "flowerp.db")).set_maintenance(
            args.mode == "on", args.reason, args.actor,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.command.startswith("task-"):
        task_store = TaskStore(Path(args.runtime_dir) / "workbench.db")
        if args.command == "task-create":
            result = task_store.create(
                args.request, args.requirement_id, args.business_ref, args.spec_path, args.actor,
                execution_mode="codex" if args.execute_code else "verify",
                write_scope=args.write_scope,
                execution_timeout_seconds=args.execution_timeout,
            )
        elif args.command == "task-submit":
            executor = CodexExecutionRunner(Path.cwd(), args.runtime_dir)
            automation = DeliveryAutomation(task_store, args.runtime_dir, execution_runner=executor)
            submitted = automation.submit(
                args.request, args.requirement_id, args.business_ref, args.actor,
                "codex" if args.execute_code else "verify", args.write_scope, args.execution_timeout,
            )
            result = automation.wait(submitted["id"], args.timeout)
        elif args.command == "task-run":
            result = run_task(
                task_store, args.task_id, args.actor,
                execution_runner=CodexExecutionRunner(Path.cwd(), args.runtime_dir),
            )
        elif args.command == "task-review":
            result = task_store.review(args.task_id, args.reviewer, args.decision, args.note)
        elif args.command == "task-show":
            result = task_store.get(args.task_id)
        else:
            result = {"items": task_store.list(args.limit)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") not in {"failed", "rework", "dead_letter"} else 1
    if args.command == "spec":
        try:
            result = load_spec(args.path).as_dict()
        except (ValueError, OSError) as exc:
            print(f"Spec 校验失败：{exc}", file=sys.stderr)
            return 1
    else:
        result = demo(args.runtime_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
