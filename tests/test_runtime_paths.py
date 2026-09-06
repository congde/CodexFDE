import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench.runtime_paths import service_runtime
from workbench.desktop import main


class RuntimePathsTests(unittest.TestCase):
    def test_saved_locations_survive_other_working_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / '.runtime').mkdir()
            for name in ['workbench', 'flowerp']:
                target = root / name
                target.mkdir()
                (target / (name + '.db')).touch()
            (root / '.runtime/services.json').write_text(json.dumps({
                'workbench': 'workbench', 'flowerp': 'flowerp'}))
            with patch('workbench.desktop.ROOT', root), patch('workbench.desktop.launch') as launch:
                launch.side_effect = [{'url': 'http://localhost:8001'}, {'url': 'http://localhost:8000'}]
                self.assertEqual(0, main([]))
                self.assertEqual(root / 'workbench', launch.call_args_list[0].args[0])
                self.assertEqual(root / 'flowerp', launch.call_args_list[1].args[0])

    def test_missing_pinned_database_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / '.runtime').mkdir()
            (root / '.runtime/workbench.db').touch()
            (root / '.runtime/services.json').write_text('{"workbench":"missing"}')
            with self.assertRaisesRegex(ValueError, '不会自动创建空库'):
                service_runtime('workbench', root=root)
            self.assertFalse((root / 'missing').exists())

    def test_invalid_configuration_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / '.runtime').mkdir()
            for payload in ['{', '[]', '{"workbench":null}']:
                (root / '.runtime/services.json').write_text(payload)
                with self.assertRaises(ValueError):
                    service_runtime('workbench', root=root)

    def test_legacy_data_is_preserved_and_new_install_is_separate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(root / '.runtime/flowerp', service_runtime('flowerp', root=root))
            (root / '.runtime').mkdir()
            (root / '.runtime/workbench.db').touch()
            self.assertEqual(root / '.runtime', service_runtime('workbench', root=root))

    def test_explicit_directory_overrides_saved_location(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Path(d).resolve(), service_runtime('workbench', d))
