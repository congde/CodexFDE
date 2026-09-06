from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DECISIONS = frozenset({"build", "experiment", "defer", "reject", "stop"})
REVERSIBILITY = frozenset({"easy", "partial", "hard"})
EVIDENCE_TYPES = frozenset({"observation", "user_feedback", "data", "incident", "experiment"})

STRICT_AREAS = frozenset({
    "inventory", "money", "payment", "auth", "permission", "personal_data", "security",
})
STANDARD_AREAS = frozenset({
    "order", "purchase", "api", "schema", "migration", "cross_team", "deployment",
})


def _text(value: object) -> str:
    return str(value or "").strip()


def _lossy_paths(value: object, path: str = "payload") -> list[str]:
    paths: list[str] = []
    if isinstance(value, str):
        if re.search(r"\?{3,}", value):
            paths.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            paths.extend(_lossy_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_lossy_paths(item, f"{path}[{index}]"))
    return paths


def _reject_lossy_text(value: object) -> None:
    paths = _lossy_paths(value)
    if paths:
        raise ValueError(
            "检测到疑似编码损坏的连续问号，已拒绝写入；请使用 UTF-8 重试。字段："
            + ", ".join(paths[:8])
        )


def _text_list(values: object) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("列表字段必须使用 JSON 数组")
    result = [_text(value) for value in values if _text(value)]
    return list(dict.fromkeys(result))


def _evidence(values: object) -> list[dict]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("evidence 必须使用 JSON 数组")
    result: list[dict] = []
    for value in values:
        if isinstance(value, str):
            item = {"type": "observation", "content": value.strip(), "source": ""}
        elif isinstance(value, dict):
            item = {
                "type": _text(value.get("type") or "observation").lower(),
                "content": _text(value.get("content")),
                "source": _text(value.get("source")),
            }
        else:
            raise ValueError("每条 evidence 必须是字符串或对象")
        if item["type"] not in EVIDENCE_TYPES:
            raise ValueError(f"未知 evidence 类型：{item['type']}")
        if not item["content"]:
            raise ValueError("evidence.content 不能为空")
        result.append(item)
    return result


def route_risk(affected_areas: list[str], reversibility: str,
               dependencies: list[str] | None = None) -> tuple[str, list[str]]:
    """Return a deterministic route recommendation, never the final human decision."""
    areas = {_text(value).lower() for value in affected_areas if _text(value)}
    reasons: list[str] = []
    strict_hits = sorted(areas & STRICT_AREAS)
    standard_hits = sorted(areas & STANDARD_AREAS)
    if strict_hits:
        reasons.append(f"涉及关键领域：{', '.join(strict_hits)}")
    if reversibility == "hard":
        reasons.append("变更难以恢复")
    if strict_hits or reversibility == "hard":
        return "strict", reasons
    if standard_hits:
        reasons.append(f"涉及共享或跨边界领域：{', '.join(standard_hits)}")
    if reversibility == "partial":
        reasons.append("只能部分恢复")
    if dependencies:
        reasons.append("存在外部依赖或协作方")
    if standard_hits or reversibility == "partial" or dependencies:
        return "standard", reasons
    return "quick", ["范围局部且容易恢复"]


class InitiativeStore:
    """Decision-first entry in front of the existing Task/Eval delivery engine."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS initiatives(
                  id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  raw_signal TEXT NOT NULL,
                  source TEXT NOT NULL,
                  problem_statement TEXT NOT NULL DEFAULT '',
                  goal TEXT NOT NULL DEFAULT '',
                  non_goals_json TEXT NOT NULL DEFAULT '[]',
                  constraints_json TEXT NOT NULL DEFAULT '[]',
                  acceptance_json TEXT NOT NULL DEFAULT '[]',
                  evidence_json TEXT NOT NULL DEFAULT '[]',
                  assumptions_json TEXT NOT NULL DEFAULT '[]',
                  affected_areas_json TEXT NOT NULL DEFAULT '[]',
                  dependencies_json TEXT NOT NULL DEFAULT '[]',
                  alternatives_json TEXT NOT NULL DEFAULT '[]',
                  reversibility TEXT NOT NULL,
                  risk_lane TEXT NOT NULL,
                  risk_reasons_json TEXT NOT NULL DEFAULT '[]',
                  owner TEXT NOT NULL,
                  reviewer TEXT NOT NULL DEFAULT '',
                  project_id TEXT NOT NULL DEFAULT '',
                  requirement_id TEXT NOT NULL DEFAULT '',
                  success_metric TEXT NOT NULL DEFAULT '',
                  stop_condition TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'investigating',
                  decision TEXT,
                  decision_rationale TEXT NOT NULL DEFAULT '',
                  decision_by TEXT NOT NULL DEFAULT '',
                  review_trigger TEXT NOT NULL DEFAULT '',
                  decided_at TEXT,
                  linked_task_id TEXT,
                  version INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS initiative_events(
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  initiative_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_initiative_events
                  ON initiative_events(initiative_id, sequence);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(initiatives)")}
            if "superseded_by" not in columns:
                conn.execute(
                    "ALTER TABLE initiatives ADD COLUMN superseded_by TEXT NOT NULL DEFAULT ''"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create(self, data: dict, actor: str) -> dict:
        _reject_lossy_text(data)
        title = _text(data.get("title"))
        raw_signal = _text(data.get("raw_signal"))
        source = _text(data.get("source"))
        owner = _text(data.get("owner")) or _text(actor)
        if not title:
            raise ValueError("事项 title 不能为空")
        if not raw_signal:
            raise ValueError("必须保留未经改写的 raw_signal")
        if not source:
            raise ValueError("必须说明 Signal 来源")
        if not owner:
            raise ValueError("事项必须有具名 Owner")
        reversibility = _text(data.get("reversibility") or "easy").lower()
        if reversibility not in REVERSIBILITY:
            raise ValueError("reversibility 必须是 easy、partial 或 hard")
        evidence = _evidence(data.get("evidence"))
        assumptions = _text_list(data.get("assumptions"))
        areas = [value.lower() for value in _text_list(data.get("affected_areas"))]
        dependencies = _text_list(data.get("dependencies"))
        lane, risk_reasons = route_risk(areas, reversibility, dependencies)
        initiative_id = f"INIT-{uuid.uuid4().hex[:10].upper()}"
        values = (
            initiative_id, title, raw_signal, source, _text(data.get("problem_statement")),
            _text(data.get("goal")), json.dumps(_text_list(data.get("non_goals")), ensure_ascii=False),
            json.dumps(_text_list(data.get("constraints")), ensure_ascii=False),
            json.dumps(_text_list(data.get("acceptance")), ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False), json.dumps(assumptions, ensure_ascii=False),
            json.dumps(areas, ensure_ascii=False), json.dumps(dependencies, ensure_ascii=False),
            json.dumps(_text_list(data.get("alternatives")), ensure_ascii=False), reversibility, lane,
            json.dumps(risk_reasons, ensure_ascii=False), owner, _text(data.get("reviewer")),
            _text(data.get("project_id")), _text(data.get("requirement_id")),
            _text(data.get("success_metric")), _text(data.get("stop_condition")),
        )
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO initiatives("
                "id,title,raw_signal,source,problem_statement,goal,non_goals_json,constraints_json,"
                "acceptance_json,evidence_json,assumptions_json,affected_areas_json,dependencies_json,"
                "alternatives_json,reversibility,risk_lane,risk_reasons_json,owner,reviewer,project_id,"
                "requirement_id,success_metric,stop_condition) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )
            self._append(conn, initiative_id, "initiative/captured", actor, {
                "source": source, "risk_lane": lane, "risk_reasons": risk_reasons,
                "evidence_count": len(evidence),
            })
        return self.get(initiative_id)

    @staticmethod
    def _append(conn: sqlite3.Connection, initiative_id: str, kind: str,
                actor: str, payload: dict) -> None:
        conn.execute(
            "INSERT INTO initiative_events(initiative_id,kind,actor,payload_json) VALUES(?,?,?,?)",
            (initiative_id, kind, _text(actor) or "system", json.dumps(payload, ensure_ascii=False)),
        )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        item = dict(row)
        for field in (
            "non_goals_json", "constraints_json", "acceptance_json", "evidence_json",
            "assumptions_json", "affected_areas_json", "dependencies_json", "alternatives_json",
            "risk_reasons_json",
        ):
            item[field.removesuffix("_json")] = json.loads(item.pop(field) or "[]")
        return item

    def _project(self, item: dict, events: list[dict] | None = None) -> dict:
        decision_missing = []
        if not item["evidence"]:
            decision_missing.append("evidence")
        if not item["problem_statement"]:
            decision_missing.append("problem_statement")
        delivery_missing = []
        for field in ("project_id", "goal"):
            if not item[field]:
                delivery_missing.append(field)
        if not item["acceptance"]:
            delivery_missing.append("acceptance")
        if item["risk_lane"] == "strict" and not item["reviewer"]:
            delivery_missing.append("reviewer")
        experiment_missing = []
        for field in ("success_metric", "stop_condition"):
            if not item[field]:
                experiment_missing.append(field)
        item["readiness"] = {
            "decision_ready": not decision_missing,
            "decision_missing": decision_missing,
            "delivery_ready": not delivery_missing,
            "delivery_missing": delivery_missing,
            "experiment_ready": not experiment_missing,
            "experiment_missing": experiment_missing,
        }
        item["work_packages"] = {
            "decision_brief": {
                "signal": item["raw_signal"], "source": item["source"],
                "problem": item["problem_statement"], "evidence": item["evidence"],
                "assumptions": item["assumptions"], "alternatives": item["alternatives"],
                "risk_lane": item["risk_lane"], "decision": item["decision"],
            },
            "delivery_contract": {
                "goal": item["goal"], "non_goals": item["non_goals"],
                "constraints": item["constraints"], "acceptance": item["acceptance"],
            },
            "evidence_pack": {
                "initiative_id": item["id"], "version": item["version"],
                "linked_task_id": item["linked_task_id"],
            },
        }
        if events is not None:
            item["events"] = events
        return item

    def get(self, initiative_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM initiatives WHERE id=?", (initiative_id,)).fetchone()
            if not row:
                raise KeyError(initiative_id)
            event_rows = conn.execute(
                "SELECT * FROM initiative_events WHERE initiative_id=? ORDER BY sequence", (initiative_id,),
            ).fetchall()
        events = []
        for event_row in event_rows:
            event = dict(event_row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return self._project(self._decode(row), events)

    def list(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM initiatives ORDER BY updated_at DESC,id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [self._project(self._decode(row)) for row in rows]

    def revise(self, initiative_id: str, changes: dict, actor: str,
               expected_version: int) -> dict:
        _reject_lossy_text(changes)
        allowed = {
            "source", "problem_statement", "goal", "non_goals", "constraints", "acceptance",
            "evidence", "assumptions", "affected_areas", "dependencies", "alternatives",
            "reversibility", "owner", "reviewer", "project_id", "requirement_id",
            "success_metric", "stop_condition",
        }
        unknown = set(changes) - allowed - {"expected_version"}
        if unknown:
            raise ValueError(f"不可修订字段：{', '.join(sorted(unknown))}")
        changed_fields = sorted(set(changes) & allowed)
        if not changed_fields:
            raise ValueError("没有可保存的修订内容")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM initiatives WHERE id=?", (initiative_id,)).fetchone()
            if not row:
                raise KeyError(initiative_id)
            item = self._decode(row)
            if item["decision"] or item["status"] != "investigating":
                raise ValueError("已形成正式决定的事项不可静默改写；请创建复查事项")
            if int(item["version"]) != int(expected_version):
                raise ValueError(f"事项版本已变化：期望 {expected_version}，实际 {item['version']}")

            scalar_fields = (
                "source", "problem_statement", "goal", "owner", "reviewer", "project_id",
                "requirement_id", "success_metric", "stop_condition",
            )
            for field in scalar_fields:
                if field in changes:
                    item[field] = _text(changes[field])
            if not item["source"]:
                raise ValueError("Signal 来源不能为空")
            if not item["owner"]:
                raise ValueError("事项必须有具名 Owner")
            for field in ("non_goals", "constraints", "acceptance", "assumptions", "dependencies", "alternatives"):
                if field in changes:
                    item[field] = _text_list(changes[field])
            if "evidence" in changes:
                item["evidence"] = _evidence(changes["evidence"])
            if "affected_areas" in changes:
                item["affected_areas"] = [value.lower() for value in _text_list(changes["affected_areas"])]
            if "reversibility" in changes:
                item["reversibility"] = _text(changes["reversibility"]).lower()
                if item["reversibility"] not in REVERSIBILITY:
                    raise ValueError("reversibility 必须是 easy、partial 或 hard")
            lane, reasons = route_risk(
                item["affected_areas"], item["reversibility"], item["dependencies"],
            )
            updated = conn.execute(
                "UPDATE initiatives SET source=?,problem_statement=?,goal=?,non_goals_json=?,"
                "constraints_json=?,acceptance_json=?,evidence_json=?,assumptions_json=?,"
                "affected_areas_json=?,dependencies_json=?,alternatives_json=?,reversibility=?,"
                "risk_lane=?,risk_reasons_json=?,owner=?,reviewer=?,project_id=?,requirement_id=?,"
                "success_metric=?,stop_condition=?,version=version+1,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND version=?",
                (
                    item["source"], item["problem_statement"], item["goal"],
                    json.dumps(item["non_goals"], ensure_ascii=False),
                    json.dumps(item["constraints"], ensure_ascii=False),
                    json.dumps(item["acceptance"], ensure_ascii=False),
                    json.dumps(item["evidence"], ensure_ascii=False),
                    json.dumps(item["assumptions"], ensure_ascii=False),
                    json.dumps(item["affected_areas"], ensure_ascii=False),
                    json.dumps(item["dependencies"], ensure_ascii=False),
                    json.dumps(item["alternatives"], ensure_ascii=False), item["reversibility"],
                    lane, json.dumps(reasons, ensure_ascii=False), item["owner"], item["reviewer"],
                    item["project_id"], item["requirement_id"], item["success_metric"],
                    item["stop_condition"], initiative_id, int(expected_version),
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("事项发生并发变化，请刷新后重试")
            self._append(conn, initiative_id, "initiative/revised", actor, {
                "changed_fields": changed_fields, "risk_lane": lane, "risk_reasons": reasons,
            })
        return self.get(initiative_id)

    def decide(self, initiative_id: str, decision: str, actor: str, rationale: str,
               expected_version: int, *, review_trigger: str = "",
               reviewer: str = "", success_metric: str = "", stop_condition: str = "") -> dict:
        _reject_lossy_text({
            "rationale": rationale,
            "review_trigger": review_trigger,
            "reviewer": reviewer,
            "success_metric": success_metric,
            "stop_condition": stop_condition,
        })
        decision = _text(decision).lower()
        rationale = _text(rationale)
        review_trigger = _text(review_trigger)
        if decision not in DECISIONS:
            raise ValueError("decision 必须是 build、experiment、defer、reject 或 stop")
        if not rationale:
            raise ValueError("决定必须说明 rationale")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM initiatives WHERE id=?", (initiative_id,)).fetchone()
            if not row:
                raise KeyError(initiative_id)
            item = self._project(self._decode(row))
            if int(item["version"]) != int(expected_version):
                raise ValueError(f"事项版本已变化：期望 {expected_version}，实际 {item['version']}")
            if decision in {"build", "experiment"} and not item["readiness"]["decision_ready"]:
                missing = ", ".join(item["readiness"]["decision_missing"])
                raise ValueError(f"证据门未通过，缺少：{missing}")
            effective_reviewer = _text(reviewer) or item["reviewer"]
            effective_metric = _text(success_metric) or item["success_metric"]
            effective_stop = _text(stop_condition) or item["stop_condition"]
            if decision == "build":
                missing = list(item["readiness"]["delivery_missing"])
                if effective_reviewer and "reviewer" in missing:
                    missing.remove("reviewer")
                if missing:
                    raise ValueError(f"交付合同未就绪，缺少：{', '.join(missing)}")
            if decision == "experiment" and (not effective_metric or not effective_stop):
                raise ValueError("Experiment 必须有 success_metric 和 stop_condition")
            if decision == "defer" and not review_trigger:
                raise ValueError("Defer 必须有 review_trigger")
            status = {
                "build": "approved_for_delivery", "experiment": "experiment",
                "defer": "deferred", "reject": "rejected", "stop": "stopped",
            }[decision]
            updated = conn.execute(
                "UPDATE initiatives SET decision=?,status=?,decision_rationale=?,decision_by=?,"
                "review_trigger=?,reviewer=?,success_metric=?,stop_condition=?,decided_at=CURRENT_TIMESTAMP,"
                "version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND version=?",
                (decision, status, rationale, _text(actor), review_trigger, effective_reviewer,
                 effective_metric, effective_stop, initiative_id, int(expected_version)),
            )
            if updated.rowcount != 1:
                raise ValueError("事项发生并发变化，请刷新后重试")
            self._append(conn, initiative_id, "decision/recorded", actor, {
                "decision": decision, "rationale": rationale, "review_trigger": review_trigger,
                "risk_lane": item["risk_lane"],
            })
        return self.get(initiative_id)

    def supersede_corrupted(self, initiative_id: str, replacement_id: str,
                            actor: str, reason: str) -> dict:
        actor = _text(actor)
        reason = _text(reason)
        if not actor:
            raise ValueError("编码修复必须有具名 actor")
        if not reason:
            raise ValueError("编码修复必须说明 reason")
        if initiative_id == replacement_id:
            raise ValueError("替代记录不能指向自身")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target_row = conn.execute(
                "SELECT * FROM initiatives WHERE id=?", (initiative_id,),
            ).fetchone()
            replacement_row = conn.execute(
                "SELECT * FROM initiatives WHERE id=?", (replacement_id,),
            ).fetchone()
            if not target_row or not replacement_row:
                raise KeyError(initiative_id if not target_row else replacement_id)
            target = self._decode(target_row)
            replacement = self._decode(replacement_row)
            if target.get("superseded_by"):
                if target["superseded_by"] != replacement_id:
                    raise ValueError(f"事项已由 {target['superseded_by']} 替代")
                return self._project(target)
            corrupted_fields = _lossy_paths(target, "initiative")
            if not corrupted_fields:
                raise ValueError("目标事项未检测到连续问号编码损坏")
            if _lossy_paths(replacement, "replacement"):
                raise ValueError("替代事项仍包含疑似编码损坏")
            target_requirement = _text(target.get("requirement_id"))
            replacement_requirement = _text(replacement.get("requirement_id"))
            if target_requirement and replacement_requirement != target_requirement:
                raise ValueError("替代事项的 requirement_id 不一致")
            updated = conn.execute(
                "UPDATE initiatives SET status='superseded',superseded_by=?,version=version+1,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND superseded_by=''",
                (replacement_id, initiative_id),
            )
            if updated.rowcount != 1:
                raise ValueError("事项替代状态发生并发变化")
            self._append(conn, initiative_id, "initiative/encoding-superseded", actor, {
                "replacement_id": replacement_id,
                "reason": reason,
                "corrupted_fields": corrupted_fields,
                "original_content_preserved": True,
            })
        return self.get(initiative_id)

    def link_delivery(self, initiative_id: str, task_id: str, actor: str,
                      expected_version: int) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM initiatives WHERE id=?", (initiative_id,)).fetchone()
            if not row:
                raise KeyError(initiative_id)
            item = self._decode(row)
            if item["decision"] != "build" or item["status"] != "approved_for_delivery":
                raise ValueError("只有 Build 决定可以进入交付")
            if item["linked_task_id"]:
                if item["linked_task_id"] != task_id:
                    raise ValueError(f"事项已关联 Task：{item['linked_task_id']}")
                return self.get(initiative_id)
            if int(item["version"]) != int(expected_version):
                raise ValueError(f"事项版本已变化：期望 {expected_version}，实际 {item['version']}")
            updated = conn.execute(
                "UPDATE initiatives SET linked_task_id=?,status='delivering',version=version+1,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND version=? AND linked_task_id IS NULL",
                (_text(task_id), initiative_id, int(expected_version)),
            )
            if updated.rowcount != 1:
                raise ValueError("事项交付关联发生并发冲突")
            self._append(conn, initiative_id, "delivery/linked", actor, {"task_id": _text(task_id)})
        return self.get(initiative_id)

    @staticmethod
    def delivery_request(item: dict) -> str:
        contract = item["work_packages"]["delivery_contract"]
        lines = [
            f"事项：{item['title']}",
            f"问题：{item['problem_statement']}",
            f"目标：{contract['goal']}",
        ]
        if contract["non_goals"]:
            lines.append("非目标：" + "；".join(contract["non_goals"]))
        if contract["constraints"]:
            lines.append("约束：" + "；".join(contract["constraints"]))
        lines.append("验收：" + "；".join(contract["acceptance"]))
        lines.append(f"决策证据：{item['id']}@v{item['version']}，风险通道={item['risk_lane']}")
        return "\n".join(lines)
