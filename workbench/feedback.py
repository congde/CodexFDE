from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


DEFAULT_DB = ".runtime/workbench.db"


@contextmanager
def _connect(path: str = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS feedback(
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, source TEXT NOT NULL,
        conclusion TEXT NOT NULL, next_step TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(feedback)")}
    migrations = {
        "status": "ALTER TABLE feedback ADD COLUMN status TEXT NOT NULL DEFAULT 'pending_review'",
        "reviewed_by": "ALTER TABLE feedback ADD COLUMN reviewed_by TEXT",
        "reviewed_at": "ALTER TABLE feedback ADD COLUMN reviewed_at TEXT",
        "review_note": "ALTER TABLE feedback ADD COLUMN review_note TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)
    conn.execute("UPDATE feedback SET status='accepted' WHERE reviewed=1 AND status='pending_review'")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_feedback(task_id: str, source: str, conclusion: str, next_step: str, path: str = DEFAULT_DB) -> dict:
    if not all(value.strip() for value in (task_id, source, conclusion, next_step)):
        raise ValueError("反馈必须包含 task_id、来源、结论和下一步")
    item = {"id": f"FB-{uuid.uuid4().hex[:8].upper()}", "task_id": task_id, "source": source, "conclusion": conclusion, "next_step": next_step}
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO feedback(id,task_id,source,conclusion,next_step) VALUES(:id,:task_id,:source,:conclusion,:next_step)", item
        )
        row = conn.execute("SELECT * FROM feedback WHERE id=?", (item["id"],)).fetchone()
    return dict(row) if row else item


def review_feedback(feedback_id: str, reviewer: str, decision: str, note: str = "", path: str = DEFAULT_DB) -> dict:
    reviewer = reviewer.strip()
    decision = decision.strip().lower()
    if not feedback_id.strip() or not reviewer:
        raise ValueError("审核必须包含反馈编号和审核人")
    if decision not in {"accept", "reject"}:
        raise ValueError("审核决定必须为 accept 或 reject")
    status = "accepted" if decision == "accept" else "rejected"
    reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(path) as conn:
        current = conn.execute("SELECT status FROM feedback WHERE id=?", (feedback_id,)).fetchone()
        if not current:
            raise KeyError(feedback_id)
        if current["status"] != "pending_review":
            raise ValueError("反馈已经审核，不能重复改变结论")
        conn.execute(
            """UPDATE feedback
               SET status=?, reviewed=1, reviewed_by=?, reviewed_at=?, review_note=?
               WHERE id=? AND status='pending_review'""",
            (status, reviewer, reviewed_at, note.strip(), feedback_id),
        )
        row = conn.execute("SELECT * FROM feedback WHERE id=?", (feedback_id,)).fetchone()
    return dict(row)


def summary(path: str = DEFAULT_DB) -> dict:
    with _connect(path) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM feedback ORDER BY created_at DESC,id DESC")]
    return {
        "total": len(rows),
        "reviewed": sum(r["status"] != "pending_review" for r in rows),
        "pending_review": sum(r["status"] == "pending_review" for r in rows),
        "accepted": sum(r["status"] == "accepted" for r in rows),
        "rejected": sum(r["status"] == "rejected" for r in rows),
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="FlowERP structured feedback")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("--task", required=True); add.add_argument("--source", required=True)
    add.add_argument("--conclusion", required=True); add.add_argument("--next", required=True)
    review = sub.add_parser("review")
    review.add_argument("--id", required=True); review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=("accept", "reject"), required=True); review.add_argument("--note", default="")
    sub.add_parser("summary")
    args = parser.parse_args()
    if args.command == "add":
        result = add_feedback(args.task, args.source, args.conclusion, args.next)
    elif args.command == "review":
        result = review_feedback(args.id, args.reviewer, args.decision, args.note)
    else:
        result = summary()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
