from __future__ import annotations

import json
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
        for body in (lesson, task):
            self.assertIn("L08 原子预占缺陷", body)
            self.assertNotIn("L05 缺陷", body)
            self.assertNotIn("L05 冻结补丁", body)

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

    def test_outline_has_three_line_contract_and_measurable_outcomes(self) -> None:
        outline = OUTLINE.read_text(encoding="utf-8")
        for marker in (
            "工作台驱动的 FlowERP 持续交付系统",
            "课程学习成果",
            "16 讲三线课程合同",
            "FlowERP 产品状态",
            "重复工程问题 → 工作台能力",
            "学生最低证据",
            "修订前后版本",
        ):
            self.assertIn(marker, outline)
        for lesson in range(1, 17):
            self.assertRegex(outline, rf"\| L{lesson:02d} \| CLO-")

    def test_systematic_refinement_blueprint_covers_all_lessons(self) -> None:
        blueprint_path = ROOT / "docs/courses/16讲系统化精修蓝图.md"
        self.assertTrue(blueprint_path.exists())
        blueprint = blueprint_path.read_text(encoding="utf-8")
        for marker in (
            "不是第二份课程大纲",
            "一个产品增量",
            "一个工作台增量",
            "一个主动失败实验",
            "一组个人学习证据",
            "一个明确交接矛盾",
            "四阶段学习弧",
            "16 讲端到端审计矩阵",
            "每讲发布门禁",
            "真实开课后",
        ):
            self.assertIn(marker, blueprint)
        for lesson in range(1, 17):
            self.assertEqual(1, blueprint.count(f"| L{lesson:02d} |"))

    def test_each_detailed_lesson_declares_one_baseline(self) -> None:
        paths = sorted((ROOT / "docs/courses").glob("L??-*.md"))
        self.assertEqual(16, len(paths))
        for path in paths:
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual(1, body.count("> **基础线唯一性**："))

    def test_l03_to_l05_keep_one_continuous_core_case(self) -> None:
        l03 = (ROOT / "docs/courses/L03-把模糊需求变成可验收Spec.md").read_text(encoding="utf-8")
        l04 = (ROOT / "docs/courses/L04-委托Codex执行一次最小变更.md").read_text(encoding="utf-8")
        l05 = (ROOT / "docs/courses/L05-先设计失败再编写Eval.md").read_text(encoding="utf-8")
        l05_task = (ROOT / "course/tasks/L05-失败优先Eval.md").read_text(encoding="utf-8")

        self.assertIn("REQ-COURSE-L03", l03)
        self.assertIn("库存可用量导出", l03)
        self.assertIn("实现最小结构解析器", l03)
        self.assertNotIn("| 本讲不做 | 不选技术方案，不写函数", l03)
        self.assertIn("inventory_export_is_stable", l04)
        for body in (l05, l05_task):
            self.assertIn("receiving_is_idempotent", body)
            self.assertIn("RECEIPT-001", body)
        self.assertNotIn("## 深读 `stock_never_negative`", l05)
        self.assertNotIn("移除 `reserve_order`", l05)

    def test_l14_and_l16_keep_product_truth_in_the_baseline(self) -> None:
        l14 = (ROOT / "docs/courses/L14-让交付状态在Web面板可见.md").read_text(encoding="utf-8")
        l14_task = (ROOT / "course/tasks/L14-Web状态面板.md").read_text(encoding="utf-8")
        for body in (l14, l14_task):
            self.assertIn("四方对账", body)
            self.assertIn("ERP 业务对象", body)

        l16 = (ROOT / "docs/courses/L16-冷启动发布与工程答辩.md").read_text(encoding="utf-8")
        self.assertIn("此前未实现", l16)
        self.assertNotIn("| 本讲不做 | 不引入新功能", l16)

    def test_application_plan_has_outcome_assessment_and_authenticity_rules(self) -> None:
        plan = (ROOT / "docs/courses/国家级一流本科课程建设方案.md").read_text(encoding="utf-8")
        for marker in (
            "课程目标—毕业要求—评价任务对齐",
            "入课诊断与分层支持",
            "通用分析量规",
            "CLO 达成度计算与判定",
            "两次提交与评价主体",
            "AI 使用与作品真实性",
            "评价工具校准",
            "证据成熟度与质量门",
            "两个教学周期的持续改进闭环",
        ):
            self.assertIn(marker, plan)

    def test_application_quality_gate_does_not_overclaim_readiness(self) -> None:
        gate_path = ROOT / "docs/courses/国家级一流本科课程申报级质量门.md"
        self.assertTrue(gate_path.exists())
        gate = gate_path.read_text(encoding="utf-8")
        for marker in (
            "不是申报资格证明",
            "G0 资格与类型",
            "G5 诚信、审查与安全",
            "红：待校方确认",
            "D 已设计",
            "P 已试教",
            "V 已验证",
            "A 申报就绪",
            "模拟学生",
            "不得写成申报成效",
        ):
            self.assertIn(marker, gate)

    def test_task_submission_contract_preserves_revision_and_authorship(self) -> None:
        readme = (ROOT / "course/tasks/README.md").read_text(encoding="utf-8")
        for marker in (
            "每讲统一提交包",
            "01-first-judgement.md",
            "03-failure/",
            "07-revision.md",
            "09-authorship.md",
            "通用四维量规",
            "首次版本与二次版本",
        ):
            self.assertIn(marker, readme)

    def test_every_lesson_opens_with_a_visual_learning_path(self) -> None:
        paths = sorted((ROOT / "docs/courses").glob("L??-*.md"))
        self.assertEqual(16, len(paths))
        image_pattern = re.compile(r"!\[[^\]]+\]\(([^)]+)\)")
        for path in paths:
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                navigation = body.index("## 图文学习导航")
                teacher_card = body.index("## 国家级一流本科课程教学设计卡")
                self.assertLess(navigation, teacher_card)
                self.assertLess(body.index("## 连续案例"), navigation)
                self.assertIn("### 先进入现场", body)
                self.assertIn("**先别看答案**", body)
                self.assertIn("```mermaid", body)
                self.assertGreaterEqual(body.count("https://"), 2)
                images = image_pattern.findall(body)
                self.assertGreaterEqual(len(images), 1)
                for image in images:
                    if image.startswith(("http://", "https://")):
                        continue
                    target = (path.parent / image).resolve()
                    self.assertTrue(target.is_file(), f"missing image: {image}")

    def test_every_lesson_is_a_continuous_decision_case_not_a_technical_outline(self) -> None:
        paths = sorted((ROOT / "docs/courses").glob("L??-*.md"))
        acts = {
            **{lesson: "第一幕" for lesson in range(1, 5)},
            **{lesson: "第二幕" for lesson in range(5, 9)},
            **{lesson: "第三幕" for lesson in range(9, 13)},
            **{lesson: "第四幕" for lesson in range(13, 17)},
        }
        for path in paths:
            lesson = int(path.name[1:3])
            body = path.read_text(encoding="utf-8")
            story_start = body.index("## 连续案例")
            story_end = body.index("## 图文学习导航")
            story = body[story_start:story_end]
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn(acts[lesson], story.splitlines()[0])
                self.assertIn("**时间**", story)
                self.assertIn("**地点**", story)
                self.assertEqual(1, story.count("**决策时刻**"))
                self.assertIn("林默", story)
                self.assertGreaterEqual(len([p for p in story.split("\n\n") if p.strip()]), 7)
                self.assertEqual(body.count("<details>"), body.count("</details>"))
                self.assertIn("本讲课程合同与建设状态（教师/助教）", body)
                self.assertIn("教师备课区：课程目标、评价与持续改进", body)
                self.assertIn("课程合同、边界与", body)

    def test_story_bible_has_one_coherent_flow_erp_campaign(self) -> None:
        bible_path = ROOT / "docs/courses/FlowERP连续案例叙事圣经.md"
        self.assertTrue(bible_path.is_file())
        bible = bible_path.read_text(encoding="utf-8")
        for marker in (
            "青禾优选",
            "学生扮演刚加入项目的资深研发负责人",
            "固定人物与利益冲突",
            "四幕十六讲故事弧",
            "逐讲事故与结尾钩子",
            "先决定，再分析",
            "人物为教学叙事角色",
            "HBS Case Method Teaching",
            "Cornell",
        ):
            self.assertIn(marker, bible)
        for lesson in range(1, 17):
            self.assertEqual(1, bible.count(f"| L{lesson:02d} |"))

    def test_first_five_lessons_are_publication_samples_not_outline_wrappers(self) -> None:
        for lesson in range(1, 6):
            paths = list((ROOT / "docs/courses").glob(f"L{lesson:02d}-*.md"))
            self.assertEqual(1, len(paths))
            path = paths[0]
            body = path.read_text(encoding="utf-8")
            student_start = body.index("## 学员正文｜")
            appendix_start = body.index(
                "<summary>附录：课程合同、教师活动、技术参考与证据模板</summary>"
            )
            student_text = body[student_start:appendix_start]
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertLess(body.index("## 连续案例"), student_start)
                self.assertLess(student_start, appendix_start)
                self.assertGreaterEqual(len(student_text), 1200)
                self.assertEqual(1, student_text.count("**第二次签字**"))
                self.assertIn("林默", student_text)
                self.assertIn(".\\.venv\\Scripts\\python.exe", student_text)
                self.assertTrue(
                    "错误路线" in student_text or "看似合理" in student_text,
                    "student narrative must expose a plausible wrong path",
                )
                self.assertEqual(body.count("<details>"), body.count("</details>"))
                self.assertLess(appendix_start, body.index("## 国家级一流本科课程教学设计卡"))

        standard_path = ROOT / "docs/courses/前5讲出版级样章审校表.md"
        self.assertTrue(standard_path.is_file())
        standard = standard_path.read_text(encoding="utf-8")
        for marker in (
            "正文与附录必须分开",
            "第一次签字",
            "错误路线",
            "概念命名",
            "第二次签字",
            "反清单规则",
            "前 5 讲的编辑焦点",
        ):
            self.assertIn(marker, standard)

    def test_all_lessons_have_student_textbook_main_text_before_collapsed_appendix(self) -> None:
        for lesson in range(1, 17):
            paths = list((ROOT / "docs/courses").glob(f"L{lesson:02d}-*.md"))
            self.assertEqual(1, len(paths))
            path = paths[0]
            body = path.read_text(encoding="utf-8")
            student_start = body.index("## 学员正文｜")
            appendix_summary = "<summary>附录：课程合同、教师活动、技术参考与证据模板</summary>"
            appendix_start = body.index(appendix_summary)
            student_text = body[student_start:appendix_start]
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertLess(body.index("## 连续案例"), student_start)
                self.assertLess(body.index("## 图文学习导航"), student_start)
                self.assertLess(student_start, appendix_start)
                self.assertGreaterEqual(len(student_text), 1200)
                self.assertEqual(1, student_text.count("**第二次签字**"))
                self.assertIn(".\\.venv\\Scripts\\python.exe", student_text)
                self.assertTrue(
                    "错误路线" in student_text or "看似合理" in student_text,
                    "student main text must expose a plausible wrong path",
                )
                self.assertEqual(body.count("<details>"), body.count("</details>"))

    def test_l06_to_l16_explain_one_decisive_code_or_contract_fragment(self) -> None:
        for lesson in range(6, 17):
            path = next((ROOT / "docs/courses").glob(f"L{lesson:02d}-*.md"))
            body = path.read_text(encoding="utf-8")
            student_start = body.index("## 学员正文｜")
            appendix_start = body.index(
                "<summary>附录：课程合同、教师活动、技术参考与证据模板</summary>"
            )
            student_text = body[student_start:appendix_start]
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual(1, student_text.count("### 把关键"))
                explanation = student_text.split("### 把关键", 1)[1]
                self.assertGreaterEqual(explanation.count("- "), 4)
                self.assertIn("```", explanation)

    def test_textbook_standard_and_primary_source_map_cover_all_lessons(self) -> None:
        standard_path = ROOT / "docs/courses/教材化与图文升级规范.md"
        reading_path = ROOT / "docs/courses/外部优秀资料与逐讲阅读地图.md"
        self.assertTrue(standard_path.is_file())
        self.assertTrue(reading_path.is_file())
        standard = standard_path.read_text(encoding="utf-8")
        reading = reading_path.read_text(encoding="utf-8")
        for marker in (
            "七幕教材结构",
            "真实项目图",
            "主动制造、能够清理、能够复现的失败",
            "预测—观察—解释",
            "外部资料用链接",
            "2026-09-01",
        ):
            self.assertIn(marker, standard)
        for lesson in range(1, 17):
            self.assertEqual(1, reading.count(f"| L{lesson:02d} |"))
        for source in (
            "GitHub Skills",
            "CMU Active Learning",
            "CDIO Standards",
            "Google SRE",
            "OpenAI",
            "RFC 9110",
            "W3C WCAG 2.2",
        ):
            self.assertIn(source, reading)

    def test_three_carrier_course_contract_covers_all_lessons(self) -> None:
        plan_path = ROOT / "docs/courses/三载体课程实施方案.md"
        script_path = ROOT / "docs/courses/PPT逐讲决策脚本.md"
        workspace_path = ROOT / "course/FlowERP-AI研发工作台.code-workspace"
        self.assertTrue(plan_path.is_file())
        self.assertTrue(script_path.is_file())
        self.assertTrue(workspace_path.is_file())

        plan = plan_path.read_text(encoding="utf-8")
        script = script_path.read_text(encoding="utf-8")
        outline = OUTLINE.read_text(encoding="utf-8")
        workspace = json.loads(workspace_path.read_text(encoding="utf-8"))
        self.assertIn("16 个独立 PPTX", script)
        self.assertIn("每讲页数不设统一上限", script)
        self.assertNotIn("16 讲 × 4 页，共 64 页", script)
        self.assertIn("16 个独立 PPTX", plan)
        self.assertIn("16 个逐讲独立课件", outline)
        self.assertIn("不设统一页数上限", outline)
        for marker in (
            "PPT 引导决策",
            "Markdown 承载教材",
            "VS Code 完成行动",
            "第一次签字",
            "第二次签字",
            "PPT → Markdown",
            "Markdown → VS Code",
            "VS Code → Markdown",
        ):
            self.assertIn(marker, plan)

        for lesson in range(1, 17):
            lesson_id = f"L{lesson:02d}"
            self.assertEqual(1, plan.count(f"| {lesson_id} |"))
            self.assertEqual(1, script.count(f"## {lesson_id}｜"))
            section_start = script.index(f"## {lesson_id}｜")
            next_start = script.find("\n## L", section_start + 1)
            section = script[section_start:] if next_start < 0 else script[section_start:next_start]
            for slide in range(1, 5):
                self.assertEqual(1, len(re.findall(rf"^{slide}\. \*\*", section, re.MULTILINE)))

        visual_assets = re.findall(r"\| L\d{2} \| `((?:assets/)[^`]+)` \|", script)
        self.assertEqual(16, len(visual_assets))
        self.assertEqual(16, len(set(visual_assets)))
        for asset in visual_assets:
            self.assertTrue((ROOT / "docs/courses" / asset).is_file(), asset)
        self.assertIn("[Sources]", script)

        lesson_docs = sorted((ROOT / "docs/courses").glob("L??-*.md"))
        task_cards = sorted((ROOT / "course/tasks").glob("L??-*.md"))
        self.assertEqual(16, len(lesson_docs))
        self.assertEqual(16, len(task_cards))
        for path in lesson_docs:
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual(1, body.count("## 三载体课堂交接"))
                self.assertIn("PPT 第一次签字 → Markdown 查证 → VS Code 行动 → Markdown 第二次签字", body)
        for path in task_cards:
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual(1, body.count("## VS Code 行动入口"))
                self.assertIn("课程/02 查看本讲合同", body)

        labels = {task["label"] for task in workspace["tasks"]["tasks"]}
        for label in (
            "课程/01 状态检查",
            "课程/02 查看本讲合同",
            "课程/03 生成本讲 Spec",
            "课程/04 运行本讲 Eval",
            "课程/06 阻断级 Eval",
            "课程/07 全量测试",
            "课程/08 启动 FlowERP（8000）",
        ):
            self.assertIn(label, labels)
        self.assertEqual(
            [str(number) for number in range(1, 17)],
            workspace["tasks"]["inputs"][0]["options"],
        )

        outline = OUTLINE.read_text(encoding="utf-8")
        instructor = (ROOT / "docs/courses/讲师手册.md").read_text(encoding="utf-8")
        task_readme = (ROOT / "course/tasks/README.md").read_text(encoding="utf-8")
        for body in (outline, instructor, task_readme):
            self.assertIn("PPT 引导决策", body)
            self.assertIn("Markdown 承载教材", body)
            self.assertIn("VS Code 完成行动", body)

    def test_root_agents_enforces_course_mainline_and_application_integrity(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for marker in (
            "个人研发自动化工作台",
            "FlowERP 是客户项目、实验场和验收场",
            "课程治理与申报级约束",
            "学生中心、产出导向、持续改进",
            "每讲必须回答四问",
            "不得用模拟学生数据",
            "待校方确认",
        ):
            self.assertIn(marker, agents)


if __name__ == "__main__":
    unittest.main()
