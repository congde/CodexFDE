from __future__ import annotations

import tempfile
import unittest

from workbench.server import App


class CourseAPITests(unittest.TestCase):
    def test_course_status_and_lesson_contracts_are_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = App(temporary, enable_legacy_workbench=True)
            status = app.api.dispatch("GET", "/api/v1/course/status", {}, None, "127.0.0.1")
            lessons = app.api.dispatch("GET", "/api/v1/course/lessons", {}, None, "127.0.0.1")
            lesson = app.api.dispatch("GET", "/api/v1/course/lessons/8", {}, None, "127.0.0.1")
            invalid = app.api.dispatch("GET", "/api/v1/course/lessons/17", {}, None, "127.0.0.1")
            self.assertEqual(200, status.status)
            self.assertTrue(status.body["contract_valid"])
            self.assertIn("course_ready", status.body)
            self.assertEqual(
                status.body["course_ready"],
                not status.body.get("missing_baseline_refs") and not status.body.get("baseline_errors"),
            )
            self.assertEqual(16, len(lessons.body["items"]))
            self.assertEqual("交付原子预占", lesson.body["erp_increment"])
            self.assertEqual(422, invalid.status)

    def test_candidate_export_and_cleanup_are_admin_api_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = App(temporary, enable_legacy_workbench=True)

            class FakeArtifacts:
                def export(self, _store, task_id):
                    return {"task_id": task_id, "patch_sha256": "a" * 64}

                def manifest(self, task_id):
                    return {"task_id": task_id, "patch_sha256": "a" * 64}

                def cleanup(self, _store, task_id):
                    return {"task_id": task_id, "removed": True}

            app.api.course_artifacts = FakeArtifacts()
            headers = {"idempotency-key": "course-export-1"}
            exported = app.api.dispatch(
                "POST", "/api/v1/course/tasks/TASK-1234567890/candidate", headers, {}, "127.0.0.1",
            )
            replay = app.api.dispatch(
                "POST", "/api/v1/course/tasks/TASK-1234567890/candidate", headers, {}, "127.0.0.1",
            )
            manifest = app.api.dispatch(
                "GET", "/api/v1/course/tasks/TASK-1234567890/candidate", {}, None, "127.0.0.1",
            )
            cleaned = app.api.dispatch(
                "POST", "/api/v1/course/tasks/TASK-1234567890/worktree-clean",
                {"idempotency-key": "course-clean-1"}, {}, "127.0.0.1",
            )
            self.assertEqual(201, exported.status)
            self.assertEqual("false", exported.headers["Idempotent-Replay"])
            self.assertEqual("true", replay.headers["Idempotent-Replay"])
            self.assertEqual("a" * 64, manifest.body["patch_sha256"])
            self.assertTrue(cleaned.body["removed"])


if __name__ == "__main__":
    unittest.main()
