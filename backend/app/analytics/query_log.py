"""질의 이력 기록·조회 — V5-D-2.4 (FR-D-05 · NFR-01, evaluation-only).

- 기록: (EVALUATION, LOGGER) pool = kosa_query_logger@kosa_text2sql — INSERT 만.
- 조회: (EVALUATION, QUERY)  pool = kosa_readonly@kosa_text2sql — SELECT 만.
- runtime·E2E DB 에는 테이블도 쓰기도 없다. DSN 미설정 시 다른 DB 로
  fallback 하지 않는다 (기록은 조용히 생략하고 id 를 null 로, 조회는 503).
- 기록 실패가 질의 응답을 깨지 않는다 — Text2SQL 은 이력 없이도 동작한다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.analytics.db_pool import (
    LogicalDb,
    PoolConfigurationError,
    PoolRole,
    pool_factory,
)
from app.analytics.schemas import (
    AnalysisQueryResponse,
    NlQueryHistoryResponse,
    NlQueryLogItem,
    NlQueryOutcome,
)

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))


def outcome_of(response: AnalysisQueryResponse) -> NlQueryOutcome:
    """응답 계약(schemas 의 validator 가 강제)에서 이력 outcome 을 파생한다."""
    if response.is_rejected:
        return NlQueryOutcome.POLICY_REJECTED
    if response.error_msg is not None:
        return NlQueryOutcome.DB_ERROR
    return NlQueryOutcome.SUCCESS


_INSERT = text(
    "INSERT INTO nl_query_log"
    " (question, generated_sql, outcome, is_valid, is_rejected,"
    "  reject_reason, row_cnt, latency_ms, error_msg)"
    " VALUES (:question, :generated_sql, :outcome, :is_valid, :is_rejected,"
    "  :reject_reason, :row_cnt, :latency_ms, :error_msg)"
    " RETURNING nl_query_log_id"
)


def record_query_log(response: AnalysisQueryResponse) -> int | None:
    """질의 한 건을 기록하고 log id 를 돌려준다. 실패 시 None (질의는 계속된다)."""
    outcome = outcome_of(response)
    params: dict[str, Any] = {
        "question": response.question,
        "generated_sql": response.generated_sql,
        "outcome": outcome.value,
        "is_valid": response.is_valid,
        "is_rejected": response.is_rejected,
        "reject_reason": response.reject_reason,
        # NlQueryLogItem outcome 규칙: SUCCESS 만 row_cnt 를 가진다
        "row_cnt": response.row_count if outcome is NlQueryOutcome.SUCCESS else None,
        "latency_ms": response.latency_ms,
        "error_msg": response.error_msg,
    }
    try:
        engine = pool_factory.get_engine(LogicalDb.EVALUATION, PoolRole.LOGGER)
        with engine.begin() as connection:
            row = connection.execute(_INSERT, params).one()
            return int(row[0])
    except PoolConfigurationError:
        # evaluation-only 선택 확장 — logger DSN 미설정 배포에서는 기록을 생략한다
        logger.info("nl_query_log 기록 생략: logger DSN 미설정 (evaluation-only)")
        return None
    except SQLAlchemyError as exc:
        # 반복 호출에서 로그가 범람되지 않도록 traceback 없이 한 줄로 남긴다
        logger.warning("nl_query_log 기록 실패 — 질의 응답은 계속한다: %s", exc)
        return None


class QueryHistoryUnavailableError(RuntimeError):
    """이력 저장소(kosa_text2sql) 미구성·접속 불가. router 가 503 으로 변환한다."""


def fetch_query_history(
    *,
    is_valid: bool | None = None,
    is_rejected: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    size: int = 20,
) -> NlQueryHistoryResponse:
    """질의 이력 페이지 조회 — asked_at DESC, nl_query_log_id DESC."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if is_valid is not None:
        clauses.append("is_valid = :is_valid")
        params["is_valid"] = is_valid
    if is_rejected is not None:
        clauses.append("is_rejected = :is_rejected")
        params["is_rejected"] = is_rejected
    if date_from:
        clauses.append("asked_at >= :date_from")
        params["date_from"] = datetime.combine(date_from, time.min, _KST)
    if date_to:
        clauses.append("asked_at < :date_to")
        params["date_to"] = datetime.combine(
            date_to + timedelta(days=1), time.min, _KST
        )
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    items_sql = text(
        "SELECT nl_query_log_id, asked_at, question, generated_sql, outcome,"
        "       is_valid, is_rejected, reject_reason, row_cnt, latency_ms, error_msg"
        f" FROM nl_query_log {where}"
        " ORDER BY asked_at DESC, nl_query_log_id DESC"
        " LIMIT :limit OFFSET :offset"
    )
    total_sql = text(f"SELECT count(*) FROM nl_query_log {where}")

    try:
        engine = pool_factory.get_engine(LogicalDb.EVALUATION, PoolRole.QUERY)
        with engine.connect() as connection:
            rows = connection.execute(
                items_sql, {**params, "limit": size, "offset": (page - 1) * size}
            ).mappings()
            items = [
                NlQueryLogItem(
                    nl_query_log_id=row["nl_query_log_id"],
                    asked_at=row["asked_at"].astimezone(_KST),
                    question=row["question"],
                    generated_sql=row["generated_sql"],
                    outcome=NlQueryOutcome(row["outcome"]),
                    is_valid=row["is_valid"],
                    is_rejected=row["is_rejected"],
                    reject_reason=row["reject_reason"],
                    row_cnt=row["row_cnt"],
                    latency_ms=row["latency_ms"],
                    error_msg=row["error_msg"],
                )
                for row in rows
            ]
            total = int(connection.execute(total_sql, params).scalar_one())
    except PoolConfigurationError as exc:
        raise QueryHistoryUnavailableError(
            "질의 이력 저장소가 구성되지 않았다 (evaluation DSN 미설정)."
        ) from exc
    except SQLAlchemyError as exc:
        raise QueryHistoryUnavailableError("질의 이력 저장소 조회에 실패했다.") from exc

    return NlQueryHistoryResponse(items=items, total=total, page=page, size=size)
