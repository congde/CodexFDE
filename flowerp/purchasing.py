from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date
from typing import Iterable

from .accounting import InventoryValuationService, LedgerService
from .audit import AuditContext, AuditService
from .identity import Principal
from .models import ApprovalRequired, Conflict, InvalidTransition, NotFound, ValidationError, calculate_tax, require_positive, validate_iso_date
from .numbering import next_number
from .store import ERPStore


class PurchasingService:
    """Purchase-order approval, receiving, quality rejection, and supplier history."""

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)
        self.valuation = InventoryValuationService(store)
        self.ledger = LedgerService(store)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    def create_order(self, principal: Principal, supplier_id: str, warehouse_id: str,
                     lines: Iterable[dict], order_date: str | None = None, expected_date: str | None = None,
                     currency: str = "CNY", freight_cents: int = 0, supplier_reference: str = "",
                     notes: str = "") -> dict:
        principal.require("purchase.write")
        prepared = list(lines)
        if not prepared: raise ValidationError("采购单至少包含一行")
        order_date = order_date or date.today().isoformat(); validate_iso_date(order_date, "采购日期")
        if expected_date:
            validate_iso_date(expected_date, "预计到货日期")
            if expected_date < order_date: raise ValidationError("预计到货日期不能早于采购日期")
        if freight_cents < 0: raise ValidationError("运费不能为负")
        currency = currency.strip().upper()
        if len(currency) != 3: raise ValidationError("币种必须是三位 ISO 代码")
        purchase_id = self._id("PUR")
        with self.store.connect() as conn:
            supplier = conn.execute("SELECT * FROM supplier_master WHERE id=? AND organization_id=? AND status='active'", (supplier_id, principal.organization_id)).fetchone()
            if not supplier: raise NotFound(f"有效供应商不存在：{supplier_id}")
            if supplier["currency"] != currency: raise ValidationError(f"采购币种必须与供应商币种一致：{supplier['currency']}")
            site = conn.execute("SELECT * FROM sites WHERE id=? AND organization_id=? AND active=1", (warehouse_id, principal.organization_id)).fetchone()
            if not site: raise NotFound(f"有效仓库不存在：{warehouse_id}")
            number = next_number(conn, principal.organization_id, "purchase_order")
            calculated = self._prepare_lines(conn, principal.organization_id, supplier_id, prepared)
            subtotal = sum(row["net_cents"] for row in calculated); tax = sum(row["tax_cents"] for row in calculated)
            conn.execute(
                "INSERT INTO purchase_orders(id,organization_id,order_number,supplier_id,status,order_date,expected_date,currency,subtotal_cents,tax_cents,freight_cents,total_cents,warehouse_id,supplier_reference,notes,created_by) "
                "VALUES(?,?,?,?,'draft',?,?,?,?,?,?,?,?,?,?,?)",
                (purchase_id, principal.organization_id, number, supplier_id, order_date, expected_date, currency,
                 subtotal, tax, freight_cents, subtotal + tax + freight_cents, warehouse_id,
                 supplier_reference.strip(), notes.strip(), principal.user_id),
            )
            for index, row in enumerate(calculated, 1):
                conn.execute(
                    "INSERT INTO purchase_order_lines(id,purchase_order_id,line_number,product_id,ordered_quantity,unit_price_cents,tax_rate_basis_points,net_cents,tax_cents,total_cents) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (row["id"], purchase_id, index, row["product_id"], row["quantity"], row["unit_price_cents"],
                     row["tax_rate_basis_points"], row["net_cents"], row["tax_cents"], row["total_cents"]),
                )
            self.audit.record(conn, AuditContext(principal), "purchase.create", "purchase_order", purchase_id,
                              after={"order_number": number, "supplier_id": supplier_id, "total_cents": subtotal + tax + freight_cents})
        return self.order(principal, purchase_id)

    @staticmethod
    def _prepare_lines(conn: sqlite3.Connection, organization_id: str, supplier_id: str, lines: list[dict]) -> list[dict]:
        result: list[dict] = []; seen: set[str] = set()
        for raw in lines:
            product_id = str(raw["product_id"])
            if product_id in seen: raise ValidationError("同一采购单不能重复商品")
            seen.add(product_id)
            product = conn.execute("SELECT * FROM product_master WHERE id=? AND organization_id=? AND active=1 AND purchasable=1", (product_id, organization_id)).fetchone()
            if not product: raise NotFound(f"可采购商品不存在：{product_id}")
            supplier_product = conn.execute("SELECT * FROM supplier_products WHERE supplier_id=? AND product_id=?", (supplier_id, product_id)).fetchone()
            quantity = int(raw["quantity"]); require_positive(quantity)
            if supplier_product and quantity < supplier_product["min_order_qty"]:
                raise ValidationError(f"采购数量低于供应商最小起订量：{supplier_product['min_order_qty']}")
            default_price = supplier_product["purchase_price_cents"] if supplier_product else product["standard_cost_cents"]
            unit_price = int(raw.get("unit_price_cents", default_price))
            if unit_price < 0: raise ValidationError("采购单价不能为负")
            tax_bp = int(raw.get("tax_rate_basis_points", product["tax_rate_basis_points"]))
            net = quantity * unit_price; tax = calculate_tax(net, tax_bp)
            result.append({"id": f"POL-{uuid.uuid4().hex.upper()}", "product_id": product_id,
                           "quantity": quantity, "unit_price_cents": unit_price, "tax_rate_basis_points": tax_bp,
                           "net_cents": net, "tax_cents": tax, "total_cents": net + tax})
        return result

    def update_draft(self, principal: Principal, purchase_id: str, version: int,
                     lines: Iterable[dict] | None = None, expected_date: str | None = None,
                     freight_cents: int | None = None, supplier_reference: str | None = None,
                     notes: str | None = None) -> dict:
        principal.require("purchase.write")
        before = self.order(principal, purchase_id)
        if before["status"] != "draft": raise InvalidTransition("只有草稿采购单可以修改")
        with self.store.connect() as conn:
            current = conn.execute(
                "SELECT * FROM purchase_orders WHERE id=? AND organization_id=? AND version=?",
                (purchase_id, principal.organization_id, version),
            ).fetchone()
            if not current: raise Conflict("采购单已被其他用户修改，请刷新后重试")
            updates: dict[str, object] = {}
            if expected_date is not None:
                if expected_date:
                    validate_iso_date(expected_date, "预计到货日期")
                    if expected_date < current["order_date"]: raise ValidationError("预计到货日期不能早于采购日期")
                updates["expected_date"] = expected_date or None
            if freight_cents is not None:
                if freight_cents < 0: raise ValidationError("运费不能为负")
                updates["freight_cents"] = freight_cents
            if supplier_reference is not None: updates["supplier_reference"] = supplier_reference.strip()
            if notes is not None: updates["notes"] = notes.strip()
            if lines is not None:
                prepared = self._prepare_lines(conn, principal.organization_id, current["supplier_id"], list(lines))
                if not prepared: raise ValidationError("采购单至少包含一行")
                conn.execute("DELETE FROM purchase_order_lines WHERE purchase_order_id=?", (purchase_id,))
                for index, row in enumerate(prepared, 1):
                    conn.execute(
                        "INSERT INTO purchase_order_lines(id,purchase_order_id,line_number,product_id,ordered_quantity,unit_price_cents,tax_rate_basis_points,net_cents,tax_cents,total_cents) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (row["id"], purchase_id, index, row["product_id"], row["quantity"], row["unit_price_cents"],
                         row["tax_rate_basis_points"], row["net_cents"], row["tax_cents"], row["total_cents"]),
                    )
                updates["subtotal_cents"] = sum(row["net_cents"] for row in prepared)
                updates["tax_cents"] = sum(row["tax_cents"] for row in prepared)
            if updates:
                updates["total_cents"] = int(updates.get("subtotal_cents", current["subtotal_cents"])) + int(updates.get("tax_cents", current["tax_cents"])) + int(updates.get("freight_cents", current["freight_cents"]))
                sql = ",".join(f"{key}=?" for key in updates)
                cursor = conn.execute(
                    f"UPDATE purchase_orders SET {sql},updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=? AND version=?",
                    (*updates.values(), purchase_id, version),
                )
                if cursor.rowcount != 1: raise Conflict("采购单已被其他用户修改，请刷新后重试")
            self.audit.record(conn, AuditContext(principal), "purchase.update", "purchase_order", purchase_id,
                              before=before, after=updates)
        return self.order(principal, purchase_id)

    def submit(self, principal: Principal, purchase_id: str) -> dict:
        principal.require("purchase.write")
        return self._transition(principal, purchase_id, "draft", "pending_approval", "purchase.submit")

    def approve(self, principal: Principal, purchase_id: str) -> dict:
        principal.require("purchase.approve")
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND organization_id=?", (purchase_id, principal.organization_id)).fetchone()
            if not row: raise NotFound(f"采购单不存在：{purchase_id}")
            if row["status"] != "pending_approval": raise InvalidTransition(f"只有待审批采购单可审批，当前为 {row['status']}")
            if row["created_by"] == principal.user_id:
                raise ApprovalRequired("采购制单人与审批人必须分离")
            stock_location = self._warehouse_stock_location(conn, principal.organization_id, row["warehouse_id"])
            lines = conn.execute("SELECT * FROM purchase_order_lines WHERE purchase_order_id=?", (purchase_id,)).fetchall()
            for line in lines:
                conn.execute(
                    "INSERT OR IGNORE INTO stock_balance(organization_id,product_id,location_id,lot_id) VALUES(?,?,?,'')",
                    (principal.organization_id, line["product_id"], stock_location),
                )
                conn.execute(
                    "UPDATE stock_balance SET incoming=incoming+?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                    "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=''",
                    (line["ordered_quantity"], principal.organization_id, line["product_id"], stock_location),
                )
            conn.execute("UPDATE purchase_orders SET status='approved',approved_by=?,approved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (principal.user_id, purchase_id))
            self.audit.record(conn, AuditContext(principal), "purchase.approve", "purchase_order", purchase_id,
                              before={"status": "pending_approval"}, after={"status": "approved"})
            self._outbox(conn, principal.organization_id, "purchase.approved", "purchase_order", purchase_id, {"order_number": row["order_number"]})
        return self.order(principal, purchase_id)

    def reject(self, principal: Principal, purchase_id: str, reason: str) -> dict:
        principal.require("purchase.approve")
        if not reason.strip(): raise ValidationError("驳回原因不能为空")
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND organization_id=?", (purchase_id, principal.organization_id)).fetchone()
            if not row: raise NotFound(f"采购单不存在：{purchase_id}")
            if row["status"] != "pending_approval": raise InvalidTransition("只有待审批采购单可驳回")
            conn.execute("UPDATE purchase_orders SET status='draft',notes=notes||?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (f"\n[驳回] {reason.strip()}", purchase_id))
            self.audit.record(conn, AuditContext(principal), "purchase.reject", "purchase_order", purchase_id,
                              before={"status": "pending_approval"}, after={"status": "draft", "reason": reason})
        return self.order(principal, purchase_id)

    def _transition(self, principal: Principal, purchase_id: str, expected: str, target: str, action: str) -> dict:
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND organization_id=?", (purchase_id, principal.organization_id)).fetchone()
            if not row: raise NotFound(f"采购单不存在：{purchase_id}")
            if row["status"] != expected: raise InvalidTransition(f"状态 {row['status']} 不能迁移到 {target}")
            conn.execute("UPDATE purchase_orders SET status=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (target, purchase_id))
            self.audit.record(conn, AuditContext(principal), action, "purchase_order", purchase_id, before={"status": expected}, after={"status": target})
        return self.order(principal, purchase_id)

    def create_receipt(self, principal: Principal, purchase_id: str, location_id: str,
                       lines: Iterable[dict], receipt_date: str | None = None,
                       supplier_delivery_note: str = "") -> dict:
        principal.require("purchase.receive")
        receipt_date = receipt_date or date.today().isoformat(); validate_iso_date(receipt_date, "收货日期")
        prepared = list(lines)
        if not prepared: raise ValidationError("收货单至少包含一行")
        receipt_id = self._id("GRC")
        with self.store.connect() as conn:
            purchase = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND organization_id=?", (purchase_id, principal.organization_id)).fetchone()
            if not purchase: raise NotFound(f"采购单不存在：{purchase_id}")
            if purchase["status"] not in {"approved", "partially_received"}: raise ApprovalRequired("采购单未审批或已结束，不能收货")
            location = conn.execute(
                "SELECT l.*,s.id AS warehouse_id FROM storage_locations l JOIN sites s ON s.id=l.site_id "
                "WHERE l.id=? AND s.organization_id=? AND l.active=1",
                (location_id, principal.organization_id),
            ).fetchone()
            if not location: raise NotFound(f"库位不存在：{location_id}")
            if location["warehouse_id"] != purchase["warehouse_id"]:
                raise ValidationError("收货库位不属于采购单指定仓库")
            number = next_number(conn, principal.organization_id, "goods_receipt")
            conn.execute(
                "INSERT INTO goods_receipts(id,organization_id,receipt_number,purchase_order_id,location_id,status,supplier_delivery_note,receipt_date,created_by) VALUES(?,?,?,?,?,'draft',?,?,?)",
                (receipt_id, principal.organization_id, number, purchase_id, location_id, supplier_delivery_note.strip(), receipt_date, principal.user_id),
            )
            seen: set[str] = set()
            for item in prepared:
                purchase_line_id = str(item["purchase_line_id"])
                if purchase_line_id in seen: raise ValidationError("收货单存在重复采购明细")
                seen.add(purchase_line_id)
                line = conn.execute("SELECT * FROM purchase_order_lines WHERE id=? AND purchase_order_id=?", (purchase_line_id, purchase_id)).fetchone()
                if not line: raise NotFound(f"采购明细不存在：{purchase_line_id}")
                accepted = int(item.get("accepted_quantity", 0)); rejected = int(item.get("rejected_quantity", 0))
                if accepted < 0 or rejected < 0 or accepted + rejected <= 0: raise ValidationError("收货数量必须大于 0")
                outstanding = line["ordered_quantity"] - line["received_quantity"] - line["rejected_quantity"]
                if accepted + rejected > outstanding: raise ValidationError("收货数量超过采购未收数量")
                lot_id = str(item.get("lot_id", ""))
                product = conn.execute("SELECT * FROM product_master WHERE id=?", (line["product_id"],)).fetchone()
                if product["tracking"] != "none" and accepted > 0 and not lot_id: raise ValidationError("批次/序列号商品收货必须指定批次")
                conn.execute(
                    "INSERT INTO goods_receipt_lines(id,receipt_id,purchase_line_id,product_id,accepted_quantity,rejected_quantity,lot_id,rejection_reason) VALUES(?,?,?,?,?,?,?,?)",
                    (self._id("GRL"), receipt_id, purchase_line_id, line["product_id"], accepted, rejected, lot_id, str(item.get("rejection_reason", "")).strip()),
                )
            self.audit.record(conn, AuditContext(principal), "goods_receipt.create", "goods_receipt", receipt_id,
                              after={"receipt_number": number, "purchase_order_id": purchase_id})
        return self.receipt(principal, receipt_id)

    def post_receipt(self, principal: Principal, receipt_id: str, event_key: str) -> dict:
        principal.require("purchase.receive")
        if not event_key.strip(): raise ValidationError("收货幂等键不能为空")
        with self.store.connect() as conn:
            receipt = conn.execute("SELECT * FROM goods_receipts WHERE id=? AND organization_id=?", (receipt_id, principal.organization_id)).fetchone()
            if not receipt: raise NotFound(f"收货单不存在：{receipt_id}")
            if receipt["status"] == "posted": return self.receipt(principal, receipt_id)
            if receipt["status"] != "draft": raise InvalidTransition("只有草稿收货单可以过账")
            purchase = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (receipt["purchase_order_id"],)).fetchone()
            if purchase["status"] not in {"approved", "partially_received"}: raise ApprovalRequired("采购单审批状态已失效")
            lines = conn.execute("SELECT gl.*,pl.unit_price_cents FROM goods_receipt_lines gl JOIN purchase_order_lines pl ON pl.id=gl.purchase_line_id WHERE gl.receipt_id=?", (receipt_id,)).fetchall()
            planned_location = self._warehouse_stock_location(
                conn, principal.organization_id, purchase["warehouse_id"]
            )
            for line in lines:
                processed_quantity = line["accepted_quantity"] + line["rejected_quantity"]
                cursor = conn.execute(
                    "UPDATE stock_balance SET incoming=incoming-?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                    "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id='' AND incoming>=?",
                    (processed_quantity, principal.organization_id, line["product_id"], planned_location, processed_quantity),
                )
                if cursor.rowcount != 1:
                    raise Conflict("采购在途数量与收货单不一致，收货被阻断")
                if line["accepted_quantity"]:
                    conn.execute("INSERT OR IGNORE INTO stock_balance(organization_id,product_id,location_id,lot_id) VALUES(?,?,?,?)", (principal.organization_id, line["product_id"], receipt["location_id"], line["lot_id"]))
                    conn.execute("UPDATE stock_balance SET on_hand=on_hand+?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=?", (line["accepted_quantity"], principal.organization_id, line["product_id"], receipt["location_id"], line["lot_id"]))
                    move_id = self._id("MOV")
                    try:
                        conn.execute(
                            "INSERT INTO stock_moves(id,organization_id,event_key,product_id,destination_location_id,lot_id,quantity,unit_cost_cents,move_type,reference_type,reference_id,created_by) VALUES(?,?,?,?,?,?,?,?,?,'goods_receipt',?,?)",
                            (move_id, principal.organization_id, f"{event_key}:{line['id']}", line["product_id"], receipt["location_id"], line["lot_id"], line["accepted_quantity"], line["unit_price_cents"], "receipt", receipt_id, principal.user_id),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise Conflict("该收货事件已经处理") from exc
                    valued = self.valuation.receive(
                        conn, principal.organization_id, move_id, line["product_id"], receipt["location_id"],
                        line["lot_id"], line["accepted_quantity"], line["unit_price_cents"], "receipt",
                    )
                    if valued["value_cents"]:
                        self.ledger.post(
                            conn, principal, "purchase", receipt["receipt_date"], "goods_receipt", receipt_id,
                            f"goods-receipt:{move_id}", f"采购收货 {receipt['receipt_number']}",
                            [{"account_code": "1405", "debit_cents": valued["value_cents"], "product_id": line["product_id"],
                              "location_id": receipt["location_id"], "lot_id": line["lot_id"]},
                             {"account_code": "2203", "credit_cents": valued["value_cents"], "partner_type": "supplier",
                              "partner_id": purchase["supplier_id"], "product_id": line["product_id"]}],
                            purchase["currency"],
                        )
                conn.execute("UPDATE purchase_order_lines SET received_quantity=received_quantity+?,rejected_quantity=rejected_quantity+? WHERE id=?", (line["accepted_quantity"], line["rejected_quantity"], line["purchase_line_id"]))
            remaining = conn.execute("SELECT SUM(ordered_quantity-received_quantity-rejected_quantity) FROM purchase_order_lines WHERE purchase_order_id=?", (receipt["purchase_order_id"],)).fetchone()[0]
            status = "received" if remaining == 0 else "partially_received"
            conn.execute("UPDATE goods_receipts SET status='posted',posted_by=?,posted_at=CURRENT_TIMESTAMP WHERE id=?", (principal.user_id, receipt_id))
            conn.execute("UPDATE purchase_orders SET status=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (status, receipt["purchase_order_id"]))
            self._outbox(conn, principal.organization_id, "goods_receipt.posted", "goods_receipt", receipt_id, {"purchase_order_id": receipt["purchase_order_id"], "status": status})
            self.audit.record(conn, AuditContext(principal), "goods_receipt.post", "goods_receipt", receipt_id,
                              before={"status": "draft"}, after={"status": "posted"})
        return self.receipt(principal, receipt_id)

    def cancel(self, principal: Principal, purchase_id: str, reason: str) -> dict:
        principal.require("purchase.write")
        if not reason.strip(): raise ValidationError("取消原因不能为空")
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND organization_id=?", (purchase_id, principal.organization_id)).fetchone()
            if not row: raise NotFound(f"采购单不存在：{purchase_id}")
            if row["status"] not in {"draft", "pending_approval", "approved"}: raise InvalidTransition(f"采购单状态不允许取消：{row['status']}")
            received = conn.execute("SELECT COALESCE(SUM(received_quantity),0) FROM purchase_order_lines WHERE purchase_order_id=?", (purchase_id,)).fetchone()[0]
            if received: raise InvalidTransition("已有收货记录的采购单不能取消")
            if row["status"] == "approved":
                planned_location = self._warehouse_stock_location(
                    conn, principal.organization_id, row["warehouse_id"]
                )
                lines = conn.execute(
                    "SELECT * FROM purchase_order_lines WHERE purchase_order_id=?", (purchase_id,)
                ).fetchall()
                for line in lines:
                    cursor = conn.execute(
                        "UPDATE stock_balance SET incoming=incoming-?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                        "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id='' AND incoming>=?",
                        (line["ordered_quantity"], principal.organization_id, line["product_id"],
                         planned_location, line["ordered_quantity"]),
                    )
                    if cursor.rowcount != 1:
                        raise Conflict("采购在途账不一致，取消被阻断")
            conn.execute("UPDATE purchase_orders SET status='cancelled',notes=notes||?,cancelled_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (f"\n[取消] {reason.strip()}", purchase_id))
            self.audit.record(conn, AuditContext(principal), "purchase.cancel", "purchase_order", purchase_id,
                              before={"status": row["status"]}, after={"status": "cancelled", "reason": reason})
        return self.order(principal, purchase_id)

    def order(self, principal: Principal, purchase_id: str) -> dict:
        principal.require("purchase.read")
        header = self.store.row(
            "SELECT p.*,s.code AS supplier_code,s.name AS supplier_name,w.code AS warehouse_code,w.name AS warehouse_name FROM purchase_orders p "
            "JOIN supplier_master s ON s.id=p.supplier_id JOIN sites w ON w.id=p.warehouse_id WHERE p.id=? AND p.organization_id=?",
            (purchase_id, principal.organization_id),
        )
        if not header: raise NotFound(f"采购单不存在：{purchase_id}")
        header["lines"] = self.store.rows(
            "SELECT l.*,p.sku,p.name AS product_name,"
            "COALESCE((SELECT SUM(il.quantity) FROM invoice_lines il JOIN invoices i ON i.id=il.invoice_id "
            "WHERE il.source_line_id=l.id AND i.status<>'void'),0) AS invoiced_quantity "
            "FROM purchase_order_lines l JOIN product_master p ON p.id=l.product_id "
            "WHERE l.purchase_order_id=? ORDER BY l.line_number", (purchase_id,),
        )
        header["receipts"] = self.store.rows("SELECT * FROM goods_receipts WHERE purchase_order_id=? ORDER BY created_at", (purchase_id,))
        return header

    def list_orders(self, principal: Principal, status: str = "", supplier_id: str = "", limit: int = 100, offset: int = 0) -> list[dict]:
        principal.require("purchase.read")
        filters = ["p.organization_id=?"]; params: list[object] = [principal.organization_id]
        if status: filters.append("p.status=?"); params.append(status)
        if supplier_id: filters.append("p.supplier_id=?"); params.append(supplier_id)
        params.extend((max(1, min(limit, 500)), max(0, offset)))
        return self.store.rows(
            f"SELECT p.*,s.code AS supplier_code,s.name AS supplier_name FROM purchase_orders p JOIN supplier_master s ON s.id=p.supplier_id "
            f"WHERE {' AND '.join(filters)} ORDER BY p.order_date DESC,p.order_number DESC LIMIT ? OFFSET ?", tuple(params),
        )

    def receipt(self, principal: Principal, receipt_id: str) -> dict:
        principal.require("purchase.read")
        header = self.store.row("SELECT * FROM goods_receipts WHERE id=? AND organization_id=?", (receipt_id, principal.organization_id))
        if not header: raise NotFound(f"收货单不存在：{receipt_id}")
        header["lines"] = self.store.rows(
            "SELECT l.*,p.sku,p.name AS product_name FROM goods_receipt_lines l JOIN product_master p ON p.id=l.product_id WHERE l.receipt_id=?", (receipt_id,),
        )
        return header

    def list_receipts(self, principal: Principal, status: str = "", purchase_id: str = "",
                      limit: int = 100) -> list[dict]:
        principal.require("purchase.read")
        filters = ["r.organization_id=?"]
        params: list[object] = [principal.organization_id]
        if status:
            filters.append("r.status=?"); params.append(status)
        if purchase_id:
            filters.append("r.purchase_order_id=?"); params.append(purchase_id)
        params.append(max(1, min(limit, 500)))
        return self.store.rows(
            "SELECT r.*,p.order_number,s.name AS supplier_name,l.code AS location_code,"
            "COALESCE(SUM(rl.accepted_quantity),0) AS accepted_quantity,"
            "COALESCE(SUM(rl.rejected_quantity),0) AS rejected_quantity "
            "FROM goods_receipts r JOIN purchase_orders p ON p.id=r.purchase_order_id "
            "JOIN supplier_master s ON s.id=p.supplier_id JOIN storage_locations l ON l.id=r.location_id "
            "LEFT JOIN goods_receipt_lines rl ON rl.receipt_id=r.id "
            f"WHERE {' AND '.join(filters)} GROUP BY r.id ORDER BY r.receipt_date DESC,r.receipt_number DESC LIMIT ?",
            tuple(params),
        )

    @staticmethod
    def _warehouse_stock_location(
        conn: sqlite3.Connection, organization_id: str, warehouse_id: str
    ) -> str:
        row = conn.execute(
            "SELECT l.id FROM storage_locations l JOIN sites s ON s.id=l.site_id "
            "WHERE s.id=? AND s.organization_id=? AND s.active=1 AND l.active=1 "
            "AND l.location_type='internal' ORDER BY l.code LIMIT 1",
            (warehouse_id, organization_id),
        ).fetchone()
        if not row:
            raise ValidationError("采购仓库没有可用的内部库存库位")
        return str(row["id"])

    @staticmethod
    def _outbox(conn: sqlite3.Connection, organization_id: str, event_type: str, aggregate_type: str,
                aggregate_id: str, payload: dict) -> None:
        conn.execute("INSERT INTO outbox_events(id,organization_id,event_type,aggregate_type,aggregate_id,payload_json) VALUES(?,?,?,?,?,?)",
                     (f"EVT-{uuid.uuid4().hex.upper()}", organization_id, event_type, aggregate_type, aggregate_id,
                      json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
