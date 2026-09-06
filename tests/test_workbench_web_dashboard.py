from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkbenchWebDashboardTests(unittest.TestCase):
    def test_cockpit_is_four_panel_follow_along_surface(self) -> None:
        html = (ROOT / "workbench_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "workbench_web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "workbench_web" / "styles.css").read_text(encoding="utf-8")
        for marker in (
            "个人研发工作台",
            "客户项目",
            "本讲合同",
            "当前任务",
            "证据链",
            "上一次工作台升级",
            "8000",
            "8010",
            "不是大纲通过项",
        ):
            self.assertIn(marker, html)
        self.assertNotIn("8010 是跟跑", html)
        self.assertNotIn("apiKey", html + script + css)
        self.assertNotIn("secret", html.lower() + script.lower())
        self.assertIn("/api/course/current", script)
        self.assertIn("/api/v1/delivery/views", script)
        self.assertIn("/api/cockpit/upgrade", script)
        self.assertIn("/verify", script)
        self.assertIn("/review", script)
        self.assertIn("提交并复验", html)
        self.assertIn("批准完成", html)
        self.assertNotIn("JSON.stringify(detail", script)
        self.assertIn("工作台正在被你造出来", script)

    def test_identity_copy_keeps_flowerp_as_case(self) -> None:
        html = (ROOT / "workbench_web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("写代码的主体", html)
        self.assertIn("FlowERP 是客户项目案例", html)
        self.assertNotIn("员工门户", html)
