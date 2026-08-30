from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Protocol


class ShellProvider(Protocol):
    def exec(self, root: Path, command: list[str], *, timeout: int = 120) -> dict: ...


_ALLOWED_SHELL_PREFIXES = (
    [sys.executable, "-m", "unittest"],
    [sys.executable, "-m", "eval.harness"],
    [sys.executable, "-X", "utf8", "-m", "unittest"],
    [sys.executable, "-X", "utf8", "-m", "eval.harness"],
    ["git", "status"],
    ["git", "diff"],
    ["git", "log"],
)


def normalize_command(command: list[str]) -> list[str]:
    return [str(part) for part in command]


def command_allowed(command: list[str]) -> bool:
    normalized = normalize_command(command)
    for allowed in _ALLOWED_SHELL_PREFIXES:
        if len(normalized) >= len(allowed) and normalized[: len(allowed)] == allowed:
            return True
    return False


class LocalShellProvider:
    """Local controlled shell provider for the shell capability seam."""

    def exec(self, root: Path, command: list[str], *, timeout: int = 120) -> dict:
        normalized = normalize_command(command)
        if not normalized:
            raise ValueError("command 不能为空")
        if not command_allowed(normalized):
            raise ValueError("命令不在 Harness 允许列表内")
        timeout = min(int(timeout), 600)
        completed = subprocess.run(
            normalized,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": normalized,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "")[-20_000:],
            "stderr": (completed.stderr or "")[-10_000:],
            "provider": "local",
        }


class DenyShellProvider:
    """Swap-in shell provider that refuses every command (proves seam replaceability)."""

    def exec(self, root: Path, command: list[str], *, timeout: int = 120) -> dict:
        raise PermissionError("shell provider=deny：拒绝执行任何命令")
