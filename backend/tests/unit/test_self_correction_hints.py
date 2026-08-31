"""자가 교정 강화 검증 — 값 도메인 힌트·0행 재시도 조건·validator 컬럼 힌트."""

from app.analytics import tools
from app.analytics.service import _has_string_equality_filter, _is_zero_scalar
from app.analytics.sql_validator import validate_sql

# ── 0값 집계 감지 (COUNT=0 은 1행 — 0행 재시도 사각지대 보정) ──────────


def test_zero_scalar_detection():
    assert _is_zero_scalar([{"chamber_count": 0}])
    assert not _is_zero_scalar([{"chamber_count": 2}])
    assert not _is_zero_scalar([])  # 진짜 0행은 별도 조건이 잡는다
    assert not _is_zero_scalar([{"a": 0, "b": 1}])  # 다컬럼은 집계 스칼라가 아니다
    assert not _is_zero_scalar([{"flag": False}])  # bool 은 0 으로 보지 않는다


# ── 0행 재시도 조건: 문자열 등호 필터 감지 ─────────────────────────────


def test_string_equality_filter_positive():
    assert _has_string_equality_filter(
        "SELECT * FROM summary_data WHERE parameter = 'CD_AEI' LIMIT 500"
    )
    # 리터럴이 좌변이어도 잡는다
    assert _has_string_equality_filter(
        "SELECT * FROM metrology WHERE 'CD_AEI' = measure_type LIMIT 500"
    )


def test_string_equality_filter_negative():
    # 숫자 등호·필터 없음·파싱 불가는 재시도 대상이 아니다
    assert not _has_string_equality_filter(
        "SELECT * FROM summary_data WHERE step_no = 3 LIMIT 500"
    )
    assert not _has_string_equality_filter("SELECT count(*) FROM fdc_trace LIMIT 500")
    assert not _has_string_equality_filter("this is not sql")


# ── 값 도메인 힌트: 스키마 컨텍스트 병기 ───────────────────────────────


def test_schema_context_includes_value_domains(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_value_domains",
        lambda: {
            ("metrology", "measure_type"): ("CD_ADI", "CD_AEI", "OVL_X"),
        },
    )
    context = tools._schema_context()
    assert "measure_type 값 목록: CD_ADI, CD_AEI, OVL_X" in context
    # 값 목록 줄은 해당 테이블 항목 아래에 붙는다
    metrology_idx = context.index("- metrology(")
    domain_idx = context.index("measure_type 값 목록")
    assert domain_idx > metrology_idx


def test_schema_context_survives_empty_domains(monkeypatch):
    # DB 미가용(fail-open) — 힌트 없이도 스키마 컨텍스트는 정상 생성된다
    monkeypatch.setattr(tools, "_value_domains", dict)
    context = tools._schema_context()
    assert "- metrology(" in context
    assert "값 목록" not in context


# ── validator 컬럼 힌트: 실패 사유에 사용 가능 컬럼 첨부 ────────────────


def test_column_failure_reason_lists_available_columns():
    result = validate_sql("SELECT chamber FROM metrology LIMIT 10")
    assert not result.valid
    assert "존재하지 않는 컬럼이다" in (result.reason or "")
    assert "사용 가능 컬럼" in (result.reason or "")
    # 정답 후보(measure_type·measured_value)가 사유에 실려 재생성을 돕는다
    assert "measure_type" in (result.reason or "")


def test_qualified_column_failure_reason_lists_available_columns():
    result = validate_sql("SELECT m.chamber FROM metrology AS m LIMIT 10")
    assert not result.valid
    assert "metrology 의 사용 가능 컬럼" in (result.reason or "")
