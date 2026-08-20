"""Text2SQL 수직 슬라이스 orchestration (개발 스파이크).

흐름: question → 계획 생성(LLM mock) → sql_validator → readonly 실행 → 응답.

LLM mock
    실제 LLM 은 아직 연결하지 않는다(V5-D-2.3 본구현에서 교체). 스파이크의
    목적은 "LLM 이 SQL 을 줬다 치고" 이후의 검증·실행 경로를 실제 DB 로
    증명하는 것이므로, planner 는 두 가지로 대신한다.
    1. question 첫 토큰이 SQL 키워드면 question 자체를 LLM 출력 SQL 로 간주
       (쓰기 구문도 그대로 통과시킨다 — 그걸 막는 것이 validator 의 몫이다)
    2. 등록된 fixture 질문이면 대응 SQL 반환

응답 계약 (schemas.AnalysisQueryResponse 가 강제)
    정책 거부  HTTP 200 · is_rejected=true · SQL 미실행 · 결과 배열 비움
    실행 오류  HTTP 200 · is_valid=true · error_msg · 결과 배열 비움
    형식 오류  FastAPI 422 (router 진입 전) — 정책 거부와 구분된다

nl_query_log_id 는 질의 이력(V5-D-2.4) 전까지 placeholder 1 을 쓴다.
"""

from __future__ import annotations

import time

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.analytics.repository import (
    QueryExecution,
    QueryExecutionError,
    execute_validated_select,
)
from app.analytics.schemas import AnalysisQueryResponse
from app.analytics.sql_validator import CHECK_KEYS, validate_sql
from app.common.enums import ChartType
from app.common.tool_contracts import (
    AnalysisPlanToolResult,
    MetricPlan,
    VisualizationPlan,
    fail,
)

#: V5-D-2.4 전까지의 placeholder. 질의 이력 도입 시 실제 log id 로 교체한다.
_LOG_ID_PLACEHOLDER = 1

#: question 을 SQL 원문으로 간주하는 첫 토큰. 쓰기 키워드를 일부러 포함한다.
#: planner 가 거르면 validator 거부 경로를 시연할 수 없다.
_SQL_LEADING_KEYWORDS: frozenset[str] = frozenset(
    {
        "select",
        "with",
        "insert",
        "update",
        "delete",
        "drop",
        "create",
        "alter",
        "truncate",
        "grant",
        "revoke",
        "copy",
        "explain",
        "vacuum",
    }
)

#: 자연어 질문 fixture. 최종 snapshot 기준으로만 작성한다(WBS §9).
_FIXTURE_PLANS: dict[str, str] = {
    "알람이 가장 많은 설비는?": (
        "SELECT eqp_id, COUNT(*) AS alarm_cnt"
        " FROM trace_alarm_history"
        " GROUP BY eqp_id"
        " ORDER BY alarm_cnt DESC"
    ),
    "trace 알람은 총 몇 건이야?": (
        "SELECT COUNT(*) AS alarm_cnt FROM trace_alarm_history"
    ),
}


def _mock_generate_plan(question: str) -> AnalysisPlanToolResult:
    """LLM 자리의 mock planner. V5-D-2.3 에서 실제 LLM 호출로 교체한다."""
    stripped = question.strip()
    leading = stripped.split(maxsplit=1)[0].lower() if stripped else ""

    if leading in _SQL_LEADING_KEYWORDS:
        sql = stripped
    elif stripped in _FIXTURE_PLANS:
        sql = _FIXTURE_PLANS[stripped]
    else:
        return fail(
            AnalysisPlanToolResult,
            "LLM_NOT_READY: 스파이크 planner 는 SQL 원문 또는 등록된 fixture "
            "질문만 처리한다.",
        )

    return AnalysisPlanToolResult(
        ok=True,
        sql=sql,
        metric=MetricPlan(type="count"),
        group_by=[],
        visualization=VisualizationPlan(chart_type=ChartType.TABLE),
    )


def _rejected_response(
    question: str, reject_reason: str, latency_ms: int
) -> AnalysisQueryResponse:
    """정책 거부. SQL 은 실행되지 않았고 결과 배열은 비어 있다."""
    return AnalysisQueryResponse(
        question=question,
        generated_sql=None,
        columns=[],
        rows=[],
        row_count=0,
        metric=None,
        metric_result=None,
        group_by=[],
        visualization=None,
        is_valid=False,
        is_rejected=True,
        reject_reason=reject_reason,
        error_msg=None,
        latency_ms=latency_ms,
        nl_query_log_id=_LOG_ID_PLACEHOLDER,
    )


def run_analysis_query(question: str) -> AnalysisQueryResponse:
    """자연어 질의 한 건을 처리한다. 어떤 입력에도 예외를 던지지 않는다."""
    started = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    # ── 1. 계획 생성 (LLM mock) ────────────────────────────────────────
    plan = _mock_generate_plan(question)
    if not plan.ok:
        return _rejected_response(question, plan.reason, _elapsed_ms())

    # ── 2. 검증 — 통과하지 못한 SQL 은 실행되지 않는다 ─────────────────
    validation = validate_sql(plan.sql or "")
    if not validation.valid or validation.normalized_sql is None:
        reason = validation.reason or "SQL 검증에 실패했다."
        return _rejected_response(question, f"POLICY_REJECTED: {reason}", _elapsed_ms())

    # ── 3. 실행 — 대상은 언제나 normalized_sql, pool 은 QUERY 전용 ─────
    engine = pool_factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
    try:
        execution: QueryExecution = execute_validated_select(
            engine, validation.normalized_sql
        )
    except QueryExecutionError as exc:
        return AnalysisQueryResponse(
            question=question,
            generated_sql=validation.normalized_sql,
            columns=[],
            rows=[],
            row_count=0,
            metric=plan.metric,
            metric_result=None,
            group_by=list(plan.group_by),
            visualization=plan.visualization,
            is_valid=True,
            is_rejected=False,
            reject_reason=None,
            error_msg=str(exc),
            latency_ms=_elapsed_ms(),
            nl_query_log_id=_LOG_ID_PLACEHOLDER,
        )

    # ── 4. 성공 ────────────────────────────────────────────────────────
    return AnalysisQueryResponse(
        question=question,
        generated_sql=validation.normalized_sql,
        columns=execution.columns,
        rows=execution.rows,
        row_count=execution.row_count,
        metric=plan.metric,
        metric_result=None,
        group_by=list(plan.group_by),
        visualization=plan.visualization,
        is_valid=True,
        is_rejected=False,
        reject_reason=None,
        error_msg=None,
        latency_ms=_elapsed_ms(),
        nl_query_log_id=_LOG_ID_PLACEHOLDER,
    )


#: /analytics/validate 응답의 check label. CHECK_KEYS 와 1:1 이다.
CHECK_LABELS: dict[str, str] = {
    "single_select": "단일 SELECT",
    "allowed_objects": "허용 객체",
    "column_allowlist": "컬럼 allowlist",
    "no_catalog_access": "시스템 카탈로그 차단",
    "no_dangerous_function": "함수 allowlist",
    "limit_enforced": "LIMIT 500",
}

assert set(CHECK_LABELS) == set(CHECK_KEYS)
