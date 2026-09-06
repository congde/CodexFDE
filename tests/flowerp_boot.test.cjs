const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
const bootSource=source.slice(source.indexOf('async function boot()'),source.indexOf('$("#login-form").addEventListener'));
async function run({token='',required=false,answers=[]}) {
  const views=[],calls=[];
  const state={token,user:null};
  const context={state,location:{protocol:'http:',hash:''},
    api:async path=>{calls.push(path);if(path.endsWith('/status'))return {initialized:true,authentication_required:required};
      const answer=answers.shift();if(answer instanceof Error)throw answer;return answer;},
    clearSession:()=>{state.token='';state.user=null;},showAuth:x=>views.push(x),showApp:()=>views.push('app'),
    bindUser:()=>{},navigate:()=>{},$:()=>({textContent:''})};
  vm.createContext(context);vm.runInContext(bootSource,context);await context.boot();
  return {views,calls,state};
}
const expired=()=>Object.assign(new Error('会话无效'),{status:401});
test('stale cookie recovers in explicitly local mode',async()=>{
  const result=await run({answers:[expired(),{username:'system'}]});
  assert.deepEqual(result.views,['app']);assert.equal(result.calls.length,3);
});
test('stale bearer and stale cookie both clear before local recovery',async()=>{
  const result=await run({token:'old',answers:[expired(),expired(),{username:'system'}]});
  assert.deepEqual(result.views,['app']);assert.equal(result.calls.length,4);
});
test('authentication-required installation stays at login',async()=>{
  const result=await run({required:true,answers:[expired()]});
  assert.deepEqual(result.views,['login']);assert.equal(result.calls.length,2);
});
test('valid cookie resumes authenticated installation without a bearer',async()=>{
  const result=await run({required:true,answers:[{username:'learner'}]});
  assert.deepEqual(result.views,['app']);
});
