from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HarnessWebDashboardTests(unittest.TestCase):
    def test_personal_workbench_is_dashboard_first(self) -> None:
        html = (ROOT / "harness_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")

        self.assertLess(html.index('id="dashboard-view"'), html.index('id="delivery-view"'))
        self.assertIn("我的 AI 研发工作台", html)
        self.assertIn("业务信号快录", html)
        self.assertIn("工作台能力证据", html)
        self.assertIn("FlowERP 业务规则", html)
        self.assertIn("反馈与进化", html)
        self.assertIn('class="compact-nav"', html)
        self.assertIn('id="attention-panel"', html)
        self.assertIn('id="review-dialog"', html)
        self.assertIn('setWorkbenchView(state.workbenchView)', script)
        self.assertNotIn("window.prompt(", script)
        self.assertIn("显示全部技术记录", html)

    def test_navigation_and_quality_cards_do_not_pretend_internal_capabilities_are_modules(self) -> None:
        html = (ROOT / "harness_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('<b>Eval / Harness</b>', html)
        self.assertNotIn('<b>Loop / Graph</b>', html)
        self.assertIn('href="http://127.0.0.1:8000/"', html)
        self.assertIn('id="composer-card" hidden aria-hidden="true"', html)
        self.assertIn('id="delivery-decision-gate"', html)
        self.assertIn("交付区只接收已经批准的 Build 事项", html)
        self.assertIn("新需求必须先通过事项与决策门", script)
        self.assertIn("openDecisionWithSignal(btn.dataset.prompt", script)
        self.assertNotIn('$("#new-chat").addEventListener("click", createSession)', script)
        self.assertIn('else if (act === "new") openDecisionWithSignal()', script)
        self.assertIn("latestEval.report_path", script)
        self.assertIn("latestEval.report_sha256", script)
        self.assertIn("latestEval.cases", script)
        self.assertIn("可用未使用", script)
        self.assertIn("职责投影 · template", script)
        self.assertIn("三个角色只是同一流水线的职责与事件标签，不是三个独立 Agent", script)
        self.assertNotIn("Agent 员工班组（不可登录）", html)

    def test_dashboard_reads_real_delivery_and_runtime_evidence(self) -> None:
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")

        for endpoint in (
            "/api/v1/course/status",
            "/api/v1/profiles/PROFILE-DEFAULT/runtime",
            "/api/v1/plugin-events?profile_id=PROFILE-DEFAULT&limit=20",
            "/api/v1/feedback",
            "/api/v1/evolutions?limit=50",
            "/api/v1/tasks?limit=100",
        ):
            self.assertIn(endpoint, script)
        self.assertIn("harness.workbench-snapshot/v1", script)
        self.assertIn("不包含密钥和运行数据库", script)

    def test_delivery_workspace_is_action_first_and_progressively_disclosed(self) -> None:
        html = (ROOT / "harness_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "harness_web" / "styles.css").read_text(encoding="utf-8")

        self.assertLess(html.index('id="recovery-banner"'), html.index('id="progress-rail"'))
        self.assertLess(html.index('id="progress-rail"'), html.index('id="collab-bar"'))
        self.assertIn('id="back-dashboard"', html)
        self.assertIn('id="toggle-message-density"', html)
        self.assertIn("state.showAllMessages ? state.messages : summaryMessages", script)
        self.assertNotIn("state.expandedRailId = RAIL[idx].id", script)
        self.assertIn('id="toggle-team"', script)
        self.assertIn('setWorkbenchView("decision");', script)
        self.assertIn(".compact-nav", styles)
        self.assertIn("overflow-x: auto", styles)

    def test_product_decision_precedes_task_delivery(self) -> None:
        html = (ROOT / "harness_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "harness_web" / "styles.css").read_text(encoding="utf-8")

        self.assertLess(html.index('id="decision-view"'), html.index('id="delivery-view"'))
        self.assertIn('data-workbench-view="decision"', html)
        self.assertIn("Build / Experiment / Defer / Reject / Stop", html)
        self.assertIn('id="initiative-signal"', html)
        self.assertIn('id="initiative-evidence"', html)
        self.assertIn('id="initiative-acceptance"', html)
        self.assertIn("/api/v1/initiatives?limit=100", script)
        self.assertIn("function createInitiative()", script)
        self.assertIn("function reviseInitiative(id)", script)
        self.assertIn("function decideInitiative(id, decision)", script)
        self.assertIn("function promoteInitiative(id)", script)
        self.assertIn("请先写下决定依据；工作台不会替你编造理由", script)
        self.assertIn(".initiative-item.strict", styles)
        self.assertIn("补充证据与交付合同", script)


if __name__ == "__main__":
    unittest.main()
