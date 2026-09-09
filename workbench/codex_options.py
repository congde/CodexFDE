"""Invocation-local options for the headless workbench; never edit user config."""
import os
import json
from pathlib import Path
try:
    import tomllib
except ImportError:  # The course also supports Python 3.10.
    tomllib = None
import re


def headless_options():
    # Desktop Node REPL bridges require an interactive host and have timed out
    # in server-owned CLI runs. Codex's own sandboxed shell is sufficient here.
    # Do not disable code_mode_host: current CLI versions require it to execute
    # tools. This is distinct from an interactive desktop node_repl MCP bridge.
    options = []
    config = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    try:
        content = config.read_text(encoding='utf-8')
        parsed = tomllib.loads(content) if tomllib else {}
        servers = parsed.get('mcp_servers', {}) if tomllib else (
            {'node_repl': {}} if re.search(r'(?m)^\s*\[mcp_servers\.node_repl\]\s*$', content) else {})
    except (OSError, ValueError):
        if os.environ.get('WORKBENCH_CODEX_CONFIG_MODE') == 'isolated':
            raise RuntimeError('独立 CLI 配置需要可读取的 config.toml；先检查本机 Codex 配置')
        return options
    if os.environ.get('WORKBENCH_CODEX_CONFIG_MODE') == 'isolated':
        if tomllib is None:
            raise RuntimeError('独立 CLI 配置模式需要 Python 3.11 或更高版本')
        if parsed.get('model_provider', 'openai') != 'openai':
            raise RuntimeError('独立 CLI 配置模式仅支持 OpenAI；自定义提供方请沿用原配置')
        options = ['--ignore-user-config']
        for key in ('model', 'model_reasoning_effort', 'service_tier'):
            if key in parsed:
                options.extend(['-c', key + '=' + json.dumps(parsed[key])])
        sandbox = parsed.get('windows', {}).get('sandbox')
        if sandbox:
            options.extend(['-c', 'windows.sandbox=' + json.dumps(sandbox)])
        return options
    if 'node_repl' in servers:
        options.extend(['-c', 'mcp_servers.node_repl.enabled=false'])
    return options


def headless_environment():
    env = os.environ.copy()
    # These identify the *parent desktop task*. A new CLI process must own its
    # tools/session instead of routing commands back into that task's IPC pipe.
    for name in ('CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID',
                 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE'):
        env.pop(name, None)
    return env
