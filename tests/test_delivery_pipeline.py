from __future__ import annotations

import unittest

from workbench.delivery_pipeline import (
    format_pipeline_for_llm,
    pipeline_payload,
    pipeline_system_message,
    stage_for_status,
)
from workbench.session_context import derive_messages
from workbench.system_prompt import assemble_system_prompt, run_pre_step


class DeliveryPipelineTests(unittest.TestCase):
    def test_stage_mapping_and_llm_text_include_all_steps(self) -> None:
        stage = stage_for_status("evaluating")
        self.assertEqual("eval", stage["id"])
        task = {
            "id": "TASK-1",
            "status": "evaluating",
            "request": "库存不足拒单",
            "execution_mode": "codex",
            "write_scope": ["flowerp", "tests"],
            "requirement_id": "REQ-1",
        }
        text = format_pipeline_for_llm(task=task, session_id="SESSION-1")
        self.assertIn("需求", text)
        self.assertIn("规格", text)
        self.assertIn("改代码", text)
        self.assertIn("验收", text)
        self.assertIn("老板终审", text)
        self.assertIn("current_status: evaluating", text)
        self.assertIn("对用户可见", text)

    def test_system_prompt_and_pre_step_inject_pipeline(self) -> None:
        task = {"id": "TASK-1", "status": "queued", "request": "入库幂等", "execution_mode": "verify"}
        prompt = assemble_system_prompt(task=task, tools=[{"id": "spec.read", "name": "spec", "description": ""}])
        self.assertTrue(any(section.id == "pipeline" for section in prompt.sections))
        self.assertIn("Delivery pipeline", prompt.as_text())
        decision = run_pre_step(task=task, tools=[], session_id="SESSION-1")
        self.assertTrue(decision.entered)
        self.assertEqual("system", decision.messages[0]["role"])
        self.assertEqual("delivery_pipeline", decision.messages[0]["source"])
        self.assertIn("流水线", decision.messages[0]["content"] + format_pipeline_for_llm(task=task))

    def test_derive_messages_projects_pipeline_events(self) -> None:
        session = {
            "events": [
                {
                    "kind": "pipeline/stage",
                    "payload": pipeline_payload("review", detail="eval green"),
                },
                {
                    "kind": "pipeline/context",
                    "payload": {"content": pipeline_system_message({"status": "review", "request": "x"})["content"]},
                },
                {
                    "kind": "agent/pre-step",
                    "payload": {
                        "action": "enter",
                        "prompt": {"text": "## Delivery pipeline\nstages visible"},
                    },
                },
            ]
        }
        messages = derive_messages(session)
        kinds = [item["kind"] for item in messages]
        self.assertIn("pipeline/stage", kinds)
        self.assertIn("pipeline/context", kinds)
        self.assertIn("agent/pre-step", kinds)
        stage_msg = next(item for item in messages if item["kind"] == "pipeline/stage")
        self.assertTrue(stage_msg["visible_to_model"])
        self.assertIn("等老板终审", stage_msg["content"])


if __name__ == "__main__":
    unittest.main()
