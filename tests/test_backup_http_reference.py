"""HTTP health routes never become filesystem dependencies in a migration."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from workbench.workbench_backup import _database_state


class BackupHTTPReferenceTests(unittest.TestCase):
    def check_references(self, *, posix_routes):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runtime=root/'runtime';runtime.mkdir()
            report=runtime/'reports'/'result.json';report.parent.mkdir();report.write_bytes(b'original evidence')
            artifact=root/'health-artifact';artifact.write_bytes(b'actual external artifact')
            database=runtime/'workbench.db'
            payload={'preview_config':{'health':{'path':'/health'}},
                'deployment_profiles':[{'health':{'path':'/ready'}}],
                'report_path':str(report),'report_sha256':hashlib.sha256(report.read_bytes()).hexdigest(),
                'actual_artifact':{'path':str(artifact)}}
            with closing(sqlite3.connect(database)) as connection,connection:
                connection.execute('CREATE TABLE project_configurations(id TEXT,payload TEXT)')
                connection.execute('INSERT INTO project_configurations VALUES (?,?)',('fixture',json.dumps(payload)))
            original=Path.is_absolute
            # Windows interprets /health differently; reproduce POSIX's lexical
            # absolute-route predicate while using real SQLite and file references.
            def absolute(path):
                return True if posix_routes and path.as_posix() in {'/health','/ready'} else original(path)
            with patch.object(Path,'is_absolute',absolute):
                _,references,dependencies=_database_state(database,runtime)
            self.assertEqual({'payload.report_path'},{r['field'] for r in references})
            self.assertEqual(payload['report_sha256'],references[0]['expected_sha256'])
            self.assertEqual({'payload.actual_artifact.path'},{d['field'] for d in dependencies})
            self.assertEqual(str(artifact),dependencies[0]['path'])

    def test_actual_platform_keeps_file_evidence_and_ignores_http_routes(self):
        self.check_references(posix_routes=False)

    def test_posix_absolute_health_routes_are_not_external_files(self):
        self.check_references(posix_routes=True)
