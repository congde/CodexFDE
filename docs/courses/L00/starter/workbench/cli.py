"""Keep the same command entry point as the course project."""
from __future__ import annotations

import argparse
import sys

from .runtime_paths import service_runtime


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="个人 AI 研发工作台：L00 壳")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("serve-workbench", help="启动工作台页面壳（默认 :8001）")
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=8001)
    command.add_argument("--runtime-dir")
    args = parser.parse_args(argv)
    from .workbench_server import serve
    try:
        serve(args.host, args.port, str(service_runtime("workbench", args.runtime_dir)))
    except (OSError, ValueError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
