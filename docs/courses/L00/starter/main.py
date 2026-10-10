"""Use the existing CLI; the mature project's desktop launcher arrives later."""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    from workbench.cli import main as cli_main

    arguments = list(sys.argv[1:] if argv is None else argv)
    return cli_main(arguments or ["serve-workbench"])


if __name__ == "__main__":
    raise SystemExit(main())
