from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from eval.harness import run_suite

from .spec import normalize_business_refs, normalize_requirement_id, write_delivery_spec
from .task_store import TaskStore
from .workflow import run_task

AgentRunner = Callable[[str, str, str], dict]


SuiteRunner = Callable[..., dict]
ExecutionRunner = Callable[[dict], dict]


class DeliveryAutomation:
    """Durable, dependency-light delivery pipeline.

    Automation deliberately stops at ``review`` or ``rework``. Human approval is
    a separate authenticated API action and is never executed by this worker.
    """

    def __init__(self, store: TaskStore, runtime_dir: str | Path,
                 suite_runner: SuiteRunner = run_suite, max_workers: int = 2,
                 execution_runner: ExecutionRunner | None = None,
                 max_attempts: int = 3,
                 agent_runner: AgentRunner | None = None) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须至少为 1")
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts 必须在 1 到 10 之间")
        self.store = store
        self.runtime_dir = Path(runtime_dir).resolve()
        self.suite_runner = suite_runner
        self.execution_runner = execution_runner
        self.agent_runner = agent_runner
        self.max_workers = max_workers
        self.max_attempts = max_attempts
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._pending: list[tuple[str, str]] = []

    def submit(self, request: str, requirement_id: str = "",
               business_refs: list[str] | None = None, actor: str = "system",
               execution_mode: str = "verify", write_scope: list[str] | None = None,
               execution_timeout_seconds: int = 900) -> dict:
        requirement = normalize_requirement_id(requirement_id)
        refs = normalize_business_refs(request, business_refs)
        if not refs:
            refs = [f"REQUIREMENT:{requirement}"]
        task_id = f"TASK-{uuid.uuid4().hex[:10].upper()}"
        spec_path = self.runtime_dir / "specs" / f"{task_id}.md"
        task = self.store.create(
            request=request,
            requirement_id=requirement,
            business_refs=refs,
            spec_path=str(spec_path),
            actor=actor,
            automation_mode="automatic",
            task_id=task_id,
            execution_mode=execution_mode,
            write_scope=write_scope,
            execution_timeout_seconds=execution_timeout_seconds,
        )
        try:
            write_delivery_spec(spec_path, task["request"], requirement, refs)
            self.store.append_event(
                task_id, "已从需求生成任务级结构化 Spec", actor="automation",
                evidence={"spec_path": str(spec_path), "business_refs": refs},
            )
        except Exception as exc:
            return self.store.transition(
                task_id, "failed", "自动生成 Spec 失败", actor="automation",
                evidence={"error_type": type(exc).__name__}, error=str(exc),
            )
        self.start(task_id, actor="automation")
        return self.store.get(task_id)

    def capabilities(self) -> dict:
        runner = self.execution_runner
        if runner is not None and hasattr(runner, "capabilities"):
            result = runner.capabilities()
            if isinstance(result, dict):
                return result
        return {
            "codex_available": False,
            "sandbox": "workspace-write",
            "reason": "工作台未配置 Codex 代码执行器",
        }

    def start(self, task_id: str, actor: str = "automation") -> dict:
        with self._lock:
            existing = self._threads.get(task_id)
            if existing and existing.is_alive():
                return self.store.get(task_id)
            if any(pending_id == task_id for pending_id, _ in self._pending):
                return self.store.get(task_id)
            task = self.store.get(task_id)
            if task["status"] not in {"queued", "rework"}:
                raise ValueError("自动流水线只允许 queued 或可安全重放的 rework 任务启动")
            if len(self._threads) >= self.max_workers:
                self._pending.append((task_id, actor))
                self.store.append_event(
                    task_id, "自动流水线等待可用 Worker", actor=actor,
                    evidence={"max_workers": self.max_workers},
                )
                return self.store.get(task_id)
            worker = threading.Thread(
                target=self._execute,
                args=(task_id, actor),
                name=f"delivery-{task_id}",
                daemon=True,
            )
            self._threads[task_id] = worker
            worker.start()
        return self.store.get(task_id)

    def _execute(self, task_id: str, actor: str) -> None:
        retry = False
        try:
            attempts = sum(
                event["detail"] == "自动流水线开始推进"
                for event in self.store.get(task_id).get("events", [])
            ) + 1
            self.store.append_event(
                task_id, "自动流水线开始推进", actor=actor,
                evidence={
                    "stages": ["spec", "execute", "blocking_eval", "human_review"],
                    "human_review_is_automatic": False,
                    "attempt": attempts,
                    "max_attempts": self.max_attempts,
                },
            )
            suite_runner = self.suite_runner
            if hasattr(suite_runner, "for_task"):
                suite_runner = suite_runner.for_task(self.store.get(task_id))
            if self.agent_runner:
                result = self.agent_runner(task_id, actor)
            else:
                result = run_task(
                    self.store, task_id, actor=actor, suite_runner=suite_runner,
                    execution_runner=self.execution_runner,
                )
            if result["status"] == "rework" and attempts < self.max_attempts:
                retry = True
                self.store.append_event(
                    task_id, "自动流水线安排有界重试", actor=actor,
                    evidence={
                        "attempt": attempts,
                        "next_attempt": attempts + 1,
                        "max_attempts": self.max_attempts,
                        "last_error": result.get("error"),
                        "last_eval": (result.get("result") or {}).get("summary"),
                    },
                )
            elif result["status"] in {"rework", "failed"}:
                self.store.transition(
                    task_id, "dead_letter", "自动流水线已耗尽重试或遇到不可重试失败，转人工处理",
                    actor=actor,
                    evidence={
                        "attempts": attempts,
                        "max_attempts": self.max_attempts,
                        "last_status": result["status"],
                        "last_error": result.get("error"),
                        "last_eval": (result.get("result") or {}).get("summary"),
                    },
                    error=result.get("error") or "自动流水线未能收敛",
                )
        finally:
            next_item: tuple[str, str] | None = None
            with self._lock:
                self._threads.pop(task_id, None)
                if retry:
                    self._pending.append((task_id, actor))
                if self._pending:
                    next_item = self._pending.pop(0)
            if next_item:
                self.start(*next_item)

    def wait(self, task_id: str, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                # Read the durable state while holding the same lock used when
                # workers leave ``_threads``. Otherwise a worker can finish and
                # unregister between these two reads, making an old recovery
                # checkpoint look terminal even though the DB already says
                # ``review``.
                task = self.store.get(task_id)
                worker = self._threads.get(task_id)
                active = bool((worker and worker.is_alive()) or any(
                    pending_id == task_id for pending_id, _ in self._pending
                ))
            # ``rework`` is both a durable replay checkpoint and a terminal
            # blocking-Eval result. During restart recovery a worker owns that
            # checkpoint, so returning it early would leave SQLite in use and
            # expose an intermediate state as the final outcome.
            if task["status"] in {"review", "completed", "dead_letter"} or (
                task["status"] in {"rework", "failed"} and not active
            ):
                return task
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"等待自动流水线超时：{task_id}")
            if worker:
                worker.join(min(remaining, 0.25))
            else:
                time.sleep(min(remaining, 0.05))

    def recover(self) -> list[str]:
        recovered = self.store.recover_automatic_tasks()
        for task_id in recovered:
            self.start(task_id, actor="automation-recovery")
        return recovered

    def is_active(self, task_id: str) -> bool:
        with self._lock:
            worker = self._threads.get(task_id)
            return bool((worker and worker.is_alive()) or any(
                pending_id == task_id for pending_id, _ in self._pending
            ))
