"""6단계 메뉴·가격·현재 영업시간 추천 판정 테스트."""

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from app.database import BusinessHours, Database, Menu, PlaceDetailStatus, Restaurant
from app.recommendation_engine import filter_recommendation_candidates


KST = ZoneInfo("Asia/Seoul")


def database_for(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{(tmp_path / 'recommend.db').as_posix()}")
    database.init_schema()
    return database


def add_restaurant(
    session,
    restaurant_id: str,
    *,
    menu_status: str = "success",
    hours_status: str = "success",
    menu_name: str | None = "짜장면",
    price: int | None = 8000,
    price_status: str = "known",
    holiday: bool | None = False,
    start: time | None = time(11, 0),
    end: time | None = time(15, 0),
    next_day: bool = False,
    day: int = 0,
) -> Restaurant:
    """판정 조건만 다르게 주는 최소 식당 데이터를 만든다."""

    restaurant = Restaurant(
        id=restaurant_id,
        name=f"식당 {restaurant_id}",
        category_name="음식점 > 중식",
        cuisine="중식",
        latitude=37.48,
        longitude=126.88,
        address="서울 테스트로 1",
        place_url=f"https://place.map.kakao.com/{restaurant_id}",
        detail_status=PlaceDetailStatus(
            menu_status=menu_status,
            hours_status=hours_status,
        ),
    )
    if menu_name is not None:
        restaurant.menus.append(
            Menu(
                name=menu_name,
                normalized_name=menu_name,
                price=price,
                price_status=price_status,
            )
        )
    if hours_status == "success":
        restaurant.business_hours.append(
            BusinessHours(
                day_of_week=day,
                holiday=holiday,
                start_time=start,
                end_time=end,
                ends_next_day=next_day,
                sequence=0,
            )
        )
    session.add(restaurant)
    return restaurant


def raw(*ids: str) -> list[dict]:
    return [
        {
            "id": value,
            "name": f"검색 식당 {value}",
            "lat": 37.48,
            "lng": 126.88,
            "distance_m": 100,
            "search_keyword": "짜장면",
        }
        for value in ids
    ]


def test_budget_and_missing_information_are_separated(tmp_path: Path):
    database = database_for(tmp_path)
    try:
        with database.session_factory.begin() as session:
            add_restaurant(session, "1", price=8000)
            add_restaurant(session, "2", price=15000)
            add_restaurant(session, "3", price=None, price_status="missing")
            add_restaurant(session, "4", menu_status="missing", menu_name=None)
            add_restaurant(session, "5", hours_status="missing")

        with database.session_factory() as session:
            result = filter_recommendation_candidates(
                session,
                raw("1", "2", "3", "4", "5"),
                "짜장면",
                10000,
                now=datetime(2026, 11, 9, 12, 0, tzinfo=KST),
            )
        assert [item["id"] for item in result.restaurants] == ["1"]
        assert [item["id"] for item in result.candidate_restaurants] == ["3", "4"]
        assert result.candidate_restaurants[0]["warnings"] == ["가격 확인 불가"]
        assert result.candidate_restaurants[1]["warnings"] == [
            "메뉴 확인 불가",
            "가격 확인 불가",
        ]
        assert result.excluded == {"over_budget": 1, "hours_unavailable": 1}
    finally:
        database.dispose()


def test_break_time_holiday_and_overnight_are_calculated(tmp_path: Path):
    database = database_for(tmp_path)
    try:
        with database.session_factory.begin() as session:
            # 브레이크 타임 중간인 16시는 두 영업 구간 어디에도 포함되지 않는다.
            restaurant = add_restaurant(session, "10", start=time(11), end=time(15))
            restaurant.business_hours.append(
                BusinessHours(
                    day_of_week=0,
                    holiday=False,
                    start_time=time(17),
                    end_time=time(21),
                    sequence=1,
                )
            )
            # 월요일 18시부터 화요일 02시까지인 익일 영업 구간이다.
            add_restaurant(
                session,
                "11",
                start=time(18),
                end=time(2),
                next_day=True,
            )
            # 화요일 휴무가 명시되면 전날 심야 구간보다 휴무 판정을 우선한다.
            closed = add_restaurant(
                session,
                "12",
                start=time(18),
                end=time(2),
                next_day=True,
            )
            closed.business_hours.append(
                BusinessHours(day_of_week=1, holiday=True, sequence=0)
            )

        with database.session_factory() as session:
            break_result = filter_recommendation_candidates(
                session,
                raw("10"),
                "짜장면",
                10000,
                now=datetime(2026, 11, 9, 16, 0, tzinfo=KST),
            )
            overnight = filter_recommendation_candidates(
                session,
                raw("11", "12"),
                "짜장면",
                10000,
                now=datetime(2026, 11, 10, 1, 0, tzinfo=KST),
            )
        assert break_result.restaurants == []
        assert break_result.excluded == {"closed": 1}
        assert [item["id"] for item in overnight.restaurants] == ["11"]
        assert overnight.excluded == {"closed": 1}
    finally:
        database.dispose()


def test_unknown_holiday_is_recommended_with_warning(tmp_path: Path):
    database = database_for(tmp_path)
    try:
        with database.session_factory.begin() as session:
            add_restaurant(session, "20", holiday=None)
        with database.session_factory() as session:
            result = filter_recommendation_candidates(
                session,
                raw("20"),
                "짜장면",
                10000,
                now=datetime(2026, 11, 9, 12, 0, tzinfo=KST),
            )
        assert result.restaurants[0]["warnings"] == ["휴무일 확인 필요"]
    finally:
        database.dispose()
