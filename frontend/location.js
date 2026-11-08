// 위치 조회와 서버 통신을 화면 코드에서 분리합니다. 좌표는 영구 저장하지 않습니다.
window.LunchRunLocation = (() => {
  // 오류 원문에는 내부 정보가 있을 수 있어 서버의 사용자 안내 메시지만 사용합니다.
  async function post(path, body, signal) {
    const response = await fetch(`/api/locations/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(12000)]) : AbortSignal.timeout(12000),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : '위치 요청을 처리하지 못했습니다. 입력을 확인해 주세요.');
    }
    return response.json();
  }

  // 좌표 필드가 뒤바뀌거나 NaN/범위 밖 값이 화면 상태에 들어오는 것을 방지합니다.
  function validCoordinates(lat, lng) {
    return Number.isFinite(lat) && Number.isFinite(lng) && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;
  }

  // 이 함수는 버튼 클릭에서만 호출합니다. 페이지 로딩 시에는 권한을 요청하지 않습니다.
  function current() {
    return new Promise((resolve, reject) => {
      if (!window.isSecureContext) { reject(new Error('현재 위치는 HTTPS 또는 localhost/127.0.0.1에서 사용할 수 있어요.')); return; }
      if (!navigator.geolocation) { reject(new Error('이 브라우저는 현재 위치 조회를 지원하지 않아요. 주소·장소 검색을 이용해 주세요.')); return; }
      navigator.geolocation.getCurrentPosition(position => {
        const { latitude: lat, longitude: lng, accuracy } = position.coords;
        if (!validCoordinates(lat, lng)) { reject(new Error('올바른 위치 좌표를 받지 못했어요. 다시 시도해 주세요.')); return; }
        resolve({ name: '현재 위치', address: `위도 ${lat.toFixed(5)} · 경도 ${lng.toFixed(5)}`, lat, lng,
          source: 'geolocation', accuracy: Number.isFinite(accuracy) && accuracy >= 0 ? accuracy : null });
      }, error => {
        const messages = {
          1: '위치 권한이 거부되었습니다. 브라우저 위치 권한을 확인하거나 주소·장소 검색을 이용해 주세요.',
          2: '현재 위치를 확인할 수 없습니다. 기기 위치 설정을 확인하거나 검색으로 선택해 주세요.',
          3: '위치 확인 시간이 초과되었습니다. 다시 시도하거나 검색으로 선택해 주세요.',
        };
        reject(new Error(messages[error.code] || '현재 위치를 가져오지 못했습니다.'));
      }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
    });
  }

  // REST 키는 브라우저에서 사용하지 않고, 검색어와 좌표만 자체 서버에 전달합니다.
  return { current, validCoordinates,
    search: (query, kind, signal) => post('search', { query, kind }, signal),
    reverse: (location, signal) => post('reverse', { lat: location.lat, lng: location.lng }, signal),
  };
})();
