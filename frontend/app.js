/* 위치와 식당 후보는 실제 카카오 검색을 사용합니다. 질문 해석은 Groq를 사용하며 메뉴 분류·인증은 데모입니다. */
const $ = (s, root = document) => root.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const defaults = () => ({ minutes: 45, budget: 12000, cuisine: '전체', diet: 'any', spicy: true });
// 위치는 실제 조회 결과만 저장합니다. 반경은 다음 식당 검색 연결을 위한 요청값입니다.
const locationSearch = { kind: 'place', query: '', items: [], error: '', busy: false, token: 0, controller: null, timer: null };
// 위치 반경은 음식 조건과 별도로 관리하며 기본값은 500m입니다.
const state = { conditions: defaults(), radiusM: 500, location: null, user: null, prefs: {}, result: null, selected: null, cardPage: 0, mapPage: 0, busy: false, spinning: false, version: 0, messages: [], chatTurn: 0, gamePlayed: false };
let toastTimer, spinTimer, spinFinish;
try { state.prefs = JSON.parse(localStorage.getItem('lr-demo-preferences') || '{}'); } catch { state.prefs = {}; }
// Each browser session starts as a guest. Demo membership is not authentication.
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 3500); }
function route() { return location.hash.slice(1) || 'home'; }
function cancelSpin() { clearInterval(spinTimer); clearTimeout(spinFinish); state.spinning = false; }
function invalidate() { state.version++; state.result = null; state.selected = null; state.cardPage = 0; state.mapPage = 0; state.gamePlayed = false; cancelSpin(); }
function go(page) { if (route() === page) render(); else location.hash = page; }
async function api(path, body) {
  const response = await fetch(`/api/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(path === 'chat' ? 45000 : 30000) });
  // 인증·권한·한도 오류를 구분하는 서버의 안전한 안내를 표시하며 실패를 빈 결과로 숨기지 않습니다.
  if (!response.ok) {
    const data=await response.json().catch(()=>({}));
    throw new Error(typeof data.detail==='string'?data.detail:response.status===422?'입력 범위를 확인해 주세요.':'요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.');
  }
  return response.json();
}
// 추천/게임/채팅이 모두 같은 좌표·반경을 전송합니다. 설정 전에는 요청하지 않습니다.
function payload(menu = false) {
  if (!state.location || !LunchRunLocation.validCoordinates(state.location.lat, state.location.lng)) throw new Error('먼저 기준 위치를 설정해 주세요.');
  return { conditions: { ...state.conditions }, menu_id: menu ? state.result?.menu?.id || null : null,
    location: { ...state.location }, radius_m: state.radiusM };
}
function money(value) { return Number(value).toLocaleString('ko-KR') + '원'; }
function button(label, action, cls = 'button secondary', more = '') { return `<button class="${cls}" data-action="${action}" ${more}>${label}</button>`; }
function header() {
  $('#account').innerHTML = state.user ? `<span class="greeting"><b>${esc(state.user.name)}</b>님 반갑습니다</span>${button('회원페이지', 'profile', 'button small secondary')}${button('로그아웃', 'logout', 'text-button')}` : `<span class="guest-badge">GUEST</span>${button('로그인 ↗', 'login', 'button small dark')}`;
  document.querySelectorAll('.site-header nav a').forEach(a => a.classList.toggle('active', a.hash === '#' + route()));
}
function locationBox() {
  // 자동 조회의 오차를 숨기지 않고 주소와 함께 표시합니다. 정확한 주소 변환은 실패할 수 있습니다.
  const accuracy = state.location?.source === 'geolocation' && state.location.accuracy != null ? ` · 정확도 약 ${Math.ceil(state.location.accuracy)}m` : '';
  return `<div class="location-bar"><span class="location-icon">⌖</span><div><small>점심의 출발점</small><strong>${esc(state.location?.name || '위치를 먼저 골라주세요')}</strong><span>${esc((state.location?.address || '현재 위치 또는 주소·장소 검색으로 시작해요') + accuracy)}</span></div>${button(state.location ? '재설정' : '위치 설정', 'location', 'button small secondary')}</div>`;
}
// 홈의 독립 입력과 조건 수정 창에서 동일한 반경 입력 규칙을 사용합니다.
function radiusField() {
  return `<label>위치 반경<span class="input-unit"><input name="radius_m" type="number" min="50" max="500" step="1" required value="${state.radiusM}" aria-label="위치 반경 미터"><span>m</span></span></label><small>선택 위치 기준 직선거리 · 50~500m · 1m 단위</small>`;
}
function radiusForm() {
  // 홈 화면의 기존 반경 적용 방식은 유지합니다.
  return `<form class="radius-form">${radiusField().replace('</label>', '</label><button class="button small secondary" type="submit">반경 적용</button>')}</form>`;
}
function applyRadius(form) {
  if (!form.reportValidity()) return false;
  const radius = Number(new FormData(form).get('radius_m'));
  if (!Number.isInteger(radius) || radius < 50 || radius > 500) return false;
  // 기존 검색 응답과 추첨을 무효화하여 이전 반경의 목록·마커를 재사용하지 않습니다.
  if (radius !== state.radiusM) { state.radiusM=radius; invalidate(); }
  return true;
}
function summaries() {
  const c = state.conditions, diet = { any: '채식 상관없음', vegetarian: '달걀·유제품 가능', vegan: '완전 채식' };
  return `<div class="context-stack">${locationBox()}<div class="condition-bar"><span class="context-label">나의 점심 조건</span><div class="chips"><span>반경 ${state.radiusM}m</span><span>${c.minutes}분</span><span>${money(c.budget)} 이하</span><span>${esc(c.cuisine)}</span><span>${diet[c.diet]}</span><span>${c.spicy ? '매운 음식 OK' : '매운 음식 제외'}</span></div>${button('조건 수정', 'conditions', 'text-button')}</div></div>`;
}
function conditionsForm(compact = false) {
  const c = state.conditions;
  return `<form id="conditions-form" class="conditions-form ${compact ? 'compact' : ''}">
    <!-- 조건 수정 창에서만 반경을 음식 조건과 함께 저장하며 홈 배치는 유지합니다. -->
    ${compact ? `<div class="radius-fields">${radiusField()}</div>` : ''}
    <div class="field-row"><label>남은 점심시간<span class="input-unit"><input name="minutes" type="number" min="10" max="120" step="1" required value="${c.minutes}"><span>분</span></span></label><label>내 예산<span class="input-unit"><input name="budget" type="number" min="1000" max="100000" step="500" required value="${c.budget}"><span>원</span></span></label></div>
    <fieldset><legend>오늘 끌리는 음식 종류</legend><div class="cuisine-options">${['전체','한식','중식','일식','양식','샐러드'].map((v,i) => `<label class="choice"><input type="radio" name="cuisine" value="${v}" ${c.cuisine === v ? 'checked' : ''}><span>${['✳','🍚','🥟','🍱','🍝','🥗'][i]} ${v}</span></label>`).join('')}</div></fieldset>
    <label>채식 여부<select name="diet"><option value="any" ${c.diet === 'any' ? 'selected' : ''}>상관없음</option><option value="vegetarian" ${c.diet === 'vegetarian' ? 'selected' : ''}>달걀 및 유제품 가능</option><option value="vegan" ${c.diet === 'vegan' ? 'selected' : ''}>완전 채식</option></select></label>
    <label class="check-row"><span><b>매운 음식도 괜찮아요</b><small>해제하면 매운 메뉴를 제외해요</small></span><input type="checkbox" name="spicy" ${c.spicy ? 'checked' : ''}></label>
    <button type="button" class="add-condition" disabled>＋ 음식 조건 추가 <small>COMING SOON</small></button>
    <button type="submit" class="button primary full">${compact ? '변경한 조건 적용' : '조건 선택 완료'} <span>↗</span></button>
    <button type="button" data-action="reset" class="text-button reset">↻ 조건 초기화</button>
  </form>`;
}
function home() {
  return `<section class="home-grid"><div class="hero"><div class="eyebrow"><span></span> A LITTLE LESS THINKING. A BETTER LUNCH.</div><h1>점심시간은 짧게,<br>고민은 <em>더 짧게.</em></h1><p class="hero-copy">오늘 뭐 먹지? 이제 그만.<br>당신의 시간과 취향에 맞춰 한 끼를 골라보세요.</p>
    <div class="plate-scene" aria-hidden="true"><span class="orbit orbit-one"></span><span class="orbit orbit-two"></span><div class="food-note">오늘의 점심을 향해 <span>↗</span></div><div class="plate"><div class="plate-inner"><span class="food-bowl">🍱</span></div></div><span class="scene-star">✳</span><span class="scene-tag">GOOD FOOD,<br>GOOD AFTERNOON.</span></div>
    <div class="steps"><span><b>01</b> 위치 정하기</span><i>→</i><span><b>02</b> 조건 고르기</span><i>→</i><span><b>03</b> 점심 출발!</span></div></div>
    <div class="setup-panel"><div class="panel-title"><div><span class="eyebrow">YOUR LUNCH, YOUR RULES</span><h2>오늘의 점심 조건</h2></div><span class="tiny-label">STEP 01</span></div>${locationBox()}${radiusForm()}${conditionsForm()}</div></section>`;
}
function pageHeading(kicker, title, text) { return `<div class="page-heading"><span class="eyebrow">${kicker}</span><h1>${title}</h1><p>${text}</p></div>`; }
function pager(page, size, count, action) {
  const pages = Math.ceil(count / size);
  if (pages < 2) return '';
  return `<div class="pager">${button('← 이전', action, 'button small secondary', `data-page="${page-1}" ${page===0?'disabled':''}`)}<span>${page+1} / ${pages}</span>${button('다음 →', action, 'button small secondary', `data-page="${page+1}" ${page===pages-1?'disabled':''}`)}</div>`;
}
function prefsFor(id) { return state.prefs[state.user?.name]?.[id]?.value; }
function preferenceButtons(item) {
  if (!state.user) return '';
  const pref = prefsFor(item.id);
  // 외부에서 받은 식당 ID도 속성에 넣기 전에 이스케이프합니다.
  return `<div class="preference-buttons">${button('♡','preference',`icon-button ${pref==='like'?'liked':''}`,`data-id="${esc(item.id)}" data-value="like" aria-label="${esc(item.name)} 선호" aria-pressed="${pref==='like'}"`)}${button('⊘','preference',`icon-button ${pref==='dislike'?'disliked':''}`,`data-id="${esc(item.id)}" data-value="dislike" aria-label="${esc(item.name)} 비선호" aria-pressed="${pref==='dislike'}"`)}</div>`;
}
function restaurantCard(item, sidebar = false) {
  // 카카오에는 메뉴 가격/평점/도보시간이 없으므로 null을 0원·0분으로 변환하지 않습니다.
  const price=typeof item.price==='number'&&Number.isFinite(item.price)?money(item.price):'데이터 없음';
  const distance=typeof item.distance_m==='number'&&Number.isFinite(item.distance_m)?`${Math.round(item.distance_m).toLocaleString('ko-KR')}m`:'데이터 없음';
  return `<article class="restaurant-card ${sidebar?'list-card':''} ${state.selected===item.id?'selected':''}">${preferenceButtons(item)}<button class="card-image" data-action="select-restaurant" data-id="${esc(item.id)}" aria-label="${esc(item.name)} 선택"><span>⌑</span><small>No image</small></button><div class="card-content"><span class="card-category">${esc(item.category||'분류 데이터 없음')}</span><button class="card-name" data-action="select-restaurant" data-id="${esc(item.id)}">${sidebar?`${(state.result?.restaurants||[]).slice(state.mapPage*5,state.mapPage*5+5).findIndex(p=>p.id===item.id)+1}. `:''}${esc(item.name)}</button><span class="menu-line">검색어: ${esc(item.search_keyword||'정보 없음')} · 판매 여부 미확인</span><span class="card-address">${esc(item.address||'주소 데이터 없음')}</span><div class="card-price">${price} <span>메뉴 가격</span></div><div class="card-meta"><span>☆ 평점 데이터 없음</span><span>직선거리 ${distance}</span></div><small class="estimate-note">이동시간 데이터 없음 · 음식 조건 미확인</small></div></article>`;
}
function resultCards() {
  if (!state.result?.restaurants?.length) return '';
  const items = state.result.restaurants;
  // 실제 검색 후보라는 점과 미검증 조건을 카드 위에 함께 표시합니다.
  return `<section class="result-section"><div class="section-heading"><div><span class="eyebrow">LUNCH SPOTS</span><h2>${esc(state.result.menu.name)} 관련 식당 후보 <small>${items.length}</small></h2></div><span class="muted">카카오 검색</span></div><p class="candidate-notice">${esc(state.result.message)}${state.result.has_more?' 현재 첫 검색 페이지의 후보만 표시합니다.':''}</p><div class="restaurant-grid">${items.slice(state.cardPage*4,state.cardPage*4+4).map(i=>restaurantCard(i)).join('')}</div>${pager(state.cardPage,4,items.length,'cards-page')}</section>`;
}
function chatMessages() {
  // 질문 해석은 예시 방식이지만 식당 목록은 실제 카카오 검색으로 조회합니다.
  if (!state.messages.length) return `<div class="chat-welcome"><span class="chat-symbol">✳</span><h3>오늘의 점심 메이트</h3><p>질문을 AI로 해석하고 조건에 맞는 예시 메뉴와 주변 식당 후보를 찾아요.<br>실제 매장의 음식 조건은 미확인입니다.</p></div>`;
  return state.messages.map((m,i) => `<div class="message ${m.role}"><span class="message-avatar">${m.role==='user'?'나':'✳'}</span><div><p>${esc(m.text)}</p>${m.patch ? `<p class="patch-summary">${esc(patchSummary(m.patch))}</p>` : ''}${m.patch ? button('조건 변경 적용','apply-patch','button small secondary',`data-message="${i}" ${m.version!==state.version||m.turn!==state.chatTurn||state.busy?'disabled':''}`):''}${m.map && m.version===state.version ? `<div class="message-actions">${button('Yes · 지도 보기','map','button small primary')}${button('No · 계속 고르기','decline-map','button small secondary',`data-message="${i}"`)}</div>`:''}</div></div>`).join('');
}
function recommendPage() {
  return `${pageHeading('MAKE YOUR LUNCH', '오늘은 어떻게 골라볼까요?', '운에 맡겨도 좋고, 점심 메이트에게 물어봐도 좋아요.')}${summaries()}<div class="recommend-layout"><section class="game-invite"><span class="eyebrow">LET LUCK PICK</span><div class="dice-art" aria-hidden="true">⚄</div><h2>고민 대신,<br>운 좋은 한 끼.</h2><p>조건에 맞는 메뉴 중 하나를<br>랜덤으로 골라드릴게요.</p>${button('메뉴 뽑으러 가기 ↗','game','button dark full')}<small>외부 게임 없이 동작하는 데모 추첨</small></section><section class="chat-panel"><div class="chat-header"><div><span class="status-dot"></span><b>점심 메이트</b></div><span id="ai-connection-summary" class="tiny-label ai-connection-summary" data-status="checking" data-busy="${state.busy}">${state.busy?'AI 응답 생성 중…':'AI 연결 확인 중'}</span></div><div id="groq-connection" class="groq-connection" data-status="checking" role="status" aria-live="polite"><div><b class="groq-status-label">Groq 연결 확인 중</b><p class="groq-status-detail">연결 상태를 확인합니다.</p><small class="groq-status-time">아직 확인된 시각이 없습니다.</small></div><button class="button small secondary" data-action="check-groq">연결 다시 확인</button></div><div class="chat-body" id="chat-body" role="log" aria-live="polite">${chatMessages()}</div><div class="quick-prompts">${['지금 조건으로 추천해줘','가까운 식당 보여줘','메뉴 설명해줘'].map(q=>button(esc(q),'prompt','prompt-button',`data-text="${q}" ${state.busy?'disabled':''}`)).join('')}</div><form id="chat-form" class="chat-input"><label class="sr-only" for="chat-message">점심 질문</label><input id="chat-message" name="message" maxlength="500" placeholder="오늘 점심은 어떤 게 좋을까요?" required ${state.busy?'disabled':''}><button class="send-button" aria-label="메시지 보내기" ${state.busy?'disabled':''}>${state.busy?'…':'↑'}</button></form></section></div>${resultCards()}`;
}
function gamePage() {
  const menu = state.result?.menu;
  // 카탈로그 예상 가격을 실제 식당 가격처럼 표시하지 않고 메뉴 분류와 검색 상태를 구분합니다.
  return `${pageHeading('A LUCKY LITTLE LUNCH', '오늘의 한 끼를 뽑아보세요.', '예시 메뉴를 고른 뒤 주변 관련 식당을 검색합니다.')}${summaries()}<section class="game-stage"><span class="tiny-label">LUNCH LOTTERY</span><div class="lottery-icon ${state.busy?'rolling':''}">${menu?menu.emoji:'🎲'}</div><h2>${state.busy?'메뉴를 고르고 식당을 검색하는 중…':menu?esc(menu.name):'오늘은 뭐가 나올까요?'}</h2><p>${esc(state.result?.message||'메뉴의 예시 분류·예상 가격으로 후보를 고릅니다. 실제 매장 조건은 미확인입니다.')}</p><div class="game-actions">${menu?button('식당 후보 보기 →','map','button dark',state.busy?'disabled':''):''}${button(menu?'한번 더! ↻':'랜덤 메뉴 뽑기 ↗','draw-menu','button primary',state.busy?'disabled':'')}</div><small>식당 가격·채식·맵기·총소요시간은 검증하지 않았습니다.</small></section>`;
}
function mapGraphic(items) {
  // 실제 지도와 로딩·실패 안내를 분리하여 마커를 가리지 않게 합니다.
  return `<section id="map-panel" class="live-map-shell" aria-label="선택 위치 주변 지도"><div class="kakao-map-canvas" aria-label="카카오 지도"></div><div class="map-status" role="status" aria-live="polite"><p>지도를 불러오는 중입니다…</p><button class="button secondary" data-action="retry-map" hidden>지도 다시 불러오기</button></div></section>`;
}
function mapPage() {
  const items = state.result?.restaurants || [];
  // 미검증 조건 안내는 목록이 비었을 때도 유지하며 검색 페이지 제한도 명시합니다.
  return `${pageHeading('FIND YOUR LUNCH SPOT', '주변 식당 후보를 살펴보세요.', '선택 위치와 현재 목록의 식당을 지도에 표시합니다. 번호 마커를 눌러 식당을 선택하세요.')}${summaries()}<p class="candidate-notice">실제 판매 메뉴·예산·채식·맵기·총소요시간은 미확인입니다.${state.result?.has_more?' 첫 검색 페이지의 후보만 표시합니다.':''}</p><div class="map-layout"><aside class="restaurant-sidebar"><div class="sidebar-title"><h2>${state.result?.menu?esc(state.result.menu.name):'식당 목록'} <small>${items.length}</small></h2><span>카카오 검색</span></div>${button(state.spinning?'두근두근… 5초 추첨 중':state.gamePlayed?'한번 더! ↻':'식당 랜덤 선택 ⚄','draw-restaurant','button primary full',`${!items.length||state.spinning?'disabled':''}`)}<div class="sidebar-items">${items.length?items.slice(state.mapPage*5,state.mapPage*5+5).map(i=>restaurantCard(i,true)).join(''):`<div class="empty"><span>⌑</span><h3>표시할 식당 후보가 없어요</h3><p>${esc(state.result?.message || '게임이나 점심 메이트에서 메뉴를 먼저 골라주세요.')}</p>${button('메뉴 고르러 가기','recommend','button small secondary')}</div>`}</div>${pager(state.mapPage,5,items.length,'map-page')}</aside>${mapGraphic(items)}</div>`;
}
function profilePage() {
  if (!state.user) return `<section class="empty page-empty"><h1>회원 전용 공간이에요</h1><p>실제 계정 없이 데모 회원 기능을 체험해 보세요.</p>${button('데모 로그인','login','button primary')}</section>`;
  const values = Object.values(state.prefs[state.user.name] || {});
  // 이전 데모 저장값과 실제 식당 저장값을 구분하며 누락 가격을 0원으로 표시하지 않습니다.
  const list = pref => values.filter(v=>v.value===pref).map(v=>`<div class="saved-item"><div><b>${esc(v.item.name)}</b><span>${esc(v.item.address||v.item.menu_name||'주소 데이터 없음')} · ${typeof v.item.price==='number'?money(v.item.price):'가격 데이터 없음'} · ${v.item.source==='kakao'?'카카오 검색 후보':'이전 데모 데이터'}</span></div>${button('해제','remove-preference','text-button',`data-id="${esc(v.item.id)}"`)}</div>`).join('') || '<div class="empty compact-empty">아직 저장한 식당이 없어요.</div>';
  return `${pageHeading('MY LUNCH NOTE', `${esc(state.user.name)}님의 점심 노트`, '좋았던 곳도, 취향과 달랐던 곳도 기억해 둘게요.')}<div class="profile-info"><div class="profile-avatar">${esc(state.user.name.slice(0,1))}</div><div><h2>${esc(state.user.name)}</h2><p>demo@lunchrun.example · 데모 회원</p></div><span class="tiny-label">이 브라우저에만 저장</span></div><div class="profile-columns"><section class="panel"><h2>♡ 선호 식당</h2>${list('like')}</section><section class="panel"><h2>⊘ 비선호 식당</h2>${list('dislike')}</section></div>`;
}
// 현재 목록의 최대 5개 식당과 선택 상태를 지도에 전달합니다.
function mountRestaurantMap(retry = false) {
  const items = (state.result?.restaurants || []).slice(state.mapPage*5,state.mapPage*5+5);
  window.LunchRunMap?.mount($('#map-panel'), state.location, retry, {
    items, radiusM:state.radiusM, selected:state.selected, spinning:state.spinning,
    onSelect:id=>{
      // 마커 선택도 카드 선택과 같은 상태를 사용하므로 양쪽 강조가 일치합니다.
      if (state.spinning) return;
      state.selected=id; render();
      $('.restaurant-sidebar .restaurant-card.selected')?.scrollIntoView({block:'nearest',behavior:'smooth'});
    },
  });
}
function render() {
  let page = route();
  if (!['home','recommend','game','map','profile'].includes(page)) { location.hash='home'; return; }
  if (['recommend','game','map'].includes(page) && !state.location) { location.hash='home'; toast('먼저 기준 위치를 선택해 주세요.'); return; }
  // 같은 지도 화면의 재렌더에서는 DOM과 지도 객체를 보존합니다.
  const previousMap = page === 'map' ? $('#map-panel') : null;
  if (page !== 'map') window.LunchRunMap?.unmount();
  header();
  $('#app').innerHTML = ({home, recommend:recommendPage, game:gamePage, map:mapPage, profile:profilePage})[page]();
  // 추첨·목록 변경으로 지도를 재생성하지 않고 위치 변경만 반영합니다.
  if (page === 'map') {
    if (previousMap) $('#map-panel').replaceWith(previousMap);
    mountRestaurantMap();
  }
  // 추천 화면 진입 시에만 확인하며 재렌더에는 모듈의 최근 결과를 재사용합니다.
  if(page==='recommend')window.LunchRunGroqStatus?.check();
  const body = $('#chat-body'); if(body) body.scrollTop=body.scrollHeight;
}
// 다른 창을 열거나 위치 창을 닫으면 진행 중 검색과 늦은 Geolocation 응답을 무효화합니다.
function cancelLocationRequest() {
  locationSearch.token++; locationSearch.controller?.abort(); clearTimeout(locationSearch.timer); locationSearch.busy=false;
}
function openModal(content) { cancelLocationRequest(); const modal=$('#modal'); modal.innerHTML=`<button data-action="close-modal" class="modal-close" aria-label="닫기">×</button>${content}`; if(!modal.open) modal.showModal(); }
function chooseLocation() {
  // 홈에서 작성 중인 유효한 조건은 위치 설정 후 다시 그려져도 유지합니다.
  const draft=$('#conditions-form');
  if(draft && draft.checkValidity()) {
    const d=new FormData(draft);const next={minutes:Number(d.get('minutes')),budget:Number(d.get('budget')),cuisine:d.get('cuisine'),diet:d.get('diet'),spicy:d.has('spicy')};
    // 작성 중 조건이 달라졌다면 위치 선택을 취소하더라도 이전 추천을 재사용하지 않습니다.
    if(JSON.stringify(next)!==JSON.stringify(state.conditions)){state.conditions=next;invalidate();}
  }
  locationSearch.query='';locationSearch.items=[];locationSearch.error='';
  openModal(`<span class="eyebrow">WHERE TO START</span><h2>점심의 출발점</h2><p class="muted">현재 위치 조회 시 권한이 필요합니다. 주소 표시를 위해 좌표를 카카오에 전달하며, 앱은 위치를 영구 저장하지 않습니다.</p>${button('⌖ 현재 위치 가져오기','current-location','button primary full')}<div class="location-tabs" role="group" aria-label="위치 검색 종류">${button('장소명 검색','location-kind','button small secondary',`data-kind="place" aria-pressed="${locationSearch.kind==='place'}"`)}${button('주소 검색','location-kind','button small secondary',`data-kind="address" aria-pressed="${locationSearch.kind==='address'}"`)}</div><form id="location-search-form"><label for="location-search">주소 또는 장소 검색</label><div class="location-search-row"><input id="location-search" name="query" maxlength="100" placeholder="예: 가산디지털단지역" required autocomplete="off"><button class="button dark small" type="submit">검색</button></div></form><p id="location-status" class="location-status" role="status" aria-live="polite">검색 후 결과를 선택해 주세요. 좌표의 주소 변환에 실패해도 위치는 사용할 수 있습니다.</p><div id="location-options"></div>`);
}
// 검색값은 HTML에 넣기 전에 이스케이프하여 외부 장소명이 마크업으로 실행되지 않게 합니다.
function showLocationResults(items) {
  $('#location-options').innerHTML=items.map((item,i)=>`<button class="location-option" data-action="pick-location" data-index="${i}"><span>⌖</span><div><b>${esc(item.name)}</b><small>${esc(item.address)}</small></div><span>→</span></button>`).join('');
}
function locationStatus(text) { const node=$('#location-status'); if(node)node.textContent=text; }
function updateLocationBusy(busy) {
  locationSearch.busy=busy;
  const button=$('[data-action="current-location"]');if(button){button.disabled=busy;button.textContent=busy?'위치 확인 중…':'⌖ 현재 위치 가져오기';}
  const submit=$('#location-search-form button');if(submit)submit.disabled=busy;
}
// 성공한 위치 선택만 기존 추천을 초기화합니다. 조회 실패 시 기존 위치는 유지됩니다.
function selectLocation(item) {
  if(!item||!LunchRunLocation.validCoordinates(item.lat,item.lng)){locationStatus('올바른 위치가 아닙니다. 다시 검색해 주세요.');return;}
  cancelLocationRequest();state.location={...item};invalidate();$('#modal').close();render();toast('기준 위치를 설정했어요.');
}
async function searchLocation(query) {
  cancelLocationRequest();const token=locationSearch.token;locationSearch.controller=new AbortController();
  locationSearch.query=query;locationSearch.items=[];showLocationResults([]);updateLocationBusy(true);locationStatus('검색 중…');
  try {
    const data=await LunchRunLocation.search(query,locationSearch.kind,locationSearch.controller.signal);
    if(token!==locationSearch.token||!$('#modal').open)return;
    locationSearch.items=data.items;showLocationResults(data.items);
    locationStatus(data.items.length?`검색 결과 ${data.items.length}개${data.has_more?' · 상위 10개입니다. 검색어를 구체적으로 입력하면 더 정확히 찾을 수 있어요.':''}`:'검색 결과가 없습니다. 주소/장소 검색 종류나 검색어를 바꿔 주세요.');
  } catch(error) {if(token===locationSearch.token)locationStatus(error.name==='TimeoutError'?'검색 시간이 초과되었습니다. 다시 시도해 주세요.':error.message);}
  finally {if(token===locationSearch.token)updateLocationBusy(false);}
}
async function getCurrentLocation() {
  if(locationSearch.busy)return;cancelLocationRequest();const token=locationSearch.token;
  locationSearch.controller=new AbortController();updateLocationBusy(true);showLocationResults([]);locationStatus('브라우저 위치 권한을 확인해 주세요.');
  // 권한 대기까지 포함해 30초 후 UI를 복구합니다. 취소 이후의 브라우저 응답은 무시합니다.
  locationSearch.timer=setTimeout(()=>{if(token===locationSearch.token){cancelLocationRequest();updateLocationBusy(false);locationStatus('위치 확인이 지연되고 있습니다. 다시 시도하거나 검색으로 선택해 주세요.');}},30000);
  try {
    const item=await LunchRunLocation.current();if(token!==locationSearch.token)return;
    locationStatus('좌표를 확인했습니다. 주소를 확인하는 중…');
    // 주소 변환 장애는 위치 확보 실패가 아닙니다. 확보한 좌표를 대체 표시합니다.
    try {const result=await LunchRunLocation.reverse(item,locationSearch.controller.signal);if(result.address)item.address=result.address;} catch { /* 좌표 문자열을 유지합니다. */ }
    if(token===locationSearch.token&&$('#modal').open)selectLocation(item);
  } catch(error) {if(token===locationSearch.token)locationStatus(error.message);}
  finally {if(token===locationSearch.token){clearTimeout(locationSearch.timer);updateLocationBusy(false);}}
}
function authModal(signup=false) {
  openModal(`<span class="eyebrow">WELCOME TO LUNCH RUN</span><h2>${signup?'회원가입 화면 체험':'가볍게, 데모 로그인'}</h2><p class="muted">실제 계정은 생성하지 않아요. 닉네임으로 회원 화면을 체험합니다.</p><div class="auth-tabs">${button('로그인','login',signup?'text-button':'text-button active')}${button('회원가입','signup',signup?'text-button active':'text-button')}</div><form id="auth-form"><label>이메일<input type="email" value="demo@lunchrun.example" disabled></label><label>닉네임<input name="nickname" maxlength="20" placeholder="예: 점심러너" required autocomplete="off"></label><label>비밀번호<input type="password" placeholder="인증 연결 전에는 입력하지 않아요" disabled></label><button class="button primary full">${signup?'데모 회원으로 시작':'데모 로그인'} ↗</button></form><div class="auth-links">${button('아이디 찾기','unavailable','text-button')}${button('비밀번호 찾기','unavailable','text-button')}</div>`);
}
async function drawMenu() {
  if(state.busy) return;
  // 새 메뉴 검색은 이전 추첨과 변경안을 무효화해 오래된 식당이 다시 선택되지 않게 합니다.
  invalidate();
  // 재검색이 실패해도 이전 식당을 새 결과처럼 보여주지 않도록 시작 시 목록을 비웁니다.
  const version=state.version;state.result=null;state.selected=null;state.gamePlayed=false;state.busy=true;render();
  try { const result=await api('recommendations',payload()); if(version!==state.version) return; state.result=result; state.selected=null; state.cardPage=0; state.mapPage=0; if(!result.menu) toast(result.message); }
  catch(e){if(version===state.version){state.result={menu:null,restaurants:[],message:e.message};toast(e.message);}} finally {state.busy=false;render();}
}
// 변경안을 실제 값으로 표시하며 사용자 확인 전에는 상태를 바꾸지 않습니다.
function patchSummary(patch) {
  const names={minutes:'점심시간',budget:'예산',cuisine:'음식 종류',diet:'채식',spicy:'매운 음식',radius_m:'반경'};
  const diets={any:'상관없음',vegetarian:'달걀·유제품 가능',vegan:'완전 채식'};
  return Object.entries(patch).map(([k,v])=>`${names[k]||k}: ${k==='diet'?diets[v]:k==='spicy'?(v?'가능':'제외'):v}${k==='radius_m'?'m':k==='minutes'?'분':k==='budget'?'원':''}`).join(' · ');
}
function applyChatPatch(index) {
  const m=state.messages[index];
  // 다른 질문·위치·조건 변경 이후의 제안이나 중복 적용은 실행하지 않습니다.
  if(state.busy||!m?.patch||m.version!==state.version||m.turn!==state.chatTurn)return false;
  const patch=m.patch, {radius_m,...food}=patch;
  const allowed=['minutes','budget','cuisine','diet','spicy','radius_m'];
  if(Object.keys(patch).some(k=>!allowed.includes(k)))return false;
  if(radius_m!==undefined&&(!Number.isInteger(radius_m)||radius_m<50||radius_m>500))return false;
  state.conditions={...state.conditions,...food};
  if(radius_m!==undefined)state.radiusM=radius_m;
  invalidate(); m.patch=null;
  state.messages.push({role:'assistant',text:'조건을 적용했어요. 새 조건으로 다시 추천해 달라고 해주세요.'});
  render(); return true;
}
async function sendChat(message) {
  if(state.busy||!message.trim())return;
  if (/^(yes|네|예|응)$/i.test(message.trim()) && state.result?.menu && state.messages.some(m=>m.map&&m.version===state.version)) { go('map'); return; }
  // 추첨 도중 다른 화면에서 채팅을 시작해도 기존 타이머가 검색 결과를 덮지 않게 합니다.
  cancelSpin();
  const version=state.version, turn=++state.chatTurn;
  state.messages.push({role:'user',text:message});state.busy=true;render();
  // Groq 해석 이후 서버가 반환한 실제 검색 결과만 기존 카드·지도에 연결합니다.
  try {
    const data=await api('chat',{...payload(true),message});
    if(version!==state.version)return;
    if(data.result){cancelSpin();state.result=data.result;state.cardPage=0;state.mapPage=0;state.selected=null;state.gamePlayed=false;}
    state.messages.push({role:'assistant',text:data.text,patch:data.suggested_patch,version,turn});
    if(data.result?.restaurants?.length)state.messages.push({role:'assistant',text:'검색된 식당 후보 목록과 주변 지도를 볼까요?',map:true,version});
  } catch(e){
    if(version===state.version){state.result=null;state.selected=null;state.messages.push({role:'assistant',text:e.name==='TimeoutError'?'전체 요청 시간이 초과되었습니다. 다시 시도해 주세요.':e.message});}
  } finally {state.busy=false;render();}
}

function drawRestaurant() {
  const items=state.result?.restaurants;if(!items?.length||state.spinning)return;
  state.spinning=true;const version=state.version;const chosen=items[Math.floor(Math.random()*items.length)];let i=0;
  const highlight=item=>{state.selected=item.id;state.mapPage=Math.floor(items.findIndex(x=>x.id===item.id)/5);render();};
  spinTimer=setInterval(()=>highlight(items[(i++)%items.length]),180);render();
  spinFinish=setTimeout(()=>{clearInterval(spinTimer);state.spinning=false;if(version!==state.version)return;state.gamePlayed=true;highlight(chosen);toast(`${chosen.name} 선택 완료!`);},5000);
}
document.addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target;const data=new FormData(form);
  // 반경은 별도 위치 조건이며 적용 후에는 새로 식당을 검색해야 합니다.
  if(form.classList.contains('radius-form')) {
    if(applyRadius(form)){render();if($('#modal').open)$('#modal').close();toast('반경을 적용했어요. 식당을 다시 검색해 주세요.');}
    return;
  }
  if(form.id==='conditions-form'){
    // 수정 창에서는 같은 폼의 반경을, 홈에서는 기존 독립 반경 폼을 검증합니다.
    if(!form.reportValidity())return;
    const radiusDraft=data.has('radius_m') ? form : $('.radius-form');
    if(radiusDraft && !applyRadius(radiusDraft))return;
    state.conditions={minutes:Number(data.get('minutes')),budget:Number(data.get('budget')),cuisine:data.get('cuisine'),diet:data.get('diet'),spicy:data.has('spicy')};invalidate();
    if($('#modal').open){$('#modal').close();render();toast('조건을 변경했어요. 이전 추천은 초기화했습니다.');}
    else if(!state.location){render();chooseLocation();toast('조건을 저장했어요. 기준 위치를 골라주세요.');}
    else go('recommend');
  }
  if(form.id==='auth-form'){state.user={name:String(data.get('nickname')).trim()||'점심러너'};$('#modal').close();render();toast('데모 회원 모드입니다. 실제 인증은 연결하지 않았어요.');}
  if(form.id==='chat-form')await sendChat(String(data.get('message')).trim());
  // Enter/검색 버튼으로만 카카오 검색을 실행하여 입력 중 불필요한 API 요청을 줄입니다.
  if(form.id==='location-search-form'){
    const query=String(data.get('query')).trim();
    if(!query){locationStatus('주소 또는 장소를 입력해 주세요.');return;}
    await searchLocation(query);
  }
});
// 검색어 수정 시 이전 결과와 진행 중 요청을 무효화해 다른 검색어의 위치 선택을 막습니다.
document.addEventListener('input',event=>{if(event.target.id==='location-search'){cancelLocationRequest();locationSearch.items=[];showLocationResults([]);updateLocationBusy(false);locationStatus('검색 후 결과를 선택해 주세요.');}});
// ESC로 닫은 경우에도 진행 중 위치 조회 결과가 나중에 적용되지 않게 처리합니다.
$('#modal').addEventListener('close',cancelLocationRequest);
document.addEventListener('click',async event=>{
  const el=event.target.closest('[data-action]');if(!el||el.disabled)return;const action=el.dataset.action;
  if(['home','recommend','game','map','profile'].includes(action)){go(action);return;}
  if(action==='location')chooseLocation();
  // 고정 예시 대신 브라우저 현재 위치 또는 카카오 검색 결과만 선택합니다.
  if(action==='current-location')await getCurrentLocation();
  if(action==='pick-location')selectLocation(locationSearch.items[Number(el.dataset.index)]);
  if(action==='location-kind'){
    cancelLocationRequest();locationSearch.kind=el.dataset.kind;locationSearch.items=[];
    showLocationResults([]);updateLocationBusy(false);
    document.querySelectorAll('[data-action="location-kind"]').forEach(node=>node.setAttribute('aria-pressed',String(node.dataset.kind===locationSearch.kind)));
    $('#location-search').placeholder=locationSearch.kind==='address'?'예: 서울 금천구 가산디지털1로 25':'예: 가산디지털단지역';
    locationStatus('검색 종류를 변경했습니다. 검색 버튼을 눌러 주세요.');
  }
  if(action==='close-modal'){cancelLocationRequest();$('#modal').close();}
  if(action==='conditions')openModal(`<span class="eyebrow">YOUR LUNCH RULES</span><h2>음식 조건 수정</h2>${conditionsForm(true)}`);
  if(action==='reset'){state.conditions=defaults();invalidate();if($('#modal').open)openModal(`<h2>음식 조건 수정</h2>${conditionsForm(true)}`);else render();toast('음식 조건을 초기화했어요. 위치는 유지됩니다.');}
  if(action==='login'||action==='signup')authModal(action==='signup');
  if(action==='logout'){state.user=null;render();toast('게스트 모드로 전환했어요.');}
  if(action==='unavailable')toast('이 기능은 인증 서비스 연결 후 사용할 수 있어요.');
  if(action==='draw-menu')await drawMenu();
  // 연결 확인 버튼은 채팅·조건·입력 내용을 초기화하지 않습니다.
  if(action==='check-groq')window.LunchRunGroqStatus?.check(true);
  if(action==='prompt')await sendChat(el.dataset.text);
  if(action==='decline-map'){state.messages[Number(el.dataset.message)].map=false;render();}
  if(action==='apply-patch')applyChatPatch(Number(el.dataset.message));
  if(action==='cards-page'){state.cardPage=Number(el.dataset.page);render();}
  // 실패 안내의 재시도는 선택 위치와 식당 목록을 유지합니다.
  if(action==='retry-map') mountRestaurantMap(true);
  if(action==='map-page'){if(state.spinning)return;state.mapPage=Number(el.dataset.page);render();}
  if(action==='select-restaurant'){
    // 선택은 실제 식당 ID로 동기화합니다. 임의 지도 배치를 안내하지 않습니다.
    if(state.spinning)return;state.selected=el.dataset.id;const items=state.result?.restaurants||[];const index=items.findIndex(x=>x.id===state.selected);if(index<0)return;state.mapPage=Math.floor(index/5);render();if(route()!=='map')toast('식당을 선택했어요. 목록 화면에서 확인할 수 있습니다.');
  }
  if(action==='draw-restaurant')drawRestaurant();
  if(action==='preference'){
    if(!state.user)return;const item=state.result?.restaurants.find(x=>x.id===el.dataset.id);if(!item)return;
    const key=state.user.name;state.prefs[key] ||= {};const old=state.prefs[key][item.id];
    if(old?.value===el.dataset.value)delete state.prefs[key][item.id];else state.prefs[key][item.id]={value:el.dataset.value,item};
    try{localStorage.setItem('lr-demo-preferences',JSON.stringify(state.prefs));}catch{toast('브라우저 저장 공간이 부족해 임시로만 보관합니다.');}render();
  }
  if(action==='remove-preference'&&state.user){delete state.prefs[state.user.name][el.dataset.id];try{localStorage.setItem('lr-demo-preferences',JSON.stringify(state.prefs));}catch{}render();}
});
window.addEventListener('hashchange',()=>{cancelSpin();render();window.scrollTo({top:0,behavior:'instant'});});
render();
