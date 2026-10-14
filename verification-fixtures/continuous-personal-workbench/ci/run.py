"""Observe this remote invocation. Never substitute old artifacts or zero exits."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PRODUCT=Path(os.environ['FLOWERP_PROJECT_ROOT']).resolve(strict=True)
REPORTS=Path(os.environ['CI_REPORT_DIR']).resolve()
REPORTS.mkdir(parents=True,exist_ok=False)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def git(root):return subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
def command(name,argv,cwd):
    result=subprocess.run(argv,cwd=cwd,text=True,encoding='utf8',capture_output=True,timeout=180)
    (REPORTS/(name+'-stdout.txt')).write_text(result.stdout,'utf8')
    (REPORTS/(name+'-stderr.txt')).write_text(result.stderr,'utf8')
    receipt={'argv':argv,'cwd':str(cwd),'exit_code':result.returncode}
    with (REPORTS/(name+'-process.json')).open('x',encoding='utf8') as stream:json.dump(receipt,stream,indent=2)
    print(name+' original exit='+str(result.returncode),flush=True)
    return result.returncode
def main():
    config=json.loads((ROOT/'selection.json').read_text('utf8'))
    paths={str(p.relative_to(ROOT)):sha(p) for directory in ('workbench','eval','tests','ci')
           for p in sorted((ROOT/directory).rglob('*.py'))}
    checks={p:sha(PRODUCT/p) for p in ('eval/cases.py','tests/test_atomic_eval_contract.py')}
    identity={'run_id':os.environ['GITHUB_RUN_ID'],'attempt':os.environ['GITHUB_RUN_ATTEMPT'],
              'event':os.environ['GITHUB_EVENT_NAME'],'ref':os.environ['GITHUB_REF'],
              'course_checkout':git(ROOT),'workflow_sha':os.environ['GITHUB_SHA'],
              'product_checkout':git(PRODUCT),'controller_files':paths,'product_checks':checks,
              'evidence_kind':'actual GitHub runner environment; compare with platform API',
              'human_acceptance':'pending'}
    with (REPORTS/'source-identity.json').open('x',encoding='utf8') as stream:json.dump(identity,stream,indent=2)
    if identity['product_checkout']!=config['product_sha']:raise RuntimeError('product checkout differs from frozen SHA')
    customer=PRODUCT/'.venv/bin/python'
    if os.name=='nt':customer=PRODUCT/'.venv/Scripts/python.exe'
    own=command('personal-unit',[sys.executable,'-B','-S','-X','utf8','-m','unittest','tests.test_report_contract','-v'],ROOT)
    product=command('customer-unit',[str(customer),'-B','-X','utf8','-m','unittest','tests.test_atomic_eval_contract','-v'],PRODUCT)
    report=REPORTS/'personal-harness.json'
    if config.get('missing_report_test'):
        print('Declared missing-report fault: do not launch Harness. This run must fail.',flush=True)
        if report.exists():raise RuntimeError('unexpected reused report')
        raise RuntimeError('current report missing; no old artifact may be consumed')
    harness=command('personal-harness',[sys.executable,'-B','-S','-X','utf8','-m','eval.harness',
        '--suite','blocking','--case','personal_harness_contract','--case','sales_credit_and_atomic_reservation',
        '--report-path',str(report)],ROOT)
    if not report.is_file():raise RuntimeError('current controller report missing')
    sys.path.insert(0,str(ROOT))
    from eval.report_contract import validate_report
    data=json.loads(report.read_text('utf8'))
    validate_report(data,['personal_harness_contract','sales_credit_and_atomic_reservation'],harness,'blocking')
    envelope={**identity,'report_sha256':sha(report),'unit_exit':own,'customer_unit_exit':product,
              'harness_exit':harness,'decision':data['summary']['decision']}
    with (REPORTS/'ci-evidence.json').open('x',encoding='utf8') as stream:json.dump(envelope,stream,indent=2)
    return int(any(code!=0 for code in (own,product,harness)))
if __name__=='__main__':raise SystemExit(main())
