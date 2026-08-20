from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTLINE = ROOT / "docs" / "课程大纲-Codex-FDE行动营-个人研发自动化工作台.md"
CONTRACT_LABELS = {
    "核心内容",
    "演示结果",
    "课内增量",
    "通过标准",
    "挑战任务",
    "验收命令",
    "最终验收命令",
}


def outline_contracts() -> dict[int, tuple[str, list[str]]]:
    text = OUTLINE.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^#### 第 (\d+) 讲｜(.+)$", text, re.MULTILINE))
    contracts: dict[int, tuple[str, list[str]]] = {}
    for index, match in enumerate(matches):
        lesson = int(match.group(1))
        if not 1 <= lesson <= 16:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end() : end]
        lines = []
        for line in section.splitlines():
            field = re.match(r"^- \*\*(.+?)\*\*：", line)
            if field and field.group(1) in CONTRACT_LABELS:
                lines.append(line)
        contracts[lesson] = (match.group(2), lines)
    return contracts


class CourseOutlineAlignmentTests(unittest.TestCase):
    def test_detailed_lessons_and_task_cards_match_outline_contract(self) -> None:
        contracts = outline_contracts()
        self.assertEqual(set(range(1, 17)), set(contracts))

        for relative_dir in (Path("docs/courses"), Path("course/tasks")):
            files = sorted((ROOT / relative_dir).glob("L??-*.md"))
            self.assertEqual(16, len(files), relative_dir.as_posix())
            for path in files:
                lesson = int(path.name[1:3])
                title, contract_lines = contracts[lesson]
                body = path.read_text(encoding="utf-8")
                with self.subTest(path=path.relative_to(ROOT)):
                    self.assertEqual(f"# L{lesson:02d}｜{title}", body.splitlines()[0])
                    self.assertTrue(contract_lines, "大纲中缺少本讲合同字段")
                    for line in contract_lines:
                        self.assertIn(line, body)

    def test_l01_keeps_business_case_as_demo_not_core_task(self) -> None:
        lesson = (ROOT / "docs/courses/L01-先看终局-一次可验证的AI交付.md").read_text(encoding="utf-8")
        task = (ROOT / "course/tasks/L01-终局与冷启动.md").read_text(encoding="utf-8")
        for body in (lesson, task):
            self.assertIn("结构、约束、验证命令、风险", body)
            self.assertIn("python -X utf8 -m workbench.cli doctor", body)
            self.assertNotIn("> **核心任务**：面对“客户要 20 台", body)

    def test_l08_requires_real_ci_engineering_work(self) -> None:
        lesson = (ROOT / "docs/courses/L08-把同一套Eval接入CI.md").read_text(encoding="utf-8")
        task = (ROOT / "course/tasks/L08-CI远程复验.md").read_text(encoding="utf-8")
        required = (
            "CI_GATE_SPEC.md",
            "workbench/ci_evidence.py",
            "Run A",
            "Run B",
            "Run C",
            "A→B",
            "B→C",
            "Evidence Envelope",
            "同伴法证",
            "只交 Run C",
        )
        for body in (lesson, task):
            for marker in required:
                self.assertIn(marker, body)
        self.assertIn("没有真实远端 Runner", lesson)
        self.assertIn("不能用讲师代跑", task)
        self.assertIn("FlowERP 提供事故，工作台吸收能力", lesson)
        self.assertIn("FlowERP 只提供输入", task)
        for body in (lesson, task):
            self.assertIn("至少 80%", body)
        self.assertIn("本讲不扩建 ERP", lesson)
        self.assertIn("不在 L08 新增", task)

    def test_every_lesson_has_paid_action_density_and_flowerp_boundary(self) -> None:
        for path in sorted((ROOT / "docs/courses").glob("L??-*.md")):
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                for marker in ("## 本讲行动工单", "工作台", "FlowERP", "迁移"):
                    self.assertIn(marker, body)

        for path in sorted((ROOT / "course/tasks").glob("L??-*.md")):
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                for marker in ("## 付费行动课交付合同", "## 主次边界", "工作台", "FlowERP", "迁移"):
                    self.assertIn(marker, body)

        audit = (ROOT / "docs/courses/16讲行动密度审计与优化记录.md").read_text(encoding="utf-8")
        for marker in ("学生亲手构造", "主动制造失败", "因果边界清楚", "陌生人可复验", "能力能够迁移", "FlowERP 不抢主线"):
            self.assertIn(marker, audit)
        for lesson in range(1, 17):
            self.assertIn(f"| L{lesson:02d} |", audit)

    def test_lessons_include_national_first_class_course_design(self) -> None:
        required = (
            "## 国家级一流本科课程教学设计卡",
            "对应课程目标",
            "工作台主线增量",
            "FlowERP 的作用",
            "高阶性",
            "创新性",
            "挑战度",
            "学生中心活动",
            "课程思政融入",
            "形成性评价",
            "持续改进数据",
        )
        for path in sorted((ROOT / "docs/courses").glob("L??-*.md")):
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                for marker in required:
                    self.assertIn(marker, body)

    def test_task_cards_keep_workbench_as_main_project(self) -> None:
        required = (
            "## 项目主线与评价证据",
            "FlowERP 现场问题",
            "工作台增量",
            "学生学习证据",
            "形成性评价",
        )
        for path in sorted((ROOT / "course/tasks").glob("L??-*.md")):
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                for marker in required:
                    self.assertIn(marker, body)

    def test_each_lesson_has_outline_and_frontier_calibration(self) -> None:
        matrix = (ROOT / "docs/courses/16讲主线与前沿校准矩阵.md").read_text(encoding="utf-8")
        for marker in ("派生索引", "稳定基础 B", "当前增强 E", "观察项 W"):
            self.assertIn(marker, matrix)
        for lesson in range(1, 17):
            self.assertIn(f"| **L{lesson:02d} ", matrix)

        lesson_markers = (
            "## 大纲锚点与前沿校准（2026-08-19）",
            "大纲锚点",
            "前沿采用",
            "不越界",
            "链路交接",
        )
        for path in sorted((ROOT / "docs/courses").glob("L??-*.md")):
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                for marker in lesson_markers:
                    self.assertIn(marker, body)

        task_markers = (
            "## 大纲与前沿硬校准",
            "必须完成",
            "当前做法",
            "退回条件",
        )
        for path in sorted((ROOT / "course/tasks").glob("L??-*.md")):
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                for marker in task_markers:
                    self.assertIn(marker, body)

    def test_course_construction_plan_states_real_application_gates(self) -> None:
        plan = (ROOT / "docs/courses/国家级一流本科课程建设方案.md").read_text(encoding="utf-8")
        for marker in (
            "唯一项目主线",
            "个人研发自动化工作台",
            "纳入普通本科人才培养方案并设置学分",
            "至少两个教学周期",
            "不得用模拟数据补齐申报材料",
        ):
            self.assertIn(marker, plan)

    def test_root_agents_enforces_course_mainline_and_application_integrity(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for marker in (
            "个人研发自动化工作台",
            "FlowERP 是客户项目、实验场和验收场",
            "课程治理与申报级约束",
            "学生中心、产出导向、持续改进",
            "每讲必须回答三问",
            "不得用模拟学生数据",
            "待校方确认",
        ):
            self.assertIn(marker, agents)


if __name__ == "__main__":
    unittest.main()
