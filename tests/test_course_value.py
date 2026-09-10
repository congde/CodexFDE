from __future__ import annotations
from workbench.external_project import flowerp_root

import unittest
from pathlib import Path

from workbench.lesson_constructibility import diagnose
from workbench.product_lineage import LINEAGE
from workbench.course_mainline import validate_mainline


ROOT = Path(__file__).resolve().parents[1]


class CourseValueTests(unittest.TestCase):
    def test_value_standard_is_not_end_state_green(self) -> None:
        text = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        self.assertIn("不能替代你亲手留下的红灯", text)
        self.assertIn("8001", text)
        self.assertIn("隔离工作区", text)

    def test_identity_copy_separates_workbench_from_customer_project(self) -> None:
        workbench = (ROOT / "workbench_web" / "index.html").read_text(encoding="utf-8")
        erp = (flowerp_root() / "web/index.html").read_text(encoding="utf-8")
        self.assertIn("个人研发工作台", workbench)
        self.assertIn("客户项目", workbench)
        self.assertIn("客户项目", erp)
        self.assertIn("8001", erp)

    def test_every_lesson_has_an_honest_failure(self) -> None:
        report = diagnose()
        self.assertEqual(16, len(report["lessons"]))
        self.assertTrue(all(item["honest_failure"] and item["student_must_construct"] for item in report["lessons"]))

    def test_unproven_ecommerce_is_challenge_not_student_credit(self) -> None:
        challenges = [item for item in LINEAGE if not item["via_workbench"]]
        self.assertTrue(challenges)
        self.assertTrue(any(item["eval"].startswith("ecommerce_") or "FIN" in item["requirement"] for item in challenges))

    def test_course_status_does_not_equate_tags_with_learning(self) -> None:
        status = validate_mainline(ROOT)
        self.assertEqual("progression_gate", status["baseline_semantics"])
        self.assertIn("Diff", status["constructibility"])


if __name__ == "__main__":
    unittest.main()
