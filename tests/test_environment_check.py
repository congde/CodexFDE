"""Installation checks must not hide missing or cross-repository packages."""
import json
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench import environment_check as env


class EnvironmentCheckTests(unittest.TestCase):
    def test_workbench_check_does_not_require_customer(self):
        with patch.object(env.external_project, 'flowerp_root', side_effect=AssertionError('customer must be optional')):
            result = env.check_environment()
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['product_checked'])

    def test_missing_customer_returns_actionable_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            result = env.check_environment(product_root=directory)
        self.assertFalse(result['ok'])
        self.assertEqual('flowerp', result['checks'][-1]['name'])
        self.assertIn('独立客户仓库', result['checks'][-1]['detail'])

    def test_customer_import_failure_and_timeout_are_not_green(self):
        with patch.object(env.external_project, 'flowerp_root', return_value=Path('/product')), \
             patch.object(env.external_project, 'python_for', return_value='product-python'), \
             patch.object(env.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 1, '', "ModuleNotFoundError: flowerp")
            result = env.check_environment(product=True)
            self.assertFalse(result['ok'])
            self.assertEqual('product-python', run.call_args.args[0][0])
            self.assertEqual(Path('/product'), run.call_args.kwargs['cwd'])
            run.side_effect = subprocess.TimeoutExpired('product-python', 30)
            self.assertFalse(env.check_environment(product=True)['ok'])

    def test_cli_reports_nonzero_for_invalid_customer(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'workbench.cli',
                                     'environment-check', '--product-root', directory],
                                    cwd=env.ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertFalse(json.loads(result.stdout)['ok'])

    def test_l00_stdlib_self_checks_run_without_project_source(self):
        directory = env.ROOT / 'docs/courses/L00'
        preparation = next(directory.glob('L00*.md')).read_text(encoding='utf-8')
        # The current L00 acquires a reference environment, then hands off to
        # either a supplied empty shell or an independently constructed shell.
        # A retired sentence about avoiding all cloning is not the contract.
        self.assertIn('../L01/实践操作手册.md#provided-shell', preparation)
        self.assertIn('../L01/实践操作手册.md#continue-shell', preparation)
        target = re.search(r'\[[^\]]+\]\(([^)]*从0开始搭建个人AI研发工作台\.md)\)', preparation)
        self.assertIsNotNone(target, '准备手册必须交接到真实建设步骤')
        lesson = (directory / target.group(1)).read_text(encoding='utf-8')
        for language, interpreter in (
            ('powershell', '& $coursePython'),
            ('zsh', '"$coursePython"'),
        ):
            with self.subTest(platform=language):
                blocks = re.findall(rf'```{language}\n(.*?)```', lesson, re.DOTALL)
                checks = [line for block in blocks for line in block.splitlines()
                          if 'sqlite3.sqlite_version' in line]
                self.assertEqual(1, len(checks), '每个平台须有一条独立环境自检')
                command = re.fullmatch(re.escape(interpreter) + r' -X utf8 -c "([^"\n]+)"', checks[0].strip())
                self.assertIsNotNone(command, '自检须调用已核对的课程虚拟环境')
                source = command.group(1)
                self.assertNotIn('workbench', source)
                self.assertNotIn('flowerp', source)
                # Windows CI executes both documented Python bodies in empty directories;
                # this checks their portable standard-library behavior, not a real Mac shell.
                with tempfile.TemporaryDirectory() as temporary:
                    self.assertEqual([], list(Path(temporary).iterdir()))
                    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', source], cwd=temporary,
                                            capture_output=True, text=True, encoding='utf-8', timeout=30)
                    self.assertEqual([], list(Path(temporary).iterdir()), '环境自检不应生成源码或业务数据')
                self.assertEqual(0, result.returncode, result.stderr)
                output = result.stdout.splitlines()
                self.assertEqual(3, len(output))
                self.assertTrue(output[0].startswith(sys.version.split()[0]), result.stdout)
                self.assertEqual(Path(sys.executable).resolve(), Path(output[1]).resolve())
                self.assertRegex(output[2], r'^SQLite: \d+\.\d+\.\d+$')

    def test_workspace_commands_use_each_platform_environment(self):
        workspace = json.loads((env.ROOT / 'docs/courses/FlowERP-AI研发工作台.code-workspace').read_text(encoding='utf-8'))
        for task in workspace['tasks']['tasks']:
            with self.subTest(task=task['label']):
                self.assertEqual('${workspaceFolder}/.venv/bin/python', task['command'])
                self.assertEqual('${workspaceFolder}/.venv/Scripts/python.exe', task['windows']['command'])
                args = task['args']
                module = args[args.index('-m') + 1]
                self.assertIsNotNone(importlib.util.find_spec(module), module)
