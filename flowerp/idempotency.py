from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .models import Conflict, ValidationError
from .store import ERPStore


class IdempotencyService:
    def __init__(self, store: ERPStore, ttl_hours: int = 24) -> None:
        self.store = store
        self.ttl_hours = ttl_hours

    @staticmethod
    def request_hash(method: str, path: str, body: object) -> str:
        normalized = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(f"{method.upper()}\n{path}\n{normalized}".encode("utf-8")).hexdigest()

    def begin(self, organization_id: str, scope: str, key: str, request_hash: str) -> dict | None:
        if not key or len(key) > 128:
            raise ValidationError("Idempotency-Key 长度必须为 1..128")
        now = datetime.now(timezone.utc); expires = now + timedelta(hours=self.ttl_hours)
        with self.store.connect() as conn:
            conn.execute("DELETE FROM idempotency_keys WHERE expires_at<=?", (now.isoformat(timespec="seconds"),))
            existing = conn.execute(
                "SELECT * FROM idempotency_keys WHERE organization_id=? AND scope=? AND key=?",
                (organization_id, scope, key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Conflict("相同幂等键不能用于不同请求")
                if existing["response_status"] is None:
                    raise Conflict("相同请求正在处理中")
                return {"status": existing["response_status"], "body": json.loads(existing["response_json"])}
            conn.execute(
                "INSERT INTO idempotency_keys(organization_id,scope,key,request_hash,expires_at) VALUES(?,?,?,?,?)",
                (organization_id, scope, key, request_hash, expires.isoformat(timespec="seconds")),
            )
        return None

    def complete(self, organization_id: str, scope: str, key: str, status: int, body: object) -> None:
        affected = self.store.execute(
            "UPDATE idempotency_keys SET response_status=?,response_json=? WHERE organization_id=? AND scope=? AND key=? AND response_status IS NULL",
            (status, json.dumps(body, ensure_ascii=False, separators=(",", ":")), organization_id, scope, key),
        )
        if affected != 1: raise Conflict("幂等请求状态已改变")

    def abandon(self, organization_id: str, scope: str, key: str) -> None:
        self.store.execute(
            "DELETE FROM idempotency_keys WHERE organization_id=? AND scope=? AND key=? AND response_status IS NULL",
            (organization_id, scope, key),
        )
