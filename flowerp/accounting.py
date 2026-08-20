from __future__ import annotations

import sqlite3
import uuid
from datetime import date
from typing import Iterable

from .identity import Principal
from .models import Conflict, NotFound, PeriodClosed, ValidationError, validate_iso_date
from .numbering import next_number
from .store import ERPStore


DEFAULT_ACCOUNTS = (
    ("1001", "库存现金", "asset", "cash", "debit"),
    ("1002", "银行存款", "asset", "bank", "debit"),
    ("1122", "应收账款", "asset", "receivable", "debit"),
    ("1123", "供应商预付款", "asset", "advance", "debit"),
    ("1405", "库存商品", "asset", "inventory", "debit"),
    ("2202", "应付账款", "liability", "payable", "credit"),
    ("2203", "采购收货暂估", "liability", "grni", "credit"),
    ("2204", "客户预收款", "liability", "advance", "credit"),
    ("2221", "销项税额", "liability", "tax", "credit"),
    ("1221", "进项税额", "asset", "tax", "debit"),
    ("5001", "销售收入", "income", "revenue", "credit"),
    ("5002", "销售退回", "income", "revenue", "credit"),
    ("6001", "销售成本", "expense", "cogs", "debit"),
    ("6002", "采购价格差异", "expense", "variance", "debit"),
    ("6051", "采购运费", "expense", "variance", "debit"),
    ("6711", "库存盘盈盘亏", "expense", "variance", "debit"),
    ("3101", "期初及库存调整权益", "equity", "variance", "credit"),
)


class LedgerService:
    """Immutable operational double-entry ledger, not a statutory localized GL."""

    def __init__(self, store: ERPStore) -> None:
        self.store = store

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    @staticmethod
    def ensure_accounts(conn: sqlite3.Connection, organization_id: str) -> None:
        for code, name, account_type, control_type, normal_side in DEFAULT_ACCOUNTS:
            conn.execute(
                "INSERT OR IGNORE INTO ledger_accounts(id,organization_id,code,name,account_type,control_type,normal_side) "
                "VALUES(?,?,?,?,?,?,?)",
                (f"ACC-{organization_id}-{code}", organization_id, code, name, account_type, control_type, normal_side),
            )

    @staticmethod
    def assert_period_open(conn: sqlite3.Connection, organization_id: str, posting_date: str) -> None:
        validate_iso_date(posting_date, "过账日期")
        moment = date.fromisoformat(posting_date)
        row = conn.execute(
            "SELECT status FROM accounting_periods WHERE organization_id=? AND year=? AND month=?",
            (organization_id, moment.year, moment.month),
        ).fetchone()
        if row and row["status"] == "closed":
            raise PeriodClosed(f"财务期间已关闭：{moment.year}-{moment.month:02d}")

    def post(self, conn: sqlite3.Connection, principal: Principal, journal_type: str, posting_date: str,
             source_type: str, source_id: str, source_event: str, description: str,
             lines: Iterable[dict], currency: str = "CNY", reversal_of_id: str | None = None) -> dict:
        self.assert_period_open(conn, principal.organization_id, posting_date)
        existing = conn.execute(
            "SELECT * FROM journal_entries WHERE organization_id=? AND source_event=?",
            (principal.organization_id, source_event),
        ).fetchone()
        if existing:
            if existing["status"] != "posted": raise Conflict("会计事件存在未完成凭证")
            return dict(existing)
        prepared = [dict(item) for item in lines]
        if len(prepared) < 2: raise ValidationError("会计凭证至少需要两行")
        debit = sum(int(item.get("debit_cents", 0)) for item in prepared)
        credit = sum(int(item.get("credit_cents", 0)) for item in prepared)
        if debit <= 0 or debit != credit:
            raise Conflict(f"借贷不平衡：借方 {debit}，贷方 {credit}")
        self.ensure_accounts(conn, principal.organization_id)
        codes = {str(item["account_code"]) for item in prepared}
        accounts = {row["code"]: row for row in conn.execute(
            f"SELECT * FROM ledger_accounts WHERE organization_id=? AND active=1 AND code IN ({','.join('?' for _ in codes)})",
            (principal.organization_id, *codes),
        ).fetchall()}
        missing = sorted(codes - set(accounts))
        if missing: raise ValidationError(f"会计科目不存在或停用：{missing}")
        entry_id = self._id("JRN"); number = next_number(conn, principal.organization_id, "journal_entry")
        conn.execute(
            "INSERT INTO journal_entries(id,organization_id,entry_number,journal_type,posting_date,source_type,source_id,source_event,description,currency,status,reversal_of_id,posted_by,posted_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'draft',?,?,CURRENT_TIMESTAMP)",
            (entry_id, principal.organization_id, number, journal_type, posting_date, source_type, source_id,
             source_event, description.strip(), currency, reversal_of_id, principal.user_id),
        )
        for index, item in enumerate(prepared, 1):
            debit_value = int(item.get("debit_cents", 0)); credit_value = int(item.get("credit_cents", 0))
            if (debit_value > 0) == (credit_value > 0): raise ValidationError("凭证明细必须且只能有借方或贷方金额")
            conn.execute(
                "INSERT INTO journal_lines(id,journal_entry_id,line_number,account_id,description,debit_cents,credit_cents,partner_type,partner_id,product_id,location_id,lot_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (self._id("JNL"), entry_id, index, accounts[str(item["account_code"])]["id"],
                 str(item.get("description", "")), debit_value, credit_value,
                 str(item.get("partner_type", "")), str(item.get("partner_id", "")),
                 item.get("product_id"), item.get("location_id"), str(item.get("lot_id", ""))),
            )
        totals = conn.execute(
            "SELECT SUM(debit_cents) AS debit,SUM(credit_cents) AS credit FROM journal_lines WHERE journal_entry_id=?",
            (entry_id,),
        ).fetchone()
        if totals["debit"] != totals["credit"]: raise Conflict("数据库凭证明细借贷不平衡")
        conn.execute("UPDATE journal_entries SET status='posted' WHERE id=? AND status='draft'", (entry_id,))
        return dict(conn.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone())

    def reverse(self, conn: sqlite3.Connection, principal: Principal, entry_id: str,
                source_event: str, posting_date: str, reason: str) -> dict:
        if not reason.strip(): raise ValidationError("冲销必须填写原因")
        original = conn.execute(
            "SELECT * FROM journal_entries WHERE id=? AND organization_id=? AND status='posted'",
            (entry_id, principal.organization_id),
        ).fetchone()
        if not original: raise NotFound(f"已过账凭证不存在：{entry_id}")
        prior = conn.execute("SELECT id FROM journal_entries WHERE reversal_of_id=?", (entry_id,)).fetchone()
        if prior: return dict(conn.execute("SELECT * FROM journal_entries WHERE id=?", (prior["id"],)).fetchone())
        rows = conn.execute(
            "SELECT l.*,a.code AS account_code FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id "
            "WHERE l.journal_entry_id=? ORDER BY l.line_number", (entry_id,),
        ).fetchall()
        return self.post(conn, principal, "reversal", posting_date, original["source_type"], original["source_id"],
                         source_event, f"冲销 {original['entry_number']}：{reason.strip()}",
                         [{"account_code": row["account_code"], "debit_cents": row["credit_cents"],
                           "credit_cents": row["debit_cents"], "description": row["description"],
                           "partner_type": row["partner_type"], "partner_id": row["partner_id"],
                           "product_id": row["product_id"], "location_id": row["location_id"],
                           "lot_id": row["lot_id"]} for row in rows], original["currency"], entry_id)

    def entry(self, principal: Principal, entry_id: str) -> dict:
        principal.require("finance.read")
        row = self.store.row(
            "SELECT * FROM journal_entries WHERE id=? AND organization_id=?", (entry_id, principal.organization_id)
        )
        if not row: raise NotFound(f"凭证不存在：{entry_id}")
        row["lines"] = self.store.rows(
            "SELECT l.*,a.code AS account_code,a.name AS account_name FROM journal_lines l "
            "JOIN ledger_accounts a ON a.id=l.account_id WHERE l.journal_entry_id=? ORDER BY l.line_number", (entry_id,)
        )
        row["debit_cents"] = sum(item["debit_cents"] for item in row["lines"])
        row["credit_cents"] = sum(item["credit_cents"] for item in row["lines"])
        return row

    def list_entries(self, principal: Principal, since: str = "", until: str = "",
                     source_type: str = "", limit: int = 200) -> list[dict]:
        principal.require("finance.read")
        filters = ["organization_id=?", "status='posted'"]; params: list[object] = [principal.organization_id]
        if since: validate_iso_date(since, "开始日期"); filters.append("posting_date>=?"); params.append(since)
        if until: validate_iso_date(until, "结束日期"); filters.append("posting_date<=?"); params.append(until)
        if source_type: filters.append("source_type=?"); params.append(source_type)
        params.append(max(1, min(limit, 1000)))
        return self.store.rows(
            f"SELECT e.*,(SELECT SUM(debit_cents) FROM journal_lines WHERE journal_entry_id=e.id) AS total_cents "
            f"FROM journal_entries e WHERE {' AND '.join(filters)} ORDER BY posting_date DESC,entry_number DESC LIMIT ?",
            tuple(params),
        )

    def list_accounts(self, principal: Principal) -> list[dict]:
        principal.require("finance.read")
        with self.store.connect() as conn:
            self.ensure_accounts(conn, principal.organization_id)
        return self.store.rows(
            "SELECT id,code,name,account_type,control_type,normal_side,active FROM ledger_accounts "
            "WHERE organization_id=? ORDER BY code", (principal.organization_id,),
        )

    def account_statement(self, principal: Principal, account_code: str, since: str = "",
                          until: str = "", partner_id: str = "", product_id: str = "",
                          limit: int = 1000) -> dict:
        principal.require("finance.read")
        if since: validate_iso_date(since, "开始日期")
        if until: validate_iso_date(until, "结束日期")
        if since and until and since > until: raise ValidationError("开始日期不能晚于结束日期")
        account = self.store.row(
            "SELECT * FROM ledger_accounts WHERE organization_id=? AND code=? AND active=1",
            (principal.organization_id, account_code),
        )
        if not account: raise NotFound(f"会计科目不存在：{account_code}")
        dimensions = ""; dimension_params: list[object] = []
        if partner_id: dimensions += " AND l.partner_id=?"; dimension_params.append(partner_id)
        if product_id: dimensions += " AND l.product_id=?"; dimension_params.append(product_id)
        opening = 0
        if since:
            opening = int(self.store.scalar(
                "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_lines l "
                "JOIN journal_entries e ON e.id=l.journal_entry_id WHERE l.account_id=? AND e.status='posted' "
                f"AND e.posting_date<?{dimensions}", (account["id"], since, *dimension_params),
            ) or 0)
        filters = ["l.account_id=?", "e.status='posted'"]; params: list[object] = [account["id"]]
        if since: filters.append("e.posting_date>=?"); params.append(since)
        if until: filters.append("e.posting_date<=?"); params.append(until)
        if partner_id: filters.append("l.partner_id=?"); params.append(partner_id)
        if product_id: filters.append("l.product_id=?"); params.append(product_id)
        params.append(max(1, min(limit, 5000)))
        rows = self.store.rows(
            "SELECT e.id AS journal_entry_id,e.entry_number,e.posting_date,e.source_type,e.source_id,e.description AS entry_description,"
            "l.line_number,l.description,l.debit_cents,l.credit_cents,l.partner_type,l.partner_id,l.product_id,l.location_id,l.lot_id "
            f"FROM journal_lines l JOIN journal_entries e ON e.id=l.journal_entry_id WHERE {' AND '.join(filters)} "
            "ORDER BY e.posting_date,e.entry_number,l.line_number LIMIT ?", tuple(params),
        )
        running = opening
        for row in rows:
            running += int(row["debit_cents"]) - int(row["credit_cents"])
            row["running_balance_cents"] = running if account["normal_side"] == "debit" else -running
        return {"account": account, "since": since or None, "until": until or None,
                "opening_balance_cents": opening if account["normal_side"] == "debit" else -opening,
                "closing_balance_cents": running if account["normal_side"] == "debit" else -running,
                "items": rows}

    def trial_balance(self, principal: Principal, as_of: str | None = None) -> dict:
        principal.require("finance.read")
        as_of = as_of or date.today().isoformat(); validate_iso_date(as_of, "截止日期")
        rows = self.store.rows(
            "SELECT a.code,a.name,a.account_type,a.normal_side,COALESCE(SUM(CASE WHEN e.id IS NOT NULL THEN l.debit_cents ELSE 0 END),0) AS debit_cents,"
            "COALESCE(SUM(CASE WHEN e.id IS NOT NULL THEN l.credit_cents ELSE 0 END),0) AS credit_cents FROM ledger_accounts a "
            "LEFT JOIN journal_lines l ON l.account_id=a.id LEFT JOIN journal_entries e ON e.id=l.journal_entry_id "
            "AND e.status='posted' AND e.posting_date<=? WHERE a.organization_id=? GROUP BY a.id ORDER BY a.code",
            (as_of, principal.organization_id),
        )
        for row in rows: row["balance_cents"] = row["debit_cents"] - row["credit_cents"]
        debit = sum(row["debit_cents"] for row in rows); credit = sum(row["credit_cents"] for row in rows)
        return {"as_of": as_of, "debit_cents": debit, "credit_cents": credit,
                "balanced": debit == credit, "accounts": rows}

    def financial_statements(self, principal: Principal, as_of: str | None = None) -> dict:
        trial = self.trial_balance(principal, as_of)
        sections: dict[str, list[dict]] = {"assets": [], "liabilities": [], "equity": [], "income": [], "expenses": []}
        totals = {key: 0 for key in sections}
        mapping = {"asset": "assets", "liability": "liabilities", "equity": "equity",
                   "income": "income", "expense": "expenses"}
        for account in trial["accounts"]:
            section = mapping[account["account_type"]]
            amount = account["balance_cents"] if account["normal_side"] == "debit" else -account["balance_cents"]
            if amount:
                sections[section].append({"code": account["code"], "name": account["name"], "amount_cents": amount})
            totals[section] += amount
        profit = totals["income"] - totals["expenses"]
        equation_right = totals["liabilities"] + totals["equity"] + profit
        return {"as_of": trial["as_of"], "sections": sections, "totals": totals,
                "current_profit_cents": profit, "balance_sheet_balanced": totals["assets"] == equation_right,
                "balance_sheet_difference_cents": totals["assets"] - equation_right}

    def reconcile_subledgers(self, principal: Principal) -> dict:
        principal.require("finance.read")
        organization_id = principal.organization_id
        def account_balance(code: str) -> int:
            return int(self.store.scalar(
                "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_lines l "
                "JOIN journal_entries e ON e.id=l.journal_entry_id JOIN ledger_accounts a ON a.id=l.account_id "
                "WHERE e.organization_id=? AND e.status='posted' AND a.code=?", (organization_id, code),
            ) or 0)
        inventory_subledger = int(self.store.scalar(
            "SELECT COALESCE(SUM(remaining_value_cents),0) FROM inventory_valuation_layers WHERE organization_id=?",
            (organization_id,),
        ) or 0)
        ar_subledger = int(self.store.scalar(
            "SELECT COALESCE(SUM(CASE WHEN invoice_type='receivable' THEN outstanding_cents "
            "WHEN invoice_type='credit_note' THEN -outstanding_cents ELSE 0 END),0) FROM invoices "
            "WHERE organization_id=? AND status<>'void'", (organization_id,),
        ) or 0)
        ap_subledger = int(self.store.scalar(
            "SELECT COALESCE(SUM(outstanding_cents),0) FROM invoices WHERE organization_id=? "
            "AND invoice_type='payable' AND status<>'void'", (organization_id,),
        ) or 0)
        grni_subledger = int(self.store.scalar(
            "SELECT COALESCE(SUM((pl.received_quantity-COALESCE(billed.quantity,0))*pl.unit_price_cents),0) "
            "FROM purchase_order_lines pl JOIN purchase_orders po ON po.id=pl.purchase_order_id "
            "LEFT JOIN (SELECT il.source_line_id,SUM(il.quantity) AS quantity FROM invoice_lines il "
            "JOIN invoices i ON i.id=il.invoice_id WHERE i.status<>'void' GROUP BY il.source_line_id) billed "
            "ON billed.source_line_id=pl.id WHERE po.organization_id=?",
            (organization_id,),
        ) or 0)
        checks = [
            {"name": "inventory", "subledger_cents": inventory_subledger, "ledger_cents": account_balance("1405")},
            {"name": "receivables", "subledger_cents": ar_subledger, "ledger_cents": account_balance("1122")},
            {"name": "payables", "subledger_cents": ap_subledger, "ledger_cents": -account_balance("2202")},
            {"name": "goods_received_not_invoiced", "subledger_cents": grni_subledger, "ledger_cents": -account_balance("2203")},
        ]
        for item in checks:
            item["difference_cents"] = item["subledger_cents"] - item["ledger_cents"]
            item["ok"] = item["difference_cents"] == 0
        return {"ok": all(item["ok"] for item in checks), "checks": checks}


class InventoryValuationService:
    """FIFO valuation layers bound to immutable stock movements."""

    def __init__(self, store: ERPStore) -> None:
        self.store = store

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    def receive(self, conn: sqlite3.Connection, organization_id: str, stock_move_id: str,
                product_id: str, location_id: str, lot_id: str, quantity: int,
                unit_cost_cents: int, layer_type: str = "receipt") -> dict:
        if quantity <= 0: raise ValidationError("估值入库数量必须大于 0")
        if unit_cost_cents <= 0:
            product = conn.execute("SELECT standard_cost_cents FROM product_master WHERE id=?", (product_id,)).fetchone()
            unit_cost_cents = int(product["standard_cost_cents"] if product else 0)
        if unit_cost_cents < 0: raise ValidationError("估值成本不能为负")
        value = quantity * unit_cost_cents; layer_id = self._id("VAL")
        conn.execute(
            "INSERT INTO inventory_valuation_layers(id,organization_id,product_id,location_id,lot_id,stock_move_id,layer_type,original_quantity,remaining_quantity,unit_cost_cents,original_value_cents,remaining_value_cents) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (layer_id, organization_id, product_id, location_id, lot_id, stock_move_id, layer_type,
             quantity, quantity, unit_cost_cents, value, value),
        )
        conn.execute("UPDATE stock_moves SET unit_cost_cents=?,total_cost_cents=?,valuation_status='valued' WHERE id=?",
                     (unit_cost_cents, value, stock_move_id))
        return {"layer_id": layer_id, "quantity": quantity, "unit_cost_cents": unit_cost_cents, "value_cents": value}

    def consume(self, conn: sqlite3.Connection, organization_id: str, stock_move_id: str,
                product_id: str, location_id: str, lot_id: str, quantity: int) -> dict:
        if quantity <= 0: raise ValidationError("估值出库数量必须大于 0")
        layers = conn.execute(
            "SELECT * FROM inventory_valuation_layers WHERE organization_id=? AND product_id=? AND location_id=? "
            "AND lot_id=? AND remaining_quantity>0 ORDER BY occurred_at,rowid",
            (organization_id, product_id, location_id, lot_id),
        ).fetchall()
        available = sum(int(row["remaining_quantity"]) for row in layers)
        if available < quantity: raise Conflict(f"库存估值层不足：需要 {quantity}，可估值 {available}")
        remaining = quantity; total = 0; slices: list[dict] = []
        for layer in layers:
            if remaining == 0: break
            layer_quantity = int(layer["remaining_quantity"]); layer_value = int(layer["remaining_value_cents"])
            used = min(remaining, layer_quantity)
            # The final slice absorbs integer-cent allocation remainders, so transfers never destroy value.
            value = layer_value if used == layer_quantity else used * layer_value // layer_quantity
            conn.execute(
                "UPDATE inventory_valuation_layers SET remaining_quantity=remaining_quantity-?,remaining_value_cents=remaining_value_cents-? "
                "WHERE id=? AND remaining_quantity>=? AND remaining_value_cents>=?",
                (used, value, layer["id"], used, value),
            )
            conn.execute(
                "INSERT INTO inventory_valuation_consumptions(id,stock_move_id,valuation_layer_id,quantity,value_cents) VALUES(?,?,?,?,?)",
                (self._id("VCO"), stock_move_id, layer["id"], used, value),
            )
            slices.append({"layer_id": layer["id"], "quantity": used, "unit_cost_cents": layer["unit_cost_cents"], "value_cents": value})
            remaining -= used; total += value
        conn.execute("UPDATE stock_moves SET unit_cost_cents=?,total_cost_cents=?,valuation_status='valued' WHERE id=?",
                     (total // quantity, total, stock_move_id))
        return {"quantity": quantity, "value_cents": total, "layers": slices}

    def transfer_in(self, conn: sqlite3.Connection, organization_id: str, stock_move_id: str,
                    product_id: str, location_id: str, lot_id: str, quantity: int, value_cents: int) -> dict:
        if quantity <= 0 or value_cents < 0: raise ValidationError("调拨估值数量或金额无效")
        unit_cost = value_cents // quantity; layer_id = self._id("VAL")
        conn.execute(
            "INSERT INTO inventory_valuation_layers(id,organization_id,product_id,location_id,lot_id,stock_move_id,layer_type,original_quantity,remaining_quantity,unit_cost_cents,original_value_cents,remaining_value_cents) "
            "VALUES(?,?,?,?,?,?, 'transfer_in',?,?,?,?,?)",
            (layer_id, organization_id, product_id, location_id, lot_id, stock_move_id,
             quantity, quantity, unit_cost, value_cents, value_cents),
        )
        conn.execute("UPDATE stock_moves SET unit_cost_cents=?,total_cost_cents=?,valuation_status='valued' WHERE id=?",
                     (unit_cost, value_cents, stock_move_id))
        return {"layer_id": layer_id, "quantity": quantity, "unit_cost_cents": unit_cost, "value_cents": value_cents}
