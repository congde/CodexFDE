from __future__ import annotations

import sqlite3
import uuid
from typing import Iterable

from .audit import AuditContext, AuditService
from .identity import Principal
from .models import Conflict, InvalidTransition, NotFound, ValidationError
from .store import ERPStore


class SerialNumberService:
    """Lifecycle and uniqueness rules for individually tracked products."""

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)

    @staticmethod
    def _id() -> str:
        return f"SER-{uuid.uuid4().hex.upper()}"

    def register(self, principal: Principal, product_id: str, serial_numbers: Iterable[str],
                 location_id: str, lot_id: str | None = None) -> list[dict]:
        principal.require("inventory.receive")
        prepared = [value.strip().upper() for value in serial_numbers]
        if not prepared or any(not value for value in prepared): raise ValidationError("序列号不能为空")
        if len(prepared) != len(set(prepared)): raise ValidationError("本次登记包含重复序列号")
        created: list[str] = []
        with self.store.connect() as conn:
            product = conn.execute("SELECT * FROM product_master WHERE id=? AND organization_id=?", (product_id, principal.organization_id)).fetchone()
            if not product: raise NotFound(f"商品不存在：{product_id}")
            if product["tracking"] != "serial": raise ValidationError("商品未启用序列号跟踪")
            location = conn.execute(
                "SELECT l.id FROM storage_locations l JOIN sites s ON s.id=l.site_id WHERE l.id=? AND s.organization_id=?",
                (location_id, principal.organization_id),
            ).fetchone()
            if not location: raise NotFound(f"库位不存在：{location_id}")
            placeholders=",".join("?" for _ in prepared)
            duplicate=conn.execute(
                f"SELECT serial_number FROM serial_numbers WHERE organization_id=? AND serial_number IN ({placeholders}) LIMIT 1",
                (principal.organization_id,*prepared),
            ).fetchone()
            if duplicate:raise Conflict(f"序列号已存在：{duplicate['serial_number']}")
            balance = conn.execute(
                "SELECT COALESCE(SUM(on_hand),0) AS on_hand FROM stock_balance WHERE organization_id=? AND product_id=? AND location_id=?",
                (principal.organization_id, product_id, location_id),
            ).fetchone()["on_hand"]
            assigned = conn.execute(
                "SELECT COUNT(*) FROM serial_numbers WHERE organization_id=? AND product_id=? AND current_location_id=? "
                "AND status IN ('available','reserved','returned','damaged')",
                (principal.organization_id, product_id, location_id),
            ).fetchone()[0]
            if assigned + len(prepared) > balance:
                raise ValidationError(f"序列号数量超过该库位实物库存：库存 {balance}，已登记 {assigned}，本次 {len(prepared)}")
            for value in prepared:
                serial_id = self._id()
                try:
                    conn.execute(
                        "INSERT INTO serial_numbers(id,organization_id,product_id,serial_number,lot_id,status,current_location_id) VALUES(?,?,?,?,?,'available',?)",
                        (serial_id, principal.organization_id, product_id, value, lot_id, location_id),
                    )
                except sqlite3.IntegrityError as exc:
                    raise Conflict(f"序列号已存在：{value}") from exc
                created.append(serial_id)
            self.audit.record(conn, AuditContext(principal), "serial.register", "product", product_id,
                              after={"location_id": location_id, "serial_numbers": prepared})
        return [self.get(principal, serial_id) for serial_id in created]

    def transition(self, principal: Principal, serial_id: str, target_status: str,
                   location_id: str | None = None, reason: str = "") -> dict:
        permission = "inventory.ship" if target_status == "shipped" else "inventory.adjust"
        principal.require(permission)
        allowed = {
            "available": {"reserved", "damaged", "scrapped"},
            "reserved": {"available", "shipped"},
            "shipped": {"returned"},
            "returned": {"available", "damaged", "scrapped"},
            "damaged": {"available", "scrapped"},
            "scrapped": set(),
        }
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM serial_numbers WHERE id=? AND organization_id=?", (serial_id, principal.organization_id)).fetchone()
            if not row: raise NotFound(f"序列号不存在：{serial_id}")
            if target_status not in allowed[row["status"]]:
                raise InvalidTransition(f"序列号状态不能从 {row['status']} 迁移到 {target_status}")
            if target_status in {"available", "returned", "damaged"} and not (location_id or row["current_location_id"]):
                raise ValidationError("在库序列号必须指定库位")
            next_location = None if target_status in {"shipped", "scrapped"} else (location_id or row["current_location_id"])
            conn.execute("UPDATE serial_numbers SET status=?,current_location_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (target_status, next_location, serial_id))
            self.audit.record(conn, AuditContext(principal), "serial.transition", "serial_number", serial_id,
                              before={"status": row["status"], "location_id": row["current_location_id"]},
                              after={"status": target_status, "location_id": next_location, "reason": reason})
        return self.get(principal, serial_id)

    def get(self, principal: Principal, serial_id_or_number: str) -> dict:
        principal.require("inventory.read")
        row = self.store.row(
            "SELECT s.*,p.sku,p.name AS product_name,l.code AS location_code FROM serial_numbers s "
            "JOIN product_master p ON p.id=s.product_id LEFT JOIN storage_locations l ON l.id=s.current_location_id "
            "WHERE s.organization_id=? AND (s.id=? OR s.serial_number=? COLLATE NOCASE)",
            (principal.organization_id, serial_id_or_number, serial_id_or_number),
        )
        if not row: raise NotFound(f"序列号不存在：{serial_id_or_number}")
        return row

    def list(self, principal: Principal, product_id: str = "", status: str = "",
             location_id: str = "", limit: int = 500) -> list[dict]:
        principal.require("inventory.read")
        filters = ["s.organization_id=?"]; params: list[object] = [principal.organization_id]
        for column,value in (("s.product_id",product_id),("s.status",status),("s.current_location_id",location_id)):
            if value: filters.append(f"{column}=?");params.append(value)
        params.append(max(1,min(limit,1000)))
        return self.store.rows(
            f"SELECT s.*,p.sku,p.name AS product_name,l.code AS location_code,lot.lot_number FROM serial_numbers s "
            f"JOIN product_master p ON p.id=s.product_id LEFT JOIN storage_locations l ON l.id=s.current_location_id "
            f"LEFT JOIN stock_lots lot ON lot.id=s.lot_id "
            f"WHERE {' AND '.join(filters)} ORDER BY s.updated_at DESC LIMIT ?", tuple(params),
        )
