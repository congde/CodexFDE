"""Real local pipe behavior; does not contact or impersonate Codex."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

from workbench.execution import CodexExecutionRunner


class ExecutionStreamTests(unittest.TestCase):
    def test_chinese_prompt_survives_the_process_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = CodexExecutionRunner(root, root)
            prompt = '按需求导出库存，不修改采购。\n第二行'
            result = runner._run_codex_streaming(
                [sys.executable, '-X', 'utf8', '-c', 'import sys;sys.stdout.write(sys.stdin.read())'],
                prompt, 3, lambda line: None, time.monotonic())
            self.assertEqual(0, result.returncode)
            self.assertEqual(prompt, result.stdout)

    def invoke(self, code, timeout=3):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = CodexExecutionRunner(root, root)
            lines = []
            start = time.monotonic()
            result = runner._run_codex_streaming(
                [sys.executable, '-u', '-c', code], '', timeout, lines.append, start)
            return result, lines, time.monotonic() - start

    def test_silent_process_cannot_bypass_timeout(self):
        result, _, elapsed = self.invoke('import time; time.sleep(30)', timeout=.3)
        self.assertEqual(124, result.returncode)
        self.assertLess(elapsed, 5)

    def test_partial_line_cannot_bypass_timeout_and_is_preserved(self):
        result, _, elapsed = self.invoke("import sys,time;sys.stdout.write('partial');sys.stdout.flush();time.sleep(30)", timeout=.5)
        self.assertEqual(124, result.returncode)
        self.assertIn('partial', result.stdout)
        self.assertLess(elapsed, 5)

    def test_full_stderr_is_drained_while_stdout_events_are_delivered(self):
        result, lines, _ = self.invoke("import sys;sys.stderr.write('x'*200000);sys.stderr.flush();print('done')")
        self.assertEqual(0, result.returncode)
        self.assertEqual(200000, len(result.stderr))
        self.assertEqual(['done'], lines)
