"""Personal Harness: explicit selection, per-case reasons, exact blocking count."""
from datetime import datetime,timezone
import json,time
from pathlib import Path
from .report_contract import validate_report

def run(entries,*,suite='all',names=None,report_path=None):
    if suite not in ('all','blocking','observing'): raise ValueError('unknown suite')
    known=[name for name,level,fn in entries]
    if not known or len(known)!=len(set(known)): raise ValueError('empty or duplicate registry')
    if any(level not in ('blocking','observing') or not callable(fn) for _,level,fn in entries):
        raise ValueError('invalid registration')
    selected_names=list(names) if names is not None else [n for n,l,f in entries if suite=='all' or l==suite]
    if not selected_names or len(selected_names)!=len(set(selected_names)): raise ValueError('empty or duplicate selection')
    lookup={n:(l,f) for n,l,f in entries}
    if any(n not in lookup or suite!='all' and lookup[n][0]!=suite for n in selected_names):
        raise ValueError('unknown case or suite mismatch')
    results=[]
    for name in selected_names:
        level,fn=lookup[name];began=time.perf_counter()
        try:
            value=fn(); passed=True;reason=None;evidence=str(value)
        except Exception as exc:
            passed=False;evidence=str(exc) or repr(exc)
            reason={'type':type(exc).__name__,'message':evidence}
        results.append({'name':name,'level':level,'passed':passed,
                        'duration_ms':round((time.perf_counter()-began)*1000),'evidence':evidence,'error':reason})
    blockers=sum(r['level']=='blocking' and not r['passed'] for r in results)
    code=1 if blockers else 0
    report={'schema_version':'1.0','suite':suite,'requested_cases':selected_names,
            'generated_at':datetime.now(timezone.utc).isoformat(),'results':results,
            'summary':{'total':len(results),'passed':sum(r['passed'] for r in results),
                       'blocking_failed':blockers,'observing_failed':sum(r['level']=='observing' and not r['passed'] for r in results),
                       'decision':'block' if blockers else 'pass'}}
    validate_report(report,selected_names,code,suite)
    if report_path is not None:
        target=Path(report_path);target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('x',encoding='utf8') as stream: json.dump(report,stream,ensure_ascii=False,indent=2)
    return report,code
