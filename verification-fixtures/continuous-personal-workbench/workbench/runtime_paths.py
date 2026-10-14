"""Resolve planned runtime locations without creating directories or databases."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def service_runtime(surface: str, explicit=None, *, root: Path = ROOT) -> Path:
    if surface != "workbench":
        raise ValueError("L00 壳只提供工作台入口，FlowERP 尚未接入")
    if explicit is not None:
        return Path(explicit).resolve()
    return root.resolve() / ".runtime" / "workbench"
