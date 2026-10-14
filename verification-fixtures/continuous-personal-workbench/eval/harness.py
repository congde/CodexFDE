"""Minimal L04 command Eval. A valid report is technical evidence, not approval."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

SCHEMA = 'l04-minimal-eval-v1'


def load_report(path):
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(report, dict) or report.get('schema') != SCHEMA:
        raise ValueError('最小 Eval 报告 schema 无效')
    checks = report.get('checks')
    if not isinstance(checks, list) or not checks:
        raise ValueError('最小 Eval 必须包含实际命令结果')
    for check in checks:
        if (not isinstance(check, dict) or not isinstance(check.get('invocation'), dict)
                or not isinstance(check['invocation'].get('argv'), list) or not check['invocation']['argv']
                or not isinstance(check['invocation'].get('cwd'), str)
                or not isinstance(check.get('stdout'), str) or not isinstance(check.get('stderr'), str)
                or not isinstance(check.get('timed_out'), bool)
                or (check.get('exitcode') is not None and type(check['exitcode']) is not int)
                or 'error' not in check):
            raise ValueError('最小 Eval 命令结果不完整')
    passed = all(c['exitcode'] == 0 and not c['timed_out'] and not c['error'] for c in checks)
    if report.get('status') != ('passed' if passed else 'failed'):
        raise ValueError('最小 Eval 报告结论与命令结果不符')
    return report


def evaluate(commands, *, cwd, timeout, report_path):
    from workbench.execution import run_command
    if not commands:
        raise ValueError('最小 Eval 命令不能为空')
    checks = []
    for command in commands:
        result = run_command(command, cwd=cwd, timeout=timeout)
        checks.append(result)
        if result['exitcode'] != 0 or result['timed_out'] or result['error']:
            break
    passed = all(c['exitcode'] == 0 and not c['timed_out'] and not c['error'] for c in checks)
    report = {'schema': SCHEMA, 'status': 'passed' if passed else 'failed', 'checks': checks}
    path = Path(report_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    loaded = load_report(path)  # Consume the saved report, never a model's summary.
    return {**loaded, 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


# Current personal registry. Later lessons append their own frozen checks.
def _receiving():
    from .cases import receiving_is_idempotent
    return receiving_is_idempotent()

def _personal_contract():
    from .cases import tests
    return tests('tests.test_report_contract')

EVALS = [('personal_harness_contract','blocking',_personal_contract)]
PROJECT_CASES = {'receiving_is_idempotent': _receiving}

from .ci_business_gate import check as _customer_atomic
EVALS.append(('sales_credit_and_atomic_reservation','blocking',_customer_atomic))

def main(argv=None):
    import argparse
    from .runner import run
    parser=argparse.ArgumentParser(description='个人 Harness：自动结论仍待人审')
    parser.add_argument('--suite',choices=('all','blocking','observing'),default='all')
    parser.add_argument('--case',dest='cases',action='append')
    parser.add_argument('--report-path')
    parser.add_argument('--no-report',action='store_true')
    args=parser.parse_args(argv)
    path=None if args.no_report else (Path(args.report_path) if args.report_path else
         Path(__file__).resolve().parents[1]/'.runtime'/('harness-'+uuid4().hex+'.json'))
    try:
        registry=EVALS + [(name,'blocking',fn) for name,fn in PROJECT_CASES.items() if args.cases and name in args.cases]
        report,code=run(registry,suite=args.suite,names=args.cases,report_path=path)
        print(json.dumps(report,ensure_ascii=False))
        return code
    except (ValueError,OSError) as error:
        print(json.dumps({'status':'error','message':str(error),'human_review':'pending_human_review'},ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
