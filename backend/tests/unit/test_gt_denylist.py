"""NFR-19 — 합성 GT 라벨(lot_history.fault_code) 비노출: 검증기 차단 + 프롬프트 제외.

발견 경위: 컬럼 allowlist 가 manifest 물리 컬럼을 그대로 써서 fault_code 가 조회되고,
bare * 는 exp.Column 이 아니라 컬럼 검사를 건너뛰었다. 검증기와 프롬프트가 같은
DENIED_COLUMNS 를 보게 해 한 곳에서 관리한다.
"""

import pytest

from app.analytics import tools
from app.analytics.sql_validator import DENIED_COLUMNS, validate_sql

# ── RED: 전부 거부되어야 한다 ─────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT lot_id, fault_code FROM lot_history",
        "SELECT lh.lot_id, lh.fault_code FROM lot_history AS lh",
        "SELECT * FROM lot_history",
        "SELECT lh.* FROM lot_history AS lh",
        "SELECT fault_code, count(*) FROM lot_history GROUP BY fault_code",
        # CTE 로 감싸도 물리 테이블 참조는 남는다
        "WITH x AS (SELECT fault_code FROM lot_history) SELECT * FROM x",
    ],
)
def test_denied_column_and_bare_star_rejected(sql):
    result = validate_sql(sql)
    assert not result.valid
    assert "차단" in (result.reason or "")


# ── GREEN: 정당한 조회는 그대로 ──────────────────────────────────────


def test_count_star_on_denied_table_allowed():
    # 집계 인자의 * 는 행을 노출하지 않는다 — 평가 gold(lot 600) 경로
    result = validate_sql("SELECT count(*) AS n FROM lot_history")
    assert result.valid


def test_explicit_safe_columns_on_denied_table_allowed():
    result = validate_sql(
        "SELECT lot_id, chamber_id, wafer_no FROM lot_history"
        " WHERE equipment_id = 'EQP01'"
    )
    assert result.valid


def test_bare_star_on_other_tables_allowed():
    # 차단 컬럼이 없는 테이블의 * 는 기존과 동일하게 허용
    result = validate_sql("SELECT * FROM dim_parameter")
    assert result.valid


# ── 계약: 프롬프트도 같은 집합을 본다 ─────────────────────────────────


def test_denied_columns_contract():
    assert ("lot_history", "fault_code") in DENIED_COLUMNS


def test_schema_context_omits_denied_column(monkeypatch):
    monkeypatch.setattr(tools, "_value_domains", dict)
    context = tools._schema_context()
    lot_line = next(
        line for line in context.splitlines() if line.startswith("- lot_history(")
    )
    assert "fault_code" not in lot_line
    assert "lot_id" in lot_line
