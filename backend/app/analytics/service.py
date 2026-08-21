"""Text2SQL 수직 슬라이스 orchestration (개발 스파이크).

흐름: question → 계획 생성(LLM 또는 SQL passthrough) → sql_validator
→ readonly 실행 → 응답.

계획 생성 경로 2가지
    1. question 첫 토큰이 SQL 키워드면 question 자체를 SQL 로 간주
       (개발·데모 편의 경로. 쓰기 구문도 그대로 통과시킨다 — 그걸 막는
       것이 validator 의 몫이다)
    2. 그 외는 generate_analysis_plan Tool(LLM) 호출. LLM 미준비·timeout 은
       정책 거부(200)로 응답하며 기본 경로를 막지 않는다

응답 계약 (schemas.AnalysisQueryResponse 가 강제)
    정책 거부  HTTP 200 · is_rejected=true · SQL 미실행 · 결과 배열 비움
    실행 오류  HTTP 200 · is_valid=true · error_msg · 결과 배열 비움
    형식 오류  FastAPI 422 (router 진입 전) — 정책 거부와 구분된다

nl_query_log_id 는 질의 이력(V5-D-2.4) 전까지 placeholder 1 을 쓴다.
"""

from __future__ import annotations

import time
from decimal import Decimal

import sqlglot
from sqlglot import expressions as exp

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.analytics.repository import (
    QueryExecution,
    QueryExecutionError,
    execute_validated_select,
)
from app.analytics.schemas import AnalysisQueryResponse
from app.analytics.sql_validator import CHECK_KEYS, validate_sql
from app.analytics.tools import generate_analysis_plan
from app.common.enums import ChartType
from app.common.tool_contracts import (
    AnalysisPlanToolInput,
    AnalysisPlanToolResult,
    MetricPlan,
    VisualizationPlan,
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


def _is_sql_passthrough(question: str) -> bool:
    """question 을 SQL 원문으로 간주할지 판정한다."""
    stripped = question.strip()
    leading = stripped.split(maxsplit=1)[0].lower() if stripped else ""
    return leading in _SQL_LEADING_KEYWORDS


def _generate_plan(question: str) -> AnalysisPlanToolResult:
    """계획 생성. SQL 원문은 passthrough, 자연어는 LLM Tool 을 탄다."""
    stripped = question.strip()

    if _is_sql_passthrough(stripped):
        return AnalysisPlanToolResult(
            ok=True,
            sql=stripped,
            metric=MetricPlan(type="count"),
            group_by=[],
            visualization=VisualizationPlan(chart_type=ChartType.TABLE),
        )

    return generate_analysis_plan(AnalysisPlanToolInput(question=stripped))


def _single_count_value(sql: str, rows: list[dict]) -> float | None:
    """결과가 'COUNT 집계 단일 값'임이 SQL 로 확인될 때만 그 값을 준다.

    projection 이 COUNT(...) 하나뿐인 단일 행 결과만 인정한다.
    SELECT temperature ... LIMIT 1 같은 일반 값 조회가 count KPI 로
    둘갑되는 것을 막는다 — metric 계획과 SQL 의 의미가 일치할 때만
    값을 채운다(P2 리뷰).
    """
    if len(rows) != 1:
        return None

    try:
        statement = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return None

    projections = list(statement.expressions)
    if len(projections) != 1:
        return None
    projection = projections[0]
    if isinstance(projection, exp.Alias):
        projection = projection.this
    if not isinstance(projection, exp.Count):
        return None

    values = [
        value
        for value in rows[0].values()
        if isinstance(value, int | float | Decimal) and not isinstance(value, bool)
    ]
    if len(values) != 1:
        return None
    return float(values[0])


def _compute_metric_result(
    metric: MetricPlan | None, sql: str, rows: list[dict]
) -> float | None:
    """[팀 잠정] 대표 KPI 값. metric 계획과 SQL 의미가 일치할 때만 계산한다.

    현재 planner 는 metric 을 항상 count 로 잡으므로, SQL projection 이
    실제 COUNT 집계 단일 값일 때만 값을 채운다. 그 외(일반 값 조회·
    그룹 결과·LIMIT 잘림)은 전부 None — 틀린 라벨로 값을 보여주느니
    비워둔다. 정식 metric 설계(sum/mean/p, planner 의미 추론)는
    본설계에서 확장한다.
    """
    if metric is None:
        return None
    if metric.type == "count":
        return _single_count_value(sql, rows)
    return None


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
    plan = _generate_plan(question)
    if not plan.ok:
        return _rejected_response(question, plan.reason, _elapsed_ms())

    # ── 2. 검증 — 통과하지 못한 SQL 은 실행되지 않는다 ─────────────────
    validation = validate_sql(plan.sql or "")

    # self-correction: LLM 경로에 한해 실패 사유를 피드백해 1회 재생성.
    # passthrough(사용자가 직접 준 SQL)는 재해석 없이 그대로 거부한다.
    if (
        not validation.valid or validation.normalized_sql is None
    ) and not _is_sql_passthrough(question):
        retry_plan = generate_analysis_plan(
            AnalysisPlanToolInput(question=question.strip()),
            retry_feedback=(
                f"직전 SQL: {plan.sql}\n검증 실패 사유: "
                f"{validation.reason or '알 수 없음'}"
            ),
        )
        if retry_plan.ok:
            # 재시도 결과는 성공·실패 상관없이 채택한다 — 최종 거부 사유는
            # 마지막 시도(사용자·로그에 남는 SQL)의 것이어야 한다.
            plan = retry_plan
            validation = validate_sql(retry_plan.sql or "")

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
        metric_result=_compute_metric_result(
            plan.metric, validation.normalized_sql, execution.rows
        ),
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
