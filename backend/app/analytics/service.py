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

질의 이력(V5-D-2.4): 성공·정책 거부·실행 오류를 kosa_text2sql 의 nl_query_log 에
기록하고 실제 log id 를 응답에 싣는다. 기록 불가(logger DSN 미구성 등)시에도
질의 응답은 계속되며 id 는 null 이다 (query_log.py).
"""

from __future__ import annotations

import re
import time
from decimal import Decimal

import sqlglot
from sqlglot import expressions as exp

from app.analytics.charts import resolve_visualization
from app.analytics.cross_check import run_cross_check
from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.analytics.query_log import record_query_log
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


# ── semantic lint: '~별' 집계 질문의 GROUP BY 누락 감지 ──────────────
#: 그룹 신호 3종 — '챔버별(로)', '각 챔버마다', '챔버 단위로'.
#: 조사까지 포함해 토큰을 잡는다.
_GROUP_SIGNAL_RE = re.compile(
    r"[가-힣A-Za-z0-9_]+별(?:로)?(?=[\s,.?!)]|$)"
    r"|각\s+[가-힣A-Za-z0-9_]+마다"
    r"|[가-힣A-Za-z0-9_]+\s*단위로"
)
#: 그룹 의미가 아닌 '별' 단어 — 오탐 차단 (유니크 토큰 기준)
_GROUP_SIGNAL_BLACKLIST: frozenset[str] = frozenset({"특별", "각별", "이별", "유별"})
#: 집계 의도 어휘 — 그룹 신호만으로는 재생성하지 않는다(목록 조회 질문 보호)
_AGGREGATE_WORDS: tuple[str, ...] = (
    "수",
    "건수",
    "개수",
    "평균",
    "합계",
    "합",
    "최대",
    "최소",
    "분포",
    "비율",
    "count",
    "avg",
    "sum",
)


def _has_group_signal(question: str) -> bool:
    """질문에 그룹별 집계 신호(~별 · 각 ~마다 · ~단위로)가 있는지 본다.

    '~별' 토큰은 그룹 의미가 아닌 단어(blacklist)를 제외한다.
    """
    for match in _GROUP_SIGNAL_RE.finditer(question):
        token = match.group(0)
        if token.endswith("마다") or token.endswith("단위로"):
            return True
        token = token[:-1] if token.endswith("로") else token
        if token not in _GROUP_SIGNAL_BLACKLIST:
            return True
    return False


def _has_group_by(sql: str) -> bool:
    try:
        statement = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return True  # 파싱 불가면 lint 를 걸지 않는다 — 판단은 validator 몫
    return statement.args.get("group") is not None


def _needs_group_by_hint(question: str, sql: str) -> bool:
    """'~별' + 집계 어휘 질문인데 SQL 에 GROUP BY 가 없으면 참 (V5 Q08 패턴).

    결정론 규칙이며 차단이 아니라 재생성 힌트 트리거다 — 힌트 반영본이
    검증을 통과하지 못하면 원안이 그대로 실행된다 (오탐 내성).
    """
    lowered = question.lower()
    if not _has_group_signal(question):
        return False
    if not any(word in lowered for word in _AGGREGATE_WORDS):
        return False
    return not _has_group_by(sql)


def _has_string_equality_filter(sql: str) -> bool:
    """문자열 리터럴 등호 비교가 있는지 본다 (0행 재시도 조건).

    0행의 흔한 원인이 코드값 오귀속(값이 다른 테이블 소속)이며,
    그 경우 SQL 에는 반드시 문자열 등호 필터가 있다. 숫자 비교나
    필터 없는 0행(정당한 빈 결과)은 재시도하지 않는다.
    """
    try:
        statement = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return False
    for eq in statement.find_all(exp.EQ):
        for side in (eq.this, eq.expression):
            if isinstance(side, exp.Literal) and side.is_string:
                return True
    return False


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
        nl_query_log_id=None,
    )


def run_analysis_query(question: str) -> AnalysisQueryResponse:
    """자연어 질의 한 건을 처리하고 이력을 기록한다. 예외를 던지지 않는다."""
    response = _execute_analysis_query(question)
    log_id = record_query_log(response)
    if log_id is None:
        return response
    return response.model_copy(update={"nl_query_log_id": log_id})


def _execute_analysis_query(question: str) -> AnalysisQueryResponse:
    """질의 본체 — 계획→검증→실행. 이력 기록은 run_analysis_query 가 한다."""
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

    # ── 2.5 semantic lint — '~별' 집계 질문의 GROUP BY 누락이면 힌트 재생성 ─
    # 검증을 통과한 '의미상 틀린' SQL(Q08)은 기존 self-correction 에 안
    # 걸린다. 힌트 반영본도 검증을 통과해야만 채택하며, 아니면 원안 유지.
    if not _is_sql_passthrough(question) and _needs_group_by_hint(
        question, validation.normalized_sql
    ):
        hinted = generate_analysis_plan(
            AnalysisPlanToolInput(question=question.strip()),
            retry_feedback=(
                f"직전 SQL: {validation.normalized_sql}\n"
                "질문의 '~별' 표현은 그룹별 집계를 요구한다. 해당 컬럼으로 "
                "GROUP BY 를 추가해 다시 작성하라."
            ),
        )
        if hinted.ok:
            hinted_validation = validate_sql(hinted.sql or "")
            if hinted_validation.valid and hinted_validation.normalized_sql:
                plan = hinted
                validation = hinted_validation

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
            nl_query_log_id=None,
        )

    # ── 3.5 0행 재시도 — 문자열 필터 오귀속(값의 소속 테이블 혼동) 보정 ─
    # 재생성본이 검증을 통과하고 실제 행을 반환할 때만 채택 — 아니면
    # 원 결과(0행)를 그대로 낸다 (정당한 빈 결과일 수 있다).
    if (
        execution.row_count == 0
        and not _is_sql_passthrough(question)
        and _has_string_equality_filter(validation.normalized_sql)
    ):
        retried = generate_analysis_plan(
            AnalysisPlanToolInput(question=question.strip()),
            retry_feedback=(
                f"직전 SQL: {validation.normalized_sql}\n"
                "실행 결과가 0행이었다. 등호 필터의 값이 이 테이블에 존재하지"
                " 않을 수 있다. 스키마의 값 목록을 참고해 값이 실제로 속한"
                " 테이블·컬럼으로 다시 작성하라."
            ),
        )
        if retried.ok:
            retried_validation = validate_sql(retried.sql or "")
            if retried_validation.valid and retried_validation.normalized_sql:
                try:
                    retried_execution: QueryExecution | None = execute_validated_select(
                        engine, retried_validation.normalized_sql
                    )
                except QueryExecutionError:
                    retried_execution = None
                if retried_execution is not None and retried_execution.row_count > 0:
                    plan = retried
                    validation = retried_validation
                    execution = retried_execution

    # ── 4. 성공 ────────────────────────────────────────────────────────
    # 교차확인(#240): 구조 질의면 그래프로 재확인해 신뢰 배지를 싣는다.
    # passthrough 는 Cypher 생성용 자연어가 없으므로 제외한다.
    cross = (
        None
        if _is_sql_passthrough(question)
        else run_cross_check(question, validation.normalized_sql, execution.rows)
    )
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
        # 차트는 실행된 rows 의 실제 모양으로 확정한다 (FR-D-04, charts.py)
        visualization=resolve_visualization(
            question, execution.columns, execution.rows, plan.visualization
        ),
        cross_check=cross,
        is_valid=True,
        is_rejected=False,
        reject_reason=None,
        error_msg=None,
        latency_ms=_elapsed_ms(),
        nl_query_log_id=None,
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
