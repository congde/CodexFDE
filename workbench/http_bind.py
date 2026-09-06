from __future__ import annotations

import errno
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ServerBindError(RuntimeError):
    """Actionable startup error raised when an HTTP listener cannot bind."""


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """Prevent two Windows processes from silently sharing one HTTP port."""

    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _bind_reason(error: OSError) -> str:
    winerror = getattr(error, "winerror", None)
    if winerror == 10048 or error.errno == errno.EADDRINUSE:
        return "端口已被其他进程占用"
    if winerror == 10013 or error.errno == errno.EACCES:
        return "Windows 拒绝绑定；端口可能已被占用、被系统保留，或被安全策略拦截"
    detail = error.strerror or str(error)
    return f"操作系统拒绝监听（{detail}）"


def create_http_server(
    host: str,
    port: int,
    handler: type[BaseHTTPRequestHandler],
    *,
    service_name: str,
    retry_command: str,
) -> ThreadingHTTPServer:
    """Create a server while converting low-level bind failures into guidance."""

    try:
        return ExclusiveThreadingHTTPServer((host, port), handler)
    except OSError as error:
        reason = _bind_reason(error)
        windows_check = (
            f"Get-NetTCPConnection -LocalPort {port} -State Listen "
            "-ErrorAction SilentlyContinue"
        )
        raise ServerBindError(
            f"无法启动 {service_name}：不能监听 {host}:{port}。\n"
            f"原因：{reason}。\n"
            f"检查占用（PowerShell）：{windows_check}\n"
            f"改用其他端口：{retry_command}"
        ) from None


def report_bind_error(error: ServerBindError) -> int:
    print(str(error), file=sys.stderr)
    return 2
