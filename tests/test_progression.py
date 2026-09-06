from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from eval.progression import (
    enabled_at_lesson_start,
    load_enabled_capabilities,
    progression_payload,
    require_capability,
)


class ProgressionTests(unittest.TestCase):
    def test_missing_file_means_all_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(load_enabled_capabilities(root))
            require_capability("inventory_export", root=root)

    def test_l04_start_blocks_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "docs" / "courses" / "labs" / "baselines" / "PROGRESSION.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(progression_payload(4)), encoding="utf-8")
            self.assertEqual(load_enabled_capabilities(root), set())
            with self.assertRaises(AssertionError):
                require_capability("inventory_export", root=root)

    def test_l05_start_keeps_export_and_blocks_receiving(self) -> None:
        enabled = set(enabled_at_lesson_start(5))
        self.assertEqual(enabled, {"inventory_export"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "docs" / "courses" / "labs" / "baselines" / "PROGRESSION.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(progression_payload(5)), encoding="utf-8")
            require_capability("inventory_export", root=root)
            with self.assertRaises(AssertionError):
                require_capability("receiving_idempotent", root=root)

    def test_l15_start_has_static_delivery_green(self) -> None:
        enabled = set(enabled_at_lesson_start(15))
        self.assertIn("delivery_evidence", enabled)
        self.assertIn("web_api", enabled)
        self.assertIn("no_secrets", enabled)


if __name__ == "__main__":
    unittest.main()
