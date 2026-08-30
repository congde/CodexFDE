"use strict";

const $ = (s) => document.querySelector(s);
const ACTOR_KEY = "harness-operator";
const WORKSPACE_KEY = "harness-workspace";
const PROFILE_KEY = "harness-profile";
const FILTER_KEY = "harness-session-filter";
const TRAJ_ACTOR_KEY = "harness-traj-actor";
const POLL_MS = 1400;
const BUSY = new Set(["queued", "spec_ready", "executing", "evaluating", "rework"]);
const TERMINAL = new Set(["completed", "failed", "dead_letter"]);
const DEFAULT_EMPLOYEES = [
  { id: "agent:spec", display_name: "规格员", duty: "澄清需求并写 Spec" },
  { id: "agent:coder", display_name: "工程师", duty: "受控改代码并跑 Eval" },
  { id: "agent:reviewer", display_name: "质检员", duty: "挑刺；不能代替老板终审" },
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
    match: ["queued"],
    title: "① 接到需求",
    summary: "自然语言需求入队，准备生成任务 Spec。",
    model_hint: "确认需求边界与 AGENTS.md；不要跳过 Spec。",
    actions: ["读取需求", "核对业务规则"],
  },
  {
    id: "spec",
    label: "规格",
    match: ["spec_ready"],
    title: "② 写好规格",
    summary: "任务级 Spec 已生成，可进入受控改代码或验证。",
    model_hint: "先读 Spec；写入必须落在 write_scope。",
    actions: ["生成 Spec", "对齐验收点"],
  },
  {
    id: "code",
    label: "改代码",
    match: ["executing", "rework"],
    title: "③ 改 FlowERP 代码",
    summary: "在 flowerp/tests 等范围内修改产品代码。",
    model_hint: "仅改 write_scope；保持库存非负、入库幂等、订单状态机、采购审批。",
    actions: ["受控改代码", "返工修复"],
  },
  {
    id: "eval",
    label: "验收",
    match: ["evaluating"],
    title: "④ 跑阻断级 Eval",
    summary: "用 Eval 验证 FlowERP 业务规则未被破坏。",
    model_hint: "根据失败用例定位；不得把失败伪装成成功。",
    actions: ["blocking Eval", "收集失败证据"],
  },
  {
    id: "human",
    label: "老板终审",
    match: ["review", "completed", "failed", "dead_letter"],
    title: "⑤ 老板终审",
    summary: "员工停工；质检员可挑刺，只有老板能通过/驳回。",
    model_hint: "review 停自动改代码；等待老板终审，不要假装已 approve。",
    actions: ["质检挑刺", "老板通过/驳回", "复制会话再试"],
  },
];

const STEP_HELP = {
  queued: { title: "已接到需求", blurb: "FlowERP 改动请求已入队，准备生成任务 Spec。" },
  spec_ready: { title: "规格已写好", blurb: "自然语言已整理成任务 Spec，下一步改代码。" },
  executing: { title: "正在改 FlowERP", blurb: "在 flowerp/tests 等写入范围内修改产品代码。" },
  evaluating: { title: "正在跑验收", blurb: "阻断级 Eval 检查库存/订单/采购等规则是否被破坏。" },
  review: { title: "等老板终审", blurb: "员工已停工。质检员意见可见；请老板具名通过或驳回。" },
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
  employees: DEFAULT_EMPLOYEES.slice(),
  expandedRailId: null,
  railAutoOpened: false,
  sessionFilter: localStorage.getItem(FILTER_KEY) || "all",
  trajActorFilter: localStorage.getItem(TRAJ_ACTOR_KEY) || "all",
};

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
    toast("老板身份不能是 Agent 员工");
    value = "boss";
    $("#actor").value = value;
  }
  localStorage.setItem(ACTOR_KEY, value);
  return value;
}

function setActor(name) {
  const value = String(name || "").trim() || "boss";
  $("#actor").value = value.startsWith("agent:") ? "boss" : value;
  localStorage.setItem(ACTOR_KEY, $("#actor").value);
  renderCollabBar();
  syncComposerEnabled();
  return $("#actor").value;
}

function employeeById(id) {
  return (state.employees || []).find((e) => e.id === id)
    || DEFAULT_EMPLOYEES.find((e) => e.id === id)
    || { id, display_name: id, duty: "" };
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
  const counts = { boss: 0, "agent:spec": 0, "agent:coder": 0, "agent:reviewer": 0, other: 0 };
  (state.events || []).forEach((event) => {
    const name = String(event.actor || "");
    if (!name) return;
    if (name.startsWith("agent:")) {
      if (counts[name] != null) counts[name] += 1;
      else counts.other += 1;
    } else if (["harness", "system", "llm", "automation"].includes(name)) {
      counts.other += 1;
    } else {
      counts.boss += 1;
    }
  });
  return counts;
}

function isBossActor(name) {
  const value = String(name || "");
  return value && !value.startsWith("agent:") && !["harness", "system", "llm", "automation", "agent"].includes(value);
}

function eventMatchesTrajFilter(event) {
  const filter = state.trajActorFilter || "all";
  if (filter === "all") return true;
  const name = String(event.actor || "");
  if (filter === "boss") return isBossActor(name);
  return name === filter;
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
  const duty = dutyForStatus(currentStatus());
  document.querySelectorAll("#employee-chips .chip-mini").forEach((el) => {
    el.classList.toggle("active", el.dataset.emp === duty);
  });
}

function syncComposerEnabled() {
  const ready = Boolean(currentProjectId());
  const blocked = state.sending || taskBusy() || (state.session && !sessionWritable());
  $("#send").disabled = !ready || blocked;
  let hint = "先选择左侧工作区";
  if (ready && state.session && state.session.status === "paused") hint = "会话已暂停，在「更多」里点继续";
  else if (ready && state.session && state.session.status === "closed") hint = "会话已结束，请复制会话或新建";
  else if (ready && taskBusy()) {
    const emp = employeeById(dutyForStatus(state.task && state.task.status));
    hint = `员工「${emp.display_name}」处理中…`;
  } else if (ready && state.task && state.task.status === "review") {
    hint = "员工已停工。请老板终审通过或驳回";
  } else if (ready && state.task && (state.task.status === "dead_letter" || state.task.status === "failed")) {
    hint = "自动化已停。可复制会话再试，并勾选「改代码」";
  } else if (ready) hint = "Enter 发送 · Shift+Enter 换行";
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
  state.view = view;
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === view);
  });
  $("#view-chat").hidden = view !== "chat";
  $("#view-trajectory").hidden = view !== "trajectory";
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
    "agent/critique": "质检员意见",
    "employees/assign": "班组分派",
    "task/created": "任务创建",
    "task/bound": "任务绑定",
    "task/status": "任务状态",
    "turn/start": "开始一轮",
    "turn/end": "结束一轮",
    "agent/stopped": "Agent 停止",
  };
  return map[kind] || kind;
}

function renderMessages() {
  const empty = $("#empty-state");
  const stream = $("#message-stream");
  if (!state.selectedSessionId && !state.messages.length) {
    empty.hidden = false;
    stream.hidden = true;
    stream.innerHTML = "";
    return;
  }
  empty.hidden = true;
  stream.hidden = false;
  if (!state.messages.length) {
    stream.innerHTML = `<div class="msg system"><div class="who">提示</div><div class="body">会话已就绪。描述要对 FlowERP 做的改动，然后点「开始交付」。</div></div>`;
    return;
  }
  stream.innerHTML = state.messages.map((m) => {
    const role = m.role || "system";
    const kind = m.kind || "";
    if (kind === "pipeline/stage") {
      const tone = m.task_status === "dead_letter" || m.task_status === "failed"
        ? "bad" : (m.task_status === "review" || m.task_status === "completed" ? "ok" : "info");
      return `<article class="msg pipeline ${tone}">
        <div class="who">流水线 · ${esc(m.title || kindLabel(kind))}</div>
        <div class="body">${esc(messageBody(m))}</div>
        <div class="pipe-tag">对模型可见 · ${esc(m.stage_id || "")} / ${esc(m.task_status || "")}</div>
      </article>`;
    }
    if (kind === "pipeline/context" || kind === "agent/pre-step") {
      return `<article class="msg pipeline context">
        <div class="who">${esc(kindLabel(kind))}（已注入大模型）</div>
        <details><summary>展开查看模型收到的流水线上下文</summary><pre class="body">${esc(messageBody(m))}</pre></details>
      </article>`;
    }
    if (kind === "agent/critique") {
      return `<article class="msg pipeline ok">
        <div class="who">质检员意见 · ${esc(m.reviewer_id || "agent:reviewer")}</div>
        <div class="body">${esc(messageBody(m))}</div>
        <div class="pipe-tag">建议=${esc(m.suggested_decision || "—")} · 不能代替老板终审</div>
      </article>`;
    }
    const who = kind ? `${roleLabel(role)} · ${kindLabel(kind)}` : roleLabel(role);
    return `<article class="msg ${esc(role)}${m.pending ? " pending" : ""}">
      <div class="who">${esc(who)}</div>
      <div class="body">${esc(messageBody(m))}</div>
    </article>`;
  }).join("");
  const scroller = document.querySelector("[data-conversation-scroll]");
  scroller.scrollTop = scroller.scrollHeight;
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
    btn.addEventListener("click", () => selectSession(btn.dataset.id));
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
  panel.innerHTML = `
    <div class="rail-panel-head">
      <div>
        <strong>${esc(step.title)}</strong>
        <span class="rail-status-note">${esc(statusNote)} · 值班 ${esc(duty.display_name)}</span>
      </div>
      <button type="button" class="linkish" id="rail-collapse">收起</button>
    </div>
    <p class="rail-summary">${esc(step.summary)}</p>
    <div class="rail-grid">
      <div>
        <h4>本步做什么</h4>
        <ul>${step.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
        <p class="muted">值班员工：${esc(duty.display_name)}（${esc(dutyId)}）</p>
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
      else if (act === "new") createSession();
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

  if (!state.railAutoOpened && idx >= 0) {
    state.expandedRailId = RAIL[idx].id;
    state.railAutoOpened = true;
  }

  $("#rail-steps").innerHTML = RAIL.map((step, i) => {
    const cls = stageStateClass(step, i, idx, status);
    const mark = cls.includes("done") ? "✓" : (cls.includes("current") ? "●" : String(i + 1));
    const duty = employeeById(DUTY_BY_RAIL[step.id] || "agent:coder");
    return `<button type="button" class="${cls}" data-rail="${esc(step.id)}" aria-expanded="${state.expandedRailId === step.id}">
      <span class="rail-mark">${mark}</span>
      <span class="rail-copy">
        <span class="rail-label">${esc(step.label)}</span>
        <span class="rail-emp">${esc(duty.display_name)}</span>
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
  const status = currentStatus();
  const dutyId = dutyForStatus(status);
  const duty = employeeById(dutyId);
  const activity = actorActivity();
  const employees = state.employees.length ? state.employees : DEFAULT_EMPLOYEES;
  bar.hidden = false;
  bar.innerHTML = `
    <div class="collab-main">
      <span class="collab-mode">OPC · 一人公司</span>
      <span>老板 <b>${esc(me)}</b> <small>${activity.boss} 条</small></span>
      <span>本轮值班 <b>${esc(duty.display_name)}</b></span>
    </div>
    <div class="opc-roster">
      ${employees.map((emp) => {
        const n = activity[emp.id] || 0;
        const on = emp.id === dutyId;
        return `<button type="button" class="opc-emp ${on ? "on" : ""}" data-traj-actor="${esc(emp.id)}" title="${esc(emp.duty || "")}">
          <b>${esc(emp.display_name)}</b>
          <small>${esc(emp.id)} · ${n} 事件</small>
        </button>`;
      }).join("")}
      <button type="button" class="opc-emp" data-traj-actor="boss" title="查看老板操作">
        <b>老板</b>
        <small>${esc(me)} · ${activity.boss} 事件</small>
      </button>
    </div>
    <p class="collab-warn" style="background:var(--ds-50);color:var(--ds-700);border:0">
      点员工卡片可过滤「过程」账本。质检员只能挑刺；只有老板能终审。
    </p>`;
  bar.querySelectorAll("[data-traj-actor]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.trajActorFilter = btn.dataset.trajActor || "all";
      localStorage.setItem(TRAJ_ACTOR_KEY, state.trajActorFilter);
      setView("trajectory");
      renderTrajectory();
      toast(`过程筛选：${state.trajActorFilter === "boss" ? "老板" : (employeeById(state.trajActorFilter).display_name || state.trajActorFilter)}`);
    });
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
      ? "Agent 员工已无法自动推进。请老板复盘后复制会话再试。"
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
    <strong>员工已停工，请老板终审这次 FlowERP 交付</strong>
    <div>质检员已完成挑刺；通过后记为完成，驳回后工程师返工。Agent 不能代替你点通过。</div>
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

async function quickReview(decision) {
  const task = state.task;
  if (!task) return;
  if ((actor() || "").startsWith("agent:")) {
    return toast("Agent 员工不能终审，请用老板身份");
  }
  const note = decision === "approve"
    ? window.prompt("通过理由（必填）", "阻断级 Eval 通过，接受本次 FlowERP 增量")
    : window.prompt("驳回理由（必填）", "需要返工后再验收");
  if (!note || !note.trim()) return toast("必须填写理由");
  try {
    await api(`/api/v1/tasks/${encodeURIComponent(task.id)}/review`, {
      method: "POST",
      headers: headers("review"),
      body: JSON.stringify({ decision, note: note.trim() }),
    });
    toast(decision === "approve" ? "老板已通过" : "老板已驳回，进入返工");
    await refreshSelected();
    await loadShell();
  } catch (err) {
    toast(err.message);
  }
}

function renderHeader() {
  const session = state.session;
  const task = state.task;
  const project = state.projects.find((p) => p.id === (session && session.project_id))
    || state.projects.find((p) => p.id === currentProjectId());
  $("#chat-title").textContent = session ? shortTitle(session.title) : "新交付";
  if (!session) {
    $("#chat-sub").textContent = "提一个 FlowERP 小需求，工作台改代码并跑验收";
  } else {
    const progress = stepHelp(currentStatus() || "queued").title;
    $("#chat-sub").textContent = `${(project && project.name) || "工作区"} · ${progress}`;
  }
  const badge = $("#task-badge");
  const raw = task ? task.status : (session ? session.status : "idle");
  badge.textContent = task ? stepHelp(raw).title : statusLabel(raw);
  badge.className = `pill ${raw || ""}`;
  renderRail();
  renderCollabBar();
  renderReviewBanner();
  renderRecoveryBanner();
  renderEmployeeChips();
  syncComposerEnabled();
}

function renderTrajectory() {
  const events = state.events || [];
  const filtered = events.filter(eventMatchesTrajFilter);
  const activity = actorActivity();
  const filterLabel = state.trajActorFilter === "all"
    ? "全部"
    : (state.trajActorFilter === "boss" ? "老板" : employeeById(state.trajActorFilter).display_name);
  $("#traj-overview").innerHTML = state.session
    ? `共 ${events.length} 条 · 当前显示 ${filtered.length} 条（筛选：${esc(filterLabel)}）。
       规格 ${activity["agent:spec"]} / 工程师 ${activity["agent:coder"]} / 质检 ${activity["agent:reviewer"]} / 老板 ${activity.boss}`
    : "未选择会话";

  const filters = $("#traj-filters");
  if (filters) {
    filters.querySelectorAll("[data-traj]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.traj === state.trajActorFilter);
    });
  }

  if (!events.length) {
    $("#traj-table").innerHTML = `<div class="traj-turn">发送需求后会出现过程记录。</div>`;
    return;
  }
  if (!filtered.length) {
    $("#traj-table").innerHTML = `<div class="traj-turn">当前筛选下没有事件。点「全部」查看完整账本。</div>`;
    return;
  }
  const chunks = [];
  filtered.forEach((event, index) => {
    if (event.kind === "turn/start") {
      chunks.push(`<div class="traj-turn">第 ${esc((event.payload || {}).turn || "?")} 轮</div>`);
    }
    const active = state.selectedEvent && state.selectedEvent.sequence === event.sequence;
    const who = event.actor
      ? (String(event.actor).startsWith("agent:")
        ? employeeById(event.actor).display_name
        : event.actor)
      : "—";
    chunks.push(`<button type="button" class="traj-row ${active ? "active" : ""}" data-seq="${esc(event.sequence)}">
      <span class="idx">#${esc(event.sequence ?? index + 1)}</span>
      <span class="kind">${esc(kindLabel(event.kind))}</span>
      <span class="who">${esc(who)}</span>
      <span class="content">${esc(String(eventPreview(event)).slice(0, 90))}</span>
    </button>`);
  });
  $("#traj-table").innerHTML = chunks.join("");
  $("#traj-table").querySelectorAll(".traj-row").forEach((row) => {
    row.addEventListener("click", () => {
      const seq = Number(row.dataset.seq);
      const event = events.find((e) => e.sequence === seq);
      openEventDetails(event);
    });
  });
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
  const employees = state.employees.length ? state.employees : DEFAULT_EMPLOYEES;

  $("#details-title").textContent = "交付进度";
  $("#details-kind").textContent = "OPC 流水线 · 员工=Agent";
  $("#details-body").innerHTML = `
    <div class="explain-card">
      <p class="explain-kicker">OPC 班组</p>
      <p>老板调度 Agent 员工交付 FlowERP。当前值班：<b>${esc(duty.display_name)}</b></p>
      <ul class="opc-detail-list">
        ${employees.map((emp) => `<li><b>${esc(emp.display_name)}</b> ${esc(emp.id)} · ${activity[emp.id] || 0} 事件</li>`).join("")}
      </ul>
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
      ${critique ? `<p class="muted">质检建议：${esc(critique.suggested_decision || "—")}</p>` : ""}
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
      if ((actor() || "").startsWith("agent:")) return toast("Agent 不能终审");
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

function renderProfileSelect() {
  const select = $("#profile-id");
  const current = localStorage.getItem(PROFILE_KEY) || "PROFILE-DEFAULT";
  select.innerHTML = (state.profiles.length ? state.profiles : [{ id: "PROFILE-DEFAULT", name: "默认" }])
    .map((p) => `<option value="${esc(p.id)}">${esc(p.name || p.id)}</option>`).join("");
  if ([...select.options].some((o) => o.value === current)) select.value = current;
  localStorage.setItem(PROFILE_KEY, select.value);
}

function renderSettings() {
  const caps = state.capabilities || {};
  $("#capability-cards").innerHTML = `
    <article><b>Codex</b><small>${caps.codex_available ? "可用" : "本机不可用（可用模板/验证模式）"}</small></article>
    <article><b>组合就绪</b><small>${(caps.composition && caps.composition.ready) ? "是" : "否"}</small></article>
    <article><b>工具</b><small>${(state.tools || []).length} 个</small></article>`;

  $("#project-cards").innerHTML = state.projects.map((p) =>
    `<article><b>${esc(p.name)}</b><small>${esc(p.id)} · ${esc(p.root_path)}</small></article>`).join("")
    || "<p style='color:var(--n-600)'>暂无项目</p>";

  $("#profile-cards").innerHTML = state.profiles.map((p) =>
    `<article><b>${esc(p.name)}</b><small>${esc(p.id)}</small></article>`).join("");

  $("#plugin-cards").innerHTML = state.plugins.map((p) => `
    <label class="plugin-row">
      <input type="checkbox" data-plugin="${esc(p.id)}" ${p.enabled ? "checked" : ""}>
      <span><b>${esc(p.name)}</b><small>${esc(p.seam)} → ${esc(p.provider)}</small></span>
    </label>`).join("");
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
  state.task = session.task_id
    ? await api(`/api/v1/tasks/${encodeURIComponent(session.task_id)}`)
    : null;
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

  if (state.task && state.task.status === "review" && !state.detailsOpen) {
    openGraphDetails();
  }
  maybePoll();
}

async function selectSession(sessionId) {
  state.selectedSessionId = sessionId;
  state.expandedRailId = null;
  state.railAutoOpened = false;
  $("#more-menu").hidden = true;
  renderSessions();
  try {
    await refreshSelected();
    if (state.task) openGraphDetails();
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
  } catch (err) {
    toast(err.message);
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

async function loadShell() {
  const [health, projects, sessions, plugins, profiles, tools, capabilities, tasks, employees, courseStatus] = await Promise.all([
    api("/api/v1/health"),
    api("/api/v1/projects"),
    api("/api/v1/sessions?limit=100"),
    api("/api/v1/plugins"),
    api("/api/v1/profiles"),
    api("/api/v1/tools"),
    api("/api/v1/capabilities"),
    api("/api/v1/tasks?limit=200").catch(() => ({ items: [] })),
    api("/api/v1/employees").catch(() => ({ items: DEFAULT_EMPLOYEES })),
    api("/api/v1/course/status").catch(() => ({ course_ready: false })),
  ]);
  state.projects = projects.items || [];
  state.sessions = sessions.items || [];
  state.plugins = plugins.items || [];
  state.profiles = profiles.items || [];
  state.tools = tools.items || [];
  state.capabilities = capabilities;
  state.courseStatus = courseStatus;
  state.employees = (employees.items && employees.items.length) ? employees.items : DEFAULT_EMPLOYEES.slice();
  ingestTasks(tasks.items || []);
  if (!$("#proj-root").value && health.repository_root) {
    $("#proj-root").value = health.repository_root;
  }
  renderEmployeeChips();
  renderSessions();
  renderSettings();
  renderCollabBar();
}

function renderEmployeeChips() {
  const box = $("#employee-chips");
  if (!box) return;
  const duty = dutyForStatus(currentStatus());
  box.innerHTML = (state.employees.length ? state.employees : DEFAULT_EMPLOYEES).map((emp) =>
    `<span class="chip-mini ${emp.id === duty ? "active" : ""}" data-emp="${esc(emp.id)}" title="${esc(emp.duty || "")}">${esc(emp.display_name)}</span>`
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

function bind() {
  $("#actor").value = localStorage.getItem(ACTOR_KEY) || "boss";
  syncSessionFilters();
  syncRoleChips();

  $("#new-chat").addEventListener("click", createSession);
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
  $("#profile-id").addEventListener("change", () => {
    localStorage.setItem(PROFILE_KEY, $("#profile-id").value);
  });
  $("#actor").addEventListener("change", () => {
    actor();
    renderCollabBar();
    renderReviewBanner();
    syncComposerEnabled();
  });
  $("#actor").addEventListener("blur", () => {
    actor();
    renderCollabBar();
    renderReviewBanner();
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
      state.trajActorFilter = btn.dataset.traj || "all";
      localStorage.setItem(TRAJ_ACTOR_KEY, state.trajActorFilter);
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
      $("#prompt").value = btn.dataset.prompt || "";
      $("#prompt").focus();
      toast("已填入示例，确认后点「开始交付」");
    });
  });

  document.querySelectorAll(".tab").forEach((tab) => {
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
loadShell()
  .then(() => {
    if (!currentProjectId() && state.projects[0]) {
      $("#project-id").value = state.projects[0].id;
      localStorage.setItem(WORKSPACE_KEY, state.projects[0].id);
      syncComposerEnabled();
    }
    if (state.sessions[0]) return selectSession(state.sessions[0].id);
    clearLocalSession();
  })
  .catch((err) => toast(err.message));
