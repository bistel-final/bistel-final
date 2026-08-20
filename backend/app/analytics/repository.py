"""검증 통과 SQL 의 readonly 실행기.

sql_validator(V4-D-2.2) 를 통과한 normalized_sql 만 받는다. 원문 SQL 을
직접 받지 않는 것이 계약이다 — 실행 대상은 언제나 검증기의 재직렬화
결과다.

방어 위치
- 1차: 계정 권한 (kosa_readonly, 002_analytics_roles.sql)
- 2차: sql_validator
- 3차: 이 모듈의 세션 설정 — statement_timeout · transaction read only

오류 계약
    DSN·SQL 원문·DB 예외 메시지를 위로 올리지 않는다. 예외 문자열에 담긴
    비밀번호·호스트가 로그로 새는 경로를 차단한다(main.py 전역 핸들러와
    같은 원칙). 진단은 예외 타입 이름까지만 허용한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.common.config import TOOL_DB_TIMEOUT_SEC


class QueryExecutionError(RuntimeError):
    """실행 실패. 메시지는 sanitize 된 요약만 담는다."""


@dataclass(frozen=True)
class QueryExecution:
    """실행 결과. rows 는 컬럼명 -> 값 dict 목록이다."""

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


def execute_validated_select(engine: Engine, normalized_sql: str) -> QueryExecution:
    """검증 통과 SQL 한 건을 readonly 세션에서 실행한다.

    - statement_timeout: TOOL_DB_TIMEOUT_SEC 초과 시 DB 가 실행을 끊는다
    - transaction read only: 계정 권한이 뚫려도 트랜잭션 수준에서 한 번 더
      쓰기를 거부한다
    """
    timeout_ms = int(TOOL_DB_TIMEOUT_SEC * 1000)

    try:
        with engine.connect() as connection:
            connection.execute(text(f"SET statement_timeout = {timeout_ms}"))
            connection.execute(text("SET transaction_read_only = on"))

            result = connection.execute(text(normalized_sql))
            columns = list(result.keys())
            rows = [dict(mapping) for mapping in result.mappings()]

        return QueryExecution(columns=columns, rows=rows)
    except SQLAlchemyError as exc:
        # DB 오류 원문(구문 위치·값·접속 정보 포함 가능)을 싣지 않는다.
        raise QueryExecutionError(
            f"쿼리 실행에 실패했다 ({type(exc).__name__})."
        ) from exc
