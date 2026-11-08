// 실제 키나 네트워크 없이 지도 수명 주기와 중심 이동을 검증합니다.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, '../frontend/map.js'), 'utf8');
function setup(ready) {
  const calls = [], text = {}, button = {}, overlay = {querySelector:s=>s==='p'?text:button};
  const canvas = {}, panel = {isConnected:true, querySelector:s=>s==='.map-status'?overlay:canvas};
  const maps = {
    // 실제 지도 없이 반경 원의 생성·갱신·제거와 경계 사용을 확인합니다.
    Circle: class {
      constructor(options) { Object.assign(this,options); calls.push(['circle',this]); }
      setMap(value) { this.map=value; }
      setPosition(value) { this.center=value; }
      setRadius(value) { this.radius=value; }
      getBounds() { return {circle:this}; }
    },
    // 커스텀 마커의 연결·해제와 범위 이동을 기록합니다.
    CustomOverlay: class {
      constructor(options) { Object.assign(this,options); calls.push(['pin',this]); }
      setMap(map) { this.map=map; }
      setZIndex(value) { this.zIndex=value; }
    },
    LatLngBounds: class { constructor() { this.points=[]; } extend(point) { this.points.push(point); } },
    LatLng: class { constructor(lat,lng) { this.lat=lat; this.lng=lng; } },
    Map: class {
      constructor(node,options) { this.center=options.center; calls.push(['create',node,this.center]); }
      getCenter() { return this.center; }
      setCenter(center) { this.center=center; calls.push(['center',center]); }
      relayout() { calls.push(['resize']); }
      panTo(center) { this.center=center; calls.push(['pan',center]); }
      setBounds(bounds) { calls.push(['bounds',bounds]); }
      setLevel(level) { calls.push(['level',level]); }
    },
  };
  let resize;
  const window = {kakao:{maps},lunchRunMapReady:ready??Promise.resolve(maps),loadLunchRunMapSdk:async()=>maps};
  const document = {createElement:()=>({classList:{toggle(){}},setAttribute(){}})};
  vm.runInNewContext(source,{window,document,ResizeObserver:class {
    constructor(callback) { resize=callback; }
    observe() {} disconnect() { calls.push(['disconnect']); }
  }});
  return {api:window.LunchRunMap,panel,calls,text,button,overlay,resize:()=>resize()};
}
test('같은 화면에서는 지도를 재사용하고 새 위치와 크기 변경을 반영함',async()=>{
  const s=setup(), location={lat:37.48,lng:126.88};
  await s.api.mount(s.panel,location);
  await s.api.mount(s.panel,location);
  await s.api.mount(s.panel,{lat:37.5,lng:127});
  s.resize();
  assert.equal(s.calls.filter(c=>c[0]==='create').length,1);
  assert.equal(s.calls.find(c=>c[0]==='create')[2].lat,37.48);
  assert.equal(s.calls.filter(c=>c[0]==='center').at(-1)[1].lng,127);
  assert.equal(s.overlay.hidden,true);
});
test('현재 목록의 정상 좌표만 표시하고 클릭·페이지 전환·정리를 연결함',async()=>{
  const s=setup(), location={lat:37,lng:127};
  let chosen;
  const items=[{id:'a',name:'<식당>',lat:37.01,lng:127.01},{id:'bad',name:'누락',lat:null,lng:127}];
  await s.api.mount(s.panel,location,false,{items,onSelect:id=>{chosen=id;}});
  const pins=s.calls.filter(c=>c[0]==='pin').map(c=>c[1]);
  assert.equal(pins.length,2);
  pins[1].content.onclick(); assert.equal(chosen,'a');
  assert.equal(s.calls.find(c=>c[0]==='bounds')[1].circle.radius,500);
  await s.api.mount(s.panel,location,false,{items:[{id:'b',name:'다음',lat:38,lng:128}]});
  assert.ok(pins.every(p=>p.map===null));
  s.api.unmount();
  assert.ok(s.calls.filter(c=>c[0]==='pin').every(c=>c[1].map===null && c[1].content.onclick===null));
});
test('추첨 중에는 중심 이동과 직접 선택을 막고 종료 시 최종 식당으로 이동함',async()=>{
  const s=setup(), location={lat:37,lng:127}, items=[{id:'a',name:'식당',lat:37.01,lng:127.01}];
  let clicks=0;
  await s.api.mount(s.panel,location,false,{items,selected:'a',spinning:true,onSelect:()=>clicks++});
  const pin=s.calls.filter(c=>c[0]==='pin')[1][1];
  pin.content.onclick(); assert.equal(clicks,0);
  assert.equal(s.calls.filter(c=>c[0]==='pan'||c[0]==='bounds').length,0);
  await s.api.mount(s.panel,location,false,{items,selected:'a',spinning:false});
  assert.equal(s.calls.filter(c=>c[0]==='pan').length,1);
  assert.equal(pin.content.textContent,'1. 식당');
});
test('SDK 실패 안내 후 재시도로 지도를 생성함',async()=>{
  const s=setup(Promise.resolve(null));
  await s.api.mount(s.panel,{lat:37,lng:127});
  assert.equal(s.button.hidden,false);
  assert.match(s.text.textContent,/JavaScript 키/);
  await s.api.mount(s.panel,{lat:37,lng:127},true);
  assert.equal(s.overlay.hidden,true);
});

test('반경 원은 점선으로 생성되고 위치·반경을 갱신하며 화면 종료 시 제거됨',async()=>{
  const s=setup();
  await s.api.mount(s.panel,{lat:37,lng:127},false,{radiusM:50});
  const circle=s.calls.find(c=>c[0]==='circle')[1];
  assert.equal(circle.radius,50); assert.equal(circle.strokeStyle,'dash');
  assert.equal(s.calls.filter(c=>c[0]==='bounds').length,1);
  await s.api.mount(s.panel,{lat:37.1,lng:127.1},false,{radiusM:500});
  assert.equal(circle.radius,500); assert.equal(circle.center.lat,37.1);
  assert.equal(s.calls.filter(c=>c[0]==='circle').length,1);
  assert.equal(s.calls.filter(c=>c[0]==='bounds').length,2);
  s.api.unmount(); assert.equal(circle.map,null);
});
test('화면을 떠난 뒤 늦게 준비된 SDK로 지도를 만들지 않음',async()=>{
  let resolve;
  const s=setup(new Promise(r=>{resolve=r;}));
  const mounting=s.api.mount(s.panel,{lat:37,lng:127});
  s.api.unmount(); resolve({}); await mounting;
  assert.equal(s.calls.filter(c=>c[0]==='create').length,0);
});
