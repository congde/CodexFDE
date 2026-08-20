from __future__ import annotations

import re
import sqlite3
import uuid
from typing import Any

from .audit import AuditContext, AuditService
from .identity import Principal
from .models import Conflict, NotFound, ValidationError, require_non_negative
from .store import ERPStore


CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class MasterDataService:
    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    @staticmethod
    def _code(value: str, label: str = "编码") -> str:
        result = value.strip().upper()
        if not CODE_RE.fullmatch(result):
            raise ValidationError(f"{label}只能包含字母、数字、点、横线和下划线，最多 64 位")
        return result

    @staticmethod
    def _required(value: str, label: str, maximum: int = 200) -> str:
        result = value.strip()
        if not result:
            raise ValidationError(f"{label}不能为空")
        if len(result) > maximum:
            raise ValidationError(f"{label}不能超过 {maximum} 个字符")
        return result

    @staticmethod
    def _email(value: str) -> str:
        value = value.strip().lower()
        if value and not EMAIL_RE.fullmatch(value):
            raise ValidationError("邮箱格式不正确")
        return value

    def create_site(self, principal: Principal, code: str, name: str, site_type: str = "warehouse",
                    address: str = "", contact_name: str = "", contact_phone: str = "") -> dict:
        principal.require("master.write")
        if site_type not in {"warehouse", "store", "transit", "virtual"}:
            raise ValidationError("仓库类型无效")
        entity_id = self._id("SITE")
        context = AuditContext(principal)
        with self.store.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO sites(id,organization_id,code,name,site_type,address,contact_name,contact_phone) VALUES(?,?,?,?,?,?,?,?)",
                    (entity_id, principal.organization_id, self._code(code), self._required(name, "仓库名称"),
                     site_type, address.strip(), contact_name.strip(), contact_phone.strip()),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict(f"仓库编码已存在：{code}") from exc
            self.audit.record(conn, context, "site.create", "site", entity_id, after={"code": code, "name": name})
        return self.site(principal, entity_id)

    def site(self, principal: Principal, site_id: str) -> dict:
        principal.require("master.read")
        row = self.store.row("SELECT * FROM sites WHERE id=? AND organization_id=?", (site_id, principal.organization_id))
        if not row:
            raise NotFound(f"仓库不存在：{site_id}")
        row["locations"] = self.store.rows("SELECT * FROM storage_locations WHERE site_id=? ORDER BY code", (site_id,))
        return row

    def list_sites(self, principal: Principal, active_only: bool = True) -> list[dict]:
        principal.require("master.read")
        sql = "SELECT * FROM sites WHERE organization_id=?"
        params: tuple[Any, ...] = (principal.organization_id,)
        if active_only:
            sql += " AND active=1"
        sites = self.store.rows(sql + " ORDER BY code", params)
        for site in sites:
            location_sql = "SELECT * FROM storage_locations WHERE site_id=?"
            location_params: tuple[Any, ...] = (site["id"],)
            if active_only:
                location_sql += " AND active=1"
            site["locations"] = self.store.rows(location_sql + " ORDER BY code", location_params)
        return sites

    def create_location(self, principal: Principal, site_id: str, code: str, name: str,
                        location_type: str = "internal") -> dict:
        principal.require("master.write")
        self.site(principal, site_id)
        if location_type not in {"internal", "receiving", "shipping", "quarantine", "damaged", "supplier", "customer"}:
            raise ValidationError("库位类型无效")
        location_id = self._id("LOC")
        with self.store.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO storage_locations(id,site_id,code,name,location_type) VALUES(?,?,?,?,?)",
                    (location_id, site_id, self._code(code, "库位编码"), self._required(name, "库位名称"), location_type),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict(f"库位编码已存在：{code}") from exc
            self.audit.record(conn, AuditContext(principal), "location.create", "location", location_id, after={"site_id": site_id, "code": code})
        return self.location(principal, location_id)

    def location(self, principal: Principal, location_id: str) -> dict:
        principal.require("master.read")
        row = self.store.row(
            "SELECT l.*,s.organization_id,s.code AS site_code,s.name AS site_name FROM storage_locations l JOIN sites s ON s.id=l.site_id "
            "WHERE l.id=? AND s.organization_id=?", (location_id, principal.organization_id),
        )
        if not row:
            raise NotFound(f"库位不存在：{location_id}")
        return row

    def create_category(self, principal: Principal, code: str, name: str, parent_id: str | None = None) -> dict:
        principal.require("master.write")
        if parent_id:
            parent = self.store.row("SELECT id FROM product_categories WHERE id=? AND organization_id=?", (parent_id, principal.organization_id))
            if not parent:
                raise NotFound(f"上级分类不存在：{parent_id}")
        category_id = self._id("CAT")
        with self.store.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO product_categories(id,organization_id,parent_id,code,name) VALUES(?,?,?,?,?)",
                    (category_id, principal.organization_id, parent_id, self._code(code), self._required(name, "分类名称")),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict(f"分类编码已存在：{code}") from exc
            self.audit.record(conn, AuditContext(principal), "category.create", "category", category_id, after={"code": code, "name": name})
        return self.store.row("SELECT * FROM product_categories WHERE id=?", (category_id,)) or {}

    def create_product(self, principal: Principal, sku: str, name: str, sales_price_cents: int = 0,
                       standard_cost_cents: int = 0, barcode: str = "", category_id: str | None = None,
                       tracking: str = "none", tax_rate_basis_points: int = 1300, min_stock: int = 0,
                       max_stock: int = 0, description: str = "", shelf_life_days: int = 0) -> dict:
        principal.require("master.write")
        sku = self._code(sku, "SKU")
        name = self._required(name, "商品名称")
        for value, label in ((sales_price_cents, "销售价"), (standard_cost_cents, "标准成本"),
                             (min_stock, "最低库存"), (max_stock, "最高库存"), (shelf_life_days, "保质期")):
            require_non_negative(value, label)
        if not 0 <= tax_rate_basis_points <= 10000:
            raise ValidationError("税率必须在 0 到 100% 之间")
        if tracking not in {"none", "lot", "serial"}:
            raise ValidationError("跟踪方式必须是 none、lot 或 serial")
        if max_stock and max_stock < min_stock:
            raise ValidationError("最高库存不能低于最低库存")
        product_id = self._id("PRD")
        with self.store.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO product_master(id,organization_id,sku,barcode,name,description,category_id,uom_id,tracking,sales_price_cents,standard_cost_cents,tax_rate_basis_points,min_stock,max_stock,shelf_life_days) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (product_id, principal.organization_id, sku, barcode.strip(), name, description.strip(), category_id,
                     "UOM-EA", tracking, sales_price_cents, standard_cost_cents, tax_rate_basis_points, min_stock, max_stock, shelf_life_days),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict(f"SKU 或条码已存在：{sku}") from exc
            self.audit.record(conn, AuditContext(principal), "product.create", "product", product_id, after={"sku": sku, "name": name})
        return self.product(principal, product_id)

    def product(self, principal: Principal, product_id_or_sku: str) -> dict:
        principal.require("master.read")
        row = self.store.row(
            "SELECT p.*,c.name AS category_name,u.code AS uom_code,u.name AS uom_name FROM product_master p "
            "LEFT JOIN product_categories c ON c.id=p.category_id LEFT JOIN units_of_measure u ON u.id=p.uom_id "
            "WHERE p.organization_id=? AND (p.id=? OR p.sku=? COLLATE NOCASE)",
            (principal.organization_id, product_id_or_sku, product_id_or_sku),
        )
        if not row:
            raise NotFound(f"商品不存在：{product_id_or_sku}")
        return row

    def list_products(self, principal: Principal, query: str = "", active_only: bool = True,
                      limit: int = 100, offset: int = 0) -> list[dict]:
        principal.require("master.read")
        filters = ["p.organization_id=?"]
        params: list[Any] = [principal.organization_id]
        if active_only:
            filters.append("p.active=1")
        if query:
            filters.append("(p.sku LIKE ? OR p.name LIKE ? OR p.barcode LIKE ?)")
            token = f"%{query.strip()}%"; params.extend((token, token, token))
        params.extend((max(1, min(limit, 500)), max(0, offset)))
        return self.store.rows(
            f"SELECT p.*,c.name AS category_name FROM product_master p LEFT JOIN product_categories c ON c.id=p.category_id "
            f"WHERE {' AND '.join(filters)} ORDER BY p.sku LIMIT ? OFFSET ?", tuple(params),
        )

    def update_product(self, principal: Principal, product_id: str, version: int, **changes: Any) -> dict:
        principal.require("master.write")
        before = self.product(principal, product_id)
        allowed = {"name", "barcode", "description", "category_id", "sales_price_cents", "standard_cost_cents",
                   "tax_rate_basis_points", "min_stock", "max_stock", "shelf_life_days", "purchasable", "saleable", "active"}
        changes = {key: value for key, value in changes.items() if key in allowed}
        if not changes:
            return before
        if "name" in changes:
            changes["name"] = self._required(str(changes["name"]), "商品名称")
        if "barcode" in changes:
            changes["barcode"] = str(changes["barcode"]).strip()
        for key in ("sales_price_cents", "standard_cost_cents", "min_stock", "max_stock", "shelf_life_days"):
            if key in changes:
                changes[key] = int(changes[key]); require_non_negative(changes[key], key)
        if "tax_rate_basis_points" in changes:
            changes["tax_rate_basis_points"] = int(changes["tax_rate_basis_points"])
            if not 0 <= changes["tax_rate_basis_points"] <= 10000:
                raise ValidationError("税率必须在 0 到 100% 之间")
        for key in ("purchasable", "saleable", "active"):
            if key in changes:
                changes[key] = int(changes[key])
                if changes[key] not in {0, 1}: raise ValidationError(f"{key} 必须为 0 或 1")
        minimum = int(changes.get("min_stock", before["min_stock"]))
        maximum = int(changes.get("max_stock", before["max_stock"]))
        if maximum and maximum < minimum:
            raise ValidationError("最高库存不能低于最低库存")
        set_sql = ",".join(f"{key}=?" for key in changes)
        with self.store.connect() as conn:
            try:
                cursor = conn.execute(
                    f"UPDATE product_master SET {set_sql},updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=? AND organization_id=? AND version=?",
                    (*changes.values(), product_id, principal.organization_id, version),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict("条码与其他商品重复") from exc
            if cursor.rowcount != 1:
                raise Conflict("商品已被其他用户修改，请刷新后重试")
            after = dict(before); after.update(changes); after["version"] = version + 1
            self.audit.record(conn, AuditContext(principal), "product.update", "product", product_id, before=before, after=after)
        return self.product(principal, product_id)

    def create_customer(self, principal: Principal, code: str, name: str, **fields: Any) -> dict:
        principal.require("master.write")
        customer_id = self._id("CUS")
        email = self._email(str(fields.get("email", "")))
        with self.store.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO customer_master(id,organization_id,code,name,customer_type,tax_number,contact_name,phone,email,billing_address,shipping_address,currency,payment_terms_days,credit_limit_cents) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (customer_id, principal.organization_id, self._code(code), self._required(name, "客户名称"),
                     fields.get("customer_type", "business"), str(fields.get("tax_number", "")).strip(),
                     str(fields.get("contact_name", "")).strip(), str(fields.get("phone", "")).strip(), email,
                     str(fields.get("billing_address", "")).strip(), str(fields.get("shipping_address", "")).strip(),
                     str(fields.get("currency", "CNY")).upper(), int(fields.get("payment_terms_days", 0)),
                     int(fields.get("credit_limit_cents", 0))),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict(f"客户编码已存在：{code}") from exc
            self.audit.record(conn, AuditContext(principal), "customer.create", "customer", customer_id, after={"code": code, "name": name})
        return self.partner(principal, "customer", customer_id)

    def create_supplier(self, principal: Principal, code: str, name: str, **fields: Any) -> dict:
        principal.require("master.write")
        supplier_id = self._id("SUP")
        email = self._email(str(fields.get("email", "")))
        with self.store.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO supplier_master(id,organization_id,code,name,tax_number,contact_name,phone,email,address,currency,payment_terms_days,lead_time_days) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (supplier_id, principal.organization_id, self._code(code), self._required(name, "供应商名称"),
                     str(fields.get("tax_number", "")).strip(), str(fields.get("contact_name", "")).strip(),
                     str(fields.get("phone", "")).strip(), email, str(fields.get("address", "")).strip(),
                     str(fields.get("currency", "CNY")).upper(), int(fields.get("payment_terms_days", 0)),
                     int(fields.get("lead_time_days", 0))),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict(f"供应商编码已存在：{code}") from exc
            self.audit.record(conn, AuditContext(principal), "supplier.create", "supplier", supplier_id, after={"code": code, "name": name})
        return self.partner(principal, "supplier", supplier_id)

    def partner(self, principal: Principal, partner_type: str, partner_id: str) -> dict:
        principal.require("master.read")
        table = "customer_master" if partner_type == "customer" else "supplier_master" if partner_type == "supplier" else ""
        if not table:
            raise ValidationError("往来单位类型无效")
        row = self.store.row(f"SELECT * FROM {table} WHERE id=? AND organization_id=?", (partner_id, principal.organization_id))
        if not row:
            raise NotFound(f"往来单位不存在：{partner_id}")
        return row

    def update_partner(self, principal: Principal, partner_type: str, partner_id: str,
                       version: int, **changes: Any) -> dict:
        """Update a customer or supplier with optimistic concurrency control."""
        principal.require("master.write")
        table = "customer_master" if partner_type == "customer" else "supplier_master" if partner_type == "supplier" else ""
        if not table:
            raise ValidationError("往来单位类型无效")
        before = self.partner(principal, partner_type, partner_id)
        common = {"name", "tax_number", "contact_name", "phone", "email", "currency",
                  "payment_terms_days", "status"}
        specific = ({"customer_type", "billing_address", "shipping_address", "credit_limit_cents"}
                    if partner_type == "customer" else {"address", "lead_time_days"})
        changes = {key: value for key, value in changes.items() if key in common | specific}
        if not changes:
            return before
        if "name" in changes:
            changes["name"] = self._required(str(changes["name"]), "往来单位名称")
        if "email" in changes:
            changes["email"] = self._email(str(changes["email"]))
        if "currency" in changes:
            currency = str(changes["currency"]).strip().upper()
            if len(currency) != 3:
                raise ValidationError("币种必须为三位代码")
            changes["currency"] = currency
        if "status" in changes and changes["status"] not in {"active", "inactive"}:
            raise ValidationError("往来单位状态无效")
        if "customer_type" in changes and changes["customer_type"] not in {"business", "individual"}:
            raise ValidationError("客户类型无效")
        for key in ("payment_terms_days", "credit_limit_cents", "lead_time_days"):
            if key in changes:
                changes[key] = int(changes[key])
                require_non_negative(changes[key], key)
        set_sql = ",".join(f"{key}=?" for key in changes)
        with self.store.connect() as conn:
            cursor = conn.execute(
                f"UPDATE {table} SET {set_sql},updated_at=CURRENT_TIMESTAMP,version=version+1 "
                "WHERE id=? AND organization_id=? AND version=?",
                (*changes.values(), partner_id, principal.organization_id, version),
            )
            if cursor.rowcount != 1:
                raise Conflict("往来单位已被其他用户修改，请刷新后重试")
            after = dict(before); after.update(changes); after["version"] = version + 1
            self.audit.record(conn, AuditContext(principal), f"{partner_type}.update", partner_type,
                              partner_id, before=before, after=after)
        return self.partner(principal, partner_type, partner_id)

    def list_partners(self, principal: Principal, partner_type: str, query: str = "") -> list[dict]:
        principal.require("master.read")
        table = "customer_master" if partner_type == "customer" else "supplier_master" if partner_type == "supplier" else ""
        if not table:
            raise ValidationError("往来单位类型无效")
        params: list[Any] = [principal.organization_id]
        where = "organization_id=?"
        if query:
            where += " AND (code LIKE ? OR name LIKE ? OR phone LIKE ?)"
            token = f"%{query.strip()}%"; params.extend((token, token, token))
        return self.store.rows(f"SELECT * FROM {table} WHERE {where} ORDER BY code LIMIT 500", tuple(params))
