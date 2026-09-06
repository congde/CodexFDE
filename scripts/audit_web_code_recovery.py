"""Real service restart with an explicitly synthetic interrupted code task.

No Codex is called; this audits process ownership and durable recovery only.
"""
from __future__ import annotations
import argparse
import json
import socket
import subprocess
import sys
import time
import uuid
from http.client import HTTPConnection
from pathlib import Path

from workbench.runtime_lease import WorkbenchRuntimeLease
from workbench.task_store import TaskStore


def paused_worker(runtime):
    with WorkbenchRuntimeLease(runtime):
        store = TaskStore(runtime / 'workbench.db')
        task = store.create('维护者恢复夹具，不是真实代码执行', execution_mode='codex', write_scope=['flowerp'])
        store.append_event(task['id'], '网页具名授权课程隔离执行', actor='maintainer-fixture', evidence={'fixture':True})
        store.transition(task['id'], 'spec_ready')
        store.transition(task['id'], 'executing')
        (runtime / 'paused.json').write_text(json.dumps(store.get(task['id'])), encoding='utf-8')
        while True:
            time.sleep(1)


def audit(runtime):
    runtime.mkdir(parents=True, exist_ok=False)
    records = {'real_codex_execution':False, 'student_achievement':False, 'runtime':str(runtime)}
    processes, logs = [], []
    def launch(command, name):
        log = (runtime / (name + '.log')).open('w', encoding='utf-8')
        logs.append(log)
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process
    try:
        first = launch([sys.executable, '-X', 'utf8', '-m', 'scripts.audit_web_code_recovery', '--worker', str(runtime)], 'first')
        deadline = time.monotonic() + 20
        while not (runtime / 'paused.json').exists():
            if first.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('初始进程未进入待中断状态')
            time.sleep(.05)
        before = json.loads((runtime / 'paused.json').read_text(encoding='utf-8'))
        records['before'] = before
        try:
            with WorkbenchRuntimeLease(runtime):
                raise AssertionError('同一运行目录被两个进程同时获取')
        except RuntimeError:
            records['second_owner_rejected'] = True
        first.kill()
        first.wait(10)
        records['terminated_pid'] = first.pid
        records['terminated_returncode'] = first.returncode
        with socket.socket() as address:
            address.bind(('127.0.0.1', 0))
            port = address.getsockname()[1]
        second = launch([sys.executable, '-X', 'utf8', '-m', 'workbench.cli', 'serve-workbench',
                         '--port', str(port), '--runtime-dir', str(runtime)], 'restarted')
        deadline = time.monotonic() + 20
        after = None
        while time.monotonic() < deadline:
            if second.poll() is not None:
                raise RuntimeError('重启服务提前退出')
            connection = HTTPConnection('127.0.0.1', port, timeout=1)
            try:
                connection.request('GET', '/api/v1/tasks/' + before['id'])
                response = connection.getresponse()
                if response.status == 200:
                    after = json.loads(response.read())
                    break
            except OSError:
                time.sleep(.05)
            finally:
                connection.close()
        assert after is not None, '重启后的任务不可查询'
        assert after['status'] == 'dead_letter', after['status']
        assert after['events'][:-1] == before['events'], '旧事件没有完整保留'
        assert after['events'][-1]['evidence']['safe_replay'] is False
        assert TaskStore(runtime / 'workbench.db').get(before['id'])['status'] == after['status']
        records.update(after=after, api_sqlite_agree=True, passed=True)
    except Exception as error:
        records.update(passed=False, error=f'{type(error).__name__}: {error}')
        raise
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(10)
        for log in logs:
            log.close()
        (runtime / 'audit.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    if args.worker:
        paused_worker(args.worker)
    else:
        result = audit(Path('.runtime/web-code-recovery-audits').resolve() / uuid.uuid4().hex)
        print(json.dumps({'passed':result['passed'], 'evidence':str(Path(result['runtime']) / 'audit.json'),
                          'real_codex_execution':False}, ensure_ascii=False))
