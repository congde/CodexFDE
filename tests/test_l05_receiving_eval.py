"""Validate the L05 teaching Eval against isolated, explicit service defects."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'docs/courses/L05/examples/receiving_contract_eval.py'


class ReceivingEvalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='l05-eval-test-')
        self.addCleanup(self.temp.cleanup)
        self.candidate = Path(self.temp.name) / 'candidate'
        shutil.copytree(ROOT / 'flowerp', self.candidate / 'flowerp',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        self.source = self.candidate / 'flowerp/service.py'

    def change(self, before, after):
        source = self.source.read_text(encoding='utf-8')
        self.assertEqual(source.count(before), 1, 'service changed; inspect fixture before adapting')
        self.source.write_text(source.replace(before, after), encoding='utf-8')

    def run_check(self, *args, code=1):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(CHECK),
                                 '--candidate', str(self.candidate), *args],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_normal_and_changed_numbers(self):
        self.assertEqual(self.run_check(code=0)['failures'], [])
        self.assertEqual(self.run_check('--opening', '11', '--quantity', '3', code=0)['failures'], [])

    def test_first_receipt_wrong_quantity_is_not_a_valid_replay_reference(self):
        self.change('(event_key, sku, quantity, 0, "receive", reference),',
                    '(event_key, sku, quantity + 1, 0, "receive", reference),')
        self.run_check('--return-only', code=0)
        failures = self.run_check()['failures']
        first = next(f for f in failures if f['requirement'] == 'AC-FIRST')
        self.assertEqual(first['expected']['events'][1]['quantity'], 8)
        self.assertEqual(first['actual']['events'][1]['quantity'], 9)
        self.assertIn('AC-NEW', [f['requirement'] for f in failures])

    def test_replay_content_change_with_same_count_has_diagnostic_states(self):
        self.change('            if exists:\n                row = conn.execute(',
                    '            if exists:\n'
                    '                conn.execute("UPDATE inventory_events SET reference=? WHERE event_key=?", ("corrupt", event_key))\n'
                    '                row = conn.execute(')
        self.run_check('--return-only', code=0)
        results = [self.run_check() for _ in range(2)]
        for result in results:
            failure = next(f for f in result['failures'] if f['requirement'] == 'AC-REPLAY')
            self.assertEqual(len(failure['actual']['events']), 2)
            self.assertEqual(failure['expected']['events'][1]['reference'], 'manual')
            self.assertEqual(failure['actual']['events'][1]['reference'], 'corrupt')
        self.assertEqual([f['requirement'] for f in results[0]['failures']],
                         [f['requirement'] for f in results[1]['failures']])

    def test_silently_ignored_new_receipt_is_detected(self):
        self.change('        sku = sku.upper()\n        with self.store.connect() as conn:\n',
                    '        if event_key == "receipt-B":\n'
                    '            return self.product(sku)\n'
                    '        sku = sku.upper()\n        with self.store.connect() as conn:\n')
        failures = self.run_check()['failures']
        self.assertEqual([f['requirement'] for f in failures], ['AC-NEW'])
        self.assertEqual(failures[0]['actual']['stock']['on_hand'], 28)

    def test_rejection_after_persistent_write_shows_before_and_after(self):
        self.change('        if quantity <= 0:\n            raise ValueError("入库数量必须大于 0")',
                    '        if quantity <= 0:\n'
                    '            with self.store.connect() as conn:\n'
                    '                conn.execute("UPDATE inventory_events SET reference=? WHERE event_key=?", (str(quantity), "receipt-A"))\n'
                    '            raise ValueError("入库数量必须大于 0")')
        failures = self.run_check()['failures']
        self.assertEqual([f['requirement'] for f in failures], ['AC-UNCHANGED', 'AC-UNCHANGED'])
        self.assertEqual(failures[0]['expected']['state']['events'][1]['reference'], 'manual')
        self.assertEqual(failures[0]['actual']['state']['events'][1]['reference'], '0')


if __name__ == '__main__':
    unittest.main()
