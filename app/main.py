from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .api import router
from .config import load_settings
from .locations import router as location_router  # 실제 위치 검색용 라우터를 별도로 연결합니다.

STATIC = Path(__file__).resolve().parent.parent / "frontend"
app = FastAPI(title="LunchRun-demo", version="0.1.0")
app.state.settings = load_settings()
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
