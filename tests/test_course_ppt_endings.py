from pathlib import Path
import re
import unittest
from zipfile import ZipFile
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
COURSES = ROOT / "docs" / "courses"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def last_slide_text(path: Path) -> str:
    with ZipFile(path) as package:
        presentation = ET.fromstring(package.read("ppt/presentation.xml"))
        slide_ids = presentation.findall(f".//{{{P_NS}}}sldId")
        if not slide_ids:
            raise AssertionError(f"{path} 没有幻灯片")
        relationship_id = slide_ids[-1].attrib[f"{{{R_NS}}}id"]

        relationships = ET.fromstring(package.read("ppt/_rels/presentation.xml.rels"))
        targets = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationships.findall(f"{{{REL_NS}}}Relationship")
        }
        target = targets[relationship_id].replace("\\", "/").lstrip("/")
        slide_part = target if target.startswith("ppt/") else f"ppt/{target}"
        slide = ET.fromstring(package.read(slide_part))
        return "\n".join(node.text or "" for node in slide.findall(f".//{{{A_NS}}}t"))


class CoursePptEndingTests(unittest.TestCase):
    def test_l03_to_l16_end_with_summary_questions_and_transition(self):
        for number in range(3, 17):
            lesson = f"L{number:02d}"
            decks = list((COURSES / lesson / "slides").glob("*.pptx"))
            self.assertEqual(len(decks), 1, f"{lesson} 应只有一份正式 PPT")
            text = last_slide_text(decks[0])
            with self.subTest(lesson=lesson):
                self.assertIn("课程总结", text)
                self.assertIn("思考", text)
                self.assertTrue(
                    "本课程完成了什么" in text or "本讲完成了什么" in text,
                    f"{lesson} 末页缺少成果回收",
                )
                for question in ("Q1", "Q2", "Q3"):
                    self.assertIn(question, text)
                transition = "课程之后" if number == 16 else "下一讲"
                self.assertIn(transition, text)


if __name__ == "__main__":
    unittest.main()
