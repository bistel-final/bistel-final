"""Cypher 생성 Tool 계약 검증 — REFUSED·추출·스키마 컨텍스트 (LLM 은 monkeypatch)."""

from app.analytics import cypher_tools
from app.analytics.cypher_tools import generate_cypher_plan
from app.common import llm


def _patch_llm(monkeypatch, reply: str):
    monkeypatch.setattr(llm, "chat", lambda messages: reply)


def test_valid_cypher_extracted_from_fence(monkeypatch):
    _patch_llm(
        monkeypatch,
        "```cypher\nMATCH (c:Chamber) RETURN c.chamber_id;\n```",
    )
    result = generate_cypher_plan("챔버 목록 보여줘")
    assert result.ok
    assert result.cypher == "MATCH (c:Chamber) RETURN c.chamber_id"


def test_refused_marker_returns_neutral_policy_reason(monkeypatch):
    _patch_llm(monkeypatch, "REFUSED: 조회 질문만 처리한다")
    result = generate_cypher_plan("그래프에 노드 하나 만들어줘")
    assert not result.ok
    assert (result.reason or "").startswith("POLICY_REJECTED")
    # 사유는 사용자가 무엇을 요청했는지 단정하지 않는 중립 문구다
    assert "생성" not in (result.reason or "").split("판정되지 않아")[0]


def test_non_match_output_rejected(monkeypatch):
    _patch_llm(monkeypatch, "CREATE (n:Chamber) RETURN n")
    result = generate_cypher_plan("챔버 보여줘")
    assert not result.ok
    assert "MATCH" in (result.reason or "")


def test_retry_feedback_appended(monkeypatch):
    captured: dict = {}

    def fake_chat(messages):
        captured["messages"] = messages
        return "MATCH (c:Chamber) RETURN c"

    monkeypatch.setattr(llm, "chat", fake_chat)
    generate_cypher_plan("챔버 보여줘", retry_feedback="직전 Cypher: X\n실패: Y")
    assert len(captured["messages"]) == 3
    assert "실패" in captured["messages"][-1]["content"]


def test_schema_context_lists_labels_relations_and_properties():
    context = cypher_tools._graph_schema_context()
    assert "- Chamber(chamber_id)" in context
    assert "(:Parameter)-[:MEASURED_ON]-(:Chamber)" in context
    assert "EQP01" in context  # 값 예시 포함
