"""6단계: 수집된 메뉴·가격·영업시간으로 카카오 검색 후보를 검증."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .database import BusinessHours, Menu, Restaurant
from .place_details import normalize_menu_name


KOREA_TIMEZONE = ZoneInfo("Asia/Seoul")


@dataclass(slots=True)
class RecommendationFilterResult:
    """검증 추천, 확인 필요 후보와 제외 사유별 개수."""

    restaurants: list[dict[str, Any]] = field(default_factory=list)
    candidate_restaurants: list[dict[str, Any]] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)

    def exclude(self, reason: str) -> None:
        """개인정보나 외부 오류 대신 통제된 제외 사유의 개수만 기록한다."""

        self.excluded[reason] = self.excluded.get(reason, 0) + 1


def _minutes(value) -> int:
    """날짜 없는 DB 시각을 자정 기준 분으로 바꾼다."""

    return value.hour * 60 + value.minute


def _open_now(
    rows: list[BusinessHours], now: datetime
) -> tuple[bool, bool]:
    """한국의 현재 요일·시각과 일반/익일 영업 구간을 비교한다."""

    today = now.weekday()
    previous_day = (today - 1) % 7
    current_minute = now.hour * 60 + now.minute

    # 오늘이 명시적 휴무라면 전날의 심야 영업 구간도 추천하지 않는다.
    if any(row.day_of_week == today and row.holiday is True for row in rows):
        return False, False

    for row in rows:
        if row.holiday is True or row.start_time is None or row.end_time is None:
            continue
        start = _minutes(row.start_time)
        end = _minutes(row.end_time)
        matched = False

        # 당일 시작 구간은 일반 영업과 자정을 넘는 영업을 나눠 계산한다.
        if row.day_of_week == today:
            if row.ends_next_day:
                matched = current_minute >= start
            else:
                matched = start <= current_minute < end
        # 자정 이후에는 전날 시작한 익일 영업 구간도 함께 확인한다.
        elif row.day_of_week == previous_day and row.ends_next_day:
            matched = current_minute < end
        if matched:
            return True, row.holiday is None
    return False, False


def _matching_menus(menus: list[Menu], target_name: str) -> list[Menu]:
    """공백 차이를 제거하고 선택 메뉴가 실제 메뉴명에 포함되는지 확인한다."""

    target = normalize_menu_name(target_name).replace(" ", "")
    return [
        menu
        for menu in menus
        if target and target in menu.normalized_name.replace(" ", "")
    ]


def _base_item(raw: Mapping[str, Any], restaurant: Restaurant) -> dict[str, Any]:
    """카카오 거리 정보에 DB의 최신 식당 기본정보를 덮어쓴다."""

    item = dict(raw)
    item.update(
        id=restaurant.id,
        name=restaurant.name,
        address=restaurant.address,
        category=restaurant.category_name or item.get("category", ""),
        lat=restaurant.latitude,
        lng=restaurant.longitude,
        place_url=restaurant.place_url,
        open_status="open",
    )
    return item


def filter_recommendation_candidates(
    session: Session,
    raw_candidates: list[Mapping[str, Any]],
    menu_name: str,
    budget: int,
    *,
    now: datetime | None = None,
) -> RecommendationFilterResult:
    """영업 중인 식당을 메뉴·가격 확인 수준에 따라 추천과 후보로 나눈다."""

    result = RecommendationFilterResult()
    if not raw_candidates:
        return result
    candidate_ids = list(dict.fromkeys(str(item.get("id", "")) for item in raw_candidates))
    candidate_ids = [value for value in candidate_ids if value]

    # 상세 상태, 메뉴, 영업 구간을 한 번에 미리 읽어 반복 쿼리를 막는다.
    restaurants = session.scalars(
        select(Restaurant)
        .where(Restaurant.id.in_(candidate_ids))
        .options(
            selectinload(Restaurant.detail_status),
            selectinload(Restaurant.menus),
            selectinload(Restaurant.business_hours),
        )
    ).all()
    by_id = {restaurant.id: restaurant for restaurant in restaurants}
    current = now.astimezone(KOREA_TIMEZONE) if now else datetime.now(KOREA_TIMEZONE)

    for raw in raw_candidates:
        restaurant = by_id.get(str(raw.get("id", "")))
        if restaurant is None:
            result.exclude("restaurant_missing")
            continue
        status = restaurant.detail_status

        # 영업시간을 확인하지 못한 식당은 현재 영업 여부를 보장할 수 없어 제외한다.
        if status is None or status.hours_status != "success":
            result.exclude("hours_unavailable")
            continue
        is_open, holiday_unknown = _open_now(restaurant.business_hours, current)
        if not is_open:
            result.exclude("closed")
            continue

        item = _base_item(raw, restaurant)
        warnings: list[str] = []
        if holiday_unknown:
            warnings.append("휴무일 확인 필요")

        # 메뉴 자체를 확인하지 못했지만 영업 중인 식당은 추천과 분리한 후보로 남긴다.
        if status.menu_status != "success":
            item.update(
                menu_verified=False,
                menu_name=None,
                price=None,
                price_status="missing",
                recommendation_kind="candidate",
            )
            warnings.extend(["메뉴 확인 불가", "가격 확인 불가"])
            item["warnings"] = warnings
            result.candidate_restaurants.append(item)
            continue

        matched_menus = _matching_menus(restaurant.menus, menu_name)
        if not matched_menus:
            result.exclude("menu_not_found")
            continue

        # 같은 메뉴가 여러 개면 예산 이내의 가장 낮은 확인 가격을 대표 가격으로 사용한다.
        affordable = [
            menu
            for menu in matched_menus
            if menu.price_status == "known" and menu.price is not None and menu.price <= budget
        ]
        if affordable:
            selected = min(affordable, key=lambda menu: menu.price or 0)
            item.update(
                menu_verified=True,
                menu_name=selected.name,
                price=selected.price,
                price_status="known",
                recommendation_kind="recommended",
                warnings=warnings,
            )
            result.restaurants.append(item)
            continue

        unknown_price = next(
            (
                menu
                for menu in matched_menus
                if menu.price is None or menu.price_status in {"missing", "variable"}
            ),
            None,
        )
        if unknown_price is not None:
            # 메뉴 판매는 확인됐지만 예산 적합성을 판단할 수 없어 후보 목록으로 분리한다.
            item.update(
                menu_verified=True,
                menu_name=unknown_price.name,
                price=None,
                price_status=unknown_price.price_status,
                recommendation_kind="candidate",
            )
            warnings.append("가격 확인 불가")
            item["warnings"] = warnings
            result.candidate_restaurants.append(item)
        else:
            result.exclude("over_budget")
    return result
