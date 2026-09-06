from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flowerp import ERPService, ERPStore
from flowerp.identity import SYSTEM_PRINCIPAL
from flowerp.import_export import ImportExportService


class AuthoritativeLedgerTests(unittest.TestCase):
    def test_export_matches_facade_and_v2_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = ERPService(ERPStore(Path(temporary) / "ledger.db"))
            service.add_product("LEDGER-A", "权威账本商品", 1000, 0)
            service.receive_stock("LEDGER-A", 4, "ledger-open")
            available = service.product("LEDGER-A")["available"]
            export = service.export_inventory()
            self.assertIn(",4,0,4", export)
            self.assertEqual(available, 4)
            v2 = ImportExportService(service.store).export_csv(SYSTEM_PRINCIPAL, "inventory")
            self.assertTrue(any(line.startswith("LEDGER-A,") and line.endswith(",4,0,4") for line in v2.splitlines()))


if __name__ == "__main__":
    unittest.main()
