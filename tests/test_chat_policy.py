"""Groq 호출 없이 계약과 예시를 검사합니다. 예시는 모델 정확도 검증 결과가 아닙니다."""
import json
import unittest
from pydantic import ValidationError
from app.chat_policy import POLICY_DIR, build_interpretation_messages, parse_interpretation, load_replies
from app.services import MENUS


class ChatPolicyTests(unittest.TestCase):
    def test_reply_configuration_and_ambiguous_json(self):
        # 중복 키와 비표준 숫자는 JSON 파서의 자동 덮어쓰기보다 먼저 차단합니다.
        self.assertIn("unsupported", load_replies())
        for raw in ('{"intent":"out_of_scope","intent":"recommend_menu"}',
                    '{"intent":"change_conditions","condition_patch":{"budget":NaN}}'):
            with self.assertRaises(ValueError):
                parse_interpretation(raw, set())

    def setUp(self):
        # 앱의 실제 메뉴 목록에서 ID를 가져와 중복 관리하지 않습니다.
        self.ids = {m["id"] for m in MENUS}

    def test_examples_follow_contract(self):
        examples = json.loads((POLICY_DIR / "examples.json").read_text(encoding="utf-8"))
        for example in examples:
            with self.subTest(question=example["question"]):
                parse_interpretation(json.dumps(example["expected"]), self.ids)

    def test_rejects_untrusted_fields_and_contradictions(self):
        # 생성된 식당·좌표, 알 수 없는 메뉴, 확인 없는 조건 변경을 거부합니다.
        invalid = [
            {"intent":"search_restaurants", "restaurants":[]},
            {"intent":"recommend_menu", "lat":37.0},
            {"intent":"explain_menu", "menu_id":"invented"},
            {"intent":"recommend_menu", "condition_patch":{"budget":10000}},
            {"intent":"change_conditions"},
            {"intent":"clarify", "clarification":" "},
            {"intent":"explain_menu"},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_interpretation(json.dumps(value), self.ids)

    def test_radius_and_types(self):
        # 최솟값·최댓값을 허용하고 소수와 문자열 자동 변환을 막습니다.
        for value in (50, 500):
            parse_interpretation(json.dumps({"intent":"change_conditions", "condition_patch":{"radius_m":value}}), self.ids)
        for value in (49, 501, 100.5, "100", True):
            with self.assertRaises(ValidationError):
                parse_interpretation(json.dumps({"intent":"change_conditions", "condition_patch":{"radius_m":value}}), self.ids)

    def test_context_is_separate_from_instructions(self):
        messages = build_interpretation_messages("USER_INPUT_UNIQUE_1289", {"budget":12000}, 500, MENUS)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertNotIn("USER_INPUT_UNIQUE_1289", messages[0]["content"])
        self.assertEqual(json.loads(messages[1]["content"])["question"], "USER_INPUT_UNIQUE_1289")
