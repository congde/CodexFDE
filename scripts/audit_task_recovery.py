"""Real process-interruption audit. The first Eval is paused, never replaced by a green fixture."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path


def worker(runtime: Path, attempt: str, pause: bool) -> None:
    from workbench.cockpit import lesson_eval_runner
    from workbench.workbench_server import WorkbenchApp, make_handler

    def factory(lesson):
        runner = lesson_eval_runner(lesson)
        def evaluate(*args, **kwargs):
            if pause:
                (runtime / "eval-entered.json").write_text(json.dumps({"lesson": lesson}), encoding="utf-8")
                while True:
                    time.sleep(0.1)
            return runner(*args, **kwargs)
        return evaluate

    app = WorkbenchApp(runtime, eval_factory=factory)
    app.automation.recover()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    (runtime / f"{attempt}-ready.json").write_text(json.dumps({"port": server.server_port}), encoding="utf-8")
    server.serve_forever()


def await_file(path: Path, process: subprocess.Popen, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"服务提前退出：{process.returncode}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            time.sleep(0.05)
    raise TimeoutError(str(path))


def request(port, method, path, body=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None,
                           {"Content-Type": "application/json", "Idempotency-Key": "recovery-audit"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def audit(runtime: Path) -> dict:
    runtime.mkdir(parents=True, exist_ok=False)
    processes, logs = [], []
    evidence = {"basis": "maintainer_process_interruption", "student_achievement": False,
                "real_codex_execution": False, "runtime": str(runtime)}

    def launch(attempt, pause=False):
        log = (runtime / f"{attempt}.log").open("w", encoding="utf-8")
        logs.append(log)
        command = [sys.executable, "-X", "utf8", "-m", "scripts.audit_task_recovery",
                   "--worker", "--runtime", str(runtime), "--attempt", attempt]
        if pause:
            command.append("--pause")
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process, await_file(runtime / f"{attempt}-ready.json", process)["port"]

    try:
        first, port = launch("first", True)
        body = {"lesson": 13, "request": "服务中断后复验采购审批边界", "actor": "maintainer-audit"}
        code, accepted = request(port, "POST", "/api/v1/delivery/requests", body)
        assert code == 202
        task_id = accepted["task_id"]
        evidence["accepted"] = accepted
        await_file(runtime / "eval-entered.json", first)
        code, before = request(port, "GET", accepted["status_url"])
        assert code == 200 and before["status"] == "evaluating"
        evidence["before"] = before
        first.kill()
        first.wait(timeout=10)
        evidence["terminated_pid"] = first.pid
        evidence["terminated_returncode"] = first.returncode
        second, port = launch("second")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            code, after = request(port, "GET", accepted["status_url"])
            if after["status"] in {"review", "rework", "failed", "dead_letter"}:
                if after["status"] != "rework" or after.get("result"):
                    break
            time.sleep(0.1)
        else:
            raise TimeoutError("恢复任务未收敛")
        evidence["after"] = after
        assert after["status"] == "review" and not after["reviewed_by"]
        assert after["result"]["summary"]["decision"] == "pass"
        assert any(event["detail"] == "检测到中断执行，进入安全重放" for event in after["events"])
        assert {event["id"] for event in before["events"]} <= {event["id"] for event in after["events"]}
        code, replay = request(port, "POST", "/api/v1/delivery/requests", body)
        assert code == 202 and replay["task_id"] == task_id
        code, conflict = request(port, "POST", "/api/v1/delivery/requests", {**body, "request": "不同需求"})
        assert code == 409
        evidence["conflict"] = conflict
        evidence["verified"] = True
        return evidence
    except Exception as exc:
        evidence.update(verified=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for log in logs:
            log.close()
        (runtime / "audit.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--pause", action="store_true")
    parser.add_argument("--attempt", default="first")
    parser.add_argument("--runtime")
    args = parser.parse_args()
    runtime = Path(args.runtime or f".runtime/task-recovery-audits/{uuid.uuid4().hex}").resolve()
    if args.worker:
        worker(runtime, args.attempt, args.pause)
    else:
        result = audit(runtime)
        print(json.dumps({"verified": result["verified"], "report": str(runtime / "audit.json")}, ensure_ascii=False))
