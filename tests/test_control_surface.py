import unittest

from workbench.control_surface import build_control_surface


class ControlSurfaceTests(unittest.TestCase):
    def task(self):
        return {
            "status": "executing",
            "automation_mode": "automatic",
            "execution_mode": "codex",
            "execution_timeout_seconds": 900,
            "write_scope": ["flowerp/"],
            "request": "Check stock",
            "spec_path": "spec.md",
            "spec": {"title": "Stock"},
            "events": [
                {"detail": "自动流水线开始推进", "evidence": {"attempt": 1, "max_attempts": 3}},
                {"detail": "开始受控执行", "evidence": {
                    "allowed_actions": ["write_code_in_task_scope"],
                    "forbidden_actions": ["skip_eval"],
                }},
            ],
        }

    def test_valid_controls_are_ready(self):
        self.assertTrue(build_control_surface(self.task())["ready"])

    def test_invalid_loop_bounds_are_not_observed(self):
        for attempt, maximum in [(1, None), (0, 3), (4, 3), (True, 3), (1, "3")]:
            with self.subTest(attempt=attempt, maximum=maximum):
                task = self.task()
                task["events"][0]["evidence"] = {"attempt": attempt, "max_attempts": maximum}
                surface = build_control_surface(task)
                self.assertFalse(surface["ready"])
                self.assertIn("loop_bounds_invalid", surface["issues"])
                self.assertFalse(surface["components"][0]["armed"])

    def test_invalid_timeout_is_reported_without_crashing(self):
        for timeout in [None, "invalid", True, 0, -1, 1.5]:
            with self.subTest(timeout=timeout):
                task = self.task()
                task["execution_timeout_seconds"] = timeout
                self.assertIn("execution_timeout_missing", build_control_surface(task)["issues"])

    def test_missing_context_and_write_permission_are_not_armed(self):
        task = self.task()
        task["request"] = ""
        task["events"][1]["evidence"]["allowed_actions"] = ["read"]
        surface = build_control_surface(task)
        components = {item["id"]: item for item in surface["components"]}
        self.assertFalse(components["context"]["armed"])
        self.assertFalse(components["tools"]["armed"])
