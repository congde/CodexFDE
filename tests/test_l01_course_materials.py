from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LESSON = ROOT / "docs/courses/L01"
spec = importlib.util.spec_from_file_location("l01_evidence", LESSON / "tools/evidence.py")
capture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture_module)


class L01CourseMaterialsTests(unittest.TestCase):
    def test_all_l01_materials_are_colocated(self):
        for name in ("README.md", "阅读讲义.md", "实践操作手册.md", "SUBMISSION.md", "行动卡.md", "教师备课说明.md", "prompts", "slides", "assets", "tools"):
            self.assertTrue((LESSON / name).exists(), name)
        self.assertFalse((ROOT / "docs/courses/labs/L01").exists())
        self.assertFalse(list((ROOT / "docs/courses/tasks").glob("L01-*.md")))
        self.assertFalse(list((ROOT / "docs/courses/slides").glob("L01-*.pptx")))
        self.assertFalse(list((ROOT / "docs/courses").glob("L01-*.md")))

    def test_capture_keeps_failures_and_real_command_results(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            failed, code = capture_module.capture(root, "red", [sys.executable, "-c", "import sys; print('actual failure'); sys.exit(3)"])
            self.assertEqual(3, code)
            original = (failed / "output.txt").read_bytes()
            passed, code = capture_module.capture(root, "green", [sys.executable, "-c", "print('actual success')"])
            self.assertEqual(0, code)
            self.assertNotEqual(passed, failed)
            self.assertEqual(original, (failed / "output.txt").read_bytes())
            meta = json.loads((failed / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(3, meta["returncode"])
            self.assertTrue(meta["observed_at"].endswith("+00:00"))
            self.assertIn(b"actual failure", original)

    def test_missing_executable_is_retained_as_failure(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            record, code = capture_module.capture(Path(temporary), "observation", ["l01-nonexistent-executable-72bad2"])
            self.assertEqual(127, code)
            self.assertTrue((record / "output.txt").read_bytes())

    def test_import_rejects_changed_output_before_calling_workbench(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            record, _ = capture_module.capture(Path(temporary), "red", [sys.executable, "-c", "raise SystemExit(1)"])
            (record / "output.txt").write_text("changed", encoding="utf-8")
            result = subprocess.run([sys.executable, "-X", "utf8", str(LESSON / "tools/import_evidence.py"), str(record / "meta.json")], cwd=temporary, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("摘要不一致", result.stderr.decode("utf-8"))
            self.assertFalse((Path(temporary) / ".runtime").exists())


if __name__ == "__main__":
    unittest.main()
