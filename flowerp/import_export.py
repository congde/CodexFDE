from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable

from .audit import AuditContext, AuditService
from .identity import Principal
from .master_data import MasterDataService
from .models import Conflict, NotFound, ValidationError
from .store import ERPStore


MAX_IMPORT_ROWS = 10_000


@dataclass(frozen=True)
class ImportColumn:
    name: str
    required: bool = False
    aliases: tuple[str, ...] = ()


IMPORT_SCHEMAS = {
    "products": (
        ImportColumn("sku", True, ("SKU", "商品编码")),
        ImportColumn("name", True, ("商品名称", "名称")),
        ImportColumn("barcode", False, ("条码",)),
        ImportColumn("sales_price_cents", False, ("销售价分",)),
        ImportColumn("standard_cost_cents", False, ("成本价分",)),
        ImportColumn("tax_rate_basis_points", False, ("税率基点",)),
        ImportColumn("min_stock", False, ("最低库存",)),
        ImportColumn("max_stock", False, ("最高库存",)),
        ImportColumn("tracking", False, ("跟踪方式",)),
    ),
    "customers": (
        ImportColumn("code", True, ("客户编码",)),
        ImportColumn("name", True, ("客户名称",)),
        ImportColumn("contact_name", False, ("联系人",)),
        ImportColumn("phone", False, ("电话", "手机")),
        ImportColumn("email", False, ("邮箱",)),
        ImportColumn("shipping_address", False, ("收货地址",)),
        ImportColumn("payment_terms_days", False, ("账期天数",)),
        ImportColumn("credit_limit_cents", False, ("信用额度分",)),
    ),
    "suppliers": (
        ImportColumn("code", True, ("供应商编码",)),
        ImportColumn("name", True, ("供应商名称",)),
        ImportColumn("contact_name", False, ("联系人",)),
        ImportColumn("phone", False, ("电话", "手机")),
        ImportColumn("email", False, ("邮箱",)),
        ImportColumn("address", False, ("地址",)),
        ImportColumn("payment_terms_days", False, ("账期天数",)),
        ImportColumn("lead_time_days", False, ("交期天数",)),
    ),
}


class ImportExportService:
    """Two-phase CSV import: validate every row first, then explicitly commit."""

    def __init__(self, store: ERPStore, master: MasterDataService | None = None,
                 audit: AuditService | None = None) -> None:
        self.store = store
        self.audit = audit or AuditService(store)
        self.master = master or MasterDataService(store, self.audit)

    @staticmethod
    def _id() -> str:
        return f"IMP-{uuid.uuid4().hex.upper()}"

    def validate_csv(self, principal: Principal, import_type: str, content: bytes | str,
                     filename: str = "") -> dict:
        principal.require("master.write")
        if import_type not in IMPORT_SCHEMAS: raise ValidationError(f"不支持的导入类型：{import_type}")
        text = self._decode(content)
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames: raise ValidationError("CSV 缺少表头")
        mapping = self._column_mapping(reader.fieldnames, IMPORT_SCHEMAS[import_type])
        rows = list(reader)
        if not rows: raise ValidationError("CSV 没有数据行")
        if len(rows) > MAX_IMPORT_ROWS: raise ValidationError(f"单次最多导入 {MAX_IMPORT_ROWS} 行")
        job_id = self._id(); valid_count = 0; invalid_count = 0
        staged: list[tuple[int, dict, dict | None, list[str], str]] = []
        seen: set[str] = set()
        for row_number, source in enumerate(rows, 2):
            normalized = {target: (source.get(actual, "") or "").strip() for target,actual in mapping.items()}
            errors = self._validate_row(import_type, normalized)
            unique_value = normalized.get("sku") or normalized.get("code") or ""
            unique_key = unique_value.upper()
            if unique_key in seen: errors.append(f"文件内编码重复：{unique_value}")
            seen.add(unique_key)
            if errors: invalid_count += 1; normalized_value = None; status = "invalid"
            else: valid_count += 1; normalized_value = self._normalize_row(import_type, normalized); status = "valid"
            staged.append((row_number, source, normalized_value, errors, status))
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO import_jobs(id,organization_id,import_type,filename,status,total_rows,valid_rows,invalid_rows,created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, principal.organization_id, import_type, filename[:255], "ready" if invalid_count == 0 else "failed",
                 len(rows), valid_count, invalid_count, principal.user_id),
            )
            conn.executemany(
                "INSERT INTO import_job_rows(job_id,row_number,source_json,normalized_json,status,errors_json) VALUES(?,?,?,?,?,?)",
                ((job_id, number, json.dumps(source, ensure_ascii=False), json.dumps(normalized, ensure_ascii=False) if normalized else None,
                  status, json.dumps(errors, ensure_ascii=False)) for number,source,normalized,errors,status in staged),
            )
            self.audit.record(conn, AuditContext(principal), "import.validate", "import_job", job_id,
                              after={"import_type": import_type, "rows": len(rows), "valid": valid_count, "invalid": invalid_count})
        return self.job(principal, job_id, include_rows=True)

    @staticmethod
    def _decode(content: bytes | str) -> str:
        if isinstance(content, str): text = content
        else:
            if len(content) > 10_000_000: raise ValidationError("CSV 文件不能超过 10 MB")
            for encoding in ("utf-8-sig","utf-8","gb18030"):
                try: text=content.decode(encoding);break
                except UnicodeDecodeError: continue
            else: raise ValidationError("CSV 编码无法识别，请使用 UTF-8")
        if "\x00" in text: raise ValidationError("CSV 包含非法空字符")
        return text

    @staticmethod
    def _column_mapping(headers: list[str], schema: tuple[ImportColumn,...]) -> dict[str,str]:
        normalized_headers = {value.strip():value for value in headers if value}
        mapping: dict[str,str] = {}
        for column in schema:
            actual = next((normalized_headers[name] for name in (column.name,*column.aliases) if name in normalized_headers),None)
            if actual: mapping[column.name]=actual
            elif column.required: raise ValidationError(f"CSV 缺少必填列：{column.name}")
        return mapping

    @staticmethod
    def _validate_row(import_type: str, row: dict[str,str]) -> list[str]:
        errors: list[str] = []
        code = row.get("sku") or row.get("code") or ""
        if not code: errors.append("编码不能为空")
        elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}",code): errors.append("编码格式无效")
        if not row.get("name"): errors.append("名称不能为空")
        integer_fields = {
            "products":("sales_price_cents","standard_cost_cents","tax_rate_basis_points","min_stock","max_stock"),
            "customers":("payment_terms_days","credit_limit_cents"),
            "suppliers":("payment_terms_days","lead_time_days"),
        }[import_type]
        for field in integer_fields:
            value=row.get(field,"")
            if value:
                try:
                    if int(value)<0: errors.append(f"{field} 不能为负")
                except ValueError: errors.append(f"{field} 必须是整数")
        if row.get("email") and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",row["email"]): errors.append("邮箱格式无效")
        if import_type=="products" and row.get("tracking","none") not in {"","none","lot","serial"}: errors.append("tracking 必须是 none/lot/serial")
        return errors

    @staticmethod
    def _normalize_row(import_type: str, row: dict[str,str]) -> dict:
        result: dict[str,object] = dict(row)
        integer_fields = {
            "products":("sales_price_cents","standard_cost_cents","tax_rate_basis_points","min_stock","max_stock"),
            "customers":("payment_terms_days","credit_limit_cents"),
            "suppliers":("payment_terms_days","lead_time_days"),
        }[import_type]
        for field in integer_fields: result[field]=int(row.get(field) or 0)
        if import_type=="products":
            result["tracking"]=row.get("tracking") or "none"
            result["tax_rate_basis_points"]=int(row.get("tax_rate_basis_points") or 1300)
        return result

    def commit(self, principal: Principal, job_id: str) -> dict:
        principal.require("master.write")
        job = self.job(principal, job_id)
        if job["status"] != "ready": raise Conflict("只有全部校验通过的导入任务可以执行")
        rows = self.store.rows("SELECT * FROM import_job_rows WHERE job_id=? AND status='valid' ORDER BY row_number", (job_id,))
        imported = 0
        try:
            with self.store.connect() as conn:
                cursor=conn.execute("UPDATE import_jobs SET status='running' WHERE id=? AND status='ready'", (job_id,))
                if cursor.rowcount!=1:raise Conflict("导入任务状态已发生变化")
                for row in rows:
                    data=json.loads(row["normalized_json"])
                    entity_id=self._import_one_transaction(conn,principal.organization_id,job["import_type"],data)
                    conn.execute("UPDATE import_job_rows SET status='imported',entity_id=? WHERE id=?",(entity_id,row["id"]))
                    imported+=1
                conn.execute("UPDATE import_jobs SET status='completed',imported_rows=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                             (imported,job_id))
                self.audit.record(conn,AuditContext(principal),"import.commit","import_job",job_id,
                                  before={"status":"ready"},after={"status":"completed","imported_rows":imported})
        except Exception as exc:
            self.store.execute("UPDATE import_jobs SET status='failed',imported_rows=?,error_summary=? WHERE id=?",
                               (0, str(exc)[:2000], job_id))
            raise
        return self.job(principal, job_id)

    @staticmethod
    def _import_one_transaction(conn,organization_id: str,import_type: str,data: dict) -> str:
        entity_id=f"{'PRD' if import_type=='products' else 'CUS' if import_type=='customers' else 'SUP'}-{uuid.uuid4().hex.upper()}"
        if import_type=="products":
            conn.execute(
                "INSERT INTO product_master(id,organization_id,sku,name,barcode,uom_id,tracking,sales_price_cents,standard_cost_cents,tax_rate_basis_points,min_stock,max_stock) "
                "VALUES(?,?,?,?,?,'UOM-EA',?,?,?,?,?,?)",
                (entity_id,organization_id,data["sku"].upper(),data["name"],data.get("barcode",""),data["tracking"],
                 data["sales_price_cents"],data["standard_cost_cents"],data["tax_rate_basis_points"],data["min_stock"],data["max_stock"]),
            )
            return entity_id
        if import_type=="customers":
            conn.execute(
                "INSERT INTO customer_master(id,organization_id,code,name,contact_name,phone,email,shipping_address,payment_terms_days,credit_limit_cents) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (entity_id,organization_id,data["code"].upper(),data["name"],data.get("contact_name",""),data.get("phone",""),
                 data.get("email",""),data.get("shipping_address",""),data["payment_terms_days"],data["credit_limit_cents"]),
            )
            return entity_id
        conn.execute(
            "INSERT INTO supplier_master(id,organization_id,code,name,contact_name,phone,email,address,payment_terms_days,lead_time_days) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (entity_id,organization_id,data["code"].upper(),data["name"],data.get("contact_name",""),data.get("phone",""),
             data.get("email",""),data.get("address",""),data["payment_terms_days"],data["lead_time_days"]),
        )
        return entity_id

    def job(self, principal: Principal, job_id: str, include_rows: bool = False) -> dict:
        principal.require("master.read")
        job = self.store.row("SELECT * FROM import_jobs WHERE id=? AND organization_id=?", (job_id,principal.organization_id))
        if not job: raise NotFound(f"导入任务不存在：{job_id}")
        if include_rows:
            job["rows"]=self.store.rows(
                "SELECT row_number,status,errors_json,source_json,normalized_json,entity_id FROM import_job_rows WHERE job_id=? ORDER BY row_number",
                (job_id,),
            )
            for row in job["rows"]:
                row["errors"]=json.loads(row.pop("errors_json"))
                row["source"]=json.loads(row.pop("source_json"))
                row["normalized"]=json.loads(row.pop("normalized_json")) if row["normalized_json"] else None
        return job

    def export_csv(self, principal: Principal, export_type: str) -> str:
        principal.require("reports.read")
        queries = {
            "products":("SELECT sku,name,barcode,tracking,sales_price_cents,standard_cost_cents,tax_rate_basis_points,min_stock,max_stock,active FROM product_master WHERE organization_id=? ORDER BY sku",(principal.organization_id,)),
            "customers":("SELECT code,name,contact_name,phone,email,shipping_address,payment_terms_days,credit_limit_cents,status FROM customer_master WHERE organization_id=? ORDER BY code",(principal.organization_id,)),
            "suppliers":("SELECT code,name,contact_name,phone,email,address,payment_terms_days,lead_time_days,status FROM supplier_master WHERE organization_id=? ORDER BY code",(principal.organization_id,)),
            "inventory":("SELECT p.sku,p.name,s.code AS site,l.code AS location,b.lot_id,b.on_hand,b.reserved,b.on_hand-b.reserved AS available FROM stock_balance b JOIN product_master p ON p.id=b.product_id JOIN storage_locations l ON l.id=b.location_id JOIN sites s ON s.id=l.site_id WHERE b.organization_id=? ORDER BY p.sku,s.code,l.code",(principal.organization_id,)),
        }
        if export_type not in queries: raise ValidationError(f"不支持的导出类型：{export_type}")
        rows=self.store.rows(*queries[export_type])
        if not rows:return ""
        output=io.StringIO(newline="");writer=csv.DictWriter(output,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        return "\ufeff"+output.getvalue()
