"""카카오 로컬 API 통신·검증·응답 정규화를 담당하는 서버 전용 클라이언트입니다."""
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

import httpx
from pydantic import Field, SecretStr, ValidationError
from ..schemas import Coordinates, LocationSearchRequest, SelectedLocation


# 원본 응답/인증 헤더를 담지 않는 오류입니다. HTTP 응답 변환은 라우터에서 처리합니다.
@dataclass
class KakaoError(Exception):
    code: str
    message: str
    status_code: int = 502

    def __str__(self) -> str:
        return self.message


# 함수를 직접 호출해도 잘못된 좌표·반경·페이지가 외부로 전달되지 않게 검증합니다.
class RestaurantSearch(Coordinates):
    radius_m: int = Field(default=500, ge=50, le=500, strict=True)
    page: int = Field(default=1, ge=1, le=45, strict=True)
    size: int = Field(default=15, ge=1, le=15, strict=True)


# 실제 검색에서 확인된 값만 채우고 가격·평점·사진·이동시간은 추정하지 않습니다.
class RestaurantCandidate(Coordinates):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    address: str = ""
    category: str = ""
    # search_restaurants는 카카오 음식점 그룹(FD6)만 요청합니다.
    category_group_code: str = "FD6"
    phone: str = ""
    place_url: str = ""
    distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    search_keyword: str
    source: str = "kakao"
    menu_verified: bool = False
    price: None = None
    rating: None = None
    image: None = None
    walk_minutes: None = None
    total_minutes: None = None


class KakaoClient:
    # 고정 URL과 허용 경로만 사용하여 이용자 입력으로 전송 대상이 바뀌지 않게 합니다.
    BASE_URL = "https://dapi.kakao.com/v2/local/"
    PATHS = frozenset({"search/address.json", "search/keyword.json", "geo/coord2address.json"})

    def __init__(self, api_key: SecretStr, *, transport: httpx.AsyncBaseTransport | None = None):
        # 모의 통신을 주입할 수 있게 하여 실제 키/네트워크 없이 테스트합니다.
        self._api_key = api_key
        self._transport = transport

    async def _request(self, path: str, params: dict) -> dict:
        # 리다이렉트를 따르지 않으며 키는 인증 헤더에만 넣습니다.
        if path not in self.PATHS:
            raise ValueError("지원하지 않는 카카오 API 경로입니다.")
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, transport=self._transport) as client:
                response = await client.get(self.BASE_URL + path, params=params,
                    headers={"Authorization": f"KakaoAK {self._api_key.get_secret_value()}"})
        except httpx.TimeoutException:
            raise KakaoError("kakao_timeout", "카카오 검색 시간이 초과되었습니다. 다시 시도해 주세요.", 504) from None
        except httpx.RequestError:
            raise KakaoError("kakao_connection", "카카오 서비스에 연결할 수 없습니다.") from None

        # 안전한 오류 종류만 분류하고 앱 이름/키 등이 포함될 수 있는 원문은 반환하지 않습니다.
        if response.status_code == 401:
            raise KakaoError("kakao_auth", "카카오 REST API 키 인증에 실패했습니다. 키와 서버 재시작 여부를 확인해 주세요.")
        if response.status_code == 403:
            try:
                error = response.json()
                message = error.get("message", "") if isinstance(error, dict) else ""
            except ValueError:
                message = ""
            if isinstance(message, str) and "disabled" in message and "OPEN_MAP_AND_LOCAL" in message:
                raise KakaoError("kakao_service_disabled", "해당 앱의 카카오맵·로컬 서비스가 비활성화되어 있습니다. 사용 설정과 권한을 확인해 주세요.")
            raise KakaoError("kakao_permission", "카카오 로컬 API 사용 권한이 없습니다. 앱과 키의 사용 가능 API 설정을 확인해 주세요.")
        if response.status_code == 429:
            raise KakaoError("kakao_rate_limit", "카카오 검색 사용 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.", 503)
        if response.status_code != 200:
            raise KakaoError("kakao_upstream", "카카오 서비스가 정상 응답하지 않았습니다.")

        # 비정상 응답이 성공/빈 결과로 오인되지 않도록 최상위 구조를 검증합니다.
        try:
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
                raise ValueError
            if not all(isinstance(item, dict) for item in data["documents"]):
                raise ValueError
            if "meta" in data and not isinstance(data["meta"], dict):
                raise ValueError
            return data
        except ValueError:
            raise KakaoError("kakao_response", "카카오 검색 응답 형식이 올바르지 않습니다.") from None

    async def search_addresses(self, query: str) -> dict:
        # 주소 검색 결과를 기존 위치 응답 형식으로 변환합니다.
        query = LocationSearchRequest(query=query).query
        data = await self._request("search/address.json", {"query": query, "size": 10})
        return self._locations(data, "address")

    async def search_places(self, query: str) -> dict:
        # 기준 위치 검색에는 음식점 분류 제한을 적용하지 않습니다.
        query = LocationSearchRequest(query=query).query
        data = await self._request("search/keyword.json", {"query": query, "size": 10})
        return self._locations(data, "place")

    @staticmethod
    def _locations(data: dict, source: str) -> dict:
        # x=경도, y=위도이며 누락·범위 밖 좌표가 있는 개별 결과는 제외합니다.
        items = []
        for item in data["documents"]:
            try:
                road = item.get("road_address") or {}
                if source == "address":
                    address = road.get("address_name") or item.get("address_name", "")
                    name = road.get("building_name") or address
                else:
                    address = item.get("road_address_name") or item.get("address_name", "")
                    name = item.get("place_name", "")
                result = SelectedLocation(name=name, address=address, lat=float(item["y"]), lng=float(item["x"]), source=source)
                items.append(result.model_dump())
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return {"items": items, "has_more": data.get("meta", {}).get("is_end", True) is False}

    async def reverse_address(self, lat: float, lng: float) -> dict:
        # 도로명 주소가 없으면 지번을 사용합니다. 주소가 없다는 결과는 정상 처리합니다.
        point = Coordinates(lat=lat, lng=lng)
        data = await self._request("geo/coord2address.json", {"x": point.lng, "y": point.lat, "input_coord": "WGS84"})
        if not data["documents"]:
            return {"address": None}
        item = data["documents"][0]
        try:
            address = (item.get("road_address") or {}).get("address_name") or (item.get("address") or {}).get("address_name")
        except AttributeError:
            raise KakaoError("kakao_response", "카카오 주소 응답 형식이 올바르지 않습니다.") from None
        if address is not None and not isinstance(address, str):
            raise KakaoError("kakao_response", "카카오 주소 응답 형식이 올바르지 않습니다.")
        return {"address": address or None}

    async def search_restaurants(self, menu_name: str, lat: float, lng: float, radius_m: int = 500, page: int = 1, size: int = 15) -> dict:
        # 4단계 저장 대상은 음식점(FD6)이며, 호출 한 번에 한 페이지만 조회합니다.
        query = LocationSearchRequest(query=menu_name).query
        search = RestaurantSearch(lat=lat, lng=lng, radius_m=radius_m, page=page, size=size)
        data = await self._request("search/keyword.json", {
            "query": query, "x": search.lng, "y": search.lat, "radius": search.radius_m,
            "category_group_code": "FD6", "sort": "distance", "page": search.page, "size": search.size,
        })
        items, seen = [], set()
        for item in data["documents"]:
            try:
                # 키워드 일치는 판매 메뉴 확인이 아닙니다. 가상 메뉴/가격을 실제 식당에 붙이지 않습니다.
                candidate = RestaurantCandidate(
                    id=item["id"], name=item["place_name"], lat=float(item["y"]), lng=float(item["x"]),
                    address=item.get("road_address_name") or item.get("address_name", ""),
                    category=item.get("category_name", ""), phone=item.get("phone", ""),
                    category_group_code=item.get("category_group_code") or "FD6",
                    place_url=item.get("place_url", ""),
                    distance_m=float(item["distance"]) if item.get("distance") not in (None, "") else None,
                    search_keyword=query,
                )
            except (ValidationError, ValueError, TypeError, KeyError):
                continue
            # API의 distance 표시값 대신 좌표로 직선거리를 재계산해 반경 밖 후보를 제외합니다.
            delta_lat = radians(candidate.lat - search.lat)
            delta_lng = radians(candidate.lng - search.lng)
            arc = sin(delta_lat / 2) ** 2 + cos(radians(search.lat)) * cos(radians(candidate.lat)) * sin(delta_lng / 2) ** 2
            distance = 2 * 6371008.8 * asin(sqrt(min(1.0, max(0.0, arc))))
            if distance > search.radius_m:
                continue
            candidate.distance_m = distance
            # 같은 식당이 중복 노출돼 추첨 확률이 달라지지 않도록 현재 페이지에서 ID로 중복 제거합니다.
            if candidate.id not in seen:
                seen.add(candidate.id)
                items.append(candidate.model_dump())
        return {"items": items, "page": search.page, "has_more": search.page < 45 and data.get("meta", {}).get("is_end", True) is False}
