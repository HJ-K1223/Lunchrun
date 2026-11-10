"""FastAPI 애플리케이션 생성과 3단계 DB 생명주기 연결."""

from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import load_settings
from .database import Database
from .locations import router as location_router  # 실제 위치 검색용 라우터를 별도로 연결합니다.

STATIC = Path(__file__).resolve().parent.parent / "frontend"
settings = load_settings()
# 애플리케이션 전체에서 엔진 하나를 공유하고 요청마다 세션만 새로 만든다.
database = Database(settings.database_url)


@asynccontextmanager
async def lifespan(application: FastAPI):
    # 로컬 프로토타입에서는 누락된 테이블만 생성합니다. 외부 DB 전환 시 마이그레이션으로 교체합니다.
    await anyio.to_thread.run_sync(application.state.database.init_schema)
    try:
        yield
    finally:
        # 서버 종료 시 연결 풀과 파일 핸들을 정리한다.
        application.state.database.dispose()


app = FastAPI(title="LunchRun-demo", version="0.1.0", lifespan=lifespan)
app.state.settings = settings
app.state.database = database
app.include_router(router)
app.include_router(location_router)  # 추천용 가상 데이터와 위치 검색의 책임을 분리합니다.
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


# 브라우저에 공개해도 되는 JavaScript 키만 반환합니다. 전체 설정이나 REST/Groq 키는 반환하지 않습니다.
@app.get("/api/public-config", include_in_schema=False)
def public_config():
    return {"kakaoJavascriptKey": app.state.settings.kakao_javascript_key}
