from __future__ import annotations

import json
import hashlib
from datetime import date, timedelta
from typing import Callable

from .alerts import AlertService
from .finance import FinanceService
from .cash_management import CashManagementService
from .channels import EcommerceChannelService
from .identity import IdentityService, Principal, SYSTEM_PRINCIPAL
from .inventory import InventoryService
from .master_data import MasterDataService
from .partners import PartnerDetailService
from .pricing import PricingService
from .purchasing import PurchasingService
from .reconciliation import ReconciliationService
from .reports import ReportService
from .sales import SalesService
from .serials import SerialNumberService
from .store import ERPStore


FIXTURE_KEY = "demo.mock_data"
FIXTURE_VERSION = 5
LOCATION = "LOC-MAIN-STOCK"
SITE = "SITE-MAIN"
MOCK_BUYER = Principal("mock-buyer", "ORG-DEFAULT", "mock-buyer", "演示采购员", SYSTEM_PRINCIPAL.permissions)
MOCK_APPROVER = Principal("mock-approver", "ORG-DEFAULT", "mock-approver", "演示审批人", SYSTEM_PRINCIPAL.permissions)
MOCK_SALES = Principal("mock-sales", "ORG-DEFAULT", "mock-sales", "演示销售员", SYSTEM_PRINCIPAL.permissions)


def _existing(store: ERPStore, table: str, column: str, value: str) -> dict | None:
    return store.row(
        f"SELECT * FROM {table} WHERE organization_id=? AND {column}=?",
        (SYSTEM_PRINCIPAL.organization_id, value),
    )


def _digits(value: str, length: int = 10) -> str:
    number = int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big")
    return f"{number % (10 ** length):0{length}d}"


def _get_or_create(find: Callable[[], dict | None], create: Callable[[], dict]) -> dict:
    return find() or create()


def _ensure_master_data(store: ERPStore) -> dict:
    principal = SYSTEM_PRINCIPAL
    identity = IdentityService(store)
    identity.ensure_local_defaults()
    master = MasterDataService(store)
    details = PartnerDetailService(store)

    category_specs = (
        ("MOCK-COMPUTING", "计算与办公"),
        ("MOCK-ACCESSORY", "数码配件"),
        ("MOCK-OFFICE", "办公环境"),
    )
    categories: dict[str, dict] = {}
    for code, name in category_specs:
        categories[code] = _get_or_create(
            lambda code=code: store.row(
                "SELECT * FROM product_categories WHERE organization_id=? AND code=?",
                (principal.organization_id, code),
            ),
            lambda code=code, name=name: master.create_category(principal, code, name),
        )

    product_specs = (
        ("MOCK-LAPTOP-PRO", "星云 Pro 商务笔记本", 899_900, 620_000, "MOCK-COMPUTING", "none", 8, 40, 0),
        ("MOCK-MONITOR-27", "27 英寸专业显示器", 239_900, 155_000, "MOCK-COMPUTING", "none", 6, 30, 0),
        ("MOCK-DOCK-12", "十二合一扩展坞", 69_900, 39_000, "MOCK-ACCESSORY", "none", 10, 60, 0),
        ("MOCK-HEADSET", "会议降噪耳机", 89_900, 51_000, "MOCK-ACCESSORY", "none", 8, 35, 0),
        ("MOCK-CABLE-C", "全功能 USB-C 线缆", 12_900, 5_200, "MOCK-ACCESSORY", "none", 10, 50, 0),
        ("MOCK-COFFEE", "精品挂耳咖啡 20 包", 8_900, 4_600, "MOCK-OFFICE", "lot", 5, 30, 365),
        ("MOCK-TABLET", "移动巡检平板", 329_900, 238_000, "MOCK-COMPUTING", "serial", 2, 12, 0),
        ("MOCK-CHAIR", "人体工学办公椅", 159_900, 92_000, "MOCK-OFFICE", "none", 4, 20, 0),
    )
    products: dict[str, dict] = {}
    for sku, name, price, cost, category, tracking, minimum, maximum, shelf_life in product_specs:
        products[sku] = _get_or_create(
            lambda sku=sku: _existing(store, "product_master", "sku", sku),
            lambda sku=sku, name=name, price=price, cost=cost, category=category, tracking=tracking,
                   minimum=minimum, maximum=maximum, shelf_life=shelf_life: master.create_product(
                principal, sku, name, price, cost,
                barcode=f"697{_digits(sku)}",
                category_id=categories[category]["id"], tracking=tracking,
                min_stock=minimum, max_stock=maximum, shelf_life_days=shelf_life,
                description="FlowERP 完整业务验收演示商品",
            ),
        )

    customer_specs = (
        ("MOCK-C001", "上海云帆数字科技有限公司", "陈晓", "13800010001", "finance@yunfan.example", 5_000_000),
        ("MOCK-C002", "杭州青禾空间设计有限公司", "林悦", "13800010002", "buyer@qinghe.example", 3_000_000),
        ("MOCK-C003", "苏州远程协作中心", "赵明", "13800010003", "ops@suzhou-remote.example", 2_000_000),
        ("MOCK-C004", "深圳极光电子商务有限公司", "周琪", "13800010004", "ap@aurora.example", 8_000_000),
    )
    customers: dict[str, dict] = {}
    for code, name, contact, phone, email, credit in customer_specs:
        customers[code] = _get_or_create(
            lambda code=code: _existing(store, "customer_master", "code", code),
            lambda code=code, name=name, contact=contact, phone=phone, email=email, credit=credit: master.create_customer(
                principal, code, name, contact_name=contact, phone=phone, email=email,
                tax_number=f"91310000{_digits(code, 8)}",
                billing_address="上海市浦东新区数据路 88 号",
                shipping_address="上海市闵行区履约路 16 号",
                payment_terms_days=30, credit_limit_cents=credit,
            ),
        )

    supplier_specs = (
        ("MOCK-S001", "华东智造供应链有限公司", "王经理", "13900020001", "sales@east-mfg.example", 7),
        ("MOCK-S002", "南方数码配件有限公司", "李经理", "13900020002", "service@south-digital.example", 5),
        ("MOCK-S003", "优享办公产业有限公司", "许经理", "13900020003", "order@office-plus.example", 12),
    )
    suppliers: dict[str, dict] = {}
    for code, name, contact, phone, email, lead_time in supplier_specs:
        suppliers[code] = _get_or_create(
            lambda code=code: _existing(store, "supplier_master", "code", code),
            lambda code=code, name=name, contact=contact, phone=phone, email=email, lead_time=lead_time: master.create_supplier(
                principal, code, name, contact_name=contact, phone=phone, email=email,
                tax_number=f"91440000{_digits(code, 8)}",
                address="广东省深圳市供应链大道 36 号",
                payment_terms_days=30, lead_time_days=lead_time,
            ),
        )

    for customer in customers.values():
        if not store.row("SELECT id FROM partner_contacts WHERE partner_type='customer' AND partner_id=?", (customer["id"],)):
            details.add_contact(principal, "customer", customer["id"], customer["contact_name"],
                                title="采购负责人", phone=customer["phone"], email=customer["email"], is_primary=True)
        if not store.row("SELECT id FROM partner_addresses WHERE partner_type='customer' AND partner_id=?", (customer["id"],)):
            details.add_address(principal, "customer", customer["id"], "shipping", recipient=customer["contact_name"],
                                phone=customer["phone"], province="上海市", city="上海市", district="闵行区",
                                street="履约路 16 号", postal_code="201100", is_default=True)
    for supplier in suppliers.values():
        if not store.row("SELECT id FROM partner_contacts WHERE partner_type='supplier' AND partner_id=?", (supplier["id"],)):
            details.add_contact(principal, "supplier", supplier["id"], supplier["contact_name"],
                                title="客户经理", phone=supplier["phone"], email=supplier["email"], is_primary=True)

    if not store.row("SELECT id FROM storage_locations WHERE site_id=? AND code='MOCK-BIN-B'", (SITE,)):
        master.create_location(principal, SITE, "MOCK-BIN-B", "演示备货库位 B")

    user_specs = (
        ("mock-sales", "演示销售员", ("sales",)),
        ("mock-buyer", "演示采购员", ("purchasing",)),
        ("mock-warehouse", "演示仓管员", ("warehouse",)),
        ("mock-finance", "演示财务员", ("finance",)),
        ("mock-auditor", "演示审计员", ("auditor",)),
    )
    for username, display_name, roles in user_specs:
        if not store.row("SELECT id FROM users WHERE organization_id=? AND username=?", (principal.organization_id, username)):
            identity.create_user(principal, username, display_name, "Mock-Only-2026!", roles,
                                 email=f"{username}@flowerp.example")

    return {"products": products, "customers": customers, "suppliers": suppliers}


def _ensure_inventory(store: ERPStore, products: dict[str, dict], suppliers: dict[str, dict]) -> None:
    principal = SYSTEM_PRINCIPAL
    inventory = InventoryService(store)
    serials = SerialNumberService(store)
    opening = {
        "MOCK-LAPTOP-PRO": (28, 620_000), "MOCK-MONITOR-27": (20, 155_000),
        "MOCK-DOCK-12": (42, 39_000), "MOCK-HEADSET": (6, 51_000),
        "MOCK-CABLE-C": (5, 5_200), "MOCK-CHAIR": (8, 92_000),
    }
    for sku, (quantity, cost) in opening.items():
        inventory.receive(principal, products[sku]["id"], LOCATION, quantity,
                          f"mock:opening:{sku}:v1", unit_cost_cents=cost,
                          reference_type="opening", reference_id=FIXTURE_KEY, reason="演示账套期初库存")

    duplicated_v2 = store.rows(
        "SELECT event_key FROM stock_moves WHERE organization_id=? AND event_key LIKE 'mock:opening:MOCK-%:v2'",
        (principal.organization_id,),
    )
    repaired = store.row(
        "SELECT setting_value FROM system_settings WHERE organization_id=? AND setting_key='demo.mock_data.v2_opening_repair'",
        (principal.organization_id,),
    )
    if duplicated_v2 and not repaired:
        count_lines = []
        for sku, (quantity, _) in opening.items():
            balance = inventory.balance(principal, products[sku]["id"], LOCATION)
            corrected = int(balance["on_hand"]) - quantity
            if corrected < int(balance["reserved"]):
                raise RuntimeError(f"无法修正 {sku} 的重复期初库存：修正后数量低于预占")
            count_lines.append({"product_id": products[sku]["id"], "counted_quantity": corrected})
        correction = inventory.create_count(
            principal, LOCATION, date.today().isoformat(), count_lines,
            "Mock v2 重复期初库存迁移修正",
        )
        inventory.post_count(principal, correction["id"])
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO system_settings(organization_id,setting_key,setting_value,value_type,updated_by) VALUES(?,?,?,?,?)",
                (principal.organization_id, "demo.mock_data.v2_opening_repair", "true", "boolean", principal.user_id),
            )

    coffee = products["MOCK-COFFEE"]
    lot = store.row("SELECT * FROM stock_lots WHERE organization_id=? AND product_id=? AND lot_number='MOCK-COFFEE-202608'",
                    (principal.organization_id, coffee["id"]))
    if not lot:
        lot = inventory.create_lot(principal, coffee["id"], "MOCK-COFFEE-202608",
                                   (date.today() - timedelta(days=45)).isoformat(),
                                   (date.today() + timedelta(days=75)).isoformat(), suppliers["MOCK-S003"]["id"])
    inventory.receive(principal, coffee["id"], LOCATION, 18, "mock:opening:coffee:v1",
                      lot["id"], 4_600, "opening", FIXTURE_KEY, "演示批次库存")

    tablet = products["MOCK-TABLET"]
    serial_lot = store.row("SELECT * FROM stock_lots WHERE organization_id=? AND product_id=? AND lot_number='MOCK-TABLET-BATCH'",
                           (principal.organization_id, tablet["id"]))
    if not serial_lot:
        serial_lot = inventory.create_lot(principal, tablet["id"], "MOCK-TABLET-BATCH",
                                          date.today().isoformat(), None, suppliers["MOCK-S001"]["id"])
    for index in range(1, 5):
        inventory.receive(principal, tablet["id"], LOCATION, 1, f"mock:opening:tablet:{index}",
                          serial_lot["id"], 238_000, "opening", FIXTURE_KEY, "演示序列号库存")
    if not serials.list(principal, tablet["id"]):
        serials.register(principal, tablet["id"], [f"MOCK-TAB-2026-{index:04d}" for index in range(1, 5)],
                         LOCATION, serial_lot["id"])

    bin_b = store.row("SELECT id FROM storage_locations WHERE site_id=? AND code='MOCK-BIN-B'", (SITE,))
    if not store.row("SELECT id FROM stock_moves WHERE organization_id=? AND event_key='mock:transfer:dock:b'",
                     (principal.organization_id,)):
        inventory.transfer(principal, products["MOCK-DOCK-12"]["id"], LOCATION, bin_b["id"], 6,
                           "mock:transfer:dock:b", reason="演示多库位调拨")


def _sales_order(store: ERPStore, sales: SalesService, reference: str, customer_id: str,
                 lines: list[dict], target: str, **fields: object) -> dict:
    principal = SYSTEM_PRINCIPAL
    row = store.row("SELECT id FROM sales_documents WHERE organization_id=? AND channel='mock' AND external_reference=?",
                    (principal.organization_id, reference))
    order = sales.order(principal, row["id"]) if row else sales.create_order(
        principal, customer_id, lines, channel="mock", external_reference=reference,
        notes="FlowERP 完整业务验收 Mock 数据", **fields,
    )
    if target == "cancelled" and order["status"] == "draft":
        return sales.cancel(principal, order["id"], "演示客户撤单")
    if target in {"confirmed", "reserved", "shipped"} and order["status"] == "draft":
        order = sales.confirm(principal, order["id"])
    if target in {"reserved", "shipped"} and order["status"] == "confirmed":
        allocations = {
            line["id"]: [{"location_id": LOCATION, "lot_id": "", "quantity": line["ordered_quantity"]}]
            for line in order["lines"]
        }
        order = sales.reserve(principal, order["id"], allocations)
    if target == "shipped" and order["status"] in {"reserved", "partially_shipped"}:
        shipment = store.row("SELECT id,status FROM shipments WHERE sales_document_id=? AND status='draft'", (order["id"],))
        shipment = sales.shipment(principal, shipment["id"]) if shipment else sales.create_shipment(
            principal, order["id"], carrier="顺丰速运", tracking_number=f"SF{_digits(reference)}"
        )
        sales.post_shipment(principal, shipment["id"], f"mock:shipment:{reference}")
        order = sales.order(principal, order["id"])
    return order


def _ensure_sales_and_finance(store: ERPStore, master: dict) -> dict:
    principal = SYSTEM_PRINCIPAL
    sales = SalesService(store)
    finance = FinanceService(store)
    products, customers = master["products"], master["customers"]
    today = date.today()

    current = _sales_order(
        store, sales, "MOCK-SO-SHIPPED-CURRENT", customers["MOCK-C001"]["id"],
        [{"product_id": products["MOCK-LAPTOP-PRO"]["id"], "quantity": 3, "discount_basis_points": 300},
         {"product_id": products["MOCK-DOCK-12"]["id"], "quantity": 3}], "shipped",
        order_date=today.isoformat(), requested_delivery_date=(today + timedelta(days=3)).isoformat(),
        freight_cents=3_000, shipping_address="上海市闵行区履约路 16 号",
    )
    overdue = _sales_order(
        store, sales, "MOCK-SO-OVERDUE", customers["MOCK-C002"]["id"],
        [{"product_id": products["MOCK-MONITOR-27"]["id"], "quantity": 2}], "shipped",
        order_date=(today - timedelta(days=45)).isoformat(),
        requested_delivery_date=(today - timedelta(days=40)).isoformat(),
    )
    _sales_order(
        store, sales, "MOCK-SO-RESERVED", customers["MOCK-C003"]["id"],
        [{"product_id": products["MOCK-DOCK-12"]["id"], "quantity": 4},
         {"product_id": products["MOCK-CABLE-C"]["id"], "quantity": 2}], "reserved",
        order_date=today.isoformat(), requested_delivery_date=(today + timedelta(days=2)).isoformat(),
    )
    _sales_order(
        store, sales, "MOCK-SO-DRAFT", customers["MOCK-C004"]["id"],
        [{"product_id": products["MOCK-CHAIR"]["id"], "quantity": 2}], "draft",
        order_date=today.isoformat(), requested_delivery_date=(today + timedelta(days=10)).isoformat(),
    )
    _sales_order(
        store, sales, "MOCK-SO-CANCELLED", customers["MOCK-C004"]["id"],
        [{"product_id": products["MOCK-HEADSET"]["id"], "quantity": 1}], "cancelled",
        order_date=today.isoformat(),
    )

    trend_specs = (
        (6, "MOCK-MONITOR-27", 1), (5, "MOCK-DOCK-12", 2),
        (4, "MOCK-LAPTOP-PRO", 1), (3, "MOCK-MONITOR-27", 3),
        (2, "MOCK-DOCK-12", 5), (1, "MOCK-LAPTOP-PRO", 2),
    )
    for offset, sku, quantity in trend_specs:
        month_index = today.year * 12 + today.month - 1 - offset
        order_date = date(month_index // 12, month_index % 12 + 1, min(12, today.day))
        _sales_order(
            store, sales, f"MOCK-SO-TREND-{offset:02d}", customers["MOCK-C001"]["id"],
            [{"product_id": products[sku]["id"], "quantity": quantity}], "shipped",
            order_date=order_date.isoformat(),
            requested_delivery_date=(order_date + timedelta(days=7)).isoformat(),
        )

    invoice_current = store.row("SELECT id FROM invoices WHERE source_type='sales_order' AND source_id=?", (current["id"],))
    invoice_current = finance.invoice(principal, invoice_current["id"]) if invoice_current else finance.create_invoice_from_sales(
        principal, current["id"], today.isoformat(), (today + timedelta(days=30)).isoformat(), "演示应收发票"
    )
    if not store.row("SELECT id FROM payments WHERE organization_id=? AND external_reference='MOCK-AR-PARTIAL'",
                     (principal.organization_id,)):
        amount = invoice_current["total_cents"] // 2
        finance.record_payment(principal, "receipt", "customer", current["customer_id"], amount,
                               payment_date=today.isoformat(), method="bank_transfer",
                               external_reference="MOCK-AR-PARTIAL",
                               allocations=[{"invoice_id": invoice_current["id"], "amount_cents": amount}],
                               notes="演示应收部分核销")

    invoice_overdue = store.row("SELECT id FROM invoices WHERE source_type='sales_order' AND source_id=?", (overdue["id"],))
    if not invoice_overdue:
        invoice_overdue = finance.create_invoice_from_sales(
            principal, overdue["id"], (today - timedelta(days=44)).isoformat(),
            (today - timedelta(days=14)).isoformat(), "演示逾期应收账款"
        )
    else:
        invoice_overdue = finance.invoice(principal, invoice_overdue["id"])

    return {"current_order": current, "overdue_order": overdue,
            "current_invoice": invoice_current, "overdue_invoice": invoice_overdue}


def _purchase_order(store: ERPStore, purchasing: PurchasingService, reference: str, supplier_id: str,
                    lines: list[dict], target: str, **fields: object) -> dict:
    principal = SYSTEM_PRINCIPAL
    row = store.row("SELECT id FROM purchase_orders WHERE organization_id=? AND supplier_reference=?",
                    (principal.organization_id, reference))
    order = purchasing.order(principal, row["id"]) if row else purchasing.create_order(
        MOCK_BUYER, supplier_id, SITE, lines, supplier_reference=reference,
        notes="FlowERP 完整业务验收 Mock 数据", **fields,
    )
    if target in {"pending_approval", "approved", "received"} and order["status"] == "draft":
        order = purchasing.submit(MOCK_BUYER, order["id"])
    if target in {"approved", "received"} and order["status"] == "pending_approval":
        order = purchasing.approve(MOCK_APPROVER, order["id"])
    if target == "received" and order["status"] in {"approved", "partially_received"}:
        receipt_row = store.row("SELECT id,status FROM goods_receipts WHERE purchase_order_id=? AND status='draft'", (order["id"],))
        if receipt_row:
            receipt = purchasing.receipt(principal, receipt_row["id"])
        else:
            current = purchasing.order(principal, order["id"])
            receipt = purchasing.create_receipt(
                principal, order["id"], LOCATION,
                [{"purchase_line_id": line["id"], "accepted_quantity": line["ordered_quantity"] - line["received_quantity"]}
                 for line in current["lines"] if line["ordered_quantity"] > line["received_quantity"]],
                supplier_delivery_note=f"DN-{reference}",
            )
        purchasing.post_receipt(principal, receipt["id"], f"mock:receipt:{reference}")
        order = purchasing.order(principal, order["id"])
    return order


def _ensure_purchasing_and_finance(store: ERPStore, master: dict) -> dict:
    principal = SYSTEM_PRINCIPAL
    purchasing = PurchasingService(store)
    finance = FinanceService(store)
    products, suppliers = master["products"], master["suppliers"]
    today = date.today()
    _purchase_order(
        store, purchasing, "MOCK-PO-PENDING", suppliers["MOCK-S001"]["id"],
        [{"product_id": products["MOCK-LAPTOP-PRO"]["id"], "quantity": 10, "unit_price_cents": 615_000}],
        "pending_approval", order_date=today.isoformat(), expected_date=(today + timedelta(days=7)).isoformat(),
    )
    _purchase_order(
        store, purchasing, "MOCK-PO-APPROVED", suppliers["MOCK-S002"]["id"],
        [{"product_id": products["MOCK-HEADSET"]["id"], "quantity": 12, "unit_price_cents": 50_000}],
        "approved", order_date=today.isoformat(), expected_date=(today + timedelta(days=5)).isoformat(),
    )
    received = _purchase_order(
        store, purchasing, "MOCK-PO-RECEIVED", suppliers["MOCK-S003"]["id"],
        [{"product_id": products["MOCK-CHAIR"]["id"], "quantity": 8, "unit_price_cents": 92_000}],
        "received", order_date=(today - timedelta(days=6)).isoformat(), expected_date=(today - timedelta(days=1)).isoformat(),
        freight_cents=8_000,
    )
    _purchase_order(
        store, purchasing, "MOCK-PO-DRAFT", suppliers["MOCK-S002"]["id"],
        [{"product_id": products["MOCK-CABLE-C"]["id"], "quantity": 40, "unit_price_cents": 5_000}],
        "draft", order_date=today.isoformat(), expected_date=(today + timedelta(days=12)).isoformat(),
    )

    payable_row = store.row("SELECT id FROM invoices WHERE source_type='purchase_order' AND source_id=?", (received["id"],))
    if payable_row:
        payable = finance.invoice(principal, payable_row["id"])
    else:
        current = purchasing.order(principal, received["id"])
        supplier_lines = [
            {"source_line_id": line["id"], "quantity": line["received_quantity"],
             "unit_price_cents": line["unit_price_cents"] + 1_000}
            for line in current["lines"]
        ]
        supplier_total = sum(
            line["quantity"] * line["unit_price_cents"] * (10_000 + 1_300) // 10_000
            for line in supplier_lines
        ) + current["freight_cents"]
        payable = finance.create_invoice_from_purchase(
            principal, received["id"], today.isoformat(), (today + timedelta(days=30)).isoformat(),
            "演示三单匹配与采购价差", supplier_invoice_number="MOCK-SUP-INV-001",
            supplier_lines=supplier_lines, price_tolerance_basis_points=200,
            supplier_total_cents=supplier_total,
        )
    if not store.row("SELECT id FROM payments WHERE organization_id=? AND external_reference='MOCK-AP-PARTIAL'",
                     (principal.organization_id,)):
        amount = payable["total_cents"] // 3
        finance.record_payment(principal, "disbursement", "supplier", received["supplier_id"], amount,
                               payment_date=today.isoformat(), method="bank_transfer",
                               external_reference="MOCK-AP-PARTIAL",
                               allocations=[{"invoice_id": payable["id"], "amount_cents": amount}],
                               notes="演示应付部分核销")
    return {"received_purchase": received, "payable_invoice": payable}


def _ensure_workflows(store: ERPStore, master: dict, sales_data: dict) -> None:
    principal = SYSTEM_PRINCIPAL
    sales = SalesService(store)
    inventory = InventoryService(store)
    pricing = PricingService(store)

    order = sales.order(principal, sales_data["current_order"]["id"])
    return_row = store.row("SELECT id FROM sales_returns WHERE sales_document_id=? AND reason_code='mock_quality'", (order["id"],))
    if not return_row:
        line = order["lines"][0]
        returned = sales.create_return(MOCK_SALES, order["id"], "mock_quality",
                                       [{"sales_line_id": line["id"], "quantity": 1, "condition": "resellable"}],
                                       "演示售后审批待收货", "refund")
    else:
        returned = sales.sales_return(principal, return_row["id"])
    if returned["status"] == "draft":
        sales.authorize_return(MOCK_APPROVER, returned["id"])

    price_list = store.row("SELECT id FROM price_lists WHERE organization_id=? AND code='MOCK-ENTERPRISE'",
                           (principal.organization_id,))
    if not price_list:
        price_list = pricing.create_price_list(principal, "MOCK-ENTERPRISE", "演示企业客户价目表",
                                               customer_id=master["customers"]["MOCK-C001"]["id"], priority=10)
    else:
        price_list = pricing.price_list(principal, price_list["id"])
    if not store.row("SELECT id FROM price_rules WHERE price_list_id=? AND product_id=? AND min_quantity=5",
                     (price_list["id"], master["products"]["MOCK-LAPTOP-PRO"]["id"])):
        pricing.add_rule(principal, price_list["id"], master["products"]["MOCK-LAPTOP-PRO"]["id"],
                         5, 839_900, discount_basis_points=300)

    if not store.row("SELECT id FROM stock_counts WHERE organization_id=? AND reason='演示待审批循环盘点'",
                     (principal.organization_id,)):
        balance = inventory.balance(principal, master["products"]["MOCK-MONITOR-27"]["id"], LOCATION)
        inventory.create_count(principal, LOCATION, date.today().isoformat(),
                               [{"product_id": master["products"]["MOCK-MONITOR-27"]["id"],
                                 "counted_quantity": balance["on_hand"]}], "演示待审批循环盘点")

    AlertService(store).refresh(principal)


def _ensure_cash_management(store: ERPStore) -> dict:
    principal = SYSTEM_PRINCIPAL
    cash = CashManagementService(store)
    account = store.row(
        "SELECT b.* FROM bank_accounts b JOIN ledger_accounts a ON a.id=b.ledger_account_id "
        "WHERE b.organization_id=? AND a.code='1002'", (principal.organization_id,),
    )
    if not account:
        account = cash.create_account(principal, "MOCK-BANK-CNY", "演示人民币基本户", "演示商业银行",
                                      "1002", "CNY", "6222020000008899", 0)
    payments = store.rows(
        "SELECT * FROM payments WHERE organization_id=? AND status='posted' AND method<>'cash' "
        "AND external_reference LIKE 'MOCK-%' ORDER BY payment_date,payment_number",
        (principal.organization_id,),
    )
    if not payments:
        return {"account": account, "statement": None}
    mock_movement = sum(int(item["amount_cents"]) * (1 if item["payment_type"] == "receipt" else -1)
                        for item in payments)
    ledger_movement = int(store.scalar(
        "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_lines l "
        "JOIN journal_entries e ON e.id=l.journal_entry_id JOIN ledger_accounts a ON a.id=l.account_id "
        "WHERE e.organization_id=? AND e.status='posted' AND a.code='1002'",
        (principal.organization_id,),
    ) or 0)
    if ledger_movement != mock_movement:
        return {"account": account, "statement": None, "reason": "银行科目包含非演示流水，未自动生成对账单"}
    for payment in payments:
        if not payment.get("bank_account_id"):
            store.execute("UPDATE payments SET bank_account_id=? WHERE id=?", (account["id"], payment["id"]))
    period_start = min(str(item["payment_date"]) for item in payments)
    period_end = max(str(item["payment_date"]) for item in payments)
    lines = [{"external_transaction_id": f"MOCK-BANK-{item['payment_number']}",
              "transaction_date": item["payment_date"],
              "signed_amount_cents": int(item["amount_cents"]) * (1 if item["payment_type"] == "receipt" else -1),
              "reference": item["external_reference"], "description": "演示银行流水"} for item in payments]
    statement = cash.import_statement(principal, account["id"], "MOCK-STATEMENT-001", period_start, period_end,
                                      0, mock_movement, lines)
    if statement["status"] != "reconciled":
        statement = cash.auto_match(principal, statement["id"])
        if statement["unmatched_count"] == 0:
            statement = cash.reconcile(principal, statement["id"])
    return {"account": cash.account(principal, account["id"]), "statement": statement}


def _ensure_ecommerce_channels(store: ERPStore, master: dict) -> dict:
    channels = EcommerceChannelService(store)
    shop = store.row("SELECT id FROM channel_shops WHERE organization_id=? AND code='MOCK-TMALL'", (SYSTEM_PRINCIPAL.organization_id,))
    if not shop:
        shop = channels.create_shop(
            SYSTEM_PRINCIPAL, "mock", "MOCK-TMALL", "天猫旗舰店（沙箱）",
            master["customers"]["MOCK-C004"]["id"], SITE, "mock-tmall-shop-001", sync_mode="manual",
        )
    else:
        shop = channels.shop(SYSTEM_PRINCIPAL, shop["id"])
    mappings = (
        ("tmall-item-cable", "tmall-sku-cable-blue", "全功能 USB-C 线缆 蓝色", "MOCK-CABLE-C"),
        ("tmall-item-headset", "tmall-sku-headset-black", "会议降噪耳机 黑色", "MOCK-HEADSET"),
    )
    for product_ref, sku_ref, title, internal_sku in mappings:
        if not store.row("SELECT id FROM channel_listings WHERE shop_id=? AND external_product_id=? AND external_sku_id=?", (shop["id"], product_ref, sku_ref)):
            channels.map_listing(SYSTEM_PRINCIPAL, shop["id"], product_ref, sku_ref, title,
                                 [{"product_id": master["products"][internal_sku]["id"], "quantity": 1,
                                   "revenue_share_basis_points": 10000}])
    common = {
        "external_status": "paid", "order_time": f"{date.today().isoformat()}T09:30:00+08:00",
        "paid_time": f"{date.today().isoformat()}T09:31:00+08:00", "currency": "CNY",
        "recipient": "陈小满", "phone": "13800138000", "country": "CN", "province": "浙江省",
        "city": "杭州市", "district": "余杭区", "street": "未来科技城履约路 18 号",
    }
    valid = {**common, "external_order_id": "MOCK-TMALL-ORDER-001", "goods_cents": 25_800,
             "discount_cents": 1_000, "freight_cents": 0, "total_cents": 24_800,
             "buyer_note": "请放驿站", "lines": [{"external_line_id": "1", "external_product_id": "tmall-item-cable",
             "external_sku_id": "tmall-sku-cable-blue", "title": "全功能 USB-C 线缆 蓝色", "quantity": 2,
             "unit_price_cents": 12_900, "discount_cents": 1_000, "total_cents": 24_800}]}
    blocked = {**common, "external_order_id": "MOCK-TMALL-ORDER-002", "recipient": "", "phone": "12",
               "province": "", "goods_cents": 69_900, "discount_cents": 0, "freight_cents": 0,
               "total_cents": 69_900, "lines": [{"external_line_id": "1", "external_product_id": "unknown-item",
               "external_sku_id": "unknown-sku", "title": "尚未建立映射的平台新品", "quantity": 1,
               "unit_price_cents": 69_900, "discount_cents": 0, "total_cents": 69_900}]}
    run = channels.ingest_orders(SYSTEM_PRINCIPAL, shop["id"], [valid, blocked], "mock")
    return {"shop": shop, "run": run}


def verify_mock_data(store: ERPStore) -> dict:
    principal = SYSTEM_PRINCIPAL
    reports = ReportService(store)
    reconciliations = ReconciliationService(store).run_all(principal)
    dashboard = reports.dashboard(principal)
    counts = {
        "products": int(store.scalar("SELECT COUNT(*) FROM product_master WHERE organization_id=? AND sku LIKE 'MOCK-%'", (principal.organization_id,)) or 0),
        "customers": int(store.scalar("SELECT COUNT(*) FROM customer_master WHERE organization_id=? AND code LIKE 'MOCK-%'", (principal.organization_id,)) or 0),
        "suppliers": int(store.scalar("SELECT COUNT(*) FROM supplier_master WHERE organization_id=? AND code LIKE 'MOCK-%'", (principal.organization_id,)) or 0),
        "sales_orders": int(store.scalar("SELECT COUNT(*) FROM sales_documents WHERE organization_id=? AND channel='mock'", (principal.organization_id,)) or 0),
        "purchase_orders": int(store.scalar("SELECT COUNT(*) FROM purchase_orders WHERE organization_id=? AND supplier_reference LIKE 'MOCK-%'", (principal.organization_id,)) or 0),
        "invoices": int(store.scalar("SELECT COUNT(*) FROM invoices WHERE organization_id=?", (principal.organization_id,)) or 0),
        "payments": int(store.scalar("SELECT COUNT(*) FROM payments WHERE organization_id=? AND external_reference LIKE 'MOCK-%'", (principal.organization_id,)) or 0),
        "journal_entries": int(store.scalar("SELECT COUNT(*) FROM journal_entries WHERE organization_id=? AND status='posted'", (principal.organization_id,)) or 0),
        "audit_events": int(store.scalar("SELECT COUNT(*) FROM audit_log WHERE organization_id=?", (principal.organization_id,)) or 0),
        "bank_accounts": int(store.scalar("SELECT COUNT(*) FROM bank_accounts WHERE organization_id=?", (principal.organization_id,)) or 0),
        "bank_statements": int(store.scalar("SELECT COUNT(*) FROM bank_statements WHERE organization_id=?", (principal.organization_id,)) or 0),
        "channel_shops": int(store.scalar("SELECT COUNT(*) FROM channel_shops WHERE organization_id=?", (principal.organization_id,)) or 0),
        "channel_listings": int(store.scalar("SELECT COUNT(*) FROM channel_listings WHERE organization_id=?", (principal.organization_id,)) or 0),
        "channel_orders": int(store.scalar("SELECT COUNT(*) FROM channel_orders WHERE organization_id=?", (principal.organization_id,)) or 0),
    }
    sales_statuses = {row["status"] for row in store.rows(
        "SELECT DISTINCT status FROM sales_documents WHERE organization_id=? AND channel='mock'", (principal.organization_id,)
    )}
    purchase_statuses = {row["status"] for row in store.rows(
        "SELECT DISTINCT status FROM purchase_orders WHERE organization_id=? AND supplier_reference LIKE 'MOCK-%'", (principal.organization_id,)
    )}
    checks = {
        "master_data": counts["products"] >= 8 and counts["customers"] >= 4 and counts["suppliers"] >= 3,
        "sales_state_coverage": {"draft", "reserved", "shipped", "cancelled"}.issubset(sales_statuses),
        "purchase_state_coverage": {"draft", "pending_approval", "approved", "received"}.issubset(purchase_statuses),
        "monthly_trend_coverage": int(store.scalar(
            "SELECT COUNT(DISTINCT substr(order_date,1,7)) FROM sales_documents WHERE organization_id=? AND channel='mock' AND status<>'cancelled'",
            (principal.organization_id,),
        ) or 0) >= 7,
        "inventory_nonnegative": not bool(store.scalar(
            "SELECT 1 FROM stock_balance WHERE organization_id=? AND (on_hand<0 OR reserved<0 OR reserved>on_hand) LIMIT 1",
            (principal.organization_id,),
        )),
        "lot_and_serial_traceability": bool(store.scalar(
            "SELECT COUNT(*)>=4 FROM serial_numbers WHERE organization_id=? AND serial_number LIKE 'MOCK-%'",
            (principal.organization_id,),
        )),
        "receivable_and_payable": dashboard["receivable_cents"] > 0 and dashboard["payable_cents"] > 0,
        "pending_workflows": all(dashboard["pending"][name] > 0 for name in (
            "sales_to_ship", "purchases_to_approve", "purchases_to_receive", "returns_to_receive"
        )),
        "double_entry_ledger": counts["journal_entries"] >= 8,
        "inventory_reconciliation": reconciliations["inventory"]["status"] == "passed",
        "sales_reconciliation": reconciliations["sales"]["status"] == "passed",
        "finance_reconciliation": reconciliations["finance"]["status"] == "passed",
        "accounting_reconciliation": reconciliations["accounting"]["status"] == "passed",
        "cash_management": counts["bank_accounts"] >= 1 and bool(store.scalar(
            "SELECT COUNT(*) FROM bank_statements WHERE organization_id=? AND status='reconciled'",
            (principal.organization_id,),
        )),
        "ecommerce_channel_hub": counts["channel_shops"] >= 1 and counts["channel_listings"] >= 2 and counts["channel_orders"] >= 2 and bool(store.scalar(
            "SELECT COUNT(*) FROM channel_orders WHERE organization_id=? AND status='blocked'", (principal.organization_id,),
        )),
    }
    return {
        "complete": all(checks.values()), "fixture_version": FIXTURE_VERSION,
        "checks": checks, "counts": counts, "dashboard": dashboard,
        "reconciliations": {name: {"id": item["id"], "status": item["status"],
                                            "checked_items": item["checked_items"],
                                            "discrepancy_count": item["discrepancy_count"]}
                              for name, item in reconciliations.items()},
    }


def load_mock_data(store: ERPStore) -> dict:
    """Load a deterministic, idempotent end-to-end ERP acceptance dataset."""
    marker = store.row("SELECT setting_value FROM system_settings WHERE organization_id=? AND setting_key=?",
                       (SYSTEM_PRINCIPAL.organization_id, FIXTURE_KEY))
    marker_value = json.loads(marker["setting_value"]) if marker else None
    if marker_value and int(marker_value.get("version", 0)) >= FIXTURE_VERSION:
        verification = verify_mock_data(store)
        return {"loaded": False, "idempotent_replay": True, "marker": marker_value,
                "verification": verification}

    master = _ensure_master_data(store)
    _ensure_inventory(store, master["products"], master["suppliers"])
    sales_data = _ensure_sales_and_finance(store, master)
    purchase_data = _ensure_purchasing_and_finance(store, master)
    cash_data = _ensure_cash_management(store)
    _ensure_workflows(store, master, sales_data)
    channel_data = _ensure_ecommerce_channels(store, master)
    verification = verify_mock_data(store)
    marker_value = {"version": FIXTURE_VERSION, "loaded_on": date.today().isoformat(),
                    "complete": verification["complete"]}
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO system_settings(organization_id,setting_key,setting_value,value_type,updated_by) VALUES(?,?,?,?,?) "
            "ON CONFLICT(organization_id,setting_key) DO UPDATE SET setting_value=excluded.setting_value,"
            "value_type=excluded.value_type,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP",
            (SYSTEM_PRINCIPAL.organization_id, FIXTURE_KEY, json.dumps(marker_value, ensure_ascii=False), "json",
             SYSTEM_PRINCIPAL.user_id),
        )
    return {"loaded": True, "idempotent_replay": False, "marker": marker_value,
            "sales": {"current_order": sales_data["current_order"]["document_number"],
                      "overdue_order": sales_data["overdue_order"]["document_number"]},
            "purchase": {"received_order": purchase_data["received_purchase"]["order_number"]},
            "cash_management": {"account": cash_data["account"]["code"],
                                "statement": cash_data.get("statement", {}).get("statement_number") if cash_data.get("statement") else None},
            "channels": {"shop": channel_data["shop"]["code"], "sync_run": channel_data["run"]["id"]},
            "verification": verification}
