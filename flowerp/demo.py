from __future__ import annotations

from dataclasses import dataclass

from .models import ApprovalRequired, InsufficientStock, OrderLine
from .seed import seed_demo
from .service import ERPService


DEMO_SKU = "NOTEBOOK-AI"
DEMO_PRODUCT = "轻薄办公笔记本"
ORDER_A = "EC-20260817-1001"
PURCHASE_ID = "PO-DEMO-20260817"
RECEIPT_KEY = "receipt:demo:ecommerce:001"


@dataclass(frozen=True)
class DemoStep:
    key: str
    title: str
    description: str


STEPS = (
    DemoStep("stocked", "活动备货", "轻薄办公笔记本活动库存 8 台"),
    DemoStep("orders", "电商订单创建", "客户一次下单 20 台笔记本"),
    DemoStep("shortage", "预占与缺货", "库存仅 8 台，系统识别缺口 12 台"),
    DemoStep("blocked", "补货待审批", "生成补货建议，未审批收货被阻断"),
    DemoStep("received", "审批并入库", "运营审批后到货 12 台，库存入账"),
    DemoStep("reserved", "恢复履约", "20 台库存原子预占成功，系统不超卖"),
    DemoStep("shipped", "出库发货", "订单完成发货，证据链闭合"),
)


class EcommerceDemo:
    """Persistent, step-by-step e-commerce ERP demonstration."""

    def __init__(self, service: ERPService) -> None:
        self.service = service
        self._ensure_state()

    def _ensure_state(self) -> None:
        with self.service.store.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS demo_state("
                "id INTEGER PRIMARY KEY CHECK(id=1), stage INTEGER NOT NULL, "
                "last_message TEXT NOT NULL, failure TEXT NOT NULL DEFAULT '')"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS demo_events("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, stage INTEGER NOT NULL, "
                "kind TEXT NOT NULL, message TEXT NOT NULL, "
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            exists = conn.execute("SELECT 1 FROM demo_state WHERE id=1").fetchone()
            current_product = conn.execute(
                "SELECT 1 FROM products WHERE sku=?", (DEMO_SKU,)
            ).fetchone()
        if not exists or not current_product:
            self.reset()

    def reset(self) -> dict:
        with self.service.store.connect() as conn:
            for table in (
                "sales_order_lines", "sales_orders", "purchase_requests",
                "inventory_events", "stock", "products",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM demo_state")
            conn.execute("DELETE FROM demo_events")
            conn.execute(
                "INSERT INTO demo_state(id,stage,last_message,failure) VALUES(1,0,?, '')",
                ("活动商品已备货 8 台，等待客户下单",),
            )
            conn.execute(
                "INSERT INTO demo_events(stage,kind,message) VALUES(0,'success',?)",
                ("活动商品已备货 8 台",),
            )
        seed_demo(self.service)
        return self.state()

    def _stage(self) -> int:
        row = self.service.store.row("SELECT stage FROM demo_state WHERE id=1")
        return int(row["stage"]) if row else 0

    def _move(self, stage: int, message: str, failure: str = "") -> None:
        with self.service.store.connect() as conn:
            conn.execute(
                "UPDATE demo_state SET stage=?,last_message=?,failure=? WHERE id=1",
                (stage, message, failure),
            )
            conn.execute(
                "INSERT INTO demo_events(stage,kind,message) VALUES(?,?,?)",
                (stage, "blocked" if failure else "success", failure or message),
            )

    def advance(self) -> dict:
        stage = self._stage()
        if stage >= len(STEPS) - 1:
            return self.state()
        if stage == 0:
            self.service.create_order(
                "星河设计工作室", [OrderLine(DEMO_SKU, 20, 599900)], ORDER_A
            )
            self._move(1, "电商订单已创建：轻薄办公笔记本 20 台")
        elif stage == 1:
            failure = ""
            try:
                self.service.reserve_order(ORDER_A)
            except InsufficientStock as exc:
                failure = str(exc)
            self._move(2, "库存仅 8 台；20 台订单被阻止超卖", failure)
        elif stage == 2:
            self.service.propose_purchase(
                DEMO_SKU, 12, "20 台订单库存仅 8 台，补足缺口 12 台", PURCHASE_ID
            )
            failure = ""
            try:
                self.service.receive_purchase(PURCHASE_ID, RECEIPT_KEY)
            except ApprovalRequired as exc:
                failure = str(exc)
            self._move(3, "补货建议已生成；未经运营审批的收货请求被拦截", failure)
        elif stage == 3:
            self.service.approve_purchase(PURCHASE_ID, "电商运营主管")
            self.service.receive_purchase(PURCHASE_ID, RECEIPT_KEY)
            self._move(4, "运营审批完成，到货 12 台已按幂等键入库")
        elif stage == 4:
            self.service.reserve_order(ORDER_A)
            self._move(5, "20 台库存原子预占成功，订单可以出库")
        elif stage == 5:
            self.service.ship_order(ORDER_A)
            self._move(6, "20 台笔记本订单已发货，可用库存归零")
        return self.state()

    def state(self) -> dict:
        stage_row = self.service.store.row(
            "SELECT stage,last_message,failure FROM demo_state WHERE id=1"
        ) or {"stage": 0, "last_message": "", "failure": ""}
        stage = int(stage_row["stage"])
        inventory = self.service.product(DEMO_SKU)
        orders = []
        for oid in (ORDER_A,):
            try:
                orders.append(self.service.order(oid))
            except Exception:
                pass
        purchases = self.service.list_purchases()
        timeline = []
        for index, step in enumerate(STEPS):
            timeline.append(
                {
                    "key": step.key,
                    "title": step.title,
                    "description": step.description,
                    "status": "done" if index < stage else "current" if index == stage else "pending",
                }
            )
        return {
            "kind": "ecommerce",
            "scenario": "电商笔记本订单库存履约",
            "stage": stage,
            "stage_key": STEPS[stage].key,
            "is_complete": stage == len(STEPS) - 1,
            "next_action": None if stage == len(STEPS) - 1 else STEPS[stage + 1].title,
            "last_message": stage_row["last_message"],
            "failure": stage_row["failure"],
            "product": inventory,
            "orders": orders,
            "purchases": purchases,
            "events": self.service.store.rows(
                "SELECT stage,kind,message,created_at FROM demo_events ORDER BY id"
            ),
            "timeline": timeline,
            "metrics": {
                "opening_stock": 8,
                "order_demand": 20 if stage >= 1 else 0,
                "shortage": 12 if 2 <= stage < 4 else 0,
                "available": inventory["available"],
            },
        }
