from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date
from typing import Iterable

from .accounting import InventoryValuationService, LedgerService
from .audit import AuditContext, AuditService
from .identity import Principal
from .models import Conflict, InsufficientStock, NotFound, ValidationError, require_positive, validate_iso_date
from .numbering import next_number
from .store import ERPStore


class InventoryService:
    """Multi-location inventory with immutable moves and atomic balances."""

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)
        self.valuation = InventoryValuationService(store)
        self.accounting = LedgerService(store)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    @staticmethod
    def _location(conn: sqlite3.Connection, organization_id: str, location_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT l.*,s.organization_id FROM storage_locations l JOIN sites s ON s.id=l.site_id WHERE l.id=? AND s.organization_id=? AND l.active=1",
            (location_id, organization_id),
        ).fetchone()
        if not row:
            raise NotFound(f"有效库位不存在：{location_id}")
        return row

    @staticmethod
    def _product(conn: sqlite3.Connection, organization_id: str, product_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM product_master WHERE id=? AND organization_id=? AND active=1", (product_id, organization_id)).fetchone()
        if not row:
            raise NotFound(f"有效商品不存在：{product_id}")
        return row

    @staticmethod
    def _ensure_balance(conn: sqlite3.Connection, organization_id: str, product_id: str,
                        location_id: str, lot_id: str = "") -> None:
        conn.execute(
            "INSERT OR IGNORE INTO stock_balance(organization_id,product_id,location_id,lot_id) VALUES(?,?,?,?)",
            (organization_id, product_id, location_id, lot_id),
        )

    @staticmethod
    def _balance_row(conn: sqlite3.Connection, organization_id: str, product_id: str,
                     location_id: str, lot_id: str = "") -> sqlite3.Row:
        InventoryService._ensure_balance(conn, organization_id, product_id, location_id, lot_id)
        return conn.execute(
            "SELECT *,(on_hand-reserved) AS available FROM stock_balance WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=?",
            (organization_id, product_id, location_id, lot_id),
        ).fetchone()

    def create_lot(self, principal: Principal, product_id: str, lot_number: str,
                   manufacture_date: str | None = None, expiry_date: str | None = None,
                   supplier_id: str | None = None) -> dict:
        principal.require("inventory.receive")
        lot_number = lot_number.strip().upper()
        if not lot_number:
            raise ValidationError("批次号不能为空")
        if manufacture_date:
            validate_iso_date(manufacture_date, "生产日期")
        if expiry_date:
            validate_iso_date(expiry_date, "失效日期")
        if manufacture_date and expiry_date and manufacture_date > expiry_date:
            raise ValidationError("失效日期不能早于生产日期")
        lot_id = self._id("LOT")
        with self.store.connect() as conn:
            product = self._product(conn, principal.organization_id, product_id)
            if product["tracking"] == "none":
                raise ValidationError("该商品未启用批次跟踪")
            try:
                conn.execute(
                    "INSERT INTO stock_lots(id,organization_id,product_id,lot_number,manufacture_date,expiry_date,supplier_id) VALUES(?,?,?,?,?,?,?)",
                    (lot_id, principal.organization_id, product_id, lot_number, manufacture_date, expiry_date, supplier_id),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict(f"批次号已存在：{lot_number}") from exc
            self.audit.record(conn, AuditContext(principal), "lot.create", "stock_lot", lot_id, after={"lot_number": lot_number, "product_id": product_id})
        return self.lot(principal, lot_id)

    def lot(self, principal: Principal, lot_id: str) -> dict:
        principal.require("inventory.read")
        row = self.store.row(
            "SELECT l.*,p.sku,p.name AS product_name FROM stock_lots l JOIN product_master p ON p.id=l.product_id WHERE l.id=? AND l.organization_id=?",
            (lot_id, principal.organization_id),
        )
        if not row:
            raise NotFound(f"批次不存在：{lot_id}")
        return row

    def receive(self, principal: Principal, product_id: str, location_id: str, quantity: int,
                event_key: str, lot_id: str = "", unit_cost_cents: int = 0,
                reference_type: str = "manual", reference_id: str = "", reason: str = "") -> dict:
        principal.require("inventory.receive")
        require_positive(quantity)
        if unit_cost_cents < 0:
            raise ValidationError("单位成本不能为负")
        if not event_key.strip():
            raise ValidationError("幂等键不能为空")
        with self.store.connect() as conn:
            replay = conn.execute(
                "SELECT id FROM stock_moves WHERE organization_id=? AND event_key=?",
                (principal.organization_id, event_key),
            ).fetchone()
            if replay:
                result = self.balance(principal, product_id, location_id, lot_id)
                result["idempotent_replay"] = True
                return result
            product = self._product(conn, principal.organization_id, product_id)
            self._location(conn, principal.organization_id, location_id)
            self._validate_tracking(conn, product, lot_id, quantity)
            self._ensure_balance(conn, principal.organization_id, product_id, location_id, lot_id)
            conn.execute(
                "UPDATE stock_balance SET on_hand=on_hand+?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=?",
                (quantity, principal.organization_id, product_id, location_id, lot_id),
            )
            move_id = self._id("MOV")
            conn.execute(
                "INSERT INTO stock_moves(id,organization_id,event_key,product_id,destination_location_id,lot_id,quantity,unit_cost_cents,move_type,reference_type,reference_id,reason,created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (move_id, principal.organization_id, event_key, product_id, location_id, lot_id, quantity,
                 unit_cost_cents, "receipt", reference_type, reference_id, reason.strip(), principal.user_id),
            )
            valuation = self.valuation.receive(
                conn, principal.organization_id, move_id, product_id, location_id, lot_id,
                quantity, unit_cost_cents, "receipt",
            )
            if valuation["value_cents"]:
                self.accounting.post(
                    conn, principal, "inventory", date.today().isoformat(), "stock_move", move_id,
                    f"inventory-receipt:{move_id}", reason or "库存入库",
                    [{"account_code": "1405", "debit_cents": valuation["value_cents"], "product_id": product_id,
                      "location_id": location_id, "lot_id": lot_id},
                     {"account_code": "3101", "credit_cents": valuation["value_cents"], "product_id": product_id}],
                )
            self._outbox(conn, principal.organization_id, "inventory.received", "stock_move", move_id,
                         {"product_id": product_id, "location_id": location_id, "quantity": quantity, "reference_id": reference_id})
            self.audit.record(conn, AuditContext(principal), "inventory.receive", "stock_move", move_id,
                              after={"product_id": product_id, "location_id": location_id, "quantity": quantity, "lot_id": lot_id})
        result = self.balance(principal, product_id, location_id, lot_id)
        result["idempotent_replay"] = False
        return result

    @staticmethod
    def _validate_tracking(conn: sqlite3.Connection, product: sqlite3.Row, lot_id: str, quantity: int) -> None:
        if product["tracking"] == "lot" and not lot_id:
            raise ValidationError("批次跟踪商品必须指定批次")
        if product["tracking"] == "serial" and (not lot_id or quantity != 1):
            raise ValidationError("序列号跟踪商品每次只能处理 1 件并指定批次/序列记录")
        if lot_id:
            lot = conn.execute("SELECT * FROM stock_lots WHERE id=? AND product_id=?", (lot_id, product["id"])).fetchone()
            if not lot:
                raise NotFound(f"商品批次不存在：{lot_id}")
            if lot["status"] != "active":
                raise ValidationError(f"批次状态不允许交易：{lot['status']}")
            if lot["expiry_date"] and lot["expiry_date"] < date.today().isoformat():
                raise ValidationError("过期批次不允许入库到可售库存")

    def reserve(self, principal: Principal, product_id: str, location_id: str, quantity: int,
                reference_type: str, reference_id: str, reference_line_id: str = "", lot_id: str = "") -> dict:
        principal.require("sales.confirm")
        require_positive(quantity)
        reservation_id = self._id("RSV")
        with self.store.connect() as conn:
            product = self._product(conn, principal.organization_id, product_id)
            self._location(conn, principal.organization_id, location_id)
            balance = self._balance_row(conn, principal.organization_id, product_id, location_id, lot_id)
            if balance["available"] < quantity:
                raise InsufficientStock(product["sku"], quantity, balance["available"])
            try:
                conn.execute(
                    "INSERT INTO stock_reservations(id,organization_id,product_id,location_id,lot_id,reference_type,reference_id,reference_line_id,quantity) VALUES(?,?,?,?,?,?,?,?,?)",
                    (reservation_id, principal.organization_id, product_id, location_id, lot_id,
                     reference_type, reference_id, reference_line_id, quantity),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict("该业务行已预占库存") from exc
            cursor = conn.execute(
                "UPDATE stock_balance SET reserved=reserved+?,outgoing=outgoing+?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=? AND on_hand-reserved>=?",
                (quantity, quantity, principal.organization_id, product_id, location_id, lot_id, quantity),
            )
            if cursor.rowcount != 1:
                raise InsufficientStock(product["sku"], quantity, max(0, balance["available"]))
            self.audit.record(conn, AuditContext(principal), "inventory.reserve", "stock_reservation", reservation_id,
                              after={"product_id": product_id, "quantity": quantity, "reference_id": reference_id})
        return self.reservation(principal, reservation_id)

    def reservation(self, principal: Principal, reservation_id: str) -> dict:
        principal.require("inventory.read")
        row = self.store.row(
            "SELECT r.*,p.sku,p.name AS product_name,l.code AS location_code FROM stock_reservations r "
            "JOIN product_master p ON p.id=r.product_id JOIN storage_locations l ON l.id=r.location_id "
            "WHERE r.id=? AND r.organization_id=?", (reservation_id, principal.organization_id),
        )
        if not row:
            raise NotFound(f"库存预占不存在：{reservation_id}")
        return row

    def release(self, principal: Principal, reservation_id: str, reason: str = "") -> dict:
        principal.require("sales.cancel")
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM stock_reservations WHERE id=? AND organization_id=?", (reservation_id, principal.organization_id)).fetchone()
            if not row:
                raise NotFound(f"库存预占不存在：{reservation_id}")
            if row["status"] != "active":
                raise Conflict(f"预占状态不允许释放：{row['status']}")
            cursor = conn.execute(
                "UPDATE stock_balance SET reserved=reserved-?,outgoing=outgoing-?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=? AND reserved>=? AND outgoing>=?",
                (row["quantity"], row["quantity"], principal.organization_id, row["product_id"],
                 row["location_id"], row["lot_id"], row["quantity"], row["quantity"]),
            )
            if cursor.rowcount != 1:
                raise Conflict("库存预占账不一致，请停止操作并检查数据")
            conn.execute("UPDATE stock_reservations SET status='released',released_at=CURRENT_TIMESTAMP WHERE id=?", (reservation_id,))
            self.audit.record(conn, AuditContext(principal), "inventory.release", "stock_reservation", reservation_id,
                              before={"status": "active"}, after={"status": "released", "reason": reason})
        return self.reservation(principal, reservation_id)

    def consume(self, principal: Principal, reservation_id: str, event_key: str,
                reference_type: str = "shipment", reference_id: str = "") -> dict:
        principal.require("inventory.ship")
        with self.store.connect() as conn:
            replay = conn.execute("SELECT id FROM stock_moves WHERE organization_id=? AND event_key=?", (principal.organization_id, event_key)).fetchone()
            if replay:
                reservation = conn.execute("SELECT * FROM stock_reservations WHERE id=?", (reservation_id,)).fetchone()
                return {"reservation": dict(reservation), "idempotent_replay": True}
            row = conn.execute("SELECT * FROM stock_reservations WHERE id=? AND organization_id=?", (reservation_id, principal.organization_id)).fetchone()
            if not row:
                raise NotFound(f"库存预占不存在：{reservation_id}")
            if row["status"] != "active":
                raise Conflict(f"预占状态不允许出库：{row['status']}")
            cursor = conn.execute(
                "UPDATE stock_balance SET on_hand=on_hand-?,reserved=reserved-?,outgoing=outgoing-?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=? AND on_hand>=? AND reserved>=? AND outgoing>=?",
                (row["quantity"], row["quantity"], row["quantity"], principal.organization_id,
                 row["product_id"], row["location_id"], row["lot_id"],
                 row["quantity"], row["quantity"], row["quantity"]),
            )
            if cursor.rowcount != 1:
                raise Conflict("库存账与预占不一致，出库被阻断")
            conn.execute("UPDATE stock_reservations SET status='consumed',released_at=CURRENT_TIMESTAMP WHERE id=?", (reservation_id,))
            move_id = self._id("MOV")
            conn.execute(
                "INSERT INTO stock_moves(id,organization_id,event_key,product_id,source_location_id,lot_id,quantity,move_type,reference_type,reference_id,created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (move_id, principal.organization_id, event_key, row["product_id"], row["location_id"], row["lot_id"],
                 row["quantity"], "shipment", reference_type, reference_id, principal.user_id),
            )
            valued = self.valuation.consume(
                conn, principal.organization_id, move_id, row["product_id"], row["location_id"],
                row["lot_id"], row["quantity"],
            )
            if valued["value_cents"]:
                self.accounting.post(
                    conn, principal, "inventory", date.today().isoformat(), reference_type, reference_id or move_id,
                    f"inventory-consume:{move_id}", "库存出库成本结转",
                    [{"account_code": "6001", "debit_cents": valued["value_cents"], "product_id": row["product_id"]},
                     {"account_code": "1405", "credit_cents": valued["value_cents"], "product_id": row["product_id"],
                      "location_id": row["location_id"], "lot_id": row["lot_id"]}],
                )
            self._outbox(conn, principal.organization_id, "inventory.shipped", "stock_move", move_id,
                         {"product_id": row["product_id"], "quantity": row["quantity"], "reference_id": reference_id})
            self.audit.record(conn, AuditContext(principal), "inventory.ship", "stock_move", move_id,
                              after={"reservation_id": reservation_id, "quantity": row["quantity"]})
        return {"reservation": self.reservation(principal, reservation_id), "move_id": move_id, "idempotent_replay": False}

    def transfer(self, principal: Principal, product_id: str, source_location_id: str,
                 destination_location_id: str, quantity: int, event_key: str, lot_id: str = "", reason: str = "") -> dict:
        principal.require("inventory.transfer")
        require_positive(quantity)
        if source_location_id == destination_location_id:
            raise ValidationError("来源和目标库位不能相同")
        with self.store.connect() as conn:
            replay = conn.execute("SELECT id FROM stock_moves WHERE organization_id=? AND event_key=?", (principal.organization_id, event_key)).fetchone()
            if replay:
                return {"move_id": replay["id"], "idempotent_replay": True}
            product = self._product(conn, principal.organization_id, product_id)
            self._location(conn, principal.organization_id, source_location_id)
            self._location(conn, principal.organization_id, destination_location_id)
            source = self._balance_row(conn, principal.organization_id, product_id, source_location_id, lot_id)
            if source["available"] < quantity:
                raise InsufficientStock(product["sku"], quantity, source["available"])
            self._ensure_balance(conn, principal.organization_id, product_id, destination_location_id, lot_id)
            cursor = conn.execute(
                "UPDATE stock_balance SET on_hand=on_hand-?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=? AND on_hand-reserved>=?",
                (quantity, principal.organization_id, product_id, source_location_id, lot_id, quantity),
            )
            if cursor.rowcount != 1:
                raise InsufficientStock(product["sku"], quantity, source["available"])
            conn.execute(
                "UPDATE stock_balance SET on_hand=on_hand+?,updated_at=CURRENT_TIMESTAMP,version=version+1 "
                "WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=?",
                (quantity, principal.organization_id, product_id, destination_location_id, lot_id),
            )
            move_id = self._id("MOV")
            conn.execute(
                "INSERT INTO stock_moves(id,organization_id,event_key,product_id,source_location_id,destination_location_id,lot_id,quantity,move_type,reason,created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (move_id, principal.organization_id, event_key, product_id, source_location_id,
                 destination_location_id, lot_id, quantity, "transfer", reason.strip(), principal.user_id),
            )
            consumed = self.valuation.consume(
                conn, principal.organization_id, move_id, product_id, source_location_id, lot_id, quantity,
            )
            self.valuation.transfer_in(
                conn, principal.organization_id, move_id, product_id, destination_location_id, lot_id,
                quantity, consumed["value_cents"],
            )
            self.audit.record(conn, AuditContext(principal), "inventory.transfer", "stock_move", move_id,
                              after={"product_id": product_id, "source": source_location_id, "destination": destination_location_id, "quantity": quantity})
        return {"move_id": move_id, "idempotent_replay": False}

    def create_count(self, principal: Principal, location_id: str, count_date: str,
                     lines: Iterable[dict], reason: str = "") -> dict:
        principal.require("inventory.adjust")
        validate_iso_date(count_date, "盘点日期")
        prepared = list(lines)
        if not prepared:
            raise ValidationError("盘点单至少包含一行")
        count_id = self._id("CNT")
        with self.store.connect() as conn:
            self._location(conn, principal.organization_id, location_id)
            number = next_number(conn, principal.organization_id, "stock_count")
            conn.execute(
                "INSERT INTO stock_counts(id,organization_id,document_number,location_id,status,count_date,reason,created_by) VALUES(?,?,?,?,?,?,?,?)",
                (count_id, principal.organization_id, number, location_id, "counting", count_date, reason.strip(), principal.user_id),
            )
            seen: set[tuple[str, str]] = set()
            for item in prepared:
                product_id = str(item["product_id"]); lot_id = str(item.get("lot_id", ""))
                key = (product_id, lot_id)
                if key in seen:
                    raise ValidationError("盘点单存在重复商品批次")
                seen.add(key)
                self._product(conn, principal.organization_id, product_id)
                balance = self._balance_row(conn, principal.organization_id, product_id, location_id, lot_id)
                counted = int(item["counted_quantity"])
                if counted < 0:
                    raise ValidationError("实盘数量不能为负")
                conn.execute(
                    "INSERT INTO stock_count_lines(id,count_id,product_id,lot_id,system_quantity,counted_quantity,variance_quantity,note) VALUES(?,?,?,?,?,?,?,?)",
                    (self._id("CTL"), count_id, product_id, lot_id, balance["on_hand"], counted,
                     counted - balance["on_hand"], str(item.get("note", "")).strip()),
                )
            conn.execute("UPDATE stock_counts SET status='pending_approval' WHERE id=?", (count_id,))
            self.audit.record(conn, AuditContext(principal), "stock_count.create", "stock_count", count_id,
                              after={"document_number": number, "line_count": len(prepared)})
        return self.count(principal, count_id)

    def post_count(self, principal: Principal, count_id: str) -> dict:
        principal.require("inventory.adjust")
        with self.store.connect() as conn:
            header = conn.execute("SELECT * FROM stock_counts WHERE id=? AND organization_id=?", (count_id, principal.organization_id)).fetchone()
            if not header:
                raise NotFound(f"盘点单不存在：{count_id}")
            if header["status"] != "pending_approval":
                raise Conflict(f"盘点单状态不允许过账：{header['status']}")
            lines = conn.execute("SELECT * FROM stock_count_lines WHERE count_id=?", (count_id,)).fetchall()
            for line in lines:
                current = self._balance_row(conn, principal.organization_id, line["product_id"], header["location_id"], line["lot_id"])
                if current["on_hand"] != line["system_quantity"]:
                    raise Conflict("盘点期间库存发生变化，请重新盘点")
                if line["counted_quantity"] < current["reserved"]:
                    raise Conflict("实盘数量低于已预占数量，不能过账")
                delta = line["variance_quantity"]
                if delta:
                    move_id = self._id("MOV")
                    conn.execute(
                        "UPDATE stock_balance SET on_hand=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE organization_id=? AND product_id=? AND location_id=? AND lot_id=?",
                        (line["counted_quantity"], principal.organization_id, line["product_id"], header["location_id"], line["lot_id"]),
                    )
                    conn.execute(
                        "INSERT INTO stock_moves(id,organization_id,event_key,product_id,source_location_id,destination_location_id,lot_id,quantity,move_type,reference_type,reference_id,reason,created_by) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (move_id, principal.organization_id, f"count:{count_id}:{line['id']}", line["product_id"],
                         header["location_id"] if delta < 0 else None, header["location_id"] if delta > 0 else None,
                         line["lot_id"], abs(delta), "adjustment", "stock_count", count_id, header["reason"], principal.user_id),
                    )
                    if delta > 0:
                        product = conn.execute("SELECT standard_cost_cents FROM product_master WHERE id=?", (line["product_id"],)).fetchone()
                        valued = self.valuation.receive(
                            conn, principal.organization_id, move_id, line["product_id"], header["location_id"],
                            line["lot_id"], delta, int(product["standard_cost_cents"]), "adjustment",
                        )
                        ledger_lines = [
                            {"account_code": "1405", "debit_cents": valued["value_cents"], "product_id": line["product_id"], "location_id": header["location_id"]},
                            {"account_code": "6711", "credit_cents": valued["value_cents"], "product_id": line["product_id"]},
                        ]
                    else:
                        valued = self.valuation.consume(
                            conn, principal.organization_id, move_id, line["product_id"], header["location_id"],
                            line["lot_id"], abs(delta),
                        )
                        ledger_lines = [
                            {"account_code": "6711", "debit_cents": valued["value_cents"], "product_id": line["product_id"]},
                            {"account_code": "1405", "credit_cents": valued["value_cents"], "product_id": line["product_id"], "location_id": header["location_id"]},
                        ]
                    if valued["value_cents"]:
                        self.accounting.post(conn, principal, "inventory", header["count_date"], "stock_count", count_id,
                                         f"stock-count:{count_id}:{line['id']}", header["reason"] or "库存盘点差异",
                                         ledger_lines)
            conn.execute(
                "UPDATE stock_counts SET status='posted',approved_by=?,posted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (principal.user_id, count_id),
            )
            self.audit.record(conn, AuditContext(principal), "stock_count.post", "stock_count", count_id,
                              before={"status": "pending_approval"}, after={"status": "posted"})
        return self.count(principal, count_id)

    def count(self, principal: Principal, count_id: str) -> dict:
        principal.require("inventory.read")
        header = self.store.row("SELECT * FROM stock_counts WHERE id=? AND organization_id=?", (count_id, principal.organization_id))
        if not header:
            raise NotFound(f"盘点单不存在：{count_id}")
        header["lines"] = self.store.rows(
            "SELECT l.*,p.sku,p.name AS product_name FROM stock_count_lines l JOIN product_master p ON p.id=l.product_id WHERE l.count_id=? ORDER BY p.sku",
            (count_id,),
        )
        return header

    def list_counts(self, principal: Principal, status: str = "", limit: int = 100) -> list[dict]:
        principal.require("inventory.read")
        filters = ["c.organization_id=?"]
        params: list[object] = [principal.organization_id]
        if status:
            filters.append("c.status=?"); params.append(status)
        params.append(max(1, min(limit, 500)))
        return self.store.rows(
            "SELECT c.*,l.code AS location_code,l.name AS location_name,"
            "COUNT(cl.id) AS line_count,COALESCE(SUM(ABS(cl.variance_quantity)),0) AS variance_quantity "
            "FROM stock_counts c JOIN storage_locations l ON l.id=c.location_id "
            "LEFT JOIN stock_count_lines cl ON cl.count_id=c.id "
            f"WHERE {' AND '.join(filters)} GROUP BY c.id ORDER BY c.count_date DESC,c.document_number DESC LIMIT ?",
            tuple(params),
        )

    def balance(self, principal: Principal, product_id: str, location_id: str, lot_id: str = "") -> dict:
        principal.require("inventory.read")
        row = self.store.row(
            "SELECT b.*,b.on_hand-b.reserved AS available,p.sku,p.name AS product_name,l.code AS location_code,l.name AS location_name "
            "FROM stock_balance b JOIN product_master p ON p.id=b.product_id JOIN storage_locations l ON l.id=b.location_id "
            "WHERE b.organization_id=? AND b.product_id=? AND b.location_id=? AND b.lot_id=?",
            (principal.organization_id, product_id, location_id, lot_id),
        )
        if not row:
            return {"organization_id": principal.organization_id, "product_id": product_id, "location_id": location_id,
                    "lot_id": lot_id, "on_hand": 0, "reserved": 0, "available": 0, "incoming": 0, "outgoing": 0}
        return row

    def list_balances(self, principal: Principal, site_id: str = "", product_id: str = "",
                      low_stock_only: bool = False) -> list[dict]:
        principal.require("inventory.read")
        filters = ["b.organization_id=?"]
        params: list[object] = [principal.organization_id]
        if site_id:
            filters.append("s.id=?"); params.append(site_id)
        if product_id:
            filters.append("b.product_id=?"); params.append(product_id)
        if low_stock_only:
            filters.append("b.on_hand-b.reserved<=p.min_stock")
        return self.store.rows(
            f"SELECT b.*,b.on_hand-b.reserved AS available,p.sku,p.name AS product_name,p.min_stock,p.max_stock,"
            f"l.code AS location_code,l.name AS location_name,s.code AS site_code FROM stock_balance b "
            f"JOIN product_master p ON p.id=b.product_id JOIN storage_locations l ON l.id=b.location_id JOIN sites s ON s.id=l.site_id "
            f"WHERE {' AND '.join(filters)} ORDER BY p.sku,s.code,l.code,b.lot_id", tuple(params),
        )

    def ledger(self, principal: Principal, product_id: str = "", location_id: str = "",
               reference_id: str = "", limit: int = 200) -> list[dict]:
        principal.require("inventory.read")
        filters = ["m.organization_id=?"]
        params: list[object] = [principal.organization_id]
        for column, value in (("m.product_id", product_id), ("m.reference_id", reference_id)):
            if value:
                filters.append(f"{column}=?"); params.append(value)
        if location_id:
            filters.append("(m.source_location_id=? OR m.destination_location_id=?)"); params.extend((location_id, location_id))
        params.append(max(1, min(limit, 1000)))
        return self.store.rows(
            f"SELECT m.*,p.sku,p.name AS product_name,sl.code AS source_code,dl.code AS destination_code "
            f"FROM stock_moves m JOIN product_master p ON p.id=m.product_id "
            f"LEFT JOIN storage_locations sl ON sl.id=m.source_location_id LEFT JOIN storage_locations dl ON dl.id=m.destination_location_id "
            f"WHERE {' AND '.join(filters)} ORDER BY m.occurred_at DESC,m.id DESC LIMIT ?", tuple(params),
        )

    @staticmethod
    def _outbox(conn: sqlite3.Connection, organization_id: str, event_type: str, aggregate_type: str,
                aggregate_id: str, payload: dict) -> None:
        conn.execute(
            "INSERT INTO outbox_events(id,organization_id,event_type,aggregate_type,aggregate_id,payload_json) VALUES(?,?,?,?,?,?)",
            (f"EVT-{uuid.uuid4().hex.upper()}", organization_id, event_type, aggregate_type, aggregate_id,
             json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )
