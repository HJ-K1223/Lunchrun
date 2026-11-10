"""2단계: 카카오 식당과 상세페이지 수집 결과를 저장하는 SQLAlchemy 모델."""

from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Time,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# SQLite에서 시작한 뒤 PostgreSQL로 옮겨도 제약조건 이름이 일정하도록 규칙을 고정한다.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """모든 ORM 테이블이 공유하는 SQLAlchemy 선언형 기반 클래스."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Restaurant(Base):
    """카카오 장소 검색 API가 직접 제공하는 식당 기본정보."""

    __tablename__ = "restaurant"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        Index("ix_restaurant_location", "latitude", "longitude"),
        Index("ix_restaurant_cuisine", "cuisine"),
    )

    # 숫자로만 보여도 카카오 API 계약상 문자열이므로 문자열 PK로 보관한다.
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # category_name은 카카오 원문, cuisine은 검색에 쓸 첫 번째 음식 분류다.
    cuisine: Mapped[str | None] = mapped_column(String(30), nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    place_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    # 식당 삭제 시 그 식당에서 수집한 메뉴와 영업시간도 함께 제거한다.
    menus: Mapped[list[Menu]] = relationship(
        back_populates="restaurant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    business_hours: Mapped[list[BusinessHours]] = relationship(
        back_populates="restaurant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # 식당당 상태 행은 최대 하나이므로 1:0..1 관계로 구성한다.
    detail_status: Mapped[PlaceDetailStatus | None] = relationship(
        back_populates="restaurant",
        cascade="all, delete-orphan",
        passive_deletes=True,
        single_parent=True,
        uselist=False,
    )


class Menu(Base):
    """카카오 상세페이지에서 확인한 메뉴 한 건과 연결된 가격."""

    __tablename__ = "menu"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "normalized_name", name="restaurant_name"),
        CheckConstraint("price IS NULL OR price >= 0", name="price_nonnegative"),
        CheckConstraint(
            "price_status IN ('known', 'missing', 'variable')",
            name="price_status_value",
        ),
        Index("ix_menu_lookup", "normalized_name", "price"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[str] = mapped_column(
        ForeignKey("restaurant.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 공백과 대소문자를 정리한 이름으로 같은 식당의 중복 메뉴를 막는다.
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 가격이 없거나 시가/변동이면 NULL로 두고 price_status로 이유를 구분한다.
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="missing", server_default="missing"
    )

    restaurant: Mapped[Restaurant] = relationship(back_populates="menus")


class BusinessHours(Base):
    """요일별 실제 영업 구간 한 건.

    브레이크 타임 전후는 같은 요일의 서로 다른 행으로 저장한다.
    월요일은 0, 일요일은 6이며, 영업시간 전체가 없으면 행을 만들지 않고
    PlaceDetailStatus.hours_status에 누락 상태를 기록한다.
    """

    __tablename__ = "business_hours"
    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "day_of_week", "sequence", name="restaurant_day_sequence"
        ),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="day_range"),
        CheckConstraint("sequence >= 0", name="sequence_nonnegative"),
        CheckConstraint(
            "(start_time IS NULL AND end_time IS NULL) OR "
            "(start_time IS NOT NULL AND end_time IS NOT NULL)",
            name="time_pair",
        ),
        CheckConstraint(
            "holiday IS NOT TRUE OR (start_time IS NULL AND end_time IS NULL)",
            name="holiday_without_hours",
        ),
        Index("ix_business_hours_lookup", "restaurant_id", "day_of_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[str] = mapped_column(
        ForeignKey("restaurant.id", ondelete="CASCADE"), nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    # True=휴무 확인, False=영업일 확인, None=휴무 여부 미확인.
    holiday: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # 종료 시각이 자정을 넘는 경우 같은 시각이라도 다음 날임을 표시한다.
    ends_next_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    last_order_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # 한 요일에 여러 구간이 있으면 0, 1, ... 순서로 구분한다.
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    restaurant: Mapped[Restaurant] = relationship(back_populates="business_hours")


class PlaceDetailStatus(Base):
    """식당별 상세페이지 메뉴·영업시간 수집 상태.

    restaurant_id가 PK이자 FK이므로 식당 하나에 상태 행이 하나만 생긴다.
    """

    __tablename__ = "place_detail_status"
    __table_args__ = (
        CheckConstraint(
            "menu_status IN ('pending', 'success', 'missing', 'fetch_failed', 'parse_failed')",
            name="menu_status_value",
        ),
        CheckConstraint(
            "hours_status IN ('pending', 'success', 'missing', 'fetch_failed', 'parse_failed')",
            name="hours_status_value",
        ),
    )

    restaurant_id: Mapped[str] = mapped_column(
        ForeignKey("restaurant.id", ondelete="CASCADE"), primary_key=True
    )
    menu_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    hours_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 외부 페이지나 브라우저 오류 원문 대신 통제된 내부 오류 코드만 저장한다.
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    restaurant: Mapped[Restaurant] = relationship(back_populates="detail_status")
