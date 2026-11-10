"""3단계: DB 엔진, 세션, 스키마 초기화와 FastAPI 의존성 구성."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event, make_url
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """SQLite 연결마다 무결성·동시성 관련 설정을 빠짐없이 적용한다."""

    cursor = dbapi_connection.cursor()
    try:
        # SQLite는 기본적으로 FK 검사를 끄므로 연결할 때마다 명시적으로 켠다.
        cursor.execute("PRAGMA foreign_keys=ON")
        # 잠깐의 쓰기 경합은 즉시 실패하지 않고 최대 5초 기다린다.
        cursor.execute("PRAGMA busy_timeout=5000")
        # WAL 모드로 읽기와 쓰기의 충돌을 줄인다.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


class Database:
    """애플리케이션이 공유할 엔진과 요청별 단기 세션 팩토리를 관리한다."""

    def __init__(self, database_url: str):
        url = make_url(database_url)
        connect_args: dict[str, object] = {}
        if url.get_backend_name() == "sqlite":
            # FastAPI의 스레드풀에서도 SQLite 연결을 사용할 수 있게 설정한다.
            connect_args.update(check_same_thread=False, timeout=5)
            # 처음 실행하는 개발자도 별도 폴더 생성 없이 DB를 만들 수 있게 한다.
            if url.database not in (None, "", ":memory:"):
                Path(url.database).expanduser().resolve().parent.mkdir(
                    parents=True, exist_ok=True
                )

        self.engine: Engine = create_engine(
            url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        if url.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", _configure_sqlite)

        # 세션은 요청이나 작업 단위로 열고 닫으며 엔진만 애플리케이션이 공유한다.
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    def init_schema(self) -> None:
        """로컬 프로토타입에서 아직 없는 테이블만 생성한다."""

        Base.metadata.create_all(bind=self.engine)

    def dispose(self) -> None:
        self.engine.dispose()


def get_db(request: Request) -> Generator[Session, None, None]:
    """FastAPI 요청마다 독립된 세션을 열고 응답 후 자동으로 닫는다."""

    database: Database = request.app.state.database
    with database.session_factory() as session:
        yield session
