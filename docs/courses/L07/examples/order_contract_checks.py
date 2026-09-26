"""L07 checks loaded by a candidate Harness. Uses independent integer expectations.

Import this file from the candidate's Eval registry; it deliberately does not add
the reference repository to sys.path. No Hook or Codex session is enabled here.
"""
from pathlib import Path
import tempfile

from flowerp.models import OrderLine, NotFound
from flowerp.service import ERPService
from flowerp.store import ERPStore


def draft_amount():
    with tempfile.TemporaryDirectory(prefix='l07-amount-') as tmp:
        store = ERPStore(Path(tmp) / 'orders.db')
        service = ERPService(store)
        service.add_product('A', '商品 A', 3000)
        service.add_product('B', '商品 B', 2000)
        before = store.rows('SELECT * FROM stock ORDER BY sku')
        order = service.create_order('课堂客户', [OrderLine('A', 2, 3000),
                    OrderLine('B', 3, 2000)], 'L07-AMOUNT')
        assert order['status'] == 'draft', order['status']
        assert order['total_cents'] == 12000, f"expected=12000 actual={order['total_cents']}"
        amounts = [row['line_total_cents'] for row in order['lines']]
        assert amounts == [6000, 6000], f'expected=[6000,6000] actual={amounts}'
        assert store.rows('SELECT * FROM stock ORDER BY sku') == before
        return '两行金额 6000+6000=12000 分；状态 draft；零库存可建草稿且库存行不变'


def rejected_order_has_no_residue():
    with tempfile.TemporaryDirectory(prefix='l07-reject-') as tmp:
        store = ERPStore(Path(tmp) / 'orders.db')
        service = ERPService(store)
        service.add_product('A', '商品 A', 3000)
        for oid, lines, expected in [
            ('L07-ZERO', [OrderLine('A', 0, 3000)], ValueError),
            ('L07-NEGATIVE', [OrderLine('A', -1, 3000)], ValueError),
            ('L07-MISSING', [OrderLine('A', 1, 3000), OrderLine('MISSING', 1, 2000)], NotFound),
        ]:
            before = {table: store.rows(f'SELECT * FROM {table}')
                      for table in ['sales_orders', 'sales_order_lines', 'stock']}
            try:
                service.create_order('课堂客户', lines, oid)
            except expected:
                pass
            else:
                raise AssertionError(f'{oid}: expected {expected.__name__}')
            after = {table: store.rows(f'SELECT * FROM {table}') for table in before}
            assert after == before, f'{oid}: rejected but state changed'
        return '数量为零或负数、第二行商品不存在均拒绝；订单头、明细、库存三表保持原样'


def amount_transfer():
    with tempfile.TemporaryDirectory(prefix='l07-transfer-') as tmp:
        service = ERPService(ERPStore(Path(tmp) / 'orders.db'))
        service.add_product('A', '商品 A', 2500)
        service.add_product('B', '商品 B', 1800)
        order = service.create_order('迁移客户', [OrderLine('A', 3, 2500),
                    OrderLine('B', 2, 1800)], 'L07-TRANSFER')
        assert order['total_cents'] == 11100, f"expected=11100 actual={order['total_cents']}"
        assert [r['line_total_cents'] for r in order['lines']] == [7500, 3600]
        return '迁移输入：3×2500+2×1800=11100 分；排除写死 12000'
