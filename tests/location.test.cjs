// 브라우저 위치 권한이나 실제 사용자 위치를 사용하지 않고 위치 어댑터의 경계 상황을 검증합니다.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../frontend/location.js'), 'utf8');

// 실제 브라우저 API 대신 모의 Geolocation을 주입하며 초기화 중 자동 조회 여부도 검사합니다.
function create(success, error, secure = true, supported = true) {
  let calls = 0;
  const window = { isSecureContext: secure };
  const navigator = supported ? { geolocation: { getCurrentPosition(ok, fail, options) {
    calls++; assert.equal(options.timeout, 10000);
    if(error)fail({code:error});else ok({coords:success});
  } } } : {};
  vm.runInNewContext(source, {window,navigator,Number,Promise,Error});
  assert.equal(calls,0);
  return window.LunchRunLocation;
}

test('공개 예시 좌표와 정확도 변환',async()=>{
  const service=create({latitude:37.48,longitude:126.88,accuracy:25});
  const result=await service.current();
  assert.equal(result.lat,37.48);assert.equal(result.lng,126.88);assert.equal(result.accuracy,25);assert.equal(result.source,'geolocation');
});
test('권한 거부·조회 불가·시간 초과 구분',async()=>{
  for(const [code,message] of [[1,/권한/],[2,/확인할 수/],[3,/초과/]])await assert.rejects(create(null,code).current(),message);
});
test('비보안 환경과 미지원 브라우저 안내',async()=>{
  await assert.rejects(create(null,null,false).current(),/HTTPS/);
  await assert.rejects(create(null,null,true,false).current(),/지원하지/);
});
test('잘못된 좌표를 저장하지 않음',async()=>{
  await assert.rejects(create({latitude:126.88,longitude:37.48,accuracy:25}).current(),/올바른/);
});
