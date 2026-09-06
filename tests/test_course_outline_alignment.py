from __future__ import annotations

import re
import unittest
import tempfile
from pathlib import Path
from urllib.parse import unquote

from workbench.course_mainline import LESSONS


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
COURSES = DOCS / "courses"
SCHEDULE = DOCS / "课表｜Codex AI 工程交付行动营.md"
OUTLINE = DOCS / "课程大纲-Codex-FDE行动营-个人研发自动化工作台.md"


def schedule_titles() -> dict[int, str]:
    body = SCHEDULE.read_text(encoding="utf-8")
    return {
        int(number): title
        for number, title in re.findall(r"^\|(\d{2})\|([^|]+)\|", body, re.MULTILINE)
    }


def lesson_files(directory: Path) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for number in range(1, 17):
        canonical = directory / f"L{number:02d}" / "阅读讲义.md"
        if directory.name == "tasks":
            canonical = directory.parent / f"L{number:02d}" / "行动卡.md"
        if canonical.is_file():
            result[number] = canonical
    for path in sorted(directory.glob("L??-*.md")):
        if path.name.endswith("-教师备课说明.md"):
            continue
        number = int(path.name[1:3])
        if 1 <= number <= 16:
            if number in result:
                raise AssertionError(f"L{number:02d} 有重复课程文件：{result[number].name}、{path.name}")
            result[number] = path
    return result


def outline_contracts() -> dict[int, tuple[str, tuple[str, ...]]]:
    body = OUTLINE.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^#### 第 (\d+) 讲｜(.+)$", body, re.MULTILINE))
    contracts: dict[int, tuple[str, tuple[str, ...]]] = {}
    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section = body[match.end():end]
        lines = tuple(
            line.strip()
            for line in section.splitlines()
            if re.match(r"^- \*\*(核心内容|演示结果|课内增量|通过标准)\*\*：", line)
        )
        contracts[number] = (match.group(2).strip(), lines)
    return contracts


class CourseOutlineAlignmentTests(unittest.TestCase):
    def test_discovery_excludes_teacher_notes_and_rejects_duplicate_student_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            handout = root / "L01-学生讲义.md"
            handout.touch()
            (root / "L01-教师备课说明.md").touch()
            self.assertEqual({1: handout}, lesson_files(root))
            (root / "L01-另一份讲义.md").touch()
            with self.assertRaisesRegex(AssertionError, "重复课程文件"):
                lesson_files(root)

    def test_student_entry_and_new_directory_contract_exist(self) -> None:
        for path in (
            DOCS / "README.md",
            COURSES / "课程蓝图.md",
            COURSES / "FlowERP-AI研发工作台.code-workspace",
            COURSES / "tasks" / "README.md",
            COURSES / "labs",
            DOCS / "reference" / "个人AI研发工作台.md",
            DOCS / "reference" / "FlowERP领域模型与业务不变量.md",
            DOCS / "reference" / "FlowERP接口与运行边界.md",
        ):
            self.assertTrue(path.exists(), path)
        self.assertFalse((ROOT / "course").exists())
        self.assertEqual(
            {f"L{number:02d}" for number in range(0, 17) if number != 1},
            {path.name for path in (COURSES / "labs").glob("L??") if path.is_dir()},
        )

    def test_repository_has_no_legacy_course_material_paths(self) -> None:
        stale: list[str] = []
        suffixes = {".md", ".py", ".json", ".yml", ".yaml", ".js", ".html", ".toml", ".code-workspace"}
        legacy_paths = (
            "course/tasks/",
            "course/labs/",
            "course/FlowERP-AI研发工作台.code-workspace",
            "course/repair-output.schema.json",
            "course/baselines/PROGRESSION.json",
        )
        source_roots = [ROOT / name for name in ("docs", "scripts", "tests", "eval", "workbench")]
        paths = [ROOT / name for name in ("README.md", "AGENTS.md", ".gitignore")]
        for source_root in source_roots:
            paths.extend(path for path in source_root.rglob("*") if path.is_file())
        for path in paths:
            if path.suffix not in suffixes and path.name != ".gitignore":
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            body = path.read_text(encoding="utf-8", errors="ignore")
            body = body.replace("/api/v1/course/tasks/", "")
            for legacy in legacy_paths:
                # These occurrences describe migration of historical Git tags,
                # not a live student entry. Other obsolete paths remain errors.
                if legacy == "course/baselines/PROGRESSION.json" and path.relative_to(ROOT).as_posix() in {
                    "workbench/lesson_constructibility.py", "tests/test_lesson_constructibility.py",
                    "docs/courses/逐讲实现与教学审计.md",
                }:
                    continue
                if legacy in body:
                    stale.append(f"{path.relative_to(ROOT)} -> {legacy}")
        self.assertFalse(stale, "\n".join(stale))

    def test_schedule_outline_machine_handouts_and_tasks_share_titles(self) -> None:
        expected = schedule_titles()
        contracts = outline_contracts()
        handouts = lesson_files(COURSES)
        tasks = lesson_files(COURSES / "tasks")
        self.assertEqual(set(range(1, 17)), set(expected))
        self.assertEqual(set(expected), set(contracts))
        self.assertEqual(set(expected), set(handouts))
        self.assertEqual(set(expected), set(tasks))
        self.assertEqual(expected, {item.number: item.title for item in LESSONS})
        for number, title in expected.items():
            with self.subTest(lesson=number):
                self.assertEqual(title, contracts[number][0])
                self.assertEqual(f"# L{number:02d}｜{title}", handouts[number].read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(f"# L{number:02d}｜{title}", tasks[number].read_text(encoding="utf-8").splitlines()[0])

    def test_handout_and_task_copy_the_four_outline_contract_lines(self) -> None:
        contracts = outline_contracts()
        handouts = lesson_files(COURSES)
        tasks = lesson_files(COURSES / "tasks")
        for number, (_title, lines) in contracts.items():
            self.assertEqual(4, len(lines), f"L{number:02d} 大纲四项合同不完整")
            for path in (handouts[number], tasks[number]):
                body = path.read_text(encoding="utf-8")
                for line in lines:
                    with self.subTest(lesson=number, file=path.name, contract=line[:20]):
                        self.assertIn(line, body)

    def test_each_handout_is_a_self_contained_student_chapter(self) -> None:
        for number, path in lesson_files(COURSES).items():
            body = path.read_text(encoding="utf-8")
            with self.subTest(lesson=number):
                for marker in (
                    "## 附录 A：本讲课程大纲合同",
                    "## 本章学习地图",
                    "FlowERP",
                    "工作台",
                    "Codex",
                    "正常路径",
                    "失败路径",
                    "失败后状态",
                    "第二次签字",
                    "## 本讲一手来源",
                ):
                    self.assertIn(marker, body)
                self.assertGreaterEqual(body.count("```"), 2)
                if path.parent.name == f"L{number:02d}":
                    self.assertRegex(body, rf"(?:\./)?slides/(?:【已确认】)?L{number:02d}-")
                    self.assertRegex(body, r"\]\((?:\./)?行动卡\.md\)")
                    self.assertRegex(body, r"\]\((?:\./)?SUBMISSION\.md\)")
                else:
                    self.assertIn(f"./slides/L{number:02d}-", body)
                    self.assertIn(f"./tasks/L{number:02d}-", body)
                    self.assertIn(f"./labs/L{number:02d}/", body)
                source_section = body.split("## 本讲一手来源", 1)[1]
                self.assertRegex(source_section, r"核验日期：\d{4}-\d{2}-\d{2}")
                self.assertGreaterEqual(source_section.count("https://"), 2)
                self.assertLessEqual(source_section.count("https://"), 5)
                self.assertNotIn("教师备课区", body)

    def test_student_entry_links_the_independent_slide_index(self) -> None:
        entry = (DOCS / "README.md").read_text(encoding="utf-8")
        index = (COURSES / "slides" / "README.md").read_text(encoding="utf-8")
        self.assertIn("./courses/slides/README.md", entry)
        self.assertEqual(16, len(re.findall(r"^\d+\. \[L\d{2}｜", index, re.MULTILINE)))

    def test_l04_bootstrap_shift_and_l16_live_delivery_are_explicit(self) -> None:
        l04 = lesson_files(COURSES)[4].read_text(encoding="utf-8")
        l16 = lesson_files(COURSES)[16].read_text(encoding="utf-8")
        self.assertIn("自举换挡", l04)
        self.assertIn("Ticket A", l04)
        self.assertIn("Ticket B", l04)
        self.assertIn("此前未实现", l16)
        self.assertIn("动态 Eval", l16)
        self.assertIn("具名人审", l16)

    def test_student_navigation_excludes_internal_governance_documents(self) -> None:
        student_nav = (DOCS / "README.md").read_text(encoding="utf-8")
        for name in ("国家级一流本科课程建设方案", "国家级一流本科课程申报级质量门"):
            self.assertNotIn(name, student_nav)
        for required in ("国家级一流本科课程建设方案.md", "国家级一流本科课程申报级质量门.md"):
            self.assertTrue((COURSES / required).is_file())

    def test_workspace_points_back_to_repository_root(self) -> None:
        workspace = (COURSES / "FlowERP-AI研发工作台.code-workspace").read_text(encoding="utf-8")
        self.assertIn('"path": "../.."', workspace)
        self.assertIn('"workbench.cli", "course-status"', workspace)

    def test_all_local_markdown_links_resolve(self) -> None:
        broken: list[str] = []
        for path in DOCS.rglob("*.md"):
            body = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", body):
                target = target.strip().strip("<>")
                if not target or target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                local = unquote(target.split("#", 1)[0])
                if not (path.parent / local).resolve().exists():
                    broken.append(f"{path.relative_to(ROOT)} -> {target}")
        self.assertFalse(broken, "\n".join(broken))

    def test_course_assets_are_all_referenced(self) -> None:
        markdown = "\n".join(path.read_text(encoding="utf-8") for path in DOCS.rglob("*.md"))
        orphaned = [
            str(path.relative_to(ROOT))
            for path in (COURSES / "assets").rglob("*")
            if path.is_file() and path.name not in markdown
        ]
        self.assertFalse(orphaned, "\n".join(orphaned))


if __name__ == "__main__":
    unittest.main()
