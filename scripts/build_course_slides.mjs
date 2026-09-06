import fs from "node:fs/promises";
import { l04Pages } from './course_slide_lessons/l04.mjs';
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceDir = process.env.WORKSPACE_DIR;
const skillDir = process.env.SKILL_DIR;
const tmpDir = process.env.TMP_DIR;
const outputDir = process.env.OUTPUT_DIR;
const runtimePython = process.env.RUNTIME_PYTHON;
const runtimeNodeModules = process.env.RUNTIME_NODE_MODULES;
if (![workspaceDir, skillDir, tmpDir, outputDir, runtimePython, runtimeNodeModules].every((value) => value && path.isAbsolute(value))) {
  throw new Error("WORKSPACE_DIR, SKILL_DIR, TMP_DIR, OUTPUT_DIR, RUNTIME_PYTHON and RUNTIME_NODE_MODULES must be absolute paths");
}

const { Presentation, PresentationFile } = await import(
  pathToFileURL(path.join(runtimeNodeModules, "@oai/artifact-tool/dist/artifact_tool.mjs")).href,
);

const { finalizePresentation } = await import(
  pathToFileURL(path.join(skillDir, "container_tools/artifact_tool_utils.mjs")).href,
);

const FONT = "Microsoft YaHei";
const CODE_FONT = "Consolas";
const WIDTH = 1280;
const HEIGHT = 720;
const EXPECTED_EMU = "12192000,6858000";
const COLORS = {
  ink: "#18242D",
  muted: "#5B6872",
  paper: "#F7F8FA",
  white: "#FFFFFF",
  line: "#D9DEE3",
  v0: "#165DFF",
  quality: "#356AC3",
  collaboration: "#B26A28",
  product: "#6B52B5",
  green: "#165DFF",
  red: "#5B6872",
  amber: "#A5621A",
  code: "#17242D",
};

const LESSONS = {
  1: {
    phase: "工作台 V0", accent: COLORS.v0,
    conflict: "一句“帮我做个研发工作台”无法决定边界、验收和责任。",
    decision: "先访谈并冻结最小范围，再让 Codex 写第一段实现。",
    mechanism: "痛点先变成可签署 Spec。验收项先变成红灯。红灯通过最小实现转绿。",
    wrong: "打开参考终态，看见测试全绿，就把结果当成自己的能力证据。",
    codex: "Codex 负责追问和候选实现。学生决定范围，并对签收负责。",
    boundary: "本讲不开发 FlowERP。它只作为后续客户项目登记。",
    normal: "访谈保留未决项，学生签署 WORKBENCH_SPEC.md，测试先红后绿，V0.1 记录自己的建设过程。",
    failure: "让 Codex 一次性设计全部功能。结果会混入任务编排、Web 和 ERP 假数据，无法证明哪项需求驱动了哪处修改。",
    unchanged: "范围未签署时，工作台能力仍未交付。\nFlowERP 保持不变。",
    evidence: "原始诉求、追问、学生决定、执行前红灯、范围内 Diff、执行后绿灯和自举记录。",
    transfer: "换一个陌生仓库，你能否先说清最小接手证据，而不是先问模型该写什么？",
    terms: [["Spec", "可签署的共同决定"], ["红灯", "实现前稳定失败的验收"], ["Diff", "实际改动与授权范围的对照"], ["自举", "工作台记录自身建设证据"]],
    codeFile: "workbench/cockpit.py", codeStart: 8, codeCount: 18,
    codeComment: "工作台先投影项目、任务和当前课次，界面才有可追溯的事实来源。",
    asset: "docs/courses/assets/l01-evidence/workbench-delivery-review.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m workbench.cli doctor",
  },
  2: {
    phase: "工作台 V0", accent: COLORS.v0,
    conflict: "运营要求把库存直接改成 100。一次口头拒绝无法约束下一次会话。",
    decision: "把长期不变量写进 AGENTS.md，同时给出合法补货路径。",
    mechanism: "规则文件保存跨会话约束。真实权限仍由沙箱、审批和工具边界执行。",
    wrong: "把当前库存值、临时任务和模型偏好都写成永久规则。",
    codex: "Codex 在新会话接受双探针。危险请求应拒绝，合法请求应继续。",
    boundary: "规则影响模型行为，但不能替代数据库约束、权限或人工审批。",
    normal: "新会话拒绝直改库存，并引导创建采购申请、具名审批和幂等入库。",
    failure: "规则只写“不允许修改库存”。合法收货也被拒绝，业务被永久卡死。",
    unchanged: "拒绝危险请求后，库存账和采购状态都不改变，拒绝理由可追溯。",
    evidence: "规则变更、危险请求响应、合法请求响应和新会话复验记录。",
    transfer: "哪些规则属于整个仓库，哪些规则应放到更近的子目录？",
    terms: [["AGENTS.md", "仓库内长期协作规则"], ["作用域", "规则覆盖的目录边界"], ["双探针", "一条危险请求加一条合法请求"], ["最小权限", "只开放任务所需能力"]],
    codeFile: "AGENTS.md", codeStart: 69, codeCount: 18,
    codeComment: "业务不变量和验证命令要写成可执行边界，不写成抽象口号。",
    asset: "docs/courses/assets/web-19-audit-trace.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m unittest tests.test_course_value -v",
  },
  3: {
    phase: "工作台 V0", accent: COLORS.v0,
    conflict: "“导出库存给运营”没有字段、排序、空结果和错误输入定义。",
    decision: "先冻结六段式 Spec，再允许任何实现进入写集。",
    mechanism: "来源、目标、非目标、约束、验收用例、完成定义：六段一起说清本次约定。",
    wrong: "把技术方案写得很详细，却没有任何人能判定业务结果是否正确。",
    codex: "Codex 协助澄清歧义并实现解析器。业务人员和学生共同签署结果口径。",
    boundary: "Spec 决定可验收结果，不替执行者选择框架或扩大产品范围。",
    normal: "固定列名、SKU 排序、空集输出和非法输入，解析器拒绝缺段或乱序合同。",
    failure: "把“支持未来订单、采购和渠道导出”塞入同一需求，导致验收无限扩张。",
    unchanged: "Spec 未通过解析和签字前，库存服务和数据库不发生修改。",
    evidence: "访谈记录、签字版 Spec、解析失败样例和解析成功结果。",
    transfer: "换成支付或权限需求，哪四类边界必须在编码前写清？",
    terms: [["Spec", "大家共同确认的任务约定"], ["SKU", "区分不同商品的编号"], ["CSV", "用逗号分列的表格文件"], ["验收用例", "别人也能照着检查的例子"]],
    codeFile: "docs/courses/labs/L03/example-spec.md", codeStart: 1, codeCount: 12,
    codeComment: "解析器只接受完整且有序的合同章节，空段和未知段直接失败。",
    asset: "docs/courses/assets/web-22-delivery-generated-spec.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m workbench.cli course-contract --lesson 3",
  },
  4: {
    phase: "工作台 V0", accent: COLORS.v0,
    conflict: "尚未独立验收的工作台准备给自己授权，再修改 FlowERP。",
    decision: "Ticket A 先证明钥匙可信。Ticket B 才用钥匙交付库存导出。",
    mechanism: "能力信封冻结读集、写集、禁止集、超时和停止条件。执行结果必须和授权逐项对账。",
    wrong: "一句“按 Spec 全部实现”允许 Codex 顺手重构无关模块。",
    codex: "Codex 先协助补齐 V0，再作为工作台中的受控执行者修改 FlowERP。",
    boundary: "执行者不能给自己验收。白名单外写入即使最后恢复，也算越界。",
    normal: "V0 独立通过后消费 L03 Spec，只修改授权文件，库存导出红灯转绿。",
    failure: "两张 Ticket 在同一次运行中完成，失败时无法区分工作台缺陷和导出缺陷。",
    unchanged: "越界或未独立验收时，候选变更不得签收，FlowERP 权威状态保持原样。",
    evidence: "两张 Ticket、能力信封、前红、真实 Diff、后绿和非执行者复验。",
    transfer: "你能否逐文件解释每一处改动对应哪条验收？",
    terms: [["能力信封", "执行前冻结的授权边界"], ["Ticket A", "独立验收 Workbench V0"], ["Ticket B", "通过 V0 交付库存导出"], ["执行者分离", "实现者不能独自签收"]],
    codeFile: "workbench/execution.py", codeStart: 34, codeCount: 27,
    codeComment: "写集先标准化并验证，后续 Diff 才能判定是否越过授权边界。",
    asset: "docs/courses/assets/web-26-delivery-real-code-executor.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m eval.harness --case inventory_export_is_stable",
  },
  5: {
    phase: "质量证据链", accent: COLORS.quality,
    conflict: "网络重试把同一收货消息发送两次，库存被重复增加。",
    decision: "先让重复入库稳定变红，再冻结 Eval 身份，最后修业务实现。",
    mechanism: "高价值 Eval 必须包含反例、权威状态和失败后不变状态。",
    wrong: "先改实现，再补一个只能证明新代码正确的测试。",
    codex: "Codex 帮助生成反例和候选修复。学生决定判据，复验者确认红绿来自同一用例。",
    boundary: "Eval 不能复制实现逻辑，否则测试和产品会一起犯错。",
    normal: "首次 event_key 入库增加库存，第二次重放返回同一结果且库存不再变化。",
    failure: "删除幂等查询后连续调用两次。第二次调用必须红，不能用异常吞掉重复写入。",
    unchanged: "失败后库存数量、流水条数和原始入库事件都保持第一次写入后的状态。",
    evidence: "同一 Eval 的修复前红灯、最小 Diff、修复后绿灯和库存账对比。",
    transfer: "支付回调、消息消费和文件导入如何复用同一个幂等判据？",
    terms: [["Eval", "可重复执行的质量判据"], ["反例", "主动证明错误会被抓住"], ["幂等键", "同一业务事件的稳定身份"], ["权威状态", "最终签收所依据的数据"]],
    codeFile: "flowerp/service.py", codeStart: 150, codeCount: 25,
    codeComment: "event_key 先查询接收记录，重放直接返回，不重复更新库存。",
    asset: "docs/courses/assets/web-24-delivery-blocking-eval.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m eval.harness --case receiving_is_idempotent",
  },
  6: {
    phase: "质量证据链", accent: COLORS.quality,
    conflict: "单元测试说通过，脚本退出码却是 0 或 1 不一致，团队无法自动签收。",
    decision: "所有质量检查统一进入 Harness，并输出等级、证据和明确退出码。",
    mechanism: "blocking 决定能否继续，observing 只暴露风险。报告 decision 必须与进程退出码一致。",
    wrong: "把所有失败都打印成日志，命令仍以 0 退出。",
    codex: "Codex 执行统一入口并读取结构化报告，不能自行降低失败等级。",
    boundary: "Harness 汇总判据，不替代业务权威状态或人工批准。",
    normal: "available 按 on_hand 减 reserved 计算，所有阻断项通过，进程以 0 退出。",
    failure: "制造负可用库存或隐藏 blocking 失败。报告必须 block，进程以非 0 退出。",
    unchanged: "评估失败只记录证据，不修改库存、订单或采购状态。",
    evidence: "结构化结果、用例级证据、等级、总判决和真实退出码。",
    transfer: "一个有 50 个检查的仓库，哪些失败应阻断，哪些只应观察？",
    terms: [["Harness", "统一执行和汇总入口"], ["blocking", "失败即停止交付"], ["observing", "失败可继续但必须暴露"], ["退出码", "自动化系统读取的最终信号"]],
    codeFile: "eval/harness.py", codeStart: 55, codeCount: 33,
    codeComment: "blocking_failed 同时驱动 JSON 判决和进程退出码，避免口径分裂。",
    asset: "docs/courses/assets/web-07-delivery-evidence.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m eval.harness --suite blocking",
  },
  7: {
    phase: "质量证据链", accent: COLORS.quality,
    conflict: "开发者忘记手工执行 Harness，销售订单变更带着回归进入提交。",
    decision: "让 Codex Stop Hook 调用同一 Harness，但保留显式失败和人工判断。",
    mechanism: "Hook 绑定生命周期事件，统一入口负责质量判决，stop_hook_active 防止递归触发。",
    wrong: "Hook 自己再实现一套测试逻辑，和 CI 的规则逐渐分叉。",
    codex: "Codex 结束一轮工作时触发 Hook。首次启用和危险动作仍需用户确认。",
    boundary: "Hook 是本地护栏，不是不可绕过的安全边界，也不能替代远端 CI。",
    normal: "订单创建满足金额和明细合同，Hook 调用 blocking suite 后允许结束。",
    failure: "制造订单总额错误。Hook 返回阻断信息，库存和订单状态不被额外修改。",
    unchanged: "Hook 失败后只保留报告，业务写入必须由事务保证原子性。",
    evidence: "Hook 配置、触发事件、Harness 报告、退出码和用户信任记录。",
    transfer: "保存、提交、推送和 Agent Stop 四个时点，各适合放什么检查？",
    terms: [["Hook", "生命周期事件触发器"], ["Stop", "Agent 准备结束一轮工作"], ["递归保护", "防止 Hook 再触发自身"], ["同一裁判", "本地和远端复用同一 Eval"]],
    codeFile: ".codex/hooks/quality_gate.py", codeStart: 1, codeCount: 27,
    codeComment: "Hook 读取事件，识别递归标记，并把统一 Harness 结果返回给 Codex。",
    asset: "docs/courses/assets/fde-evidence-chain.png",
    command: ".\\.venv\\Scripts\\python.exe .codex\\hooks\\quality_gate.py",
  },
  8: {
    phase: "质量证据链", accent: COLORS.quality,
    conflict: "开发者只提交最终绿灯截图，无法证明 CI 跑的是同一提交和同一判据。",
    decision: "用 Run A、Run B、Run C 建立假绿、可信红、可信绿的远端证据链。",
    mechanism: "Evidence Envelope 绑定 commit、run、suite、report hash 和 artifact 身份。",
    wrong: "在 CI 中重新写判据，或只上传一张最终绿色截图。",
    codex: "Codex 可以修改工作流和候选代码。平台签名、运行身份和制品保留由 CI 负责。",
    boundary: "远端绿色证明指定提交通过指定检查，不证明业务方已经批准上线。",
    normal: "同一 Eval 在本地和远端执行。Run C 的报告与提交、Run 和制品一一对应。",
    failure: "Run A 缺报告或身份仍显示成功。证据信封校验必须拒绝这类假绿。",
    unchanged: "远端复验失败不会自动修改主分支，也不会改变 FlowERP 运行数据。",
    evidence: "A 到 B 到 C 的提交身份、工作流、原始日志、报告哈希和 Artifact。",
    transfer: "换成发布流水线，怎样证明部署包来自刚刚通过测试的提交？",
    terms: [["Workflow", "远端复验步骤定义"], ["Artifact", "与 Run 绑定的证据制品"], ["Envelope", "报告和运行身份的封装"], ["可信绿", "判据、提交和证据可对账"]],
    codeFile: ".github/workflows/eval.yml", codeStart: 1, codeCount: 28,
    codeComment: "CI 安装同一仓库并运行同一 blocking suite，随后封装可核验的报告。",
    asset: "docs/courses/assets/web-09-delivery-evidence-fixed.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m unittest tests.test_ci_evidence -v",
  },
  9: {
    phase: "受控协作", accent: COLORS.collaboration,
    conflict: "取消订单没有释放预占。把整份长日志交给 Agent 会诱发无边界猜测。",
    decision: "把失败报告确定性映射为最小 Repair Task，再授权修复。",
    mechanism: "失败项、业务引用、允许写集、复验命令和停止条件共同构成修复合同。",
    wrong: "让 Agent 自由阅读所有日志，并“顺便把相关问题一起修好”。",
    codex: "Codex 消费结构化修复任务。映射规则由工作台执行，不能让模型改写原始失败。",
    boundary: "Repair Task 不是根因结论。它只压缩授权范围和必需证据。",
    normal: "cancellation_releases_reservation 失败映射到订单取消写集，修复后完整释放预占。",
    failure: "报告缺少用例身份或业务引用时，映射器拒绝生成可执行任务。",
    unchanged: "任务生成失败不修改订单。修复失败时原始报告和失败状态保留。",
    evidence: "原始 Harness 报告、确定性映射结果、范围内 Diff 和同一用例复验。",
    transfer: "安全扫描、性能回归和数据质量失败，分别需要哪些最小修复字段？",
    terms: [["Repair Task", "由失败证据生成的受控任务"], ["映射", "从失败身份到写集的确定性规则"], ["业务引用", "订单、SKU 等稳定对象身份"], ["停止条件", "何时必须停止继续修改"]],
    codeFile: "agent/repair.py", codeStart: 1, codeCount: 34,
    codeComment: "映射器从阻断失败提取允许文件和复验命令，报告为空时直接拒绝。",
    asset: "docs/courses/assets/web-23-delivery-event-trace.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m agent.repair",
  },
  10: {
    phase: "受控协作", accent: COLORS.collaboration,
    conflict: "修复脚本每轮都在改代码，却没有接近通过，成本持续增加。",
    decision: "同时限制轮次、时间、Token 和有效进展，任何一项触发都停止。",
    mechanism: "Loop 每轮重新评估，只消费当前失败，并把未收敛写成明确终态。",
    wrong: "使用 while true，直到 Agent 自己宣布已经修好。",
    codex: "Codex 提出每轮最小修复。工作台掌握预算、判据和停止权。",
    boundary: "自动循环不能跨越人工审核，也不能把预算耗尽包装成成功。",
    normal: "非法订单状态迁移在第一轮修复后转绿，Loop 以 converged 结束。",
    failure: "构造重复失败且无新证据的执行器。Loop 必须在预算内停止并报告 remaining_failures。",
    unchanged: "未收敛时，最后一次可信业务状态保持不变，历史轮次不可删除。",
    evidence: "每轮输入、Diff、Eval、Token 用量、停止理由和剩余失败。",
    transfer: "数据库迁移或基础设施修复，怎样定义“有进展”而不只看文件变化？",
    terms: [["Loop", "评估和修复的有界循环"], ["预算", "轮次、时间和 Token 上限"], ["进展", "失败集合或证据发生有效变化"], ["未收敛", "安全停止后的真实终态"]],
    codeFile: "agent/loop.py", codeStart: 51, codeCount: 40,
    codeComment: "每轮先检查预算，再运行统一 Harness。全绿、无进展和预算耗尽各有不同终态。",
    asset: "docs/courses/assets/web-23-delivery-event-trace.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m agent.loop --max-rounds 3",
  },
  11: {
    phase: "受控协作", accent: COLORS.collaboration,
    conflict: "采购申请跨业务、测试和文档。盲目并行会让多个 Agent 同时改同一文件。",
    decision: "先画读写集冲突图，只并行真正独立的任务，主 Agent 串行集成。",
    mechanism: "共享写集产生冲突。只读任务可并行。最终结论和复验仍由主 Agent 负责。",
    wrong: "按任务名称不同就判断可以并行，忽略它们实际修改同一个模块。",
    codex: "原生 Subagents 负责隔离调查或独立实现。主 Agent 汇总结论并拒绝冲突写集。",
    boundary: "并行提高等待效率，不扩大授权，也不降低每个子任务的证据要求。",
    normal: "领域调查和测试设计并行完成，采购申请由单一写入者实现，随后统一复验。",
    failure: "两个子任务都写 flowerp/service.py。调度器必须判冲突并改为串行。",
    unchanged: "冲突任务未启动前不留下部分文件，也不创建采购申请。",
    evidence: "子任务合同、读写集、调度决定、独立输出、集成 Diff 和 Harness 结果。",
    transfer: "代码、测试、文档和数据迁移四类任务，哪些组合能够安全并行？",
    terms: [["Subagent", "受控上下文中的独立工作者"], ["写集", "任务可能修改的文件集合"], ["冲突", "两个任务共享至少一个写目标"], ["串行集成", "主 Agent 逐项合并并统一复验"]],
    codeFile: "agent/schedule.py", codeStart: 1, codeCount: 42,
    codeComment: "调度器用规范化读写集判定冲突。共享写集必须进入串行批次。",
    asset: "docs/courses/assets/web-15-purchase-approval.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m unittest tests.test_schedule -v",
  },
  12: {
    phase: "受控协作", accent: COLORS.collaboration,
    conflict: "所有自动检查都通过，但采购审批属于职责分离，模型不能给自己签字。",
    decision: "用显式 Graph 保存状态、回退边和具名人工审核点。",
    mechanism: "状态图把暂停和恢复变成持久事实。ERP 状态与 Agent 状态必须分别存储并对账。",
    wrong: "把“质量全绿”直接映射为采购已批准。",
    codex: "Codex 在 develop 与 test 节点工作。human_review 节点只接受具名人的决定。",
    boundary: "Graph 组织流程，不获得业务审批权。匿名批准和模型自批都必须失败。",
    normal: "采购请求通过测试后停在 human_review，具名批准后才幂等入库。",
    failure: "提交空 reviewer 或非法回退。Graph 拒绝迁移，库存保持不变。",
    unchanged: "审批缺失时采购保持待审，库存账和入库流水没有变化。",
    evidence: "状态快照、迁移轨迹、具名决定、ERP 对账和恢复命令。",
    transfer: "生产发布、退款和权限提升，哪些节点必须由不同角色签字？",
    terms: [["Graph", "显式状态和迁移关系"], ["HITL", "流程中的人工决策点"], ["回退边", "失败后允许返回的状态"], ["职责分离", "申请、执行和批准由不同主体承担"]],
    codeFile: "agent/graph.py", codeStart: 59, codeCount: 43,
    codeComment: "Graph 在测试全绿后进入 human_review，不允许直接跳到 completed。",
    asset: "docs/courses/assets/web-25-delivery-human-gate.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m agent.graph --max-rounds 3",
  },
  13: {
    phase: "产品化与反馈", accent: COLORS.product,
    conflict: "脚本只能在开发者电脑运行，业务方无法获得稳定任务身份或查询进度。",
    decision: "把执行链封装成 Task API，创建请求只返回 task_id 和真实初始状态。",
    mechanism: "任务是资源。状态迁移、幂等键、事件和错误语义共同构成 API 合同。",
    wrong: "POST 请求一返回就写 completed，即使后台执行尚未开始。",
    codex: "Codex 在任务内部执行。API 决定身份、权限、状态和对外可见证据。",
    boundary: "异步接受不是完成。HTTP 成功也不等于业务交付成功。",
    normal: "补货请求返回稳定 task_id，客户端轮询事件，质量全绿后停在人工审核。",
    failure: "重复 idempotency-key 携带不同请求体。API 返回冲突，不创建第二个任务。",
    unchanged: "请求校验失败时任务库和 FlowERP 业务库都不产生部分记录。",
    evidence: "请求体哈希、task_id、事件序列、状态版本、Eval 和人审。",
    transfer: "长时间数据任务怎样区分 accepted、running、review 和 completed？",
    terms: [["Task API", "把执行链暴露为可查询资源"], ["202", "请求已接受但尚未完成"], ["幂等键", "重复请求的稳定身份"], ["状态版本", "防止并发覆盖新状态"]],
    codeFile: "workbench/api_v2.py", codeStart: 276, codeCount: 44,
    codeComment: "API 先验证 business_refs 和 write_scope，再通过幂等层创建交付任务。",
    asset: "docs/courses/assets/web-20-delivery-task-live.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m unittest tests.test_course_api -v",
  },
  14: {
    phase: "产品化与反馈", accent: COLORS.product,
    conflict: "页面显示“运行中”，但数据库里没有任务。静态假进度掩盖真实失败。",
    decision: "工作台和 FlowERP 分面展示，并让页面只投影各自权威 API。",
    mechanism: "工作台呈现任务、证据和下一步。FlowERP 呈现客户业务状态。两套数据库不得混用。",
    wrong: "用 setTimeout 和固定 JSON 模拟进度，演示结束后无法复验。",
    codex: "Codex 帮助实现页面和接口。状态真伪由 API、SQLite 和业务账对账决定。",
    boundary: "Web 负责可见性，不重新计算库存，也不在前端保存密钥。",
    normal: "任务页面显示真实状态和失败证据，业务页面显示同一订单和库存结果。",
    failure: "断开 API 或篡改前端状态。页面必须暴露不可用，不得继续显示成功。",
    unchanged: "渲染失败不修改任务状态或 ERP 数据，刷新后仍以服务器事实为准。",
    evidence: "DOM 文本、API 响应、工作台数据库和 FlowERP 权威状态四方对账。",
    transfer: "一个监控看板怎样避免把缓存、估算和真实状态混在一起？",
    terms: [["状态投影", "把权威状态转换为可读视图"], ["分面", "工作台与客户产品各自负责一类事实"], ["状态消息", "无需焦点变化也能通知用户"], ["可观测性", "从信号解释系统真实行为"]],
    codeFile: "workbench_web/app.js", codeStart: 97, codeCount: 24,
    codeComment: "页面只从健康检查、课程合同与任务 API 读取事实；接口失败时明确暴露不可用。",
    asset: "docs/courses/assets/web-13-dashboard-live-data.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m workbench.cli serve-workbench",
  },
  15: {
    phase: "产品化与反馈", accent: COLORS.product,
    conflict: "用户说“列表不好用”。原文反馈直接进入自动修复会改错对象和判据。",
    decision: "原始反馈先留存，再由具名人员决定接受、拒绝或补充信息。",
    mechanism: "反馈、审核决定、改进任务和采用证据使用不同身份，历史只追加不改写。",
    wrong: "模型把反馈总结成需求后，覆盖原始反馈并立即修改 blocking 判据。",
    codex: "Codex 可以归类和提出候选改进。具名审核者决定是否晋级，独立任务负责交付。",
    boundary: "接受反馈不等于接受解决方案，也不允许直接降低当前质量门。",
    normal: "真实反馈经审核成为新任务，工作台交付小改进并记录采用结果。",
    failure: "匿名审核或反馈直接改判据。系统拒绝写入，原始文本继续保留。",
    unchanged: "待审反馈不会影响当前 blocking 判决，也不会修改 FlowERP。",
    evidence: "原文、具名决定、改进 Spec、独立 Task、前后证据和采用结论。",
    transfer: "告警、客服工单和代码评审意见，怎样避免被自动摘要篡改原意？",
    terms: [["原始反馈", "未经加工的用户表达"], ["具名审核", "责任人作出的晋级决定"], ["Evolution", "由反馈形成的受控改进记录"], ["采用证据", "用户是否真正受益的后续事实"]],
    codeFile: "workbench/feedback.py", codeStart: 142, codeCount: 25,
    codeComment: "审核函数要求 reviewer 和合法 decision，并拒绝对同一反馈重复裁决。",
    asset: "docs/courses/assets/web-27-delivery-evolution-record.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m workbench.feedback summary",
  },
  16: {
    phase: "产品化与反馈", accent: COLORS.product,
    conflict: "熟悉仓库里的演示全绿，无法证明陌生环境和新需求下仍能完成交付。",
    decision: "在干净环境现场抽取未实现小需求，用完整工作台完成一次受控交付。",
    mechanism: "冷启动验证安装、数据、服务和证据。动态 Eval 验证新需求，不复用预先写好的答案。",
    wrong: "演示已有功能，再用历史截图拼成答辩证据。",
    codex: "Codex 作为工作台中的现场开发伙伴。学生负责范围、授权、复验和答辩。",
    boundary: "随机需求必须受控且此前未实现。答辩不能绕过业务不变量和人工审核。",
    normal: "陌生人按文档启动两套服务，抽题后完成 Spec、前红、Diff、后绿、人审和发布索引。",
    failure: "缺依赖、端口冲突或抽中需求超出范围。系统明确停止并保留恢复建议。",
    unchanged: "冷启动失败不写业务数据。需求未通过人审时不进入发布状态。",
    evidence: "环境清单、随机题身份、动态 Eval、完整轨迹、具名签收和剩余风险。",
    transfer: "离开 FlowERP 后，你能否用同一工作台在陌生仓库交付一个小需求？",
    terms: [["冷启动", "从干净环境恢复可运行系统"], ["动态 Eval", "围绕现场新需求建立的判据"], ["证据索引", "从需求定位到签收的稳定目录"], ["工程答辩", "用可复验事实解释判断和取舍"]],
    codeFile: "deploy/Dockerfile", codeStart: 1, codeCount: 12,
    codeComment: "镜像安装本仓库并声明健康检查。真正的冷启动还要复验数据和双服务边界。",
    asset: "docs/courses/assets/web-12-clean-bootstrap-current.png",
    command: ".\\.venv\\Scripts\\python.exe -X utf8 -m workbench.cli course-status",
  },
};

function plain(text) {
  return text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[`*_]/g, "")
    .replace(/<br\s*\/?\s*>/gi, " ")
    .trim();
}

function safeFileName(number, title) {
  return `L${String(number).padStart(2, "0")}-${plain(title)}`
    .replace(/[<>:"/\\|?*]/g, "-")
    .replace(/\s+/g, "")
    .replace(/-+/g, "-");
}

async function parseBlueprint() {
  const source = await fs.readFile(path.join(workspaceDir, "docs/courses/课程蓝图.md"), "utf8");
  const headers = [...source.matchAll(/^## L(\d{2})｜(.+)$/gm)];
  const result = new Map();
  for (let index = 0; index < headers.length; index += 1) {
    const match = headers[index];
    const number = Number(match[1]);
    const end = headers[index + 1]?.index ?? source.length;
    const section = source.slice(match.index, end);
    const contracts = [...section.matchAll(/^- \*\*(核心内容|演示结果|课内增量|通过标准)\*\*：(.+)$/gm)]
      .map((item) => [item[1], plain(item[2])]);
    const pages = [...section.matchAll(/^\| (\d+) \| ([^|]+) \| (.+) \|$/gm)]
      .map((item) => ({ number: Number(item[1]), time: item[2].trim(), title: plain(item[3]) }));
    const sourcesLine = section.match(/^\*\*一手来源（核验：([^）]+)）\*\*：(.+)$/m);
    const sources = sourcesLine
      ? [...sourcesLine[2].matchAll(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g)].map((item) => ({ label: item[1], url: item[2] }))
      : [];
    if (contracts.length !== 4 || pages.length !== 22 || sources.length < 2) {
      throw new Error(`L${match[1]} blueprint contract is incomplete`);
    }
    result.set(number, { number, title: plain(match[2]), contracts, pages, sources, verifiedAt: sourcesLine[1] });
  }
  return result;
}

function addText(slide, text, position, style = {}, box = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: box.fill ?? "none",
    line: box.line ?? { fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    typeface: style.typeface ?? FONT,
    fontSize: style.fontSize ?? 24,
    bold: style.bold ?? false,
    color: style.color ?? COLORS.ink,
    alignment: style.alignment ?? "left",
    verticalAlignment: style.verticalAlignment ?? "top",
    autoFit: style.autoFit ?? "shrinkText",
    lineSpacing: style.lineSpacing ?? 1.08,
    insets: style.insets ?? { top: 8, right: 10, bottom: 8, left: 10 },
  };
  return shape;
}

function addFrame(slide, lesson, page) {
  slide.background.fill = COLORS.paper;
  addText(slide, `L${String(lesson.number).padStart(2, "0")}  ${lesson.phase}`, { left: 60, top: 26, width: 450, height: 34 }, {
    fontSize: 17, bold: true, color: lesson.accent, verticalAlignment: "middle",
  });
  addText(slide, String(page).padStart(2, "0"), { left: 1175, top: 651, width: 48, height: 28 }, {
    fontSize: 15, bold: true, color: COLORS.muted, alignment: "right",
  });
}

function addTitle(slide, title, lesson) {
  const fontSize = title.length > 26 ? 30 : title.length > 20 ? 34 : 40;
  addText(slide, title, { left: 60, top: 72, width: 1160, height: 76 }, {
    fontSize,
    bold: true,
    color: COLORS.ink,
    verticalAlignment: "middle",
    lineSpacing: 0.96,
  });
  addText(slide, "", { left: 60, top: 151, width: 1160, height: 3 }, {}, {
    fill: lesson.accent,
    line: { fill: "none", width: 0 },
  });
}

function addBody(slide, text, { left = 78, top = 188, width = 1124, height = 420, fontSize = 26, color = COLORS.ink, bold = false, fill = "none", align = "left" } = {}) {
  return addText(slide, text, { left, top, width, height }, {
    fontSize, color, bold, alignment: align, verticalAlignment: "middle", lineSpacing: 1.16,
    insets: { top: 20, right: 24, bottom: 20, left: 24 },
  }, { fill, line: fill === "none" ? { fill: "none", width: 0 } : { fill: COLORS.line, width: 1 } });
}

function addTwoColumns(slide, leftTitle, leftText, rightTitle, rightText, lesson, { leftColor = COLORS.ink, rightColor = COLORS.ink } = {}) {
  addText(slide, leftTitle, { left: 78, top: 190, width: 510, height: 42 }, { fontSize: 22, bold: true, color: lesson.accent });
  addText(slide, leftText, { left: 78, top: 242, width: 510, height: 310 }, { fontSize: 25, color: leftColor, lineSpacing: 1.15 });
  addText(slide, rightTitle, { left: 674, top: 190, width: 510, height: 42 }, { fontSize: 22, bold: true, color: lesson.accent });
  addText(slide, rightText, { left: 674, top: 242, width: 510, height: 310 }, { fontSize: 25, color: rightColor, lineSpacing: 1.15 });
}

function addEditableRelationship(slide, lesson) {
  const base = slide.shapes.add({ geometry: "roundRect", position: { left: 310, top: 468, width: 660, height: 104 }, fill: lesson.accent, line: { fill: lesson.accent, width: 1 } });
  base.text = "个人研发自动化工作台\n写代码与交付的主体";
  base.text.style = { typeface: FONT, fontSize: 28, bold: true, color: COLORS.white, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText", insets: { top: 10, right: 12, bottom: 10, left: 12 } };
  const caseBox = slide.shapes.add({ geometry: "roundRect", position: { left: 410, top: 306, width: 460, height: 82 }, fill: COLORS.white, line: { fill: lesson.accent, width: 2 } });
  caseBox.text = "FlowERP 客户案例\n真实问题与验收场";
  caseBox.text.style = { typeface: FONT, fontSize: 25, bold: true, color: COLORS.ink, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  const codex = slide.shapes.add({ geometry: "roundRect", position: { left: 465, top: 190, width: 350, height: 70 }, fill: "#E8ECEF", line: { fill: COLORS.line, width: 1 } });
  codex.text = "Codex 底座\n生成、执行与工具调用";
  codex.text.style = { typeface: FONT, fontSize: 23, bold: true, color: COLORS.ink, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  slide.shapes.connect(codex, caseBox, { fromSide: "bottom", toSide: "top", kind: "elbow", line: { fill: COLORS.muted, width: 2 }, tail: { type: "arrow", width: "sm", length: "sm" } });
  slide.shapes.connect(caseBox, base, { fromSide: "bottom", toSide: "top", kind: "elbow", line: { fill: lesson.accent, width: 3 }, head: { type: "arrow", width: "sm", length: "sm" }, tail: { type: "arrow", width: "sm", length: "sm" } });
  addText(slide, "工作台组织授权与复验；FlowERP 的事故和反馈迫使工作台升级", { left: 290, top: 590, width: 700, height: 42 }, { fontSize: 19, color: COLORS.muted, alignment: "center", verticalAlignment: "middle" });
}

function addProcess(slide, lesson, steps) {
  const width = 196;
  const gap = 34;
  const left = steps.length < 5 ? (WIDTH - steps.length * width - (steps.length - 1) * gap) / 2 : 65;
  const shapes = [];
  steps.forEach((step, index) => {
    const shape = slide.shapes.add({
      geometry: "roundRect",
      position: { left: left + index * (width + gap), top: 274, width, height: 126 },
      fill: index === steps.length - 1 ? lesson.accent : COLORS.white,
      line: { fill: lesson.accent, width: 2 },
    });
    shape.text = `${index + 1}\n${step}`;
    shape.text.style = { typeface: FONT, fontSize: 22, bold: true, color: index === steps.length - 1 ? COLORS.white : COLORS.ink, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText", insets: { top: 8, right: 8, bottom: 8, left: 8 } };
    shapes.push(shape);
  });
  for (let index = 0; index < shapes.length - 1; index += 1) {
    slide.shapes.connect(shapes[index], shapes[index + 1], { fromSide: "right", toSide: "left", kind: "straight", line: { fill: lesson.accent, width: 2 }, tail: { type: "arrow", width: "sm", length: "sm" } });
  }
}

async function readSnippet(meta) {
  const body = await fs.readFile(path.join(workspaceDir, meta.codeFile), "utf8");
  const lines = body.replace(/\r\n/g, "\n").split("\n");
  if (meta.codeFile.endsWith('example-spec.md')) return lines.filter((line, index) => index < 12).join('\n');
  return lines.slice(meta.codeStart - 1, meta.codeStart - 1 + meta.codeCount)
    .map((line, index) => {
      const expanded = line.replace(/\t/g, "    ");
      const visible = expanded.length > 82 ? `${expanded.slice(0, 81)}…` : expanded;
      return `${String(meta.codeStart + index).padStart(3, " ")}  ${visible}`;
    })
    .join("\n");
}

async function addImageEvidence(slide, meta, lesson) {
  const assetPath = path.join(workspaceDir, meta.asset);
  const bytes = await fs.readFile(assetPath);
  addText(slide, "证据要回答", { left: 64, top: 188, width: 350, height: 38 }, { fontSize: 21, bold: true, color: lesson.accent });
  addText(slide, meta.evidence, { left: 64, top: 234, width: 360, height: 316 }, { fontSize: 23, color: COLORS.ink, lineSpacing: 1.13 });
  slide.images.add({
    blob: bytes,
    contentType: "image/png",
    alt: `L${String(lesson.number).padStart(2, "0")} 仓库运行证据`,
    fit: "contain",
    position: { left: 460, top: 180, width: 744, height: 410 },
  });
  addText(slide, "截图用于定位，签收仍以命令、退出码和权威状态为准", { left: 460, top: 598, width: 744, height: 35 }, { fontSize: 16, color: COLORS.muted, alignment: "center" });
}

function addContract(slide, blueprint, lesson) {
  blueprint.contracts.forEach(([label, value], index) => {
    const top = 185 + index * 104;
    addText(slide, label, { left: 70, top, width: 150, height: 46 }, { fontSize: 21, bold: true, color: lesson.accent, verticalAlignment: "middle" });
    addText(slide, value, { left: 220, top, width: 970, height: 76 }, { fontSize: 22, color: COLORS.ink, verticalAlignment: "middle", lineSpacing: 1.08 });
  });
}

function addTermsTable(slide, meta, lesson) {
  const values = [["术语", "可操作定义"], ...meta.terms];
  const table = slide.tables.add({ rows: values.length, columns: 2, left: 110, top: 190, width: 1060, height: 360, columnWidths: [280, 780], values });
  table.borders.assign({ style: "solid", fill: COLORS.line, width: 1 });
  for (let row = 0; row < values.length; row += 1) {
    for (let column = 0; column < 2; column += 1) {
      const cell = table.getCell(row, column);
      cell.fill = row === 0 ? lesson.accent : row % 2 ? COLORS.white : "#EEF1F4";
      cell.text.style = { typeface: FONT, fontSize: row === 0 ? 22 : 21, bold: row === 0 || column === 0, color: row === 0 ? COLORS.white : COLORS.ink, autoFit: "shrinkText", verticalAlignment: "middle" };
    }
  }
}

function addNotes(slide, blueprint, meta, page) {
  const sources = blueprint.sources.map((source) => `${source.label}: ${source.url}`).join("\n");
  slide.speakerNotes.textFrame.setText([
    `课程：Codex AI 工程交付行动营`,
    `课次：L${String(blueprint.number).padStart(2, "0")} ${blueprint.title}`,
    `时间点：${page.time}`,
    `本页目的：${page.title}`,
    `讲解边界：${meta.boundary}`,
    `讲义：docs/courses/L${String(blueprint.number).padStart(2, "0")}-*.md`,
    `一手来源核验日期：${blueprint.verifiedAt}`,
    sources,
  ].join("\n"));
}

async function buildDeck(blueprint) {
  const meta = LESSONS[blueprint.number];
  if (!meta) throw new Error(`Missing lesson metadata for ${blueprint.number}`);
  const presentation = Presentation.create({ slideSize: { width: WIDTH, height: HEIGHT } });
  const snippet = blueprint.number === 4 ? '' : await readSnippet(meta);

  for (const page of blueprint.pages) {
    let notesTitle = page.title;
    const slide = presentation.slides.add();
    addFrame(slide, { ...blueprint, ...meta }, page.number);

    if (page.number === 1) {
      slide.background.fill = meta.accent;
      addText(slide, `L${String(blueprint.number).padStart(2, "0")}`, { left: 70, top: 58, width: 210, height: 80 }, { fontSize: 58, bold: true, color: COLORS.white, verticalAlignment: "middle" });
      addText(slide, blueprint.title, { left: 70, top: 170, width: 1050, height: 205 }, { fontSize: blueprint.title.length > 24 ? 46 : 54, bold: true, color: COLORS.white, verticalAlignment: "middle", lineSpacing: 0.95 });
      addText(slide, `${meta.phase}　工作台是主体，FlowERP 是验证场，Codex 是底座`, { left: 74, top: 438, width: 1080, height: 70 }, { fontSize: 23, color: "#EDF5F6", verticalAlignment: "middle" });
      addText(slide, "Codex AI 工程交付行动营", { left: 74, top: 614, width: 720, height: 38 }, { fontSize: 18, color: "#DDE8EA" });
    } else {
      if (blueprint.number === 4) {
        const content = l04Pages[page.number];
        if (!content) throw new Error(`L04 missing authored page ${page.number}`);
        addTitle(slide, content.title, {...blueprint,...meta});
        if (content.kind === 'contract') addContract(slide, blueprint, {...blueprint,...meta});
        else if (content.kind === 'terms') addTermsTable(slide, meta, {...blueprint,...meta});
        else if (content.kind === 'code') {
          addText(slide, content.code, {left:75,top:205,width:1130,height:325}, {typeface:CODE_FONT,fontSize:26,autoFit:'none'}, {fill:COLORS.white});
          addText(slide, content.note, {left:75,top:565,width:1130,height:80}, {fontSize:23,color:COLORS.muted});
        } else if (content.flow) {
          addProcess(slide, {...blueprint,...meta}, content.flow);
          addText(slide, content.note, {left:105,top:465,width:1070,height:130}, {fontSize:25,alignment:'center'});
        } else addTwoColumns(slide, ...content.left, ...content.right, {...blueprint,...meta});
        addNotes(slide, blueprint, meta, {...page,title:content.title});
        continue;
      }
      const beginnerTitle = blueprint.number === 3 ? {2:'本讲怎样与 Codex 协作',3:'“导出库存”还缺哪些约定',4:'开始实现前，先确认验收标准',5:'两种写需求的方式',6:'本讲交付与通过标准',7:'先认识四个词',8:'六段 Spec：把约定写完整',9:'解析器怎样检查合同',10:'常见误区：只写技术方案',11:'Codex 与人的分工',12:'本讲完成后，下一讲怎样接手',13:'一份最短的课堂合同',14:'解析通过，是否就能开始实现？',15:'运行最小解析示例',16:'完整输入与缺段输入的结果',17:'练习：让验收标准更明确',18:'正常路径：先约定，再检查结构',19:'常见误区：把后续需求一起塞进来',20:'检查失败后，保留什么',21:'迁移练习：换一个需求',22:'课后提交与复验入口'}[page.number] : null;
      addTitle(slide, beginnerTitle || page.title, { ...blueprint, ...meta });
      notesTitle = beginnerTitle || page.title;
      switch (page.number) {
        case 2:
          if (blueprint.number === 3) {
            addProcess(slide, {...blueprint,...meta}, ['提出问题','Codex 追问','学生定范围','实现解析器','学生复验']);
            addText(slide, '本讲由学生直接监督 Codex 建造工作台能力；FlowERP 业务代码保持不变。', {left:120,top:465,width:1040,height:100}, {fontSize:25,alignment:'center'});
            break;
          }
          addEditableRelationship(slide, { ...blueprint, ...meta });
          break;
        case 3:
          addBody(slide, meta.conflict, { left: 130, top: 215, width: 1020, height: 280, fontSize: 34, fill: COLORS.white, align: "center" });
          addText(slide, "先写下你的判断，再继续下一页", { left: 350, top: 535, width: 580, height: 45 }, { fontSize: 20, bold: true, color: meta.accent, alignment: "center" });
          break;
        case 4:
          addTwoColumns(slide, "批准条件", meta.decision, "必须拒绝", meta.wrong, { ...blueprint, ...meta }, { rightColor: COLORS.red });
          break;
        case 5:
          if (blueprint.number === 3) {
            addTwoColumns(slide, '只有愿望', '导出要好用。\n\n不同的人可能理解成不同功能。', '有可核对的约定', '空库存也输出列名。\n\n拿一个空库存，就能检查结果。', {...blueprint,...meta});
            break;
          }
          addBody(slide, `选择“${meta.decision}”时，你承担的是可解释的范围风险。\n\n选择“${meta.wrong}”时，失败会失去稳定归因。`, { left: 105, top: 205, width: 1070, height: 360, fontSize: 28, fill: COLORS.white });
          break;
        case 6:
          addContract(slide, blueprint, { ...blueprint, ...meta });
          break;
        case 7:
          addTermsTable(slide, meta, { ...blueprint, ...meta });
          break;
        case 8:
          addBody(slide, meta.mechanism, { left: 110, top: 215, width: 1060, height: 310, fontSize: 31, fill: COLORS.white, align: "center" });
          break;
        case 9:
          if (blueprint.number === 3) {
            addProcess(slide, {...blueprint,...meta}, ['读入文本','找六段标题','检查顺序','检查空内容','返回结果']);
            addText(slide, '少段、重复、乱序或空内容：返回明确错误，暂不进入业务实现。', {left:120,top:465,width:1040,height:100}, {fontSize:25,alignment:'center'});
            break;
          }
          addProcess(slide, { ...blueprint, ...meta }, ["现场问题", "冻结合同", "受控执行", "统一复验", "证据回流"]);
          addText(slide, meta.mechanism, { left: 160, top: 455, width: 960, height: 100 }, { fontSize: 21, color: COLORS.muted, alignment: "center", verticalAlignment: "middle" });
          break;
        case 10:
          addBody(slide, `看似合理的错误方案\n\n${meta.wrong}`, { left: 130, top: 210, width: 1020, height: 330, fontSize: 31, color: COLORS.red, fill: "#FFF5F4", align: "center" });
          break;
        case 11:
          addTwoColumns(slide, "Codex 可以承担", meta.codex, "人和工作台保留", meta.boundary, { ...blueprint, ...meta });
          break;
        case 12:
          if (blueprint.number === 3) {
            addTwoColumns(slide, 'L03 留下', '具体需求合同\n最小结构解析器\n正常与失败的检查记录', 'L04 接手', '先独立验收工作台 V0，\n再授权它组织库存导出。\n\n本讲还不修改库存服务。', {...blueprint,...meta});
            break;
          }
          addProcess(slide, { ...blueprint, ...meta }, ["学生判断", "Codex 协作", "工作台授权", "FlowERP 验真", "具名签收"]);
          addText(slide, "工作台控制写集、预算、复验和审核。Codex 不拥有最终签收权。", { left: 170, top: 455, width: 940, height: 90 }, { fontSize: 22, color: COLORS.muted, alignment: "center", verticalAlignment: "middle" });
          break;
        case 13:
          if (blueprint.number === 3) {
            addText(slide, '先看这三件事', {left:70,top:195,width:370,height:44}, {fontSize:28,bold:true,color:meta.accent});
            addText(slide, '来源：谁提出了问题\n\n非目标：哪些事本次不做\n\n验收用例：怎样检查结果', {left:70,top:254,width:370,height:260}, {fontSize:25,autoFit:'none'});
            addText(slide, snippet, {left:460,top:185,width:745,height:420}, {typeface:CODE_FONT,fontSize:24,autoFit:'none',lineSpacing:1.03}, {fill:COLORS.white,line:{fill:COLORS.line,width:1}});
            addText(slide, '课堂假设示例 · 结构完整不等于业务细节已充分', {left:70,top:617,width:1100,height:38}, {fontSize:20,color:COLORS.muted});
            break;
          }
          addText(slide, meta.codeFile, { left: 74, top: 175, width: 900, height: 34 }, { fontSize: 18, bold: true, color: meta.accent });
          addText(slide, snippet, { left: 74, top: 214, width: 1132, height: 350 }, { typeface: CODE_FONT, fontSize: 17, color: "#E7EEF2", lineSpacing: 1.0, autoFit: "shrinkText", insets: { top: 16, right: 18, bottom: 16, left: 18 } }, { fill: COLORS.code, line: { fill: COLORS.code, width: 1 } });
          addText(slide, meta.codeComment, { left: 100, top: 583, width: 1080, height: 56 }, { fontSize: 19, color: COLORS.muted, alignment: "center", verticalAlignment: "middle" });
          break;
        case 14:
          if (blueprint.number === 3) {
            addTwoColumns(slide, '机器能检查', '六段齐全、顺序正确、内容非空。\n\n少了“完成定义”会被拒绝。', '仍要由人判断', '“可用量”怎么算？\nSKU 重复怎么办？\n谁负责最终验收？', {...blueprint,...meta});
            break;
          }
          addTwoColumns(slide, "代码必须守住", meta.mechanism, "代码不能替代", meta.boundary, { ...blueprint, ...meta });
          break;
        case 15:
          if (blueprint.number === 3) {
            addText(slide, 'Path：找到文本文件\n\nparse_spec：检查六段结构\n\nprint：显示提取出的目标', {left:70,top:220,width:390,height:330}, {fontSize:25});
            addText(slide, 'from pathlib import Path\nfrom workbench.spec import parse_spec\n\npath = Path(\n    "docs/courses/labs/L03/example-spec.md"\n)\nspec = parse_spec(path.read_text(encoding="utf-8"))\nprint(spec.goal)', {left:470,top:210,width:740,height:360}, {typeface:CODE_FONT,fontSize:22,autoFit:'none'}, {fill:COLORS.white});
            addText(slide, '在仓库根目录、项目虚拟环境中运行；example-spec.md 就是前面的课堂示例。', {left:70,top:603,width:1140,height:52}, {fontSize:20,color:COLORS.muted});
            break;
          }
          addBody(slide, meta.evidence, { left: 110, top: 220, width: 1060, height: 300, fontSize: 30, fill: COLORS.white, align: "center" });
          addText(slide, "证据必须绑定稳定身份，能够由其他人重新取得", { left: 210, top: 540, width: 860, height: 46 }, { fontSize: 21, bold: true, color: meta.accent, alignment: "center" });
          break;
        case 16:
          if (blueprint.number === 3) {
            addTwoColumns(slide, '完整示例：显示目标', '导出包含 SKU 与可用量的 CSV。', '删去“完成定义”', 'Spec 缺少必要章节：完成定义\n\n大白话：还没有约定怎样才算完成。', {...blueprint,...meta});
            break;
          }
          await addImageEvidence(slide, meta, { ...blueprint, ...meta });
          break;
        case 17:
          if (blueprint.number === 3) {
            addBody(slide, '请补全：“导出结果要正确”\n\n提示：列名是什么？空库存怎样输出？\n\n先写你的答案，再与同伴互相找歧义。', {left:110,top:210,width:1060,height:340,fontSize:30,fill:COLORS.white});
            break;
          }
          addBody(slide, `暂停 30 秒\n\n${meta.transfer}`, { left: 130, top: 210, width: 1020, height: 330, fontSize: 32, fill: COLORS.white, align: "center" });
          break;
        case 18:
          addBody(slide, `正常路径\n\n${meta.normal}`, { left: 110, top: 210, width: 1060, height: 330, fontSize: 29, color: COLORS.green, fill: "#F0F8F4", align: "center" });
          break;
        case 19:
          addBody(slide, `失败路径\n\n${meta.failure}`, { left: 110, top: 210, width: 1060, height: 330, fontSize: 29, color: COLORS.red, fill: "#FFF5F4", align: "center" });
          break;
        case 20:
          addTwoColumns(slide, "失败后必须保持", meta.unchanged, "可以新增的事实", "失败报告、退出码、责任人和下一步。不得删掉首次失败。", { ...blueprint, ...meta }, { leftColor: COLORS.red, rightColor: COLORS.green });
          break;
        case 21:
          addBody(slide, `${meta.transfer}\n\n请写出：权威状态在哪里；失败后什么不能变；陌生人用哪条命令复验。`, { left: 105, top: 205, width: 1070, height: 360, fontSize: 28, fill: COLORS.white, align: "center" });
          break;
        case 22:
          addText(slide, "课后行动", { left: 100, top: 182, width: 300, height: 45 }, { fontSize: 23, bold: true, color: meta.accent });
          addBody(slide, `${meta.command}\n\n任务卡：${[1, 2].includes(blueprint.number) ? `docs/courses/L${String(blueprint.number).padStart(2, "0")}/行动卡.md` : `docs/courses/tasks/L${String(blueprint.number).padStart(2, "0")}-*.md`}\n实验：${[1, 2].includes(blueprint.number) ? `docs/courses/L${String(blueprint.number).padStart(2, "0")}/` : `docs/courses/labs/L${String(blueprint.number).padStart(2, "0")}/`}\n\n交付首次判断、失败证据、范围内 Diff、复验结果和第二次签字。`, { left: 100, top: 235, width: 1080, height: 330, fontSize: 24, fill: COLORS.white });
          break;
        default:
          throw new Error(`Unhandled page ${page.number}`);
      }
    }
    addNotes(slide, blueprint, meta, {...page, title:notesTitle});
  }
  return presentation;
}

async function main() {
  await fs.mkdir(tmpDir, { recursive: true });
  await fs.mkdir(outputDir, { recursive: true });
  const blueprints = await parseBlueprint();
  const lessonArg = process.argv.find((value) => value.startsWith("--lesson="));
  const onlyLesson = lessonArg ? Number(lessonArg.split("=", 2)[1]) : null;
  const pilot = process.argv.includes("--pilot");
  const selected = [...blueprints.values()].filter((item) => onlyLesson === null || item.number === onlyLesson);
  if (!selected.length) throw new Error("No lesson selected");

  for (const blueprint of selected) {
    const presentation = await buildDeck(blueprint);
    const basename = `${safeFileName(blueprint.number, blueprint.title)}.pptx`;
    const pilotDir = path.join(workspaceDir, ".tmp", "course-slides-pilot");
    if (pilot) await fs.mkdir(pilotDir, { recursive: true });
    const lessonOutputDir = [1, 2].includes(blueprint.number) && path.resolve(outputDir) === path.join(workspaceDir, "docs", "courses", "slides")
      ? path.join(workspaceDir, "docs", "courses", `L${String(blueprint.number).padStart(2, "0")}`, "slides") : outputDir;
    await fs.mkdir(lessonOutputDir, { recursive: true });
    const finalPath = pilot ? path.join(pilotDir, `pilot-${basename}`) : path.join(lessonOutputDir, basename);
    const candidatePath = path.join(tmpDir, `candidate-L${String(blueprint.number).padStart(2, "0")}.pptx`);
    const receiptPath = path.join(tmpDir, `L${String(blueprint.number).padStart(2, "0")}.validation.json`);
    await (await PresentationFile.exportPptx(presentation)).save(candidatePath);
    await finalizePresentation({
      explicitTotalSlideCount: 22,
      requiredNativeTableOwnerSlides: [7],
      requiredNativeChartOwnerSlides: [],
      workspaceDir,
      candidatePath,
      finalPath,
      pythonExecutable: runtimePython,
      integrityValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_package_integrity.py"),
      layoutValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_layout_geometry.py"),
      layoutArgs: [
        "--expected-slide-size-emu", EXPECTED_EMU,
        "--validate-bullet-geometry",
        "--validate-heading-fit",
        "--require-native-table-slide", "7",
      ],
      requiredNativeTableOwnerSlides: [7],
      fontPolicy: {
        basis: "design",
        families: [FONT, CODE_FONT],
        scriptFonts: { ea: FONT },
      },
      verifyArtifactToolImport: true,
      receiptPath,
    });
    if (process.argv.includes('--render')) {
      const renderDir = path.join(tmpDir, `renders-L${String(blueprint.number).padStart(2,'0')}`);
      await fs.mkdir(renderDir, {recursive:true});
      for (let index=0; index<presentation.slides.items.length; index++) {
        const rendered = await presentation.export({slide:presentation.slides.items[index],format:'png',scale:1});
        await fs.writeFile(path.join(renderDir, `${String(index+1).padStart(2,'0')}.png`), new Uint8Array(await rendered.arrayBuffer()));
      }
    }
    process.stdout.write(`${finalPath}\n`);
  }
}

await main();
