// 지도 생성 코드에서는 이 Promise를 기다린 뒤 kakao.maps를 사용합니다.
// 준비되면 maps 객체, 키가 없거나 로딩에 실패하면 null을 반환해 기존 데모 화면을 유지합니다.
// 성공한 SDK는 재사용하고 진행 중인 요청은 공유하여 중복 다운로드를 막습니다.
let lunchRunSdkPending = null;
let lunchRunSdkMaps = null;
window.loadLunchRunMapSdk = function () {
  if (lunchRunSdkMaps) return Promise.resolve(lunchRunSdkMaps);
  if (lunchRunSdkPending) return lunchRunSdkPending;
  lunchRunSdkPending = loadKakaoMapsSdk().then(maps => {
    lunchRunSdkMaps = maps;
    lunchRunSdkPending = null;
    return maps;
  });
  return lunchRunSdkPending;
};
async function loadKakaoMapsSdk() {
  try {
    // .env 자체는 접근하지 않습니다. 서버가 허용한 공개 JavaScript 키만 가져옵니다.
    const response = await fetch('/api/public-config', {
      signal: AbortSignal.timeout(10000),
      cache: 'no-store',
    });
    if (!response.ok) throw new Error('공개 지도 설정을 가져오지 못했습니다.');
    const config = await response.json();
    const key = config.kakaoJavascriptKey;

    // 키를 아직 입력하지 않아도 앱은 정상 실행됩니다. REST API 키를 대신 넣으면 안 됩니다.
    if (typeof key !== 'string' || !key.trim()) return null;

    return await new Promise(resolve => {
      // SDK 장애가 앱 전체를 멈추지 않도록 제한시간과 오류 처리를 둡니다.
      let finished = false;
      let timer;
      const finish = maps => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        // 실패한 스크립트를 제거한 뒤 재시도에서 새 요청을 보냅니다.
        if (!maps) script.remove();
        resolve(maps);
      };
      const script = document.createElement('script');
      // SDK의 자동 초기화를 끄고, 다운로드 완료 후 maps.load로 사용 준비를 확인합니다.
      script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(key.trim())}&autoload=false`;
      script.async = true;
      script.onload = () => {
        if (finished) return;
        if (typeof window.kakao?.maps?.load !== 'function') {
          console.warn('지도 SDK를 사용할 수 없습니다. JavaScript 키와 등록 도메인을 확인하세요.');
          finish(null);
          return;
        }
        // SDK 내부 초기화가 실패해도 Promise가 영원히 대기하지 않게 처리합니다.
        try { window.kakao.maps.load(() => finish(window.kakao.maps)); }
        catch { finish(null); }
      };
      script.onerror = () => {
        console.warn('지도 SDK 다운로드에 실패했습니다. 네트워크와 등록 도메인을 확인하세요.');
        finish(null);
      };
      timer = setTimeout(() => {
        console.warn('지도 SDK 로딩 시간이 초과되었습니다.');
        finish(null);
      }, 10000);
      // SDK 요청에는 공개 JavaScript 키만 포함되며 REST/Groq 키는 사용하지 않습니다.
      document.head.appendChild(script);
    });
  } catch {
    // 비밀값이나 SDK 요청 URL을 로그에 출력하지 않습니다.
    console.warn('지도 SDK 준비에 실패했습니다. 서버 실행 상태를 확인하세요.');
    return null;
  }
}
// 기존 화면 코드에서도 같은 준비 Promise를 기다릴 수 있도록 공개합니다.
window.lunchRunMapReady = window.loadLunchRunMapSdk();
