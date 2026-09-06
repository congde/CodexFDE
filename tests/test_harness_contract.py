from __future__ import annotations

import unittest
from unittest.mock import patch

from eval.harness import run_suite


class HarnessContractTests(unittest.TestCase):
    def test_wrong_suite_cannot_turn_requested_case_into_zero_tests_green(self):
        with patch("eval.harness.EVALS", [("observation", "observing", lambda: "ok")]):
            with self.assertRaisesRegex(ValueError, "suite"):
                run_suite("blocking", False, ["observation"])

    def test_empty_registry_cannot_look_like_a_pass(self):
        with patch("eval.harness.EVALS", []), self.assertRaisesRegex(ValueError, "用例"):
            run_suite("blocking", False)

    def test_error_keeps_type_and_remaining_cases_still_run(self):
        calls = []
        def crash():
            raise RuntimeError("fixture failure")
        def last():
            calls.append("last")
            return "checked"
        with patch("eval.harness.EVALS", [("broken", "blocking", crash), ("last", "blocking", last)]):
            result = run_suite("blocking", False)
        self.assertEqual(["last"], calls)
        self.assertEqual("RuntimeError", result["results"][0]["error"]["type"])
        self.assertEqual("block", result["summary"]["decision"])

    def test_observing_failure_does_not_block(self):
        def failure():
            raise AssertionError("fixture observation")
        with patch("eval.harness.EVALS", [("observe", "observing", failure)]):
            result = run_suite("observing", False)
        self.assertEqual("pass", result["summary"]["decision"])
        self.assertEqual(1, result["summary"]["observing_failed"])
        self.assertFalse(result["results"][0]["passed"])


if __name__ == "__main__":
    unittest.main()
