"""Prepare and verify the L06 personal stock practice. Never edits the control repository."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from workbench.course_experiments import copy_experiment_sources
HERE=Path(__file__).resolve().parent
L05=('tests/test_l05_receiving.py','tests/test_l05_scope.py','eval/l05_scope.py')
ALLOWED=('eval/l06_runner.py','flowerp/service.py')
NAMES=('l06_stock_consistency','l05_personal_receiving','l05_personal_scope','help_image')


def read(path):return json.loads(Path(path).read_text('utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
def inventory(candidate):
    return {p.relative_to(candidate).as_posix():sha(p) for p in sorted(candidate.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
def save(path,value):
    with Path(path).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def session(path):
    s=read(path);s['candidate']=Path(s['candidate']).resolve();return s


def prepare(run,l05_session):
    old=read(l05_session);submission=read(Path(old['run'])/'submission.json')
    source=Path(submission['isolation']['path']).resolve();frozen=read(Path(old['run'])/'frozen-checks.json')['files']
    for n in L05:
        if sha(source/n)!=frozen[n]:raise ValueError('L05 original check differs from frozen version: '+n)
    run=run.resolve()
    if run.exists():raise FileExistsError('Use a new run directory; resume from existing session.json')
    run.mkdir(parents=True);candidate=run/'candidate';candidate.mkdir()
    copy_experiment_sources(candidate)
    (candidate/'tests').mkdir();(candidate/'tests/__init__.py').write_text('',encoding='utf8')
    for name in L05:
        (candidate/name).parent.mkdir(exist_ok=True);shutil.copy2(source/name,candidate/name)
    for origin,target in [('runner_starter.py','eval/l06_runner.py'),
                          ('checks_starter.py','eval/l06_checks.py'),
                          ('test_runner_contract.py','tests/test_l06_runner.py')]:
        (candidate/target).write_text((HERE/origin).read_text('utf8'),'utf8')
    service=candidate/'flowerp/service.py'
    code=service.read_text('utf8')
    start=code.index('    def export_inventory(')
    end=code.index('\n    def ',start+5)
    original=code[start:end]
    if original.count("{row['available']}")!=1:
        raise ValueError('Cannot identify CSV available field; source changed, do not guess')
    changed=original.replace("{row['available']}","{row['on_hand']}")
    service.write_text(code[:start]+changed+code[end:],'utf8')
    files=inventory(candidate);save(run/'baseline.json',files)
    for n in ALLOWED:
        dest=run/'before'/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(candidate/n,dest)
    s=dict(candidate=str(candidate),run=str(run),control=str(ROOT),l05_session=str(l05_session.resolve()),
           l05_task=submission['task']['id'],l05_source=str(source),l05_hashes={n:sha(source/n) for n in L05},
           allowed=list(ALLOWED),status='prepared',scope='local personal practice; not a course-submit or human acceptance')
    save(run/'session.json',s);print(json.dumps(s,ensure_ascii=False,indent=2))


def verify(s):
    before=read(Path(s['run'])/'baseline.json');after=inventory(s['candidate'])
    changed=[n for n in sorted(before.keys()|after.keys()) if before.get(n)!=after.get(n)]
    unexpected=[n for n in changed if n not in ALLOWED]
    if unexpected:raise ValueError('Frozen files changed or unexpected files: '+repr(unexpected))
    return dict(changed=changed,frozen_unchanged=True,candidate=str(s['candidate']))


def execute(s,report,opening=8,reserved=3,contract=False):
    verify(s);report=report.resolve()
    if report.is_relative_to(s['candidate']):raise ValueError('Reports must be outside candidate')
    receipt=report.with_suffix('.receipt.json')
    if report.exists() or receipt.exists():raise FileExistsError('Use a new report path')
    report.parent.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,'-B','-X','utf8','-m']
    command+=(['unittest','tests.test_l06_runner','-v'] if contract else
             ['eval.l06_checks','--opening',str(opening),'--reserved',str(reserved),'--report-path',str(report)])
    hashes=inventory(s['candidate'])
    result=subprocess.run(command,cwd=s['candidate'],capture_output=True,text=True,encoding='utf8',timeout=120)
    record=dict(command=command,cwd=str(s['candidate']),exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr,
                time_utc=datetime.now(timezone.utc).isoformat(),files=hashes,kind='contract' if contract else 'business',
                report_path=str(report),report_sha256=sha(report) if report.exists() else None,
                unchanged_during_run=inventory(s['candidate'])==hashes)
    save(receipt,record)
    print(result.stdout);print(result.stderr,file=sys.stderr);print('receipt: '+str(receipt))
    if not record['unchanged_during_run']:return 2
    return result.returncode


def review(s,report):
    verify(s);report=report.resolve();record=read(report.with_suffix('.receipt.json'))
    if record['cwd']!=str(s['candidate']) or record['report_path']!=str(report):raise ValueError('Wrong candidate or report path')
    if record['files']!=inventory(s['candidate']):raise ValueError('Report does not describe current candidate files')
    if not record['unchanged_during_run'] or record['report_sha256']!=sha(report):raise ValueError('Candidate or report changed')
    sys.path.insert(0,str(ROOT))
    from eval.report_contract import validate_report
    validate_report(read(report),NAMES,record['exit_code'],'all')
    # All mandatory business checks must retain their frozen blocking levels.
    levels={r['name']:r['level'] for r in read(report)['results']}
    if levels!={n:('observing' if n=='help_image' else 'blocking') for n in NAMES}:raise ValueError('Required levels changed')
    print(json.dumps(dict(report_consistent=True,business_decision=read(report)['summary']['decision']),ensure_ascii=False))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('prepare');a.add_argument('--run',type=Path,required=True);a.add_argument('--l05-session',type=Path,required=True)
    for name in ['run','contract','review','verify']:
        a=sub.add_parser(name);a.add_argument('--session',type=Path,required=True)
        if name!='verify':a.add_argument('--report',type=Path,required=True)
        if name=='run':
            a.add_argument('--opening',type=int,default=8);a.add_argument('--reserved',type=int,default=3)
    a=p.parse_args()
    try:
        if a.action=='prepare':prepare(a.run,a.l05_session);return 0
        s=session(a.session)
        if a.action=='verify':print(json.dumps(verify(s),ensure_ascii=False,indent=2));return 0
        if a.action=='review':review(s,a.report);return 0
        if a.action=='run' and not 0<a.reserved<a.opening:raise ValueError('require 0 < reserved < opening')
        return execute(s,a.report,getattr(a,'opening',8),getattr(a,'reserved',3),a.action=='contract')
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.TimeoutExpired) as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
