"""Course baseline progression gate.

When ``docs/courses/labs/baselines/PROGRESSION.json`` is absent, every gated capability is
treated as delivered (end-state / follow-along HEAD).

When the file is present, Eval cases call ``require_capability`` so lesson-start
commits can be honestly red for the current lesson and green for prior ones.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRESSION_RELATIVE = Path("docs/courses/labs/baselines/PROGRESSION.json")
PROGRESSION_PATH = REPO_ROOT / PROGRESSION_RELATIVE

# Capability unlocked when the listed lesson's product increment is complete.
UNLOCKS_AFTER_LESSON: dict[int, tuple[str, ...]] = {
    4: ("inventory_export",),
    5: ("receiving_idempotent",),
    6: ("stock_non_negative",),
    7: ("order_total",),
    8: ("sales_atomic",),
    9: ("cancel_release",),
    10: ("illegal_transition",),
    11: ("purchase_request",),
    12: ("purchase_approval",),
    13: ("delivery_evidence",),
    14: ("web_api", "no_secrets"),
}

CASE_CAPABILITY: dict[str, str] = {
    "inventory_export_is_stable": "inventory_export",
    "receiving_is_idempotent": "receiving_idempotent",
    "stock_never_negative": "stock_non_negative",
    "order_total_matches_lines": "order_total",
    "sales_credit_and_atomic_reservation": "sales_atomic",
    "cancellation_releases_reservation": "cancel_release",
    "illegal_transition_is_blocked": "illegal_transition",
    "purchase_request_preserves_reason": "purchase_request",
    "purchase_requires_approval": "purchase_approval",
    "delivery_evidence_and_review_controls": "delivery_evidence",
    "web_api_and_persistence_projection_agree": "web_api",
    "no_committed_secrets": "no_secrets",
}


def enabled_at_lesson_start(lesson_number: int) -> list[str]:
    """Capabilities already delivered before lesson ``lesson_number`` starts."""
    enabled: list[str] = []
    for completed in range(4, lesson_number):
        enabled.extend(UNLOCKS_AFTER_LESSON.get(completed, ()))
    return enabled


def progression_payload(lesson_number: int) -> dict:
    return {
        "schema_version": "1.0",
        "lesson_start": lesson_number,
        "enabled": enabled_at_lesson_start(lesson_number),
        "note": (
            "课程起始基线红绿门闩。终态跟跑仓库不应提交本文件"
            "（或 enabled 含全部能力）；禁止用终态 HEAD 冒充 16 个起始标签。"
        ),
    }


def load_enabled_capabilities(root: Path | None = None) -> set[str] | None:
    """Return enabled set, or None when progression file is absent (all delivered)."""
    path = (root or REPO_ROOT) / PROGRESSION_RELATIVE
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(item) for item in (data.get("enabled") or [])}


def require_capability(capability: str, *, root: Path | None = None) -> None:
    enabled = load_enabled_capabilities(root)
    if enabled is None:
        return
    if capability not in enabled:
        raise AssertionError(f"课程能力尚未交付（progression）：{capability}")
