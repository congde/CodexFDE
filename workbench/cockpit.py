from __future__ import annotations

import re
from typing import Iterable

from .course_mainline import COURSE_BUILD_THESIS, lesson_contract
from .evolution import EvolutionStore

THESIS = {
    "workbench": "研发工作台是写代码的主体",
    "flowerp": "FlowERP 是验证场，证明工作台有效且能自迭代",
    "codex": "Codex 是底座，既造工作台也被工作台约束",
}

_LESSON_REQ_LOOSE = re.compile(r"REQ-COURSE-L(\d{2})")


def stage_band(number: int) -> str:
    if number <= 3:
        return "造工作台"
    if number == 4:
        return "换挡"
    if number <= 15:
        return "用工作台交 ERP"
    return "冷启动终验"


def classify_lane(task: dict) -> str:
    refs = [str(item) for item in (task.get("business_refs") or [])]
    requirement = str(task.get("requirement_id") or "")
    request = str(task.get("request") or "")
    blob = " ".join(refs + [requirement, request])
    if any(token in blob for token in ("PERSONAL-WORKBENCH", "CASE-WB-", "REQ-WB")):
        return "workbench"
    match = _LESSON_REQ_LOOSE.search(requirement)
    if match:
        lesson = int(match.group(1))
        return "workbench" if lesson <= 3 else "erp"
    if any(ref.startswith(("SKU:", "ORDER:", "PURCHASE:", "CHANNEL:")) for ref in refs):
        return "erp"
    return "workbench"


def spec_title(spec: object) -> str:
    if not isinstance(spec, dict):
        return ""
    goal = str(spec.get("goal") or "").strip()
    if goal:
        return goal.splitlines()[0][:120]
    return str(spec.get("source") or "").splitlines()[0][:120] if spec.get("source") else ""


def infer_lesson_number(tasks: Iterable[dict]) -> int:
    for task in tasks:
        match = _LESSON_REQ_LOOSE.search(str(task.get("requirement_id") or ""))
        if match:
            return int(match.group(1))
    return 1


def bootstrap_state(tasks: Iterable[dict]) -> str:
    for task in tasks:
        refs = [str(item) for item in (task.get("business_refs") or [])]
        requirement = str(task.get("requirement_id") or "")
        if "PERSONAL-WORKBENCH" in refs or "PERSONAL-WORKBENCH" in requirement:
            return "bootstrapped"
    return "not_constructed"


def current_course(number: int | None = None, *, tasks: Iterable[dict] = ()) -> dict:
    items = list(tasks)
    lesson_number = number if number is not None else infer_lesson_number(items)
    if not 1 <= lesson_number <= 16:
        raise ValueError("课次必须在 1 到 16 之间")
    lesson = lesson_contract(lesson_number)
    payload = lesson.as_dict()
    constructed = bootstrap_state(items)
    return {
        "lesson": lesson_number,
        "title": lesson.title,
        "construction_stage": payload["construction_stage"],
        "stage_band": stage_band(lesson_number),
        "codex_role": payload["codex_role"],
        "write_scope": list(lesson.write_scope),
        "workbench_increment": lesson.workbench_increment,
        "erp_increment": lesson.erp_increment,
        "course_build_thesis": COURSE_BUILD_THESIS,
        "thesis": THESIS,
        "honest_empty": lesson_number <= 3 and constructed == "not_constructed",
        "bootstrap_state": constructed,
        "follow_along": True,
        "optional_harness_required": False,
        "optional_harness_port": 8010,
        "eval_cases": list(lesson.eval_cases),
        "acceptance": list(lesson.acceptance),
    }


def lesson_number_from_requirement(requirement_id: str) -> int | None:
    match = _LESSON_REQ_LOOSE.search(str(requirement_id or ""))
    return int(match.group(1)) if match else None


def lesson_eval_runner(lesson: int | None):
    """Run only this lesson's declared cases. Task-scoped report is written by the workflow."""
    from eval.harness import run_suite

    cases = list(lesson_contract(lesson).eval_cases) if lesson else None

    def runner(suite: str, write_report: bool = True, case_names=None):
        _ = write_report
        return run_suite(suite, False, case_names or cases)

    return runner


def last_upgrade(store: EvolutionStore) -> dict:
    items = store.list(20)
    preferred = [item for item in items if str(item.get("classification") or "").startswith("workbench")]
    chosen = (preferred or items)[0] if (preferred or items) else None
    if chosen is None:
        return {"available": False, "message": "还没有工作台升级记录"}
    return {
        "available": True,
        "id": chosen.get("id"),
        "classification": chosen.get("classification"),
        "status": chosen.get("status"),
        "failure_signature": chosen.get("failure_signature"),
        "source_task_id": chosen.get("source_task_id"),
        "updated_at": chosen.get("updated_at") or chosen.get("created_at"),
    }
