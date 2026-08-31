"""자동 교차확인 계약 — 판정기·비교기·SKIPPED 안전성 (그래프 경로는 fake)."""

from app.analytics import cross_check
from app.analytics.cross_check import _compare, is_structural_sql, run_cross_check
from app.analytics.schemas import GraphQueryResponse


def _graph_response(rows):
    return GraphQueryResponse(
        question="q",
        generated_cypher="MATCH (c:Chamber) RETURN c.chamber_id LIMIT 500",
        columns=["c.chamber_id"],
        rows=rows,
        row_count=len(rows),
        is_valid=True,
        is_rejected=False,
        reject_reason=None,
        error_msg=None,
        latency_ms=10,
    )


# ── 판정기: 구조 질의만 통과 (좁고 확실하게) ───────────────────────────


def test_structural_sql_positive():
    assert is_structural_sql(
        "SELECT DISTINCT chamber FROM summary_data WHERE equipment = 'EQP01'"
    )
    assert is_structural_sql("SELECT count(DISTINCT chamber) AS n FROM summary_data")
    assert is_structural_sql(
        "SELECT equipment_id, count(DISTINCT chamber_id) FROM lot_history"
        " GROUP BY equipment_id"
    )


def test_structural_sql_negative():
    # 비구조 컬럼(수치·알람)·비구조 테이블은 교차확인 대상이 아니다
    assert not is_structural_sql(
        "SELECT AVG(value_mean) FROM summary_data WHERE parameter = 'PH_FOCUS'"
    )
    assert not is_structural_sql("SELECT count(*) FROM trace_alarm_history")
    assert not is_structural_sql("this is not sql")


# ── 비교기: scalar / set 자동 판별 ─────────────────────────────────────


def test_compare_scalar_and_set():
    matched, _ = _compare([{"n": 12}], [{"n": 12}])
    assert matched
    matched, _ = _compare([{"n": 12}], [{"n": 11}])
    assert not matched
    matched, _ = _compare(
        [{"chamber": "EQP01-PM1"}, {"chamber": "EQP01-PM2"}],
        [{"c.chamber_id": "EQP01-PM2"}, {"c.chamber_id": "EQP01-PM1"}],
    )
    assert matched  # 집합 비교 — 순서 무관


def test_compare_unwraps_node_dicts():
    # 그래프가 RETURN c 로 노드를 통째로 돌려줘도 속성값으로 풀어 집합 바교한다
    matched, _ = _compare(
        [{"chamber": "EQP01-PM1"}, {"chamber": "EQP01-PM2"}],
        [{"c": {"chamber_id": "EQP01-PM2"}}, {"c": {"chamber_id": "EQP01-PM1"}}],
    )
    assert matched
    # 다속성 노드는 *_id 하나를 business key 로 골라 바교한다
    matched, _ = _compare(
        [{"equipment": "EQP01"}],
        [{"e": {"equipment_id": "EQP01", "area": "Photo", "model_code": "PH-9000"}}],
    )
    assert matched


def test_compare_count_scalar_vs_list_fallback():
    # SQL 은 COUNT=2, 그래프는 노드 목록 2건 — 같은 사실, 일치로 판정
    matched, summary = _compare(
        [{"chamber_count": 2}],
        [{"c.chamber_id": "EQP01-PM1"}, {"c.chamber_id": "EQP01-PM2"}],
    )
    assert matched
    assert "목록 2건" in summary
    # 반대 방향도 동일
    matched, _ = _compare(
        [{"chamber": "EQP01-PM1"}, {"chamber": "EQP01-PM2"}],
        [{"n": 2}],
    )
    assert matched
    # 개수가 다르면 여전히 불일치
    matched, _ = _compare([{"chamber_count": 3}], [{"c": "EQP01-PM1"}])
    assert not matched


# ── run_cross_check: 상태 계약 ─────────────────────────────────────────


def test_non_structural_sql_skipped_without_graph_call(monkeypatch):
    def boom(question):
        raise AssertionError("비구조 질의에는 그래프 경로를 호출하지 않는다")

    monkeypatch.setattr(cross_check, "run_graph_query", boom)
    result = run_cross_check(
        "알람 몇 건?", "SELECT count(*) AS n FROM trace_alarm_history", [{"n": 138}]
    )
    assert result.status == "SKIPPED"


def test_match_and_mismatch(monkeypatch):
    monkeypatch.setattr(
        cross_check,
        "run_graph_query",
        lambda question: _graph_response([{"n": 12}]),
    )
    sql = "SELECT count(DISTINCT chamber) AS n FROM summary_data"
    assert run_cross_check("챔버 수", sql, [{"n": 12}]).status == "MATCH"
    mismatch = run_cross_check("챔버 수", sql, [{"n": 11}])
    assert mismatch.status == "MISMATCH"
    assert mismatch.cypher is not None


def test_graph_failure_is_skipped_not_mismatch(monkeypatch):
    rejected = GraphQueryResponse(
        question="q",
        generated_cypher=None,
        columns=[],
        rows=[],
        row_count=0,
        is_valid=False,
        is_rejected=True,
        reject_reason="POLICY_REJECTED: x",
        error_msg=None,
        latency_ms=5,
    )
    monkeypatch.setattr(cross_check, "run_graph_query", lambda question: rejected)
    result = run_cross_check(
        "챔버 수", "SELECT count(DISTINCT chamber) AS n FROM summary_data", [{"n": 12}]
    )
    assert result.status == "SKIPPED"
