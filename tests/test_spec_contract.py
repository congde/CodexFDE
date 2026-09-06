from __future__ import annotations

import unittest

from workbench.spec import REQUIRED_SECTIONS, parse_spec


def valid_spec() -> str:
    return "# 库存导出\n\n" + "\n\n".join(f"## {name}\n{name}的明确内容" for name in REQUIRED_SECTIONS)


class SpecContractTests(unittest.TestCase):
    def test_complete_contract_is_read_without_changing_it(self):
        source = valid_spec()
        parsed = parse_spec(source)
        self.assertEqual("目标的明确内容", parsed.goal)
        self.assertEqual("来源的明确内容", parsed.source)
        self.assertEqual(source, valid_spec())

    def test_example_markdown_inside_fences_is_not_a_contract_section(self):
        for fence in ("```", "~~~~"):
            with self.subTest(fence=fence):
                example = f"\n{fence}markdown\n## 目标\n这只是导出示例中的文字\n## 未知标题\n{fence}\n"
                source = valid_spec().replace("验收用例的明确内容", "验收用例的明确内容" + example)
                parsed = parse_spec(source)
                self.assertIn("## 未知标题", parsed.acceptance)
                self.assertEqual("目标的明确内容", parsed.goal)

    def test_unclosed_fence_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "围栏未闭合"):
            parse_spec(valid_spec() + "\n```text\n没有结束标记")

    def test_crlf_and_longer_closing_fence_work(self):
        source = valid_spec().replace("来源的明确内容", "来源的明确内容\n```text\n## 假章节\n````")
        parsed = parse_spec(source.replace("\n", "\r\n"))
        self.assertIn("假章节", parsed.source)

    def test_heading_without_name_cannot_consume_next_line(self):
        with self.assertRaises(ValueError):
            parse_spec(valid_spec().replace("## 目标\n目标的明确内容", "## \n目标\n目标的明确内容"))

    def test_missing_empty_duplicate_misordered_and_unknown_are_rejected(self):
        valid = valid_spec()
        invalid = (
            valid.replace("## 来源\n来源的明确内容\n\n", ""),
            valid.replace("目标的明确内容", "   "),
            valid + "\n## 目标\n重复", valid.replace("## 来源", "## 未知"),
            valid.replace("## 来源", "## 临时").replace("## 目标", "## 来源").replace("## 临时", "## 目标"),
        )
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_spec(source)


if __name__ == "__main__":
    unittest.main()
