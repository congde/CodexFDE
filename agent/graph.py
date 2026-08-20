from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from eval.harness import run_suite
from .repair import build_repair_task


@dataclass
class DeliveryState:
    state: str = "develop"
    round_no: int = 0
    report: dict | None = None
    repair_task: dict | None = None
    trace: list[dict] = field(default_factory=list)
    error: str | None = None
    reviewer: str | None = None
    review_decision: str | None = None
    reviewed_at: str | None = None

    def move(self, target: str, reason: str) -> None:
        self.trace.append({"from": self.state, "to": target, "reason": reason, "round": self.round_no})
        self.state = target


def _load_state(path: Path) -> DeliveryState:
    data = json.loads(path.read_text(encoding="utf-8"))
    return DeliveryState(
        state=str(data.get("status", data.get("state", "develop"))),
        round_no=int(data.get("rounds", data.get("round_no", 0))),
        report=data.get("report"), repair_task=data.get("repair_task"),
        trace=list(data.get("trace", [])), error=data.get("error"),
        reviewer=data.get("reviewer"), review_decision=data.get("review_decision"),
        reviewed_at=data.get("reviewed_at"),
    )


def _result(state: DeliveryState) -> dict:
    return {
        "status": state.state, "rounds": state.round_no, "trace": state.trace,
        "report_summary": state.report["summary"] if state.report else None,
        "report": state.report, "repair_task": state.repair_task, "error": state.error,
        "reviewer": state.reviewer, "review_decision": state.review_decision,
        "reviewed_at": state.reviewed_at,
    }


def _save_state(path: Path | None, state: DeliveryState) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_result(state), ensure_ascii=False, indent=2), encoding="utf-8")


def run_graph(max_rounds: int = 3, reject_once: bool = False, *, require_human_review: bool = False,
              state_file: str | Path | None = None, review_decision: str | None = None,
              reviewer: str = "") -> dict:
    if max_rounds < 1 or max_rounds > 10:
        raise ValueError("max_rounds 必须在 1..10")
    if review_decision not in {None, "approve", "reject"}:
        raise ValueError("review_decision 必须为 approve 或 reject")
    if review_decision and not reviewer.strip():
        raise ValueError("提交人工审核决定时必须记录审核人")
    persistence = Path(state_file) if state_file else None
    state = _load_state(persistence) if persistence and persistence.exists() else DeliveryState()
    if state.state == "awaiting_human_review" and review_decision:
        state.reviewer = reviewer.strip()
        state.review_decision = review_decision
        state.reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if review_decision == "approve":
            state.move("completed", f"审核人 {state.reviewer} 批准交付")
        else:
            state.move("develop", f"审核人 {state.reviewer} 打回交付")
    try:
        while state.state not in {"completed", "failed", "stopped", "awaiting_human_review"}:
            if state.state == "develop":
                state.round_no += 1
                if state.round_no > max_rounds: state.move("stopped", "达到最大轮数，保留剩余失败"); continue
                state.move("test", "本轮最小变更完成，交给统一 Harness")
            elif state.state == "test":
                state.report = run_suite("blocking", write_report=True)
                if state.report["summary"]["blocking_failed"]:
                    state.repair_task = build_repair_task(state.report, ".runtime/graph-repair-task.json")
                    state.move("rework", "阻断级 Eval 失败")
                else: state.move("human_review", "阻断项为零，进入人工决策点")
            elif state.state == "rework":
                if state.round_no >= max_rounds: state.move("stopped", "未收敛，禁止宣称成功")
                else: state.move("develop", "仅携带失败证据和禁止变更进入下一轮")
            elif state.state == "human_review":
                if reject_once:
                    reject_once = False; state.move("develop", "演示：人工打回一次，验证回退路径")
                elif require_human_review or persistence:
                    state.move("awaiting_human_review", "阻断项为零，等待具名审核人批准或打回")
                else: state.move("completed", "本地演示审批策略自动通过")
            else: state.move("failed", f"未知状态：{state.state}")
    except Exception as exc:
        state.error = f"{type(exc).__name__}: {exc}"; state.move("failed", "异常显式进入失败态")
    _save_state(persistence, state)
    return _result(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicit FlowERP delivery state graph")
    parser.add_argument("--max-rounds", type=int, default=3); parser.add_argument("--reject-once", action="store_true")
    parser.add_argument("--require-human-review", action="store_true")
    parser.add_argument("--state-file"); parser.add_argument("--review-decision", choices=("approve", "reject"))
    parser.add_argument("--reviewer", default="")
    args = parser.parse_args(); result = run_graph(
        args.max_rounds, args.reject_once, require_human_review=args.require_human_review,
        state_file=args.state_file, review_decision=args.review_decision, reviewer=args.reviewer,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "completed": return 0
    if result["status"] == "awaiting_human_review": return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
