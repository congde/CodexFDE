/* Project delivery projections. Counts are actual initiatives, never demo claims. */
const deliveryStageViews = {
  idle: ['待开始', '说明这次要解决的问题', '保存需求不会执行代码。先让 Codex 查看项目，再一起明确本期目标。', 'attention', 0, 'action'],
  researching: ['调研中', 'Codex 正在查看项目', '后台正在处理，结果会回到这里。你可以先写补充内容，或停止本轮。', 'running', 0, 'action'],
  clarifying: ['等你回答', '先确定这次的交付边界', '回答下面的问题，工作台会据此整理可执行的方案。', 'attention', 0, 'action'],
  ready: ['等你确认', '核对目标、范围与验收条件', '先确认产品需求，再确认技术方案。你的确认只对应当前版本。', 'attention', 1, 'plan'],
  confirmed: ['等待授权', '方案已确认，可以开始执行', '授权后，工作台将创建隔离副本，交给 Codex 修改并运行项目检查。', 'attention', 2, 'plan'],
  queued: ['排队中', '已接收，等待执行位置', '本机按顺序处理代码交付；无需重复提交。', 'running', 3, 'action'],
  executing: ['执行与复验', '工作台正在组织本轮交付', '实际改动与独立检查结果会一并返回。你可以离开页面，稍后回来查看。', 'running', 3, 'action'],
  cancelling: ['正在停止', '正在停止并保留本轮记录', '停止完成后可以补充需求，再发起新一轮。', 'running', 3, 'action'],
  cancelled: ['已停止', '核对已有记录，再继续', '此前输出和候选会保留。补充新的要求后，重新调研。', 'attention', 0, 'action'],
  interrupted: ['运行中断', '上次运行已中断，需要重新核对', '工作台不会自动重复执行旧任务。请先查看记录，再决定如何继续。', 'attention', 0, 'action'],
  failed: ['需要处理', '本轮没有完成，先看失败原因', '失败记录已保留。你可以补充说明，让 Codex 带着已有结果重新调研。', 'attention', 0, 'action'],
  rework: ['需要返工', '检查未通过，需要修订', '查看失败证据，在这里给出修改意见，继续同一项交付。', 'attention', 3, 'action'],
  review: ['等你验收', '检查已结束，请核对实际成果', '检查通过不等于需求完成。对照验收条件看候选，由指定负责人决定是否接受。', 'attention', 4, 'result'],
  accepted: ['等待集成', '候选已接受，确认是否集成', '工作台会再次核对源码基线并保留备份；集成不会自动发布。', 'attention', 4, 'result'],
  integrating: ['集成中', '正在将已验收改动集成到项目', '正在核对基线并写入已验收改动。请等待结果。', 'running', 4, 'action'],
  integrated: ['已集成', '代码已集成，还需要实际发布', '人工完成发布后，在这里登记版本、环境与证据。', 'outcome', 5, 'result'],
  released: ['效果待观察', '已经发布，等待真实使用结果', '记录观察周期、实际指标与用户反馈，判断这次交付是否解决了原问题。', 'outcome', 5, 'result'],
  observed: ['已回收效果', '用真实反馈决定下一轮', '查看目标与实际结果。需要改进时，从这里开启下一轮并保留前轮引用。', 'outcome', 5, 'result']
};
function deliveryStageView(stage) {
  const v = deliveryStageViews[stage] || ['状态待核对', '暂时无法读取进展', '刷新后重试，不根据缺失的数据推断任务已完成。', 'unknown', -1, 'action'];
  return {label:v[0], title:v[1], description:v[2], group:v[3], step:v[4], pane:v[5]};
}
function isDemoInitiative(item) { return /^\[Mock课程演示\]/.test(item.title || ''); }
function uiElement(tag, className, text) {
  const el=document.createElement(tag); if(className)el.className=className;
  if(text!==undefined)el.textContent=text; return el;
}
let homeRows=[], homeFilter='attention', homeRead=0;
async function setHomeCleared(row, button) {
  if(button.disabled)return;
  button.disabled=true;
  try {
    await api('/api/v1/initiatives/'+encodeURIComponent(row.item.id)+(row.item.home_hidden?'/restore-home':'/clear-home'),
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({actor:actorName(),version:row.item.version})});
    await refreshProjectHome();
  } catch(error) {show('home-status','清除或恢复未完成：'+error.message);}
  finally {button.disabled=false;}
}
async function openProjectInitiative(id) {
  if(initiativeDirty) { show('home-status','请先保存当前事项的修改，再切换。');return; }
  workspaceView('decision');
  try { const item=await api('/api/v1/initiatives/'+encodeURIComponent(id)); showInitiative(item); window.scrollTo({top:0,behavior:'smooth'}); }
  catch(error) { show('initiative-status','事项暂不可读：'+error.message); }
}
function renderProjectHome() {
  const selected=document.getElementById('home-project').value;
  const include=document.getElementById('home-include-mock').checked;
  const cleared=document.getElementById('home-include-cleared').checked;
  const rows=homeRows.filter(row=>(selected==='all' || row.item.project_id===selected) && (include || !isDemoInitiative(row.item)) && (cleared || !row.item.home_hidden || row.view.group==='running'));
  ['attention','running','outcome'].forEach(group=>{
    show('home-'+group,rows.filter(row=>row.view.group===group).length);
    document.querySelector('[data-home-filter="'+group+'"]').classList.toggle('selected',homeFilter===group);
  });
  show('home-mock-note',homeRows.some(row=>isDemoInitiative(row.item)) ? '课程演示单独标识，不作为真实交付成果。' : '只根据已保存的事项和执行记录展示进展。');
  const failed=rows.filter(row=>row.view.group==='unknown').length;
  show('home-status',failed ? failed+' 项进展暂不可读，请刷新重试。' : rows.length+' 项'+(include?'事项（含演示）':'真实事项')+' · 点击卡片继续，不必重新解释背景');
  show('home-list-title',({attention:'下一步，等你来定',running:'正在后台推进',outcome:'交付之后，核对效果',all:'所有交付事项'})[homeFilter]);
  const list=document.getElementById('home-items');list.replaceChildren();
  const visible=rows.filter(row=>(cleared && row.item.home_hidden) || homeFilter==='all' || row.view.group===homeFilter || row.view.group==='unknown');
  for(const row of visible) {
    const {item,work,view}=row;
    const card=uiElement('article','initiative-card '+view.group);
    const meta=uiElement('div','card-meta');meta.append(uiElement('span','',work?.project?.name || '项目'),uiElement('span','state-badge '+view.group,view.label));
    if(isDemoInitiative(item))meta.append(uiElement('span','demo-badge','课程演示'));
    card.append(meta,uiElement('h3','',item.title),uiElement('p','card-goal',work?.proposal?.goal || item.goal || item.raw_signal));
    const next=uiElement('div','card-next');next.append(uiElement('span','',view.title));
    const button=uiElement('button','text-button',view.group==='running'?'查看进展 →':'继续这项交付 →');
    button.onclick=()=>openProjectInitiative(item.id);next.append(button);card.append(next);list.append(card);
    const clear=uiElement('button','text-button',item.home_hidden?'恢复到首页':'清除');
    clear.title='从首页移出，可恢复；交付记录和失败证据保留。';
    clear.disabled=view.group==='running' || view.group==='unknown';
    if(clear.disabled)clear.title='请等待运行结束并刷新状态后再清除。';
    clear.onclick=()=>setHomeCleared(row,clear);next.append(clear);
    if(item.home_hidden)meta.append(uiElement('span','demo-badge','已从首页清除'));
  }
  if(!visible.length) {
    const empty=uiElement('div','workspace-empty');
    empty.append(uiElement('h3','',homeFilter==='running'?'当前没有后台任务':homeFilter==='outcome'?'还没有进入效果阶段的事项':'这里暂时没有待处理事项'),
      uiElement('p','',homeFilter==='outcome'?'实际集成、发布和效果回收后，记录会出现在这里。':'从一个真实问题开始，工作台会把进展和需要你决定的事带回这里。'));
    const button=uiElement('button','secondary','新建交付事项');button.onclick=()=>document.getElementById('new-task').click();empty.append(button);list.append(empty);
  }
  const released=rows.filter(r=>!isDemoInitiative(r.item) && r.work?.current_release).length;
  const observed=rows.filter(r=>!isDemoInitiative(r.item) && r.work?.stage==='observed').length;
  show('home-outcome-note',released ? released+' 项登记了实际发布，其中 '+observed+' 项已回收效果。' : '目前尚无真实发布记录。测试通过之后，还要核对需求是否真正得到解决。');
}
async function refreshProjectHome() {
  const version=++homeRead;
  try {
    const [body,projects]=await Promise.all([api('/api/v1/initiatives'),api('/api/v1/projects')]);
    const select=document.getElementById('home-project'),selected=select.value;
    const option=uiElement('option','','全部项目');option.value='all';select.replaceChildren(option);
    projects.items.forEach(p=>{const o=uiElement('option','',p.name);o.value=p.id;select.append(o);});
    select.value=projects.items.some(p=>p.id===selected)?selected:'all';
    const rows=await Promise.all(body.items.map(async item=>{
      try {const work=await api('/api/v1/initiatives/'+item.id+'/workflow');return {item,work,view:deliveryStageView(work.stage)};}
      catch(_){return {item,work:null,view:deliveryStageView('unavailable')};}
    }));
    if(version!==homeRead)return;
    homeRows=rows;renderProjectHome();
  } catch(error) { show('home-status','暂时无法读取项目交付：'+error.message);['attention','running','outcome'].forEach(k=>show('home-'+k,'—')); }
}
function initProjectHome() {
  document.querySelectorAll('[data-home-filter]').forEach(b=>b.onclick=()=>{homeFilter=b.dataset.homeFilter;renderProjectHome();});
  document.getElementById('home-project').onchange=renderProjectHome;
  document.getElementById('home-include-mock').onchange=renderProjectHome;
  document.getElementById('home-include-cleared').onchange=renderProjectHome;
  document.getElementById('home-all').onclick=()=>{homeFilter='all';renderProjectHome();};
  document.getElementById('home-show-outcomes').onclick=()=>{homeFilter='outcome';renderProjectHome();};
  document.getElementById('back-to-initiatives').onclick=()=>{
    if(initiativeDirty){show('initiative-status','请先保存当前修改。');return;}
    document.getElementById('view-decision').classList.remove('item-open');
    document.getElementById('initiative-editor').hidden=true;showInitiativeWork(null);refreshInitiatives();
  };
  refreshProjectHome();
  setInterval(()=>{if(!document.hidden && !document.getElementById('view-overview').hidden)refreshProjectHome();},15000);
}
