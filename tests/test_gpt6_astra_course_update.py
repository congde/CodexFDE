from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ModelNeutralCourseTests(unittest.TestCase):
    def test_student_materials_do_not_bind_to_a_short_lived_model_name(self) -> None:
        paths = [ROOT / "docs" / "课程大纲-Codex-FDE行动营-个人研发自动化工作台.md"]
        paths.extend((ROOT / "docs" / "courses").glob("L??-*.md"))
        paths.extend((ROOT / "docs" / "courses" / "L01").rglob("*.md"))
        paths.extend((ROOT / "docs" / "courses" / "tasks").glob("L??-*.md"))
        for path in paths:
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("GPT-6 Astra", body)
                self.assertNotIn("模型换代复验协议", body)

    def test_outline_requires_official_reverification_without_changing_contracts(self) -> None:
        body = (ROOT / "docs" / "课程大纲-Codex-FDE行动营-个人研发自动化工作台.md").read_text(encoding="utf-8")
        self.assertIn("易变能力的核验规则（核验：2026-09-04）", body)
        self.assertIn("只用官方文档复核", body)
        self.assertIn("go / hold / rollback", body)


if __name__ == "__main__":
    unittest.main()
