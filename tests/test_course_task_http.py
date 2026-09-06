from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from workbench.task_store import TaskStore, TaskSubmissionConflict
from workbench.workbench_server import WorkbenchApp, make_handler


class CourseTaskHTTPTests(unittest.TestCase):
    def test_http_acceptance_query_conflict_and_persistent_review(self):
        with tempfile.TemporaryDirectory() as directory:
            app = WorkbenchApp(directory)
            entered, release = threading.Event(), threading.Event()

            def fixture(*args, **kwargs):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("fixture was not released")
                return {"summary": {"decision": "pass", "blocking_failed": 0},
                        "results": [{"name": "boundary_fixture", "level": "blocking", "passed": True}]}

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(method, path, body=None, key="lesson-submit"):
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    connection.request(method, path, json.dumps(body) if body is not None else None,
                                       {"Content-Type": "application/json", "Idempotency-Key": key})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()

            task_id = None
            try:
                with patch("workbench.workbench_server.lesson_eval_runner", return_value=fixture):
                    code, capabilities = request("GET", "/api/v1/delivery/capabilities")
                    self.assertEqual((200, ["verify"]), (code, capabilities["execution_modes"]))
                    body = {"lesson": 13, "request": "复验采购审批边界", "actor": "student-li", "business_refs": ["PURCHASE:PUR-FIXTURE"]}
                    code, accepted = request("POST", "/api/v1/delivery/requests", body)
                    self.assertEqual(202, code)
                    task_id = accepted["task_id"]
                    self.assertTrue(entered.wait(5))
                    self.assertFalse(release.is_set(), "202 must arrive before Eval finishes")
                    code, replay = request("POST", "/api/v1/delivery/requests", body)
                    self.assertEqual((202, task_id), (code, replay["task_id"]))
                    code, _ = request("POST", "/api/v1/delivery/requests", {**body, "request": "不同需求"})
                    self.assertEqual(409, code)
                    code, _ = request("POST", "/api/v1/delivery/requests", {**body, "execution_mode": "codex"}, "codex")
                    self.assertEqual(400, code)
                    code, _ = request("POST", "/api/v1/delivery/requests", {**body, "business_refs": ["PURCHASE:PUR-OTHER"]})
                    self.assertEqual(409, code)
                    code, _ = request("POST", "/api/v1/delivery/requests", {**body, "business_refs": "invalid"}, "invalid-refs")
                    self.assertEqual(400, code)
                    self.assertEqual(1, len(app.tasks.list()))
                    release.set()
                    self.assertEqual("review", app.automation.wait(task_id)["status"])
                    code, queried = request("GET", accepted["status_url"])
                    self.assertEqual((200, "review"), (code, queried["status"]))
                    self.assertIsNone(queried["reviewed_by"])
                    reopened = WorkbenchApp(directory)
                    self.assertEqual("review", reopened.tasks.get(task_id)["status"])
                    self.assertIn("PURCHASE:PUR-FIXTURE", reopened.tasks.get(task_id)["business_refs"])
                    code, view = request("GET", accepted["view_url"])
                    self.assertEqual(200, code)
                    self.assertIn("PURCHASE:PUR-FIXTURE", view["business_refs"])
                    self.assertEqual([], reopened.automation.recover())
                    self.assertEqual(404, request("GET", "/api/v1/tasks/TASK-MISSING")[0])
                    self.assertFalse((Path(directory) / "flowerp.db").exists())
            finally:
                release.set()
                if task_id:
                    app.automation.wait(task_id)
                server.shutdown()
                thread.join()
                server.server_close()

    def test_submission_is_atomic_across_store_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workbench.db"
            stores = [TaskStore(path), TaskStore(path)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda store: store.create("同一需求", submission_key="same-key"), stores))
            self.assertEqual(results[0]["id"], results[1]["id"])
            self.assertEqual(1, len(stores[0].list()))
            with self.assertRaises(TaskSubmissionConflict):
                stores[1].create("不同需求", submission_key="same-key")

    def test_each_task_keeps_its_own_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            app = WorkbenchApp(directory)
            first = app.create_course_task(13, "需求一", "student")
            second = app.create_course_task(13, "需求二", "student")
            paths = [Path(app.tasks.get(view["task_id"])["spec_path"]) for view in (first, second)]
            self.assertNotEqual(*paths)
            self.assertTrue(all(path.is_file() for path in paths))
