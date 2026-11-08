// 실제 네트워크 없이 상태 문구·캐시·중복 요청·실패 안내를 검증합니다.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../frontend/groq-status.js'),'utf8');
function setup(fetch){
  const nodes=new Map();
  const box={dataset:{},querySelector:s=>{if(!nodes.has(s))nodes.set(s,{});return nodes.get(s);}};
  const summary={dataset:{busy:'false'},textContent:''};
  const window={};
  vm.runInNewContext(source,{window,fetch,Date,AbortSignal,document:{querySelector:s=>s==='#groq-connection'?box:s==='#ai-connection-summary'?summary:null}});
  return {api:window.LunchRunGroqStatus,box,nodes,summary};
}
test('연결 상태·확인 시각을 표시하고 최근 결과는 재사용함',async()=>{
  let calls=0;
  const s=setup(async()=>{calls++;return {ok:true,json:async()=>({status:'connected',message:'인증 확인',checked_at:new Date().toISOString()})};});
  await s.api.check();await s.api.check();
  assert.equal(calls,1);assert.equal(s.box.dataset.status,'connected');
  assert.equal(s.nodes.get('.groq-status-label').textContent,'Groq 연결 확인됨');
  assert.equal(s.summary.textContent,'AI 연결됨');
  assert.equal(s.summary.dataset.status,'connected');
  assert.match(s.nodes.get('.groq-status-time').textContent,/마지막 확인/);
  await s.api.check(true);assert.equal(calls,2);
});
test('진행 중에는 중복 요청을 막고 오류 시 연결 확인 실패로 표시함',async()=>{
  let reject,calls=0;
  const s=setup(()=>{calls++;return new Promise((_,r)=>{reject=r;});});
  const request=s.api.check();await s.api.check(true);
  assert.equal(calls,1);assert.equal(s.box.dataset.status,'checking');
  reject(new Error());await request;
  assert.equal(s.box.dataset.status,'unknown');
  assert.equal(s.nodes.get('button').disabled,false);
});
test('인증 실패는 연결 안 됨으로 표시함',async()=>{
  const s=setup(async()=>({ok:true,json:async()=>({status:'disconnected',message:'인증 키 확인',checked_at:new Date().toISOString()})}));
  await s.api.check();assert.equal(s.nodes.get('.groq-status-label').textContent,'Groq 연결 안 됨');assert.equal(s.summary.textContent,'AI 미연결');
});
test('응답 생성 중에는 연결 결과보다 작업 상태를 우선 표시함',async()=>{
  const s=setup(async()=>({ok:true,json:async()=>({status:'connected',message:'인증 확인',checked_at:new Date().toISOString()})}));
  s.summary.dataset.busy='true';await s.api.check();
  assert.equal(s.summary.textContent,'AI 응답 생성 중…');assert.equal(s.summary.dataset.status,'checking');
});
