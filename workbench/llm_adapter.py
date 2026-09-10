from __future__ import annotations

import json
import os
import shutil
from .codex_command import resolve_codex_command
import subprocess
from pathlib import Path

from .codex_events import SessionCodexStreamer
from .runtime_store import HarnessRuntimeStore


class TemplateLLMAdapter:
    """Minimal llm seam: emit assistant/chunk* then assistant/message into Session log."""

    def __init__(self, runtime: HarnessRuntimeStore, provider: str = "template") -> None:
        self.runtime = runtime
        self.provider = provider

    def emit_delivery_plan(
        self,
        session_id: str,
        *,
        turn: int,
        task: dict,
        actor: str = "llm",
        chunk_size: int = 48,
    ) -> dict:
        request = str(task.get("request") or "").strip()
        mode = task.get("execution_mode", "verify")
        status = str(task.get("status") or "queued")
        from .delivery_pipeline import format_pipeline_for_llm, status_title

        pipeline = format_pipeline_for_llm(task=task, session_id=session_id)
        plan = (
            f"Turn {turn} · 当前流水线 {status_title(status)} ({status}) · "
            f"mode={mode}。先对齐可见流水线，再"
            f"{'在 write_scope 内改代码' if mode == 'codex' else '只做验证'}，"
            f"跑 blocking Eval，停在 review。目标: {request[:120]}\n\n{pipeline}"
        )
        chunks = [plan[i:i + chunk_size] for i in range(0, len(plan), chunk_size)] or [plan]
        for index, text in enumerate(chunks):
            self.runtime.append(
                session_id,
                "assistant/chunk",
                actor,
                {"turn": turn, "index": index, "text": text, "provider": self.provider},
                source_key=f"turn:{turn}:assistant:chunk:{index}",
            )
        self.runtime.append(
            session_id,
            "assistant/message",
            actor,
            {"turn": turn, "content": plan, "provider": self.provider},
            source_key=f"turn:{turn}:assistant:message",
        )
        return {"content": plan, "chunks": len(chunks), "provider": self.provider}


class CodexLLMAdapter:
    """Codex-backed llm seam with stdout projection into Session assistant events."""

    def __init__(
        self,
        runtime: HarnessRuntimeStore,
        repository_root: str | Path,
        *,
        executable: str | None = None,
        provider: str = "codex",
    ) -> None:
        self.runtime = runtime
        self.repository_root = Path(repository_root).resolve()
        self.executable = resolve_codex_command(executable)
        self.provider = provider

    def available(self) -> bool:
        if not shutil.which(self.executable) and self.executable == "codex":
            return False
        try:
            probe = subprocess.run(
                [self.executable, "--version"], text=True, capture_output=True, timeout=15, check=False,
            )
            return probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def emit_delivery_plan(
        self,
        session_id: str,
        *,
        turn: int,
        task: dict,
        actor: str = "llm",
    ) -> dict:
        request = str(task.get("request") or "").strip()
        streamer = SessionCodexStreamer(self.runtime, session_id, actor, turn=turn, provider=self.provider)
        if not self.available():
            fallback = TemplateLLMAdapter(self.runtime, "template")
            result = fallback.emit_delivery_plan(session_id, turn=turn, task=task, actor=actor)
            result["fallback"] = "codex_unavailable"
            return result
        prompt = (
            "你是交付 Harness 的计划器。只输出 JSON 对象，字段 plan(字符串)、tools(数组)、risks(数组)。"
            f"需求：{request[:500]}"
        )
        completed = subprocess.run(
            [self.executable, "exec", "--json", "--sandbox", "read-only", "--ephemeral", "-"],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
            cwd=self.repository_root,
        )
        streamer.ingest_stdout(completed.stdout or completed.stderr or "")
        if completed.returncode != 0 and not completed.stdout.strip():
            streamer.emit_text(
                f"Turn {turn} codex plan fallback: verify spec, execute within scope, run blocking eval for {request[:120]}",
            )
        return {
            "provider": self.provider,
            "returncode": completed.returncode,
            "available": True,
        }


def create_llm_adapter(
    runtime: HarnessRuntimeStore,
    provider: str,
    repository_root: str | Path,
) -> TemplateLLMAdapter | CodexLLMAdapter:
    if provider == "codex":
        return CodexLLMAdapter(runtime, repository_root, provider="codex")
    return TemplateLLMAdapter(runtime, provider=provider)
