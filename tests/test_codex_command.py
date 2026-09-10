import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench.codex_command import resolve_codex_command


class CodexCommandTests(unittest.TestCase):
    def test_path_has_priority(self):
        with patch('workbench.codex_command.shutil.which', return_value='C:/cli/codex.exe'):
            self.assertEqual(resolve_codex_command(), 'C:/cli/codex.exe')

    def test_desktop_cli_without_path_and_missing_install(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {'LOCALAPPDATA': temp, 'FLOWERP_CODEX_COMMAND': ''}), \
                    patch('workbench.codex_command.shutil.which', return_value=None):
                self.assertEqual(resolve_codex_command(), 'codex')
                if os.name != 'nt':
                    return
                root = Path(temp)/'OpenAI'/'Codex'/'bin'
                older = root/'old'/'codex.exe'
                newer = root/'new'/'codex.exe'
                for file in (older, newer):
                    file.parent.mkdir(parents=True)
                    file.write_bytes(b'test fixture, never executed')
                os.utime(older, (10, 10)); os.utime(newer, (20, 20))
                self.assertEqual(resolve_codex_command(), str(newer))
                self.assertEqual(resolve_codex_command('missing-custom-cli'), 'missing-custom-cli')
                with patch.dict(os.environ, {'FLOWERP_CODEX_COMMAND': 'missing-override'}):
                    self.assertEqual(resolve_codex_command(), 'missing-override')
