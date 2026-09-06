/* One persistent initiative, rendered inside the workbench's decision view. */
let initiativeWork = null;
let initiativeWorkId = null;
let initiativeWorkTimer = null;
let initiativeWorkPending = false;
let initiativeWorkRead = 0;
const iw = id => document.getElementById('iw-' + id);
const iwStages = {queued:'已排队，等待执行',cancelling:'正在取消并保留证据',cancelled:'已取消，可重新调研',interrupted:'服务重启，本轮中断，请核对后重试',released:'已发布，效果待观察',observed:'已回收实际效果',idle:'先让 Codex 检查已有实现',researching:'Codex 正在调研与整理问题',clarifying:'等待你回答业务问题',ready:'方案已提出，等待你确认',confirmed:'目标已确认，等待授权执行',executing:'正在修改与独立复验',review:'本轮候选等待验收',rework:'本轮需要修订，可在这里反馈',accepted:'候选已接受，等待确认集成',integrating:'正在核对并集成',integrated:'已集成到当前项目源码',failed:'本轮已停止，原记录保留'};
function iwList(id, values) {
  iw(id).replaceChildren();
  (values || []).forEach(text=>{const item=document.createElement('li');item.textContent=text;iw(id).appendChild(item);});
}
function renderInitiativeWork(data) {
  if(initiativeWork && initiativeWork.active_task_id!==data.active_task_id) {
    iw('preview-frame').hidden=true;iw('preview-frame').removeAttribute('src');iw('preview-link').hidden=true;iw('preview-status').textContent='';
  }
  initiativeWork=data;
  if(data.stage!=='idle')document.getElementById('initiative-decision').hidden=true;
  iw('stage').textContent=iwStages[data.stage] || data.stage;
  iw('error').textContent=data.error || data.warning || (!data.enabled ? '当前服务尚未开启 Codex 执行。请保留原运行目录，以 --enable-code-execution 启动工作台。' : '');
  const busy=['researching','queued','executing','cancelling','integrating'].includes(data.stage);
  const complete=['integrated','released','observed'].includes(data.stage);
  iw('project').textContent=data.project ? '项目：'+data.project.name : '项目：FlowERP';
  iw('cancel').hidden=!['researching','queued','executing'].includes(data.stage);
  iw('cancel').disabled=initiativeWorkPending;
  iw('delivery').hidden=!complete;
  iw('outcome-form').hidden=!data.current_release;
  iw('observation-status').textContent=data.observation_status || '尚未登记实际发布';
  iw('documents').textContent=JSON.stringify({documents:data.documents || [],confirmations:data.document_confirmations || [],delivery_records:data.delivery_records || [],completed_cycles:data.completed_cycles || []},null,2);
  ['release','outcome','reopen'].forEach(key=>iw(key).disabled=initiativeWorkPending);
  const doc=(data.documents || []).slice(-1)[0];
  iw('prd').textContent=doc ? '版本 '+doc.version+'\n业务问题：'+doc.prd.business_problem : '';
  if(doc && iw('prd-metric').dataset.version!==data.id+':'+doc.version){iw('prd-metric').value=doc.prd.success_metric || '';iw('prd-metric').dataset.version=data.id+':'+doc.version;}
  iw('prd-metric').disabled=data.stage!=='ready' || data.prd_confirmed;
  iw('technical').textContent=doc ? '依据：'+doc.technical_plan.findings.join('\n')+'\n测试计划：'+(doc.technical_plan.test_plan || []).join('；') : '';
  iw('confirm-prd').hidden=data.stage!=='ready' || data.prd_confirmed;
  iw('confirm-prd').disabled=initiativeWorkPending;

  iw('discuss').disabled=busy || !data.enabled || initiativeWorkPending || complete;
  iw('message').disabled=complete;
  iw('discuss').textContent=data.iterations.length ? '带着反馈继续调研与修订' : data.messages.length ? '发送回答，继续与 Codex 讨论' : '让 Codex 调研并讨论';
  const messages=iw('messages');
  if(messages.dataset.revision!==JSON.stringify(data.messages)) {
    messages.replaceChildren();
    data.messages.forEach(message=>{
      const article=document.createElement('article');
      const title=document.createElement('b'); title.textContent=message.role==='codex' ? 'Codex · 源码调研' : message.role==='user' ? message.actor || '事项参与者' : '工作台';
      const text=document.createElement('p');text.textContent=message.text;
      article.append(title,text);
      (message.questions || []).forEach(question=>{const q=document.createElement('p');q.textContent='需要你确认：'+question;article.append(q);});
      messages.append(article);
    });
    messages.dataset.revision=JSON.stringify(data.messages);
  }
  const proposal=data.proposal;
  iw('proposal').hidden=!proposal || !doc || busy || data.stage==='clarifying';
  if(proposal) {
    iw('goal').textContent=proposal.goal;
    iwList('acceptance',proposal.acceptance);iwList('nongoals',proposal.non_goals);iwList('steps',proposal.steps);
    iw('scope').textContent='调研依据：\n'+proposal.sources.join('\n')+'\n\n建议修改范围：\n'+proposal.write_scope.join('\n');
    iw('confirm').hidden=data.stage!=='ready';
    iw('confirm').disabled=initiativeWorkPending || !data.prd_confirmed;
    iw('execute').hidden=data.stage!=='confirmed';iw('execute').disabled=initiativeWorkPending || !data.enabled;
    iw('reviewer').disabled=data.stage!=='ready';
    if(data.reviewer && data.stage!=='ready') iw('reviewer').value=data.reviewer;
  }
  const task=data.task;
  iw('result').hidden=!task;
  if(task) {
    const summary=task.summary;
    iw('result-summary').textContent=task.id+' · '+task.status+(summary ? '\n自动检查：'+summary.passed+' / '+summary.total+' 项通过' : '')+(task.error ? '\n'+task.error : '');
    iw('diff').textContent=task.diff || '尚无最终文件变化记录';
    iw('checks').textContent=JSON.stringify({summary,changed_files:task.changed_files,events:task.events},null,2);
    const packaged=task.events.some(event=>event.detail==='日常研发交付包已保存');
    iw('patch').hidden=!packaged;iw('patch').href='/api/v1/tasks/'+task.id+'/patch';
    iw('preview').hidden=!['review','accepted','integrated'].includes(data.stage) || !!(data.project && data.project.id!=='PROJECT-FLOWERP');iw('preview').disabled=initiativeWorkPending;
    iw('accept').hidden=data.stage!=='review';iw('accept').disabled=initiativeWorkPending;
    iw('note').disabled=data.stage!=='review';
    iw('integrate').hidden=iw('integration-help').hidden=data.stage!=='accepted';iw('integrate').disabled=initiativeWorkPending;
    if(data.integration) iw('result-summary').textContent+='\n已集成 '+data.integration.files.length+' 个源文件；未提交 Git、未部署。';
  }
  const invocation=['researching','clarifying','ready','confirmed'].includes(data.stage) ? data.invocation : task && task.execution && task.execution.invocation || data.invocation;
  iw('invocation').textContent=invocation ? '工作目录：'+invocation.workspace+'\n\n启动参数：\n'+JSON.stringify(invocation.command)+'\n\n交给 Codex 的任务：\n'+invocation.prompt : '尚未启动 Codex。工作台会保存实际启动参数、任务文字与返回结果。';
  const live=task ? task.events.filter(event=>event.detail==='日常研发执行输出').map(event=>event.evidence.line) : [];
  iw('progress').textContent=(busy && data.stage==='researching' ? data.progress : live.length ? live : data.progress).join('\n\n') || '尚无本轮输出';
  if(iw('history').dataset.rounds!==JSON.stringify(data.iterations)) {
  iw('history').dataset.rounds=JSON.stringify(data.iterations);
  iw('history').replaceChildren();
  data.iterations.forEach((iteration,index)=>{
    const row=document.createElement('p');const button=document.createElement('button');
    button.type='button';button.textContent='第 '+(index+1)+' 轮 · '+iteration.task_id;
    button.onclick=async()=>{try { const detail=await api('/api/v1/tasks/'+iteration.task_id); const pre=document.createElement('pre');pre.textContent=JSON.stringify(detail,null,2);row.replaceChildren(button,pre); }catch(e){iw('error').textContent=e.message;}};
    row.append(button);iw('history').append(row);
  });
  }
  renderDeliveryWorkspace(data);
}
async function refreshInitiativeWork() {
  if(!initiativeWorkId)return;
  const id=initiativeWorkId, request=++initiativeWorkRead;
  try {
    const data=await api('/api/v1/initiatives/'+id+'/workflow');
    if(id===initiativeWorkId && request===initiativeWorkRead)renderInitiativeWork(data);
  }catch(error){if(id===initiativeWorkId)iw('error').textContent=error.message;}
}
function showInitiativeWork(item) {
  clearInterval(initiativeWorkTimer);initiativeWorkTimer=null;++initiativeWorkRead;
  initiativeWorkId=item && item.id;initiativeWork=null;
  document.getElementById('initiative-work').hidden=!item;
  activeIwPane=null;activeIwStage=null;
  if(!item)return;
  iw('message').value='';iw('note').value='';iw('reviewer').value=item.reviewer || '';
  iw('preview-frame').hidden=true;iw('preview-frame').removeAttribute('src');iw('preview-link').hidden=true;iw('preview-status').textContent='';
  refreshInitiativeWork();
  initiativeWorkTimer=setInterval(()=>{if(!document.getElementById('view-decision').hidden && !initiativeWorkPending)refreshInitiativeWork();},2500);
}
async function initiativeWorkAction(action, extra={}) {
  if(!initiativeWork || initiativeWorkPending)return;
  const itemId=initiativeWorkId;
  let actionError='';
  initiativeWorkPending=true;renderInitiativeWork(initiativeWork);
  try {
    const data=await api('/api/v1/initiatives/'+itemId+'/workflow/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({actor:actorName(),revision:initiativeWork.revision,...extra})});
    if(itemId!==initiativeWorkId)return;
    renderInitiativeWork(data);
    if(action==='discuss')iw('message').value='';
    if(action==='confirm') {
      currentInitiative=await api('/api/v1/initiatives/'+itemId);
      document.getElementById('initiative-decision').hidden=true;
    }
  }catch(error){actionError=error.message;}
  finally{initiativeWorkPending=false;if(initiativeWork)renderInitiativeWork(initiativeWork);if(actionError)iw('error').textContent=actionError;}
}
function initInitiativeWork() {
  document.querySelectorAll('[data-iw-pane]').forEach(b=>b.onclick=()=>selectIwPane(b.dataset.iwPane));
  iw('primary-next').onclick=()=>selectIwPane(deliveryStageView(initiativeWork.stage).pane);
  iw('discuss').onclick=()=>initiativeWorkAction('discuss',{text:iw('message').value.trim() || (initiativeWork && !initiativeWork.messages.length && currentInitiative && currentInitiative.raw_signal) || ''});
  iw('confirm').onclick=()=>initiativeWorkAction('confirm',{reviewer:iw('reviewer').value.trim()});
  iw('confirm-prd').onclick=()=>initiativeWorkAction('confirm-prd',{success_metric:iw('prd-metric').value.trim()});
  iw('cancel').onclick=()=>initiativeWorkAction('cancel');
  iw('release').onclick=()=>initiativeWorkAction('release',{fields:{version:iw('release-version').value.trim(),environment:iw('release-environment').value.trim(),evidence:iw('release-evidence').value.trim()}});
  iw('outcome').onclick=()=>{const fields={};['period','target','actual','observation','evidence','conclusion'].forEach(k=>fields[k]=iw('outcome-'+k).value.trim());initiativeWorkAction('outcome',{fields});};
  iw('reopen').onclick=()=>initiativeWorkAction('reopen',{text:iw('next').value.trim()});
  iw('execute').onclick=()=>initiativeWorkAction('execute');
  iw('accept').onclick=()=>initiativeWorkAction('accept',{note:iw('note').value.trim()});
  iw('integrate').onclick=()=>initiativeWorkAction('integrate');
  iw('preview').onclick=async()=>{
    if(!initiativeWork || !initiativeWork.task)return;
    const taskId=initiativeWork.task.id;iw('preview').disabled=true;iw('preview-status').textContent='正在启动本事项的隔离候选…';
    try {
      const result=await api('/api/v1/tasks/'+taskId+'/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({actor:actorName()})});
      if(!initiativeWork.task || initiativeWork.task.id!==taskId)return;
      // FlowERP forbids framing. Keep its protections and offer a top-level view.
      iw('preview-frame').hidden=true;iw('preview-frame').removeAttribute('src');
      iw('preview-link').href=result.url;iw('preview-link').hidden=false;
      iw('preview-status').textContent='候选已就绪，请从下方链接在独立窗口验收。'+result.notice;
    }catch(error){iw('preview-status').textContent=error.message;}finally{iw('preview').disabled=false;}
  };
}

let activeIwPane=null, activeIwStage=null;
function selectIwPane(pane) {
  activeIwPane=pane;
  ['action','plan','result'].forEach(key=>{
    iw('pane-'+key).hidden=key!==pane;
    const button=document.querySelector('[data-iw-pane="'+key+'"]');
    button.classList.toggle('active',key===pane);button.setAttribute('aria-pressed',String(key===pane));
  });
}
function renderDeliveryWorkspace(data) {
  const view=deliveryStageView(data.stage);
  const busy=view.group==='running';
  iw('stage').className='state-badge '+view.group;
  iw('stage').textContent=view.label;
  iw('next-owner').textContent=busy ? '工作台正在处理' : view.group==='outcome' ? '交付后的下一步' : '现在需要你决定';
  iw('next-title').textContent=view.title;iw('next-description').textContent=view.description;
  const proposal=data.proposal;
  iwList('questions',data.stage==='clarifying' && proposal ? proposal.questions : []);
  const latest=[...data.messages].reverse().find(m=>m.role==='codex');
  iw('findings').hidden=!latest || busy;
  iw('latest-answer').textContent=latest ? latest.text : '';
  iw('message-count').textContent='（'+data.messages.length+' 条）';
  const started=data.messages.slice(-1)[0];
  const elapsed=started ? Math.max(0,Math.floor(Date.now()/1000-started.at)) : 0;
  iw('live-status').textContent=busy ? '已等待 '+(elapsed<60 ? elapsed+' 秒' : Math.floor(elapsed/60)+' 分 '+elapsed%60+' 秒')+' · 进展自动更新' : '';
  const updates=(data.progress || []).filter(p=>!/^(?:[a-z_]+[.][a-z_.]+|"|\{)/.test(p));
  iw('live-output').hidden=!busy;
  iw('live-output').textContent=updates.length ? updates[updates.length-1].slice(0,350) : '已接收本轮请求，正在等待后台返回新的进展。';
  iw('composer-hint').textContent=busy ? '可以先写补充内容，当前轮结束后再发送。' : '需求、修改意见与决定，都留在同一事项中。';
  iw('message-label').textContent=busy ? '先写下你的补充内容' : data.stage==='clarifying' ? '你的回答或范围调整' : '补充需求或修改意见';
  iw('discuss').textContent=initiativeWorkPending ? '正在提交…' : busy ? view.label+'…' : ['failed','interrupted','cancelled','rework'].includes(data.stage) ? '带着反馈重新调研' : data.stage==='idle' ? '让 Codex 开始调研' : '发送给 Codex';
  iw('primary-next').hidden=view.pane==='action';
  iw('primary-next').textContent=view.pane==='plan' ? '查看方案并确认 →' : '查看成果与下一步 →';
  iw('plan-empty').hidden=!iw('proposal').hidden;
  iw('plan-empty').textContent=busy ? '本轮仍在处理。旧方案保存在历史记录里，待新结果返回后再确认。' : '需求还在澄清。形成可执行方案后，再分别确认产品需求和技术方案。';
  iw('result-empty').hidden=!!data.task || ['integrated','released','observed'].includes(data.stage);
  const stages=['业务问题','产品需求','技术方案','开发与测试','验收与集成','效果回收'];
  iw('stage-track').replaceChildren();
  stages.forEach((label,index)=>{
    const li=uiElement('li',index<view.step ? 'done' : index===view.step ? 'current' : '',label);
    if(index===view.step)li.setAttribute('aria-current','step');iw('stage-track').append(li);
  });
  if(!activeIwPane || activeIwStage!==data.stage){selectIwPane(view.pane);activeIwStage=data.stage;}
}
