from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from eval.harness import run_suite
from .repair import build_repair_task


def _signature(report: dict) -> tuple[str, ...]:
    return tuple(sorted(r["name"] for r in report["results"] if not r["passed"] and r["level"] == "blocking"))


def _run_codex(task: dict, round_no: int, timeout: int) -> dict:
    runtime = Path(".runtime/loop"); runtime.mkdir(parents=True, exist_ok=True)
    task_path = runtime / f"repair-round-{round_no}.json"
    result_path = runtime / f"codex-round-{round_no}.txt"
    task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = (
        f"读取 {task_path}。在当前仓库做最小修复；先复现，再修改，再运行 acceptance。"
        "不得删除 Eval、降低等级或改写失败证据。最后列出修改文件、验证命令和剩余风险。"
    )
    completed = subprocess.run(
        ["codex", "exec", "--json", "--sandbox", "workspace-write", "--ephemeral", "-o", str(result_path), prompt],
        text=True, capture_output=True, timeout=timeout, check=False,
    )
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate = event.get("usage") if isinstance(event, dict) else None
        if not isinstance(candidate, dict):
            continue
        input_tokens = int(candidate.get("input_tokens", 0) or 0)
        output_tokens = int(candidate.get("output_tokens", 0) or 0)
        total_tokens = int(candidate.get("total_tokens", input_tokens + output_tokens) or 0)
        if total_tokens >= usage["total_tokens"]:
            usage = {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens}
    return {
        "returncode": completed.returncode, "stdout": completed.stdout[-1000:],
        "stderr": completed.stderr[-1000:], "result_file": str(result_path), "usage": usage,
    }


def run_loop(max_rounds: int = 3, token_budget: int = 30_000, timeout_seconds: int = 900,
             use_codex: bool = False, *, suite_runner: Callable[..., dict] = run_suite,
             executor: Callable[[dict, int, int], dict] = _run_codex) -> dict:
    if max_rounds < 1 or max_rounds > 10: raise ValueError("max_rounds 必须在 1..10")
    if token_budget < 1: raise ValueError("token_budget 必须大于 0")
    if timeout_seconds < 1: raise ValueError("timeout_seconds 必须大于 0")
    started = time.monotonic(); history: list[dict] = []; previous: tuple[str, ...] | None = None
    tokens_used = 0
    def finish(status: str, round_no: int, failures: tuple[str, ...] | list[str]) -> dict:
        return {
            "status": status, "rounds": round_no, "history": history,
            "remaining_failures": list(failures), "token_budget": token_budget,
            "tokens_used": tokens_used, "tokens_remaining": max(0, token_budget - tokens_used),
        }
    for round_no in range(1, max_rounds + 1):
        report = suite_runner("blocking", write_report=True); failures = _signature(report)
        entry: dict = {
            "round": round_no, "failures": list(failures), "decision": report["summary"]["decision"],
            "tokens_used_before": tokens_used, "tokens_remaining_before": max(0, token_budget - tokens_used),
        }
        history.append(entry)
        if not failures:
            return finish("converged", round_no, [])
        if failures == previous:
            return finish("stopped_no_progress", round_no, failures)
        if time.monotonic() - started >= timeout_seconds:
            return finish("stopped_time_budget", round_no, failures)
        if use_codex and tokens_used >= token_budget:
            return finish("stopped_token_budget", round_no, failures)
        task = build_repair_task(report, f".runtime/loop/repair-round-{round_no}.json")
        entry["repair_task"] = task
        if not use_codex:
            entry["executor"] = "dry-run: 仅生成修复任务；传 --codex 才授权本机 Codex 修改"
            previous = failures; continue
        remaining_seconds = max(1, int(timeout_seconds - (time.monotonic() - started)))
        entry["codex"] = executor(task, round_no, remaining_seconds)
        usage = entry["codex"].get("usage", {})
        measured = int(usage.get("total_tokens", 0) or 0) if isinstance(usage, dict) else 0
        if measured <= 0:
            entry["budget_decision"] = "停止：执行器未返回可核验 token 用量"
            return finish("stopped_token_usage_unavailable", round_no, failures)
        tokens_used += measured
        entry["tokens_used_after"] = tokens_used
        entry["tokens_remaining_after"] = max(0, token_budget - tokens_used)
        if tokens_used >= token_budget:
            entry["budget_decision"] = "停止：累计用量达到或超过预算，不再启动下一轮"
            return finish("stopped_token_budget", round_no, failures)
        previous = failures
    remaining = history[-1]["failures"] if history else []
    return finish("stopped_max_rounds", len(history), remaining)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded FlowERP repair loop")
    parser.add_argument("--max-rounds", type=int, default=3); parser.add_argument("--token-budget", type=int, default=30000)
    parser.add_argument("--timeout", type=int, default=900); parser.add_argument("--codex", action="store_true", help="显式授权调用本机 codex exec")
    args = parser.parse_args(); result = run_loop(args.max_rounds, args.token_budget, args.timeout, args.codex)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "converged" else 2


if __name__ == "__main__":
    raise SystemExit(main())
