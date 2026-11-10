"""5단계: 식당 한 곳의 조회·수집·정규화·저장을 연결하는 애플리케이션 서비스."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import anyio
from playwright.async_api import Error as PlaywrightError

from .collectors import KakaoPlaceCollector, PlaceCollectionError
from .database import Database, Restaurant
from .place_details import parse_place_page
from .repositories.place_details import (
    record_place_detail_failure,
    save_place_details,
)


def _restaurant_url(database: Database, restaurant_id: str) -> str:
    """브라우저를 열기 전에 짧은 세션으로 식당 URL만 조회한다."""

    with database.session_factory() as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise LookupError("restaurant_not_found")
        return restaurant.place_url


def _save(database: Database, restaurant_id: str, details) -> dict:
    """정규화 결과를 별도 DB 세션에서 저장한다."""

    with database.session_factory() as session:
        return save_place_details(session, restaurant_id, details)


def _save_failure(
    database: Database, restaurant_id: str, status: str, code: str
) -> None:
    """수집 실패 상태만 기록하고 이전 상세 데이터는 보존한다."""

    with database.session_factory() as session:
        record_place_detail_failure(session, restaurant_id, status, code)


async def collect_restaurant_details(
    database: Database,
    restaurant_id: str,
    collector: KakaoPlaceCollector | None = None,
) -> dict:
    """긴 브라우저 수집 중에는 DB 트랜잭션을 잡지 않고 결과만 짧게 저장한다."""

    place_url = await anyio.to_thread.run_sync(
        _restaurant_url, database, restaurant_id
    )
    # 테스트에서는 가짜 수집기를 주입하고 실제 실행에서는 Playwright를 사용한다.
    active_collector = collector or KakaoPlaceCollector()
    try:
        page = await active_collector.collect(restaurant_id, place_url)
        details = parse_place_page(page)
        return await anyio.to_thread.run_sync(
            _save, database, restaurant_id, details
        )
    except PlaceCollectionError as error:
        # 외부 오류 원문 대신 fetch_failed/parse_failed와 통제된 코드만 기록한다.
        await anyio.to_thread.run_sync(
            _save_failure, database, restaurant_id, error.status, error.code
        )
        return {
            "restaurant_id": restaurant_id,
            "menu_status": error.status,
            "hours_status": error.status,
            "menus": 0,
            "business_hours": 0,
            "error_code": error.code,
        }


def _utc(value: datetime | None) -> datetime | None:
    """SQLite가 시간대 정보를 제거해도 UTC 시각으로 안전하게 비교한다."""

    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _collection_targets(
    database: Database,
    restaurant_ids: list[str],
    limit: int,
    checked_at: datetime,
) -> list[str]:
    """미수집 또는 재수집 시점이 지난 식당을 원래 검색 순서대로 고른다."""

    targets: list[str] = []
    with database.session_factory() as session:
        for restaurant_id in dict.fromkeys(restaurant_ids):
            restaurant = session.get(Restaurant, restaurant_id)
            if restaurant is None:
                continue
            status = restaurant.detail_status
            last_attempt = _utc(status.last_attempt_at) if status else None
            states = {status.menu_status, status.hours_status} if status else {"pending"}

            # 신규 식당은 즉시 수집하고 실패는 1시간, 정상 결과는 7일 뒤 재시도한다.
            due = "pending" in states or last_attempt is None
            if states & {"fetch_failed", "parse_failed"} and last_attempt is not None:
                due = checked_at - last_attempt >= timedelta(hours=1)
            elif states <= {"success", "missing"} and last_attempt is not None:
                due = checked_at - last_attempt >= timedelta(days=7)
            if due:
                targets.append(restaurant_id)
            if len(targets) >= limit:
                break
    return targets


async def collect_restaurant_details_batch(
    database: Database,
    restaurant_ids: list[str],
    *,
    limit: int = 3,
    checked_at: datetime | None = None,
    collector: KakaoPlaceCollector | None = None,
) -> list[dict]:
    """추천 요청 한 번에 제한된 식당만 수집하고 브라우저 하나를 재사용한다."""

    if limit < 1:
        return []
    now = checked_at or datetime.now(timezone.utc)
    targets = await anyio.to_thread.run_sync(
        _collection_targets, database, restaurant_ids, limit, now
    )
    if not targets:
        return []

    results: list[dict] = []
    if collector is not None:
        # 테스트나 상위 배치가 수집기를 제공하면 브라우저 생명주기를 여기서 관리하지 않는다.
        for restaurant_id in targets:
            results.append(
                await collect_restaurant_details(database, restaurant_id, collector)
            )
        return results
    try:
        # 브라우저 시작 비용을 줄이기 위해 대상 식당들이 하나의 Chromium을 공유한다.
        async with KakaoPlaceCollector() as collector:
            for restaurant_id in targets:
                results.append(
                    await collect_restaurant_details(database, restaurant_id, collector)
                )
    except (PlaceCollectionError, PlaywrightError):
        # 브라우저 자체를 열지 못한 경우에도 기존 상세정보는 삭제하지 않는다.
        for restaurant_id in targets:
            await anyio.to_thread.run_sync(
                _save_failure,
                database,
                restaurant_id,
                "fetch_failed",
                "browser_unavailable",
            )
            results.append(
                {
                    "restaurant_id": restaurant_id,
                    "menu_status": "fetch_failed",
                    "hours_status": "fetch_failed",
                    "menus": 0,
                    "business_hours": 0,
                    "error_code": "browser_unavailable",
                }
            )
    return results
