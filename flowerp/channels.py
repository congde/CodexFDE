from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Iterable

from .audit import AuditContext, AuditService
from .identity import Principal
from .models import Conflict, InvalidTransition, NotFound, ValidationError
from .sales import SalesService
from .store import ERPStore


PLATFORMS = {"taobao", "jd", "pinduoduo", "douyin", "wechat", "kuaishou", "amazon", "shopee", "custom", "mock"}
PAID_STATUSES = {"paid", "awaiting_shipment", "partially_shipped", "buyer_paid", "ready_to_ship"}


class EcommerceChannelService:
    """Connector-neutral e-commerce order hub.

    Platform adapters normalize remote payloads before calling ``ingest_orders``.
    Credentials are referenced by environment-variable name and are never stored
    in this database.
    """

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)
        self.sales = SalesService(store, self.audit)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    @staticmethod
    def _clean_env_name(value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"[A-Z_][A-Z0-9_]{1,127}", value):
            raise ValidationError("凭据环境变量名格式无效")
        return value

    def create_shop(self, principal: Principal, platform: str, code: str, name: str,
                    settlement_customer_id: str, default_site_id: str, external_shop_id: str,
                    currency: str = "CNY", sync_mode: str = "pull_webhook",
                    credential_env: str = "", webhook_secret_env: str = "") -> dict:
        principal.require("sales.write")
        platform = platform.strip().lower(); code = code.strip().upper(); name = name.strip()
        external_shop_id = external_shop_id.strip(); currency = currency.strip().upper()
        if platform not in PLATFORMS: raise ValidationError("不支持的平台类型")
        if not code or not name or not external_shop_id: raise ValidationError("店铺编码、名称和平台店铺 ID 不能为空")
        if sync_mode not in {"pull", "webhook", "pull_webhook", "manual"}: raise ValidationError("同步模式无效")
        if len(currency) != 3: raise ValidationError("币种必须是三位 ISO 代码")
        credential_env = self._clean_env_name(credential_env)
        webhook_secret_env = self._clean_env_name(webhook_secret_env)
        shop_id = self._id("SHPCH")
        with self.store.connect() as conn:
            customer = conn.execute("SELECT id,currency FROM customer_master WHERE id=? AND organization_id=? AND status='active'", (settlement_customer_id, principal.organization_id)).fetchone()
            if not customer: raise NotFound("平台结算客户不存在或已停用")
            if customer["currency"] != currency: raise ValidationError("店铺币种必须与平台结算客户币种一致")
            site = conn.execute("SELECT id FROM sites WHERE id=? AND organization_id=? AND active=1", (default_site_id, principal.organization_id)).fetchone()
            if not site: raise NotFound("默认发货仓不存在或已停用")
            try:
                conn.execute(
                    "INSERT INTO channel_shops(id,organization_id,platform,code,name,external_shop_id,settlement_customer_id,default_site_id,currency,sync_mode,credential_env,webhook_secret_env,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (shop_id, principal.organization_id, platform, code, name, external_shop_id, settlement_customer_id, default_site_id, currency, sync_mode, credential_env, webhook_secret_env, principal.user_id),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper(): raise Conflict("店铺编码或平台店铺 ID 已存在") from exc
                raise
            self.audit.record(conn, AuditContext(principal), "channel.shop.create", "channel_shop", shop_id,
                              after={"platform": platform, "code": code, "external_shop_id": external_shop_id})
        return self.shop(principal, shop_id)

    def shop(self, principal: Principal, shop_id: str) -> dict:
        principal.require("sales.read")
        item = self.store.row(
            "SELECT s.*,c.code AS customer_code,c.name AS customer_name,site.code AS site_code,site.name AS site_name "
            "FROM channel_shops s JOIN customer_master c ON c.id=s.settlement_customer_id JOIN sites site ON site.id=s.default_site_id "
            "WHERE s.id=? AND s.organization_id=?", (shop_id, principal.organization_id))
        if not item: raise NotFound("渠道店铺不存在")
        item["connection_status"] = self._connection_status(item)
        return item

    @staticmethod
    def _connection_status(shop: dict) -> str:
        if shop["status"] != "active": return shop["status"]
        if shop["platform"] == "mock": return "sandbox"
        env_name = shop.get("credential_env", "")
        return "configured" if env_name and bool(os.environ.get(env_name)) else "unconfigured"

    def list_shops(self, principal: Principal) -> list[dict]:
        principal.require("sales.read")
        items = self.store.rows(
            "SELECT s.*,c.name AS customer_name,site.name AS site_name FROM channel_shops s "
            "JOIN customer_master c ON c.id=s.settlement_customer_id JOIN sites site ON site.id=s.default_site_id "
            "WHERE s.organization_id=? ORDER BY s.status,s.platform,s.code", (principal.organization_id,))
        for item in items: item["connection_status"] = self._connection_status(item)
        return items

    def map_listing(self, principal: Principal, shop_id: str, external_product_id: str,
                    external_sku_id: str, title: str, components: Iterable[dict]) -> dict:
        principal.require("sales.write")
        external_product_id = external_product_id.strip(); external_sku_id = external_sku_id.strip()
        prepared = list(components)
        if not external_product_id or not external_sku_id or not prepared: raise ValidationError("平台商品、规格和映射明细不能为空")
        listing_id = self._id("CHSKU")
        with self.store.connect() as conn:
            shop = conn.execute("SELECT id FROM channel_shops WHERE id=? AND organization_id=? AND status='active'", (shop_id, principal.organization_id)).fetchone()
            if not shop: raise NotFound("有效渠道店铺不存在")
            product_ids: set[str] = set(); shares = 0
            for component in prepared:
                product_id = str(component.get("product_id", "")).strip()
                quantity = int(component.get("quantity", 1)); share = int(component.get("revenue_share_basis_points", 0))
                if not product_id or quantity <= 0 or share <= 0: raise ValidationError("映射商品、用量和分摊比例必须有效")
                if product_id in product_ids: raise ValidationError("同一映射不能重复内部 SKU")
                product_ids.add(product_id); shares += share
                product = conn.execute("SELECT id FROM product_master WHERE id=? AND organization_id=? AND active=1 AND saleable=1", (product_id, principal.organization_id)).fetchone()
                if not product: raise NotFound(f"可销售内部商品不存在：{product_id}")
            if shares != 10000: raise ValidationError("映射收入分摊比例合计必须为 100%")
            try:
                conn.execute("INSERT INTO channel_listings(id,organization_id,shop_id,external_product_id,external_sku_id,title,created_by) VALUES(?,?,?,?,?,?,?)",
                             (listing_id, principal.organization_id, shop_id, external_product_id, external_sku_id, title.strip(), principal.user_id))
            except Exception as exc:
                if "UNIQUE" in str(exc).upper(): raise Conflict("该店铺的平台商品规格已映射") from exc
                raise
            for component in prepared:
                conn.execute("INSERT INTO channel_listing_components(id,listing_id,product_id,quantity,revenue_share_basis_points) VALUES(?,?,?,?,?)",
                             (self._id("CHCMP"), listing_id, str(component["product_id"]), int(component.get("quantity", 1)), int(component["revenue_share_basis_points"])))
            self.audit.record(conn, AuditContext(principal), "channel.listing.map", "channel_listing", listing_id,
                              after={"shop_id": shop_id, "external_sku_id": external_sku_id, "component_count": len(prepared)})
        return self.listing(principal, listing_id)

    def listing(self, principal: Principal, listing_id: str) -> dict:
        principal.require("sales.read")
        item = self.store.row("SELECT l.*,s.code AS shop_code,s.name AS shop_name FROM channel_listings l JOIN channel_shops s ON s.id=l.shop_id WHERE l.id=? AND l.organization_id=?", (listing_id, principal.organization_id))
        if not item: raise NotFound("平台商品映射不存在")
        item["components"] = self.store.rows("SELECT c.*,p.sku,p.name AS product_name FROM channel_listing_components c JOIN product_master p ON p.id=c.product_id WHERE c.listing_id=? ORDER BY p.sku", (listing_id,))
        return item

    def list_listings(self, principal: Principal, shop_id: str = "") -> list[dict]:
        principal.require("sales.read")
        sql = "SELECT l.*,s.code AS shop_code,s.name AS shop_name,COUNT(c.id) AS component_count,GROUP_CONCAT(p.sku||' ×'||c.quantity,', ') AS internal_skus FROM channel_listings l JOIN channel_shops s ON s.id=l.shop_id LEFT JOIN channel_listing_components c ON c.listing_id=l.id LEFT JOIN product_master p ON p.id=c.product_id WHERE l.organization_id=?"
        params: list[object] = [principal.organization_id]
        if shop_id: sql += " AND l.shop_id=?"; params.append(shop_id)
        sql += " GROUP BY l.id ORDER BY s.code,l.external_product_id,l.external_sku_id"
        return self.store.rows(sql, tuple(params))

    @staticmethod
    def _normalized_payload(raw: dict) -> dict:
        lines = []
        for index, item in enumerate(raw.get("lines", []), 1):
            quantity = int(item.get("quantity", 0)); unit = int(item.get("unit_price_cents", 0)); discount = int(item.get("discount_cents", 0))
            lines.append({
                "external_line_id": str(item.get("external_line_id") or index).strip(),
                "external_product_id": str(item.get("external_product_id", "")).strip(),
                "external_sku_id": str(item.get("external_sku_id", "")).strip(),
                "title": str(item.get("title", "")).strip(), "quantity": quantity,
                "unit_price_cents": unit, "discount_cents": discount,
                "total_cents": int(item.get("total_cents", quantity * unit - discount)),
            })
        goods = int(raw.get("goods_cents", sum(i["quantity"] * i["unit_price_cents"] for i in lines)))
        discount = int(raw.get("discount_cents", sum(i["discount_cents"] for i in lines)))
        freight = int(raw.get("freight_cents", 0))
        return {
            "external_order_id": str(raw.get("external_order_id", "")).strip(),
            "external_status": str(raw.get("external_status", "")).strip().lower(),
            "order_time": str(raw.get("order_time", "")).strip(), "paid_time": str(raw.get("paid_time", "")).strip(),
            "currency": str(raw.get("currency", "CNY")).strip().upper(), "goods_cents": goods,
            "discount_cents": discount, "freight_cents": freight,
            "total_cents": int(raw.get("total_cents", goods - discount + freight)),
            "buyer_reference": str(raw.get("buyer_reference", "")).strip(), "recipient": str(raw.get("recipient", "")).strip(),
            "phone": str(raw.get("phone", "")).strip(), "country": str(raw.get("country", "CN")).strip().upper(),
            "province": str(raw.get("province", "")).strip(), "city": str(raw.get("city", "")).strip(),
            "district": str(raw.get("district", "")).strip(), "street": str(raw.get("street", "")).strip(),
            "postal_code": str(raw.get("postal_code", "")).strip(), "buyer_note": str(raw.get("buyer_note", "")).strip(),
            "lines": lines,
        }

    @staticmethod
    def _validate_timestamp(value: str, label: str, required: bool = True) -> None:
        if not value and not required: return
        try: datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc: raise ValidationError(f"{label}必须是 ISO 8601 时间") from exc

    def ingest_orders(self, principal: Principal, shop_id: str, orders: Iterable[dict], trigger_type: str = "manual", cursor: str = "") -> dict:
        principal.require("sales.write")
        if trigger_type not in {"pull", "webhook", "manual", "mock"}: raise ValidationError("同步触发类型无效")
        batch = list(orders)
        if not batch: raise ValidationError("同步批次至少包含一个订单")
        shop = self.shop(principal, shop_id)
        if shop["status"] != "active": raise InvalidTransition("店铺未启用，不能接收订单")
        run_id = self._id("CHRUN")
        with self.store.connect() as conn:
            conn.execute("INSERT INTO channel_sync_runs(id,organization_id,shop_id,trigger_type,cursor_value,status,created_by) VALUES(?,?,?,?,?,'running',?)", (run_id, principal.organization_id, shop_id, trigger_type, cursor.strip(), principal.user_id))
        counters = {"received_count": 0, "imported_count": 0, "blocked_count": 0, "replay_count": 0, "error_count": 0}; results = []
        errors: list[str] = []
        for raw in batch:
            try:
                item = self._ingest_one(principal, shop, raw)
                results.append(item); counters["replay_count" if item["idempotent_replay"] else "received_count"] += 1
                if item["status"] == "blocked": counters["blocked_count"] += 1
            except Exception as exc:
                counters["error_count"] += 1; errors.append(str(exc)[:240])
        status = "completed" if not errors else ("partial" if results else "failed")
        with self.store.connect() as conn:
            conn.execute("UPDATE channel_sync_runs SET status=?,received_count=?,imported_count=?,blocked_count=?,replay_count=?,error_count=?,error_summary=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
                         (status, counters["received_count"], counters["imported_count"], counters["blocked_count"], counters["replay_count"], counters["error_count"], "；".join(errors[:5]), run_id))
            if status != "failed": conn.execute("UPDATE channel_shops SET last_synced_at=CURRENT_TIMESTAMP,last_error='',updated_at=CURRENT_TIMESTAMP WHERE id=?", (shop_id,))
            else: conn.execute("UPDATE channel_shops SET last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", ("；".join(errors[:5]), shop_id))
        return {"id": run_id, "status": status, **counters, "errors": errors, "items": results}

    def _ingest_one(self, principal: Principal, shop: dict, raw: dict) -> dict:
        data = self._normalized_payload(raw)
        if not data["external_order_id"]: raise ValidationError("外部订单号不能为空")
        if not data["lines"]: raise ValidationError(f"订单 {data['external_order_id']} 没有商品明细")
        self._validate_timestamp(data["order_time"], "下单时间")
        self._validate_timestamp(data["paid_time"], "支付时间", False)
        if any(not x["external_product_id"] or not x["external_sku_id"] or x["quantity"] <= 0 or x["unit_price_cents"] < 0 or x["discount_cents"] < 0 or x["total_cents"] < 0 for x in data["lines"]):
            raise ValidationError(f"订单 {data['external_order_id']} 的商品明细无效")
        digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        order_id = self._id("CHORD")
        with self.store.connect() as conn:
            existing = conn.execute("SELECT id,payload_hash FROM channel_orders WHERE shop_id=? AND external_order_id=?", (shop["id"], data["external_order_id"])).fetchone()
            if existing:
                if existing["payload_hash"] != digest: raise Conflict(f"外部订单 {data['external_order_id']} 重复但内容不一致，已阻断覆盖")
                item = self._order_in_conn(conn, principal.organization_id, existing["id"]); item["idempotent_replay"] = True; return item
            line_rows = []
            for index, line in enumerate(data["lines"], 1):
                listing = conn.execute("SELECT id FROM channel_listings WHERE shop_id=? AND external_product_id=? AND external_sku_id=? AND status='active'", (shop["id"], line["external_product_id"], line["external_sku_id"])).fetchone()
                line_rows.append((index, line, listing["id"] if listing else None))
            blockers = self._blockers(data, shop, line_rows)
            conn.execute(
                "INSERT INTO channel_orders(id,organization_id,shop_id,external_order_id,external_status,status,order_time,paid_time,currency,goods_cents,discount_cents,freight_cents,total_cents,buyer_reference,recipient,phone,country,province,city,district,street,postal_code,buyer_note,payload_hash,blocker_codes_json,blocker_details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, principal.organization_id, shop["id"], data["external_order_id"], data["external_status"], "blocked" if blockers else "received", data["order_time"], data["paid_time"] or None, data["currency"], data["goods_cents"], data["discount_cents"], data["freight_cents"], data["total_cents"], data["buyer_reference"], data["recipient"], data["phone"], data["country"], data["province"], data["city"], data["district"], data["street"], data["postal_code"], data["buyer_note"], digest, json.dumps([x["code"] for x in blockers]), json.dumps(blockers, ensure_ascii=False)),
            )
            for index, line, listing_id in line_rows:
                conn.execute("INSERT INTO channel_order_lines(id,channel_order_id,line_number,external_line_id,external_product_id,external_sku_id,title,quantity,unit_price_cents,discount_cents,total_cents,listing_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                             (self._id("CHOL"), order_id, index, line["external_line_id"], line["external_product_id"], line["external_sku_id"], line["title"], line["quantity"], line["unit_price_cents"], line["discount_cents"], line["total_cents"], listing_id))
            self.audit.record(conn, AuditContext(principal), "channel.order.ingest", "channel_order", order_id,
                              after={"shop_id": shop["id"], "external_order_id": data["external_order_id"], "blockers": [x["code"] for x in blockers]})
            item = self._order_in_conn(conn, principal.organization_id, order_id); item["idempotent_replay"] = False; return item

    @staticmethod
    def _blockers(data: dict, shop: dict, line_rows: list[tuple[int, dict, str | None]]) -> list[dict]:
        blockers: list[dict] = []
        if data["external_status"] not in PAID_STATUSES: blockers.append({"code": "ORDER_NOT_PAID", "message": "平台订单尚未进入可履约状态"})
        if data["currency"] != shop["currency"]: blockers.append({"code": "CURRENCY_MISMATCH", "message": "订单币种与店铺结算币种不一致"})
        if not data["recipient"]: blockers.append({"code": "RECIPIENT_MISSING", "message": "收件人为空"})
        digits = re.sub(r"\D", "", data["phone"])
        if len(digits) < 7: blockers.append({"code": "PHONE_INVALID", "message": "收件电话无效"})
        if not data["province"] or not data["city"] or not data["street"]: blockers.append({"code": "ADDRESS_INCOMPLETE", "message": "省、市和详细地址必须完整"})
        missing = [line["external_sku_id"] for _, line, listing_id in line_rows if not listing_id]
        if missing: blockers.append({"code": "SKU_UNMAPPED", "message": "存在未映射的平台 SKU", "external_sku_ids": missing})
        calculated_goods = sum(line["quantity"] * line["unit_price_cents"] for _, line, _ in line_rows)
        calculated_line_total = sum(line["total_cents"] for _, line, _ in line_rows)
        if (calculated_goods != data["goods_cents"] or
                calculated_line_total != data["goods_cents"] - data["discount_cents"] or
                calculated_line_total + data["freight_cents"] != data["total_cents"]):
            blockers.append({"code": "AMOUNT_MISMATCH", "message": "订单头金额与商品明细不平"})
        return blockers

    def _order_in_conn(self, conn, organization_id: str, order_id: str) -> dict:
        row = conn.execute("SELECT o.*,s.code AS shop_code,s.name AS shop_name,s.platform,s.default_site_id FROM channel_orders o JOIN channel_shops s ON s.id=o.shop_id WHERE o.id=? AND o.organization_id=?", (order_id, organization_id)).fetchone()
        if not row: raise NotFound("渠道订单不存在")
        item = dict(row); item["blocker_codes"] = json.loads(item.pop("blocker_codes_json")); item["blockers"] = json.loads(item.pop("blocker_details_json"))
        item["lines"] = [dict(x) for x in conn.execute("SELECT l.*,m.title AS mapping_title FROM channel_order_lines l LEFT JOIN channel_listings m ON m.id=l.listing_id WHERE l.channel_order_id=? ORDER BY l.line_number", (order_id,)).fetchall()]
        return item

    def order(self, principal: Principal, order_id: str) -> dict:
        principal.require("sales.read")
        with self.store.connect() as conn: return self._order_in_conn(conn, principal.organization_id, order_id)

    def list_orders(self, principal: Principal, status: str = "", shop_id: str = "", limit: int = 200) -> list[dict]:
        principal.require("sales.read")
        filters = ["o.organization_id=?"]; params: list[object] = [principal.organization_id]
        if status: filters.append("o.status=?"); params.append(status)
        if shop_id: filters.append("o.shop_id=?"); params.append(shop_id)
        params.append(max(1, min(int(limit), 500)))
        rows = self.store.rows("SELECT o.*,s.code AS shop_code,s.name AS shop_name,s.platform,COUNT(l.id) AS line_count FROM channel_orders o JOIN channel_shops s ON s.id=o.shop_id LEFT JOIN channel_order_lines l ON l.channel_order_id=o.id WHERE " + " AND ".join(filters) + " GROUP BY o.id ORDER BY o.order_time DESC,o.created_at DESC LIMIT ?", tuple(params))
        for item in rows:
            item["blocker_codes"] = json.loads(item.pop("blocker_codes_json")); item.pop("blocker_details_json")
        return rows

    def review_and_import(self, principal: Principal, order_id: str, reserve: bool = True) -> dict:
        principal.require("sales.confirm")
        with self.store.connect() as conn:
            order = self._order_in_conn(conn, principal.organization_id, order_id)
            if order["status"] == "imported": return order
            if order["status"] == "cancelled": raise InvalidTransition("已取消渠道订单不能审单")
            shop = dict(conn.execute("SELECT * FROM channel_shops WHERE id=?", (order["shop_id"],)).fetchone())
            for line in order["lines"]:
                mapping = conn.execute(
                    "SELECT id FROM channel_listings WHERE shop_id=? AND external_product_id=? AND external_sku_id=? AND status='active'",
                    (order["shop_id"], line["external_product_id"], line["external_sku_id"]),
                ).fetchone()
                line["listing_id"] = mapping["id"] if mapping else None
                conn.execute("UPDATE channel_order_lines SET listing_id=? WHERE id=?", (line["listing_id"], line["id"]))
            line_rows = [(x["line_number"], x, x["listing_id"]) for x in order["lines"]]
            data = dict(order); blockers = self._blockers(data, shop, line_rows)
            conn.execute("UPDATE channel_orders SET status=?,blocker_codes_json=?,blocker_details_json=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?",
                         ("blocked" if blockers else "approved", json.dumps([x["code"] for x in blockers]), json.dumps(blockers, ensure_ascii=False), principal.user_id, order_id))
            components = [] if blockers else conn.execute("SELECT ol.id AS order_line_id,ol.quantity AS external_quantity,ol.total_cents,c.product_id,c.quantity,c.revenue_share_basis_points,p.name FROM channel_order_lines ol JOIN channel_listing_components c ON c.listing_id=ol.listing_id JOIN product_master p ON p.id=c.product_id WHERE ol.channel_order_id=? ORDER BY ol.line_number,c.id", (order_id,)).fetchall()
        if blockers: raise Conflict("审单未通过：" + "；".join(x["message"] for x in blockers))
        merged: dict[str, dict] = {}; allocated_lines = 0
        for row in components:
            quantity = int(row["external_quantity"]) * int(row["quantity"])
            allocation = int(row["total_cents"]) * int(row["revenue_share_basis_points"]) // 10000
            unit = allocation // quantity
            allocated_lines += unit * quantity
            item = merged.setdefault(row["product_id"], {"product_id": row["product_id"], "quantity": 0, "amount": 0, "description": row["name"]})
            item["quantity"] += quantity; item["amount"] += unit * quantity
        sales_lines = [{"product_id": x["product_id"], "quantity": x["quantity"], "unit_price_cents": x["amount"] // x["quantity"], "tax_rate_basis_points": 0, "warehouse_id": order["default_site_id"], "description": x["description"]} for x in merged.values()]
        represented = sum(x["quantity"] * x["unit_price_cents"] for x in sales_lines)
        allocation_rounding = order["total_cents"] - represented
        shipping = " ".join(x for x in [order["country"], order["province"], order["city"], order["district"], order["street"], order["recipient"], order["phone"]] if x)
        channel_ref = f"{order['platform']}:{order['shop_code']}"
        existing = self.store.row("SELECT id FROM sales_documents WHERE organization_id=? AND channel=? AND external_reference=?", (principal.organization_id, channel_ref, order["external_order_id"]))
        if existing:
            sales = self.sales.order(principal, existing["id"])
        else:
            sales = self.sales.create_order(principal, shop["settlement_customer_id"], sales_lines,
                order_date=order["order_time"][:10], currency=order["currency"], freight_cents=allocation_rounding,
                shipping_address=shipping, channel=channel_ref, external_reference=order["external_order_id"],
                notes=f"渠道订单 {order['external_order_id']}；买家备注：{order['buyer_note']}")
            sales = self.sales.confirm(principal, sales["id"])
        reserve_error = ""
        if reserve and sales["status"] == "confirmed":
            try: sales = self.sales.reserve(principal, sales["id"])
            except Exception as exc: reserve_error = str(exc)
        with self.store.connect() as conn:
            status = "exception" if reserve_error else "imported"
            blockers = [{"code": "INVENTORY_SHORTAGE", "message": reserve_error}] if reserve_error else []
            conn.execute("UPDATE channel_orders SET status=?,sales_document_id=?,blocker_codes_json=?,blocker_details_json=?,imported_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?",
                         (status, sales["id"], json.dumps([x["code"] for x in blockers]), json.dumps(blockers, ensure_ascii=False), order_id))
            self.audit.record(conn, AuditContext(principal), "channel.order.import", "channel_order", order_id,
                              after={"sales_document_id": sales["id"], "sales_status": sales["status"], "reserve_error": reserve_error})
        if reserve_error: raise Conflict("销售单已生成但库存预占失败：" + reserve_error)
        return self.order(principal, order_id)

    def change_shipping_address(self, principal: Principal, order_id: str, recipient: str, phone: str,
                                province: str, city: str, district: str, street: str) -> dict:
        principal.require("sales.write")
        values = [x.strip() for x in (recipient, phone, province, city, district, street)]
        if not all((values[0], values[1], values[2], values[3], values[5])): raise ValidationError("收件人、电话、省、市和详细地址不能为空")
        with self.store.connect() as conn:
            order = conn.execute("SELECT * FROM channel_orders WHERE id=? AND organization_id=?", (order_id, principal.organization_id)).fetchone()
            if not order: raise NotFound("渠道订单不存在")
            if order["status"] == "cancelled": raise InvalidTransition("已取消订单不能改址")
            if order["sales_document_id"]:
                shipped = conn.execute("SELECT 1 FROM shipments WHERE sales_document_id=? AND status='shipped' LIMIT 1", (order["sales_document_id"],)).fetchone()
                if shipped: raise InvalidTransition("订单已发货，不能修改地址")
            address = " ".join(x for x in [order["country"], values[2], values[3], values[4], values[5], values[0], values[1]] if x)
            conn.execute("UPDATE channel_orders SET recipient=?,phone=?,province=?,city=?,district=?,street=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (*values, order_id))
            if order["sales_document_id"]: conn.execute("UPDATE sales_documents SET shipping_address=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (address, order["sales_document_id"]))
            task_id = self._id("CHCB")
            conn.execute("INSERT OR IGNORE INTO channel_callback_tasks(id,organization_id,shop_id,channel_order_id,task_type,source_type,source_id,payload_json) VALUES(?,?,?,?,?,'channel_order',?,?)", (task_id, principal.organization_id, order["shop_id"], order_id, "address_change", order_id, json.dumps({"external_order_id": order["external_order_id"], "recipient": values[0], "phone": values[1], "address": address}, ensure_ascii=False)))
            self.audit.record(conn, AuditContext(principal), "channel.order.address_change", "channel_order", order_id, after={"address": address})
        return self.order(principal, order_id)

    def cancel_order(self, principal: Principal, order_id: str, reason: str) -> dict:
        principal.require("sales.cancel")
        if not reason.strip(): raise ValidationError("取消原因不能为空")
        order = self.order(principal, order_id)
        if order["status"] == "cancelled": return order
        if order["sales_document_id"]: self.sales.cancel(principal, order["sales_document_id"], reason)
        with self.store.connect() as conn:
            conn.execute("UPDATE channel_orders SET status='cancelled',external_status='cancelled',updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (order_id,))
            conn.execute("INSERT OR IGNORE INTO channel_callback_tasks(id,organization_id,shop_id,channel_order_id,task_type,source_type,source_id,payload_json) VALUES(?,?,?,?,?,'channel_order',?,?)", (self._id("CHCB"), principal.organization_id, order["shop_id"], order_id, "cancellation", order_id, json.dumps({"external_order_id": order["external_order_id"], "reason": reason.strip()}, ensure_ascii=False)))
            self.audit.record(conn, AuditContext(principal), "channel.order.cancel", "channel_order", order_id, after={"reason": reason.strip()})
        return self.order(principal, order_id)

    def list_callbacks(self, principal: Principal, status: str = "") -> list[dict]:
        principal.require("sales.read")
        params: list[object] = [principal.organization_id]; where = "t.organization_id=?"
        if status: where += " AND t.status=?"; params.append(status)
        rows = self.store.rows("SELECT t.*,s.code AS shop_code,o.external_order_id FROM channel_callback_tasks t JOIN channel_shops s ON s.id=t.shop_id JOIN channel_orders o ON o.id=t.channel_order_id WHERE " + where + " ORDER BY t.created_at DESC LIMIT 300", tuple(params))
        for row in rows: row["payload"] = json.loads(row.pop("payload_json"))
        return rows

    def complete_callback(self, principal: Principal, task_id: str, success: bool, error: str = "") -> dict:
        principal.require("sales.write")
        with self.store.connect() as conn:
            task = conn.execute("SELECT * FROM channel_callback_tasks WHERE id=? AND organization_id=?", (task_id, principal.organization_id)).fetchone()
            if not task: raise NotFound("渠道回传任务不存在")
            if task["status"] == "succeeded": return dict(task)
            attempts = int(task["attempts"]) + 1
            status = "succeeded" if success else ("dead_letter" if attempts >= 5 else "failed")
            conn.execute("UPDATE channel_callback_tasks SET status=?,attempts=?,last_error=?,processing_owner='',lease_expires_at=NULL,completed_at=CASE WHEN ?='succeeded' THEN CURRENT_TIMESTAMP ELSE completed_at END WHERE id=?", (status, attempts, "" if success else error.strip()[:1000], status, task_id))
        return self.store.row("SELECT * FROM channel_callback_tasks WHERE id=?", (task_id,)) or {}

    def overview(self, principal: Principal) -> dict:
        principal.require("sales.read")
        org = principal.organization_id
        return {
            "shops": int(self.store.scalar("SELECT COUNT(*) FROM channel_shops WHERE organization_id=? AND status='active'", (org,)) or 0),
            "unconfigured_shops": sum(1 for x in self.list_shops(principal) if x["connection_status"] == "unconfigured"),
            "waiting_review": int(self.store.scalar("SELECT COUNT(*) FROM channel_orders WHERE organization_id=? AND status='received'", (org,)) or 0),
            "blocked": int(self.store.scalar("SELECT COUNT(*) FROM channel_orders WHERE organization_id=? AND status IN ('blocked','exception')", (org,)) or 0),
            "pending_callbacks": int(self.store.scalar("SELECT COUNT(*) FROM channel_callback_tasks WHERE organization_id=? AND status IN ('pending','failed')", (org,)) or 0),
        }
