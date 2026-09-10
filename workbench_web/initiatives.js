let currentInitiative = null;
let initiativeReadVersion = 0;
let initiativeDirty = false;
const initiativeFields = {title:'title',raw_signal:'raw',source:'source',problem_statement:'problem',goal:'goal',non_goals:'nongoals',acceptance:'acceptance',evidence:'evidence',reviewer:'reviewer',success_metric:'metric'};
const initiativeLabels = {released:'已发布，待观察',observed:'已回收实际效果',investigating:'待整理',approved_for_delivery:'已确认方案',delivering:'正在推进交付',integrated:'已集成',deferred:'已暂缓',rejected:'已决定不做',stopped:'已停止',experiment:'试验中'};
function initiativeInput(key) { return document.getElementById('init-' + key); }
function initiativeLines(key) { return initiativeInput(key).value.split(/\n/).map(x=>x.trim()).filter(Boolean); }
function showInitiative(item) {
  currentInitiative = item;
  document.getElementById("view-decision").classList.add("item-open");
  initiativeInput('project').disabled = !!item;
  if(item) initiativeInput('project').value=item.project_id;
  loadInitiativeProjects();
  initiativeDirty = false;
  document.getElementById('initiative-editor').hidden = false;
  document.getElementById('initiative-contract').open = !item;
  show('initiative-title', item ? item.title : '开始一项新的交付');
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
    show('initiative-to-delivery', '在本事项中继续推进');
  }
  showInitiativeWork(item);
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
  const data = {source:initiativeInput('source').value.trim(), problem_statement:initiativeInput('problem').value.trim(), goal:initiativeInput('goal').value.trim(), non_goals:initiativeLines('nongoals'), acceptance:initiativeLines('acceptance'), evidence:initiativeLines('evidence'), reviewer:initiativeInput('reviewer').value.trim(), affected_areas:initiativeInput('area').value ? [initiativeInput('area').value] : [], project_id:initiativeInput('project').value, success_metric:initiativeInput('metric').value.trim()};
  const item = currentInitiative;
  if(item && initiativeInput('area').disabled) data.affected_areas = item.affected_areas;
  if(item && initiativeInput('evidence').value === item.evidence.map(x=>x.content).join('\n')) data.evidence = item.evidence;
  if(!item) {data.title=initiativeInput('title').value.trim(); data.raw_signal=initiativeInput('raw').value.trim() || data.title; data.source=data.source || '工作台事项输入';}
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
  initInitiativeWork();
  document.getElementById('capture-initiative').onclick=function(){ if(initiativeDirty){show('initiative-status','请先保存当前修改。');return;} ++initiativeReadVersion; showInitiative(null); };
  document.getElementById('initiative-form').addEventListener('submit',saveInitiative);
  document.getElementById('initiative-form').addEventListener('input',function(){initiativeDirty=true;});
  document.getElementById('decide-initiative').onclick=decideInitiative;
  document.getElementById('initiative-to-delivery').onclick=function(){
    if(!currentInitiative || currentInitiative.decision !== 'build')return;
    document.getElementById('initiative-work').scrollIntoView({behavior:'smooth'});
  };
}

async function loadInitiativeProjects() {
  try {
    const body=await api('/api/v1/projects');
    const select=initiativeInput('project');
    const selected=currentInitiative ? currentInitiative.project_id : select.value || body.default_project;
    select.replaceChildren();
    body.items.forEach(project=>{const option=document.createElement('option');option.value=project.id;option.textContent=project.name+' · '+project.root_path;select.append(option);});
    select.value=body.items.some(p=>p.id===selected) ? selected : body.default_project;
    renderRegisteredProjects(body);
  } catch(error){show('project-status',error.message);}
}
let projectEditing=null, projectPending=false, projectRegistrationAvailable=true;
function projectField(id) {return document.getElementById('project-'+id);}
function projectSourceView() {
  const git=projectField('source').value==='git';
  projectField('url-field').hidden=!git || !!projectEditing;
  projectField('init-field').hidden=git || !!projectEditing;
  projectField('root-label').textContent=git ? '克隆到本地目录（目录须尚不存在）' : '本地目录';
  projectField('source-help').textContent=projectEditing ? '项目归属目录固定；配置质量检查不会移动已有事项。' : git ? '使用本机 Git 的现有登录。支持 HTTPS、git@host:path 或 ssh://git@host/path；请勿把令牌填入链接。克隆不会覆盖已有目录。' : '填写已有目录的绝对路径。已有 Git 仓库直接登记，不会重建。';
}
function editRegisteredProject(project=null) {
  if(projectPending)return;
  projectEditing=project;
  projectField('source').value='local';
  projectField('name').value=project?.name || '';
  projectField('root').value=project?.root_path || '';
  projectField('url').value='';
  projectField('eval').value=project?.eval_command?.length ? JSON.stringify(project.eval_command,null,2) : '';
  projectField('default').checked=false;
  ['source','name','root'].forEach(id=>projectField(id).disabled=!!project);
  projectField('form-title').textContent=project ? '配置项目：'+project.name : '添加项目';
  projectField('register').textContent=project ? '保存项目配置' : '添加项目';
  projectField('new').hidden=!project;
  projectSourceView();
}
function renderRegisteredProjects(body) {
  projectRegistrationAvailable=!!body.registration?.sources?.includes('git');
  projectField('register').disabled=projectPending || !projectRegistrationAvailable;
  if(!projectRegistrationAvailable)show('project-status','当前服务尚未加载新版添加项目功能，请重启工作台服务后刷新页面。');
  const list=projectField('list');list.replaceChildren();
  body.items.forEach(project=>{
    const row=document.createElement('article');
    const name=document.createElement('strong');name.textContent=project.name+(project.id===body.default_project?'（默认）':'');
    const detail=document.createElement('p');detail.textContent=project.root_path+' · '+(project.eval_command.length?'已配置检查命令，执行前仍需检查环境':'可调研 · 执行前待配置质量检查');
    const button=document.createElement('button');button.type='button';button.className='text-button';button.textContent='配置项目';button.disabled=projectPending || !projectRegistrationAvailable;
    button.onclick=()=>editRegisteredProject(project);row.append(name,detail,button);list.append(row);
  });
}
function projectRegistrationPayload() {
  const raw=projectField('eval').value.trim();
  let command=[];
  if(raw){try{command=JSON.parse(raw);}catch(_){throw new Error('质量检查命令不是有效的 JSON 数组；也可以先留空。');}}
  if(!Array.isArray(command) || command.some(p=>typeof p!=='string'||!p.trim()))throw new Error('质量检查命令须为字符串参数数组。');
  return {actor:actorName(),name:projectField('name').value.trim(),source_type:projectField('source').value,
    root_path:projectField('root').value.trim(),git_url:projectField('url').value.trim(),
    initialize_git:projectField('init').checked,make_default:projectField('default').checked,eval_command:command};
}
projectField('source').onchange=projectSourceView;
projectField('new').onclick=()=>editRegisteredProject();
document.getElementById('open-project-registration').onclick=()=>{
  workspaceView('decision');projectField('registration').open=true;
  projectField('registration').scrollIntoView({behavior:'smooth'});loadInitiativeProjects();
};
projectField('register').onclick=async function() {
  if(projectPending || !projectRegistrationAvailable)return;
  let payload;
  try{payload=projectRegistrationPayload();if(!payload.actor)throw new Error('请先填写上方的操作署名。');}
  catch(error){show('project-status',error.message);return;}
  projectPending=true;this.disabled=true;
  ['source','name','root','url','eval','init','default'].forEach(id=>projectField(id).disabled=true);
  show('project-status',projectEditing?'正在保存配置…':payload.source_type==='git'?'正在克隆并添加项目，请勿重复提交；较大仓库可能需要两分钟。':'正在检查本地目录并添加项目…');
  try {
    const endpoint=projectEditing ? '/api/v1/projects/'+encodeURIComponent(projectEditing.id)+'/settings' : '/api/v1/projects';
    const project=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    await loadInitiativeProjects();if(!currentInitiative)initiativeInput('project').value=project.id;
    show('project-status','已保存项目：'+project.name+' · '+project.root_path+(project.eval_command.length?'':'。可以开始调研；代码交付前请配置质量检查命令。'));
    if(typeof refreshProjectHome==='function')refreshProjectHome();
  } catch(error){show('project-status','添加或配置失败：'+error.message+'。若连接中断，请先刷新项目列表核对结果。');}
  finally{projectPending=false;this.disabled=!projectRegistrationAvailable;
    ['source','name','root','url','eval','init','default'].forEach(id=>projectField(id).disabled=!!projectEditing && ['source','name','root'].includes(id));
    projectField('list').querySelectorAll('button').forEach(b=>b.disabled=!projectRegistrationAvailable);}
};
