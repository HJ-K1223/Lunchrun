// 실제 식당의 누락 정보·빈 결과·실패 처리를 가상 DOM에서 검증합니다.
const test=require('node:test');const assert=require('node:assert/strict');
const vm=require('node:vm');const fs=require('node:fs');const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8').replace(/\nrender\(\);\s*$/,'');
function setup(fetch){
  // 화면 함수만 실행하며 브라우저 권한·네트워크·실제 위치를 사용하지 않습니다.
  const context=vm.createContext({clearInterval,clearTimeout,document:{querySelector:()=>({addEventListener(){}}),querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){}},localStorage:{getItem:()=>null},location:{hash:'#game'},AbortSignal,fetch,LunchRunLocation:{validCoordinates:()=>true}});
  vm.runInContext(source,context);vm.runInContext('render=()=>{};toast=()=>{};state.location={lat:37.48,lng:126.88,name:"예시",source:"place"};',context);
  return code=>vm.runInContext(code,context);
}
test('누락 가격을 0원으로, 이동시간을 null분으로 표시하지 않음',()=>{
  const run=setup();const html=run('restaurantCard({id:"test",name:"식당",price:null,walk_minutes:null,distance_m:120,source:"kakao"})');
  assert.ok(html.includes('데이터 없음'));assert.ok(html.includes('120m'));assert.ok(!html.includes('0원'));assert.ok(!html.includes('null분'));assert.ok(!html.includes('가상 식당'));
});
test('외부 식당명과 ID를 HTML에 그대로 삽입하지 않음',()=>{
  const run=setup();const html=run('restaurantCard({id:"\\\" onclick=\\\"alert(1)",name:"<script>test</script>",price:null})');
  assert.ok(!html.includes('<script>'));assert.ok(!html.includes('data-id="" onclick='));
});
test('검증 추천과 확인 필요 후보를 별도 목록으로 표시함',()=>{
  const run=setup();
  run('state.result={menu:{name:"짜장면"},message:"검증 결과",has_more:false,restaurants:[{id:"1",name:"추천식당",menu_verified:true,menu_name:"짜장면",price:8000,price_status:"known",open_status:"open"}],candidate_restaurants:[{id:"2",name:"확인식당",menu_verified:false,price:null,price_status:"missing",open_status:"open",warnings:["메뉴 확인 불가","가격 확인 불가"]}]};');
  const html=run('resultCards()');
  assert.ok(html.includes('추천 식당'));
  assert.ok(html.includes('확인이 필요한 후보'));
  assert.ok(html.includes('8,000원'));
  assert.ok(html.includes('메뉴 확인 불가'));
});
test('실제 식당을 가상 지도 좌표에 배치하지 않음',()=>{
  const run=setup();const html=run('mapGraphic([{id:"test",name:"식당"}])');
  assert.ok(html.includes('id="map-panel"'));assert.ok(!html.includes('map-pin'));
});
test('검색 실패 시 이전 결과가 남지 않음',async()=>{
  const run=setup(async()=>({ok:false,status:502,json:async()=>({detail:'카카오 권한 확인'})}));
  run('state.result={menu:{id:"old"},restaurants:[{id:"old-place"}]};');
  await run('drawMenu()');
  assert.equal(run('state.result.restaurants.length'),0);assert.equal(run('state.result.message'),'카카오 권한 확인');
});

// 반경 입력 위치와 API 요청의 현재 반경을 함께 확인합니다.
test('반경 입력은 위치와 음식 조건 사이에 있으며 현재 반경으로 요청함',()=>{
  const run=setup();
  const html=run('home()');
  assert.ok(html.indexOf('class="location-bar"') < html.indexOf('class="radius-form"'));
  assert.ok(html.indexOf('class="radius-form"') < html.indexOf('id="conditions-form"'));
  assert.ok(html.includes('min="50" max="500" step="1"'));
  run('state.radiusM=51;');
  assert.equal(run('payload().radius_m'),51);
});

// 홈 배치는 유지하면서 다른 화면은 요약과 공통 조건 수정 창을 사용합니다.
test('반경은 조건 요약에 표시하고 수정 창에서만 음식 조건과 함께 입력함',()=>{
  const run=setup();
  run('state.radiusM=123;');
  const summary=run('summaries()');
  assert.ok(summary.includes('반경 123m'));
  assert.ok(!summary.includes('class="radius-form"'));
  assert.ok(run('conditionsForm(true)').includes('name="radius_m"'));
  assert.ok(!run('conditionsForm(false)').includes('name="radius_m"'));
  assert.ok(run('home()').includes('class="radius-form"'));
});

test('채팅 변경안은 확인 후 반경과 음식 조건에 분리 적용하고 이전 결과를 제거함',()=>{
  const run=setup();
  run('state.chatTurn=1;state.result={menu:{id:"old"}};state.messages=[{patch:{radius_m:100,budget:9000},version:state.version,turn:1}];');
  assert.equal(run('state.radiusM'),500);
  assert.equal(run('applyChatPatch(0)'),true);
  assert.equal(run('state.radiusM'),100);
  assert.equal(run('state.conditions.budget'),9000);
  assert.equal(run('"radius_m" in state.conditions'),false);
  assert.equal(run('state.result'),null);
  assert.equal(run('applyChatPatch(0)'),false);
});

test('다른 질문 이후의 조건 변경안은 적용되지 않음',()=>{
  const run=setup();
  run('state.chatTurn=2;state.messages=[{patch:{radius_m:100},version:state.version,turn:1}];');
  assert.equal(run('applyChatPatch(0)'),false);
  assert.equal(run('state.radiusM'),500);
});

test('메뉴 재검색 시작 시 추첨과 이전 변경안을 무효화함',async()=>{
  let resolve;
  const run=setup(()=>new Promise(r=>{resolve=r;}));
  run('state.spinning=true;state.chatTurn=1;state.messages=[{patch:{budget:9000},version:state.version,turn:1}];');
  const request=run('drawMenu()');
  assert.equal(run('state.spinning'),false);
  assert.equal(run('state.messages[0].version < state.version'),true);
  resolve({ok:true,json:async()=>({menu:null,restaurants:[],message:'결과 없음'})});
  await request;
});

test('채팅 실패 뒤에도 이전 추첨은 재개되지 않음',async()=>{
  const run=setup(async()=>({ok:false,status:503,json:async()=>({detail:'잠시 후 재시도'})}));
  run('state.spinning=true;');
  await run('sendChat("추천해줘")');
  assert.equal(run('state.spinning'),false);
  assert.equal(run('state.result'),null);
  assert.match(run('state.messages.at(-1).text'),/재시도/);
});
