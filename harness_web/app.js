"use strict";

const $ = (s) => document.querySelector(s);
const ACTOR_KEY = "harness-operator";
const ACTOR_RECENTS_KEY = "harness-operator-recents";
const WORKSPACE_KEY = "harness-workspace";
const PROFILE_KEY = "harness-profile";
const FILTER_KEY = "harness-session-filter";
const TRAJ_ACTOR_KEY = "harness-traj-actor";
const TRAJ_SOURCE_KEY = "harness-traj-source";
const RUNTIME_FILTERS = ["user", "agent", "tool"];
const WORKBENCH_VIEW_KEY = "harness-workbench-view";
const POLL_MS = 1400;
const COURSE_STATUS_TIMEOUT_MS = 10000;
const BUSY = new Set(["queued", "spec_ready", "executing", "evaluating", "rework"]);
const TERMINAL = new Set(["completed", "failed", "dead_letter"]);
const DEFAULT_EMPLOYEES = [
  { id: "agent:spec", display_name: "产品", duty: "澄清业务信号、兼 PM 范围与验收点、写 Spec" },
  { id: "agent:coder", display_name: "开发", duty: "受控改代码并跑 Eval" },
  { id: "agent:reviewer", display_name: "测试", duty: "挑刺；不能代替老板/Leader 终审" },
];
const DUTY_BY_STATUS = {
  queued: "agent:spec",
  spec_ready: "agent:spec",
  executing: "agent:coder",
  rework: "agent:coder",
  evaluating: "agent:coder",
  review: "agent:reviewer",
  completed: "agent:reviewer",
  failed: "agent:reviewer",
  dead_letter: "agent:reviewer",
};
const DUTY_BY_RAIL = {
  request: "agent:spec",
  spec: "agent:spec",
  code: "agent:coder",
  eval: "agent:coder",
  human: "agent:reviewer",
};

/** Full delivery flow — each node is expandable and model-visible */
const RAIL = [
  {
    id: "request",
    label: "需求",
    role: "业务",
    match: ["queued"],
    title: "① 接到需求",
    summary: "自然语言需求入队，准备生成任务 Spec。",
    model_hint: "确认需求边界与 AGENTS.md；不要跳过 Spec。",
    actions: ["读取需求", "核对业务规则"],
  },
  {
    id: "spec",
    label: "规格",
    role: "产品",
    match: ["spec_ready"],
    title: "② 写好规格",
    summary: "任务级 Spec 已生成，可进入受控改代码或验证。产品兼 PM：框定范围与验收点。",
    model_hint: "先读 Spec；写入必须落在 write_scope。",
    actions: ["生成 Spec", "对齐验收点"],
  },
  {
    id: "code",
    label: "改代码",
    role: "开发",
    match: ["executing", "rework"],
    title: "③ 改 FlowERP 代码",
    summary: "在 flowerp/tests 等范围内修改产品代码。",
    model_hint: "仅改 write_scope；保持库存非负、入库幂等、订单状态机、采购审批。",
    actions: ["受控改代码", "返工修复"],
  },
  {
    id: "eval",
    label: "验收",
    role: "测试",
    match: ["evaluating"],
    title: "④ 跑阻断级 Eval",
    summary: "用 Eval 验证 FlowERP 业务规则未被破坏。",
    model_hint: "根据失败用例定位；不得把失败伪装成成功。",
    actions: ["blocking Eval", "收集失败证据"],
  },
  {
    id: "human",
    label: "老板终审",
    role: "Leader",
    match: ["review", "completed", "failed", "dead_letter"],
    title: "⑤ 老板终审",
    summary: "自动化停工；测试可挑刺，只有老板/Leader 能通过/驳回。",
    model_hint: "review 停自动改代码；等待老板终审，不要假装已 approve。",
    actions: ["测试挑刺", "老板通过/驳回", "复制会话再试"],
  },
];

const STEP_HELP = {
  queued: { title: "已接到需求", blurb: "FlowERP 改动请求已入队，准备生成任务 Spec。" },
  spec_ready: { title: "规格已写好", blurb: "自然语言已整理成任务 Spec，下一步改代码。" },
  executing: { title: "正在改 FlowERP", blurb: "在 flowerp/tests 等写入范围内修改产品代码。" },
  evaluating: { title: "正在跑验收", blurb: "阻断级 Eval 检查库存/订单/采购等规则是否被破坏。" },
  review: { title: "等老板终审", blurb: "自动化已停工。测试意见可见；请老板/Leader 具名通过或驳回。" },
  rework: { title: "需要返工", blurb: "验收未过或被驳回，可再执行。" },
  completed: { title: "交付已接受", blurb: "你已具名接受这次 FlowERP 增量。" },
  failed: { title: "交付失败", blurb: "本轮未完成，可看过程记录或复制会话再试。" },
  dead_letter: { title: "进入死信", blurb: "任务不可自动继续，需人工介入。" },
};

const STATUS_LABEL = {
  idle: "空闲", queued: "排队中", spec_ready: "规格就绪", executing: "改代码中",
  evaluating: "验收中", rework: "返工",   review: "待老板终审", completed: "已完成",
  failed: "失败", dead_letter: "死信", active: "进行中", paused: "已暂停", closed: "已结束",
};

const ROLE_LABEL = { user: "你", assistant: "助手", system: "系统", tool: "工具" };

let state = {
  projects: [], sessions: [], plugins: [], profiles: [], tools: [],
  capabilities: null, selectedSessionId: null, messages: [],
  session: null, task: null, graph: null, agent: null, events: [],
  selectedEvent: null, view: "chat", sending: false, pollTimer: null,
  detailsOpen: false, sidebarCollapsed: false, detailsMode: "graph",
  taskStatusBySession: {},
  taskById: {},
  deliveryViewByTask: {},
  employees: DEFAULT_EMPLOYEES.slice(),
  expandedRailId: null,
  railAutoOpened: false,
  sessionFilter: localStorage.getItem(FILTER_KEY) || "all",
  trajActorFilter: normalizeRuntimeFilter(localStorage.getItem(TRAJ_ACTOR_KEY) || "all"),
  trajSourceFilter: localStorage.getItem(TRAJ_SOURCE_KEY) || "all",
  dumpConfig: null, composition: null,
  workbenchView: localStorage.getItem(WORKBENCH_VIEW_KEY) || "dashboard",
  health: null, courseStatus: null, pluginRuntime: null, pluginEvents: [],
  feedbackSummary: null, evolutionSummary: null, tasks: [], initiatives: [],
  courseLessons: [], flowerpStatus: null, verifyingEval: false,
  showAllMessages: false, teamExpanded: false,
  dutyFocus: "",
};
let courseStatusController = null;
let courseStatusRequestId = 0;
state.dutyFocus = RUNTIME_FILTERS.includes(state.trajActorFilter) ? state.trajActorFilter : "";

function normalizeRuntimeFilter(value) {
  const raw = String(value || "all").trim();
  if (raw === "all") return "all";
  if (raw === "user" || raw === "boss") return "user";
  if (raw === "tool") return "tool";
  if (raw === "agent" || raw.startsWith("agent:")) return "agent";
  return "all";
}

function runtimeFilterLabel(id) {
  return { user: "你（User）", agent: "Agent", tool: "工具", all: "全部" }[id] || id;
}

function currentRailRole() {
  const step = RAIL[railIndex(currentStatus())];
  return (step && step.role) || "—";
}

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 2800);
}

function actor() {
  let value = ($("#actor").value || "").trim();
  if (!value) {
    value = "boss";
    $("#actor").value = value;
  }
  if (value.startsWith("agent:")) {
    toast("老板身份不能使用自动化职责 actor");
    value = "boss";
    $("#actor").value = value;
  }
  localStorage.setItem(ACTOR_KEY, value);
  return value;
}

function rememberActor(name) {
  const value = String(name || "").trim();
  if (!value || value.startsWith("agent:")) return;
  let recents = [];
  try { recents = JSON.parse(localStorage.getItem(ACTOR_RECENTS_KEY) || "[]"); } catch (err) { recents = []; }
  if (!Array.isArray(recents)) recents = [];
  localStorage.setItem(
    ACTOR_RECENTS_KEY,
    JSON.stringify([value, ...recents.filter((item) => item !== value)].slice(0, 8)),
  );
}

function knownBossActors() {
  let recents = [];
  try { recents = JSON.parse(localStorage.getItem(ACTOR_RECENTS_KEY) || "[]"); } catch (err) { recents = []; }
  const fromEvents = (state.events || []).map((event) => event.actor).filter(isBossActor);
  const fromSessions = (state.sessions || []).flatMap((session) => [session.created_by, session.actor]);
  const current = (($("#actor") && $("#actor").value) || localStorage.getItem(ACTOR_KEY) || "boss").trim();
  return [...new Set(["boss", "operator-a", current, ...recents, ...fromEvents, ...fromSessions]
    .map((name) => String(name || "").trim())
    .filter((name) => name && !name.startsWith("agent:")))];
}

function renderActorOptions() {
  const list = $("#actor-options");
  if (list) {
    list.innerHTML = knownBossActors().map((name) => `<option value="${esc(name)}"></option>`).join("");
  }
  const recents = $("#actor-recents");
  if (!recents) return;
  const current = ($("#actor").value || "").trim() || "boss";
  const names = knownBossActors().filter((name) => name !== current).slice(0, 4);
  recents.hidden = !names.length;
  recents.innerHTML = names.map((name) =>
    `<button type="button" class="chip-mini" data-actor="${esc(name)}" title="切换终审身份">${esc(name)}</button>`
  ).join("");
}

function renderActorHint() {
  const node = $("#actor-hint");
  if (node) node.textContent = `写操作与终审以「${($("#actor").value || "").trim() || "boss"}」入账`;
}

function commitActor(name, { announce = false } = {}) {
  const previous = localStorage.getItem(ACTOR_KEY) || "";
  const value = setActor(name);
  rememberActor(value);
  renderActorOptions();
  renderActorHint();
  renderCollabBar();
  renderReviewBanner();
  syncComposerEnabled();
  if (announce && previous !== value) {
    toast(`终审身份已切换为 ${value}。后续写操作都以这个名字入账。`);
  }
  return value;
}

function setActor(name) {
  const value = String(name || "").trim() || "boss";
  $("#actor").value = value.startsWith("agent:") ? "boss" : value;
  localStorage.setItem(ACTOR_KEY, $("#actor").value);
  return $("#actor").value;
}

function focusDuty(empId) {
  const id = normalizeRuntimeFilter(empId);
  if (RUNTIME_FILTERS.includes(id)) {
    state.dutyFocus = id;
    state.trajActorFilter = id;
  } else {
    state.dutyFocus = "";
    state.trajActorFilter = "all";
  }
  localStorage.setItem(TRAJ_ACTOR_KEY, state.trajActorFilter);
  state.expandedRailId = null;
  setWorkbenchView("delivery");
  setView("trajectory");
  renderEmployeeChips();
  renderTrajectory();
  renderCollabBar();
  toast(`已切换到「${runtimeFilterLabel(state.trajActorFilter)}」视角。这是 Harness 运行时筛选，不是独立登录账号。`);
}

function employeeById(id) {
  return (state.employees || []).find((e) => e.id === id)
    || DEFAULT_EMPLOYEES.find((e) => e.id === id)
    || { id, display_name: id, duty: "" };
}

function llmProvider() {
  return String((state.capabilities && state.capabilities.providers && state.capabilities.providers.llm) || "unknown");
}

function hasModelBackedAgentRuntime() {
  const provider = llmProvider().toLowerCase();
  return provider !== "template" && provider !== "off" && provider !== "unknown";
}

function roleRuntimeLabel() {
  return hasModelBackedAgentRuntime() ? `Agent 执行 · ${llmProvider()}` : "职责投影 · template";
}

function roleRuntimeNote() {
  return hasModelBackedAgentRuntime()
    ? `当前 LLM Provider=${llmProvider()}；角色事件由模型执行，但仍不是独立登录账号。`
    : "当前 LLM Provider=template；User / Agent / 工具是 Session 事件视角，不是三个独立登录。交付轨道仍是业务→产品→开发→测试→Leader。";
}

function dutyForStatus(status) {
  return DUTY_BY_STATUS[status] || "agent:coder";
}

function latestCritique() {
  const events = state.events || [];
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].kind === "agent/critique") return events[i].payload || {};
  }
  return null;
}

function actorActivity() {
  const counts = { user: 0, agent: 0, tool: 0, other: 0 };
  (state.events || []).forEach((event) => {
    const kind = String(event.kind || "");
    if (kind.startsWith("tool/") || kind.startsWith("tools/") || kind.startsWith("approval/")) {
      counts.tool += 1;
    }
    const name = String(event.actor || "");
    if (!name) return;
    if (name.startsWith("agent:")) counts.agent += 1;
    else if (isBossActor(name)) counts.user += 1;
    else counts.other += 1;
  });
  return counts;
}

function isBossActor(name) {
  const value = String(name || "");
  return value && !value.startsWith("agent:") && !["harness", "system", "llm", "automation", "agent"].includes(value);
}

function eventMatchesTrajFilter(event) {
  const filter = state.trajActorFilter || "all";
  if (filter !== "all") {
    const name = String(event.actor || "");
    const kind = String(event.kind || "");
    if (filter === "user") {
      if (!isBossActor(name)) return false;
    } else if (filter === "agent") {
      if (!name.startsWith("agent:")) return false;
    } else if (filter === "tool") {
      if (!(kind.startsWith("tool/") || kind.startsWith("tools/") || kind.startsWith("approval/"))) return false;
    } else if (name !== filter) {
      return false;
    }
  }
  const source = state.trajSourceFilter || "all";
  if (source === "all") return true;
  const kind = String(event.kind || "");
  if (source === "turn") return kind.startsWith("turn/") || kind.startsWith("step/");
  if (source === "tool") return kind.startsWith("tool/") || kind.startsWith("tools/") || kind.startsWith("approval/");
  if (source === "llm") {
    return kind.startsWith("assistant/") || kind.startsWith("agent/") || kind.startsWith("llm/")
      || kind === "user/message";
  }
  if (source === "task") return kind.startsWith("task/") || kind.startsWith("pipeline/") || kind.startsWith("spec/") || kind.startsWith("eval/");
  return true;
}

function requireSession() {
  if (!state.selectedSessionId || !state.session) {
    toast("请先点「＋」新建会话，或直接开始交付自动创建");
    return null;
  }
  return state.selectedSessionId;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || body.error || "请求失败");
  return body;
}

function key(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function headers(prefix) {
  return {
    "Content-Type": "application/json",
    "X-Workbench-Actor": actor(),
    "Idempotency-Key": key(prefix),
  };
}

function shortTitle(text) {
  const value = String(text || "未命名").trim().replace(/\s+/g, " ");
  return value.length > 36 ? `${value.slice(0, 36)}…` : value;
}

function statusLabel(value) {
  return STATUS_LABEL[value] || value || "空闲";
}

function stepHelp(id) {
  return STEP_HELP[id] || { title: statusLabel(id), blurb: "" };
}

function roleLabel(role) {
  return ROLE_LABEL[role] || role;
}

function currentProjectId() {
  return $("#project-id").value || localStorage.getItem(WORKSPACE_KEY) || "";
}

function currentProfileId() {
  return $("#profile-id").value || localStorage.getItem(PROFILE_KEY) || "PROFILE-DEFAULT";
}

function taskBusy() {
  return Boolean(state.task && BUSY.has(state.task.status));
}

function sessionWritable() {
  const status = state.session && state.session.status;
  return !status || status === "active";
}

function currentStatus() {
  return (state.graph && state.graph.current)
    || (state.task && state.task.status)
    || null;
}

function railIndex(status) {
  if (!status) return -1;
  for (let i = 0; i < RAIL.length; i += 1) {
    if (RAIL[i].match.includes(status)) return i;
  }
  if (BUSY.has(status)) return 2;
  return -1;
}

function syncShell() {
  $("#app").classList.toggle("details-open", state.detailsOpen);
  $("#app").classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
  $("#details").hidden = !state.detailsOpen;
}

function syncRoleChips() {
  const focus = state.dutyFocus || "";
  document.querySelectorAll("#employee-chips [data-emp]").forEach((el) => {
    el.classList.toggle("focus", Boolean(focus) && el.dataset.emp === focus);
    el.classList.toggle("active", el.dataset.emp === focus);
  });
}

function syncComposerEnabled() {
  const ready = Boolean(currentProjectId());
  const blocked = state.sending || taskBusy() || (state.session && !sessionWritable());
  $("#send").disabled = true;
  let hint = "先选择左侧工作区";
  if (ready && state.session && state.session.status === "paused") hint = "会话已暂停，在「更多」里点继续";
  else if (ready && state.session && state.session.status === "closed") hint = "会话已结束，请复制会话或新建";
  else if (ready && taskBusy()) {
    hint = `交付角色「${currentRailRole()}」处理中…`;
  } else if (ready && state.task && state.task.status === "review") {
    hint = "自动化已停工。请老板终审通过或驳回";
  } else if (ready && state.task && (state.task.status === "dead_letter" || state.task.status === "failed")) {
    hint = "自动化已停。可复制会话再试，并勾选「改代码」";
  } else if (ready) hint = "新需求必须先通过事项与决策门";
  $("#composer-hint").textContent = hint;

  const project = state.projects.find((p) => p.id === currentProjectId());
  $("#workspace-label").textContent = project ? project.name : "未选择";
  $("#pick-workspace").classList.toggle("ready", Boolean(project));
  $("#composer-card").classList.toggle("locked", !ready);
  $("#session-actions").hidden = !state.selectedSessionId;
  syncRoleChips();
  if (state.session) {
    $("#btn-pause").disabled = state.session.status !== "active";
    $("#btn-resume").disabled = state.session.status !== "paused";
    $("#btn-close").disabled = state.session.status === "closed";
  }
}

function setView(view) {
  const next = view === "trajectory" ? "trajectory" : "chat";
  const switched = state.view !== next;
  state.view = next;
  if (switched) {
    state.expandedRailId = null;
  }
  const delivery = $("#delivery-view");
  if (delivery) {
    delivery.classList.toggle("tab-chat", next === "chat");
    delivery.classList.toggle("tab-trajectory", next === "trajectory");
  }
  document.querySelectorAll(".view-tabs [data-view]").forEach((tab) => {
    const on = tab.dataset.view === next;
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
  });
  $("#view-chat").hidden = next !== "chat";
  $("#view-trajectory").hidden = next !== "trajectory";
  if (switched) {
    renderRail();
    if (next === "trajectory") renderTrajectory();
    const scroller = document.querySelector("[data-conversation-scroll]");
    if (scroller) scroller.scrollTop = 0;
  }
}

function messageBody(message) {
  if (typeof message.content === "string") return message.content;
  if (message.tool_call) {
    return `${message.tool_call.name || "?"}\n${JSON.stringify(message.tool_call.arguments || {}, null, 2)}`;
  }
  if (message.tools) return JSON.stringify(message.tools, null, 2);
  return JSON.stringify(message.content || message, null, 2);
}

function eventPreview(event) {
  const payload = event.payload || {};
  if (typeof payload === "string") return payload;
  return payload.tool_id || payload.content || payload.request || payload.reason
    || payload.decision || payload.message || JSON.stringify(payload);
}

function kindLabel(kind) {
  const map = {
    "user/message": "你的需求",
    "assistant/message": "助手回复",
    "tool/call": "调用工具",
    "tool/result": "工具结果",
    "agent/plan": "执行计划",
    "agent/pre-step": "给模型的上下文",
    "pipeline/stage": "流水线步骤",
    "pipeline/context": "流水线上下文",
    "agent/critique": "测试意见",
    "employees/assign": "班组分派",
    "task/created": "任务创建",
    "task/bound": "任务绑定",
    "task/status": "任务状态",
    "turn/start": "开始一轮",
    "turn/end": "结束一轮",
    "agent/stopped": "Agent 停止",
    "spec/ready": "任务 Spec",
    "eval/report": "Eval 报告",
    "task/review": "老板终审",
    "task/event": "任务事件",
  };
  return map[kind] || kind;
}

function eventTime(value) {
  const text = String(value || "").replace("T", " ");
  if (!text) return "";
  return text.slice(5, 16);
}

function actorDisplay(actor) {
  const name = String(actor || "");
  if (!name) return "—";
  if (name.startsWith("agent:")) return `Agent · ${name}`;
  return name;
}

function messagesFromEvents(events) {
  const kinds = new Set([
    "user/message", "assistant/message", "assistant/chunk",
    "pipeline/stage", "agent/critique", "agent/stopped", "tool/result",
  ]);
  return (events || []).flatMap((event) => {
    const kind = event.kind || "";
    if (!kinds.has(kind)) return [];
    const payload = event.payload || {};
    const role = kind === "user/message"
      ? "user"
      : (kind === "assistant/message" || kind === "assistant/chunk" ? "assistant" : "system");
    return [{
      role,
      kind,
      content: kind === "user/message"
        ? (payload.request || payload.content || eventPreview(event))
        : eventPreview(event),
      created_at: event.created_at,
      actor: event.actor,
      title: payload.title,
      task_status: payload.task_status || payload.to_status,
      stage_id: payload.stage_id,
      suggested_decision: payload.suggested_decision,
      reviewer_id: payload.reviewer_id || event.actor,
    }];
  });
}

function evidenceCards() {
  const task = state.task;
  const view = task && state.deliveryViewByTask[task.id];
  if (!view) return [];
  const cards = [];
  const spec = view.spec || {};
  if (spec.available) {
    const content = spec.content || {};
    const goal = content.goal || content.title || content.problem || "";
    cards.push({
      role: "system",
      kind: "spec/ready",
      content: goal
        ? `任务 Spec 已就绪：${goal}`
        : `任务 Spec 已就绪${spec.path ? ` · ${spec.path}` : ""}`,
      created_at: task.spec_ready_at || "",
    });
  }
  const evalView = view.eval || {};
  if (evalView.available) {
    cards.push({
      role: "system",
      kind: "eval/report",
      content: `阻断级 Eval：${evalView.decision || "—"}（通过 ${evalView.blocking_passed || 0} / 失败 ${evalView.blocking_failed || 0}）`,
    });
  }
  const review = view.review || {};
  if (review.decision) {
    cards.push({
      role: "system",
      kind: "task/review",
      content: `老板终审：${review.decision}${review.note ? ` · ${review.note}` : ""}${review.reviewed_by ? ` · ${review.reviewed_by}` : ""}`,
      created_at: review.reviewed_at || "",
    });
  }
  return cards;
}

function conversationMessages() {
  let messages = Array.isArray(state.messages) ? state.messages.slice() : [];
  if (!messages.length) messages = messagesFromEvents(state.events);
  const existing = new Set(messages.map((item) => item.kind));
  evidenceCards().forEach((card) => {
    if (!existing.has(card.kind)) messages.push(card);
  });
  return messages;
}

function renderMessages() {
  const empty = $("#empty-state");
  const stream = $("#message-stream");
  const allMessages = conversationMessages();
  if (!state.selectedSessionId && !allMessages.length) {
    empty.hidden = false;
    stream.hidden = true;
    $("#transcript-toolbar").hidden = true;
    stream.innerHTML = "";
    return;
  }
  empty.hidden = true;
  stream.hidden = false;
  if (!allMessages.length) {
    stream.innerHTML = `<div class="msg system"><div class="who">提示</div><div class="body">这个会话还没有可展示的对话记录。过程账本里可能已有事件，可切换到「过程」查看。</div></div>`;
    $("#transcript-toolbar").hidden = true;
    return;
  }
  const lastPipelineIndex = allMessages.reduce((found, message, index) =>
    (message.kind === "pipeline/stage" ? index : found), -1);
  const seenRequests = new Set();
  const summaryMessages = allMessages.filter((message, index) => {
    const kind = message.kind || "";
    if (message.role === "user" || kind === "user/message") {
      const key = messageBody(message).trim();
      if (seenRequests.has(key)) return false;
      seenRequests.add(key);
      return true;
    }
    if (index === lastPipelineIndex) return true;
    return kind === "agent/critique" || kind === "agent/stopped"
      || kind === "assistant/message" || kind === "assistant/chunk"
      || kind === "spec/ready" || kind === "eval/report" || kind === "task/review";
  });
  const visibleMessages = state.showAllMessages ? allMessages : summaryMessages;
  const toolbar = $("#transcript-toolbar");
  toolbar.hidden = false;
  $("#message-count").textContent = state.showAllMessages
    ? `${allMessages.length} 条全部记录`
    : `${summaryMessages.length} 条关键记录 · 已隐藏 ${Math.max(0, allMessages.length - summaryMessages.length)} 条技术噪声`;
  $("#toggle-message-density").textContent = state.showAllMessages ? "只看交付摘要" : "显示全部技术记录";
  stream.innerHTML = visibleMessages.map((m) => {
    const role = m.role || "system";
    const kind = m.kind || "";
    const when = eventTime(m.created_at);
    const whenHtml = when ? `<time>${esc(when)}</time>` : "";
    if (kind === "pipeline/stage") {
      const tone = m.task_status === "dead_letter" || m.task_status === "failed"
        ? "bad" : (m.task_status === "review" || m.task_status === "completed" ? "ok" : "info");
      return `<article class="msg pipeline ${tone}">
        <div class="who">流水线 · ${esc(m.title || kindLabel(kind))}${whenHtml}</div>
        <div class="body">${esc(messageBody(m))}</div>
        <div class="pipe-tag">对模型可见 · ${esc(m.stage_id || "")} / ${esc(m.task_status || "")}</div>
      </article>`;
    }
    if (kind === "pipeline/context" || kind === "agent/pre-step") {
      return `<article class="msg pipeline context">
        <div class="who">${esc(kindLabel(kind))}（已注入大模型）${whenHtml}</div>
        <details><summary>展开查看模型收到的流水线上下文</summary><pre class="body">${esc(messageBody(m))}</pre></details>
      </article>`;
    }
    if (kind === "agent/critique") {
      return `<article class="msg pipeline ok">
        <div class="who">测试意见 · ${esc(m.reviewer_id || "agent:reviewer")}${whenHtml}</div>
        <div class="body">${esc(messageBody(m))}</div>
        <div class="pipe-tag">建议=${esc(m.suggested_decision || "—")} · 不能代替老板终审</div>
      </article>`;
    }
    if (kind === "spec/ready" || kind === "eval/report" || kind === "task/review") {
      return `<article class="msg pipeline ok">
        <div class="who">${esc(kindLabel(kind))}${whenHtml}</div>
        <div class="body">${esc(messageBody(m))}</div>
      </article>`;
    }
    const who = kind ? `${roleLabel(role)} · ${kindLabel(kind)}` : roleLabel(role);
    return `<article class="msg ${esc(role)}${m.pending ? " pending" : ""}">
      <div class="who">${esc(who)}${whenHtml}</div>
      <div class="body">${esc(messageBody(m))}</div>
    </article>`;
  }).join("");
  const scroller = document.querySelector("[data-conversation-scroll]");
  if (scroller && (state.sending || !state.selectedSessionId)) scroller.scrollTop = scroller.scrollHeight;
}

function sessionTaskStatus(session) {
  if (!session) return null;
  return state.taskStatusBySession[session.id]
    || (session.id === state.selectedSessionId && state.task && state.task.status)
    || (session.task_id && state.taskById[session.task_id] && state.taskById[session.task_id].status)
    || null;
}

function sessionMatchesFilter(session) {
  const filter = state.sessionFilter || "all";
  if (filter === "all") return true;
  const st = sessionTaskStatus(session);
  if (filter === "review") return st === "review";
  if (filter === "terminal") return TERMINAL.has(st) || session.status === "closed";
  if (filter === "active") {
    if (BUSY.has(st)) return true;
    if (!st && session.status === "active") return true;
    return false;
  }
  return true;
}

function filterCounts() {
  const counts = { all: 0, active: 0, review: 0, terminal: 0 };
  (state.sessions || []).forEach((session) => {
    counts.all += 1;
    const st = sessionTaskStatus(session);
    if (st === "review") counts.review += 1;
    if (TERMINAL.has(st) || session.status === "closed") counts.terminal += 1;
    if (BUSY.has(st) || (!st && session.status === "active")) counts.active += 1;
  });
  return counts;
}

function syncSessionFilters() {
  const counts = filterCounts();
  const labels = {
    all: "全部",
    active: "进行中",
    review: "待验收",
    terminal: "终态",
  };
  document.querySelectorAll("#session-filters .filter").forEach((btn) => {
    const key = btn.dataset.filter || "all";
    const n = counts[key] || 0;
    btn.classList.toggle("active", key === state.sessionFilter);
    btn.textContent = n ? `${labels[key] || key} ${n}` : (labels[key] || key);
  });
}

function ingestTasks(items) {
  const byId = {};
  (items || []).forEach((task) => {
    if (task && task.id) byId[task.id] = task;
  });
  state.taskById = byId;
  const map = { ...state.taskStatusBySession };
  (state.sessions || []).forEach((session) => {
    if (session.task_id && byId[session.task_id]) {
      map[session.id] = byId[session.task_id].status;
    }
  });
  if (state.selectedSessionId && state.task) {
    map[state.selectedSessionId] = state.task.status;
  }
  state.taskStatusBySession = map;
}

function setWorkbenchView(view, target = "") {
  const normalized = ["delivery", "decision"].includes(view) ? view : "dashboard";
  state.workbenchView = normalized;
  localStorage.setItem(WORKBENCH_VIEW_KEY, normalized);
  $("#dashboard-view").hidden = normalized !== "dashboard";
  $("#decision-view").hidden = normalized !== "decision";
  $("#delivery-view").hidden = normalized !== "delivery";
  if (normalized === "dashboard" && state.detailsOpen) closeDetails();
  document.querySelectorAll("[data-workbench-view]").forEach((button) => {
    const sameView = button.dataset.workbenchView === normalized;
    const sameTarget = normalized !== "dashboard" || (target
      ? button.dataset.target === target
      : button.dataset.target === "dashboard-top");
    button.classList.toggle("active", sameView && sameTarget);
  });
  if (normalized === "dashboard" && target) {
    requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }
}

function openDecisionWithSignal(signal = "", extras = {}) {
  const value = String(signal || "").trim();
  fillInitiativeDraft({
    title: extras.title,
    signal: value,
    source: extras.source || (value ? "工作台业务信号快录" : ""),
    problem: extras.problem,
    goal: extras.goal,
    acceptance: extras.acceptance,
    evidence: extras.evidence,
    areas: extras.areas,
  });
  setWorkbenchView("decision");
  const titleFilled = Boolean(($("#initiative-title").value || "").trim());
  (titleFilled ? $("#initiative-problem") : $("#initiative-title")).focus();
}

function fillInitiativeDraft(draft = {}) {
  const signal = String(draft.signal || "").trim();
  if (signal) {
    ["title", "signal", "source", "problem", "goal", "acceptance", "evidence", "areas"]
      .forEach((field) => {
        const node = $("#initiative-" + field);
        if (node) node.value = "";
      });
  }
  const setIf = (selector, value) => {
    if (value == null || value === "") return;
    $(selector).value = value;
  };
  setIf("#initiative-title", draft.title || (signal ? shortTitle(signal) : ""));
  setIf("#initiative-signal", signal);
  setIf("#initiative-source", draft.source);
  setIf("#initiative-problem", draft.problem || (signal ? `现场信号：${signal}` : ""));
  setIf("#initiative-goal", draft.goal);
  setIf("#initiative-acceptance", Array.isArray(draft.acceptance) ? draft.acceptance.join("\n") : draft.acceptance);
  setIf("#initiative-evidence", Array.isArray(draft.evidence) ? draft.evidence.join("\n") : draft.evidence);
  setIf("#initiative-areas", Array.isArray(draft.areas) ? draft.areas.join(", ") : draft.areas);
}

function extrasFromDataset(el) {
  if (!el || !el.dataset) return {};
  return {
    title: el.dataset.title || "",
    source: el.dataset.source || "",
    problem: el.dataset.problem || "",
    goal: el.dataset.goal || "",
    acceptance: el.dataset.acceptance || "",
    evidence: el.dataset.evidence || "",
    areas: el.dataset.areas || "",
  };
}

function sessionForTask(taskId) {
  return (state.sessions || []).find((session) => session.task_id === taskId) || null;
}

async function openTaskDelivery(taskId) {
  const session = sessionForTask(taskId);
  setWorkbenchView("delivery");
  if (session) {
    await selectSession(session.id);
    return;
  }
  toast("还没有绑定该 Task 的会话，已打开交付区");
}

function openFilteredDelivery(filter) {
  state.sessionFilter = filter || "all";
  localStorage.setItem(FILTER_KEY, state.sessionFilter);
  renderSessions();
  setWorkbenchView("delivery");
  const visible = (state.sessions || []).filter(sessionMatchesFilter);
  if (visible[0]) selectSession(visible[0].id);
}

function flowerpHref(hash) {
  const url = (state.flowerpStatus && state.flowerpStatus.url) || "http://127.0.0.1:8000";
  return `${url}/${hash || "#dashboard"}`;
}

async function runVerifyEval() {
  const projectId = currentProjectId();
  if (!projectId) {
    $("#workspace-dialog").showModal();
    return toast("请先选择工作区");
  }
  if (state.verifyingEval) return toast("正在复验，请稍候");
  state.verifyingEval = true;
  const button = $("#dashboard-run-eval");
  if (button) button.disabled = true;
  try {
    const task = await api("/api/v1/tasks", {
      method: "POST",
      headers: headers("verify-eval"),
      body: JSON.stringify({
        project_id: projectId,
        request: "复验当前仓库阻断级 Eval，不修改代码。",
        requirement_id: "REQ-VERIFY-BLOCKING",
        execute_code: false,
        write_scope: [],
        profile_id: currentProfileId(),
      }),
    });
    toast("已启动 Blocking Eval 复验");
    await loadShell();
    setWorkbenchView("delivery");
    if (task.session_id) await selectSession(task.session_id);
  } catch (err) {
    toast(err.message);
  } finally {
    state.verifyingEval = false;
    if (button) button.disabled = false;
  }
}

async function reviewFeedbackItem(feedbackId, decision) {
  const note = (
    document.querySelector(`[data-feedback-note="${feedbackId}"]`)
    || { value: "" }
  ).value.trim();
  if (!note) return toast("审核反馈必须写下判断依据");
  try {
    await api(`/api/v1/feedback/${encodeURIComponent(feedbackId)}/review`, {
      method: "POST",
      headers: headers("feedback-review"),
      body: JSON.stringify({ decision, note }),
    });
    toast(decision === "accept" ? "反馈已接受" : "反馈已驳回");
    await loadShell();
  } catch (err) {
    toast(err.message);
  }
}

async function proposeEvolution(feedbackId) {
  const item = ((state.feedbackSummary && state.feedbackSummary.items) || [])
    .find((entry) => entry.id === feedbackId);
  if (!item) return;
  const signature = String(item.next_step || item.conclusion || feedbackId).slice(0, 200);
  try {
    await api("/api/v1/evolutions", {
      method: "POST",
      headers: headers("evolution-create"),
      body: JSON.stringify({
        feedback_id: feedbackId,
        failure_signature: signature,
        classification: "workbench_observability",
      }),
    });
    toast("已建立进化候选");
    await loadShell();
  } catch (err) {
    toast(err.message);
  }
}

function startLessonInitiative(number) {
  const lesson = (state.courseLessons || []).find((item) => Number(item.number) === Number(number));
  if (!lesson) return toast("未找到该讲合同");
  const pad = String(lesson.number).padStart(2, "0");
  openDecisionWithSignal(lesson.request, {
    title: `L${pad} ${lesson.title}`,
    source: `课程合同 ${lesson.requirement_id}`,
    problem: lesson.erp_increment,
    goal: lesson.workbench_increment,
    acceptance: (lesson.acceptance || []).join("\n"),
    evidence: `课程合同 ${lesson.requirement_id} · ${lesson.baseline_ref}`,
    areas: (lesson.write_scope || []).map((path) => {
      if (String(path).startsWith("flowerp")) return "inventory";
      if (String(path).includes("web")) return "api";
      return "";
    }).filter(Boolean).join(", ") || "inventory",
  });
  toast(`已载入 L${pad} 合同，补证据后做决定`);
}

function activateCapability(name) {
  if (name === "Eval" || name === "Harness") return runVerifyEval();
  if (name === "Spec") return setWorkbenchView("decision");
  if (name === "Loop" || name === "Graph") {
    setWorkbenchView("delivery");
    if (state.selectedSessionId) openGraphDetails();
    else if (state.sessions[0]) selectSession(state.sessions[0].id);
    return;
  }
  if (name === "API") {
    toast(state.health ? `API 已响应 · ${state.health.product}` : "健康接口尚未返回");
    return;
  }
  toast("当前就是工作台 Web");
}

function deliveryViewsByRecency() {
  return Object.values(state.deliveryViewByTask || {}).sort((left, right) =>
    String((right.task || {}).updated_at || "").localeCompare(String((left.task || {}).updated_at || ""))
  );
}

function latestEvalView() {
  return deliveryViewsByRecency().find((view) => view.eval && view.eval.available) || null;
}

function ingestDeliveryViews(items) {
  const byTask = {};
  (items || []).forEach((view) => {
    if (view && view.task_id) byTask[view.task_id] = view;
  });
  state.deliveryViewByTask = byTask;
  ingestTasks((items || []).map((view) => view.task).filter(Boolean));
}

function sessionDotClass(session) {
  const st = sessionTaskStatus(session);
  if (!st) return session.status === "paused" ? "busy" : "";
  if (BUSY.has(st)) return "busy";
  if (st === "review") return "review";
  if (st === "completed") return "done";
  if (st === "failed" || st === "dead_letter") return "bad";
  return "";
}

function renderSessions() {
  const list = $("#session-list");
  syncSessionFilters();
  if (!state.sessions.length) {
    list.innerHTML = `<p style="color:var(--n-600);padding:8px;font-size:12px">还没有交付会话。点右上角 ＋ 或直接开始交付。</p>`;
    return;
  }
  const visible = state.sessions.filter(sessionMatchesFilter);
  if (!visible.length) {
    list.innerHTML = `<p style="color:var(--n-600);padding:8px;font-size:12px">当前筛选下没有会话。试试「全部」。</p>`;
    return;
  }
  list.innerHTML = visible.map((s) => {
    const st = sessionTaskStatus(s);
    const progress = st ? stepHelp(st).title : statusLabel(s.status);
    const dot = sessionDotClass(s);
    return `<button type="button" class="session-item ${s.id === state.selectedSessionId ? "active" : ""}" data-id="${esc(s.id)}">
      <b>${esc(shortTitle(s.title))}</b>
      <small><span class="dot ${dot}"></span>${esc(progress)} · ${esc((s.updated_at || "").slice(5, 16))}</small>
    </button>`;
  }).join("");
  list.querySelectorAll(".session-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      setWorkbenchView("delivery");
      selectSession(btn.dataset.id);
    });
  });
}

function eventsForStage(step) {
  const events = state.events || [];
  const statuses = new Set(step.match);
  return events.filter((event) => {
    const kind = event.kind || "";
    const payload = event.payload || {};
    if (kind === "pipeline/stage") {
      return step.id === payload.stage_id || statuses.has(payload.task_status);
    }
    if (kind === "task/status" || kind === "task/event") {
      return statuses.has(payload.to_status);
    }
    if (kind === "task/created" && step.id === "request") return true;
    if (kind === "agent/stopped" && step.id === "human") return true;
    if (kind === "pipeline/context" && step.id === (RAIL[railIndex(currentStatus())] || {}).id) return true;
    return false;
  }).slice(-6);
}

function stageStateClass(step, index, currentIdx, status) {
  const fail = status === "failed" || status === "dead_letter";
  let cls = "rail-step";
  if (fail && index === RAIL.length - 1) cls += " fail current";
  else if (status === "completed" && index <= RAIL.length - 1) {
    cls += index < RAIL.length - 1 ? " done" : " done current";
  } else if (currentIdx < 0) cls += index === 0 ? "" : " todo";
  else if (index < currentIdx) cls += " done";
  else if (index === currentIdx) cls += " current";
  else cls += " todo";
  if (state.expandedRailId === step.id) cls += " open";
  return cls;
}

function renderRailPanel(step) {
  const panel = $("#rail-panel");
  if (!step) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  const status = currentStatus();
  const related = eventsForStage(step);
  const onThis = step.match.includes(status);
  const statusNote = onThis
    ? `当前就在这一步（${statusLabel(status)}）`
    : (railIndex(status) > RAIL.findIndex((s) => s.id === step.id)
      ? "此步已走过"
      : "尚未到达");

  let footer = "";
  if (step.id === "human" && state.task && state.task.status === "review") {
    footer = `<div class="rail-actions">
      <button type="button" class="send" data-rail-act="approve">通过验收</button>
      <button type="button" class="ghost-btn" data-rail-act="reject">驳回</button>
    </div>`;
  } else if (step.id === "human" && state.task && (state.task.status === "dead_letter" || state.task.status === "failed")) {
    footer = `<div class="rail-actions">
      <button type="button" class="send" data-rail-act="fork">复制会话再试</button>
      <button type="button" class="ghost-btn" data-rail-act="export">下载证据</button>
      <button type="button" class="linkish" data-rail-act="new">新建交付</button>
    </div>`;
  }

  panel.hidden = false;
  const dutyId = DUTY_BY_RAIL[step.id] || dutyForStatus(status);
  const duty = employeeById(dutyId);
  const role = step.role || duty.display_name;
  panel.innerHTML = `
    <div class="rail-panel-head">
      <div>
        <strong>${esc(step.title)}</strong>
        <span class="rail-status-note">${esc(statusNote)} · 值班 ${esc(role)}</span>
      </div>
      <button type="button" class="linkish" id="rail-collapse">收起</button>
    </div>
    <p class="rail-summary">${esc(step.summary)}</p>
    <div class="rail-grid">
      <div>
        <h4>本步做什么</h4>
        <ul>${step.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
        <p class="muted">当前职责：${esc(role)}（事件标签 ${esc(duty.display_name)} / ${esc(dutyId)}）</p>
      </div>
      <div>
        <h4>给大模型的提示</h4>
        <p class="model-hint">${esc(step.model_hint)}</p>
        <p class="muted">该节点会写入 Session，并注入 pre-step / system prompt。</p>
      </div>
    </div>
    <div class="rail-evidence">
      <h4>相关过程（${related.length}）</h4>
      ${related.length
        ? `<ul class="rail-event-list">${related.map((event) => {
          const payload = event.payload || {};
          const line = payload.title || payload.detail || payload.to_status
            || payload.reason || event.kind;
          const who = event.actor ? employeeById(event.actor).display_name || event.actor : "—";
          return `<li><button type="button" data-seq="${esc(event.sequence)}"><b>${esc(kindLabel(event.kind))}</b> <small>${esc(who)}</small> ${esc(String(line).slice(0, 90))}</button></li>`;
        }).join("")}</ul>`
        : `<p class="muted">本会话还没有落到这一步的事件。新建交付并跑通后会出现可展开证据。</p>`}
    </div>
    ${footer}`;

  const collapse = $("#rail-collapse");
  if (collapse) {
    collapse.addEventListener("click", () => {
      state.expandedRailId = null;
      renderRail();
    });
  }
  panel.querySelectorAll("button[data-seq]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const event = (state.events || []).find((e) => String(e.sequence) === String(btn.dataset.seq));
      if (event) {
        openEventDetails(event);
        setView("trajectory");
      }
    });
  });
  panel.querySelectorAll("[data-rail-act]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const act = btn.dataset.railAct;
      if (act === "approve") quickReview("approve");
      else if (act === "reject") quickReview("reject");
      else if (act === "fork") forkSession();
      else if (act === "export") exportSession();
      else if (act === "new") openDecisionWithSignal();
    });
  });
}

function renderRail() {
  const rail = $("#progress-rail");
  const status = currentStatus();
  if (!state.selectedSessionId) {
    rail.hidden = true;
    state.expandedRailId = null;
    state.railAutoOpened = false;
    return;
  }
  rail.hidden = false;
  const idx = railIndex(status);

  if (!state.railAutoOpened) state.railAutoOpened = true;

  $("#rail-steps").innerHTML = RAIL.map((step, i) => {
    const cls = stageStateClass(step, i, idx, status);
    const mark = cls.includes("done") ? "✓" : (cls.includes("current") ? "●" : String(i + 1));
    const role = step.role || employeeById(DUTY_BY_RAIL[step.id] || "agent:coder").display_name;
    return `<button type="button" class="${cls}" data-rail="${esc(step.id)}" aria-expanded="${state.expandedRailId === step.id}">
      <span class="rail-mark">${mark}</span>
      <span class="rail-copy">
        <span class="rail-label">${esc(step.label)}</span>
        <span class="rail-emp">${esc(role)}</span>
      </span>
      <span class="rail-chevron">${state.expandedRailId === step.id ? "▾" : "▸"}</span>
    </button>`;
  }).join("");

  $("#rail-steps").querySelectorAll("[data-rail]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.rail;
      state.expandedRailId = state.expandedRailId === id ? null : id;
      renderRail();
    });
  });

  const open = RAIL.find((s) => s.id === state.expandedRailId) || null;
  renderRailPanel(open);
}

function renderCollabBar() {
  const bar = $("#collab-bar");
  if (!bar) return;
  if (!state.selectedSessionId) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }
  const me = ($("#actor").value || "").trim() || "boss";
  const activity = actorActivity();
  bar.hidden = false;
  bar.innerHTML = `
    <div class="collab-main">
      <span><span class="collab-mode">${esc(roleRuntimeLabel())}</span> 交付角色 <b>${esc(currentRailRole())}</b> · 终审人 <b>${esc(me)}</b></span>
      <span class="collab-actions"><button type="button" id="toggle-team" class="linkish">${state.teamExpanded ? "收起运行时视角" : "查看 User / Agent / 工具"}</button></span>
    </div>
    <div class="opc-roster" ${state.teamExpanded ? "" : "hidden"}>
      <button type="button" class="opc-emp ${state.dutyFocus === "user" ? "on" : ""}" data-traj-actor="user" title="筛选人类操作；不是独立登录">
        <b>你（User）</b>
        <small>${esc(me)} · ${activity.user} 事件</small>
      </button>
      <button type="button" class="opc-emp ${state.dutyFocus === "agent" ? "on" : ""}" data-traj-actor="agent" title="筛选 agent:* 事件">
        <b>Agent</b>
        <small>agent:* · ${activity.agent} 事件</small>
      </button>
      <button type="button" class="opc-emp ${state.dutyFocus === "tool" ? "on" : ""}" data-traj-actor="tool" title="筛选 tool / approval/ask">
        <b>工具</b>
        <small>tool · ${activity.tool} 事件</small>
      </button>
    </div>
    <p class="collab-warn" ${state.teamExpanded ? "" : "hidden"} style="background:var(--ds-50);color:var(--ds-700);border:0">
      ${esc(roleRuntimeNote())} 点卡片只过滤「过程」账本；交付终审只有老板/Leader 能做。
    </p>`;
  $("#toggle-team").addEventListener("click", () => {
    state.teamExpanded = !state.teamExpanded;
    renderCollabBar();
  });
  bar.querySelectorAll("[data-traj-actor]").forEach((btn) => {
    btn.addEventListener("click", () => focusDuty(btn.dataset.trajActor));
  });
}

function renderRecoveryBanner() {
  const banner = $("#recovery-banner");
  if (!banner) return;
  const task = state.task;
  if (!task || (task.status !== "dead_letter" && task.status !== "failed")) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  const dead = task.status === "dead_letter";
  banner.hidden = false;
  banner.innerHTML = `
    <strong>${dead ? "任务进入死信，不能自动继续" : "本轮交付失败"}</strong>
    <div>${dead
      ? "自动化流水线已无法继续推进。请老板复盘后复制会话再试。"
      : "可查看过程账本定位失败点，再复制会话或新建交付。"}</div>
    ${task.error ? `<div class="recovery-error">${esc(task.error)}</div>` : ""}
    <div class="actions">
      <button type="button" class="send" id="recovery-fork">复制会话再试</button>
      <button type="button" class="ghost-btn" id="recovery-code">启用「改代码」</button>
      <button type="button" class="ghost-btn" id="recovery-export">下载证据</button>
      <button type="button" class="linkish" id="recovery-new">新建交付</button>
    </div>`;
  $("#recovery-fork").addEventListener("click", forkSession);
  $("#recovery-export").addEventListener("click", exportSession);
  $("#recovery-new").addEventListener("click", createSession);
  $("#recovery-code").addEventListener("click", () => {
    $("#execute-code").checked = true;
    document.querySelectorAll(".chip").forEach((el) => el.classList.remove("active"));
    const std = document.querySelector('.chip[data-mode="standard"]');
    if (std) std.classList.add("active");
    const box = $("#advanced");
    if (box) box.hidden = false;
    $("#toggle-advanced").textContent = "收起选项";
    toast("已勾选改代码；复制会话后再发送需求");
  });
}

function renderReviewBanner() {
  const banner = $("#review-banner");
  const task = state.task;
  if (!task || task.status !== "review") {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  const critique = latestCritique();
  banner.hidden = false;
  banner.innerHTML = `
    <strong>自动化已停工，请老板终审这次 FlowERP 交付</strong>
    <div>质检职责已完成检查；通过后记为完成，驳回后返回实现职责。自动化不能代替你点通过。</div>
    ${critique ? `<div class="banner-warn">质检建议：<b>${esc(critique.suggested_decision || "—")}</b><br>${esc(String(critique.note || "").slice(0, 280))}</div>` : ""}
    <div class="actions">
      <button type="button" class="send" id="banner-approve">老板通过</button>
      <button type="button" class="ghost-btn" id="banner-reject">老板驳回</button>
      <button type="button" class="linkish" id="banner-open-details">查看进度详情</button>
    </div>`;
  $("#banner-approve").addEventListener("click", () => quickReview("approve"));
  $("#banner-reject").addEventListener("click", () => quickReview("reject"));
  $("#banner-open-details").addEventListener("click", openGraphDetails);
}

function quickReview(decision) {
  const task = state.task;
  if (!task) return;
  if ((actor() || "").startsWith("agent:")) {
    return toast("自动化职责 actor 不能终审，请用老板身份");
  }
  $("#review-decision").value = decision;
  $("#review-dialog-title").textContent = decision === "approve" ? "通过本次交付" : "驳回并要求返工";
  $("#review-note").value = decision === "approve"
    ? "阻断级 Eval 通过，证据完整，接受本次 FlowERP 增量"
    : "当前证据不足或验收未通过，需要最小范围返工后重新验收";
  $("#review-confirm").textContent = decision === "approve" ? "确认通过" : "确认驳回";
  $("#review-dialog").showModal();
  $("#review-note").focus();
}

async function submitReview() {
  const task = state.task;
  const decision = $("#review-decision").value;
  const note = ($("#review-note").value || "").trim();
  if (!task || !["approve", "reject"].includes(decision)) return;
  if (!note) return toast("必须填写判断依据");
  $("#review-confirm").disabled = true;
  try {
    await api(`/api/v1/tasks/${encodeURIComponent(task.id)}/review`, {
      method: "POST",
      headers: headers("review"),
      body: JSON.stringify({ decision, note }),
    });
    $("#review-dialog").close();
    toast(decision === "approve" ? "老板已通过" : "老板已驳回，进入返工");
    await refreshSelected();
    await loadShell();
  } catch (err) {
    toast(err.message);
  } finally {
    $("#review-confirm").disabled = false;
  }
}

function renderHeader() {
  const session = state.session;
  const task = state.task;
  const deliveryView = task && state.deliveryViewByTask[task.id];
  const project = state.projects.find((p) => p.id === (session && session.project_id))
    || state.projects.find((p) => p.id === currentProjectId());
  $("#chat-title").textContent = session ? shortTitle(session.title) : "新交付";
  if (!session) {
    $("#chat-sub").textContent = "提一个 FlowERP 小需求，工作台改代码并跑验收";
  } else {
    const progress = deliveryView ? deliveryView.status.title : stepHelp(currentStatus() || "queued").title;
    const next = deliveryView ? ` · 下一步：${deliveryView.status.next_action}` : "";
    $("#chat-sub").textContent = `${(project && project.name) || "工作区"} · ${progress}${next}`;
  }
  const badge = $("#task-badge");
  const raw = task ? task.status : (session ? session.status : "idle");
  badge.textContent = deliveryView ? deliveryView.status.title : (task ? stepHelp(raw).title : statusLabel(raw));
  badge.className = `pill ${raw || ""}`;
  renderRail();
  renderCollabBar();
  renderReviewBanner();
  renderRecoveryBanner();
  renderEmployeeChips();
  syncComposerEnabled();
}

function renderTrajectoryInspector(event) {
  const box = $("#traj-inspector");
  if (!box) return;
  if (!event) {
    box.innerHTML = `<div class="explain-card">
      <p class="explain-kicker">过程账本</p>
      <p>点左侧一条记录，查看谁在何时做了什么。这是答辩复盘用的事件账本，不是 FlowERP 业务单据。</p>
    </div>`;
    return;
  }
  box.innerHTML = `<div class="explain-card">
    <p class="explain-kicker">${esc(kindLabel(event.kind))}</p>
    <h3>${esc(String(eventPreview(event)).slice(0, 160) || event.kind || "事件")}</h3>
    <div class="kv traj-kv">
      <div><span>时间</span><b>${esc(event.created_at || "—")}</b></div>
      <div><span>操作者</span><b>${esc(actorDisplay(event.actor))}</b></div>
      <div><span>类型</span><b>${esc(event.kind || "—")}</b></div>
      <div><span>序号</span><b>#${esc(event.sequence ?? "—")}</b></div>
    </div>
    <pre class="traj-payload">${esc(JSON.stringify(event.payload || {}, null, 2))}</pre>
  </div>`;
}

function renderTrajectory() {
  const table = $("#traj-table");
  if (!table) return;
  const events = state.events || [];
  const filtered = events.filter(eventMatchesTrajFilter);
  const activity = actorActivity();
  const filterLabel = runtimeFilterLabel(state.trajActorFilter);
  const sourceLabel = {
    all: "全部来源",
    turn: "turn/step",
    tool: "tool",
    llm: "模型",
    task: "交付（课程扩展）",
  }[state.trajSourceFilter] || "全部来源";
  $("#traj-overview").innerHTML = state.session
    ? `共 ${events.length} 条 · 当前显示 ${filtered.length} 条（${esc(filterLabel)} · ${esc(sourceLabel)}）。对照 DeepSeek Trajectory / Codex event stream。
       User ${activity.user} / Agent ${activity.agent} / 工具 ${activity.tool}`
    : "未选择会话。打开一次交付后，这里会显示该次改动的全部过程事件。";

  const filters = $("#traj-filters");
  if (filters) {
    filters.querySelectorAll("[data-traj]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.traj === state.trajActorFilter);
    });
  }
  const sources = $("#traj-source-filters");
  if (sources) {
    sources.querySelectorAll("[data-traj-source]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.trajSource === state.trajSourceFilter);
    });
  }

  const selected = state.selectedEvent
    && filtered.some((event) => event.sequence === state.selectedEvent.sequence)
    ? state.selectedEvent
    : null;

  if (!state.session) {
    table.innerHTML = `<div class="traj-turn">先在左侧打开一次交付，这里会显示该次改动的全部过程事件。</div>`;
    renderTrajectoryInspector(null);
    return;
  }
  if (!events.length) {
    table.innerHTML = `<div class="traj-turn">这次交付还没有过程事件。流水线跑起来后，规格、改代码、Eval 和终审都会记在这里。</div>`;
    renderTrajectoryInspector(null);
    return;
  }
  if (!filtered.length) {
    table.innerHTML = `<div class="traj-turn">当前筛选下没有事件。点「全部」查看完整账本。</div>`;
    renderTrajectoryInspector(null);
    return;
  }
  const inspect = selected || filtered[filtered.length - 1];
  const chunks = [];
  filtered.forEach((event, index) => {
    if (event.kind === "turn/start") {
      chunks.push(`<div class="traj-turn">第 ${esc((event.payload || {}).turn || "?")} 轮</div>`);
    }
    const active = inspect && inspect.sequence === event.sequence;
    chunks.push(`<button type="button" class="traj-row ${active ? "active" : ""}" data-seq="${esc(event.sequence)}">
      <span class="idx">#${esc(event.sequence ?? index + 1)}</span>
      <span class="when">${esc(eventTime(event.created_at) || "—")}</span>
      <span class="kind">${esc(kindLabel(event.kind))}</span>
      <span class="who">${esc(actorDisplay(event.actor))}</span>
      <span class="content">${esc(String(eventPreview(event)).slice(0, 90))}</span>
    </button>`);
  });
  table.innerHTML = chunks.join("");
  table.querySelectorAll(".traj-row").forEach((row) => {
    row.addEventListener("click", () => {
      const seq = Number(row.dataset.seq);
      const event = events.find((item) => item.sequence === seq);
      state.selectedEvent = event || null;
      renderTrajectory();
    });
  });
  renderTrajectoryInspector(inspect);
}

function renderGraphDetails() {
  const graph = state.graph || {};
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : Object.values(graph.nodes || {});
  const current = currentStatus() || "queued";
  const help = stepHelp(current);
  const project = state.projects.find((p) => p.id === (state.session && state.session.project_id))
    || state.projects.find((p) => p.id === currentProjectId());
  const request = (state.task && state.task.request)
    || ((state.messages.find((m) => m.role === "user") || {}).content)
    || "（尚未发送需求）";
  const duty = employeeById(dutyForStatus(current));
  const activity = actorActivity();
  const critique = latestCritique();

  $("#details-title").textContent = "交付进度";
  $("#details-kind").textContent = roleRuntimeLabel();
  $("#details-body").innerHTML = `
    <div class="explain-card">
      <p class="explain-kicker">两层词汇</p>
      <p>交付轨道当前角色：<b>${esc(currentRailRole())}</b>（事件标签 ${esc(duty.id)}）。Harness 运行时：User ${activity.user} / Agent ${activity.agent} / 工具 ${activity.tool}。</p>
      <p>${esc(roleRuntimeNote())}</p>
    </div>
    <div class="explain-card">
      <p class="explain-kicker">和 FlowERP 的关系</p>
      <p>工作区 <b>${esc((project && project.name) || "—")}</b> 是产品代码。这里跟踪<strong>这一次改动</strong>是否受控交付。</p>
    </div>
    <div class="explain-card current-step">
      <p class="explain-kicker">当前</p>
      <h3>${esc(help.title)}</h3>
      <p>${esc(help.blurb)}</p>
      <p class="muted">${esc(String(request).slice(0, 160))}</p>
      <p class="muted">${esc((state.task && state.task.id) || "尚未创建任务")}</p>
      ${critique ? `<p class="muted">挑刺建议：${esc(critique.suggested_decision || "—")}</p>` : ""}
    </div>
    <div class="pipeline">
      ${nodes.map((n) => {
        const h = stepHelp(n.id);
        const cls = ["pipe-step", n.visited ? "visited" : "", n.id === current ? "current" : ""].filter(Boolean).join(" ");
        return `<div class="${cls}"><b>${esc(h.title)}</b><small>${esc(h.blurb || statusLabel(n.id))}</small></div>`;
      }).join("")}
    </div>
    <details class="tech-fold">
      <summary>技术细节</summary>
      <pre>${esc(JSON.stringify({
        current,
        duty: duty.id,
        activity,
        edges: (graph.edges || []).slice(-6),
        tools: (graph.tools || []).slice(-8),
      }, null, 2))}</pre>
    </details>`;
}

function openEventDetails(event) {
  if (!event) return;
  state.selectedEvent = event;
  state.detailsMode = "event";
  state.detailsOpen = true;
  syncShell();
  $("#details-title").textContent = "过程事件";
  $("#details-kind").textContent = kindLabel(event.kind);
  $("#details-body").innerHTML = `<div class="explain-card">
    <p class="explain-kicker">过程账本</p>
    <p>用于追溯工作台动作，不是 FlowERP 业务单据。</p>
  </div>
  <div class="kv">
    <div><span>类型</span><b>${esc(event.kind)}</b></div>
    <div><span>序号</span><b>${esc(event.sequence)}</b></div>
    <div><span>操作者</span><b>${esc(event.actor || "—")}</b></div>
    <div><span>时间</span><b>${esc(event.created_at || "—")}</b></div>
    <div><span>内容</span><pre>${esc(JSON.stringify(event.payload || {}, null, 2))}</pre></div>
  </div>`;
  renderReview();
  renderTrajectory();
}

function openGraphDetails() {
  if (!requireSession()) return;
  state.selectedEvent = null;
  state.detailsMode = "graph";
  state.detailsOpen = true;
  syncShell();
  renderGraphDetails();
  renderReview();
  renderTrajectory();
  $("#more-menu").hidden = true;
}

function closeDetails() {
  state.detailsOpen = false;
  state.selectedEvent = null;
  $("#details-title").textContent = "交付进度";
  $("#details-kind").textContent = "这次改代码任务走到哪一步";
  $("#details-body").innerHTML = "";
  syncShell();
}

function renderReview() {
  const task = state.task;
  if (task && task.status === "review") {
    const critique = latestCritique();
    $("#trail-review").innerHTML = `<form id="review-form" class="review-box">
      <strong>老板终审</strong>
      ${critique ? `<p class="banner-warn">质检建议：${esc(critique.suggested_decision || "—")}</p>` : ""}
      <select name="decision"><option value="approve">通过</option><option value="reject">驳回</option></select>
      <textarea name="note" required placeholder="说明理由"></textarea>
      <button type="submit" class="send">提交终审</button>
    </form>`;
    $("#review-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if ((actor() || "").startsWith("agent:")) return toast("自动化职责 actor 不能终审");
      const data = new FormData(event.target);
      try {
        await api(`/api/v1/tasks/${encodeURIComponent(task.id)}/review`, {
          method: "POST",
          headers: headers("review"),
          body: JSON.stringify({ decision: data.get("decision"), note: data.get("note") }),
        });
        toast("终审已提交");
        await refreshSelected();
        await loadShell();
      } catch (err) {
        toast(err.message);
      }
    });
  } else if (task && task.error) {
    $("#trail-review").innerHTML = `<div class="kv"><div><span>错误</span><b>${esc(task.error)}</b></div></div>`;
  } else {
    $("#trail-review").innerHTML = "";
  }
}

function syncProfileSelects(profileId) {
  const id = String(profileId || currentProfileId() || "PROFILE-DEFAULT");
  ["#profile-id", "#settings-profile"].forEach((selector) => {
    const node = $(selector);
    if (!node || !node.options) return;
    if (![...node.options].some((option) => option.value === id) && id) {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = id;
      node.appendChild(option);
    }
    if ([...node.options].some((option) => option.value === id)) node.value = id;
  });
  localStorage.setItem(PROFILE_KEY, id);
}

function renderProfileSelect() {
  const current = localStorage.getItem(PROFILE_KEY) || "PROFILE-DEFAULT";
  const items = state.profiles.length ? state.profiles : [{ id: "PROFILE-DEFAULT", name: "默认" }];
  ["#profile-id", "#settings-profile"].forEach((selector) => {
    const select = $(selector);
    if (!select) return;
    select.innerHTML = items.map((p) => `<option value="${esc(p.id)}">${esc(p.name || p.id)}</option>`).join("");
  });
  syncProfileSelects(current);
}

async function switchProfile(profileId) {
  syncProfileSelects(profileId);
  try {
    await loadShell();
    toast(`已切换 Profile ${currentProfileId()}。这是教学组合，不是 dsh / Codex 官方运行时。`);
  } catch (err) {
    toast(err.message);
  }
}

async function activatePlugin(pluginId) {
  try {
    await api(`/api/v1/profiles/${encodeURIComponent(currentProfileId())}/activate`, {
      method: "POST",
      headers: headers("plugin"),
      body: JSON.stringify({ plugin_id: pluginId }),
    });
    toast(`已把 ${pluginId} 用于当前 Profile`);
    await loadShell();
  } catch (err) {
    toast(err.message);
  }
}

function renderSeamMap() {
  const box = $("#seam-map");
  if (!box) return;
  const composition = state.composition || (state.capabilities && state.capabilities.composition) || {};
  const seams = composition.seams || [];
  const missing = composition.missing_seams || [];
  const profile = (composition.profile && composition.profile.id) || currentProfileId();
  box.innerHTML = `
    <article><b>Profile</b><small>${esc(profile)}</small></article>
    <article><b>组合就绪</b><small>${composition.ready ? "是" : "否"}</small></article>
    ${seams.map((seam) => `<article><b>${esc(seam)}</b><small>seam</small></article>`).join("")}
    ${missing.map((seam) => `<article class="missing-seam"><b>${esc(seam)}</b><small>缺失</small></article>`).join("")}
  `;
}

function renderDumpConfig() {
  const dump = $("#dump-config");
  if (!dump) return;
  dump.textContent = state.dumpConfig
    ? JSON.stringify(state.dumpConfig, null, 2)
    : "尚未加载 dump-config";
}

function renderSettings() {
  const caps = state.capabilities || {};
  const providers = (caps.providers || {});
  $("#capability-cards").innerHTML = `
    <article><b>Codex CLI</b><small>${caps.codex_available ? "本机可用（受控改码）" : "不可用，可走 template / 只验证"}</small></article>
    <article><b>LLM Provider</b><small>${esc(providers.llm || llmProvider())}</small></article>
    <article><b>工具</b><small>${(state.tools || []).length} 个</small></article>
    <article><b>交付终审</b><small>老板/Leader 具名通过或驳回</small></article>
    <article><b>工具 ask</b><small>approval=ask 一次性放行；有事件才出现，不是审批队列产品</small></article>
    <article><b>Interrupt</b><small>会话暂停/继续</small></article>`;

  renderSeamMap();
  renderDumpConfig();

  $("#project-cards").innerHTML = state.projects.map((p) =>
    `<article><b>${esc(p.name)}</b><small>${esc(p.id)} · ${esc(p.root_path)}</small></article>`).join("")
    || "<p style='color:var(--n-600)'>暂无项目</p>";

  $("#profile-cards").innerHTML = state.profiles.map((p) =>
    `<article><b>${esc(p.name)}</b><small>${esc(p.id)}</small></article>`).join("");

  $("#plugin-cards").innerHTML = state.plugins.map((p) => `
    <div class="plugin-row">
      <label>
        <input type="checkbox" data-plugin="${esc(p.id)}" ${p.enabled ? "checked" : ""}>
        <span><b>${esc(p.name)}</b><small>${esc(p.seam)} → ${esc(p.provider)}</small></span>
      </label>
      <button type="button" class="ghost-btn" data-activate="${esc(p.id)}">用于当前 Profile</button>
    </div>`).join("");
  $("#plugin-cards").querySelectorAll("input[data-plugin]").forEach((input) => {
    input.addEventListener("change", async () => {
      try {
        await api(`/api/v1/plugins/${encodeURIComponent(input.dataset.plugin)}/enabled`, {
          method: "POST",
          headers: headers("plugin"),
          body: JSON.stringify({ enabled: input.checked }),
        });
        toast(input.checked ? "已启用" : "已禁用");
        await loadShell();
      } catch (err) {
        input.checked = !input.checked;
        toast(err.message);
      }
    });
  });
  $("#plugin-cards").querySelectorAll("[data-activate]").forEach((btn) => {
    btn.addEventListener("click", () => activatePlugin(btn.dataset.activate));
  });

  $("#tool-cards").innerHTML = state.tools.slice(0, 16).map((t) =>
    `<article><b>${esc(t.name || t.id)}</b><small>${esc(t.description || "")}</small></article>`).join("");

  const select = $("#project-id");
  const current = currentProjectId();
  select.innerHTML = state.projects.map((p) =>
    `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("") || `<option value="">无</option>`;
  if (current && state.projects.some((p) => p.id === current)) select.value = current;
  else if (state.projects[0]) select.value = state.projects[0].id;
  localStorage.setItem(WORKSPACE_KEY, select.value || "");
  renderProfileSelect();
  syncComposerEnabled();
  renderWorkspaceOptions();
}

function renderWorkspaceOptions() {
  const box = $("#workspace-options");
  box.innerHTML = state.projects.length
    ? state.projects.map((p) => `
      <button type="button" data-id="${esc(p.id)}">
        <b>${esc(p.name)}</b>
        <small>${esc(p.id)} · ${esc(p.root_path)}</small>
      </button>`).join("")
    : `<p style="color:var(--n-600)">暂无工作区</p>`;
  box.querySelectorAll("button[data-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("#project-id").value = btn.dataset.id;
      localStorage.setItem(WORKSPACE_KEY, btn.dataset.id);
      syncComposerEnabled();
      $("#workspace-dialog").close();
      toast(`工作区：${btn.dataset.id}`);
    });
  });
}

function stopPoll() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function maybePoll() {
  stopPoll();
  if (!state.selectedSessionId || !taskBusy()) return;
  state.pollTimer = setInterval(() => {
    refreshSelected().catch(() => {});
  }, POLL_MS);
}

async function refreshSelected() {
  if (!state.selectedSessionId) return;
  const sessionId = state.selectedSessionId;
  const [session, messagesPayload, graph, agent] = await Promise.all([
    api(`/api/v1/sessions/${encodeURIComponent(sessionId)}`),
    api(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`),
    api(`/api/v1/sessions/${encodeURIComponent(sessionId)}/graph`),
    api(`/api/v1/sessions/${encodeURIComponent(sessionId)}/agent`).catch(() => null),
  ]);
  state.session = session;
  state.messages = messagesPayload.messages || [];
  state.graph = graph;
  state.agent = agent;
  state.events = session.events || [];
  state.task = null;
  if (session.task_id) {
    const view = await api(`/api/v1/delivery/views/${encodeURIComponent(session.task_id)}`);
    state.deliveryViewByTask[session.task_id] = view;
    state.task = view.task;
  }
  if (state.task) state.taskStatusBySession[sessionId] = state.task.status;

  // Keep inbox filters useful when task lands in review / dead letter.
  if (state.task && state.task.status === "review" && state.sessionFilter === "active") {
    state.sessionFilter = "review";
    localStorage.setItem(FILTER_KEY, "review");
  } else if (state.task && TERMINAL.has(state.task.status) && state.sessionFilter === "active") {
    state.sessionFilter = "terminal";
    localStorage.setItem(FILTER_KEY, "terminal");
  }

  renderMessages();
  renderHeader();
  renderSessions();
  renderTrajectory();
  renderReview();
  if (state.detailsOpen && state.detailsMode === "graph") renderGraphDetails();

  if (state.workbenchView === "delivery" && state.task && state.task.status === "review" && !state.detailsOpen) {
    openGraphDetails();
  }
  maybePoll();
}

async function selectSession(sessionId) {
  state.selectedSessionId = sessionId;
  state.expandedRailId = null;
  state.railAutoOpened = false;
  state.showAllMessages = false;
  state.teamExpanded = false;
  $("#more-menu").hidden = true;
  renderSessions();
  try {
    await refreshSelected();
    setView(state.view || "chat");
    const scroller = document.querySelector("[data-conversation-scroll]");
    if (scroller) scroller.scrollTop = 0;
  } catch (err) {
    toast(err.message);
  }
}

async function createSession() {
  const projectId = currentProjectId();
  if (!projectId) {
    $("#workspace-dialog").showModal();
    return toast("请先选择工作区");
  }
  try {
    setWorkbenchView("delivery");
    const session = await api("/api/v1/sessions", {
      method: "POST",
      headers: headers("session"),
      body: JSON.stringify({
        project_id: projectId,
        title: "新交付",
        profile_id: currentProfileId(),
      }),
    });
    toast("已新建会话");
    await loadShell();
    await selectSession(session.id);
    setView("chat");
    $("#prompt").focus();
    return session;
  } catch (err) {
    toast(err.message);
    return null;
  }
}

function clearLocalSession() {
  stopPoll();
  state.selectedSessionId = null;
  state.session = null;
  state.task = null;
  state.messages = [];
  state.events = [];
  state.graph = null;
  state.agent = null;
  state.selectedEvent = null;
  closeDetails();
  setView("chat");
  renderSessions();
  renderMessages();
  renderHeader();
  renderTrajectory();
  renderCollabBar();
  renderRecoveryBanner();
}

async function sendPrompt() {
  if (state.sending || taskBusy()) return;
  if (state.task && state.task.status === "review") {
    return toast("请先完成验收，再提下一个需求");
  }
  const request = ($("#prompt").value || "").trim();
  if (!request) return toast("请输入需求");
  const projectId = currentProjectId();
  if (!projectId) {
    $("#workspace-dialog").showModal();
    return toast("请先选择工作区");
  }

  state.sending = true;
  syncComposerEnabled();
  state.messages = [...state.messages, { role: "user", kind: "user/message", content: request, pending: true }];
  $("#empty-state").hidden = true;
  $("#message-stream").hidden = false;
  setView("chat");
  renderMessages();
  $("#prompt").value = "";

  try {
    const scopes = ($("#write-scope").value || "").split(",").map((s) => s.trim()).filter(Boolean);
    const body = {
      request,
      requirement_id: ($("#requirement-id").value || "").trim(),
      write_scope: scopes,
      execute_code: $("#execute-code").checked,
    };
    let task;
    if (state.selectedSessionId && state.session && sessionWritable()) {
      task = await api(`/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/prompt`, {
        method: "POST",
        headers: headers("prompt"),
        body: JSON.stringify(body),
      });
    } else {
      const session = await api("/api/v1/sessions", {
        method: "POST",
        headers: headers("session"),
        body: JSON.stringify({
          project_id: projectId,
          title: request.slice(0, 120),
          profile_id: currentProfileId(),
        }),
      });
      state.selectedSessionId = session.id;
      task = await api(`/api/v1/sessions/${encodeURIComponent(session.id)}/prompt`, {
        method: "POST",
        headers: headers("prompt"),
        body: JSON.stringify(body),
      });
    }
    toast("交付已启动");
    await loadShell();
    if (task.session_id) {
      await selectSession(task.session_id);
      openGraphDetails();
    }
  } catch (err) {
    toast(err.message);
    if (state.selectedSessionId) await refreshSelected().catch(() => {});
  } finally {
    state.sending = false;
    syncComposerEnabled();
  }
}

async function setSessionStatus(status) {
  const sessionId = requireSession();
  if (!sessionId) return;
  try {
    await api(`/api/v1/sessions/${encodeURIComponent(sessionId)}/status`, {
      method: "POST",
      headers: headers("status"),
      body: JSON.stringify({ status }),
    });
    toast(`会话已${statusLabel(status)}`);
    $("#more-menu").hidden = true;
    await loadShell();
    await refreshSelected();
  } catch (err) {
    toast(err.message);
  }
}

async function forkSession() {
  const sessionId = requireSession();
  if (!sessionId) return;
  try {
    const forked = await api(`/api/v1/sessions/${encodeURIComponent(sessionId)}/fork`, {
      method: "POST",
      headers: headers("fork"),
      body: JSON.stringify({ title: `再试 · ${shortTitle(state.session && state.session.title)}` }),
    });
    $("#execute-code").checked = true;
    document.querySelectorAll(".chip").forEach((el) => el.classList.remove("active"));
    const std = document.querySelector('.chip[data-mode="standard"]');
    if (std) std.classList.add("active");
    toast("已复制会话，并启用「改代码」");
    $("#more-menu").hidden = true;
    await loadShell();
    await selectSession(forked.id);
  } catch (err) {
    toast(err.message);
  }
}

async function exportSession() {
  const sessionId = requireSession();
  if (!sessionId) return;
  try {
    const bundle = await api(`/api/v1/sessions/${encodeURIComponent(sessionId)}/export`);
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${sessionId}.export.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast("证据包已下载");
    $("#more-menu").hidden = true;
  } catch (err) {
    toast(err.message);
  }
}

function workbenchStatusCounts() {
  const counts = { active: 0, review: 0, blocked: 0, completed: 0 };
  state.sessions.forEach((session) => {
    const status = sessionTaskStatus(session) || session.status;
    if (BUSY.has(status) || status === "active" || status === "paused") counts.active += 1;
    if (status === "review") counts.review += 1;
    if (status === "failed" || status === "dead_letter" || status === "rework") counts.blocked += 1;
    if (status === "completed") counts.completed += 1;
  });
  return counts;
}

function renderDashboard() {
  const now = new Date();
  $("#dashboard-date").textContent = new Intl.DateTimeFormat("zh-CN", {
    month: "long", day: "numeric", weekday: "long",
  }).format(now);
  const project = state.projects.find((item) => item.id === currentProjectId()) || state.projects[0];
  $("#dashboard-project").textContent = project ? project.name : "尚未选择工作区";
  const course = state.courseStatus || {};
  if (course.loading) {
    $("#dashboard-course").textContent = "课程状态检查中…";
  } else if (course.error) {
    $("#dashboard-course").textContent = "课程状态不可用 · 刷新重试";
  } else {
    $("#dashboard-course").textContent = course.course_ready
      ? `L01–L${String(course.lesson_count || 16).padStart(2, "0")} 跟跑就绪`
      : (course.contract_valid ? "课程合同有效 · 基线待补" : "课程合同待修复");
  }

  const counts = workbenchStatusCounts();
  $("#metric-active").textContent = counts.active;
  $("#metric-review").textContent = counts.review;
  $("#metric-blocked").textContent = counts.blocked;
  $("#metric-completed").textContent = counts.completed;

  const actionable = state.sessions.find((session) => sessionTaskStatus(session) === "review")
    || state.sessions.find((session) => ["failed", "dead_letter", "rework"].includes(sessionTaskStatus(session)));
  const attention = $("#attention-panel");
  if (actionable) {
    const actionStatus = sessionTaskStatus(actionable);
    const needsReview = actionStatus === "review";
    attention.className = `attention-panel ${needsReview ? "warn" : "bad"}`;
    attention.innerHTML = `
      <span class="attention-icon">${needsReview ? "!" : "×"}</span>
      <span class="attention-copy"><b>${needsReview ? "有交付等待你的终审" : "有失败需要人工处理"}</b><span>${esc(shortTitle(actionable.title))} · ${esc(statusLabel(actionStatus))}</span></span>
      <button type="button" class="${needsReview ? "send" : "ghost-btn"}" id="attention-open">${needsReview ? "现在处理" : "查看失败证据"}</button>`;
    $("#attention-open").addEventListener("click", async () => {
      setWorkbenchView("delivery");
      await selectSession(actionable.id);
    });
  } else {
    attention.className = "attention-panel";
    attention.innerHTML = `<span class="attention-icon">✓</span><span class="attention-copy"><b>目前没有需要你处理的阻断项</b><span>可以记录一个新的业务信号，先判断是否值得开发。</span></span><button type="button" class="ghost-btn" id="attention-new">新建事项</button>`;
    $("#attention-new").addEventListener("click", () => setWorkbenchView("decision"));
  }

  const recent = state.sessions.slice(0, 5);
  $("#today-task-list").innerHTML = recent.length ? recent.map((session) => {
    const status = sessionTaskStatus(session) || session.status || "idle";
    return `<button type="button" class="today-task" data-dashboard-session="${esc(session.id)}">
      <span class="task-dot ${sessionDotClass(session)}"></span>
      <span><b>${esc(shortTitle(session.title))}</b><small>${esc(statusLabel(status))} · ${esc((session.updated_at || "").slice(0, 16))}</small></span>
      <span class="task-arrow">→</span>
    </button>`;
  }).join("") : `<div class="empty-dashboard">还没有交付记录。先在上方写下第一个 FlowERP 小需求。</div>`;
  document.querySelectorAll("[data-dashboard-session]").forEach((button) => {
    button.addEventListener("click", async () => {
      setWorkbenchView("delivery");
      await selectSession(button.dataset.dashboardSession);
    });
  });

  const toolIds = new Set((state.tools || []).map((tool) => tool.id));
  const runtime = state.pluginRuntime || {};
  const activePlugins = runtime.active_plugins || [];
  const runtimeReady = activePlugins.length > 0 && !(runtime.plugins || []).some((item) => item.error);
  const platformReady = Boolean(state.health);
  const deliveryViews = deliveryViewsByRecency();
  const specEvidenceCount = deliveryViews.filter((view) => view.spec && view.spec.available).length;
  const evalEvidenceCount = deliveryViews.filter((view) => view.eval && view.eval.available).length;
  const harnessEvidenceCount = deliveryViews.filter((view) => view.eval && view.eval.report_path && view.eval.report_sha256).length;
  const loopEvidence = Boolean(state.agent && Number(state.agent.turns_completed || 0) > 0);
  const graphEvidence = Boolean(state.graph && Array.isArray(state.graph.nodes) && state.graph.nodes.length);
  const capabilities = [
    ["Spec", toolIds.has("spec.read"), specEvidenceCount > 0, `${specEvidenceCount} 个 Task`],
    ["Eval", toolIds.has("eval.blocking"), evalEvidenceCount > 0, `${evalEvidenceCount} 份结果`],
    ["Harness", runtimeReady, harnessEvidenceCount > 0, `${harnessEvidenceCount} 份可校验报告`],
    ["Loop", platformReady, loopEvidence, loopEvidence ? `${state.agent.turns_completed} 轮轨迹` : "当前无运行轨迹"],
    ["Graph", platformReady, graphEvidence, graphEvidence ? "已读取当前任务图" : "选择任务后验证"],
    ["API", platformReady, platformReady, "健康接口已响应"],
    ["Web", true, true, "当前页面已加载"],
  ];
  $("#capability-map").innerHTML = capabilities.map(([name, available, evidenced, detail]) => {
    const evidenceState = evidenced ? "ready" : (available ? "installed" : "attention");
    const evidenceText = evidenced ? `已验证 · ${detail}` : (available ? `可用未使用 · ${detail}` : "未配置");
    return `<button type="button" class="capability-node ${evidenceState}" data-capability="${esc(name)}"><b>${name}</b><small>${esc(evidenceText)}</small></button>`;
  }).join("");
  document.querySelectorAll("[data-capability]").forEach((button) => {
    button.addEventListener("click", () => activateCapability(button.dataset.capability));
  });
  const runtimeChip = $("#runtime-summary");
  runtimeChip.textContent = runtimeReady ? `${activePlugins.length} 个组件已加载 · 非交付结论` : "运行时未就绪";
  runtimeChip.className = `status-chip ${runtimeReady ? "good" : "warn"}`;

  const latestView = latestEvalView();
  const latestEval = latestView ? latestView.eval : null;
  const qualityReady = Boolean(latestView && latestEval.decision === "pass"
    && latestEval.blocking_failed === 0 && latestView.integrity && latestView.integrity.truthful);
  $("#quality-dot").className = `live-dot ${qualityReady ? "good" : "bad"}`;
  $("#quality-summary").innerHTML = latestView ? `
    <div class="quality-line"><span>Task</span><b>${esc(latestView.task_id)}</b></div>
    <div class="quality-line"><span>Blocking</span><b>${latestEval.blocking_passed} 通过 / ${latestEval.blocking_failed} 失败</b></div>
    <div class="quality-line"><span>结论</span><b>${esc(latestEval.decision || "未判定")} · ${latestView.integrity.truthful ? "证据一致" : "证据需核对"}</b></div>
    <div class="quality-line"><span>报告</span><b>${esc(latestEval.report_path || "未保存报告")}</b></div>
    <div class="quality-line"><span>SHA-256</span><b>${esc((latestEval.report_sha256 || "未提供").slice(0, 12))}${latestEval.report_sha256 ? "…" : ""}</b></div>`
    : `<div class="empty-dashboard">尚无真实 Eval 报告。点右上角「复验 Blocking Eval」会创建一次只验证、不改代码的任务。</div>`;
  const qualityOpen = $("#quality-open-task");
  if (qualityOpen) {
    qualityOpen.disabled = !latestView;
    qualityOpen.onclick = () => latestView && openTaskDelivery(latestView.task_id);
  }

  const rules = [
    ["可用库存不得为负，预占必须原子化", ["stock_never_negative"], "#inventory"],
    ["同一个入库幂等键只能生效一次", ["receiving_is_idempotent"], "#inventory"],
    ["订单状态只能按状态机迁移，取消释放预占", ["illegal_transition_is_blocked", "cancellation_releases_reservation"], "#sales"],
    ["采购补货必须人工审批后才能入库", ["purchase_requires_approval"], "#purchases"],
    ["任务、Eval 与反馈可追溯，失败不能伪装成功", ["delivery_evidence_and_review_controls"], "#delivery"],
  ];
  const latestCases = new Map(((latestEval && latestEval.cases) || []).map((item) => [item.name, item]));
  let verifiedRuleCount = 0;
  const flowerp = state.flowerpStatus || {};
  const flowerpLive = $("#flowerp-live");
  if (flowerpLive) {
    flowerpLive.innerHTML = `
    <a class="flowerp-chip ${flowerp.live ? "on" : ""}" href="${esc(flowerpHref("#dashboard"))}" target="_blank" rel="noopener">
      ${flowerp.live ? "FlowERP 在线 · 打开产品" : "FlowERP 未探测到 · 仍可打开 :8000"}
    </a>
    ${(flowerp.pages || []).map((page) =>
      `<a class="flowerp-chip" href="${esc(page.href)}" target="_blank" rel="noopener">${esc(page.label)}</a>`
    ).join("")}`;
  }
  $("#flow-rules").innerHTML = rules.map(([rule, caseNames, hash]) => {
    const cases = caseNames.map((name) => latestCases.get(name)).filter(Boolean);
    const failed = cases.some((item) => !item.passed);
    const verified = cases.length === caseNames.length && cases.every((item) => item.passed);
    if (verified) verifiedRuleCount += 1;
    const stateClass = failed ? "failed" : (verified ? "" : "unverified");
    const mark = failed ? "×" : (verified ? "✓" : "·");
    const label = failed ? "最近验证失败" : (verified ? "最近验证通过" : "本次无验证证据");
    return `<a class="rule-item ${stateClass}" href="${esc(flowerpHref(hash))}" target="_blank" rel="noopener"><span>${mark}</span><div>${esc(rule)}<small>${esc(label)} · 打开产品页</small></div></a>`;
  }).join("");
  $("#flow-rules-status").textContent = latestEval ? `${verifiedRuleCount} / ${rules.length} 最近验证` : "等待 Eval";
  $("#flow-rules-status").className = `status-chip ${verifiedRuleCount === rules.length ? "good" : "warn"}`;

  const feedback = state.feedbackSummary || {};
  const evolution = state.evolutionSummary || {};
  $("#evolution-count").textContent = `${evolution.total || 0} 条`;
  $("#feedback-summary").innerHTML = `
    <div class="evolution-row"><span>待复核反馈</span><b>${feedback.pending_review || 0}</b></div>
    <div class="evolution-row"><span>已接受反馈</span><b>${feedback.accepted || 0}</b></div>
    <div class="evolution-row"><span>资产已变更</span><b>${evolution.asset_changed || 0}</b></div>
    <div class="evolution-row"><span>迁移验证完成</span><b>${evolution.verified || 0}</b></div>`;
  const feedbackItems = (feedback.items || []).slice(0, 8);
  const pending = feedbackItems.filter((item) => item.status === "pending_review");
  const accepted = feedbackItems.filter((item) => item.status === "accepted");
  const feedbackBox = $("#feedback-actions");
  if (feedbackBox) {
    feedbackBox.innerHTML = pending.length ? pending.map((item) => `
    <article class="feedback-item">
      <div><b>${esc(item.id)}</b><small>${esc(item.conclusion || item.source || "")}</small></div>
      <label>判断依据<textarea rows="2" data-feedback-note="${esc(item.id)}" placeholder="为什么接受或驳回"></textarea></label>
      <div class="initiative-actions">
        <button type="button" class="send" data-feedback-review="accept" data-feedback-id="${esc(item.id)}">接受</button>
        <button type="button" class="ghost-btn danger" data-feedback-review="reject" data-feedback-id="${esc(item.id)}">驳回</button>
        <button type="button" class="linkish" data-feedback-open="${esc(item.task_id || "")}">打开交付</button>
      </div>
    </article>`).join("") : `<div class="empty-dashboard">${accepted.length ? "没有待审核反馈。已接受的可提升为进化候选。" : "还没有反馈。失败任务会自动沉淀观察，人审后才能进入进化。"}</div>`;
    if (accepted.length) {
      feedbackBox.innerHTML += accepted.slice(0, 3).map((item) => `
      <article class="feedback-item accepted">
        <div><b>${esc(item.id)}</b><small>已接受 · ${esc(item.conclusion || "")}</small></div>
        <div class="initiative-actions">
          <button type="button" class="ghost-btn" data-evolution-create="${esc(item.id)}">建立进化候选</button>
          <button type="button" class="linkish" data-feedback-open="${esc(item.task_id || "")}">打开交付</button>
        </div>
      </article>`).join("");
    }
    feedbackBox.querySelectorAll("[data-feedback-review]").forEach((button) => {
      button.addEventListener("click", () => reviewFeedbackItem(button.dataset.feedbackId, button.dataset.feedbackReview));
    });
    feedbackBox.querySelectorAll("[data-evolution-create]").forEach((button) => {
      button.addEventListener("click", () => proposeEvolution(button.dataset.evolutionCreate));
    });
    feedbackBox.querySelectorAll("[data-feedback-open]").forEach((button) => {
      button.addEventListener("click", () => button.dataset.feedbackOpen && openTaskDelivery(button.dataset.feedbackOpen));
    });
  }

  const plugins = runtime.plugins || state.plugins || [];
  $("#plugin-summary").innerHTML = plugins.length ? plugins.slice(0, 8).map((plugin) => {
    const on = plugin.state === "active" || plugin.enabled;
    return `<span class="plugin-pill ${on ? "on" : ""}">${esc(plugin.name || plugin.id)} · ${on ? "active" : "off"}</span>`;
  }).join("") : `<span class="muted">暂无插件运行证据</span>`;

  $("#recent-evidence").innerHTML = deliveryViews.slice(0, 5).map((view) => {
    const task = view.task || {};
    const report = view.eval && view.eval.report_path
      ? view.eval.report_path
      : (view.spec && view.spec.available ? "只有 Spec，尚无 Eval 报告" : "尚未生成可核对证据");
    return `<button type="button" class="evidence-item" data-evidence-task="${esc(task.id || "")}">
      <span class="task-dot ${TERMINAL.has(task.status) ? (task.status === "completed" ? "done" : "bad") : "busy"}"></span>
      <span><b>${esc(task.requirement_id || task.id || "交付证据")}</b><small>${esc(report)}</small></span>
      <small>${esc(statusLabel(task.status || "idle"))}</small>
    </button>`;
  }).join("") || `<div class="empty-dashboard">证据会在首次交付后出现在这里。也可以先点「复验 Blocking Eval」。</div>`;
  document.querySelectorAll("[data-evidence-task]").forEach((button) => {
    button.addEventListener("click", () => button.dataset.evidenceTask && openTaskDelivery(button.dataset.evidenceTask));
  });

  const lessons = state.courseLessons || [];
  const courseChip = $("#course-ready-chip");
  if (courseChip) {
    courseChip.textContent = course.loading
      ? "课程合同读取中"
      : (course.course_ready ? `${lessons.length || course.lesson_count || 16} 讲就绪` : "合同待修复");
    courseChip.className = `status-chip ${course.course_ready ? "good" : "warn"}`;
  }
  const lessonBox = $("#course-lesson-list");
  if (lessonBox) {
    lessonBox.innerHTML = lessons.length ? lessons.map((lesson) => {
      const pad = String(lesson.number).padStart(2, "0");
      return `<button type="button" class="course-lesson" data-lesson="${esc(lesson.number)}">
        <b>L${pad} ${esc(lesson.title)}</b>
        <small>${esc(lesson.erp_increment)} · ${esc(lesson.workbench_increment)}</small>
      </button>`;
    }).join("") : `<div class="empty-dashboard">${course.error || "课程合同读取中…"}</div>`;
    lessonBox.querySelectorAll("[data-lesson]").forEach((button) => {
      button.addEventListener("click", () => startLessonInitiative(button.dataset.lesson));
    });
  }
}

function linesFrom(selector, separator = /\r?\n/) {
  return ($(selector).value || "").split(separator).map((value) => value.trim()).filter(Boolean);
}

async function createInitiative() {
  const projectId = currentProjectId();
  if (!projectId) {
    $("#workspace-dialog").showModal();
    return;
  }
  if (!($("#initiative-source").value || "").trim()) {
    $("#initiative-source").value = "工作台业务信号快录";
  }
  try {
    const created = await api("/api/v1/initiatives", {
      method: "POST",
      headers: headers("initiative"),
      body: JSON.stringify({
        title: ($("#initiative-title").value || "").trim(),
        raw_signal: ($("#initiative-signal").value || "").trim(),
        source: ($("#initiative-source").value || "").trim(),
        problem_statement: ($("#initiative-problem").value || "").trim(),
        goal: ($("#initiative-goal").value || "").trim(),
        acceptance: linesFrom("#initiative-acceptance"),
        evidence: linesFrom("#initiative-evidence"),
        assumptions: linesFrom("#initiative-assumptions"),
        affected_areas: linesFrom("#initiative-areas", /[,，\r\n]+/),
        reversibility: $("#initiative-reversibility").value,
        non_goals: linesFrom("#initiative-non-goals"),
        constraints: linesFrom("#initiative-constraints"),
        project_id: projectId,
        owner: actor(),
      }),
    });
    state.initiatives.unshift(created);
    renderInitiatives();
    ["title", "signal", "source", "problem", "goal", "acceptance", "evidence", "assumptions", "areas", "non-goals", "constraints"]
      .forEach((field) => { $("#initiative-" + field).value = ""; });
    toast(`事项已保存，风险通道：${created.risk_lane}`);
  } catch (err) {
    toast(err.message);
  }
}

async function reviseInitiative(id) {
  const item = state.initiatives.find((candidate) => candidate.id === id);
  if (!item) return;
  const value = (name) => document.querySelector(`[data-initiative-revise-${name}="${id}"]`).value;
  try {
    const revised = await api(`/api/v1/initiatives/${id}/revise`, {
      method: "POST",
      headers: headers("initiative-revise"),
      body: JSON.stringify({
        expected_version: item.version,
        problem_statement: value("problem").trim(),
        goal: value("goal").trim(),
        evidence: value("evidence").split(/\r?\n/).map((entry) => entry.trim()).filter(Boolean),
        acceptance: value("acceptance").split(/\r?\n/).map((entry) => entry.trim()).filter(Boolean),
        affected_areas: value("areas").split(/[,，\r\n]+/).map((entry) => entry.trim()).filter(Boolean),
        reversibility: value("reversibility"),
      }),
    });
    state.initiatives = state.initiatives.map((candidate) => candidate.id === id ? revised : candidate);
    renderInitiatives();
    toast(`修订已保存，当前风险通道：${revised.risk_lane}`);
  } catch (err) {
    toast(err.message);
  }
}

async function decideInitiative(id, decision) {
  const item = state.initiatives.find((candidate) => candidate.id === id);
  if (!item) return;
  const rationale = document.querySelector(`[data-initiative-rationale="${id}"]`).value.trim();
  if (!rationale) return toast("请先写下决定依据；工作台不会替你编造理由");
  try {
    const decided = await api(`/api/v1/initiatives/${id}/decision`, {
      method: "POST",
      headers: headers("initiative-decision"),
      body: JSON.stringify({
        decision, rationale, expected_version: item.version,
        reviewer: document.querySelector(`[data-initiative-reviewer="${id}"]`).value.trim(),
        review_trigger: document.querySelector(`[data-initiative-trigger="${id}"]`).value.trim(),
        success_metric: document.querySelector(`[data-initiative-metric="${id}"]`).value.trim(),
        stop_condition: document.querySelector(`[data-initiative-stop="${id}"]`).value.trim(),
      }),
    });
    state.initiatives = state.initiatives.map((candidate) => candidate.id === id ? decided : candidate);
    renderInitiatives();
    toast(`已记录决定：${decision}`);
  } catch (err) {
    toast(err.message);
  }
}

async function promoteInitiative(id) {
  const item = state.initiatives.find((candidate) => candidate.id === id);
  if (!item) return;
  try {
    const promoted = await api(`/api/v1/initiatives/${id}/delivery`, {
      method: "POST",
      headers: headers("initiative-delivery"),
      body: JSON.stringify({
        expected_version: item.version,
        execute_code: $("#execute-code").checked,
        write_scope: linesFrom("#write-scope", /[,，\r\n]+/),
        profile_id: currentProfileId(),
      }),
    });
    toast("已生成交付合同并进入 Harness");
    await loadShell();
    setWorkbenchView("delivery");
    if (promoted.session_id) await selectSession(promoted.session_id);
  } catch (err) {
    toast(err.message);
  }
}

function renderInitiatives() {
  const project = state.projects.find((candidate) => candidate.id === currentProjectId());
  $("#initiative-project").textContent = project ? project.name : "未选择";
  $("#initiative-count").textContent = `${state.initiatives.length} 项`;
  const box = $("#initiative-list");
  if (!state.initiatives.length) {
    box.innerHTML = `<div class="empty-dashboard">还没有事项。先保存原始 Signal，再补证据和边界。</div>`;
    return;
  }
  const decisionLabel = { build: "Build", experiment: "Experiment", defer: "Defer", reject: "Reject", stop: "Stop" };
  box.innerHTML = state.initiatives.map((item) => {
    const readiness = item.readiness || {};
    const missing = [...(readiness.decision_missing || []), ...(readiness.delivery_missing || [])];
    const decided = Boolean(item.decision);
    const superseded = item.status === "superseded" && item.superseded_by;
    const displayTitle = superseded ? "历史编码损坏记录（已替代）" : item.title;
    const displaySignal = superseded
      ? `原始中文在写入前已损坏；请以 ${item.superseded_by} 为准。原始事件仍保留用于审计。`
      : item.raw_signal;
    return `<article class="initiative-item ${esc(item.risk_lane)} ${superseded ? "superseded" : ""}">
      <div class="initiative-item-head">
        <div><h3>${esc(displayTitle)}</h3><p>${esc(displaySignal)}</p></div>
        <span class="risk-badge">${esc(item.risk_lane)} · v${esc(item.version)}</span>
      </div>
      <div class="initiative-facts">
        <span>Owner ${esc(item.owner)}</span><span>证据 ${(item.evidence || []).length}</span>
        <span>${esc(item.status)}</span>${item.decision ? `<span>决定 ${esc(decisionLabel[item.decision] || item.decision)}</span>` : ""}
      </div>
      ${missing.length && !decided ? `<div class="initiative-blockers">当前缺口：${esc([...new Set(missing)].join("、"))}</div>` : ""}
      ${!decided ? `<details class="initiative-revise"><summary>补充证据与交付合同</summary>
        <div class="initiative-decision-fields">
          <label>问题陈述<textarea rows="2" data-initiative-revise-problem="${esc(item.id)}">${esc(item.problem_statement || "")}</textarea></label>
          <label>交付目标<textarea rows="2" data-initiative-revise-goal="${esc(item.id)}">${esc(item.goal || "")}</textarea></label>
          <label>证据（每行一条）<textarea rows="2" data-initiative-revise-evidence="${esc(item.id)}">${esc((item.evidence || []).map((entry) => entry.content).join("\n"))}</textarea></label>
          <label>验收（每行一条）<textarea rows="2" data-initiative-revise-acceptance="${esc(item.id)}">${esc((item.acceptance || []).join("\n"))}</textarea></label>
          <label>影响领域<input data-initiative-revise-areas="${esc(item.id)}" value="${esc((item.affected_areas || []).join(", "))}"></label>
          <label>可恢复性<select data-initiative-revise-reversibility="${esc(item.id)}">
            ${["easy", "partial", "hard"].map((value) => `<option value="${value}" ${item.reversibility === value ? "selected" : ""}>${value}</option>`).join("")}
          </select></label>
        </div>
        <div class="initiative-actions"><button type="button" class="ghost-btn" data-initiative-revise="${esc(item.id)}">保存修订并重新路由</button></div>
      </details>` : ""}
      ${!decided ? `<div class="initiative-decision-fields">
        <label>决定依据<textarea rows="2" data-initiative-rationale="${esc(item.id)}" placeholder="证据、备选与权衡；必填"></textarea></label>
        <label>Reviewer<input data-initiative-reviewer="${esc(item.id)}" placeholder="Strict Build 必填"></label>
        <label>复查触发器<input data-initiative-trigger="${esc(item.id)}" placeholder="Defer 必填"></label>
        <label>实验成功指标<input data-initiative-metric="${esc(item.id)}" placeholder="Experiment 必填"></label>
        <label>实验停止条件<input data-initiative-stop="${esc(item.id)}" placeholder="Experiment 必填"></label>
      </div>
      <div class="initiative-actions">
        ${["build", "experiment", "defer", "reject", "stop"].map((decision) =>
          `<button type="button" class="${["reject", "stop"].includes(decision) ? "ghost-btn danger" : "ghost-btn"}" data-initiative-decision="${decision}" data-initiative-id="${esc(item.id)}">${decisionLabel[decision]}</button>`
        ).join("")}
      </div>` : ""}
      ${item.decision === "build" && !item.linked_task_id ? `<div class="initiative-actions"><button type="button" class="send" data-initiative-promote="${esc(item.id)}">生成交付合同并进入 Harness</button></div>` : ""}
      ${superseded
        ? `<div class="initiative-linked">原记录已由 ${esc(item.superseded_by)} 替代；原 Task ${esc(item.linked_task_id || "—")} 仅作为历史证据保留。</div>`
        : item.linked_task_id ? `<div class="initiative-linked">已关联 <button type="button" class="linkish" data-open-task="${esc(item.linked_task_id)}">${esc(item.linked_task_id)}</button>；后续质量结论以 Task / Eval / 人审证据为准。</div>` : ""}
    </article>`;
  }).join("");
  document.querySelectorAll("[data-initiative-decision]").forEach((button) => {
    button.addEventListener("click", () => decideInitiative(button.dataset.initiativeId, button.dataset.initiativeDecision));
  });
  document.querySelectorAll("[data-initiative-revise]").forEach((button) => {
    button.addEventListener("click", () => reviseInitiative(button.dataset.initiativeRevise));
  });
  document.querySelectorAll("[data-initiative-promote]").forEach((button) => {
    button.addEventListener("click", () => promoteInitiative(button.dataset.initiativePromote));
  });
  document.querySelectorAll("[data-open-task]").forEach((button) => {
    button.addEventListener("click", () => openTaskDelivery(button.dataset.openTask));
  });
}

function exportWorkbenchSnapshot() {
  const project = state.projects.find((item) => item.id === currentProjectId()) || null;
  const snapshot = {
    schema_version: "harness.workbench-snapshot/v1",
    exported_at: new Date().toISOString(),
    project,
    course: state.courseStatus,
    metrics: workbenchStatusCounts(),
    runtime: state.pluginRuntime,
    feedback: state.feedbackSummary,
    evolutions: state.evolutionSummary,
    initiatives: state.initiatives.slice(0, 100),
    tasks: state.tasks.slice(0, 100),
    sessions: state.sessions.slice(0, 100),
    note: "不包含密钥和运行数据库；这是用于复盘的只读证据快照。",
  };
  const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `flow-erp-workbench-${new Date().toISOString().slice(0, 10)}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
  toast("工作台证据快照已导出");
}

async function loadCourseStatus() {
  const requestId = ++courseStatusRequestId;
  if (courseStatusController) courseStatusController.abort();
  const controller = new AbortController();
  courseStatusController = controller;
  state.courseStatus = {
    loading: true, course_ready: false, contract_valid: null, checked_at: null,
  };
  renderDashboard();
  const timeout = window.setTimeout(() => controller.abort(), COURSE_STATUS_TIMEOUT_MS);
  try {
    const result = await api("/api/v1/course/status", { signal: controller.signal });
    if (requestId !== courseStatusRequestId) return;
    state.courseStatus = { ...result, loading: false, error: null };
  } catch (err) {
    if (requestId !== courseStatusRequestId) return;
    state.courseStatus = {
      loading: false,
      course_ready: false,
      contract_valid: null,
      checked_at: null,
      error: err && err.name === "AbortError" ? "课程状态检查超时" : (err.message || "课程状态请求失败"),
    };
  } finally {
    window.clearTimeout(timeout);
    if (requestId === courseStatusRequestId) {
      courseStatusController = null;
      renderDashboard();
    }
  }
}

async function loadShell() {
  void loadCourseStatus();
  const profileId = currentProfileId();
  const [health, projects, sessions, plugins, profiles, tools, capabilities, deliveryViews, employees,
    pluginRuntime, pluginEvents, feedbackSummary, evolutionSummary, tasks, initiatives, courseLessons, flowerpStatus,
    dumpConfig, composition] = await Promise.all([
    api("/api/v1/health"),
    api("/api/v1/projects"),
    api("/api/v1/sessions?limit=100"),
    api("/api/v1/plugins"),
    api("/api/v1/profiles"),
    api("/api/v1/tools"),
    api("/api/v1/capabilities"),
    api("/api/v1/delivery/views?limit=200").catch(() => ({ items: [] })),
    api("/api/v1/employees").catch(() => ({ items: DEFAULT_EMPLOYEES })),
    api(`/api/v1/profiles/${encodeURIComponent(profileId)}/runtime`).catch(() => null),
    api(`/api/v1/plugin-events?profile_id=${encodeURIComponent(profileId)}&limit=20`).catch(() => ({ items: [] })),
    api("/api/v1/feedback").catch(() => ({ total: 0, items: [] })),
    api("/api/v1/evolutions?limit=50").catch(() => ({ total: 0, items: [] })),
    api("/api/v1/tasks?limit=100").catch(() => ({ items: [] })),
    api("/api/v1/initiatives?limit=100").catch(() => ({ items: [] })),
    api("/api/v1/course/lessons").catch(() => ({ items: [] })),
    api("/api/v1/flowerp/status").catch(() => ({ live: false, url: "http://127.0.0.1:8000", pages: [] })),
    api(`/api/v1/dump-config?profile_id=${encodeURIComponent(profileId)}`).catch(() => null),
    api(`/api/v1/profiles/${encodeURIComponent(profileId)}/composition`).catch(() => null),
  ]);
  state.projects = projects.items || [];
  state.sessions = sessions.items || [];
  state.plugins = plugins.items || [];
  state.profiles = profiles.items || [];
  state.tools = tools.items || [];
  state.capabilities = capabilities;
  state.health = health;
  state.pluginRuntime = pluginRuntime;
  state.pluginEvents = pluginEvents.items || [];
  state.feedbackSummary = feedbackSummary;
  state.evolutionSummary = evolutionSummary;
  state.tasks = tasks.items || [];
  state.initiatives = initiatives.items || [];
  state.courseLessons = courseLessons.items || [];
  state.flowerpStatus = flowerpStatus;
  state.dumpConfig = dumpConfig;
  state.composition = composition;
  state.employees = (employees.items && employees.items.length) ? employees.items : DEFAULT_EMPLOYEES.slice();
  ingestDeliveryViews(deliveryViews.items || []);
  if (!$("#proj-root").value && health.repository_root) {
    $("#proj-root").value = health.repository_root;
  }
  renderEmployeeChips();
  renderActorOptions();
  renderActorHint();
  renderSessions();
  renderSettings();
  renderCollabBar();
  renderDashboard();
  renderInitiatives();
}

function renderEmployeeChips() {
  const box = $("#employee-chips");
  if (!box) return;
  const focus = state.dutyFocus || "";
  const views = [
    { id: "user", label: "你（User）", title: "筛选人类操作；不是独立登录" },
    { id: "agent", label: "Agent", title: "筛选 agent:* 事件" },
    { id: "tool", label: "工具", title: "筛选 tool / approval/ask 事件" },
  ];
  box.innerHTML = views.map((item) =>
    `<button type="button" class="chip-mini ${item.id === focus ? "active focus" : ""}" data-emp="${item.id}" title="${item.title}">${item.label}</button>`
  ).join("");
}

async function registerProject() {
  try {
    const evalCommand = JSON.parse($("#proj-eval").value || "[]");
    const created = await api("/api/v1/projects", {
      method: "POST",
      headers: headers("project"),
      body: JSON.stringify({
        name: ($("#proj-name").value || "FlowERP").trim(),
        root_path: ($("#proj-root").value || "").trim(),
        eval_command: evalCommand,
      }),
    });
    $("#project-id").value = created.id;
    localStorage.setItem(WORKSPACE_KEY, created.id);
    toast("项目已注册");
    await loadShell();
    $("#settings").close();
  } catch (err) {
    toast(err.message);
  }
}

function closeDialog(dialog) {
  if (dialog && dialog.open) dialog.close();
}

function bindDialogDismiss() {
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });
    const form = dialog.querySelector("form");
    if (form) form.addEventListener("submit", (event) => event.preventDefault());
  });
  document.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-close-dialog]");
    if (!btn) return;
    const named = btn.getAttribute("data-close-dialog");
    const dialog = (named && document.getElementById(named)) || btn.closest("dialog");
    closeDialog(dialog);
  });
}

function bind() {
  $("#actor").value = localStorage.getItem(ACTOR_KEY) || "boss";
  rememberActor($("#actor").value);
  renderActorOptions();
  renderActorHint();
  syncSessionFilters();
  syncRoleChips();
  bindDialogDismiss();

  $("#new-chat").addEventListener("click", () => openDecisionWithSignal());
  $("#back-dashboard").addEventListener("click", () => setWorkbenchView("dashboard"));
  $("#decision-back").addEventListener("click", () => setWorkbenchView("dashboard"));
  $("#initiative-create").addEventListener("click", createInitiative);
  document.querySelectorAll("[data-workbench-view]").forEach((button) => {
    button.addEventListener("click", () => setWorkbenchView(button.dataset.workbenchView, button.dataset.target || ""));
  });
  document.querySelectorAll("[data-open-delivery]").forEach((button) => {
    button.addEventListener("click", () => setWorkbenchView("delivery"));
  });
  $("#dashboard-new").addEventListener("click", () => setWorkbenchView("decision"));
  const runEval = $("#dashboard-run-eval");
  if (runEval) runEval.addEventListener("click", runVerifyEval);
  $("#empty-go-decision").addEventListener("click", () => openDecisionWithSignal());
  $("#delivery-new-initiative").addEventListener("click", () => openDecisionWithSignal());
  $("#dashboard-settings").addEventListener("click", () => $("#settings").showModal());
  $("#dashboard-export").addEventListener("click", exportWorkbenchSnapshot);
  $("#toggle-message-density").addEventListener("click", () => {
    state.showAllMessages = !state.showAllMessages;
    renderMessages();
  });
  $("#review-cancel").addEventListener("click", () => $("#review-dialog").close());
  $("#review-confirm").addEventListener("click", submitReview);
  document.querySelectorAll("[data-session-filter]").forEach((button) => {
    button.addEventListener("click", () => openFilteredDelivery(button.dataset.sessionFilter));
  });
  $("#dashboard-send").addEventListener("click", () => {
    const request = ($("#dashboard-prompt").value || "").trim();
    if (!request) return toast("先记录用户、业务或运行现场的原始信号");
    $("#dashboard-prompt").value = "";
    openDecisionWithSignal(request, { source: "工作台业务信号快录" });
  });
  $("#dashboard-prompt").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#dashboard-send").click();
    }
  });
  $("#send").addEventListener("click", sendPrompt);
  $("#open-settings").addEventListener("click", () => $("#settings").showModal());
  $("#open-help").addEventListener("click", () => $("#help-dialog").showModal());
  $("#pick-workspace").addEventListener("click", () => $("#workspace-dialog").showModal());
  $("#register-project").addEventListener("click", registerProject);
  $("#close-details").addEventListener("click", closeDetails);
  $("#btn-fork").addEventListener("click", forkSession);
  $("#btn-pause").addEventListener("click", () => setSessionStatus("paused"));
  $("#btn-resume").addEventListener("click", () => setSessionStatus("active"));
  $("#btn-close").addEventListener("click", () => setSessionStatus("closed"));
  $("#btn-export").addEventListener("click", exportSession);
  $("#btn-details").addEventListener("click", openGraphDetails);
  $("#toggle-sidebar").addEventListener("click", () => {
    state.sidebarCollapsed = !state.sidebarCollapsed;
    syncShell();
  });
  $("#profile-id").addEventListener("change", () => switchProfile($("#profile-id").value));
  const settingsProfile = $("#settings-profile");
  if (settingsProfile) {
    settingsProfile.addEventListener("change", () => switchProfile(settingsProfile.value));
  }
  $("#actor").addEventListener("change", () => commitActor($("#actor").value, { announce: true }));
  $("#actor").addEventListener("blur", () => commitActor($("#actor").value));
  $("#actor").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitActor($("#actor").value, { announce: true });
      $("#actor").blur();
    }
  });
  $("#employee-chips").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-emp]");
    if (btn) focusDuty(btn.dataset.emp);
  });
  $("#actor-recents").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-actor]");
    if (btn) commitActor(btn.dataset.actor, { announce: true });
  });
  document.querySelectorAll("#session-filters .filter").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.sessionFilter = btn.dataset.filter || "all";
      localStorage.setItem(FILTER_KEY, state.sessionFilter);
      renderSessions();
    });
  });
  document.querySelectorAll("#traj-filters [data-traj]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.trajActorFilter = normalizeRuntimeFilter(btn.dataset.traj || "all");
      state.dutyFocus = RUNTIME_FILTERS.includes(state.trajActorFilter) ? state.trajActorFilter : "";
      localStorage.setItem(TRAJ_ACTOR_KEY, state.trajActorFilter);
      renderEmployeeChips();
      renderTrajectory();
    });
  });
  document.querySelectorAll("#traj-source-filters [data-traj-source]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.trajSourceFilter = btn.dataset.trajSource || "all";
      localStorage.setItem(TRAJ_SOURCE_KEY, state.trajSourceFilter);
      renderTrajectory();
    });
  });
  $("#toggle-advanced").addEventListener("click", () => {
    const box = $("#advanced");
    box.hidden = !box.hidden;
    $("#toggle-advanced").textContent = box.hidden ? "选项" : "收起选项";
  });
  $("#more-toggle").addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = $("#more-menu");
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", (event) => {
    if (!$("#session-actions").contains(event.target)) {
      $("#more-menu").hidden = true;
    }
  });

  document.querySelectorAll(".example").forEach((btn) => {
    btn.addEventListener("click", () => {
      openDecisionWithSignal(btn.dataset.prompt || "", extrasFromDataset(btn));
      toast("已保留为原始 Signal；请补来源、证据和决定");
    });
  });

  document.querySelectorAll(".view-tabs [data-view]").forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });

  $("#composer-card").addEventListener("click", (event) => {
    if (!currentProjectId() && event.target.id !== "send") {
      $("#workspace-dialog").showModal();
    }
  });

  $("#execute-code").addEventListener("change", () => {
    document.querySelectorAll(".chip").forEach((el) => el.classList.remove("active"));
    if ($("#execute-code").checked) {
      document.querySelector('.chip[data-mode="standard"]').classList.add("active");
    } else {
      $("#mode-verify").classList.add("active");
    }
  });
  document.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".chip").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      $("#execute-code").checked = btn.dataset.mode === "standard";
    });
  });

  $("#prompt").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendPrompt();
    }
  });
}

bind();
syncShell();
setView("chat");
setWorkbenchView(state.workbenchView);
loadShell()
  .then(() => {
    if (!currentProjectId() && state.projects[0]) {
      $("#project-id").value = state.projects[0].id;
      localStorage.setItem(WORKSPACE_KEY, state.projects[0].id);
      syncComposerEnabled();
      renderInitiatives();
    }
    if (state.sessions[0]) return selectSession(state.sessions[0].id);
    clearLocalSession();
  })
  .catch((err) => {
    console.error("workbench bootstrap failed", err);
    const runtime = $("#runtime-summary");
    if (runtime) {
      runtime.textContent = `加载失败：${err.message}`;
      runtime.className = "status-chip warn";
    }
    toast(err.message);
  });
