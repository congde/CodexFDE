"""Run the real Loop against fresh Python processes and a copied ERP service.

The executor is a predetermined teaching patch, NOT Codex. Its positive usage
field is an explicitly synthetic protocol placeholder, NOT measured tokens.
Each run requires a new directory; existing evidence is never overwritten.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from agent.loop import run_loop
from eval.report_contract import validate_report
from workbench.course_experiments import copy_experiment_sources

CASES = ["l10_legal", "l10_draft", "l10_cancelled", "l10_repeat"]
GOOD = '            if order["status"] != OrderStatus.RESERVED:'
BAD = '            if True:  # TEACHING: incorrectly reject every shipment'


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(mode: str, directory: Path) -> dict:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    candidate = directory / "candidate"
    candidate.mkdir()
    copy_experiment_sources(candidate)
    # Keep the real Harness; register four declared business checks locally.
    business = (ROOT / "docs/courses/L10/examples/order_transition_lab.py").read_text(encoding="utf-8")
    business = business.split('if __name__ == "__main__":')[0]
    business = business.replace('sys.path.insert(0, str(Path(__file__).resolve().parents[4]))', '')
    business = business.replace('from eval import harness', '')
    business = business.replace('from workbench.course_experiments import ensure_product_process', '')
    business = business.replace('ensure_product_process(__file__)', '')
    (candidate / "shipping_checks.py").write_text(business, encoding="utf-8")
    (candidate / "eval/cases.py").write_text('# Only the declared L10 checks are registered in this candidate.\n', encoding="utf-8")
    harness = candidate / "eval/harness.py"
    code = (ROOT / "eval/harness.py").read_text(encoding="utf-8")
    start = code.index('EVALS: list[')
    end = code.index('\n\n\ndef run_suite', start)
    code = code[:start] + '''import os
from shipping_checks import check
def selected(mode):
    return lambda: check(mode, Path(os.environ["L10_STATE_DIR"]) / (mode + ".json"))
EVALS = [("l10_legal", "blocking", selected("legal")),
         ("l10_draft", "blocking", selected("draft-ship")),
         ("l10_cancelled", "blocking", selected("cancelled-ship")),
         ("l10_repeat", "blocking", selected("double-ship"))]
''' + code[end:]
    harness.write_text(code, encoding="utf-8")
    service = candidate / "flowerp/service.py"
    original = service.read_text(encoding="utf-8")
    pos = original.index('    def ship_order(')
    assert GOOD in original[pos:]
    broken = original[:pos] + original[pos:].replace(GOOD, BAD, 1)
    service.write_text(broken, encoding="utf-8")
    (directory / "injected.diff").write_text(''.join(difflib.unified_diff(
        original.splitlines(True), broken.splitlines(True), fromfile='original/service.py', tofile='candidate/service.py')), encoding="utf-8")
    checks, calls = [], []
    import os
    def evaluate(label: str) -> dict:
        folder = directory / label
        folder.mkdir()
        report_path = folder / "report.json"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', L10_STATE_DIR=str(folder / 'states'))
        command = [sys.executable, '-B', '-X', 'utf8', '-m', 'eval.harness', '--suite', 'blocking', '--report-path', str(report_path)]
        for case in CASES:
            command += ['--case', case]
        result = subprocess.run(command, cwd=candidate, env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
        raw = {'command': command, 'cwd': str(candidate), 'exit_code': result.returncode,
               'stdout': result.stdout, 'stderr': result.stderr, 'service_sha256': fingerprint(service),
               'checks_sha256': fingerprint(candidate / 'shipping_checks.py')}
        save(folder / 'process.json', raw)
        report = json.loads(report_path.read_text(encoding='utf-8'))
        validate_report(report, tuple(CASES), result.returncode)
        return {'label': label, 'report': report, **raw}
    def suite_runner(suite, write_report):
        entry = evaluate(f'check-{len(checks)+1:02}')
        checks.append(entry)
        return entry['report']
    patcher = candidate / 'teaching_patch.py'
    patcher.write_text('''from pathlib import Path
import sys
p=Path("flowerp/service.py")
s=p.read_text(encoding="utf-8")
if sys.argv[1] != "no-change":
    bad='            if True:  # TEACHING: incorrectly reject every shipment'
    good='            if order["status"] != OrderStatus.RESERVED:'
    assert s.count(bad)==1
    p.write_text(s.replace(bad,good,1),encoding="utf-8")
print("TEACHING predetermined patch; no Codex invocation; mode="+sys.argv[1])
''', encoding='utf-8')
    def executor(task, round_no, timeout):
        before = service.read_text(encoding='utf-8')
        command = [sys.executable, '-B', '-X', 'utf8', str(patcher), mode]
        result = subprocess.run(command, cwd=candidate, text=True, capture_output=True, timeout=timeout)
        after = service.read_text(encoding='utf-8')
        diff = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile='before/service.py', tofile='after/service.py'))
        (directory / f'repair-{round_no}.diff').write_text(diff, encoding='utf-8')
        entry = {'command': command, 'cwd': str(candidate), 'returncode': result.returncode,
                 'stdout': result.stdout, 'stderr': result.stderr, 'changed': before != after,
                 'source': 'predetermined teaching patch subprocess', 'usage': {'total_tokens': 1},
                 'usage_is_synthetic': True, 'real_codex': False}
        calls.append(entry)
        save(directory / f'execution-{round_no}.json', entry)
        return entry
    result = run_loop(max_rounds=1 if mode == 'last-repair' else 3, token_budget=100,
                      timeout_seconds=120, use_codex=True, suite_runner=suite_runner,
                      executor=executor, runtime_dir=directory / 'tasks')
    save(directory / 'loop-result.json', result)
    # Deliberately outside the Loop: establish what its final unverified edit did.
    audit = evaluate('after-loop-audit')
    expected = {'repair': ('converged', 2), 'no-change': ('stopped_no_progress', 2),
                'last-repair': ('stopped_max_rounds', 1)}[mode]
    assert (result['status'], len(checks)) == expected
    assert len(calls) == 1
    assert audit['exit_code'] == (1 if mode == 'no-change' else 0)
    assert len({entry['checks_sha256'] for entry in checks + [audit]}) == 1
    summary = {'mode': mode, 'status': result['status'], 'loop_checks': len(checks),
               'executions': len(calls), 'outside_audit_exit': audit['exit_code'],
               'remaining_failures_from_loop': result['remaining_failures'],
               'checks_unchanged': True, 'real_codex': False, 'usage_is_synthetic': True,
               'boundary': 'Real copied ERP, real Harness and Loop, predetermined code repair. Outside audit is not a Loop round or named acceptance.'}
    save(directory / 'index.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['repair', 'no-change', 'last-repair'])
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.mode, Path(args.run_dir)), ensure_ascii=False, indent=2))
