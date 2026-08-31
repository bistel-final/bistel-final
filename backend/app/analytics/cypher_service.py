"""Text2Cypher 오케스트레이션 — 계획 → 검증 → self-correction → readonly 실행.

service.py(Text2SQL)의 미러다. B 합의(#238) 준수 지점:
    - 실행되는 것은 언제나 validator 의 normalized_cypher 뿐이다
    - Neo4j 세션은 READ_ACCESS 로 연다 — 계정 권한과 별개의 이중 방어
    - 사용자 Cypher 직접 입력 경로는 없다 (passthrough 미제공 — SQL 과 다른
      의도적 차이: B 조건 "임의 Cypher 실행 금지")
    - 오류 원문(접속 정보·구문 위치 포함 가능)은 응답에 싣지 않는다
"""

from __future__ import annotations

import time
from typing import Any

from neo4j import READ_ACCESS
from neo4j.exceptions import DriverError, Neo4jError
from neo4j.graph import Node, Path, Relationship

from app.analytics.cypher_tools import generate_cypher_plan
from app.analytics.cypher_validator import validate_cypher
from app.analytics.schemas import GraphQueryResponse
from app.common.neo4j import get_neo4j_driver

_QUERY_TIMEOUT_SEC = 10.0


def _coerce_value(value: Any) -> Any:
    """Neo4j 그래프 객체를 표 렌더 가능한 값으로 접는다.

    프롬프트는 속성 반환(RETURN c.chamber_id)을 유도하지만, LLM 이 노드
    자체를 반환하면(RETURN c) 속성 dict 로 펼친다 — 실패 대신 최선의 표.
    """
    if isinstance(value, Node | Relationship):
        return dict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_coerce_value(item) for item in value]
    return value


def _rejected(question: str, reason: str, latency_ms: int) -> GraphQueryResponse:
    return GraphQueryResponse(
        question=question,
        generated_cypher=None,
        columns=[],
        rows=[],
        row_count=0,
        is_valid=False,
        is_rejected=True,
        reject_reason=reason,
        error_msg=None,
        latency_ms=latency_ms,
    )


def run_graph_query(question: str) -> GraphQueryResponse:
    """그래프 자연어 질의 한 건을 처리한다. 예외를 던지지 않는다."""
    started = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    # ── 1. 계획 (LLM) ──────────────────────────────────────────────────
    plan = generate_cypher_plan(question)
    if not plan.ok:
        return _rejected(
            question, plan.reason or "계획 생성에 실패했다.", _elapsed_ms()
        )

    # ── 2. 검증 — 통과하지 못한 Cypher 는 실행되지 않는다 ──────────────
    validation = validate_cypher(plan.cypher or "")

    if not validation.valid or validation.normalized_cypher is None:
        # self-correction: 실패 사유를 피드백해 1회 재생성 (SQL 경로 미러)
        retry = generate_cypher_plan(
            question,
            retry_feedback=(
                f"직전 Cypher: {plan.cypher}\n검증 실패 사유: "
                f"{validation.reason or '알 수 없음'}"
            ),
        )
        if retry.ok:
            plan = retry
            validation = validate_cypher(retry.cypher or "")

    if not validation.valid or validation.normalized_cypher is None:
        reason = validation.reason or "Cypher 검증에 실패했다."
        return _rejected(question, f"POLICY_REJECTED: {reason}", _elapsed_ms())

    # ── 3. 실행 — READ_ACCESS 세션, 대상은 언제나 normalized_cypher ────
    try:
        driver = get_neo4j_driver()
        with driver.session(default_access_mode=READ_ACCESS) as session:
            result = session.run(
                validation.normalized_cypher, timeout=_QUERY_TIMEOUT_SEC
            )
            columns = list(result.keys())
            rows = [
                {key: _coerce_value(value) for key, value in record.items()}
                for record in result
            ]
    except (Neo4jError, DriverError) as exc:
        # Neo4jError = 서버 오류, DriverError = 접속·라우팅 장애(ServiceUnavailable 등)
        # — 어느 쪽이든 응답은 200 + error_msg 로 안전하게 접는다 (원문 비노출)
        return GraphQueryResponse(
            question=question,
            generated_cypher=validation.normalized_cypher,
            columns=[],
            rows=[],
            row_count=0,
            is_valid=True,
            is_rejected=False,
            reject_reason=None,
            # 오류 원문은 싣지 않는다 — 분류 코드만 (repository.py 미러)
            error_msg=f"그래프 질의 실행에 실패했다 ({type(exc).__name__}).",
            latency_ms=_elapsed_ms(),
        )

    # ── 4. 성공 ────────────────────────────────────────────────────────
    return GraphQueryResponse(
        question=question,
        generated_cypher=validation.normalized_cypher,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        is_valid=True,
        is_rejected=False,
        reject_reason=None,
        error_msg=None,
        latency_ms=_elapsed_ms(),
    )
