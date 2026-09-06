"""Explicit maintenance exercise: real Codex, abrupt owner exit, HTTP recovery.

All writes and process lifetimes belong to a new audit runtime. No human review
is issued, and the result is never student achievement evidence.
"""
from pathlib import Path
import argparse
import ctypes
from ctypes import wintypes
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import build_opener, ProxyHandler

from workbench.runtime_lease import WorkbenchRuntimeLease
from workbench.task_store import TaskStore
from workbench.web_execution import WebExecution

ROOT = Path(__file__).resolve().parent.parent


def worker(runtime):
    with WorkbenchRuntimeLease(runtime):
        tasks = TaskStore(runtime / 'workbench.db')
        service = WebExecution(ROOT, runtime, tasks, enabled=True)
        plan = service.prepare(13, 'Codex中断维护演练（非学员证据）')
        service.authorize(plan['plan_id'], plan['confirmation'], plan['actor'])
        (runtime / 'owner.json').write_text(json.dumps({'pid':os.getpid(), 'plan_id':plan['plan_id']}), encoding='utf-8')
        while True:
            if (runtime / 'interrupt-owner').exists():
                os._exit(91)  # Deliberately skip Python finally blocks, like a crash.
            if service.get(plan['plan_id'])['state'] in {'finished', 'failed'}:
                return
            time.sleep(.05)


def audit():
    if os.name != 'nt':
        raise RuntimeError('此演练针对 Windows')
    runtime = ROOT / '.runtime/real-code-restart-audits' / uuid.uuid4().hex
    runtime.mkdir(parents=True)
    print('演练目录：' + str(runtime), flush=True)
    record = {'runtime':str(runtime), 'passed':False, 'real_codex_execution':False, 'student_achievement':False}
    processes, logs, handles = [], [], []
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    def launch(args, name):
        log = (runtime / (name + '.log')).open('wb'); logs.append(log)
        p = subprocess.Popen([sys.executable,'-X','utf8',*args], cwd=ROOT,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append(p); return p
    try:
        first = launch(['-m','scripts.audit_real_code_restart','--worker',str(runtime)], 'owner')
        deadline = time.monotonic() + 150
        selected = []
        while time.monotonic() < deadline:
            if first.poll() is not None:
                raise RuntimeError('拥有者在执行观察前已退出，请查 owner.log')
            if (runtime / 'owner.json').exists():
                owner = json.loads((runtime / 'owner.json').read_text(encoding='utf-8'))
                # Only use command text to distinguish this owned exec from --version.
                result = subprocess.run(['powershell','-NoProfile','-Command',
                    'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress'],
                    capture_output=True, encoding='utf-8', errors='replace', timeout=15)
                rows = json.loads(result.stdout)
                descendants = {owner['pid']}
                for _ in range(20):
                    expanded = descendants | {r['ProcessId'] for r in rows if r['ParentProcessId'] in descendants}
                    if expanded == descendants: break
                    descendants = expanded
                selected = [r for r in rows if r['ProcessId'] in descendants and
                            str(r['Name']).lower().startswith('codex') and
                            ' exec ' in str(r['CommandLine']) and '--sandbox' in str(r['CommandLine'])]
                if selected: break
            time.sleep(.2)
        if not selected:
            raise RuntimeError('没有观察到属于本次拥有者的真实 Codex exec，不算通过')
        for row in selected:
            handle = api.OpenProcess(0x100000, False, row['ProcessId'])
            if not handle or api.WaitForSingleObject(handle, 0) != 258:
                if handle: api.CloseHandle(handle)
                raise RuntimeError('Codex 已提前退出，未执行中断')
            handles.append(handle)
        store = TaskStore(runtime / 'workbench.db')
        before = store.get(store.list()[0]['id'])
        if before['status'] != 'executing':
            raise RuntimeError('任务已离开执行阶段，未执行中断')
        record.update(real_codex_execution=True, before=before, plan_id=owner['plan_id'],
                      codex_pids=[r['ProcessId'] for r in selected])
        (runtime / 'interrupt-owner').write_text('maintenance crash request', encoding='utf-8')
        first.wait(timeout=10)
        record['owner_exit_code'] = first.returncode
        for handle in handles:
            assert api.WaitForSingleObject(handle, 5000) == 0, '真实 Codex 未随拥有者退出'
        record['owned_codex_terminated'] = True
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        second = launch(['-m','workbench.cli','serve-workbench','--port',str(port),'--runtime-dir',str(runtime)], 'restarted')
        opener = build_opener(ProxyHandler({}))
        def get(path):
            with opener.open(f'http://127.0.0.1:{port}' + path, timeout=2) as response:
                return json.load(response)
        deadline = time.monotonic() + 25
        while True:
            try:
                after = get('/api/v1/tasks/' + before['id']); break
            except OSError:
                if second.poll() is not None or time.monotonic() > deadline: raise
                time.sleep(.1)
        plan = get('/api/v1/execution/plans/' + owner['plan_id'])
        assert after['status'] == 'dead_letter', after['status']
        assert after['events'][:-1] == before['events'], '原始事件发生变化'
        assert plan['state'] == 'failed' and plan['task_id'] == before['id']
        assert len(store.list()) == 1, '重启重复创建了任务'
        assert store.get(before['id'])['status'] == after['status']
        record.update(passed=True, after=after, recovered_plan=plan, duplicate_tasks=False)
    finally:
        # Ask only the audit owner to crash; its job closes its own descendants.
        (runtime / 'interrupt-owner').touch()
        for p in processes:
            if p.poll() is None:
                try: p.wait(timeout=2)
                except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=5)
        for handle in handles: api.CloseHandle(handle)
        for log in logs: log.close()
        (runtime / 'audit.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':record['passed'], 'evidence':str(runtime/'audit.json')},ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    worker(args.worker) if args.worker else audit()
