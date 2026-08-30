"""Cypher 검증기 방어 fixture — B파트 합의 조건(#238)을 테스트로 박제한다.

fixture 전부가 '실행 미도달'이어야 한다 — SQL validator 방어 테스트의 미러.
allowlist 는 실제 정본(neo4j.graph.json)을 읽는다.
"""

import pytest

from app.analytics.cypher_validator import validate_cypher

_OK = (
    "MATCH (c:Chamber)-[:PART_OF]->(e:Equipment {equipment_id: 'EQP01'})"
    " RETURN c.chamber_id"
)


# ── 통과 경로 ──────────────────────────────────────────────────────────


def test_valid_match_passes_and_limit_injected():
    result = validate_cypher(_OK)
    assert result.valid
    assert result.normalized_cypher.endswith("LIMIT 500")
    assert all(check.passed for check in result.checks)


def test_existing_limit_kept_and_oversized_limit_reduced():
    kept = validate_cypher(_OK + " LIMIT 10")
    assert kept.valid and kept.normalized_cypher.endswith("LIMIT 10")
    reduced = validate_cypher(_OK + " LIMIT 99999")
    assert reduced.valid and reduced.normalized_cypher.endswith("LIMIT 500")


def test_optional_match_and_string_literal_keyword_not_false_positive():
    # 문자열 리터럴 안의 CREATE 는 쓰기 구문이 아니다 — 오탐 금지
    result = validate_cypher(
        "MATCH (c:Chamber) WHERE c.chamber_id = 'CREATE ROOM' RETURN c"
    )
    assert result.valid


# ── 읽기 전용 강제 (B 조건: MATCH/RETURN 외 차단) ──────────────────────


@pytest.mark.parametrize(
    "cypher",
    [
        "CREATE (n:Chamber {chamber_id: 'X'})",
        "MERGE (n:Chamber {chamber_id: 'X'}) RETURN n",
        "MATCH (n:Chamber) DELETE n",
        "MATCH (n:Chamber) DETACH DELETE n",
        "MATCH (n:Chamber) SET n.x = 1 RETURN n",
        "MATCH (n:Chamber) REMOVE n.x RETURN n",
        "DROP INDEX chamber_idx",
        "MATCH (n:Chamber) FOREACH (x IN [1] | SET n.y = x)",
        "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
        "EXPLAIN MATCH (n:Chamber) RETURN n",
    ],
)
def test_write_and_meta_statements_rejected(cypher):
    result = validate_cypher(cypher)
    assert not result.valid
    assert result.normalized_cypher is None


def test_return_required_and_match_leading_required():
    assert not validate_cypher("MATCH (n:Chamber)").valid
    assert not validate_cypher("RETURN 1").valid


# ── 프로시저 차단 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n:Chamber) CALL db.labels() YIELD label RETURN label",
        "MATCH (n:Chamber) RETURN apoc.version()",
        "MATCH (n:Chamber) RETURN dbms.components()",
    ],
)
def test_procedure_calls_rejected(cypher):
    result = validate_cypher(cypher)
    assert not result.valid
    assert "프로시저" in (result.reason or "") or "허용되지" in (result.reason or "")


# ── 다중 문장 ──────────────────────────────────────────────────────────


def test_multiple_statements_rejected():
    result = validate_cypher("MATCH (n:Chamber) RETURN n; MATCH (m:Equipment) RETURN m")
    assert not result.valid


# ── allowlist (정본 neo4j.graph.json 기반) ─────────────────────────────


def test_unknown_label_rejected_with_candidates():
    result = validate_cypher("MATCH (u:User) RETURN u")
    assert not result.valid
    assert "허용되지 않은 라벨" in (result.reason or "")
    assert "Chamber" in (result.reason or "")  # 사유가 정답 후보를 안내한다


def test_unknown_relation_rejected():
    result = validate_cypher("MATCH (c:Chamber)-[:OWNS]->(e:Equipment) RETURN c")
    assert not result.valid
    assert "허용되지 않은 관계" in (result.reason or "")


def test_unclosed_string_fail_closed():
    result = validate_cypher("MATCH (c:Chamber) WHERE c.x = 'oops RETURN c")
    assert not result.valid
