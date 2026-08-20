from __future__ import annotations

import json
import sqlite3
import uuid
from calendar import monthrange
from datetime import date, timedelta
from typing import Iterable

from .accounting import LedgerService
from .audit import AuditContext, AuditService
from .identity import Principal
from .models import Conflict, InvalidTransition, NotFound, PeriodClosed, ValidationError, calculate_tax, require_positive, validate_iso_date
from .numbering import next_number
from .store import ERPStore


class FinanceService:
    """Operational AR/AP integrated with an immutable business ledger."""

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)
        self.ledger = LedgerService(store)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    @staticmethod
    def _assert_period_open(conn: sqlite3.Connection, organization_id: str, value: str) -> None:
        validate_iso_date(value)
        moment = date.fromisoformat(value)
        row = conn.execute(
            "SELECT status FROM accounting_periods WHERE organization_id=? AND year=? AND month=?",
            (organization_id, moment.year, moment.month),
        ).fetchone()
        if row and row["status"] == "closed":
            raise PeriodClosed(f"财务期间已关闭：{moment.year}-{moment.month:02d}")

    def create_invoice_from_sales(self, principal: Principal, sales_document_id: str,
                                  invoice_date: str | None = None, due_date: str | None = None,
                                  notes: str = "") -> dict:
        principal.require("finance.write")
        invoice_date = invoice_date or date.today().isoformat()
        with self.store.connect() as conn:
            self._assert_period_open(conn, principal.organization_id, invoice_date)
            order = conn.execute("SELECT * FROM sales_documents WHERE id=? AND organization_id=?", (sales_document_id, principal.organization_id)).fetchone()
            if not order: raise NotFound(f"销售单不存在：{sales_document_id}")
            if order["status"] not in {"partially_shipped", "shipped", "returned"}: raise InvalidTransition("销售订单发货后才能开票")
            customer = conn.execute("SELECT * FROM customer_master WHERE id=?", (order["customer_id"],)).fetchone()
            due_date = due_date or (date.fromisoformat(invoice_date) + timedelta(days=customer["payment_terms_days"])).isoformat()
            validate_iso_date(due_date, "到期日")
            if due_date < invoice_date: raise ValidationError("到期日不能早于开票日")
            lines = self._uninvoiced_sales_lines(conn, principal.organization_id, sales_document_id)
            if not lines:
                raise Conflict("销售订单没有新增已发货数量可开票")
            subtotal = sum(line["net_cents"] for line in lines)
            tax = sum(line["tax_cents"] for line in lines)
            prior_count = conn.execute(
                "SELECT COUNT(*) FROM invoices WHERE organization_id=? AND source_type='sales_order' AND source_id=? AND status<>'void'",
                (principal.organization_id, sales_document_id),
            ).fetchone()[0]
            freight = 0 if prior_count else int(order["freight_cents"])
            return self._insert_invoice(conn, principal, "receivable", "customer", order["customer_id"],
                                        "sales_order", sales_document_id, invoice_date, due_date, order["currency"],
                                        subtotal, tax, subtotal + tax + freight, notes, lines)

    def create_invoice_from_purchase(self, principal: Principal, purchase_order_id: str,
                                     invoice_date: str | None = None, due_date: str | None = None,
                                     notes: str = "", supplier_invoice_number: str = "",
                                     supplier_lines: Iterable[dict] | None = None,
                                     price_tolerance_basis_points: int = 0,
                                     supplier_total_cents: int | None = None) -> dict:
        principal.require("finance.write")
        invoice_date = invoice_date or date.today().isoformat()
        supplier_invoice_number = supplier_invoice_number.strip()[:128]
        if not 0 <= price_tolerance_basis_points <= 10000:
            raise ValidationError("价格容差必须在 0..10000 基点之间")
        with self.store.connect() as conn:
            self._assert_period_open(conn, principal.organization_id, invoice_date)
            order = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND organization_id=?", (purchase_order_id, principal.organization_id)).fetchone()
            if not order: raise NotFound(f"采购单不存在：{purchase_order_id}")
            if order["status"] not in {"partially_received", "received"}: raise InvalidTransition("采购订单收货后才能登记应付")
            supplier = conn.execute("SELECT * FROM supplier_master WHERE id=?", (order["supplier_id"],)).fetchone()
            due_date = due_date or (date.fromisoformat(invoice_date) + timedelta(days=supplier["payment_terms_days"])).isoformat()
            validate_iso_date(due_date, "到期日")
            if due_date < invoice_date:
                raise ValidationError("到期日不能早于开票日")
            lines = self._uninvoiced_purchase_lines(conn, principal.organization_id, purchase_order_id)
            if not lines:
                raise Conflict("采购订单没有新增已收货数量可开票")
            match_status = "system_generated"
            match_details: dict[str, object] = {
                "policy": "received_quantity", "purchase_order_id": purchase_order_id,
                "supplier_invoice_number": supplier_invoice_number,
            }
            if supplier_lines is not None:
                if not supplier_invoice_number:
                    raise ValidationError("三单匹配必须填写供应商发票号")
                lines, match_details = self._match_purchase_invoice_lines(
                    lines, list(supplier_lines), price_tolerance_basis_points,
                )
                match_details.update({"policy": "three_way", "purchase_order_id": purchase_order_id,
                                      "supplier_invoice_number": supplier_invoice_number})
                match_status = "matched"
            subtotal = sum(line["net_cents"] for line in lines)
            tax = sum(line["tax_cents"] for line in lines)
            prior_count = conn.execute(
                "SELECT COUNT(*) FROM invoices WHERE organization_id=? AND source_type='purchase_order' AND source_id=? AND status<>'void'",
                (principal.organization_id, purchase_order_id),
            ).fetchone()[0]
            freight = 0 if prior_count else int(order["freight_cents"])
            total = subtotal + tax + freight
            if supplier_total_cents is not None and int(supplier_total_cents) != total:
                raise Conflict(f"供应商发票总额与匹配结果不一致：发票 {supplier_total_cents}，匹配 {total}")
            match_details.update({"subtotal_cents": subtotal, "tax_cents": tax,
                                  "freight_cents": freight, "total_cents": total})
            try:
                return self._insert_invoice(conn, principal, "payable", "supplier", order["supplier_id"],
                                            "purchase_order", purchase_order_id, invoice_date, due_date, order["currency"],
                                            subtotal, tax, total, notes, lines, supplier_invoice_number,
                                            match_status, match_details)
            except sqlite3.IntegrityError as exc:
                if supplier_invoice_number:
                    raise Conflict("该供应商发票号已经登记") from exc
                raise

    def create_credit_note(self, principal: Principal, return_id: str,
                           invoice_date: str | None = None, notes: str = "") -> dict:
        principal.require("finance.write")
        invoice_date = invoice_date or date.today().isoformat()
        with self.store.connect() as conn:
            self._assert_period_open(conn, principal.organization_id, invoice_date)
            ret = conn.execute("SELECT * FROM sales_returns WHERE id=? AND organization_id=?", (return_id, principal.organization_id)).fetchone()
            if not ret: raise NotFound(f"退货单不存在：{return_id}")
            if ret["status"] not in {"received", "refunded"}: raise InvalidTransition("退货收货后才能开红字单")
            prior = conn.execute(
                "SELECT id FROM invoices WHERE organization_id=? AND source_type='sales_return' AND source_id=? AND status<>'void'",
                (principal.organization_id, return_id),
            ).fetchone()
            if prior:
                raise Conflict("该退货单已存在有效红字单")
            order = conn.execute("SELECT * FROM sales_documents WHERE id=?", (ret["sales_document_id"],)).fetchone()
            total = conn.execute("SELECT COALESCE(SUM(refund_cents),0) FROM sales_return_lines WHERE return_id=?", (return_id,)).fetchone()[0]
            if total <= 0: raise ValidationError("退货退款金额必须大于 0")
            return self._insert_invoice(conn, principal, "credit_note", "customer", order["customer_id"],
                                        "sales_return", return_id, invoice_date, invoice_date, order["currency"],
                                        total, 0, total, notes)

    def _insert_invoice(self, conn: sqlite3.Connection, principal: Principal, invoice_type: str,
                        partner_type: str, partner_id: str, source_type: str, source_id: str,
                        invoice_date: str, due_date: str, currency: str, subtotal: int, tax: int,
                        total: int, notes: str, lines: list[dict] | None = None,
                        external_reference: str = "", match_status: str = "not_required",
                        match_details: dict | None = None) -> dict:
        self._assert_period_open(conn, principal.organization_id, invoice_date)
        validate_iso_date(due_date, "到期日")
        invoice_id = self._id("INV")
        sequence = "credit_note" if invoice_type == "credit_note" else f"{invoice_type}_invoice"
        number = next_number(conn, principal.organization_id, sequence)
        conn.execute(
            "INSERT INTO invoices(id,organization_id,invoice_number,invoice_type,partner_type,partner_id,source_type,source_id,status,invoice_date,due_date,currency,subtotal_cents,tax_cents,total_cents,outstanding_cents,notes,issued_by,issued_at,external_reference,match_status,match_details_json) "
            "VALUES(?,?,?,?,?,?,?,?,'issued',?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?,?,?)",
            (invoice_id, principal.organization_id, number, invoice_type, partner_type, partner_id, source_type,
             source_id, invoice_date, due_date, currency, subtotal, tax, total, total, notes.strip(), principal.user_id,
             external_reference, match_status, json.dumps(match_details or {}, ensure_ascii=False, separators=(",", ":"))),
        )
        for line in lines or []:
            conn.execute(
                "INSERT INTO invoice_lines(id,invoice_id,source_line_id,product_id,description,quantity,unit_price_cents,net_cents,tax_cents,total_cents) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self._id("INL"), invoice_id, line["source_line_id"], line.get("product_id"), line["description"],
                 line["quantity"], line["unit_price_cents"], line["net_cents"], line["tax_cents"], line["total_cents"]),
            )
        ledger_lines: list[dict] = []
        if invoice_type == "receivable":
            ledger_lines = [
                {"account_code": "1122", "debit_cents": total, "partner_type": "customer", "partner_id": partner_id},
                {"account_code": "5001", "credit_cents": total - tax},
            ]
            if tax: ledger_lines.append({"account_code": "2221", "credit_cents": tax})
        elif invoice_type == "credit_note":
            ledger_lines = [
                {"account_code": "5002", "debit_cents": total},
                {"account_code": "1122", "credit_cents": total, "partner_type": "customer", "partner_id": partner_id},
            ]
        elif invoice_type == "payable":
            expected_grni = 0
            for line in lines or []:
                purchase_line = conn.execute(
                    "SELECT unit_price_cents FROM purchase_order_lines WHERE id=?", (line["source_line_id"],)
                ).fetchone()
                expected_grni += int(purchase_line["unit_price_cents"]) * int(line["quantity"])
            variance = subtotal - expected_grni; freight = total - subtotal - tax
            if expected_grni: ledger_lines.append({"account_code": "2203", "debit_cents": expected_grni,
                                                    "partner_type": "supplier", "partner_id": partner_id})
            if tax: ledger_lines.append({"account_code": "1221", "debit_cents": tax})
            if freight: ledger_lines.append({"account_code": "6051", "debit_cents": freight})
            if variance > 0: ledger_lines.append({"account_code": "6002", "debit_cents": variance})
            if variance < 0: ledger_lines.append({"account_code": "6002", "credit_cents": -variance})
            ledger_lines.append({"account_code": "2202", "credit_cents": total,
                                 "partner_type": "supplier", "partner_id": partner_id})
        self.ledger.post(conn, principal, "sales" if invoice_type != "payable" else "purchase",
                         invoice_date, "invoice", invoice_id, f"invoice:{invoice_id}",
                         f"开立发票 {number}", ledger_lines, currency)
        self.audit.record(conn, AuditContext(principal), "invoice.issue", "invoice", invoice_id,
                          after={"invoice_number": number, "invoice_type": invoice_type, "total_cents": total})
        row = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        result = dict(row)
        result["lines"] = list(lines or [])
        result["allocations"] = []
        result["match_details"] = match_details or {}
        return result

    @staticmethod
    def _match_purchase_invoice_lines(available: list[dict], submitted: list[dict],
                                      tolerance_basis_points: int) -> tuple[list[dict], dict]:
        if not submitted:
            raise ValidationError("供应商发票至少包含一行")
        candidates = {str(line["source_line_id"]): line for line in available}
        seen: set[str] = set(); matched: list[dict] = []; details: list[dict] = []
        for raw in submitted:
            source_line_id = str(raw.get("source_line_id", ""))
            if source_line_id in seen: raise ValidationError("供应商发票存在重复采购明细")
            seen.add(source_line_id)
            expected = candidates.get(source_line_id)
            if not expected: raise Conflict(f"发票明细不属于本采购单的未开票收货：{source_line_id}")
            quantity = int(raw.get("quantity", 0)); require_positive(quantity, "发票数量")
            if quantity > int(expected["quantity"]):
                raise Conflict(f"发票数量超过已收未开票数量：{source_line_id}")
            unit_price = int(raw.get("unit_price_cents", -1))
            if unit_price < 0: raise ValidationError("供应商发票单价不能为负")
            ordered_price = int(expected["unit_price_cents"])
            allowed_variance = ordered_price * tolerance_basis_points // 10000
            if abs(unit_price - ordered_price) > allowed_variance:
                raise Conflict(
                    f"供应商发票单价超出容差：采购价 {ordered_price}，发票价 {unit_price}，"
                    f"容差 {tolerance_basis_points / 100:.2f}%"
                )
            net = quantity * unit_price
            tax = calculate_tax(net, int(expected["tax_rate_basis_points"]))
            matched.append({"source_line_id": source_line_id, "product_id": expected["product_id"],
                            "description": expected["description"], "quantity": quantity,
                            "unit_price_cents": unit_price, "net_cents": net,
                            "tax_cents": tax, "total_cents": net + tax})
            details.append({"source_line_id": source_line_id, "received_unbilled_quantity": expected["quantity"],
                            "invoiced_quantity": quantity, "purchase_unit_price_cents": ordered_price,
                            "invoice_unit_price_cents": unit_price, "quantity_match": True, "price_match": True})
        return matched, {"price_tolerance_basis_points": tolerance_basis_points, "lines": details}

    @staticmethod
    def _uninvoiced_sales_lines(conn: sqlite3.Connection, organization_id: str, source_id: str) -> list[dict]:
        rows = conn.execute(
            "SELECT l.*,p.name AS product_name,COALESCE(SUM(CASE WHEN i.status<>'void' THEN il.quantity ELSE 0 END),0) AS invoiced_quantity," 
            "COALESCE(SUM(CASE WHEN i.status<>'void' THEN il.net_cents ELSE 0 END),0) AS invoiced_net_cents," 
            "COALESCE(SUM(CASE WHEN i.status<>'void' THEN il.tax_cents ELSE 0 END),0) AS invoiced_tax_cents "
            "FROM sales_document_lines l JOIN product_master p ON p.id=l.product_id "
            "LEFT JOIN invoice_lines il ON il.source_line_id=l.id LEFT JOIN invoices i ON i.id=il.invoice_id AND i.organization_id=? "
            "WHERE l.document_id=? GROUP BY l.id ORDER BY l.line_number",
            (organization_id, source_id),
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            quantity = int(row["shipped_quantity"]) - int(row["invoiced_quantity"])
            if quantity <= 0:
                continue
            target_net = int(row["net_cents"]) * int(row["shipped_quantity"]) // int(row["ordered_quantity"])
            target_tax = int(row["tax_cents"]) * int(row["shipped_quantity"]) // int(row["ordered_quantity"])
            net = target_net - int(row["invoiced_net_cents"])
            tax = target_tax - int(row["invoiced_tax_cents"])
            result.append({"source_line_id": row["id"], "product_id": row["product_id"],
                           "description": row["description"] or row["product_name"], "quantity": quantity,
                           "unit_price_cents": row["unit_price_cents"], "net_cents": net,
                           "tax_cents": tax, "total_cents": net + tax})
        return result

    @staticmethod
    def _uninvoiced_purchase_lines(conn: sqlite3.Connection, organization_id: str, source_id: str) -> list[dict]:
        rows = conn.execute(
            "SELECT l.*,p.name AS product_name,COALESCE(SUM(CASE WHEN i.status<>'void' THEN il.quantity ELSE 0 END),0) AS invoiced_quantity," 
            "COALESCE(SUM(CASE WHEN i.status<>'void' THEN il.net_cents ELSE 0 END),0) AS invoiced_net_cents," 
            "COALESCE(SUM(CASE WHEN i.status<>'void' THEN il.tax_cents ELSE 0 END),0) AS invoiced_tax_cents "
            "FROM purchase_order_lines l JOIN product_master p ON p.id=l.product_id "
            "LEFT JOIN invoice_lines il ON il.source_line_id=l.id LEFT JOIN invoices i ON i.id=il.invoice_id AND i.organization_id=? "
            "WHERE l.purchase_order_id=? GROUP BY l.id ORDER BY l.line_number",
            (organization_id, source_id),
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            quantity = int(row["received_quantity"]) - int(row["invoiced_quantity"])
            if quantity <= 0:
                continue
            target_net = int(row["net_cents"]) * int(row["received_quantity"]) // int(row["ordered_quantity"])
            target_tax = int(row["tax_cents"]) * int(row["received_quantity"]) // int(row["ordered_quantity"])
            net = target_net - int(row["invoiced_net_cents"])
            tax = target_tax - int(row["invoiced_tax_cents"])
            result.append({"source_line_id": row["id"], "product_id": row["product_id"],
                           "description": row["product_name"], "quantity": quantity,
                           "unit_price_cents": row["unit_price_cents"],
                           "tax_rate_basis_points": row["tax_rate_basis_points"], "net_cents": net,
                           "tax_cents": tax, "total_cents": net + tax})
        return result

    def record_payment(self, principal: Principal, payment_type: str, partner_type: str, partner_id: str,
                       amount_cents: int, payment_date: str | None = None, currency: str = "CNY",
                       method: str = "bank_transfer", external_reference: str = "",
                       allocations: Iterable[dict] = (), notes: str = "", bank_account_id: str = "") -> dict:
        principal.require("finance.write")
        require_positive(amount_cents, "付款金额")
        payment_date = payment_date or date.today().isoformat()
        if payment_type not in {"receipt", "disbursement", "refund"}: raise ValidationError("收付款类型无效")
        if partner_type not in {"customer", "supplier"}: raise ValidationError("往来单位类型无效")
        if method not in {"cash", "bank_transfer", "card", "online", "other"}: raise ValidationError("支付方式无效")
        prepared = list(allocations)
        invoice_ids = [str(item["invoice_id"]) for item in prepared]
        if len(invoice_ids) != len(set(invoice_ids)):
            raise ValidationError("同一笔收付款不能重复核销同一张发票")
        if sum(int(item["amount_cents"]) for item in prepared) > amount_cents:
            raise ValidationError("核销金额不能超过收付款金额")
        if payment_type == "refund" and sum(int(item["amount_cents"]) for item in prepared) != amount_cents:
            raise ValidationError("退款必须完整核销红字单")
        payment_id = self._id("PAY")
        with self.store.connect() as conn:
            self._assert_period_open(conn, principal.organization_id, payment_date)
            table = "customer_master" if partner_type == "customer" else "supplier_master"
            partner = conn.execute(f"SELECT id,currency FROM {table} WHERE id=? AND organization_id=?", (partner_id, principal.organization_id)).fetchone()
            if not partner: raise NotFound(f"往来单位不存在：{partner_id}")
            if partner["currency"] != currency: raise ValidationError("付款币种必须与往来单位币种一致")
            selected_bank_account_id = bank_account_id.strip() or None
            bank_code = "1001" if method == "cash" else "1002"
            if method == "cash" and selected_bank_account_id:
                raise ValidationError("现金收付款不能选择银行账户")
            if selected_bank_account_id:
                bank_account = conn.execute(
                    "SELECT b.*,a.code AS ledger_account_code FROM bank_accounts b JOIN ledger_accounts a ON a.id=b.ledger_account_id "
                    "WHERE b.id=? AND b.organization_id=? AND b.status='active'",
                    (selected_bank_account_id, principal.organization_id),
                ).fetchone()
                if not bank_account: raise NotFound(f"可用银行账户不存在：{selected_bank_account_id}")
                if bank_account["currency"] != currency: raise ValidationError("收付款币种必须与银行账户币种一致")
                bank_code = bank_account["ledger_account_code"]
            number = next_number(conn, principal.organization_id, "payment_receipt" if payment_type == "receipt" else "payment_disbursement")
            stored_reference = external_reference.strip() or f"__internal__:{payment_id}"
            try:
                conn.execute(
                    "INSERT INTO payments(id,organization_id,payment_number,payment_type,partner_type,partner_id,payment_date,amount_cents,currency,method,external_reference,status,notes,posted_by,bank_account_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,'posted',?,?,?)",
                    (payment_id, principal.organization_id, number, payment_type, partner_type, partner_id,
                     payment_date, amount_cents, currency, method, stored_reference, notes.strip(), principal.user_id,
                     selected_bank_account_id),
                )
            except sqlite3.IntegrityError as exc:
                if external_reference: raise Conflict("银行流水号或外部支付号已处理") from exc
                raise
            for item in prepared:
                invoice_id = str(item["invoice_id"]); amount = int(item["amount_cents"]); require_positive(amount, "核销金额")
                invoice = conn.execute("SELECT * FROM invoices WHERE id=? AND organization_id=?", (invoice_id, principal.organization_id)).fetchone()
                if not invoice: raise NotFound(f"发票不存在：{invoice_id}")
                if invoice["partner_type"] != partner_type or invoice["partner_id"] != partner_id: raise ValidationError("付款与发票往来单位不一致")
                if invoice["currency"] != currency: raise ValidationError("付款与发票币种不一致")
                if invoice["status"] not in {"issued", "partially_paid"}: raise InvalidTransition("发票状态不允许核销")
                expected_invoice_type = {"receipt": "receivable", "disbursement": "payable", "refund": "credit_note"}[payment_type]
                if invoice["invoice_type"] != expected_invoice_type:
                    raise ValidationError("收付款类型与发票类型不匹配")
                if amount > invoice["outstanding_cents"]: raise ValidationError("核销金额超过发票未付金额")
                conn.execute("INSERT INTO payment_allocations(id,payment_id,invoice_id,amount_cents) VALUES(?,?,?,?)", (self._id("PAL"), payment_id, invoice_id, amount))
                outstanding = invoice["outstanding_cents"] - amount
                status = "paid" if outstanding == 0 else "partially_paid"
                conn.execute("UPDATE invoices SET paid_cents=paid_cents+?,outstanding_cents=?,status=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (amount, outstanding, status, invoice_id))
            allocated = sum(int(i["amount_cents"]) for i in prepared); unallocated = amount_cents - allocated
            if payment_type == "receipt":
                ledger_lines = [{"account_code": bank_code, "debit_cents": amount_cents}]
                if allocated: ledger_lines.append({"account_code": "1122", "credit_cents": allocated,
                                                   "partner_type": "customer", "partner_id": partner_id})
                if unallocated: ledger_lines.append({"account_code": "2204", "credit_cents": unallocated,
                                                     "partner_type": "customer", "partner_id": partner_id})
            elif payment_type == "disbursement":
                ledger_lines = []
                if allocated: ledger_lines.append({"account_code": "2202", "debit_cents": allocated,
                                                   "partner_type": "supplier", "partner_id": partner_id})
                if unallocated: ledger_lines.append({"account_code": "1123", "debit_cents": unallocated,
                                                     "partner_type": "supplier", "partner_id": partner_id})
                ledger_lines.append({"account_code": bank_code, "credit_cents": amount_cents})
            else:
                ledger_lines = [{"account_code": "1122", "debit_cents": amount_cents,
                                 "partner_type": "customer", "partner_id": partner_id},
                                {"account_code": bank_code, "credit_cents": amount_cents}]
            self.ledger.post(conn, principal, "cash", payment_date, "payment", payment_id,
                             f"payment:{payment_id}", f"收付款 {number}", ledger_lines, currency)
            self.audit.record(conn, AuditContext(principal), "payment.post", "payment", payment_id,
                              after={"payment_number": number, "amount_cents": amount_cents, "allocated_cents": allocated})
        return self.payment(principal, payment_id)

    def void_payment(self, principal: Principal, payment_id: str, reason: str) -> dict:
        principal.require("finance.write")
        if not reason.strip():
            raise ValidationError("作废收付款必须填写原因")
        with self.store.connect() as conn:
            payment = conn.execute(
                "SELECT * FROM payments WHERE id=? AND organization_id=?",
                (payment_id, principal.organization_id),
            ).fetchone()
            if not payment:
                raise NotFound(f"收付款记录不存在：{payment_id}")
            if payment["status"] == "void":
                return self.payment(principal, payment_id)
            if payment["status"] != "posted":
                raise InvalidTransition(f"收付款状态不允许作废：{payment['status']}")
            self._assert_period_open(conn, principal.organization_id, payment["payment_date"])
            allocations = conn.execute(
                "SELECT a.*,i.status AS invoice_status,i.paid_cents,i.outstanding_cents,i.total_cents "
                "FROM payment_allocations a JOIN invoices i ON i.id=a.invoice_id WHERE a.payment_id=?",
                (payment_id,),
            ).fetchall()
            for allocation in allocations:
                if allocation["invoice_status"] == "void" or allocation["paid_cents"] < allocation["amount_cents"]:
                    raise Conflict("发票核销状态异常，收付款作废被阻断")
                paid = int(allocation["paid_cents"]) - int(allocation["amount_cents"])
                outstanding = int(allocation["outstanding_cents"]) + int(allocation["amount_cents"])
                if outstanding > int(allocation["total_cents"]):
                    raise Conflict("发票未结金额校验失败，收付款作废被阻断")
                status = "issued" if paid == 0 else "partially_paid"
                conn.execute(
                    "UPDATE invoices SET paid_cents=?,outstanding_cents=?,status=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?",
                    (paid, outstanding, status, allocation["invoice_id"]),
                )
            conn.execute(
                "UPDATE payments SET status='void',notes=notes||? WHERE id=?",
                (f"\n[作废] {reason.strip()}", payment_id),
            )
            journal = conn.execute(
                "SELECT id FROM journal_entries WHERE organization_id=? AND source_event=?",
                (principal.organization_id, f"payment:{payment_id}"),
            ).fetchone()
            if not journal: raise Conflict("收付款缺少已过账会计凭证，禁止作废")
            self.ledger.reverse(conn, principal, journal["id"], f"payment-void:{payment_id}",
                                date.today().isoformat(), reason)
            self.audit.record(
                conn, AuditContext(principal), "payment.void", "payment", payment_id,
                before={"status": "posted"}, after={"status": "void", "reason": reason.strip()},
            )
        return self.payment(principal, payment_id)

    def void_invoice(self, principal: Principal, invoice_id: str, reason: str) -> dict:
        principal.require("finance.write")
        if not reason.strip(): raise ValidationError("作废原因不能为空")
        with self.store.connect() as conn:
            invoice = conn.execute("SELECT * FROM invoices WHERE id=? AND organization_id=?", (invoice_id, principal.organization_id)).fetchone()
            if not invoice: raise NotFound(f"发票不存在：{invoice_id}")
            self._assert_period_open(conn, principal.organization_id, invoice["invoice_date"])
            if invoice["status"] != "issued" or invoice["paid_cents"] != 0: raise InvalidTransition("已核销或非已开立发票不能直接作废")
            conn.execute("UPDATE invoices SET status='void',notes=notes||?,outstanding_cents=0,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (f"\n[作废] {reason.strip()}", invoice_id))
            journal = conn.execute(
                "SELECT id FROM journal_entries WHERE organization_id=? AND source_event=?",
                (principal.organization_id, f"invoice:{invoice_id}"),
            ).fetchone()
            if not journal: raise Conflict("发票缺少已过账会计凭证，禁止作废")
            self.ledger.reverse(conn, principal, journal["id"], f"invoice-void:{invoice_id}",
                                date.today().isoformat(), reason)
            self.audit.record(conn, AuditContext(principal), "invoice.void", "invoice", invoice_id,
                              before={"status": invoice["status"]}, after={"status": "void", "reason": reason})
        return self.invoice(principal, invoice_id)

    def close_period(self, principal: Principal, year: int, month: int) -> dict:
        principal.require("finance.close")
        if not 2000 <= year <= 2200 or not 1 <= month <= 12: raise ValidationError("会计期间无效")
        period_id = f"PER-{principal.organization_id}-{year:04d}{month:02d}"
        last_day = date(year, month, monthrange(year, month)[1]).isoformat()
        with self.store.connect() as conn:
            unposted_receipts = conn.execute("SELECT COUNT(*) FROM goods_receipts WHERE organization_id=? AND status='draft' AND receipt_date<=?", (principal.organization_id, last_day)).fetchone()[0]
            draft_invoices = conn.execute("SELECT COUNT(*) FROM invoices WHERE organization_id=? AND status='draft' AND invoice_date<=?", (principal.organization_id, last_day)).fetchone()[0]
            if unposted_receipts or draft_invoices: raise Conflict(f"期间内还有未过账单据：收货 {unposted_receipts}，发票 {draft_invoices}")
            unbalanced_journals = conn.execute(
                "SELECT COUNT(*) FROM (SELECT e.id FROM journal_entries e JOIN journal_lines l ON l.journal_entry_id=e.id "
                "WHERE e.organization_id=? AND e.status='posted' AND e.posting_date<=? GROUP BY e.id "
                "HAVING SUM(l.debit_cents)<>SUM(l.credit_cents))",
                (principal.organization_id, last_day),
            ).fetchone()[0]
            pending_valuation = conn.execute(
                "SELECT COUNT(*) FROM stock_moves WHERE organization_id=? AND valuation_status='pending' AND date(occurred_at)<=?",
                (principal.organization_id, last_day),
            ).fetchone()[0]
            if unbalanced_journals or pending_valuation:
                raise Conflict(f"关账检查失败：借贷不平凭证 {unbalanced_journals}，待估值库存流水 {pending_valuation}")
            unreconciled_statements = conn.execute(
                "SELECT COUNT(*) FROM bank_statements WHERE organization_id=? AND status='imported' AND period_end<=?",
                (principal.organization_id, last_day),
            ).fetchone()[0]
            if unreconciled_statements:
                raise Conflict(f"关账检查失败：仍有 {unreconciled_statements} 张银行对账单未完成")

            def account_net(code: str) -> int:
                return int(conn.execute(
                    "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_lines l "
                    "JOIN journal_entries e ON e.id=l.journal_entry_id JOIN ledger_accounts a ON a.id=l.account_id "
                    "WHERE e.organization_id=? AND e.status='posted' AND e.posting_date<=? AND a.code=?",
                    (principal.organization_id, last_day, code),
                ).fetchone()[0] or 0)

            inventory_subledger = int(conn.execute(
                "SELECT COALESCE(SUM(v.original_value_cents-COALESCE((SELECT SUM(c.value_cents) "
                "FROM inventory_valuation_consumptions c JOIN stock_moves m ON m.id=c.stock_move_id "
                "WHERE c.valuation_layer_id=v.id AND date(m.occurred_at)<=?),0)),0) "
                "FROM inventory_valuation_layers v WHERE v.organization_id=? AND date(v.occurred_at)<=?",
                (last_day, principal.organization_id, last_day),
            ).fetchone()[0] or 0)
            ar_subledger = int(conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN i.invoice_type='receivable' THEN i.total_cents-COALESCE(p.allocated,0) "
                "WHEN i.invoice_type='credit_note' THEN -(i.total_cents-COALESCE(p.allocated,0)) ELSE 0 END),0) "
                "FROM invoices i LEFT JOIN (SELECT a.invoice_id,SUM(a.amount_cents) AS allocated FROM payment_allocations a "
                "JOIN payments pay ON pay.id=a.payment_id WHERE pay.status='posted' AND pay.payment_date<=? GROUP BY a.invoice_id) p "
                "ON p.invoice_id=i.id WHERE i.organization_id=? AND i.status<>'void' AND i.invoice_date<=?",
                (last_day, principal.organization_id, last_day),
            ).fetchone()[0] or 0)
            ap_subledger = int(conn.execute(
                "SELECT COALESCE(SUM(i.total_cents-COALESCE(p.allocated,0)),0) FROM invoices i "
                "LEFT JOIN (SELECT a.invoice_id,SUM(a.amount_cents) AS allocated FROM payment_allocations a "
                "JOIN payments pay ON pay.id=a.payment_id WHERE pay.status='posted' AND pay.payment_date<=? GROUP BY a.invoice_id) p "
                "ON p.invoice_id=i.id WHERE i.organization_id=? AND i.invoice_type='payable' "
                "AND i.status<>'void' AND i.invoice_date<=?", (last_day, principal.organization_id, last_day),
            ).fetchone()[0] or 0)
            grni_subledger = int(conn.execute(
                "SELECT COALESCE(SUM(received.amount_cents),0)-COALESCE(SUM(billed.amount_cents),0) FROM purchase_order_lines pl "
                "JOIN purchase_orders po ON po.id=pl.purchase_order_id "
                "LEFT JOIN (SELECT grl.purchase_line_id,SUM(grl.accepted_quantity*pol.unit_price_cents) AS amount_cents "
                "FROM goods_receipt_lines grl JOIN goods_receipts gr ON gr.id=grl.receipt_id "
                "JOIN purchase_order_lines pol ON pol.id=grl.purchase_line_id WHERE gr.status='posted' AND gr.receipt_date<=? GROUP BY grl.purchase_line_id) received ON received.purchase_line_id=pl.id "
                "LEFT JOIN (SELECT il.source_line_id,SUM(il.quantity*pol.unit_price_cents) AS amount_cents FROM invoice_lines il "
                "JOIN invoices i ON i.id=il.invoice_id JOIN purchase_order_lines pol ON pol.id=il.source_line_id "
                "WHERE i.status<>'void' AND i.invoice_date<=? GROUP BY il.source_line_id) billed ON billed.source_line_id=pl.id "
                "WHERE po.organization_id=?", (last_day, last_day, principal.organization_id),
            ).fetchone()[0] or 0)
            differences = {
                "库存": inventory_subledger - account_net("1405"),
                "应收": ar_subledger - account_net("1122"),
                "应付": ap_subledger + account_net("2202"),
                "采购暂估": grni_subledger + account_net("2203"),
            }
            mismatches = {name: value for name, value in differences.items() if value}
            if mismatches: raise Conflict(f"关账前子账与总账不一致：{mismatches}")
            conn.execute(
                "INSERT INTO accounting_periods(id,organization_id,year,month,status,closed_by,closed_at) VALUES(?,?,?,?,'closed',?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(organization_id,year,month) DO UPDATE SET status='closed',closed_by=excluded.closed_by,closed_at=CURRENT_TIMESTAMP",
                (period_id, principal.organization_id, year, month, principal.user_id),
            )
            self.audit.record(conn, AuditContext(principal), "period.close", "accounting_period", period_id,
                              after={"year": year, "month": month, "status": "closed"})
        return self.store.row("SELECT * FROM accounting_periods WHERE id=?", (period_id,)) or {}

    def reopen_period(self, principal: Principal, year: int, month: int, reason: str) -> dict:
        principal.require("finance.close")
        if not reason.strip(): raise ValidationError("重新打开期间必须填写原因")
        period_id = f"PER-{principal.organization_id}-{year:04d}{month:02d}"
        with self.store.connect() as conn:
            cursor = conn.execute("UPDATE accounting_periods SET status='open',closed_by=NULL,closed_at=NULL WHERE id=? AND status='closed'", (period_id,))
            if cursor.rowcount != 1: raise NotFound("已关闭期间不存在")
            self.audit.record(conn, AuditContext(principal), "period.reopen", "accounting_period", period_id,
                              before={"status": "closed"}, after={"status": "open", "reason": reason})
        return self.store.row("SELECT * FROM accounting_periods WHERE id=?", (period_id,)) or {}

    def invoice(self, principal: Principal, invoice_id: str) -> dict:
        principal.require("finance.read")
        row = self.store.row("SELECT * FROM invoices WHERE id=? AND organization_id=?", (invoice_id, principal.organization_id))
        if not row: raise NotFound(f"发票不存在：{invoice_id}")
        row["match_details"] = json.loads(row.get("match_details_json") or "{}")
        row["lines"] = self.store.rows(
            "SELECT il.*,p.sku,p.name AS product_name FROM invoice_lines il "
            "LEFT JOIN product_master p ON p.id=il.product_id WHERE il.invoice_id=? ORDER BY il.created_at,il.id",
            (invoice_id,),
        )
        row["allocations"] = self.store.rows(
            "SELECT a.*,p.payment_number,p.payment_date,p.method,p.status AS payment_status FROM payment_allocations a JOIN payments p ON p.id=a.payment_id WHERE a.invoice_id=? ORDER BY p.payment_date", (invoice_id,),
        )
        return row

    def list_invoices(self, principal: Principal, invoice_type: str = "", status: str = "",
                      partner_id: str = "", overdue_only: bool = False, limit: int = 200) -> list[dict]:
        principal.require("finance.read")
        filters = ["organization_id=?"]; params: list[object] = [principal.organization_id]
        for column, value in (("invoice_type", invoice_type), ("status", status), ("partner_id", partner_id)):
            if value: filters.append(f"{column}=?"); params.append(value)
        if overdue_only:
            filters.append("due_date<? AND outstanding_cents>0"); params.append(date.today().isoformat())
        params.append(max(1, min(limit, 1000)))
        return self.store.rows(f"SELECT * FROM invoices WHERE {' AND '.join(filters)} ORDER BY invoice_date DESC,invoice_number DESC LIMIT ?", tuple(params))

    def payment(self, principal: Principal, payment_id: str) -> dict:
        principal.require("finance.read")
        row = self.store.row("SELECT * FROM payments WHERE id=? AND organization_id=?", (payment_id, principal.organization_id))
        if not row: raise NotFound(f"收付款记录不存在：{payment_id}")
        if str(row["external_reference"]).startswith("__internal__:"):
            row["external_reference"] = ""
        row["allocations"] = self.store.rows(
            "SELECT a.*,i.invoice_number,i.invoice_type FROM payment_allocations a JOIN invoices i ON i.id=a.invoice_id WHERE a.payment_id=?", (payment_id,),
        )
        row["unallocated_cents"] = row["amount_cents"] - sum(item["amount_cents"] for item in row["allocations"])
        return row

    def list_payments(self, principal: Principal, payment_type: str = "", status: str = "",
                      partner_id: str = "", limit: int = 200) -> list[dict]:
        principal.require("finance.read")
        filters = ["p.organization_id=?"]
        params: list[object] = [principal.organization_id]
        for column, value in (("payment_type", payment_type), ("status", status), ("partner_id", partner_id)):
            if value:
                filters.append(f"p.{column}=?"); params.append(value)
        params.append(max(1, min(limit, 1000)))
        rows = self.store.rows(
            "SELECT p.*,CASE WHEN p.partner_type='customer' THEN c.name ELSE s.name END AS partner_name,"
            "COALESCE(SUM(a.amount_cents),0) AS allocated_cents "
            "FROM payments p LEFT JOIN customer_master c ON p.partner_type='customer' AND c.id=p.partner_id "
            "LEFT JOIN supplier_master s ON p.partner_type='supplier' AND s.id=p.partner_id "
            "LEFT JOIN payment_allocations a ON a.payment_id=p.id "
            f"WHERE {' AND '.join(filters)} GROUP BY p.id ORDER BY p.payment_date DESC,p.payment_number DESC LIMIT ?",
            tuple(params),
        )
        for row in rows:
            if str(row.get("external_reference", "")).startswith("__internal__:"):
                row["external_reference"] = ""
            row["unallocated_cents"] = int(row["amount_cents"]) - int(row["allocated_cents"])
        return rows

    def list_periods(self, principal: Principal, limit: int = 120) -> list[dict]:
        principal.require("finance.read")
        return self.store.rows(
            "SELECT p.*,u.display_name AS closed_by_name FROM accounting_periods p "
            "LEFT JOIN users u ON u.id=p.closed_by WHERE p.organization_id=? "
            "ORDER BY p.year DESC,p.month DESC LIMIT ?",
            (principal.organization_id, max(1, min(limit, 240))),
        )

    def partner_statement(self, principal: Principal, partner_type: str, partner_id: str,
                          since: str = "", until: str = "") -> dict:
        principal.require("finance.read")
        filters = ["organization_id=?", "partner_type=?", "partner_id=?", "status<>'void'"]
        params: list[object] = [principal.organization_id, partner_type, partner_id]
        if since: filters.append("invoice_date>=?"); params.append(validate_iso_date(since, "开始日期"))
        if until: filters.append("invoice_date<=?"); params.append(validate_iso_date(until, "结束日期"))
        invoices = self.store.rows(f"SELECT * FROM invoices WHERE {' AND '.join(filters)} ORDER BY invoice_date,invoice_number", tuple(params))
        payments = self.store.rows(
            "SELECT * FROM payments WHERE organization_id=? AND partner_type=? AND partner_id=? AND status='posted' ORDER BY payment_date,payment_number",
            (principal.organization_id, partner_type, partner_id),
        )
        return {"partner_type": partner_type, "partner_id": partner_id, "invoices": invoices, "payments": payments,
                "invoice_total_cents": sum(i["total_cents"] for i in invoices),
                "paid_cents": sum(i["paid_cents"] for i in invoices),
                "outstanding_cents": sum(i["outstanding_cents"] for i in invoices)}
