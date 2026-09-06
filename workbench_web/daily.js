'use strict';
const el = id => document.getElementById(id);
let plan = null, selected = null, activePlan = null, polling = false;
const states = {queued:'已接收', spec_ready:'需求已准备', executing:'正在执行', evaluating:'正在检查', review:'等待验收', completed:'候选已验收', rework:'需要返工', failed:'执行失败', dead_letter:'执行中断'};
async function api(path, body) {
  const response = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || data.error || '请求失败');
  return data;
}
function message(value) { el('message').textContent = value; }
function actor() { const value = el('actor').value.trim(); if(!value) throw new Error('请填写署名'); return value; }
el('daily-form').onsubmit = async event => {
  event.preventDefault(); el('prepare').disabled = true; plan = null; el('plan').hidden = true;
  try {
    plan = await api('/api/v1/execution/daily-plans', {actor:actor(), request:el('request').value,
      acceptance:el('acceptance').value, non_goals:el('non-goals').value,
      write_scope:el('scope').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean)});
    el('plan-text').textContent = '需求：' + plan.request + '\n验收：' + plan.acceptance + '\n修改范围：' + plan.write_scope.join(', ') +
      '\n自动检查：' + plan.eval_cases.length + ' 项阻断用例\n起点：当前源码 ' + plan.source_sha256.slice(0,12) + '\n' + plan.boundary;
    el('plan').hidden = false; el('authorize').disabled = false; message('核对方案后授权；源码变化时需重新准备。');
  } catch(error) { message(error.message); } finally { el('prepare').disabled = false; }
};
el('daily-form').addEventListener('input', () => { plan = null; el('plan').hidden = true; });
el('authorize').onclick = async () => {
  if(!plan) return;
  el('authorize').disabled = true;
  try {
    activePlan = plan.plan_id;
    // Store before sending so a lost response can be recovered without a duplicate run.
    localStorage.setItem('workbench-daily-plan', activePlan);
    await api('/api/v1/execution/plans/' + activePlan + '/authorize', {actor:plan.actor, confirmation:plan.confirmation});
    message('已受理，正在建立隔离副本。'); await refresh();
  } catch(error) { message(error.message + '；保留方案编号，可刷新核对。'); el('authorize').disabled = false; }
};
async function showTask(id) {
  const task = await api('/api/v1/tasks/' + encodeURIComponent(id)); selected = id;
  el('result').hidden = false; el('result-title').textContent = task.request;
  el('task-state').textContent = task.id + ' · ' + (states[task.status] || task.status) + (task.error ? '\n' + task.error : '');
  const events = task.events || [];
  const execution = events.findLast(e=>e.detail === '受控执行阶段完成');
  const packageEvent = events.findLast(e=>e.detail === '日常研发交付包已保存');
  const source = events.findLast(e=>e.detail === '已创建日常研发隔离副本');
  let download = document.getElementById('download-patch');
  if(!download) { download=document.createElement('a'); download.id='download-patch'; el('task-detail').after(download); }
  download.hidden=!packageEvent; download.textContent='下载完整改动补丁';
  download.href='/api/v1/tasks/' + encodeURIComponent(id) + '/patch';
  const summary = task.result && task.result.summary;
  el('task-detail').textContent = packageEvent ? '候选文件已保存。' +
    (summary ? '\n自动检查：' + summary.passed + ' / ' + summary.total + ' 项通过。' : '') +
    '\n工作目录：' + packageEvent.evidence.workspace + '\n请下载完整补丁并核对具体需求，再作验收决定。' :
    source ? '隔离副本：' + source.evidence.path : '正在准备执行证据';
  el('diff').textContent = execution && execution.evidence.diff || '尚无实际改动记录。';
  el('events').textContent = events.map(e=>e.detail + '\n' + JSON.stringify(e.evidence || {},null,2)).join('\n\n');
  el('approve').hidden = el('reject').hidden = task.status !== 'review' || !packageEvent;
}
async function refresh() {
  if(polling) return; polling = true;
  try {
    if(activePlan) {
      const current = await api('/api/v1/execution/plans/' + activePlan);
      if(current.task_id) selected = current.task_id;
      if(current.state === 'failed') message(current.error || '执行失败，请核对记录');
      if(current.state === 'finished') message('执行已结束，请查看任务的检查结论和候选改动。');
      if(current.state === 'prepared') message('该方案尚未执行，请重新准备并核对方案。');
      if(['failed','finished','prepared'].includes(current.state)) {activePlan=null; localStorage.removeItem('workbench-daily-plan');}
    }
    const data = await api('/api/v1/tasks?limit=100');
    const daily = data.items.filter(t=>(t.requirement_id || '').startsWith('REQ-DAILY-'));
    const existing = new Map(Array.from(el('tasks').children).map(button=>[button.dataset.taskId,button]));
    const retained = new Set(daily.map(task=>task.id));
    existing.forEach((button,id)=> {if(!retained.has(id)) button.remove();});
    daily.forEach(task=> {
      let button = existing.get(task.id);
      if(!button) {button=document.createElement('button'); button.className='task-row'; button.dataset.taskId=task.id; el('tasks').appendChild(button);}
      button.textContent=(states[task.status] || task.status) + ' · ' + task.request;
      button.onclick=()=>showTask(task.id).catch(e=>message(e.message));
    });
    if(selected) await showTask(selected);
  } catch(error) { message(error.message); } finally { polling=false; }
}
async function review(decision) {
  try {
    const note=el('review-note').value.trim(); if(!note) throw new Error('请填写验收意见');
    await api('/api/v1/tasks/' + selected + '/review', {reviewer:actor(), decision:decision, note:note}); await refresh();
  } catch(error) { message(error.message); }
}
el('approve').onclick=()=>review('approve'); el('reject').onclick=()=>review('reject'); el('refresh').onclick=refresh;
api('/api/v1/delivery/capabilities').then(data=> {
  el('readiness').textContent=data.code_readiness.ready ? '执行环境可用。填写真实需求，核对当前源码与修改范围后开始。' : data.code_readiness.message;
  if(!data.web_code_execution) el('readiness').textContent += ' 请从课程任务窗口的“如何开启网页执行”查看启动方式。';
  el('prepare').disabled=!data.code_readiness.ready;
}).catch(e=>message(e.message));
activePlan=localStorage.getItem('workbench-daily-plan'); refresh(); setInterval(refresh, 3000);
