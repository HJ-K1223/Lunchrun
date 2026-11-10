"""4단계: 카카오 음식점 검색 결과를 검증하고 restaurant 테이블에 저장."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from ..database import PlaceDetailStatus, Restaurant


class KakaoRestaurantRecord(BaseModel):
    """DB 저장 전에 필요한 카카오 식당 필드만 다시 검증하는 입력 모델."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    address: str = Field(default="", max_length=500)
    category: str = Field(default="", max_length=300)
    place_url: str = Field(default="", max_length=1000)
    category_group_code: str = Field(default="FD6", max_length=10)

    @field_validator("id", "name", "address", "category", "place_url", mode="before")
    @classmethod
    def trim_strings(cls, value: Any) -> Any:
        # 같은 값이 앞뒤 공백 차이로 다르게 저장되지 않도록 정리한다.
        return value.strip() if isinstance(value, str) else value


@dataclass(slots=True)
class RestaurantSaveResult:
    """한 번의 검색 결과 저장에서 발생한 작업별 건수."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

    def model_dump(self) -> dict[str, int]:
        return asdict(self)


def cuisine_from_category(category_name: str) -> str | None:
    """'음식점 > 한식 > 찌개'에서 앱 검색용 '한식'을 꺼낸다."""

    parts = [part.strip() for part in category_name.split(">") if part.strip()]
    if parts and parts[0] == "음식점":
        parts = parts[1:]
    return parts[0][:30] if parts else None


def save_restaurants(
    session: Session, candidates: Iterable[Mapping[str, Any]]
) -> RestaurantSaveResult:
    """카카오 ID 기준으로 추가·갱신하고, 한 건의 오류가 전체를 취소하지 않게 한다."""

    result = RestaurantSaveResult()
    for candidate in candidates:
        try:
            record = KakaoRestaurantRecord.model_validate(candidate)
        except (ValidationError, TypeError):
            result.failed += 1
            continue

        # 카카오 음식점 그룹(FD6)이 아닌 장소는 restaurant에 넣지 않는다.
        if record.category_group_code != "FD6":
            result.skipped += 1
            continue

        try:
            # 건별 SAVEPOINT를 사용해 한 식당의 제약조건 오류만 되돌린다.
            with session.begin_nested():
                restaurant = session.get(Restaurant, record.id)
                values = {
                    "name": record.name,
                    "category_name": record.category or None,
                    "cuisine": cuisine_from_category(record.category),
                    "latitude": record.lat,
                    "longitude": record.lng,
                    "address": record.address,
                    "place_url": record.place_url,
                }
                if restaurant is None:
                    # 신규 식당은 다음 상세 수집을 기다리는 pending 상태도 함께 만든다.
                    restaurant = Restaurant(id=record.id, **values)
                    restaurant.detail_status = PlaceDetailStatus()
                    session.add(restaurant)
                    action = "inserted"
                else:
                    # 동일 카카오 ID가 있으면 새 행을 만들지 않고 최신 기본정보로 갱신한다.
                    for key, value in values.items():
                        setattr(restaurant, key, value)
                    if restaurant.detail_status is None:
                        restaurant.detail_status = PlaceDetailStatus()
                    action = "updated"
                session.flush()
                setattr(result, action, getattr(result, action) + 1)
        except (IntegrityError, StatementError, ValueError, TypeError):
            result.failed += 1

    # 정상 처리된 모든 식당을 한 번에 확정한다.
    session.commit()
    return result
