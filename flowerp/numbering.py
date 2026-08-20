from __future__ import annotations

import sqlite3
from datetime import datetime


DEFAULT_SEQUENCES = {
    "sales_order": "SO",
    "quotation": "QT",
    "shipment": "SH",
    "sales_return": "SR",
    "purchase_order": "PO",
    "goods_receipt": "GR",
    "stock_count": "SC",
    "stock_transfer": "ST",
    "receivable_invoice": "AR",
    "payable_invoice": "AP",
    "credit_note": "CN",
    "payment_receipt": "RC",
    "payment_disbursement": "PY",
}


def next_number(conn: sqlite3.Connection, organization_id: str, key: str, when: datetime | None = None) -> str:
    prefix = DEFAULT_SEQUENCES.get(key, key.upper()[:4])
    moment = when or datetime.now()
    current_date = moment.strftime("%Y%m%d")
    conn.execute(
        "INSERT OR IGNORE INTO number_sequences(organization_id,sequence_key,prefix) VALUES(?,?,?)",
        (organization_id, key, prefix),
    )
    conn.execute(
        "UPDATE number_sequences SET current_value=CASE WHEN number_sequences.current_date=? THEN current_value+1 ELSE 1 END,current_date=? "
        "WHERE organization_id=? AND sequence_key=?",
        (current_date, current_date, organization_id, key),
    )
    row = conn.execute(
        "SELECT prefix,current_value,padding FROM number_sequences WHERE organization_id=? AND sequence_key=?",
        (organization_id, key),
    ).fetchone()
    return f"{row['prefix']}-{current_date}-{row['current_value']:0{row['padding']}d}"
