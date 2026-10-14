"""Standard-library HTTP shell with no task ledger or execution engine."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from .bootstrap import LedgerError
from .initiatives import InitiativeLedger

WEB_ROOT = Path(__file__).resolve().parent.parent / "workbench_web"


class WorkbenchApp:
    def __init__(self, runtime_dir: str | Path = ".runtime", *, port: int = 8001, eval_factory=None,
                 enable_code_execution=False, erp_url="http://127.0.0.1:8000", enable_advanced_runtime=False) -> None:
        self.runtime = Path(runtime_dir).resolve()
        self.port = port
        # These reserved arguments keep the course interface shape. The shell
        # never calls Codex, an ERP, an evaluator or an advanced runtime.

    def health(self) -> dict:
        database = self.runtime / "workbench.db"
        return {"surface": "workbench", "capabilities": ["shell"],
                "workbench_port": self.port, "runtime": str(self.runtime),
                "database": str(database), "database_exists": database.is_file(),
                "message": "工作台入口可访问；事项登记与 L01 账本可用，L04 受控执行通过 CLI。保存与自动绿灯仍待人审。",
                "initiative_registration": True, "acceptance": "pending_human_review"}


def make_handler(app: WorkbenchApp):
    class Handler(BaseHTTPRequestHandler):
        def json_response(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if urlsplit(self.path).path != '/api/initiatives':
                self.send_error(501, 'This route has no write operation')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 100000:
                    raise LedgerError('invalid_body', '请求体为空或过大')
                if not self.headers.get('Content-Type', '').startswith('application/json'):
                    raise LedgerError('invalid_body', '请求须使用 application/json')
                if self.headers.get('Origin') and urlsplit(self.headers['Origin']).netloc != self.headers.get('Host'):
                    raise LedgerError('invalid_origin', '仅接受同源事项登记')
                value = json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(value, dict) or set(value) - {'actor', 'requirement', 'scope', 'evidence'}:
                    raise LedgerError('invalid_body', '事项字段无效')
                item = InitiativeLedger(app.runtime).create(actor=value.get('actor'), requirement=value.get('requirement'),
                                                           scope=value.get('scope'), evidence=value.get('evidence', ''))
                self.json_response(item, 201)
            except (LedgerError, ValueError, UnicodeError) as error:
                self.json_response({'ok': False, 'errors': [getattr(error, 'code', 'invalid_body')],
                                    'message': str(error), 'acceptance': 'pending_human_review'}, 400)
            except (OSError, sqlite3.Error):
                self.json_response({'ok': False, 'errors': ['storage_failed']}, 500)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/initiatives' or path.startswith('/api/initiatives/'):
                try:
                    ledger = InitiativeLedger(app.runtime)
                    result = {'ok': True, 'items': ledger.list()} if path == '/api/initiatives' else ledger.get(path.removeprefix('/api/initiatives/'))
                    self.json_response(result)
                except LedgerError as error:
                    self.json_response({'ok': False, 'errors': [error.code], 'message': str(error)}, 404)
                return
            if path == "/api/health":
                body = json.dumps(app.health(), ensure_ascii=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            else:
                files = {"/": ("index.html", "text/html; charset=utf-8"),
                         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                         "/styles.css": ("styles.css", "text/css; charset=utf-8")}
                if path not in files:
                    self.send_error(404, "The L00 shell has no such route")
                    return
                filename, content_type = files[path]
                try:
                    body = (WEB_ROOT / filename).read_bytes()
                except OSError:
                    self.send_error(500, "Shell asset is missing")
                    return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8001, runtime_dir: str = ".runtime", *, enable_code_execution=False,
          erp_url="http://127.0.0.1:8000", enable_advanced_runtime=False) -> None:
    app = WorkbenchApp(runtime_dir, port=port, enable_code_execution=enable_code_execution,
                       erp_url=erp_url, enable_advanced_runtime=enable_advanced_runtime)
    with ThreadingHTTPServer((host, port), make_handler(app)) as server:
        app.port = server.server_port
        print(f"个人 AI 研发工作台 http://{host}:{server.server_port}/", flush=True)
        print(f"源码：{Path(__file__).resolve().parent.parent}", flush=True)
        print(f"数据库：{app.runtime / 'workbench.db'}（读取不创建，登记时保存）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
