"""질문 해석 계약·가드레일 검증과 Groq 입력 구성을 관리합니다."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


POLICY_DIR = Path(__file__).resolve().parent / "prompts"


def load_replies() -> dict[str, str]:
    # 직접 수정 가능한 안내문도 빈 값·오타 키·너무 긴 문장을 검사합니다.
    replies = json.loads((POLICY_DIR / "replies.json").read_text(encoding="utf-8"))
    required = {"ambiguous", "radius", "location", "unsupported", "conflict", "menu", "out_of_scope", "notice"}
    if not isinstance(replies, dict) or set(replies) != required:
        raise ValueError("답변 안내문의 항목을 확인해 주세요.")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 500 for v in replies.values()):
        raise ValueError("안내문은 1~500자의 문자열이어야 합니다.")
    return replies


class ConditionPatch(BaseModel):
    # 미지정 조건은 None이며, 저장할 때 exclude_none=True로 기존 조건을 보존합니다.
    model_config = ConfigDict(extra="forbid", strict=True)
    minutes: int | None = Field(default=None, ge=10, le=120)
    budget: int | None = Field(default=None, ge=1000, le=100000)
    cuisine: Literal["전체", "한식", "중식", "일식", "양식", "샐러드"] | None = None
    diet: Literal["any", "vegetarian", "vegan"] | None = None
    spicy: bool | None = None
    radius_m: int | None = Field(default=None, ge=50, le=500)


class QuestionInterpretation(BaseModel):
    # 모델은 실행 계획만 제안합니다. 식당·좌표·가격·자유 형식 답변은 허용하지 않습니다.
    model_config = ConfigDict(extra="forbid", strict=True)
    intent: Literal["recommend_menu", "search_restaurants", "explain_menu", "change_conditions", "clarify", "out_of_scope"]
    menu_id: str | None = Field(default=None, max_length=40)
    condition_patch: ConditionPatch = Field(default_factory=ConditionPatch)
    clarification: str | None = Field(default=None, min_length=1, max_length=200)
    # 확인 질문은 자유 생성 문장 대신 서버의 검증된 안내 유형으로 표시합니다.
    clarification_kind: Literal["ambiguous", "radius", "location", "unsupported", "conflict", "menu"] | None = None

    @field_validator("menu_id")
    @classmethod
    def known_menu(cls, value: str | None, info: ValidationInfo) -> str | None:
        # 호출 측에서 전달한 최신 메뉴 목록만 허용하고 알 수 없는 ID는 거부합니다.
        if value is not None and value not in (info.context or {}).get("menu_ids", set()):
            raise ValueError("앱의 메뉴 목록에 없는 ID입니다.")
        return value

    @model_validator(mode="after")
    def consistent_intent(self):
        # 조건 변경은 확인이 필요한 전용 의도로 제한해 검색으로 바로 전달하지 않습니다.
        has_patch = bool(self.condition_patch.model_dump(exclude_none=True))
        if has_patch != (self.intent == "change_conditions"):
            raise ValueError("조건 변경 의도와 변경 항목이 일치해야 합니다.")
        if self.intent == "clarify":
            if self.clarification is None or not self.clarification.strip():
                raise ValueError("추가 확인 질문이 필요합니다.")
        elif self.clarification is not None:
            raise ValueError("확인 질문은 clarify 의도에서만 허용합니다.")
        if self.intent != "clarify" and self.clarification_kind is not None:
            raise ValueError("확인 유형은 clarify 의도에서만 허용합니다.")
        if self.intent == "explain_menu" and self.menu_id is None:
            raise ValueError("설명할 메뉴 ID가 필요합니다.")
        if self.intent in {"clarify", "out_of_scope", "change_conditions"} and self.menu_id is not None:
            raise ValueError("이 의도에는 메뉴 ID를 지정할 수 없습니다.")
        return self


def parse_interpretation(raw: str, menu_ids: set[str]) -> QuestionInterpretation:
    # 중복 키는 마지막 값으로 덮어쓰지 않고 거부하며 비표준 NaN/Infinity도 차단합니다.
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("중복된 JSON 키입니다.")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("유한한 JSON 숫자만 허용합니다.")
    json.loads(raw, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    # JSON 이외의 설명문·코드 블록은 자동 보정하지 않고 거부합니다.
    return QuestionInterpretation.model_validate_json(raw, context={"menu_ids": menu_ids})


def build_interpretation_messages(message: str, conditions: dict, radius_m: int,
                                  menus: list[dict], selected_menu_id: str | None = None) -> list[dict]:
    # 사용자 질문과 앱 상태는 별도 user 메시지의 데이터로 전달합니다. 위치 좌표는 보내지 않습니다.
    examples = json.loads((POLICY_DIR / "examples.json").read_text(encoding="utf-8"))
    system = (POLICY_DIR / "system.txt").read_text(encoding="utf-8")
    system += "\n출력 JSON Schema:\n" + json.dumps(QuestionInterpretation.model_json_schema(), ensure_ascii=False)
    system += "\n해석 예시:\n" + json.dumps(examples, ensure_ascii=False)
    context = {"question": message, "current_conditions": conditions, "radius_m": radius_m,
               "selected_menu_id": selected_menu_id,
               "menus": [{"id": m["id"], "name": m["name"]} for m in menus]}
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
