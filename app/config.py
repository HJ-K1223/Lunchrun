"""Server-only settings, loaded from the project root regardless of cwd."""
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    map_api_key: SecretStr
    # 지도 SDK용 공개 키입니다. REST 키와 분리하며, 미설정 시 SDK 로딩을 생략합니다.
    kakao_javascript_key: str = ""
    # Groq 키가 없어도 지도는 실행되며 실제 AI 호출 시에만 누락 오류를 반환합니다.
    groq_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    groq_model: str = Field(default="openai/gpt-oss-20b", min_length=1)
    groq_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    groq_max_completion_tokens: int = Field(default=1024, ge=128, le=8192)


def load_settings() -> Settings:
    # Deployment environment variables take precedence over the local file.
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    map_api_key = os.environ.get("MAP_API_KEY", "").strip()
    if not map_api_key:
        # Never include credential values in validation errors.
        raise RuntimeError(
            "MAP_API_KEY가 없거나 비어 있습니다. 프로젝트 루트의 .env "
            "또는 서버 환경변수에 카카오 REST API 키를 설정해 주세요."
        )
    # .env에 선택적으로 추가한 JavaScript 키만 읽습니다. 기존 REST 키는 재사용하지 않습니다.
    return Settings(
        map_api_key=map_api_key,
        kakao_javascript_key=os.environ.get("KAKAO_JAVASCRIPT_KEY", "").strip(),
        # 기존 환경변수가 있으면 .env보다 우선하며 변경 후 서버를 재시작해야 합니다.
        groq_api_key=os.environ.get("GROQ_API_KEY", "").strip(),
        groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b").strip(),
        groq_timeout_seconds=os.environ.get("GROQ_TIMEOUT_SECONDS", "30"),
        groq_max_completion_tokens=os.environ.get("GROQ_MAX_COMPLETION_TOKENS", "1024"),
    )
