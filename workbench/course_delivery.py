"""Shared course delivery orchestration for command-line and local workbench callers.

An explicit execute_code argument authorizes only the isolated course workspace.
This module does not approve or promote candidates into the source repository.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from eval.harness import run_suite
from .course_mainline import create_lesson_task, lesson_baseline_status, lesson_contract
from .course_workspace import CourseWorktreeManager, LessonSubprocessEvalRunner, differential_evidence, dynamic_start_evidence
from .execution import CodexExecutionRunner
from .task_store import TaskStore
from .workflow import run_task
from .bootstrap_handoff import require_bootstrap_ticket, link_bootstrap_ticket


def submit_course(*, repository_root: str | Path, runtime_dir: str | Path,
                  lesson_number: int, actor: str, execute_code: bool,
                  execution_timeout: int = 900, eval_cases: tuple[str, ...] = (),
                  session_baseline_ref: str | None = None,
                  expected_baseline_commit: str | None = None,
                  bootstrap_task_id: str | None = None,
                  requirement_spec_text: str | None = None,
                  write_scope: tuple[str, ...] | None = None,
                  on_task_created=None) -> dict:
    if type(execute_code) is not bool:
        raise ValueError("execute_code 必须是明确的布尔授权")
    if not 4 <= lesson_number <= 16:
        raise ValueError("课程交付只允许 L04-L16")
    repository = Path(repository_root).resolve()
    runtime = Path(runtime_dir).resolve()
    task_store = TaskStore(runtime / "workbench.db")
    bootstrap = require_bootstrap_ticket(task_store, bootstrap_task_id, repository) if lesson_number == 4 and execute_code else None
    lesson = lesson_contract(lesson_number)
    dynamic_cases = tuple(dict.fromkeys(eval_cases))
    if lesson.dynamic_eval_required:
        if not dynamic_cases:
            raise ValueError(f"L{lesson_number:02d} 必须用 --eval-case 声明本次需求新增的 Eval")
        reused = sorted(set(dynamic_cases) & set(lesson.eval_cases))
        if reused:
            raise ValueError("动态 Eval 必须是本次需求新增用例，不能重复静态合同 Eval：" + ", ".join(reused))
        if execute_code and not session_baseline_ref:
            raise ValueError(f"L{lesson_number:02d} 真实执行必须提供 --session-baseline-ref")
    selected_cases = lesson.eval_cases + dynamic_cases
    baseline = lesson_baseline_status(repository, lesson_number)
    if expected_baseline_commit and baseline.get("baseline_commit") != expected_baseline_commit:
        raise ValueError("课程基线已变化，请重新确认执行方案")
    if execute_code and not baseline["baseline_commit"]:
        raise ValueError(
            f"真实课程执行需要 {lesson.baseline_ref} 起始标签；请先建设课程基线"
        )
    task = create_lesson_task(
        task_store, lesson_number, runtime, actor=actor,
        execution_mode="codex" if execute_code else "verify",
        execution_timeout_seconds=execution_timeout,
        additional_eval_cases=dynamic_cases,
        requirement_spec_text=requirement_spec_text,
        write_scope=write_scope,
    )
    if bootstrap:
        try:
            link_bootstrap_ticket(task_store, bootstrap, task['id'], actor)
        except ValueError as error:
            task_store.transition(task['id'], 'failed', '前置验收关联失败，未启动代码执行',
                                  actor=actor, error=str(error))
            raise
    if on_task_created is not None:
        on_task_created(task)
    workspace = repository
    pre_report = None
    if execute_code:
        try:
            worktree_ref = lesson.baseline_ref
            qualified_ref = False
            if session_baseline_ref:
                resolved = subprocess.run(
                    ["git", "rev-parse", "--verify", f"{session_baseline_ref}^{{commit}}"],
                    cwd=repository, text=True, capture_output=True, check=False,
                )
                if resolved.returncode != 0:
                    raise ValueError("会话基线无法解析为 Git 提交")
                ancestry = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", baseline["baseline_commit"], resolved.stdout.strip()],
                    cwd=repository, capture_output=True, check=False,
                )
                if ancestry.returncode != 0:
                    raise ValueError("会话基线必须位于本讲固定起始基线之后")
                worktree_ref = resolved.stdout.strip()
                qualified_ref = True
            isolation = CourseWorktreeManager(repository, runtime).prepare(
                task["id"], worktree_ref, qualified_ref=qualified_ref,
                lesson_number=lesson_number,
            )
            workspace = Path(isolation["path"])
            task_store.append_event(
                task["id"], "已创建课程隔离 Worktree", actor=actor, evidence=isolation,
            )
            pre_runner = LessonSubprocessEvalRunner(
                workspace, runtime, task["id"], selected_cases, "pre",
            )
            pre_report = pre_runner()
            task_store.append_event(
                task["id"], "执行前课程 Eval 已完成", actor=actor,
                evidence={"summary": pre_report.get("summary"), "runner": pre_report.get("runner")},
            )
            if lesson.dynamic_eval_required:
                start_evidence = dynamic_start_evidence(pre_report, lesson.eval_cases, dynamic_cases)
                task_store.append_event(task['id'], '新增需求起始证据已核对', actor=actor, evidence=start_evidence)
                if not start_evidence['accepted']:
                    result = task_store.transition(task['id'], 'failed',
                        '新增需求起点无效：既有合同须通过，新增用例须真实失败', actor=actor, evidence=start_evidence)
                    return {'lesson': lesson_number, 'implementation_evidence': False,
                            'baseline': baseline, 'isolation': isolation, 'task': result}
            if pre_report.get("summary", {}).get("decision") != "block":
                result = task_store.transition(
                    task["id"], "failed", "课程起始基线没有稳定红灯，拒绝零增量交付",
                    actor=actor, evidence={"pre_eval": pre_report.get("summary")},
                )
                return {
                    "lesson": lesson_number, "implementation_evidence": False,
                    "baseline": baseline, "isolation": isolation, "task": result,
                }
            lesson_suite = LessonSubprocessEvalRunner(
                workspace, runtime, task["id"], selected_cases, "post",
            )
        except Exception as exc:
            result = task_store.transition(
                task["id"], "failed", "课程隔离或执行前 Eval 失败", actor=actor,
                evidence={"error_type": type(exc).__name__}, error=str(exc),
            )
            return {"lesson": lesson_number, "implementation_evidence": False, "task": result}
    else:
        isolation = None

        def lesson_suite(suite: str, write_report: bool = True) -> dict:
            return run_suite(suite, write_report, selected_cases)

    result = run_task(
        task_store, task["id"], actor, suite_runner=lesson_suite,
        execution_runner=CodexExecutionRunner(workspace, runtime),
    )
    differential = None
    if execute_code and pre_report is not None:
        differential = differential_evidence(pre_report, result.get("result") or {}, result)
        task_store.append_event(
            task["id"], "课程红绿差分判定已完成", actor=actor, evidence=differential,
        )
        if result.get("status") == "review" and not differential["accepted"]:
            result = task_store.transition(
                task["id"], "rework", "未满足执行前红、代码有变更、执行后绿的课程差分合同",
                actor=actor, evidence=differential,
            )
    result = task_store.get(task["id"])
    output = {
        "lesson": lesson_number,
        "implementation_evidence": bool(differential and differential["accepted"]),
        "baseline": baseline,
        "isolation": isolation,
        "differential": differential,
        "warning": None if execute_code else "verify-only 只复验已有候选，不是本讲实现证据",
        "task": result,
    }
    return output
