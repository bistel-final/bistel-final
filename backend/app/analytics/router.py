"""Analytics router — 선택 확장 Text2SQL 수직 슬라이스.

API v3 §5.2 선택 확장 계약:
    POST /analytics/query     자연어(스파이크에서는 SQL 원문 포함) → 검증 → 실행
    POST /analytics/validate  SQL 실행 없는 검증

정책 거부는 HTTP 200 으로 응답한다(is_rejected=true). 422 는 요청 형식
오류에만 쓴다 — 둘을 구분하는 것이 계약이다(요구사항 FR-D-02).
"""

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.analytics.audit import (
    AuditLogItem,
    AuditLogPageResponse,
    fetch_audit_logs,
    fetch_audit_logs_paged,
)
from app.analytics.cypher_service import run_graph_query
from app.analytics.evaluation_store import list_evaluations
from app.analytics.query_log import QueryHistoryUnavailableError, fetch_query_history
from app.analytics.schemas import (
    AnalysisQueryRequest,
    AnalysisQueryResponse,
    EvaluationListResponse,
    GraphQueryRequest,
    GraphQueryResponse,
    NlQueryHistoryResponse,
    SqlValidateRequest,
    SqlValidateResponse,
    ValidationCheck,
)
from app.analytics.service import CHECK_LABELS, run_analysis_query
from app.analytics.sql_validator import validate_sql
from app.common.db import get_app_engine

router = APIRouter(tags=["Analytics"])


@router.get("/analytics/history", response_model=NlQueryHistoryResponse)
def get_query_history(
    is_valid: bool | None = None,
    is_rejected: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> NlQueryHistoryResponse:
    """질의 이력 조회 (V5-D-2.4, FR-D-05 · evaluation-only).

    kosa_text2sql 의 nl_query_log 만 읽는다. 저장소 미구성·장애는 503 —
    이 기능 불능이 기본 화면을 막지 않는다 (NFR-17).
    """
    try:
        return fetch_query_history(
            is_valid=is_valid,
            is_rejected=is_rejected,
            date_from=date_from,
            date_to=date_to,
            page=page,
            size=size,
        )
    except QueryHistoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/analytics/evaluations", response_model=EvaluationListResponse)
def get_evaluations(
    latest: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> EvaluationListResponse:
    """Text2SQL 평가 이력 (V5-D-2.6, FR-D-08) — artifact 의 read-only projection.

    채점 로직은 재구현하지 않는다. 명세 정렬 executed_at DESC, run_id DESC.
    """
    return list_evaluations(latest=latest, page=page, size=size)


@router.get("/audit-logs", response_model=list[AuditLogItem])
def get_audit_logs(
    event_type: str | None = None,
    actor_type: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[AuditLogItem]:
    """호환 필수 감사로그 조회 — bare array (API v3 3.8, FR-D-07 · NFR-05).

    화면 total 은 items.length 로 해석한다. 페이지·집계는 /paged 에서만 제공한다.
    entity_id 는 부분 일치, date 필터는 Asia/Seoul 자정 기준(NFR-13)이다.
    """
    return fetch_audit_logs(
        get_app_engine(),
        event_type=event_type,
        actor_type=actor_type,
        entity_type=entity_type,
        entity_id=entity_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/audit-logs/paged", response_model=AuditLogPageResponse)
def get_audit_logs_paged(
    event_type: str | None = None,
    actor_type: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> AuditLogPageResponse:
    """선택 확장 — PageEnvelope + 동일 필터 전체 집계 (API v3 5.2, V5-D-1.2)."""
    return fetch_audit_logs_paged(
        get_app_engine(),
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


@router.post("/analytics/graph-query", response_model=GraphQueryResponse)
def post_analytics_graph_query(request: GraphQueryRequest) -> GraphQueryResponse:
    """그래프(온톨로지) 자연어 질의 한 건을 검증·실행한다.

    B 합의(#238): backend 전용 경로 — 사용자 Cypher 직접 입력 경로는
    없다 (SQL 과 달리 passthrough 미제공이 의도된 차이다).
    """
    return run_graph_query(request.question)


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
