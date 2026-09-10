const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function setup() {
  const nodes=new Map();
  const element=tag=>({tag,dataset:{},value:'',children:[],hidden:false,disabled:false,
    append(...children){this.children.push(...children);},replaceChildren(){this.children=[];},
    querySelectorAll(tag){return this.children.filter(el=>el.tag===tag);}});
  const document={createElement:element,getElementById(id){if(!nodes.has(id))nodes.set(id,element('div'));return nodes.get(id);}};
  const context=vm.createContext({document,console});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../workbench_web/initiative_workflow.js'),'utf8'),context);
  document.getElementById('iw-message');
  const render=(questions,id='one',stage='clarifying')=>context.renderIwAnswers({id,stage,enabled:true,proposal:{questions}},false);
  return {context,nodes,render};
}

test('polling preserves individual answers and combines them with extra instructions',()=>{
  const {context,nodes,render}=setup();
  render(['本期范围？','验收条件？']);
  const answers=nodes.get('iw-answers').querySelectorAll('textarea');
  answers[0].value='只改库存';answers[1].value='空结果也能导出';
  nodes.get('iw-message').value='保留失败记录';
  render(['本期范围？','验收条件？']);
  assert.equal(nodes.get('iw-answers').querySelectorAll('textarea')[0],answers[0]);
  assert.equal(context.iwDiscussionText('重新核对项目'),'问题：本期范围？\n回答：只改库存\n\n问题：验收条件？\n回答：空结果也能导出\n\n保留失败记录\n\n重新核对项目');
  render(['本期范围？'],'another');
  assert.equal(nodes.get('iw-answers').querySelectorAll('textarea')[0].value,'');
});

test('personnel deferral is offered only for personnel questions, not mixed product decisions',()=>{
  const {nodes,render}=setup();
  render(['独立复验者和最终人工验收负责人分别由谁担任？']);
  assert.equal(nodes.get('iw-defer-people').hidden,false);
  render(['独立复验者由谁担任？','库存不足时是否允许出库？']);
  assert.equal(nodes.get('iw-defer-people').hidden,true);
  render([],'one','ready');
  assert.equal(nodes.get('iw-defer-people').hidden,true);
});
