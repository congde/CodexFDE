from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .schema_v2 import apply_v2_schema
from .schema_extensions import apply_extensions


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS products (
  sku TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
  reorder_point INTEGER NOT NULL DEFAULT 0 CHECK(reorder_point >= 0)
);
CREATE TABLE IF NOT EXISTS customers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS suppliers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  contact TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stock (
  sku TEXT PRIMARY KEY REFERENCES products(sku),
  on_hand INTEGER NOT NULL DEFAULT 0 CHECK(on_hand >= 0),
  reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved >= 0 AND reserved <= on_hand)
);
CREATE TABLE IF NOT EXISTS inventory_events (
  event_key TEXT PRIMARY KEY,
  sku TEXT NOT NULL REFERENCES products(sku),
  quantity INTEGER NOT NULL,
  reserved_delta INTEGER NOT NULL DEFAULT 0,
  event_type TEXT NOT NULL,
  reference TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sales_orders (
  id TEXT PRIMARY KEY,
  customer TEXT NOT NULL,
  customer_id TEXT,
  channel TEXT NOT NULL DEFAULT 'online',
  remark TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  total_cents INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sales_order_lines (
  order_id TEXT NOT NULL REFERENCES sales_orders(id),
  sku TEXT NOT NULL REFERENCES products(sku),
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
  PRIMARY KEY(order_id, sku)
);
CREATE TABLE IF NOT EXISTS purchase_requests (
  id TEXT PRIMARY KEY,
  sku TEXT NOT NULL REFERENCES products(sku),
  supplier_id TEXT,
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  status TEXT NOT NULL,
  reason TEXT NOT NULL,
  approved_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class ERPStore:
    def __init__(self, path: str | Path = ".runtime/flowerp.db", busy_timeout_ms: int = 5000) -> None:
        self.path = str(path)
        self.busy_timeout_ms = busy_timeout_ms
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._keeper: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._keeper = sqlite3.connect(":memory:", check_same_thread=False)
            self._keeper.row_factory = sqlite3.Row
            self._keeper.executescript(SCHEMA)
            self._migrate(self._keeper)
            apply_v2_schema(self._keeper)
            apply_extensions(self._keeper)
        else:
            with self.connect() as conn:
                conn.executescript(SCHEMA)
                self._migrate(conn)
                apply_v2_schema(conn)
                apply_extensions(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Apply additive migrations so existing course databases remain usable."""
        migrations = {
            "inventory_events": {
                "reserved_delta": "INTEGER NOT NULL DEFAULT 0",
            },
            "sales_orders": {
                "customer_id": "TEXT",
                "channel": "TEXT NOT NULL DEFAULT 'online'",
                "remark": "TEXT NOT NULL DEFAULT ''",
            },
            "purchase_requests": {
                "supplier_id": "TEXT",
            },
        }
        for table, columns in migrations.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._keeper if self._keeper is not None else sqlite3.connect(self.path)
        assert conn is not None
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        if self._keeper is None:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if self._keeper is None:
                conn.close()

    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def row(self, sql: str, params: tuple = ()) -> dict | None:
        with self.connect() as conn:
            value = conn.execute(sql, params).fetchone()
            return dict(value) if value else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            return cursor.rowcount

    def scalar(self, sql: str, params: tuple = ()) -> object | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else None

    def integrity_check(self) -> dict:
        with self.connect() as conn:
            quick = conn.execute("PRAGMA quick_check").fetchone()[0]
            foreign_keys = [dict(row) for row in conn.execute("PRAGMA foreign_key_check")]
            return {"ok": quick == "ok" and not foreign_keys, "quick_check": quick, "foreign_key_errors": foreign_keys}

    def checkpoint(self) -> tuple[int, int, int]:
        with self.connect() as conn:
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            return tuple(row) if row else (0, 0, 0)
