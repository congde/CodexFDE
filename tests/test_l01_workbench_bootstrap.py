"""L01 reference acceptance: exercise the public CLI in an isolated data directory."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class WorkbenchBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "workbench"
        self.spec = self.root / "spec.md"
        self.spec.write_text("目标：任务证据可追溯\n验收：证据缺失时报失败\n决定人：学生", encoding="utf-8")
        self.problem = self.root / "problem.md"
        self.problem.write_text("测试夹具：找不到原始运行记录", encoding="utf-8")

    def cli(self, command, *args, expected=0):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "workbench.cli", command,
             "--runtime-dir", str(self.runtime), *map(str, args)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def setup_task(self):
        self.cli("workbench-init", "--owner", "student")
        self.cli("workbench-project-add", "--project-id", "PERSONAL-WORKBENCH", "--name", "个人工作台")
        self.cli("workbench-task-create", "--project-id", "PERSONAL-WORKBENCH",
                 "--task-id", "CASE-WB-L01-001", "--title", "构造证据账", "--spec-file", self.spec,
                 "--problem-file", self.problem)

    def evidence(self, phase, code, minute, command="python -m unittest tests.test_l01_workbench_bootstrap"):
        output = self.root / (phase + ".txt")
        output.write_text("教学测试夹具；不是学生运行证据：" + phase, encoding="utf-8")
        return self.cli("workbench-evidence-add", "--task-id", "CASE-WB-L01-001",
                        "--phase", phase, "--command-text", command, "--output-file", output,
                        "--returncode", code, "--observed-at", f"2026-09-05T10:{minute:02d}:00+08:00")

    def status(self, expected=0):
        return self.cli("workbench-status", "--require-project", "PERSONAL-WORKBENCH",
                        "--require-task", "CASE-WB-L01-001", "--require-red-green-evidence", expected=expected)

    def test_bootstrap_retains_red_diff_green_without_accepting_delivery(self):
        self.setup_task()
        self.evidence("red", 1, 0)
        self.evidence("diff", 0, 1, "git diff -- workbench tests")
        self.evidence("green", 0, 2)
        report = self.status()
        self.assertTrue(report["evidence_complete"])
        self.assertFalse(report["flowerp_connected"])
        self.assertEqual("pending_human_review", report["acceptance"])
        self.assertEqual(3, len(report["tasks"][0]["evidence"]))
        self.assertFalse((self.runtime / "flowerp.db").exists())
        # Copying the supplied output preserves it even if the source is changed later.
        (self.root / "red.txt").write_text("changed", encoding="utf-8")
        self.assertIn("red", self.status()["tasks"][0]["evidence"][0]["output"])

    def test_missing_evidence_is_failure_and_read_does_not_initialize(self):
        self.cli("workbench-status", expected=1)
        self.assertFalse(self.runtime.exists())
        self.setup_task()
        self.assertFalse(self.status(expected=1)["evidence_complete"])

    def test_different_test_command_cannot_form_same_case_green(self):
        self.setup_task()
        self.evidence("red", 1, 0)
        self.evidence("diff", 0, 1, "git diff")
        self.evidence("green", 0, 2, "python -m unittest some_other_test")
        self.assertIn("same_command_red_diff_green_missing", self.status(expected=1)["errors"])

    def test_duplicate_task_and_missing_project_do_not_overwrite(self):
        self.setup_task()
        self.cli("workbench-task-create", "--project-id", "MISSING", "--task-id", "OTHER",
                 "--title", "bad", "--spec-file", self.spec, expected=1)
        self.cli("workbench-task-create", "--project-id", "PERSONAL-WORKBENCH", "--task-id", "CASE-WB-L01-001",
                 "--title", "overwrite", "--spec-file", self.spec, expected=1)
        report = self.cli("workbench-status")
        self.assertEqual(["构造证据账"], [task["title"] for task in report["tasks"]])

    def test_wrong_observation_order_is_not_accepted(self):
        self.setup_task()
        self.evidence("green", 0, 0)
        self.evidence("diff", 0, 1, "git diff")
        self.evidence("red", 1, 2)
        self.status(expected=1)

    def test_later_failure_does_not_reuse_old_green(self):
        self.setup_task()
        self.evidence("red", 1, 0)
        self.evidence("diff", 0, 1, "git diff")
        self.evidence("green", 0, 2)
        self.status()
        self.evidence("red", 1, 3)
        self.status(expected=1)


if __name__ == "__main__":
    unittest.main()
