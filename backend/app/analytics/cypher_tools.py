"""Text2Cypher 생성 Tool — 자연어를 그래프 조회 Cypher 계획으로 변환한다.

Text2SQL Tool(tools.generate_analysis_plan)의 미러다. 같은 계약:
    - 예외를 던지지 않는다 — 실패는 {ok: False, reason} 으로 돌아온다
    - 비조회 요청은 REFUSED 마커로 거부한다 (중립 사유 문구)
    - retry_feedback 이 self-correction 재생성 통로다
    - 여기서 만든 Cypher 도 신뢰하지 않는다 — 반드시 cypher_validator 를
      통과해야 실행된다

그래프 스키마 컨텍스트는 B파트 정본(neo4j.graph.json)의 라벨·관계 분포와,
그 스키마 계약(#238: 무변경 전제)에 따라 고정 서술한 속성 목록으로 만든다.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.analytics.cypher_validator import _graph_allowlists
from app.common import llm

_CYPHER_FENCE_RE = re.compile(r"```(?:cypher)?\s*(.+?)```", re.IGNORECASE | re.DOTALL)

#: 노드 속성 — 그래프 정본(master_graph.cypher)에서 추출한 고정 서술.
#: B 합의(#238)상 스키마 무변경 전제라 상수로 둔다. 스키마가 바뀌면 B가
#: 통지하고, manifest(라벨·관계)와 이 목록을 함께 갱신한다.
_NODE_PROPERTIES: dict[str, str] = {
    "Area": "area_id, area_name",
    "Equipment": "equipment_id, area, model_code",
    "EquipmentModel": "model_code, model_name",
    "Chamber": "chamber_id",
    "Parameter": "parameter_id",
    "ProcessStep": "step_id, step_seq",
    "Recipe": "recipe_id, recipe_name",
    "RecipeStep": "recipe_id, recipe_step_no",
}

#: 관계 방향 — 스키마 이해를 돕는 고정 서술 (정본 cypher 의 MERGE 방향 그대로)
_RELATION_SHAPES: tuple[str, ...] = (
    "(:Chamber)-[:PART_OF]->(:Equipment)",
    "(:Parameter)-[:MEASURED_ON]->(:Chamber)",
    "(:Equipment)-[:OF_MODEL]->(:EquipmentModel)",
    "(:Equipment)-[:PERFORMS]->(:ProcessStep)",
    "(:ProcessStep)-[:IN_AREA]->(:Area)",
    "(:EquipmentModel)-[:IN_AREA]->(:Area)",
    "(:RecipeStep)-[:STEP_OF]->(:Recipe)",
    "(:ProcessStep)-[:NEXT_STEP]->(:ProcessStep)",
)

_SYSTEM_PROMPT = """당신은 반도체 FDC 설비 온톨로지(Neo4j)의 Text2Cypher 변환기다.

규칙:
1. 그래프 조회 질문만 처리한다. 생성·수정·삭제·적재 등 조회가 아닌 요청이면
   Cypher 를 작성하지 말고 정확히 `REFUSED: 조회 질문만 처리한다` 한 줄만
   출력한다. 요청을 조회로 바꿔 해석하지 않는다.
2. MATCH 로 시작하고 RETURN 으로 끝나는 읽기 전용 조회 하나만 작성한다.
   CREATE·MERGE·DELETE·SET·REMOVE·CALL 등 쓰기·프로시저 구문 금지.
3. 아래 목록의 라벨·관계 타입·속성만 사용한다. 없는 것을 지어내지 않는다.
4. 결과 행이 많을 수 있으면 LIMIT 를 명시한다 (최대 500).
5. 설명 없이 Cypher 만 출력한다. 코드 블록(```cypher) 사용 가능.

그래프 스키마:
{schema}"""


class CypherPlanToolResult(BaseModel):
    """Cypher 계획 Tool 결과 — Tool envelope 계약(ok/reason) 미러."""

    ok: bool
    cypher: str | None = None
    reason: str | None = None


def _fail(reason: str) -> CypherPlanToolResult:
    return CypherPlanToolResult(ok=False, cypher=None, reason=reason)


def _graph_schema_context() -> str:
    """라벨(정본 manifest)·속성(고정 서술)·관계 방향으로 스키마 요약을 만든다."""
    allowlists = _graph_allowlists()
    labels = sorted(allowlists[0]) if allowlists else sorted(_NODE_PROPERTIES)
    lines = ["노드 라벨(속성):"]
    for label in labels:
        properties = _NODE_PROPERTIES.get(label)
        lines.append(f"- {label}({properties})" if properties else f"- {label}")
    lines.append("관계 (방향 그대로 사용):")
    lines.extend(f"- {shape}" for shape in _RELATION_SHAPES)
    lines.append(
        "값 예시: equipment_id='EQP01'~'EQP06', chamber_id='EQP01-PM1' 형식,"
        " area_id='Photo'|'Etch'"
    )
    return "\n".join(lines)


def _extract_cypher(raw: str) -> str | None:
    """LLM 출력에서 Cypher 본문을 추출한다. MATCH 시작만 인정한다."""
    fenced = _CYPHER_FENCE_RE.search(raw)
    candidate = (fenced.group(1) if fenced else raw).strip().rstrip(";").strip()
    if not candidate:
        return None
    leading = candidate.split(maxsplit=1)[0].lower()
    if leading not in {"match", "optional"}:
        return None
    return candidate


def generate_cypher_plan(
    question: str,
    retry_feedback: str | None = None,
) -> CypherPlanToolResult:
    """자연어 질문 하나를 Cypher 계획으로 변환한다. 예외를 던지지 않는다."""
    messages = [
        {
            "role": "system",
            "content": _SYSTEM_PROMPT.format(schema=_graph_schema_context()),
        },
        {"role": "user", "content": question.strip()},
    ]
    if retry_feedback:
        messages.append(
            {
                "role": "user",
                "content": (
                    "직전 Cypher 가 검증에 실패했다. 실패 내용을 반영해 규칙을"
                    " 지키는 Cypher 로 다시 작성하라.\n" + retry_feedback
                ),
            }
        )

    try:
        raw = llm.chat(messages)
    except llm.LlmNotReadyError as exc:
        return _fail(f"LLM_NOT_READY: {exc}")
    except llm.LlmTimeoutError as exc:
        return _fail(f"TIMEOUT: {exc}")
    except llm.LlmDependencyError as exc:
        return _fail(f"DEPENDENCY_ERROR: {exc}")

    if raw.strip().upper().startswith("REFUSED"):
        return _fail(
            "POLICY_REJECTED: 그래프 조회 질문으로 판정되지 않아 Cypher 를 "
            "생성하지 않았다. 이 경로는 온톨로지 조회만 수행하며, 조회 외 "
            "동작은 실행되지 않는다."
        )

    cypher = _extract_cypher(raw)
    if cypher is None:
        return _fail("DEPENDENCY_ERROR: LLM 출력에서 MATCH 조회를 찾지 못했다.")

    return CypherPlanToolResult(ok=True, cypher=cypher, reason=None)
