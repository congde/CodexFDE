from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flowerp.mock_data import load_mock_data, verify_mock_data
from flowerp.store import ERPStore


class MockDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ERPStore(Path(self.tmp.name) / "flowerp.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_complete_fixture_is_idempotent_and_reconciles(self) -> None:
        first = load_mock_data(self.store)
        self.assertTrue(first["loaded"])
        self.assertTrue(first["verification"]["complete"])
        self.assertTrue(all(first["verification"]["checks"].values()))
        counts = first["verification"]["counts"]

        replay = load_mock_data(self.store)
        self.assertFalse(replay["loaded"])
        self.assertTrue(replay["idempotent_replay"])
        for key in ("products", "customers", "suppliers", "sales_orders", "purchase_orders", "invoices", "payments", "journal_entries"):
            self.assertEqual(counts[key], replay["verification"]["counts"][key])

        self.store.execute(
            "UPDATE system_settings SET setting_value=? WHERE organization_id='ORG-DEFAULT' AND setting_key='demo.mock_data'",
            ('{"version":2,"complete":true}',),
        )
        upgraded = load_mock_data(self.store)
        self.assertTrue(upgraded["loaded"])
        self.assertEqual(counts["journal_entries"], upgraded["verification"]["counts"]["journal_entries"])
        self.assertTrue(upgraded["verification"]["checks"]["monthly_trend_coverage"])

    def test_verification_exposes_tampered_sales_total(self) -> None:
        fixture = load_mock_data(self.store)
        order_id = self.store.scalar(
            "SELECT id FROM sales_documents WHERE channel='mock' AND external_reference='MOCK-SO-DRAFT'"
        )
        self.store.execute("UPDATE sales_documents SET total_cents=total_cents+1 WHERE id=?", (order_id,))

        result = verify_mock_data(self.store)
        self.assertFalse(result["complete"])
        self.assertFalse(result["checks"]["sales_reconciliation"])
        self.assertGreater(result["reconciliations"]["sales"]["discrepancy_count"], 0)


if __name__ == "__main__":
    unittest.main()
