"""Resolve local service data without silently replacing a configured database."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def service_runtime(surface: str, explicit=None, *, root: Path = ROOT) -> Path:
    if surface not in {"workbench", "flowerp"}:
        raise ValueError("未知的本地服务")
    if explicit is not None:
        return Path(explicit).resolve()
    root = root.resolve()
    config = root / ".runtime" / "services.json"
    if config.exists():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            value = data[surface]
            if not isinstance(value, str) or not value.strip():
                raise ValueError("运行目录必须是非空字符串")
            runtime = (root / value).resolve()
            if not (runtime / (surface + ".db")).is_file():
                raise ValueError(f"配置的数据文件不存在：{runtime / (surface + '.db')}")
            return runtime
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"无法加载 {config}：{error}。请恢复正确配置或数据，不会自动创建空库。") from error
    # Preserve legacy installations; fresh installs have separate directories.
    legacy = root / ".runtime"
    if (legacy / (surface + ".db")).is_file():
        return legacy
    return legacy / surface
