from __future__ import annotations

import difflib
import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator
from .process_guard import spawn as spawn_owned_process
from .codex_command import resolve_codex_command


ProcessRunner = Callable[..., subprocess.CompletedProcess]
CodexLineCallback = Callable[[str], None]

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
        value = str(raw).strip().replace("\\", "/")
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


def validate_v0_authorization(mode, workspace_path, scopes, timeout):
    if mode not in {'verify', 'codex'}:
        raise ValueError('执行模式必须是 verify 或 codex')
    if type(timeout) is not int or not 30 <= timeout <= 3600:
        raise ValueError('超时必须是 30..3600 秒的整数')
    if not workspace_path or not Path(workspace_path).is_absolute() or not Path(workspace_path).is_dir():
        raise ValueError('必须指定存在的工作目录绝对路径')
    workspace = Path(workspace_path).resolve()
    if mode == 'codex':
        if not isinstance(scopes, list) or any(not isinstance(value, str) for value in scopes):
            raise ValueError('逐文件允许清单必须是路径数组')
        controller = Path(__file__).resolve().parents[1]
        if workspace == controller or controller.is_relative_to(workspace):
            raise ValueError('编码必须使用隔离候选目录，不能直接修改工作台源仓库')
        files = normalize_write_scope(scopes)
        if not files:
            raise ValueError('编码必须指定逐文件允许清单')
        for name in files:
            target = workspace / name
            if target.is_dir() or not target.resolve().is_relative_to(workspace):
                raise ValueError(f'允许清单必须是候选目录内的具体文件：{name}')
            for node in (target, *target.parents):
                if node == workspace.parent:
                    break
                if node.is_symlink() or (node.exists() and getattr(node.stat(), 'st_file_attributes', 0) & 0x400):
                    raise ValueError('允许文件路径不能包含链接或重解析点')
    return str(workspace)


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
        self.executable = resolve_codex_command(executable)
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

    def __call__(self, task: dict, *, on_codex_line: CodexLineCallback | None = None) -> dict:
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
        if task.get('authorization_policy') == 'v0':
            bound = validate_v0_authorization(mode, task.get('workspace_path'), task.get('write_scope'),
                                               task.get('execution_timeout_seconds'))
            if Path(bound) != self.workspace_root:
                raise ValueError('执行器工作目录与任务授权候选不一致')
            if self.runtime_dir.is_relative_to(self.workspace_root):
                raise ValueError('执行证据目录必须位于编码候选之外')
        scopes = normalize_write_scope(task.get("write_scope", []))
        if not scopes:
            raise ValueError("Codex 代码执行至少需要一个明确写入范围")
        timeout = int(task.get("execution_timeout_seconds", 900))
        if not 30 <= timeout <= 3600:
            raise ValueError("execution_timeout_seconds 必须在 30..3600")
        with self._process_lock, self._workspace_lock(timeout=min(timeout, 60)):
            return self._run_codex(task, scopes, timeout, on_codex_line=on_codex_line)

    def _run_codex(
        self,
        task: dict,
        scopes: list[str],
        timeout: int,
        *,
        on_codex_line: CodexLineCallback | None = None,
    ) -> dict:
        task_id = str(task["id"])
        attempt_id = uuid.uuid4().hex
        run_dir = self.runtime_dir / "delivery" / task_id / attempt_id
        run_dir.mkdir(parents=True, exist_ok=False)
        schema_path = run_dir / "output-schema.json"
        result_path = run_dir / "final.json"
        result_path.unlink(missing_ok=True)
        (run_dir / "evidence.json").unlink(missing_ok=True)
        schema_path.write_text(json.dumps(self._output_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
        strict = task.get('authorization_policy') == 'v0'
        before = self._snapshot(strict=strict)
        if strict and any(state.digest.startswith('link:') for state in before.values()):
            raise ValueError('编码候选包含链接或重解析点，请先核对隔离目录')
        prompt = self._build_prompt(task, scopes)
        from .codex_options import headless_options
        command = [
            self.executable, "exec", *headless_options(), "--json", "--sandbox", "workspace-write", "--ephemeral",
            "--output-schema", str(schema_path), "--output-last-message", str(result_path),
            "--cd", str(self.workspace_root), "-",
        ]
        (run_dir / 'prompt.txt').write_text(prompt, encoding='utf-8')
        (run_dir / 'invocation.json').write_text(json.dumps({
            'command': command, 'workspace': str(self.workspace_root), 'sandbox': 'workspace-write',
            'prompt_path': str(run_dir / 'prompt.txt')}, ensure_ascii=False, indent=2), encoding='utf-8')
        started = time.monotonic()
        timed_out = False
        launch_error = ""
        if self.process_runner is subprocess.run:
            completed = self._run_codex_streaming(
                command, prompt, timeout, on_codex_line or (lambda line: None), started,
            )
            timed_out = completed.returncode == 124
            if timed_out:
                launch_error = f"Codex 执行超过 {timeout} 秒"
        else:
            try:
                completed = self.process_runner(
                    command, input=prompt, text=True, capture_output=True, timeout=timeout,
                    check=False, cwd=self.workspace_root,
                )
            except subprocess.TimeoutExpired as exc:
                completed = subprocess.CompletedProcess(
                    command, 124, stdout=_text(exc.stdout), stderr=_text(exc.stderr),
                )
                timed_out = True
                launch_error = f"Codex 执行超过 {timeout} 秒"
            except OSError as exc:
                completed = subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))
                launch_error = f"Codex CLI 无法启动：{type(exc).__name__}: {exc}"
        duration_ms = int((time.monotonic() - started) * 1000)
        # Persist raw process results before filesystem inspection can fail.
        (run_dir / 'stdout.txt').write_text(_text(completed.stdout), encoding='utf-8')
        (run_dir / 'stderr.txt').write_text(_text(completed.stderr), encoding='utf-8')
        (run_dir / 'process.json').write_text(json.dumps({'command': command, 'returncode': completed.returncode,
            'timed_out': timed_out, 'workspace': str(self.workspace_root)}, ensure_ascii=False), encoding='utf-8')
        inspection_error = ''
        try:
            after = self._snapshot(strict=strict)
            changes = self._changes(before, after)
        except OSError as exc:
            after, changes = {}, {}
            inspection_error = f'无法核对执行后的文件差异：{exc}；原始进程结果已保存'
        changed_files = sorted(changes)
        out_of_scope = [path for path in changed_files if not (
            path in scopes and not _is_sensitive_path(path) and not (after.get(path) and after[path].digest.startswith('link:')) if strict
            else self._allowed(path, scopes))]
        stdout_path, stderr_path = run_dir / 'stdout.txt', run_dir / 'stderr.txt'
        stdout_path.write_text(_text(completed.stdout), encoding='utf-8')
        stderr_path.write_text(_text(completed.stderr), encoding='utf-8')
        diff_path = run_dir / 'changes.diff'
        diff_path.write_text(self._diff(before, after, changed_files, limit=None), encoding='utf-8')
        events = self._parse_events(_text(completed.stdout))
        final = self._load_final(result_path)
        success = completed.returncode == 0 and not timed_out and not launch_error and not out_of_scope and not inspection_error
        message = "Codex 已完成受控代码执行" if success else (
            launch_error or inspection_error or
            (f"检测到越界写入：{', '.join(out_of_scope)}" if out_of_scope else f"Codex 退出码为 {completed.returncode}")
        )
        evidence = {
            "attempt_id": attempt_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "provenance": "injected_process_runner" if self.process_runner is not subprocess.run else "codex_cli",
            "success": success,
            "invocation": {"command": command, "workspace": str(self.workspace_root), "prompt": prompt},
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
            "inspection_error": inspection_error,
            "duration_ms": duration_ms,
            "thread_id": events["thread_id"],
            "usage": events["usage"],
            "commands": events["commands"],
            "file_change_events": events["file_changes"],
            "final": final,
            "stdout_tail": _text(completed.stdout)[-20_000:],
            "stderr_tail": _text(completed.stderr)[-10_000:],
            "artifacts": {"output_schema": str(schema_path), "final_message": str(result_path),
                          "stdout": str(stdout_path), "stderr": str(stderr_path), "diff": str(diff_path)},
        }
        evidence['artifact_sha256'] = {key: hashlib.sha256(Path(value).read_bytes()).hexdigest()
                                      for key, value in evidence['artifacts'].items() if Path(value).is_file()}
        (run_dir / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return evidence

    def _run_codex_streaming(
        self,
        command: list[str],
        prompt: str,
        timeout: int,
        on_codex_line: CodexLineCallback,
        started: float,
    ) -> subprocess.CompletedProcess:
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        lines = queue.Queue()
        try:
            from .codex_options import headless_environment
            process, owner, prefix = spawn_owned_process(command, self.workspace_root, env=headless_environment())
        except OSError as exc:
            return subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))

        def drain(stream, label):
            try:
                for line in stream:
                    lines.put((label, line))
            finally:
                stream.close()
                lines.put((label, None))

        def feed():
            try:
                process.stdin.write(prefix + prompt)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        readers = [threading.Thread(target=drain, args=(process.stdout, "out"), daemon=True),
                   threading.Thread(target=drain, args=(process.stderr, "err"), daemon=True)]
        writer = threading.Thread(target=feed, daemon=True)
        for thread in readers:
            thread.start()
        writer.start()
        deadline = started + timeout
        ended = set()
        timed_out = False
        was_cancelled = False
        try:
            while len(ended) < 2 or process.poll() is None:
                from .execution_control import cancelled
                if cancelled():
                    stderr_parts.append('任务已取消')
                    was_cancelled = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    channel, line = lines.get(timeout=min(.05, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    ended.add(channel)
                elif channel == "out":
                    stdout_parts.append(line)
                    on_codex_line(line.rstrip("\n"))
                else:
                    stderr_parts.append(line)
        finally:
            if owner:
                owner.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for thread in [*readers, writer]:
                thread.join(timeout=1)
            # Preserve output already drained when timeout or a callback interrupted us.
            while not lines.empty():
                channel, line = lines.get_nowait()
                if line is not None:
                    (stdout_parts if channel == "out" else stderr_parts).append(line)
        return subprocess.CompletedProcess(
            command, 130 if was_cancelled else 124 if timed_out else int(process.returncode or 0),
            stdout="".join(stdout_parts), stderr="".join(stderr_parts),
        )

    def _snapshot(self, *, strict=False) -> dict[str, _FileState]:
        result: dict[str, _FileState] = {}
        for path in self.workspace_root.rglob("*"):
            relative_path = path.relative_to(self.workspace_root)
            if strict and (path.is_symlink() or getattr(path.stat(), 'st_file_attributes', 0) & 0x400):
                result[relative_path.as_posix()] = _FileState('link:' + str(path.resolve()), 0, None)
                continue
            if not path.is_file() or path.is_symlink():
                continue
            relative_path = path.relative_to(self.workspace_root)
            excluded = (_EXCLUDED_DIRS - {'.git', '.runtime', '.tmp'}) if strict else _EXCLUDED_DIRS
            if any(part.lower() in excluded for part in relative_path.parts):
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
    def _diff(before: dict[str, _FileState], after: dict[str, _FileState], paths: list[str], limit=_MAX_DIFF_CHARS) -> str:
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
            if limit is not None and sum(len(value) for value in chunks) >= limit:
                chunks.append("\n... diff truncated by workbench ...\n")
                break
        return "".join(chunks)[:limit]

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
            "不得直接写入 .runtime、.tmp、.git、.codex、.env、现有运行数据库、密钥或凭据文件；"
            "不得批准业务单据，不得删除或降低 Eval。\n"
            "可以运行测试，在系统 tempfile 目录中创建独立的测试数据库；不得连接或修改现有业务数据库。"
            "真实任务与审核证据由外层工作台记录，不要自行编造或写入运行账本。\n"
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
