from __future__ import annotations

import argparse
import getpass
import json
import tempfile
from pathlib import Path

from flowerp import EcommerceDemo, ERPService, ERPStore
from flowerp.config import load_settings
from flowerp.identity import IdentityService
from flowerp.mock_data import load_mock_data, verify_mock_data
from flowerp.operations import BackupService, HealthService, RuntimeCoordinator
from .automation import DeliveryAutomation
from .execution import CodexExecutionRunner
from .feedback import add_feedback
from .server import serve
from .spec import load_spec
from .task_store import TaskStore
from .workflow import run_task


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
    parser = argparse.ArgumentParser(description="FlowERP delivery workbench")
    sub = parser.add_subparsers(dest="command", required=True)
    demo_cmd = sub.add_parser("demo"); demo_cmd.add_argument("--runtime-dir")
    mock_cmd = sub.add_parser("mock-data", help="生成幂等的完整 ERP 验收账套")
    mock_cmd.add_argument("--runtime-dir", default=".runtime")
    verify_mock_cmd = sub.add_parser("verify-mock-data", help="验证完整 ERP 验收账套")
    verify_mock_cmd.add_argument("--runtime-dir", default=".runtime")
    serve_cmd = sub.add_parser("serve"); serve_cmd.add_argument("--host", default="127.0.0.1"); serve_cmd.add_argument("--port", type=int, default=8000); serve_cmd.add_argument("--runtime-dir", default=".runtime")
    spec_cmd = sub.add_parser("spec"); spec_cmd.add_argument("path", nargs="?", default="FDE_SPEC.md")
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
    if args.command == "serve": serve(args.host, args.port, args.runtime_dir); return 0
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
        return 0 if result.get("status") not in {"failed", "rework"} else 1
    result = demo(args.runtime_dir) if args.command == "demo" else load_spec(args.path).as_dict()
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
