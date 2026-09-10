import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from workbench.workbench_server import WorkbenchApp, make_handler
from workbench.initiative import InitiativeStore


class HomeClearTests(unittest.TestCase):
    def test_clear_restore_persist_and_keep_evidence_with_stale_write_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            app = WorkbenchApp(Path(directory))
            item = app.initiatives.create({'title': 'fixture', 'raw_signal': 'failed run',
                'source': 'test', 'evidence': ['failure evidence']}, 'tester')
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            client = HTTPConnection('127.0.0.1', server.server_port)
            try:
                def post(action, version, origin=None):
                    headers = {'Content-Type': 'application/json'}
                    if origin: headers['Origin'] = origin
                    client.request('POST', '/api/v1/initiatives/'+item['id']+'/'+action,
                        json.dumps({'actor': 'tester', 'version': version}), headers)
                    response = client.getresponse()
                    return response.status, json.loads(response.read())
                self.assertEqual(403, post('clear-home', item['version'], 'https://elsewhere.example')[0])
                status, cleared = post('clear-home', item['version'])
                self.assertEqual(200, status)
                persisted = InitiativeStore(app.initiatives.path).get(item['id'])
                self.assertEqual(1, persisted['home_hidden'])
                self.assertEqual(item['evidence'], persisted['evidence'])
                self.assertEqual(item['status'], persisted['status'])
                self.assertNotEqual(200, post('restore-home', item['version'])[0])
                status, restored = post('restore-home', cleared['version'])
                self.assertEqual(200, status)
                self.assertEqual(0, restored['home_hidden'])
                self.assertEqual(item['evidence'], restored['evidence'])
            finally:
                client.close()
                server.shutdown()
                server.server_close()
                thread.join(5)
