from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from workbench.course_snapshot import prepare_source_snapshot


ROOT = Path(__file__).resolve().parents[1]


class SourceSnapshotTests(unittest.TestCase):
    def test_external_product_is_copied_only_to_candidate_with_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'controller'; source.mkdir()
            self.source(source)
            product = Path(directory) / 'product'
            for name, content in {'flowerp/__init__.py': '', 'flowerp/server.py': '# server',
                                  'flowerp/service.py': '# unchanged product', 'eval/cases.py': '# product checks',
                                  'web/index.html': '<title>ERP</title>', 'flowerp/auth.json': 'private'}.items():
                path = product / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
            with patch('workbench.external_project.flowerp_root', return_value=product):
                result = prepare_source_snapshot(source, source / '.runtime', 5)
            candidate = Path(result['path'])
            self.assertFalse((source / 'flowerp').exists())
            self.assertFalse((candidate / 'flowerp/auth.json').exists())
            self.assertEqual('# product checks', (candidate / 'eval/erp_cases.py').read_text(encoding='utf-8'))
            self.assertEqual('# uncommitted source\n', (candidate / 'eval/harness.py').read_text(encoding='utf-8'))
            self.assertEqual(str(product), result['external_product_source']['root'])
            self.assertIn('flowerp/service.py', result['external_product_source']['manifest'])
            self.assertEqual('# unchanged product', (product / 'flowerp/service.py').read_text(encoding='utf-8'))

    def source(self, root):
        for name in ("workbench/__init__.py", "workbench/cli.py", "eval/__init__.py", "eval/harness.py"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# uncommitted source\n", encoding="utf-8")
        (root / "workbench/spec.py").write_text((ROOT / "workbench/spec.py").read_text(encoding="utf-8"), encoding="utf-8")

    def test_snapshot_uses_current_files_without_publishing_tags_or_touching_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"; source.mkdir()
            self.source(source)
            (source / ".env").write_text("private", encoding="utf-8")
            (source / "workbench/private.db").write_text("private", encoding="utf-8")
            (source / "workbench/auth.json").write_text("private", encoding="utf-8")
            original = (source / "workbench/spec.py").read_bytes()
            result = prepare_source_snapshot(source, source / ".runtime", 3)
            target = Path(result["path"])
            self.assertEqual("working_tree_snapshot", result["baseline_semantics"])
            self.assertFalse((source / ".git").exists())
            self.assertEqual(original, (source / "workbench/spec.py").read_bytes())
            self.assertIn("if False and missing", (target / "workbench/spec.py").read_text(encoding="utf-8"))
            self.assertFalse((target / ".env").exists())
            self.assertFalse((target / "workbench/private.db").exists())
            self.assertFalse((target / "workbench/auth.json").exists())
            self.assertEqual("# uncommitted source\n", (target / "workbench/cli.py").read_text(encoding="utf-8"))
            check = subprocess.run(["git", "status", "--porcelain"], cwd=target, capture_output=True, text=True)
            self.assertEqual(0, check.returncode)
            self.assertEqual("", check.stdout.strip())
            evidence = source / ".runtime/course-worktrees/TASK-PREP-L03.json"
            self.assertEqual(result["source_tree_sha256"], json.loads(evidence.read_text(encoding="utf-8"))["source_tree_sha256"])
            with self.assertRaises(FileExistsError):
                prepare_source_snapshot(source, source / ".runtime", 3)

    def test_invalid_id_and_incomplete_source_are_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for task_id in ("TASK-../outside", "TASK-x:stream", "TASK-x/y"):
                with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                    prepare_source_snapshot(root, root / ".runtime", 1, task_id)
            with self.assertRaisesRegex(ValueError, "源文件不完整"):
                prepare_source_snapshot(root, root / ".runtime", 1)
            self.assertFalse((root / ".runtime").exists())


if __name__ == "__main__":
    unittest.main()
