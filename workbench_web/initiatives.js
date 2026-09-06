let currentInitiative = null;
let initiativeReadVersion = 0;
let initiativeDirty = false;
const initiativeFields = {title:'title',raw_signal:'raw',source:'source',problem_statement:'problem',goal:'goal',non_goals:'nongoals',acceptance:'acceptance',evidence:'evidence',reviewer:'reviewer'};
const initiativeLabels = {investigating:'待整理',approved_for_delivery:'已决定进入交付准备',delivering:'已关联交付任务',deferred:'已暂缓',rejected:'已决定不做',stopped:'已停止',experiment:'试验中'};
function initiativeInput(key) { return document.getElementById('init-' + key); }
function initiativeLines(key) { return initiativeInput(key).value.split(/\n/).map(x=>x.trim()).filter(Boolean); }
function showInitiative(item) {
  currentInitiative = item;
  initiativeDirty = false;
  document.getElementById('initiative-editor').hidden = false;
  document.getElementById('initiative-contract').open = !(item && item.decision);
  show('initiative-title', item ? item.title + ' · 版本 ' + item.version : '记录新事项');
  Object.entries(initiativeFields).forEach(function([field,id]) {
    let value = item ? item[field] : '';
    if (Array.isArray(value)) value = value.map(x=>typeof x === 'object' ? x.content : x).join('\n');
    initiativeInput(id).value = value || '';
    initiativeInput(id).disabled = !!(item && (item.decision || ['title','raw'].includes(id)));
  });
  initiativeInput('area').value = item && item.affected_areas ? item.affected_areas[0] || '' : '';
  const areas = item && item.affected_areas || [];
  initiativeInput('area').disabled = !!(item && item.decision) || areas.length > 1 ||
    (areas.length === 1 && !['inventory','money','order','purchase','auth'].includes(areas[0]));
  document.getElementById('save-initiative').hidden = !!(item && item.decision);
  document.getElementById('save-initiative').disabled = false;
  document.getElementById('initiative-decision').hidden = !item || !!item.decision;
  document.getElementById('initiative-saved-decision').hidden = !item || !item.decision;
  document.getElementById('decide-initiative').disabled = false;
  ['rationale','trigger'].forEach(id=>initiativeInput(id).value = '');
  if (item) {
    const labels = {evidence:'已有证据',problem_statement:'具体问题',project_id:'项目',goal:'期望结果',acceptance:'验收标准',reviewer:'验收负责人'};
    const missing = [...new Set([...item.readiness.decision_missing, ...item.readiness.delivery_missing])];
    show('initiative-readiness', missing.length ? '进入交付前仍需补充：' + missing.map(x=>labels[x] || x).join('、') : '事项信息已具备，仍请你判断是否值得实施。');
    show('initiative-verdict', (initiativeLabels[item.status] || item.status) + '\n决定人：' + (item.decision_by || '未记录') + '\n理由：' + (item.decision_rationale || '') + (item.review_trigger ? '\n再评估条件：' + item.review_trigger : ''));
    document.getElementById('initiative-to-delivery').hidden = item.decision !== 'build';
    show('initiative-to-delivery', item.linked_task_id ? '查看交付任务 ' + item.linked_task_id : '核对课程交付方案');
  }
}
async function refreshInitiatives() {
  const version = ++initiativeReadVersion;
  show('initiative-list', '正在更新事项列表…');
  show('initiative-status', '正在读取事项…');
  try {
    const body = await api('/api/v1/initiatives');
    if (version !== initiativeReadVersion) return;
    const list = document.getElementById('initiative-list');
    list.innerHTML = '';
    if (!Array.isArray(body.items)) throw new Error('事项列表不完整');
    if (!body.items.length) list.textContent = '还没有事项。从一条现场反馈开始。';
    body.items.forEach(function(item) {
      const button = document.createElement('button'); button.type = 'button';
      button.className = 'initiative-item' + (currentInitiative && currentInitiative.id === item.id ? ' active' : '');
      const title = document.createElement('b'); title.textContent = item.title;
      const state = document.createElement('span'); state.textContent = (initiativeLabels[item.status] || item.status) + ' · ' + item.owner;
      button.appendChild(title); button.appendChild(state);
      button.onclick = async function() {
        if (initiativeDirty) { show('initiative-status','当前有未保存修改，请先保存再切换事项。'); return; }
        const selectedVersion = ++initiativeReadVersion;
        document.getElementById('initiative-editor').hidden = true;
        try {
          const selected = await api('/api/v1/initiatives/' + encodeURIComponent(item.id));
          if (selectedVersion !== initiativeReadVersion) return;
          showInitiative(selected); show('initiative-status','已读取最新事项版本。');
        } catch(error) { if(selectedVersion === initiativeReadVersion) show('initiative-status','读取失败：' + error.message); }
      };
      list.appendChild(button);
    });
    show('initiative-status','已读取 ' + body.items.length + ' 项记录。保存事项不会调用 Codex。');
    return true;
  } catch(error) { if(version === initiativeReadVersion) {show('initiative-list','列表读取失败，请重新打开事项与决策。');show('initiative-status','事项服务暂不可用：' + error.message);} return false; }
}
async function saveInitiative(event) {
  event.preventDefault();
  const button = document.getElementById('save-initiative'); if(button.disabled) return;
  const data = {source:initiativeInput('source').value.trim(), problem_statement:initiativeInput('problem').value.trim(), goal:initiativeInput('goal').value.trim(), non_goals:initiativeLines('nongoals'), acceptance:initiativeLines('acceptance'), evidence:initiativeLines('evidence'), reviewer:initiativeInput('reviewer').value.trim(), affected_areas:initiativeInput('area').value ? [initiativeInput('area').value] : [], project_id:'FlowERP'};
  const item = currentInitiative;
  if(item && initiativeInput('area').disabled) data.affected_areas = item.affected_areas;
  if(item && initiativeInput('evidence').value === item.evidence.map(x=>x.content).join('\n')) data.evidence = item.evidence;
  if(!item) {data.title=initiativeInput('title').value.trim(); data.raw_signal=initiativeInput('raw').value.trim();}
  button.disabled = true;
  try {
    const result = await api('/api/v1/initiatives' + (item ? '/' + item.id + '/revise' : ''), {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({actor:actorName(),version:item && item.version,data})});
    showInitiative(result); const refreshed = await refreshInitiatives(); show('initiative-status',refreshed ? '事项已保存，可继续补充或留下决定。' : '事项已保存，但列表暂不可读，请重新打开事项与决策。');
  } catch(error) { show('initiative-status','尚未确认保存：' + error.message + '。请先核对事项列表，避免重复新建。'); }
  finally {button.disabled=false;}
}
async function decideInitiative() {
  if(!currentInitiative || currentInitiative.decision) return;
  if(initiativeDirty) {show('initiative-status','请先保存修改，再对已保存的版本作决定。');return;}
  const button=document.getElementById('decide-initiative'); if(button.disabled)return;
  button.disabled=true;
  try {
    const result=await api('/api/v1/initiatives/' + currentInitiative.id + '/decide', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({actor:actorName(),version:currentInitiative.version,decision:initiativeInput('decision').value,rationale:initiativeInput('rationale').value.trim(),review_trigger:initiativeInput('trigger').value.trim()})});
    showInitiative(result); const refreshed = await refreshInitiatives(); show('initiative-status',refreshed ? '决定与署名已保存。代码尚未执行。' : '决定已保存，但列表暂不可读，请重新打开事项与决策。');
  }catch(error){show('initiative-status','决定未确认：' + error.message);}finally{button.disabled=false;}
}
function initInitiatives() {
  document.getElementById('capture-initiative').onclick=function(){ if(initiativeDirty){show('initiative-status','请先保存当前修改。');return;} ++initiativeReadVersion; showInitiative(null); };
  document.getElementById('initiative-form').addEventListener('submit',saveInitiative);
  document.getElementById('initiative-form').addEventListener('input',function(){initiativeDirty=true;});
  document.getElementById('decide-initiative').onclick=decideInitiative;
  document.getElementById('initiative-to-delivery').onclick=function(){
    if(!currentInitiative || currentInitiative.decision !== 'build')return;
    if(currentInitiative.linked_task_id) {loadDetail(currentInitiative.linked_task_id);return;}
    document.getElementById('task-request').value='事项：' + currentInitiative.title + '\n目标：' + currentInitiative.goal + '\n验收：' + currentInitiative.acceptance.join('；');
    openComposer({id:currentInitiative.id,version:currentInitiative.version});
    show('task-submit-status','已带入事项摘要；请核对课程合同是否覆盖本次需求。代码实现的具体任务以授权方案为准。');
  };
}
