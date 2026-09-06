import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from workbench.workbench_server import WorkbenchApp, make_handler


class CourseInitiativesHTTPTests(unittest.TestCase):
    def test_saved_decision_survives_reopen_and_rejects_stale_or_agent_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = WorkbenchApp(directory)
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(method, path, body=None):
                connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    connection.request(method, path, json.dumps(body) if body else None,
                                       {'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()

            try:
                data = {'title': '测试事项', 'raw_signal': '按钮看不清', 'source': '测试夹具',
                        'goal': '提高按钮可读性', 'project_id': 'FlowERP', 'acceptance': ['投影可读']}
                code, item = request('POST', '/api/v1/initiatives', {'actor': 'fixture-human', 'data': data})
                self.assertEqual(code, 201)
                base = '/api/v1/initiatives/' + item['id']
                decide = {'actor': 'fixture-human', 'version': 1, 'decision': 'build', 'rationale': '测试决定'}
                self.assertEqual(request('POST', base + '/decide', decide)[0], 400)
                revised = {'actor': 'fixture-human', 'version': 1,
                           'data': {'problem_statement': '字号太小', 'evidence': ['课堂可读性测试夹具']}}
                code, item = request('POST', base + '/revise', revised)
                self.assertEqual((code, item['version']), (200, 2))
                self.assertEqual(request('POST', base + '/revise', revised)[0], 400)
                self.assertEqual(request('POST', base + '/decide', {**decide, 'version': 2, 'actor': 'agent:coder'})[0], 400)
                code, item = request('POST', base + '/decide', {**decide, 'version': 2})
                self.assertEqual((code, item['decision']), (200, 'build'))
                self.assertEqual(request('POST', base + '/decide', {**decide, 'version': 3})[0], 400)
                reopened = WorkbenchApp(directory).initiatives.get(item['id'])
                self.assertEqual(reopened['decision_by'], 'fixture-human')
                self.assertEqual(reopened['raw_signal'], data['raw_signal'])
                self.assertIsNone(reopened['linked_task_id'])
                self.assertEqual(app.tasks.list(), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)
