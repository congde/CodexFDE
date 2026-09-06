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
        self.assertIn('id="course-lesson-list"', html)
        self.assertIn('id="dashboard-run-eval"', html)
        self.assertIn('id="feedback-actions"', html)
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
        self.assertIn("User / Agent / 工具是 Session 事件视角，不是三个独立登录", script)
        self.assertNotIn("Agent 员工班组（不可登录）", html)

    def test_dashboard_reads_real_delivery_and_runtime_evidence(self) -> None:
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")
        server = (ROOT / "workbench" / "platform_server.py").read_text(encoding="utf-8")

        for endpoint in (
            "/api/v1/course/status",
            "/api/v1/feedback",
            "/api/v1/evolutions?limit=50",
            "/api/v1/tasks?limit=100",
            "/api/v1/course/lessons",
            "/api/v1/flowerp/status",
            "/api/v1/dump-config?profile_id=",
        ):
            self.assertIn(endpoint, script)
        self.assertIn("/api/v1/profiles/${encodeURIComponent(profileId)}/runtime", script)
        self.assertIn("/api/v1/plugin-events?profile_id=${encodeURIComponent(profileId)}&limit=20", script)
        self.assertIn("function runVerifyEval()", script)
        self.assertIn("function reviewFeedbackItem(feedbackId, decision)", script)
        self.assertIn("function startLessonInitiative(number)", script)
        self.assertIn("function fillInitiativeDraft(draft", script)
        self.assertIn("harness.workbench-snapshot/v1", script)
        self.assertIn("不包含密钥和运行数据库", script)
        self.assertIn("课程状态检查中", script)
        self.assertIn("AbortController", script)
        self.assertIn("courseStatusRequestId", script)
        self.assertNotIn('.catch(() => ({ course_ready: false }))', script)
        self.assertIn("BrokenPipeError", server)
        self.assertIn('content_type += "; charset=utf-8"', server)
        self.assertIn('self.send_header("Cache-Control", "no-cache")', server)

    def test_delivery_workspace_is_action_first_and_progressively_disclosed(self) -> None:
        html = (ROOT / "harness_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "harness_web" / "styles.css").read_text(encoding="utf-8")

        self.assertLess(html.index('id="recovery-banner"'), html.index('id="progress-rail"'))
        self.assertLess(html.index('id="progress-rail"'), html.index('id="collab-bar"'))
        self.assertIn('id="back-dashboard"', html)
        self.assertIn('id="toggle-message-density"', html)
        self.assertIn("state.showAllMessages ? allMessages : summaryMessages", script)
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
        self.assertIn("历史编码损坏记录（已替代）", script)
        self.assertIn("item.superseded_by", script)
        self.assertIn("请先写下决定依据；工作台不会替你编造理由", script)
        self.assertIn(".initiative-item.strict", styles)
        self.assertIn("补充证据与交付合同", script)

    def test_chat_and_trajectory_tabs_show_real_session_evidence(self) -> None:
        html = (ROOT / "harness_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "harness_web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('aria-controls="view-chat"', html)
        self.assertIn('aria-controls="view-trajectory"', html)
        self.assertIn('id="traj-inspector"', html)
        self.assertIn('class="delivery-view tab-chat"', html)
        self.assertIn("function conversationMessages()", script)
        self.assertIn("function messagesFromEvents(", script)
        self.assertIn("function renderTrajectoryInspector(", script)
        self.assertIn('delivery.classList.toggle("tab-trajectory"', script)
        self.assertIn("state.expandedRailId = null", script)
        self.assertNotIn(
            'if (state.task && state.workbenchView === "delivery") openGraphDetails()',
            script,
        )
        self.assertIn(".traj-workspace", styles)
        self.assertIn("min-height: 280px", styles)
        self.assertIn("点左侧一条记录，查看完整事件", html)
        self.assertIn("点左侧一条记录，查看谁在何时做了什么", script)

    def test_boss_identity_and_duty_chips_are_switchable(self) -> None:
        html = (ROOT / "harness_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="actor-options"', html)
        self.assertIn('id="actor-recents"', html)
        self.assertIn('id="actor-hint"', html)
        self.assertIn('data-emp="user"', html)
        self.assertIn('<button type="button" class="chip-mini" data-emp="user">你（User）</button>', html)
        self.assertIn('<button type="button" class="chip-mini" data-emp="agent">Agent</button>', html)
        self.assertIn('<button type="button" class="chip-mini" data-emp="tool">工具</button>', html)
        self.assertNotIn('data-emp="agent:spec">产品', html)
        self.assertNotIn('data-traj="agent:spec"', html)
        self.assertIn("function focusDuty(", script)
        self.assertIn("function commitActor(", script)
        self.assertIn('$("#employee-chips").addEventListener("click"', script)
        self.assertIn("这是 Harness 运行时筛选，不是独立登录账号", script)
        self.assertIn('终审身份已切换为', script)
        self.assertIn("User / Agent / 工具是 Session 事件视角，不是三个独立登录", script)
        self.assertNotIn("规格员", html)
        self.assertNotIn("规格员", script)
        self.assertIn('role: "业务"', script)
        self.assertIn('role: "Leader"', script)
        self.assertNotIn('setActor("agent:', script)

    def test_settings_dialog_can_be_dismissed(self) -> None:
        html = (ROOT / "harness_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "harness_web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="settings"', html)
        self.assertIn("data-close-dialog", html)
        self.assertIn('aria-label="关闭设置"', html)
        self.assertIn("function closeDialog(", script)
        self.assertIn("function bindDialogDismiss()", script)
        self.assertIn("event.target === dialog", script)
        self.assertIn(".dlg-foot", styles)
        self.assertIn(".dlg-body", styles)
        self.assertIn("bindDialogDismiss()", script)

    def test_harness_ui_references_deepseek_and_codex_without_claiming_equivalence(self) -> None:
        html = (ROOT / "harness_web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_web" / "app.js").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        gap = (ROOT / "docs" / "reference" / "个人AI研发工作台.md").read_text(encoding="utf-8")

        self.assertIn("对照 DeepSeek Harness", html)
        self.assertIn("对照 Codex Harness", html)
        self.assertIn("openai/codex", html)
        self.assertIn("不是 Cordis", html)
        self.assertIn("Thread / Session", html)
        self.assertIn("Event stream", html)
        self.assertIn("暂停 · Interrupt", html)
        self.assertIn("交付终审", script)
        self.assertIn("工具 ask", script)
        self.assertIn("approval=ask", script)
        self.assertIn('id="dump-config"', html)
        self.assertIn('id="settings-profile"', html)
        self.assertIn('id="traj-source-filters"', html)
        self.assertIn("function switchProfile(", script)
        self.assertIn("function activatePlugin(", script)
        self.assertIn("function renderDumpConfig(", script)
        self.assertNotIn("已接入 openai/codex app-server", html)
        self.assertIn("docs/reference/个人AI研发工作台.md", readme)
        self.assertIn("不得", gap)
        self.assertIn("app-server", gap)


if __name__ == "__main__":
    unittest.main()
