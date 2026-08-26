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
1. 데이터 조회 질문만 처리한다. 삭제·수정·생성 등 조회가 아닌 요청이면
   SQL을 작성하지 말고 정확히 `REFUSED: 조회 질문만 처리한다` 한 줄만 출력한다.
   요청을 조회로 바꿔 해석하지 않는다. 단, 데이터를 표·차트·히스토그램·
   그래프로 보여 달라는 시각화 요청은 데이터 조회다 — 거부하지 않는다.
2. 단일 SELECT 문 하나만 작성한다. 쓰기·DDL·다중 문장 금지.
3. 아래 목록의 테이블·컬럼만 사용한다. 목록에 없는 것을 지어내지 않는다.
4. '~별'(예: 챔버별, 파라미터별)·'각 ~마다'·'~ 단위로' 표현은 그룹별
   집계를 의미한다 — 해당 컬럼을 SELECT 에 포함하고 그 컬럼으로 GROUP BY 한다.
5. 결과 행이 많을 수 있으면 LIMIT 를 명시한다 (최대 500).
6. 설명 없이 SQL 만 출력한다. 코드 블록(```sql) 사용 가능.

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
    """GROUP BY 범주 축을 **결과 행의 키 기준**으로 추출한다.

    group_by 는 차트 UI 가 rows 에서 범주 축을 찾는 키다. rows 의 키는
    SELECT projection(alias 적용 후)이므로, GROUP BY 원본 컴럼이 SELECT
    에서 alias 를 달고 나오면 alias 를 반환한다.
    예: SELECT eqp_id AS equipment ... GROUP BY eqp_id → ["equipment"]

    projection 에 없는 GROUP BY 컴럼(결과에 안 나오는 범주)은 축으로 쓸
    수 없으므로 제외한다. 해석 실패나 비컴럼 표현식(위치 번호·함수)도
    건너뛴다 — 이 함수는 표현 메타데이터용이지 검증이 아니므로(검증은
    sql_validator 소관) 보수적으로 비워도 안전하다.
    """
    try:
        statement = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return []

    group = statement.args.get("group")
    if group is None:
        return []

    # 비교용 키와 응답용 표기를 분리한다.
    # - 비교(매핑) 키: lower 정규화 — GROUP BY 참조와 projection 대응용
    # - 응답 값: PostgreSQL 결과 키 규칙 그대로 — 인용("Equipment")은
    #   대소문자 보존, 비인용은 lower 폴딩. rows 의 실제 키와 일치해야
    #   차트가 축을 찾는다.
    def _column_key(column: exp.Column) -> tuple[str, str]:
        return ((column.table or "").lower(), column.name.lower())

    def _result_key_of(identifier: exp.Identifier | None, fallback: str) -> str:
        if identifier is None:
            return fallback.lower()
        if identifier.quoted:
            return identifier.name
        return identifier.name.lower()

    projected: dict[tuple[str, str], str] = {}
    for projection in statement.expressions:
        if isinstance(projection, exp.Alias) and isinstance(
            projection.this, exp.Column
        ):
            alias_identifier = projection.args.get("alias")
            projected[_column_key(projection.this)] = _result_key_of(
                alias_identifier
                if isinstance(alias_identifier, exp.Identifier)
                else None,
                projection.alias,
            )
        elif isinstance(projection, exp.Column):
            column_identifier = projection.this
            projected[_column_key(projection)] = _result_key_of(
                column_identifier
                if isinstance(column_identifier, exp.Identifier)
                else None,
                projection.name,
            )

    columns: list[str] = []
    for expression in group.expressions:
        if not isinstance(expression, exp.Column):
            continue
        result_key = projected.get(_column_key(expression))
        if result_key is None and not expression.table:
            # GROUP BY 가 무수식 참조일 때 유일하게 대응되는 수식 projection
            # 이 있으면 그것을 쓴다 (SELECT a.x AS y ... GROUP BY x).
            matches = [
                value
                for (_table, name), value in projected.items()
                if name == expression.name.lower()
            ]
            if len(matches) == 1:
                result_key = matches[0]
        if result_key is not None:
            columns.append(result_key)
    return columns


def _plan_from_sql(sql: str, question: str = "") -> AnalysisPlanToolResult:
    """[팀 잠정] metric·visualization 은 SQL 형태 + 질문 키워드 최소 heuristic.

    FR-D-04 는 table·bar·line·histogram 4종을 요구한다. 질문이 분포·
    히스토그램을 명시하면 HISTOGRAM, 추이·시계열이면 LINE 을 지정한다.
    그 외에는 범주 축(group_by)이 있을 때만 BAR, 아니면 TABLE.
    GROUP BY 가 있어도 컴럼을 추출하지 못하면 TABLE 로 내려 메타데이터와
    차트 지정이 모순되지 않게 한다.
    """
    group_by = _extract_group_by_columns(sql)
    q = question.lower()
    if "히스토그램" in q or "histogram" in q:
        chart = ChartType.HISTOGRAM
    elif "추이" in q or "시계열" in q or "trend" in q:
        chart = ChartType.LINE
    else:
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
    retry_feedback: str | None = None,
) -> AnalysisPlanToolResult:
    """자연어 질문 하나를 SQL 계획으로 변환한다. 예외를 던지지 않는다.

    retry_feedback: self-correction 용. 직전 시도의 SQL 과 검증 실패 사유를
    넘기면 추가 user 메시지로 붙여 LLM 이 수정 재생성하게 한다.
    """
    messages = [
        {
            "role": "system",
            "content": _SYSTEM_PROMPT.format(schema=_schema_context()),
        },
        {"role": "user", "content": tool_input.question},
    ]
    if retry_feedback:
        messages.append(
            {
                "role": "user",
                "content": (
                    "직전 SQL 이 검증에 실패했다. 실패 내용을 반영해 규칙을"
                    " 지키는 SQL 로 다시 작성하라.\n" + retry_feedback
                ),
            }
        )

    try:
        raw = llm.chat(messages)
    except llm.LlmNotReadyError as exc:
        return fail(AnalysisPlanToolResult, f"LLM_NOT_READY: {exc}")
    except llm.LlmTimeoutError as exc:
        return fail(AnalysisPlanToolResult, f"TIMEOUT: {exc}")
    except llm.LlmDependencyError as exc:
        return fail(AnalysisPlanToolResult, f"DEPENDENCY_ERROR: {exc}")

    # 프롬프트 규칙 1: 비조회 요청은 LLM 이 REFUSED 마커로 거부한다.
    # 조회로 암묵 변환하지 않고 정직하게 거부하는 것이 계약이다.
    # 사유 문구는 중립으로 유지한다 — 사용자가 무엇을 요청했는지 단정하지
    # 않는다 (조회 질문이 오판으로 거부될 수도 있다).
    if raw.strip().upper().startswith("REFUSED"):
        return fail(
            AnalysisPlanToolResult,
            "POLICY_REJECTED: 조회 질문으로 판정되지 않아 SQL 을 생성하지 "
            "않았다. 이 시스템은 데이터 조회만 수행하며, 조회 외 동작은 "
            "실행되지 않는다.",
        )

    sql = _extract_sql(raw)
    if sql is None:
        return fail(
            AnalysisPlanToolResult,
            "DEPENDENCY_ERROR: LLM 출력에서 SELECT 문을 찾지 못했다.",
        )

    return _plan_from_sql(sql, tool_input.question)
