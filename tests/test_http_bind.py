from __future__ import annotations

import errno
import io
import unittest
from http.server import BaseHTTPRequestHandler
from unittest.mock import patch

from workbench.http_bind import ServerBindError, create_http_server, report_bind_error


class _Handler(BaseHTTPRequestHandler):
    pass


class HTTPBindTests(unittest.TestCase):
    def test_available_port_starts_normally(self) -> None:
        server = create_http_server(
            "127.0.0.1",
            0,
            _Handler,
            service_name="测试服务",
            retry_command="serve --port 8080",
        )
        try:
            self.assertGreater(server.server_address[1], 0)
        finally:
            server.server_close()

    def test_second_server_cannot_share_the_same_port(self) -> None:
        first = create_http_server(
            "127.0.0.1",
            0,
            _Handler,
            service_name="测试服务",
            retry_command="serve --port 8080",
        )
        try:
            port = first.server_address[1]
            with self.assertRaises(ServerBindError):
                create_http_server(
                    "127.0.0.1",
                    port,
                    _Handler,
                    service_name="第二个测试服务",
                    retry_command="serve --port 8081",
                )
        finally:
            first.server_close()

    def test_address_in_use_becomes_actionable_error(self) -> None:
        failure = OSError(errno.EADDRINUSE, "address already in use")
        with patch("workbench.http_bind.ExclusiveThreadingHTTPServer", side_effect=failure):
            with self.assertRaisesRegex(ServerBindError, "端口已被其他进程占用") as raised:
                create_http_server(
                    "127.0.0.1",
                    8000,
                    _Handler,
                    service_name="FlowERP",
                    retry_command="serve --port 8080",
                )

        message = str(raised.exception)
        self.assertIn("127.0.0.1:8000", message)
        self.assertIn("Get-NetTCPConnection", message)
        self.assertIn("serve --port 8080", message)

    def test_permission_denied_explains_windows_causes(self) -> None:
        failure = PermissionError(errno.EACCES, "permission denied")
        with patch("workbench.http_bind.ExclusiveThreadingHTTPServer", side_effect=failure):
            with self.assertRaisesRegex(ServerBindError, "被系统保留"):
                create_http_server(
                    "127.0.0.1",
                    8010,
                    _Handler,
                    service_name="完整 Harness 平台",
                    retry_command="serve-web --port 8090",
                )

    def test_reporter_returns_nonzero_without_traceback(self) -> None:
        output = io.StringIO()
        with patch("sys.stderr", output):
            exit_code = report_bind_error(ServerBindError("端口启动失败"))

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "端口启动失败\n")

if __name__ == "__main__":
    unittest.main()
