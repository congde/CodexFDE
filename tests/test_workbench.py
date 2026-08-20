from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.graph import run_graph
from agent.loop import run_loop
from workbench.automation import DeliveryAutomation
from workbench.execution import CodexExecutionRunner, normalize_write_scope
from workbench.evolution import EvolutionStore
from workbench.feedback import add_feedback, review_feedback, summary
from workbench.spec import load_spec, parse_spec
from workbench.task_store import TaskStore
from workbench.workflow import run_task


class WorkbenchTests(unittest.TestCase):
    @staticmethod
    def report(*failures: str) -> dict:
        results = [
            {"name": name, "level": "blocking", "passed": False, "duration_ms": 1, "evidence": "expected failure"}
            for name in failures
        ]
        if not results:
            results.append({
                "name": "stock_never_negative", "level": "blocking", "passed": True,
                "duration_ms": 1, "evidence": "atomic reservation verified",
            })
        return {
            "summary": {"decision": "block" if failures else "pass", "blocking_failed": len(failures)},
            "results": results,
        }

    @staticmethod
    def persist_report(database_path: str, task_id: str, report: dict) -> dict:
        report_dir = Path(database_path).parent / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{task_id}-harness-blocking.json"
        report["report_path"] = (Path("reports") / report_path.name).as_posix()
        payload = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
        report_path.write_bytes(payload)
        report["report_sha256"] = hashlib.sha256(payload).hexdigest()
        return report

    def test_spec_parser_requires_all_sections(self) -> None:
        self.assertIn("库存", load_spec().goal)
        with self.assertRaises(ValueError): parse_spec("## 目标\n只有目标")

    def test_task_state_is_persistent_and_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.db"; store = TaskStore(path); task = store.create(
                "测试", "REQ-TEST-001", ["SKU:NOTEBOOK-AI"], actor="tester",
            )
            store.transition(task["id"], "spec_ready", "ok", spec={"goal": "x"})
            restored = TaskStore(path).get(task["id"])
            self.assertEqual("spec_ready", restored["status"]); self.assertEqual("x", restored["spec"]["goal"])
            self.assertEqual("REQ-TEST-001", restored["requirement_id"])
            self.assertEqual(["SKU:NOTEBOOK-AI"], restored["business_refs"])
            self.assertEqual("tester", restored["events"][0]["actor"])
            with self.assertRaises(ValueError): store.transition(task["id"], "completed")

    def test_task_persists_explicit_code_execution_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.db")
            task = store.create(
                "实现库存接口", execution_mode="codex",
                write_scope=["flowerp", "tests/test_flowerp.py"], execution_timeout_seconds=300,
            )
            restored = TaskStore(Path(tmp) / "tasks.db").get(task["id"])
            self.assertEqual("codex", restored["execution_mode"])
            self.assertEqual(["flowerp", "tests/test_flowerp.py"], restored["write_scope"])
            self.assertEqual(300, restored["execution_timeout_seconds"])
            with self.assertRaises(ValueError):
                normalize_write_scope(["../outside"])
            with self.assertRaises(ValueError):
                normalize_write_scope([".env"])

    def test_codex_execution_runner_records_real_diff_commands_and_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp); (workspace / "flowerp").mkdir()
            target = workspace / "flowerp" / "service.py"; target.write_text("before = 1\n", encoding="utf-8")

            def process(command, **_kwargs):
                target.write_text("before = 2\n", encoding="utf-8")
                result_path = Path(command[command.index("--output-last-message") + 1])
                result_path.write_text(json.dumps({
                    "summary": "changed", "tests": ["unit"], "risks": [], "next_step": "review",
                }), encoding="utf-8")
                stdout = "\n".join((
                    json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                    json.dumps({"type": "item.completed", "item": {
                        "type": "command_execution", "command": "python -m unittest", "status": "completed",
                        "exit_code": 0,
                    }}),
                    json.dumps({"type": "turn.completed", "usage": {
                        "input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 30,
                        "total_tokens": 130,
                    }}),
                ))
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

            runner = CodexExecutionRunner(
                workspace, workspace / ".runtime", executable="fake-codex", process_runner=process,
            )
            evidence = runner({
                "id": "TASK-EXECUTE01", "request": "change", "execution_mode": "codex",
                "write_scope": ["flowerp"], "execution_timeout_seconds": 60,
            })
            self.assertTrue(evidence["success"])
            self.assertEqual(["flowerp/service.py"], evidence["changed_files"])
            self.assertIn("-before = 1", evidence["diff"])
            self.assertIn("+before = 2", evidence["diff"])
            self.assertEqual(130, evidence["usage"]["total_tokens"])
            self.assertEqual("python -m unittest", evidence["commands"][0]["command"])
            self.assertEqual("thread-1", evidence["thread_id"])

    def test_codex_execution_runner_blocks_out_of_scope_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp); (workspace / "flowerp").mkdir()
            outside = workspace / "README.md"; outside.write_text("before\n", encoding="utf-8")

            def process(command, **_kwargs):
                outside.write_text("after\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            evidence = CodexExecutionRunner(
                workspace, workspace / ".runtime", executable="fake-codex", process_runner=process,
            )({
                "id": "TASK-OUTSIDE01", "request": "change", "execution_mode": "codex",
                "write_scope": ["flowerp"], "execution_timeout_seconds": 60,
            })
            self.assertFalse(evidence["success"])
            self.assertEqual(["README.md"], evidence["out_of_scope_files"])
            self.assertIn("越界写入", evidence["message"])

    def test_green_workflow_stops_for_named_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.db")
            task = store.create("验证具名审核", "REQ-REVIEW-001")
            report = {
                "summary": {"decision": "pass", "blocking_failed": 0, "blocking_passed": 1},
                "results": [{"name": "stock_never_negative", "passed": True, "level": "blocking"}],
            }
            pending = run_task(store, task["id"], "executor-a", suite_runner=lambda *_args, **_kwargs: report)
            self.assertEqual("review", pending["status"])
            self.assertIsNone(pending["reviewed_by"])
            with self.assertRaises(ValueError):
                store.transition(task["id"], "completed", "anonymous")
            completed = store.review(task["id"], "reviewer-a", "approve", "证据与 Spec 一致")
            self.assertEqual("completed", completed["status"])
            self.assertEqual("reviewer-a", completed["reviewed_by"])
            self.assertEqual("approve", completed["review_decision"])

    def test_failed_workflow_enters_rework_without_human_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.db")
            task = store.create("验证失败回退")
            report = self.report("stock_never_negative")
            result = run_task(store, task["id"], suite_runner=lambda *_args, **_kwargs: report)
            self.assertEqual("rework", result["status"])
            self.assertEqual(1, result["result"]["summary"]["blocking_failed"])

    def test_automatic_pipeline_builds_task_spec_and_stops_at_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.db")
            automation = DeliveryAutomation(
                store, tmp, suite_runner=lambda *_args, **_kwargs: self.report(),
                execution_runner=lambda task: {
                    "mode": "test_executor", "changed_files": ["flowerp/service.py"],
                    "requirement_id": task["requirement_id"],
                },
            )
            submitted = automation.submit(
                "修复 SKU:NOTEBOOK-AI 在 CHANNEL_ORDER:EC-1001 缺货时的原子预占",
                business_refs=["SKU:NOTEBOOK-AI"], actor="requester-a",
            )
            finished = automation.wait(submitted["id"])
            self.assertEqual("automatic", finished["automation_mode"])
            self.assertTrue(finished["requirement_id"].startswith("REQ-AUTO-"))
            self.assertEqual(
                ["SKU:NOTEBOOK-AI", "CHANNEL_ORDER:EC-1001"], finished["business_refs"],
            )
            self.assertEqual("review", finished["status"])
            self.assertIn("原子预占", finished["spec"]["goal"])
            self.assertIn("整单失败", finished["spec"]["acceptance"])
            self.assertIn("平台订单重复推送", finished["spec"]["acceptance"])
            self.assertTrue(Path(finished["spec_path"]).is_file())
            report_path = Path(store.path).parent / finished["result"]["report_path"]
            self.assertTrue(report_path.is_file())
            self.assertEqual(
                hashlib.sha256(report_path.read_bytes()).hexdigest(),
                finished["result"]["report_sha256"],
            )
            self.assertEqual(
                ["queued", "queued", "queued", "spec_ready", "executing", "executing", "evaluating", "review"],
                [event["to_status"] for event in finished["events"]],
            )
            execution_event = next(event for event in finished["events"] if event["detail"] == "受控执行阶段完成")
            self.assertEqual(["flowerp/service.py"], execution_event["evidence"]["changed_files"])
            self.assertTrue(all("evidence_json" not in event for event in finished["events"]))
            self.assertIsNone(finished["reviewed_by"])

    def test_automatic_pipeline_preserves_blocking_failure_for_rework(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.db")
            automation = DeliveryAutomation(
                store, tmp, suite_runner=lambda *_args, **_kwargs: self.report("stock_never_negative"),
            )
            submitted = automation.submit(
                "检查缺货订单", "REQ-AUTO-FAIL", ["SALES_ORDER:SO-1001"], actor="requester-a",
            )
            finished = automation.wait(submitted["id"])
            self.assertEqual("rework", finished["status"])
            self.assertEqual(1, finished["result"]["summary"]["blocking_failed"])
            self.assertIsNone(finished["reviewed_by"])

    def test_automatic_pipeline_recovers_interrupted_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.db")
            task = store.create(
                "恢复中断任务", "REQ-RECOVER-001", ["SKU:NOTEBOOK-AI"],
                automation_mode="automatic",
            )
            store.transition(task["id"], "spec_ready", "spec", spec={"goal": "recover"})
            store.transition(task["id"], "executing", "execute")
            store.transition(task["id"], "evaluating", "evaluate")
            automation = DeliveryAutomation(
                store, tmp, suite_runner=lambda *_args, **_kwargs: self.report(),
            )
            self.assertEqual([task["id"]], automation.recover())
            finished = automation.wait(task["id"])
            self.assertEqual("review", finished["status"])
            self.assertTrue(any(
                event["actor"] == "automation-recovery" and event["to_status"] == "rework"
                for event in finished["events"]
            ))

    def test_automatic_pipeline_bounds_workers_and_drains_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.db")
            entered = threading.Event(); release = threading.Event(); calls = 0

            def runner(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    entered.set(); release.wait(3)
                return self.report()

            automation = DeliveryAutomation(store, tmp, suite_runner=runner, max_workers=1)
            first = automation.submit("任务一 SKU:SKU-A", "REQ-QUEUE-001")
            self.assertTrue(entered.wait(2))
            second = automation.submit("任务二 SKU:SKU-B", "REQ-QUEUE-002")
            queued = store.get(second["id"])
            self.assertEqual("queued", queued["status"])
            self.assertTrue(any("等待可用 Worker" in event["detail"] for event in queued["events"]))
            release.set()
            self.assertEqual("review", automation.wait(first["id"], 5)["status"])
            self.assertEqual("review", automation.wait(second["id"], 5)["status"])

    def test_loop_stops_when_green(self) -> None:
        result = run_loop(max_rounds=3)
        self.assertEqual("converged", result["status"]); self.assertEqual(1, result["rounds"])

    def test_graph_trace_reaches_human_review(self) -> None:
        with patch("agent.graph.run_suite", return_value=self.report()):
            result = run_graph(max_rounds=3, reject_once=True)
        self.assertEqual("completed", result["status"])
        self.assertTrue(any(step["to"] == "human_review" for step in result["trace"]))
        self.assertTrue(any(step["to"] == "develop" and "人工打回" in step["reason"] for step in result["trace"]))

    def test_graph_persists_human_review_and_resumes_with_named_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "delivery.json"
            with patch("agent.graph.run_suite", return_value=self.report()):
                pending = run_graph(require_human_review=True, state_file=state_file)
            self.assertEqual("awaiting_human_review", pending["status"])
            self.assertTrue(state_file.is_file())
            approved = run_graph(state_file=state_file, review_decision="approve", reviewer="reviewer-a")
            self.assertEqual("completed", approved["status"])
            self.assertEqual("reviewer-a", approved["reviewer"])
            self.assertEqual("approve", approved["review_decision"])
            self.assertTrue(approved["reviewed_at"])

    def test_graph_rejects_anonymous_review(self) -> None:
        with self.assertRaises(ValueError):
            run_graph(review_decision="approve", reviewer="")

    def test_loop_enforces_measured_token_budget(self) -> None:
        reports = iter((self.report("first"), self.report("second")))
        result = run_loop(
            max_rounds=3, token_budget=50, timeout_seconds=30, use_codex=True,
            suite_runner=lambda *_args, **_kwargs: next(reports),
            executor=lambda *_args: {"returncode": 0, "usage": {"total_tokens": 60}},
        )
        self.assertEqual("stopped_token_budget", result["status"])
        self.assertEqual(60, result["tokens_used"])
        self.assertEqual(0, result["tokens_remaining"])

    def test_loop_stops_if_executor_cannot_report_usage(self) -> None:
        result = run_loop(
            token_budget=50, timeout_seconds=30, use_codex=True,
            suite_runner=lambda *_args, **_kwargs: self.report("first"),
            executor=lambda *_args: {"returncode": 0},
        )
        self.assertEqual("stopped_token_usage_unavailable", result["status"])

    def test_feedback_requires_review_and_records_named_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "feedback.db")
            item = add_feedback("TASK-1", "demo", "需要改进", "补边界测试", path)
            self.assertEqual("pending_review", item["status"])
            reviewed = review_feedback(item["id"], "reviewer-a", "accept", "证据充分", path)
            self.assertEqual("accepted", reviewed["status"])
            self.assertEqual("reviewer-a", reviewed["reviewed_by"])
            self.assertEqual(1, summary(path)["accepted"])
            with self.assertRaises(ValueError):
                review_feedback(item["id"], "reviewer-b", "reject", "重复审核", path)

    def test_evolution_requires_governed_assets_and_next_verified_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "workbench.db")
            tasks = TaskStore(path)
            source = tasks.create("修复库存状态裂缝", "REQ-EVO-001", ["SKU:NOTEBOOK-AI"], actor="author")
            feedback = add_feedback(source["id"], "web-review", "详情状态与列表不一致", "固定失败签名", path)
            evolutions = EvolutionStore(path)
            with self.assertRaises(ValueError):
                evolutions.create(feedback["id"], "delivery/status-drift", "workbench_observability")
            review_feedback(feedback["id"], "reviewer-a", "accept", "已独立复现", path)
            evolution = evolutions.create(
                feedback["id"], "delivery/status-drift", "workbench_observability", actor="reviewer-a"
            )
            self.assertEqual("proposed", evolution["status"])
            self.assertEqual(["SKU:NOTEBOOK-AI"], evolution["business_refs"])
            with self.assertRaises(ValueError):
                evolutions.create(feedback["id"], "duplicate", "workbench_observability")
            approved = evolutions.review(evolution["id"], "reviewer-b", "approve", "提升为状态投影约束")
            self.assertEqual("approved", approved["status"])
            changed = evolutions.record_assets(evolution["id"], [
                {"type": "spec", "path": "task-spec:delivery-detail", "reason": "统一状态来源"},
                {"type": "eval", "path": "tests/test_http_api.py", "reason": "保留旧红新绿反例"},
            ], "author")
            self.assertEqual("asset_changed", changed["status"])
            changed = evolutions.record_assets(evolution["id"], [
                {"type": "implementation", "path": "web/app.js", "reason": "统一页面状态投影"},
            ], "author")
            self.assertEqual(3, len(changed["asset_changes"]))
            with self.assertRaises(ValueError):
                evolutions.record_assets(evolution["id"], [
                    {"type": "implementation", "path": "web/app.js", "reason": "重复路径"},
                ], "author")
            with self.assertRaises(ValueError):
                evolutions.verify(evolution["id"], source["id"], ".runtime/eval/blocking-report.json", "verifier")
            unrelated = tasks.create("验证另一个订单", "REQ-EVO-UNRELATED", ["ORDER:SO-OTHER"], actor="author")
            tasks.transition(unrelated["id"], "spec_ready", "Spec ready", spec={"goal": "other"})
            tasks.transition(unrelated["id"], "executing", "execute")
            tasks.transition(unrelated["id"], "evaluating", "evaluate")
            unrelated_report = self.persist_report(path, unrelated["id"], self.report())
            tasks.transition(unrelated["id"], "review", "pass", result=unrelated_report)
            tasks.review(unrelated["id"], "business-owner", "approve", "另一个对象已验证")
            with self.assertRaises(ValueError):
                evolutions.verify(evolution["id"], unrelated["id"], "", "verifier")
            candidate = tasks.create("交付状态投影修复", "REQ-EVO-002", ["SKU:NOTEBOOK-AI"], actor="author")
            tasks.transition(candidate["id"], "spec_ready", "Spec ready", spec={"goal": "fix"})
            tasks.transition(candidate["id"], "executing", "execute")
            tasks.transition(candidate["id"], "evaluating", "evaluate")
            candidate_report = self.persist_report(path, candidate["id"], self.report())
            tasks.transition(candidate["id"], "review", "pass", result=candidate_report)
            tasks.review(candidate["id"], "business-owner", "approve", "状态一致且证据完整")
            with self.assertRaises(ValueError):
                evolutions.verify(evolution["id"], candidate["id"], "wrong-report.json", "verifier")
            report_file = Path(path).parent / candidate_report["report_path"]
            original_report = report_file.read_bytes()
            report_file.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                evolutions.verify(evolution["id"], candidate["id"], "", "verifier")
            report_file.write_bytes(original_report)
            verified = evolutions.verify(
                evolution["id"], candidate["id"], "", "verifier"
            )
            self.assertEqual("verified", verified["status"])
            self.assertEqual(candidate["id"], verified["candidate_task_id"])
            self.assertEqual("verifier", verified["verified_by"])
            self.assertEqual(5, len(verified["events"]))
            self.assertEqual(1, evolutions.summary()["verified"])

    def test_rejected_evolution_cannot_register_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "workbench.db")
            tasks = TaskStore(path)
            source = tasks.create("调查反馈", "REQ-EVO-003", ["ORDER:SO-001"])
            feedback = add_feedback(source["id"], "support", "无法复现", "等待更多证据", path)
            review_feedback(feedback["id"], "reviewer-a", "accept", "允许调查", path)
            evolutions = EvolutionStore(path)
            evolution = evolutions.create(feedback["id"], "unreproduced/order", "erp_workflow")
            evolutions.review(evolution["id"], "reviewer-b", "reject", "独立环境无法复现")
            with self.assertRaises(ValueError):
                evolutions.record_assets(evolution["id"], [
                    {"type": "spec", "path": "FDE_SPEC.md", "reason": "不应写入"},
                ], "author")


if __name__ == "__main__": unittest.main()
