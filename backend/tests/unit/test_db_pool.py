"""V4-D-1.2 pool factory 단위 테스트.

실제 DB 접속 없이 검증한다. engine 생성은 지연이므로 connect 를 호출하지
않는 한 네트워크가 필요 없다.
"""

from __future__ import annotations

import pytest

from app.analytics.db_pool import (
    AnalyticsPoolFactory,
    LogicalDb,
    PoolConfigurationError,
    PoolRole,
)

RUNTIME_QUERY_DSN = (
    "postgresql+psycopg://kosa_readonly:secret_pw@db.example.com:53001/kosa_agent"
)
RUNTIME_LOGGER_DSN = (
    "postgresql+psycopg://kosa_query_logger:secret_pw@db.example.com:53001/kosa_agent"
)


@pytest.fixture()
def factory() -> AnalyticsPoolFactory:
    instance = AnalyticsPoolFactory()
    yield instance
    instance.dispose_all()


def _set_runtime_dsns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT2SQL_DATABASE_URL", RUNTIME_QUERY_DSN)
    monkeypatch.setenv("TEXT2SQL_LOG_DATABASE_URL", RUNTIME_LOGGER_DSN)


# ---------------------------------------------------------------------------
# 지연 생성·캐시
# ---------------------------------------------------------------------------


def test_same_key_returns_same_engine(
    factory: AnalyticsPoolFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_runtime_dsns(monkeypatch)

    first = factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
    second = factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)

    assert first is second


def test_query_and_logger_pools_are_distinct(
    factory: AnalyticsPoolFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """query 와 logger 는 계정이 다르므로 engine 도 달라야 한다."""
    _set_runtime_dsns(monkeypatch)

    query = factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
    logger = factory.get_engine(LogicalDb.RUNTIME, PoolRole.LOGGER)

    assert query is not logger
    assert query.url.username == "kosa_readonly"
    assert logger.url.username == "kosa_query_logger"


def test_evaluation_dsn_is_optional_until_requested(
    factory: AnalyticsPoolFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """평가 DSN 이 비어도 Runtime pool 은 정상 동작해야 한다 (V4-D-7.x 전)."""
    _set_runtime_dsns(monkeypatch)
    monkeypatch.delenv("TEXT2SQL_EVAL_DATABASE_URL", raising=False)

    # Runtime 은 된다.
    factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)

    # 평가는 요청하는 순간에만 실패한다.
    with pytest.raises(PoolConfigurationError):
        factory.get_engine(LogicalDb.EVALUATION, PoolRole.QUERY)


# ---------------------------------------------------------------------------
# 실패 계약
# ---------------------------------------------------------------------------


def test_missing_dsn_raises_with_env_name(
    factory: AnalyticsPoolFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TEXT2SQL_DATABASE_URL", raising=False)

    with pytest.raises(PoolConfigurationError) as excinfo:
        factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)

    assert "TEXT2SQL_DATABASE_URL" in str(excinfo.value)


def test_malformed_dsn_error_hides_password(
    factory: AnalyticsPoolFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """잘못된 DSN 이라도 원본(비밀번호 포함)이 메시지에 남으면 안 된다."""
    monkeypatch.setenv("TEXT2SQL_DATABASE_URL", "://supersecret@no-scheme")

    with pytest.raises(PoolConfigurationError) as excinfo:
        factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)

    assert "supersecret" not in str(excinfo.value)


def test_wrong_driver_is_rejected(
    factory: AnalyticsPoolFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "TEXT2SQL_DATABASE_URL",
        "mysql://kosa_readonly:pw@db.example.com:3306/kosa_agent",
    )

    with pytest.raises(PoolConfigurationError) as excinfo:
        factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)

    assert "postgresql+psycopg" in str(excinfo.value)


# ---------------------------------------------------------------------------
# DSN 비노출
# ---------------------------------------------------------------------------


def test_pool_info_masks_password(
    factory: AnalyticsPoolFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_runtime_dsns(monkeypatch)

    info = factory.get_pool_info(LogicalDb.RUNTIME, PoolRole.QUERY)
    described = info.describe()

    assert "secret_pw" not in described
    assert "kosa_readonly" in described
    assert "kosa_agent" in described


def test_engine_repr_hides_password(
    factory: AnalyticsPoolFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLAlchemy URL repr 은 비밀번호를 *** 로 가린다. 회귀 방지로 고정한다."""
    _set_runtime_dsns(monkeypatch)

    engine = factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)

    assert "secret_pw" not in repr(engine.url)
    assert "secret_pw" not in str(engine)
