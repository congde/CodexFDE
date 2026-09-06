let selectedTask = null;
let detailVersion = 0;
let listVersion = 0;
let taskCache = [];
let taskFilter = "all";
let progressTimer = null;
let pendingSubmission = null;
let executionPlan = null;
let webCodeAvailable = false;
let composerMode = 'verify';
let selectedInitiativeBinding = null;
let deliveryPanel = 'summary';
function selectDeliveryPanel(panel) {
  deliveryPanel = panel === 'evidence' ? 'evidence' : 'summary';
  document.getElementById('delivery-digest').hidden = deliveryPanel !== 'summary';
  document.getElementById('delivery-evidence').hidden = deliveryPanel !== 'evidence';
  document.querySelectorAll('[data-delivery-panel]').forEach(function(button) {
    button.classList.toggle('active', button.dataset.deliveryPanel === deliveryPanel);
    button.setAttribute('aria-pressed', String(button.dataset.deliveryPanel === deliveryPanel));
  });
}
function renderDigest(detail) {
  const container = document.getElementById('delivery-digest');
  container.innerHTML = '';
  const steps = [
    ['需求约定', document.getElementById('evidence-spec-summary').textContent, 'spec-card'],
    ['执行记录', document.getElementById('execution-summary').textContent + '\n' + document.getElementById('evidence-diff').textContent, 'diff-card'],
    ['工作台复验', document.getElementById('evidence-eval').textContent, 'eval-card'],
    ['具名验收', document.getElementById('evidence-review').textContent + '\n' + document.getElementById('evidence-review-note').textContent, 'task-actions']
  ];
  steps.forEach(function(step, index) {
    const row = document.createElement('article');
    row.className = 'digest-entry digest-step-' + index;
    const heading = document.createElement('h3');
    heading.textContent = step[0];
    const body = document.createElement('p');
    body.textContent = step[1];
    const button = document.createElement('button');
    button.className = 'digest-link';
    button.type = 'button';
    button.textContent = index === 3 ? '查看验收决定 ↗' : '核对原始证据 ↗';
    button.onclick = function() { focusEvidence(step[2]); };
    row.appendChild(heading); row.appendChild(body); row.appendChild(button);
    container.appendChild(row);
  });
  show('digest-count', '按任务记录整理 · ' + (detail.events || []).length + ' 条原始事件可核对');
  selectDeliveryPanel(deliveryPanel);
}
function chooseComposerMode(mode) {
  composerMode = mode === 'codex' && webCodeAvailable ? 'codex' : 'verify';
  resetCodePlan();
  document.getElementById('verify-fields').hidden = composerMode === 'codex';
  document.getElementById('code-entry').hidden = composerMode !== 'codex';
  document.querySelectorAll('[data-mode]').forEach(function(button) {
    button.classList.toggle('active', button.dataset.mode === composerMode);
    button.setAttribute('aria-pressed', String(button.dataset.mode === composerMode));
  });
}
function openComposer(binding) {
  try { actorName(); }
  catch(error) { show('identity-status', error.message); return; }
  selectedInitiativeBinding = binding && typeof binding.id === 'string' && binding.id.startsWith('INIT-') ? binding : null;
  chooseComposerMode(composerMode);
  document.getElementById('code-requirement-spec').hidden = !!selectedInitiativeBinding;
  document.querySelector('label[for="code-requirement-spec"]').hidden = !!selectedInitiativeBinding;
  if(selectedInitiativeBinding) show('mode-help', '需求来源：' + binding.id + ' · 版本 ' + binding.version + '。L15/L16 代码执行会使用已保存需求并记录任务关联；仅复验时不建立该关联。');
  document.getElementById('task-composer').showModal();
}
function focusEvidence(id) {
  if (['spec-card', 'diff-card', 'eval-card', 'event-history'].includes(id)) selectDeliveryPanel('evidence');
  const target = document.getElementById(id);
  if (target.tagName === 'DETAILS') target.open = true;
  target.scrollIntoView({behavior:'smooth', block:'center'});
  target.focus({preventScroll:true});
}
function resetCodePlan() {
  executionPlan = null;
  document.getElementById('task-form').hidden = false;
  document.getElementById('code-options').hidden = false;
  document.getElementById('code-plan').hidden = true;
  document.getElementById('code-bootstrap-field').hidden = document.getElementById('task-lesson').value !== '4';
  show('code-status', '');
}

async function prepareCode() {
  executionPlan = null;
  document.getElementById("code-plan").hidden = true;
  show("code-status", "正在核对课程基线与执行条件…");
  try {
    executionPlan = await api('/api/v1/execution/plans', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({lesson:Number(document.getElementById('task-lesson').value), actor:actorName(),
        eval_cases:document.getElementById('code-cases').value.split(/[,，]/).map(function(x){return x.trim();}).filter(Boolean),
        session_ref:document.getElementById('code-baseline').value.trim() || null,
        bootstrap_task_id:document.getElementById('code-bootstrap').value.trim() || null,
        requirement_spec_text:selectedInitiativeBinding ? null : document.getElementById('code-requirement-spec').value.trim() || null,
        initiative_id:selectedInitiativeBinding && selectedInitiativeBinding.id,
        initiative_version:selectedInitiativeBinding && selectedInitiativeBinding.version})});
    show('code-plan-text', '授权人：' + executionPlan.actor + '\n本讲：' + executionPlan.lesson_title + '\n任务：' + executionPlan.request +
      '\n验收要求：\n' + (executionPlan.acceptance || []).map(function(item){return '• ' + item;}).join('\n') +
      '\n\n' + executionPlan.boundary + '\n方案有效期：15 分钟');
    if (executionPlan.non_goals) show('code-plan-text', document.getElementById('code-plan-text').textContent + '\n本次不做：' + executionPlan.non_goals);
    if (executionPlan.material_version) show('code-plan-text', document.getElementById('code-plan-text').textContent +
      '\n课程材料：' + executionPlan.material_version.name + '\n' + executionPlan.material_version.validation);
    if (executionPlan.initiative) show('code-plan-text', document.getElementById('code-plan-text').textContent +
      '\n关联事项：' + executionPlan.initiative.title + ' · 版本 ' + executionPlan.initiative.version + '\n决定人：' + executionPlan.initiative.decision_by);
    if (executionPlan.bootstrap) {
      show('code-plan-text', document.getElementById('code-plan-text').textContent +
        '\n\n前置工作台验收：' + executionPlan.bootstrap.task_id +
        '\n独立复验人：' + executionPlan.bootstrap.evaluated_by +
        '\n接受人：' + executionPlan.bootstrap.reviewed_by);
    }
    show('code-plan-technical', '允许修改：' + executionPlan.write_scope.join('、') + '\n检查用例：' + executionPlan.eval_cases.join('、') +
      '\n起始提交：' + executionPlan.session_commit +
      (executionPlan.bootstrap ? '\n已验收控制源码指纹：' + executionPlan.bootstrap.source_sha256 : '') +
      (executionPlan.requirement_sha256 ? '\n本次需求合同指纹：' + executionPlan.requirement_sha256 : ''));
    document.getElementById('code-plan').hidden = false;
    document.getElementById('task-form').hidden = true;
    document.getElementById('code-options').hidden = true;
    document.getElementById('authorize-code').disabled = false;
    show('code-status', '尚未执行。确认上面的具体任务后再授权。');
  } catch (error) { show('code-status', String(error.message || error)); }
}

async function authorizeCode() {
  if (!executionPlan) return;
  document.getElementById('authorize-code').disabled = true;
  const plan = executionPlan;
  try {
    const actor = actorName();
    try { localStorage.setItem('workbench-pending-plan', plan.plan_id); } catch (_) { /* Storage is optional. */ }
    await api('/api/v1/execution/plans/' + plan.plan_id + '/authorize', {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({confirmation:plan.confirmation, actor:actor})});
    show('code-status', '已授权，正在创建隔离交付任务…');
    await followExecutionPlan(plan.plan_id);
  } catch (error) {
    show('code-status', '请核对或重试同一方案：' + String(error.message || error));
    document.getElementById('authorize-code').disabled = false;
  }
}

async function followExecutionPlan(planId) {
  const plan = await api('/api/v1/execution/plans/' + encodeURIComponent(planId));
  if (plan.task_id) {
    document.getElementById('task-composer').close();
    workspaceView('delivery');
    await refreshTasks(plan.task_id);
    if (plan.state === 'failed') {
      stopProgress();
      document.getElementById('execution-recovery').hidden = false;
      show('execution-recovery-status', '原方案已停止：' + (plan.error || '执行未完成') +
        (plan.task_record_error ? '\n' + plan.task_record_error : '') +
        '\n已打开原任务。请先核对失败记录与实际文件，再决定下一次交付。');
      return;
    }
    try { localStorage.removeItem('workbench-pending-plan'); } catch (_) { /* Storage is optional. */ }
    document.getElementById('execution-recovery').hidden = true;
  } else if (plan.state === 'failed') {
    show('code-status', '未能启动：' + plan.error);
    show('execution-recovery-status', '原方案已停止：' + plan.error);
  } else if (plan.state === 'prepared') {
    show('execution-recovery-status', '原方案尚未获授权，没有开始执行。请重新核对需求并准备方案。');
    show('code-status', '尚未授权，未开始执行。请核对方案后再决定。');
  } else {
    show('execution-recovery-status', '原方案正在创建任务，进度会自动更新。无需再次授权。');
    setTimeout(function(){ followExecutionPlan(planId).catch(function(error){
      show('code-status', '进度读取失败，请重试同一方案：' + error.message);
      show('execution-recovery-status', '进度暂时不可读，请再次查看原记录：' + error.message);
      document.getElementById('authorize-code').disabled = false;
    }); }, 2000);
  }
}

async function resumeExecution() {
  let id;
  try { id = localStorage.getItem('workbench-pending-plan'); } catch (_) { return; }
  if (!id) return;
  try { await followExecutionPlan(id); }
  catch(error) { show('execution-recovery-status', '原记录暂时不可读，请先核对任务列表，不要重复提交：' + error.message); }
}

function stopProgress() {
  if (progressTimer !== null) clearTimeout(progressTimer);
  progressTimer = null;
}

function workspaceView(view) {
  const delivery = view === "delivery";
  const decision = view === 'decision';
  if (!delivery) { stopProgress(); ++detailVersion; }
  document.getElementById("view-overview").hidden = view !== "overview";
  document.getElementById("view-course").hidden = view !== "course";
  document.getElementById('view-decision').hidden = !decision;
  document.getElementById("view-delivery").hidden = !delivery;
  show("page-section", delivery ? "交付记录" : view === "course" ? "课程与练习" : "项目交付");
  show("page-title", delivery ? "核对成果，再作出验收决定。" : view === "course" ? "通过真实交付，练习工程能力。" : "从目标，到可验收的结果。");
  document.getElementById("mock-notice").classList.toggle("course-notice", view === "course" || view === "delivery");
  if(view === "overview") refreshProjectHome();
  if (decision) { show('page-section', '事项与决策'); show('page-title', '把这项交付，向前推进一步。'); }
  document.querySelectorAll("[data-view]").forEach(function (button) {
    button.classList.toggle("active", button.dataset.view === view);
  });
}

function openDelivery() {
  workspaceView('delivery');
  const first = taskCache.find(item => item.status.code === 'review') || taskCache[0];
  if (selectedTask || first) return loadDetail(selectedTask || first.task_id);
  return refreshTasks().catch(function () { /* Error is already visible. */ });
}

function renderPipeline(detail) {
  const code = detail.status.code;
  const stage = {queued:0,spec_ready:1,executing:2,evaluating:3,review:4,completed:4,rework:2}[code];
  const stages = ["确认需求", "准备合同", "执行方式", "自动检查", "人工验收"];
  const notes = ["由你限定范围", "保存任务约定", detail.policy && detail.policy.execution_mode === "codex" ? "已授权 Codex" : "本次仅复验", "检查真实结果", "由你接受或打回"];
  const container = document.getElementById("delivery-pipeline");
  container.innerHTML = "";
  stages.forEach(function (label, index) {
    const node = document.createElement("button");
    node.type = 'button';
    node.onclick = function() { focusEvidence(['spec-card','spec-card','diff-card','eval-card','task-actions'][index]); };
    node.className = "pipeline-step" + (stage === index ? " current" : "");
    const title = document.createElement("b");
    title.textContent = String(index + 1).padStart(2,"0") + " · " + label;
    const note = document.createElement("small");
    note.textContent = notes[index];
    node.appendChild(title); node.appendChild(note); container.appendChild(node);
  });
  show("delivery-status", detail.status.title || code);
  document.getElementById("next-action").className = "next-action state-" + code;
  show("decision-title", code === "review" ? "检查已结束，等待你的验收" : code === "rework" ? "这项交付还需要完善" : code === "completed" ? "已验收，保留交付证据" : "查看当前进度与下一步");
  const failure = document.getElementById('delivery-failure');
  failure.hidden = !['failed', 'dead_letter', 'rework'].includes(code);
  const lastEvent = (detail.events || []).slice(-1)[0] || {};
  const failureReason = detail.error || lastEvent.detail || '请展开核验证据，查看最后一条执行记录。';
  show('delivery-failure', failure.hidden ? '' : '本次停止原因：' +
    (failureReason.includes('起始基线没有稳定红灯') ?
      '本讲起始材料已经通过检查，无法证明本次新增了能力。请先更新课程起始材料，再开始交付。原记录已保留。' : failureReason));
  const reviewInput = document.getElementById("review-note");
  reviewInput.hidden = code !== "review";
  document.querySelector('label[for="review-note"]').hidden = code !== "review";
}

async function api(path, options) {
  const read = !options || !options.method || options.method.toUpperCase() === "GET";
  const controller = read ? new AbortController() : null;
  const timer = controller ? setTimeout(function () { controller.abort(); }, 10000) : null;
  try {
    const response = await fetch(path, Object.assign({ cache: "no-store" },
      controller ? { signal: controller.signal } : {}, options));
    const body = await response.json();
    if (!response.ok) throw new Error(body.message || body.error || path);
    return body;
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

function show(id, text) {
  const node = document.getElementById(id);
  if (node) node.textContent = text;
}

function laneLabel(lane) {
  return lane === "erp" ? "FlowERP 案例" : "造台";
}

function ownerKind(ownerId) {
  const id = String(ownerId || "");
  if (id === "automation") return "工作台";
  if (id === "agent:reviewer") return "复验者";
  if (id.startsWith("agent:")) return "Codex";
  return "人";
}

function actorName() {
  const input = document.getElementById('task-actor');
  const name = input.value.trim();
  if (!name || name.length > 80 || name.toLowerCase().startsWith('agent:')) {
    input.focus();
    throw new Error(!name ? '请先填写你的姓名或课堂昵称，再开始交付。' :
      name.length > 80 ? '署名请控制在 80 个字以内。' : '不能使用 agent: 开头的执行器署名，请填写你的姓名或课堂昵称。');
  }
  try { localStorage.setItem('workbench-actor', name); } catch (_) { /* Browsing without storage remains usable. */ }
  show('identity-status', '已使用你的署名：' + name + '。决定会与证据一起保存。');
  return name;
}

function renderContract(course) {
  show("stage-band", "L" + String(course.lesson).padStart(2, "0") + " · " + (course.stage_band || ""));
  show("lesson-title", course.title || "");
  show("construction-stage", course.construction_stage || "");
  show("codex-role", course.codex_role || "");
  show("write-scope", (course.write_scope || []).join("、") || "本讲无额外写集");
  show("acceptance", (course.acceptance || []).join("；") || "本讲未声明验收点");
  show("eval-contract", (course.eval_cases || []).join("、") || "本讲未声明 Eval");
  const empty = document.getElementById("honest-empty");
  empty.hidden = !course.honest_empty;
  show(
    "bootstrap-state",
    course.bootstrap_state === "bootstrapped" ? "已自举：能查到 PERSONAL-WORKBENCH" : "尚未构造：不要把参考终态的命令当成自己的 V0.1",
  );
}

function renderTasks(items, selectedTaskId) {
  const list = document.getElementById("task-list");
  taskCache = items;
  const demoNotice = document.getElementById('mock-notice');
  if (demoNotice) demoNotice.hidden = !items.some(item => (item.business_refs || []).includes('MOCK:COURSE-SHOWCASE'));
  const guide = document.getElementById('first-steps-guide');
  if (guide && items.length) guide.open = false;
  show("metric-total", items.length);
  show("metric-review", items.filter(function (item) { return item.status.code === "review"; }).length);
  show("metric-rework", items.filter(function (item) { return item.status.code === "rework"; }).length);
  show("review-count", items.filter(function (item) { return item.status.code === "review"; }).length);
  items = items.filter(function (item) {
    if (taskFilter === 'active') return ['queued','spec_ready','executing','evaluating'].includes(item.status.code);
    if (taskFilter === 'terminal') return ['completed','failed','dead_letter'].includes(item.status.code);
    return taskFilter === "all" || item.status.code === taskFilter;
  });
  if (!items.length) {
    list.textContent = taskFilter === "all" ? "还没有交付任务。L01～L03 工作台正在被你造出来；L04 起可新建任务。" : "这个分类下暂无任务。";
    return;
  }
  list.innerHTML = "";
  items.forEach(function (view) {
    const button = document.createElement("button");
    button.className = "task";
    button.type = "button";
    if (view.task_id === selectedTaskId) button.classList.add("active");
    const lane = document.createElement("span");
    lane.className = "lane" + (view.lane === "erp" ? " lane-erp" : "");
    lane.textContent = laneLabel(view.lane);
    button.appendChild(lane);
    const name = document.createElement("span");
    name.className = "task-name";
    name.textContent = view.request || "未命名任务";
    button.title = name.textContent;
    const meta = document.createElement("span");
    meta.className = "task-meta";
    meta.textContent = ((view.status && (view.status.title || view.status.label)) || "未知阶段") + " · " + (view.task_id || "").replace("TASK-", "");
    button.appendChild(name); button.appendChild(meta);
    button.onclick = function () {
      list.querySelectorAll(".task").forEach(function (node) { node.classList.remove("active"); });
      button.classList.add("active");
      loadDetail(view.task_id).then(function () {
        const evidence = document.getElementById('evidence-card');
        if (selectedTask === view.task_id && evidence) evidence.scrollIntoView({behavior:'smooth', block:'start'});
      });
    };
    list.appendChild(button);
  });
}

function renderCases(cases) {
  const list = document.getElementById("eval-cases");
  list.innerHTML = "";
  if (!cases || !cases.length) {
    const item = document.createElement("li");
    item.textContent = "还没有 Eval 报告。提交后才会跑本讲用例。";
    list.appendChild(item);
    return;
  }
  cases.forEach(function (item) {
    const row = document.createElement("li");
    row.className = item.passed ? "case-pass" : "case-fail";
    row.textContent = (item.passed ? "通过" : "失败") + " · " + (item.name || "未命名") + (item.level ? " · " + item.level : "");
    list.appendChild(row);
  });
}

function renderActions(detail) {
  const actions = detail.allowed_actions || [];
  const verify = document.getElementById("action-verify");
  const approve = document.getElementById("action-approve");
  const reject = document.getElementById("action-reject");
  verify.hidden = actions.indexOf("run") < 0;
  approve.hidden = actions.indexOf("approve") < 0;
  reject.hidden = actions.indexOf("reject") < 0;
  if (detail.policy && detail.policy.execution_mode === 'codex') {
    verify.hidden = true;
    if (!(detail.events || []).some(function(event){return event.detail === '课程红绿差分判定已完成' && event.evidence && event.evidence.accepted === true;})) approve.hidden = true;
  }
  [verify, approve, reject].forEach(function (button) { button.disabled = false; });
  verify.onclick = function () { runVerify(detail.task_id); };
  approve.onclick = function () { runReview(detail.task_id, "approve"); };
  reject.onclick = function () { runReview(detail.task_id, "reject"); };
}

async function preparePreview(taskId) {
  const button = document.getElementById('prepare-preview');
  button.disabled = true;
  show('preview-status', '正在准备独立预览，必要时会等待上次运行释放数据，请稍候。');
  try {
    const result = await api('/api/v1/tasks/' + encodeURIComponent(taskId) + '/preview', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({actor:actorName()})
    });
    if (selectedTask !== taskId) return;
    if (result.task_id !== taskId || !/^http:\/\/127\.0\.0\.1:\d+$/.test(result.url)) throw new Error('预览地址或任务编号不匹配');
    const link = document.getElementById('preview-link');
    link.href = result.url;
    link.hidden = false;
    show('preview-status', result.label + '。' + result.notice);
  } catch(error) {
    if (selectedTask === taskId) show('preview-status', '暂未打开预览：' + error.message);
  } finally {
    if (selectedTask === taskId) button.disabled = false;
  }
}

function renderEvidence(detail) {
  workspaceView("delivery");
  show('page-title', detail.request || '交付与验收');
  renderPipeline(detail);
  document.getElementById("task-detail").hidden = false;
  document.getElementById("evidence-empty").hidden = true;
  show("evidence-task-id", detail.task_id);
  const next = (detail.status && detail.status.next_action) || "先提交并复验";
  show("next-action", "下一步：" + next);
  show("evidence-request", detail.request || "");
  show("evidence-lane", laneLabel(detail.lane) + " · " + (detail.requirement_id || ""));
  show('evidence-business-refs', (detail.business_refs || []).join('\n') || '尚未关联业务对象');
  const initiativeEvent = (detail.events || []).find(event=>event.detail === '已关联具名决定的事项与冻结合同');
  const initiative = initiativeEvent && initiativeEvent.evidence;
  show('evidence-initiative', initiative ? '来源事项：' + initiative.title + ' · ' + initiative.id + ' · 版本 ' + initiative.version + ' · 决定人 ' + initiative.decision_by : '未关联事项记录');
  const owner = detail.status && detail.status.owner;
  show("evidence-owner", ((detail.status && detail.status.title) || "") + " / " + ownerKind(owner && owner.id) + " · " + ((owner && owner.label) || ""));
  const spec = Object.assign({}, detail.spec || {}, (detail.spec && detail.spec.content) || {});
  show("evidence-spec", ['goal','non_goals','constraints','acceptance','done'].map(function(key,index) {
    return spec[key] ? ['目标','本次不做','约束','验收用例','完成定义'][index] + '\n' + spec[key] : '';
  }).filter(Boolean).join('\n\n') || spec.title || spec.path || '尚无 Spec');
  show("evidence-spec-summary", (spec.goal || spec.title || "尚无任务约定").split("\n\n")[0]);
  show("evidence-scope", ((detail.policy && detail.policy.write_scope) || []).join("、") || "未声明写集");
  const evalView = detail.eval || {};
  show("evidence-eval", evalView.available
    ? (({pass:"检查通过",block:"检查未通过"}[evalView.decision] || "结果未知") + " · 阻断失败 " + (evalView.blocking_failed || 0) + " 项")
    : "还没有 Eval 报告");
  const review = detail.review || {};
  show("evidence-review", review.reviewed_by
    ? (({approve:"已接受交付",reject:"已打回返工"}[review.decision] || "已记录审核") + " · " + review.reviewed_by)
    : (review.required ? "等待具名人审" : "尚未进入人审"));
  show("evidence-review-note", review.note || "尚无审核理由");
  const files = (detail.execution && detail.execution.changed_files) || [];
  const isCode = detail.policy && detail.policy.execution_mode === "codex";
  const canPreview = isCode && ['review','completed'].includes((detail.status || {}).code) &&
    (detail.events || []).some(event=>event.detail === '课程红绿差分判定已完成' && event.evidence && event.evidence.accepted === true);
  document.getElementById('candidate-preview').hidden = !canPreview;
  document.getElementById('preview-link').hidden = true;
  document.getElementById('prepare-preview').disabled = false;
  document.getElementById('prepare-preview').onclick = function(){ preparePreview(detail.task_id); };
  show('preview-status', '');
  const stopped = ['failed', 'dead_letter'].includes((detail.status || {}).code);
  show('role-human', review.reviewed_by ? '已由 ' + review.reviewed_by + ' 留下审核决定' : stopped ? '先核对停止原因与实际改动' : '限定范围 · 等待具名验收');
  show('role-codex', isCode ? (detail.execution && detail.execution.available ? '已留下执行记录，点击核对改动' : stopped ? '本次已停止，执行记录尚不完整' : '已授权，尚待执行证据') : '本次未调用，仅运行复验');
  show('role-workbench', evalView.available ? '已保存自动检查报告' : stopped ? '本次没有完成检查，请先核对停止原因' : '等待检查结果入账');
  show("execution-summary", isCode ? "本次已授权 Codex 修改代码" : "本次仅复验，未调用 Codex");
  show("evidence-diff", files.length ? files.join("\n") : isCode ? "尚无已记录的文件改动，请结合执行事件核对。" : "本次不修改文件；自动检查结果不能证明完成了新功能。");
  renderCases(evalView.cases);
  renderActions(detail);
  renderEvents(detail.events);
  renderDigest(detail);
}

function renderEvents(events) {
  const list = document.getElementById("task-events");
  list.innerHTML = "";
  const items = Array.isArray(events) ? events : [];
  show("event-count", "展开任务事件（" + items.length + " 条）");
  if (!items.length) {
    list.textContent = "尚无可查询的事件，不能据此认定执行成功。";
    return;
  }
  items.forEach(function (event) {
    const row = document.createElement("li");
    const heading = document.createElement("p");
    heading.textContent = "事件 " + event.id + " · " + (event.created_at || "时间未记录") +
      " · " + (event.actor || "未记录操作人") + " · " +
      (event.from_status || "起点") + " → " + (event.to_status || "未知状态");
    row.appendChild(heading);
    const description = document.createElement("p");
    description.textContent = event.detail || "无说明";
    row.appendChild(description);
    if (event.evidence !== null && event.evidence !== undefined) {
      const evidence = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "查看该事件的核验数据";
      const data = document.createElement("pre");
      data.textContent = JSON.stringify(event.evidence, null, 2);
      evidence.appendChild(summary);
      evidence.appendChild(data);
      row.appendChild(evidence);
    }
    list.appendChild(row);
  });
}

function renderUpgrade(upgrade) {
  if (!upgrade.available) {
    show("upgrade-text", upgrade.message || "还没有工作台升级记录");
    return;
  }
  show(
    "upgrade-text",
    (upgrade.classification || "") + " · " + (upgrade.status || "") + " · " + (upgrade.failure_signature || upgrade.id || ""),
  );
}

function clearEvidence(message) {
  show("delivery-status", "等待读取");
  document.getElementById("task-detail").hidden = true;
  document.getElementById("evidence-empty").hidden = false;
  show("evidence-empty", message);
  show("next-action", "请重新读取任务证据后再操作。");
  ["action-verify", "action-approve", "action-reject"].forEach(function (id) {
    document.getElementById(id).hidden = true;
    document.getElementById(id).disabled = true;
  });
}

async function refreshTasks(selectedTaskId) {
  stopProgress();
  const version = ++listVersion;
  ++detailVersion;
  if (selectedTaskId) selectedTask = selectedTaskId;
  clearEvidence("正在刷新任务，旧证据暂不可用于审核。");
  taskCache = [];
  document.getElementById("task-list").textContent = "正在读取最新任务…";
  show("data-status", "正在读取最新任务…");
  ["metric-total", "metric-review", "metric-rework", "review-count"].forEach(function (id) { show(id, "—"); });
  try {
    const tasks = await api("/api/v1/delivery/views?limit=20");
    if (version !== listVersion) return;
    if (!Array.isArray(tasks.items)) throw new Error("任务列表格式不完整");
    renderTasks(tasks.items, selectedTask);
    show("data-status", "任务列表读取于 " + new Date().toLocaleTimeString() + "；可点击刷新核对变化。");
    if (selectedTask) await loadDetail(selectedTask);
    else clearEvidence("还没有选中任务，请从列表选择。");
  } catch (error) {
    if (version !== listVersion) return;
    ++detailVersion;
    document.getElementById("task-list").textContent = "任务列表不可用，请恢复服务后重试。";
    clearEvidence("无法读取最新证据，已停止显示旧结论。");
    show("data-status", "读取失败：" + String(error.message || error));
    throw error;
  }
}

async function loadDetail(taskId, polling) {
  stopProgress();
  if (selectedTask !== taskId) deliveryPanel = 'summary';
  selectedTask = taskId;
  const version = ++detailVersion;
  if (!polling) clearEvidence("正在读取 " + taskId + " 的最新证据…");
  try {
    const detail = await api("/api/v1/delivery/views/" + encodeURIComponent(taskId));
    if (version !== detailVersion) return;
    if (detail.task_id !== taskId || !detail.status || !detail.status.code) throw new Error("任务证据与请求不一致");
    renderEvidence(detail);
    const index = taskCache.findIndex(function (item) { return item.task_id === taskId; });
    if (index >= 0) { taskCache[index] = detail; renderTasks(taskCache, taskId); }
    const pendingCodeGate = detail.status.code === 'review' && detail.policy && detail.policy.execution_mode === 'codex' &&
      !(detail.events || []).some(function(event){return event.detail === '课程红绿差分判定已完成';});
    const running = pendingCodeGate || ["queued", "spec_ready", "executing", "evaluating"].includes(detail.status.code);
    show("data-status", "任务读取于 " + new Date().toLocaleTimeString() + (running ? "；正在自动更新进度。" : "；本阶段已停止自动更新，可手动刷新。"));
    if (running) progressTimer = setTimeout(function () { loadDetail(taskId, true); }, 2000);
  } catch (error) {
    if (version !== detailVersion) return;
    clearEvidence("任务读取失败，旧结论不可用于审核：" + String(error.message || error));
  }
}

async function showCourse(lesson) {
  if (!lesson) return;
  renderContract(await api("/api/course/current?lesson=" + encodeURIComponent(lesson)));
}

async function submitTask(event) {
  event.preventDefault();
  const lesson = document.getElementById("task-lesson").value;
  let actor;
  try { actor = actorName(); }
  catch(error) { show('task-submit-status', error.message); return; }
  const request = document.getElementById("task-request").value.trim();
  const button = document.getElementById("task-submit");
  if (button.disabled) return;
  const refs = document.getElementById('task-business-refs').value.split(/[,，\n]/).map(function(ref){return ref.trim();}).filter(Boolean);
  const payload = JSON.stringify({ lesson: Number(lesson), actor: actor, request: request, business_refs: refs });
  if (!pendingSubmission || pendingSubmission.payload !== payload) {
    pendingSubmission = {payload: payload, key: crypto.randomUUID()};
  }
  button.disabled = true;
  show("task-submit-status", "正在提交，受理后即可查看检查进度…");
  try {
    const created = await api("/api/v1/delivery/requests", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": pendingSubmission.key },
      body: pendingSubmission.payload,
    });
    if (!created.accepted || !created.task_id) throw new Error("未收到完整的受理凭据，请重试同一需求核对。");
    pendingSubmission = null;
    document.getElementById("task-request").value = "";
    document.getElementById("task-composer").close();
    workspaceView("delivery");
    show("task-submit-status", "已受理 " + created.task_id + "，可在交付页查看进度。");
    try { await refreshTasks(created.task_id); }
    catch (error) { show("data-status", "任务 " + created.task_id + " 已受理，但进度暂不可读。请刷新核对，无需重新提交。"); }
  } catch (error) {
    show("task-submit-status", "未确认受理结果：" + String(error.message || error) + "。重试同一内容会复用本次提交编号。");
  } finally {
    button.disabled = false;
  }
}

async function runVerify(taskId) {
  const button = document.getElementById("action-verify");
  button.disabled = true;
  show("action-status", "正在复验…");
  try {
    const view = await api("/api/v1/tasks/" + taskId + "/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: actorName() }),
    });
    await refreshTasks(view.task_id);
    show("action-status", "复验完成，状态 " + ((view.status && view.status.code) || ""));
  } catch (error) {
    show("action-status", "复验失败：" + String(error.message || error));
  } finally {
    button.disabled = false;
  }
}

async function runReview(taskId, decision) {
  const note = document.getElementById("review-note").value.trim();
  show("action-status", "正在提交审核…");
  try {
    const view = await api("/api/v1/tasks/" + taskId + "/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer: actorName(), decision: decision, note: note }),
    });
    await refreshTasks(view.task_id);
    show("action-status", "审核结果：" + ((view.review && view.review.decision) || decision));
  } catch (error) {
    show("action-status", "审核失败：" + String(error.message || error));
  }
}

function focusIdentity() {
  const input = document.getElementById('task-actor');
  input.scrollIntoView({behavior:'smooth', block:'center'});
  input.focus({preventScroll:true});
  show('identity-status', '填写姓名或课堂昵称。它会记录在你的任务和验收决定上。');
}
function readCurrentContract() {
  const card = document.getElementById('contract-card');
  const details = card.querySelector('details');
  if (details) details.open = true;
  card.setAttribute('tabindex', '-1');
  card.scrollIntoView({behavior:'smooth', block:'center'});
  card.focus({preventScroll:true});
}
async function boot() {
  document.getElementById('back-to-tasks').onclick = function () {
    document.getElementById('delivery-task-browser').scrollIntoView({behavior:'smooth', block:'start'});
  };
  document.getElementById('focus-identity').onclick = focusIdentity;
  document.getElementById('read-current-contract').onclick = readCurrentContract;
  document.getElementById('start-guided-delivery').onclick = openComposer;
  try { document.getElementById('execution-recovery').hidden = !localStorage.getItem('workbench-pending-plan'); } catch (_) { /* Storage is optional. */ }
  document.getElementById('resume-execution').onclick = resumeExecution;
  const identity = document.getElementById('task-actor');
  try { identity.value = localStorage.getItem('workbench-actor') || ''; } catch (_) { /* Storage is optional. */ }
  if (identity.value.trim()) show('identity-status', '当前署名：' + identity.value.trim() + '。确认与验收会记录此署名。');
  identity.addEventListener('change', function() {
    try { localStorage.removeItem('workbench-actor'); } catch (_) { /* Storage is optional. */ }
    try { actorName(); } catch(error) { show('identity-status', error.message); }
  });
  initInitiatives();
  initProjectHome();
  document.querySelectorAll('[data-delivery-panel]').forEach(function(button) {
    button.onclick = function() { selectDeliveryPanel(button.dataset.deliveryPanel); };
  });
  document.querySelectorAll('[data-mode]').forEach(function(button) {
    button.onclick = function() { chooseComposerMode(button.dataset.mode); };
  });
  document.querySelectorAll('[data-evidence]').forEach(function(button) {
    button.onclick = function() { focusEvidence(button.dataset.evidence); };
  });
  document.getElementById('prepare-code').onclick = prepareCode;
  document.getElementById('authorize-code').onclick = authorizeCode;
  document.getElementById('edit-code').onclick = resetCodePlan;
  document.querySelectorAll("[data-view]").forEach(function (button) {
    button.onclick = function () {
      workspaceView(button.dataset.view);
      if (button.dataset.view === 'decision') refreshInitiatives();
      if (button.dataset.view === 'delivery') openDelivery();
    };
  });
  document.querySelectorAll("[data-filter]").forEach(function (button) {
    button.onclick = function () {
      taskFilter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach(function (item) { item.classList.toggle("active", item === button); });
      renderTasks(taskCache, selectedTask);
    };
  });
  ["new-task-side"].forEach(function (id) {
    document.getElementById(id).onclick = openComposer;
  });
  ["new-task", "new-task-hero"].forEach(function (id) {
    document.getElementById(id).onclick = function () {
      workspaceView('decision'); refreshInitiatives();
      if (!initiativeDirty) showInitiative(null);
    };
  });
  document.getElementById("close-composer").onclick = function () { document.getElementById("task-composer").close(); };
  document.querySelector(".brand").onclick = function (event) { event.preventDefault(); workspaceView("overview"); };
  document.getElementById("task-form").addEventListener("submit", submitTask);
  document.getElementById("refresh-tasks").onclick = function () {
    if(!document.getElementById("view-overview").hidden)refreshProjectHome();
    if(!document.getElementById("view-decision").hidden){refreshInitiatives();refreshInitiativeWork();}
    refreshTasks(selectedTask).catch(function () { /* Error is already visible. */ });
  };
  try {
    const health = await api("/api/health");
    const capabilities = await api('/api/v1/delivery/capabilities');
    const readiness = capabilities.code_readiness;
    webCodeAvailable = !!capabilities.web_code_execution && (!readiness || readiness.ready === true);
    document.getElementById('mode-codex').disabled = !webCodeAvailable;
    show('mode-help', readiness ? readiness.message : webCodeAvailable ? '执行方案经过你的确认后，才会启动 Codex。' : '当前工作台仅开放复验，代码执行入口尚未启用。');
    document.getElementById('execution-setup').hidden = webCodeAvailable;
    chooseComposerMode('verify');
    if (health.erp_url) document.getElementById("erp-link").href = health.erp_url;
    const thesis = health.thesis || {};
    show("thesis", [thesis.workbench, thesis.flowerp, thesis.codex].filter(Boolean).join(" · "));
    const course = await api("/api/course/current");
    renderContract(course);
    await refreshTasks();
    renderUpgrade(await api("/api/cockpit/upgrade"));
    document.getElementById("task-lesson").addEventListener("change", function () {
      document.getElementById('code-bootstrap-field').hidden = this.value !== '4';
      showCourse(this.value).catch(function (error) {
        show("task-submit-status", "合同读取失败：" + String(error.message || error));
      });
    });
  } catch (error) {
    show("thesis", "工作台服务未启动。请运行 python -X utf8 -m workbench.cli serve-workbench");
    show("lesson-title", String(error.message || error));
    document.getElementById("task-list").textContent = "无法读取任务。确认当前打开的是工作台端口 :8001，而不是 FlowERP :8000 或可选 Harness :8010。";
  }
}

boot();
