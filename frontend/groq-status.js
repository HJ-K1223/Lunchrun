// 연결 확인은 채팅 처리 상태와 독립적으로 관리하고 입력 중인 화면은 다시 그리지 않습니다.
window.LunchRunGroqStatus = (() => {
  let pending = false, expires = 0;
  let current = {status:'checking', message:'Groq 연결 상태를 확인합니다.', checked_at:null};
  const labels = {checking:'Groq 연결 확인 중', connected:'Groq 연결 확인됨', disconnected:'Groq 연결 안 됨', unknown:'Groq 연결 확인 실패'};
  const summaryLabels = {checking:'AI 연결 확인 중', connected:'AI 연결됨', disconnected:'AI 미연결', unknown:'AI 연결 확인 실패'};
  function paint() {
    const box=document.querySelector('#groq-connection');
    if(!box)return;
    const status=pending?'checking':current.status;
    box.dataset.status=status;
    box.querySelector('.groq-status-label').textContent=labels[status];
    box.querySelector('.groq-status-detail').textContent=pending?'인증과 모델 사용 가능 여부를 확인하는 중입니다.':current.message;
    const date=current.checked_at?new Date(current.checked_at):null;
    box.querySelector('.groq-status-time').textContent=date&&!Number.isNaN(date.getTime())?`마지막 확인: ${date.toLocaleString('ko-KR')}${current.cached?' · 최근 확인 결과':''}`:'아직 확인된 시각이 없습니다.';
    box.querySelector('button').disabled=pending;
    // 채팅창 우측 상단도 동일한 검사 결과를 사용해 고정 문구와 실제 상태가 어긋나지 않게 합니다.
    const summary=document.querySelector('#ai-connection-summary');
    if(summary){
      const busy=summary.dataset.busy==='true';
      summary.dataset.status=busy?'checking':status;
      summary.textContent=busy?'AI 응답 생성 중…':summaryLabels[status];
    }
  }
  async function check(refresh=false) {
    if(pending)return;
    if(!refresh && Date.now()<expires){paint();return;}
    pending=true;paint();
    try {
      const response=await fetch(`/api/groq/status${refresh?'?refresh=true':''}`,{cache:'no-store',signal:AbortSignal.timeout(35000)});
      if(!response.ok)throw new Error();
      const data=await response.json();
      if(!['connected','disconnected','unknown'].includes(data.status)||typeof data.message!=='string')throw new Error();
      current=data;
      // 서버 응답 시각 기준으로 유효기간을 계산해 화면 이동이 캐시 수명을 늘리지 않게 합니다.
      const checked=Date.parse(data.checked_at);
      expires=Number.isFinite(checked)?checked+60000:Date.now()+10000;
    } catch {
      current={status:'unknown',message:'서버 또는 네트워크 상태를 확인해 주세요. 서버 재시작 후 다시 확인할 수 있습니다.',checked_at:null};
      expires=Date.now()+10000;
    } finally {pending=false;paint();}
  }
  return {check};
})();
