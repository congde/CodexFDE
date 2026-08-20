from __future__ import annotations

import sqlite3


SCHEMA_EXTENSIONS = """
CREATE TABLE IF NOT EXISTS price_lists (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  code TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  customer_id TEXT REFERENCES customer_master(id),
  channel TEXT NOT NULL DEFAULT '',
  valid_from TEXT,
  valid_until TEXT,
  priority INTEGER NOT NULL DEFAULT 100,
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, code)
);

CREATE TABLE IF NOT EXISTS price_rules (
  id TEXT PRIMARY KEY,
  price_list_id TEXT NOT NULL REFERENCES price_lists(id) ON DELETE CASCADE,
  product_id TEXT NOT NULL REFERENCES product_master(id),
  min_quantity INTEGER NOT NULL DEFAULT 1 CHECK(min_quantity > 0),
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
  discount_basis_points INTEGER NOT NULL DEFAULT 0 CHECK(discount_basis_points BETWEEN 0 AND 10000),
  valid_from TEXT,
  valid_until TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(price_list_id, product_id, min_quantity)
);

CREATE TABLE IF NOT EXISTS partner_contacts (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  partner_type TEXT NOT NULL CHECK(partner_type IN ('customer','supplier')),
  partner_id TEXT NOT NULL,
  name TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  department TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_partner_contacts ON partner_contacts(partner_type, partner_id, active);

CREATE TABLE IF NOT EXISTS partner_addresses (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  partner_type TEXT NOT NULL CHECK(partner_type IN ('customer','supplier')),
  partner_id TEXT NOT NULL,
  address_type TEXT NOT NULL CHECK(address_type IN ('billing','shipping','office','warehouse')),
  recipient TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT 'CN',
  province TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '',
  district TEXT NOT NULL DEFAULT '',
  street TEXT NOT NULL DEFAULT '',
  postal_code TEXT NOT NULL DEFAULT '',
  is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_partner_addresses ON partner_addresses(partner_type, partner_id, address_type, active);

CREATE TABLE IF NOT EXISTS import_jobs (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  import_type TEXT NOT NULL CHECK(import_type IN ('products','customers','suppliers','opening_stock')),
  filename TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'validating' CHECK(status IN ('validating','ready','running','completed','failed','cancelled')),
  total_rows INTEGER NOT NULL DEFAULT 0,
  valid_rows INTEGER NOT NULL DEFAULT 0,
  invalid_rows INTEGER NOT NULL DEFAULT 0,
  imported_rows INTEGER NOT NULL DEFAULT 0,
  error_summary TEXT NOT NULL DEFAULT '',
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS import_job_rows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
  row_number INTEGER NOT NULL,
  source_json TEXT NOT NULL,
  normalized_json TEXT,
  status TEXT NOT NULL CHECK(status IN ('valid','invalid','imported','skipped')),
  errors_json TEXT NOT NULL DEFAULT '[]',
  entity_id TEXT,
  UNIQUE(job_id, row_number)
);

CREATE TABLE IF NOT EXISTS reconciliations (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  reconciliation_type TEXT NOT NULL CHECK(reconciliation_type IN ('inventory','sales','purchase','finance','accounting')),
  as_of TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('running','passed','failed')),
  checked_items INTEGER NOT NULL DEFAULT 0,
  discrepancy_count INTEGER NOT NULL DEFAULT 0,
  result_json TEXT NOT NULL DEFAULT '{}',
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  alert_type TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  entity_type TEXT NOT NULL DEFAULT '',
  entity_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','resolved','dismissed')),
  assigned_to TEXT,
  acknowledged_by TEXT,
  acknowledged_at TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, alert_type, entity_type, entity_id, status)
);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(organization_id, status, severity, created_at);

CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
  sha256 TEXT NOT NULL,
  storage_key TEXT NOT NULL,
  uploaded_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, storage_key)
);
CREATE INDEX IF NOT EXISTS idx_attachments_entity ON attachments(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS invoice_lines (
  id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  source_line_id TEXT NOT NULL DEFAULT '',
  product_id TEXT REFERENCES product_master(id),
  description TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
  net_cents INTEGER NOT NULL CHECK(net_cents >= 0),
  tax_cents INTEGER NOT NULL CHECK(tax_cents >= 0),
  total_cents INTEGER NOT NULL CHECK(total_cents >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(invoice_id, source_line_id)
);
CREATE INDEX IF NOT EXISTS idx_invoice_lines_source ON invoice_lines(source_line_id, invoice_id);

CREATE TABLE IF NOT EXISTS shipment_serials (
  shipment_line_id TEXT NOT NULL REFERENCES shipment_lines(id) ON DELETE CASCADE,
  serial_number_id TEXT NOT NULL REFERENCES serial_numbers(id),
  status TEXT NOT NULL DEFAULT 'claimed' CHECK(status IN ('claimed','shipped','released')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(shipment_line_id, serial_number_id)
);

CREATE TABLE IF NOT EXISTS sales_return_serials (
  return_line_id TEXT NOT NULL REFERENCES sales_return_lines(id) ON DELETE CASCADE,
  serial_number_id TEXT NOT NULL REFERENCES serial_numbers(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(return_line_id, serial_number_id)
);
CREATE INDEX IF NOT EXISTS idx_return_serial_number ON sales_return_serials(serial_number_id);

CREATE TABLE IF NOT EXISTS runtime_state (
  id INTEGER PRIMARY KEY CHECK(id=1),
  maintenance_mode INTEGER NOT NULL DEFAULT 0 CHECK(maintenance_mode IN (0,1)),
  maintenance_reason TEXT NOT NULL DEFAULT '',
  changed_by TEXT NOT NULL DEFAULT '',
  changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO runtime_state(id) VALUES(1);

CREATE TABLE IF NOT EXISTS instance_leases (
  lease_name TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  fencing_token INTEGER NOT NULL DEFAULT 1 CHECK(fencing_token > 0),
  acquired_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backup_catalog (
  id TEXT PRIMARY KEY,
  backup_path TEXT NOT NULL UNIQUE,
  manifest_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  verified_at TEXT NOT NULL,
  database_sha256 TEXT NOT NULL,
  compressed_sha256 TEXT NOT NULL,
  compressed_bytes INTEGER NOT NULL CHECK(compressed_bytes >= 0),
  verified_mtime_ns INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'verified' CHECK(status IN ('verified','failed','pruned')),
  last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_backup_catalog_status ON backup_catalog(status, verified_at DESC);

CREATE TABLE IF NOT EXISTS ledger_accounts (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  account_type TEXT NOT NULL CHECK(account_type IN ('asset','liability','equity','income','expense')),
  control_type TEXT NOT NULL DEFAULT '' CHECK(control_type IN ('','cash','bank','receivable','inventory','payable','grni','tax','revenue','cogs','variance','advance')),
  normal_side TEXT NOT NULL CHECK(normal_side IN ('debit','credit')),
  allow_manual INTEGER NOT NULL DEFAULT 0 CHECK(allow_manual IN (0,1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id,code)
);

CREATE TABLE IF NOT EXISTS journal_entries (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  entry_number TEXT NOT NULL,
  journal_type TEXT NOT NULL CHECK(journal_type IN ('inventory','sales','purchase','cash','general','reversal')),
  posting_date TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_event TEXT NOT NULL,
  description TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted')),
  reversal_of_id TEXT REFERENCES journal_entries(id),
  posted_by TEXT,
  posted_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id,entry_number),
  UNIQUE(organization_id,source_event)
);
CREATE INDEX IF NOT EXISTS idx_journal_posting_date ON journal_entries(organization_id,posting_date,status);
CREATE INDEX IF NOT EXISTS idx_journal_source ON journal_entries(source_type,source_id);

CREATE TABLE IF NOT EXISTS journal_lines (
  id TEXT PRIMARY KEY,
  journal_entry_id TEXT NOT NULL REFERENCES journal_entries(id) ON DELETE RESTRICT,
  line_number INTEGER NOT NULL,
  account_id TEXT NOT NULL REFERENCES ledger_accounts(id),
  description TEXT NOT NULL DEFAULT '',
  debit_cents INTEGER NOT NULL DEFAULT 0 CHECK(debit_cents >= 0),
  credit_cents INTEGER NOT NULL DEFAULT 0 CHECK(credit_cents >= 0),
  partner_type TEXT NOT NULL DEFAULT '' CHECK(partner_type IN ('','customer','supplier')),
  partner_id TEXT NOT NULL DEFAULT '',
  product_id TEXT REFERENCES product_master(id),
  location_id TEXT REFERENCES storage_locations(id),
  lot_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK((debit_cents > 0 AND credit_cents = 0) OR (credit_cents > 0 AND debit_cents = 0)),
  UNIQUE(journal_entry_id,line_number)
);
CREATE INDEX IF NOT EXISTS idx_journal_lines_account ON journal_lines(account_id,journal_entry_id);

CREATE TABLE IF NOT EXISTS bank_accounts (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  bank_name TEXT NOT NULL,
  account_number_masked TEXT NOT NULL DEFAULT '',
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  ledger_account_id TEXT NOT NULL REFERENCES ledger_accounts(id),
  opening_balance_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id,code),
  UNIQUE(organization_id,ledger_account_id)
);

CREATE TABLE IF NOT EXISTS bank_statements (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  bank_account_id TEXT NOT NULL REFERENCES bank_accounts(id),
  statement_number TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  opening_balance_cents INTEGER NOT NULL,
  closing_balance_cents INTEGER NOT NULL,
  currency TEXT NOT NULL CHECK(length(currency)=3),
  import_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'imported' CHECK(status IN ('imported','reconciled')),
  reconciled_by TEXT,
  reconciled_at TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(bank_account_id,statement_number)
);
CREATE INDEX IF NOT EXISTS idx_bank_statement_period ON bank_statements(organization_id,bank_account_id,period_end,status);

CREATE TABLE IF NOT EXISTS bank_statement_lines (
  id TEXT PRIMARY KEY,
  statement_id TEXT NOT NULL REFERENCES bank_statements(id) ON DELETE RESTRICT,
  bank_account_id TEXT NOT NULL REFERENCES bank_accounts(id),
  external_transaction_id TEXT NOT NULL,
  transaction_date TEXT NOT NULL,
  value_date TEXT NOT NULL,
  signed_amount_cents INTEGER NOT NULL CHECK(signed_amount_cents<>0),
  counterparty_name TEXT NOT NULL DEFAULT '',
  reference TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'unmatched' CHECK(status IN ('unmatched','matched')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(bank_account_id,external_transaction_id)
);
CREATE INDEX IF NOT EXISTS idx_bank_line_status ON bank_statement_lines(bank_account_id,status,transaction_date);

CREATE TABLE IF NOT EXISTS channel_shops (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  platform TEXT NOT NULL CHECK(platform IN ('taobao','jd','pinduoduo','douyin','wechat','kuaishou','amazon','shopee','custom','mock')),
  code TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  external_shop_id TEXT NOT NULL DEFAULT '',
  settlement_customer_id TEXT NOT NULL REFERENCES customer_master(id),
  default_site_id TEXT NOT NULL REFERENCES sites(id),
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  sync_mode TEXT NOT NULL DEFAULT 'pull_webhook' CHECK(sync_mode IN ('pull','webhook','pull_webhook','manual')),
  credential_env TEXT NOT NULL DEFAULT '',
  webhook_secret_env TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','disabled')),
  last_synced_at TEXT,
  last_error TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id,code),
  UNIQUE(organization_id,platform,external_shop_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_shops_status ON channel_shops(organization_id,status,platform);

CREATE TABLE IF NOT EXISTS channel_listings (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  shop_id TEXT NOT NULL REFERENCES channel_shops(id),
  external_product_id TEXT NOT NULL,
  external_sku_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','disabled')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(shop_id,external_product_id,external_sku_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_listing_sku ON channel_listings(shop_id,external_sku_id,status);

CREATE TABLE IF NOT EXISTS channel_listing_components (
  id TEXT PRIMARY KEY,
  listing_id TEXT NOT NULL REFERENCES channel_listings(id) ON DELETE CASCADE,
  product_id TEXT NOT NULL REFERENCES product_master(id),
  quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity>0),
  revenue_share_basis_points INTEGER NOT NULL CHECK(revenue_share_basis_points BETWEEN 1 AND 10000),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(listing_id,product_id)
);

CREATE TABLE IF NOT EXISTS channel_orders (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  shop_id TEXT NOT NULL REFERENCES channel_shops(id),
  external_order_id TEXT NOT NULL,
  external_status TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'received' CHECK(status IN ('received','blocked','approved','imported','cancelled','exception')),
  order_time TEXT NOT NULL,
  paid_time TEXT,
  currency TEXT NOT NULL CHECK(length(currency)=3),
  goods_cents INTEGER NOT NULL CHECK(goods_cents>=0),
  discount_cents INTEGER NOT NULL DEFAULT 0 CHECK(discount_cents>=0),
  freight_cents INTEGER NOT NULL DEFAULT 0 CHECK(freight_cents>=0),
  total_cents INTEGER NOT NULL CHECK(total_cents>=0),
  buyer_reference TEXT NOT NULL DEFAULT '',
  recipient TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT 'CN',
  province TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '',
  district TEXT NOT NULL DEFAULT '',
  street TEXT NOT NULL DEFAULT '',
  postal_code TEXT NOT NULL DEFAULT '',
  buyer_note TEXT NOT NULL DEFAULT '',
  payload_hash TEXT NOT NULL,
  blocker_codes_json TEXT NOT NULL DEFAULT '[]',
  blocker_details_json TEXT NOT NULL DEFAULT '[]',
  sales_document_id TEXT REFERENCES sales_documents(id),
  reviewed_by TEXT,
  reviewed_at TEXT,
  imported_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(shop_id,external_order_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_orders_queue ON channel_orders(organization_id,status,order_time);

CREATE TABLE IF NOT EXISTS channel_order_lines (
  id TEXT PRIMARY KEY,
  channel_order_id TEXT NOT NULL REFERENCES channel_orders(id) ON DELETE CASCADE,
  line_number INTEGER NOT NULL,
  external_line_id TEXT NOT NULL,
  external_product_id TEXT NOT NULL,
  external_sku_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL CHECK(quantity>0),
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents>=0),
  discount_cents INTEGER NOT NULL DEFAULT 0 CHECK(discount_cents>=0),
  total_cents INTEGER NOT NULL CHECK(total_cents>=0),
  listing_id TEXT REFERENCES channel_listings(id),
  UNIQUE(channel_order_id,line_number),
  UNIQUE(channel_order_id,external_line_id)
);

CREATE TABLE IF NOT EXISTS channel_sync_runs (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  shop_id TEXT NOT NULL REFERENCES channel_shops(id),
  trigger_type TEXT NOT NULL CHECK(trigger_type IN ('pull','webhook','manual','mock')),
  cursor_value TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK(status IN ('running','completed','partial','failed')),
  received_count INTEGER NOT NULL DEFAULT 0,
  imported_count INTEGER NOT NULL DEFAULT 0,
  blocked_count INTEGER NOT NULL DEFAULT 0,
  replay_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  error_summary TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_channel_sync_runs ON channel_sync_runs(shop_id,started_at DESC);

CREATE TABLE IF NOT EXISTS channel_callback_tasks (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  shop_id TEXT NOT NULL REFERENCES channel_shops(id),
  channel_order_id TEXT NOT NULL REFERENCES channel_orders(id),
  task_type TEXT NOT NULL CHECK(task_type IN ('shipment','cancellation','address_change')),
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','succeeded','failed','dead_letter')),
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processing_owner TEXT NOT NULL DEFAULT '',
  lease_expires_at TEXT,
  last_error TEXT NOT NULL DEFAULT '',
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(shop_id,task_type,source_type,source_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_callbacks_queue ON channel_callback_tasks(organization_id,status,available_at);

CREATE TABLE IF NOT EXISTS bank_payment_matches (
  id TEXT PRIMARY KEY,
  statement_line_id TEXT NOT NULL REFERENCES bank_statement_lines(id) ON DELETE RESTRICT,
  payment_id TEXT NOT NULL REFERENCES payments(id) ON DELETE RESTRICT,
  amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
  matched_by TEXT NOT NULL,
  matched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(statement_line_id),
  UNIQUE(payment_id)
);

CREATE TABLE IF NOT EXISTS inventory_valuation_layers (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  product_id TEXT NOT NULL REFERENCES product_master(id),
  location_id TEXT NOT NULL REFERENCES storage_locations(id),
  lot_id TEXT NOT NULL DEFAULT '',
  stock_move_id TEXT NOT NULL REFERENCES stock_moves(id),
  layer_type TEXT NOT NULL CHECK(layer_type IN ('receipt','transfer_in','return','adjustment')),
  original_quantity INTEGER NOT NULL CHECK(original_quantity > 0),
  remaining_quantity INTEGER NOT NULL CHECK(remaining_quantity >= 0),
  unit_cost_cents INTEGER NOT NULL CHECK(unit_cost_cents >= 0),
  original_value_cents INTEGER NOT NULL CHECK(original_value_cents >= 0),
  remaining_value_cents INTEGER NOT NULL CHECK(remaining_value_cents >= 0),
  occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(stock_move_id)
);
CREATE INDEX IF NOT EXISTS idx_valuation_fifo ON inventory_valuation_layers(organization_id,product_id,location_id,lot_id,remaining_quantity,occurred_at,id);

CREATE TABLE IF NOT EXISTS inventory_valuation_consumptions (
  id TEXT PRIMARY KEY,
  stock_move_id TEXT NOT NULL REFERENCES stock_moves(id),
  valuation_layer_id TEXT NOT NULL REFERENCES inventory_valuation_layers(id),
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  value_cents INTEGER NOT NULL CHECK(value_cents >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(stock_move_id,valuation_layer_id)
);

CREATE TRIGGER IF NOT EXISTS trg_posted_journal_entry_immutable
BEFORE UPDATE ON journal_entries
WHEN OLD.status='posted'
BEGIN
  SELECT RAISE(ABORT,'posted journal entry is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_posted_journal_entry_no_delete
BEFORE DELETE ON journal_entries
WHEN OLD.status='posted'
BEGIN
  SELECT RAISE(ABORT,'posted journal entry cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_posted_journal_line_immutable_update
BEFORE UPDATE ON journal_lines
WHEN (SELECT status FROM journal_entries WHERE id=OLD.journal_entry_id)='posted'
BEGIN
  SELECT RAISE(ABORT,'posted journal line is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_posted_journal_line_immutable_delete
BEFORE DELETE ON journal_lines
WHEN (SELECT status FROM journal_entries WHERE id=OLD.journal_entry_id)='posted'
BEGIN
  SELECT RAISE(ABORT,'posted journal line cannot be deleted');
END;
"""


def apply_extensions(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_EXTENSIONS)
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(3,'pricing imports reconciliation')")
    shipment_columns = {row[1] for row in conn.execute("PRAGMA table_info(shipment_lines)")}
    if "reservation_id" not in shipment_columns:
        conn.execute("ALTER TABLE shipment_lines ADD COLUMN reservation_id TEXT REFERENCES stock_reservations(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shipment_reservation ON shipment_lines(reservation_id)")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(4,'partial shipment reservation binding')")
    reservation_columns = {row[1] for row in conn.execute("PRAGMA table_info(stock_reservations)")}
    if "claimed_by_shipment_id" not in reservation_columns:
        conn.execute("ALTER TABLE stock_reservations ADD COLUMN claimed_by_shipment_id TEXT REFERENCES shipments(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reservation_shipment_claim ON stock_reservations(claimed_by_shipment_id)")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(5,'atomic shipment reservation claims')")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(6,'invoice line subledger')")
    return_columns = {row[1] for row in conn.execute("PRAGMA table_info(sales_return_lines)")}
    if "lot_id" not in return_columns:
        conn.execute("ALTER TABLE sales_return_lines ADD COLUMN lot_id TEXT NOT NULL DEFAULT ''")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(7,'serialized fulfillment and return lots')")
    shipment_serial_columns = {row[1] for row in conn.execute("PRAGMA table_info(shipment_serials)")}
    if "status" not in shipment_serial_columns:
        conn.execute("ALTER TABLE shipment_serials RENAME TO shipment_serials_legacy")
        conn.execute(
            "CREATE TABLE shipment_serials (shipment_line_id TEXT NOT NULL REFERENCES shipment_lines(id) ON DELETE CASCADE," 
            "serial_number_id TEXT NOT NULL REFERENCES serial_numbers(id),status TEXT NOT NULL DEFAULT 'claimed' "
            "CHECK(status IN ('claimed','shipped','released')),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP," 
            "PRIMARY KEY(shipment_line_id,serial_number_id))"
        )
        conn.execute(
            "INSERT INTO shipment_serials(shipment_line_id,serial_number_id,status,created_at) "
            "SELECT old.shipment_line_id,old.serial_number_id,CASE s.status WHEN 'shipped' THEN 'shipped' "
            "WHEN 'cancelled' THEN 'released' ELSE 'claimed' END,old.created_at FROM shipment_serials_legacy old "
            "JOIN shipment_lines sl ON sl.id=old.shipment_line_id JOIN shipments s ON s.id=sl.shipment_id"
        )
        conn.execute("DROP TABLE shipment_serials_legacy")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shipment_serial_active ON shipment_serials(serial_number_id) WHERE status='claimed'")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(8,'serial assignment history')")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(9,'lot and serial return traceability')")
    outbox_columns = {row[1] for row in conn.execute("PRAGMA table_info(outbox_events)")}
    for name, definition in {
        "processing_owner": "TEXT NOT NULL DEFAULT ''",
        "lease_expires_at": "TEXT",
    }.items():
        if name not in outbox_columns:
            conn.execute(f"ALTER TABLE outbox_events ADD COLUMN {name} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_lease ON outbox_events(status,lease_expires_at,available_at)")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(10,'runtime coordination and leased outbox')")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(11,'verified backup recovery points')")
    backup_columns = {row[1] for row in conn.execute("PRAGMA table_info(backup_catalog)")}
    if "verified_mtime_ns" not in backup_columns:
        conn.execute("ALTER TABLE backup_catalog ADD COLUMN verified_mtime_ns INTEGER NOT NULL DEFAULT 0")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(12,'backup change detection evidence')")
    invoice_columns = {row[1] for row in conn.execute("PRAGMA table_info(invoices)")}
    for name, definition in {
        "external_reference": "TEXT NOT NULL DEFAULT ''",
        "match_status": "TEXT NOT NULL DEFAULT 'not_required' CHECK(match_status IN ('not_required','system_generated','matched','exception'))",
        "match_details_json": "TEXT NOT NULL DEFAULT '{}'",
    }.items():
        if name not in invoice_columns:
            conn.execute(f"ALTER TABLE invoices ADD COLUMN {name} {definition}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_payable_supplier_invoice_reference "
        "ON invoices(organization_id,partner_id,external_reference) "
        "WHERE invoice_type='payable' AND status<>'void' AND external_reference<>''"
    )
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(13,'purchase invoice three way matching')")
    stock_move_columns = {row[1] for row in conn.execute("PRAGMA table_info(stock_moves)")}
    if "total_cost_cents" not in stock_move_columns:
        conn.execute("ALTER TABLE stock_moves ADD COLUMN total_cost_cents INTEGER NOT NULL DEFAULT 0 CHECK(total_cost_cents >= 0)")
    if "valuation_status" not in stock_move_columns:
        conn.execute("ALTER TABLE stock_moves ADD COLUMN valuation_status TEXT NOT NULL DEFAULT 'pending' CHECK(valuation_status IN ('pending','valued','not_applicable'))")
        conn.execute("UPDATE stock_moves SET valuation_status='not_applicable'")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(14,'double entry ledger and fifo valuation')")
    reconciliation_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='reconciliations'"
    ).fetchone()[0]
    if "'accounting'" not in reconciliation_sql:
        conn.execute("ALTER TABLE reconciliations RENAME TO reconciliations_legacy")
        conn.execute(
            "CREATE TABLE reconciliations (id TEXT PRIMARY KEY,organization_id TEXT NOT NULL REFERENCES organizations(id),"
            "reconciliation_type TEXT NOT NULL CHECK(reconciliation_type IN ('inventory','sales','purchase','finance','accounting')) ,"
            "as_of TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('passed','failed')),checked_items INTEGER NOT NULL DEFAULT 0,"
            "discrepancy_count INTEGER NOT NULL DEFAULT 0,result_json TEXT NOT NULL DEFAULT '{}',created_by TEXT NOT NULL,"
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO reconciliations(id,organization_id,reconciliation_type,as_of,status,checked_items,discrepancy_count,result_json,created_by,created_at) "
            "SELECT id,organization_id,reconciliation_type,as_of,status,checked_items,discrepancy_count,result_json,created_by,created_at FROM reconciliations_legacy"
        )
        conn.execute("DROP TABLE reconciliations_legacy")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reconciliation_org ON reconciliations(organization_id,reconciliation_type,created_at)")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(15,'accounting reconciliation close controls')")
    payment_columns = {row[1] for row in conn.execute("PRAGMA table_info(payments)")}
    if "bank_account_id" not in payment_columns:
        conn.execute("ALTER TABLE payments ADD COLUMN bank_account_id TEXT REFERENCES bank_accounts(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_bank_account ON payments(organization_id,bank_account_id,payment_date,status)")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(16,'cash management and bank reconciliation')")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(17,'ecommerce channel order hub')")
