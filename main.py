"""一键启动 FlowERP：同一进程提供 API 后端与 Web 前端。"""

from __future__ import annotations

import argparse
import sys

from workbench.http_bind import ServerBindError, report_bind_error
from workbench.server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 FlowERP 前后端（API + Web）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument("--runtime-dir", default=".runtime", help="运行数据目录，默认 .runtime")
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}/"
    print(f"FlowERP 启动中：后端 API=/api/v1  前端 Web={url}", flush=True)
    print("按 Ctrl+C 停止。", flush=True)
    try:
        serve(args.host, args.port, args.runtime_dir)
    except ServerBindError as error:
        return report_bind_error(error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
