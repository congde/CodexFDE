"""CI-specific explicit process boundary; records observed child exit and report."""
import hashlib,json,os,subprocess,sys
from pathlib import Path

def check():
    product=Path(os.environ['FLOWERP_PROJECT_ROOT']).resolve(strict=True)
    interpreter=product/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    report_dir=Path(os.environ['CI_REPORT_DIR']).resolve()
    report_dir.mkdir(parents=True,exist_ok=True)
    report=report_dir/'product-harness.json'
    command=[str(interpreter),'-B','-X','utf8','-m','eval.harness','--suite','blocking',
             '--case','sales_credit_and_atomic_reservation','--report-path',str(report)]
    result=subprocess.run(command,cwd=product,text=True,encoding='utf8',capture_output=True,timeout=120)
    (report_dir/'product-stdout.txt').write_text(result.stdout,'utf8')
    (report_dir/'product-stderr.txt').write_text(result.stderr,'utf8')
    receipt={'argv':command,'cwd':str(product),'exit_code':result.returncode,
             'report':str(report),'report_sha256':hashlib.sha256(report.read_bytes()).hexdigest() if report.is_file() else None}
    with (report_dir/'product-process.json').open('x',encoding='utf8') as stream:json.dump(receipt,stream,indent=2)
    if not report.is_file():raise RuntimeError('current customer report missing; original exit '+str(result.returncode))
    data=json.loads(report.read_text('utf8'))
    # The customer's own report is checked independently before consuming its color.
    from eval.report_contract import validate_report
    validate_report(data,['sales_credit_and_atomic_reservation'],result.returncode,'blocking')
    if result.returncode:
        raise AssertionError('customer gate blocks: '+json.dumps(data['results'],ensure_ascii=False))
    return 'two-line normal, shortage and second-write rollback passed in independent customer process'
