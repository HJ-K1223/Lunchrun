"""Kakao restaurant mapping and idempotent persistence tests."""

from pathlib import Path

from sqlalchemy import func, select

from app.database import Database, PlaceDetailStatus, Restaurant
from app.repositories import save_restaurants


def candidate(**overrides):
    data = {
        "id": "kakao-100",
        "name": "테스트 식당",
        "lat": 37.48,
        "lng": 126.88,
        "address": "서울 테스트로 1",
        "category": "음식점 > 한식 > 찌개",
        "category_group_code": "FD6",
        "place_url": "https://place.map.kakao.com/100",
    }
    data.update(overrides)
    return data


def test_insert_and_update_by_kakao_id(tmp_path: Path):
    database = Database(f"sqlite:///{(tmp_path / 'restaurants.db').as_posix()}")
    database.init_schema()
    try:
        with database.session_factory() as session:
            first = save_restaurants(session, [candidate()])
            assert first.model_dump() == {
                "inserted": 1, "updated": 0, "skipped": 0, "failed": 0
            }

        with database.session_factory() as session:
            status = session.get(PlaceDetailStatus, "kakao-100")
            assert status is not None
            status.menu_status = "success"
            session.commit()

        with database.session_factory() as session:
            second = save_restaurants(
                session,
                [candidate(name="변경된 식당명", address="서울 변경로 2")],
            )
            assert second.updated == 1
            assert session.scalar(select(func.count()).select_from(Restaurant)) == 1
            stored = session.get(Restaurant, "kakao-100")
            assert stored is not None
            assert stored.name == "변경된 식당명"
            assert stored.address == "서울 변경로 2"
            assert stored.category_name == "음식점 > 한식 > 찌개"
            assert stored.cuisine == "한식"
            assert stored.detail_status.menu_status == "success"
    finally:
        database.dispose()


def test_bad_and_non_food_records_do_not_cancel_valid_record(tmp_path: Path):
    database = Database(f"sqlite:///{(tmp_path / 'partial.db').as_posix()}")
    database.init_schema()
    try:
        with database.session_factory() as session:
            result = save_restaurants(
                session,
                [
                    candidate(id=""),
                    candidate(id="not-food", category_group_code="CE7"),
                    candidate(id="valid"),
                ],
            )
            assert result.model_dump() == {
                "inserted": 1, "updated": 0, "skipped": 1, "failed": 1
            }
            assert session.get(Restaurant, "valid") is not None
            assert session.get(Restaurant, "not-food") is None
    finally:
        database.dispose()
