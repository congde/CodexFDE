from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from workbench.server import App, make_handler, _structured_log


class HTTPAPITests(unittest.TestCase):
    def persist_report(self, task_id: str, report: dict) -> dict:
        report_dir = Path(self.app.api.tasks.path).parent / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{task_id}-harness-blocking.json"
        report["report_path"] = (Path("reports") / report_path.name).as_posix()
        payload = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
        report_path.write_bytes(payload)
        report["report_sha256"] = hashlib.sha256(payload).hexdigest()
        return report

    def test_broken_log_stream_does_not_abort_request_processing(self) -> None:
        with patch("builtins.print", side_effect=BrokenPipeError):
            _structured_log({"event": "http_request"})

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(self.tmp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None,
                headers: dict | None = None) -> tuple[int, object, dict]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        merged = {"Accept": "application/json", **(headers or {})}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            merged["Content-Type"] = "application/json"
        conn.request(method, path, payload, merged)
        response = conn.getresponse()
        raw = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        conn.close()
        if "application/json" in response_headers.get("content-type", ""):
            return response.status, json.loads(raw.decode("utf-8")), response_headers
        return response.status, raw.decode("utf-8"), response_headers

    def test_static_application_has_security_headers(self) -> None:
        status, body, headers = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn("FlowERP", body)
        self.assertEqual("DENY", headers["x-frame-options"])
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertEqual("nosniff", headers["x-content-type-options"])

    def test_application_shell_exposes_accessible_product_navigation(self) -> None:
        status, body, _ = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn('class="skip-link"', body)
        self.assertIn('id="global-search-button"', body)
        self.assertIn('id="command-backdrop"', body)
        self.assertIn('aria-labelledby="drawer-title"', body)
        self.assertIn('href="./styles.css?v=25"', body)
        self.assertIn('id="channel-orders-table"', body)
        self.assertIn('id="offline-form"', body)
        self.assertIn('id="returns-table"', body)
        self.assertIn('id="receipts-table"', body)
        self.assertIn('id="counts-table"', body)
        self.assertIn('id="payments-table"', body)
        self.assertIn('id="bank-account-summary"', body)
        self.assertIn('id="bank-statements-table"', body)
        self.assertIn('id="periods-table"', body)
        self.assertIn('id="serials-table"', body)
        self.assertIn('id="pricing-table"', body)
        self.assertIn('id="reconciliations-table"', body)
        self.assertIn('id="alerts-table"', body)
        self.assertIn('id="import-result"', body)
        css_status, css, _ = self.request("GET", "/static/styles.css")
        self.assertEqual(200, css_status)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("focus-visible", css)
        relative_css_status, relative_css, _ = self.request("GET", "/styles.css")
        self.assertEqual(200, relative_css_status)
        self.assertEqual(css, relative_css)
        script_status, script, _ = self.request("GET", "/app.js")
        self.assertEqual(200, script_status)
        self.assertIn('location.protocol === "file:"', script)
        self.assertIn('/api/v1/sales/returns?limit=500', script)
        self.assertIn('/api/v1/purchases/receipts?limit=500', script)
        self.assertIn('/api/v1/finance/periods/reopen', script)
        self.assertIn('toast("系统初始化完成，已进入工作台")', script)
        self.assertIn('toast("系统已初始化，已为您进入工作台")', script)
        self.assertIn('>查看应收票</button>', script)
        self.assertIn('/api/v1/dashboard/trends?months=12', script)
        self.assertIn('id="dashboard-trend-chart"', body)
        self.assertIn('id="page-delivery"', body)
        self.assertIn('id="delivery-task-form"', body)
        self.assertIn('src="./app.js?v=35"', body)
        self.assertNotIn('prompt(', script)
        self.assertIn('/api/v1/tasks?limit=100', script)
        self.assertIn('/api/v1/feedback', script)
        self.assertIn('/api/v1/evolutions', script)
        self.assertIn('id="delivery-evolution-form"', body)
        self.assertIn('id="delivery-completed-task-options"', body)
        self.assertIn('系统自动绑定候选 Task 的专属报告快照', body)
        self.assertIn('Array.isArray(value)?value.join("；"):String(value||"—")', script)
        self.assertIn('x.evidence||x.detail||x.message', script)

    def test_static_path_traversal_is_blocked(self) -> None:
        status, _, _ = self.request("GET", "/static/../AGENTS.md")
        self.assertEqual(404, status)

    def test_health_and_setup_endpoints(self) -> None:
        status, body, headers = self.request("GET", "/api/v1/health/ready")
        self.assertEqual(200, status)
        self.assertEqual("ready", body["status"])
        self.assertIn("x-request-id", headers)
        status, body, _ = self.request("GET", "/api/v1/setup/status")
        self.assertEqual(200, status)
        self.assertFalse(body["initialized"])

    def test_bootstrap_login_and_authenticated_me(self) -> None:
        status, _, _ = self.request("POST", "/api/v1/setup/bootstrap", {
            "organization_name": "HTTP 测试组织",
            "username": "admin",
            "password": "Correct-Horse-2026",
        })
        self.assertEqual(201, status)
        status, body, headers = self.request("POST", "/api/v1/auth/login", {
            "organization": "DEFAULT",
            "username": "admin",
            "password": "Correct-Horse-2026",
        })
        self.assertEqual(200, status)
        self.assertIn("HttpOnly", headers["set-cookie"])
        token = body["token"]
        status, me, _ = self.request("GET", "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(200, status)
        self.assertEqual("admin", me["username"])
        self.assertIn("users.manage", me["permissions"])

    def test_write_requires_json_content_type(self) -> None:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        conn.request("POST", "/api/v1/products", "not-json", {"Content-Type": "text/plain"})
        response = conn.getresponse(); response.read(); conn.close()
        self.assertEqual(400, response.status)

    def test_idempotency_replays_same_api_request(self) -> None:
        body = {"sku": "HTTP-1", "name": "HTTP 商品", "sales_price_cents": 1000}
        headers = {"Idempotency-Key": "http-product-1"}
        first_status, first, first_headers = self.request("POST", "/api/v1/products", body, headers)
        second_status, second, second_headers = self.request("POST", "/api/v1/products", body, headers)
        self.assertEqual(201, first_status)
        self.assertEqual(201, second_status)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual("false", first_headers["idempotent-replay"])
        self.assertEqual("true", second_headers["idempotent-replay"])

    def test_idempotency_key_cannot_be_reused_for_different_payload(self) -> None:
        headers = {"Idempotency-Key": "same-key"}
        self.request("POST", "/api/v1/products", {"sku": "HTTP-A", "name": "A"}, headers)
        status, body, _ = self.request("POST", "/api/v1/products", {"sku": "HTTP-B", "name": "B"}, headers)
        self.assertEqual(409, status)
        self.assertEqual("conflict", body["error"]["code"])

    def test_formal_delivery_task_api_persists_task_and_rejects_duplicate_run(self) -> None:
        status, task, _ = self.request("POST", "/api/v1/tasks", {
            "request": "补充任务 API 的正常和失败路径", "requirement_id": "REQ-HTTP-001",
            "business_refs": ["CHANNEL:HTTP-ORDER-1"],
        }, {"Idempotency-Key": "task-create-1"})
        self.assertEqual(201, status)
        self.assertEqual("queued", task["status"])
        self.assertEqual("REQ-HTTP-001", task["requirement_id"])
        self.assertEqual(["CHANNEL:HTTP-ORDER-1"], task["business_refs"])
        status, listing, _ = self.request("GET", "/api/v1/tasks")
        self.assertEqual(200, status)
        self.assertEqual(task["id"], listing["items"][0]["id"])

        def reach_review(store, task_id, actor="system"):
            store.transition(task_id, "spec_ready", "test", spec={"goal": "test"})
            store.transition(task_id, "executing", "test")
            store.transition(task_id, "evaluating", "test")
            return store.transition(task_id, "review", "test", result={
                "summary": {"decision": "pass", "blocking_failed": 0}, "results": [],
            })

        with patch("workbench.api_v2.run_task", side_effect=reach_review):
            run_status, pending, _ = self.request("POST", f"/api/v1/tasks/{task['id']}/run", {}, {"Idempotency-Key": "task-run-1"})
        self.assertEqual(200, run_status)
        self.assertEqual("review", pending["status"])
        replay_status, replay, replay_headers = self.request("POST", f"/api/v1/tasks/{task['id']}/run", {}, {"Idempotency-Key": "task-run-1"})
        self.assertEqual(200, replay_status)
        self.assertEqual(pending["id"], replay["id"])
        self.assertEqual("true", replay_headers["idempotent-replay"])
        duplicate_status, duplicate, _ = self.request("POST", f"/api/v1/tasks/{task['id']}/run", {}, {"Idempotency-Key": "task-run-2"})
        self.assertEqual(409, duplicate_status)
        self.assertEqual("conflict", duplicate["error"]["code"])
        review_status, completed, _ = self.request("POST", f"/api/v1/tasks/{task['id']}/review", {
            "decision": "approve", "note": "阻断证据完整",
        }, {"Idempotency-Key": "task-review-1"})
        self.assertEqual(200, review_status)
        self.assertEqual("completed", completed["status"])
        self.assertEqual("system", completed["reviewed_by"])
        missing_status, _, _ = self.request("GET", "/api/v1/tasks/TASK-NOT-FOUND")
        self.assertEqual(404, missing_status)

    def test_automatic_delivery_request_runs_without_second_click(self) -> None:
        self.app.api.automation.suite_runner = lambda *_args, **_kwargs: {
            "summary": {"decision": "pass", "blocking_failed": 0},
            "results": [{"name": "stock_never_negative", "level": "blocking", "passed": True}],
        }
        status, submitted, _ = self.request("POST", "/api/v1/delivery/requests", {
            "request": "处理 SKU:NOTEBOOK-AI 对应的 CHANNEL_ORDER:HTTP-1001",
            "requirement_id": "REQ-HTTP-AUTO-001",
        }, {"Idempotency-Key": "delivery-request-1"})
        self.assertEqual(202, status)
        self.assertEqual("automatic", submitted["automation_mode"])
        finished = self.app.api.automation.wait(submitted["id"], timeout=5)
        self.assertEqual("review", finished["status"])
        self.assertEqual(
            ["SKU:NOTEBOOK-AI", "CHANNEL_ORDER:HTTP-1001"], finished["business_refs"],
        )
        self.assertIsNone(finished["reviewed_by"])
        replay_status, replay, replay_headers = self.request(
            "POST", "/api/v1/delivery/requests", {
                "request": "处理 SKU:NOTEBOOK-AI 对应的 CHANNEL_ORDER:HTTP-1001",
                "requirement_id": "REQ-HTTP-AUTO-001",
            }, {"Idempotency-Key": "delivery-request-1"},
        )
        self.assertEqual(202, replay_status)
        self.assertEqual(submitted["id"], replay["id"])
        self.assertEqual("true", replay_headers["idempotent-replay"])

    def test_delivery_request_can_authorize_real_code_execution_policy(self) -> None:
        self.app.api.automation.suite_runner = lambda *_args, **_kwargs: {
            "summary": {"decision": "pass", "blocking_failed": 0}, "results": [],
        }
        self.app.api.automation.execution_runner = lambda task: {
            "success": True, "mode": "codex_exec", "changed_files": ["flowerp/service.py"],
            "write_scope": task["write_scope"], "usage": {"total_tokens": 321},
        }
        status, submitted, _ = self.request("POST", "/api/v1/delivery/requests", {
            "request": "实现库存边界检查",
            "execution_mode": "codex",
            "write_scope": ["flowerp", "tests"],
            "execution_timeout_seconds": 300,
        }, {"Idempotency-Key": "delivery-code-1"})
        self.assertEqual(202, status)
        finished = self.app.api.automation.wait(submitted["id"], timeout=5)
        self.assertEqual("review", finished["status"])
        self.assertEqual("codex", finished["execution_mode"])
        self.assertEqual(["flowerp", "tests"], finished["write_scope"])
        execution = next(event for event in finished["events"] if event["detail"] == "受控执行阶段完成")
        self.assertEqual("codex_exec", execution["evidence"]["mode"])
        self.assertEqual(321, execution["evidence"]["usage"]["total_tokens"])

    def test_feedback_api_has_pending_and_named_review_states(self) -> None:
        status, feedback, _ = self.request("POST", "/api/v1/feedback", {
            "task_id": "TASK-HTTP", "source": "验收", "conclusion": "需要补测试", "next_step": "执行失败路径",
        }, {"Idempotency-Key": "feedback-create-1"})
        self.assertEqual(201, status)
        self.assertEqual("pending_review", feedback["status"])
        status, reviewed, _ = self.request("POST", f"/api/v1/feedback/{feedback['id']}/review", {
            "decision": "accept", "note": "结论有效",
        }, {"Idempotency-Key": "feedback-review-1"})
        self.assertEqual(200, status)
        self.assertEqual("accepted", reviewed["status"])
        self.assertEqual("system", reviewed["reviewed_by"])
        duplicate_status, _, _ = self.request("POST", f"/api/v1/feedback/{feedback['id']}/review", {
            "decision": "reject", "note": "重复",
        }, {"Idempotency-Key": "feedback-review-2"})
        self.assertEqual(422, duplicate_status)

    def test_evolution_api_closes_feedback_to_verified_asset_loop(self) -> None:
        missing_status, _, _ = self.request("POST", "/api/v1/evolutions", {
            "feedback_id": "FB-NOTFOUND", "failure_signature": "missing/source",
            "classification": "workbench_control",
        }, {"Idempotency-Key": "evo-missing-feedback"})
        self.assertEqual(404, missing_status)
        status, source, _ = self.request("POST", "/api/v1/tasks", {
            "request": "调查交付详情状态漂移",
            "requirement_id": "REQ-EVO-HTTP-001",
            "business_refs": ["SKU:NOTEBOOK-AI"],
        }, {"Idempotency-Key": "evo-source-task"})
        self.assertEqual(201, status)
        status, feedback, _ = self.request("POST", "/api/v1/feedback", {
            "task_id": source["id"], "source": "web-review",
            "conclusion": "列表完成但详情待启动", "next_step": "固定失败签名",
        }, {"Idempotency-Key": "evo-feedback"})
        self.assertEqual(201, status)
        blocked_status, _, _ = self.request("POST", "/api/v1/evolutions", {
            "feedback_id": feedback["id"], "failure_signature": "delivery/status-drift",
            "classification": "workbench_observability",
        }, {"Idempotency-Key": "evo-before-feedback-review"})
        self.assertEqual(422, blocked_status)
        status, _, _ = self.request("POST", f"/api/v1/feedback/{feedback['id']}/review", {
            "decision": "accept", "note": "已复现",
        }, {"Idempotency-Key": "evo-feedback-review"})
        self.assertEqual(200, status)
        status, evolution, _ = self.request("POST", "/api/v1/evolutions", {
            "feedback_id": feedback["id"], "failure_signature": "delivery/status-drift",
            "classification": "workbench_observability",
        }, {"Idempotency-Key": "evo-create"})
        self.assertEqual(201, status)
        self.assertEqual("proposed", evolution["status"])
        status, evolution, _ = self.request("POST", f"/api/v1/evolutions/{evolution['id']}/review", {
            "decision": "approve", "note": "提升为状态投影约束",
        }, {"Idempotency-Key": "evo-review"})
        self.assertEqual(200, status)
        status, evolution, _ = self.request("POST", f"/api/v1/evolutions/{evolution['id']}/assets", {
            "asset_changes": [
                {"type": "spec", "path": "task-spec:delivery-detail", "reason": "统一状态来源"},
                {"type": "eval", "path": "tests/test_http_api.py", "reason": "增加回归反例"},
            ],
        }, {"Idempotency-Key": "evo-assets"})
        self.assertEqual(200, status)
        status, evolution, _ = self.request("POST", f"/api/v1/evolutions/{evolution['id']}/assets", {
            "asset_changes": [
                {"type": "implementation", "path": "web/app.js", "reason": "统一页面状态投影"},
            ],
        }, {"Idempotency-Key": "evo-assets-append"})
        self.assertEqual(200, status)
        self.assertEqual(3, len(evolution["asset_changes"]))
        candidate = self.app.api.tasks.create(
            "验证状态投影修复", "REQ-EVO-HTTP-002", ["SKU:NOTEBOOK-AI"], actor="system"
        )
        self.app.api.tasks.transition(candidate["id"], "spec_ready", "Spec ready", spec={"goal": "fix"})
        self.app.api.tasks.transition(candidate["id"], "executing", "execute")
        self.app.api.tasks.transition(candidate["id"], "evaluating", "evaluate")
        candidate_report = self.persist_report(candidate["id"], {
            "summary": {"decision": "pass", "blocking_failed": 0},
            "results": [{"name": "stock_never_negative", "level": "blocking", "passed": True}],
        })
        self.app.api.tasks.transition(candidate["id"], "review", "pass", result=candidate_report)
        self.app.api.tasks.review(candidate["id"], "business-owner", "approve", "状态一致")
        status, evolution, _ = self.request("POST", f"/api/v1/evolutions/{evolution['id']}/verify", {
            "candidate_task_id": candidate["id"],
        }, {"Idempotency-Key": "evo-verify"})
        self.assertEqual(200, status)
        self.assertEqual("verified", evolution["status"])
        self.assertEqual(candidate_report["report_path"], evolution["blocking_report"])
        status, listing, _ = self.request("GET", "/api/v1/evolutions")
        self.assertEqual(200, status)
        self.assertEqual(1, listing["verified"])


if __name__ == "__main__":
    unittest.main()
