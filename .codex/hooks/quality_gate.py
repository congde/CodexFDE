from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    event = json.load(sys.stdin)
    if event.get("stop_hook_active"):
        print(json.dumps({"continue": True, "systemMessage": "质量 Hook 已运行过一次，避免重复续跑。"}, ensure_ascii=False))
        return 0
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "eval.harness", "--suite", "blocking"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode == 0:
        print(json.dumps({"continue": True, "systemMessage": "FlowERP 阻断级 Eval 已通过。"}, ensure_ascii=False))
    else:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-10:])
        print(json.dumps({"decision": "block", "reason": "阻断级 Eval 未通过。修复后重新验证：\n" + tail}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
