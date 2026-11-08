// 화면 컨트롤러를 가상 DOM에서 실행해 응답 순서·취소·좌표 전달을 검증합니다.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'),'utf8').replace(/\nrender\(\);\s*$/, '');
const example = {name:'공개 예시',address:'예시 주소',lat:37.48,lng:126.88,source:'place'};

// 초기 화면 그리기만 생략하고 실제 함수들을 실행합니다. 외부 API와 실제 위치는 사용하지 않습니다.
function harness(service) {
  const nodes = new Map();
  const node = selector => {
    if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',open:true,addEventListener(){},close(){this.open=false;},classList:{add(){},remove(){}}});
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document:{querySelector:node,querySelectorAll:()=>[],addEventListener(){}},
    window:{addEventListener(){}},location:{hash:'#home'},localStorage:{getItem:()=>null},
    AbortController, setTimeout:()=>1,clearTimeout(){},clearInterval(){},
    LunchRunLocation:{validCoordinates:(lat,lng)=>Number.isFinite(lat)&&Number.isFinite(lng)&&Math.abs(lat)<=90&&Math.abs(lng)<=180,...service},
  });
  vm.runInContext(source,context);
  vm.runInContext('render=()=>{}; toast=()=>{};',context);
  return {context,node,run:code=>vm.runInContext(code,context)};
}

test('이전 검색 응답이 새 검색 결과를 덮어쓰지 않음',async()=>{
  const pending=[];
  const h=harness({search:()=>new Promise(resolve=>pending.push(resolve))});
  const first=h.run("searchLocation('첫 검색')");
  const second=h.run("searchLocation('두 번째 검색')");
  pending[1]({items:[{...example,name:'두 번째 결과'}],has_more:false});await second;
  pending[0]({items:[{...example,name:'첫 번째 결과'}],has_more:false});await first;
  assert.equal(h.run('locationSearch.items[0].name'),'두 번째 결과');
});
test('창 닫기 이후 Geolocation 응답 무시',async()=>{
  let resolve;
  const h=harness({current:()=>new Promise(r=>{resolve=r;})});
  const operation=h.run('getCurrentLocation()');
  h.run('cancelLocationRequest()');
  resolve({...example,source:'geolocation'});await operation;
  assert.equal(h.run('state.location'),null);
});
test('주소 변환 장애에도 확보한 좌표 유지 및 요청 전달',async()=>{
  const h=harness({current:async()=>({...example,source:'geolocation',accuracy:25}),reverse:async()=>{throw new Error('offline');}});
  await h.run('getCurrentLocation()');
  const request=h.run('payload()');
  assert.equal(request.location.lat,37.48);assert.equal(request.location.lng,126.88);assert.equal(request.radius_m,500);
  assert.equal(h.run('state.version'),1);
});
test('위치 미설정 시 추천 요청 차단',()=>{
  const h=harness({});assert.throws(()=>h.run('payload()'),/기준 위치/);
});
