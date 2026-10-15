/* One persistent initiative, rendered inside the workbench's decision view. */
let initiativeWork = null;
let initiativeWorkId = null;
let initiativeWorkTimer = null;
let initiativeWorkPending = false;
let initiativeWorkEpoch = 0;
const initiativeWorkActions = new Map();
let initiativeWorkRead = 0;
let initiativeWorkReadable = true;
let initiativeReadGeneration = 0;
const workbenchPendingReads = new Map();
function shareWorkbenchRead(scope, resource, read) {
  const key=JSON.stringify([scope,resource]);
  if(workbenchPendingReads.has(key))return workbenchPendingReads.get(key);
  const pending=read().finally(()=>{if(workbenchPendingReads.get(key)===pending)workbenchPendingReads.delete(key);});
  workbenchPendingReads.set(key,pending);return pending;
}
function invalidateInitiativeReads() {
  ++initiativeReadGeneration;++initiativeWorkRead;
  if(typeof platformState!=='undefined'){++platformState.runtimeRead;++platformState.deploymentRead;++platformState.planRead;}
}
function invalidateInitiativeWork(message, clear=false) {
  initiativeWorkReadable=false;
  iw('error').textContent=message;
  iw('stage').textContent='状态不可读';
  document.getElementById('initiative-work').querySelectorAll?.('button').forEach(button=>button.disabled=true);
  if(clear) {
    ['proposal','result','delivery','findings'].forEach(id=>iw(id).hidden=true);
    ['documents','invocation','progress','checks','diff','result-summary','latest-answer',
      'learning-generation-evidence','learning-generation-status','learning-summary','learning-evidence','learning-assets','learning-matches'].forEach(id=>{if(iw(id))iw(id).textContent='';});
    iw('history').replaceChildren();iw('answers').replaceChildren();
    iw('preview-link').hidden=true;iw('preview-frame').hidden=true;iw('preview-frame').removeAttribute('src');
  }
  if(typeof invalidatePlatformRuntime==='function')invalidatePlatformRuntime(message);
}
const iw = id => document.getElementById('iw-' + id);
function initiativeReviewBase(data) {
  const evidence=data.eval_harness || {},runner=evidence.runner || {};
  return [data.active_task_id || data.task?.id || '',runner.candidate_sha256 || '',runner.report_path || '',evidence.generated_at ?? null];
}
const iwStages = {queued:'已排队，等待执行',cancelling:'正在取消并保留证据',cancelled:'已取消，可重新调研',interrupted:'服务重启，本轮中断，请核对后重试',released:'已发布，效果待观察',observed:'已回收实际效果',idle:'先让 Codex 检查已有实现',researching:'Codex 正在调研与整理问题',clarifying:'等待你回答业务问题',ready:'方案已提出，等待你确认',confirmed:'目标已确认，等待授权执行',executing:'正在修改与独立复验',review:'本轮候选等待验收',rework:'本轮需要修订，可在这里反馈',accepted:'候选已接受，等待确认集成',integrating:'正在核对并集成',integrated:'已集成到当前项目源码',failed:'本轮已停止，原记录保留'};
function iwList(id, values) {
  iw(id).replaceChildren();
  (values || []).forEach(text=>{const item=document.createElement('li');item.textContent=text;iw(id).appendChild(item);});
}
function renderDeliveryDecisions(data) {
  const route=data.delivery_policy || {mode:'standard',quick_eligible:false,reasons:[]};
  const editable=['idle','clarifying','ready'].includes(data.stage) && !data.v0;
  const labels={standard:'常规交付',quick:'局部小修改',strict:'关键业务交付',course:'课程训练'};
  iw('route-panel').hidden=!!data.v0;
  iw('route-status').textContent=(labels[route.mode] || '常规交付')+' · '+(route.reasons || []).join('；');
  const binding=JSON.stringify([data.id,route.decision?.at || 0]);
  if(iw('route-mode').dataset.binding!==binding){iw('route-mode').value=route.mode;iw('route-mode').dataset.binding=binding;}
  iw('route-mode').disabled=initiativeWorkPending || !editable || route.course_bound;
  iw('route-note').disabled=initiativeWorkPending || !editable;
  iw('route-save').disabled=initiativeWorkPending || !editable;
  const quickOption=[...iw('route-mode').querySelectorAll('option')].find(o=>o.value==='quick');
  if(quickOption)quickOption.disabled=!route.quick_eligible;
  const quick=data.stage==='ready' && route.mode==='quick' && route.quick_eligible;
  iw('quick-execute').hidden=!quick;
  iw('quick-execute').disabled=initiativeWorkPending || !data.enabled || !!(data.source_check && data.source_check.status!=='current');
  if(quick){
    iw('confirm-prd').hidden=true;iw('confirm').hidden=true;
    iw('next-title').textContent='核对这项局部修改并授权';
    iw('next-description').textContent='核对目标、文件范围、验收条件和验收负责人后，一次确认并授权；检查结束仍需人审。';
  }
  iw('renew-panel').hidden=data.stage!=='confirmed' || !data.authorization_expired || !!data.v0;
  iw('renew').disabled=initiativeWorkPending || !!(data.source_check && data.source_check.status!=='current');
  if(data.authorization_expired && data.stage==='confirmed')iw('execute').disabled=true;
  if(data.authorization_expired && data.stage==='confirmed'){
    iw('next-title').textContent='执行授权已过期，先核对续期';
    iw('next-description').textContent='合同与源码未变化时可以续期，保留原确认；续期后再授权执行。';
  }
  const repairing=!data.v0 && ['review','rework','failed','interrupted','cancelled'].includes(data.stage);
  iw('rework-panel').hidden=!repairing;
  iw('open-rework').hidden=!repairing;
  iw('open-rework').disabled=initiativeWorkPending || !data.enabled || !repairing;
  iw('rework-submit').disabled=initiativeWorkPending || !data.enabled || !repairing;
  iw('rework-reason').disabled=initiativeWorkPending || !repairing;
  iw('rework-note').disabled=initiativeWorkPending || !repairing;
  renderReworkHelp();
}
function renderReworkHelp() {
  const help={implementation:'保留原目标、需求确认、写集和已消耗预算，建立新一轮修复方案。核对后再授权执行。',
    technical:'重新调研技术方案；业务目标与验收条件未变化时沿用原需求确认，技术方案仍需重新确认。',
    requirement:'重新澄清需求并生成新版本；原始判断、失败与确认记录继续保留。',
    environment:'保留代码、失败记录和已消耗预算。可复验的候选只重跑原检查；中断任务核对后准备新轮授权。预算耗尽或用量不可核验时先重新讨论。'};
  iw('rework-help').textContent=help[iw('rework-reason').value] || help.implementation;
}
function iwDiscussionText(extra='') {
  const answers=[...iw('answers').querySelectorAll('textarea')].filter(el=>el.value.trim())
    .map(el=>'问题：'+el.dataset.question+'\n回答：'+el.value.trim());
  return [...answers,iw('message').value.trim(),extra].filter(Boolean).join('\n\n');
}
function renderIwAnswers(data, busy) {
  const questions=data.stage==='clarifying' ? data.proposal?.questions || [] : [];
  const signature=JSON.stringify([data.id,data.document_version || 0,questions]);
  const box=iw('answers');
  // Polling must not overwrite an answer while the user is typing.
  if(box.dataset.questions!==signature && !busy) {
    box.replaceChildren();box.dataset.questions=signature;
    questions.forEach((question,index)=>{
      const label=document.createElement('label');label.textContent=question;label.htmlFor='iw-answer-'+index;
      const input=document.createElement('textarea');input.id=label.htmlFor;input.rows=2;input.maxLength=3000;
      input.dataset.question=question;input.placeholder='填写这一项的回答；也可在下方补充统一说明。';
      box.append(label,input);
    });
  }
  const peopleOnly=questions.length>0 && questions.every(q=>/复验者|验收负责人|验收人/.test(q) && /谁|姓名|人员/.test(q));
  iw('defer-people').hidden=!peopleOnly || busy;
  iw('defer-people').disabled=initiativeWorkPending || !data.enabled;
}
function renderInitiativeWork(data) {
  if(typeof WorkbenchDrafts!=='undefined')WorkbenchDrafts.flush();
  if(initiativeWork && (initiativeWork.id!==data.id ||
      JSON.stringify(initiativeReviewBase(initiativeWork))!==JSON.stringify(initiativeReviewBase(data)))) {
    if(typeof WorkbenchDrafts!=='undefined')WorkbenchDrafts.detach(['review']);
    iw('note').value='';
  }
  if(initiativeWorkReadable)document.getElementById('initiative-work').querySelectorAll?.('button').forEach(button=>button.disabled=false);
  renderEvalHarness(data);
  const loop=data.repair_loop || {config:{},history:[]};
  iw('loop-status').textContent=(loop.config.enabled ? '停止闸门已启用 · ' : '当前仅观察，停止闸门未启用 · ')+
    loop.status+' · '+loop.reason+' · '+loop.rounds+'/'+(loop.config.max_rounds || 3)+' 轮 · Token '+loop.tokens_used+'/'+(loop.config.token_budget || 30000);
  iw('loop-save').disabled=initiativeWorkPending || ['queued','executing','checking','integrating'].includes(data.stage);
  if(document.activeElement!==iw('loop-rounds'))iw('loop-rounds').value=loop.config.max_rounds || 3;
  if(document.activeElement!==iw('loop-time'))iw('loop-time').value=loop.config.time_budget_seconds || 900;
  if(document.activeElement!==iw('loop-tokens'))iw('loop-tokens').value=loop.config.token_budget || 30000;
  iw('loop-enabled').checked=!!loop.config.enabled;
  iw('loop-detail').textContent=JSON.stringify({history:loop.history,remaining_failures:loop.remaining_failures,handoff:loop.handoff},null,2);
  const ci=data.ci_evidence || {status:'missing',history:[]};
  iw('ci-status').textContent=ci.status==='verified' ? '已核验真实 CI 证据；仍需人工验收。' : '尚未登记可回查的真实 CI 证据。';
  iw('ci-record').disabled=initiativeWorkPending || !ci.can_record;
  iw('ci-detail').textContent=JSON.stringify({latest:ci.latest || null,history:ci.history || []},null,2);
  renderQualityHook(data);
  if(typeof renderLearning==='function')renderLearning(data);
  if(initiativeWork && initiativeWork.active_task_id!==data.active_task_id) {
    iw('preview-frame').hidden=true;iw('preview-frame').removeAttribute('src');iw('preview-link').hidden=true;iw('preview-status').textContent='';
  }
  initiativeWork=data;
  document.getElementById('v0-submit').disabled=initiativeWorkPending || !['idle','rework','failed','cancelled','interrupted'].includes(data.stage);
  document.getElementById('v0-open-task').hidden=!data.task;
  document.getElementById('v0-status').textContent=data.v0 ? '本次任务：' + (data.active_task_id || '') + ' · ' + ((data.task || {}).status || data.stage) + '。原始合同、每轮输出和人工确认请到交付记录查看。' : '';
  if(data.stage!=='idle')document.getElementById('initiative-decision').hidden=true;
  iw('stage').textContent=iwStages[data.stage] || data.stage;
  iw('error').textContent=data.error || data.warning || (!data.enabled ? '当前服务尚未开启 Codex 执行。请保留原运行目录，以 --enable-code-execution 启动工作台。' : '');
  const busy=['researching','queued','executing','checking','cancelling','integrating'].includes(data.stage);
  const complete=['integrated','released','observed'].includes(data.stage);
  renderIwAnswers(data,busy);
  const check=data.source_check;
  iw('source-check').hidden=!check || check.status==='current';
  iw('source-summary').textContent='查看所属项目的文件差异（'+(check?.files.length || 0)+' 项）';
  iwList('source-files',(check?.files || []).map(f=>({added:'新纳入',removed:'不再纳入',modified:'内容修改'}[f.kind])+ '：'+f.path));
  iw('recheck').hidden=check?.status==='current';
  iw('recheck').disabled=busy || initiativeWorkPending || !data.enabled;
  iw('project').textContent=data.project ? '项目：'+data.project.name+' · '+data.project.root_path : '项目：尚未关联';
  iw('cancel').hidden=!['researching','queued','executing','checking'].includes(data.stage);
  iw('cancel').disabled=initiativeWorkPending;
  iw('delivery').hidden=!complete;
  iw('outcome-form').hidden=!data.current_release;
  iw('observation-status').textContent=data.observation_status || '尚未登记实际发布';
  iw('documents').textContent=JSON.stringify({documents:data.documents || [],confirmations:data.document_confirmations || [],
    route_decisions:data.route_decisions || [],rework_decisions:data.rework_decisions || [],authorization_renewals:data.authorization_renewals || [],
    delivery_records:data.delivery_records || [],completed_cycles:data.completed_cycles || []},null,2);
  ['release','outcome','reopen'].forEach(key=>iw(key).disabled=initiativeWorkPending);
  const doc=(data.documents || []).slice(-1)[0];
  iw('prd').textContent=doc ? '版本 '+doc.version+'\n业务问题：'+doc.prd.business_problem+
    '\n使用者与场景：'+((doc.prd.users || []).join('；') || '此版本未单独记录，请在讨论中补充')+
    '\n本期产品范围：'+((doc.prd.scope || []).join('；') || '此版本未单独记录，请核对下方目标与验收')+
    '\n待确认问题：'+((doc.prd.questions || []).join('；') || '没有待答问题；仍需人工核对产品口径') : '';
  if(doc && iw('prd-metric').dataset.version!==data.id+':'+doc.version){iw('prd-metric').value=doc.prd.success_metric || '';iw('prd-metric').dataset.version=data.id+':'+doc.version;}
  iw('prd-metric').disabled=data.stage!=='ready' || data.prd_confirmed;
  iw('technical').textContent=doc ? '依据：'+doc.technical_plan.findings.join('\n')+'\n测试计划：'+
    ((doc.technical_plan.test_plan || []).join('\n') || '此版本未单独记录测试方法，请补充数据、操作、检查与失败路径') : '';
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
    iw('confirm').disabled=initiativeWorkPending || !data.prd_confirmed || !!(check && check.status!=='current');
    iw('execute').hidden=data.stage!=='confirmed';iw('execute').disabled=initiativeWorkPending || !data.enabled || !!(check && check.status!=='current');
    iw('reviewer').disabled=data.stage!=='ready';
    if(data.reviewer && data.stage!=='ready') iw('reviewer').value=data.reviewer;
  }
  const task=data.task;
  iw('result').hidden=!task;
  if(task) {
    const summary=data.eval_harness?.summary || task.summary;
    iw('result-summary').textContent=task.id+' · '+task.status+(summary ? '\n自动检查：'+summary.passed+' / '+summary.total+' 项通过' : '')+(task.error ? '\n'+task.error : '');
    iw('diff').textContent=task.diff || '尚无最终文件变化记录';
    iw('checks').textContent=JSON.stringify({summary,changed_files:task.changed_files,events:task.events},null,2);
    const packaged=task.events.some(event=>event.detail==='日常研发交付包已保存');
    iw('patch').hidden=!packaged;iw('patch').href='/api/v1/tasks/'+task.id+'/patch';
    iw('preview').hidden=!['review','accepted','integrated'].includes(data.stage);iw('preview').disabled=initiativeWorkPending;
    iw('accept').hidden=data.stage!=='review';iw('accept').disabled=initiativeWorkPending ||
      ['stale','unavailable'].includes(data.eval_harness?.freshness) || !!data.eval_harness?.error;
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
  if(typeof renderPlatformWorkflow==='function')renderPlatformWorkflow(data);
  if(data.v0){['accept','integrate','preview','execute','discuss'].forEach(id=>iw(id).hidden=true);}
  renderDeliveryDecisions(data);
  if(typeof trackWorkflowDrafts==='function')trackWorkflowDrafts(data);
  if(!initiativeWorkReadable)invalidateInitiativeWork('当前记录未完成重新核对，旧结论不可用于授权或验收。');
}
function refreshInitiativeWork({force=false,afterMutation=false}={}) {
  if(!initiativeWorkId || !afterMutation && (initiativeWorkPending || typeof platformState!=='undefined' && platformState.runtimePending))return Promise.resolve();
  if(afterMutation)invalidateInitiativeReads();
  const id=initiativeWorkId,epoch=initiativeWorkEpoch,generation=initiativeReadGeneration;
  const pending=shareWorkbenchRead([id,epoch,generation],'workflow',async()=>{
    const request=++initiativeWorkRead;
    try {
      const data=await api('/api/v1/initiatives/'+encodeURIComponent(id)+'/workflow');
      if(id!==initiativeWorkId || epoch!==initiativeWorkEpoch || request!==initiativeWorkRead)return;
      if(data.id!==id || !data.stage || !Number.isInteger(data.revision))throw new Error('事项证据与请求不一致');
      initiativeWorkReadable=true;renderInitiativeWork(data);return data;
    }catch(error){if(id===initiativeWorkId && epoch===initiativeWorkEpoch && request===initiativeWorkRead)invalidateInitiativeWork('读取失败，旧结论不可用于授权或验收：'+error.message);}
  });
  return pending.then(data=>{if(data && id===initiativeWorkId && epoch===initiativeWorkEpoch && generation===initiativeReadGeneration && typeof refreshPlatformViews==='function')return refreshPlatformViews({force});});
}
function showInitiativeWork(item) {
  if(typeof WorkbenchDrafts!=='undefined')WorkbenchDrafts.detach(['discussion','review','v0','learning-candidate','learning-decisions','delivery-route','rework-decision','authorization-renewal']);
  clearInterval(initiativeWorkTimer);initiativeWorkTimer=null;++initiativeWorkRead;
  ++initiativeWorkEpoch;
  initiativeWorkId=item && item.id;initiativeWork=null;
  initiativeWorkPending=initiativeWorkActions.has(initiativeWorkId);
  if(typeof resetPlatformInitiative==='function')resetPlatformInitiative(item);
  document.getElementById('initiative-work').hidden=!item;
  activeIwPane=null;activeIwStage=null;
  document.getElementById('v0-confirmed').checked=false;
  ['v0-spec','v0-workspace','v0-files','iw-message','iw-note','iw-next','iw-route-note','iw-rework-note','iw-renew-note',
    'iw-release-version','iw-release-environment','iw-release-evidence',
    'iw-outcome-period','iw-outcome-target','iw-outcome-actual','iw-outcome-observation','iw-outcome-evidence','iw-outcome-conclusion',
    ...['task','feedback','supersedes','title','content','applies','excludes','boundary','conflict','parameters','paths','contains','precheck','implement','eval','review','outputs','stop','rollback',
      'generation-id','evidence-refs','guidance','check-preconditions','check-precheck','check-implement','check-eval','required-implement','required-eval'].map(k=>'iw-learning-'+k)
  ].forEach(id=>{const el=document.getElementById(id);if(el)el.value='';});
  iw('answers').replaceChildren();delete iw('answers').dataset.questions;
  iw('learning-kind').value='memory';iw('learning-recipe').hidden=true;
  if(iw('learning-schema'))iw('learning-schema').value='1';if(iw('learning-v2'))iw('learning-v2').hidden=true;
  ['task','feedback'].forEach(k=>{iw('learning-'+k).replaceChildren();delete iw('learning-'+k).dataset.sources;delete iw('learning-'+k).dataset.initiative;});
  if(!item)return;
  invalidateInitiativeWork('正在读取本事项的最新进展…',true);
  iw('message').value='';iw('note').value='';iw('reviewer').value=item.reviewer || '';
  iw('preview-frame').hidden=true;iw('preview-frame').removeAttribute('src');iw('preview-link').hidden=true;iw('preview-status').textContent='';
  refreshInitiativeWork();
  initiativeWorkTimer=setInterval(()=>{if(!document.getElementById('view-decision').hidden && !initiativeWorkPending)refreshInitiativeWork();},2500);
}
async function initiativeWorkAction(action, extra={}) {
  if(!initiativeWork || initiativeWorkPending || initiativeWorkActions.has(initiativeWorkId) || !initiativeWorkReadable)return;
  const itemId=initiativeWorkId,epoch=initiativeWorkEpoch,token={};
  const currentView=()=>itemId===initiativeWorkId && epoch===initiativeWorkEpoch;
  const draftGroup=({discuss:'discussion',accept:'review',v0:'v0','delivery-route':'delivery-route',rework:'rework-decision','renew-authorization':'authorization-renewal'})[action] ||
    (action==='learning' ? ({create:'learning-candidate',decide:'learning-decisions'})[extra.fields?.action] : null);
  const draftSnapshot=draftGroup && typeof WorkbenchDrafts!=='undefined' ? WorkbenchDrafts.capture?.(draftGroup) : null;
  if(action==='accept' && (!extra.note?.trim() || draftSnapshot &&
      draftSnapshot.base!==JSON.stringify(initiativeReviewBase(initiativeWork)))) {
    iw('error').textContent='请核对当前候选和 Eval，填写属于本轮的验收意见后再接受。';return;
  }
  const discussionInput=()=>({message:iw('message').value,
    answers:Object.fromEntries([...iw('answers').querySelectorAll('textarea')].map(el=>[el.dataset.question,el.value]))});
  const submittedDiscussion=action==='discuss' ? JSON.stringify(discussionInput()) : null;
  const clearDiscussion=()=>{iw('message').value='';iw('answers').querySelectorAll('textarea').forEach(el=>el.value='');};
  let actionError='';
  invalidateInitiativeReads();initiativeWorkActions.set(itemId,token);initiativeWorkPending=true;renderInitiativeWork(initiativeWork);
  try {
    const body={actor:actorName(),revision:initiativeWork.revision,...extra};
    if(action==='cancel' && ['queued','executing'].includes(initiativeWork.stage) &&
        typeof platformState!=='undefined' && platformState.runtimeReadable && platformState.itemId===itemId) {
      const runtime=platformState.runtime;
      if(runtime && runtime.run_id===initiativeWork.plan?.plan_id &&
          ['running','queued','awaiting_approval','pausing'].includes(runtime.state) &&
          typeof runtime.session_id==='string' && runtime.session_id && Number.isInteger(runtime.control_revision))
        Object.assign(body,{run_id:runtime.run_id,session_id:runtime.session_id,control_revision:runtime.control_revision});
    }
    if(action==='learning' && ['generate','cancel_generation'].includes(extra.fields?.action) && typeof platformKey==='function')body.submission_key=platformKey(itemId+'/learning/'+extra.fields.action,body);
    if(['delivery-route','confirm-execute','rework','renew-authorization'].includes(action) && typeof platformKey==='function')body.submission_key=platformKey(itemId+'/workflow/'+action,body);
    const path='/api/v1/initiatives/'+itemId+'/workflow/'+action;
    const data=await (body.submission_key && typeof platformPost==='function' ? platformPost(path,body) : api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
    if(!currentView()){if(draftSnapshot)WorkbenchDrafts.clearSubmitted(draftSnapshot);return;}
    if(data.id!==itemId || !data.stage || !Number.isInteger(data.revision))throw new Error('事项操作返回了无法核对的记录');
    invalidateInitiativeReads();initiativeWorkReadable=true;
    if(draftSnapshot)WorkbenchDrafts.clearSubmitted(draftSnapshot,action==='discuss' ? clearDiscussion : undefined);
    else if(action==='discuss' && JSON.stringify(discussionInput())===submittedDiscussion)clearDiscussion();
    renderInitiativeWork(data);
    if(['confirm','confirm-execute'].includes(action)) {
      const confirmed=await api('/api/v1/initiatives/'+itemId);
      if(!currentView())return;
      currentInitiative=confirmed;
      document.getElementById('initiative-decision').hidden=true;
    }
    if(typeof refreshPlatformViews==='function')await refreshPlatformViews({force:true});
    return true;
  }catch(error){actionError=error.message;if(currentView())initiativeWorkReadable=false;}
  finally{
    if(typeof WorkbenchDrafts!=='undefined')WorkbenchDrafts.flush();
    if(initiativeWorkActions.get(itemId)===token)initiativeWorkActions.delete(itemId);
    if(itemId===initiativeWorkId){
      initiativeWorkPending=initiativeWorkActions.has(itemId);
      if(currentView()){
        if(initiativeWork)renderInitiativeWork(initiativeWork);
        if(actionError)invalidateInitiativeWork('操作结果待核对，请刷新本事项后再决定：'+actionError);
      }else if(!initiativeWorkPending)void refreshInitiativeWork();
    }
  }
}
function initInitiativeWork() {
  iw('eval-run').onclick=()=>initiativeWorkAction('eval');
  iw('loop-save').onclick=()=>initiativeWorkAction('loop-config',{fields:{enabled:iw('loop-enabled').checked,
    max_rounds:Number(iw('loop-rounds').value),time_budget_seconds:Number(iw('loop-time').value),token_budget:Number(iw('loop-tokens').value)}});
  iw('ci-record').onclick=async()=>{
    const report=iw('ci-report').files[0], envelope=iw('ci-envelope').files[0];
    if(!report || !envelope){iw('error').textContent='请选择同一次 Run 的 Harness 报告和 Evidence Envelope。';return;}
    try {
      const envelopeData=JSON.parse(await envelope.text());
      await initiativeWorkAction('ci-evidence',{fields:{report_text:await report.text(),envelope:envelopeData,
        run_url:iw('ci-run-url').value.trim(),candidate_sha:iw('ci-sha').value.trim(),job_conclusion:iw('ci-conclusion').value}});
    } catch(error) { iw('error').textContent='CI 证据读取失败：'+error.message; }
  };
  iw('hook-prepare').onclick=()=>initiativeWorkAction('prepare-hook');
  if(typeof initLearning==='function')initLearning();
  if(typeof initPlatform==='function')initPlatform();
  document.getElementById('v0-submit').onclick=async()=>{
    const value=id=>document.getElementById(id).value;
    if(!document.getElementById('v0-confirmed').checked){document.getElementById('v0-status').textContent='请先核对并确认本次合同和执行参数。';return;}
    const extra={spec_text:value('v0-spec'), execution_mode:value('v0-mode'),
      workspace_path:value('v0-workspace').trim(), write_scope:value('v0-files').split(/\r?\n/).map(v=>v.trim()).filter(Boolean),
      execution_timeout_seconds:Number(value('v0-timeout')), confirmed:true};
    const text=extra.spec_text, payload=JSON.stringify({actor:document.getElementById('task-actor').value.trim(),revision:initiativeWork?.revision,...extra});
    if([...text].length>24000){document.getElementById('v0-status').textContent='合同最多 24,000 个字符。输入已保留，请缩短后提交。';return;}
    if(new TextEncoder().encode(payload).length>524288){document.getElementById('v0-status').textContent='本次请求超过 512 KiB。输入已保留，请缩短路径或范围内容。';return;}
    if(await initiativeWorkAction('v0',extra))document.getElementById('v0-confirmed').checked=false;
  };
  document.getElementById('v0-open-task').onclick=()=>{
    if(initiativeWork && initiativeWork.task)loadDetail(initiativeWork.task.id);
  };
  document.querySelectorAll('[data-iw-pane]').forEach(b=>b.onclick=()=>selectIwPane(b.dataset.iwPane));
  iw('primary-next').onclick=()=>selectIwPane(deliveryStageView(initiativeWork.stage).pane);
  iw('discuss').onclick=()=>initiativeWorkAction('discuss',{text:iwDiscussionText() || (initiativeWork && !initiativeWork.messages.length && currentInitiative && currentInitiative.raw_signal) || ''});
  iw('recheck').onclick=()=>initiativeWorkAction('discuss',{text:iwDiscussionText('请依据最新项目文件重新核对既有方案，保留此前回答与已确定范围；仅询问因变化新增的必要问题。')});
  iw('defer-people').onclick=()=>initiativeWorkAction('discuss',{text:iwDiscussionText('独立复验者和最终人工验收负责人暂待确认。请先完善产品与技术方案，在相应确认和人工接受决定前落实真实人员及职责，不把人员待定作为重复澄清问题。')});
  iw('confirm').onclick=()=>initiativeWorkAction('confirm',{reviewer:iw('reviewer').value.trim()});
  iw('confirm-prd').onclick=()=>initiativeWorkAction('confirm-prd',{success_metric:iw('prd-metric').value.trim()});
  iw('route-save').onclick=()=>initiativeWorkAction('delivery-route',{mode:iw('route-mode').value,note:iw('route-note').value.trim()});
  iw('quick-execute').onclick=()=>initiativeWorkAction('confirm-execute',{reviewer:iw('reviewer').value.trim(),success_metric:iw('prd-metric').value.trim(),confirmed:true});
  iw('renew').onclick=()=>initiativeWorkAction('renew-authorization',{note:iw('renew-note').value.trim(),confirmed:true});
  iw('rework-reason').onchange=renderReworkHelp;
  iw('open-rework').onclick=()=>selectIwPane('action');
  iw('rework-submit').onclick=()=>initiativeWorkAction('rework',{reason:iw('rework-reason').value,note:iw('rework-note').value.trim()});
  iw('cancel').onclick=()=>initiativeWorkAction('cancel');
  iw('release').onclick=()=>initiativeWorkAction('release',{fields:{version:iw('release-version').value.trim(),environment:iw('release-environment').value.trim(),evidence:iw('release-evidence').value.trim()}});
  iw('outcome').onclick=()=>{const fields={};['period','target','actual','observation','evidence','conclusion'].forEach(k=>fields[k]=iw('outcome-'+k).value.trim());initiativeWorkAction('outcome',{fields});};
  iw('reopen').onclick=()=>initiativeWorkAction('reopen',{text:iw('next').value.trim()});
  iw('execute').onclick=()=>initiativeWorkAction('execute');
  iw('accept').onclick=()=>initiativeWorkAction('accept',{note:iw('note').value.trim()});
  iw('integrate').onclick=()=>initiativeWorkAction('integrate');
  iw('preview').onclick=()=>openPlatformPreview();
}

function renderQualityHook(data) {
  const hook=data.quality_hook || {}, project=data.plan?.project || {};
  const labels={not_prepared:'尚未准备',prepared:'待审文件已准备，尚未安装',installed:'安装文件匹配，信任与真实触发待核对',
    different:'已有安装文件与本次待审版本不一致',stale:'绑定的候选或任务已变化，请重新准备',unavailable:'文件无法核对'};
  iw('hook-status').textContent=(labels[hook.status] || '状态未知')+(hook.error ? '：'+hook.error : '');
  iw('hook-prepare').disabled=initiativeWorkPending || !hook.can_prepare;
  iw('hook-next').textContent=hook.path ? '先审查下方文件。将 hooks.json 的 Stop 规则合并到候选 .codex/hooks.json，将处理器安装到 .codex/hooks/quality_gate.py；已有文件先备份，再到 /hooks 审查与信任。' : '取得本轮项目候选后，可在这里准备 Hook。';
  iw('hook-files').textContent=JSON.stringify({candidate:data.workspace || null,task:data.active_task_id || null,
    command:project.eval_command || [],package:hook.path || null,prepared_by:hook.actor || null,
    handler_sha256:hook.handler_sha256 || null,config_sha256:hook.config_sha256 || null},null,2);
  iw('hook-source').textContent=Object.entries(hook.review_files || {}).map(([name,text])=>name+'\n'+text).join('\n\n');
  iw('hook-runs').replaceChildren();
  (hook.runs || []).forEach(run=>{
    const li=document.createElement('li');
    li.textContent=new Date(run.at*1000).toLocaleString()+' · '+
      ({pass:'所选检查通过',block:'阻断失败',skipped:'重入跳过，未验证',unverified:'未完成验证'}[run.outcome] || run.outcome)+
      (run.freshness ? ' · '+({current:'报告与候选一致',stale:'报告已过期',unavailable:'报告无法核对',unverified:'来源未验证'}[run.freshness] || run.freshness) : '')+
      (run.error ? ' · '+run.error : '')+' · 宿主自动触发待核对';
    iw('hook-runs').append(li);
  });
}

function renderEvalHarness(data) {
  const view=data.eval_harness || {}, summary=view.summary || {};
  const running=data.stage==='checking';
  iw('eval-run').disabled=initiativeWorkPending || running || !view.can_run;
  iw('eval-run').textContent=running ? '检查正在运行…' : '重新运行候选检查';
  iw('eval-summary').textContent=running ? '正在执行候选检查，旧结果仅供追溯。' : view.error ? '本次检查未形成可信报告：'+view.error : !view.available ?
    '尚无检查报告，不能判断是否通过。' :
    ({pass:'所选阻断检查通过',block:'存在阻断失败，需要返工'}[summary.decision] || '检查结论未知')+
    ' · 通过 '+(summary.passed ?? '未知')+'/'+(summary.total ?? '未知')+
    ' · 阻断失败 '+(summary.blocking_failed ?? '未知')+' · 观察告警 '+(summary.observing_failed ?? 0);
  iw('eval-source').textContent=({current:'来源已核对：报告与当前候选一致。',
    stale:'报告或候选已变化：旧结论不可用于验收，请重新检查。',
    unavailable:'来源无法读取：请恢复候选与报告后复验。',
    unverified:'此记录尚无可核对的候选来源绑定。'}[view.freshness || 'unverified']);
  iw('eval-results').replaceChildren();
  (view.results || []).forEach(row=>{
    const li=document.createElement('li'), title=document.createElement('strong'), evidence=document.createElement('p');
    title.textContent=(row.passed ? '通过' : row.level==='blocking' ? '阻断' : '观察')+' · '+row.name+
      ' · '+(row.duration_ms == null ? '未记录耗时' : row.duration_ms+' ms');
    evidence.textContent=String(row.evidence || '')+(row.error ? '\n'+JSON.stringify(row.error) : '');
    li.append(title,evidence);iw('eval-results').append(li);
  });
  iw('eval-provenance').textContent=JSON.stringify(view.runner || {},null,2);
  iw('eval-history').replaceChildren();
  (data.eval_runs || []).slice().reverse().forEach(run=>{
    const li=document.createElement('li');
    li.textContent=new Date(run.at*1000).toLocaleString()+' · '+run.actor+' · '+run.task_id+' · '+
      (run.error ? '执行失败：'+run.error : run.report.summary.decision+' · '+run.report.runner.report_path);
    iw('eval-history').append(li);
  });
}

let activeIwPane=null, activeIwStage=null;
function selectIwPane(pane) {
  activeIwPane=pane;
  ['action','plan','result','runtime'].forEach(key=>{
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
