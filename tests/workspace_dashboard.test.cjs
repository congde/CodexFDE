const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup() {
  const nodes = new Map();
  function element(tag) {
    return {tag, hidden:false, disabled:false, value:'', textContent:'', dataset:{}, children:[],
      classList:{toggle() {}}, setAttribute() {}, removeAttribute() {},
      showModal() {this.hidden=false;}, close() {this.hidden=true;},
      append(...children) {this.children.push(...children);},
      appendChild(child) {this.children.push(child);}, replaceChildren(...children) {this.children=children;},
      querySelectorAll(selector) {return this.children.filter(child=>child.tag===selector);}};
  }
  const document = {createElement:element, getElementById(id) {
    if(!nodes.has(id)) nodes.set(id,element('div'));return nodes.get(id);
  }, querySelectorAll() {return [];}, querySelector(selector) {return this.getElementById(selector);}};
  const intervals=[];
  const context=vm.createContext({document,Date,URLSearchParams,setTimeout,clearTimeout,
    setInterval:callback=>{intervals.push(callback);return intervals.length;}});
  for(const name of ['workspace-dashboard.js','initiative_workflow.js'])
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../workbench_web',name),'utf8'),context);
  return {context,node:id=>document.getElementById(id),document,intervals};
}
function workflow(stage) {
  return {id:'I1',stage,enabled:true,messages:[],iterations:[],documents:[],progress:[],
    proposal:{goal:'goal',acceptance:[],non_goals:[],sources:[],write_scope:[],steps:[],questions:['Which scope?']}};
}

test('Eval panel distinguishes warning, stale evidence, running and failed attempts',()=>{
  const {context,node}=setup();
  const work=workflow('review');
  work.task={id:'T1',status:'review',events:[]};
  work.eval_harness={available:true,can_run:true,freshness:'current',
    summary:{decision:'pass',passed:1,total:2,blocking_failed:0,observing_failed:1},
    results:[{name:'help',level:'observing',passed:false,duration_ms:3,evidence:'<script>not executable</script>'}]};
  context.renderInitiativeWork(work);
  assert.match(node('iw-eval-summary').textContent,/观察告警 1/);
  assert.match(node('iw-eval-results').children[0].children[0].textContent,/观察.*3 ms/);
  assert.equal(node('iw-eval-results').children[0].children[1].textContent,'<script>not executable</script>');
  work.eval_harness.freshness='stale';
  context.renderInitiativeWork(work);
  assert.equal(node('iw-accept').disabled,true);
  assert.match(node('iw-eval-source').textContent,/旧结论不可用于验收/);
  work.stage='checking';
  context.renderInitiativeWork(work);
  assert.equal(node('iw-eval-run').disabled,true);
  assert.equal(node('iw-accept').hidden,true);
  assert.equal(node('iw-pane-result').hidden,false);
  work.stage='failed';work.eval_harness={available:false,error:'invalid report'};
  context.renderInitiativeWork(work);
  assert.match(node('iw-eval-summary').textContent,/未形成可信报告/);
  assert.equal(node('iw-eval-results').children.length,0);
});
test('failed, interrupted and unreadable work never appears as an outcome',()=>{
  const {context}=setup();
  for(const stage of ['failed','interrupted','cancelled','rework','unavailable'])
    assert.notEqual(context.deliveryStageView(stage).group,'outcome');
  assert.equal(context.deliveryStageView('unavailable').step,-1);
  assert.equal(context.deliveryStageView('review').pane,'result');
  assert.equal(context.deliveryStageView('confirmed').pane,'plan');
});
test('research allows a draft while preventing another submission and hiding stale plans',()=>{
  const {context,node}=setup();
  node('iw-message').value='unsent feedback';
  context.renderInitiativeWork(workflow('researching'));
  assert.equal(node('iw-discuss').disabled,true);
  assert.equal(node('iw-message').disabled,false);
  assert.equal(node('iw-message').value,'unsent feedback');
  assert.equal(node('iw-cancel').hidden,false);
  assert.equal(node('iw-proposal').hidden,true);
  assert.match(node('iw-discuss').textContent,/调研/);
});
test('polling preserves a chosen pane and stage changes lead to the new decision',()=>{
  const {context,node}=setup();
  context.renderInitiativeWork(workflow('clarifying'));
  context.selectIwPane('result');
  context.renderInitiativeWork(workflow('clarifying'));
  assert.equal(node('iw-pane-result').hidden,false);
  context.renderInitiativeWork(workflow('ready'));
  assert.equal(node('iw-pane-plan').hidden,false);
});
test('home excludes course simulations until explicitly included',()=>{
  const {context,node}=setup();
  context.show=(id,value)=>{node(id).textContent=value;};
  node('home-project').value='all';
  vm.runInContext(`homeRows=[
    {item:{id:'real',title:'真实需求'},work:{},view:deliveryStageView('clarifying')},
    {item:{id:'mock',title:'[Mock课程演示] 已完成'},work:{},view:deliveryStageView('observed')}
  ];`,context);
  context.renderProjectHome();
  assert.equal(node('home-attention').textContent,1);
  assert.equal(node('home-outcome').textContent,0);
  node('home-include-mock').checked=true;
  context.renderProjectHome();
  assert.equal(node('home-outcome').textContent,1);
  assert.match(node('home-status').textContent,/含演示/);
});

test('cleared cards stay hidden until requested and running cards cannot be cleared',()=>{
  const {context,node}=setup();
  context.show=(id,value)=>{node(id).textContent=value;};
  node('home-project').value='all';
  vm.runInContext(`homeRows=[
    {item:{id:'old',title:'old',home_hidden:1},work:{},view:deliveryStageView('failed')},
    {item:{id:'active',title:'active'},work:{},view:deliveryStageView('executing')}
  ];homeFilter='all';`,context);
  context.renderProjectHome();
  assert.equal(node('home-items').children.length,1);
  assert.equal(node('home-items').children[0].children[3].children[2].disabled,true);
  node('home-include-cleared').checked=true;
  context.renderProjectHome();
  assert.equal(node('home-items').children.length,2);
  assert.equal(node('home-items').children[0].children[3].children[2].textContent,'恢复到首页');
});

test('old service and request failures explain the problem beside the clicked card',async()=>{
  const {context}=setup();
  let calls=0;
  context.api=async()=>{calls++;throw new Error('network unavailable');};
  context.actorName=()=> 'tester';
  const notice={textContent:''},button={disabled:false};
  await context.setHomeCleared({item:{id:'old'}},button,notice);
  assert.equal(calls,0);
  assert.match(notice.textContent,/重启 8001/);
  await context.setHomeCleared({item:{id:'new',home_hidden:0,version:1}},button,notice);
  assert.equal(calls,1);
  assert.match(notice.textContent,/network unavailable/);
  assert.equal(button.disabled,false);
  assert.match(vm.runInContext("homeClearNotices.get('new')",context),/network unavailable/);
});

test('candidate inspection uses a top-level link without a blocked cross-origin frame',async()=>{
  const {context,node}=setup();
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../workbench_web/platform.js'),'utf8'),context);
  context.crypto=require('node:crypto').webcrypto;context.initPlatform=()=>{};
  context.actorName=()=> 'automated test';
  const calls=[],plan={task_id:'TASK-TEST',plan_id:'PLAN-TEST',configuration_revision:1,expected_candidate_sha256:'sha-test',legacy:false};
  context.api=async(url,options)=>{calls.push({url,method:options?.method || 'GET',body:options?.body?JSON.parse(options.body):null});return {...plan,url:'http://127.0.0.1:51053/',notice:'独立测试数据'};};
  const work=workflow('review');
  work.task={id:'TASK-TEST',status:'review',events:[]};
  context.renderInitiativeWork(work);
  context.initInitiativeWork();
  await node('iw-preview').onclick();
  assert.equal(calls.length,1);assert.equal(calls[0].method,'GET');assert.equal(node('iw-preview-link').hidden,true);
  await context.startPlatformPreview();assert.equal(calls.length,2);assert.equal(calls[1].body.plan_id,'PLAN-TEST');assert.equal(calls[1].body.confirmed,true);
  assert.equal(node('iw-preview-frame').hidden,true);
  assert.equal(node('iw-preview-link').hidden,false);
  assert.equal(node('iw-preview-link').href,'http://127.0.0.1:51053/');
  assert.match(node('iw-preview-status').textContent,/独立窗口/);
});

function summary(ids, extra={}) {
  return {items:ids.map(id=>({item:{id,title:id,project_id:'P1',home_hidden:0,version:1},
    work:{stage:'clarifying',project:{name:'甲项目'},proposal:{goal:'保存目标'}}})),
    counts:{attention:121,running:3,outcome:2,unknown:0},total:121,matching_total:126,
    next_cursor:null,projects:[{id:'P1',name:'甲项目'},{id:'P2',name:'乙项目'}],
    outcomes:{released:2,observed:1},has_mock:true,...extra};
}

test('home fetches one summary endpoint with server filters and renders full match counts',async()=>{
  const {context,node}=setup();
  context.show=(id,value)=>{node(id).textContent=value;};
  node('home-project').value='P1';node('home-search').value='中文%需求';
  node('home-include-cleared').checked=true;
  const urls=[];
  context.api=async url=>{urls.push(url);return summary(['new']);};
  await context.refreshProjectHome(true);
  assert.equal(urls.length,1);
  const params=new URLSearchParams(urls[0].split('?')[1]);
  assert.equal(urls[0].split('?')[0],'/api/v1/initiatives/home');
  assert.equal(params.get('project_id'),'P1');assert.equal(params.get('group'),'attention');
  assert.equal(params.get('q'),'中文%需求');assert.equal(params.get('include_hidden'),'true');
  assert.equal(params.get('limit'),'20');
  assert.equal(node('home-attention').textContent,121);
  assert.equal(node('home-running').textContent,3);
  assert.match(node('home-page-status').textContent,/1 \/ 121/);
  assert.equal(node('home-load-more').hidden,true);
});

test('load more appends a page and polling preserves expansion without per-item reads',async()=>{
  const {context,node}=setup();context.show=(id,value)=>{node(id).textContent=value;};
  node('home-project').value='all';
  const urls=[];
  context.api=async url=>{
    urls.push(url);
    return url.includes('cursor=') ? summary(['new','older']) : summary(['new'],{next_cursor:'cursor-1'});
  };
  await context.refreshProjectHome(true);
  assert.equal(node('home-load-more').hidden,false);
  await context.loadMoreProjectHome();
  assert.equal(node('home-items').children.length,2);
  assert.equal(node('home-attention').textContent,121);
  assert.equal(node('home-load-more').hidden,true);
  assert.match(urls[1],/cursor=cursor-1/);
  await context.refreshProjectHome();
  assert.equal(node('home-items').children.length,2);
  assert.equal(urls.length,4);
  assert.equal(urls.every(url=>url.startsWith('/api/v1/initiatives/home?')),true);
  await context.refreshProjectHome(true);
  assert.equal(node('home-items').children.length,1);
  assert.equal(vm.runInContext('homePages',context),1);
});

test('late responses and errors cannot replace a newer filter result',async()=>{
  const {context,node}=setup();context.show=(id,value)=>{node(id).textContent=value;};
  node('home-project').value='P1';
  const pending=[];
  context.api=url=>new Promise((resolve,reject)=>pending.push({url,resolve,reject}));
  const old=context.refreshProjectHome(true);
  node('home-project').value='P2';
  const current=context.refreshProjectHome(true);
  pending[1].resolve(summary(['current'],{counts:{attention:2,running:0,outcome:0,unknown:0}}));
  await current;
  pending[0].reject(new Error('old failure'));
  await old;
  assert.equal(node('home-attention').textContent,2);
  assert.equal(vm.runInContext('homeRows[0].item.id',context),'current');
  assert.doesNotMatch(node('home-status').textContent,/old failure/);
});

test('home polling pauses when document or overview is hidden',async()=>{
  const {context,node,document,intervals}=setup();
  let refreshes=0;context.refreshProjectHome=async()=>{refreshes++;};
  context.initProjectHome();
  assert.equal(refreshes,1);assert.equal(intervals.length,1);
  document.hidden=true;intervals[0]();assert.equal(refreshes,1);
  document.hidden=false;node('view-overview').hidden=true;intervals[0]();assert.equal(refreshes,1);
  node('view-overview').hidden=false;intervals[0]();assert.equal(refreshes,2);
});
