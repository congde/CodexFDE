from __future__ import annotations

import io
import json
import signal
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import Mock, patch

from workbench.harness_cli import build_parser, main as harness_main
from workbench.http_bind import ExclusiveThreadingHTTPServer
from workbench.managed_flowerp import (
    FlowERPStartupError,
    ManagedFlowERP,
    is_flowerp_live,
    launch_flowerp,
)
from workbench.platform_server import serve as serve_platform


class ManagedFlowERPTests(unittest.TestCase):
    def test_local_health_probe_bypasses_environment_proxy(self) -> None:
        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = b'{"status":"ok","service":"flowerp"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args) -> None:
                return

        server = ExclusiveThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertTrue(is_flowerp_live("127.0.0.1", server.server_address[1]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_existing_flowerp_is_reused_and_not_owned(self) -> None:
        with patch("workbench.managed_flowerp.is_flowerp_live", return_value=True), patch(
            "workbench.managed_flowerp.subprocess.Popen"
        ) as popen:
            managed = launch_flowerp(Path.cwd())

        self.assertFalse(managed.owned)
        self.assertEqual(managed.summary()["status"], "already_running")
        managed.stop()
        popen.assert_not_called()

    def test_non_flowerp_listener_is_not_reused(self) -> None:
        with patch("workbench.managed_flowerp.is_flowerp_live", return_value=False), patch(
            "workbench.managed_flowerp._port_is_open", return_value=True
        ):
            with self.assertRaisesRegex(FlowERPStartupError, "非 FlowERP 服务占用"):
                launch_flowerp(Path.cwd(), port=8000)

    def test_owned_child_is_stopped_with_harness(self) -> None:
        process = Mock(pid=4321)
        process.poll.return_value = None
        with patch(
            "workbench.managed_flowerp.is_flowerp_live", side_effect=[False, True]
        ), patch("workbench.managed_flowerp._port_is_open", return_value=False), patch(
            "workbench.managed_flowerp.subprocess.Popen", return_value=process
        ):
            managed = launch_flowerp(Path.cwd(), port=8080)

        self.assertTrue(managed.owned)
        self.assertEqual(managed.summary()["pid"], 4321)
        managed.stop()
        if sys.platform == "win32" and hasattr(signal, "CTRL_BREAK_EVENT"):
            process.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate.assert_called_once_with()
        process.wait.assert_called_once()

    def test_boot_is_explicit_combined_launch_flag(self) -> None:
        args = build_parser().parse_args(["serve-web", "--boot", "--flowerp-port", "8080"])
        self.assertTrue(args.boot)
        self.assertEqual(args.flowerp_port, 8080)

    def test_harness_cli_forwards_boot_as_bootstrap_and_flowerp(self) -> None:
        with patch("workbench.platform_server.serve") as serve, patch("sys.stdout", io.StringIO()):
            exit_code = harness_main([
                "--runtime-dir", ".harness-test",
                "serve-web",
                "--boot",
                "--port", "8090",
                "--flowerp-port", "8080",
            ])

        self.assertEqual(exit_code, 0)
        call = serve.call_args.args
        self.assertEqual(call[1], 8090)
        self.assertTrue(call[4])
        self.assertTrue(call[5])
        self.assertEqual(call[7], 8080)

    def test_managed_summary_marks_owned_process(self) -> None:
        process = Mock(pid=99)
        managed = ManagedFlowERP("127.0.0.1", 8080, process)
        self.assertEqual(
            managed.summary(),
            {
                "status": "started",
                "url": "http://127.0.0.1:8080",
                "pid": 99,
                "owned_by_harness": True,
            },
        )

    def test_combined_server_log_labels_and_flushes_harness_address(self) -> None:
        api = Mock(repository_root=Path.cwd())
        server = Mock()
        managed = Mock()
        managed.summary.return_value = {
            "status": "started",
            "url": "http://127.0.0.1:8000",
        }
        with patch("workbench.platform_server.HarnessPlatformAPI", return_value=api), patch(
            "workbench.platform_server.create_http_server", return_value=server
        ), patch("workbench.platform_server.launch_flowerp", return_value=managed), patch(
            "builtins.print"
        ) as output:
            serve_platform(with_flowerp=True)

        payload = json.loads(output.call_args.args[0])
        self.assertEqual(payload["event"], "harness_server_started")
        self.assertEqual(payload["service"], "harness")
        self.assertEqual(payload["harness_url"], "http://127.0.0.1:8010")
        self.assertEqual(payload["url"], payload["harness_url"])
        self.assertEqual(payload["flowerp"]["url"], "http://127.0.0.1:8000")
        self.assertTrue(output.call_args.kwargs["flush"])
        managed.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
