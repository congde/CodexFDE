from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workbench.spec import REQUIRED_SECTIONS


class SpecCliTests(unittest.TestCase):
    def test_spec_process_output_and_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            for case in ("complete", "missing_section", "missing_file"):
                with self.subTest(case=case):
                    sections = REQUIRED_SECTIONS if case == "complete" else tuple(
                        name for name in REQUIRED_SECTIONS if name != "非目标"
                    )
                    source = "\n\n".join(f"## {name}\ncontent" for name in sections)
                    path.write_text(source, encoding="utf-8")
                    if case == "missing_file":
                        path.unlink()
                    result = subprocess.run(
                        [sys.executable, "-X", "utf8", "-m", "workbench.cli", "spec", str(path)],
                        cwd=Path(__file__).resolve().parents[1],
                        capture_output=True, text=True, encoding="utf-8",
                    )
                    if case == "complete":
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(len(json.loads(result.stdout)), 6)
                        self.assertEqual(result.stderr, "")
                    else:
                        self.assertEqual(result.returncode, 1)
                        self.assertEqual(result.stdout, "")
                        self.assertIn("Spec 校验失败：", result.stderr)
                        self.assertNotIn("Traceback", result.stderr)
                        if case == "missing_section":
                            self.assertIn("Spec 缺少必要章节：非目标", result.stderr)
                    if path.exists():
                        self.assertEqual(path.read_text(encoding="utf-8"), source)
