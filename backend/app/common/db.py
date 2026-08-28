from collections.abc import Iterator
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Connection, Engine, make_url

from app.common.config import (
    APP_DATABASE_URL,
    APP_DB_PASSWORD,
    APP_DB_USER,
    EVALUATION_DB_PASSWORD,
    EVALUATION_DB_USER,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PORT,
    READONLY_PASSWORD,
    READONLY_USER,
)

#: 합성 라벨(fault_code) 평가 전용 DB. V5-CM-3.5 role matrix의 evaluation
#: profile과 정확히 하나(kosa_text2sql)로 고정한다 — Runtime처럼 여러 DB
#: 중 하나를 고르는 개념이 아니다(설계서 v2.1 2.6).
EVALUATION_POSTGRES_DB = "kosa_text2sql"


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
_evaluation_engine: Engine | None = None


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


def create_evaluation_postgres_url() -> URL:
    """kosa_evaluation role 전용 DSN(V5-A-2.3).

    fault_code 등 합성 라벨은 이 role로만 읽는다(시스템설계서 v2.1 2.6,
    V5-CM-3.5 role matrix `ManagedRole.EVALUATION`). `create_app_postgres_url`이
    `APP_DB_USER`를 고정 검증하는 것과 같은 이유로 계정명·DB명을 여기서도
    고정 검증한다 — role이 1차 방어선이므로 설정 실수로 그 방어선이
    조용히 무너지면 안 된다.
    """

    if EVALUATION_DB_USER != "kosa_evaluation":
        raise RuntimeError("EVALUATION_DB_USER는 kosa_evaluation이어야 합니다")
    if EVALUATION_DB_PASSWORD is None:
        raise RuntimeError("EVALUATION_DB_PASSWORD가 없습니다")
    return URL.create(
        drivername="postgresql+psycopg",
        username=EVALUATION_DB_USER,
        password=EVALUATION_DB_PASSWORD,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=EVALUATION_POSTGRES_DB,
    )


def get_evaluation_engine() -> Engine:
    """합성 라벨 평가 전용 engine. 다른 engine과 같은 원칙으로 첫 사용
    시점에 생성한다 — 평가 DSN이 없어도(V5-A-2.4 holdout 평가를 아직
    실행하지 않는 한) 앱 기동·다른 engine 사용에는 영향이 없다.
    """

    global _evaluation_engine

    if _evaluation_engine is not None:
        return _evaluation_engine
    with _engine_lock:
        if _evaluation_engine is None:
            _evaluation_engine = _create_engine(create_evaluation_postgres_url())
        return _evaluation_engine


def get_db_connection() -> Iterator[Connection]:
    with get_app_engine().connect() as connection:
        yield connection


def get_readonly_connection() -> Iterator[Connection]:
    with get_readonly_engine().connect() as connection:
        yield connection


def dispose_engines() -> None:
    global _app_engine, _readonly_engine, _evaluation_engine

    with _engine_lock:
        engines = (_app_engine, _readonly_engine, _evaluation_engine)
        _app_engine = None
        _readonly_engine = None
        _evaluation_engine = None
    for engine in engines:
        if engine is not None:
            engine.dispose()
