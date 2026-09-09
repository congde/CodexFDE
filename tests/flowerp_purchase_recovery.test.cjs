const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
const slice=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
function setup(){
 const nodes=new Map();const node=k=>{if(!nodes.has(k))nodes.set(k,{innerHTML:'',textContent:'',value:'',setAttribute(){},classList:{add(){},toggle(){}}});return nodes.get(k);};
 const ctx={state:{purchases:[],receipts:[],purchaseRequest:0},$:node,api:async p=>ctx.answer(p),answer:()=>({items:[]}),setLoading(){},toast(){},bindActions(){},esc:String,money:String,status:String,table:(h,rows)=>rows.join(''),empty:(a,b)=>a+' '+b};
 vm.createContext(ctx);vm.runInContext(slice('async function loadBase()','function commandItems()')+slice('function purchaseActions(','function setInventoryStatus('),ctx);
 return {ctx,node};
}
const order={id:'PO-ONE',order_number:'PO-ONE',supplier_name:'供应商',status:'pending_approval'};
const defer=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve};};
test('failed purchase refresh removes stale approval, preserves query, and recovers same order',async()=>{
 const h=setup();h.ctx.answer=p=>({items:p.includes('/purchases/orders')?[order]:[]});
 await h.ctx.loadPage('purchases');assert.match(h.node('#purchases-table').innerHTML,/purchase-approve/);
 h.node('#purchase-search').value='PO-ONE';h.ctx.answer=()=>{throw Error('offline');};
 await h.ctx.loadPage('purchases');assert.match(h.node('#purchases-table').innerHTML,/加载失败/);assert.doesNotMatch(h.node('#purchases-table').innerHTML,/purchase-approve/);assert.equal(h.ctx.state.purchases.length,0);assert.equal(h.node('#purchase-search').value,'PO-ONE');
 h.ctx.answer=p=>({items:p.includes('/purchases/orders')?[order]:[]});await h.ctx.loadPage('purchases');assert.match(h.node('#purchases-table').innerHTML,/PO-ONE/);assert.match(h.node('#purchases-table').innerHTML,/purchase-approve/);
});
test('late successful purchase response cannot overwrite a newer failure',async()=>{
 const h=setup(),d=defer();h.ctx.answer=p=>p.includes('/purchases/orders')?d.promise:{items:[]};
 const old=h.ctx.loadPage('purchases');await new Promise(setImmediate);
 h.ctx.answer=()=>{throw Error('new failure');};await h.ctx.loadPage('purchases');d.resolve({items:[order]});await old;
 assert.match(h.node('#purchases-table').innerHTML,/加载失败/);assert.equal(h.ctx.state.purchases.length,0);
});
