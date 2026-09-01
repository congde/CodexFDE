from __future__ import annotations

import unittest

from workbench.agent_planner import expand_plan_after_tool, paths_from_grep_result, plan_turn_tools


class AgentPlannerTests(unittest.TestCase):
    def test_turn_one_includes_workspace_discovery(self) -> None:
        task = {"execution_mode": "verify", "requirement_id": "REQ-001", "request": "inventory"}
        plan = plan_turn_tools(task, turn=1)
        tool_ids = [item["tool_id"] for item in plan]
        self.assertEqual(["spec.read", "workspace.list", "workspace.grep", "eval.blocking"], tool_ids)

    def test_codex_mode_inserts_executor_before_eval(self) -> None:
        task = {"execution_mode": "codex", "requirement_id": "", "request": "fix bug"}
        plan = plan_turn_tools(task, turn=2)
        tool_ids = [item["tool_id"] for item in plan]
        self.assertEqual(["spec.read", "codex.exec", "eval.blocking"], tool_ids)

    def test_expand_after_grep_inserts_reads_before_eval(self) -> None:
        remaining = [{"tool_id": "eval.blocking", "args": {}}]
        result = {
            "matches": [
                {"path": "flowerp/inventory.py", "line": 10, "text": "reserve"},
                {"path": "flowerp/inventory.py", "line": 20, "text": "again"},
                {"path": "tests/test_inventory.py", "line": 1, "text": "reserve"},
            ],
        }
        expanded = expand_plan_after_tool(remaining, tool_id="workspace.grep", result=result)
        self.assertEqual(
            ["workspace.read", "workspace.read", "eval.blocking"],
            [item["tool_id"] for item in expanded],
        )
        self.assertEqual("flowerp/inventory.py", expanded[0]["args"]["path"])
        self.assertTrue(expanded[0].get("adaptive"))

    def test_paths_from_grep_dedupes_and_skips_binaries(self) -> None:
        paths = paths_from_grep_result({
            "matches": [
                {"path": "a.py"},
                {"path": "a.py"},
                {"path": "logo.png"},
                {"path": "b.py"},
            ],
        })
        self.assertEqual(["a.py", "b.py"], paths)


if __name__ == "__main__":
    unittest.main()
