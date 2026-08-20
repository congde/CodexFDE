"""Additive production schema for FlowERP.

The original teaching tables remain readable.  New tables use immutable ledger
records and explicit document headers/lines so upgrades never rewrite history.
SQLite is intentionally supported as a single-instance deployment database.
"""

from __future__ import annotations

import sqlite3


SCHEMA_V2 = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS organizations (
  id TEXT PRIMARY KEY,
  code TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  legal_name TEXT NOT NULL DEFAULT '',
  tax_number TEXT NOT NULL DEFAULT '',
  base_currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(base_currency)=3),
  timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sites (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  code TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  site_type TEXT NOT NULL DEFAULT 'warehouse'
    CHECK(site_type IN ('warehouse','store','transit','virtual')),
  address TEXT NOT NULL DEFAULT '',
  contact_name TEXT NOT NULL DEFAULT '',
  contact_phone TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, code)
);

CREATE TABLE IF NOT EXISTS storage_locations (
  id TEXT PRIMARY KEY,
  site_id TEXT NOT NULL REFERENCES sites(id),
  code TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  location_type TEXT NOT NULL DEFAULT 'internal'
    CHECK(location_type IN ('internal','receiving','shipping','quarantine','damaged','supplier','customer')),
  allow_negative INTEGER NOT NULL DEFAULT 0 CHECK(allow_negative IN (0,1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  UNIQUE(site_id, code)
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  username TEXT NOT NULL COLLATE NOCASE,
  display_name TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  password_salt TEXT NOT NULL,
  password_iterations INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','locked','disabled')),
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  password_changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(organization_id, username)
);

CREATE TABLE IF NOT EXISTS roles (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  code TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  system INTEGER NOT NULL DEFAULT 0 CHECK(system IN (0,1)),
  UNIQUE(organization_id, code)
);

CREATE TABLE IF NOT EXISTS permissions (
  code TEXT PRIMARY KEY,
  description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS role_permissions (
  role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission_code TEXT NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
  PRIMARY KEY(role_id, permission_code)
);

CREATE TABLE IF NOT EXISTS user_roles (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  PRIMARY KEY(user_id, role_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  csrf_token TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  revoked_at TEXT,
  remote_addr TEXT NOT NULL DEFAULT '',
  user_agent TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  organization_id TEXT,
  actor_id TEXT,
  actor_name TEXT NOT NULL DEFAULT 'system',
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  request_id TEXT NOT NULL DEFAULT '',
  before_json TEXT,
  after_json TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  remote_addr TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

CREATE TABLE IF NOT EXISTS number_sequences (
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  sequence_key TEXT NOT NULL,
  prefix TEXT NOT NULL,
  date_pattern TEXT NOT NULL DEFAULT '%Y%m%d',
  current_date TEXT NOT NULL DEFAULT '',
  current_value INTEGER NOT NULL DEFAULT 0,
  padding INTEGER NOT NULL DEFAULT 5 CHECK(padding BETWEEN 3 AND 12),
  PRIMARY KEY(organization_id, sequence_key)
);

CREATE TABLE IF NOT EXISTS product_categories (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  parent_id TEXT REFERENCES product_categories(id),
  code TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  UNIQUE(organization_id, code)
);

CREATE TABLE IF NOT EXISTS units_of_measure (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  code TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'unit',
  ratio_micros INTEGER NOT NULL DEFAULT 1000000 CHECK(ratio_micros > 0),
  rounding INTEGER NOT NULL DEFAULT 1 CHECK(rounding > 0),
  UNIQUE(organization_id, code)
);

CREATE TABLE IF NOT EXISTS product_master (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  sku TEXT NOT NULL COLLATE NOCASE,
  barcode TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
  name TEXT NOT NULL,
  short_name TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  category_id TEXT REFERENCES product_categories(id),
  uom_id TEXT REFERENCES units_of_measure(id),
  tracking TEXT NOT NULL DEFAULT 'none' CHECK(tracking IN ('none','lot','serial')),
  sales_price_cents INTEGER NOT NULL DEFAULT 0 CHECK(sales_price_cents >= 0),
  standard_cost_cents INTEGER NOT NULL DEFAULT 0 CHECK(standard_cost_cents >= 0),
  tax_rate_basis_points INTEGER NOT NULL DEFAULT 1300 CHECK(tax_rate_basis_points BETWEEN 0 AND 10000),
  min_stock INTEGER NOT NULL DEFAULT 0 CHECK(min_stock >= 0),
  max_stock INTEGER NOT NULL DEFAULT 0 CHECK(max_stock >= 0),
  shelf_life_days INTEGER NOT NULL DEFAULT 0 CHECK(shelf_life_days >= 0),
  purchasable INTEGER NOT NULL DEFAULT 1 CHECK(purchasable IN (0,1)),
  saleable INTEGER NOT NULL DEFAULT 1 CHECK(saleable IN (0,1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(organization_id, sku)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_product_barcode
  ON product_master(organization_id, barcode) WHERE barcode <> '';

CREATE TABLE IF NOT EXISTS customer_master (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  code TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  customer_type TEXT NOT NULL DEFAULT 'business' CHECK(customer_type IN ('business','individual')),
  tax_number TEXT NOT NULL DEFAULT '',
  contact_name TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  billing_address TEXT NOT NULL DEFAULT '',
  shipping_address TEXT NOT NULL DEFAULT '',
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  payment_terms_days INTEGER NOT NULL DEFAULT 0 CHECK(payment_terms_days >= 0),
  credit_limit_cents INTEGER NOT NULL DEFAULT 0 CHECK(credit_limit_cents >= 0),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(organization_id, code)
);

CREATE TABLE IF NOT EXISTS supplier_master (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  code TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  tax_number TEXT NOT NULL DEFAULT '',
  contact_name TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  payment_terms_days INTEGER NOT NULL DEFAULT 0 CHECK(payment_terms_days >= 0),
  lead_time_days INTEGER NOT NULL DEFAULT 0 CHECK(lead_time_days >= 0),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(organization_id, code)
);

CREATE TABLE IF NOT EXISTS supplier_products (
  supplier_id TEXT NOT NULL REFERENCES supplier_master(id),
  product_id TEXT NOT NULL REFERENCES product_master(id),
  supplier_sku TEXT NOT NULL DEFAULT '',
  min_order_qty INTEGER NOT NULL DEFAULT 1 CHECK(min_order_qty > 0),
  purchase_price_cents INTEGER NOT NULL DEFAULT 0 CHECK(purchase_price_cents >= 0),
  lead_time_days INTEGER NOT NULL DEFAULT 0 CHECK(lead_time_days >= 0),
  preferred INTEGER NOT NULL DEFAULT 0 CHECK(preferred IN (0,1)),
  PRIMARY KEY(supplier_id, product_id)
);

CREATE TABLE IF NOT EXISTS stock_lots (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  product_id TEXT NOT NULL REFERENCES product_master(id),
  lot_number TEXT NOT NULL COLLATE NOCASE,
  manufacture_date TEXT,
  expiry_date TEXT,
  supplier_id TEXT REFERENCES supplier_master(id),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','quarantine','expired','recalled')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, product_id, lot_number)
);

CREATE TABLE IF NOT EXISTS serial_numbers (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  product_id TEXT NOT NULL REFERENCES product_master(id),
  serial_number TEXT NOT NULL COLLATE NOCASE,
  lot_id TEXT REFERENCES stock_lots(id),
  status TEXT NOT NULL DEFAULT 'available'
    CHECK(status IN ('available','reserved','shipped','returned','damaged','scrapped')),
  current_location_id TEXT REFERENCES storage_locations(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, serial_number)
);

CREATE TABLE IF NOT EXISTS stock_balance (
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  product_id TEXT NOT NULL REFERENCES product_master(id),
  location_id TEXT NOT NULL REFERENCES storage_locations(id),
  lot_id TEXT NOT NULL DEFAULT '',
  on_hand INTEGER NOT NULL DEFAULT 0 CHECK(on_hand >= 0),
  reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved >= 0 AND reserved <= on_hand),
  incoming INTEGER NOT NULL DEFAULT 0 CHECK(incoming >= 0),
  outgoing INTEGER NOT NULL DEFAULT 0 CHECK(outgoing >= 0),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(organization_id, product_id, location_id, lot_id)
);

CREATE TABLE IF NOT EXISTS stock_moves (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  event_key TEXT NOT NULL,
  product_id TEXT NOT NULL REFERENCES product_master(id),
  source_location_id TEXT REFERENCES storage_locations(id),
  destination_location_id TEXT REFERENCES storage_locations(id),
  lot_id TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  unit_cost_cents INTEGER NOT NULL DEFAULT 0 CHECK(unit_cost_cents >= 0),
  move_type TEXT NOT NULL CHECK(move_type IN
    ('opening','receipt','shipment','transfer','adjustment','return_in','return_out','scrap')),
  reference_type TEXT NOT NULL DEFAULT '',
  reference_id TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_stock_moves_product ON stock_moves(product_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_moves_reference ON stock_moves(reference_type, reference_id);

CREATE TABLE IF NOT EXISTS stock_reservations (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  product_id TEXT NOT NULL REFERENCES product_master(id),
  location_id TEXT NOT NULL REFERENCES storage_locations(id),
  lot_id TEXT NOT NULL DEFAULT '',
  reference_type TEXT NOT NULL,
  reference_id TEXT NOT NULL,
  reference_line_id TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','released','consumed')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  released_at TEXT,
  UNIQUE(organization_id, reference_type, reference_id, reference_line_id, product_id, location_id, lot_id)
);

CREATE TABLE IF NOT EXISTS stock_counts (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  document_number TEXT NOT NULL,
  location_id TEXT NOT NULL REFERENCES storage_locations(id),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','counting','pending_approval','posted','cancelled')),
  count_date TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  approved_by TEXT,
  posted_at TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, document_number)
);

CREATE TABLE IF NOT EXISTS stock_count_lines (
  id TEXT PRIMARY KEY,
  count_id TEXT NOT NULL REFERENCES stock_counts(id) ON DELETE CASCADE,
  product_id TEXT NOT NULL REFERENCES product_master(id),
  lot_id TEXT NOT NULL DEFAULT '',
  system_quantity INTEGER NOT NULL,
  counted_quantity INTEGER CHECK(counted_quantity >= 0),
  variance_quantity INTEGER,
  note TEXT NOT NULL DEFAULT '',
  UNIQUE(count_id, product_id, lot_id)
);

CREATE TABLE IF NOT EXISTS sales_documents (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  document_number TEXT NOT NULL,
  document_type TEXT NOT NULL DEFAULT 'order' CHECK(document_type IN ('quotation','order','return')),
  customer_id TEXT NOT NULL REFERENCES customer_master(id),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN
    ('draft','confirmed','reserved','partially_shipped','shipped','cancelled','returned')),
  order_date TEXT NOT NULL,
  requested_delivery_date TEXT,
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  exchange_rate_micros INTEGER NOT NULL DEFAULT 1000000 CHECK(exchange_rate_micros > 0),
  subtotal_cents INTEGER NOT NULL DEFAULT 0,
  discount_cents INTEGER NOT NULL DEFAULT 0 CHECK(discount_cents >= 0),
  tax_cents INTEGER NOT NULL DEFAULT 0 CHECK(tax_cents >= 0),
  freight_cents INTEGER NOT NULL DEFAULT 0 CHECK(freight_cents >= 0),
  total_cents INTEGER NOT NULL DEFAULT 0,
  paid_cents INTEGER NOT NULL DEFAULT 0 CHECK(paid_cents >= 0),
  shipping_address TEXT NOT NULL DEFAULT '',
  billing_address TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT 'direct',
  external_reference TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  cancellation_reason TEXT NOT NULL DEFAULT '',
  confirmed_at TEXT,
  shipped_at TEXT,
  cancelled_at TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(organization_id, document_number),
  UNIQUE(organization_id, channel, external_reference)
);

CREATE TABLE IF NOT EXISTS sales_document_lines (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES sales_documents(id) ON DELETE CASCADE,
  line_number INTEGER NOT NULL,
  product_id TEXT NOT NULL REFERENCES product_master(id),
  description TEXT NOT NULL DEFAULT '',
  ordered_quantity INTEGER NOT NULL CHECK(ordered_quantity > 0),
  reserved_quantity INTEGER NOT NULL DEFAULT 0 CHECK(reserved_quantity >= 0),
  shipped_quantity INTEGER NOT NULL DEFAULT 0 CHECK(shipped_quantity >= 0),
  returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK(returned_quantity >= 0),
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
  discount_basis_points INTEGER NOT NULL DEFAULT 0 CHECK(discount_basis_points BETWEEN 0 AND 10000),
  tax_rate_basis_points INTEGER NOT NULL DEFAULT 0 CHECK(tax_rate_basis_points BETWEEN 0 AND 10000),
  net_cents INTEGER NOT NULL,
  tax_cents INTEGER NOT NULL,
  total_cents INTEGER NOT NULL,
  warehouse_id TEXT REFERENCES sites(id),
  UNIQUE(document_id, line_number)
);

CREATE TABLE IF NOT EXISTS shipments (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  shipment_number TEXT NOT NULL,
  sales_document_id TEXT NOT NULL REFERENCES sales_documents(id),
  location_id TEXT NOT NULL REFERENCES storage_locations(id),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','picked','packed','shipped','cancelled')),
  carrier TEXT NOT NULL DEFAULT '',
  tracking_number TEXT NOT NULL DEFAULT '',
  recipient TEXT NOT NULL DEFAULT '',
  shipping_address TEXT NOT NULL DEFAULT '',
  shipped_at TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, shipment_number)
);

CREATE TABLE IF NOT EXISTS shipment_lines (
  id TEXT PRIMARY KEY,
  shipment_id TEXT NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
  sales_line_id TEXT NOT NULL REFERENCES sales_document_lines(id),
  product_id TEXT NOT NULL REFERENCES product_master(id),
  lot_id TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  UNIQUE(shipment_id, sales_line_id, lot_id)
);

CREATE TABLE IF NOT EXISTS sales_returns (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  return_number TEXT NOT NULL,
  sales_document_id TEXT NOT NULL REFERENCES sales_documents(id),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','authorized','received','refunded','rejected','cancelled')),
  reason_code TEXT NOT NULL,
  reason_detail TEXT NOT NULL DEFAULT '',
  resolution TEXT NOT NULL DEFAULT 'refund' CHECK(resolution IN ('refund','replace','credit','repair')),
  created_by TEXT,
  approved_by TEXT,
  received_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, return_number)
);

CREATE TABLE IF NOT EXISTS sales_return_lines (
  id TEXT PRIMARY KEY,
  return_id TEXT NOT NULL REFERENCES sales_returns(id) ON DELETE CASCADE,
  sales_line_id TEXT NOT NULL REFERENCES sales_document_lines(id),
  product_id TEXT NOT NULL REFERENCES product_master(id),
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  received_quantity INTEGER NOT NULL DEFAULT 0 CHECK(received_quantity >= 0),
  condition TEXT NOT NULL DEFAULT 'resellable' CHECK(condition IN ('resellable','damaged','defective','opened')),
  refund_cents INTEGER NOT NULL DEFAULT 0 CHECK(refund_cents >= 0)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  order_number TEXT NOT NULL,
  supplier_id TEXT NOT NULL REFERENCES supplier_master(id),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN
    ('draft','pending_approval','approved','partially_received','received','cancelled')),
  order_date TEXT NOT NULL,
  expected_date TEXT,
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  exchange_rate_micros INTEGER NOT NULL DEFAULT 1000000 CHECK(exchange_rate_micros > 0),
  subtotal_cents INTEGER NOT NULL DEFAULT 0,
  tax_cents INTEGER NOT NULL DEFAULT 0,
  freight_cents INTEGER NOT NULL DEFAULT 0,
  total_cents INTEGER NOT NULL DEFAULT 0,
  warehouse_id TEXT NOT NULL REFERENCES sites(id),
  supplier_reference TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  approved_by TEXT,
  approved_at TEXT,
  cancelled_at TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(organization_id, order_number)
);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
  id TEXT PRIMARY KEY,
  purchase_order_id TEXT NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
  line_number INTEGER NOT NULL,
  product_id TEXT NOT NULL REFERENCES product_master(id),
  ordered_quantity INTEGER NOT NULL CHECK(ordered_quantity > 0),
  received_quantity INTEGER NOT NULL DEFAULT 0 CHECK(received_quantity >= 0),
  rejected_quantity INTEGER NOT NULL DEFAULT 0 CHECK(rejected_quantity >= 0),
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
  tax_rate_basis_points INTEGER NOT NULL DEFAULT 0 CHECK(tax_rate_basis_points BETWEEN 0 AND 10000),
  net_cents INTEGER NOT NULL,
  tax_cents INTEGER NOT NULL,
  total_cents INTEGER NOT NULL,
  UNIQUE(purchase_order_id, line_number)
);

CREATE TABLE IF NOT EXISTS goods_receipts (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  receipt_number TEXT NOT NULL,
  purchase_order_id TEXT NOT NULL REFERENCES purchase_orders(id),
  location_id TEXT NOT NULL REFERENCES storage_locations(id),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted','cancelled')),
  supplier_delivery_note TEXT NOT NULL DEFAULT '',
  receipt_date TEXT NOT NULL,
  posted_by TEXT,
  posted_at TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, receipt_number)
);

CREATE TABLE IF NOT EXISTS goods_receipt_lines (
  id TEXT PRIMARY KEY,
  receipt_id TEXT NOT NULL REFERENCES goods_receipts(id) ON DELETE CASCADE,
  purchase_line_id TEXT NOT NULL REFERENCES purchase_order_lines(id),
  product_id TEXT NOT NULL REFERENCES product_master(id),
  accepted_quantity INTEGER NOT NULL CHECK(accepted_quantity >= 0),
  rejected_quantity INTEGER NOT NULL DEFAULT 0 CHECK(rejected_quantity >= 0),
  lot_id TEXT NOT NULL DEFAULT '',
  rejection_reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS accounting_periods (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  year INTEGER NOT NULL,
  month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
  closed_by TEXT,
  closed_at TEXT,
  UNIQUE(organization_id, year, month)
);

CREATE TABLE IF NOT EXISTS invoices (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  invoice_number TEXT NOT NULL,
  invoice_type TEXT NOT NULL CHECK(invoice_type IN ('receivable','payable','credit_note')),
  partner_type TEXT NOT NULL CHECK(partner_type IN ('customer','supplier')),
  partner_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','issued','partially_paid','paid','void')),
  invoice_date TEXT NOT NULL,
  due_date TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  subtotal_cents INTEGER NOT NULL,
  tax_cents INTEGER NOT NULL,
  total_cents INTEGER NOT NULL,
  paid_cents INTEGER NOT NULL DEFAULT 0 CHECK(paid_cents >= 0),
  outstanding_cents INTEGER NOT NULL CHECK(outstanding_cents >= 0),
  notes TEXT NOT NULL DEFAULT '',
  issued_by TEXT,
  issued_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(organization_id, invoice_number)
);

CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  payment_number TEXT NOT NULL,
  payment_type TEXT NOT NULL CHECK(payment_type IN ('receipt','disbursement','refund')),
  partner_type TEXT NOT NULL CHECK(partner_type IN ('customer','supplier')),
  partner_id TEXT NOT NULL,
  payment_date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
  currency TEXT NOT NULL DEFAULT 'CNY' CHECK(length(currency)=3),
  method TEXT NOT NULL CHECK(method IN ('cash','bank_transfer','card','online','other')),
  external_reference TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'posted' CHECK(status IN ('draft','posted','void')),
  notes TEXT NOT NULL DEFAULT '',
  posted_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(organization_id, payment_number),
  UNIQUE(organization_id, method, external_reference)
);

CREATE TABLE IF NOT EXISTS payment_allocations (
  id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL REFERENCES payments(id),
  invoice_id TEXT NOT NULL REFERENCES invoices(id),
  amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(payment_id, invoice_id)
);

CREATE TABLE IF NOT EXISTS outbox_events (
  id TEXT PRIMARY KEY,
  organization_id TEXT,
  event_type TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','published','failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_at TEXT,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(status, available_at);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  organization_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_status INTEGER,
  response_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT NOT NULL,
  PRIMARY KEY(organization_id, scope, key)
);

CREATE TABLE IF NOT EXISTS system_settings (
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  setting_key TEXT NOT NULL,
  setting_value TEXT NOT NULL,
  value_type TEXT NOT NULL DEFAULT 'string' CHECK(value_type IN ('string','integer','boolean','json')),
  updated_by TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(organization_id, setting_key)
);

CREATE TRIGGER IF NOT EXISTS trg_stock_balance_nonnegative_insert
BEFORE INSERT ON stock_balance
WHEN NEW.on_hand < 0 OR NEW.reserved < 0 OR NEW.reserved > NEW.on_hand
BEGIN SELECT RAISE(ABORT, 'stock balance invariant violated'); END;

CREATE TRIGGER IF NOT EXISTS trg_stock_balance_nonnegative_update
BEFORE UPDATE ON stock_balance
WHEN NEW.on_hand < 0 OR NEW.reserved < 0 OR NEW.reserved > NEW.on_hand
BEGIN SELECT RAISE(ABORT, 'stock balance invariant violated'); END;

CREATE TRIGGER IF NOT EXISTS trg_sales_line_quantity_update
BEFORE UPDATE ON sales_document_lines
WHEN NEW.shipped_quantity > NEW.ordered_quantity
  OR NEW.reserved_quantity > (NEW.ordered_quantity - NEW.shipped_quantity)
  OR NEW.returned_quantity > NEW.shipped_quantity
BEGIN SELECT RAISE(ABORT, 'sales line quantity invariant violated'); END;

CREATE TRIGGER IF NOT EXISTS trg_purchase_line_quantity_update
BEFORE UPDATE ON purchase_order_lines
WHEN NEW.received_quantity + NEW.rejected_quantity > NEW.ordered_quantity
BEGIN SELECT RAISE(ABORT, 'purchase line quantity invariant violated'); END;
"""


PERMISSIONS = {
    "master.read": "查看基础资料",
    "master.write": "维护基础资料",
    "inventory.read": "查看库存",
    "inventory.receive": "执行入库",
    "inventory.ship": "执行出库",
    "inventory.transfer": "执行调拨",
    "inventory.adjust": "盘点与调整库存",
    "sales.read": "查看销售单据",
    "sales.write": "创建和修改销售单据",
    "sales.confirm": "确认销售订单",
    "sales.cancel": "取消销售订单",
    "purchase.read": "查看采购单据",
    "purchase.write": "创建和修改采购单据",
    "purchase.approve": "审批采购单",
    "purchase.receive": "采购收货",
    "finance.read": "查看应收应付",
    "finance.write": "开票和收付款",
    "finance.close": "关闭财务期间",
    "reports.read": "查看和导出报表",
    "audit.read": "查看审计日志",
    "users.manage": "管理用户和权限",
    "settings.manage": "管理系统设置",
}


ROLE_PERMISSIONS = {
    "admin": tuple(PERMISSIONS),
    "sales": ("master.read", "inventory.read", "sales.read", "sales.write", "sales.confirm", "sales.cancel", "reports.read"),
    "purchasing": ("master.read", "inventory.read", "purchase.read", "purchase.write", "reports.read"),
    "warehouse": ("master.read", "inventory.read", "inventory.receive", "inventory.ship", "inventory.transfer", "purchase.read", "purchase.receive", "sales.read", "reports.read"),
    "finance": ("master.read", "sales.read", "purchase.read", "finance.read", "finance.write", "finance.close", "reports.read"),
    "auditor": ("master.read", "inventory.read", "sales.read", "purchase.read", "finance.read", "reports.read", "audit.read"),
}


def apply_v2_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_V2)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version,name) VALUES(2,'production foundation')"
    )
    conn.executemany(
        "INSERT OR IGNORE INTO permissions(code,description) VALUES(?,?)",
        PERMISSIONS.items(),
    )
