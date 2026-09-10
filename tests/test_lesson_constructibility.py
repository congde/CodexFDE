from __future__ import annotations
from workbench.external_project import flowerp_root

import time
import tempfile
import unittest
from pathlib import Path

from workbench.lesson_constructibility import apply_student_start, diagnose, lesson_gap


def _read_text(path: Path) -> str:
    last_error: Exception | None = None
    for _ in range(5):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)
    raise last_error or PermissionError(path)


class LessonConstructibilityTests(unittest.TestCase):
    def test_prepared_version_keeps_existing_gap_without_adding_it_again(self):
        source = Path(__file__).resolve().parents[1] / 'workbench/workbench_server.py'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'workbench').mkdir()
            target=root/'workbench/workbench_server.py'
            target.write_text(source.read_text(encoding='utf-8'),encoding='utf-8')
            apply_student_start(root,13)
            before=target.read_bytes()
            second=apply_student_start(root,13)
            self.assertTrue(second['start_gap_applied'])
            self.assertEqual([],second['applied_overlays'])
            self.assertEqual(['workbench/workbench_server.py'],second['existing_overlays'])
            self.assertEqual(before,target.read_bytes())

    def test_l13_current_api_signature_has_a_real_start_gap(self):
        source = Path(__file__).resolve().parents[1] / 'workbench/workbench_server.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'workbench').mkdir()
            target = root / 'workbench/workbench_server.py'
            target.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
            result = apply_student_start(root, 13)
            self.assertTrue(result['start_gap_applied'])
            self.assertIn('raise NotImplementedError("L13 工作台异步任务受理尚未实现")', target.read_text(encoding='utf-8'))

    def test_missing_anchor_is_reported_instead_of_claiming_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workbench").mkdir()
            (root / "workbench/workbench_server.py").write_text("# changed source", encoding="utf-8")
            result = apply_student_start(root, 13)
            self.assertFalse(result["start_gap_applied"])
            self.assertEqual([{"path": "workbench/workbench_server.py", "reason": "anchor_missing"}], result["unapplied_overlays"])

    def test_missing_source_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = apply_student_start(temporary, 14)
            self.assertFalse(result["start_gap_applied"])
            self.assertEqual("source_missing", result["unapplied_overlays"][0]["reason"])

    def test_l01_start_removes_answers_but_cli_registration_remains_importable(self) -> None:
        import argparse
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in lesson_gap(1).overlay_files:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("reference answer", encoding="utf-8")
            result = apply_student_start(root, 1)
            self.assertEqual(3, len(result["applied_overlays"]))
            self.assertFalse((root / "tests/test_l01_workbench_bootstrap.py").exists())
            self.assertFalse((root / "docs/courses/L01/WORKBENCH_SPEC.md").exists())
            namespace = {}
            exec((root / "workbench/bootstrap.py").read_text(encoding="utf-8"), namespace)
            parser = argparse.ArgumentParser()
            sub = parser.add_subparsers()
            namespace["add_bootstrap_commands"](sub)
            self.assertNotIn("workbench-init", sub.choices)

    def test_overlay_refuses_controller_repository(self) -> None:
        with self.assertRaisesRegex(ValueError, "控制仓库"):
            apply_student_start(Path(__file__).resolve().parents[1], 1)

    def test_diagnosis_covers_all_lessons(self) -> None:
        report = diagnose()
        self.assertEqual(16, len(report["lessons"]))
        self.assertIn("HEAD 在提供答案", report["finding"])
        self.assertTrue(lesson_gap(4).already_answered_on_head)

    def test_l03_overlay_accepts_incomplete_spec(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "workbench"
            target.mkdir()
            (target / "spec.py").write_text((source / "workbench" / "spec.py").read_text(encoding="utf-8"), encoding="utf-8")
            result = apply_student_start(root, 3)
            self.assertIn("workbench/spec.py", result["applied_overlays"])
            self.assertIn("if False and missing:", _read_text(root / "workbench" / "spec.py"))

    def test_l04_overlay_breaks_export_contract(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "flowerp"
            target.mkdir()
            (target / "service.py").write_text(( flowerp_root() / "flowerp/service.py").read_text(encoding="utf-8"), encoding="utf-8")
            result = apply_student_start(root, 4)
            self.assertIn("flowerp/service.py", result["applied_overlays"])
            self.assertIn('["sku,name,available"]', _read_text(root / "flowerp" / "service.py"))

    def test_historical_progression_gate_is_preserved_as_preparation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gate = root / 'course' / 'baselines' / 'PROGRESSION.json'
            gate.parent.mkdir(parents=True)
            gate.write_text('{"enabled": []}', encoding='utf-8')
            result = apply_student_start(root, 4)
            self.assertFalse(gate.exists())
            self.assertTrue(result['removed_progression_gate'])
            self.assertEqual('course/baselines/PROGRESSION.json', result['removed_progression_gates'][0]['path'])
            self.assertEqual('{"enabled": []}', result['removed_progression_gates'][0]['content'])
            self.assertEqual(64, len(result['removed_progression_gates'][0]['sha256']))

    def test_l04_historical_exporter_loses_only_inventory_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'flowerp').mkdir()
            (root / 'flowerp/service.py').write_text('class ERPService: pass\n', encoding='utf-8')
            exporter = root / 'flowerp/import_export.py'
            exporter.write_text('def export_csv():\n    queries = {\n        "products": "keep",\n        "inventory":("SELECT p.sku,p.name,stock FROM stock",()),\n    }\n    return queries\n', encoding='utf-8')
            result = apply_student_start(root, 4)
            self.assertTrue(result['start_gap_applied'])
            self.assertEqual(['flowerp/import_export.py'], result['applied_overlays'])
            text = exporter.read_text(encoding='utf-8')
            self.assertIn('"products": "keep"', text)
            self.assertNotIn('"inventory":', text)

    def test_l05_overlay_breaks_idempotent_receive(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "flowerp"
            target.mkdir()
            (target / "service.py").write_text(
                ( flowerp_root() / "flowerp/service.py").read_text(encoding="utf-8"), encoding="utf-8",
            )
            (root / "docs" / "courses" / "labs" / "baselines").mkdir(parents=True)
            (root / "docs" / "courses" / "labs" / "baselines" / "PROGRESSION.json").write_text("{}", encoding="utf-8")
            result = apply_student_start(root, 5)
            self.assertTrue(result["removed_progression_gate"])
            self.assertIn("flowerp/service.py", result["applied_overlays"])
            text = _read_text(root / "flowerp" / "service.py")
            self.assertIn("if False and exists:", text)


if __name__ == "__main__":
    unittest.main()
