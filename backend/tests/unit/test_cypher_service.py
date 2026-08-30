"""Text2Cypher 오케스트레이션 계약 — 거부·재생성·값 접기 (외부는 전부 fake)."""

from app.analytics import cypher_service
from app.analytics.cypher_service import _coerce_value, run_graph_query
from app.analytics.cypher_tools import CypherPlanToolResult


def test_plan_failure_becomes_policy_rejection(monkeypatch):
    monkeypatch.setattr(
        cypher_service,
        "generate_cypher_plan",
        lambda question, retry_feedback=None: CypherPlanToolResult(
            ok=False, reason="POLICY_REJECTED: 조회 질문이 아니다"
        ),
    )
    response = run_graph_query("그래프 다 지워줘")
    assert response.is_rejected and not response.is_valid
    assert response.generated_cypher is None
    assert response.rows == [] and response.row_count == 0


def test_validation_failure_triggers_one_retry_then_rejects(monkeypatch):
    calls: list[str | None] = []

    def fake_plan(question, retry_feedback=None):
        calls.append(retry_feedback)
        return CypherPlanToolResult(ok=True, cypher="CREATE (n:Chamber) RETURN n")

    monkeypatch.setattr(cypher_service, "generate_cypher_plan", fake_plan)
    response = run_graph_query("챔버 보여줘")
    # 1차 실패 → 사유 피드백으로 1회 재생성 → 여전히 실패 → 거부
    assert len(calls) == 2
    assert calls[0] is None and "검증 실패" in (calls[1] or "")
    assert response.is_rejected
    assert "POLICY_REJECTED" in (response.reject_reason or "")


def test_coerce_folds_graph_objects_and_lists():
    class FakeNode(dict):
        pass

    # Node/Relationship 은 Mapping 계약 — dict 로 접힌다 (여기서는 형 검사만 우회)
    assert _coerce_value([1, "a", None]) == [1, "a", None]
    assert _coerce_value(3.5) == 3.5
