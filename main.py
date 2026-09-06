"""Start or reopen the workbench and FlowERP using saved local data paths."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    # The desktop launcher and its children must use the repository environment.
    root = Path(__file__).resolve().parent
    python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if sys.prefix == sys.base_prefix:
        if not python.is_file():
            print('请先运行首次使用，准备 .venv 环境。', file=sys.stderr)
            return 1
        import subprocess
        return subprocess.call([str(python), '-X', 'utf8', str(root / 'main.py'),
                                *(sys.argv[1:] if argv is None else argv)], cwd=root)
    from workbench.desktop import main as launch
    return launch(sys.argv[1:] if argv is None else argv)


if __name__ == '__main__':
    raise SystemExit(main())
