"""Resolve a local CLI without requiring a desktop launcher to inherit shell PATH."""
import os
from pathlib import Path
import shutil


def resolve_codex_command(executable: str | None = None) -> str:
    explicit = executable or os.getenv('FLOWERP_CODEX_COMMAND')
    command = explicit or 'codex'
    resolved = shutil.which(command)
    if resolved:
        return resolved
    # An explicit override must never silently select a different installation.
    if explicit or os.name != 'nt':
        return command
    local = os.getenv('LOCALAPPDATA')
    if local:
        directory = Path(local) / 'OpenAI' / 'Codex' / 'bin'
        candidates = [p for p in directory.glob('*/codex.exe') if p.is_file()]
        if candidates:
            return str(max(candidates, key=lambda p: (p.stat().st_mtime_ns, str(p))))
    return command
