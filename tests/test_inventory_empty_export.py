import csv
import io
from pathlib import Path
import tempfile
import unittest

from flowerp import ERPService, ERPStore
from flowerp.identity import Principal, SYSTEM_PRINCIPAL
from flowerp.import_export import ImportExportService
from flowerp.models import PermissionDenied, ValidationError


class InventoryEmptyExportTests(unittest.TestCase):
    headers = ['sku', 'name', 'site', 'location', 'lot_id', 'on_hand', 'reserved', 'available']

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = ERPStore(Path(temporary.name) / 'erp.db')
        self.exporter = ImportExportService(self.store)

    def rows(self, principal=SYSTEM_PRINCIPAL):
        content = self.exporter.export_csv(principal, 'inventory')
        return list(csv.reader(io.StringIO(content.lstrip('\ufeff'))))

    def test_empty_inventory_retains_exact_schema(self):
        self.assertEqual(self.rows(), [self.headers])

    def test_populated_export_and_other_organization_keep_read_only_contract(self):
        service = ERPService(self.store)
        service.add_product('EMPTY-EXPORT', '导出回归', 100, 1)
        service.receive_stock('EMPTY-EXPORT', 7, 'empty-export-regression')
        before = self.store.rows('SELECT * FROM stock_balance ORDER BY product_id')
        rows = self.rows()
        self.assertEqual(rows[0], self.headers)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], 'EMPTY-EXPORT')
        self.assertEqual(rows[1][-3:], ['7', '0', '7'])
        other = Principal('other', 'ORG-OTHER', 'other', '另一个组织', frozenset({'reports.read'}))
        self.assertEqual(self.rows(other), [self.headers])
        self.assertEqual(self.store.rows('SELECT * FROM stock_balance ORDER BY product_id'), before)

    def test_rejected_exports_leave_inventory_unchanged(self):
        before = self.store.rows('SELECT * FROM stock_balance ORDER BY product_id')
        denied = Principal('denied', 'ORG-DEFAULT', 'denied', '无报告权限', frozenset())
        with self.assertRaises(PermissionDenied):
            self.rows(denied)
        with self.assertRaises(ValidationError):
            self.exporter.export_csv(SYSTEM_PRINCIPAL, 'unknown-export-type')
        self.assertEqual(self.store.rows('SELECT * FROM stock_balance ORDER BY product_id'), before)
