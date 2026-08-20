from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta

from .audit import AuditContext, AuditService
from .identity import Principal
from .models import InvalidTransition, NotFound, ValidationError
from .store import ERPStore


class AlertService:
    """Materialize actionable ERP exceptions without relying on an external service."""

    def __init__(self, store: ERPStore, audit: AuditService | None=None) -> None:
        self.store=store;self.audit=audit or AuditService(store)

    @staticmethod
    def _id() -> str:
        return f"ALT-{uuid.uuid4().hex.upper()}"

    def refresh(self, principal: Principal) -> dict:
        principal.require("reports.read")
        generated=0;resolved=0
        with self.store.connect() as conn:
            candidates=self._candidates(conn,principal.organization_id)
            active_keys={(row["alert_type"],row["entity_type"],row["entity_id"]) for row in candidates}
            existing=conn.execute("SELECT * FROM alerts WHERE organization_id=? AND status IN ('open','acknowledged')",
                                  (principal.organization_id,)).fetchall()
            for row in existing:
                key=(row["alert_type"],row["entity_type"],row["entity_id"])
                if key not in active_keys:
                    conn.execute("UPDATE alerts SET status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE id=?",(row["id"],));resolved+=1
            for candidate in candidates:
                found=conn.execute(
                    "SELECT id FROM alerts WHERE organization_id=? AND alert_type=? AND entity_type=? AND entity_id=? AND status IN ('open','acknowledged')",
                    (principal.organization_id,candidate["alert_type"],candidate["entity_type"],candidate["entity_id"]),
                ).fetchone()
                if found:continue
                conn.execute(
                    "INSERT INTO alerts(id,organization_id,alert_type,severity,title,message,entity_type,entity_id) VALUES(?,?,?,?,?,?,?,?)",
                    (self._id(),principal.organization_id,candidate["alert_type"],candidate["severity"],candidate["title"],
                     candidate["message"],candidate["entity_type"],candidate["entity_id"]),
                );generated+=1
            self.audit.record(conn,AuditContext(principal),"alerts.refresh","organization",principal.organization_id,
                              after={"generated":generated,"resolved":resolved,"active_candidates":len(candidates)})
        return {"generated":generated,"resolved":resolved,"open":len(self.list(principal,status="open"))}

    @staticmethod
    def _candidates(conn: sqlite3.Connection, organization_id: str) -> list[dict]:
        result:list[dict]=[]
        low_stock=conn.execute(
            "SELECT p.id,p.sku,p.name,p.min_stock,COALESCE(SUM(b.on_hand-b.reserved),0) AS available FROM product_master p "
            "LEFT JOIN stock_balance b ON b.product_id=p.id WHERE p.organization_id=? AND p.active=1 GROUP BY p.id HAVING available<=p.min_stock",
            (organization_id,),
        ).fetchall()
        result.extend({"alert_type":"low_stock","severity":"warning","title":f"{row['name']} 库存不足",
                       "message":f"SKU {row['sku']} 可用 {row['available']}，补货点 {row['min_stock']}",
                       "entity_type":"product","entity_id":row["id"]} for row in low_stock)
        overdue=conn.execute(
            "SELECT i.id,i.invoice_number,i.due_date,i.outstanding_cents,c.name FROM invoices i JOIN customer_master c ON c.id=i.partner_id "
            "WHERE i.organization_id=? AND i.invoice_type='receivable' AND i.status IN ('issued','partially_paid') AND i.due_date<date('now')",
            (organization_id,),
        ).fetchall()
        result.extend({"alert_type":"overdue_receivable","severity":"critical","title":f"{row['name']} 应收逾期",
                       "message":f"发票 {row['invoice_number']} 到期 {row['due_date']}，未收 {row['outstanding_cents']} 分",
                       "entity_type":"invoice","entity_id":row["id"]} for row in overdue)
        expiring=conn.execute(
            "SELECT l.id,l.lot_number,l.expiry_date,p.name FROM stock_lots l JOIN product_master p ON p.id=l.product_id "
            "JOIN stock_balance b ON b.lot_id=l.id WHERE l.organization_id=? AND l.status='active' AND b.on_hand>0 "
            "AND l.expiry_date IS NOT NULL AND l.expiry_date<=date('now','+30 day')",
            (organization_id,),
        ).fetchall()
        result.extend({"alert_type":"lot_expiring","severity":"warning","title":f"{row['name']} 批次临期",
                       "message":f"批次 {row['lot_number']} 将于 {row['expiry_date']} 到期",
                       "entity_type":"stock_lot","entity_id":row["id"]} for row in expiring)
        delayed=conn.execute(
            "SELECT id,order_number,expected_date FROM purchase_orders WHERE organization_id=? "
            "AND status IN ('approved','partially_received') AND expected_date<date('now')",(organization_id,)
        ).fetchall()
        result.extend({"alert_type":"purchase_delayed","severity":"warning","title":f"采购单 {row['order_number']} 逾期未收齐",
                       "message":f"预计到货日 {row['expected_date']} 已过","entity_type":"purchase_order","entity_id":row["id"]} for row in delayed)
        return result

    def acknowledge(self, principal: Principal, alert_id: str) -> dict:
        principal.require("reports.read")
        with self.store.connect() as conn:
            row=conn.execute("SELECT * FROM alerts WHERE id=? AND organization_id=?",(alert_id,principal.organization_id)).fetchone()
            if not row:raise NotFound(f"预警不存在：{alert_id}")
            if row["status"]!="open":raise InvalidTransition("只有未处理预警可以确认")
            conn.execute("UPDATE alerts SET status='acknowledged',acknowledged_by=?,acknowledged_at=CURRENT_TIMESTAMP WHERE id=?",
                         (principal.user_id,alert_id))
            self.audit.record(conn,AuditContext(principal),"alert.acknowledge","alert",alert_id,
                              before={"status":"open"},after={"status":"acknowledged"})
        return self.get(principal,alert_id)

    def dismiss(self, principal: Principal, alert_id: str, reason: str) -> dict:
        principal.require("settings.manage")
        if not reason.strip():raise ValidationError("忽略预警必须填写原因")
        with self.store.connect() as conn:
            row=conn.execute("SELECT * FROM alerts WHERE id=? AND organization_id=?",(alert_id,principal.organization_id)).fetchone()
            if not row:raise NotFound(f"预警不存在：{alert_id}")
            if row["status"] not in {"open","acknowledged"}:raise InvalidTransition("当前状态不能忽略")
            conn.execute("UPDATE alerts SET status='dismissed',resolved_at=CURRENT_TIMESTAMP WHERE id=?",(alert_id,))
            self.audit.record(conn,AuditContext(principal),"alert.dismiss","alert",alert_id,
                              before={"status":row["status"]},after={"status":"dismissed","reason":reason})
        return self.get(principal,alert_id)

    def get(self, principal: Principal, alert_id: str) -> dict:
        principal.require("reports.read")
        row=self.store.row("SELECT * FROM alerts WHERE id=? AND organization_id=?",(alert_id,principal.organization_id))
        if not row:raise NotFound(f"预警不存在：{alert_id}")
        return row

    def list(self, principal: Principal, status: str="",severity: str="",limit: int=200) -> list[dict]:
        principal.require("reports.read")
        filters=["organization_id=?"];params:list[object]=[principal.organization_id]
        if status:filters.append("status=?");params.append(status)
        if severity:filters.append("severity=?");params.append(severity)
        params.append(max(1,min(limit,1000)))
        return self.store.rows(f"SELECT * FROM alerts WHERE {' AND '.join(filters)} ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,created_at DESC LIMIT ?",tuple(params))
