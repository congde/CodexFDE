"""Independently recount report results; colors and summary text are insufficient."""
from datetime import datetime
def validate_report(report,expected,returncode,suite):
    def reject(message): raise RuntimeError(message)
    if not isinstance(report,dict) or report.get('schema_version')!='1.0': reject('unknown schema')
    if suite not in ('all','blocking','observing') or report.get('suite')!=suite: reject('suite changed')
    wanted=list(expected)
    if not wanted or len(set(wanted))!=len(wanted): reject('empty or duplicated expected cases')
    if report.get('requested_cases')!=wanted: reject('requested cases changed')
    try:
        stamp=datetime.fromisoformat(report['generated_at'])
        if stamp.tzinfo is None: reject('report timestamp lacks timezone')
    except (KeyError,TypeError,ValueError): reject('invalid timestamp')
    rows=report.get('results')
    if not isinstance(rows,list) or len(rows)!=len(wanted): reject('missing or extra results')
    if [row.get('name') for row in rows if isinstance(row,dict)]!=wanted: reject('result identities changed')
    for row in rows:
        level=row.get('level')
        if level not in ('blocking','observing') or (suite!='all' and level!=suite): reject('invalid level')
        if type(row.get('passed')) is not bool: reject('passed must be a bool')
        if type(row.get('duration_ms')) is not int or row['duration_ms']<0: reject('invalid duration')
        if not isinstance(row.get('evidence'),str): reject('missing evidence')
        error=row.get('error')
        if row['passed'] and error is not None: reject('successful result has an error')
        if not row['passed'] and (not isinstance(error,dict) or not all(isinstance(error.get(k),str) and error[k] for k in ('type','message'))):
            reject('failure reason missing')
    blocking=sum(not row['passed'] and row['level']=='blocking' for row in rows)
    actual={'total':len(rows),'passed':sum(row['passed'] for row in rows),'blocking_failed':blocking,
            'observing_failed':sum(not row['passed'] and row['level']=='observing' for row in rows),
            'decision':'block' if blocking else 'pass'}
    summary=report.get('summary')
    if not isinstance(summary,dict) or any(type(summary.get(k)) is not int for k in ('total','passed','blocking_failed','observing_failed')) or summary!=actual:
        reject('summary contradicts real results')
    if type(returncode) is not int or returncode!=(1 if blocking else 0): reject('exit contradicts blocking results')
    return report
