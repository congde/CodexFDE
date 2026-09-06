const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
const slice=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to));
function setup() {
  const nodes=new Map(), calls=[], bindings=[];
  function node(key) {
    if(!nodes.has(key))nodes.set(key,{innerHTML:'',textContent:'',value:'',dataset:{},setAttribute(){},classList:{add(){},toggle(){}}});
    return nodes.get(key);
  }
  const tabs=node('tabs');tabs.dataset.tabs='inventory';
  const buttons=['balances','ledger','counts','serials','reorder'].map(tab=>({...node('button-'+tab),dataset:{tab}}));
  const panels=buttons.map(b=>({...node('panel-'+b.dataset.tab),dataset:{tabPanel:b.dataset.tab}}));
  const ctx={state:{inventory:[],inventoryKeyword:'',inventoryStatus:'idle',inventoryRequest:0},statusNames:{},
    $:node,$$:s=>s==='[data-tabs]'?[tabs]:s==='button'?buttons:panels,
    api:async path=>{calls.push(path);return ctx.answer(path);},answer:()=>({items:[]}),
    setLoading(){},toast(){},bindActions:n=>bindings.push(n)};
  vm.createContext(ctx);
  vm.runInContext(slice('function esc(', 'function randomKey(')+slice('async function loadBase()', 'function commandItems()')+slice('function setInventoryStatus(', 'async function renderFinance()')+slice('$$("[data-tabs]")', '$("#close-period")'),ctx);
  return {ctx,node,calls,bindings,buttons,input(value){node('#inventory-keyword').value=value;node('#inventory-keyword').oninput();},clear(){node('#inventory-clear').onclick();},html:()=>node('#inventory-table').innerHTML,count:()=>node('#inventory-filter-status').textContent};
}
const rows=[{sku:'AbC-01',product_name:'红色茶杯',site_code:'WH-East',location_code:'Rack-A',on_hand:8,reserved:2,available:6,min_stock:1},{sku:'XYZ-02',product_name:'蓝色碗',site_code:'WH-West',location_code:'Bin-B',on_hand:4,reserved:0,available:4,min_stock:2}];
async function load(h,items=rows){h.ctx.answer=path=>({items:path.endsWith('/balances')?items:[]});await h.ctx.loadPage('inventory');}
test('four independent substring fields, whitespace, case, counts, clear and no extra requests',async()=>{
  const h=setup(),original=JSON.stringify(rows);await load(h);const requests=h.calls.length;
  for(const keyword of ['  aBc-0  ','红色','wh-eA','rACK-a']){h.input(keyword);assert.equal(h.count(),'当前显示 1 / 已加载 2 条');assert.match(h.html(),/红色茶杯/);assert.doesNotMatch(h.html(),/蓝色碗/);}
  h.input('01红色');assert.match(h.html(),/没有匹配/);
  h.input('missing');assert.equal(h.count(),'当前显示 0 / 已加载 2 条');assert.match(h.html(),/点击清空/);
  h.clear();assert.equal(h.count(),'当前显示 2 / 已加载 2 条');assert.equal(h.node('#inventory-keyword').value,'');
  h.input(' \t ');assert.equal(h.count(),'当前显示 2 / 已加载 2 条');assert.ok(h.html().indexOf('AbC-01')<h.html().indexOf('XYZ-02'));
  assert.equal(JSON.stringify(rows),original);assert.equal(h.calls.length,requests);
});
test('empty data differs from no match; special characters are literal and escaped',async()=>{
  const h=setup();await load(h,[]);h.input('missing');assert.equal(h.count(),'当前显示 0 / 已加载 0 条');assert.match(h.html(),/暂无库存余额/);assert.doesNotMatch(h.html(),/没有匹配/);
  const evil='<img src=x onerror="alert(1)">&\'';
  await load(h,[{...rows[0],sku:evil,product_name:evil,site_code:evil,location_code:evil}]);h.clear();assert.doesNotMatch(h.html(),/<img/);assert.equal((h.html().match(/&lt;img/g)||[]).length,4);
  h.input(evil);assert.equal(h.count(),'当前显示 1 / 已加载 1 条');h.input('.*');assert.equal(h.count(),'当前显示 0 / 已加载 1 条');h.input('[');assert.match(h.html(),/没有匹配/);
});
test('other tab content and action bindings survive filtering and tab switches',async()=>{
  const h=setup();await load(h);const ids=['ledger','counts','serials','reorder'];
  const before=ids.map(id=>h.node('#'+id+'-table').innerHTML),bindings=h.bindings.slice();h.input('abc');
  for(const button of h.buttons)button.onclick();h.buttons[0].onclick();
  assert.deepEqual(ids.map(id=>h.node('#'+id+'-table').innerHTML),before);assert.deepEqual(h.bindings,bindings);assert.equal(bindings.length,1);assert.equal(h.count(),'当前显示 1 / 已加载 2 条');assert.equal(h.node('#inventory-keyword').value,'abc');
});
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
for(const failure of ['/products?','/customers','/suppliers','/sites','/inventory/balances','/inventory/ledger','/reports/reorder','/inventory/counts','/inventory/serials'])test('loading clears old balances and failure recovers: '+failure,async()=>{
  const h=setup();await load(h);const gate=deferred();h.ctx.answer=path=>path.includes(failure)?gate.promise:{items:[]};
  const pending=h.ctx.loadPage('inventory');assert.match(h.count(),/正在加载/);assert.doesNotMatch(h.html(),/红色茶杯/);h.clear();assert.doesNotMatch(h.html(),/红色茶杯/);
  await new Promise(setImmediate);gate.reject(new Error('test request failure'));await pending;
  assert.match(h.count(),/加载失败/);assert.equal(h.ctx.state.inventory.length,0);h.input('abc');h.clear();assert.match(h.html(),/加载失败/);await load(h);assert.equal(h.count(),'当前显示 2 / 已加载 2 条');
});
for(const stage of ['base','inventory'])for(const outcome of ['success','failure'])test('older '+stage+' '+outcome+' cannot overwrite latest load',async()=>{
  const h=setup(),gate=deferred();h.ctx.answer=path=>path.includes(stage==='base'?'/products?':'/inventory/balances')?gate.promise:{items:[]};
  const old=h.ctx.loadPage('inventory');await new Promise(setImmediate);await load(h,[rows[1]]);
  if(outcome==='success')gate.resolve({items:[rows[0]]});else gate.reject(new Error('stale failure'));
  await old;assert.equal(h.count(),'当前显示 1 / 已加载 1 条');assert.match(h.html(),/蓝色碗/);assert.doesNotMatch(h.html(),/红色茶杯/);
});
test('older success cannot revive latest failed state',async()=>{
  const h=setup(),gate=deferred();h.ctx.answer=path=>path.endsWith('/balances')?gate.promise:{items:[]};const old=h.ctx.loadPage('inventory');await new Promise(setImmediate);
  h.ctx.answer=async()=>{throw new Error('latest failure');};await h.ctx.loadPage('inventory');gate.resolve({items:rows});await old;h.clear();assert.match(h.html(),/加载失败/);assert.equal(h.ctx.state.inventory.length,0);
});
