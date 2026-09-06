"""Create an isolated, labelled purchase scenario for real page reconciliation."""
import json
import secrets
import argparse
from pathlib import Path

from flowerp.identity import IdentityService, SYSTEM_PRINCIPAL
from flowerp.master_data import MasterDataService
from flowerp.models import ApprovalRequired
from flowerp.purchasing import PurchasingService
from flowerp.store import ERPStore


def prepare(runtime: Path, *, password: str | None = None) -> dict:
    if (runtime / "flowerp.db").exists():
        raise FileExistsError("已有验收库，拒绝覆盖")
    runtime.mkdir(parents=True, exist_ok=True)
    store = ERPStore(runtime / "flowerp.db")
    identity = IdentityService(store)
    identity.bootstrap("维护者隔离页面验收", "page-audit", password or secrets.token_urlsafe(32))
    identity.ensure_local_defaults()
    principal = SYSTEM_PRINCIPAL
    master = MasterDataService(store)
    site = master.create_site(principal, "AUDIT", "验收仓")
    location = master.create_location(principal, site["id"], "STOCK", "验收库位")
    supplier = master.create_supplier(principal, "AUDIT-SUP", "验收供应商")
    product = master.create_product(principal, "L14-AUDIT", "验收商品", 1200, 600)
    purchasing = PurchasingService(store)
    order = purchasing.create_order(principal, supplier["id"], site["id"],
        [{"product_id": product["id"], "quantity": 3, "unit_price_cents": 600}],
        notes="维护者隔离验收；不是学生作品或真实商业订单")
    before = store.rows("SELECT * FROM stock_balance WHERE product_id=?", (product["id"],))
    try:
        purchasing.create_receipt(principal, order["id"], location["id"],
            [{"purchase_line_id": order["lines"][0]["id"], "accepted_quantity": 3}])
    except ApprovalRequired as error:
        rejection = str(error)
    else:
        raise AssertionError("未审批采购居然允许收货")
    assert store.rows("SELECT * FROM stock_balance WHERE product_id=?", (product["id"],)) == before
    assert store.scalar("SELECT COUNT(*) FROM goods_receipts WHERE purchase_order_id=?", (order["id"],)) == 0
    evidence = {"basis": "maintainer_isolated_scenario", "student_achievement": False,
                "product": product, "site": site, "location": location, "purchase": order,
                "before_stock": before, "unapproved_receipt_rejected": rejection,
                "rejection_left_stock_unchanged": True}
    (runtime / "scenario.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"runtime": str(runtime.resolve()), "purchase_id": order["id"], "order_number": order["order_number"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', required=True, help='新的隔离目录；已有库不会覆盖')
    args = parser.parse_args()
    print(json.dumps(prepare(Path(args.runtime_dir)), ensure_ascii=False))
