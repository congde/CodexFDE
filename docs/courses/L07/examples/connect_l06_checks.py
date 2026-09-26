"""Connect verified personal L06 checks to an isolated L07 candidate, without copying product code."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from workbench.external_project import flowerp_root

spec = importlib.util.spec_from_file_location('stock_practice', ROOT/'docs/courses/L06/examples/stock_practice.py')
stock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stock)
FILES = stock.L05 + ('eval/l06_runner.py', 'eval/l06_checks.py', 'tests/test_l06_runner.py')
NAMES = ('l06_stock_consistency', 'l05_personal_receiving', 'l05_personal_scope',
         'help_image', 'l06_harness_contract', 'l07_draft_amount', 'l07_rejected_order', 'l07_amount_transfer')
BLOCK = '''# L06-L07 personal check registration; no business implementation copied.
from .l06_checks import ENTRIES as _personal_checks, harness_contract as _runner_contract
from .l07_order_checks import draft_amount, rejected_order_has_no_residue, amount_transfer
_additions = list(_personal_checks) + [
    ("l06_harness_contract", "blocking", _runner_contract),
    ("l07_draft_amount", "blocking", draft_amount),
    ("l07_rejected_order", "blocking", rejected_order_has_no_residue),
    ("l07_amount_transfer", "blocking", amount_transfer),
]
if {x[0] for x in EVALS} & {x[0] for x in _additions}:
    raise ValueError("Duplicate personal course checks")
EVALS.extend(_additions)

'''


def connect(session, report, target, receipt):
    s = stock.session(session)
    stock.review(s, report)  # includes source freshness, scope, levels and report consistency
    if stock.read(report)['summary']['decision'] != 'pass':
        raise ValueError('L06 business is still blocked; do not migrate')
    target = target.resolve(strict=True)
    source = s['candidate']
    if target in (ROOT, flowerp_root(), source) or source in target.parents or target in source.parents:
        raise ValueError('Use a separate isolated L07 candidate, not source or maintained repositories')
    receipt = receipt.resolve()
    if receipt.exists() or receipt.is_relative_to(target):
        raise ValueError('Use a new receipt outside target')
    registry = target/'eval/harness.py'
    before = registry.read_text('utf8')
    anchor = 'if __name__ == "__main__":'
    if before.count(anchor) != 1 or 'EVALS' not in before or any(n in before for n in NAMES):
        raise ValueError('Unexpected registry or already connected; inspect rather than overwrite')
    payload = {name: (source/name).read_bytes() for name in FILES}
    payload['eval/l07_order_checks.py'] = (Path(__file__).parent/'order_contract_checks.py').read_bytes()
    if not (target/'tests/__init__.py').exists():
        payload['tests/__init__.py'] = b''
    for name in payload:
        if (target/name).exists():
            raise FileExistsError(target/name)
    evidence = dict(source_session=str(Path(session).resolve()), source_report=str(Path(report).resolve()),
                    target=str(target), files={name: hashlib.sha256(data).hexdigest() for name,data in payload.items()},
                    registry_before_sha256=hashlib.sha256(before.encode()).hexdigest(), names=NAMES,
                    status='connected; execution and human acceptance pending')
    for name,data in payload.items():
        dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
    after=before.replace(anchor,BLOCK+anchor)
    registry.write_text(after,'utf8')
    evidence['registry_after_sha256']=hashlib.sha256(after.encode()).hexdigest()
    receipt.parent.mkdir(parents=True,exist_ok=True)
    with receipt.open('x',encoding='utf8') as stream:
        json.dump(evidence,stream,ensure_ascii=False,indent=2)
    return evidence


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--l06-session',type=Path,required=True)
    p.add_argument('--l06-report',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args()
    try:
        print(json.dumps(connect(a.l06_session,a.l06_report,a.candidate,a.receipt),ensure_ascii=False,indent=2))
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        p.exit(1, f'{type(exc).__name__}: {exc}\n')
