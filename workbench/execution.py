from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator


ProcessRunner = Callable[..., subprocess.CompletedProcess]

_EXCLUDED_DIRS = {".git", ".runtime", ".tmp", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_PROTECTED_PARTS = {".git", ".runtime", ".tmp", ".codex", "__pycache__"}
_PROTECTED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx"}
_MAX_DIFF_CHARS = 50_000
_MAX_TEXT_SNAPSHOT_BYTES = 1_000_000


@dataclass(frozen=True)
class _FileState:
    digest: str
    size: int
    text: str | None


def normalize_write_scope(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """Return safe, workspace-relative paths suitable for a task allowlist."""
    normalized: list[str] = []
    for raw in values or []:
        value = str(raw).strip().replace("\\", "/").strip("/")
        if not value:
            continue
        path = PurePosixPath(value)
        parts = path.parts
        if value in {".", "*"} or path.is_absolute() or ".." in parts or ":" in value or "*" in value:
            raise ValueError(f"写入范围必须是明确的工作区相对路径：{raw}")
        lowered = [part.lower() for part in parts]
        if any(part in _PROTECTED_PARTS for part in lowered) or _is_sensitive_path(value):
            raise ValueError(f"写入范围包含受保护路径：{raw}")
        clean = path.as_posix()
        if clean not in normalized:
            normalized.append(clean)
    if len(normalized) > 20:
        raise ValueError("写入范围最多 20 项")
    return normalized


def _is_sensitive_path(relative: str) -> bool:
    path = PurePosixPath(relative.lower())
    name = path.name
    return (
        name == ".env"
        or name.startswith(".env.") and name != ".env.example"
        or path.suffix in _PROTECTED_SUFFIXES
        or any(part in _PROTECTED_PARTS for part in path.parts)
        or "secret" in name
        or "credential" in name
    )


class CodexExecutionRunner:
    """Run one task through ``codex exec`` and return independently derived evidence.

    The runner edits the current workspace only when the task explicitly uses
    ``execution_mode=codex``. It serializes code-writing runs, constrains the
    prompt to a task allowlist, and verifies the actual filesystem delta after
    Codex exits. Human review remains a separate workflow state.
    """

    _process_lock = threading.Lock()

    def __init__(
        self,
        workspace_root: str | Path,
        runtime_dir: str | Path,
        *,
        executable: str | None = None,
        process_runner: ProcessRunner | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.executable = executable or os.getenv("FLOWERP_CODEX_COMMAND", "codex")
        self.process_runner = process_runner or subprocess.run

    def capabilities(self) -> dict:
        if self.process_runner is not subprocess.run:
            return {
                "codex_available": True,
                "codex_command": self.executable,
                "sandbox": "workspace-write",
                "reason": "injected_process_runner",
            }
        resolved = shutil.which(self.executable)
        if not resolved:
            return {
                "codex_available": False,
                "codex_command": self.executable,
                "sandbox": "workspace-write",
                "reason": "找不到 Codex CLI；请安装 CLI 或设置 FLOWERP_CODEX_COMMAND",
            }
        try:
            probe = subprocess.run(
                [resolved, "--version"], text=True, capture_output=True,
                timeout=5, check=False, cwd=self.workspace_root,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                "codex_available": False,
                "codex_command": resolved,
                "sandbox": "workspace-write",
                "reason": f"Codex CLI 无法启动：{type(exc).__name__}: {exc}",
            }
        version = (probe.stdout or probe.stderr).strip()[:200]
        return {
            "codex_available": probe.returncode == 0,
            "codex_command": resolved,
            "codex_version": version,
            "sandbox": "workspace-write",
            "reason": "ready" if probe.returncode == 0 else f"Codex CLI 探测失败：{version}",
        }

    def __call__(self, task: dict) -> dict:
        mode = str(task.get("execution_mode", "verify"))
        if mode == "verify":
            return {
                "success": True,
                "mode": "verification_only",
                "changed_files": [],
                "message": "任务选择仅验证模式；不调用 Codex，不产生代码写入",
            }
        if mode != "codex":
            raise ValueError(f"未知执行模式：{mode}")
        scopes = normalize_write_scope(task.get("write_scope", []))
        if not scopes:
            raise ValueError("Codex 代码执行至少需要一个明确写入范围")
        timeout = int(task.get("execution_timeout_seconds", 900))
        if not 30 <= timeout <= 3600:
            raise ValueError("execution_timeout_seconds 必须在 30..3600")
        with self._process_lock, self._workspace_lock(timeout=min(timeout, 60)):
            return self._run_codex(task, scopes, timeout)

    def _run_codex(self, task: dict, scopes: list[str], timeout: int) -> dict:
        task_id = str(task["id"])
        run_dir = self.runtime_dir / "delivery" / task_id
        run_dir.mkdir(parents=True, exist_ok=True)
        schema_path = run_dir / "output-schema.json"
        result_path = run_dir / "final.json"
        result_path.unlink(missing_ok=True)
        (run_dir / "evidence.json").unlink(missing_ok=True)
        schema_path.write_text(json.dumps(self._output_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
        before = self._snapshot()
        prompt = self._build_prompt(task, scopes)
        command = [
            self.executable, "exec", "--json", "--sandbox", "workspace-write", "--ephemeral",
            "--output-schema", str(schema_path), "--output-last-message", str(result_path),
            "--cd", str(self.workspace_root), "-",
        ]
        started = time.monotonic()
        try:
            completed = self.process_runner(
                command, input=prompt, text=True, capture_output=True, timeout=timeout,
                check=False, cwd=self.workspace_root,
            )
            timed_out = False
            launch_error = ""
        except subprocess.TimeoutExpired as exc:
            completed = subprocess.CompletedProcess(
                command, 124, stdout=_text(exc.stdout), stderr=_text(exc.stderr),
            )
            timed_out = True
            launch_error = f"Codex 执行超过 {timeout} 秒"
        except OSError as exc:
            completed = subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))
            timed_out = False
            launch_error = f"Codex CLI 无法启动：{type(exc).__name__}: {exc}"
        duration_ms = int((time.monotonic() - started) * 1000)
        after = self._snapshot()
        changes = self._changes(before, after)
        changed_files = sorted(changes)
        out_of_scope = [path for path in changed_files if not self._allowed(path, scopes)]
        events = self._parse_events(_text(completed.stdout))
        final = self._load_final(result_path)
        success = completed.returncode == 0 and not timed_out and not launch_error and not out_of_scope
        message = "Codex 已完成受控代码执行" if success else (
            launch_error or
            (f"检测到越界写入：{', '.join(out_of_scope)}" if out_of_scope else f"Codex 退出码为 {completed.returncode}")
        )
        evidence = {
            "success": success,
            "mode": "codex_exec",
            "message": message,
            "sandbox": "workspace-write",
            "write_scope": scopes,
            "changed_files": changed_files,
            "out_of_scope_files": out_of_scope,
            "change_manifest": [
                {"path": path, "change": change, "before_sha256": before.get(path).digest if path in before else None,
                 "after_sha256": after.get(path).digest if path in after else None}
                for path, change in changes.items()
            ],
            "diff": self._diff(before, after, changed_files),
            "returncode": completed.returncode,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "thread_id": events["thread_id"],
            "usage": events["usage"],
            "commands": events["commands"],
            "file_change_events": events["file_changes"],
            "final": final,
            "stdout_tail": _text(completed.stdout)[-20_000:],
            "stderr_tail": _text(completed.stderr)[-10_000:],
            "artifacts": {"output_schema": str(schema_path), "final_message": str(result_path)},
        }
        (run_dir / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return evidence

    def _snapshot(self) -> dict[str, _FileState]:
        result: dict[str, _FileState] = {}
        for path in self.workspace_root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative_path = path.relative_to(self.workspace_root)
            if any(part.lower() in _EXCLUDED_DIRS for part in relative_path.parts):
                continue
            relative = relative_path.as_posix()
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            size = path.stat().st_size
            text = None
            if size <= _MAX_TEXT_SNAPSHOT_BYTES and not _is_sensitive_path(relative):
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    pass
            result[relative] = _FileState(digest.hexdigest(), size, text)
        return result

    @staticmethod
    def _changes(before: dict[str, _FileState], after: dict[str, _FileState]) -> dict[str, str]:
        changes: dict[str, str] = {}
        for path in sorted(before.keys() | after.keys()):
            if path not in before:
                changes[path] = "created"
            elif path not in after:
                changes[path] = "deleted"
            elif before[path].digest != after[path].digest:
                changes[path] = "modified"
        return changes

    @staticmethod
    def _allowed(path: str, scopes: list[str]) -> bool:
        if _is_sensitive_path(path):
            return False
        return any(path == scope or path.startswith(scope.rstrip("/") + "/") for scope in scopes)

    @staticmethod
    def _diff(before: dict[str, _FileState], after: dict[str, _FileState], paths: list[str]) -> str:
        chunks: list[str] = []
        for path in paths:
            old = before.get(path)
            new = after.get(path)
            if (old and old.text is None) or (new and new.text is None):
                chunks.append(f"Binary or protected file changed: {path}\n")
                continue
            old_lines = (old.text if old else "").splitlines(keepends=True)
            new_lines = (new.text if new else "").splitlines(keepends=True)
            chunks.extend(difflib.unified_diff(
                old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
            ))
            if sum(len(value) for value in chunks) >= _MAX_DIFF_CHARS:
                chunks.append("\n... diff truncated by workbench ...\n")
                break
        return "".join(chunks)[:_MAX_DIFF_CHARS]

    @staticmethod
    def _parse_events(raw: str) -> dict:
        thread_id = ""
        usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        commands: list[dict] = []
        file_changes: list[dict] = []
        for line in raw.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                thread_id = str(event.get("thread_id", ""))
            candidate = event.get("usage")
            if isinstance(candidate, dict):
                input_tokens = int(candidate.get("input_tokens", 0) or 0)
                cached = int(candidate.get("cached_input_tokens", 0) or 0)
                output_tokens = int(candidate.get("output_tokens", 0) or 0)
                total = int(candidate.get("total_tokens", input_tokens + output_tokens) or 0)
                if total >= usage["total_tokens"]:
                    usage = {"input_tokens": input_tokens, "cached_input_tokens": cached,
                             "output_tokens": output_tokens, "total_tokens": total}
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "command_execution" and len(commands) < 50:
                commands.append({
                    "command": str(item.get("command", ""))[:1000],
                    "status": item.get("status"),
                    "exit_code": item.get("exit_code"),
                })
            elif item_type == "file_change" and len(file_changes) < 100:
                file_changes.append({key: item.get(key) for key in ("path", "kind", "status") if key in item})
        return {"thread_id": thread_id, "usage": usage, "commands": commands, "file_changes": file_changes}

    @staticmethod
    def _load_final(path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"raw": value}
        except (json.JSONDecodeError, OSError) as exc:
            return {"parse_error": str(exc), "raw": path.read_text(encoding="utf-8", errors="replace")[:20_000]}

    @staticmethod
    def _output_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "tests": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "next_step": {"type": "string"},
            },
            "required": ["summary", "tests", "risks", "next_step"],
            "additionalProperties": False,
        }

    @staticmethod
    def _build_prompt(task: dict, scopes: list[str]) -> str:
        payload = {
            "task_id": task.get("id"),
            "request": task.get("request"),
            "requirement_id": task.get("requirement_id"),
            "business_refs": task.get("business_refs", []),
            "spec": task.get("spec", {}),
            "write_scope": scopes,
        }
        return (
            "你是 FlowERP 研发交付执行器。读取仓库 AGENTS.md 并完成下面的任务。\n"
            "必须先理解 Spec，再做最小代码修改，再运行与改动相关的测试。\n"
            f"只允许修改这些相对路径：{', '.join(scopes)}。不得修改其他路径。\n"
            "不得写入 .runtime、.tmp、.git、.codex、.env、数据库、密钥或凭据文件；"
            "不得批准业务单据，不得删除或降低 Eval。\n"
            "如果需求无法在写入范围内安全完成，停止修改并在 risks 中说明。\n"
            "最终必须按给定 JSON Schema 返回摘要、实际测试、剩余风险和下一步。\n\n"
            "任务上下文：\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    @contextmanager
    def _workspace_lock(self, timeout: int) -> Iterator[None]:
        lock_path = self.runtime_dir / "delivery-code.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        acquired = False
        try:
            while not acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("等待工作区代码写锁超时")
                    time.sleep(0.1)
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
