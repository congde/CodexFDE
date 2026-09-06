const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup() {
  const nodes = new Map();
  const element = tag => ({tag, hidden:false, disabled:false, textContent:'', value:'', children:[],
    scrollIntoView() { this.scrolled = true; },
    focus() { this.focused = true; },
    close() { this.closed = true; },
    appendChild(child) { this.children.push(child); },
    set innerHTML(value) { this.children = []; this.textContent = value; },
  });
  const document = { getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, element('div'));
    return nodes.get(id);
  }, createElement: element, querySelector: selector => document.getElementById(selector), querySelectorAll: () => []};
  const pending = [];
  const context = vm.createContext({document, Date, console, encodeURIComponent, AbortController, setTimeout, clearTimeout,
    crypto:require('node:crypto').webcrypto,
    fetch: (url, options) => new Promise((resolve, reject) => pending.push({resolve, reject, url, options}))});
  const source = fs.readFileSync(path.join(__dirname, '../workbench_web/app.js'), 'utf8');
  vm.runInContext(source.replace(/boot\(\);\s*$/, ''), context);
  context.renderEvidence = detail => {
    context.rendered = detail.task_id;
    document.getElementById('task-detail').hidden = false;
  };
  return {context, nodes, pending};
}
const response = body => ({ok:true, json:async () => body});

test('recovery reads the original plan without repeating authorization', async () => {
  const {context, nodes, pending} = setup();
  context.localStorage={getItem:()=> 'original-plan'};
  const resumed=context.resumeExecution();
  assert.equal(pending.length,1);
  assert.equal(pending[0].url,'/api/v1/execution/plans/original-plan');
  assert.notEqual(pending[0].options.method,'POST');
  pending[0].resolve(response({state:'prepared',task_id:null}));
  await resumed;
  assert.match(nodes.get('execution-recovery-status').textContent,/没有开始执行/);
  assert.equal(pending.length,1);
});

test('recovery opens an existing task and clears its pending marker only after loading', async () => {
  const {context, nodes, pending} = setup();
  let cleared=false, opened;
  context.localStorage={getItem:()=> 'original-plan',removeItem:()=>{cleared=true;}};
  context.workspaceView=()=>{};
  context.refreshTasks=async id=>{assert.equal(cleared,false);opened=id;};
  const resumed=context.resumeExecution();
  pending[0].resolve(response({state:'finished',task_id:'TASK-EXISTING'}));
  await resumed;
  assert.equal(opened,'TASK-EXISTING');assert.equal(cleared,true);
  assert.equal(nodes.get('execution-recovery').hidden,true);
});

test('unsigned submission sends nothing and points to the signature', async () => {
  const {context, nodes, pending} = setup();
  await context.submitTask({preventDefault() {}});
  assert.equal(pending.length, 0);
  assert.equal(nodes.get('task-actor').focused, true);
  assert.match(nodes.get('task-submit-status').textContent, /课堂昵称/);
});

test('candidate preview rejects a link for another task', async () => {
  const {context,nodes,pending}=setup();
  context.document.getElementById('task-actor').value='maintenance';
  vm.runInContext("selectedTask='TASK-PREVIEW';",context);
  const opened=context.preparePreview('TASK-PREVIEW');
  pending[0].resolve(response({task_id:'TASK-OTHER',url:'http://127.0.0.1:9999'}));
  await opened;
  assert.match(nodes.get('preview-status').textContent,/不匹配/);
  assert.equal(nodes.get('prepare-preview').disabled,false);
  assert.equal(nodes.get('preview-link'),undefined);
});

test('late candidate preview cannot insert a link into another task', async () => {
  const {context,nodes,pending}=setup();
  context.document.getElementById('task-actor').value='maintenance';
  vm.runInContext("selectedTask='TASK-PREVIEW';",context);
  const opened=context.preparePreview('TASK-PREVIEW');
  vm.runInContext("selectedTask='TASK-NEW';",context);
  pending[0].resolve(response({task_id:'TASK-PREVIEW',url:'http://127.0.0.1:9999'}));
  await opened;
  assert.equal(nodes.get('preview-link'),undefined);
});

test('failed plan with an existing task stays visible and keeps its recovery marker', async () => {
  const {context, nodes, pending} = setup();
  let cleared = false, opened, stopped = false;
  context.localStorage = {getItem:()=> 'failed-plan', removeItem:()=>{cleared=true;}};
  context.workspaceView = ()=>{};
  context.refreshTasks = async id=>{opened=id;};
  context.stopProgress = ()=>{stopped=true;};
  const resumed = context.resumeExecution();
  pending[0].resolve(response({state:'failed', task_id:'TASK-FAILED',
    error:'original failure', task_record_error:'task status not synchronized'}));
  await resumed;
  assert.equal(opened,'TASK-FAILED');
  assert.equal(cleared,false);
  assert.equal(stopped,true);
  assert.equal(nodes.get('execution-recovery').hidden,false);
  assert.match(nodes.get('execution-recovery-status').textContent,/original failure/);
  assert.match(nodes.get('execution-recovery-status').textContent,/task status not synchronized/);
  assert.equal(pending.length,1);
});

test('executor signature is rejected before a human decision is sent', async () => {
  const {context, nodes, pending} = setup();
  context.document.getElementById('task-actor').value = 'agent:reviewer';
  await context.runReview('TASK-TEST', 'approve');
  assert.equal(pending.length, 0);
  assert.match(nodes.get('action-status').textContent, /不能使用 agent:/);
});

test('delivery digest preserves verify-only and pending review facts, clears previous task', () => {
  const {context, nodes} = setup();
  context.show('evidence-spec-summary', '<img src=x onerror=alert(1)>');
  context.show('execution-summary', '本次仅复验，未调用 Codex');
  context.show('evidence-diff', '本次不修改文件');
  context.show('evidence-eval', '检查通过 · 阻断失败 0 项');
  context.show('evidence-review', '等待具名人审');
  context.show('evidence-review-note', '尚无审核理由');
  context.renderDigest({events:[{id:1}]});
  const rows = nodes.get('delivery-digest').children;
  assert.equal(rows.length, 4);
  assert.equal(rows[0].children[1].textContent, '<img src=x onerror=alert(1)>');
  assert.match(rows[1].children[1].textContent, /仅复验.*未调用 Codex/);
  assert.match(rows[3].children[1].textContent, /等待具名人审/);
  rows[1].children[2].onclick();
  assert.equal(nodes.get('delivery-evidence').hidden, false);
  assert.equal(nodes.get('delivery-digest').hidden, true);
  assert.equal(nodes.get('diff-card').focused, true);
  context.show('evidence-spec-summary', '下一项任务');
  context.renderDigest({events:[]});
  assert.equal(nodes.get('delivery-digest').children.length, 4);
  assert.equal(nodes.get('delivery-digest').children[0].children[1].textContent, '下一项任务');
});

test('late response cannot replace the latest task selection', async () => {
  const {context, pending} = setup();
  const first = context.loadDetail('TASK-FIRST');
  const second = context.loadDetail('TASK-SECOND');
  pending[1].resolve(response({task_id:'TASK-SECOND', status:{code:'review'}}));
  await second;
  pending[0].resolve(response({task_id:'TASK-FIRST', status:{code:'review'}}));
  await first;
  assert.equal(context.rendered, 'TASK-SECOND');
});

test('failed read hides old evidence and disables approval', async () => {
  const {context, nodes, pending} = setup();
  const read = context.loadDetail('TASK-A');
  pending[0].reject(new Error('offline'));
  await read;
  assert.equal(nodes.get('task-detail').hidden, true);
  assert.equal(nodes.get('action-approve').disabled, true);
  assert.match(nodes.get('evidence-empty').textContent, /旧结论不可用于审核/);
});

test('wrong task identity is rejected even with a successful HTTP response', async () => {
  const {context, nodes, pending} = setup();
  const read = context.loadDetail('TASK-A');
  pending[0].resolve(response({task_id:'TASK-B', status:{code:'completed'}}));
  await read;
  assert.equal(context.rendered, undefined);
  assert.match(nodes.get('evidence-empty').textContent, /不一致/);
});

test('list failure invalidates a pending detail and removes old task verdicts', async () => {
  const {context, nodes, pending} = setup();
  const detail = context.loadDetail('TASK-A');
  const refresh = context.refreshTasks();
  pending[1].reject(new Error('offline'));
  await assert.rejects(refresh);
  pending[0].resolve(response({task_id:'TASK-A', status:{code:'review'}}));
  await detail;
  assert.equal(context.rendered, undefined);
  assert.match(nodes.get('task-list').textContent, /不可用/);
  assert.equal(nodes.get('action-approve').disabled, true);
});

test('event history preserves order, identity and evidence as text', () => {
  const {context, nodes} = setup();
  const hostile = '<img src=x onerror=alert(1)>';
  context.renderEvents([
    {id:41, created_at:'2026-09-05 10:00:00', actor:'student', from_status:'review', to_status:'rework',
      detail:hostile, evidence:{reason:'缺少库存对账'}},
    {id:42, actor:'reviewer', to_status:'evaluating', detail:'重新复验'},
  ]);
  const rows = nodes.get('task-events').children;
  assert.equal(rows.length, 2);
  assert.match(rows[0].children[0].textContent, /事件 41.*student.*review → rework/);
  assert.equal(rows[0].children[1].textContent, hostile);
  assert.equal(rows[0].children[2].children[1].tag, 'pre');
  assert.deepEqual(JSON.parse(rows[0].children[2].children[1].textContent), {reason:'缺少库存对账'});
  assert.match(rows[1].children[0].textContent, /事件 42/);
  assert.equal(nodes.get('event-count').textContent, '展开任务事件（2 条）');
});

test('empty history clears the previous task rather than retaining its events', () => {
  const {context, nodes} = setup();
  context.renderEvents([{id:1, detail:'旧任务'}]);
  context.renderEvents([]);
  assert.equal(nodes.get('task-events').children.length, 0);
  assert.match(nodes.get('task-events').textContent, /不能据此认定执行成功/);
});

test('workspace navigation separates overview from delivery', () => {
  const {context, nodes} = setup();
  context.workspaceView('delivery');
  assert.equal(nodes.get('view-overview').hidden, true);
  assert.equal(nodes.get('view-delivery').hidden, false);
  context.workspaceView('overview');
  assert.equal(nodes.get('view-delivery').hidden, true);
});

test('pipeline reflects human review without pretending verify mode wrote code', () => {
  const {context, nodes} = setup();
  context.renderPipeline({status:{code:'review',title:'等待验收'},policy:{execution_mode:'verify'}});
  const stages = nodes.get('delivery-pipeline').children;
  assert.equal(stages.length, 5);
  assert.match(stages[4].className, /current/);
  assert.equal(stages[2].children[1].textContent, '本次仅复验');
  assert.equal(nodes.get('review-note').hidden, false);
  context.renderPipeline({status:{code:'rework'},policy:{execution_mode:'verify'}});
  assert.equal(nodes.get('review-note').hidden, true);
});

test('uncertain submission retries the same key and closes after acceptance even if the list is offline', async () => {
  const {context, nodes, pending} = setup();
  context.document.getElementById('task-lesson').value = '13';
  context.document.getElementById('task-actor').value = 'maintainer';
  context.document.getElementById('task-request').value = '检查补货';
  const first = context.submitTask({preventDefault() {}});
  const key = pending[0].options.headers['Idempotency-Key'];
  assert.ok(key);
  assert.equal(pending[0].url, '/api/v1/delivery/requests');
  pending[0].reject(new Error('response lost'));
  await first;
  context.refreshTasks = async () => { throw new Error('offline'); };
  const retry = context.submitTask({preventDefault() {}});
  assert.equal(pending[1].options.headers['Idempotency-Key'], key);
  pending[1].resolve(response({accepted:true, task_id:'TASK-ACCEPTED'}));
  await retry;
  assert.equal(nodes.get('task-composer').closed, true);
  assert.match(nodes.get('data-status').textContent, /已受理.*无需重新提交/);
  assert.equal(nodes.get('task-request').value, '');
});

test('running tasks refresh until review and stop there', async () => {
  const {context, pending} = setup();
  const timers = [];
  context.setTimeout = (fn, delay) => { timers.push({fn,delay}); return timers.length; };
  context.clearTimeout = () => {};
  const read = context.loadDetail('TASK-RUN');
  pending[0].resolve(response({task_id:'TASK-RUN',status:{code:'evaluating'}}));
  await read;
  const poll = timers.find(item => item.delay === 2000);
  assert.ok(poll);
  poll.fn();
  pending[1].resolve(response({task_id:'TASK-RUN',status:{code:'review'}}));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(timers.filter(item => item.delay === 2000).length, 1);
  assert.equal(context.rendered, 'TASK-RUN');
});

test('returning to overview prevents an in-flight task response from reopening delivery', async () => {
  const {context, pending} = setup();
  const read = context.loadDetail('TASK-RUN');
  context.workspaceView('overview');
  pending[0].resolve(response({task_id:'TASK-RUN',status:{code:'review'}}));
  await read;
  assert.equal(context.rendered, undefined);
});

test('code delivery approval stays hidden until the course differential gate passes', () => {
  const {context, nodes} = setup();
  const detail = {task_id:'TASK-CODE', policy:{execution_mode:'codex'}, allowed_actions:['run','approve','reject'], events:[]};
  context.renderActions(detail);
  assert.equal(nodes.get('action-verify').hidden, true);
  assert.equal(nodes.get('action-approve').hidden, true);
  detail.events = [{detail:'课程红绿差分判定已完成', evidence:{accepted:true}}];
  context.renderActions(detail);
  assert.equal(nodes.get('action-approve').hidden, false);
  assert.equal(nodes.get('action-verify').hidden, true);
});


test('execution choice preserves draft but invalidates previous authorization preview', () => {
  const {context, nodes} = setup();
  context.document.getElementById('task-request').value = '尚未提交的需求';
  vm.runInContext("webCodeAvailable = true; executionPlan = {plan_id:'old'};", context);
  context.chooseComposerMode('codex');
  assert.equal(nodes.get('verify-fields').hidden, true);
  assert.equal(nodes.get('code-entry').hidden, false);
  assert.equal(vm.runInContext('executionPlan', context), null);
  context.chooseComposerMode('verify');
  assert.equal(nodes.get('verify-fields').hidden, false);
  assert.equal(nodes.get('code-entry').hidden, true);
  assert.equal(nodes.get('task-request').value, '尚未提交的需求');
  vm.runInContext('webCodeAvailable = false', context);
  context.chooseComposerMode('codex');
  assert.equal(nodes.get('code-entry').hidden, true);
});

test('pipeline links to the corresponding actual evidence without changing task state', () => {
  const {context, nodes} = setup();
  context.renderPipeline({status:{code:'review'},policy:{execution_mode:'verify'}});
  nodes.get('delivery-pipeline').children[3].onclick();
  assert.equal(nodes.get('eval-card').focused, true);
  assert.equal(nodes.get('eval-card').scrolled, true);
  assert.equal(nodes.get('delivery-status').textContent, 'review');
});

test('initiative read failure removes old list verdicts', async () => {
  const {context, nodes, pending} = setup();
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../workbench_web/initiatives.js'), 'utf8'), context);
  context.show('initiative-list', '旧状态：待整理');
  const refresh = context.refreshInitiatives();
  assert.equal(nodes.get('initiative-list').textContent, '正在更新事项列表…');
  pending[0].reject(new Error('offline'));
  assert.equal(await refresh, false);
  assert.match(nodes.get('initiative-list').textContent, /读取失败/);
  assert.doesNotMatch(nodes.get('initiative-list').textContent, /待整理/);
});

test('initiative execution sends saved identity instead of a competing pasted Spec', async () => {
  const {context, pending} = setup();
  vm.runInContext("selectedInitiativeBinding = {id:'INIT-FIXTURE',version:2};", context);
  const doc = context.document;
  doc.getElementById('task-lesson').value = '15';
  doc.getElementById('task-actor').value = 'fixture-human';
  doc.getElementById('code-cases').value = 'new_case';
  doc.getElementById('code-baseline').value = 'fixture-base';
  doc.getElementById('code-requirement-spec').value = '另一个旧需求';
  const result = context.prepareCode();
  const body = JSON.parse(pending[0].options.body);
  assert.equal(body.initiative_id, 'INIT-FIXTURE');
  assert.equal(body.initiative_version, 2);
  assert.equal(body.requirement_spec_text, null);
  pending[0].resolve(response({actor:'fixture-human',request:'已保存需求',acceptance:[],write_scope:[],eval_cases:[],
    initiative:{title:'测试事项',version:2,decision_by:'fixture-human'}}));
  await result;
  assert.match(doc.getElementById('code-plan-text').textContent, /已保存需求/);
  assert.match(doc.getElementById('code-plan-text').textContent, /版本 2/);
});

test('delivery navigation opens a review task and preserves an existing selection', async () => {
  const {context} = setup();
  context.workspaceView = () => {};
  const opened = [];
  context.loadDetail = async id => { opened.push(id); };
  vm.runInContext("taskCache = [{task_id:'READY',status:{code:'spec_ready'}},{task_id:'REVIEW',status:{code:'review'}}]; selectedTask = null;", context);
  await context.openDelivery();
  assert.deepEqual(opened, ['REVIEW']);
  vm.runInContext("selectedTask = 'READY';", context);
  await context.openDelivery();
  assert.deepEqual(opened, ['REVIEW', 'READY']);
});
