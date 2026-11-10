"""Groq 질문 해석을 검증된 메뉴 선정·카카오 검색·서버 답변에 연결합니다."""
import random
import anyio
from sqlalchemy.orm import Session
from .schemas import Conditions, SelectedLocation
from .clients.kakao import KakaoClient
from .clients.groq import GroqClient, GroqError
from .chat_policy import load_replies
from .database import Database
from .detail_collection import collect_restaurant_details_batch
from .recommendation_engine import filter_recommendation_candidates
from .repositories import save_restaurants

# 6단계에서도 채식·맵기·총소요시간은 검증하지 않으므로 확인 범위를 명확히 알린다.
CANDIDATE_NOTICE = "메뉴·가격·현재 영업 여부를 확인한 결과입니다. 채식·맵기·총소요시간은 아직 확인하지 않습니다."

MENUS = [
    dict(id="bibimbap", name="비빔밥", cuisine="한식", price=9000, diet="vegetarian", spicy=False, emoji="🥗", description="여러 채소와 밥을 곁들이는 메뉴예요. 데모에서는 달걀이 포함된 예시로 분류했어요."),
    dict(id="kimchi", name="김치찌개", cuisine="한식", price=9500, diet="any", spicy=True, emoji="🍲", description="따뜻한 국물 메뉴예요. 데모 데이터에는 고기가 포함되어 있어요."),
    dict(id="tofu", name="두부 채소 덮밥", cuisine="한식", price=8500, diet="vegan", spicy=False, emoji="🍚", description="두부와 채소를 곁들인 가상의 완전 채식 메뉴예요. 실제 매장의 재료를 확인한 정보가 아닙니다."),
    dict(id="noodle", name="짜장면", cuisine="중식", price=8000, diet="any", spicy=False, emoji="🍜", description="춘장 소스를 곁들인 면 요리예요. 데모에서는 고기가 포함된 예시입니다."),
    dict(id="spicynoodle", name="짬뽕", cuisine="중식", price=10000, diet="any", spicy=True, emoji="🍜", description="매운 국물과 해산물을 곁들인 면 메뉴 예시예요."),
    dict(id="cutlet", name="돈가스", cuisine="일식", price=11000, diet="any", spicy=False, emoji="🍱", description="바삭한 튀김과 밥을 함께 먹는 메뉴 예시예요."),
    dict(id="udon", name="우동", cuisine="일식", price=8500, diet="any", spicy=False, emoji="🍜", description="따뜻한 면 메뉴예요. 데모에서는 생선 육수가 포함된 것으로 분류했어요."),
    dict(id="pasta", name="크림 파스타", cuisine="양식", price=12000, diet="vegetarian", spicy=False, emoji="🍝", description="크림 소스와 면을 곁들인 예시로, 유제품이 포함되어 있어요."),
    dict(id="salad", name="병아리콩 샐러드", cuisine="샐러드", price=9500, diet="vegan", spicy=False, emoji="🥙", description="병아리콩과 채소로 구성한 가상의 완전 채식 메뉴예요."),
    dict(id="pizza", name="치즈 피자", cuisine="양식", price=13000, diet="vegetarian", spicy=False, emoji="🍕", description="치즈를 곁들인 메뉴 예시예요. 유제품이 포함되어 있어요."),
]


def eligible_menus(conditions: Conditions) -> list[dict]:
    # 현재 단계에서는 예시 메뉴의 음식 종류와 예산만 적용하고 채식·맵기는 판정하지 않는다.
    return [m.copy() for m in MENUS if
            (conditions.cuisine == "전체" or m["cuisine"] == conditions.cuisine)
            and m["price"] <= conditions.budget]


def _filter_with_database(
    database: Database, candidates: list[dict], menu_name: str, budget: int
):
    """상세 수집이 끝난 뒤 새 세션에서 최신 DB 값으로 추천을 판정한다."""

    with database.session_factory() as detail_session:
        return filter_recommendation_candidates(
            detail_session, candidates, menu_name, budget
        )


async def recommend(conditions: Conditions, location: SelectedLocation, radius_m: int,
                    client: KakaoClient, menu_id: str | None = None,
                    session: Session | None = None,
                    database: Database | None = None) -> dict:
    # 게임과 챗봇이 같은 경로를 사용하며, 선택한 메뉴에 대해서만 한 번 검색합니다.
    candidates = eligible_menus(conditions)
    if menu_id:
        selected = next((m for m in candidates if m["id"] == menu_id), None)
    else:
        selected = random.SystemRandom().choice(candidates) if candidates else None
    # 조건에 맞지 않는 지정 메뉴는 검색하지 않고, 검색 실패 시 가상 식당으로 보완하지 않습니다.
    result = dict(demo=False, menu_catalog_demo=True, constraints_verified=False,
                  menus=candidates, menu={**selected, "catalog_demo": True} if selected else None,
                  restaurants=[], candidate_restaurants=[], message=CANDIDATE_NOTICE,
                  has_more=False)
    if not selected:
        result["message"] = "조건에 맞는 예시 메뉴가 없거나 선택 메뉴가 유효하지 않습니다. 조건을 조정해 주세요."
        return result
    found = await client.search_restaurants(selected["name"], location.lat, location.lng, radius_m=radius_m)
    if session is not None:
        # 동기 SQLAlchemy 작업은 스레드에서 실행해 비동기 API 이벤트 루프를 막지 않는다.
        saved = await anyio.to_thread.run_sync(save_restaurants, session, found["items"])
        # 저장 결과를 응답에 넣어 신규·갱신·제외·실패 건수를 검증할 수 있게 한다.
        result["storage"] = saved.model_dump()
    if database is not None:
        # 새 식당 중 최대 3곳만 상세 수집하여 요청 지연과 페이지 접근량을 제한한다.
        collection = await collect_restaurant_details_batch(
            database, [str(item["id"]) for item in found["items"]], limit=3
        )
        result["collection"] = {
            "attempted": len(collection),
            "success": sum(
                item["menu_status"] in {"success", "missing"}
                and item["hours_status"] in {"success", "missing"}
                for item in collection
            ),
        }
        filtered = await anyio.to_thread.run_sync(
            _filter_with_database,
            database,
            found["items"],
            selected["name"],
            conditions.budget,
        )
        result["restaurants"] = filtered.restaurants
        result["candidate_restaurants"] = filtered.candidate_restaurants
        result["excluded"] = filtered.excluded
        result["verified_fields"] = ["menu", "price", "business_hours"]
    else:
        # DB가 주입되지 않는 독립 단위 테스트에서는 기존 검색 응답 형식을 유지한다.
        result["restaurants"] = found["items"]
    result["has_more"] = found["has_more"]
    # 이동시간은 거리로 추측하지 않으며, 현재는 첫 검색 페이지의 후보만 표시합니다.
    if not found["items"]:
        result["message"] = "선택 반경 안에 해당 메뉴의 식당 검색 결과가 없습니다. 다른 메뉴를 고르거나 위치·반경을 변경해 주세요."
    elif result["restaurants"]:
        result["message"] = (
            f"현재 영업 중이며 메뉴와 예산이 확인된 식당 {len(result['restaurants'])}곳입니다."
        )
        if result["candidate_restaurants"]:
            result["message"] += (
                f" 추가로 확인이 필요한 후보 {len(result['candidate_restaurants'])}곳이 있습니다."
            )
    elif result["candidate_restaurants"]:
        result["message"] = (
            f"조건이 모두 확인된 식당은 없고, 확인이 필요한 후보 "
            f"{len(result['candidate_restaurants'])}곳이 있습니다."
        )
    else:
        result["message"] = "현재 영업 중이면서 메뉴와 예산 조건이 확인된 식당이 없습니다."
    return result


async def chat(message: str, conditions: Conditions, menu_id: str | None, location: SelectedLocation,
               radius_m: int, client: KakaoClient, groq: GroqClient,
               session: Session | None = None,
               database: Database | None = None) -> dict:
    # AI는 의도만 해석하고 실제 메뉴 선정·조건 검증·식당 조회는 서버가 실행합니다.
    if menu_id and not any(m["id"] == menu_id for m in MENUS):
        return dict(text="선택 메뉴가 유효하지 않습니다. 메뉴를 다시 골라주세요.", suggested_patch=None, result=None)
    parsed = await groq.interpret(message, conditions, radius_m, MENUS, menu_id)
    try:
        replies = load_replies()
    except (OSError, ValueError, TypeError):
        raise GroqError("groq_policy", "답변 안내문 설정을 확인해 주세요.", 503) from None
    response = dict(text="", suggested_patch=None, result=None)
    if parsed.intent == "change_conditions":
        # 현재와 같은 값은 제외하고 변경안만 반환합니다. 이 단계에서는 검색하지 않습니다.
        current = {**conditions.model_dump(), "radius_m": radius_m}
        patch = {k:v for k,v in parsed.condition_patch.model_dump(exclude_none=True).items() if current[k] != v}
        response.update(text="변경할 조건을 확인하고 적용해 주세요." if patch else "이미 해당 조건이 적용되어 있습니다.",
                        suggested_patch=patch or None)
    elif parsed.intent == "clarify":
        # 모델이 만든 확인 문장에 매장 사실이나 지시가 섞여도 사용자에게 그대로 전달하지 않습니다.
        response["text"] = replies[parsed.clarification_kind or "ambiguous"]
    elif parsed.intent == "out_of_scope":
        response["text"] = replies["out_of_scope"]
    elif parsed.intent == "explain_menu":
        menu = next(m for m in MENUS if m["id"] == parsed.menu_id)
        response["text"] = menu["description"] + " 실제 매장의 재료·가격 정보는 별도 확인이 필요합니다."
    else:
        target = parsed.menu_id
        if parsed.intent == "search_restaurants":
            target = target or menu_id
            if not target:
                response["text"] = "어떤 메뉴의 식당을 찾을까요? 먼저 메뉴를 골라주세요."
                return response
        # 지정 메뉴도 예산·분류·식이·맵기 조건을 통과해야 검색할 수 있습니다.
        result = await recommend(
            conditions, location, radius_m, client, target, session, database
        )
        # 추천 답변은 서버 카탈로그와 확정 조건만 사용해 중복 경고를 줄입니다.
        summary = (f"적용 조건: 반경 {radius_m}m · 예산 {conditions.budget:,}원 이하 · {conditions.cuisine} · "
                   f"점심시간 {conditions.minutes}분 · 채식·맵기 조건 미적용")
        has_places = result["restaurants"] or result.get("candidate_restaurants")
        text = (f"추천 메뉴: {result['menu']['name']}\n{summary}\n"
                f"추천 식당: {len(result['restaurants'])}곳 · 확인 필요 후보: "
                f"{len(result.get('candidate_restaurants', []))}곳\n{replies['notice']}") if has_places else result["message"]
        response.update(result=result, text=text)
    return response
