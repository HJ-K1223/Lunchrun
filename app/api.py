from fastapi import APIRouter, HTTPException, Request
import asyncio
from datetime import datetime, timezone
from time import monotonic
from fastapi.responses import JSONResponse
from .clients.kakao import KakaoClient, KakaoError
from .clients.groq import GroqClient, GroqError
from .schemas import ChatRequest, RecommendationRequest
from . import services


router = APIRouter(prefix="/api")


@router.get("/groq/status")
async def groq_status(request: Request, refresh: bool = False):
    # 서버 내 단일 진행 요청과 60초 캐시를 공유합니다. 수동 확인도 최소 5초 간격입니다.
    state = request.app.state
    if not hasattr(state, "groq_status_lock"):
        state.groq_status_lock = asyncio.Lock()
    async with state.groq_status_lock:
        cache = getattr(state, "groq_status_cache", None)
        ttl = 5 if refresh else 60
        if cache and cache[0] == state.settings and monotonic() - cache[1] < ttl:
            result = {**cache[2], "cached": True}
        else:
            result = await GroqClient(state.settings).check_connection()
            result.update(checked_at=datetime.now(timezone.utc).isoformat(), cached=False)
            state.groq_status_cache = (state.settings, monotonic(), result.copy())
    # 브라우저·프록시 캐시 대신 위 서버 캐시만 사용하고 확인 시각을 함께 전달합니다.
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/health")
def health(request: Request):
    # 설정 여부만 공개하며 키나 모델 호출 성공 여부를 노출·보장하지 않습니다.
    return {"status": "ok", "mode": "related_candidates", "external_services": True, "location_service": "kakao", "restaurant_service": "kakao", "chat_service": "groq", "chat_configured": bool(request.app.state.settings.groq_api_key.get_secret_value())}


@router.post("/recommendations")
async def recommendations(body: RecommendationRequest, request: Request):
    # 입력 검증 후 선택한 위치와 반경을 서버 전용 클라이언트로 전달합니다.
    client = KakaoClient(request.app.state.settings.map_api_key)
    try:
        result = await services.recommend(body.conditions, body.location, body.radius_m, client, body.menu_id)
    except KakaoError as error:
        # 인증 실패를 빈 결과나 가상 식당으로 대체하지 않고 안전한 오류를 반환합니다.
        raise HTTPException(error.status_code, error.message, headers={"X-Error-Code": error.code}) from None
    return {**result, "location": body.location.model_dump(), "radius_m": body.radius_m}


@router.post("/chat")
async def chat(body: ChatRequest, request: Request):
    # 챗봇도 게임과 동일한 위치·반경·검색 경로를 사용합니다.
    client = KakaoClient(request.app.state.settings.map_api_key)
    try:
        result = await services.chat(body.message.strip(), body.conditions, body.menu_id, body.location, body.radius_m, client, GroqClient(request.app.state.settings))
    except (KakaoError, GroqError) as error:
        # 외부 오류 원문이나 키는 공개하지 않고 진단 코드만 헤더에 포함합니다.
        raise HTTPException(error.status_code, error.message, headers={"X-Error-Code": error.code}) from None
    return {**result, "location": body.location.model_dump(), "radius_m": body.radius_m}
