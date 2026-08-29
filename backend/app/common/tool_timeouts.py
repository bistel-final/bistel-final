"""Agent read Tool의 server-side hard timeout 공통 계약.

caller가 제어권을 회수하는 soft deadline과 DB server가 실행 중인
statement/transaction을 중단하는 hard timeout을 분리한다. 이 모듈은
``app.common.config``를 import하지 않는다. config가 caller 정본을 import해
``TOOL_DB_TIMEOUT_SEC < 8``을 검증해도 순환 import가 생기지 않게 하기
위함이다.
"""

from __future__ import annotations

import math
from typing import Final

from neo4j.exceptions import Neo4jError
from psycopg import errors as psycopg_errors
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from app.common.tool_deadlines import READ_TOOL_CALLER_DEADLINE_SECONDS

POSTGRES_STATEMENT_TIMEOUT_CODE: Final[str] = "DB_STATEMENT_TIMEOUT"
NEO4J_TRANSACTION_TIMEOUT_CODE: Final[str] = "NEO4J_TRANSACTION_TIMEOUT"

# Neo4j 5.x의 server default 종료와 Query(timeout=...)의 client configuration으로
# 더 짧은 제한을 적용한 경우만 허용한다.
NEO4J_TRANSACTION_TIMEOUT_CODES: Final[frozenset[str]] = frozenset(
    {
        "Neo.ClientError.Transaction.TransactionTimedOut",
        "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
    }
)

_SET_LOCAL_STATEMENT_TIMEOUT = text(
    "SELECT set_config('statement_timeout', :timeout_ms, true)"
)


class DependencyTimeoutError(TimeoutError):
    """driver 원문을 담지 않는 의존성 timeout."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _timeout_milliseconds(timeout_seconds: float) -> int:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds는 0보다 큰 유한한 수여야 합니다")
    timeout_ms = int(timeout_seconds * 1000)
    if timeout_ms < 1:
        raise ValueError("timeout_seconds는 1ms 이상이어야 합니다")
    return timeout_ms


def apply_postgres_statement_timeout(
    connection: Connection,
    *,
    timeout_seconds: float,
) -> None:
    """PostgreSQL transaction-local statement timeout을 parameterized 설정한다."""

    timeout_ms = _timeout_milliseconds(timeout_seconds)
    connection.execute(
        _SET_LOCAL_STATEMENT_TIMEOUT,
        {"timeout_ms": str(timeout_ms)},
    )


def postgres_timeout_error(exc: BaseException) -> DependencyTimeoutError | None:
    """SQLAlchemy/raw psycopg의 57014만 sanitized timeout으로 바꾼다."""

    if isinstance(exc, psycopg_errors.QueryCanceled):
        return DependencyTimeoutError(POSTGRES_STATEMENT_TIMEOUT_CODE)
    if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) == "57014":
        return DependencyTimeoutError(POSTGRES_STATEMENT_TIMEOUT_CODE)
    return None


def neo4j_timeout_error(exc: BaseException) -> DependencyTimeoutError | None:
    """Neo4j server가 반환한 transaction-timeout code만 사상한다."""

    if isinstance(exc, Neo4jError) and exc.code in NEO4J_TRANSACTION_TIMEOUT_CODES:
        return DependencyTimeoutError(NEO4J_TRANSACTION_TIMEOUT_CODE)
    return None


__all__ = [
    "DependencyTimeoutError",
    "NEO4J_TRANSACTION_TIMEOUT_CODE",
    "NEO4J_TRANSACTION_TIMEOUT_CODES",
    "POSTGRES_STATEMENT_TIMEOUT_CODE",
    "READ_TOOL_CALLER_DEADLINE_SECONDS",
    "apply_postgres_statement_timeout",
    "neo4j_timeout_error",
    "postgres_timeout_error",
]
