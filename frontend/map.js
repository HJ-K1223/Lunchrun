// 지도와 선택 위치·식당 번호 마커의 수명 주기를 함께 관리합니다.
window.LunchRunMap = (() => {
  let panel = null, map = null, observer = null, generation = 0, coordinates = '';
  let sdk = null, pins = [], markerKey = '', selectedId = null, wasSpinning = false;
  let radiusCircle = null, circleKey = '';
  function clearCircle() {
    // 화면을 떠나거나 SDK 오류가 나면 이전 반경 원도 지도에서 제거합니다.
    radiusCircle?.setMap(null); radiusCircle = null; circleKey = '';
  }
  function updateCircle(location, radiusM = 500) {
    // 검색에 적용한 미터 단위 반경을 그대로 사용하고 원은 마커 아래에 배치합니다.
    const key = `${location.lat},${location.lng},${radiusM}`;
    if (key === circleKey) return false;
    if (!radiusCircle) {
      radiusCircle = new sdk.Circle({map,center:new sdk.LatLng(location.lat,location.lng),radius:radiusM,
        strokeWeight:2,strokeColor:'#234c76',strokeOpacity:0.85,strokeStyle:'dash',
        fillColor:'#528ed0',fillOpacity:0.08,zIndex:0});
    } else {
      radiusCircle.setPosition(new sdk.LatLng(location.lat,location.lng));
      radiusCircle.setRadius(radiusM);
    }
    circleKey = key;
    return true;
  }
  // 문자열 좌표나 누락값을 0으로 변환하지 않고 정상적인 숫자 좌표만 허용합니다.
  const validPoint = p => Number.isFinite(p?.lat) && Number.isFinite(p?.lng) && Math.abs(p.lat) <= 90 && Math.abs(p.lng) <= 180;
  function clearPins() {
    for (const pin of pins) { pin.node.onclick = null; pin.overlay.setMap(null); }
    pins = [];
  }
  function updatePins(location, options) {
    const {items = [], selected = null, spinning = false, onSelect = () => {}} = options;
    const visible = items.slice(0, 5);
    const circleChanged = updateCircle(location, options.radiusM);
    // 이름과 좌표 변경도 감지하여 이전 결과의 마커가 남지 않게 합니다.
    const key = JSON.stringify([location.lat, location.lng, visible.map(p => [p.id,p.name,p.lat,p.lng])]);
    const changed = key !== markerKey;
    if (changed) {
      clearPins();
      const add = (item, index, origin = false) => {
        if (!validPoint(item)) return;
        // 외부 식당명은 HTML로 조합하지 않고 textContent와 접근성 이름에 넣습니다.
        const node = document.createElement(origin ? 'span' : 'button');
        node.className = origin ? 'location-map-pin' : 'restaurant-map-pin';
        node.textContent = origin ? '⌖ 선택 위치' : String(index + 1);
        if (!origin) { node.type = 'button'; node.setAttribute('aria-label', `${index + 1}. ${item.name}`); }
        const overlay = new sdk.CustomOverlay({map, position:new sdk.LatLng(item.lat,item.lng),content:node,yAnchor:1.15,zIndex:origin?3:2,clickable:true});
        pins.push({item,node,overlay,origin});
      };
      add(location, 0, true);
      visible.forEach((item,index) => add(item,index));
      markerKey = key;
    }
    const active = pins.find(p => !p.origin && p.item.id === selected);
    for (const pin of pins) {
      if (pin.origin) continue;
      const chosen = pin === active;
      pin.node.classList.toggle('is-selected', chosen);
      pin.node.setAttribute('aria-pressed', String(chosen));
      pin.overlay.setZIndex(chosen ? 4 : 2);
      // 재렌더마다 최신 콜백을 사용하며 추첨 중에는 직접 선택을 막습니다.
      pin.node.onclick = () => { if (!spinning) onSelect(pin.item.id); };
      pin.node.textContent = chosen ? `${visible.findIndex(p=>p.id===pin.item.id)+1}. ${pin.item.name}` : String(visible.findIndex(p=>p.id===pin.item.id)+1);
    }
    // 추첨 중에는 강조만 바꾸고, 추첨 종료 또는 직접 선택 시에만 이동합니다.
    if (!spinning) {
      if (circleChanged) {
        // 최초 표시와 위치·반경 변경 시에는 식당 유무와 관계없이 원 전체를 보여줍니다.
        map.setBounds(radiusCircle.getBounds(),35,35,35,35);
      } else if (active && (selected !== selectedId || wasSpinning)) map.panTo(new sdk.LatLng(active.item.lat,active.item.lng));
      else if (changed) {
        if (radiusCircle) {
          // 목록 페이지가 바뀌어도 반경 전체를 유지해 거리 범위를 비교할 수 있게 합니다.
          map.setBounds(radiusCircle.getBounds(),35,35,35,35);
        } else if (pins.length > 1) {
          const bounds = new sdk.LatLngBounds();
          pins.forEach(p => bounds.extend(new sdk.LatLng(p.item.lat,p.item.lng)));
          map.setBounds(bounds,60,60,60,60);
        } else { map.setCenter(new sdk.LatLng(location.lat,location.lng)); map.setLevel(3); }
      }
    }
    selectedId = selected; wasSpinning = spinning;
  }
  function status(message, failed = false) {
    const overlay = panel?.querySelector('.map-status');
    if (!overlay) return;
    overlay.hidden = !message;
    overlay.querySelector('p').textContent = message;
    overlay.querySelector('button').hidden = !failed;
  }
  function relayout() {
    // 크기 변경 뒤에도 사용자가 이동시킨 지도 중심을 유지합니다.
    if (!map || !panel?.isConnected) return;
    const center = map.getCenter();
    map.relayout();
    map.setCenter(center);
  }
  function unmount() {
    // 늦게 도착한 SDK 응답이 떠난 화면에 지도를 생성하지 못하게 합니다.
    generation++;
    observer?.disconnect();
    clearPins();
    clearCircle();
    sdk = null; markerKey = ''; selectedId = null; wasSpinning = false;
    observer = null; map = null; panel = null; coordinates = '';
  }
  async function mount(target, location, retry = false, options = {}) {
    if (!target) return;
    if (panel !== target) { unmount(); panel = target; }
    const token = ++generation;
    const nextCoordinates = `${location.lat},${location.lng}`;
    try {
      if (!map) {
        status('지도를 불러오는 중입니다…');
        // 재시도는 실패한 SDK 다운로드와 공개 설정 조회도 다시 수행합니다.
        const maps = await (retry ? window.loadLunchRunMapSdk() : window.lunchRunMapReady);
        if (token !== generation || !panel?.isConnected) return;
        if (!maps) throw new Error('SDK unavailable');
        sdk = maps;
        map = new maps.Map(panel.querySelector('.kakao-map-canvas'), {
          center: new maps.LatLng(location.lat, location.lng), level: 3,
        });
        // 사이드바·모바일 레이아웃 변화로 지도 영역이 바뀌는 경우도 반영합니다.
        observer = new ResizeObserver(relayout);
        observer.observe(panel.querySelector('.kakao-map-canvas'));
        coordinates = nextCoordinates;
      } else if (coordinates !== nextCoordinates) {
        // 위치 재설정에만 중심을 바꾸며 추첨·페이지 변경 시에는 지도를 유지합니다.
        map.setCenter(new sdk.LatLng(location.lat, location.lng));
        coordinates = nextCoordinates;
      }
      status('');
      relayout();
      updatePins(location, options);
    } catch {
      if (token !== generation || !panel?.isConnected) return;
      observer?.disconnect(); observer = null; map = null;
      clearPins(); markerKey = ''; selectedId = null; wasSpinning = false;
      clearCircle();
      status('지도를 표시하지 못했습니다. JavaScript 키, 등록한 웹 도메인과 네트워크를 확인해 주세요.', true);
    }
  }
  return { mount, unmount };
})();
