const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const {dom}=require('./web_dom.cjs');

function setup() {
  const ui=dom();
  const context=vm.createContext({document:ui.document,console,setInterval(){},clearInterval(){}});
  vm.runInContext(fs.readFileSync('workbench_web/initiative_workflow.js','utf8'),context);
  const select=ui.node('iw-route-mode');
  for(const mode of ['standard','quick','strict','course']){
    const option=ui.element('option');option.value=mode;select.append(option);
  }
  return {ui,context};
}
function data(overrides={}) {
  return {id:'I1',stage:'ready',enabled:true,source_check:{status:'current'},
    delivery_policy:{mode:'quick',quick_eligible:true,reasons:['局部文件']},...overrides};
}
test('quick route exposes one explicit authorization; course route retains separate confirmations',()=>{
  const {ui,context}=setup();
  context.renderDeliveryDecisions(data());
  assert.equal(ui.node('iw-quick-execute').hidden,false);
  assert.equal(ui.node('iw-confirm-prd').hidden,true);
  assert.equal(ui.node('iw-confirm').hidden,true);
  ui.node('iw-confirm-prd').hidden=false;ui.node('iw-confirm').hidden=false;
  context.renderDeliveryDecisions(data({delivery_policy:{mode:'course',course_bound:true,quick_eligible:false,reasons:[]}}));
  assert.equal(ui.node('iw-quick-execute').hidden,true);
  assert.equal(ui.node('iw-route-mode').disabled,true);
  assert.equal(ui.node('iw-confirm-prd').hidden,false);
  assert.equal(ui.node('iw-confirm').hidden,false);
});
test('expired authorization disables execution and offers renewal only against readable current source',()=>{
  const {ui,context}=setup();
  context.renderDeliveryDecisions(data({stage:'confirmed',authorization_expired:true}));
  assert.equal(ui.node('iw-execute').disabled,true);
  assert.equal(ui.node('iw-renew-panel').hidden,false);
  assert.equal(ui.node('iw-renew').disabled,false);
  context.renderDeliveryDecisions(data({stage:'confirmed',authorization_expired:true,source_check:{status:'unavailable'}}));
  assert.equal(ui.node('iw-renew').disabled,true);
});
test('rework explains distinct destinations and does not offer writing while busy',()=>{
  const {ui,context}=setup();
  ui.node('iw-rework-reason').value='environment';
  context.renderDeliveryDecisions(data({stage:'rework'}));
  assert.equal(ui.node('iw-rework-panel').hidden,false);
  assert.equal(ui.node('iw-open-rework').hidden,false);
  assert.match(ui.node('iw-rework-help').textContent,/只重跑原检查/);
  ui.node('iw-rework-reason').value='technical';context.renderReworkHelp();
  assert.match(ui.node('iw-rework-help').textContent,/沿用原需求确认|未变化的需求确认/);
  context.renderDeliveryDecisions(data({stage:'executing'}));
  assert.equal(ui.node('iw-rework-submit').disabled,true);
  assert.equal(ui.node('iw-rework-panel').hidden,true);
  assert.equal(ui.node('iw-open-rework').hidden,true);
});
test('new delivery actions use stable mutation receipts and keep the original revision',async()=>{
  for(const action of ['delivery-route','rework','renew-authorization','confirm-execute']){
    const {context}=setup();context.initial={...data(),revision:8,messages:[]};
    vm.runInContext("initiativeWorkId='I1';initiativeWork=initial;initiativeWorkReadable=true;",context);
    context.actorName=()=> 'human';context.renderInitiativeWork=()=>{};
    context.platformKey=()=> 'original-request-key';
    let request;
    context.platformPost=async(path,body)=>{request={path,body};return {...context.initial,revision:9};};
    context.api=async()=>({id:'I1'});
    await context.initiativeWorkAction(action,{note:'reviewed evidence',confirmed:true});
    assert.equal(request.body.submission_key,'original-request-key');
    assert.equal(request.body.revision,8);
    assert.equal(request.body.actor,'human');
    assert.equal(request.path,'/api/v1/initiatives/I1/workflow/'+action);
  }
});
