"""Exercise real local CSV publication and the manual's independent probe."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from flowerp.inventory_export import export_inventory_file


ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / 'docs/courses/L04/scripts/check_inventory_delivery.py'
spec = importlib.util.spec_from_file_location('l04_inventory_probe', PROBE_PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class L04InventoryDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_content_contracts_use_real_inventory_services(self):
        for name in ('normal', 'empty', 'special', 'ordering'):
            with self.subTest(name=name):
                directory = self.directory / name
                directory.mkdir()
                probe.content_check(directory, name)

    def test_real_writer_preserves_existing_and_absent_targets_on_partial_write(self):
        probe.file_check(self.directory, 'flowerp.inventory_export:export_inventory_file')

    def test_publication_failure_keeps_old_report_and_database(self):
        store, _ = probe.prepare(self.directory, 'normal')
        target = self.directory / 'inventory.csv'
        target.write_bytes(b'previous report')
        before = probe.snapshot(store)
        with patch('flowerp.inventory_export.os.replace', side_effect=PermissionError('target locked')):
            with self.assertRaises(PermissionError):
                export_inventory_file(store, target)
        self.assertEqual(b'previous report', target.read_bytes())
        self.assertEqual(before, probe.snapshot(store))
        self.assertEqual([], list(self.directory.glob('*.tmp')))

    def test_database_cannot_be_overwritten_by_file_api(self):
        store, _ = probe.prepare(self.directory, 'normal')
        before = Path(store.path).read_bytes()
        with self.assertRaisesRegex(ValueError, '不能覆盖数据库'):
            export_inventory_file(store, store.path)
        self.assertEqual(before, Path(store.path).read_bytes())

    def test_probe_rejects_wrong_csv_instead_of_counting_green(self):
        with patch('flowerp.import_export.ImportExportService.export_csv', return_value='sku,name\nP001,wrong\n'):
            with self.assertRaisesRegex(AssertionError, 'CSV'):
                probe.content_check(self.directory, 'normal')
        self.assertTrue((self.directory / 'actual.csv').exists())
        self.assertTrue((self.directory / 'after.json').exists())

    def test_cli_requires_existing_database(self):
        missing = self.directory / 'missing.db'
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'flowerp.inventory_export',
                                 '--database', str(missing), '--output', str(self.directory / 'out.csv')],
                                cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(1, result.returncode)
        self.assertIn('数据库不存在', result.stdout)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
