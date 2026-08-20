"""V5-D-2.3 generate_analysis_plan Tool.

자연어 질문을 SQL 계획(AnalysisPlanToolResult)으로 변환한다. LLM 출력은
신뢰하지 않는다 — 여기서 만든 SQL 도 반드시 sql_validator 를 거쳐야
실행된다(2차 방어). 이 Tool 의 책임은 "그럴듯한 SQL 후보" 생성까지다.

프롬프트 컨텍스트
- allowlist 객체와 manifest 컬럼만 알려준다. 스키마 전체를 노출하지 않는다.
- ground_truth 계열 컬럼은 컨텍스트에서 제외한다(Fault GT 비노출, FR-C-15).

계약
- 성공: {ok:true, sql, metric, group_by, visualization}
- 실패: {ok:false, reason} — reason 은 REASON_PREFIXES 접두어를 지킨다.
  LLM 미준비는 LLM_NOT_READY, timeout 은 TIMEOUT, 그 외는 DEPENDENCY_ERROR.
- 어떤 입력에도 예외를 던지지 않는다.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import expressions as exp

from app.analytics.sql_validator import ALLOWED_OBJECTS, _manifest_columns
from app.common import llm
from app.common.enums import ChartType
from app.common.tool_contracts import (
    AnalysisPlanToolInput,
    AnalysisPlanToolResult,
    MetricPlan,
    VisualizationPlan,
    fail,
)

#: Fault GT·합성 라벨 계열 컬럼은 프롬프트에 노출하지 않는다.
_EXCLUDED_COLUMN_PREFIXES: tuple[str, ...] = ("ground_truth",)

_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.+?)```", re.IGNORECASE | re.DOTALL)


def _schema_context() -> str:
    """allowlist 객체 + manifest 컬럼으로 프롬프트용 스키마 요약을 만든다.

    manifest 에 없는 객체(뷰 등)는 이름만 노출한다. manifest 를 읽지 못해도
    Tool 은 동작한다 — 컬럼 검증은 validator 의 몫이고 여기는 힌트일 뿐이다.
    """
    columns = _manifest_columns() or {}
    lines: list[str] = []
    for name in sorted(ALLOWED_OBJECTS):
        table_columns = [
            col
            for col in sorted(columns.get(name, frozenset()))
            if not col.startswith(_EXCLUDED_COLUMN_PREFIXES)
        ]
        if table_columns:
            lines.append(f"- {name}({', '.join(table_columns)})")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


_SYSTEM_PROMPT = """당신은 반도체 FDC 데이터의 PostgreSQL Text2SQL 변환기다.

규칙:
1. 단일 SELECT 문 하나만 작성한다. 쓰기·DDL·다중 문장 금지.
2. 아래 목록의 테이블·컬럼만 사용한다. 목록에 없는 것을 지어내지 않는다.
3. 결과 행이 많을 수 있으면 LIMIT 를 명시한다 (최대 500).
4. 설명 없이 SQL 만 출력한다. 코드 블록(```sql) 사용 가능.

사용 가능한 테이블:
{schema}"""


def _extract_sql(raw: str) -> str | None:
    """LLM 출력에서 SQL 본문을 추출한다. SELECT/WITH 로 시작해야 인정한다."""
    fenced = _SQL_FENCE_RE.search(raw)
    candidate = (fenced.group(1) if fenced else raw).strip().rstrip(";").strip()

    if not candidate:
        return None
    leading = candidate.split(maxsplit=1)[0].lower()
    if leading not in {"select", "with"}:
        return None
    return candidate


def _extract_group_by_columns(sql: str) -> list[str]:
    """GROUP BY 대상 컴럼명을 추출한다. 차트의 범주 축 메타데이터다.

    해석 실패나 비컴럼 표현식(위치 번호, 함수 등)은 건너뛴다 — 이 함수는
    표현 메타데이터용이지 검증이 아니므로(검증은 sql_validator 소관)
    보수적으로 비워도 안전하다.
    """
    try:
        statement = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return []

    group = statement.args.get("group")
    if group is None:
        return []

    columns: list[str] = []
    for expression in group.expressions:
        if isinstance(expression, exp.Column):
            columns.append(expression.name.lower())
    return columns


def _plan_from_sql(sql: str) -> AnalysisPlanToolResult:
    """[팀 잠정] metric·visualization 은 SQL 형태 기반 최소 heuristic.

    metric_result 계산·차트 세분화는 후속(V5-D-2.3 잔여)이다. 계약상
    성공 결과에 세 필드가 필수라 최소값을 채운다.

    표현 일관성: BAR 는 범주 축(group_by)이 있을 때만 지정한다. GROUP BY
    가 있어도 컴럼을 추출하지 못하면(위치 번호·함수 등) TABLE 로
    내려 메타데이터와 차트 지정이 모순되지 않게 한다.
    """
    group_by = _extract_group_by_columns(sql)
    chart = ChartType.BAR if group_by else ChartType.TABLE
    return AnalysisPlanToolResult(
        ok=True,
        sql=sql,
        metric=MetricPlan(type="count"),
        group_by=group_by,
        visualization=VisualizationPlan(chart_type=chart),
    )


def generate_analysis_plan(
    tool_input: AnalysisPlanToolInput,
) -> AnalysisPlanToolResult:
    """자연어 질문 하나를 SQL 계획으로 변환한다. 예외를 던지지 않는다."""
    messages = [
        {
            "role": "system",
            "content": _SYSTEM_PROMPT.format(schema=_schema_context()),
        },
        {"role": "user", "content": tool_input.question},
    ]

    try:
        raw = llm.chat(messages)
    except llm.LlmNotReadyError as exc:
        return fail(AnalysisPlanToolResult, f"LLM_NOT_READY: {exc}")
    except llm.LlmTimeoutError as exc:
        return fail(AnalysisPlanToolResult, f"TIMEOUT: {exc}")
    except llm.LlmDependencyError as exc:
        return fail(AnalysisPlanToolResult, f"DEPENDENCY_ERROR: {exc}")

    sql = _extract_sql(raw)
    if sql is None:
        return fail(
            AnalysisPlanToolResult,
            "DEPENDENCY_ERROR: LLM 출력에서 SELECT 문을 찾지 못했다.",
        )

    return _plan_from_sql(sql)
