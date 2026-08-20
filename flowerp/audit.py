from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .identity import Principal
from .store import ERPStore


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class AuditContext:
    principal: Principal
    request_id: str = ""
    remote_addr: str = ""


class AuditService:
    def __init__(self, store: ERPStore) -> None:
        self.store = store

    def record(self, conn: sqlite3.Connection, context: AuditContext, action: str, entity_type: str,
               entity_id: str, before: object = None, after: object = None, metadata: object = None) -> None:
        conn.execute(
            "INSERT INTO audit_log(organization_id,actor_id,actor_name,action,entity_type,entity_id,request_id,before_json,after_json,metadata_json,remote_addr) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (context.principal.organization_id, context.principal.user_id, context.principal.username,
             action, entity_type, entity_id, context.request_id, _json(before), _json(after),
             _json(metadata or {}), context.remote_addr),
        )

    def search(self, principal: Principal, entity_type: str = "", entity_id: str = "", actor_id: str = "",
               action: str = "", since: str = "", until: str = "", limit: int = 100, offset: int = 0) -> list[dict]:
        principal.require("audit.read")
        filters = ["organization_id=?"]
        params: list[object] = [principal.organization_id]
        for column, value in (("entity_type", entity_type), ("entity_id", entity_id), ("actor_id", actor_id), ("action", action)):
            if value:
                filters.append(f"{column}=?")
                params.append(value)
        if since:
            filters.append("created_at>=?"); params.append(since)
        if until:
            filters.append("created_at<=?"); params.append(until)
        params.extend((max(1, min(limit, 1000)), max(0, offset)))
        rows = self.store.rows(
            f"SELECT * FROM audit_log WHERE {' AND '.join(filters)} ORDER BY id DESC LIMIT ? OFFSET ?", tuple(params)
        )
        for row in rows:
            for key in ("before_json", "after_json", "metadata_json"):
                value = row.pop(key)
                row[key[:-5]] = json.loads(value) if value else None
        return rows

    def entity_history(self, principal: Principal, entity_type: str, entity_id: str) -> list[dict]:
        return self.search(principal, entity_type=entity_type, entity_id=entity_id, limit=500)

    def export_csv(self, principal: Principal, **filters: str) -> str:
        rows = self.search(principal, limit=1000, **filters)
        output = io.StringIO(newline="")
        fields = ["id", "created_at", "actor_name", "action", "entity_type", "entity_id", "request_id", "remote_addr"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
        return output.getvalue()
