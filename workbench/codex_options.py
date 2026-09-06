"""Invocation-local options for the headless workbench; never edit user config."""
import os
from pathlib import Path
try:
    import tomllib
except ImportError:  # The course also supports Python 3.10.
    tomllib = None
import re


def headless_options():
    # Desktop Node REPL bridges require an interactive host and have timed out
    # in server-owned CLI runs. Codex's own sandboxed shell is sufficient here.
    config = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    try:
        content = config.read_text(encoding='utf-8')
        servers = tomllib.loads(content).get('mcp_servers', {}) if tomllib else (
            {'node_repl': {}} if re.search(r'(?m)^\s*\[mcp_servers\.node_repl\]\s*$', content) else {})
    except (OSError, ValueError):
        return []
    return ['-c', 'mcp_servers.node_repl.enabled=false'] if 'node_repl' in servers else []


def headless_environment():
    env = os.environ.copy()
    # These identify the *parent desktop task*. A new CLI process must own its
    # tools/session instead of routing commands back into that task's IPC pipe.
    for name in ('CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID',
                 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE'):
        env.pop(name, None)
    return env
