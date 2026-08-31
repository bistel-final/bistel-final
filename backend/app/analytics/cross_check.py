"""자동 교차확인 — SQL 답을 그래프로 재확인해 신뢰 배지를 만든다 (#240).

흐름 (service.py 성공 분기의 마지막 단계):
    SQL 답 확보 → 판정기(이 질문이 그래프로도 답 가능한 구조 질의인가?)
    → 통과 시 그래프 경로(run_graph_query: 생성·검증·재시도·READ 실행) 실행
    → 두 답 비교 → MATCH / MISMATCH / SKIPPED

판정은 결정론 규칙이다 — 좁고 확실하게. 오탐(비구조 질문에 그래프 경로를
돌려 지연·오경보)보다 미탐(배지가 안 붙음)이 훨씬 싼 실패이므로,
생성 SQL 이 구조 테이블의 구조 컬럼만 조회할 때만 시도한다.
그래프 경로 실패·거부는 MISMATCH 가 아니라 SKIPPED 다 — 교차확인은
품질 보증 계층이지 응답을 막는 관문이 아니다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import sqlglot
from sqlglot import expressions as exp

from app.analytics.cypher_service import run_graph_query
from app.analytics.schemas import CrossCheck

#: 그래프(온톨로지)가 아는 구조 컬럼 — 이 밖의 컬럼이 등장하면 SKIPPED.
_STRUCTURAL_COLUMNS: frozenset[str] = frozenset(
    {
        "chamber",
        "equipment",
        "parameter",
        "area",
        "recipe",
        "chamber_id",
        "equipment_id",
        "parameter_id",
        "area_id",
        "recipe_id",
        "step_id",
        "model_code",
    }
)
#: 구조 정보를 담은 테이블 — 알람·측정 시계열 테이블이 끼면 SKIPPED.
_STRUCTURAL_TABLES: frozenset[str] = frozenset(
    {"summary_data", "lot_history", "dim_parameter"}
)


def is_structural_sql(sql: str) -> bool:
    """SQL 이 '그래프로도 답 가능한 구조 조회'인지 결정론으로 판정한다."""
    try:
        statement = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return False
    tables = {t.name.lower() for t in statement.find_all(exp.Table)}
    if not tables or not tables <= _STRUCTURAL_TABLES:
        return False
    for column in statement.find_all(exp.Column):
        if column.name.lower() not in _STRUCTURAL_COLUMNS:
            return False
    return True


def _first_values(rows: list[dict[str, Any]]) -> list[Any]:
    """각 행의 첫 컬럼 값. 그래프가 노드를 통째로 돌려줘(RETURN c) 값이 속성 dict 이면
    단일 속성은 그 값으로 풀고, 다속성이면 business key 로 보이는 것(*_id)을 골라
    SQL 문자열 목록과 비교 가능하게 한다 (오경보 방지)."""
    values: list[Any] = []
    for row in rows:
        if not row:
            continue
        value = next(iter(row.values()))
        if isinstance(value, dict):
            if len(value) == 1:
                value = next(iter(value.values()))
            else:
                key_like = [v for k, v in value.items() if str(k).endswith("_id")]
                value = key_like[0] if len(key_like) == 1 else value
        values.append(value)
    return values


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _compare(
    sql_rows: list[dict[str, Any]], graph_rows: list[dict[str, Any]]
) -> tuple[bool, str]:
    """값 비교 — 단일 수치면 scalar 동등, 그 외는 첫 컬럼 집합 동등.

    형태 폴백: 한쪽이 count 스칼라고 다른 쪽이 목록이면 '개수 == 목록
    길이'로 비교한다 — 같은 사실("챔버 2개")을 SQL 은 COUNT 로,
    Cypher 는 노드 목록으로 답하는 자연스러운 표현 차이를 불일치로
    오판하지 않기 위함이다.
    """
    sql_values = _first_values(sql_rows)
    graph_values = _first_values(graph_rows)
    sql_scalar = len(sql_values) == 1 and _is_number(sql_values[0])
    graph_scalar = len(graph_values) == 1 and _is_number(graph_values[0])
    if sql_scalar and graph_scalar:
        matched = float(sql_values[0]) == float(graph_values[0])
        return matched, f"SQL {sql_values[0]} · 그래프 {graph_values[0]}"
    if sql_scalar and not graph_scalar:
        matched = float(sql_values[0]) == float(len(graph_values))
        return matched, f"SQL {sql_values[0]} · 그래프 목록 {len(graph_values)}건"
    if graph_scalar and not sql_scalar:
        matched = float(graph_values[0]) == float(len(sql_values))
        return matched, f"SQL 목록 {len(sql_values)}건 · 그래프 {graph_values[0]}"
    matched = sorted(map(str, sql_values)) == sorted(map(str, graph_values))
    return matched, f"SQL {len(sql_values)}건 · 그래프 {len(graph_values)}건"


def run_cross_check(
    question: str, normalized_sql: str, sql_rows: list[dict[str, Any]]
) -> CrossCheck:
    """SQL 답 한 건을 그래프로 재확인한다. 예외를 던지지 않는다."""
    try:
        if not is_structural_sql(normalized_sql):
            return CrossCheck(status="SKIPPED", cypher=None, summary=None)

        graph = run_graph_query(question)
        if graph.is_rejected or graph.error_msg is not None:
            # 그래프 경로 실패는 검증 불가일 뿐 불일치가 아니다
            return CrossCheck(status="SKIPPED", cypher=None, summary=None)

        matched, summary = _compare(sql_rows, graph.rows)
        return CrossCheck(
            status="MATCH" if matched else "MISMATCH",
            cypher=graph.generated_cypher,
            summary=summary,
        )
    except Exception:
        return CrossCheck(status="SKIPPED", cypher=None, summary=None)
