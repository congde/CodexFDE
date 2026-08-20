from __future__ import annotations

import re
import sqlite3
import uuid

from .audit import AuditContext, AuditService
from .identity import Principal
from .models import NotFound, ValidationError
from .store import ERPStore


class PartnerDetailService:
    """Multiple contacts and structured addresses for business partners."""

    def __init__(self, store: ERPStore, audit: AuditService | None = None) -> None:
        self.store=store;self.audit=audit or AuditService(store)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    @staticmethod
    def _partner(conn: sqlite3.Connection, organization_id: str, partner_type: str, partner_id: str) -> sqlite3.Row:
        table={"customer":"customer_master","supplier":"supplier_master"}.get(partner_type)
        if not table:raise ValidationError("往来方类型无效")
        row=conn.execute(f"SELECT * FROM {table} WHERE id=? AND organization_id=?",(partner_id,organization_id)).fetchone()
        if not row:raise NotFound(f"往来单位不存在：{partner_id}")
        return row

    def add_contact(self, principal: Principal, partner_type: str, partner_id: str, name: str,
                    title: str="", department: str="", phone: str="", email: str="",
                    is_primary: bool=False) -> dict:
        principal.require("master.write")
        name=name.strip();email=email.strip().lower()
        if not name:raise ValidationError("联系人姓名不能为空")
        if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email):raise ValidationError("邮箱格式无效")
        contact_id=self._id("CON")
        with self.store.connect() as conn:
            self._partner(conn,principal.organization_id,partner_type,partner_id)
            if is_primary:
                conn.execute("UPDATE partner_contacts SET is_primary=0 WHERE organization_id=? AND partner_type=? AND partner_id=?",
                             (principal.organization_id,partner_type,partner_id))
            conn.execute(
                "INSERT INTO partner_contacts(id,organization_id,partner_type,partner_id,name,title,department,phone,email,is_primary) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (contact_id,principal.organization_id,partner_type,partner_id,name,title.strip(),department.strip(),phone.strip(),email,int(is_primary)),
            )
            self.audit.record(conn,AuditContext(principal),"partner_contact.create","partner_contact",contact_id,
                              after={"partner_type":partner_type,"partner_id":partner_id,"name":name,"is_primary":is_primary})
        return self.contact(principal,contact_id)

    def update_contact(self, principal: Principal, contact_id: str, **changes: object) -> dict:
        principal.require("master.write")
        before=self.contact(principal,contact_id)
        allowed={"name","title","department","phone","email","is_primary","active"}
        changes={key:value for key,value in changes.items() if key in allowed}
        if not changes:return before
        if "name" in changes and not str(changes["name"]).strip():raise ValidationError("联系人姓名不能为空")
        if "email" in changes:
            email=str(changes["email"]).strip().lower()
            if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email):raise ValidationError("邮箱格式无效")
            changes["email"]=email
        with self.store.connect() as conn:
            if bool(changes.get("is_primary")):
                conn.execute("UPDATE partner_contacts SET is_primary=0 WHERE organization_id=? AND partner_type=? AND partner_id=?",
                             (principal.organization_id,before["partner_type"],before["partner_id"]))
            sql=",".join(f"{key}=?" for key in changes)
            conn.execute(f"UPDATE partner_contacts SET {sql} WHERE id=? AND organization_id=?",
                         (*changes.values(),contact_id,principal.organization_id))
            self.audit.record(conn,AuditContext(principal),"partner_contact.update","partner_contact",contact_id,
                              before=before,after=changes)
        return self.contact(principal,contact_id)

    def contact(self, principal: Principal, contact_id: str) -> dict:
        principal.require("master.read")
        row=self.store.row("SELECT * FROM partner_contacts WHERE id=? AND organization_id=?",(contact_id,principal.organization_id))
        if not row:raise NotFound(f"联系人不存在：{contact_id}")
        return row

    def contacts(self, principal: Principal, partner_type: str, partner_id: str,
                 active_only: bool=True) -> list[dict]:
        principal.require("master.read")
        sql="SELECT * FROM partner_contacts WHERE organization_id=? AND partner_type=? AND partner_id=?"
        if active_only:sql+=" AND active=1"
        return self.store.rows(sql+" ORDER BY is_primary DESC,name",(principal.organization_id,partner_type,partner_id))

    def add_address(self, principal: Principal, partner_type: str, partner_id: str, address_type: str,
                    recipient: str="", phone: str="", country: str="CN", province: str="", city: str="",
                    district: str="", street: str="", postal_code: str="", is_default: bool=False) -> dict:
        principal.require("master.write")
        if address_type not in {"billing","shipping","office","warehouse"}:raise ValidationError("地址类型无效")
        if not street.strip():raise ValidationError("详细地址不能为空")
        if len(country.strip())!=2:raise ValidationError("国家代码必须是两位 ISO 代码")
        address_id=self._id("ADR")
        with self.store.connect() as conn:
            self._partner(conn,principal.organization_id,partner_type,partner_id)
            if is_default:
                conn.execute("UPDATE partner_addresses SET is_default=0 WHERE organization_id=? AND partner_type=? AND partner_id=? AND address_type=?",
                             (principal.organization_id,partner_type,partner_id,address_type))
            conn.execute(
                "INSERT INTO partner_addresses(id,organization_id,partner_type,partner_id,address_type,recipient,phone,country,province,city,district,street,postal_code,is_default) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (address_id,principal.organization_id,partner_type,partner_id,address_type,recipient.strip(),phone.strip(),
                 country.strip().upper(),province.strip(),city.strip(),district.strip(),street.strip(),postal_code.strip(),int(is_default)),
            )
            self.audit.record(conn,AuditContext(principal),"partner_address.create","partner_address",address_id,
                              after={"partner_type":partner_type,"partner_id":partner_id,"address_type":address_type,"is_default":is_default})
        return self.address(principal,address_id)

    def address(self, principal: Principal, address_id: str) -> dict:
        principal.require("master.read")
        row=self.store.row("SELECT * FROM partner_addresses WHERE id=? AND organization_id=?",(address_id,principal.organization_id))
        if not row:raise NotFound(f"地址不存在：{address_id}")
        row["formatted"]=self.format_address(row)
        return row

    def addresses(self, principal: Principal, partner_type: str, partner_id: str,
                  address_type: str="", active_only: bool=True) -> list[dict]:
        principal.require("master.read")
        filters=["organization_id=?","partner_type=?","partner_id=?"]
        params:list[object]=[principal.organization_id,partner_type,partner_id]
        if address_type:filters.append("address_type=?");params.append(address_type)
        if active_only:filters.append("active=1")
        rows=self.store.rows(f"SELECT * FROM partner_addresses WHERE {' AND '.join(filters)} ORDER BY is_default DESC,created_at",tuple(params))
        for row in rows:row["formatted"]=self.format_address(row)
        return rows

    @staticmethod
    def format_address(address: dict) -> str:
        domestic=address.get("country")=="CN"
        parts=[address.get("province",""),address.get("city",""),address.get("district",""),address.get("street","")]
        text="".join(filter(None,parts)) if domestic else ", ".join(reversed(list(filter(None,parts))))
        if address.get("postal_code"):text+=f" {address['postal_code']}"
        return text
