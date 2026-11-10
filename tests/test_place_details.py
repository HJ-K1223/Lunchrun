"""Kakao detail parsing, persistence, and failure-preservation tests."""

import asyncio
from datetime import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.collectors import (
    PlaceCollectionError,
    RawBusinessDay,
    RawMenu,
    RawPlacePage,
    validate_place_url,
)
from app.database import BusinessHours, Database, Menu, PlaceDetailStatus, Restaurant
from app.detail_collection import (
    collect_restaurant_details,
    collect_restaurant_details_batch,
)
from app.place_details import NormalizedPlaceDetails, parse_place_page
from app.repositories import record_place_detail_failure, save_place_details


def raw_page() -> RawPlacePage:
    return RawPlacePage(
        menu_section_present=True,
        hours_section_present=True,
        menus=(
            RawMenu(" 김치 찌개 ", "9,000원"),
            RawMenu("제철회", "시가"),
            RawMenu("음료", "가격 정보 없음"),
        ),
        business_days=(
            RawBusinessDay(
                "월(11/9)",
                (
                    "11:00 ~ 21:30",
                    "15:00 ~ 17:00 브레이크타임",
                    "14:45 라스트오더",
                    "20:45 라스트오더",
                ),
            ),
            RawBusinessDay("화(11/10)", ("매주 화요일 휴무",)),
            RawBusinessDay("금(11/13)", ("18:00 ~ 02:00", "01:30 라스트오더")),
        ),
    )


def make_database(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{(tmp_path / 'details.db').as_posix()}")
    database.init_schema()
    with database.session_factory.begin() as session:
        session.add(
            Restaurant(
                id="123",
                name="상세 테스트 식당",
                category_name="음식점 > 한식",
                cuisine="한식",
                latitude=37.48,
                longitude=126.88,
                address="서울 테스트로 1",
                place_url="https://place.map.kakao.com/123",
                detail_status=PlaceDetailStatus(),
            )
        )
    return database


def test_place_url_is_canonical_and_rejects_other_destinations():
    assert (
        validate_place_url("123", "http://place.map.kakao.com/123")
        == "https://place.map.kakao.com/123"
    )
    for url in (
        "https://example.com/123",
        "https://place.map.kakao.com/456",
        "https://place.map.kakao.com/123/extra",
        "https://user@place.map.kakao.com/123",
        "https://place.map.kakao.com:invalid/123",
    ):
        with pytest.raises(PlaceCollectionError) as captured:
            validate_place_url("123", url)
        assert captured.value.code == "invalid_place_url"


def test_menu_price_and_business_hour_normalization():
    details = parse_place_page(raw_page())
    assert details.menu_status == "success"
    assert [(item.name, item.price, item.price_status) for item in details.menus] == [
        ("김치 찌개", 9000, "known"),
        ("제철회", None, "variable"),
        ("음료", None, "missing"),
    ]
    assert details.hours_status == "success"
    monday = [item for item in details.business_hours if item.day_of_week == 0]
    assert [(item.start_time, item.end_time) for item in monday] == [
        (time(11, 0), time(15, 0)),
        (time(17, 0), time(21, 30)),
    ]
    assert [item.last_order_time for item in monday] == [time(14, 45), time(20, 45)]
    holiday = next(item for item in details.business_hours if item.day_of_week == 1)
    assert holiday.holiday is True
    overnight = next(item for item in details.business_hours if item.day_of_week == 4)
    assert (overnight.start_time, overnight.end_time, overnight.ends_next_day) == (
        time(18, 0),
        time(2, 0),
        True,
    )


def test_save_replaces_success_but_failure_preserves_existing_data(tmp_path: Path):
    database = make_database(tmp_path)
    try:
        with database.session_factory() as session:
            result = save_place_details(session, "123", parse_place_page(raw_page()))
            assert result["menus"] == 3
            assert result["business_hours"] == 4

        failed = NormalizedPlaceDetails(
            menus=(),
            business_hours=(),
            menu_status="parse_failed",
            hours_status="parse_failed",
            error_code="page_structure",
        )
        with database.session_factory() as session:
            save_place_details(session, "123", failed)
            assert session.scalar(select(func.count()).select_from(Menu)) == 3
            assert session.scalar(select(func.count()).select_from(BusinessHours)) == 4

        with database.session_factory() as session:
            record_place_detail_failure(
                session, "123", "fetch_failed", "page_timeout"
            )
            status = session.get(PlaceDetailStatus, "123")
            assert status.menu_status == "fetch_failed"
            assert status.hours_status == "fetch_failed"
            assert status.error_code == "page_timeout"
            assert session.scalar(select(func.count()).select_from(Menu)) == 3
    finally:
        database.dispose()


class FakeCollector:
    async def collect(self, kakao_id: str, place_url: str) -> RawPlacePage:
        assert (kakao_id, place_url) == ("123", "https://place.map.kakao.com/123")
        return raw_page()


def test_collection_service_reads_then_persists(tmp_path: Path):
    database = make_database(tmp_path)
    try:
        result = asyncio.run(
            collect_restaurant_details(database, "123", FakeCollector())
        )
        assert result["menu_status"] == "success"
        assert result["hours_status"] == "success"
        with database.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Menu)) == 3
    finally:
        database.dispose()


class CountingCollector:
    def __init__(self):
        self.ids = []

    async def collect(self, kakao_id: str, place_url: str) -> RawPlacePage:
        # 배치 제한이 검색 순서대로 적용되는지만 확인하고 외부 페이지는 열지 않는다.
        self.ids.append(kakao_id)
        assert place_url == f"https://place.map.kakao.com/{kakao_id}"
        return raw_page()


def test_collection_batch_limits_new_restaurants(tmp_path: Path):
    database = make_database(tmp_path)
    collector = CountingCollector()
    try:
        with database.session_factory.begin() as session:
            for restaurant_id in ("124", "125", "126"):
                session.add(
                    Restaurant(
                        id=restaurant_id,
                        name=f"식당 {restaurant_id}",
                        latitude=37.48,
                        longitude=126.88,
                        address="서울 테스트로 1",
                        place_url=f"https://place.map.kakao.com/{restaurant_id}",
                        detail_status=PlaceDetailStatus(),
                    )
                )
        results = asyncio.run(
            collect_restaurant_details_batch(
                database,
                ["123", "124", "125", "126"],
                limit=2,
                collector=collector,
            )
        )
        assert collector.ids == ["123", "124"]
        assert len(results) == 2
    finally:
        database.dispose()
