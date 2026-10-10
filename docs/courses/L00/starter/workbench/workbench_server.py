"""Standard-library HTTP shell with no task ledger or execution engine."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

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
                "message": "页面壳可访问；账本、受控执行和业务验收将在后续课程建设。"}


def make_handler(app: WorkbenchApp):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
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
        print(f"个人 AI 研发工作台 L00 壳 http://{host}:{server.server_port}/", flush=True)
        print(f"源码：{Path(__file__).resolve().parent.parent}", flush=True)
        print(f"计划数据库：{app.runtime / 'workbench.db'}（本壳不创建数据库）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
