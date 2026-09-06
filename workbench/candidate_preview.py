"""Local, task-bound candidate UI previews with separate data and owned processes."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
from urllib.request import build_opener, ProxyHandler

from .process_guard import spawn
from .desktop import wait_for_product_release


class CandidatePreviews:
    def __init__(self, runtime, tasks):
        self.runtime = Path(runtime).resolve()
        self.tasks = tasks
        self.running = {}
        self.lock = threading.Lock()

    def start(self, task_id, actor):
        if not isinstance(actor, str) or not actor.strip() or actor.strip().lower().startswith('agent:') or len(actor) > 80:
            raise ValueError('请填写查看本次成果的署名')
        if not re.fullmatch(r'TASK-[A-Za-z0-9_-]+', task_id):
            raise ValueError('任务编号无效')
        task = self.tasks.get(task_id)
        gate = next((e.get('evidence') or {} for e in reversed(task['events'])
                     if e['detail'] == '课程红绿差分判定已完成'), {})
        package = next((e.get('evidence') or {} for e in reversed(task['events'])
                        if e['detail'] == '日常研发交付包已保存'), {})
        daily_ready = package.get('status') == 'review' and (task.get('result') or {}).get('summary', {}).get('decision') == 'pass'
        if task['status'] not in {'review', 'completed'} or not (gate.get('accepted') or daily_ready):
            raise ValueError('本次候选成果还未完成范围与自动检查，请先查看交付证据')
        workspace = Path(package['workspace']) if daily_ready else self.runtime / 'course-worktrees' / task_id
        if workspace.is_symlink() or not workspace.resolve().is_relative_to(self.runtime) or not (workspace/'web/index.html').is_file():
            raise ValueError('本次隔离成果不在原运行目录，或尚无可预览的客户界面')
        execution = next((e.get('evidence') or {} for e in reversed(task['events'])
                          if e['detail'] == '受控执行阶段完成'), {})
        manifest = execution.get('change_manifest') or []
        if not manifest:
            raise ValueError('缺少本次修改的文件校验记录，无法启动预览')
        for item in manifest:
            path = workspace / item['path']
            if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
                raise ValueError('候选文件路径越界')
            expected = item.get('after_sha256')
            if (expected is None and path.exists()) or (expected is not None and
                    (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected)):
                raise ValueError('本次修改文件在检查后又发生变化，请重新核验候选成果')
        with self.lock:
            old = self.running.get(task_id)
            if old and old[0].poll() is None:
                return dict(old[2], reused=True)
            if old and old[1]: old[1].close()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            data = self.runtime / 'candidate-previews' / task_id
            data.mkdir(parents=True, exist_ok=True)
            wait_for_product_release(data)
            command = [sys.executable, '-X', 'utf8', '-m', 'workbench.cli', 'serve',
                       '--host', '127.0.0.1', '--port', str(port), '--runtime-dir', str(data)]
            process, owner, prefix = spawn(command, workspace)
            try:
                process.stdin.write(prefix); process.stdin.close()
            except OSError:
                if owner: owner.close()
                if process.poll() is None: process.kill()
                process.wait(timeout=5)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    pipe.close()
                raise ValueError('候选预览进程提前退出，请检查本次隔离代码')
            # Continuously drain both pipes so the local server cannot block on logs.
            def drain(pipe, name):
                with (data/name).open('a', encoding='utf-8') as log, pipe:
                    for line in pipe: log.write(line); log.flush()
            for pipe, name in [(process.stdout,'stdout.log'), (process.stderr,'stderr.log')]:
                threading.Thread(target=drain,args=(pipe,name),daemon=True).start()
            url = f'http://127.0.0.1:{port}'
            payload = {'task_id':task_id, 'url':url, 'workspace':str(workspace),
                       'label':'本次候选成果 · 独立预览数据', 'human_accepted':task['status']=='completed',
                       'notice':'请对照本次验收标准操作。此预览不代表正式发布；数据独立于当前客户项目。'}
            self.running[task_id] = (process, owner, payload)
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with opener.open(url+'/api/v1/health/live',timeout=.5) as response:
                        health = json.load(response)
                    identity = hashlib.sha256(os.path.normcase(str(data.resolve())).encode()).hexdigest()
                    if (health.get('service') == 'flowerp' and health.get('status') == 'ok'
                            and health.get('runtime_id') == identity):
                        self.tasks.append_event(task_id, '已打开本次候选成果预览', actor=actor,
                                                evidence=payload)
                        return payload
                except (OSError, ValueError): pass
                time.sleep(.1)
            if owner: owner.close()
            if process.poll() is None: process.kill()
            process.wait(timeout=5)
            del self.running[task_id]
            raise ValueError('候选预览未能启动，日志已保留在本任务的 candidate-previews 目录')

    def close(self):
        with self.lock:
            for process, owner, _ in self.running.values():
                if owner: owner.close()
                if process.poll() is None: process.kill()
                process.wait(timeout=5)
            self.running.clear()
