"""5단계: 정규화된 메뉴·영업시간과 상세페이지 수집 상태를 저장."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..database import BusinessHours, Menu, PlaceDetailStatus, Restaurant
from ..place_details import NormalizedPlaceDetails


def _status_for(session: Session, restaurant: Restaurant) -> PlaceDetailStatus:
    """기존 상태 행을 반환하고, 아직 없다면 pending 상태로 생성한다."""

    status = restaurant.detail_status
    if status is None:
        status = PlaceDetailStatus(restaurant_id=restaurant.id)
        session.add(status)
    return status


def save_place_details(
    session: Session,
    restaurant_id: str,
    details: NormalizedPlaceDetails,
    *,
    attempted_at: datetime | None = None,
) -> dict[str, int | str | None]:
    """정상적으로 읽은 상세정보를 기존 데이터와 교체하고 상태를 갱신한다."""

    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise LookupError("restaurant_not_found")
    now = attempted_at or datetime.now(timezone.utc)
    status = _status_for(session, restaurant)

    # success/missing은 페이지를 정상 확인한 결과이므로 오래된 메뉴를 교체한다.
    # parse_failed는 수집기가 잘못 읽었을 가능성이 있어 기존 메뉴를 보존한다.
    if details.menu_status in {"success", "missing"}:
        session.execute(delete(Menu).where(Menu.restaurant_id == restaurant_id))
        if details.menu_status == "success":
            session.add_all(
                Menu(
                    restaurant_id=restaurant_id,
                    name=item.name,
                    normalized_name=item.normalized_name,
                    price=item.price,
                    price_status=item.price_status,
                )
                for item in details.menus
            )
    if details.hours_status in {"success", "missing"}:
        # 브레이크 타임으로 나뉜 모든 구간을 통째로 교체해 중복을 막는다.
        session.execute(
            delete(BusinessHours).where(BusinessHours.restaurant_id == restaurant_id)
        )
        if details.hours_status == "success":
            session.add_all(
                BusinessHours(
                    restaurant_id=restaurant_id,
                    day_of_week=item.day_of_week,
                    holiday=item.holiday,
                    start_time=item.start_time,
                    end_time=item.end_time,
                    ends_next_day=item.ends_next_day,
                    last_order_time=item.last_order_time,
                    sequence=item.sequence,
                )
                for item in details.business_hours
            )

    # 최근 시도 시각은 항상 기록하고, 두 항목 모두 파싱 실패가 아니면 성공 시각도 갱신한다.
    status.menu_status = details.menu_status
    status.hours_status = details.hours_status
    status.last_attempt_at = now
    if "parse_failed" not in {details.menu_status, details.hours_status}:
        status.last_success_at = now
    status.error_code = details.error_code
    session.commit()
    return {
        "restaurant_id": restaurant_id,
        "menu_status": status.menu_status,
        "hours_status": status.hours_status,
        "menus": len(details.menus) if details.menu_status == "success" else 0,
        "business_hours": (
            len(details.business_hours) if details.hours_status == "success" else 0
        ),
        "error_code": status.error_code,
    }


def record_place_detail_failure(
    session: Session,
    restaurant_id: str,
    failure_status: str,
    error_code: str,
    *,
    attempted_at: datetime | None = None,
) -> None:
    """페이지 접근·분석 실패를 기록하되 기존 메뉴와 영업시간은 건드리지 않는다."""

    if failure_status not in {"fetch_failed", "parse_failed"}:
        raise ValueError("invalid failure status")
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise LookupError("restaurant_not_found")
    status = _status_for(session, restaurant)
    # 수집 단계에서 페이지 자체를 읽지 못했으므로 메뉴와 영업시간에 같은 실패를 기록한다.
    status.menu_status = failure_status
    status.hours_status = failure_status
    status.last_attempt_at = attempted_at or datetime.now(timezone.utc)
    status.error_code = error_code[:50]
    session.commit()
