from __future__ import annotations

import uuid
from typing import Iterable

from .models import (
    ApprovalRequired,
    InsufficientStock,
    InvalidTransition,
    NotFound,
    OrderLine,
    OrderStatus,
    PurchaseStatus,
)
from .store import ERPStore


class ERPService:
    """Transaction boundary for the small ERP domain."""

    def __init__(self, store: ERPStore) -> None:
        self.store = store

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"

    def add_customer(
        self, name: str, phone: str = "", email: str = "", address: str = "",
        customer_id: str | None = None,
    ) -> dict:
        if not name.strip():
            raise ValueError("客户名称不能为空")
        cid = customer_id or self._new_id("CUS")
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO customers(id,name,phone,email,address) VALUES(?,?,?,?,?)",
                (cid, name.strip(), phone.strip(), email.strip(), address.strip()),
            )
        return self.customer(cid)

    def customer(self, customer_id: str) -> dict:
        item = self.store.row("SELECT * FROM customers WHERE id=?", (customer_id,))
        if not item:
            raise NotFound(f"客户不存在：{customer_id}")
        return item

    def list_customers(self) -> list[dict]:
        return self.store.rows("SELECT * FROM customers ORDER BY created_at DESC,id DESC")

    def add_supplier(
        self, name: str, contact: str = "", phone: str = "",
        supplier_id: str | None = None,
    ) -> dict:
        if not name.strip():
            raise ValueError("供应商名称不能为空")
        sid = supplier_id or self._new_id("SUP")
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO suppliers(id,name,contact,phone) VALUES(?,?,?,?)",
                (sid, name.strip(), contact.strip(), phone.strip()),
            )
        return self.supplier(sid)

    def supplier(self, supplier_id: str) -> dict:
        item = self.store.row("SELECT * FROM suppliers WHERE id=?", (supplier_id,))
        if not item:
            raise NotFound(f"供应商不存在：{supplier_id}")
        return item

    def list_suppliers(self) -> list[dict]:
        return self.store.rows("SELECT * FROM suppliers ORDER BY created_at DESC,id DESC")

    def add_product(self, sku: str, name: str, unit_price_cents: int, reorder_point: int = 0) -> dict:
        sku = sku.strip().upper()
        if not sku or unit_price_cents < 0 or reorder_point < 0:
            raise ValueError("SKU 不能为空，价格和补货点不能为负")
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO products(sku,name,unit_price_cents,reorder_point) VALUES(?,?,?,?) "
                "ON CONFLICT(sku) DO UPDATE SET name=excluded.name, unit_price_cents=excluded.unit_price_cents, reorder_point=excluded.reorder_point",
                (sku, name.strip(), unit_price_cents, reorder_point),
            )
            conn.execute("INSERT OR IGNORE INTO stock(sku,on_hand,reserved) VALUES(?,0,0)", (sku,))
        return self.product(sku)

    def product(self, sku: str) -> dict:
        item = self.store.row(
            "SELECT p.*, s.on_hand, s.reserved, (s.on_hand-s.reserved) AS available "
            "FROM products p JOIN stock s USING(sku) WHERE p.sku=?",
            (sku.upper(),),
        )
        if not item:
            raise NotFound(f"商品不存在：{sku}")
        return item

    def inventory(self) -> list[dict]:
        return self.store.rows(
            "SELECT p.sku,p.name,s.on_hand,s.reserved,(s.on_hand-s.reserved) AS available,p.reorder_point "
            "FROM products p JOIN stock s USING(sku) ORDER BY p.sku"
        )

    def inventory_events(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        return self.store.rows(
            "SELECT event_key,sku,quantity,reserved_delta,event_type,reference,created_at "
            "FROM inventory_events ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,)
        )

    def receive_stock(self, sku: str, quantity: int, event_key: str, reference: str = "manual") -> dict:
        if quantity <= 0:
            raise ValueError("入库数量必须大于 0")
        sku = sku.upper()
        with self.store.connect() as conn:
            if not conn.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                raise NotFound(f"商品不存在：{sku}")
            exists = conn.execute("SELECT 1 FROM inventory_events WHERE event_key=?", (event_key,)).fetchone()
            if exists:
                row = conn.execute(
                    "SELECT p.*,s.on_hand,s.reserved,(s.on_hand-s.reserved) AS available "
                    "FROM products p JOIN stock s USING(sku) WHERE p.sku=?", (sku,)
                ).fetchone()
                result = dict(row)
                result["idempotent_replay"] = True
                return result
            conn.execute(
                "INSERT INTO inventory_events(event_key,sku,quantity,reserved_delta,event_type,reference) VALUES(?,?,?,?,?,?)",
                (event_key, sku, quantity, 0, "receive", reference),
            )
            conn.execute("UPDATE stock SET on_hand=on_hand+? WHERE sku=?", (quantity, sku))
        result = self.product(sku)
        result["idempotent_replay"] = False
        return result

    def create_order(
        self, customer: str, lines: Iterable[OrderLine], order_id: str | None = None,
        customer_id: str | None = None, channel: str = "online", remark: str = "",
    ) -> dict:
        prepared = list(lines)
        if not customer.strip() or not prepared:
            raise ValueError("客户和订单明细不能为空")
        if any(line.quantity <= 0 or line.unit_price_cents < 0 for line in prepared):
            raise ValueError("数量必须为正，单价不能为负")
        if len({line.sku.upper() for line in prepared}) != len(prepared):
            raise ValueError("同一订单不能出现重复 SKU")
        oid = order_id or f"SO-{uuid.uuid4().hex[:8].upper()}"
        total = sum(line.line_total_cents for line in prepared)
        with self.store.connect() as conn:
            if customer_id and not conn.execute(
                "SELECT 1 FROM customers WHERE id=?", (customer_id,)
            ).fetchone():
                raise NotFound(f"客户不存在：{customer_id}")
            conn.execute(
                "INSERT INTO sales_orders(id,customer,customer_id,channel,remark,status,total_cents) "
                "VALUES(?,?,?,?,?,?,?)",
                (oid, customer.strip(), customer_id, channel.strip() or "online", remark.strip(), OrderStatus.DRAFT, total),
            )
            for line in prepared:
                sku = line.sku.upper()
                if not conn.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                    raise NotFound(f"商品不存在：{sku}")
                conn.execute(
                    "INSERT INTO sales_order_lines(order_id,sku,quantity,unit_price_cents) VALUES(?,?,?,?)",
                    (oid, sku, line.quantity, line.unit_price_cents),
                )
        return self.order(oid)

    def order(self, order_id: str) -> dict:
        order = self.store.row("SELECT * FROM sales_orders WHERE id=?", (order_id,))
        if not order:
            raise NotFound(f"订单不存在：{order_id}")
        order["lines"] = self.store.rows(
            "SELECT sku,quantity,unit_price_cents,quantity*unit_price_cents AS line_total_cents "
            "FROM sales_order_lines WHERE order_id=? ORDER BY sku",
            (order_id,),
        )
        return order

    def list_orders(self) -> list[dict]:
        orders = self.store.rows("SELECT * FROM sales_orders ORDER BY created_at DESC,id DESC")
        for order in orders:
            order["lines"] = self.store.rows(
                "SELECT sku,quantity,unit_price_cents,quantity*unit_price_cents AS line_total_cents "
                "FROM sales_order_lines WHERE order_id=? ORDER BY sku", (order["id"],)
            )
        return orders

    def reserve_order(self, order_id: str) -> dict:
        with self.store.connect() as conn:
            order = conn.execute("SELECT status FROM sales_orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                raise NotFound(f"订单不存在：{order_id}")
            if order["status"] != OrderStatus.DRAFT:
                raise InvalidTransition(f"只有 draft 订单可预占，当前为 {order['status']}")
            lines = conn.execute(
                "SELECT l.sku,l.quantity,s.on_hand-s.reserved AS available FROM sales_order_lines l "
                "JOIN stock s USING(sku) WHERE l.order_id=? ORDER BY l.sku", (order_id,)
            ).fetchall()
            for line in lines:
                if line["available"] < line["quantity"]:
                    raise InsufficientStock(line["sku"], line["quantity"], line["available"])
            for line in lines:
                conn.execute("UPDATE stock SET reserved=reserved+? WHERE sku=?", (line["quantity"], line["sku"]))
                conn.execute(
                    "INSERT INTO inventory_events(event_key,sku,quantity,reserved_delta,event_type,reference) "
                    "VALUES(?,?,?,?,?,?)",
                    (f"reserve:{order_id}:{line['sku']}", line["sku"], 0, line["quantity"], "reserve", order_id),
                )
            conn.execute(
                "UPDATE sales_orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (OrderStatus.RESERVED, order_id),
            )
        return self.order(order_id)

    def cancel_order(self, order_id: str) -> dict:
        with self.store.connect() as conn:
            order = conn.execute("SELECT status FROM sales_orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                raise NotFound(f"订单不存在：{order_id}")
            status = order["status"]
            if status not in {OrderStatus.DRAFT, OrderStatus.RESERVED}:
                raise InvalidTransition(f"状态 {status} 不允许取消")
            if status == OrderStatus.RESERVED:
                lines = conn.execute("SELECT sku,quantity FROM sales_order_lines WHERE order_id=?", (order_id,)).fetchall()
                for line in lines:
                    conn.execute("UPDATE stock SET reserved=reserved-? WHERE sku=?", (line["quantity"], line["sku"]))
                    conn.execute(
                        "INSERT INTO inventory_events(event_key,sku,quantity,reserved_delta,event_type,reference) "
                        "VALUES(?,?,?,?,?,?)",
                        (f"release:{order_id}:{line['sku']}", line["sku"], 0, -line["quantity"], "release", order_id),
                    )
            conn.execute(
                "UPDATE sales_orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (OrderStatus.CANCELLED, order_id),
            )
        return self.order(order_id)

    def ship_order(self, order_id: str) -> dict:
        with self.store.connect() as conn:
            order = conn.execute("SELECT status FROM sales_orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                raise NotFound(f"订单不存在：{order_id}")
            if order["status"] != OrderStatus.RESERVED:
                raise InvalidTransition(f"只有 reserved 订单可发货，当前为 {order['status']}")
            lines = conn.execute("SELECT sku,quantity FROM sales_order_lines WHERE order_id=?", (order_id,)).fetchall()
            for line in lines:
                conn.execute(
                    "UPDATE stock SET on_hand=on_hand-?,reserved=reserved-? WHERE sku=?",
                    (line["quantity"], line["quantity"], line["sku"]),
                )
                conn.execute(
                    "INSERT INTO inventory_events(event_key,sku,quantity,reserved_delta,event_type,reference) VALUES(?,?,?,?,?,?)",
                    (f"ship:{order_id}:{line['sku']}", line["sku"], -line["quantity"], -line["quantity"], "ship", order_id),
                )
            conn.execute(
                "UPDATE sales_orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (OrderStatus.SHIPPED, order_id),
            )
        return self.order(order_id)

    def propose_purchase(
        self, sku: str, quantity: int, reason: str, request_id: str | None = None,
        supplier_id: str | None = None,
    ) -> dict:
        if quantity <= 0 or not reason.strip():
            raise ValueError("采购数量和原因不能为空")
        sku = sku.upper()
        self.product(sku)
        pid = request_id or f"PR-{uuid.uuid4().hex[:8].upper()}"
        with self.store.connect() as conn:
            if supplier_id and not conn.execute(
                "SELECT 1 FROM suppliers WHERE id=?", (supplier_id,)
            ).fetchone():
                raise NotFound(f"供应商不存在：{supplier_id}")
            conn.execute(
                "INSERT INTO purchase_requests(id,sku,supplier_id,quantity,status,reason) VALUES(?,?,?,?,?,?)",
                (pid, sku, supplier_id, quantity, PurchaseStatus.PROPOSED, reason.strip()),
            )
        return self.purchase(pid)

    def purchase(self, request_id: str) -> dict:
        item = self.store.row("SELECT * FROM purchase_requests WHERE id=?", (request_id,))
        if not item:
            raise NotFound(f"采购建议不存在：{request_id}")
        return item

    def list_purchases(self) -> list[dict]:
        return self.store.rows("SELECT * FROM purchase_requests ORDER BY created_at DESC,id DESC")

    def approve_purchase(self, request_id: str, approved_by: str) -> dict:
        if not approved_by.strip():
            raise ValueError("审批人不能为空")
        with self.store.connect() as conn:
            item = conn.execute("SELECT status FROM purchase_requests WHERE id=?", (request_id,)).fetchone()
            if not item:
                raise NotFound(f"采购建议不存在：{request_id}")
            if item["status"] != PurchaseStatus.PROPOSED:
                raise InvalidTransition(f"只有 proposed 采购可审批，当前为 {item['status']}")
            conn.execute(
                "UPDATE purchase_requests SET status=?,approved_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (PurchaseStatus.APPROVED, approved_by.strip(), request_id),
            )
        return self.purchase(request_id)

    def reject_purchase(self, request_id: str, rejected_by: str) -> dict:
        if not rejected_by.strip():
            raise ValueError("操作人不能为空")
        with self.store.connect() as conn:
            item = conn.execute("SELECT status FROM purchase_requests WHERE id=?", (request_id,)).fetchone()
            if not item:
                raise NotFound(f"采购建议不存在：{request_id}")
            if item["status"] != PurchaseStatus.PROPOSED:
                raise InvalidTransition(f"只有 proposed 采购可驳回，当前为 {item['status']}")
            conn.execute(
                "UPDATE purchase_requests SET status=?,approved_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (PurchaseStatus.REJECTED, rejected_by.strip(), request_id),
            )
        return self.purchase(request_id)

    def dashboard(self) -> dict:
        summary = self.store.row(
            "SELECT COUNT(*) AS product_count,COALESCE(SUM(s.on_hand),0) AS on_hand," 
            "COALESCE(SUM(s.reserved),0) AS reserved,COALESCE(SUM(s.on_hand-s.reserved),0) AS available "
            "FROM products p JOIN stock s USING(sku)"
        ) or {}
        low_stock = self.store.rows(
            "SELECT p.sku,p.name,s.on_hand,s.reserved,s.on_hand-s.reserved AS available,p.reorder_point "
            "FROM products p JOIN stock s USING(sku) WHERE s.on_hand-s.reserved<=p.reorder_point ORDER BY available"
        )
        order_counts = self.store.rows("SELECT status,COUNT(*) AS count FROM sales_orders GROUP BY status")
        purchase_counts = self.store.rows("SELECT status,COUNT(*) AS count FROM purchase_requests GROUP BY status")
        return {**summary, "low_stock": low_stock,
                "orders": {row["status"]: row["count"] for row in order_counts},
                "purchases": {row["status"]: row["count"] for row in purchase_counts}}

    def receive_purchase(self, request_id: str, event_key: str) -> dict:
        item = self.purchase(request_id)
        if item["status"] == PurchaseStatus.RECEIVED:
            existing = self.store.row(
                "SELECT event_key FROM inventory_events WHERE reference=? AND event_type='receive'",
                (request_id,),
            )
            if existing and existing["event_key"] == event_key:
                result = self.receive_stock(item["sku"], item["quantity"], event_key, reference=request_id)
                return {"purchase": item, "stock": result}
            raise InvalidTransition(f"采购 {request_id} 已完成入库")
        if item["status"] != PurchaseStatus.APPROVED:
            raise ApprovalRequired(f"采购 {request_id} 未审批，不允许入库")
        result = self.receive_stock(item["sku"], item["quantity"], event_key, reference=request_id)
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE purchase_requests SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (PurchaseStatus.RECEIVED, request_id),
            )
        return {"purchase": self.purchase(request_id), "stock": result}
