"""Analytics router — 선택 확장 Text2SQL 수직 슬라이스.

API v3 §5.2 선택 확장 계약:
    POST /analytics/query     자연어(스파이크에서는 SQL 원문 포함) → 검증 → 실행
    POST /analytics/validate  SQL 실행 없는 검증

정책 거부는 HTTP 200 으로 응답한다(is_rejected=true). 422 는 요청 형식
오류에만 쓴다 — 둘을 구분하는 것이 계약이다(요구사항 FR-D-02).
"""

from datetime import date

from fastapi import APIRouter, Query

from app.analytics.audit import AuditLogPageResponse, fetch_audit_logs
from app.analytics.schemas import (
    AnalysisQueryRequest,
    AnalysisQueryResponse,
    SqlValidateRequest,
    SqlValidateResponse,
    ValidationCheck,
)
from app.analytics.service import CHECK_LABELS, run_analysis_query
from app.analytics.sql_validator import validate_sql
from app.common.db import engine

router = APIRouter(tags=["Analytics"])


@router.get("/audit-logs", response_model=AuditLogPageResponse)
def get_audit_logs(
    event_type: str | None = None,
    actor_type: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
) -> AuditLogPageResponse:
    """감사로그 조회(append-only · 읽기 전용, FR-D-07 · NFR-05).

    entity_id 는 부분 일치, date 필터는 Asia/Seoul 자정 기준(NFR-13)이다.
    """
    return fetch_audit_logs(
        engine,
        event_type=event_type,
        actor_type=actor_type,
        entity_type=entity_type,
        entity_id=entity_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        size=size,
    )


@router.post("/analytics/query", response_model=AnalysisQueryResponse)
def post_analytics_query(request: AnalysisQueryRequest) -> AnalysisQueryResponse:
    """자연어 질의 한 건을 검증·실행한다."""
    return run_analysis_query(request.question)


@router.post("/analytics/validate", response_model=SqlValidateResponse)
def post_analytics_validate(request: SqlValidateRequest) -> SqlValidateResponse:
    """SQL 을 실행 없이 검증한다. 어떤 입력에도 실행 경로가 없다."""
    result = validate_sql(request.sql)
    return SqlValidateResponse(
        valid=result.valid,
        normalized_sql=result.normalized_sql,
        reason=result.reason or ("통과" if result.valid else "검증 실패"),
        checks=[
            ValidationCheck(
                key=check.key,
                label=CHECK_LABELS.get(check.key, check.key),
                ok=check.passed,
            )
            for check in result.checks
        ],
    )
