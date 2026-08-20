from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date

from .audit import AuditContext, AuditService
from .identity import Principal
from .models import Conflict, NotFound, ValidationError, validate_iso_date
from .store import ERPStore


class CashManagementService:
    """Bank-account, statement import and payment reconciliation controls."""

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    @staticmethod
    def _masked(value: str) -> str:
        compact = "".join(character for character in value.strip() if character.isalnum())
        if not compact:
            return ""
        return compact if len(compact) <= 4 else f"****{compact[-4:]}"

    def create_account(self, principal: Principal, code: str, name: str, bank_name: str,
                       ledger_account_code: str = "1002", currency: str = "CNY",
                       account_number: str = "", opening_balance_cents: int = 0) -> dict:
        principal.require("finance.write")
        code = code.strip().upper(); name = name.strip(); bank_name = bank_name.strip()
        ledger_account_code = ledger_account_code.strip()
        currency = currency.strip().upper()
        if not code or not name or not bank_name: raise ValidationError("银行账户代码、名称和开户行不能为空")
        if len(currency) != 3 or not currency.isalpha(): raise ValidationError("币种必须是三位 ISO 代码")
        account_id = self._id("BNK")
        with self.store.connect() as conn:
            ledger = conn.execute(
                "SELECT * FROM ledger_accounts WHERE organization_id=? AND code=?",
                (principal.organization_id, ledger_account_code),
            ).fetchone()
            if ledger and (ledger["account_type"] != "asset" or ledger["control_type"] != "bank"):
                raise ValidationError("银行账户必须绑定资产类银行控制科目")
            if not ledger:
                ledger_id = self._id("ACC")
                conn.execute(
                    "INSERT INTO ledger_accounts(id,organization_id,code,name,account_type,control_type,normal_side,allow_manual) "
                    "VALUES(?,?,?,?,'asset','bank','debit',0)",
                    (ledger_id, principal.organization_id, ledger_account_code, f"银行存款-{name}"),
                )
            else:
                ledger_id = ledger["id"]
            try:
                conn.execute(
                    "INSERT INTO bank_accounts(id,organization_id,code,name,bank_name,account_number_masked,currency,ledger_account_id,opening_balance_cents,created_by) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (account_id, principal.organization_id, code, name, bank_name, self._masked(account_number),
                     currency, ledger_id, int(opening_balance_cents), principal.user_id),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict("银行账户代码或总账控制科目已被占用") from exc
            self.audit.record(conn, AuditContext(principal), "bank_account.create", "bank_account", account_id,
                              after={"code": code, "currency": currency, "ledger_account_code": ledger_account_code})
        return self.account(principal, account_id)

    def account(self, principal: Principal, account_id: str) -> dict:
        principal.require("finance.read")
        row = self.store.row(
            "SELECT b.*,a.code AS ledger_account_code,a.name AS ledger_account_name FROM bank_accounts b "
            "JOIN ledger_accounts a ON a.id=b.ledger_account_id WHERE b.id=? AND b.organization_id=?",
            (account_id, principal.organization_id),
        )
        if not row: raise NotFound(f"银行账户不存在：{account_id}")
        row.update(self._account_balances(principal.organization_id, account_id, row["ledger_account_id"], int(row["opening_balance_cents"])))
        return row

    def list_accounts(self, principal: Principal, active_only: bool = True) -> list[dict]:
        principal.require("finance.read")
        where = " AND b.status='active'" if active_only else ""
        rows = self.store.rows(
            "SELECT b.*,a.code AS ledger_account_code,a.name AS ledger_account_name FROM bank_accounts b "
            "JOIN ledger_accounts a ON a.id=b.ledger_account_id WHERE b.organization_id=?" + where + " ORDER BY b.code",
            (principal.organization_id,),
        )
        for row in rows:
            row.update(self._account_balances(principal.organization_id, row["id"], row["ledger_account_id"], int(row["opening_balance_cents"])))
        return rows

    def _account_balances(self, organization_id: str, account_id: str, ledger_account_id: str,
                          opening_balance_cents: int) -> dict:
        ledger_movement = int(self.store.scalar(
            "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_lines l "
            "JOIN journal_entries e ON e.id=l.journal_entry_id WHERE e.organization_id=? AND e.status='posted' AND l.account_id=?",
            (organization_id, ledger_account_id),
        ) or 0)
        latest = self.store.row(
            "SELECT closing_balance_cents,period_end FROM bank_statements WHERE bank_account_id=? ORDER BY period_end DESC,created_at DESC LIMIT 1",
            (account_id,),
        )
        unmatched = int(self.store.scalar(
            "SELECT COUNT(*) FROM bank_statement_lines WHERE bank_account_id=? AND status='unmatched'", (account_id,)
        ) or 0)
        return {"book_balance_cents": opening_balance_cents + ledger_movement,
                "latest_statement_balance_cents": int(latest["closing_balance_cents"]) if latest else None,
                "latest_statement_date": latest["period_end"] if latest else None,
                "unmatched_line_count": unmatched}

    def import_statement(self, principal: Principal, bank_account_id: str, statement_number: str,
                         period_start: str, period_end: str, opening_balance_cents: int,
                         closing_balance_cents: int, lines: list[dict]) -> dict:
        principal.require("finance.write")
        statement_number = statement_number.strip()
        validate_iso_date(period_start, "对账单开始日期"); validate_iso_date(period_end, "对账单结束日期")
        if date.fromisoformat(period_start) > date.fromisoformat(period_end): raise ValidationError("对账单开始日期不能晚于结束日期")
        if not statement_number: raise ValidationError("对账单号不能为空")
        if not lines: raise ValidationError("银行对账单至少包含一条流水")
        prepared: list[dict] = []
        external_ids: set[str] = set()
        for index, raw in enumerate(lines, 1):
            external_id = str(raw.get("external_transaction_id", "")).strip()
            transaction_date = str(raw.get("transaction_date", ""))
            value_date = str(raw.get("value_date") or transaction_date)
            validate_iso_date(transaction_date, f"第 {index} 行交易日期")
            validate_iso_date(value_date, f"第 {index} 行入账日期")
            if not period_start <= transaction_date <= period_end: raise ValidationError(f"第 {index} 行交易日期不在对账单期间")
            amount = int(raw.get("signed_amount_cents", 0))
            if not external_id: raise ValidationError(f"第 {index} 行外部流水号不能为空")
            if external_id in external_ids: raise ValidationError(f"对账单内银行流水号重复：{external_id}")
            if amount == 0: raise ValidationError(f"第 {index} 行流水金额不能为零")
            external_ids.add(external_id)
            prepared.append({"external_transaction_id": external_id, "transaction_date": transaction_date,
                             "value_date": value_date, "signed_amount_cents": amount,
                             "counterparty_name": str(raw.get("counterparty_name", "")).strip(),
                             "reference": str(raw.get("reference", "")).strip(),
                             "description": str(raw.get("description", "")).strip()})
        if int(opening_balance_cents) + sum(item["signed_amount_cents"] for item in prepared) != int(closing_balance_cents):
            raise ValidationError("对账单不平：期初余额加流水净额必须等于期末余额")
        fingerprint = hashlib.sha256(json.dumps({"bank_account_id": bank_account_id,
            "statement_number": statement_number, "period_start": period_start, "period_end": period_end,
            "opening_balance_cents": int(opening_balance_cents), "closing_balance_cents": int(closing_balance_cents),
            "lines": prepared}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        statement_id = self._id("BST")
        with self.store.connect() as conn:
            account = conn.execute("SELECT * FROM bank_accounts WHERE id=? AND organization_id=? AND status='active'",
                                   (bank_account_id, principal.organization_id)).fetchone()
            if not account: raise NotFound(f"可用银行账户不存在：{bank_account_id}")
            existing = conn.execute("SELECT * FROM bank_statements WHERE bank_account_id=? AND statement_number=?",
                                    (bank_account_id, statement_number)).fetchone()
            if existing:
                if existing["import_hash"] == fingerprint:
                    result = self._statement_with_connection(conn, principal.organization_id, existing["id"])
                    result["idempotent_replay"] = True
                    return result
                raise Conflict("同一对账单号已导入，但内容不一致")
            prior = conn.execute(
                "SELECT closing_balance_cents,period_end FROM bank_statements WHERE bank_account_id=? AND period_end<? ORDER BY period_end DESC LIMIT 1",
                (bank_account_id, period_start),
            ).fetchone()
            if prior and int(prior["closing_balance_cents"]) != int(opening_balance_cents):
                raise Conflict("对账单期初余额与上一期末余额不衔接")
            try:
                conn.execute(
                    "INSERT INTO bank_statements(id,organization_id,bank_account_id,statement_number,period_start,period_end,opening_balance_cents,closing_balance_cents,currency,import_hash,created_by) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (statement_id, principal.organization_id, bank_account_id, statement_number, period_start, period_end,
                     int(opening_balance_cents), int(closing_balance_cents), account["currency"], fingerprint, principal.user_id),
                )
                for item in prepared:
                    conn.execute(
                        "INSERT INTO bank_statement_lines(id,statement_id,bank_account_id,external_transaction_id,transaction_date,value_date,signed_amount_cents,counterparty_name,reference,description) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (self._id("BSL"), statement_id, bank_account_id, item["external_transaction_id"],
                         item["transaction_date"], item["value_date"], item["signed_amount_cents"],
                         item["counterparty_name"], item["reference"], item["description"]),
                    )
            except sqlite3.IntegrityError as exc:
                raise Conflict("银行流水号已在该账户的其他对账单中导入") from exc
            self.audit.record(conn, AuditContext(principal), "bank_statement.import", "bank_statement", statement_id,
                              after={"statement_number": statement_number, "line_count": len(prepared),
                                     "closing_balance_cents": int(closing_balance_cents)})
        result = self.statement(principal, statement_id); result["idempotent_replay"] = False
        return result

    def list_statements(self, principal: Principal, bank_account_id: str = "", status: str = "",
                        limit: int = 100) -> list[dict]:
        principal.require("finance.read")
        filters = ["s.organization_id=?"]; params: list[object] = [principal.organization_id]
        if bank_account_id: filters.append("s.bank_account_id=?"); params.append(bank_account_id)
        if status: filters.append("s.status=?"); params.append(status)
        params.append(max(1, min(int(limit), 500)))
        return self.store.rows(
            "SELECT s.*,b.code AS bank_account_code,b.name AS bank_account_name,"
            "COUNT(l.id) AS line_count,SUM(CASE WHEN l.status='matched' THEN 1 ELSE 0 END) AS matched_count "
            "FROM bank_statements s JOIN bank_accounts b ON b.id=s.bank_account_id "
            "LEFT JOIN bank_statement_lines l ON l.statement_id=s.id WHERE " + " AND ".join(filters) +
            " GROUP BY s.id ORDER BY s.period_end DESC,s.created_at DESC LIMIT ?", tuple(params))

    def statement(self, principal: Principal, statement_id: str) -> dict:
        principal.require("finance.read")
        with self.store.connect() as conn:
            result = self._statement_with_connection(conn, principal.organization_id, statement_id)
        if not result: raise NotFound(f"银行对账单不存在：{statement_id}")
        return result

    @staticmethod
    def _statement_with_connection(conn: sqlite3.Connection, organization_id: str, statement_id: str) -> dict:
        row = conn.execute(
            "SELECT s.*,b.code AS bank_account_code,b.name AS bank_account_name,b.ledger_account_id "
            "FROM bank_statements s JOIN bank_accounts b ON b.id=s.bank_account_id WHERE s.id=? AND s.organization_id=?",
            (statement_id, organization_id),
        ).fetchone()
        if not row: return {}
        result = dict(row)
        result["lines"] = [dict(item) for item in conn.execute(
            "SELECT l.*,m.payment_id,p.payment_number,p.payment_type,p.partner_id,m.matched_at FROM bank_statement_lines l "
            "LEFT JOIN bank_payment_matches m ON m.statement_line_id=l.id LEFT JOIN payments p ON p.id=m.payment_id "
            "WHERE l.statement_id=? ORDER BY l.transaction_date,l.created_at", (statement_id,)).fetchall()]
        result["line_count"] = len(result["lines"])
        result["matched_count"] = sum(1 for item in result["lines"] if item["status"] == "matched")
        result["unmatched_count"] = result["line_count"] - result["matched_count"]
        return result

    def match_candidates(self, principal: Principal, statement_line_id: str, date_tolerance_days: int = 3) -> list[dict]:
        principal.require("finance.read")
        tolerance = max(0, min(int(date_tolerance_days), 30))
        line = self.store.row(
            "SELECT l.*,s.currency,s.organization_id FROM bank_statement_lines l JOIN bank_statements s ON s.id=l.statement_id "
            "WHERE l.id=? AND s.organization_id=?", (statement_line_id, principal.organization_id))
        if not line: raise NotFound(f"银行流水不存在：{statement_line_id}")
        if line["status"] == "matched": return []
        expected = "receipt" if int(line["signed_amount_cents"]) > 0 else "disbursement,refund"
        types = expected.split(",")
        placeholders = ",".join("?" for _ in types)
        rows = self.store.rows(
            "SELECT p.*,COALESCE(c.name,s.name,'') AS partner_name FROM payments p "
            "LEFT JOIN customer_master c ON p.partner_type='customer' AND c.id=p.partner_id "
            "LEFT JOIN supplier_master s ON p.partner_type='supplier' AND s.id=p.partner_id "
            "LEFT JOIN bank_payment_matches m ON m.payment_id=p.id "
            f"WHERE p.organization_id=? AND p.status='posted' AND p.method<>'cash' AND p.currency=? AND p.amount_cents=? "
            f"AND p.payment_type IN ({placeholders}) AND m.id IS NULL AND (p.bank_account_id IS NULL OR p.bank_account_id=?)",
            (principal.organization_id, line["currency"], abs(int(line["signed_amount_cents"])), *types, line["bank_account_id"]),
        )
        transaction_day = date.fromisoformat(line["transaction_date"])
        candidates: list[dict] = []
        haystack = f"{line['reference']} {line['description']} {line['counterparty_name']}".casefold()
        for payment in rows:
            difference = abs((date.fromisoformat(payment["payment_date"]) - transaction_day).days)
            if difference > tolerance: continue
            score = 70 + max(0, 20 - difference * 5)
            external_reference = str(payment["external_reference"])
            if external_reference and not external_reference.startswith("__internal__") and external_reference.casefold() in haystack:
                score += 60
            if payment["partner_name"] and str(payment["partner_name"]).casefold() in haystack: score += 15
            payment["score"] = score; payment["date_difference_days"] = difference
            candidates.append(payment)
        return sorted(candidates, key=lambda item: (-int(item["score"]), int(item["date_difference_days"]), item["payment_number"]))

    def confirm_match(self, principal: Principal, statement_line_id: str, payment_id: str) -> dict:
        principal.require("finance.write")
        with self.store.connect() as conn:
            line = conn.execute(
                "SELECT l.*,s.organization_id,s.currency,s.status AS statement_status,b.ledger_account_id FROM bank_statement_lines l "
                "JOIN bank_statements s ON s.id=l.statement_id JOIN bank_accounts b ON b.id=l.bank_account_id "
                "WHERE l.id=? AND s.organization_id=?", (statement_line_id, principal.organization_id)).fetchone()
            if not line: raise NotFound(f"银行流水不存在：{statement_line_id}")
            if line["statement_status"] == "reconciled": raise Conflict("已完成的银行对账单不能修改匹配")
            if line["status"] != "unmatched": raise Conflict("银行流水已完成匹配")
            payment = conn.execute("SELECT * FROM payments WHERE id=? AND organization_id=?",
                                   (payment_id, principal.organization_id)).fetchone()
            if not payment: raise NotFound(f"收付款不存在：{payment_id}")
            expected_positive = payment["payment_type"] == "receipt"
            if (int(line["signed_amount_cents"]) > 0) != expected_positive: raise ValidationError("银行流水收支方向与收付款类型不一致")
            if payment["status"] != "posted" or payment["method"] == "cash": raise ValidationError("只能匹配已过账的非现金收付款")
            if payment["currency"] != line["currency"] or int(payment["amount_cents"]) != abs(int(line["signed_amount_cents"])):
                raise ValidationError("银行流水与收付款币种或金额不一致")
            if payment["bank_account_id"] and payment["bank_account_id"] != line["bank_account_id"]:
                raise Conflict("收付款已归属其他银行账户")
            ledger_amount = conn.execute(
                "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_entries e JOIN journal_lines l ON l.journal_entry_id=e.id "
                "WHERE e.organization_id=? AND e.source_type='payment' AND e.source_id=? AND e.status='posted' AND l.account_id=?",
                (principal.organization_id, payment_id, line["ledger_account_id"]),
            ).fetchone()[0]
            if int(ledger_amount or 0) != int(line["signed_amount_cents"]):
                raise Conflict("收付款凭证未记入该银行账户的控制科目")
            try:
                conn.execute("INSERT INTO bank_payment_matches(id,statement_line_id,payment_id,amount_cents,matched_by) VALUES(?,?,?,?,?)",
                             (self._id("BMT"), statement_line_id, payment_id, abs(int(line["signed_amount_cents"])), principal.user_id))
            except sqlite3.IntegrityError as exc:
                raise Conflict("银行流水或收付款已经被其他记录匹配") from exc
            conn.execute("UPDATE bank_statement_lines SET status='matched' WHERE id=?", (statement_line_id,))
            if not payment["bank_account_id"]:
                conn.execute("UPDATE payments SET bank_account_id=? WHERE id=?", (line["bank_account_id"], payment_id))
            self.audit.record(conn, AuditContext(principal), "bank_statement.match", "bank_statement_line", statement_line_id,
                              after={"payment_id": payment_id, "amount_cents": abs(int(line["signed_amount_cents"]))})
        return self.statement(principal, line["statement_id"])

    def auto_match(self, principal: Principal, statement_id: str) -> dict:
        principal.require("finance.write")
        statement = self.statement(principal, statement_id)
        if statement["status"] == "reconciled": raise Conflict("已完成的银行对账单不能重新匹配")
        matched = 0; ambiguous = 0
        for line in statement["lines"]:
            if line["status"] != "unmatched": continue
            candidates = self.match_candidates(principal, line["id"])
            if candidates and candidates[0]["score"] >= 90 and (len(candidates) == 1 or candidates[0]["score"] > candidates[1]["score"]):
                self.confirm_match(principal, line["id"], candidates[0]["id"]); matched += 1
            elif candidates:
                ambiguous += 1
        result = self.statement(principal, statement_id)
        result["auto_match"] = {"matched_count": matched, "ambiguous_count": ambiguous,
                                "remaining_count": result["unmatched_count"]}
        return result

    def unmatch(self, principal: Principal, statement_line_id: str, reason: str) -> dict:
        principal.require("finance.write")
        if not reason.strip(): raise ValidationError("取消匹配必须填写原因")
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT l.*,s.organization_id,s.status AS statement_status,m.payment_id FROM bank_statement_lines l "
                "JOIN bank_statements s ON s.id=l.statement_id LEFT JOIN bank_payment_matches m ON m.statement_line_id=l.id "
                "WHERE l.id=? AND s.organization_id=?", (statement_line_id, principal.organization_id)).fetchone()
            if not row: raise NotFound(f"银行流水不存在：{statement_line_id}")
            if row["statement_status"] == "reconciled": raise Conflict("已完成的银行对账单不能取消匹配")
            if row["status"] != "matched" or not row["payment_id"]: raise Conflict("银行流水尚未匹配")
            conn.execute("DELETE FROM bank_payment_matches WHERE statement_line_id=?", (statement_line_id,))
            conn.execute("UPDATE bank_statement_lines SET status='unmatched' WHERE id=?", (statement_line_id,))
            self.audit.record(conn, AuditContext(principal), "bank_statement.unmatch", "bank_statement_line", statement_line_id,
                              before={"payment_id": row["payment_id"]}, after={"reason": reason.strip()})
        return self.statement(principal, row["statement_id"])

    def reconcile(self, principal: Principal, statement_id: str) -> dict:
        principal.require("finance.close")
        with self.store.connect() as conn:
            statement = self._statement_with_connection(conn, principal.organization_id, statement_id)
            if not statement: raise NotFound(f"银行对账单不存在：{statement_id}")
            if statement["status"] == "reconciled": return statement
            if statement["unmatched_count"]: raise Conflict(f"仍有 {statement['unmatched_count']} 条银行流水未匹配")
            account = conn.execute("SELECT * FROM bank_accounts WHERE id=?", (statement["bank_account_id"],)).fetchone()
            ledger_movement = int(conn.execute(
                "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_lines l JOIN journal_entries e ON e.id=l.journal_entry_id "
                "WHERE e.organization_id=? AND e.status='posted' AND l.account_id=? AND e.posting_date<=?",
                (principal.organization_id, account["ledger_account_id"], statement["period_end"]),).fetchone()[0] or 0)
            book_balance = int(account["opening_balance_cents"]) + ledger_movement
            difference = book_balance - int(statement["closing_balance_cents"])
            if difference: raise Conflict(f"银行账与总账不一致：差异 {difference} 分")
            conn.execute("UPDATE bank_statements SET status='reconciled',reconciled_by=?,reconciled_at=CURRENT_TIMESTAMP WHERE id=?",
                         (principal.user_id, statement_id))
            self.audit.record(conn, AuditContext(principal), "bank_statement.reconcile", "bank_statement", statement_id,
                              before={"status": "imported"}, after={"status": "reconciled", "book_balance_cents": book_balance,
                                                                             "bank_balance_cents": statement["closing_balance_cents"]})
        return self.statement(principal, statement_id)
