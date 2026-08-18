"""V4-D-2.1 — Text2SQL 안전 검증 red/green fixture.

이 모듈은 검증기(V4-D-2.2, V4-D-2.3) 구현 **전에** 통과·차단 기준을 먼저 고정한다.
`app.analytics.sql_validator.validate_sql`이 아직 없으므로 전 케이스가 red(실패)다.
이것은 의도된 상태이며, V4-D-2.2가 구현되면 별도 수정 없이 green으로 바뀐다.

근거
    요구사항 v2.0 FR-D-02
    시스템설계서 v2.0 10.2 (SQL 안전 검증 단계)
    WBS v4 V4-D-2.1

허용 객체(allowlist) 기준
    base 9 table: dim_parameter, lot_history, fdc_trace, summary_data, evaluation,
                  trace_alarm_history, summary_alarm_history, metrology, action_history

    TODO(V4-CM-2.1): `001_reference_extensions.sql` 적용 후 reference 6종을 추가한다.
        r03_alarm_history, v_alarm_event, nl_query_log,
        document_corpus, document, document_chunk
        추가 시 RED_NOT_ALLOWED_CASES에서 해당 객체를 GREEN으로 옮긴다.

케이스 소비 방식
    GREEN_CASES / RED_CASES / ALL_CASES는 모듈 상수로 노출한다.
    V4-D-2.2 검증기 구현과 V4-D-7 평가셋(DEFENSE 케이스)이 그대로 import해 재사용한다.
"""

from dataclasses import dataclass, field

import pytest

try:  # V4-D-2.2 미구현 상태에서 collection이 깨지지 않도록 분리한다
    from app.analytics.sql_validator import validate_sql
except ImportError:  # pragma: no cover - 구현 후 제거 대상
    validate_sql = None


#: 검증기 호출이 필요한 테스트에 붙인다.
#:
#: V4-D-2.2 미구현 동안 red 의도를 유지하되 suite 전체는 green 으로 둔다.
#: red 를 그대로 두면 팀원이 매번 83건 실패를 보게 되어 실제 회귀가 묻힌다.
#:
#: 조건을 `validate_sql is None` 로 두었으므로 V4-D-2.2 가 구현되면 마커가
#: 자동으로 비활성화되고 테스트는 정상 판정으로 돌아간다. 수동 제거가 필요 없다.
#: `strict=True` 이므로 미구현 상태에서 통과해버리면 XPASS 로 실패해
#: 기대와 실제가 어긋난 사실이 드러난다.
requires_validator = pytest.mark.xfail(
    validate_sql is None,
    strict=True,
    reason="V4-D-2.2 sql_validator.validate_sql 미구현 — 의도된 red (V4-D-2.1)",
)


# --------------------------------------------------------------------------
# 케이스 정의
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SqlCase:
    """검증기에 입력할 SQL 한 건과 기대 결과."""

    case_id: str
    category: str
    sql: str
    expect_valid: bool
    reason_keywords: tuple[str, ...] = field(default=())
    note: str = ""


# --------------------------------------------------------------------------
# GREEN — 통과해야 하는 정상 질의
# --------------------------------------------------------------------------

GREEN_CASES: tuple[SqlCase, ...] = (
    SqlCase(
        case_id="G01_simple_select",
        category="GREEN_BASIC",
        sql="SELECT parameter, alarm_type FROM trace_alarm_history",
        expect_valid=True,
        note="단일 SELECT. LIMIT은 검증기가 주입한다(V4-D-2.3).",
    ),
    SqlCase(
        case_id="G02_where_order",
        category="GREEN_BASIC",
        sql=(
            "SELECT alarm_id, occurred_at, chamber FROM summary_alarm_history "
            "WHERE limit_type = 'UCL' ORDER BY occurred_at DESC"
        ),
        expect_valid=True,
    ),
    SqlCase(
        case_id="G03_group_by",
        category="GREEN_AGGREGATE",
        sql=(
            "SELECT chamber, COUNT(*) AS alarm_cnt FROM trace_alarm_history "
            "GROUP BY chamber ORDER BY alarm_cnt DESC"
        ),
        expect_valid=True,
    ),
    SqlCase(
        case_id="G04_join",
        category="GREEN_JOIN",
        sql=(
            "SELECT l.chamber_id, COUNT(*) AS oos_cnt "
            "FROM evaluation e JOIN lot_history l ON e.lot_hist_id = l.lot_hist_id "
            "WHERE e.alarm_type = 'OOS' GROUP BY l.chamber_id"
        ),
        expect_valid=True,
        note="허용 테이블 간 JOIN.",
    ),
    SqlCase(
        case_id="G05_three_table_join",
        category="GREEN_JOIN",
        sql=(
            "SELECT p.parameter_name, AVG(s.value_mean) AS avg_mean "
            "FROM summary_data s "
            "JOIN dim_parameter p ON s.parameter = p.parameter_id "
            "JOIN lot_history l ON s.lot_hist_id = l.lot_hist_id "
            "GROUP BY p.parameter_name"
        ),
        expect_valid=True,
    ),
    SqlCase(
        case_id="G06_cte",
        category="GREEN_CTE",
        sql=(
            "WITH oos AS (SELECT lot_hist_id, parameter FROM evaluation "
            "WHERE alarm_type = 'OOS') "
            "SELECT parameter, COUNT(*) AS cnt FROM oos GROUP BY parameter"
        ),
        expect_valid=True,
        note="CTE 별칭 oos를 비허용 객체로 오인하면 안 된다.",
    ),
    SqlCase(
        case_id="G07_subquery",
        category="GREEN_SUBQUERY",
        sql=(
            "SELECT lot_id, wafer_no FROM metrology "
            "WHERE lot_hist_id IN (SELECT lot_hist_id FROM evaluation "
            "WHERE alarm_type = 'OOS')"
        ),
        expect_valid=True,
    ),
    SqlCase(
        case_id="G08_semicolon_in_literal",
        category="GREEN_EDGE",
        sql="SELECT action_id FROM action_history WHERE reason = 'a;b'",
        expect_valid=True,
        note="문자열 리터럴 내부 세미콜론을 다중 문장으로 오탐하면 안 된다.",
    ),
    SqlCase(
        case_id="G09_trailing_semicolon",
        category="GREEN_EDGE",
        sql="SELECT parameter_id FROM dim_parameter;",
        expect_valid=True,
        note="문장 끝 세미콜론 1개는 단일 문장이다.",
    ),
    SqlCase(
        case_id="G10_existing_limit",
        category="GREEN_EDGE",
        sql="SELECT lot_hist_id, value FROM fdc_trace LIMIT 10",
        expect_valid=True,
        note="500 이하 LIMIT은 그대로 유지한다(V4-D-2.3).",
    ),
)


# --------------------------------------------------------------------------
# RED 1 — 쓰기·DDL 구문
# --------------------------------------------------------------------------

RED_WRITE_DDL_CASES: tuple[SqlCase, ...] = (
    SqlCase(
        "R01_insert",
        "RED_WRITE",
        "INSERT INTO metrology (metrology_id) VALUES ('M1')",
        False,
        ("SELECT",),
    ),
    SqlCase(
        "R02_update",
        "RED_WRITE",
        "UPDATE action_history SET action_code = 'EQP_HOLD'",
        False,
        ("SELECT",),
    ),
    SqlCase(
        "R03_delete",
        "RED_WRITE",
        "DELETE FROM trace_alarm_history",
        False,
        ("SELECT",),
    ),
    SqlCase(
        "R04_truncate",
        "RED_WRITE",
        "TRUNCATE TABLE evaluation",
        False,
        ("SELECT",),
    ),
    SqlCase("R05_create", "RED_DDL", "CREATE TABLE tmp_x (id int)", False, ("SELECT",)),
    SqlCase(
        "R06_alter",
        "RED_DDL",
        "ALTER TABLE lot_history ADD COLUMN x int",
        False,
        ("SELECT",),
    ),
    SqlCase("R07_drop", "RED_DDL", "DROP TABLE summary_data", False, ("SELECT",)),
    SqlCase(
        "R08_select_into",
        "RED_WRITE",
        "SELECT * INTO tmp_copy FROM evaluation",
        False,
        ("SELECT",),
        note="SELECT로 시작하지만 결과를 새 테이블에 쓴다.",
    ),
    SqlCase(
        "R09_cte_write",
        "RED_WRITE",
        "WITH d AS (DELETE FROM evaluation RETURNING lot_hist_id) SELECT * FROM d",
        False,
        ("SELECT",),
        note="CTE 내부 쓰기 구문. AST 재귀 검사 필요.",
    ),
)


# --------------------------------------------------------------------------
# RED 2 — 다중 문장
# --------------------------------------------------------------------------

RED_MULTI_STATEMENT_CASES: tuple[SqlCase, ...] = (
    SqlCase(
        "R10_multi_drop",
        "RED_MULTI",
        "SELECT 1; DROP TABLE evaluation",
        False,
        ("다중", "문장"),
    ),
    SqlCase(
        "R11_multi_select",
        "RED_MULTI",
        "SELECT parameter FROM dim_parameter; SELECT lot_id FROM lot_history",
        False,
        ("다중", "문장"),
        note="둘 다 SELECT여도 다중 문장은 차단한다.",
    ),
    SqlCase(
        "R12_empty",
        "RED_MULTI",
        "   ",
        False,
        ("문장",),
        note="빈 SQL은 문장 0개로 차단한다.",
    ),
)


# --------------------------------------------------------------------------
# RED 3 — 비허용 객체
# --------------------------------------------------------------------------

RED_NOT_ALLOWED_CASES: tuple[SqlCase, ...] = (
    SqlCase(
        "R13_runtime_agent_run",
        "RED_NOT_ALLOWED",
        "SELECT * FROM agent_run",
        False,
        ("허용",),
        note="002 runtime 테이블. Text2SQL allowlist 밖이다.",
    ),
    SqlCase(
        "R14_runtime_approval",
        "RED_NOT_ALLOWED",
        "SELECT approval_id FROM approval_request",
        False,
        ("허용",),
    ),
    SqlCase(
        "R15_audit_log",
        "RED_NOT_ALLOWED",
        "SELECT * FROM audit_log",
        False,
        ("허용",),
        note="감사로그는 전용 API(GET /audit-logs)로만 조회한다. FR-D-07.",
    ),
    SqlCase(
        "R16_legacy_fdc_alarm",
        "RED_NOT_ALLOWED",
        "SELECT * FROM fdc_alarm",
        False,
        ("허용",),
        note="구 데이터 테이블. v2.0에서 폐기됐다.",
    ),
    SqlCase(
        "R17_legacy_dim_sensor",
        "RED_NOT_ALLOWED",
        "SELECT * FROM dim_sensor",
        False,
        ("허용",),
        note="구 마스터. dim_parameter로 대체됐다.",
    ),
    SqlCase(
        "R18_hidden_in_cte",
        "RED_NOT_ALLOWED",
        "WITH x AS (SELECT * FROM agent_run) SELECT * FROM x",
        False,
        ("허용",),
        note="CTE 내부에 비허용 객체를 숨긴 우회.",
    ),
    SqlCase(
        "R19_hidden_in_subquery",
        "RED_NOT_ALLOWED",
        "SELECT lot_id FROM lot_history WHERE lot_id IN (SELECT lot_id FROM agent_run)",
        False,
        ("허용",),
        note="서브쿼리 내부 우회.",
    ),
    SqlCase(
        "R20_hidden_in_join",
        "RED_NOT_ALLOWED",
        "SELECT e.lot_hist_id FROM evaluation e JOIN audit_log a ON true",
        False,
        ("허용",),
        note="JOIN 대상에 비허용 객체.",
    ),
    SqlCase(
        "R21_qualified_other_db",
        "RED_NOT_ALLOWED",
        "SELECT * FROM otherdb.public.evaluation",
        False,
        ("허용",),
        note="db+name 정규화 후 판정해야 한다. 다른 DB 참조는 차단.",
    ),
    SqlCase(
        "R22_non_public_schema",
        "RED_NOT_ALLOWED",
        "SELECT * FROM secret.evaluation",
        False,
        ("허용",),
        note="public 외 schema 차단.",
    ),
)


# --------------------------------------------------------------------------
# RED 4 — 위험 함수
# --------------------------------------------------------------------------

RED_DANGEROUS_FUNCTION_CASES: tuple[SqlCase, ...] = (
    SqlCase("R23_pg_sleep", "RED_FUNCTION", "SELECT pg_sleep(10)", False, ("함수",)),
    SqlCase(
        "R24_pg_read_file",
        "RED_FUNCTION",
        "SELECT pg_read_file('/etc/passwd')",
        False,
        ("함수",),
    ),
    SqlCase(
        "R25_dblink",
        "RED_FUNCTION",
        "SELECT * FROM dblink('host=x', 'SELECT 1') AS t(x int)",
        False,
        ("함수",),
    ),
    SqlCase(
        "R26_lo_import",
        "RED_FUNCTION",
        "SELECT lo_import('/etc/passwd')",
        False,
        ("함수",),
    ),
    SqlCase(
        "R27_nested_pg_sleep",
        "RED_FUNCTION",
        "SELECT lot_id FROM lot_history WHERE pg_sleep(5) IS NULL",
        False,
        ("함수",),
        note="WHERE 절에 숨긴 위험 함수.",
    ),
)


# --------------------------------------------------------------------------
# RED 5 — 시스템 카탈로그
# --------------------------------------------------------------------------

RED_CATALOG_CASES: tuple[SqlCase, ...] = (
    SqlCase(
        "R28_pg_catalog_qualified",
        "RED_CATALOG",
        "SELECT * FROM pg_catalog.pg_tables",
        False,
        ("카탈로그",),
    ),
    SqlCase(
        "R29_information_schema",
        "RED_CATALOG",
        "SELECT table_name FROM information_schema.tables",
        False,
        ("카탈로그",),
    ),
    SqlCase(
        "R30_bare_pg_tables",
        "RED_CATALOG",
        "SELECT * FROM pg_tables",
        False,
        ("카탈로그",),
        note="스키마 수식 없는 카탈로그 접근. search_path로 해석되므로 차단.",
    ),
    SqlCase(
        "R31_bare_pg_class",
        "RED_CATALOG",
        "SELECT relname FROM pg_class",
        False,
        ("카탈로그",),
    ),
    SqlCase(
        "R32_catalog_in_cte",
        "RED_CATALOG",
        "WITH t AS (SELECT * FROM information_schema.columns) SELECT * FROM t",
        False,
        ("카탈로그",),
    ),
)


# --------------------------------------------------------------------------
# RED 6 — 존재하지 않는 컬럼
# --------------------------------------------------------------------------

RED_UNKNOWN_COLUMN_CASES: tuple[SqlCase, ...] = (
    SqlCase(
        "R33_unknown_column",
        "RED_COLUMN",
        "SELECT nonexistent_col FROM evaluation",
        False,
        ("컬럼",),
        note="pool별 column allowlist 필요. 선행 V4-D-1.3.",
    ),
    SqlCase(
        "R34_unknown_column_qualified",
        "RED_COLUMN",
        "SELECT e.no_such_field FROM evaluation e",
        False,
        ("컬럼",),
        note="별칭 수식 컬럼도 해석해야 한다.",
    ),
)


RED_CASES: tuple[SqlCase, ...] = (
    *RED_WRITE_DDL_CASES,
    *RED_MULTI_STATEMENT_CASES,
    *RED_NOT_ALLOWED_CASES,
    *RED_DANGEROUS_FUNCTION_CASES,
    *RED_CATALOG_CASES,
    *RED_UNKNOWN_COLUMN_CASES,
)

ALL_CASES: tuple[SqlCase, ...] = (*GREEN_CASES, *RED_CASES)

#: V4-D-2.3에서 검증할 LIMIT 주입·축소·유지 기대값.
LIMIT_EXPECTATIONS: tuple[tuple[str, str, int], ...] = (
    ("L01_inject", "SELECT lot_id FROM lot_history", 500),
    ("L02_shrink", "SELECT lot_id FROM lot_history LIMIT 5000", 500),
    ("L03_keep", "SELECT lot_id FROM lot_history LIMIT 10", 10),
)

#: 검증 결과에 반드시 포함돼야 하는 check key.
EXPECTED_CHECK_KEYS: tuple[str, ...] = (
    "single_select",
    "allowed_objects",
    "column_allowlist",
    "no_catalog_access",
    "no_dangerous_function",
    "limit_enforced",
)


# --------------------------------------------------------------------------
# 테스트
# --------------------------------------------------------------------------


def _validate(sql: str):
    """검증기 호출 래퍼.

    V4-D-2.2 미구현 상태에서는 명시적으로 실패시킨다.
    collection 단계에서 죽지 않고 케이스별로 red를 보여주기 위한 장치다.
    """
    if validate_sql is None:
        pytest.fail(
            "V4-D-2.2 sql_validator.validate_sql 미구현 — 의도된 red 상태 (V4-D-2.1)"
        )
    return validate_sql(sql)


def _ids(cases: tuple[SqlCase, ...]) -> list[str]:
    return [case.case_id for case in cases]


@requires_validator
@pytest.mark.parametrize("case", GREEN_CASES, ids=_ids(GREEN_CASES))
def test_green_case_is_valid(case: SqlCase) -> None:
    """허용 범위 내 정상 질의는 통과해야 한다."""
    result = _validate(case.sql)

    assert result.valid is True, f"{case.case_id}: {case.note or case.sql}"
    assert result.normalized_sql, "통과한 질의는 normalized_sql을 제공해야 한다"


@requires_validator
@pytest.mark.parametrize("case", RED_CASES, ids=_ids(RED_CASES))
def test_red_case_is_blocked(case: SqlCase) -> None:
    """차단 대상은 valid=false와 사유를 반환하고, 예외를 던지지 않는다."""
    result = _validate(case.sql)

    assert result.valid is False, f"{case.case_id}: {case.note or case.sql}"
    assert result.reason, "차단된 질의는 reason이 있어야 한다"


@requires_validator
@pytest.mark.parametrize("case", RED_CASES, ids=_ids(RED_CASES))
def test_red_case_reason_mentions_cause(case: SqlCase) -> None:
    """거부 사유가 원인 범주를 식별할 수 있어야 한다."""
    if not case.reason_keywords:
        pytest.skip("기대 키워드가 지정되지 않은 케이스")

    result = _validate(case.sql)

    assert any(keyword in result.reason for keyword in case.reason_keywords), (
        f"{case.case_id}: reason={result.reason!r}에 "
        f"{case.reason_keywords} 중 하나가 포함돼야 한다"
    )


@requires_validator
@pytest.mark.parametrize(
    ("case_id", "sql", "expected_limit"),
    LIMIT_EXPECTATIONS,
    ids=[case_id for case_id, _, _ in LIMIT_EXPECTATIONS],
)
def test_limit_is_enforced(case_id: str, sql: str, expected_limit: int) -> None:
    """LIMIT은 주입·축소·유지 세 분기를 따른다. (V4-D-2.3)"""
    result = _validate(sql)

    assert result.valid is True, case_id
    assert (
        f"LIMIT {expected_limit}" in (result.normalized_sql or "").upper()
    ), f"{case_id}: normalized_sql에 LIMIT {expected_limit}이 반영돼야 한다"


@requires_validator
def test_checks_expose_all_keys() -> None:
    """검증 결과는 단계별 check를 노출해 화면이 근거를 표시할 수 있어야 한다."""
    result = _validate("SELECT parameter FROM dim_parameter")

    assert result.checks is not None, "checks는 null이 아니어야 한다"
    keys = {check.key for check in result.checks}
    missing = set(EXPECTED_CHECK_KEYS) - keys
    assert not missing, f"누락된 check key: {sorted(missing)}"


@requires_validator
def test_invalid_sql_does_not_raise() -> None:
    """파싱 불가 SQL도 예외 대신 valid=false로 응답해야 한다."""
    result = _validate("SELEC * FRM evaluation")

    assert result.valid is False
    assert result.reason


def test_case_ids_are_unique() -> None:
    """케이스 ID 중복은 평가셋 집계를 망가뜨린다."""
    case_ids = [case.case_id for case in ALL_CASES]

    assert len(case_ids) == len(set(case_ids)), "case_id가 중복됐다"


def test_defense_categories_are_covered() -> None:
    """FR-D-02가 요구하는 방어 6종이 모두 포함돼야 한다."""
    required = {
        "RED_WRITE",
        "RED_MULTI",
        "RED_NOT_ALLOWED",
        "RED_FUNCTION",
        "RED_CATALOG",
        "RED_COLUMN",
    }
    present = {case.category for case in RED_CASES}

    assert required <= present, f"누락된 방어 범주: {sorted(required - present)}"
