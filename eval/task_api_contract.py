"""Exercise the task HTTP router; the injected Eval is a boundary fixture only."""
from pathlib import Path


def check_task_api(runtime):
    return check_required_workbench(runtime)


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
