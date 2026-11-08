"""위치 HTTP 요청을 처리하고 카카오 통신은 전용 클라이언트에 위임합니다."""
from fastapi import APIRouter, HTTPException, Request
from .schemas import Coordinates, LocationSearchRequest
from .clients.kakao import KakaoClient, KakaoError

# 임의 URL을 받지 않고 카카오 공식 로컬 API로만 요청해 REST 키 유출을 막습니다.
router = APIRouter(prefix="/api/locations", tags=["위치"])


def get_client(request: Request) -> KakaoClient:
    # 키는 서버 설정에서만 가져옵니다. 라우터는 인증 헤더를 직접 구성하지 않습니다.
    return KakaoClient(request.app.state.settings.map_api_key)


def location_error(error: KakaoError) -> HTTPException:
    # 프론트엔드의 기존 detail 문자열을 유지하고 안전한 진단 코드만 헤더에 추가합니다.
    return HTTPException(error.status_code, error.message, headers={"X-Error-Code": error.code})


# 검색어는 POST 본문으로 받아 서버 접근 로그의 URL에 주소가 남는 것을 줄입니다.
@router.post("/search")
async def search_locations(body: LocationSearchRequest, request: Request):
    # 기존 검색 종류와 응답 형식은 유지하고 실제 처리는 전용 클라이언트에 위임합니다.
    client = get_client(request)
    try:
        if body.kind == "address":
            return await client.search_addresses(body.query)
        return await client.search_places(body.query)
    except KakaoError as error:
        raise location_error(error) from None


# 사용자가 현재 위치 버튼을 누른 경우에만 좌표를 카카오에 전달해 주소로 변환합니다.
@router.post("/reverse")
async def reverse_location(body: Coordinates, request: Request):
    # 좌표 순서 및 주소 우선순위도 공통 클라이언트에서 관리합니다.
    try:
        return await get_client(request).reverse_address(body.lat, body.lng)
    except KakaoError as error:
        raise location_error(error) from None
