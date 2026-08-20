from __future__ import annotations

import json
from pathlib import Path


def build_repair_task(report: dict, output: str | Path = ".runtime/repair-task.json") -> dict:
    failed = [r for r in report.get("results", []) if not r.get("passed") and r.get("level") == "blocking"]
    task = {
        "objective": "仅修复阻断级 Eval，保持现有公共接口和业务不变量",
        "scope": [r["name"] for r in failed],
        "evidence": [{"eval": r["name"], "reason": r["evidence"]} for r in failed],
        "reproduce": "python -X utf8 -m eval.harness --suite blocking",
        "acceptance": "阻断级失败为 0，退出码为 0",
        "forbidden": ["删除或跳过 Eval", "把 blocking 降级为 observing", "直接修改运行数据库", "提交密钥"],
    }
    path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return task
