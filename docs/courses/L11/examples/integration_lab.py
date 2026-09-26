"""Compare partial and complete checks on one copied purchase candidate.

Teacher injection and restoration, sequential processes, no agents or model use.
Every run needs a fresh directory. Reports and state snapshots are preserved.
"""
from __future__ import annotations
import argparse, ast, difflib, hashlib, json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from eval.report_contract import validate_report
from workbench.course_experiments import copy_experiment_sources

CHECKS = '''import json, os, sqlite3, tempfile
from pathlib import Path
from flowerp import ERPService, ERPStore
from flowerp.models import NotFound, OrderLine
def require_capability(name): pass  # isolated demonstration, not course gate
def _service():
    tmp=tempfile.TemporaryDirectory(prefix="l11-field-")
    service=ERPService(ERPStore(Path(tmp.name)/"eval.db"))
    service.add_product("SKU-A","验收商品",1000,2)
    return tmp,service

def complete(mode):
    with tempfile.TemporaryDirectory(prefix="l11-state-") as folder:
        store=ERPStore(Path(folder)/"eval.db"); s=ERPService(store)
        s.add_product("A","商品 A",100)
        s.receive_stock("A",10,"opening")
        s.create_order("其他客户",[OrderLine("A",2,100)],"OTHER"); s.reserve_order("OTHER")
        # Existing unrelated application is fixed fixture data, not the action under test.
        with store.connect() as conn:
            conn.execute("INSERT INTO purchase_requests(id,sku,quantity,status,reason) VALUES(?,?,?,?,?)",("PR-OTHER","A",3,"proposed","另一项需求"))
            if mode=="duplicate-id":
                conn.execute("INSERT INTO purchase_requests(id,sku,quantity,status,reason) VALUES(?,?,?,?,?)",("PR-TARGET","A",7,"proposed","低于补货点"))
        tables=("purchase_requests","stock","inventory_events","sales_orders","sales_order_lines")
        def snapshot(): return {t:store.rows(f"SELECT * FROM {t} ORDER BY 1") for t in tables}
        before=snapshot(); observed=None
        try:
            quantity={"zero":0,"negative":-1}.get(mode,7)
            reason="   " if mode=="blank-reason" else "  低于补货点  "
            sku="UNKNOWN" if mode=="unknown-sku" else "a"
            expected={"zero":ValueError,"negative":ValueError,"blank-reason":ValueError,"unknown-sku":NotFound,"duplicate-id":sqlite3.IntegrityError}.get(mode)
            try: result=s.propose_purchase(sku,quantity,reason,"PR-TARGET")
            except Exception as exc:
                observed=type(exc).__name__
                if expected is None or not isinstance(exc,expected): raise
            else:
                assert expected is None,"invalid request accepted"
                assert (result["id"],result["sku"],result["quantity"],result["reason"],result["status"],result["approved_by"])==("PR-TARGET","A",7,"低于补货点","proposed",None)
                assert s.purchase("PR-TARGET")==result
            after=snapshot()
            if expected: assert after==before,"rejection changed persisted state"
            else:
                assert all(after[t]==before[t] for t in tables if t!="purchase_requests"),"application changed stock, events, or orders"
                assert [p for p in after["purchase_requests"] if p["id"]!="PR-TARGET"]==before["purchase_requests"]
                assert len(after["purchase_requests"])==len(before["purchase_requests"])+1
            return mode+": independent state expectations passed"
        finally:
            target=Path(os.environ["L11_STATES"])/(mode+".json"); target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("x",encoding="utf-8") as stream:
                json.dump({"mode":mode,"before":before,"after":snapshot(),"exception":observed},stream,ensure_ascii=False,indent=2)
'''

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream: json.dump(data, stream, ensure_ascii=False, indent=2)

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def run(directory):
    directory=directory.resolve(); directory.mkdir(parents=True, exist_ok=False)
    candidate=directory/'candidate'; candidate.mkdir()
    origins = copy_experiment_sources(candidate)
    source=(Path(origins['product_root'])/'eval/cases.py').read_text(encoding='utf-8')
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='purchase_request_preserves_reason')
    field_check=ast.get_source_segment(source,node)
    (candidate/'purchase_checks.py').write_text(CHECKS+'\n'+field_check+'\n',encoding='utf-8')
    (candidate/'eval/cases.py').write_text('# Isolated declared checks only.\n',encoding='utf-8')
    code=(ROOT/'eval/harness.py').read_text(encoding='utf-8'); start=code.index('EVALS: list['); end=code.index('\n\n\ndef run_suite',start)
    modes=('normal','zero','negative','blank-reason','unknown-sku','duplicate-id')
    registry='from purchase_checks import complete, purchase_request_preserves_reason\nEVALS = [("purchase_request_preserves_reason","blocking",purchase_request_preserves_reason)] + [("l11_"+m,"blocking",lambda m=m: complete(m)) for m in '+repr(modes)+']\n'
    (candidate/'eval/harness.py').write_text(code[:start]+registry+code[end:],encoding='utf-8')
    service=candidate/'flowerp/service.py'; original=service.read_text(encoding='utf-8')
    pos=original.index('    def propose_purchase('); stop=original.index('    def purchase(',pos)
    marker='        return self.purchase(pid)'
    injection='        self.receive_stock(sku, quantity, "teaching-premature-" + pid)\n'
    assert original[pos:stop].count(marker)==1
    broken=original[:pos]+original[pos:stop].replace(marker,injection+marker)+original[stop:]
    service.write_text(broken,encoding='utf-8')
    (directory/'injected.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True),broken.splitlines(True),fromfile='original/service.py',tofile='candidate/service.py')),encoding='utf-8')
    results=[]
    def evaluate(label,cases):
        target=directory/label; target.mkdir(); report_path=target/'report.json'
        command=[sys.executable,'-B','-X','utf8','-m','eval.harness','--suite','blocking','--report-path',str(report_path)]
        for name in cases: command += ['--case',name]
        proc=subprocess.run(command,cwd=candidate,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',L11_STATES=str(target/'states')),capture_output=True,text=True,encoding='utf-8',timeout=60)
        data={'label':label,'command':command,'cwd':str(candidate),'exit_code':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr,'service_sha256':digest(service),'checks_sha256':digest(candidate/'purchase_checks.py')}
        save(target/'process.json',data)
        report=json.loads(report_path.read_text(encoding='utf-8'));validate_report(report,tuple(cases),proc.returncode)
        results.append(dict(data,summary=report['summary']))
    evaluate('01-partial-v1',['purchase_request_preserves_reason'])
    evaluate('02-complete-v1',['l11_'+m for m in modes])
    service.write_text(original,encoding='utf-8')
    (directory/'restored.diff').write_text(''.join(difflib.unified_diff(broken.splitlines(True),original.splitlines(True),fromfile='v1/service.py',tofile='v2/service.py')),encoding='utf-8')
    evaluate('03-integrated-v2',['purchase_request_preserves_reason']+['l11_'+m for m in modes])
    assert [r['exit_code'] for r in results]==[0,1,0],results
    assert results[0]['service_sha256']==results[1]['service_sha256']!=results[2]['service_sha256']
    assert len({r['checks_sha256'] for r in results})==1
    result={'native_subagents':False,'parallel_execution':False,'teacher_injection_and_restoration':True,'runs':results,'boundary':'Sequential real-code verification experiment; not native delegation, model repair, efficiency measurement, or human acceptance.'}
    save(directory/'index.json',result);return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-dir',required=True,type=Path)
    args=parser.parse_args(); print(json.dumps(run(args.run_dir),ensure_ascii=False,indent=2))
