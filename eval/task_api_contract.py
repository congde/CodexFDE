"""Exercise the task HTTP router; the injected Eval is a boundary fixture only."""
from pathlib import Path


def check_task_api(runtime: Path) -> None:
    from workbench.server import App
    from workbench.task_store import TaskStore

    app = App(runtime)
    app.api.automation.suite_runner = lambda *_a, **_k: {
        "summary": {"decision": "pass", "blocking_failed": 0},
        "results": [{"name": "api_boundary_fixture", "level": "blocking", "passed": True}],
    }
    body = {"request": "验证补货请求的受理与人审边界", "requirement_id": "REQ-API-EVAL",
            "business_refs": ["PURCHASE:API-EVAL"]}
    headers = {"idempotency-key": "api-eval-submit"}
    response = app.api.dispatch("POST", "/api/v1/delivery/requests", headers, body, "127.0.0.1")
    assert response.status == 202, "提交 API 尚未提供 202 受理能力"
    task_id = response.body["id"]
    try:
        task = app.api.automation.wait(task_id, timeout=10)
        assert task["status"] == "review" and not task["reviewed_by"], "全绿不得自动完成人审"
        assert task["execution_mode"] == "verify", "未授权请求不得执行 Codex"
        replay = app.api.dispatch("POST", "/api/v1/delivery/requests", headers, body, "127.0.0.1")
        assert replay.status == 202 and replay.body["id"] == task_id
        conflict = app.api.dispatch("POST", "/api/v1/delivery/requests", headers,
                                    {**body, "request": "另一条需求"}, "127.0.0.1")
        assert conflict.status == 409, "同一幂等键不能悄悄替换需求"
        queried = app.api.dispatch("GET", f"/api/v1/tasks/{task_id}", {}, None, "127.0.0.1")
        reopened = TaskStore(runtime / "workbench.db").get(task_id)
        assert queried.status == 200 and queried.body["status"] == reopened["status"] == "review"
        assert reopened["events"] and reopened["business_refs"] == body["business_refs"]
        missing = app.api.dispatch("GET", "/api/v1/tasks/TASK-MISSING", {}, None, "127.0.0.1")
        assert missing.status == 404
    finally:
        app.api.automation.wait(task_id, timeout=10)


def check_required_workbench(runtime: Path) -> None:
    """Required :8001 service contract; Eval fixture prevents recursive evaluation."""
    from workbench.workbench_server import WorkbenchApp
    from workbench.task_store import TaskSubmissionConflict

    def factory(lesson):
        assert lesson == 13
        return lambda *_a, **_k: {
            "summary": {"decision": "pass", "blocking_failed": 0},
            "results": [{"name": "required_api_boundary_fixture", "level": "blocking", "passed": True}],
        }

    app = WorkbenchApp(runtime, eval_factory=factory)
    accepted = app.accept_course_task(13, "复验采购审批", "eval-student", "required-api-eval")
    task_id = accepted["task_id"]
    try:
        task = app.automation.wait(task_id)
        assert task["status"] == "review" and task["execution_mode"] == "verify"
        assert not task["reviewed_by"]
        replay = app.accept_course_task(13, "复验采购审批", "eval-student", "required-api-eval")
        assert replay["task_id"] == task_id and len(app.tasks.list()) == 1
        try:
            app.accept_course_task(13, "另一需求", "eval-student", "required-api-eval")
        except TaskSubmissionConflict:
            pass
        else:
            raise AssertionError("工作台接受了同键不同需求")
        assert WorkbenchApp(runtime).tasks.get(task_id)["status"] == "review"
        assert not (runtime / "flowerp.db").exists()
    finally:
        app.automation.wait(task_id)
