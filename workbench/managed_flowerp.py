from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class FlowERPStartupError(RuntimeError):
    """Raised when the combined Harness launch cannot provide FlowERP."""


def report_flowerp_startup_error(error: FlowERPStartupError) -> int:
    print(str(error), file=sys.stderr)
    return 2


def _probe_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host


def is_flowerp_live(host: str, port: int, *, timeout: float = 0.3) -> bool:
    url = f"http://{_probe_host(host)}:{port}/api/v1/health/live"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError):
        return False
    return response.status == 200 and payload.get("service") == "flowerp"


def _port_is_open(host: str, port: int, *, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((_probe_host(host), port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class ManagedFlowERP:
    host: str
    port: int
    process: subprocess.Popen[bytes] | None = None

    @property
    def owned(self) -> bool:
        return self.process is not None

    def summary(self) -> dict[str, object]:
        return {
            "status": "started" if self.owned else "already_running",
            "url": f"http://{_probe_host(self.host)}:{self.port}",
            "pid": self.process.pid if self.process else None,
            "owned_by_harness": self.owned,
        }

    def stop(self, *, timeout: float = 5.0) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        if sys.platform == "win32" and hasattr(signal, "CTRL_BREAK_EVENT"):
            try:
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                self.process.terminate()
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=timeout)


def launch_flowerp(
    repository_root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    runtime_dir: str | Path = ".runtime",
    startup_timeout: float = 10.0,
) -> ManagedFlowERP:
    """Reuse a real FlowERP listener or start one owned child process."""

    if is_flowerp_live(host, port):
        return ManagedFlowERP(host, port)
    if _port_is_open(host, port):
        raise FlowERPStartupError(
            f"不能联动启动 FlowERP：{host}:{port} 已被非 FlowERP 服务占用。\n"
            "请停止占用进程，或追加 --flowerp-port 8080。"
        )

    root = Path(repository_root).resolve()
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "workbench.cli",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
        "--runtime-dir",
        str(runtime_dir),
    ]
    creationflags = 0
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(command, cwd=root, creationflags=creationflags)
    except OSError as error:
        raise FlowERPStartupError(f"无法创建 FlowERP 子进程：{error}") from None
    managed = ManagedFlowERP(host, port, process)
    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise FlowERPStartupError(
                f"FlowERP 子进程启动失败，退出码 {exit_code}。请查看上方启动日志。"
            )
        if is_flowerp_live(host, port):
            return managed
        time.sleep(0.1)

    managed.stop()
    raise FlowERPStartupError(
        f"FlowERP 在 {startup_timeout:g} 秒内未就绪：{host}:{port}。"
    )
