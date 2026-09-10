const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function setup(){
  const nodes=new Map();
  const element=tag=>({tag,value:'',textContent:'',checked:false,disabled:false,hidden:false,children:[],
    append(...children){this.children.push(...children);},replaceChildren(){this.children=[];},
    querySelectorAll(tag){return this.children.flatMap(el=>el.tag===tag?[el]:el.querySelectorAll(tag));},
    scrollIntoView(){}});
  const document={createElement:element,getElementById(id){if(!nodes.has(id))nodes.set(id,element('div'));return nodes.get(id);}};
  const calls=[];
  const context=vm.createContext({document,console,encodeURIComponent,actorName:()=> 'fixture-user',
    show:(id,text)=>document.getElementById(id).textContent=text,workspaceView:()=>{},api:async(url,options)=>{
      calls.push({url,body:JSON.parse(options.body)});
      return {id:'PROJECT-NEW',name:'项目',root_path:'D:/projects/new',eval_command:[]};
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../workbench_web/initiatives.js'),'utf8'),context);
  context.loadInitiativeProjects=async()=>{};
  context.editRegisteredProject();
  return {context,document,calls,nodes};
}

test('local folder is submitted without requiring an Eval command',async()=>{
  const {context,document,calls}=setup();
  document.getElementById('project-name').value='客户系统';
  document.getElementById('project-root').value='D:/projects/new';
  document.getElementById('project-init').checked=true;
  document.getElementById('project-default').checked=true;
  await document.getElementById('project-register').onclick();
  assert.equal(calls[0].url,'/api/v1/projects');
  assert.deepEqual(calls[0].body.eval_command,[]);
  assert.equal(calls[0].body.initialize_git,true);
  assert.equal(calls[0].body.make_default,true);
  assert.equal(document.getElementById('init-project').value,'PROJECT-NEW');
  assert.match(document.getElementById('project-status').textContent,/可以开始调研/);
});

test('Git source switches fields, sends URL and target, and invalid config sends no request',async()=>{
  const {context,document,calls}=setup();
  document.getElementById('project-source').value='git';context.projectSourceView();
  assert.equal(document.getElementById('project-url-field').hidden,false);
  assert.equal(document.getElementById('project-init-field').hidden,true);
  document.getElementById('project-url').value='https://example.org/repo.git';
  document.getElementById('project-root').value='D:/projects/new';
  document.getElementById('project-eval').value='not JSON';
  await document.getElementById('project-register').onclick();assert.equal(calls.length,0);
  document.getElementById('project-eval').value='';
  await document.getElementById('project-register').onclick();
  assert.equal(calls[0].body.git_url,'https://example.org/repo.git');
  assert.equal(calls[0].body.source_type,'git');
  assert.equal(calls[0].body.root_path,'D:/projects/new');
});

test('configuring existing project uses settings endpoint and locks its repository',async()=>{
  const {context,document,calls}=setup();
  context.editRegisteredProject({id:'PROJECT-EXISTING',name:'ERP',root_path:'D:/erp',eval_command:[]});
  assert.equal(document.getElementById('project-root').disabled,true);
  document.getElementById('project-eval').value='["D:/erp/.venv/Scripts/python.exe","check.py"]';
  await document.getElementById('project-register').onclick();
  assert.equal(calls[0].url,'/api/v1/projects/PROJECT-EXISTING/settings');
  assert.equal(document.getElementById('project-root').disabled,true);
  assert.equal(document.getElementById('project-eval').disabled,false);
});

test('an older running backend cannot receive unsupported registration requests',async()=>{
  const {context,document,calls}=setup();
  context.renderRegisteredProjects({items:[],default_project:'old'});
  assert.equal(document.getElementById('project-register').disabled,true);
  await document.getElementById('project-register').onclick();
  assert.equal(calls.length,0);
  assert.match(document.getElementById('project-status').textContent,/重启工作台/);
  context.renderRegisteredProjects({items:[],registration:{sources:['local','git']}});
  assert.equal(document.getElementById('project-register').disabled,false);
});
