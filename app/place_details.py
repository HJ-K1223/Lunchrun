"""5단계: 카카오 페이지 원문을 DB에 넣을 메뉴·영업시간 형식으로 정규화."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
import re

from .collectors import RawPlacePage


# Python weekday 규칙과 맞춰 월요일 0부터 일요일 6까지 사용한다.
DAY_INDEX = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*~\s*(\d{1,2}):(\d{2})")
SINGLE_TIME = re.compile(r"(\d{1,2}):(\d{2})")
KNOWN_PRICE = re.compile(r"(?<!\d)(\d[\d,]*)\s*원")


@dataclass(frozen=True, slots=True)
class NormalizedMenu:
    """중복 비교와 가격 필터에 바로 사용할 수 있는 메뉴."""

    name: str
    normalized_name: str
    price: int | None
    price_status: str


@dataclass(frozen=True, slots=True)
class NormalizedBusinessHours:
    """브레이크 타임을 제외한 실제 영업 구간 한 건."""

    day_of_week: int
    holiday: bool | None
    start_time: time | None
    end_time: time | None
    ends_next_day: bool
    last_order_time: time | None
    sequence: int


@dataclass(frozen=True, slots=True)
class NormalizedPlaceDetails:
    """한 식당의 정규화 결과와 항목별 수집 상태."""

    menus: tuple[NormalizedMenu, ...]
    business_hours: tuple[NormalizedBusinessHours, ...]
    menu_status: str
    hours_status: str
    error_code: str | None = None


def normalize_menu_name(name: str) -> str:
    """중복 확인용으로 연속 공백과 대소문자 차이를 제거한다."""

    return " ".join(name.split()).casefold()


def normalize_price(price_text: str) -> tuple[int | None, str]:
    """가격 문자열을 정수 원 단위와 known/missing/variable 상태로 변환한다."""

    if any(label in price_text for label in ("변동", "시가")):
        return None, "variable"
    match = KNOWN_PRICE.search(price_text)
    if not match:
        return None, "missing"
    return int(match.group(1).replace(",", "")), "known"


def _minutes(hour: str, minute: str) -> int:
    # 24:00은 다음 날 00:00을 뜻하므로 1440분으로 유지한다.
    hours, minutes = int(hour), int(minute)
    if hours > 24 or minutes > 59 or (hours == 24 and minutes != 0):
        raise ValueError("invalid time")
    return hours * 60 + minutes


def _range(text: str) -> tuple[int, int] | None:
    """시간 범위를 분 단위로 바꾸고 자정을 넘으면 종료값에 하루를 더한다."""

    match = TIME_RANGE.search(text)
    if not match:
        return None
    start = _minutes(match.group(1), match.group(2))
    end = _minutes(match.group(3), match.group(4))
    if end <= start:
        end += 24 * 60
    return start, end


def _clock(minutes: int) -> time:
    # DB Time 열에는 날짜가 없으므로 24시간 단위로 되돌리고 익일 여부는 별도 저장한다.
    value = minutes % (24 * 60)
    return time(value // 60, value % 60)


def _split_intervals(
    openings: list[tuple[int, int]], breaks: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """전체 영업시간에서 브레이크 타임을 빼 실제 추천 가능 구간으로 나눈다."""

    intervals: list[tuple[int, int]] = []
    for opening_start, opening_end in openings:
        pieces = [(opening_start, opening_end)]
        for break_start, break_end in breaks:
            if opening_end > 24 * 60 and break_start < opening_start:
                break_start += 24 * 60
                break_end += 24 * 60
            next_pieces = []
            for piece_start, piece_end in pieces:
                if break_end <= piece_start or break_start >= piece_end:
                    next_pieces.append((piece_start, piece_end))
                    continue
                if piece_start < break_start:
                    next_pieces.append((piece_start, break_start))
                if break_end < piece_end:
                    next_pieces.append((break_end, piece_end))
            pieces = next_pieces
        intervals.extend(piece for piece in pieces if piece[0] < piece[1])
    return intervals


def _last_order_for_interval(
    values: list[int], start: int, end: int
) -> time | None:
    """여러 라스트오더 중 현재 영업 구간에 속하는 시각을 연결한다."""

    for value in values:
        adjusted = value + 24 * 60 if end > 24 * 60 and value < start else value
        if start <= adjusted <= end:
            return _clock(adjusted)
    return None


def parse_place_page(page: RawPlacePage) -> NormalizedPlaceDetails:
    """수집 원문을 정규화하고 메뉴·영업시간별 성공 여부를 판정한다."""

    # 메뉴 이름을 정리하고 같은 이름이 여러 번 노출돼도 한 번만 저장한다.
    menus: list[NormalizedMenu] = []
    seen_names: set[str] = set()
    invalid_menu = False
    for raw in page.menus:
        name = " ".join(raw.name.split())
        normalized_name = normalize_menu_name(name)
        if not name or not normalized_name:
            invalid_menu = True
            continue
        if normalized_name in seen_names:
            continue
        price, price_status = normalize_price(raw.price_text)
        menus.append(NormalizedMenu(name, normalized_name, price, price_status))
        seen_names.add(normalized_name)

    # 요일별 텍스트를 휴무, 영업, 브레이크, 라스트오더로 분리한다.
    hours: list[NormalizedBusinessHours] = []
    invalid_hours = False
    for raw_day in page.business_days:
        day_match = re.match(r"\s*([월화수목금토일])", raw_day.day_text)
        if day_match is None:
            invalid_hours = True
            continue
        day = DAY_INDEX[day_match.group(1)]
        if any("휴무" in detail for detail in raw_day.details):
            # 휴무일은 시작·종료시각 없이 holiday=True인 행 하나로 표현한다.
            hours.append(
                NormalizedBusinessHours(day, True, None, None, False, None, 0)
            )
            continue

        opening_texts = [
            detail
            for detail in raw_day.details
            if "브레이크" not in detail and "라스트오더" not in detail
        ]
        break_texts = [detail for detail in raw_day.details if "브레이크" in detail]
        order_texts = [detail for detail in raw_day.details if "라스트오더" in detail]
        try:
            openings = [value for text in opening_texts if (value := _range(text))]
            if any("24시간" in text for text in opening_texts):
                openings.append((0, 24 * 60))
            breaks = [value for text in break_texts if (value := _range(text))]
            last_orders = []
            for text in order_texts:
                match = SINGLE_TIME.search(text)
                if match:
                    last_orders.append(_minutes(match.group(1), match.group(2)))
        except ValueError:
            invalid_hours = True
            continue
        if not openings:
            if raw_day.details:
                invalid_hours = True
            continue
        for sequence, (start, end) in enumerate(_split_intervals(openings, breaks)):
            # 브레이크 타임 앞뒤 구간은 sequence로 순서를 구분한다.
            hours.append(
                NormalizedBusinessHours(
                    day_of_week=day,
                    holiday=False,
                    start_time=_clock(start),
                    end_time=_clock(end),
                    ends_next_day=end >= 24 * 60,
                    last_order_time=_last_order_for_interval(last_orders, start, end),
                    sequence=sequence,
                )
            )

    # 항목이 없으면 missing, 원문은 있으나 모두 해석 실패면 parse_failed다.
    if menus:
        menu_status = "success"
    elif page.menus and invalid_menu:
        menu_status = "parse_failed"
    else:
        menu_status = "missing"

    if hours:
        hours_status = "success"
    elif page.business_days and invalid_hours:
        hours_status = "parse_failed"
    else:
        hours_status = "missing"

    errors = []
    if menu_status == "parse_failed":
        errors.append("menu_parse")
    if hours_status == "parse_failed":
        errors.append("hours_parse")
    return NormalizedPlaceDetails(
        menus=tuple(menus),
        business_hours=tuple(hours),
        menu_status=menu_status,
        hours_status=hours_status,
        error_code="_".join(errors) or None,
    )
