from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from tests.test_course_outline_alignment import schedule_titles


ROOT = Path(__file__).resolve().parents[1]
COURSES = ROOT / "docs" / "courses"
BLUEPRINT = COURSES / "课程蓝图.md"
SLIDES = COURSES / "slides"


def pptx_name(number: int, title: str) -> str:
    stem = f"L{number:02d}-{title.replace('`', '')}"
    stem = re.sub(r'[<>:"/\\|?*]', "-", stem)
    stem = re.sub(r"\s+", "", stem)
    stem = re.sub(r"-+", "-", stem)
    return f"{stem}.pptx"


def slide_text(archive: zipfile.ZipFile, number: int) -> str:
    root = ET.fromstring(archive.read(f"ppt/slides/slide{number}.xml"))
    return "".join(root.itertext())


def normalized(text: str) -> str:
    return re.sub(r"[`\s]", "", text)


class CourseBlueprintTests(unittest.TestCase):
    def test_blueprint_has_22_pages_for_each_lesson(self) -> None:
        body = BLUEPRINT.read_text(encoding="utf-8")
        starts = list(re.finditer(r"^## L(\d{2})｜(.+)$", body, re.MULTILINE))
        self.assertEqual(16, len(starts))
        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
            section = body[match.end():end]
            pages = re.findall(r"^\|\s*(\d{1,2})\s*\|\s*(\d{1,2}:\d{2})\s*\|", section, re.MULTILINE)
            with self.subTest(lesson=match.group(1)):
                self.assertEqual([str(i) for i in range(1, 23)], [page for page, _time in pages])
                self.assertEqual("0:00", pages[0][1])
                self.assertEqual("29:00", pages[-1][1])
                self.assertIn("课程大纲四项合同", section)
                self.assertIn("一手来源（核验：2026-09-04）", section)

    def test_blueprint_has_ordered_timing_and_beginner_learning_support(self) -> None:
        body = BLUEPRINT.read_text(encoding="utf-8")
        for marker in (
            "不超过 30 分钟",
            "教师示范",
            "学生尝试",
            "独立检查",
            "正常路径",
            "失败路径",
            "任务卡交接",
        ):
            self.assertIn(marker, body)
        sections = re.split(r"^## L\d{2}｜.+$", body, flags=re.MULTILINE)[1:]
        for section in sections:
            times = [60 * int(m) + int(s) for m, s in re.findall(
                r"^\|\s*\d{1,2}\s*\|\s*(\d{1,2}):(\d{2})\s*\|", section, re.MULTILINE)]
            self.assertTrue(all(a < b for a, b in zip(times, times[1:])))
            self.assertTrue(all(0 <= time < 1800 for time in times))

    def test_editable_course_diagrams_and_previews_exist(self) -> None:
        for stem in ("course-three-layer", "workbench-capability-growth", "fde-feedback-loop"):
            source = COURSES / "assets" / f"{stem}.drawio"
            self.assertTrue(source.is_file())
            self.assertTrue((COURSES / "assets" / f"{stem}.svg").is_file())
            ET.parse(source)

    def test_each_lesson_has_one_validated_independent_deck(self) -> None:
        expected_titles = schedule_titles()
        expected = {(COURSES / f"L{number:02d}" / "slides" if number in (1, 2) else SLIDES) / pptx_name(number, title) for number, title in expected_titles.items()}
        recording = COURSES / "L01" / "slides" / pptx_name(1, expected_titles[1]).replace(".pptx", "-录课版.pptx")
        expected.add(recording)
        visual = recording.with_name(recording.name.replace("-录课版.pptx", "-图解版.pptx"))
        expected.add(visual)
        actual = set((ROOT / "docs").rglob("*.pptx"))
        self.assertEqual(expected, actual)

        decks = [(number, title, (COURSES / f"L{number:02d}" / "slides" if number in (1, 2) else SLIDES) / pptx_name(number, title))
                 for number, title in expected_titles.items()]
        decks.append((1, expected_titles[1], recording))
        decks.append((1, expected_titles[1], visual))
        for number, title, deck in decks:
            with self.subTest(lesson=number), zipfile.ZipFile(deck) as archive:
                names = archive.namelist()
                slide_names = [name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
                note_names = [name for name in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name)]
                self.assertEqual(22, len(slide_names))
                self.assertEqual(22, len(note_names))
                self.assertIn(normalized(title), normalized(slide_text(archive, 1)))
                # The visual L01 deck opens with a learning map; its full-course
                # product relationship is explicitly separated onto slide 4.
                relation = slide_text(archive, 4 if deck == visual else 2)
                # The relationship slide may use the classroom shorthand 工作台.
                for marker in ("工作台", "FlowERP", "Codex"):
                    self.assertIn(marker, relation)
                self.assertIn("<a:tbl", archive.read("ppt/slides/slide7.xml").decode("utf-8"))
                self.assertIn("正常路径", slide_text(archive, 18))
                self.assertIn("失败路径", slide_text(archive, 19))
                self.assertIn("任务卡", slide_text(archive, 22))
                notes = archive.read("ppt/notesSlides/notesSlide1.xml").decode("utf-8")
                self.assertIn("核验日期", notes)
                self.assertIn("https://", notes)

    def test_no_inspection_outputs_are_published_with_student_materials(self) -> None:
        self.assertFalse(list((ROOT / "docs").rglob("*.inspect.ndjson")))


if __name__ == "__main__":
    unittest.main()
