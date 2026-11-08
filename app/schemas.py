"""Shared request contracts. All values in this version are demo data."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


# 모든 위치 입력은 WGS84 위도/경도를 사용하며 무한대·NaN·범위 밖 좌표를 거부합니다.
class Coordinates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=True)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)


# 자동 위치와 주소/장소 검색 결과를 같은 요청 형식으로 전달합니다.
class SelectedLocation(Coordinates):
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(default="", max_length=500)
    source: Literal["geolocation", "address", "place"]
    accuracy: float | None = Field(default=None, ge=0, allow_inf_nan=False)


# 검색어는 서버에서 공백을 제거하고 빈 검색 및 지나치게 긴 검색을 차단합니다.
class LocationSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=100)
    kind: Literal["address", "place"] = "place"

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("주소 또는 장소를 입력해 주세요.")
        return value.strip()


class Conditions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(default=30, ge=1, le=120)
    budget: int = Field(default=10000, ge=1000, le=100000)
    cuisine: Literal["전체", "한식", "중식", "일식", "양식", "샐러드"] = "전체"
    diet: Literal["any", "vegetarian", "vegan"] = "any"
    spicy: bool = True


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 위치는 필수입니다. 반경은 서비스 조건에 맞게 50~500m 정수로 제한합니다.
    location: SelectedLocation
    radius_m: int = Field(default=500, ge=50, le=500, strict=True)
    conditions: Conditions = Field(default_factory=Conditions)
    menu_id: str | None = Field(default=None, max_length=40)


class ChatRequest(RecommendationRequest):
    message: str = Field(min_length=1, max_length=500)
