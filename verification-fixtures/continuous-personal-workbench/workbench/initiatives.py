"""Minimal initiative ledger; registration never signs off a delivery."""
from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .bootstrap import LedgerError
from .task_store import TaskStore, required


class InitiativeLedger:
    def __init__(self, runtime_dir):
        self.runtime = Path(runtime_dir).resolve()
        self.database = self.runtime / 'workbench.db'

    def create(self, *, actor, requirement, scope, evidence=''):
        actor, requirement, scope = (required(value, name) for value, name in
                                     ((actor, 'actor'), (requirement, 'requirement'), (scope, 'scope')))
        if not isinstance(evidence, str) or max(map(len, (actor, requirement, scope, evidence))) > 20000:
            raise LedgerError('invalid_argument', '字段须为文本且每项不超过 20000 字符')
        item = {'ok': True, 'id': 'ITEM-' + uuid4().hex.upper(), 'actor': actor, 'requirement': requirement,
                'scope': scope, 'evidence': evidence, 'task_id': None, 'status': 'registered',
                'created_at': datetime.now(timezone.utc).isoformat(), 'acceptance': 'pending_human_review'}
        self.runtime.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS initiatives (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            connection.execute('INSERT INTO initiatives VALUES (?, ?)', (item['id'], json.dumps(item, ensure_ascii=False)))
        return item

    def list(self):
        if not self.database.is_file():
            return []
        with closing(sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True)) as connection, connection:
            if connection.execute("SELECT name FROM sqlite_master WHERE name='initiatives'").fetchone() is None:
                return []
            return [json.loads(row[0]) for row in connection.execute('SELECT body FROM initiatives ORDER BY rowid DESC')]

    def get(self, initiative_id):
        for item in self.list():
            if item['id'] == initiative_id:
                return item
        raise LedgerError('initiative_missing', '事项不存在')

    def link(self, initiative_id, task_id):
        TaskStore(self.runtime).get(task_id)  # Only an actual task in this runtime can be linked.
        self.get(initiative_id)
        with closing(sqlite3.connect(self.database.as_uri() + '?mode=rw', uri=True)) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT body FROM initiatives WHERE id=?', (initiative_id,)).fetchone()
            if row is None:
                raise LedgerError('initiative_missing', '事项不存在')
            item = json.loads(row[0])
            if item['task_id'] is not None and item['task_id'] != task_id:
                raise LedgerError('initiative_already_linked', '事项已有任务关联，不能覆盖')
            item['task_id'] = task_id
            connection.execute('UPDATE initiatives SET body=? WHERE id=?', (json.dumps(item, ensure_ascii=False), initiative_id))
        return item
