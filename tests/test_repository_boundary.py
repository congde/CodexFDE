"""Controller imports, external process dispatch, and candidate failure isolation."""
import ast
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench import external_project

ROOT = Path(__file__).resolve().parents[1]


class RepositoryBoundaryTests(unittest.TestCase):
    def test_legacy_serve_preserves_saved_data_and_normalizes_explicit_paths(self):
        from workbench.cli import main
        with patch('workbench.runtime_paths.service_runtime', return_value=Path('/retained')) as runtime, \
             patch.object(external_project, 'run') as run:
            run.return_value.returncode = 0
            with patch.object(sys, 'argv', ['workbench', 'serve']):
                self.assertEqual(0, main())
            self.assertEqual(['serve', '--runtime-dir', str(Path('/retained'))], run.call_args.args[0])
            runtime.assert_called_once_with('flowerp')
            with patch.object(sys, 'argv', ['workbench', 'serve', '--runtime-dir=local-data']):
                self.assertEqual(0, main())
            self.assertIn('--runtime-dir=' + str(Path('local-data').resolve()), run.call_args.args[0])

    def test_controller_has_no_in_process_erp_imports_or_source(self):
        self.assertFalse((ROOT / 'flowerp').exists())
        self.assertFalse((ROOT / 'web').exists())
        for directory in ('workbench', 'eval', 'agent'):
            for path in (ROOT / directory).rglob('*.py'):
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                    names = ([node.module or ''] if isinstance(node, ast.ImportFrom)
                             else [n.name for n in node.names] if isinstance(node, ast.Import) else [])
                    self.assertFalse(any(n == 'flowerp' or n.startswith('flowerp.') for n in names), str(path))

    def test_external_root_and_environment_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                external_project.flowerp_root(root)
            (root / 'flowerp').mkdir()
            (root / 'flowerp/server.py').write_text('', encoding='utf-8')
            self.assertEqual(root.resolve(), external_project.flowerp_root(root))
            with self.assertRaises(ValueError):
                external_project.python_for(root)

    def test_dispatch_uses_product_interpreter_and_working_directory(self):
        with patch.object(external_project, 'flowerp_root', return_value=Path('/product')), \
             patch.object(external_project, 'python_for', return_value='product-python'), \
             patch.object(external_project.subprocess, 'run') as run:
            external_project.run(['doctor'])
            self.assertEqual(['product-python', '-X', 'utf8', '-m', 'flowerp', 'doctor'], run.call_args.args[0])
            self.assertEqual(Path('/product'), run.call_args.kwargs['cwd'])

    def test_candidate_failure_never_falls_back_to_live_product(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'flowerp').mkdir()
            (root / 'flowerp/__init__.py').write_text('', encoding='utf-8')
            (root / 'eval').mkdir()
            checks = root / 'eval/erp_cases.py'
            with patch.object(external_project.Path, 'cwd', return_value=root), \
                 patch.object(external_project, 'flowerp_root', side_effect=AssertionError('must not use live source')):
                checks.write_text("def fixture():\n    return 'candidate-only'\n", encoding='utf-8')
                self.assertIn('candidate-only', external_project.evaluate_case('fixture'))
                checks.write_text("def fixture():\n    raise AssertionError('candidate-defect')\n", encoding='utf-8')
                with self.assertRaisesRegex(AssertionError, 'candidate-defect'):
                    external_project.evaluate_case('fixture')
                (root / 'flowerp/__init__.py').unlink()
                with self.assertRaisesRegex(AssertionError, '不能回退'):
                    external_project.evaluate_case('fixture')


if __name__ == '__main__':
    unittest.main()
