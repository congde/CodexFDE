import tempfile
import unittest
from pathlib import Path

from workbench.automation import DeliveryAutomation
from workbench.task_store import TaskStore


class TaskRecoveryPolicyTests(unittest.TestCase):
    def test_web_code_recovery_preserves_only_finished_differential_reviews(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / 'workbench.db')
            for gate in (None, False, True):
                task = store.create('网页执行夹具', execution_mode='codex', write_scope=['flowerp'])
                store.append_event(task['id'], '网页具名授权课程隔离执行', evidence={'fixture':True})
                for phase in ('spec_ready', 'executing', 'evaluating', 'review'):
                    store.transition(task['id'], phase)
                if gate is not None:
                    store.append_event(task['id'], '课程红绿差分判定已完成', evidence={'accepted':gate})
                before = store.get(task['id'])['events']
                changed = store.quarantine_interrupted_web_code_tasks()
                after = store.get(task['id'])
                if gate is True:
                    self.assertEqual([], changed)
                    self.assertEqual('review', after['status'])
                    self.assertEqual(before, after['events'])
                else:
                    self.assertEqual([task['id']], changed)
                    self.assertEqual('dead_letter', after['status'])
                    self.assertEqual(before, after['events'][:-1])
                self.assertEqual([], store.quarantine_interrupted_web_code_tasks())

    def test_web_recovery_does_not_touch_cli_or_verify_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / 'workbench.db')
            cli_task = store.create('CLI任务', execution_mode='codex', write_scope=['flowerp'])
            verify = store.create('仅复验')
            self.assertEqual([], store.quarantine_interrupted_web_code_tasks())
            self.assertEqual('queued', store.get(cli_task['id'])['status'])
            self.assertEqual('queued', store.get(verify['id'])['status'])

    def test_prepared_task_resumes_instead_of_remaining_stuck(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "workbench.db")
            runner = DeliveryAutomation(store, directory, max_attempts=1, suite_runner=lambda *_a, **_k: {
                "summary": {"decision": "pass", "blocking_failed": 0},
                "results": [{"name": "recovery_fixture", "level": "blocking", "passed": True}],
            })
            task = runner.submit("复验恢复边界", auto_start=False)
            from workbench.workflow import prepare_task
            prepare_task(store, task["id"], "student")
            self.assertEqual([task["id"]], runner.recover())
            self.assertEqual("review", runner.wait(task["id"])["status"])

    def test_interrupted_code_tasks_require_manual_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "workbench.db")
            for phase in ("executing", "evaluating"):
                task = store.create("改代码", automation_mode="automatic", execution_mode="codex", write_scope=["flowerp/"])
                store.transition(task["id"], "spec_ready", spec={"goal": "修复"})
                store.transition(task["id"], "executing")
                if phase == "evaluating":
                    store.transition(task["id"], "evaluating")
                prior = store.get(task["id"])["events"]
                self.assertEqual([], store.recover_automatic_tasks())
                after = store.get(task["id"])
                self.assertEqual("dead_letter", after["status"])
                self.assertFalse(after["events"][-1]["evidence"]["safe_replay"])
                self.assertEqual(prior, after["events"][:-1])
                self.assertEqual([], store.recover_automatic_tasks())

    def test_rework_is_not_retried_on_every_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "workbench.db")
            task = store.create("保持真实失败", automation_mode="automatic")
            store.transition(task["id"], "spec_ready")
            store.transition(task["id"], "executing")
            store.transition(task["id"], "rework", error="真实阻断失败")
            self.assertEqual([], store.recover_automatic_tasks())
            self.assertEqual("真实阻断失败", store.get(task["id"])["error"])
