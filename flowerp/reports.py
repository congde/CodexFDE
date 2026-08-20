from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from .identity import Principal
from .models import ValidationError, validate_iso_date
from .store import ERPStore


class ReportService:
    def __init__(self, store: ERPStore) -> None:
        self.store = store

    def dashboard(self, principal: Principal) -> dict:
        principal.require("reports.read")
        org = principal.organization_id
        inventory = self.store.row(
            "SELECT COUNT(DISTINCT b.product_id) AS stocked_products,COALESCE(SUM(b.on_hand),0) AS on_hand,"
            "COALESCE(SUM(b.reserved),0) AS reserved,"
            "(SELECT COALESCE(SUM(v.remaining_value_cents),0) FROM inventory_valuation_layers v WHERE v.organization_id=?) AS inventory_value_cents "
            "FROM stock_balance b WHERE b.organization_id=?", (org, org)
        ) or {}
        sales = self.store.row(
            "SELECT COUNT(*) AS order_count,COALESCE(SUM(total_cents),0) AS sales_total_cents FROM sales_documents "
            "WHERE organization_id=? AND document_type='order' AND status<>'cancelled' AND order_date>=date('now','start of month')", (org,)
        ) or {}
        receivables = self.store.row(
            "SELECT COALESCE(SUM(outstanding_cents),0) AS receivable_cents,"
            "COALESCE(SUM(CASE WHEN due_date<date('now') THEN outstanding_cents ELSE 0 END),0) AS overdue_receivable_cents "
            "FROM invoices WHERE organization_id=? AND invoice_type='receivable' AND status IN ('issued','partially_paid')", (org,)
        ) or {}
        payables = self.store.row(
            "SELECT COALESCE(SUM(outstanding_cents),0) AS payable_cents FROM invoices WHERE organization_id=? AND invoice_type='payable' AND status IN ('issued','partially_paid')", (org,)
        ) or {}
        pending = {
            "sales_to_ship": int(self.store.scalar("SELECT COUNT(*) FROM sales_documents WHERE organization_id=? AND status IN ('reserved','partially_shipped')", (org,)) or 0),
            "purchases_to_approve": int(self.store.scalar("SELECT COUNT(*) FROM purchase_orders WHERE organization_id=? AND status='pending_approval'", (org,)) or 0),
            "purchases_to_receive": int(self.store.scalar("SELECT COUNT(*) FROM purchase_orders WHERE organization_id=? AND status IN ('approved','partially_received')", (org,)) or 0),
            "returns_to_receive": int(self.store.scalar("SELECT COUNT(*) FROM sales_returns WHERE organization_id=? AND status='authorized'", (org,)) or 0),
        }
        low_stock = self.store.rows(
            "SELECT p.id,p.sku,p.name,p.min_stock,COALESCE(SUM(b.on_hand-b.reserved),0) AS available FROM product_master p "
            "LEFT JOIN stock_balance b ON b.product_id=p.id WHERE p.organization_id=? AND p.active=1 GROUP BY p.id HAVING available<=p.min_stock ORDER BY available,p.sku LIMIT 20", (org,)
        )
        return {**inventory, **sales, **receivables, **payables, "pending": pending, "low_stock": low_stock,
                "generated_at": self.store.scalar("SELECT CURRENT_TIMESTAMP")}

    def dashboard_trends(self, principal: Principal, months: int = 12, as_of: str | None = None) -> dict:
        """Return month-end dashboard trends backed by documents and the posted general ledger."""
        principal.require("reports.read")
        if not 3 <= months <= 36:
            raise ValidationError("趋势月份必须在 3 到 36 之间")
        as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        month_start = as_of_date.replace(day=1)

        def shift_month(value: date, offset: int) -> date:
            index = value.year * 12 + value.month - 1 + offset
            return date(index // 12, index % 12 + 1, 1)

        starts = [shift_month(month_start, offset) for offset in range(1 - months, 1)]
        keys = [value.strftime("%Y-%m") for value in starts]
        first = starts[0].isoformat()
        sales_rows = self.store.rows(
            "SELECT substr(order_date,1,7) AS month,COALESCE(SUM(total_cents),0) AS value "
            "FROM sales_documents WHERE organization_id=? AND document_type='order' AND status<>'cancelled' "
            "AND order_date>=? AND order_date<=? GROUP BY substr(order_date,1,7)",
            (principal.organization_id, first, as_of_date.isoformat()),
        )
        sales = {row["month"]: int(row["value"]) for row in sales_rows}
        account_codes = ("1405", "1122", "2202")
        opening_rows = self.store.rows(
            "SELECT a.code,COALESCE(SUM(l.debit_cents-l.credit_cents),0) AS value FROM journal_lines l "
            "JOIN journal_entries e ON e.id=l.journal_entry_id JOIN ledger_accounts a ON a.id=l.account_id "
            "WHERE e.organization_id=? AND e.status='posted' AND e.posting_date<? AND a.code IN (?,?,?) GROUP BY a.code",
            (principal.organization_id, first, *account_codes),
        )
        balances = {code: 0 for code in account_codes}
        balances.update({row["code"]: int(row["value"]) for row in opening_rows})
        movement_rows = self.store.rows(
            "SELECT substr(e.posting_date,1,7) AS month,a.code,COALESCE(SUM(l.debit_cents-l.credit_cents),0) AS value "
            "FROM journal_lines l JOIN journal_entries e ON e.id=l.journal_entry_id "
            "JOIN ledger_accounts a ON a.id=l.account_id WHERE e.organization_id=? AND e.status='posted' "
            "AND e.posting_date>=? AND e.posting_date<=? AND a.code IN (?,?,?) GROUP BY substr(e.posting_date,1,7),a.code",
            (principal.organization_id, first, as_of_date.isoformat(), *account_codes),
        )
        movements = {(row["month"], row["code"]): int(row["value"]) for row in movement_rows}
        series = {"sales": [], "inventory": [], "receivable": [], "payable": []}
        for key in keys:
            for code in account_codes:
                balances[code] += movements.get((key, code), 0)
            series["sales"].append(sales.get(key, 0))
            series["inventory"].append(max(0, balances["1405"]))
            series["receivable"].append(max(0, balances["1122"]))
            series["payable"].append(max(0, -balances["2202"]))
        return {"months": keys, "series": series, "as_of": as_of_date.isoformat(), "currency": "CNY"}

    def sales_summary(self, principal: Principal, since: str, until: str, group_by: str = "day") -> list[dict]:
        principal.require("reports.read")
        validate_iso_date(since, "开始日期"); validate_iso_date(until, "结束日期")
        if since > until: raise ValidationError("开始日期不能晚于结束日期")
        if group_by not in {"day", "month", "customer", "product", "channel"}: raise ValidationError("汇总维度无效")
        if group_by == "product":
            return self.store.rows(
                "SELECT p.sku AS group_key,p.name AS group_name,SUM(l.shipped_quantity) AS quantity,SUM(l.total_cents) AS amount_cents "
                "FROM sales_document_lines l JOIN sales_documents d ON d.id=l.document_id JOIN product_master p ON p.id=l.product_id "
                "WHERE d.organization_id=? AND d.order_date BETWEEN ? AND ? AND d.status<>'cancelled' GROUP BY p.id ORDER BY amount_cents DESC",
                (principal.organization_id, since, until),
            )
        expressions = {"day": "d.order_date", "month": "substr(d.order_date,1,7)", "customer": "c.code", "channel": "d.channel"}
        expression = expressions[group_by]
        join = "JOIN customer_master c ON c.id=d.customer_id" if group_by == "customer" else ""
        return self.store.rows(
            f"SELECT {expression} AS group_key,COUNT(*) AS order_count,SUM(d.total_cents) AS amount_cents,"
            f"SUM(CASE WHEN d.status='shipped' THEN 1 ELSE 0 END) AS shipped_count FROM sales_documents d {join} "
            f"WHERE d.organization_id=? AND d.order_date BETWEEN ? AND ? AND d.document_type='order' AND d.status<>'cancelled' "
            f"GROUP BY {expression} ORDER BY group_key", (principal.organization_id, since, until),
        )

    def inventory_valuation(self, principal: Principal, site_id: str = "") -> list[dict]:
        principal.require("reports.read")
        params: list[object] = [principal.organization_id]
        where = "v.organization_id=? AND v.remaining_quantity>0"
        if site_id: where += " AND s.id=?"; params.append(site_id)
        return self.store.rows(
            f"SELECT p.sku,p.name,s.code AS site_code,SUM(v.remaining_quantity) AS on_hand,"
            f"CASE WHEN SUM(v.remaining_quantity)=0 THEN 0 ELSE SUM(v.remaining_value_cents)/SUM(v.remaining_quantity) END AS average_cost_cents,"
            f"SUM(v.remaining_value_cents) AS value_cents,COUNT(v.id) AS open_layers "
            f"FROM inventory_valuation_layers v JOIN product_master p ON p.id=v.product_id "
            f"JOIN storage_locations l ON l.id=v.location_id JOIN sites s ON s.id=l.site_id WHERE {where} "
            f"GROUP BY p.id,s.id ORDER BY p.sku,s.code", tuple(params),
        )

    def inventory_aging(self, principal: Principal, as_of: str | None = None) -> list[dict]:
        principal.require("reports.read")
        as_of = as_of or date.today().isoformat(); validate_iso_date(as_of, "截止日期")
        return self.store.rows(
            "SELECT p.sku,p.name,l.lot_number,l.expiry_date,b.on_hand,b.reserved,b.on_hand-b.reserved AS available,"
            "CAST(julianday(?) - julianday(COALESCE(l.manufacture_date,l.created_at)) AS INTEGER) AS age_days,"
            "CAST(julianday(l.expiry_date)-julianday(?) AS INTEGER) AS days_to_expiry "
            "FROM stock_balance b JOIN product_master p ON p.id=b.product_id LEFT JOIN stock_lots l ON l.id=b.lot_id "
            "WHERE b.organization_id=? AND b.on_hand>0 ORDER BY age_days DESC,p.sku", (as_of, as_of, principal.organization_id),
        )

    def ar_aging(self, principal: Principal, as_of: str | None = None) -> dict:
        principal.require("finance.read")
        as_of = as_of or date.today().isoformat(); validate_iso_date(as_of, "截止日期")
        rows = self.store.rows(
            "SELECT i.id,i.invoice_number,i.partner_id,c.code AS partner_code,c.name AS partner_name,i.due_date,i.outstanding_cents,"
            "CAST(julianday(?) - julianday(i.due_date) AS INTEGER) AS overdue_days FROM invoices i JOIN customer_master c ON c.id=i.partner_id "
            "WHERE i.organization_id=? AND i.invoice_type='receivable' AND i.status IN ('issued','partially_paid') ORDER BY i.due_date",
            (as_of, principal.organization_id),
        )
        buckets = {"current": 0, "1_30": 0, "31_60": 0, "61_90": 0, "over_90": 0}
        for row in rows:
            days = row["overdue_days"]
            key = "current" if days <= 0 else "1_30" if days <= 30 else "31_60" if days <= 60 else "61_90" if days <= 90 else "over_90"
            buckets[key] += row["outstanding_cents"]; row["bucket"] = key
        return {"as_of": as_of, "buckets": buckets, "total_cents": sum(buckets.values()), "items": rows}

    def reorder_suggestions(self, principal: Principal, site_id: str = "") -> list[dict]:
        principal.require("purchase.read")
        params: list[object] = [principal.organization_id]
        site_filter = ""
        if site_id: site_filter = " AND s.id=?"; params.append(site_id)
        return self.store.rows(
            "SELECT p.id AS product_id,p.sku,p.name,p.min_stock,p.max_stock,COALESCE(SUM(b.on_hand-b.reserved+b.incoming),0) AS projected,"
            "MAX(0,p.max_stock-COALESCE(SUM(b.on_hand-b.reserved+b.incoming),0)) AS suggested_quantity "
            "FROM product_master p LEFT JOIN stock_balance b ON b.product_id=p.id LEFT JOIN storage_locations l ON l.id=b.location_id "
            f"LEFT JOIN sites s ON s.id=l.site_id WHERE p.organization_id=? AND p.active=1 AND p.purchasable=1{site_filter} "
            "GROUP BY p.id HAVING projected<=p.min_stock AND suggested_quantity>0 ORDER BY suggested_quantity DESC,p.sku", tuple(params),
        )

    @staticmethod
    def to_csv(rows: list[dict]) -> str:
        if not rows: return ""
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
        return output.getvalue()
