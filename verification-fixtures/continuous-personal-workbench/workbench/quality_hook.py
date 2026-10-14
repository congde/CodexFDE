"""Prepare a personal Stop handler; installation and native trust are separate."""
import base64,json,shlex,sys
from pathlib import Path

HANDLER = r'''
import hashlib,json,os,subprocess,sys
from pathlib import Path
from uuid import uuid4
BINDING = __BINDING__
def main():
    binding=json.loads(Path(BINDING).read_text('utf8'))
    root=Path(binding['workspace']).resolve(strict=True)
    record={'host_event_verified':False,'preparation':binding['preparation'],
            'workspace':str(root),'outcome':'unverified'}
    response={'decision':'block','reason':'Hook 未完成验证'}
    folder=Path(binding['runs'])/uuid4().hex
    folder.mkdir(parents=True)
    try:
        event=json.load(sys.stdin)
        if not isinstance(event,dict) or event.get('hook_event_name')!='Stop':
            raise ValueError('需要 Stop 事件')
        cwd=Path(event['cwd']).resolve(strict=True)
        if cwd!=root and not cwd.is_relative_to(root): raise ValueError('事件目录越界')
        if type(event.get('stop_hook_active',False)) is not bool:raise ValueError('重入标记须为布尔值')
        record['session_id']=event.get('session_id')
        if event.get('stop_hook_active'):
            record['outcome']='skipped'
            response={'continue':True,'systemMessage':'重入跳过；本次未复验，不产生通过结论。'}
        else:
            for name,sha in binding['checks'].items():
                if hashlib.sha256((root/name).read_bytes()).hexdigest()!=sha:
                    raise ValueError('冻结检查变化：'+name)
            path=folder/'harness.json'
            command=binding['command']+['--report-path',str(path)]
            result=subprocess.run(command,cwd=root,text=True,encoding='utf8',capture_output=True,
                                  timeout=binding['timeout'],env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
            (folder/'stdout.txt').write_text(result.stdout,'utf8')
            (folder/'stderr.txt').write_text(result.stderr,'utf8')
            record.update(command=command,exit_code=result.returncode,report=str(path),
                          code_sha256=hashlib.sha256((root/'flowerp/service.py').read_bytes()).hexdigest())
            if not path.is_file():raise ValueError('本次报告未生成')
            report=json.loads(path.read_text('utf8'))
            sys.path.insert(0,str(root))
            from eval.report_contract import validate_report
            validate_report(report,binding['names'],result.returncode,'blocking')
            record['report_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            record['outcome']='passed' if result.returncode==0 else 'blocked'
            if result.returncode==0:
                response={'continue':True,'systemMessage':'本次候选阻断检查已复验；仍待独立人审。'}
            else:
                failed=[r['evidence'] for r in report['results'] if not r['passed']]
                response={'decision':'block','reason':'本次候选阻断失败：'+'\n'.join(failed)}
    except Exception as error:
        record['error']=type(error).__name__+': '+str(error)
        response={'decision':'block','reason':'Hook 未完成验证：'+record['error']}
    record['response']=response
    with (folder/'event.json').open('x',encoding='utf8') as stream:json.dump(record,stream,ensure_ascii=False,indent=2)
    print(json.dumps(response,ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())
'''

def prepare(*,workspace,runtime,preparation,names,checks,timeout=900):
    root=Path(workspace).resolve(strict=True)
    runtime=Path(runtime).resolve()
    if timeout<=0 or runtime.is_relative_to(root):raise ValueError('Hook runtime must be outside candidate')
    folder=root/'hook_staging'
    folder.mkdir(exist_ok=False)
    binding=runtime/'hook-binding.json'
    binding.parent.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,'-B','-S','-X','utf8','-m','eval.harness','--suite','blocking']
    for name in names:command+=['--case',name]
    data={'workspace':str(root),'preparation':str(preparation),'runs':str(runtime/'hook-runs'),
          'names':names,'checks':checks,'command':command,'timeout':timeout}
    with binding.open('x',encoding='utf8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
    handler=folder/'quality_gate.py'
    handler.write_text(HANDLER.replace('__BINDING__',repr(str(binding))),'utf8')
    installed=root/'.codex/hooks/quality_gate.py'
    ps="& '"+sys.executable.replace("'","''")+"' -B -S -X utf8 '"+str(installed).replace("'","''")+"'"
    encoded=base64.b64encode(ps.encode('utf-16-le')).decode()
    config={'hooks':{'Stop':[{'hooks':[{'type':'command',
        'command':' '.join(shlex.quote(a) for a in [sys.executable,'-B','-S','-X','utf8',str(installed)]),
        'commandWindows':'powershell.exe -NoProfile -EncodedCommand '+encoded,
        'timeout':timeout+60,'statusMessage':'复验本事项的个人候选'}]}]}}
    with (folder/'hooks.json').open('x',encoding='utf8') as stream:json.dump(config,stream,ensure_ascii=False,indent=2)
    return {'folder':str(folder),'binding':str(binding),'native_trust':'pending'}
