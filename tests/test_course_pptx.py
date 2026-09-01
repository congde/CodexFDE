from __future__ import annotations

import re
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
PPT_DIR = ROOT / "docs" / "courses" / "ppt"
TEXT_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"


def _numbered_members(archive: zipfile.ZipFile, prefix: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.xml$")
    numbered: list[tuple[int, str]] = []
    for name in archive.namelist():
        match = pattern.match(name)
        if match:
            numbered.append((int(match.group(1)), name))
    return [name for _, name in sorted(numbered)]


def _xml_text(archive: zipfile.ZipFile, member: str) -> str:
    root = ElementTree.fromstring(archive.read(member))
    return " ".join(node.text or "" for node in root.iter(TEXT_TAG))


class CoursePptxContractTests(unittest.TestCase):
    def test_sixteen_independent_decks_preserve_three_carrier_order(self) -> None:
        decks = sorted(PPT_DIR.glob("L??-*.pptx"))
        self.assertEqual(16, len(decks))
        self.assertEqual(
            [f"L{number:02d}" for number in range(1, 17)],
            [deck.name[:3] for deck in decks],
        )
        self.assertFalse((PPT_DIR / "FlowERP-AI研发工作台-16讲决策课件.pptx").exists())

        first_signature_pages: list[str] = []
        for deck in decks:
            with self.subTest(deck=deck.name), zipfile.ZipFile(deck) as archive:
                slides = _numbered_members(archive, "ppt/slides/slide")
                notes = _numbered_members(archive, "ppt/notesSlides/notesSlide")
                self.assertGreater(len(slides), 4)
                self.assertEqual(len(slides), len(notes))

                slide_texts = [_xml_text(archive, member) for member in slides]
                note_texts = [_xml_text(archive, member) for member in notes]
                first = [index for index, text in enumerate(slide_texts, 1) if "第一次签字" in text]
                second = [index for index, text in enumerate(slide_texts, 1) if "第二次签字" in text]

                self.assertEqual(1, len(first))
                self.assertEqual(1, len(second))
                self.assertLessEqual(first[0], 4)
                self.assertLess(first[0], second[0])
                self.assertEqual(len(slides), second[0])
                self.assertIn("VS Code", slide_texts[second[0] - 1])
                self.assertTrue(all("[Sources]" in text for text in note_texts))
                first_signature_pages.append(slide_texts[first[0] - 1])

        self.assertEqual(16, len(set(first_signature_pages)))


if __name__ == "__main__":
    unittest.main()
