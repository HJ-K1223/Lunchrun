"""SQLite schema, foreign-key enforcement, and cascade behavior."""

from datetime import time
from pathlib import Path

from sqlalchemy import inspect, select, text

from app.database import BusinessHours, Database, Menu, PlaceDetailStatus, Restaurant


def test_sqlite_schema_and_foreign_key_cascade(tmp_path: Path):
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    try:
        database.init_schema()
        inspector = inspect(database.engine)
        assert set(inspector.get_table_names()) == {
            "business_hours",
            "menu",
            "place_detail_status",
            "restaurant",
        }

        with database.engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"

        with database.session_factory.begin() as session:
            restaurant = Restaurant(
                id="test-kakao-id",
                name="테스트 식당",
                category_name="음식점 > 한식",
                cuisine="한식",
                latitude=37.48,
                longitude=126.88,
                address="서울 테스트로 1",
                place_url="https://place.map.kakao.com/test-kakao-id",
            )
            restaurant.menus.append(
                Menu(
                    name="김치찌개",
                    normalized_name="김치찌개",
                    price=9000,
                    price_status="known",
                )
            )
            restaurant.business_hours.append(
                BusinessHours(
                    day_of_week=0,
                    holiday=False,
                    start_time=time(11, 0),
                    end_time=time(15, 0),
                    sequence=0,
                )
            )
            restaurant.detail_status = PlaceDetailStatus(
                menu_status="success", hours_status="success"
            )
            session.add(restaurant)

        with database.session_factory.begin() as session:
            stored = session.get(Restaurant, "test-kakao-id")
            assert stored is not None
            session.delete(stored)

        with database.session_factory() as session:
            assert session.scalar(select(Menu.id)) is None
            assert session.scalar(select(BusinessHours.id)) is None
            assert session.scalar(select(PlaceDetailStatus.restaurant_id)) is None
    finally:
        database.dispose()
