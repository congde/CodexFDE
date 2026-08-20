from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .models import AuthenticationError, Conflict, NotFound, PermissionDenied, ValidationError
from .schema_v2 import ROLE_PERMISSIONS
from .store import ERPStore


PBKDF2_ITERATIONS = 310_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def hash_password(password: str, salt_hex: str | None = None, iterations: int = PBKDF2_ITERATIONS) -> tuple[str, str, int]:
    if len(password) < 10:
        raise ValidationError("密码至少 10 位")
    if len(password.encode("utf-8")) > 256:
        raise ValidationError("密码过长")
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return digest.hex(), salt.hex(), iterations


def verify_password(password: str, expected: str, salt_hex: str, iterations: int) -> bool:
    actual, _, _ = hash_password(password, salt_hex, iterations)
    return hmac.compare_digest(actual, expected)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    user_id: str
    organization_id: str
    username: str
    display_name: str
    permissions: frozenset[str]
    session_id: str = ""

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise PermissionDenied(f"缺少权限：{permission}")


SYSTEM_PRINCIPAL = Principal(
    user_id="system",
    organization_id="ORG-DEFAULT",
    username="system",
    display_name="系统",
    permissions=frozenset(ROLE_PERMISSIONS["admin"]),
)


class IdentityService:
    def __init__(self, store: ERPStore, session_hours: int = 12) -> None:
        self.store = store
        self.session_hours = session_hours

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex.upper()}"

    def bootstrap(self, organization_name: str, username: str, password: str) -> dict:
        username = username.strip().lower()
        if not organization_name.strip() or not username:
            raise ValidationError("组织名称和管理员账号不能为空")
        with self.store.connect() as conn:
            existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if existing:
                raise Conflict("系统已初始化")
            org_id = "ORG-DEFAULT"
            conn.execute(
                "INSERT OR IGNORE INTO organizations(id,code,name,legal_name) VALUES(?,?,?,?)",
                (org_id, "DEFAULT", organization_name.strip(), organization_name.strip()),
            )
            self._seed_roles(conn, org_id)
            user = self._create_user_in_transaction(conn, org_id, username, "系统管理员", password)
            role_id = conn.execute(
                "SELECT id FROM roles WHERE organization_id=? AND code='admin'", (org_id,)
            ).fetchone()[0]
            conn.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (user["id"], role_id))
            self._audit(conn, org_id, user["id"], username, "system.bootstrap", "organization", org_id, None, {"name": organization_name})
        return self.user(user["id"])

    def ensure_local_defaults(self) -> None:
        """Create organization/master defaults but never invent credentials."""
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO organizations(id,code,name,legal_name) VALUES('ORG-DEFAULT','DEFAULT','FlowERP','FlowERP')"
            )
            self._seed_roles(conn, "ORG-DEFAULT")
            conn.execute(
                "INSERT OR IGNORE INTO units_of_measure(id,organization_id,code,name,category) VALUES('UOM-EA','ORG-DEFAULT','EA','个','unit')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO sites(id,organization_id,code,name,site_type) VALUES('SITE-MAIN','ORG-DEFAULT','MAIN','主仓库','warehouse')"
            )
            defaults = (
                ("LOC-MAIN-STOCK", "STOCK", "主库存", "internal"),
                ("LOC-MAIN-RECV", "RECEIVING", "收货区", "receiving"),
                ("LOC-MAIN-SHIP", "SHIPPING", "发货区", "shipping"),
                ("LOC-MAIN-QC", "QUARANTINE", "待检区", "quarantine"),
                ("LOC-MAIN-DMG", "DAMAGED", "残次区", "damaged"),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO storage_locations(id,site_id,code,name,location_type) VALUES(?,?,?,?,?)",
                ((item[0], "SITE-MAIN", item[1], item[2], item[3]) for item in defaults),
            )

    def _seed_roles(self, conn: sqlite3.Connection, organization_id: str) -> None:
        for code, permissions in ROLE_PERMISSIONS.items():
            role_id = f"ROLE-{organization_id}-{code.upper()}"
            conn.execute(
                "INSERT OR IGNORE INTO roles(id,organization_id,code,name,system) VALUES(?,?,?,?,1)",
                (role_id, organization_id, code, code.title()),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO role_permissions(role_id,permission_code) VALUES(?,?)",
                ((role_id, permission) for permission in permissions),
            )

    def _create_user_in_transaction(
        self, conn: sqlite3.Connection, organization_id: str, username: str,
        display_name: str, password: str, email: str = "",
    ) -> dict:
        digest, salt, iterations = hash_password(password)
        user_id = self._id("USR")
        try:
            conn.execute(
                "INSERT INTO users(id,organization_id,username,display_name,email,password_hash,password_salt,password_iterations) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (user_id, organization_id, username.lower(), display_name.strip(), email.strip().lower(), digest, salt, iterations),
            )
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"用户已存在：{username}") from exc
        return {"id": user_id, "username": username}

    def create_user(
        self, principal: Principal, username: str, display_name: str, password: str,
        role_codes: Iterable[str], email: str = "",
    ) -> dict:
        principal.require("users.manage")
        role_codes = tuple(dict.fromkeys(code.strip().lower() for code in role_codes if code.strip()))
        if not role_codes:
            raise ValidationError("用户至少需要一个角色")
        with self.store.connect() as conn:
            user = self._create_user_in_transaction(
                conn, principal.organization_id, username.strip().lower(), display_name, password, email
            )
            rows = conn.execute(
                f"SELECT id,code FROM roles WHERE organization_id=? AND code IN ({','.join('?' for _ in role_codes)})",
                (principal.organization_id, *role_codes),
            ).fetchall()
            found = {row["code"] for row in rows}
            if found != set(role_codes):
                raise ValidationError(f"未知角色：{', '.join(sorted(set(role_codes) - found))}")
            conn.executemany("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", ((user["id"], row["id"]) for row in rows))
            self._audit(conn, principal.organization_id, principal.user_id, principal.username, "user.create", "user", user["id"], None, {"username": username, "roles": role_codes})
        return self.user(user["id"])

    def user(self, user_id: str) -> dict:
        user = self.store.row(
            "SELECT id,organization_id,username,display_name,email,status,failed_attempts,locked_until,last_login_at,created_at,updated_at,version "
            "FROM users WHERE id=?", (user_id,)
        )
        if not user:
            raise NotFound(f"用户不存在：{user_id}")
        user["roles"] = [row["code"] for row in self.store.rows(
            "SELECT r.code FROM roles r JOIN user_roles ur ON ur.role_id=r.id WHERE ur.user_id=? ORDER BY r.code", (user_id,)
        )]
        return user

    def list_users(self, principal: Principal) -> list[dict]:
        principal.require("users.manage")
        users = self.store.rows(
            "SELECT id FROM users WHERE organization_id=? ORDER BY username", (principal.organization_id,)
        )
        return [self.user(item["id"]) for item in users]

    def managed_user(self, principal: Principal, user_id: str) -> dict:
        principal.require("users.manage")
        user = self.user(user_id)
        if user["organization_id"] != principal.organization_id:
            raise NotFound(f"用户不存在：{user_id}")
        return user

    def update_user(self, principal: Principal, user_id: str, version: int,
                    display_name: str | None = None, email: str | None = None,
                    role_codes: Iterable[str] | None = None, status: str | None = None) -> dict:
        """Maintain user profile, roles and account status with concurrency control."""
        principal.require("users.manage")
        before = self.user(user_id)
        if before["organization_id"] != principal.organization_id:
            raise NotFound(f"用户不存在：{user_id}")
        if status is not None and status not in {"active", "disabled"}:
            raise ValidationError("用户状态只能是 active 或 disabled")
        if user_id == principal.user_id and status == "disabled":
            raise ValidationError("不能停用当前登录账号")
        roles: tuple[str, ...] | None = None
        if role_codes is not None:
            roles = tuple(dict.fromkeys(str(code).strip().lower() for code in role_codes if str(code).strip()))
            if not roles:
                raise ValidationError("用户至少需要一个角色")
            if user_id == principal.user_id and "admin" not in roles:
                raise ValidationError("不能移除当前登录账号的管理员角色")
        changes: dict[str, object] = {}
        if display_name is not None:
            value = display_name.strip()
            if not value:
                raise ValidationError("显示名称不能为空")
            changes["display_name"] = value
        if email is not None:
            changes["email"] = email.strip().lower()
        if status is not None:
            changes["status"] = status
            changes["failed_attempts"] = 0
            changes["locked_until"] = None
        with self.store.connect() as conn:
            current = conn.execute(
                "SELECT version FROM users WHERE id=? AND organization_id=?",
                (user_id, principal.organization_id),
            ).fetchone()
            if not current or int(current["version"]) != int(version):
                raise Conflict("用户资料已被其他管理员修改，请刷新后重试")
            if roles is not None:
                rows = conn.execute(
                    f"SELECT id,code FROM roles WHERE organization_id=? AND code IN ({','.join('?' for _ in roles)})",
                    (principal.organization_id, *roles),
                ).fetchall()
                found = {row["code"] for row in rows}
                if found != set(roles):
                    raise ValidationError(f"未知角色：{', '.join(sorted(set(roles) - found))}")
                conn.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
                conn.executemany("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)",
                                 ((user_id, row["id"]) for row in rows))
            if changes or roles is not None:
                assignments = ",".join(f"{key}=?" for key in changes)
                prefix = f"{assignments}," if assignments else ""
                conn.execute(
                    f"UPDATE users SET {prefix}updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?",
                    (*changes.values(), user_id),
                )
                if user_id != principal.user_id and (roles is not None or status == "disabled"):
                    conn.execute("UPDATE sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL", (user_id,))
                after = dict(before); after.update(changes)
                if roles is not None: after["roles"] = list(roles)
                self._audit(conn, principal.organization_id, principal.user_id, principal.username,
                            "user.update", "user", user_id, before, after)
        return self.user(user_id)

    def login(self, organization_code: str, username: str, password: str, remote_addr: str = "", user_agent: str = "") -> tuple[str, Principal]:
        now = utc_now()
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT u.*,o.id AS org_id FROM users u JOIN organizations o ON o.id=u.organization_id "
                "WHERE o.code=? COLLATE NOCASE AND u.username=? COLLATE NOCASE",
                (organization_code.strip(), username.strip()),
            ).fetchone()
            if not row:
                self._dummy_verify(password)
                raise AuthenticationError("账号或密码错误")
            user = dict(row)
            if user["status"] == "disabled":
                raise AuthenticationError("账号已停用")
            if user["status"] == "locked" and user["locked_until"]:
                if datetime.fromisoformat(user["locked_until"]) > now:
                    raise AuthenticationError("账号已暂时锁定")
                conn.execute("UPDATE users SET status='active',failed_attempts=0,locked_until=NULL WHERE id=?", (user["id"],))
            if not verify_password(password, user["password_hash"], user["password_salt"], user["password_iterations"]):
                attempts = user["failed_attempts"] + 1
                locked = attempts >= 5
                conn.execute(
                    "UPDATE users SET failed_attempts=?,status=?,locked_until=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (attempts, "locked" if locked else "active", iso(now + timedelta(minutes=15)) if locked else None, user["id"]),
                )
                self._audit(conn, user["organization_id"], user["id"], username, "auth.failure", "user", user["id"], None, {"failed_attempts": attempts}, remote_addr)
                raise AuthenticationError("账号或密码错误")
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            session_id = self._id("SES")
            expires = now + timedelta(hours=self.session_hours)
            conn.execute(
                "INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,remote_addr,user_agent) VALUES(?,?,?,?,?,?,?)",
                (session_id, user["id"], token_digest(token), csrf, iso(expires), remote_addr[:128], user_agent[:512]),
            )
            conn.execute("UPDATE users SET failed_attempts=0,status='active',locked_until=NULL,last_login_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (iso(now), user["id"]))
            self._audit(conn, user["organization_id"], user["id"], username, "auth.login", "session", session_id, None, {"expires_at": iso(expires)}, remote_addr)
        return token, self.authenticate(token)

    @staticmethod
    def _dummy_verify(password: str) -> None:
        hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"0" * 16, PBKDF2_ITERATIONS)

    def authenticate(self, token: str) -> Principal:
        if not token:
            raise AuthenticationError("请先登录")
        row = self.store.row(
            "SELECT s.id AS session_id,s.expires_at,s.revoked_at,u.id AS user_id,u.organization_id,u.username,u.display_name,u.status "
            "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
            (token_digest(token),),
        )
        if not row or row["revoked_at"] or row["status"] != "active":
            raise AuthenticationError("会话无效")
        if datetime.fromisoformat(row["expires_at"]) <= utc_now():
            raise AuthenticationError("会话已过期")
        permissions = self.store.rows(
            "SELECT DISTINCT rp.permission_code FROM user_roles ur JOIN role_permissions rp ON rp.role_id=ur.role_id WHERE ur.user_id=?",
            (row["user_id"],),
        )
        self.store.execute("UPDATE sessions SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?", (row["session_id"],))
        return Principal(
            user_id=row["user_id"], organization_id=row["organization_id"], username=row["username"],
            display_name=row["display_name"], permissions=frozenset(item["permission_code"] for item in permissions),
            session_id=row["session_id"],
        )

    def csrf_token(self, principal: Principal) -> str:
        value = self.store.scalar("SELECT csrf_token FROM sessions WHERE id=?", (principal.session_id,))
        if not value:
            raise AuthenticationError("会话无效")
        return str(value)

    def validate_csrf(self, principal: Principal, token: str) -> None:
        if not hmac.compare_digest(self.csrf_token(principal), token or ""):
            raise PermissionDenied("CSRF 校验失败")

    def logout(self, principal: Principal) -> None:
        with self.store.connect() as conn:
            conn.execute("UPDATE sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=?", (principal.session_id,))
            self._audit(conn, principal.organization_id, principal.user_id, principal.username, "auth.logout", "session", principal.session_id, None, {})

    def change_password(self, principal: Principal, old_password: str, new_password: str) -> None:
        with self.store.connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE id=?", (principal.user_id,)).fetchone()
            if not user or not verify_password(old_password, user["password_hash"], user["password_salt"], user["password_iterations"]):
                raise AuthenticationError("原密码错误")
            digest, salt, iterations = hash_password(new_password)
            conn.execute(
                "UPDATE users SET password_hash=?,password_salt=?,password_iterations=?,password_changed_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?",
                (digest, salt, iterations, principal.user_id),
            )
            conn.execute("UPDATE sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND id<>?", (principal.user_id, principal.session_id))
            self._audit(conn, principal.organization_id, principal.user_id, principal.username, "user.password_change", "user", principal.user_id, None, {})

    @staticmethod
    def _audit(conn: sqlite3.Connection, organization_id: str, actor_id: str | None, actor_name: str, action: str,
               entity_type: str, entity_id: str, before: object, after: object, remote_addr: str = "") -> None:
        conn.execute(
            "INSERT INTO audit_log(organization_id,actor_id,actor_name,action,entity_type,entity_id,before_json,after_json,remote_addr) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (organization_id, actor_id, actor_name, action, entity_type, entity_id,
             json.dumps(before, ensure_ascii=False) if before is not None else None,
             json.dumps(after, ensure_ascii=False) if after is not None else None, remote_addr),
        )
