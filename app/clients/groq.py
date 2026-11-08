"""3단계: Groq 질문 해석 전용 클라이언트입니다. 앱 채팅 경로 연결은 다음 단계입니다."""
import asyncio
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from ..chat_policy import QuestionInterpretation, build_interpretation_messages, parse_interpretation
from ..config import Settings
from ..schemas import Conditions


@dataclass
class GroqError(Exception):
    # 외부 응답 원문·질문·인증 헤더는 오류 객체에 저장하지 않습니다.
    code: str
    message: str
    status_code: int = 502

    def __str__(self) -> str:
        return self.message


class GroqClient:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    async def check_connection(self) -> dict:
        # 생성 토큰을 쓰지 않고 모델 목록 조회로 인증·설정 모델의 사용 가능 여부를 확인합니다.
        key = self._settings.groq_api_key.get_secret_value().strip()
        if not key:
            return dict(status="disconnected", code="groq_config", message="Groq API 키가 설정되지 않았습니다.")
        try:
            async with asyncio.timeout(self._settings.groq_timeout_seconds):
                async with httpx.AsyncClient(timeout=self._settings.groq_timeout_seconds,
                                             follow_redirects=False, transport=self._transport) as client:
                    response = await client.get("https://api.groq.com/openai/v1/models",
                                                headers={"Authorization": f"Bearer {key}"})
        except (TimeoutError, httpx.TimeoutException):
            return dict(status="unknown", code="groq_timeout", message="연결 확인 시간이 초과되었습니다.")
        except httpx.RequestError:
            return dict(status="unknown", code="groq_connection", message="Groq 네트워크 연결을 확인하지 못했습니다.")
        # 오류 원문이나 모델 전체 목록은 브라우저로 보내지 않습니다.
        if response.status_code in (401, 403):
            return dict(status="disconnected", code="groq_auth" if response.status_code == 401 else "groq_permission",
                        message="Groq 인증 키 또는 사용 권한을 확인해 주세요.")
        if response.status_code != 200:
            return dict(status="unknown", code="groq_rate_limit" if response.status_code == 429 else "groq_upstream",
                        message="사용량 제한 또는 서비스 오류로 연결 상태를 확인하지 못했습니다.")
        try:
            data = response.json()["data"]
            if not isinstance(data, list) or any(not isinstance(m, dict) for m in data):
                raise ValueError
            available = any(m.get("id") == self._settings.groq_model and m.get("active") is True for m in data)
        except (ValueError, TypeError, KeyError):
            return dict(status="unknown", code="groq_response", message="연결 확인 응답 형식이 올바르지 않습니다.")
        if not available:
            return dict(status="disconnected", code="groq_model", message="설정한 Groq 모델을 사용할 수 없습니다.")
        return dict(status="connected", code="ok", message="인증과 설정 모델을 확인했습니다. 채팅 성공을 보장하는 검사는 아닙니다.")

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        # 테스트에서는 모의 HTTP 전송을 주입하며 실제 키를 사용하지 않습니다.
        self._settings = settings
        self._transport = transport

    async def interpret(self, message: str, conditions: Conditions, radius_m: int,
                        menus: list[dict], selected_menu_id: str | None = None) -> QuestionInterpretation:
        key = self._settings.groq_api_key.get_secret_value().strip()
        if not key:
            raise GroqError("groq_config", "Groq API 키가 설정되지 않았습니다. 서버 설정을 확인해 주세요.", 503)
        # 호출자가 잘못된 데이터를 주더라도 외부 요청 이전에 검사합니다.
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 500:
            raise ValueError("질문은 1~500자로 입력해 주세요.")
        if type(radius_m) is not int or not 50 <= radius_m <= 500:
            raise ValueError("반경은 50~500m 정수여야 합니다.")
        menu_ids = {m["id"] for m in menus}
        if selected_menu_id is not None and selected_menu_id not in menu_ids:
            raise ValueError("선택 메뉴가 메뉴 목록에 없습니다.")
        try:
            messages = build_interpretation_messages(message.strip(), conditions.model_dump(), radius_m, menus, selected_menu_id)
        except (OSError, ValueError, KeyError, TypeError):
            raise GroqError("groq_policy", "질문 해석 지침 파일을 확인해 주세요.", 503) from None
        body = {"model": self._settings.groq_model, "messages": messages,
                "max_completion_tokens": self._settings.groq_max_completion_tokens,
                "response_format": {"type": "json_object"}, "temperature": 0}
        # GPT-OSS는 짧은 해석 작업에 낮은 추론 강도를 사용하고 추론 텍스트를 반환하지 않습니다.
        if self._settings.groq_model.startswith("openai/gpt-oss-"):
            body.update(reasoning_effort="low", include_reasoning=False)
        try:
            # 개별 통신 대기와 전체 요청 모두 제한하며 자동 재시도·리다이렉트는 하지 않습니다.
            async with asyncio.timeout(self._settings.groq_timeout_seconds):
                async with httpx.AsyncClient(timeout=self._settings.groq_timeout_seconds,
                                             follow_redirects=False, transport=self._transport) as client:
                    response = await client.post(self.URL, json=body,
                                                 headers={"Authorization": f"Bearer {key}"})
        except (TimeoutError, httpx.TimeoutException):
            raise GroqError("groq_timeout", "AI 응답 시간이 초과되었습니다. 다시 시도해 주세요.", 504) from None
        except httpx.RequestError:
            raise GroqError("groq_connection", "AI 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.", 503) from None
        # 서버 키의 인증 실패를 사용자 로그인 실패(401)와 혼동하지 않도록 503으로 반환합니다.
        errors = {
            401: ("groq_auth", "Groq API 키 인증에 실패했습니다. 서버 설정을 확인해 주세요.", 503),
            403: ("groq_permission", "해당 Groq 모델의 사용 권한을 확인해 주세요.", 503),
            429: ("groq_rate_limit", "AI 사용 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.", 503),
            400: ("groq_request", "Groq 모델과 요청 옵션을 확인해 주세요.", 502),
            404: ("groq_model", "Groq 모델 이름과 사용 가능 여부를 확인해 주세요.", 503),
        }
        if response.status_code in errors:
            raise GroqError(*errors[response.status_code])
        if response.status_code != 200:
            raise GroqError("groq_upstream", "AI 서비스가 정상 응답하지 않았습니다.")
        try:
            data = response.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise GroqError("groq_truncated", "AI 출력 한도에 도달해 응답이 잘렸습니다. 서버의 출력 토큰 설정을 확인해 주세요.")
            if choice.get("finish_reason") != "stop":
                raise ValueError("Incomplete response")
            result = choice["message"]
            if result.get("tool_calls") or result.get("refusal"):
                raise ValueError("Unsupported response")
            # JSON 모드만으로 의미·범위가 보장되지 않으므로 Pydantic 검증을 반드시 거칩니다.
            return parse_interpretation(result["content"], menu_ids)
        except (ValidationError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise GroqError("groq_response", "AI 응답을 안전하게 해석하지 못했습니다. 다시 질문해 주세요.") from None
