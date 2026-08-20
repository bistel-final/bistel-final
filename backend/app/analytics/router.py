"""Analytics router — 선택 확장 Text2SQL 수직 슬라이스.

API v3 §5.2 선택 확장 계약:
    POST /analytics/query     자연어(스파이크에서는 SQL 원문 포함) → 검증 → 실행
    POST /analytics/validate  SQL 실행 없는 검증

정책 거부는 HTTP 200 으로 응답한다(is_rejected=true). 422 는 요청 형식
오류에만 쓴다 — 둘을 구분하는 것이 계약이다(요구사항 FR-D-02).
"""

from fastapi import APIRouter

from app.analytics.schemas import (
    AnalysisQueryRequest,
    AnalysisQueryResponse,
    SqlValidateRequest,
    SqlValidateResponse,
    ValidationCheck,
)
from app.analytics.service import CHECK_LABELS, run_analysis_query
from app.analytics.sql_validator import validate_sql

router = APIRouter(tags=["Analytics"])


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
