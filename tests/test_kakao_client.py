"""공개 예시 좌표와 모의 HTTP 응답으로 카카오 클라이언트를 검증합니다."""
import unittest
import httpx
from pydantic import SecretStr, ValidationError
from app.clients.kakao import KakaoClient, KakaoError

# 실제 앱 정보와 무관한 테스트용 장소입니다. 키는 모의 인증 헤더 검증에만 사용합니다.
PLACE = {"id": "123", "place_name": "테스트 식당", "x": "126.88", "y": "37.48", "distance": "120", "category_name": "음식점 > 한식"}
KEY = SecretStr("fake-test-key")


class KakaoClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_radius_rechecked_from_coordinates(self):
        # API가 잘못된 거리값을 반환해도 약 56m 후보만 남기고 약 111m 후보는 제외합니다.
        documents = [{**PLACE, "id": "near", "y": "37.4805", "distance": "9999"},
                     {**PLACE, "id": "far", "y": "37.481", "distance": "1"}]
        client = KakaoClient(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"documents": documents})))
        result = await client.search_restaurants("메뉴", 37.48, 126.88, 100)
        self.assertEqual([p["id"] for p in result["items"]], ["near"])
        self.assertAlmostEqual(result["items"][0]["distance_m"], 55.6, places=1)

    async def test_restaurant_parameters_and_missing_data(self):
        # 한 번의 호출만 발생하고 좌표/반경/페이지/분류/정렬이 정확하게 전달되는지 확인합니다.
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"documents": [PLACE, PLACE], "meta": {"is_end": False}})
        client = KakaoClient(KEY, transport=httpx.MockTransport(handler))
        result = await client.search_restaurants(" 김치찌개 ", 37.48, 126.88, 500, 2, 4)
        self.assertEqual(len(calls), 1)
        self.assertEqual(dict(calls[0].url.params), {"query": "김치찌개", "x": "126.88", "y": "37.48", "radius": "500", "category_group_code": "FD6", "sort": "distance", "page": "2", "size": "4"})
        self.assertEqual(calls[0].headers["Authorization"], "KakaoAK fake-test-key")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual((item["lat"], item["lng"], item["distance_m"]), (37.48, 126.88, 0.0))
        for field in ("price", "rating", "image", "walk_minutes", "total_minutes"):
            self.assertIsNone(item[field])
        self.assertFalse(item["menu_verified"])
        self.assertTrue(result["has_more"])

    async def test_place_search_not_restricted_to_restaurants(self):
        # 역/건물 등 기준 위치 검색에 음식점 필터가 섞이지 않아야 합니다.
        def handler(request):
            self.assertNotIn("category_group_code", request.url.params)
            return httpx.Response(200, json={"documents": [PLACE], "meta": {"is_end": True}})
        client = KakaoClient(KEY, transport=httpx.MockTransport(handler))
        result = await client.search_places("가산역")
        self.assertEqual(result["items"][0]["source"], "place")

    async def test_invalid_search_never_calls_upstream(self):
        # 좌표/페이지/반경 오류는 외부 사용량을 소모하기 전에 거부합니다.
        def handler(request):
            self.fail("invalid input reached upstream")
        client = KakaoClient(KEY, transport=httpx.MockTransport(handler))
        for args in (("메뉴", 126.88, 37.48), ("메뉴", 37.48, 126.88, 20001), ("메뉴", 37.48, 126.88, 1000, 46), ("   ", 37.48, 126.88)):
            with self.assertRaises(ValidationError):
                await client.search_restaurants(*args)

    async def test_empty_results_and_invalid_records(self):
        # 일부 비정상 장소가 있더라도 잘못된 좌표를 목록에 넣지 않습니다.
        for documents in ([], [{**PLACE, "y": "126.88"}], [{**PLACE, "distance": "nan"}]):
            client = KakaoClient(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"documents": documents})))
            self.assertEqual((await client.search_restaurants("메뉴", 37.48, 126.88))["items"], [])

    async def test_auth_permission_and_disabled_classification(self):
        # 비활성화 오류에서 앱 이름과 외부 원문이 공개 오류에 포함되지 않아야 합니다.
        cases = [(401, {}, "kakao_auth"), (403, {}, "kakao_permission"),
                 (403, {"message": "App(private-name) disabled OPEN_MAP_AND_LOCAL service."}, "kakao_service_disabled")]
        for status, payload, code in cases:
            client = KakaoClient(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))
            with self.assertRaises(KakaoError) as captured:
                await client.search_places("가산역")
            self.assertEqual(captured.exception.code, code)
            self.assertNotIn("private-name", str(captured.exception))
            self.assertNotIn(KEY.get_secret_value(), str(captured.exception))

    async def test_malformed_response_is_not_empty_success(self):
        # 문서 목록이 아닌 응답이나 잘못된 메타데이터는 통신 실패로 분류합니다.
        for payload in ({}, {"documents": [None]}, {"documents": [], "meta": []}):
            client = KakaoClient(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
            with self.assertRaises(KakaoError) as captured:
                await client.search_places("가산역")
            self.assertEqual(captured.exception.code, "kakao_response")

    async def test_last_page_does_not_offer_page_46(self):
        # 외부 응답이 다음 페이지가 있다고 해도 API 페이지 상한을 넘기지 않습니다.
        client = KakaoClient(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"documents": [], "meta": {"is_end": False}})))
        result = await client.search_restaurants("메뉴", 37.48, 126.88, page=45)
        self.assertFalse(result["has_more"])
