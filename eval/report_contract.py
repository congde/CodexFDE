"""Validate a report against the cases requested by a caller, not its own green summary."""
from __future__ import annotations


def validate_report(report: dict, case_names: tuple[str, ...], returncode: int,
                    suite: str = "blocking") -> None:
    if not isinstance(report, dict) or report.get("schema_version") != "1.0":
        raise RuntimeError("Eval 报告 Schema 无效")
    if report.get("suite") != suite:
        raise RuntimeError("Eval 报告 suite 与请求不一致")
    requested = report.get("requested_cases")
    results = report.get("results")
    if not isinstance(requested, list) or any(not isinstance(name, str) for name in requested):
        raise RuntimeError("Eval 报告缺少请求用例身份")
    if len(requested) != len(set(requested)) or set(requested) != set(case_names):
        raise RuntimeError("Eval 请求用例与本次任务不一致")
    if not isinstance(results, list) or not results or any(not isinstance(item, dict) for item in results):
        raise RuntimeError("Eval 结果必须包含实际用例")
    names = [item.get("name") for item in results]
    if any(not isinstance(name, str) for name in names) or len(names) != len(set(names)) or set(names) != set(case_names):
        raise RuntimeError("Eval 实际用例缺失、重复或被替换")
    for item in results:
        if type(item.get("passed")) is not bool:
            raise RuntimeError("Eval passed 必须为布尔值")
        if item.get("level") not in {"blocking", "observing"}:
            raise RuntimeError("Eval 用例等级无效")
        if suite in {"blocking", "observing"} and item["level"] != suite:
            raise RuntimeError("Eval 用例等级与 suite 不一致")
    blocking_failed = sum(not item["passed"] and item["level"] == "blocking" for item in results)
    expected = {
        "total": len(results), "passed": sum(item["passed"] for item in results),
        "blocking_failed": blocking_failed,
        "observing_failed": sum(not item["passed"] and item["level"] == "observing" for item in results),
        "decision": "block" if blocking_failed else "pass",
    }
    summary = report.get("summary")
    if not isinstance(summary, dict) or any(summary.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Eval 汇总与逐项结果不一致")
    if any(type(summary.get(key)) is not int for key in ("total", "passed", "blocking_failed", "observing_failed")):
        raise RuntimeError("Eval 汇总计数必须为整数")
    if returncode != (1 if blocking_failed else 0):
        raise RuntimeError("Eval 进程退出码与报告结论不一致")
