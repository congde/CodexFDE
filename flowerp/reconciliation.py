from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .audit import AuditContext, AuditService
from .accounting import LedgerService
from .identity import Principal
from .models import NotFound, ValidationError
from .store import ERPStore


class ReconciliationService:
    """Cross-check denormalized balances against immutable source documents."""

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store=store;self.audit=audit or AuditService(store);self.ledger=LedgerService(store)

    def run_inventory(self, principal: Principal) -> dict:
        principal.require("audit.read")
        discrepancies: list[dict] = []
        negative=self.store.rows(
            "SELECT * FROM stock_balance WHERE organization_id=? AND (on_hand<0 OR reserved<0 OR reserved>on_hand)",
            (principal.organization_id,),
        )
        discrepancies.extend({"type":"balance_invariant","balance":row} for row in negative)
        reservation_rows=self.store.rows(
            "SELECT b.product_id,b.location_id,b.lot_id,b.reserved,b.outgoing,"
            "COALESCE(SUM(CASE WHEN r.status='active' THEN r.quantity ELSE 0 END),0) AS reservation_total "
            "FROM stock_balance b LEFT JOIN stock_reservations r ON r.organization_id=b.organization_id AND r.product_id=b.product_id "
            "AND r.location_id=b.location_id AND r.lot_id=b.lot_id WHERE b.organization_id=? GROUP BY b.product_id,b.location_id,b.lot_id "
            "HAVING b.reserved<>reservation_total OR b.outgoing<>reservation_total", (principal.organization_id,),
        )
        discrepancies.extend({"type":"reservation_mismatch","balance":row} for row in reservation_rows)
        sales_rows=self.store.rows(
            "SELECT l.id,l.reserved_quantity,COALESCE(SUM(CASE WHEN r.status='active' THEN r.quantity ELSE 0 END),0) AS reservation_total "
            "FROM sales_document_lines l JOIN sales_documents d ON d.id=l.document_id LEFT JOIN stock_reservations r ON r.reference_line_id=l.id "
            "WHERE d.organization_id=? GROUP BY l.id HAVING l.reserved_quantity<>reservation_total",(principal.organization_id,),
        )
        discrepancies.extend({"type":"sales_reservation_mismatch","line":row} for row in sales_rows)
        serial_rows=self.store.rows(
            "SELECT s.product_id,s.current_location_id,COUNT(*) AS serial_count,COALESCE(b.on_hand,0) AS on_hand "
            "FROM serial_numbers s LEFT JOIN stock_balance b ON b.organization_id=s.organization_id AND b.product_id=s.product_id "
            "AND b.location_id=s.current_location_id WHERE s.organization_id=? AND s.status IN ('available','reserved','returned','damaged') "
            "GROUP BY s.product_id,s.current_location_id HAVING serial_count>on_hand",(principal.organization_id,),
        )
        discrepancies.extend({"type":"serial_exceeds_balance","serial":row} for row in serial_rows)
        incoming_rows=self.store.rows(
            "SELECT b.product_id,loc.site_id,SUM(b.incoming) AS incoming,"
            "MAX(COALESCE(expected.expected_quantity,0)) AS expected_quantity "
            "FROM stock_balance b JOIN storage_locations loc ON loc.id=b.location_id "
            "LEFT JOIN (SELECT po.organization_id,pol.product_id,po.warehouse_id,"
            "SUM(pol.ordered_quantity-pol.received_quantity-pol.rejected_quantity) AS expected_quantity "
            "FROM purchase_orders po JOIN purchase_order_lines pol ON pol.purchase_order_id=po.id "
            "WHERE po.status IN ('approved','partially_received') "
            "GROUP BY po.organization_id,pol.product_id,po.warehouse_id) expected "
            "ON expected.organization_id=b.organization_id AND expected.product_id=b.product_id "
            "AND expected.warehouse_id=loc.site_id "
            "WHERE b.organization_id=? AND b.lot_id='' GROUP BY b.product_id,loc.site_id "
            "HAVING incoming<>expected_quantity",
            (principal.organization_id,),
        )
        discrepancies.extend({"type":"incoming_purchase_mismatch","balance":row} for row in incoming_rows)
        checked=int(self.store.scalar("SELECT COUNT(*) FROM stock_balance WHERE organization_id=?",(principal.organization_id,)) or 0)
        return self._save(principal,"inventory",checked,discrepancies)

    def run_sales(self, principal: Principal) -> dict:
        principal.require("audit.read")
        discrepancies=[]
        totals=self.store.rows(
            "SELECT d.id,d.document_number,d.subtotal_cents,d.tax_cents,d.total_cents,"
            "COALESCE(SUM(l.net_cents),0) AS line_subtotal,COALESCE(SUM(l.tax_cents),0) AS line_tax "
            "FROM sales_documents d LEFT JOIN sales_document_lines l ON l.document_id=d.id WHERE d.organization_id=? "
            "GROUP BY d.id HAVING d.subtotal_cents<>line_subtotal OR d.tax_cents<>line_tax OR d.total_cents<>line_subtotal+line_tax+d.freight_cents",
            (principal.organization_id,),
        )
        discrepancies.extend({"type":"sales_total_mismatch","document":row} for row in totals)
        quantities=self.store.rows(
            "SELECT l.* FROM sales_document_lines l JOIN sales_documents d ON d.id=l.document_id WHERE d.organization_id=? "
            "AND (l.shipped_quantity>l.ordered_quantity OR l.returned_quantity>l.shipped_quantity OR l.reserved_quantity>l.ordered_quantity-l.shipped_quantity)",
            (principal.organization_id,),
        )
        discrepancies.extend({"type":"sales_quantity_invariant","line":row} for row in quantities)
        checked=int(self.store.scalar("SELECT COUNT(*) FROM sales_documents WHERE organization_id=?",(principal.organization_id,)) or 0)
        return self._save(principal,"sales",checked,discrepancies)

    def run_finance(self, principal: Principal) -> dict:
        principal.require("audit.read")
        discrepancies=[]
        invoices=self.store.rows(
            "SELECT i.id,i.invoice_number,i.total_cents,i.paid_cents,i.outstanding_cents,COALESCE(SUM(a.amount_cents),0) AS allocated "
            "FROM invoices i LEFT JOIN payment_allocations a ON a.invoice_id=i.id WHERE i.organization_id=? GROUP BY i.id "
            "HAVING i.paid_cents<>allocated OR (i.status<>'void' AND i.total_cents<>i.paid_cents+i.outstanding_cents)",
            (principal.organization_id,),
        )
        discrepancies.extend({"type":"invoice_allocation_mismatch","invoice":row} for row in invoices)
        overallocated=self.store.rows(
            "SELECT p.id,p.payment_number,p.amount_cents,COALESCE(SUM(a.amount_cents),0) AS allocated FROM payments p "
            "LEFT JOIN payment_allocations a ON a.payment_id=p.id WHERE p.organization_id=? GROUP BY p.id HAVING allocated>p.amount_cents",
            (principal.organization_id,),
        )
        discrepancies.extend({"type":"payment_overallocated","payment":row} for row in overallocated)
        checked=int(self.store.scalar("SELECT COUNT(*) FROM invoices WHERE organization_id=?",(principal.organization_id,)) or 0)
        return self._save(principal,"finance",checked,discrepancies)

    def run_all(self, principal: Principal) -> dict:
        return {"inventory":self.run_inventory(principal),"sales":self.run_sales(principal),
                "finance":self.run_finance(principal),"accounting":self.run_accounting(principal)}

    def run_accounting(self, principal: Principal) -> dict:
        principal.require("audit.read")
        trial = self.ledger.trial_balance(principal)
        subledgers = self.ledger.reconcile_subledgers(principal)
        discrepancies: list[dict] = []
        if not trial["balanced"]:
            discrepancies.append({"type":"journal_imbalance","debit_cents":trial["debit_cents"],
                                  "credit_cents":trial["credit_cents"]})
        discrepancies.extend({"type":"subledger_mismatch",**item} for item in subledgers["checks"] if not item["ok"])
        checked = len(trial["accounts"]) + len(subledgers["checks"])
        return self._save(principal,"accounting",checked,discrepancies)

    def _save(self, principal: Principal, reconciliation_type: str, checked: int, discrepancies: list[dict]) -> dict:
        reconciliation_id=f"REC-{uuid.uuid4().hex.upper()}";status="passed" if not discrepancies else "failed"
        result={"discrepancies":discrepancies}
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO reconciliations(id,organization_id,reconciliation_type,as_of,status,checked_items,discrepancy_count,result_json,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                (reconciliation_id,principal.organization_id,reconciliation_type,datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 status,checked,len(discrepancies),json.dumps(result,ensure_ascii=False),principal.user_id),
            )
            self.audit.record(conn,AuditContext(principal),"reconciliation.run","reconciliation",reconciliation_id,
                              after={"type":reconciliation_type,"status":status,"checked":checked,"discrepancies":len(discrepancies)})
        return self.get(principal,reconciliation_id)

    def get(self, principal: Principal, reconciliation_id: str) -> dict:
        principal.require("audit.read")
        row=self.store.row("SELECT * FROM reconciliations WHERE id=? AND organization_id=?",(reconciliation_id,principal.organization_id))
        if not row:raise NotFound(f"对账任务不存在：{reconciliation_id}")
        row["result"]=json.loads(row.pop("result_json"));return row

    def list(self, principal: Principal, limit: int=100) -> list[dict]:
        principal.require("audit.read")
        return self.store.rows("SELECT id,reconciliation_type,as_of,status,checked_items,discrepancy_count,created_by,created_at FROM reconciliations WHERE organization_id=? ORDER BY created_at DESC LIMIT ?",
                               (principal.organization_id,max(1,min(limit,500))))
