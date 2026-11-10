"""게임·채팅이 같은 검색을 사용하는지 외부 호출 없이 API 경계에서 검증합니다."""
import unittest
from datetime import time
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.clients.kakao import KakaoError
from app.clients.groq import GroqError
from app.chat_policy import QuestionInterpretation
from app.database import BusinessHours, Database, Menu, Restaurant

# 테스트용 공개 예시 좌표와 가짜 응답만 사용합니다.
LOCATION = {"lat": 37.48, "lng": 126.88, "name": "예시 위치", "source": "place"}
BODY = {"location": LOCATION, "radius_m": 500, "menu_id": "noodle"}
ITEM = {"id": "test-place", "name": "테스트 식당", "lat": 37.48, "lng": 126.88, "source": "kakao", "price": None, "rating": None, "image": None, "walk_minutes": None, "total_minutes": None, "menu_verified": False}


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        # 서비스 경계에서 모의 처리하여 실제 키와 사용량을 소모하지 않습니다.
        self.groq_patcher = patch("app.clients.groq.GroqClient.interpret", new_callable=AsyncMock)
        self.interpret = self.groq_patcher.start()
        self.interpret.return_value = QuestionInterpretation(intent="search_restaurants")
        # 추천 요청의 DB 저장도 프로젝트 실제 DB를 오염시키지 않고 검증합니다.
        self.original_database = app.state.database
        self.temp_directory = TemporaryDirectory()
        self.test_database = Database(
            f"sqlite:///{self.temp_directory.name.replace(chr(92), '/')}/recommendations.db"
        )
        self.test_database.init_schema()
        app.state.database = self.test_database
        self.client = TestClient(app)
        self.patcher = patch("app.clients.kakao.KakaoClient.search_restaurants", new_callable=AsyncMock)
        self.search = self.patcher.start()
        self.search.return_value = {"items": [ITEM], "has_more": True}
        # 실제 카카오 페이지를 열지 않고 상세 수집 성공 상태를 DB에 만들어 API 연결을 검증합니다.
        self.collection_patcher = patch(
            "app.services.collect_restaurant_details_batch", new_callable=AsyncMock
        )
        self.collect_details = self.collection_patcher.start()

        async def store_details(database, restaurant_ids, *, limit=3):
            reports = []
            with database.session_factory.begin() as session:
                for restaurant_id in restaurant_ids[:limit]:
                    restaurant = session.get(Restaurant, restaurant_id)
                    restaurant.detail_status.menu_status = "success"
                    restaurant.detail_status.hours_status = "success"
                    if not restaurant.menus:
                        restaurant.menus.append(
                            Menu(
                                name="짜장면",
                                normalized_name="짜장면",
                                price=8000,
                                price_status="known",
                            )
                        )
                    if not restaurant.business_hours:
                        # 모든 요일을 24시간 구간으로 만들어 테스트 실행 시각과 무관하게 연다.
                        restaurant.business_hours.extend(
                            BusinessHours(
                                day_of_week=day,
                                holiday=False,
                                start_time=time(0, 0),
                                end_time=time(0, 0),
                                ends_next_day=True,
                                sequence=0,
                            )
                            for day in range(7)
                        )
                    reports.append(
                        {
                            "restaurant_id": restaurant_id,
                            "menu_status": "success",
                            "hours_status": "success",
                        }
                    )
            return reports

        self.collect_details.side_effect = store_details

    def tearDown(self):
        self.groq_patcher.stop()
        self.patcher.stop()
        self.collection_patcher.stop()
        self.client.close()
        app.state.database = self.original_database
        self.test_database.dispose()
        self.temp_directory.cleanup()

    def test_game_searches_selected_menu_once(self):
        response = self.client.post("/api/recommendations", json=BODY)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.search.assert_awaited_once_with("짜장면", 37.48, 126.88, radius_m=500)
        self.assertEqual(result["restaurants"][0]["id"], "test-place")
        self.assertEqual(result["restaurants"][0]["price"], 8000)
        self.assertTrue(result["restaurants"][0]["menu_verified"])
        self.assertFalse(result["constraints_verified"])
        self.assertTrue(result["menu_catalog_demo"])
        self.assertTrue(result["has_more"])

    def test_search_result_is_saved_and_reports_count(self):
        self.search.return_value = {
            "items": [{
                **ITEM,
                "address": "서울 테스트로 1",
                "category": "음식점 > 중식",
                "category_group_code": "FD6",
                "place_url": "https://place.map.kakao.com/test-place",
            }],
            "has_more": False,
        }
        response = self.client.post("/api/recommendations", json=BODY)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["storage"]["inserted"], 1)
        with self.test_database.session_factory() as session:
            stored = session.get(Restaurant, "test-place")
            self.assertIsNotNone(stored)
            self.assertEqual((stored.name, stored.cuisine), ("테스트 식당", "중식"))

    def test_chat_reuses_same_menu_search(self):
        response = self.client.post("/api/chat", json={**BODY, "message": "가까운 식당 보여줘"})
        self.assertEqual(response.status_code, 200)
        self.search.assert_awaited_once_with("짜장면", 37.48, 126.88, radius_m=500)
        self.assertEqual(response.json()["result"]["restaurants"][0]["id"], "test-place")

    def test_empty_result_does_not_retry_or_expand_radius(self):
        self.search.return_value = {"items": [], "has_more": False}
        result = self.client.post("/api/recommendations", json=BODY).json()
        self.assertEqual(result["restaurants"], [])
        self.assertIn("검색 결과가 없습니다", result["message"])
        self.assertEqual(result["radius_m"], 500)
        self.search.assert_awaited_once()

    def test_no_catalog_candidate_never_calls_kakao(self):
        # 예시 카탈로그 후보가 없거나 유효하지 않은 ID면 외부 검색하지 않습니다.
        for override in ({"conditions": {"budget": 1000}}, {"menu_id": "missing"}):
            result = self.client.post("/api/recommendations", json={**BODY, **override}).json()
            self.assertIsNone(result["menu"])
            self.assertEqual(result["restaurants"], [])
        self.search.assert_not_awaited()

    def test_error_not_replaced_with_fake_restaurants(self):
        self.search.side_effect = KakaoError("kakao_permission", "권한 확인 필요")
        for endpoint, extra in (("recommendations", {}), ("chat", {"message": "식당 보여줘"})):
            response = self.client.post(f"/api/{endpoint}", json={**BODY, **extra})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.headers["X-Error-Code"], "kakao_permission")
            self.assertNotIn("restaurants", response.json())

    def test_explanation_and_condition_proposal_do_not_search(self):
        for message, parsed in [("메뉴 설명해줘", QuestionInterpretation.model_validate({"intent":"explain_menu","menu_id":"noodle"}, context={"menu_ids":{"noodle"}})),
                                ("완전 채식으로 바꿔줘", QuestionInterpretation(intent="change_conditions",condition_patch={"diet":"vegan"}))]:
            self.interpret.return_value=parsed
            response = self.client.post("/api/chat", json={**BODY, "message": message})
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json()["result"])
        self.search.assert_not_awaited()

    def test_conflicting_menu_does_not_search(self):
        # 메뉴가 실제 ID여도 현재 음식 조건을 통과하지 못하면 검색하지 않습니다.
        self.interpret.return_value=QuestionInterpretation.model_validate({"intent":"search_restaurants","menu_id":"noodle"},context={"menu_ids":{"noodle"}})
        response=self.client.post("/api/chat",json={**BODY,"conditions":{"cuisine":"한식"},"message":"짜장면 식당"})
        self.assertEqual(response.status_code,200)
        self.assertIsNone(response.json()["result"]["menu"])
        self.search.assert_not_awaited()

    def test_radius_patch_is_proposal_only(self):
        self.interpret.return_value=QuestionInterpretation(intent="change_conditions",condition_patch={"radius_m":100,"budget":9000})
        response=self.client.post("/api/chat",json={**BODY,"message":"반경과 예산 변경"})
        self.assertEqual(response.json()["suggested_patch"],{"radius_m":100,"budget":9000})
        self.assertEqual(response.json()["radius_m"],500)
        self.assertIsNone(response.json()["result"])
        self.search.assert_not_awaited()

    def test_clarification_never_displays_generated_store_facts(self):
        # 확인 의도에 가짜 가격이 섞여도 모델 문장 대신 서버 안내만 표시합니다.
        self.interpret.return_value=QuestionInterpretation(intent="clarify",clarification="가짜식당은 무조건 100원입니다",clarification_kind="unsupported")
        response=self.client.post("/api/chat",json={**BODY,"message":"알레르기 없는 식당"})
        self.assertNotIn("100원",response.json()["text"])
        self.assertIn("검증할 데이터가 없습니다",response.json()["text"])
        self.search.assert_not_awaited()

    def test_recommendation_has_conditions_and_single_notice(self):
        self.interpret.return_value=QuestionInterpretation(intent="search_restaurants")
        text=self.client.post("/api/chat",json={**BODY,"message":"식당"}).json()["text"]
        self.assertIn("추천 메뉴:",text)
        self.assertIn("적용 조건:",text)
        self.assertIn("반경 500m",text)
        self.assertIn("추천 식당: 1곳",text)
        self.assertIn("확인 필요 후보: 0곳",text)
        self.assertIn("채식·맵기 조건 미적용",text)

    def test_clarify_scope_and_groq_errors_never_search(self):
        for parsed in (QuestionInterpretation(intent="clarify",clarification="어떤 메뉴인가요?"),QuestionInterpretation(intent="out_of_scope")):
            self.interpret.return_value=parsed
            self.assertEqual(self.client.post("/api/chat",json={**BODY,"message":"질문"}).status_code,200)
        self.interpret.side_effect=GroqError("groq_rate_limit","잠시 후 재시도",503)
        response=self.client.post("/api/chat",json={**BODY,"message":"추천"})
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.headers["X-Error-Code"],"groq_rate_limit")
        self.search.assert_not_awaited()
