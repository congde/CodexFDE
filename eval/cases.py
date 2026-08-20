from __future__ import annotations

import tempfile
from pathlib import Path

from flowerp import ERPService, ERPStore, InventoryService, LedgerService, MasterDataService, SalesService
from flowerp.finance import FinanceService
from flowerp.cash_management import CashManagementService
from flowerp.channels import EcommerceChannelService
from flowerp.identity import Principal, SYSTEM_PRINCIPAL
from flowerp.models import ApprovalRequired, Conflict, InsufficientStock, InvalidTransition, OrderLine
from flowerp.operations import BackupService
from flowerp.purchasing import PurchasingService
from workbench.feedback import add_feedback, review_feedback, summary as feedback_summary
from workbench.task_store import TaskStore


def _service() -> tuple[tempfile.TemporaryDirectory, ERPService]:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-")
    service = ERPService(ERPStore(Path(tmp.name) / "eval.db"))
    service.add_product("SKU-A", "验收商品", 1000, 2)
    return tmp, service


def stock_never_negative() -> str:
    tmp, service = _service()
    try:
        service.receive_stock("SKU-A", 5, "open")
        order = service.create_order("客户", [OrderLine("SKU-A", 6, 1000)], "SO-EVAL-NEG")
        try: service.reserve_order(order["id"])
        except InsufficientStock: pass
        else: raise AssertionError("库存不足仍然预占成功")
        stock = service.product("SKU-A")
        assert stock["available"] == 5 and stock["reserved"] == 0
        return "缺货请求被拒绝，库存未发生部分写入"
    finally: tmp.cleanup()


def receiving_is_idempotent() -> str:
    tmp, service = _service()
    try:
        first = service.receive_stock("SKU-A", 5, "receipt:001")
        second = service.receive_stock("SKU-A", 5, "receipt:001")
        assert first["on_hand"] == 5 and second["on_hand"] == 5 and second["idempotent_replay"]
        return "重复入库键只生效一次"
    finally: tmp.cleanup()


def cancellation_releases_reservation() -> str:
    tmp, service = _service()
    try:
        service.receive_stock("SKU-A", 5, "open")
        order = service.create_order("客户", [OrderLine("SKU-A", 3, 1000)], "SO-EVAL-CANCEL")
        service.reserve_order(order["id"]); service.cancel_order(order["id"])
        stock = service.product("SKU-A")
        assert stock["available"] == 5 and stock["reserved"] == 0
        return "取消 reserved 订单后完整释放预占"
    finally: tmp.cleanup()


def illegal_transition_is_blocked() -> str:
    tmp, service = _service()
    try:
        order = service.create_order("客户", [OrderLine("SKU-A", 1, 1000)], "SO-EVAL-STATE")
        try: service.ship_order(order["id"])
        except InvalidTransition: return "draft 订单不能跳过预占直接发货"
        raise AssertionError("非法状态迁移未被阻断")
    finally: tmp.cleanup()


def purchase_requires_approval() -> str:
    tmp, service = _service()
    try:
        purchase = service.propose_purchase("SKU-A", 7, "低于补货点", "PR-EVAL-APPROVAL")
        try: service.receive_purchase(purchase["id"], "receipt:pr")
        except ApprovalRequired: pass
        else: raise AssertionError("未审批采购被入库")
        service.approve_purchase(purchase["id"], "reviewer")
        service.receive_purchase(purchase["id"], "receipt:pr")
        assert service.product("SKU-A")["on_hand"] == 7
        return "未审批被阻断，审批后可入库"
    finally: tmp.cleanup()


def order_total_matches_lines() -> str:
    tmp, service = _service()
    try:
        order = service.create_order("客户", [OrderLine("SKU-A", 3, 1250)], "SO-EVAL-TOTAL")
        assert order["total_cents"] == sum(line["line_total_cents"] for line in order["lines"]) == 3750
        return "订单总额与明细计算一致"
    finally: tmp.cleanup()


def ecommerce_channel_order_is_idempotent_and_guarded() -> str:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-channel-")
    try:
        store = ERPStore(Path(tmp.name) / "eval.db")
        from flowerp.identity import IdentityService
        IdentityService(store).ensure_local_defaults()
        master = MasterDataService(store); inventory = InventoryService(store); channels = EcommerceChannelService(store)
        product = master.create_product(SYSTEM_PRINCIPAL, "EVAL-EC", "渠道验收商品", 12900, 6000)
        customer = master.create_customer(SYSTEM_PRINCIPAL, "EVAL-PLATFORM", "平台结算客户", credit_limit_cents=100000)
        shop = channels.create_shop(SYSTEM_PRINCIPAL, "mock", "EVAL-SHOP", "验收沙箱店", customer["id"], "SITE-MAIN", "eval-shop")
        channels.map_listing(SYSTEM_PRINCIPAL, shop["id"], "item-1", "sku-1", "验收商品",
                             [{"product_id": product["id"], "quantity": 1, "revenue_share_basis_points": 10000}])
        payload = {"external_order_id": "EC-ORDER-1", "external_status": "paid", "order_time": "2026-08-14T10:00:00+08:00",
                   "currency": "CNY", "goods_cents": 12900, "total_cents": 12900, "recipient": "验收员",
                   "phone": "13800138000", "province": "上海市", "city": "上海市", "street": "验证路 1 号",
                   "lines": [{"external_line_id": "1", "external_product_id": "item-1", "external_sku_id": "sku-1",
                              "quantity": 1, "unit_price_cents": 12900, "total_cents": 12900}]}
        first = channels.ingest_orders(SYSTEM_PRINCIPAL, shop["id"], [payload])
        replay = channels.ingest_orders(SYSTEM_PRINCIPAL, shop["id"], [payload])
        assert first["items"][0]["id"] == replay["items"][0]["id"] and replay["replay_count"] == 1
        inventory.receive(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK", 1, "eval-channel-stock", unit_cost_cents=6000)
        imported = channels.review_and_import(SYSTEM_PRINCIPAL, first["items"][0]["id"])
        assert imported["status"] == "imported"
        shortage = dict(payload); shortage["external_order_id"] = "EC-ORDER-2"; shortage["lines"] = [dict(payload["lines"][0], external_line_id="2", quantity=2, total_cents=25800)]; shortage["goods_cents"] = 25800; shortage["total_cents"] = 25800
        second = channels.ingest_orders(SYSTEM_PRINCIPAL, shop["id"], [shortage])["items"][0]
        try: channels.review_and_import(SYSTEM_PRINCIPAL, second["id"])
        except Conflict: pass
        else: raise AssertionError("渠道订单缺货仍完成预占")
        balance = inventory.balance(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK")
        assert balance["reserved"] == 1 and balance["available"] == 0
        return "渠道订单重放幂等，审单生成销售单并原子预占，缺货订单保留异常且未超卖"
    finally:
        tmp.cleanup()


def no_committed_secrets() -> str:
    root = Path(__file__).resolve().parent.parent
    suspicious: list[str] = []
    markers = ("sk-proj-", "-----BEGIN PRIVATE KEY-----", "AKIA")
    ignored = {".git", ".tmp", ".runtime", ".cache"}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts): continue
        if path.suffix.lower() not in {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".html", ".txt"}: continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(marker in text for marker in markers) and path.name != "cases.py": suspicious.append(str(path.relative_to(root)))
    assert not suspicious, f"疑似密钥文件：{suspicious}"
    return "文本源文件未发现常见密钥特征"


def course_assets_present() -> str:
    root = Path(__file__).resolve().parent.parent
    required = ["AGENTS.md", "FDE_SPEC.md", "course/tasks", "deploy/Dockerfile", "web/index.html"]
    missing = [item for item in required if not (root / item).exists()]
    assert not missing, f"课程资产待补齐：{missing}"
    return "关键课程资产齐备"


def production_schema_invariants() -> str:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-v2-")
    try:
        store = ERPStore(Path(tmp.name) / "eval.db")
        assert store.scalar("SELECT MAX(version) FROM schema_migrations") >= 16
        assert store.scalar("SELECT COUNT(*) FROM permissions") >= 20
        assert store.integrity_check()["ok"]
        return "生产 Schema、权限字典、外键与数据库完整性检查通过"
    finally:
        tmp.cleanup()


def multi_location_transfer_conserves_stock() -> str:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-transfer-")
    try:
        store = ERPStore(Path(tmp.name) / "eval.db")
        from flowerp.identity import IdentityService
        IdentityService(store).ensure_local_defaults()
        master = MasterDataService(store); inventory = InventoryService(store)
        product = master.create_product(SYSTEM_PRINCIPAL, "EVAL-X", "调拨验收商品", 1000, 500)
        target = master.create_location(SYSTEM_PRINCIPAL, "SITE-MAIN", "EVAL-BIN", "验收库位")
        inventory.receive(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK", 10, "eval-opening")
        inventory.transfer(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK", target["id"], 4, "eval-transfer")
        balances = inventory.list_balances(SYSTEM_PRINCIPAL, product_id=product["id"])
        assert sum(row["on_hand"] for row in balances) == 10
        assert sorted(row["on_hand"] for row in balances) == [4, 6]
        return "跨库位调拨前后库存总量守恒，来源与目标账一致"
    finally:
        tmp.cleanup()


def stale_stock_count_is_blocked() -> str:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-count-")
    try:
        store = ERPStore(Path(tmp.name) / "eval.db")
        from flowerp.identity import IdentityService
        IdentityService(store).ensure_local_defaults()
        master = MasterDataService(store); inventory = InventoryService(store)
        product = master.create_product(SYSTEM_PRINCIPAL, "EVAL-C", "盘点验收商品", 1000, 500)
        inventory.receive(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK", 5, "count-opening")
        count = inventory.create_count(SYSTEM_PRINCIPAL, "LOC-MAIN-STOCK", "2026-08-13",
                                       [{"product_id": product["id"], "counted_quantity": 4}], "验收")
        inventory.receive(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK", 1, "count-change")
        try:
            inventory.post_count(SYSTEM_PRINCIPAL, count["id"])
        except Conflict:
            return "盘点快照后发生库存变化，过账被可靠阻断"
        raise AssertionError("过期盘点快照仍然成功过账")
    finally:
        tmp.cleanup()


def sales_credit_and_atomic_reservation() -> str:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-sales-")
    try:
        store = ERPStore(Path(tmp.name) / "eval.db")
        from flowerp.identity import IdentityService
        IdentityService(store).ensure_local_defaults()
        master = MasterDataService(store); inventory = InventoryService(store); sales = SalesService(store)
        product = master.create_product(SYSTEM_PRINCIPAL, "EVAL-S", "销售验收商品", 1000, 500)
        customer = master.create_customer(SYSTEM_PRINCIPAL, "EVAL-CUS", "验收客户", credit_limit_cents=100000)
        inventory.receive(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK", 2, "sales-opening")
        order = sales.create_order(SYSTEM_PRINCIPAL, customer["id"], [{"product_id": product["id"], "quantity": 3}])
        sales.confirm(SYSTEM_PRINCIPAL, order["id"])
        try:
            sales.reserve(SYSTEM_PRINCIPAL, order["id"])
        except InsufficientStock:
            balance = inventory.balance(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK")
            assert balance["reserved"] == 0 and balance["available"] == 2
            return "正式销售订单缺货时整单回滚，没有部分预占"
        raise AssertionError("正式销售订单超卖未被阻断")
    finally:
        tmp.cleanup()


def backup_is_restorable() -> str:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-backup-")
    try:
        root = Path(tmp.name); store = ERPStore(root / "eval.db")
        backup = BackupService(store, root / "backups")
        created = backup.create("eval")
        verification = backup.verify(created["backup"])
        assert verification["ok"] and verification["integrity"]["schema_version"] >= 2
        return "在线备份校验和、解压与 SQLite 完整性复验全部通过"
    finally:
        tmp.cleanup()


def purchase_invoice_three_way_match() -> str:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-3way-")
    try:
        store = ERPStore(Path(tmp.name) / "eval.db")
        from flowerp.identity import IdentityService
        IdentityService(store).ensure_local_defaults()
        buyer = Principal("eval-buyer", "ORG-DEFAULT", "buyer", "制单人", SYSTEM_PRINCIPAL.permissions)
        approver = Principal("eval-approver", "ORG-DEFAULT", "approver", "审批人", SYSTEM_PRINCIPAL.permissions)
        master = MasterDataService(store); purchasing = PurchasingService(store); finance = FinanceService(store)
        product = master.create_product(SYSTEM_PRINCIPAL, "EVAL-3W", "三单匹配商品", 10000, 6000)
        supplier = master.create_supplier(SYSTEM_PRINCIPAL, "EVAL-SUP", "验收供应商")
        purchase = purchasing.create_order(buyer, supplier["id"], "SITE-MAIN",
                                           [{"product_id": product["id"], "quantity": 3, "unit_price_cents": 6000}])
        purchasing.submit(buyer, purchase["id"]); purchasing.approve(approver, purchase["id"])
        line_id = purchasing.order(SYSTEM_PRINCIPAL, purchase["id"])["lines"][0]["id"]
        receipt = purchasing.create_receipt(SYSTEM_PRINCIPAL, purchase["id"], "LOC-MAIN-STOCK",
                                             [{"purchase_line_id": line_id, "accepted_quantity": 2}])
        purchasing.post_receipt(SYSTEM_PRINCIPAL, receipt["id"], "eval-3way")
        try:
            finance.create_invoice_from_purchase(
                SYSTEM_PRINCIPAL, purchase["id"], supplier_invoice_number="BAD-001",
                supplier_lines=[{"source_line_id": line_id, "quantity": 3, "unit_price_cents": 6000}],
            )
        except Conflict:
            pass
        else:
            raise AssertionError("供应商发票超过已收数量仍通过匹配")
        invoice = finance.create_invoice_from_purchase(
            SYSTEM_PRINCIPAL, purchase["id"], supplier_invoice_number="OK-001",
            supplier_lines=[{"source_line_id": line_id, "quantity": 2, "unit_price_cents": 6000}],
        )
        assert invoice["match_status"] == "matched" and invoice["lines"][0]["quantity"] == 2
        return "采购订单、已过账收货与供应商发票的数量和单价匹配通过，超收发票被阻断"
    finally:
        tmp.cleanup()


def double_entry_fifo_and_subledger_reconciliation() -> str:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-ledger-")
    try:
        store = ERPStore(Path(tmp.name) / "eval.db")
        from flowerp.identity import IdentityService
        IdentityService(store).ensure_local_defaults()
        master = MasterDataService(store); inventory = InventoryService(store)
        sales = SalesService(store); finance = FinanceService(store); ledger = LedgerService(store)
        product = master.create_product(SYSTEM_PRINCIPAL, "EVAL-GL", "总账验收商品", 12000, 5000)
        customer = master.create_customer(SYSTEM_PRINCIPAL, "EVAL-GL-C", "总账验收客户", credit_limit_cents=200000)
        inventory.receive(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK", 2, "eval-gl-a", unit_cost_cents=5000)
        inventory.receive(SYSTEM_PRINCIPAL, product["id"], "LOC-MAIN-STOCK", 2, "eval-gl-b", unit_cost_cents=7000)
        order = sales.create_order(SYSTEM_PRINCIPAL, customer["id"], [{"product_id": product["id"], "quantity": 3}])
        sales.confirm(SYSTEM_PRINCIPAL, order["id"]); sales.reserve(SYSTEM_PRINCIPAL, order["id"])
        shipment = sales.create_shipment(SYSTEM_PRINCIPAL, order["id"])
        sales.post_shipment(SYSTEM_PRINCIPAL, shipment["id"], "eval-gl-ship")
        move = store.row("SELECT total_cost_cents FROM stock_moves WHERE reference_id=?", (shipment["id"],))
        assert move and move["total_cost_cents"] == 17000, move
        invoice = finance.create_invoice_from_sales(SYSTEM_PRINCIPAL, order["id"])
        finance.record_payment(
            SYSTEM_PRINCIPAL, "receipt", "customer", customer["id"], invoice["total_cents"],
            external_reference="EVAL-GL-PAY",
            allocations=[{"invoice_id": invoice["id"], "amount_cents": invoice["total_cents"]}],
        )
        trial = ledger.trial_balance(SYSTEM_PRINCIPAL)
        reconciliation = ledger.reconcile_subledgers(SYSTEM_PRINCIPAL)
        assert trial["balanced"] and trial["debit_cents"] == trial["credit_cents"], trial
        assert reconciliation["ok"], reconciliation
        assert store.scalar("SELECT COUNT(*) FROM stock_moves WHERE valuation_status='pending'") == 0
        return "FIFO 销售成本、复式凭证、收款核销及库存/应收子账总账自动对账全部通过"
    finally:
        tmp.cleanup()


def bank_statement_control_and_reconciliation() -> str:
    tmp = tempfile.TemporaryDirectory(prefix="flowerp-eval-bank-")
    try:
        store = ERPStore(Path(tmp.name) / "eval.db")
        from flowerp.identity import IdentityService
        IdentityService(store).ensure_local_defaults()
        master = MasterDataService(store); finance = FinanceService(store); cash = CashManagementService(store)
        customer = master.create_customer(SYSTEM_PRINCIPAL, "EVAL-BANK-C", "银行对账验收客户")
        account = cash.create_account(SYSTEM_PRINCIPAL, "EVAL-BANK", "验收基本户", "验收银行", "1002")
        payment = finance.record_payment(
            SYSTEM_PRINCIPAL, "receipt", "customer", customer["id"], 25_800, "2026-08-14",
            external_reference="EVAL-BANK-TX", bank_account_id=account["id"],
        )
        try:
            cash.import_statement(
                SYSTEM_PRINCIPAL, account["id"], "BAD-BALANCE", "2026-08-01", "2026-08-31", 0, 1,
                [{"external_transaction_id": "BAD", "transaction_date": "2026-08-14",
                  "signed_amount_cents": 25_800}],
            )
        except Exception:
            pass
        else:
            raise AssertionError("不平银行对账单仍被导入")
        statement = cash.import_statement(
            SYSTEM_PRINCIPAL, account["id"], "EVAL-202608", "2026-08-01", "2026-08-31", 0, 25_800,
            [{"external_transaction_id": "EVAL-LINE-1", "transaction_date": "2026-08-14",
              "signed_amount_cents": 25_800, "reference": "EVAL-BANK-TX"}],
        )
        matched = cash.auto_match(SYSTEM_PRINCIPAL, statement["id"])
        assert matched["unmatched_count"] == 0 and matched["lines"][0]["payment_id"] == payment["id"]
        reconciled = cash.reconcile(SYSTEM_PRINCIPAL, statement["id"])
        assert reconciled["status"] == "reconciled"
        return "不平对账单被阻断，银行流水自动匹配收付款后与银行存款总账余额一致"
    finally:
        tmp.cleanup()


def delivery_evidence_and_review_controls() -> str:
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
        assert feedback_summary(str(path))["pending_review"] == 0
        return "需求自动生成任务级 Spec 并推进至审核；完成需要具名审核；反馈默认待审且审核后不可重复改写"
    finally:
        tmp.cleanup()
