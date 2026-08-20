from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import ERPStore


@dataclass(frozen=True)
class BackupManifest:
    format_version: int
    application: str
    created_at: str
    database_name: str
    database_sha256: str
    compressed_sha256: str
    database_bytes: int
    compressed_bytes: int
    sqlite_version: str


class BackupService:
    """Online, consistent SQLite backup with checksums and restore verification."""

    def __init__(self, store: ERPStore, backup_dir: str | Path) -> None:
        if store.path == ":memory:":
            raise ValueError("内存数据库不能备份")
        self.store = store
        self.backup_dir = Path(backup_dir).resolve()
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create(self, label: str = "manual") -> dict:
        safe_label = "".join(ch for ch in label if ch.isalnum() or ch in "-_")[:32] or "manual"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"flowerp-{stamp}-{safe_label}.sqlite3"
        raw_path = self.backup_dir / base
        compressed_path = self.backup_dir / f"{base}.gz"
        manifest_path = self.backup_dir / f"{base}.manifest.json"
        # sqlite3.Connection.__exit__ commits or rolls back but does not guarantee
        # an immediate close.  Keep the close explicit so Windows can unlink the
        # temporary raw backup on Python 3.12+.
        with closing(sqlite3.connect(self.store.path)) as source, closing(sqlite3.connect(raw_path)) as target:
            source.execute(f"PRAGMA busy_timeout={self.store.busy_timeout_ms}")
            source.backup(target, pages=1000)
        check = self._check_database(raw_path)
        if not check["ok"]:
            raw_path.unlink(missing_ok=True)
            raise RuntimeError(f"备份完整性检查失败：{check}")
        raw_hash = self._sha256(raw_path)
        with raw_path.open("rb") as source, gzip.open(compressed_path, "wb", compresslevel=6) as target:
            shutil.copyfileobj(source, target)
        compressed_hash = self._sha256(compressed_path)
        manifest = BackupManifest(
            format_version=1, application="FlowERP", created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            database_name=base, database_sha256=raw_hash, compressed_sha256=compressed_hash,
            database_bytes=raw_path.stat().st_size, compressed_bytes=compressed_path.stat().st_size,
            sqlite_version=sqlite3.sqlite_version,
        )
        manifest_path.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raw_path.unlink()
        verification = self.verify(compressed_path)
        if not verification["ok"]:
            compressed_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            raise RuntimeError(f"备份压缩包校验失败：{verification}")
        self.store.execute(
            "INSERT INTO backup_catalog(id,backup_path,manifest_path,created_at,verified_at,database_sha256,"
            "compressed_sha256,compressed_bytes,verified_mtime_ns,status) VALUES(?,?,?,?,?,?,?,?,?, 'verified')",
            (uuid.uuid4().hex, str(compressed_path), str(manifest_path), manifest.created_at,
             datetime.now(timezone.utc).isoformat(timespec="seconds"), manifest.database_sha256,
             manifest.compressed_sha256, manifest.compressed_bytes, compressed_path.stat().st_mtime_ns),
        )
        return {"backup": str(compressed_path), "manifest": str(manifest_path), "verified": True, **asdict(manifest)}

    def verify(self, compressed_path: str | Path) -> dict:
        path = Path(compressed_path).resolve()
        manifest_path = path.with_name(path.name[:-3] + ".manifest.json" if path.name.endswith(".gz") else path.name + ".manifest.json")
        if not path.is_file() or not manifest_path.is_file():
            self._record_verification_failure(path, "备份文件或清单不存在")
            return {"ok": False, "error": "备份文件或清单不存在"}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            required = {"format_version", "application", "database_name", "database_sha256", "compressed_sha256"}
            if not required.issubset(manifest) or manifest["format_version"] != 1 or manifest["application"] != "FlowERP":
                raise ValueError("备份清单格式或应用标识无效")
            database_name = str(manifest["database_name"])
            if Path(database_name).name != database_name:
                raise ValueError("备份清单中的数据库文件名不安全")
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._record_verification_failure(path, str(exc))
            return {"ok": False, "error": f"备份清单无效：{exc}"}
        if self._sha256(path) != manifest["compressed_sha256"]:
            self._record_verification_failure(path, "压缩备份校验和不匹配")
            return {"ok": False, "error": "压缩备份校验和不匹配"}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                restored = Path(tmp) / database_name
                with gzip.open(path, "rb") as source, restored.open("wb") as target:
                    shutil.copyfileobj(source, target)
                if self._sha256(restored) != manifest["database_sha256"]:
                    self._record_verification_failure(path, "解压数据库校验和不匹配")
                    return {"ok": False, "error": "解压数据库校验和不匹配"}
                check = self._check_database(restored)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            self._record_verification_failure(path, str(exc))
            return {"ok": False, "error": f"备份解压失败：{exc}"}
        if check["ok"]:
            self.store.execute(
                "UPDATE backup_catalog SET status='verified',verified_at=?,compressed_bytes=?,verified_mtime_ns=?,"
                "last_error='' WHERE backup_path=?",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), path.stat().st_size,
                 path.stat().st_mtime_ns, str(path)),
            )
        else:
            self._record_verification_failure(path, str(check))
        return {"ok": check["ok"], "manifest": manifest, "integrity": check}

    def _record_verification_failure(self, path: Path, error: str) -> None:
        self.store.execute(
            "UPDATE backup_catalog SET status='failed',last_error=? WHERE backup_path=?",
            (error[:2000], str(path)),
        )

    def restore(self, compressed_path: str | Path, destination: str | Path, overwrite: bool = False) -> dict:
        verification = self.verify(compressed_path)
        if not verification["ok"]:
            raise RuntimeError(f"拒绝恢复无效备份：{verification}")
        target = Path(destination).resolve()
        if target.exists() and not overwrite:
            raise FileExistsError(f"目标数据库已存在：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".restore.tmp")
        try:
            with gzip.open(Path(compressed_path), "rb") as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            if not self._check_database(temporary)["ok"]:
                raise RuntimeError("恢复后的数据库完整性检查失败")
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return {"ok": True, "destination": str(target), "integrity": self._check_database(target)}

    def prune(self, keep_days: int = 30, keep_minimum: int = 7) -> dict:
        if keep_days < 1 or keep_minimum < 1:
            raise ValueError("保留策略必须为正数")
        backups = sorted(self.backup_dir.glob("flowerp-*.sqlite3.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        removed: list[str] = []
        for index, path in enumerate(backups):
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if index >= keep_minimum and modified < cutoff:
                manifest = path.with_name(path.name[:-3] + ".manifest.json" if path.name.endswith(".gz") else path.name + ".manifest.json")
                path.unlink(); manifest.unlink(missing_ok=True); removed.append(path.name)
                self.store.execute(
                    "UPDATE backup_catalog SET status='pruned' WHERE backup_path=?", (str(path.resolve()),),
                )
        return {"removed": removed, "remaining": len(backups) - len(removed)}

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _check_database(path: Path) -> dict:
        try:
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
                quick = conn.execute("PRAGMA quick_check").fetchone()[0]
                foreign = conn.execute("PRAGMA foreign_key_check").fetchall()
                migrations = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            return {"ok": quick == "ok" and not foreign, "quick_check": quick,
                    "foreign_key_errors": len(foreign), "schema_version": migrations}
        except sqlite3.DatabaseError as exc:
            return {"ok": False, "error": str(exc)}


class HealthService:
    def __init__(self, store: ERPStore, runtime_dir: str | Path, minimum_free_mb: int = 256,
                 backup_max_age_hours: int = 36, require_recent_backup: bool = False) -> None:
        self.store = store
        self.runtime_dir = Path(runtime_dir)
        self.minimum_free_mb = minimum_free_mb
        self.backup_max_age_hours = backup_max_age_hours
        self.require_recent_backup = require_recent_backup

    def live(self) -> dict:
        return {"status": "ok", "service": "flowerp", "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def ready(self) -> tuple[bool, dict]:
        checks: dict[str, object] = {}
        try:
            checks["database"] = {"ok": self.store.scalar("SELECT 1") == 1}
            checks["integrity"] = self.store.integrity_check()
            version = self.store.scalar("SELECT MAX(version) FROM schema_migrations")
            checks["schema"] = {"ok": int(version or 0) >= 15, "version": version}
            maintenance = self.store.row("SELECT * FROM runtime_state WHERE id=1") or {}
            checks["maintenance"] = {
                "ok": not bool(maintenance.get("maintenance_mode")),
                "enabled": bool(maintenance.get("maintenance_mode")),
                "reason": maintenance.get("maintenance_reason", ""),
            }
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.runtime_dir, delete=True):
                pass
            checks["runtime_writable"] = {"ok": True}
            free_bytes = shutil.disk_usage(self.runtime_dir).free
            checks["disk_space"] = {
                "ok": free_bytes >= self.minimum_free_mb * 1024 * 1024,
                "free_bytes": free_bytes, "minimum_free_mb": self.minimum_free_mb,
            }
            stale_outbox = int(self.store.scalar(
                "SELECT COUNT(*) FROM outbox_events WHERE (status='processing' AND lease_expires_at<CURRENT_TIMESTAMP) "
                "OR (status='failed' AND attempts>=10)"
            ) or 0)
            checks["outbox"] = {"ok": stale_outbox == 0, "stale_or_exhausted": stale_outbox}
            unbalanced = int(self.store.scalar(
                "SELECT COUNT(*) FROM (SELECT e.id FROM journal_entries e JOIN journal_lines l ON l.journal_entry_id=e.id "
                "WHERE e.status='posted' GROUP BY e.id HAVING SUM(l.debit_cents)<>SUM(l.credit_cents))"
            ) or 0)
            pending_valuation = int(self.store.scalar(
                "SELECT COUNT(*) FROM stock_moves WHERE valuation_status='pending'"
            ) or 0)
            checks["accounting_integrity"] = {
                "ok": unbalanced == 0 and pending_valuation == 0,
                "unbalanced_journals": unbalanced, "pending_valuation_moves": pending_valuation,
            }
            recovery_point = self.store.row(
                "SELECT * FROM backup_catalog WHERE status='verified' ORDER BY verified_at DESC LIMIT 1"
            )
            backup_path = Path(recovery_point["backup_path"]) if recovery_point else None
            backup_exists = bool(backup_path and backup_path.is_file()
                                 and Path(recovery_point["manifest_path"]).is_file())
            backup_unchanged = bool(backup_exists and backup_path and
                                    backup_path.stat().st_size == recovery_point["compressed_bytes"] and
                                    backup_path.stat().st_mtime_ns == recovery_point["verified_mtime_ns"])
            verified_at = (datetime.fromisoformat(recovery_point["verified_at"]).timestamp()
                           if recovery_point and recovery_point.get("verified_at") else None)
            age_hours = ((datetime.now(timezone.utc).timestamp() - verified_at) / 3600) if verified_at else None
            backup_ok = backup_unchanged and age_hours is not None and age_hours <= self.backup_max_age_hours
            checks["backup_freshness"] = {
                "ok": backup_ok if self.require_recent_backup else True,
                "warning": not backup_ok,
                "verified": bool(recovery_point),
                "files_present": backup_exists,
                "unchanged_since_verification": backup_unchanged,
                "age_hours": round(age_hours, 2) if age_hours is not None else None,
                "maximum_age_hours": self.backup_max_age_hours,
                "required": self.require_recent_backup,
            }
        except Exception as exc:
            checks["exception"] = {"ok": False, "message": str(exc)}
        ok = all(bool(item.get("ok")) for item in checks.values() if isinstance(item, dict))
        return ok, {"status": "ready" if ok else "not_ready", "checks": checks}

    def metrics(self) -> str:
        queries = {
            "flowerp_users_total": "SELECT COUNT(*) FROM users",
            "flowerp_products_total": "SELECT COUNT(*) FROM product_master WHERE active=1",
            "flowerp_sales_orders_total": "SELECT COUNT(*) FROM sales_documents WHERE document_type='order'",
            "flowerp_purchase_orders_total": "SELECT COUNT(*) FROM purchase_orders",
            "flowerp_inventory_units": "SELECT COALESCE(SUM(on_hand),0) FROM stock_balance",
            "flowerp_inventory_reserved_units": "SELECT COALESCE(SUM(reserved),0) FROM stock_balance",
            "flowerp_receivables_cents": "SELECT COALESCE(SUM(outstanding_cents),0) FROM invoices WHERE invoice_type='receivable' AND status IN ('issued','partially_paid')",
            "flowerp_payables_cents": "SELECT COALESCE(SUM(outstanding_cents),0) FROM invoices WHERE invoice_type='payable' AND status IN ('issued','partially_paid')",
            "flowerp_outbox_pending": "SELECT COUNT(*) FROM outbox_events WHERE status='pending'",
            "flowerp_outbox_failed": "SELECT COUNT(*) FROM outbox_events WHERE status='failed'",
            "flowerp_outbox_processing": "SELECT COUNT(*) FROM outbox_events WHERE status='processing'",
            "flowerp_maintenance_mode": "SELECT maintenance_mode FROM runtime_state WHERE id=1",
            "flowerp_journal_entries_total": "SELECT COUNT(*) FROM journal_entries WHERE status='posted'",
            "flowerp_inventory_valuation_cents": "SELECT COALESCE(SUM(remaining_value_cents),0) FROM inventory_valuation_layers",
            "flowerp_pending_valuation_moves": "SELECT COUNT(*) FROM stock_moves WHERE valuation_status='pending'",
        }
        lines = ["# FlowERP operational metrics"]
        for name, sql in queries.items():
            try: value = self.store.scalar(sql) or 0
            except sqlite3.DatabaseError: value = 0
            lines.append(f"# TYPE {name} gauge"); lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"


class OutboxService:
    """Claim and acknowledge transactional events for external integrations."""

    def __init__(self, store: ERPStore) -> None:
        self.store = store

    def claim(self, limit: int = 100, worker_id: str = "", lease_seconds: int = 60) -> list[dict]:
        limit = max(1, min(limit, 1000))
        lease_seconds = max(5, min(lease_seconds, 3600))
        owner = worker_id.strip()[:128] or f"worker-{uuid.uuid4().hex}"
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE outbox_events SET status='failed',processing_owner='',lease_expires_at=NULL," 
                "last_error=CASE WHEN last_error='' THEN 'processing lease expired' ELSE last_error END "
                "WHERE status='processing' AND lease_expires_at<CURRENT_TIMESTAMP"
            )
            rows = conn.execute(
                "SELECT * FROM outbox_events WHERE status IN ('pending','failed') AND available_at<=CURRENT_TIMESTAMP "
                "AND attempts<10 ORDER BY created_at LIMIT ?", (limit,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                conn.execute(
                    f"UPDATE outbox_events SET status='processing',attempts=attempts+1,processing_owner=?," 
                    f"lease_expires_at=datetime('now',?) WHERE id IN ({','.join('?' for _ in ids)}) "
                    f"AND status IN ('pending','failed')",
                    (owner, f"+{lease_seconds} seconds", *ids),
                )
            return [{**dict(row), "processing_owner": owner,
                     "payload": json.loads(row["payload_json"])} for row in rows]

    def acknowledge(self, event_id: str, worker_id: str = "") -> None:
        owner_clause = " AND processing_owner=?" if worker_id else ""
        params = (event_id, worker_id) if worker_id else (event_id,)
        affected = self.store.execute(
            "UPDATE outbox_events SET status='published',published_at=CURRENT_TIMESTAMP,last_error=''," 
            "processing_owner='',lease_expires_at=NULL WHERE id=? AND status='processing'" + owner_clause,
            params,
        )
        if affected != 1: raise ValueError("事件不存在或未被领取")

    def fail(self, event_id: str, error: str, retry_after_seconds: int = 60, worker_id: str = "") -> None:
        delay = max(1, min(retry_after_seconds, 86400))
        owner_clause = " AND processing_owner=?" if worker_id else ""
        params = (error[:2000], f"+{delay} seconds", event_id, worker_id) if worker_id else (error[:2000], f"+{delay} seconds", event_id)
        affected = self.store.execute(
            "UPDATE outbox_events SET status='failed',last_error=?,available_at=datetime('now',?)," 
            "processing_owner='',lease_expires_at=NULL WHERE id=? AND status='processing'" + owner_clause,
            params,
        )
        if affected != 1: raise ValueError("事件不存在或未被领取")


class RuntimeCoordinator:
    """Maintenance gate and fenced single-writer lease for SQLite deployments."""

    def __init__(self, store: ERPStore) -> None:
        self.store = store

    def status(self) -> dict:
        return self.store.row("SELECT * FROM runtime_state WHERE id=1") or {"maintenance_mode": 0}

    def set_maintenance(self, enabled: bool, reason: str, actor: str) -> dict:
        if enabled and not reason.strip():
            raise ValueError("启用维护模式必须填写原因")
        self.store.execute(
            "UPDATE runtime_state SET maintenance_mode=?,maintenance_reason=?,changed_by=?,changed_at=CURRENT_TIMESTAMP WHERE id=1",
            (1 if enabled else 0, reason.strip() if enabled else "", actor),
        )
        return self.status()

    def acquire(self, lease_name: str, owner_id: str, ttl_seconds: int = 30) -> dict:
        ttl_seconds = max(10, min(ttl_seconds, 300))
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT *,expires_at>CURRENT_TIMESTAMP AS active FROM instance_leases WHERE lease_name=?",
                (lease_name,),
            ).fetchone()
            if existing and existing["owner_id"] != owner_id and existing["active"]:
                raise RuntimeError(f"运行租约已被实例 {existing['owner_id']} 持有")
            token = int(existing["fencing_token"]) + 1 if existing and existing["owner_id"] != owner_id else int(existing["fencing_token"]) if existing else 1
            conn.execute(
                "INSERT INTO instance_leases(lease_name,owner_id,fencing_token,acquired_at,heartbeat_at,expires_at) "
                "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,datetime('now',?)) "
                "ON CONFLICT(lease_name) DO UPDATE SET owner_id=excluded.owner_id,fencing_token=excluded.fencing_token," 
                "acquired_at=excluded.acquired_at,heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at",
                (lease_name, owner_id, token, f"+{ttl_seconds} seconds"),
            )
        return {"lease_name": lease_name, "owner_id": owner_id, "fencing_token": token}

    def renew(self, lease_name: str, owner_id: str, ttl_seconds: int = 30) -> bool:
        affected = self.store.execute(
            "UPDATE instance_leases SET heartbeat_at=CURRENT_TIMESTAMP,expires_at=datetime('now',?) "
            "WHERE lease_name=? AND owner_id=? AND expires_at>CURRENT_TIMESTAMP",
            (f"+{max(10, min(ttl_seconds, 300))} seconds", lease_name, owner_id),
        )
        return affected == 1

    def release(self, lease_name: str, owner_id: str) -> bool:
        return self.store.execute(
            "UPDATE instance_leases SET heartbeat_at=CURRENT_TIMESTAMP,expires_at=datetime('now','-1 second') "
            "WHERE lease_name=? AND owner_id=?", (lease_name, owner_id),
        ) == 1
