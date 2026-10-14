"""Real subprocess checks against temporary, explicitly synthetic projects."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workbench.eval_harness import report_view, validate_project_report
from workbench.project_delivery import CandidateProjectEval


def report(block=False, warning=False):
    return {'summary': {'total': 2, 'passed': 2-int(block)-int(warning),
        'blocking_failed': int(block), 'observing_failed': int(warning),
        'decision': 'block' if block else 'pass'}, 'results': [
        {'name': 'business', 'level': 'blocking', 'passed': not block, 'duration_ms': 1, 'evidence': 'fixture'},
        {'name': 'help', 'level': 'observing', 'passed': not warning, 'duration_ms': 2, 'evidence': 'fixture'}]}


class EvalHarnessTests(unittest.TestCase):
    def test_grades_and_false_green(self):
        validate_project_report(report(warning=True), 0)
        validate_project_report(report(block=True), 1)
        bad = report(block=True)
        bad['summary'].update(blocking_failed=0, decision='pass')
        with self.assertRaises(RuntimeError):
            validate_project_report(bad, 0)
        for mutate in (lambda r: r.update(results=[]),
                       lambda r: r['results'].append(copy.deepcopy(r['results'][0])),
                       lambda r: r['results'][0].update(passed='true'),
                       lambda r: r['summary'].update(passed=0)):
            bad = report()
            mutate(bad)
            with self.assertRaises(RuntimeError):
                validate_project_report(bad, 0)

    def test_subprocess_history_freshness_and_mutating_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            root.mkdir()
            runtime = Path(temp) / 'runtime'
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            script = root / 'fixture_eval.py'
            script.write_text('import json\nprint(' + repr(json.dumps(report(warning=True))) + ')', encoding='utf-8')
            runner = CandidateProjectEval(root, runtime, 'TASK-test', [sys.executable, '-B', 'fixture_eval.py'], 'test')
            first, second = runner(), runner()
            self.assertNotEqual(first['runner']['report_path'], second['runner']['report_path'])
            self.assertEqual('current', report_view(first, root, runtime)['freshness'])
            Path(first['runner']['report_path']).write_text('{}')
            self.assertEqual('stale', report_view(first, root, runtime)['freshness'])
            script.write_text(script.read_text()+'\nfrom pathlib import Path\nPath("new.py").write_text("changed")')
            self.assertEqual('stale', report_view(second, root, runtime)['freshness'])
            with self.assertRaisesRegex(RuntimeError, '候选源码变化'):
                runner()
            self.assertEqual(3, len(list((runtime / 'project-reports').rglob('process.json'))))
            git_receipts = list((runtime / 'git-processes').rglob('process.json'))
            self.assertTrue(git_receipts, 'source enumeration must retain real Git receipts')
            for path in git_receipts:
                receipt = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                 receipt['command'])
                self.assertEqual(0, receipt['returncode'])
                self.assertTrue(receipt['success'])


if __name__ == '__main__':
    unittest.main()
