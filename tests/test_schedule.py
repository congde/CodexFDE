from __future__ import annotations

import unittest

from agent.schedule import Subtask, assert_parallel_safe, conflict_pairs


class ScheduleTests(unittest.TestCase):
    def test_directory_write_conflicts_with_file_read(self) -> None:
        with self.assertRaises(ValueError):
            assert_parallel_safe((Subtask("implementation", ("flowerp/",)),
                                  Subtask("review", (), ("flowerp/service.py",))))

    def test_parent_paths_and_windows_separators_cannot_hide_write_conflicts(self) -> None:
        pairs = conflict_pairs((Subtask("a", ("./flowerp/",)), Subtask("b", ("flowerp\\service.py",))))
        self.assertEqual([("a", "b", ["flowerp/service.py"])], pairs)

    def test_distinct_files_and_shared_read_only_paths_are_safe(self) -> None:
        self.assertTrue(assert_parallel_safe((Subtask("a", ("workbench/one.py",), ("AGENTS.md",)),
                                              Subtask("b", ("workbench/two.py",), ("AGENTS.md",))))["parallel"])

    def test_invalid_scope_and_duplicate_task_identity_are_rejected(self) -> None:
        for scope in ("../outside.py", "/absolute", "C:\\repo", "", "flowerp/*.py"):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                assert_parallel_safe((Subtask("a", (scope,)),))
        with self.assertRaises(ValueError):
            assert_parallel_safe((Subtask("same", ()), Subtask("same", ())))

    def test_overlapping_writes_are_conflicts(self) -> None:
        pairs = conflict_pairs((
            Subtask("a", ("flowerp/service.py",)),
            Subtask("b", ("flowerp/service.py", "tests/test_flowerp.py")),
        ))
        self.assertEqual(pairs[0][2], ["flowerp/service.py"])

    def test_read_only_tasks_may_run_in_parallel(self) -> None:
        result = assert_parallel_safe((
            Subtask("review", (), ("eval/cases.py",)),
            Subtask("risk", (), ("AGENTS.md",)),
        ))
        self.assertTrue(result["parallel"])


if __name__ == "__main__":
    unittest.main()
