from __future__ import annotations

import sqlite3
import uuid
from datetime import date
from typing import Iterable

from .audit import AuditContext, AuditService
from .identity import Principal
from .models import Conflict, NotFound, ValidationError, calculate_tax, require_positive, validate_iso_date
from .store import ERPStore


class PricingService:
    """Deterministic customer/channel price resolution with quantity breaks."""

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    def create_price_list(self, principal: Principal, code: str, name: str, currency: str = "CNY",
                          customer_id: str | None = None, channel: str = "", valid_from: str | None = None,
                          valid_until: str | None = None, priority: int = 100) -> dict:
        principal.require("master.write")
        code = code.strip().upper(); name = name.strip(); currency = currency.strip().upper()
        if not code or not name: raise ValidationError("价目表编码和名称不能为空")
        if len(currency) != 3: raise ValidationError("币种必须为三位 ISO 代码")
        if valid_from: validate_iso_date(valid_from, "生效日期")
        if valid_until: validate_iso_date(valid_until, "失效日期")
        if valid_from and valid_until and valid_from > valid_until: raise ValidationError("失效日期不能早于生效日期")
        if not 0 <= priority <= 10000: raise ValidationError("优先级必须在 0..10000")
        price_list_id = self._id("PL")
        with self.store.connect() as conn:
            if customer_id:
                customer = conn.execute("SELECT id FROM customer_master WHERE id=? AND organization_id=?", (customer_id, principal.organization_id)).fetchone()
                if not customer: raise NotFound(f"客户不存在：{customer_id}")
            try:
                conn.execute(
                    "INSERT INTO price_lists(id,organization_id,code,name,currency,customer_id,channel,valid_from,valid_until,priority) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (price_list_id, principal.organization_id, code, name, currency, customer_id, channel.strip(), valid_from, valid_until, priority),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict(f"价目表编码已存在：{code}") from exc
            self.audit.record(conn, AuditContext(principal), "price_list.create", "price_list", price_list_id,
                              after={"code": code, "name": name, "customer_id": customer_id, "channel": channel})
        return self.price_list(principal, price_list_id)

    def add_rule(self, principal: Principal, price_list_id: str, product_id: str, min_quantity: int,
                 unit_price_cents: int, discount_basis_points: int = 0,
                 valid_from: str | None = None, valid_until: str | None = None) -> dict:
        principal.require("master.write")
        require_positive(min_quantity, "起订数量")
        if unit_price_cents < 0: raise ValidationError("单价不能为负")
        if not 0 <= discount_basis_points <= 10000: raise ValidationError("折扣率无效")
        if valid_from: validate_iso_date(valid_from)
        if valid_until: validate_iso_date(valid_until)
        rule_id = self._id("PRC")
        with self.store.connect() as conn:
            price_list = conn.execute("SELECT * FROM price_lists WHERE id=? AND organization_id=?", (price_list_id, principal.organization_id)).fetchone()
            if not price_list: raise NotFound(f"价目表不存在：{price_list_id}")
            product = conn.execute("SELECT id FROM product_master WHERE id=? AND organization_id=?", (product_id, principal.organization_id)).fetchone()
            if not product: raise NotFound(f"商品不存在：{product_id}")
            try:
                conn.execute(
                    "INSERT INTO price_rules(id,price_list_id,product_id,min_quantity,unit_price_cents,discount_basis_points,valid_from,valid_until) VALUES(?,?,?,?,?,?,?,?)",
                    (rule_id, price_list_id, product_id, min_quantity, unit_price_cents, discount_basis_points, valid_from, valid_until),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict("相同商品和数量阶梯已经存在") from exc
            self.audit.record(conn, AuditContext(principal), "price_rule.create", "price_rule", rule_id,
                              after={"price_list_id": price_list_id, "product_id": product_id, "min_quantity": min_quantity})
        return self.store.row("SELECT * FROM price_rules WHERE id=?", (rule_id,)) or {}

    def resolve(self, principal: Principal, product_id: str, quantity: int, customer_id: str | None = None,
                channel: str = "", pricing_date: str | None = None, currency: str = "CNY") -> dict:
        principal.require("sales.read")
        require_positive(quantity)
        pricing_date = pricing_date or date.today().isoformat(); validate_iso_date(pricing_date, "计价日期")
        product = self.store.row("SELECT * FROM product_master WHERE id=? AND organization_id=? AND active=1", (product_id, principal.organization_id))
        if not product: raise NotFound(f"商品不存在：{product_id}")
        params = (principal.organization_id, currency.upper(), customer_id, customer_id, channel, channel,
                  pricing_date, pricing_date, pricing_date, pricing_date, product_id, quantity)
        rule = self.store.row(
            "SELECT r.*,l.code AS price_list_code,l.name AS price_list_name,l.priority,"
            "CASE WHEN l.customer_id IS NOT NULL THEN 4 ELSE 0 END+CASE WHEN l.channel<>'' THEN 2 ELSE 0 END AS specificity "
            "FROM price_lists l JOIN price_rules r ON r.price_list_id=l.id WHERE l.organization_id=? AND l.active=1 AND l.currency=? "
            "AND (l.customer_id IS NULL OR l.customer_id=?) AND (? IS NOT NULL OR l.customer_id IS NULL) "
            "AND (l.channel='' OR l.channel=?) AND (?<>'' OR l.channel='') "
            "AND (l.valid_from IS NULL OR l.valid_from<=?) AND (l.valid_until IS NULL OR l.valid_until>=?) "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) AND (r.valid_until IS NULL OR r.valid_until>=?) "
            "AND r.product_id=? AND r.min_quantity<=? "
            "ORDER BY specificity DESC,l.priority ASC,r.min_quantity DESC,l.id LIMIT 1", params,
        )
        unit_price = rule["unit_price_cents"] if rule else product["sales_price_cents"]
        discount_bp = rule["discount_basis_points"] if rule else 0
        gross = unit_price * quantity; discount = calculate_tax(gross, discount_bp); net = gross - discount
        tax = calculate_tax(net, product["tax_rate_basis_points"])
        return {"product_id": product_id, "sku": product["sku"], "quantity": quantity, "currency": currency.upper(),
                "unit_price_cents": unit_price, "discount_basis_points": discount_bp, "gross_cents": gross,
                "discount_cents": discount, "net_cents": net, "tax_cents": tax, "total_cents": net + tax,
                "price_list_id": rule["price_list_id"] if rule else None,
                "price_list_code": rule["price_list_code"] if rule else "STANDARD"}

    def price_list(self, principal: Principal, price_list_id: str) -> dict:
        principal.require("master.read")
        header = self.store.row("SELECT * FROM price_lists WHERE id=? AND organization_id=?", (price_list_id, principal.organization_id))
        if not header: raise NotFound(f"价目表不存在：{price_list_id}")
        header["rules"] = self.store.rows(
            "SELECT r.*,p.sku,p.name AS product_name FROM price_rules r JOIN product_master p ON p.id=r.product_id "
            "WHERE r.price_list_id=? ORDER BY p.sku,r.min_quantity", (price_list_id,),
        )
        return header

    def list_price_lists(self, principal: Principal, active_only: bool = True) -> list[dict]:
        principal.require("master.read")
        sql = ("SELECT pl.*,c.name AS customer_name,COUNT(r.id) AS rule_count FROM price_lists pl "
               "LEFT JOIN customer_master c ON c.id=pl.customer_id LEFT JOIN price_rules r ON r.price_list_id=pl.id "
               "WHERE pl.organization_id=?")
        if active_only: sql += " AND pl.active=1"
        return self.store.rows(sql+" GROUP BY pl.id ORDER BY pl.priority,pl.code", (principal.organization_id,))
