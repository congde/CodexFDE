"""Run real HTTP checks of the current first version, without later modules."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from workbench.runtime_paths import ROOT, service_runtime
from workbench.workbench_server import WorkbenchApp, make_handler


class WorkbenchServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workbench-http-check-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.runtime = self.base / "data"
        self.app = WorkbenchApp(self.runtime, port=0)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.app.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(ProxyHandler({}))

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_home_and_static_files_are_served(self):
        with self.opener.open(self.url + "/", timeout=3) as response:
            page = response.read().decode("utf-8")
        self.assertIn("个人 AI 研发工作台", page)
        self.assertIn("事项与决策", page)
        for path, content_type in (("/app.js", "text/javascript"), ("/styles.css", "text/css")):
            with self.subTest(path=path), self.opener.open(self.url + path, timeout=3) as response:
                self.assertIn(content_type, response.headers["Content-Type"])
                self.assertTrue(response.read())

    def test_health_reports_actual_locations_without_creating_database(self):
        with self.opener.open(self.url + "/api/health", timeout=3) as response:
            health = json.load(response)
        self.assertEqual(health["surface"], "workbench")
        self.assertEqual(health["capabilities"], ["shell"])
        self.assertEqual(health["runtime"], str(self.runtime))
        self.assertEqual(health["database"], str(self.runtime / "workbench.db"))
        self.assertFalse(health["database_exists"])
        self.assertNotIn("ready", health)
        self.assertFalse(self.runtime.exists())

    def test_unimplemented_routes_and_writes_are_rejected(self):
        for path in ("/missing", "/../README.md", "/api/v1/initiatives"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.opener.open(self.url + path, timeout=3)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            self.opener.open(Request(self.url + "/api/v1/initiatives", data=b"", method="POST"), timeout=3)
        self.assertEqual(error.exception.code, 501)
        self.assertFalse(self.runtime.exists())

    def test_default_and_explicit_runtime_paths(self):
        self.assertEqual(service_runtime("workbench"), ROOT.resolve() / ".runtime/workbench")
        self.assertEqual(service_runtime("workbench", self.runtime), self.runtime)
        self.assertEqual(service_runtime("workbench", root=self.base), self.base / ".runtime/workbench")
        self.assertFalse(self.runtime.exists())
        self.assertFalse((self.base / ".runtime").exists())


if __name__ == "__main__":
    unittest.main()
