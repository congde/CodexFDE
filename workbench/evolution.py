from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .feedback import _connect as feedback_connect
from .task_store import TaskStore


DEFAULT_DB = ".runtime/workbench.db"
CLASSIFICATIONS = {
    "erp_rule",
    "erp_workflow",
    "workbench_control",
    "workbench_observability",
    "new_requirement",
}
ASSET_TYPES = {"rule", "spec", "eval", "skill", "harness", "implementation", "documentation"}


class EvolutionStore:
    """Governed evidence that one observed failure became a verified reusable asset."""

    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        TaskStore(self.path)
        with feedback_connect(self.path):
            pass
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS evolutions(
                  id TEXT PRIMARY KEY,
                  feedback_id TEXT NOT NULL UNIQUE,
                  source_task_id TEXT NOT NULL,
                  candidate_task_id TEXT,
                  business_refs_json TEXT NOT NULL,
                  failure_signature TEXT NOT NULL,
                  classification TEXT NOT NULL,
                  status TEXT NOT NULL,
                  asset_changes_json TEXT NOT NULL DEFAULT '[]',
                  decision_by TEXT,
                  decision_at TEXT,
                  decision_note TEXT,
                  blocking_report TEXT,
                  human_acceptance TEXT,
                  verified_by TEXT,
                  verified_at TEXT,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS evolution_events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  evolution_id TEXT NOT NULL,
                  from_status TEXT,
                  to_status TEXT NOT NULL,
                  detail TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  evidence_json TEXT,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_evolutions_status_created
                  ON evolutions(status,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_evolution_events_record
                  ON evolution_events(evolution_id,id);
                """
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["business_refs"] = json.loads(item.pop("business_refs_json") or "[]")
        item["asset_changes"] = json.loads(item.pop("asset_changes_json") or "[]")
        return item

    def create(
        self,
        feedback_id: str,
        failure_signature: str,
        classification: str,
        business_refs: list[str] | None = None,
        actor: str = "system",
    ) -> dict:
        feedback_id = feedback_id.strip()
        failure_signature = failure_signature.strip()
        classification = classification.strip().lower()
        actor = actor.strip() or "system"
        if not feedback_id or not failure_signature:
            raise ValueError("进化记录必须包含反馈编号和稳定失败签名")
        if len(failure_signature) > 200:
            raise ValueError("失败签名不能超过 200 个字符")
        if classification not in CLASSIFICATIONS:
            raise ValueError("进化分类无效")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            feedback = conn.execute(
                "SELECT id,task_id,status FROM feedback WHERE id=?", (feedback_id,)
            ).fetchone()
            if not feedback:
                raise KeyError(feedback_id)
            if feedback["status"] != "accepted":
                raise ValueError("只有具名接受的反馈才能提升为进化记录")
            task = conn.execute(
                "SELECT id,business_refs_json FROM tasks WHERE id=?", (feedback["task_id"],)
            ).fetchone()
            if not task:
                raise ValueError("反馈关联的源任务不存在，不能建立进化因果链")
            refs = [str(value).strip() for value in (business_refs or json.loads(task["business_refs_json"] or "[]")) if str(value).strip()]
            if not refs:
                raise ValueError("进化记录至少关联一个 ERP 业务对象")
            if len(refs) != len(set(refs)):
                raise ValueError("进化记录的业务对象引用不能重复")
            evolution_id = f"EVO-{uuid.uuid4().hex[:10].upper()}"
            try:
                conn.execute(
                    "INSERT INTO evolutions(id,feedback_id,source_task_id,business_refs_json,"
                    "failure_signature,classification,status) VALUES(?,?,?,?,?,?,?)",
                    (
                        evolution_id,
                        feedback_id,
                        feedback["task_id"],
                        json.dumps(refs, ensure_ascii=False),
                        failure_signature,
                        classification,
                        "proposed",
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("同一反馈只能形成一条进化记录") from exc
            self._event(
                conn,
                evolution_id,
                None,
                "proposed",
                "已从具名接受的反馈建立进化候选",
                actor,
                {"feedback_id": feedback_id, "source_task_id": feedback["task_id"], "business_refs": refs},
            )
        return self.get(evolution_id)

    def review(self, evolution_id: str, reviewer: str, decision: str, note: str) -> dict:
        reviewer = reviewer.strip()
        decision = decision.strip().lower()
        note = note.strip()
        if not reviewer or not note:
            raise ValueError("进化审核必须包含具名审核人和理由")
        targets = {"approve": "approved", "reject": "rejected", "defer": "deferred"}
        if decision not in targets:
            raise ValueError("进化审核决定必须是 approve、reject 或 defer")
        target = targets[decision]
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._status(conn, evolution_id)
            if current != "proposed":
                raise ValueError(f"非法进化状态迁移：{current} -> {target}")
            decided_at = self._now()
            updated = conn.execute(
                "UPDATE evolutions SET status=?,decision_by=?,decision_at=?,decision_note=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='proposed'",
                (target, reviewer, decided_at, note, evolution_id),
            )
            if updated.rowcount != 1:
                raise ValueError("进化状态已变化，拒绝并发审核")
            self._event(
                conn,
                evolution_id,
                current,
                target,
                f"具名进化审核：{decision}；{note}",
                reviewer,
                {"decision": decision, "note": note},
            )
        return self.get(evolution_id)

    def record_assets(self, evolution_id: str, asset_changes: list[dict], actor: str) -> dict:
        actor = actor.strip()
        if not actor:
            raise ValueError("登记资产变更必须具名")
        assets = self._validate_assets(asset_changes)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._status(conn, evolution_id)
            if current not in {"approved", "asset_changed"}:
                raise ValueError(f"非法进化状态迁移：{current} -> asset_changed")
            row = conn.execute(
                "SELECT asset_changes_json FROM evolutions WHERE id=?", (evolution_id,)
            ).fetchone()
            existing = json.loads(row["asset_changes_json"] or "[]")
            identities = {(item["type"], item["path"]) for item in existing}
            duplicates = [item for item in assets if (item["type"], item["path"]) in identities]
            if duplicates:
                raise ValueError("同一类型和路径的资产变化不能重复登记")
            merged = existing + assets
            updated = conn.execute(
                "UPDATE evolutions SET status='asset_changed',asset_changes_json=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
                (json.dumps(merged, ensure_ascii=False), evolution_id, current),
            )
            if updated.rowcount != 1:
                raise ValueError("进化状态已变化，拒绝并发登记资产")
            self._event(
                conn,
                evolution_id,
                current,
                "asset_changed",
                "已登记版本化工程资产变化" if current == "approved" else "已追加版本化工程资产变化",
                actor,
                {"asset_changes": assets},
            )
        return self.get(evolution_id)

    def verify(
        self,
        evolution_id: str,
        candidate_task_id: str,
        blocking_report: str,
        actor: str,
    ) -> dict:
        candidate_task_id = candidate_task_id.strip()
        blocking_report = blocking_report.strip()
        actor = actor.strip()
        if not candidate_task_id or not actor:
            raise ValueError("验证进化必须包含候选任务和验证人")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            evolution = conn.execute(
                "SELECT source_task_id,business_refs_json,status,asset_changes_json,created_at "
                "FROM evolutions WHERE id=?", (evolution_id,)
            ).fetchone()
            if not evolution:
                raise KeyError(evolution_id)
            if evolution["status"] != "asset_changed":
                raise ValueError(f"非法进化状态迁移：{evolution['status']} -> verified")
            if candidate_task_id == evolution["source_task_id"]:
                raise ValueError("进化必须由下一项独立交付任务验证，不能复用源任务")
            task = conn.execute(
                "SELECT status,result_json,reviewed_by,review_decision,reviewed_at,"
                "business_refs_json,created_at FROM tasks WHERE id=?",
                (candidate_task_id,),
            ).fetchone()
            if not task:
                raise ValueError("候选交付任务不存在")
            evolution_refs = set(json.loads(evolution["business_refs_json"] or "[]"))
            candidate_refs = set(json.loads(task["business_refs_json"] or "[]"))
            if not evolution_refs.intersection(candidate_refs):
                raise ValueError("候选任务必须与进化记录共享至少一个 ERP 业务对象")
            if task["created_at"] < evolution["created_at"]:
                raise ValueError("候选任务必须在进化候选建立后创建")
            result = json.loads(task["result_json"] or "{}")
            summary = result.get("summary", {}) if isinstance(result, dict) else {}
            if task["status"] != "completed" or task["review_decision"] != "approve" or not task["reviewed_by"]:
                raise ValueError("候选任务必须完成 Blocking Eval 并经过具名交付验收")
            if summary.get("decision") != "pass" or int(summary.get("blocking_failed", 0)) != 0:
                raise ValueError("候选任务的阻断级 Eval 未通过")
            results = result.get("results", []) if isinstance(result, dict) else []
            blocking_results = [
                item for item in results
                if isinstance(item, dict) and item.get("level") == "blocking"
            ]
            if not blocking_results or any(item.get("passed") is not True for item in blocking_results):
                raise ValueError("候选任务缺少逐项通过的阻断级 Eval 证据")
            canonical_report = str(result.get("report_path", "")).strip()
            expected_hash = str(result.get("report_sha256", "")).strip().lower()
            if not canonical_report or not expected_hash:
                raise ValueError("候选任务缺少不可替换的 Task 级 Blocking 报告快照")
            runtime_root = Path(self.path).resolve().parent
            report_root = (runtime_root / "reports").resolve()
            report_ref = Path(canonical_report)
            report_file = report_ref.resolve() if report_ref.is_absolute() else (runtime_root / report_ref).resolve()
            if report_root != report_file.parent or not report_file.is_file():
                raise ValueError("候选任务的 Blocking 报告不在受控运行目录或已经丢失")
            actual_hash = hashlib.sha256(report_file.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError("候选任务的 Blocking 报告校验和不一致")
            if blocking_report:
                supplied_ref = Path(blocking_report)
                supplied_file = supplied_ref.resolve() if supplied_ref.is_absolute() else (runtime_root / supplied_ref).resolve()
                if supplied_file != report_file:
                    raise ValueError("提交的 Blocking 报告与候选任务持久化证据不一致")
            verified_at = self._now()
            acceptance = f"{task['reviewed_by']}@{task['reviewed_at']}"
            updated = conn.execute(
                "UPDATE evolutions SET status='verified',candidate_task_id=?,blocking_report=?,"
                "human_acceptance=?,verified_by=?,verified_at=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='asset_changed'",
                (candidate_task_id, canonical_report, acceptance, actor, verified_at, evolution_id),
            )
            if updated.rowcount != 1:
                raise ValueError("进化状态已变化，拒绝并发验证")
            self._event(
                conn,
                evolution_id,
                "asset_changed",
                "verified",
                "下一项交付任务已用阻断证据和具名验收证明资产升级有效",
                actor,
                {
                    "candidate_task_id": candidate_task_id,
                    "blocking_report": canonical_report,
                    "report_sha256": expected_hash,
                    "decision": summary.get("decision"),
                    "blocking_failed": summary.get("blocking_failed"),
                    "human_acceptance": acceptance,
                },
            )
        return self.get(evolution_id)

    def get(self, evolution_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM evolutions WHERE id=?", (evolution_id,)).fetchone()
            if not row:
                raise KeyError(evolution_id)
            item = self._decode(row)
            events = []
            for event_row in conn.execute(
                "SELECT * FROM evolution_events WHERE evolution_id=? ORDER BY id", (evolution_id,)
            ):
                event = dict(event_row)
                event["evidence"] = json.loads(event.pop("evidence_json") or "null")
                events.append(event)
            item["events"] = events
            return item

    def list(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self.connect() as conn:
            return [self._decode(row) for row in conn.execute(
                "SELECT * FROM evolutions ORDER BY created_at DESC,id DESC LIMIT ?", (limit,)
            )]

    def summary(self, limit: int = 100) -> dict:
        items = self.list(limit)
        with self.connect() as conn:
            counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM evolutions GROUP BY status"
                )
            }
        return {
            "total": sum(counts.values()),
            "proposed": counts.get("proposed", 0),
            "approved": counts.get("approved", 0),
            "asset_changed": counts.get("asset_changed", 0),
            "verified": counts.get("verified", 0),
            "rejected": counts.get("rejected", 0),
            "deferred": counts.get("deferred", 0),
            "items": items,
        }

    @staticmethod
    def _validate_assets(asset_changes: list[dict]) -> list[dict]:
        if not isinstance(asset_changes, list) or not asset_changes:
            raise ValueError("至少登记一项版本化资产变化")
        if len(asset_changes) > 20:
            raise ValueError("单次最多登记 20 项资产变化")
        assets = []
        identities: set[tuple[str, str]] = set()
        for item in asset_changes:
            if not isinstance(item, dict):
                raise ValueError("资产变化必须是对象数组")
            asset_type = str(item.get("type", "")).strip().lower()
            path = str(item.get("path", "")).strip()
            reason = str(item.get("reason", "")).strip()
            if asset_type not in ASSET_TYPES or not path or not reason:
                raise ValueError("每项资产变化必须包含有效 type、path 和 reason")
            if len(path) > 500 or len(reason) > 1000:
                raise ValueError("资产路径或变更原因过长")
            identity = (asset_type, path)
            if identity in identities:
                raise ValueError("同一批次不能重复登记相同类型和路径的资产")
            identities.add(identity)
            assets.append({"type": asset_type, "path": path, "reason": reason})
        return assets

    @staticmethod
    def _status(conn: sqlite3.Connection, evolution_id: str) -> str:
        row = conn.execute("SELECT status FROM evolutions WHERE id=?", (evolution_id,)).fetchone()
        if not row:
            raise KeyError(evolution_id)
        return str(row["status"])

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        evolution_id: str,
        from_status: str | None,
        to_status: str,
        detail: str,
        actor: str,
        evidence: object = None,
    ) -> None:
        conn.execute(
            "INSERT INTO evolution_events(evolution_id,from_status,to_status,detail,actor,evidence_json) "
            "VALUES(?,?,?,?,?,?)",
            (
                evolution_id,
                from_status,
                to_status,
                detail,
                actor,
                json.dumps(evidence, ensure_ascii=False) if evidence is not None else None,
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="FlowERP governed evolution records")
    parser.add_argument("--db", default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--feedback", required=True)
    create.add_argument("--signature", required=True)
    create.add_argument("--classification", choices=sorted(CLASSIFICATIONS), required=True)
    create.add_argument("--business-ref", action="append", default=[])
    create.add_argument("--actor", default="cli-operator")
    review = sub.add_parser("review")
    review.add_argument("--id", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=("approve", "reject", "defer"), required=True)
    review.add_argument("--note", required=True)
    assets = sub.add_parser("assets")
    assets.add_argument("--id", required=True)
    assets.add_argument("--asset-json", required=True)
    assets.add_argument("--actor", default="cli-operator")
    verify = sub.add_parser("verify")
    verify.add_argument("--id", required=True)
    verify.add_argument("--task", required=True)
    verify.add_argument("--report", default="", help="可选交叉检查；权威报告自动取自候选 Task")
    verify.add_argument("--actor", default="cli-operator")
    sub.add_parser("summary")
    args = parser.parse_args()
    store = EvolutionStore(args.db)
    if args.command == "create":
        result = store.create(args.feedback, args.signature, args.classification, args.business_ref, args.actor)
    elif args.command == "review":
        result = store.review(args.id, args.reviewer, args.decision, args.note)
    elif args.command == "assets":
        result = store.record_assets(args.id, json.loads(args.asset_json), args.actor)
    elif args.command == "verify":
        result = store.verify(args.id, args.task, args.report, args.actor)
    else:
        result = store.summary()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
