from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, timedelta
from typing import Iterable

from .accounting import InventoryValuationService, LedgerService
from .audit import AuditContext, AuditService
from .identity import Principal
from .models import ApprovalRequired, Conflict, CreditLimitExceeded, InsufficientStock, InvalidTransition, NotFound, ValidationError, calculate_tax, require_positive, validate_iso_date
from .numbering import next_number
from .store import ERPStore


class SalesService:
    """Quotation-to-cash workflow with atomic reservation and partial shipment."""

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)
        self.valuation = InventoryValuationService(store)
        self.ledger = LedgerService(store)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    def create_order(self, principal: Principal, customer_id: str, lines: Iterable[dict],
                     order_date: str | None = None, requested_delivery_date: str | None = None,
                     currency: str = "CNY", freight_cents: int = 0, shipping_address: str = "",
                     billing_address: str = "", channel: str = "direct", external_reference: str = "",
                     notes: str = "", document_type: str = "order") -> dict:
        principal.require("sales.write")
        prepared = list(lines)
        if not prepared:
            raise ValidationError("销售单至少包含一行")
        if document_type not in {"quotation", "order"}:
            raise ValidationError("销售单类型无效")
        order_date = order_date or date.today().isoformat()
        validate_iso_date(order_date, "订单日期")
        if requested_delivery_date:
            validate_iso_date(requested_delivery_date, "要求交期")
            if requested_delivery_date < order_date:
                raise ValidationError("要求交期不能早于订单日期")
        if freight_cents < 0:
            raise ValidationError("运费不能为负")
        currency = currency.strip().upper()
        if len(currency) != 3:
            raise ValidationError("币种必须是三位 ISO 代码")
        document_id = self._id("SAL")
        with self.store.connect() as conn:
            customer = conn.execute(
                "SELECT * FROM customer_master WHERE id=? AND organization_id=? AND status='active'",
                (customer_id, principal.organization_id),
            ).fetchone()
            if not customer:
                raise NotFound(f"有效客户不存在：{customer_id}")
            if customer["currency"] != currency:
                raise ValidationError(f"订单币种必须与客户币种一致：{customer['currency']}")
            number = next_number(conn, principal.organization_id, "quotation" if document_type == "quotation" else "sales_order")
            calculated = self._prepare_lines(conn, principal.organization_id, prepared)
            subtotal = sum(item["net_cents"] for item in calculated)
            discount = sum(item["discount_cents"] for item in calculated)
            tax = sum(item["tax_cents"] for item in calculated)
            total = subtotal + tax + freight_cents
            stored_external_reference = external_reference.strip() or f"__internal__:{document_id}"
            try:
                conn.execute(
                    "INSERT INTO sales_documents(id,organization_id,document_number,document_type,customer_id,status,order_date,requested_delivery_date,currency,subtotal_cents,discount_cents,tax_cents,freight_cents,total_cents,shipping_address,billing_address,channel,external_reference,notes,created_by) "
                    "VALUES(?,?,?,?,?,'draft',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (document_id, principal.organization_id, number, document_type, customer_id, order_date,
                     requested_delivery_date, currency, subtotal, discount, tax, freight_cents, total,
                     shipping_address.strip() or customer["shipping_address"], billing_address.strip() or customer["billing_address"],
                     channel.strip() or "direct", stored_external_reference, notes.strip(), principal.user_id),
                )
            except sqlite3.IntegrityError as exc:
                if external_reference:
                    raise ValidationError("该渠道外部订单号已存在") from exc
                raise
            for index, item in enumerate(calculated, 1):
                conn.execute(
                    "INSERT INTO sales_document_lines(id,document_id,line_number,product_id,description,ordered_quantity,unit_price_cents,discount_basis_points,tax_rate_basis_points,net_cents,tax_cents,total_cents,warehouse_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], document_id, index, item["product_id"], item["description"], item["quantity"],
                     item["unit_price_cents"], item["discount_basis_points"], item["tax_rate_basis_points"],
                     item["net_cents"], item["tax_cents"], item["total_cents"], item["warehouse_id"]),
                )
            self.audit.record(conn, AuditContext(principal), "sales.create", "sales_document", document_id,
                              after={"document_number": number, "type": document_type, "total_cents": total, "line_count": len(calculated)})
        return self.order(principal, document_id)

    @staticmethod
    def _prepare_lines(conn: sqlite3.Connection, organization_id: str, lines: list[dict]) -> list[dict]:
        result: list[dict] = []
        seen: set[str] = set()
        for raw in lines:
            product_id = str(raw["product_id"])
            if product_id in seen:
                raise ValidationError("同一销售单不能重复商品，请合并数量")
            seen.add(product_id)
            product = conn.execute(
                "SELECT * FROM product_master WHERE id=? AND organization_id=? AND active=1 AND saleable=1",
                (product_id, organization_id),
            ).fetchone()
            if not product:
                raise NotFound(f"可销售商品不存在：{product_id}")
            quantity = int(raw["quantity"]); require_positive(quantity)
            unit_price = int(raw.get("unit_price_cents", product["sales_price_cents"]))
            if unit_price < 0:
                raise ValidationError("销售单价不能为负")
            discount_bp = int(raw.get("discount_basis_points", 0))
            if not 0 <= discount_bp <= 10000:
                raise ValidationError("折扣率必须在 0 到 100% 之间")
            tax_bp = int(raw.get("tax_rate_basis_points", product["tax_rate_basis_points"]))
            gross = quantity * unit_price
            discount = calculate_tax(gross, discount_bp)
            net = gross - discount
            tax = calculate_tax(net, tax_bp)
            warehouse_id = str(raw.get("warehouse_id") or "").strip()
            if warehouse_id:
                warehouse = conn.execute(
                    "SELECT s.id FROM sites s WHERE s.id=? AND s.organization_id=? AND s.active=1 "
                    "AND EXISTS(SELECT 1 FROM storage_locations l WHERE l.site_id=s.id AND l.active=1 AND l.location_type='internal')",
                    (warehouse_id, organization_id),
                ).fetchone()
                if not warehouse:
                    raise ValidationError(f"发货仓库不存在或没有可用库位：{warehouse_id}")
            else:
                warehouses = conn.execute(
                    "SELECT s.id FROM sites s WHERE s.organization_id=? AND s.active=1 "
                    "AND EXISTS(SELECT 1 FROM storage_locations l WHERE l.site_id=s.id AND l.active=1 AND l.location_type='internal') "
                    "ORDER BY s.code LIMIT 2",
                    (organization_id,),
                ).fetchall()
                if len(warehouses) != 1:
                    raise ValidationError("销售明细必须指定发货仓库")
                warehouse_id = str(warehouses[0]["id"])
            result.append({
                "id": f"SOL-{uuid.uuid4().hex.upper()}", "product_id": product_id,
                "description": str(raw.get("description", product["name"])).strip(), "quantity": quantity,
                "unit_price_cents": unit_price, "discount_basis_points": discount_bp, "discount_cents": discount,
                "tax_rate_basis_points": tax_bp, "net_cents": net, "tax_cents": tax, "total_cents": net + tax,
                "warehouse_id": warehouse_id,
            })
        return result

    def order(self, principal: Principal, document_id: str) -> dict:
        principal.require("sales.read")
        header = self.store.row(
            "SELECT d.*,CASE WHEN d.external_reference LIKE '__internal__:%' THEN '' ELSE d.external_reference END AS external_reference," 
            "c.code AS customer_code,c.name AS customer_name,c.contact_name,c.phone FROM sales_documents d "
            "JOIN customer_master c ON c.id=d.customer_id WHERE d.id=? AND d.organization_id=?",
            (document_id, principal.organization_id),
        )
        if not header:
            raise NotFound(f"销售单不存在：{document_id}")
        header["lines"] = self.store.rows(
            "SELECT l.*,p.sku,p.name AS product_name,p.tracking FROM sales_document_lines l JOIN product_master p ON p.id=l.product_id "
            "WHERE l.document_id=? ORDER BY l.line_number", (document_id,),
        )
        header["shipments"] = self.store.rows(
            "SELECT id,shipment_number,status,carrier,tracking_number,shipped_at,created_at FROM shipments WHERE sales_document_id=? ORDER BY created_at",
            (document_id,),
        )
        header["fulfillment_allocations"] = self.store.rows(
            "SELECT sl.sales_line_id,sl.product_id,sl.lot_id,COALESCE(lot.lot_number,'') AS lot_number," 
            "p.sku,p.name AS product_name,p.tracking,SUM(sl.quantity) AS shipped_quantity "
            "FROM shipment_lines sl JOIN shipments s ON s.id=sl.shipment_id JOIN product_master p ON p.id=sl.product_id "
            "LEFT JOIN stock_lots lot ON lot.id=sl.lot_id WHERE s.sales_document_id=? AND s.status='shipped' "
            "GROUP BY sl.sales_line_id,sl.product_id,sl.lot_id ORDER BY p.sku,lot.lot_number",
            (document_id,),
        )
        for allocation in header["fulfillment_allocations"]:
            allocation["serials"] = self.store.rows(
                "SELECT DISTINCT sn.id,sn.serial_number,sn.status FROM shipment_serials ss "
                "JOIN shipment_lines sl ON sl.id=ss.shipment_line_id JOIN shipments s ON s.id=sl.shipment_id "
                "JOIN serial_numbers sn ON sn.id=ss.serial_number_id WHERE s.sales_document_id=? AND s.status='shipped' "
                "AND sl.sales_line_id=? AND sl.lot_id=? AND ss.status='shipped' ORDER BY sn.serial_number",
                (document_id, allocation["sales_line_id"], allocation["lot_id"]),
            )
        header["reservation_allocations"] = self.store.rows(
            "SELECT r.id AS reservation_id,r.reference_line_id AS sales_line_id,r.product_id,r.location_id,r.lot_id,r.quantity," 
            "p.sku,p.name AS product_name,p.tracking,loc.code AS location_code,COALESCE(lot.lot_number,'') AS lot_number "
            "FROM stock_reservations r JOIN product_master p ON p.id=r.product_id "
            "JOIN storage_locations loc ON loc.id=r.location_id LEFT JOIN stock_lots lot ON lot.id=r.lot_id "
            "WHERE r.organization_id=? AND r.reference_id=? AND r.reference_type='sales_order' AND r.status='active' "
            "AND r.claimed_by_shipment_id IS NULL ORDER BY p.sku,lot.lot_number",
            (principal.organization_id, document_id),
        )
        for allocation in header["reservation_allocations"]:
            allocation["serials"] = self.store.rows(
                "SELECT id,serial_number FROM serial_numbers WHERE organization_id=? AND product_id=? "
                "AND current_location_id=? AND COALESCE(lot_id,'')=? AND status='available' ORDER BY serial_number",
                (principal.organization_id, allocation["product_id"], allocation["location_id"], allocation["lot_id"]),
            )
        return header

    def list_orders(self, principal: Principal, status: str = "", customer_id: str = "",
                    since: str = "", until: str = "", limit: int = 100, offset: int = 0) -> list[dict]:
        principal.require("sales.read")
        filters = ["d.organization_id=?"]
        params: list[object] = [principal.organization_id]
        for column, value in (("d.status", status), ("d.customer_id", customer_id)):
            if value:
                filters.append(f"{column}=?"); params.append(value)
        if since:
            filters.append("d.order_date>=?"); params.append(validate_iso_date(since, "开始日期"))
        if until:
            filters.append("d.order_date<=?"); params.append(validate_iso_date(until, "结束日期"))
        params.extend((max(1, min(limit, 500)), max(0, offset)))
        return self.store.rows(
            f"SELECT d.*,CASE WHEN d.external_reference LIKE '__internal__:%' THEN '' ELSE d.external_reference END AS external_reference," 
            f"c.code AS customer_code,c.name AS customer_name FROM sales_documents d JOIN customer_master c ON c.id=d.customer_id "
            f"WHERE {' AND '.join(filters)} ORDER BY d.order_date DESC,d.document_number DESC LIMIT ? OFFSET ?", tuple(params),
        )

    def update_draft(self, principal: Principal, document_id: str, version: int, lines: Iterable[dict] | None = None,
                     requested_delivery_date: str | None = None, freight_cents: int | None = None,
                     shipping_address: str | None = None, notes: str | None = None) -> dict:
        principal.require("sales.write")
        before = self.order(principal, document_id)
        if before["status"] != "draft":
            raise InvalidTransition("只有草稿销售单可以修改")
        with self.store.connect() as conn:
            current = conn.execute("SELECT * FROM sales_documents WHERE id=? AND version=?", (document_id, version)).fetchone()
            if not current:
                raise Conflict("销售单已被其他用户修改，请刷新后重试")
            updates: dict[str, object] = {}
            if requested_delivery_date is not None:
                if requested_delivery_date:
                    validate_iso_date(requested_delivery_date, "要求交期")
                updates["requested_delivery_date"] = requested_delivery_date or None
            if freight_cents is not None:
                if freight_cents < 0: raise ValidationError("运费不能为负")
                updates["freight_cents"] = freight_cents
            if shipping_address is not None: updates["shipping_address"] = shipping_address.strip()
            if notes is not None: updates["notes"] = notes.strip()
            if lines is not None:
                prepared = self._prepare_lines(conn, principal.organization_id, list(lines))
                if not prepared: raise ValidationError("销售单至少包含一行")
                conn.execute("DELETE FROM sales_document_lines WHERE document_id=?", (document_id,))
                for index, item in enumerate(prepared, 1):
                    conn.execute(
                        "INSERT INTO sales_document_lines(id,document_id,line_number,product_id,description,ordered_quantity,unit_price_cents,discount_basis_points,tax_rate_basis_points,net_cents,tax_cents,total_cents,warehouse_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (item["id"], document_id, index, item["product_id"], item["description"], item["quantity"], item["unit_price_cents"],
                         item["discount_basis_points"], item["tax_rate_basis_points"], item["net_cents"], item["tax_cents"], item["total_cents"], item["warehouse_id"]),
                    )
                updates.update(subtotal_cents=sum(i["net_cents"] for i in prepared), discount_cents=sum(i["discount_cents"] for i in prepared),
                               tax_cents=sum(i["tax_cents"] for i in prepared))
            if updates:
                total = int(updates.get("subtotal_cents", current["subtotal_cents"])) + int(updates.get("tax_cents", current["tax_cents"])) + int(updates.get("freight_cents", current["freight_cents"]))
                updates["total_cents"] = total
                sql = ",".join(f"{key}=?" for key in updates)
                cursor = conn.execute(f"UPDATE sales_documents SET {sql},updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=? AND version=?", (*updates.values(), document_id, version))
                if cursor.rowcount != 1: raise Conflict("销售单已被其他用户修改")
            self.audit.record(conn, AuditContext(principal), "sales.update", "sales_document", document_id, before=before, after=updates)
        return self.order(principal, document_id)

    def confirm(self, principal: Principal, document_id: str) -> dict:
        principal.require("sales.confirm")
        with self.store.connect() as conn:
            header = conn.execute("SELECT * FROM sales_documents WHERE id=? AND organization_id=?", (document_id, principal.organization_id)).fetchone()
            if not header: raise NotFound(f"销售单不存在：{document_id}")
            if header["status"] != "draft": raise InvalidTransition(f"只有草稿可确认，当前为 {header['status']}")
            customer = conn.execute("SELECT * FROM customer_master WHERE id=?", (header["customer_id"],)).fetchone()
            exposure = conn.execute(
                "SELECT COALESCE(SUM(outstanding_cents),0) FROM invoices WHERE organization_id=? AND partner_type='customer' AND partner_id=? AND status IN ('issued','partially_paid')",
                (principal.organization_id, header["customer_id"]),
            ).fetchone()[0]
            if customer["credit_limit_cents"] and exposure + header["total_cents"] > customer["credit_limit_cents"]:
                raise CreditLimitExceeded(f"客户信用额度不足：额度 {customer['credit_limit_cents']}，占用 {exposure}，本单 {header['total_cents']}")
            conn.execute("UPDATE sales_documents SET status='confirmed',confirmed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (document_id,))
            self.audit.record(conn, AuditContext(principal), "sales.confirm", "sales_document", document_id,
                              before={"status": "draft"}, after={"status": "confirmed"})
        return self.order(principal, document_id)

    def reserve(self, principal: Principal, document_id: str, allocations: dict[str, list[dict]] | None = None) -> dict:
        """Reserve every remaining line in one transaction; any shortage rolls all back."""
        principal.require("sales.confirm")
        with self.store.connect() as conn:
            header = conn.execute("SELECT * FROM sales_documents WHERE id=? AND organization_id=?", (document_id, principal.organization_id)).fetchone()
            if not header: raise NotFound(f"销售单不存在：{document_id}")
            if header["status"] not in {"confirmed", "reserved"}: raise InvalidTransition(f"订单状态不允许预占：{header['status']}")
            lines = conn.execute("SELECT l.*,p.sku FROM sales_document_lines l JOIN product_master p ON p.id=l.product_id WHERE l.document_id=? ORDER BY l.line_number", (document_id,)).fetchall()
            planned: list[tuple[sqlite3.Row, str, str, int]] = []
            for line in lines:
                needed = line["ordered_quantity"] - line["shipped_quantity"] - line["reserved_quantity"]
                if needed <= 0: continue
                requested = (allocations or {}).get(line["id"], [])
                if requested:
                    if sum(int(item["quantity"]) for item in requested) != needed:
                        raise ValidationError("指定批次分配数量必须等于待预占数量")
                    candidates = [(str(i["location_id"]), str(i.get("lot_id", "")), int(i["quantity"])) for i in requested]
                else:
                    candidates = self._fefo_allocate(conn, principal.organization_id, line["product_id"], line["warehouse_id"], needed)
                total = sum(item[2] for item in candidates)
                if total < needed:
                    raise InsufficientStock(line["sku"], needed, total)
                planned.extend((line, location_id, lot_id, quantity) for location_id, lot_id, quantity in candidates)
            for line, location_id, lot_id, quantity in planned:
                balance = conn.execute(
                    "SELECT on_hand-reserved AS available FROM stock_balance WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=?",
                    (principal.organization_id, line["product_id"], location_id, lot_id),
                ).fetchone()
                available = balance["available"] if balance else 0
                cursor = conn.execute(
                    "UPDATE stock_balance SET reserved=reserved+?,outgoing=outgoing+?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                    "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=? AND on_hand-reserved>=?",
                    (quantity, quantity, principal.organization_id, line["product_id"], location_id, lot_id, quantity),
                )
                if cursor.rowcount != 1: raise InsufficientStock(line["sku"], quantity, available)
                conn.execute(
                    "INSERT INTO stock_reservations(id,organization_id,product_id,location_id,lot_id,reference_type,reference_id,reference_line_id,quantity) VALUES(?,?,?,?,?,'sales_order',?,?,?)",
                    (self._id("RSV"), principal.organization_id, line["product_id"], location_id, lot_id, document_id, line["id"], quantity),
                )
                conn.execute("UPDATE sales_document_lines SET reserved_quantity=reserved_quantity+? WHERE id=?", (quantity, line["id"]))
            remaining = conn.execute("SELECT COALESCE(SUM(ordered_quantity-shipped_quantity-reserved_quantity),0) FROM sales_document_lines WHERE document_id=?", (document_id,)).fetchone()[0]
            status = "reserved" if remaining == 0 else "confirmed"
            conn.execute("UPDATE sales_documents SET status=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (status, document_id))
            self.audit.record(conn, AuditContext(principal), "sales.reserve", "sales_document", document_id,
                              after={"status": status, "allocations": len(planned)})
        return self.order(principal, document_id)

    @staticmethod
    def _fefo_allocate(conn: sqlite3.Connection, organization_id: str, product_id: str, warehouse_id: str, needed: int) -> list[tuple[str, str, int]]:
        rows = conn.execute(
            "SELECT b.location_id,b.lot_id,b.on_hand-b.reserved AS available,COALESCE(lot.expiry_date,'9999-12-31') AS expiry_date "
            "FROM stock_balance b JOIN storage_locations loc ON loc.id=b.location_id "
            "LEFT JOIN stock_lots lot ON lot.id=b.lot_id WHERE b.organization_id=? AND b.product_id=? AND loc.site_id=? "
            "AND loc.location_type='internal' AND b.on_hand-b.reserved>0 AND (lot.status IS NULL OR lot.status='active') "
            "ORDER BY expiry_date,b.updated_at,b.location_id",
            (organization_id, product_id, warehouse_id),
        ).fetchall()
        allocated: list[tuple[str, str, int]] = []
        remaining = needed
        for row in rows:
            amount = min(remaining, row["available"])
            if amount > 0:
                allocated.append((row["location_id"], row["lot_id"], amount)); remaining -= amount
            if remaining == 0: break
        return allocated

    def create_shipment(self, principal: Principal, document_id: str, lines: Iterable[dict] | None = None,
                        carrier: str = "", tracking_number: str = "") -> dict:
        principal.require("inventory.ship")
        with self.store.connect() as conn:
            header = conn.execute("SELECT * FROM sales_documents WHERE id=? AND organization_id=?", (document_id, principal.organization_id)).fetchone()
            if not header:
                raise NotFound(f"销售订单不存在：{document_id}")
            if header["status"] not in {"reserved", "partially_shipped"}:
                raise InvalidTransition(f"订单状态不允许创建发货单：{header['status']}")
            active = conn.execute(
                "SELECT r.*,l.id AS sales_line_id,p.tracking FROM stock_reservations r "
                "JOIN sales_document_lines l ON l.id=r.reference_line_id JOIN product_master p ON p.id=r.product_id "
                "WHERE r.organization_id=? AND r.reference_id=? AND r.status='active' "
                "AND r.claimed_by_shipment_id IS NULL ORDER BY l.line_number,r.created_at",
                (principal.organization_id, document_id),
            ).fetchall()
            active_by_id = {row["id"]: row for row in active}
            if lines is None:
                requested = {row["id"]: {"quantity": int(row["quantity"]), "serial_ids": []} for row in active}
            else:
                requested = {}
                for item in lines:
                    reservation_id = str(item.get("reservation_id", "")).strip()
                    if not reservation_id:
                        raise ValidationError("发货明细必须指定 reservation_id")
                    if reservation_id in requested:
                        raise ValidationError(f"发货明细重复引用预占：{reservation_id}")
                    requested[reservation_id] = {
                        "quantity": int(item.get("quantity", 0)),
                        "serial_ids": [str(value).strip() for value in item.get("serial_ids", [])],
                    }
            unknown = set(requested) - set(active_by_id)
            if unknown:
                raise Conflict(f"预占不存在、已失效或已被其他发货单占用：{sorted(unknown)[0]}")
            selected: list[tuple[sqlite3.Row, int, list[str]]] = []
            for reservation_id, request in requested.items():
                row = active_by_id[reservation_id]
                quantity = int(request["quantity"])
                serial_ids = request["serial_ids"]
                require_positive(quantity)
                if quantity > int(row["quantity"]):
                    raise ValidationError(f"发货数量超过预占数量：{reservation_id}")
                if row["tracking"] == "serial":
                    if len(serial_ids) != quantity or len(serial_ids) != len(set(serial_ids)):
                        raise ValidationError("序列号商品必须按发货数量提供不重复的 serial_ids")
                elif serial_ids:
                    raise ValidationError("非序列号商品不能指定 serial_ids")
                selected.append((row, quantity, serial_ids))
            if not selected:
                raise ValidationError("发货单没有可出库的预占")
            shipment_id = self._id("SHP"); number = next_number(conn, principal.organization_id, "shipment")
            location_ids = {row["location_id"] for row, _, _ in selected}
            if len(location_ids) != 1:
                raise ValidationError("一张发货单只能从一个库位发货，请按库位拆单")
            conn.execute(
                "INSERT INTO shipments(id,organization_id,shipment_number,sales_document_id,location_id,status,carrier,tracking_number,shipping_address,created_by) VALUES(?,?,?,?,?,'draft',?,?,?,?)",
                (shipment_id, principal.organization_id, number, document_id, next(iter(location_ids)), carrier.strip(), tracking_number.strip(), header["shipping_address"], principal.user_id),
            )
            for row, quantity, serial_ids in selected:
                claimed = conn.execute(
                    "UPDATE stock_reservations SET claimed_by_shipment_id=? "
                    "WHERE id=? AND organization_id=? AND status='active' AND claimed_by_shipment_id IS NULL",
                    (shipment_id, row["id"], principal.organization_id),
                )
                if claimed.rowcount != 1:
                    raise Conflict(f"预占已被其他发货单占用：{row['id']}")
                shipment_line_id = self._id("SHL")
                conn.execute(
                    "INSERT INTO shipment_lines(id,shipment_id,sales_line_id,product_id,lot_id,quantity,reservation_id) VALUES(?,?,?,?,?,?,?)",
                    (shipment_line_id, shipment_id, row["sales_line_id"], row["product_id"], row["lot_id"], quantity, row["id"]),
                )
                for serial_id in serial_ids:
                    serial = conn.execute(
                        "SELECT * FROM serial_numbers WHERE id=? AND organization_id=? AND product_id=? "
                        "AND current_location_id=? AND COALESCE(lot_id,'')=?",
                        (serial_id, principal.organization_id, row["product_id"], row["location_id"], row["lot_id"]),
                    ).fetchone()
                    if not serial or serial["status"] != "available":
                        raise Conflict(f"序列号不可用或与发货批次/库位不一致：{serial_id}")
                    claimed_serial = conn.execute(
                        "UPDATE serial_numbers SET status='reserved',updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND status='available' AND current_location_id=?",
                        (serial_id, row["location_id"]),
                    )
                    if claimed_serial.rowcount != 1:
                        raise Conflict(f"序列号已被其他发货单占用：{serial_id}")
                    conn.execute(
                        "INSERT INTO shipment_serials(shipment_line_id,serial_number_id) VALUES(?,?)",
                        (shipment_line_id, serial_id),
                    )
            self.audit.record(conn, AuditContext(principal), "shipment.create", "shipment", shipment_id,
                              after={"shipment_number": number, "sales_document_id": document_id, "line_count": len(selected)})
        return self.shipment(principal, shipment_id)

    def post_shipment(self, principal: Principal, shipment_id: str, event_key: str) -> dict:
        principal.require("inventory.ship")
        if not event_key.strip(): raise ValidationError("出库幂等键不能为空")
        with self.store.connect() as conn:
            shipment = conn.execute("SELECT * FROM shipments WHERE id=? AND organization_id=?", (shipment_id, principal.organization_id)).fetchone()
            if not shipment:
                raise NotFound(f"发货单不存在：{shipment_id}")
            if shipment["status"] == "shipped": return self.shipment(principal, shipment_id)
            if shipment["status"] not in {"draft", "picked", "packed"}:
                raise InvalidTransition(f"发货单状态不允许过账：{shipment['status']}")
            rows = conn.execute(
                "SELECT sl.*,r.location_id,r.quantity AS reservation_quantity,r.status AS reservation_status,p.tracking," 
                "r.organization_id AS reservation_organization_id,r.reference_type,r.reference_id,r.reference_line_id "
                "FROM shipment_lines sl JOIN stock_reservations r ON r.id=sl.reservation_id JOIN product_master p ON p.id=sl.product_id "
                "WHERE sl.shipment_id=?",
                (shipment_id,),
            ).fetchall()
            line_count = conn.execute("SELECT COUNT(*) FROM shipment_lines WHERE shipment_id=?", (shipment_id,)).fetchone()[0]
            if not rows or len(rows) != line_count:
                raise Conflict("发货明细缺少可追溯的库存预占")
            for row in rows:
                if (row["reservation_status"] != "active" or
                        row["reservation_organization_id"] != principal.organization_id):
                    raise Conflict("发货引用的库存预占已失效")
                if int(row["quantity"]) > int(row["reservation_quantity"]):
                    raise Conflict("发货数量超过当前预占数量")
                serial_rows = conn.execute(
                    "SELECT s.* FROM shipment_serials ss JOIN serial_numbers s ON s.id=ss.serial_number_id "
                    "WHERE ss.shipment_line_id=?",
                    (row["id"],),
                ).fetchall()
                if row["tracking"] == "serial" and len(serial_rows) != int(row["quantity"]):
                    raise Conflict("发货单的序列号数量与发货数量不一致")
                if any(serial["status"] != "reserved" or serial["current_location_id"] != row["location_id"] for serial in serial_rows):
                    raise Conflict("发货单序列号状态或库位已变更")
                cursor = conn.execute(
                    "UPDATE stock_balance SET on_hand=on_hand-?,reserved=reserved-?,outgoing=outgoing-?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                    "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=? AND on_hand>=? AND reserved>=? AND outgoing>=?",
                    (row["quantity"], row["quantity"], row["quantity"], principal.organization_id,
                     row["product_id"], row["location_id"], row["lot_id"],
                     row["quantity"], row["quantity"], row["quantity"]),
                )
                if cursor.rowcount != 1:
                    raise Conflict("库存与预占不一致，发货被阻断")
                residual = int(row["reservation_quantity"]) - int(row["quantity"])
                if residual:
                    reservation_update = conn.execute(
                        "UPDATE stock_reservations SET quantity=?,claimed_by_shipment_id=NULL "
                        "WHERE id=? AND status='active' AND claimed_by_shipment_id=? AND quantity=?",
                        (residual, row["reservation_id"], shipment_id, row["reservation_quantity"]),
                    )
                else:
                    reservation_update = conn.execute(
                        "UPDATE stock_reservations SET status='consumed',released_at=CURRENT_TIMESTAMP,claimed_by_shipment_id=NULL "
                        "WHERE id=? AND status='active' AND claimed_by_shipment_id=? AND quantity=?",
                        (row["reservation_id"], shipment_id, row["reservation_quantity"]),
                    )
                if reservation_update.rowcount != 1:
                    raise Conflict("预占在发货过程中被并发修改")
                conn.execute("UPDATE sales_document_lines SET reserved_quantity=reserved_quantity-?,shipped_quantity=shipped_quantity+? WHERE id=?", (row["quantity"], row["quantity"], row["sales_line_id"]))
                for serial in serial_rows:
                    conn.execute(
                        "UPDATE serial_numbers SET status='shipped',current_location_id=NULL,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND status='reserved'",
                        (serial["id"],),
                    )
                conn.execute("UPDATE shipment_serials SET status='shipped' WHERE shipment_line_id=?", (row["id"],))
                move_id = self._id("MOV")
                conn.execute(
                    "INSERT INTO stock_moves(id,organization_id,event_key,product_id,source_location_id,lot_id,quantity,move_type,reference_type,reference_id,created_by) VALUES(?,?,?,?,?,?,?,'shipment','shipment',?,?)",
                    (move_id, principal.organization_id, f"{event_key}:{row['id']}", row["product_id"], row["location_id"], row["lot_id"], row["quantity"], shipment_id, principal.user_id),
                )
                valued = self.valuation.consume(
                    conn, principal.organization_id, move_id, row["product_id"], row["location_id"],
                    row["lot_id"], row["quantity"],
                )
                if valued["value_cents"]:
                    self.ledger.post(
                        conn, principal, "inventory", date.today().isoformat(), "shipment", shipment_id,
                        f"shipment-cost:{move_id}", f"销售出库 {shipment['shipment_number']}",
                        [{"account_code": "6001", "debit_cents": valued["value_cents"], "product_id": row["product_id"]},
                         {"account_code": "1405", "credit_cents": valued["value_cents"], "product_id": row["product_id"],
                          "location_id": row["location_id"], "lot_id": row["lot_id"]}],
                    )
            remaining = conn.execute("SELECT SUM(ordered_quantity-shipped_quantity) FROM sales_document_lines WHERE document_id=?", (shipment["sales_document_id"],)).fetchone()[0]
            order_status = "shipped" if remaining == 0 else "partially_shipped"
            conn.execute("UPDATE shipments SET status='shipped',shipped_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (shipment_id,))
            conn.execute("UPDATE sales_documents SET status=?,shipped_at=CASE WHEN ?='shipped' THEN CURRENT_TIMESTAMP ELSE shipped_at END,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (order_status, order_status, shipment["sales_document_id"]))
            self._outbox(conn, principal.organization_id, "shipment.posted", "shipment", shipment_id, {"sales_document_id": shipment["sales_document_id"], "status": order_status})
            channel_order = conn.execute(
                "SELECT o.id,o.shop_id,o.external_order_id FROM channel_orders o WHERE o.sales_document_id=?",
                (shipment["sales_document_id"],),
            ).fetchone()
            if channel_order:
                callback_payload = json.dumps({
                    "external_order_id": channel_order["external_order_id"],
                    "shipment_id": shipment_id,
                    "carrier": shipment["carrier"],
                    "tracking_number": shipment["tracking_number"],
                    "status": order_status,
                }, ensure_ascii=False, sort_keys=True)
                conn.execute(
                    "INSERT OR IGNORE INTO channel_callback_tasks(id,organization_id,shop_id,channel_order_id,task_type,source_type,source_id,payload_json) "
                    "VALUES(?,?,?,?,?,'shipment',?,?)",
                    (self._id("CHCB"), principal.organization_id, channel_order["shop_id"], channel_order["id"],
                     "shipment", shipment_id, callback_payload),
                )
            self.audit.record(conn, AuditContext(principal), "shipment.post", "shipment", shipment_id,
                              before={"status": shipment["status"]}, after={"status": "shipped"})
        return self.shipment(principal, shipment_id)

    def cancel_shipment(self, principal: Principal, shipment_id: str, reason: str) -> dict:
        principal.require("inventory.ship")
        if not reason.strip():
            raise ValidationError("取消发货单必须填写原因")
        with self.store.connect() as conn:
            shipment = conn.execute(
                "SELECT * FROM shipments WHERE id=? AND organization_id=?",
                (shipment_id, principal.organization_id),
            ).fetchone()
            if not shipment:
                raise NotFound(f"发货单不存在：{shipment_id}")
            if shipment["status"] == "cancelled":
                return self.shipment(principal, shipment_id)
            if shipment["status"] not in {"draft", "picked", "packed"}:
                raise InvalidTransition(f"发货单状态不允许取消：{shipment['status']}")
            conn.execute(
                "UPDATE stock_reservations SET claimed_by_shipment_id=NULL "
                "WHERE organization_id=? AND claimed_by_shipment_id=? AND status='active'",
                (principal.organization_id, shipment_id),
            )
            conn.execute(
                "UPDATE serial_numbers SET status='available',updated_at=CURRENT_TIMESTAMP "
                "WHERE id IN (SELECT ss.serial_number_id FROM shipment_serials ss "
                "JOIN shipment_lines sl ON sl.id=ss.shipment_line_id WHERE sl.shipment_id=?) AND status='reserved'",
                (shipment_id,),
            )
            conn.execute(
                "UPDATE shipment_serials SET status='released' WHERE shipment_line_id IN "
                "(SELECT id FROM shipment_lines WHERE shipment_id=?) AND status='claimed'",
                (shipment_id,),
            )
            conn.execute(
                "UPDATE shipments SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (shipment_id,),
            )
            self.audit.record(
                conn, AuditContext(principal), "shipment.cancel", "shipment", shipment_id,
                before={"status": shipment["status"]}, after={"status": "cancelled", "reason": reason.strip()},
            )
        return self.shipment(principal, shipment_id)

    def cancel(self, principal: Principal, document_id: str, reason: str) -> dict:
        principal.require("sales.cancel")
        if not reason.strip(): raise ValidationError("取消原因不能为空")
        with self.store.connect() as conn:
            header = conn.execute("SELECT * FROM sales_documents WHERE id=? AND organization_id=?", (document_id, principal.organization_id)).fetchone()
            if not header: raise NotFound(f"销售单不存在：{document_id}")
            if header["status"] not in {"draft", "confirmed", "reserved"}: raise InvalidTransition(f"订单状态不允许取消：{header['status']}")
            reservations = conn.execute("SELECT * FROM stock_reservations WHERE reference_id=? AND status='active'", (document_id,)).fetchall()
            if any(row["claimed_by_shipment_id"] for row in reservations):
                raise Conflict("订单存在待处理发货单，请先取消发货单")
            for row in reservations:
                cursor = conn.execute(
                    "UPDATE stock_balance SET reserved=reserved-?,outgoing=outgoing-?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                    "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=? AND reserved>=? AND outgoing>=?",
                    (row["quantity"], row["quantity"], principal.organization_id, row["product_id"],
                     row["location_id"], row["lot_id"], row["quantity"], row["quantity"]),
                )
                if cursor.rowcount != 1: raise Conflict("库存预占账不一致，取消被阻断")
                conn.execute("UPDATE stock_reservations SET status='released',released_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
                conn.execute("UPDATE sales_document_lines SET reserved_quantity=reserved_quantity-? WHERE id=?", (row["quantity"], row["reference_line_id"]))
            conn.execute("UPDATE sales_documents SET status='cancelled',cancellation_reason=?,cancelled_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (reason.strip(), document_id))
            self.audit.record(conn, AuditContext(principal), "sales.cancel", "sales_document", document_id,
                              before={"status": header["status"]}, after={"status": "cancelled", "reason": reason})
        return self.order(principal, document_id)

    def shipment(self, principal: Principal, shipment_id: str) -> dict:
        principal.require("sales.read")
        header = self.store.row("SELECT * FROM shipments WHERE id=? AND organization_id=?", (shipment_id, principal.organization_id))
        if not header: raise NotFound(f"发货单不存在：{shipment_id}")
        header["lines"] = self.store.rows(
            "SELECT l.*,p.sku,p.name AS product_name FROM shipment_lines l JOIN product_master p ON p.id=l.product_id WHERE l.shipment_id=?",
            (shipment_id,),
        )
        for line in header["lines"]:
            line["serials"] = self.store.rows(
                "SELECT s.id,s.serial_number,s.status FROM shipment_serials ss JOIN serial_numbers s ON s.id=ss.serial_number_id "
                "WHERE ss.shipment_line_id=? ORDER BY s.serial_number",
                (line["id"],),
            )
        return header

    def create_return(self, principal: Principal, document_id: str, reason_code: str, lines: Iterable[dict],
                      reason_detail: str = "", resolution: str = "refund") -> dict:
        principal.require("sales.write")
        prepared = list(lines)
        if not prepared: raise ValidationError("退货申请至少包含一行")
        if resolution not in {"refund", "replace", "credit", "repair"}: raise ValidationError("退货处理方式无效")
        return_id = self._id("RET")
        with self.store.connect() as conn:
            order = conn.execute("SELECT * FROM sales_documents WHERE id=? AND organization_id=?", (document_id, principal.organization_id)).fetchone()
            if not order: raise NotFound(f"原销售单不存在：{document_id}")
            if order["status"] not in {"partially_shipped", "shipped", "returned"}: raise InvalidTransition("只有已发货订单可以退货")
            number = next_number(conn, principal.organization_id, "sales_return")
            conn.execute(
                "INSERT INTO sales_returns(id,organization_id,return_number,sales_document_id,status,reason_code,reason_detail,resolution,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                (return_id, principal.organization_id, number, document_id, "draft", reason_code.strip(), reason_detail.strip(), resolution, principal.user_id),
            )
            for item in prepared:
                sales_line_id = str(item["sales_line_id"]); quantity = int(item["quantity"]); require_positive(quantity)
                line = conn.execute(
                    "SELECT l.*,p.tracking FROM sales_document_lines l JOIN product_master p ON p.id=l.product_id "
                    "WHERE l.id=? AND l.document_id=?",
                    (sales_line_id, document_id),
                ).fetchone()
                if not line: raise NotFound(f"销售明细不存在：{sales_line_id}")
                lot_id = str(item.get("lot_id", "")).strip()
                if line["tracking"] != "none" and not lot_id:
                    raise ValidationError("批次/序列号商品退货必须指定原发货批次")
                shipped_for_lot = conn.execute(
                    "SELECT COALESCE(SUM(sl.quantity),0) FROM shipment_lines sl JOIN shipments s ON s.id=sl.shipment_id "
                    "WHERE s.sales_document_id=? AND s.status='shipped' AND sl.sales_line_id=? AND sl.lot_id=?",
                    (document_id, sales_line_id, lot_id),
                ).fetchone()[0]
                pending = conn.execute(
                    "SELECT COALESCE(SUM(rl.quantity),0) AS quantity,COALESCE(SUM(rl.refund_cents),0) AS refund_cents "
                    "FROM sales_return_lines rl JOIN sales_returns r ON r.id=rl.return_id "
                    "WHERE rl.sales_line_id=? AND rl.lot_id=? AND r.status NOT IN ('rejected','cancelled')",
                    (sales_line_id, lot_id),
                ).fetchone()
                if quantity + pending["quantity"] > shipped_for_lot: raise ValidationError("退货数量超过该批次的可退数量")
                default_refund = int(line["total_cents"]) * quantity // int(line["ordered_quantity"])
                refund = int(item.get("refund_cents", default_refund))
                if refund < 0: raise ValidationError("退款金额不能为负")
                if refund + int(pending["refund_cents"]) > int(line["total_cents"]):
                    raise ValidationError("累计退款金额不能超过原销售明细的含税折后金额")
                serial_ids = [str(value).strip() for value in item.get("serial_ids", [])]
                if line["tracking"] == "serial" and (len(serial_ids) != quantity or len(serial_ids) != len(set(serial_ids))):
                    raise ValidationError("序列号商品退货必须按数量提供不重复的 serial_ids")
                if line["tracking"] != "serial" and serial_ids:
                    raise ValidationError("非序列号商品退货不能指定 serial_ids")
                return_line_id = self._id("RTL")
                conn.execute(
                    "INSERT INTO sales_return_lines(id,return_id,sales_line_id,product_id,quantity,condition,refund_cents,lot_id) VALUES(?,?,?,?,?,?,?,?)",
                    (return_line_id, return_id, sales_line_id, line["product_id"], quantity, str(item.get("condition", "resellable")), refund, lot_id),
                )
                for serial_id in serial_ids:
                    serial = conn.execute(
                        "SELECT sn.id FROM serial_numbers sn JOIN shipment_serials ss ON ss.serial_number_id=sn.id "
                        "JOIN shipment_lines sl ON sl.id=ss.shipment_line_id JOIN shipments s ON s.id=sl.shipment_id "
                        "WHERE sn.id=? AND sn.organization_id=? AND sn.product_id=? AND COALESCE(sn.lot_id,'')=? "
                        "AND sn.status='shipped' AND ss.status='shipped' AND s.status='shipped' AND s.sales_document_id=?",
                        (serial_id, principal.organization_id, line["product_id"], lot_id, document_id),
                    ).fetchone()
                    prior_return = conn.execute(
                        "SELECT 1 FROM sales_return_serials rs JOIN sales_return_lines rl ON rl.id=rs.return_line_id "
                        "JOIN sales_returns r ON r.id=rl.return_id WHERE rs.serial_number_id=? "
                        "AND r.status NOT IN ('rejected','cancelled') LIMIT 1",
                        (serial_id,),
                    ).fetchone()
                    if not serial or prior_return:
                        raise Conflict(f"序列号未由该订单发出或已申请退货：{serial_id}")
                    conn.execute(
                        "INSERT INTO sales_return_serials(return_line_id,serial_number_id) VALUES(?,?)",
                        (return_line_id, serial_id),
                    )
            self.audit.record(conn, AuditContext(principal), "sales_return.create", "sales_return", return_id,
                              after={"return_number": number, "sales_document_id": document_id})
        return self.sales_return(principal, return_id)

    def authorize_return(self, principal: Principal, return_id: str, approve: bool = True) -> dict:
        principal.require("sales.confirm")
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM sales_returns WHERE id=? AND organization_id=?", (return_id, principal.organization_id)).fetchone()
            if not row: raise NotFound(f"退货单不存在：{return_id}")
            if row["status"] != "draft": raise InvalidTransition("只有草稿退货单可以审批")
            if row["created_by"] == principal.user_id:
                raise ApprovalRequired("退货申请不能由创建人本人审批")
            status = "authorized" if approve else "rejected"
            conn.execute("UPDATE sales_returns SET status=?,approved_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, principal.user_id, return_id))
            self.audit.record(conn, AuditContext(principal), "sales_return.authorize", "sales_return", return_id,
                              before={"status": "draft"}, after={"status": status})
        return self.sales_return(principal, return_id)

    def receive_return(self, principal: Principal, return_id: str, location_id: str, event_key: str) -> dict:
        principal.require("inventory.receive")
        if not event_key.strip():
            raise ValidationError("退货收货幂等键不能为空")
        with self.store.connect() as conn:
            header = conn.execute("SELECT * FROM sales_returns WHERE id=? AND organization_id=?", (return_id, principal.organization_id)).fetchone()
            if not header: raise NotFound(f"退货单不存在：{return_id}")
            if header["status"] == "received": return self.sales_return(principal, return_id)
            if header["status"] != "authorized": raise InvalidTransition("退货必须审批后才能收货")
            location = conn.execute("SELECT l.* FROM storage_locations l JOIN sites s ON s.id=l.site_id WHERE l.id=? AND s.organization_id=?", (location_id, principal.organization_id)).fetchone()
            if not location: raise NotFound(f"库位不存在：{location_id}")
            lines = conn.execute("SELECT * FROM sales_return_lines WHERE return_id=?", (return_id,)).fetchall()
            for line in lines:
                target = location_id
                if line["condition"] != "resellable" and location["location_type"] == "internal":
                    raise ValidationError("残次或问题商品不能退回可售库位")
                conn.execute("INSERT OR IGNORE INTO stock_balance(organization_id,product_id,location_id,lot_id) VALUES(?,?,?,?)", (principal.organization_id, line["product_id"], target, line["lot_id"]))
                conn.execute("UPDATE stock_balance SET on_hand=on_hand+?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=?", (line["quantity"], principal.organization_id, line["product_id"], target, line["lot_id"]))
                move_id = self._id("MOV")
                conn.execute(
                    "INSERT INTO stock_moves(id,organization_id,event_key,product_id,destination_location_id,lot_id,quantity,move_type,reference_type,reference_id,created_by) VALUES(?,?,?,?,?,?,?,'return_in','sales_return',?,?)",
                    (move_id, principal.organization_id, f"{event_key}:{line['id']}", line["product_id"], target, line["lot_id"], line["quantity"], return_id, principal.user_id),
                )
                cost = conn.execute(
                    "SELECT COALESCE(SUM(m.total_cost_cents),0) AS value,COALESCE(SUM(m.quantity),0) AS quantity "
                    "FROM stock_moves m JOIN shipments s ON s.id=m.reference_id "
                    "WHERE m.reference_type='shipment' AND s.sales_document_id=? AND m.product_id=? AND m.lot_id=?",
                    (header["sales_document_id"], line["product_id"], line["lot_id"]),
                ).fetchone()
                unit_cost = int(cost["value"]) // int(cost["quantity"]) if int(cost["quantity"]) else 0
                valued = self.valuation.receive(
                    conn, principal.organization_id, move_id, line["product_id"], target, line["lot_id"],
                    line["quantity"], unit_cost, "return",
                )
                if valued["value_cents"]:
                    self.ledger.post(
                        conn, principal, "inventory", date.today().isoformat(), "sales_return", return_id,
                        f"sales-return-cost:{move_id}", f"销售退货入库 {header['return_number']}",
                        [{"account_code": "1405", "debit_cents": valued["value_cents"], "product_id": line["product_id"],
                          "location_id": target, "lot_id": line["lot_id"]},
                         {"account_code": "6001", "credit_cents": valued["value_cents"], "product_id": line["product_id"]}],
                    )
                serial_ids = conn.execute(
                    "SELECT serial_number_id FROM sales_return_serials WHERE return_line_id=?", (line["id"],)
                ).fetchall()
                for serial in serial_ids:
                    changed = conn.execute(
                        "UPDATE serial_numbers SET status='returned',current_location_id=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND organization_id=? AND status='shipped'",
                        (target, serial["serial_number_id"], principal.organization_id),
                    )
                    if changed.rowcount != 1:
                        raise Conflict("退货序列号状态已变更")
                conn.execute("UPDATE sales_return_lines SET received_quantity=quantity WHERE id=?", (line["id"],))
                conn.execute("UPDATE sales_document_lines SET returned_quantity=returned_quantity+? WHERE id=?", (line["quantity"], line["sales_line_id"]))
            conn.execute("UPDATE sales_returns SET status='received',received_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (return_id,))
            self.audit.record(conn, AuditContext(principal), "sales_return.receive", "sales_return", return_id,
                              before={"status": "authorized"}, after={"status": "received"})
        return self.sales_return(principal, return_id)

    def sales_return(self, principal: Principal, return_id: str) -> dict:
        principal.require("sales.read")
        header = self.store.row("SELECT * FROM sales_returns WHERE id=? AND organization_id=?", (return_id, principal.organization_id))
        if not header: raise NotFound(f"退货单不存在：{return_id}")
        header["lines"] = self.store.rows(
            "SELECT l.*,p.sku,p.name AS product_name FROM sales_return_lines l JOIN product_master p ON p.id=l.product_id WHERE l.return_id=?", (return_id,),
        )
        for line in header["lines"]:
            line["serials"] = self.store.rows(
                "SELECT s.id,s.serial_number,s.status FROM sales_return_serials rs JOIN serial_numbers s ON s.id=rs.serial_number_id "
                "WHERE rs.return_line_id=? ORDER BY s.serial_number",
                (line["id"],),
            )
        return header

    def list_returns(self, principal: Principal, status: str = "", order_id: str = "",
                     limit: int = 100) -> list[dict]:
        principal.require("sales.read")
        filters = ["r.organization_id=?"]
        params: list[object] = [principal.organization_id]
        if status:
            filters.append("r.status=?"); params.append(status)
        if order_id:
            filters.append("r.sales_document_id=?"); params.append(order_id)
        params.append(max(1, min(limit, 500)))
        return self.store.rows(
            "SELECT r.*,d.document_number,c.name AS customer_name,"
            "COALESCE(SUM(rl.quantity),0) AS return_quantity,"
            "COALESCE(SUM(rl.refund_cents),0) AS refund_cents "
            "FROM sales_returns r JOIN sales_documents d ON d.id=r.sales_document_id "
            "JOIN customer_master c ON c.id=d.customer_id LEFT JOIN sales_return_lines rl ON rl.return_id=r.id "
            f"WHERE {' AND '.join(filters)} GROUP BY r.id ORDER BY r.created_at DESC,r.return_number DESC LIMIT ?",
            tuple(params),
        )

    @staticmethod
    def _outbox(conn: sqlite3.Connection, organization_id: str, event_type: str, aggregate_type: str,
                aggregate_id: str, payload: dict) -> None:
        conn.execute(
            "INSERT INTO outbox_events(id,organization_id,event_type,aggregate_type,aggregate_id,payload_json) VALUES(?,?,?,?,?,?)",
            (f"EVT-{uuid.uuid4().hex.upper()}", organization_id, event_type, aggregate_type, aggregate_id,
             json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )
