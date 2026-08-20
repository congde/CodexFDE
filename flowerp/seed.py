from __future__ import annotations

from .models import NotFound, OrderLine
from .service import ERPService


CATALOG = [
    ("NOTEBOOK-AI", "轻薄办公笔记本", 599900, 4, 8),
    ("CHARGE-65W", "65W 氮化镓充电器", 19900, 8, 24),
    ("DESK-LAMP", "无频闪桌面台灯", 29900, 6, 16),
]


def seed_demo(service: ERPService) -> None:
    for sku, name, price, reorder, opening in CATALOG:
        service.add_product(sku, name, price, reorder)
        service.receive_stock(sku, opening, f"seed:{sku}", "demo-seed")


def load_ecommerce_sample(service: ERPService) -> dict:
    """Idempotently load editable master data and the 20/8/12 sample order."""
    try:
        customer = service.customer("CUS-SAMPLE")
    except NotFound:
        customer = service.add_customer(
            "星河设计工作室", "13800001111", "buyer@example.com",
            "上海市浦东新区示例路 88 号", "CUS-SAMPLE",
        )
    try:
        supplier = service.supplier("SUP-SAMPLE")
    except NotFound:
        supplier = service.add_supplier(
            "远景数码供应链", "周经理", "13900002222", "SUP-SAMPLE"
        )
    seed_demo(service)
    try:
        order = service.order("SO-SAMPLE-20")
    except NotFound:
        product = service.product("NOTEBOOK-AI")
        order = service.create_order(
            customer["name"],
            [OrderLine("NOTEBOOK-AI", 20, product["unit_price_cents"])],
            "SO-SAMPLE-20", customer["id"], "enterprise", "课程示例：库存 8 台，需求 20 台",
        )
    return {"customer": customer, "supplier": supplier, "order": order}
