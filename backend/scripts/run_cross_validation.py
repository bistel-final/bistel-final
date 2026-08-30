"""SQL ↔ 그래프 교차검증 러너 — 두 저장소가 같은 사실을 아는지 실측한다.

실행:
    cd backend && set -a && source ../.env && set +a && \
    .venv/bin/python scripts/run_cross_validation.py

질문마다 두 경로로 답을 얻어 비교한다:
    - 경로 A (SQL):    gold SQL 을 검증·readonly 실행 (Text2SQL passthrough 와
                       동일 안전 경로 — validate_sql → execute_validated_select)
    - 경로 B (그래프): gold Cypher 를 검증·READ 실행 (cypher_validator →
                       READ_ACCESS 세션)

비교 방식 3종:
    scalar   — 단일 수치 동등 (예: 챔버 수 12)
    set      — 값 집합 동등 (예: Photo 구역 장비 목록)
    hybrid   — 그래프가 준 집합을 SQL 필터로 주입해 집계한 값 ==
               RDB 자체 컬럼으로 집계한 값 (구조 정보의 실데이터 검증)

gold 는 사람이 작성한다 — LLM 출력을 LLM 으로 채점하지 않는다는 평가
러너(#138)의 원칙 그대로. 불일치는 러너 실패가 아니라 '두 저장소의 정합성
결함 발견'으로 리포트된다.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from neo4j import READ_ACCESS  # noqa: E402

from app.analytics.cypher_validator import validate_cypher  # noqa: E402
from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory  # noqa: E402
from app.analytics.repository import execute_validated_select  # noqa: E402
from app.analytics.sql_validator import validate_sql  # noqa: E402
from app.common.neo4j import get_neo4j_driver  # noqa: E402

ARTIFACT_DIR = BACKEND_ROOT / "artifacts" / "cross_validation"

# ── 질문 정의 ──────────────────────────────────────────────────────────
# mode=scalar|set: sql 과 cypher 가 같은 값/집합을 내야 한다.
# mode=hybrid: cypher 가 준 첫 컬럼 값들을 sql 의 :graph_values 에 주입한
#   집계값 == baseline_sql 의 집계값. 그래프의 구조 정보(소속)가 RDB 실데이터
#   집계와 모순 없는지 본다.
CASES: list[dict[str, Any]] = [
    {
        "id": "X01",
        "question": "전체 챔버 수",
        "mode": "scalar",
        "sql": "SELECT count(DISTINCT chamber) AS n FROM summary_data",
        "cypher": "MATCH (c:Chamber) RETURN count(c) AS n",
    },
    {
        "id": "X02",
        "question": "EQP01 의 챔버 목록",
        "mode": "set",
        "sql": (
            "SELECT DISTINCT chamber FROM summary_data" " WHERE equipment = 'EQP01'"
        ),
        "cypher": (
            "MATCH (c:Chamber)-[:PART_OF]->(:Equipment {equipment_id: 'EQP01'})"
            " RETURN c.chamber_id"
        ),
    },
    {
        "id": "X03",
        "question": "챔버-파라미터 측정 조합 수",
        "mode": "scalar",
        "sql": (
            "SELECT count(*) AS n FROM"
            " (SELECT DISTINCT chamber, parameter FROM summary_data) t"
        ),
        "cypher": "MATCH (:Parameter)-[r:MEASURED_ON]->(:Chamber) RETURN count(r) AS n",
    },
    {
        "id": "X04",
        "question": "Photo 구역 장비 목록",
        "mode": "set",
        "sql": ("SELECT DISTINCT equipment FROM summary_data WHERE area = 'Photo'"),
        "cypher": "MATCH (e:Equipment {area: 'Photo'}) RETURN e.equipment_id",
    },
    {
        "id": "X05",
        "question": "장비별 챔버 수",
        "mode": "set",
        "sql": (
            "SELECT equipment || ':' || count(DISTINCT chamber) AS pair"
            " FROM summary_data GROUP BY equipment"
        ),
        "cypher": (
            "MATCH (c:Chamber)-[:PART_OF]->(e:Equipment)"
            " WITH e.equipment_id AS eq, count(c) AS n"
            " RETURN eq + ':' + toString(n) AS pair"
        ),
    },
    {
        "id": "X06",
        "question": "Photo 구역 장비들의 SUMMARY 행 수 (그래프 소속 vs RDB area 컬럼)",
        "mode": "hybrid",
        "cypher": "MATCH (e:Equipment {area: 'Photo'}) RETURN e.equipment_id",
        "sql": (
            "SELECT count(*) AS n FROM summary_data" " WHERE equipment IN :graph_values"
        ),
        "baseline_sql": "SELECT count(*) AS n FROM summary_data WHERE area = 'Photo'",
    },
    {
        "id": "X07",
        "question": "ET-7500 모델 장비들의 lot 처리 건수 (그래프 모델 소속 vs RDB)",
        "mode": "hybrid",
        "cypher": (
            "MATCH (e:Equipment)-[:OF_MODEL]->"
            "(:EquipmentModel {model_code: 'ET-7500'}) RETURN e.equipment_id"
        ),
        "sql": (
            "SELECT count(*) AS n FROM lot_history"
            " WHERE equipment_id IN :graph_values"
        ),
        "baseline_sql": (
            "SELECT count(*) AS n FROM lot_history WHERE area_id = 'Etch'"
        ),
    },
]


def _run_sql(sql: str) -> list[dict[str, Any]]:
    validation = validate_sql(sql)
    if not validation.valid or validation.normalized_sql is None:
        raise RuntimeError(f"gold SQL 검증 실패: {validation.reason}")
    engine = pool_factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
    return execute_validated_select(engine, validation.normalized_sql).rows


def _run_cypher(cypher: str) -> list[dict[str, Any]]:
    validation = validate_cypher(cypher)
    if not validation.valid or validation.normalized_cypher is None:
        raise RuntimeError(f"gold Cypher 검증 실패: {validation.reason}")
    driver = get_neo4j_driver()
    with driver.session(default_access_mode=READ_ACCESS) as session:
        result = session.run(validation.normalized_cypher)
        return [dict(record) for record in result]


def _first_values(rows: list[dict[str, Any]]) -> list[Any]:
    return [next(iter(row.values())) for row in rows if row]


def _inject_values(sql: str, values: list[Any]) -> str:
    quoted = ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)
    return sql.replace(":graph_values", f"({quoted})")


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    mode = case["mode"]
    if mode == "hybrid":
        graph_values = sorted(
            str(v) for v in _first_values(_run_cypher(case["cypher"]))
        )
        via_graph = _first_values(_run_sql(_inject_values(case["sql"], graph_values)))
        baseline = _first_values(_run_sql(case["baseline_sql"]))
        passed = via_graph == baseline
        detail = {
            "graph_filter": graph_values,
            "via_graph": via_graph,
            "baseline": baseline,
        }
    else:
        sql_values = _first_values(_run_sql(case["sql"]))
        cypher_values = _first_values(_run_cypher(case["cypher"]))
        if mode == "set":
            passed = sorted(map(str, sql_values)) == sorted(map(str, cypher_values))
        else:
            passed = sql_values == cypher_values
        detail = {"sql": sql_values, "cypher": cypher_values}
    return {
        "id": case["id"],
        "question": case["question"],
        "mode": mode,
        "passed": passed,
        "detail": detail,
    }


def main() -> int:
    results = []
    for case in CASES:
        try:
            outcome = run_case(case)
        except Exception as exc:  # noqa: BLE001 — 러너는 끝까지 돌고 리포트한다
            outcome = {
                "id": case["id"],
                "question": case["question"],
                "mode": case["mode"],
                "passed": False,
                "detail": {"error": str(exc)},
            }
        status = "PASS" if outcome["passed"] else "FAIL"
        print(f"[{status}] {outcome['id']} {outcome['question']}")
        if not outcome["passed"]:
            print(f"       detail: {outcome['detail']}")
        results.append(outcome)

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"== 교차검증: {passed}/{total} 일치")

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    artifact = ARTIFACT_DIR / f"result_{stamp}.json"
    artifact.write_text(
        json.dumps(
            {"executed_at": stamp, "passed": passed, "total": total, "items": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"== artifact: {artifact.relative_to(BACKEND_ROOT)}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
