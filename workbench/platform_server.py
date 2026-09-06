from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from .platform_api import HarnessPlatformAPI
from .platform_bootstrap import bootstrap_default_project
from .http_bind import ServerBindError, create_http_server, report_bind_error
from .managed_flowerp import (
    FlowERPStartupError,
    ManagedFlowERP,
    launch_flowerp,
    report_flowerp_startup_error,
)


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = (ROOT / "harness_web").resolve()


def make_handler(api: HarnessPlatformAPI):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HarnessWorkbench/1.0"

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )

        def _send(self, status: int, payload: object) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._security_headers(); self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                # 浏览器超时或刷新后连接可能已关闭；响应已经失去消费者，无需留下线程异常。
                return

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000: raise ValueError("请求体过大")
            if length and "application/json" not in self.headers.get("Content-Type", "").lower():
                raise ValueError("写操作只接受 application/json")
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        def _api(self, body: object = None) -> bool:
            if not urlparse(self.path).path.startswith("/api/v1/"): return False
            headers = {key.lower(): value for key, value in self.headers.items()}
            response = api.dispatch(self.command, self.path, headers, body or {})
            self._send(response.status, response.body)
            return True

        def do_GET(self) -> None:  # noqa: N802
            if self._api(): return
            path = urlparse(self.path).path
            relative = "index.html" if path == "/" else path.lstrip("/")
            target = (WEB_ROOT / relative).resolve()
            if WEB_ROOT not in target.parents or not target.is_file():
                return self._send(404, {"error": "not_found"})
            data = target.read_bytes(); self.send_response(200)
            content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {
                "application/javascript", "application/json", "image/svg+xml",
            }:
                content_type += "; charset=utf-8"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self._security_headers(); self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self) -> None:  # noqa: N802
            try: body = self._body()
            except (ValueError, json.JSONDecodeError) as exc:
                return self._send(400, {"error": "invalid_json", "message": str(exc)})
            if not self._api(body): self._send(404, {"error": "not_found"})

        def log_message(self, fmt: str, *args: object) -> None:
            return

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8010,
          runtime_dir: str = ".harness-runtime", repository_root: str | Path | None = None,
          bootstrap: bool = False, with_flowerp: bool = False,
          flowerp_host: str = "127.0.0.1", flowerp_port: int = 8000,
          flowerp_runtime_dir: str = ".runtime") -> None:
    api = HarnessPlatformAPI(runtime_dir, repository_root)
    harness_url = f"http://{host}:{port}"
    startup: dict[str, object] = {
        "level": "INFO",
        "event": "harness_server_started",
        "service": "harness",
        "product": "Harness Workbench",
        "url": harness_url,
        "harness_url": harness_url,
    }
    if bootstrap:
        startup["bootstrap"] = bootstrap_default_project(api)
    server = create_http_server(
        host,
        port,
        make_handler(api),
        service_name="完整 Harness 平台",
        retry_command="python -X utf8 -m workbench.harness_cli serve-web --port 8090",
    )
    managed_flowerp: ManagedFlowERP | None = None
    try:
        if with_flowerp:
            managed_flowerp = launch_flowerp(
                api.repository_root,
                host=flowerp_host,
                port=flowerp_port,
                runtime_dir=flowerp_runtime_dir,
            )
            startup["flowerp"] = managed_flowerp.summary()
        print(json.dumps(startup, ensure_ascii=False), flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if managed_flowerp is not None:
            managed_flowerp.stop()
        server.server_close()
        api.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone personal delivery Harness platform")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--runtime-dir", default=".harness-runtime")
    parser.add_argument("--repository-root")
    parser.add_argument("--bootstrap", action="store_true",
                        help="启动时自动注册当前仓库为默认目标项目 PROJECT-FLOWERP")
    parser.add_argument("--boot", action="store_true",
                        help="组合启动：注册当前项目并联动启动 FlowERP")
    parser.add_argument("--with-flowerp", action="store_true", help="联动启动或复用 FlowERP")
    parser.add_argument("--flowerp-host", default="127.0.0.1")
    parser.add_argument("--flowerp-port", type=int, default=8000)
    parser.add_argument("--flowerp-runtime-dir", default=".runtime")
    args = parser.parse_args()
    try:
        serve(
            args.host,
            args.port,
            args.runtime_dir,
            args.repository_root,
            args.bootstrap or args.boot,
            args.with_flowerp or args.boot,
            args.flowerp_host,
            args.flowerp_port,
            args.flowerp_runtime_dir,
        )
    except ServerBindError as error:
        return report_bind_error(error)
    except FlowERPStartupError as error:
        return report_flowerp_startup_error(error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
