"""공개 예시 좌표와 모의 응답만 사용합니다. 실제 키/위치를 출력하거나 외부 요청을 하지 않습니다."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from app.main import app
from app.config import Settings
from app.chat_policy import QuestionInterpretation

# 모의 전송을 주입할 때 원본 생성자를 보관해 재귀 호출을 방지합니다.
OriginalAsyncClient = httpx.AsyncClient
LOCATION = {"lat": 37.48, "lng": 126.88, "name": "공개 예시 위치", "source": "place"}


class LocationTests(unittest.TestCase):
    def test_radius_contract_boundaries(self):
        # 최소·최대와 1m 단위 정수를 허용하고 소수·문자열·범위 밖은 거부합니다.
        from app.schemas import RecommendationRequest, ChatRequest
        from pydantic import ValidationError
        for schema in (RecommendationRequest, ChatRequest):
            extra = {"message": "추천"} if schema is ChatRequest else {}
            for radius in (50, 51, 499, 500):
                self.assertEqual(schema(location=LOCATION, radius_m=radius, **extra).radius_m, radius)
            for radius in (49, 501, 50.5, "100", True):
                with self.assertRaises(ValidationError):
                    schema(location=LOCATION, radius_m=radius, **extra)

    # 테스트에서는 실제 환경의 키 대신 가짜 키를 사용하고 종료 시 기존 설정을 복원합니다.
    def setUp(self):
        self.original_settings = app.state.settings
        app.state.settings = Settings(map_api_key="fake-rest-key")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.state.settings = self.original_settings

    def test_location_required_and_coordinate_bounds(self):
        # 위치 누락, 뒤바뀐 위도, 음수 반경, 허용되지 않은 출처를 모두 거부해야 합니다.
        for body in ({}, {"location": {**LOCATION, "lat": 126.88}},
                     {"location": LOCATION, "radius_m": -1},
                     {"location": {**LOCATION, "source": "unknown"}}):
            self.assertEqual(self.client.post("/api/recommendations", json=body).status_code, 422)

    def test_location_received_by_game_and_chat(self):
        # 두 경로가 같은 좌표를 수신하는지 확인하되 외부 식당 검색은 모의 처리합니다.
        with patch("app.clients.groq.GroqClient.interpret", AsyncMock(return_value=QuestionInterpretation(intent="recommend_menu"))), patch("app.clients.kakao.KakaoClient.search_restaurants", AsyncMock(return_value={"items": [], "has_more": False})):
            for endpoint, extra in (("recommendations", {}), ("chat", {"message": "추천해줘"})):
                response = self.client.post(f"/api/{endpoint}", json={"location": LOCATION, "radius_m": 500, **extra})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["location"]["lat"], 37.48)
                self.assertEqual(response.json()["radius_m"], 500)

    def test_search_maps_xy_and_escapes_no_credentials(self):
        # 키워드 검색은 식당 분류로 제한하지 않으며 x=경도/y=위도 순서를 보존합니다.
        data = {"documents": [{"place_name": "가산 예시", "address_name": "예시 지번", "road_address_name": "예시 도로", "x": "126.88", "y": "37.48"}], "meta": {"is_end": True}}
        # 공통 클라이언트의 통신 경계만 모의 처리하여 기존 라우터 응답을 검증합니다.
        with patch("app.clients.kakao.KakaoClient._request", AsyncMock(return_value=data)) as request:
            response = self.client.post("/api/locations/search", json={"query": " 가산 ", "kind": "place"})
            self.assertEqual(response.status_code, 200)
            item = response.json()["items"][0]
            self.assertEqual((item["lat"], item["lng"], item["source"]), (37.48, 126.88, "place"))
            self.assertEqual(request.call_args.args[1]["query"], "가산")
            self.assertNotIn("fake-rest-key", response.text)

    def test_address_road_preferred_and_empty_results(self):
        # 주소 검색은 도로명과 건물명을 우선 사용하고, 결과 없음은 정상 응답입니다.
        data = {"documents": [{"address_name": "예시 지번", "road_address": {"address_name": "예시 도로", "building_name": "예시 건물"}, "x": "126.88", "y": "37.48"}]}
        with patch("app.clients.kakao.KakaoClient._request", AsyncMock(return_value=data)):
            item = self.client.post("/api/locations/search", json={"query": "주소", "kind": "address"}).json()["items"][0]
            self.assertEqual((item["name"], item["address"]), ("예시 건물", "예시 도로"))
        with patch("app.clients.kakao.KakaoClient._request", AsyncMock(return_value={"documents": []})):
            self.assertEqual(self.client.post("/api/locations/search", json={"query": "없는 장소"}).json()["items"], [])
            self.assertIsNone(self.client.post("/api/locations/reverse", json={"lat": 37.48, "lng": 126.88}).json()["address"])

    def test_reverse_uses_longitude_for_x(self):
        # 도로명이 없는 위치에서는 지번을 반환하고 외부 파라미터 순서도 검사합니다.
        data = {"documents": [{"road_address": None, "address": {"address_name": "예시 지번"}}]}
        with patch("app.clients.kakao.KakaoClient._request", AsyncMock(return_value=data)) as request:
            response = self.client.post("/api/locations/reverse", json={"lat": 37.48, "lng": 126.88})
            self.assertEqual(response.json()["address"], "예시 지번")
            self.assertEqual(request.call_args.args[1]["x"], 126.88)

    def test_blank_query_rejected_without_external_call(self):
        # 빈 검색은 외부 API 사용량을 소모하지 않아야 합니다.
        with patch("app.clients.kakao.KakaoClient._request", AsyncMock()) as request:
            self.assertEqual(self.client.post("/api/locations/search", json={"query": "   "}).status_code, 422)
            request.assert_not_called()

    def test_upstream_errors_sanitized(self):
        # 외부 오류 본문/인증 헤더가 프론트엔드로 노출되지 않는지 검사합니다.
        for status, expected in ((401, 502), (403, 502), (429, 503), (500, 502)):
            def handler(request):
                self.assertEqual(request.headers["Authorization"], "KakaoAK fake-rest-key")
                return httpx.Response(status, json={"message": "private upstream details"})
            def factory(**kwargs):
                # 클라이언트가 전달한 선택적 transport를 테스트용 모의 통신으로 교체합니다.
                kwargs["transport"] = httpx.MockTransport(handler)
                return OriginalAsyncClient(**kwargs)
            with patch("app.clients.kakao.httpx.AsyncClient", side_effect=factory):
                response = self.client.post("/api/locations/search", json={"query": "가산"})
            self.assertEqual(response.status_code, expected)
            # 401과 403을 구분하는 진단 코드가 기존 문자열 응답과 함께 제공됩니다.
            if status in (401, 403):
                self.assertEqual(response.headers["X-Error-Code"], "kakao_auth" if status == 401 else "kakao_permission")
            self.assertNotIn("private upstream details", response.text)
            self.assertNotIn("fake-rest-key", response.text)

    def test_timeout_has_specific_message(self):
        # 위치 서비스 시간 초과는 서버 장애와 구분합니다.
        def handler(request):
            raise httpx.ReadTimeout("private request information", request=request)
        def factory(**kwargs):
            # 모의 타임아웃을 주입하되 실제 HTTPS 요청은 보내지 않습니다.
            kwargs["transport"] = httpx.MockTransport(handler)
            return OriginalAsyncClient(**kwargs)
        with patch("app.clients.kakao.httpx.AsyncClient", side_effect=factory):
            response = self.client.post("/api/locations/search", json={"query": "가산"})
        self.assertEqual(response.status_code, 504)
        self.assertNotIn("private request information", response.text)
