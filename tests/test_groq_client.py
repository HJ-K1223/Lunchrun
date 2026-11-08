"""실제 네트워크 없이 Groq 요청·오류·응답 검증을 검사합니다."""
import json
import unittest

import httpx

from app.clients.groq import GroqClient, GroqError
from app.config import Settings
from app.schemas import Conditions
from app.services import MENUS


def completion(content=None, finish="stop"):
    # 테스트용 응답만 생성하고 실제 키나 사용자 질문은 기록하지 않습니다.
    if content is None:
        content = json.dumps({"intent":"recommend_menu", "condition_patch":{}})
    return {"choices":[{"finish_reason":finish,"message":{"content":content}}]}


class GroqClientTests(unittest.IsolatedAsyncioTestCase):
    def settings(self, **kwargs):
        return Settings(map_api_key="fake-map", groq_api_key="fake-groq", **kwargs)

    async def invoke(self, handler, settings=None):
        client = GroqClient(settings or self.settings(), transport=httpx.MockTransport(handler))
        return await client.interpret("점심 추천", Conditions(), 500, MENUS)

    async def test_request_and_validated_response(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(request.headers["Authorization"], "Bearer fake-groq")
            self.assertEqual(str(request.url), GroqClient.URL)
            self.assertEqual(body["max_completion_tokens"], 1024)
            self.assertEqual(body["response_format"], {"type":"json_object"})
            self.assertEqual(body["reasoning_effort"], "low")
            self.assertFalse(body["include_reasoning"])
            context = json.loads(body["messages"][1]["content"])
            self.assertNotIn("location", context)
            self.assertEqual(context["radius_m"], 500)
            return httpx.Response(200, json=completion())
        self.assertEqual((await self.invoke(handler)).intent, "recommend_menu")

    async def test_missing_key_never_calls_upstream(self):
        def handler(request):
            self.fail("키가 없으면 호출하면 안 됩니다.")
        settings = Settings(map_api_key="fake-map")
        with self.assertRaises(GroqError) as error:
            await self.invoke(handler, settings)
        self.assertEqual(error.exception.code, "groq_config")
        self.assertNotIn("fake-groq", repr(self.settings()))

    async def test_status_errors_sanitized_without_retry(self):
        for status, code in [(401,"groq_auth"),(403,"groq_permission"),(429,"groq_rate_limit"),
                             (400,"groq_request"),(404,"groq_model"),(500,"groq_upstream"),(302,"groq_upstream")]:
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, json={"error":"private-secret"})
            with self.subTest(status=status), self.assertRaises(GroqError) as error:
                await self.invoke(handler)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("private-secret", str(error.exception))
            self.assertEqual(len(calls), 1)

    async def test_timeout_and_connection(self):
        for kind, code in [(httpx.ReadTimeout,"groq_timeout"),(httpx.ConnectError,"groq_connection")]:
            def handler(request):
                raise kind("private-secret", request=request)
            with self.subTest(code=code), self.assertRaises(GroqError) as error:
                await self.invoke(handler)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("private-secret", str(error.exception))

    async def test_bad_payload_and_token_truncation(self):
        for data in [{}, {"choices":[]}, completion("not-json"),
                     completion(json.dumps({"intent":"explain_menu","menu_id":"unknown"})),
                     completion(json.dumps({"intent":"recommend_menu","restaurants":[]})),
                     completion(finish="length"), completion(finish="tool_calls")]:
            with self.subTest(data=data), self.assertRaises(GroqError) as error:
                await self.invoke(lambda r: httpx.Response(200, json=data))
            expected = "groq_truncated" if data.get("choices") and data["choices"][0].get("finish_reason")=="length" else "groq_response"
            self.assertEqual(error.exception.code, expected)
