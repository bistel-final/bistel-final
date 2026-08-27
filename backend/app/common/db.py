from collections.abc import Iterator
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Connection, Engine, make_url

from app.common.config import (
    APP_DATABASE_URL,
    APP_DB_PASSWORD,
    APP_DB_USER,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PORT,
    READONLY_PASSWORD,
    READONLY_USER,
)


def create_postgres_url(username: str, password: str) -> URL:
    return URL.create(
        drivername="postgresql+psycopg",
        username=username,
        password=password,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
    )


def create_app_postgres_url() -> URL:
    if APP_DATABASE_URL is not None:
        url = make_url(APP_DATABASE_URL)
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise RuntimeError("APP_DATABASE_URL은 PostgreSQL psycopg DSN이어야 합니다")
        if url.username != "kosa_app":
            raise RuntimeError("APP_DATABASE_URL username은 kosa_app 이어야 합니다")
        if url.database not in {"kosa_agent", "kosa_agent_e2e"}:
            raise RuntimeError(
                "APP_DATABASE_URL database는 Runtime profile이어야 합니다"
            )
        if not url.password or not url.host or url.port is None:
            raise RuntimeError("APP_DATABASE_URL 연결 정보가 불완전합니다")
        return url.set(drivername="postgresql+psycopg")
    if APP_DB_PASSWORD is None:
        raise RuntimeError("APP_DB_PASSWORD가 없습니다")
    if POSTGRES_DB not in {"kosa_agent", "kosa_agent_e2e"}:
        raise RuntimeError("POSTGRES_DB는 Runtime profile이어야 합니다")
    return create_postgres_url(APP_DB_USER, APP_DB_PASSWORD)


_engine_lock = Lock()
_app_engine: Engine | None = None
_readonly_engine: Engine | None = None


def _create_engine(url: URL) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 5},
    )


def get_app_engine() -> Engine:
    """앱 전용 engine을 첫 DB 사용 시 생성한다.

    credential 검증을 늦추는 것이지 완화하는 것이 아니다. URL·role·Runtime DB·연결정보
    검사는 이 함수가 부르는 ``create_app_postgres_url``에서 그대로 fail-closed다.
    """

    global _app_engine

    if _app_engine is not None:
        return _app_engine
    with _engine_lock:
        if _app_engine is None:
            _app_engine = _create_engine(create_app_postgres_url())
        return _app_engine


def get_readonly_engine() -> Engine:
    """기존 readonly engine도 import 시점이 아닌 첫 사용 시 생성한다."""

    global _readonly_engine

    if _readonly_engine is not None:
        return _readonly_engine
    with _engine_lock:
        if _readonly_engine is None:
            _readonly_engine = _create_engine(
                create_postgres_url(
                    username=READONLY_USER,
                    password=READONLY_PASSWORD,
                )
            )
        return _readonly_engine


def get_db_connection() -> Iterator[Connection]:
    with get_app_engine().connect() as connection:
        yield connection


def get_readonly_connection() -> Iterator[Connection]:
    with get_readonly_engine().connect() as connection:
        yield connection


def dispose_engines() -> None:
    global _app_engine, _readonly_engine

    with _engine_lock:
        engines = (_app_engine, _readonly_engine)
        _app_engine = None
        _readonly_engine = None
    for engine in engines:
        if engine is not None:
            engine.dispose()
