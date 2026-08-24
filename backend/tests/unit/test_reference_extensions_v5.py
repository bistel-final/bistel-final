"""`V5-CM-3.1` final reference extension 계약 회귀.

DB를 열지 않는다. SQL 실물과 순수 판정 함수만 본다. 실제 PostgreSQL 동작은
`test_reference_extensions_v5_container.py`가 본다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPOSITORY_ROOT / "backend" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_reference_extensions_v5 as v5  # noqa: E402

MANIFEST_DIR = REPOSITORY_ROOT / "infra" / "bootstrap" / "manifests"
#: **구 계보 manifest는 history에 있다.**
#:
#: `V5-CM-1.8`이 active를 final로 교체했다. CM-3.1의 migration contract는 "무엇을
#: 대체했는가"를 기록하므로 그 입력은 정의상 history 계보다. active에서 읽으면
#: 파일이 없거나(evaluation) final manifest를 대체 대상으로 오인한다(runtime).
HISTORY_MANIFEST_DIR = (
    REPOSITORY_ROOT / "infra" / "bootstrap" / "history" / "kosa_0813" / "manifests"
)
PROFILE_MANIFESTS = {
    "runtime": HISTORY_MANIFEST_DIR / "runtime.runtime_clean.json",
    "evaluation": HISTORY_MANIFEST_DIR / "evaluation.evaluation_mock.json",
}


def _rows(
    columns: tuple[tuple[str, str, int | None, bool], ...],
) -> list[dict[str, Any]]:
    return [
        {
            "column_name": name,
            "data_type": data_type,
            "character_maximum_length": length,
            "is_nullable": "YES" if nullable else "NO",
        }
        for name, data_type, length, nullable in columns
    ]


FIXTURE_DIR = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_3_1"
CANONICAL_VIEW = FIXTURE_DIR / "canonical_view.sql"


def _canonical_view_definition() -> str:
    """격리 PostgreSQL 16이 돌려준 실측 `pg_get_viewdef` 정규형."""

    body = CANONICAL_VIEW.read_text(encoding="utf-8")
    return "\n".join(
        line for line in body.splitlines() if not line.startswith("--")
    ).strip()


def _constraint_rows(mapping: Any = None) -> list[dict[str, Any]]:
    """`pg_constraint` + `pg_get_constraintdef` 행."""

    source = v5.R03_CONSTRAINT_DEFINITIONS if mapping is None else mapping
    return [
        {"conname": name, "contype": contype, "definition": definition}
        for name, (contype, definition) in source.items()
    ]


def _view_rows(
    columns: tuple[tuple[str, str], ...] | None = None,
) -> list[dict[str, Any]]:
    source = v5.VIEW_COLUMNS if columns is None else columns
    return [{"column_name": name, "data_type": data_type} for name, data_type in source]


def _fk_rows() -> list[dict[str, Any]]:
    return [
        {
            "conname": name,
            "column_name": column,
            "referenced_table": parent,
            "delete_action": v5.EXPECTED_FK_ACTION,
        }
        for name, (column, parent) in v5.R03_FOREIGN_KEYS.items()
    ]


# ---------------------------------------------------------------------------
# SQL 실물 (계획 §4.1 · §9.1)
# ---------------------------------------------------------------------------


def test_v5_sql_files_stay_inside_their_statement_allowlist() -> None:
    """두 실물 파일이 각자의 허용 `(operation, target)` 안에 있다."""

    v5.assert_migration_files_in_scope()
    for path in (v5.CANONICAL_SQL, v5.SUCCESSOR_SQL):
        text = path.read_text(encoding="utf-8")
        assert set(v5.created_objects(text)) == {v5.R03_TABLE, v5.ALARM_VIEW}


@pytest.mark.parametrize(
    "token", ["document", "document_chunk", "nl_query_log", "vector"]
)
def test_other_area_objects_never_appear_in_executable_sql(token: str) -> None:
    """ "만들지 않는다"는 "지운다"가 아니다. 실행 statement에 이름조차 없어야 한다."""

    for path in (v5.CANONICAL_SQL, v5.SUCCESSOR_SQL):
        body = v5.executable_sql(path.read_text(encoding="utf-8")).lower()
        assert token not in body, f"{path.name}: {token}"


def test_create_target_guard_rejects_an_extra_object() -> None:
    """**CREATE 대상 검사만** 건드리는 입력이어야 한다.

    `CREATE TABLE nl_query_log`은 금지 토큰 검사에도 걸려서, 둘 중 하나를 지워도
    통과한다 — 두 방어가 서로를 가린다(변이 N1·N2). 금지 토큰이 아닌 이름을 쓴다.
    """

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_sql_scope("CREATE TABLE some_side_table (id int);")
    assert caught.value.reason_code == "SQL_SCOPE_VIOLATION"

    with pytest.raises(v5.ReferenceV5Error):
        v5.assert_sql_scope("CREATE INDEX ix_side ON r03_alarm_history (alarm_id);")


def test_forbidden_token_guard_rejects_a_non_create_reference() -> None:
    """**금지 토큰 검사만** 건드리는 입력이어야 한다.

    CREATE가 아니면 대상 검사는 통과하므로 이 입력은 토큰 검사만 깨뜨린다.
    "만들지 않는다"는 "지운다"도 아니므로 DROP·SELECT 모두 금지다.
    """

    # **statement 자체는 허용 목록 안**이어야 토큰 검사만 남는다. `SELECT * FROM
    # nl_query_log`은 `(SELECT, nl_query_log)`라 allowlist에도 걸려서 두 방어가 서로를
    # 가린다(변이 N2).
    for statement in (
        "CREATE VIEW v_alarm_event AS SELECT * FROM document;",
        "CREATE VIEW v_alarm_event AS SELECT 1 FROM nl_query_log;",
        "CREATE TABLE r03_alarm_history (embedding vector(1024));",
        "COMMENT ON TABLE r03_alarm_history IS 'document_chunk를 대체한다';",
    ):
        allowed = v5.classify_statement(statement) in v5.CANONICAL_STATEMENTS
        assert allowed, f"allowlist 안이어야 토큰 검사만 남는다: {statement}"
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.assert_sql_scope(statement)
        assert caught.value.reason_code == "SQL_SCOPE_VIOLATION", statement


def test_comments_may_name_other_areas() -> None:
    """파일 머리말이 다른 영역을 **설명**하는 것과 **건드리는** 것은 다르다."""

    v5.assert_sql_scope(
        "-- nl_query_log는 만들지 않는다\nCREATE TABLE r03_alarm_history ();"
    )


@pytest.mark.parametrize("path", [v5.CANONICAL_SQL, v5.SUCCESSOR_SQL])
def test_view_sql_resolves_on_wafer_id(path: Path) -> None:
    """final epoch의 alarm `wafer`는 varchar라 `wafer_no`와 비교할 수 없다.

    설계 §3.3 `:415`.
    """

    text = path.read_text(encoding="utf-8")
    v5.assert_view_sql_shape(text)
    body = " ".join(v5.executable_sql(text).split())
    assert body.count(v5.WAFER_JOIN) == 2
    assert v5.LEGACY_WAFER_JOIN not in body
    assert body.count("UNION ALL") == 2


def _view_sql(*, joins: int, legacy: bool, unions: int = 2) -> str:
    """계약을 만족하는 최소 View SQL을 만들고 한 가지만 어긋나게 한다."""

    body = ["CREATE VIEW v_alarm_event AS SELECT 1"]
    for _ in range(joins):
        body.append("LEFT JOIN lot_history AS h ON h.wafer_id = a.wafer")
    if legacy:
        body.append("LEFT JOIN lot_history AS h ON h.wafer_no = a.wafer")
    body.extend(["UNION ALL SELECT 1"] * unions)
    body.extend(f"'{code}'" for code in v5.VIEW_RULE_CODES.values())
    return " ".join(body) + ";"


def test_view_sql_requires_exactly_two_wafer_id_joins() -> None:
    """**join 개수만** 어긋나게 한다. legacy join은 없다(변이 N7)."""

    v5.assert_view_sql_shape(_view_sql(joins=2, legacy=False))
    for joins in (0, 1, 3):
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.assert_view_sql_shape(_view_sql(joins=joins, legacy=False))
        assert caught.value.reason_code == "VIEW_CONTRACT_MISMATCH", joins


def test_view_sql_rejects_the_legacy_join_even_with_two_correct_joins() -> None:
    """**legacy join 검사만** 어긋나게 한다. 개수는 맞다(변이 N8)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_view_sql_shape(_view_sql(joins=2, legacy=True))
    assert caught.value.reason_code == "VIEW_CONTRACT_MISMATCH"


def test_view_sql_requires_two_union_all() -> None:
    with pytest.raises(v5.ReferenceV5Error):
        v5.assert_view_sql_shape(_view_sql(joins=2, legacy=False, unions=1))


def test_both_paths_share_one_ddl_body() -> None:
    """두 경로가 다른 DDL을 만들면 `V5_REFERENCE_FINAL`이 하나의 상태가 못 된다.

    successor는 guard와 drop 뒤에 canonical DDL을 **그대로** 이어 붙인다.
    """

    canonical = v5.CANONICAL_SQL.read_text(encoding="utf-8")
    successor = v5.SUCCESSOR_SQL.read_text(encoding="utf-8")
    body = canonical[canonical.index("CREATE TABLE r03_alarm_history") :]
    assert successor.endswith(body)


def test_successor_guards_row_count_and_v4_shape() -> None:
    """0행·V4 형상 확인 없이 drop하면 데이터를 추측해 버리는 것과 같다."""

    body = v5.SUCCESSOR_SQL.read_text(encoding="utf-8")
    assert "count(*)" in body and "RAISE EXCEPTION" in body
    assert "member_refs" in body, "V4 형상 확인이 없다"
    # guard가 DROP보다 앞에 있어야 한다.
    assert body.index("RAISE EXCEPTION") < body.index("DROP TABLE")


def test_successor_drops_the_view_without_cascade() -> None:
    """CASCADE는 예상 못 한 dependent object를 조용히 지운다(계획 §6.5)."""

    statements = v5.split_statements(v5.SUCCESSOR_SQL.read_text(encoding="utf-8"))
    pairs = [v5.classify_statement(statement) for statement in statements]
    assert ("DROP VIEW", v5.ALARM_VIEW) in pairs
    assert ("DROP TABLE", v5.R03_TABLE) in pairs
    # View가 R03를 참조하므로 View DROP이 먼저여야 한다.
    assert pairs.index(("DROP VIEW", v5.ALARM_VIEW)) < pairs.index(
        ("DROP TABLE", v5.R03_TABLE)
    )
    assert "CASCADE" not in " ".join(statements).upper()


def test_migration_bundle_hash_covers_both_files(tmp_path: Path) -> None:
    """한쪽만 바뀌어도 identity가 달라져야 한다."""

    base = v5.migration_bundle_sha256()
    assert base == v5.migration_bundle_sha256()

    changed = tmp_path / "changed.sql"
    changed.write_text(
        v5.SUCCESSOR_SQL.read_text(encoding="utf-8") + "\n-- drift\nSELECT 1;",
        encoding="utf-8",
    )
    assert v5.migration_bundle_sha256(successor=changed) != base

    # 줄 끝 공백·CRLF 같은 **의미 없는** 차이만 흡수한다.
    trailing = tmp_path / "trailing.sql"
    trailing.write_text(
        v5.SUCCESSOR_SQL.read_text(encoding="utf-8").replace("\n", "   \n"),
        encoding="utf-8",
    )
    assert v5.migration_bundle_sha256(successor=trailing) == base

    # string literal 안의 공백은 **의미가 있다.** 흡수하면 안 된다
    # (구현리뷰 1차 권장 1).
    literal = tmp_path / "literal.sql"
    literal.write_text(
        v5.SUCCESSOR_SQL.read_text(encoding="utf-8").replace(
            "'R03_CONSEC_V1'", "'R03_CONSEC_V1 '"
        ),
        encoding="utf-8",
    )
    assert v5.migration_bundle_sha256(successor=literal) != base


def test_migration_id_does_not_collide_with_the_v4_lineage() -> None:
    """`applied_migrations`가 이름 문자열로 참조하므로 겹치면 구분되지 않는다."""

    assert v5.MIGRATION_ID == "v5_001_reference_extensions_final"
    assert v5.MIGRATION_ID != "001_reference_extensions"
    assert not v5.MIGRATION_ID.startswith("001_")


# ---------------------------------------------------------------------------
# catalog 판정 (계획 §5 · §6)
# ---------------------------------------------------------------------------


def test_r03_contract_is_the_design_twelve_columns() -> None:
    """설계 §3.2 필수 컬럼과 순서까지 같아야 한다."""

    assert len(v5.R03_COLUMNS) == 12
    assert [name for name, _t, _l, _n in v5.R03_COLUMNS] == [
        "alarm_id",
        "occurred_at",
        "lot_hist_id",
        "lot_id",
        "equipment_id",
        "chamber_id",
        "parameter_id",
        "recipe_step_no",
        "trigger_wafer_no",
        "member_wafer_refs",
        "member_alarm_refs",
        "policy_version",
    ]
    # 구 V4의 단일 `member_refs`는 더 이상 없다.
    assert "member_refs" not in {name for name, _t, _l, _n in v5.R03_COLUMNS}
    assert all(not nullable for _n, _t, _l, nullable in v5.R03_COLUMNS)
    v5.assert_r03_columns(_rows(v5.R03_COLUMNS))


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda rows: rows[:-1], id="컬럼_누락"),
        pytest.param(lambda rows: rows[::-1], id="순서_역전"),
        pytest.param(
            lambda rows: [{**rows[0], "data_type": "text"}, *rows[1:]], id="타입_변경"
        ),
        pytest.param(
            lambda rows: [{**rows[0], "is_nullable": "YES"}, *rows[1:]], id="NULL_허용"
        ),
        pytest.param(
            lambda rows: [{**rows[0], "character_maximum_length": 32}, *rows[1:]],
            id="길이_변경",
        ),
        pytest.param(
            lambda rows: [
                {**row, "column_name": "member_refs"}
                if row["column_name"] == "member_wafer_refs"
                else row
                for row in rows
            ],
            id="구_member_refs",
        ),
    ],
)
def test_r03_column_drift_is_refused(mutate: Any) -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_r03_columns(mutate(_rows(v5.R03_COLUMNS)))
    assert caught.value.reason_code == "R03_CONTRACT_MISMATCH"


def test_r03_constraint_counts_are_exact() -> None:
    """계획 §5.2가 고정한 개수. CHECK 7개가 핵심이다."""

    assert dict(v5.R03_CONSTRAINT_COUNTS) == {"p": 1, "u": 1, "f": 2, "c": 7}
    assert len(v5.R03_CONSTRAINTS) == sum(v5.R03_CONSTRAINT_COUNTS.values()) == 11
    # 개수는 이름·종류 map에서 **유도**돼야 한다. 두 상수가 갈리면 계약이 둘이 된다.
    assert v5.constraint_type_counts() == dict(v5.R03_CONSTRAINT_COUNTS)
    v5.assert_r03_constraints(_constraint_rows())


def test_constraint_count_constant_is_derived_not_asserted() -> None:
    """`R03_CONSTRAINT_COUNTS`를 손으로 고치면 이름 map과 갈린다(변이 N4)."""

    assert v5.constraint_type_counts({"a": "c", "b": "c"}) == {"c": 2}  # noqa: E501
    drifted = {**v5.R03_CONSTRAINTS, "r03_extra_check": "c"}
    assert v5.constraint_type_counts(drifted) != dict(v5.R03_CONSTRAINT_COUNTS)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda m: {k: v for k, v in m.items() if v[0] != "c"}, id="CHECK_전부_제거"
        ),
        pytest.param(
            lambda m: {
                **m,
                "r03_alarm_history_policy_version_check": (
                    "u",
                    m["r03_alarm_history_policy_version_check"][1],
                ),
            },
            id="종류_변경",
        ),
        pytest.param(
            lambda m: {**m, "extra_check": ("c", "CHECK (true)")}, id="여분_추가"
        ),
        pytest.param(
            lambda m: {
                ("renamed" if k == "r03_alarm_history_incident_key" else k): v
                for k, v in m.items()
            },
            id="이름_변경",
        ),
        # 구현리뷰 1차 필수 2 — 이름·종류는 같고 **정의만** 틀린 경우.
        pytest.param(
            lambda m: {
                k: (v[0], "CHECK (false)") if v[0] == "c" else v for k, v in m.items()
            },
            id="CHECK_식_변조",
        ),
        pytest.param(
            lambda m: {**m, "r03_alarm_history_pkey": ("p", "PRIMARY KEY (lot_id)")},
            id="PK_컬럼_변조",
        ),
        pytest.param(
            lambda m: {
                **m,
                "r03_alarm_history_incident_key": ("u", "UNIQUE (lot_hist_id)"),
            },
            id="unique_컬럼_변조",
        ),
        pytest.param(
            lambda m: {
                **m,
                "r03_alarm_history_lot_hist_id_fkey": (
                    "f",
                    "FOREIGN KEY (lot_hist_id) REFERENCES lot_history(lot_id)",
                ),
            },
            id="FK_참조컬럼_변조",
        ),
    ],
)
def test_r03_constraint_drift_is_refused(mutate: Any) -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_r03_constraints(
            _constraint_rows(mutate(dict(v5.R03_CONSTRAINT_DEFINITIONS)))
        )
    assert caught.value.reason_code == "R03_CONTRACT_MISMATCH"


def test_r03_foreign_keys_must_be_no_action() -> None:
    """CASCADE면 base 9 DELETE가 R03로 번진다."""

    v5.assert_r03_foreign_keys(_fk_rows())
    cascading = [{**row, "delete_action": "c"} for row in _fk_rows()]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_r03_foreign_keys(cascading)
    assert caught.value.reason_code == "R03_CONTRACT_MISMATCH"


def test_view_contract_is_the_design_seventeen_columns() -> None:
    """설계 §3.5 목록과 순서까지 같아야 한다."""

    assert len(v5.VIEW_COLUMNS) == 17
    assert [name for name, _t in v5.VIEW_COLUMNS] == [
        "source",
        "alarm_id",
        "occurred_at",
        "area",
        "equipment_id",
        "chamber_id",
        "parameter_id",
        "recipe_id",
        "lot_hist_id",
        "lot_id",
        "wafer_id",
        "wafer_no",
        "recipe_step_no",
        "seq_no",
        "value",
        "alarm_type",
        "rule_code",
    ]
    v5.assert_view_columns(_view_rows())


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda cols: cols[:-1], id="컬럼_누락"),
        pytest.param(lambda cols: cols[::-1], id="순서_역전"),
        pytest.param(lambda cols: (("source", "text"), *cols[1:]), id="타입_변경"),
        pytest.param(lambda cols: (("origin", cols[0][1]), *cols[1:]), id="이름_변경"),
    ],
)
def test_view_column_drift_is_refused(mutate: Any) -> None:
    """positive만 있으면 exact 비교를 지워도 통과한다(변이 N5)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_view_columns(_view_rows(mutate(v5.VIEW_COLUMNS)))
    assert caught.value.reason_code == "VIEW_CONTRACT_MISMATCH"


def test_view_does_not_carry_dto_enrichment_fields() -> None:
    """AlarmEvidence(설계 §3.1)는 19필드지만 View는 17이다(계획 §6.1).

    `limit_type`·`limit_value`·`source_detail`은 Repository가 보강한다. 설계 §3.5의
    "R03의 고정 limit field는 NULL"이 View가 그 셋을 갖는 것처럼 읽혀 혼동하기 쉽다.
    """

    names = {name for name, _t in v5.VIEW_COLUMNS}
    assert not (names & v5.DTO_ONLY_FIELDS)
    assert v5.DTO_ONLY_FIELDS == {"limit_type", "limit_value", "source_detail"}
    # 실행 시 재확인은 두지 않는다 — 17컬럼 exact 비교가 이미 함의하고, 어떤 입력으로도
    # 단독으로 깨뜨릴 수 없어 죽은 방어가 된다(변이 N6).


def test_view_rule_codes_and_r03_alarm_type() -> None:
    """R03는 `alarm_type=OOS` + `rule_code=R03_CONSEC`다.

    `R03_CONSEC`를 AlarmType에 추가하지 않는다(설계 §3.1 `:373`).
    """

    assert dict(v5.VIEW_RULE_CODES) == {
        "TRACE": "TRACE_OOS",
        "SUMMARY": "SUMMARY_OOC",
        "R03": "R03_CONSEC",
    }
    assert v5.R03_ALARM_TYPE == "OOS"


# ---------------------------------------------------------------------------
# schema identity와 data phase 분리 (계획 §4.2 · 1차 계획리뷰 필수 2)
# ---------------------------------------------------------------------------


def _signature(**overrides: Any) -> str:
    payload = {
        "r03_columns": _rows(v5.R03_COLUMNS),
        "r03_constraints": _constraint_rows(),
        "view_columns": _view_rows(),
        "view_definition": _canonical_view_definition(),
        "r03_comment": v5.R03_COMMENT,
        "view_comment": v5.VIEW_COMMENT,
    }
    payload.update(overrides)
    return v5.schema_signature_sha256(**payload)


def test_schema_signature_is_independent_of_row_counts() -> None:
    """`V5-A-1.4`가 R03 3건을 넣어도 schema identity는 그대로여야 한다.

    행 수를 identity에 넣으면 정상 진행한 DB가 drift로 판정된다.
    """

    import inspect

    source = inspect.getsource(v5.schema_signature_sha256)
    for forbidden in ("row", "rows", "count"):
        assert f'"{forbidden}"' not in source
    # 같은 schema면 같은 signature다. 행 수는 입력조차 아니다.
    assert set(inspect.signature(v5.schema_signature_sha256).parameters) == {
        "r03_columns",
        "r03_constraints",
        "view_columns",
        "view_definition",
        # comment도 schema 계약이다(구현리뷰 7차 필수 1).
        "r03_comment",
        "view_comment",
    }
    assert _signature() == _signature()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        pytest.param(
            {"r03_columns": _rows(v5.R03_COLUMNS)[:-1]},
            "R03_CONTRACT_MISMATCH",
            id="R03_컬럼",
        ),
        pytest.param(
            {"r03_constraints": _constraint_rows({"only": ("c", "CHECK (true)")})},
            "R03_CONTRACT_MISMATCH",
            id="constraint_집합",
        ),
        pytest.param(
            {
                "r03_constraints": _constraint_rows(
                    {
                        k: (v[0], "CHECK (false)") if v[0] == "c" else v
                        for k, v in v5.R03_CONSTRAINT_DEFINITIONS.items()
                    }
                )
            },
            "R03_CONTRACT_MISMATCH",
            id="constraint_정의만",
        ),
        pytest.param(
            {"view_columns": _view_rows(v5.VIEW_COLUMNS[:-1])},
            "VIEW_CONTRACT_MISMATCH",
            id="view_컬럼",
        ),
    ],
)
def test_a_non_canonical_catalog_cannot_become_a_signature(
    overrides: Any, reason: str
) -> None:
    """**non-canonical catalog는 signature를 만들 수 없다.**

    View만 identity를 보고 R03 컬럼·constraint·View 컬럼은 안 보면, 이름·종류는 같고
    `CHECK (false)`인 catalog가 canonical과 같은 signature를 낸다(구현리뷰 2차 필수 3).
    """

    with pytest.raises(v5.ReferenceV5Error) as caught:
        _signature(**overrides)
    assert caught.value.reason_code == reason


def test_constraint_definition_is_in_the_signature_payload() -> None:
    """정의를 payload에 담지 않으면 `CHECK (false)`가 같은 hash를 낸다."""

    import inspect

    source = inspect.getsource(v5.schema_signature_sha256)
    assert '"definition"' in source, "constraint 정의가 signature payload에 없다"


def test_a_non_canonical_view_definition_cannot_become_a_signature() -> None:
    """틀린 정의를 hash하기만 하면 그것이 최초 marker의 정본이 된다.

    정의를 hash하기 전에 canonical identity와 대조해야 한다(구현리뷰 1차 필수 3).
    """

    canonical = _canonical_view_definition()
    v5.assert_view_identity(canonical)
    assert v5.view_definition_sha256(canonical) == v5.CANONICAL_VIEW_SHA256

    # `pg_get_viewdef`가 정규화한 실제 표기를 바꾼다. 원문에 없는 문자열을 replace하면
    # 아무것도 안 바뀌어 회귀가 공허해진다.
    fakes = {
        "가짜_SELECT": "SELECT 999 AS source",
        "rule_code": canonical.replace("'TRACE_OOS'", "'TRACE_XXX'"),
        "wafer_resolve": canonical.replace(
            "h.wafer_id::text = a.wafer::text", "h.wafer_no = a.wafer"
        ),
        "join_종류": canonical.replace("LEFT JOIN", "JOIN"),
        "R03_alarm_type": canonical.replace("'OOS'", "'OOC'"),
        "branch_mapping": canonical.replace("a.wafer AS wafer_id", "h.wafer_id"),
    }
    for label, fake in fakes.items():
        assert fake != canonical, f"{label}: 원문이 안 바뀌었다"
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.assert_view_identity(fake)
        assert caught.value.reason_code == "VIEW_CONTRACT_MISMATCH", label
        with pytest.raises(v5.ReferenceV5Error):
            _signature(view_definition=fake)


def test_canonical_view_fixture_is_the_measured_definition() -> None:
    """fixture는 손으로 쓴 것이 아니라 PostgreSQL 16이 돌려준 정규형이다."""

    body = CANONICAL_VIEW.read_text(encoding="utf-8")
    assert "pg_get_viewdef" in body, "출처 주석이 없다"
    assert v5.VIEW_SERVER_MAJOR == 16
    definition = _canonical_view_definition()
    # 17 branch mapping이 실제로 들어 있다.
    for source in v5.VIEW_SOURCES:
        assert f"'{source}'" in definition, source
    for rule_code in v5.VIEW_RULE_CODES.values():
        assert f"'{rule_code}'" in definition, rule_code
    assert definition.count("UNION ALL") == 2
    # PostgreSQL이 정규화하면 `h.wafer_id::text = a.wafer::text`가 된다.
    assert definition.count("h.wafer_id::text = a.wafer::text") == 2
    assert "h.wafer_no = a.wafer" not in definition
    assert (
        definition.count("LEFT JOIN") == 2
    ), "TRACE·SUMMARY는 stored alarm을 숨기면 안 된다"


def test_both_normal_data_phases_pass() -> None:
    """`--verify`는 두 정상 phase를 모두 통과시켜야 한다(계획 §4.2)."""

    assert v5.classify_data_phase(r03_rows=0, view_rows=189) == "REFERENCE_EMPTY"
    assert v5.classify_data_phase(r03_rows=3, view_rows=192) == "R03_POPULATED"


@pytest.mark.parametrize(
    ("r03_rows", "view_rows"),
    [(1, 190), (0, 192), (3, 189), (2, 191)],
)
def test_partial_data_phase_is_refused(r03_rows: int, view_rows: int) -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.classify_data_phase(r03_rows=r03_rows, view_rows=view_rows)
    assert caught.value.reason_code == "DATA_PHASE_UNKNOWN"


def test_stored_alarm_and_branch_rows_match_the_design() -> None:
    assert v5.STORED_ALARM_ROWS == 189
    assert dict(v5.BRANCH_ROWS_EMPTY) == {"TRACE": 138, "SUMMARY": 51, "R03": 0}
    assert sum(v5.BRANCH_ROWS_EMPTY.values()) == v5.STORED_ALARM_ROWS


# ---------------------------------------------------------------------------
# profile manifest projection (2차 계획리뷰 필수 1)
# ---------------------------------------------------------------------------


REGISTERED_STAGE = {"runtime": "runtime_clean", "evaluation": "evaluation_mock"}


def _registered(profile: str) -> dict[str, Any]:
    return json.loads(PROFILE_MANIFESTS[profile].read_text(encoding="utf-8"))


def test_cm31_owns_only_r03_and_the_view() -> None:
    """**소유권과 물리 inventory는 다른 것이다**(구현리뷰 4차 필수 2).

    CM-3.1이 만들거나 교체하는 것은 둘뿐이지만, 구 등록 manifest는 과거 형상의 물리
    inventory라 CM-2.6이 보존한 table을 전부 담는다. 계획 §4.1의 "runtime 23 ·
    evaluation 14 유지"는 **그 fixture를 좁히지 말라는 뜻**이지 현재·final 개수가
    23/14라는 뜻이 아니다(구현리뷰 18차 권장 1).
    """

    assert v5.OWNED_BY_CM31 == (v5.R03_TABLE, v5.ALARM_VIEW)
    for task, tables in v5.OWNED_BY_OTHER_TASKS.items():
        assert v5.R03_TABLE not in tables, task
        assert v5.ALARM_VIEW not in tables, task
    assert v5.OWNED_BY_OTHER_TASKS["V5-B-1.1"] == ("document", "document_chunk")
    assert v5.OWNED_BY_OTHER_TASKS["V5-D-2.4"] == ("nl_query_log",)


def test_not_adopted_is_not_the_same_as_hidden_from_inventory() -> None:
    """`document_corpus`는 채택 대상이 아니지만 **구 등록 manifest에는 기록돼 있다.**

    채택하지 않는 것과 과거 형상 기록에서 지우는 것은 다르다. 제거는 `V5-B-1.1`이
    하고 그 뒤 final inventory는 22/13이다. 보존 projection에는 어느 단계에서도
    넣지 않는다(구현리뷰 18차 권장 1).
    """

    assert v5.NOT_ADOPTED_TABLES == ("document_corpus",)
    for tables in v5.OWNED_BY_OTHER_TASKS.values():
        assert "document_corpus" not in tables
    # 구 등록 manifest에는 기록돼 있다 — 지우지 않는다.
    assert "document_corpus" in _registered("evaluation")["tables"]
    # 그러나 보존 projection에는 없다.
    assert "document_corpus" not in v5.PRESERVED_TABLES_BY_PROFILE["evaluation"]


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_the_superseded_registration_fixture_is_not_narrowed(profile: str) -> None:
    """**구 등록 manifest 23 · 14를 그대로 둔다** — 좁히지 않는다.

    이것은 live도 final도 아닌 **과거 형상 fixture**다. final은 22/13이고
    `V5-CM-1.8`이 `V5-B-1.1` 뒤에 발급한다(구현리뷰 18차 권장 1).
    """

    tables = set(_registered(profile)["tables"])
    assert len(tables) == v5.SUPERSEDED_PROFILE_TABLE_COUNTS[profile]
    assert set(v5.BASE_TABLE_NAMES) <= tables
    assert v5.R03_TABLE in tables


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_the_registered_manifest_passes_the_existing_validator(profile: str) -> None:
    """**따로 만들지 않고 `manifest_v3` 검증기를 쓴다**(구현리뷰 4차 필수 1).

    3차·4차에서는 등록본 검증과 최종 epoch 검증을 한 함수에 넣었다. 그런데
    `manifest_v3.DATASET_EPOCH`가 `kosa_0813`이라 최종 epoch을 담은 입력은 예외 없이
    거부되고(CM-1.8 이후에는 그 반대로 history 계보가 active 검증에 걸린다), 등록본은
    최종 행 수를 갖지 않는다. 두 조건을 동시에 만족하는 입력이
    존재하지 않아 **어떤 검사를 지워도 결과가 같은 죽은 gate**가 됐다(변이 Q1~Q12).
    """

    v5.assert_registered_manifest_contract(
        _registered(profile), profile=profile, stage=REGISTERED_STAGE[profile]
    )


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_the_validator_rejects_what_only_it_can_see(profile: str) -> None:
    """history validator만 잡는 입력. 다른 guard는 건드리지 않는다."""

    broken = _registered(profile)
    broken["tables"]["dim_parameter"]["verification_policy"] = "anything"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_registered_manifest_contract(
            broken, profile=profile, stage=REGISTERED_STAGE[profile]
        )
    assert caught.value.reason_code == "MANIFEST_CONTRACT_MISMATCH"


def test_the_validator_is_not_reimplemented() -> None:
    """3차까지 top-level key·secret scan·entry 계약을 직접 구현했고 더 약했다.

    **문자열 검색이 아니라 실제 call node를 본다**(구현리뷰 3차 권장 1). 초판은
    source에 함수명이 있는지만 봐서, 위임 호출을 지워도 docstring에 이름이 남으면
    계속 green이었다 — `V5-CM-1.8`이 history validator로 바꿨을 때 실제로 그 상태였다.
    """

    import ast
    import inspect
    import textwrap

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(v5.assert_registered_manifest_contract))
    )
    called = {
        ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
    }

    assert "manifest_v3.validate_historical_bootstrap_manifest" in called
    # 재구현 흔적이 남아 있으면 안 된다.
    for gone in (
        "MANIFEST_TOP_LEVEL_KEYS",
        "_assert_no_sensitive",
        "_SENSITIVE_VALUE",
        "STAGE_TABLE_NAMES",
        "assert_manifest_contract",
    ):
        assert not hasattr(v5, gone), gone


def test_the_history_validator_is_actually_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**spy로 실제 호출을 확인한다.**

    AST가 call node를 보긴 하지만 도달 불가 분기에 있을 수도 있다.
    """

    import manifest_v3

    seen: list[tuple[str, str]] = []
    real = manifest_v3.validate_historical_bootstrap_manifest

    def spy(manifest, *, profile: str, stage: str) -> None:
        seen.append((profile, stage))
        real(manifest, profile=profile, stage=stage)

    monkeypatch.setattr(manifest_v3, "validate_historical_bootstrap_manifest", spy)
    v5.assert_registered_manifest_contract(
        _registered("runtime"), profile="runtime", stage="runtime_clean"
    )

    assert seen == [("runtime", "runtime_clean")]


def test_registered_contract_rejects_an_unknown_profile() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_registered_manifest_contract(
            _registered("runtime"), profile="corrected", stage="runtime_clean"
        )
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# 최종 epoch 계약 — 등록부와 **분리해서** 본다
# ---------------------------------------------------------------------------


def _final(profile: str) -> dict[str, Any]:
    """최종 계보 manifest fixture.

    등록본을 최종 epoch·archive·행 수로 올린 것이다. `content_hash`는 그대로 둔다 —
    CM-3.1이 pin하는 대상이 아니고, 최종 stage를 등록하는 predecessor Task가 다시
    계산할 값이다. 이 fixture는 **`manifest_v3` 검증기를 통과하지 못한다.** 그것이
    `final_manifest_blockers()`가 세는 공백 자체다.
    """

    manifest = _registered(profile)
    manifest["dataset_epoch"] = v5.FINAL_DATASET_EPOCH
    manifest["source_archive_sha256"] = v5.FINAL_SOURCE_ARCHIVE_SHA256
    manifest["correction_version"] = "final-v1"
    for name, rows in v5.FINAL_BASE_ROWS.items():
        manifest["tables"][name]["row_count"] = rows
    manifest["tables"]["action_history"]["row_count"] = v5.FINAL_ACTION_ROWS[profile]
    manifest["tables"][v5.R03_TABLE]["row_count"] = 0
    return manifest


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_the_final_fixture_satisfies_the_final_contract(profile: str) -> None:
    v5.assert_final_epoch_contract(_final(profile), profile=profile)


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_final_contract_rejects_the_deprecated_epoch(profile: str) -> None:
    manifest = _final(profile)
    manifest["dataset_epoch"] = "kosa_0813"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_epoch_contract(manifest, profile=profile)
    assert caught.value.reason_code == "MANIFEST_EPOCH_NOT_FINAL"


def test_final_contract_rejects_the_deprecated_archive() -> None:
    """epoch만 바꾸고 archive를 두면 통과해선 안 된다."""

    manifest = _final("runtime")
    manifest["source_archive_sha256"] = "8b" + "0" * 62
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_epoch_contract(manifest, profile="runtime")
    assert caught.value.reason_code == "MANIFEST_ARCHIVE_NOT_FINAL"


def test_final_contract_rejects_the_deprecated_correction_revision() -> None:
    manifest = _final("runtime")
    manifest["correction_version"] = "corrected-base-v1"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_epoch_contract(manifest, profile="runtime")
    assert caught.value.reason_code == "MANIFEST_CORRECTION_DEPRECATED"


def test_final_contract_rejects_a_missing_correction_revision() -> None:
    manifest = _final("runtime")
    manifest["correction_version"] = ""
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_epoch_contract(manifest, profile="runtime")
    assert caught.value.reason_code == "MANIFEST_CORRECTION_MISSING"


def test_final_contract_rejects_a_non_mapping_tables() -> None:
    manifest = _final("runtime")
    manifest["tables"] = []
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_epoch_contract(manifest, profile="runtime")
    assert caught.value.reason_code == "MANIFEST_CONTRACT_MISMATCH"


def test_final_contract_rejects_an_unknown_profile() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_epoch_contract(_final("runtime"), profile="corrected")
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"


@pytest.mark.parametrize("table", ["trace_alarm_history", "summary_alarm_history"])
def test_final_contract_rejects_a_single_stale_row_count(table: str) -> None:
    """행 하나만 구값으로 되돌려도 잡는다 — envelope만 바꾼 manifest를 막는 검사다."""

    manifest = _final("runtime")
    manifest["tables"][table]["row_count"] = 1
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_epoch_contract(manifest, profile="runtime")
    assert caught.value.reason_code == "MANIFEST_CONTENT_NOT_FINAL"


def test_final_contract_requires_r03_to_be_empty() -> None:
    """R03 3건은 `V5-A-1.4`가 넣는다. CM-3.1 직후에는 0행이다."""

    manifest = _final("runtime")
    manifest["tables"][v5.R03_TABLE]["row_count"] = 3
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_epoch_contract(manifest, profile="runtime")
    assert caught.value.reason_code == "MANIFEST_CONTENT_NOT_FINAL"


def test_final_table_metadata_rejects_an_unknown_profile() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_table_metadata(_final("runtime")["tables"], profile="corrected")
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Gate 2 — 최종 manifest를 막는 것이 무엇인지 코드로 남긴다
# ---------------------------------------------------------------------------


def test_the_final_fixture_cannot_be_registered_yet() -> None:
    """**두 계약을 동시에 만족하는 manifest는 아직 존재할 수 없다.**

    이것이 Gate 2의 실체다. 최종 fixture는 최종 계약을 통과하지만 등록 검증기에는
    걸린다 — 최종 fixture는 등록 stage가 요구하는 행 수·계보를 갖지 않기 때문이다.
    `V5-CM-1.8`이 epoch을 전환한 뒤에도 stage 등록이 남아 있어 이 Gate는 유지된다.
    """

    manifest = _final("runtime")
    v5.assert_final_epoch_contract(manifest, profile="runtime")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_registered_manifest_contract(
            manifest, profile="runtime", stage="runtime_clean"
        )
    assert caught.value.reason_code == "MANIFEST_CONTRACT_MISMATCH"


def test_gate2_blockers_are_enumerated_in_code() -> None:
    """산문 대신 코드로 남긴다. **공백이 닫히면 이 회귀가 실패한다.**

    그때 계획·코드·fixture를 함께 고친다.
    """

    assert v5.final_manifest_blockers() == v5.GATE2_BLOCKERS


def test_manifest_v3_epoch_now_agrees_with_the_registration_file() -> None:
    """**`V5-CM-1.8`이 전환을 끝냈다 — 역방향 회귀다**(계획 §3.8).

    CM-3.1은 "`dataset-epoch.json`은 이미 최종인데 `manifest_v3` 상수는 폐기 계보"라는
    사실을 계약으로 고정했다. 그 전제를 **삭제하지 않고 뒤집는다.** 삭제하면 전환이
    실제로 끝났다는 사실을 아무도 지키지 않는다.
    """

    import manifest_v3

    registration = json.loads(
        (REPOSITORY_ROOT / "infra" / "bootstrap" / "dataset-epoch.json").read_text(
            encoding="utf-8"
        )
    )
    assert registration["dataset_epoch"] == v5.FINAL_DATASET_EPOCH
    assert registration["archive"]["sha256"] == v5.FINAL_SOURCE_ARCHIVE_SHA256
    assert manifest_v3.DATASET_EPOCH == v5.FINAL_DATASET_EPOCH
    assert manifest_v3.FINAL_ARCHIVE_SHA256 == v5.FINAL_SOURCE_ARCHIVE_SHA256
    assert "MANIFEST_V3_EPOCH_IS_DEPRECATED" not in v5.final_manifest_blockers()

    # 폐기 epoch은 history 검증에만 남는다 — active 기준에서는 사라졌다.
    assert manifest_v3.SUPERSEDED_DATASET_EPOCH == "kosa_0813"


def test_the_final_evaluation_stage_carries_the_real_action_rows() -> None:
    """**역방향 회귀 — 48행 MOCK 특례가 사라졌다.**

    CM-3.1은 "등록된 evaluation stage가 `action_history`를 MOCK·48행으로 못 박아 최종
    12행을 표현할 수 없다"를 blocker로 셌다. `V5-CM-1.8`이 `evaluation_reference`를
    등록하면서 그 공백이 닫혔다.

    구 stage는 active 등록부에서 사라지고 history 계보로만 남는다.
    """

    import manifest_v3

    assert (
        "evaluation",
        "evaluation_mock",
    ) not in manifest_v3.BOOTSTRAP_STAGE_CONTRACTS
    assert ("evaluation", "evaluation_mock") in manifest_v3.HISTORICAL_CONTRACTS

    contract = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[
        ("evaluation", "evaluation_reference")
    ]
    assert contract.action_rows == v5.FINAL_ACTION_ROWS["evaluation"] == 12
    assert contract.action_fixture_type == "REFERENCE"
    assert "EVALUATION_MOCK_PINS_48_ACTION_ROWS" not in v5.final_manifest_blockers()

    # 등록본(history 계보)은 여전히 48행이며 그 사실은 바뀌지 않는다.
    assert _registered("evaluation")["tables"]["action_history"]["row_count"] == 48


def test_the_v5_final_stage_is_now_registered() -> None:
    """**역방향 회귀 — `V5-CM-1.8`이 등록을 끝냈다.**

    CM-3.1은 "V5 final stage가 등록부에 없다"를 계약으로 고정했다. 등록이 predecessor
    보완 Task 소관이었고, 그 Task가 CM-1.8이다. 삭제하지 않고 뒤집는다.
    """

    import manifest_v3

    # `V5-CM-3.3`이 `runtime_guarded`를 더했다. predecessor도 남는다 — CM-3.2 marker가
    # 그 stage를 증명하고 있어서 빼면 검증할 계약을 잃는다.
    for profile, stage in (
        ("runtime", "runtime_clean"),
        ("runtime", "runtime_guarded"),
        ("evaluation", "evaluation_reference"),
    ):
        contract = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[(profile, stage)]
        assert v5.MIGRATION_ID in contract.applied_migrations, profile
        if stage == v5.FINAL_STAGE_BY_PROFILE[profile]:
            # live final stage만 `PROFILE_MIGRATIONS`와 exact다.
            assert contract.applied_migrations == v5.PROFILE_MIGRATIONS[profile]

    # 현재 live final과 registrar 발급 stage는 다르다(구현리뷰 필수 3).
    assert v5.FINAL_STAGE_BY_PROFILE["runtime"] == "runtime_guarded"
    assert v5.REGISTRAR_STAGE_BY_PROFILE["runtime"] == "runtime_clean"

    assert "NO_STAGE_REGISTERS_THE_FINAL_MIGRATION" not in v5.final_manifest_blockers()


def test_expected_final_migrations_are_documented_not_enforced_here() -> None:
    """이 상수는 stage 등록부에 무엇을 넣어야 하는지 적어 둔 기대값이다."""

    import inspect

    assert v5.PROFILE_MIGRATIONS["runtime"] == (
        v5.MIGRATION_ID,
        "002_agent_runtime_clean",
        "003_agent_run_severity_pair",
    )
    assert v5.PROFILE_MIGRATIONS["evaluation"] == (v5.MIGRATION_ID,)
    # artifact builder가 직접 쓰지 않는다. **docstring이 아니라 코드 본문**을 본다 —
    # 설명에 이름이 나오는 것과 값을 쓰는 것은 다르다.
    import ast

    tree = ast.parse(inspect.getsource(v5.build_migration_contract))
    body = ast.unparse(
        ast.Module(
            body=[
                node
                for node in tree.body[0].body
                if not (
                    isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                )
            ],
            type_ignores=[],
        )
    )
    assert "applied_migrations" not in body


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_final_row_contract_rejects_the_old_content(profile: str) -> None:
    """TRACE 126 · SUMMARY 47 · evaluation action 48은 최종이 아니다."""

    tables = _registered(profile)["tables"]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_final_table_metadata(tables, profile=profile)
    assert caught.value.reason_code == "MANIFEST_CONTENT_NOT_FINAL"


def test_final_row_contract_matches_the_transition_result() -> None:
    """`V5-CM-2.6`이 공용 3 DB에 실제로 만든 값이다."""

    assert v5.FINAL_BASE_ROWS["trace_alarm_history"] == 138
    assert v5.FINAL_BASE_ROWS["summary_alarm_history"] == 51
    assert dict(v5.FINAL_ACTION_ROWS) == {"runtime": 0, "evaluation": 12}
    assert (
        sum(
            v5.FINAL_BASE_ROWS[name]
            for name in ("trace_alarm_history", "summary_alarm_history")
        )
        == v5.STORED_ALARM_ROWS
    )


def test_final_epoch_constants_come_from_the_v4_source_manifest() -> None:
    source = json.loads(
        (REPOSITORY_ROOT / "infra" / "bootstrap" / "source-manifest-v4.json").read_text(
            encoding="utf-8"
        )
    )
    assert source["dataset_epoch"] == v5.FINAL_DATASET_EPOCH
    assert source["source_archive_sha256"] == v5.FINAL_SOURCE_ARCHIVE_SHA256


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_registered_manifests_still_carry_the_deprecated_lineage(profile: str) -> None:
    """**데이터 정본은 최종 `project.zip`뿐이다.** 등록본은 아직 구 계보다."""

    registered = _registered(profile)
    assert registered["dataset_epoch"] == "kosa_0813"
    assert registered["source_archive_sha256"].startswith("8bbe0bdd")
    assert registered["correction_version"].startswith("corrected-")
    assert registered["tables"][v5.R03_TABLE]["columns"] == v5.V4_R03_COLUMNS


def test_corrected_manifest_is_never_an_input() -> None:
    assert "corrected_base" not in {path.name for path in PROFILE_MANIFESTS.values()}
    paths = [
        value
        for value in vars(v5).values()
        if isinstance(value, Path) and "corrected" in value.name
    ]
    assert paths == []


# ---------------------------------------------------------------------------
# V4 계보 분리 (계획 §3.2 · §4.1)
# ---------------------------------------------------------------------------


def test_v5_module_does_not_import_the_frozen_v4_module() -> None:
    """V4 `apply_reference_extensions`는 동결이다. final이 참조하면 계보가 얽힌다.

    5차에서 blocker 측정을 위해 이 규칙을 함수 하나의 예외로 좁혔는데, 그 import가
    `db_target`→SQLAlchemy까지 끌고 와 "DB를 열지 않는 순수 계약 모듈"을 깼다
    (구현리뷰 6차 필수 3). **전면 금지로 되돌린다.** 실물 대조는 테스트에서만 한다.

    문자열 검사로는 안 된다 — module docstring이 그 모듈을 **설명**하기 때문이다.
    실제 import 문만 본다.
    """

    import ast
    import inspect

    tree = ast.parse(inspect.getsource(v5))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "apply_reference_extensions" not in imported
    assert not any(name.startswith("apply_agent_runtime") for name in imported)
    assert not any(name.startswith("app.analytics") for name in imported)


def test_building_the_artifact_loads_no_heavy_dependency() -> None:
    """순수 계약 모듈이다.

    artifact를 만들어도 V4·Text2SQL·SQLAlchemy가 딸려오면 안 된다.
    """

    import sys

    for name in ("apply_reference_extensions", "app.analytics.sql_validator"):
        sys.modules.pop(name, None)
    v5.build_migration_contract(
        _registered("runtime"), profile="runtime", stage="runtime_clean"
    )
    assert "apply_reference_extensions" not in sys.modules
    assert "app.analytics.sql_validator" not in sys.modules


def test_the_frozen_v4_module_still_declares_the_old_contract() -> None:
    """동결 확인. V4 상수가 final로 바뀌면 두 계보가 하나가 돼 버린다."""

    import apply_reference_extensions as v4

    columns = [name for name, _type, _null in v4.EXPECTED_TABLE_COLUMNS[v5.R03_TABLE]]
    assert "member_refs" in columns
    assert "member_wafer_refs" not in columns
    assert dict(v4.EXPECTED_CONSTRAINT_COUNTS[v5.R03_TABLE]) == {
        "p": 1,
        "u": 1,
        "f": 2,
        "c": 3,
    }


# ---------------------------------------------------------------------------
# 구현리뷰 1차 필수 1 — statement 단위 mutation 차단
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("DROP TABLE lot_history;", id="base_DROP"),
        pytest.param("TRUNCATE evaluation;", id="base_TRUNCATE"),
        pytest.param("ALTER TABLE fdc_trace ADD COLUMN x int;", id="base_ALTER"),
        pytest.param("DELETE FROM summary_data;", id="base_DELETE"),
        pytest.param("UPDATE metrology SET value = 0;", id="base_UPDATE"),
        pytest.param("INSERT INTO dim_parameter VALUES (1);", id="base_INSERT"),
        pytest.param("COPY lot_history FROM '/tmp/x';", id="base_COPY"),
        pytest.param("GRANT SELECT ON lot_history TO public;", id="GRANT"),
        pytest.param("REVOKE SELECT ON lot_history FROM public;", id="REVOKE"),
        pytest.param("CREATE INDEX ix ON lot_history (lot_id);", id="base_INDEX"),
        pytest.param("CREATE SCHEMA other;", id="SCHEMA"),
        pytest.param("DROP TABLE trace_alarm_history;", id="alarm_DROP"),
    ],
)
def test_base_nine_mutation_is_refused(statement: str) -> None:
    """base 9는 View가 **읽어야** 하므로 금지 토큰에 넣을 수 없다.

    operation을 안 보면 `DROP TABLE lot_history`를 막을 수단이 없다
    (구현리뷰 1차 필수 1). 계획 §3.2의 "base 9 변경 0건"이 이것으로 지켜진다.
    """

    text = v5.CANONICAL_SQL.read_text(encoding="utf-8") + "\n" + statement
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_sql_scope(text)
    assert caught.value.reason_code == "SQL_SCOPE_VIOLATION"


def test_canonical_allowlist_does_not_permit_the_successor_drops() -> None:
    """canonical은 fresh DB용이다. DROP이 들어가면 안 된다."""

    for statement in ("DROP VIEW v_alarm_event;", "DROP TABLE r03_alarm_history;"):
        with pytest.raises(v5.ReferenceV5Error):
            v5.assert_sql_scope(statement, allowed=v5.CANONICAL_STATEMENTS)
        # successor 집합에서는 허용된다.
        v5.assert_sql_scope(statement, allowed=v5.SUCCESSOR_STATEMENTS)


def test_a_do_block_may_only_read() -> None:
    """guard가 mutation을 품으면 read-only 전제가 깨진다."""

    read_only = "DO $$ BEGIN PERFORM count(*) FROM r03_alarm_history; END $$;"
    v5.assert_sql_scope(read_only, allowed=v5.SUCCESSOR_STATEMENTS)

    for verb in ("DELETE FROM r03_alarm_history", "TRUNCATE r03_alarm_history"):
        hidden = f"DO $$ BEGIN {verb}; END $$;"
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.assert_sql_scope(hidden, allowed=v5.SUCCESSOR_STATEMENTS)
        assert caught.value.reason_code == "SQL_SCOPE_VIOLATION", verb


def test_block_comments_cannot_hide_a_mutation() -> None:
    """`--`만 걷어내면 `/* */` 안에 숨긴 statement가 그대로 실행된다."""

    hidden = "/* 설명 */ DROP TABLE lot_history;"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_sql_scope(hidden)
    assert caught.value.reason_code == "SQL_SCOPE_VIOLATION"

    # 주석 안의 언급은 설명이다.
    v5.assert_sql_scope(
        "/* nl_query_log는 만들지 않는다 */ CREATE TABLE r03_alarm_history ();"
    )


def test_an_unclosed_block_comment_is_refused() -> None:
    """어디까지가 주석인지 알 수 없으면 fail-closed다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_sql_scope("/* 안 닫힘 CREATE TABLE r03_alarm_history ();")
    assert caught.value.reason_code == "SQL_SCOPE_VIOLATION"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("SELECT 'unterminated", id="single_quote"),
        pytest.param('CREATE TABLE "unterminated', id="double_quote"),
        pytest.param("SELECT 'escaped '' still open", id="escaped_quote"),
    ],
)
def test_an_unclosed_quote_is_refused(text: str) -> None:
    """닫히지 않은 literal은 typed reason으로 끝나야 한다.

    경계 검사를 빼면 `IndexError`가 그대로 새어나가 reason도 exit code도 없다(변이 M14).
    """

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.split_statements(text)
    assert caught.value.reason_code == "SQL_SCOPE_VIOLATION"


def test_a_quoted_semicolon_is_not_a_statement_boundary() -> None:
    """literal 안의 `;`로 statement를 나누면 guard가 다른 것을 보게 된다."""

    text = "COMMENT ON TABLE r03_alarm_history IS 'a;b';"
    assert len(v5.split_statements(text)) == 1
    v5.assert_sql_scope(text)

    quoted_identifier = 'CREATE TABLE "r03_alarm_history" ();'
    assert len(v5.split_statements(quoted_identifier)) == 1


def test_an_unclosed_dollar_quote_is_refused() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.split_statements("DO $$ BEGIN NULL;")
    assert caught.value.reason_code == "SQL_SCOPE_VIOLATION"


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("VACUUM r03_alarm_history;", id="VACUUM"),
        pytest.param("SET search_path TO other;", id="SET"),
        pytest.param("CALL some_procedure();", id="CALL"),
        pytest.param("EXECUTE stmt;", id="EXECUTE"),
        pytest.param("REFRESH MATERIALIZED VIEW mv;", id="REFRESH"),
    ],
)
def test_an_unclassifiable_statement_is_refused(statement: str) -> None:
    """`(operation, target)`을 못 뽑으면 통과시키면 안 된다.

    allowlist는 아는 operation만 막는다. 모르는 것을 그냥 넘기면 allowlist 밖으로
    나가는 길이 열린다(변이 N27).
    """

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.classify_statement(statement)
    assert caught.value.reason_code == "SQL_SCOPE_VIOLATION"

    with pytest.raises(v5.ReferenceV5Error):
        v5.assert_sql_scope(statement, allowed=v5.SUCCESSOR_STATEMENTS)


def test_statement_splitter_keeps_dollar_quoted_bodies_whole() -> None:
    """`DO $$ ... ; ... $$`를 `;`로 쪼개면 guard가 본문을 못 본다."""

    text = "DO $$ BEGIN PERFORM 1; PERFORM 2; END $$;\nDROP VIEW v_alarm_event;"
    statements = v5.split_statements(text)
    assert len(statements) == 2
    assert v5.classify_statement(statements[0]) == ("DO", "")
    assert v5.classify_statement(statements[1]) == ("DROP VIEW", "v_alarm_event")


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(r"SELECT E'a\';DROP TABLE lot_history;';", id="E_escape"),
        pytest.param("SELECT U&'a';", id="unicode_escape"),
        pytest.param('SELECT U&"a";', id="unicode_identifier"),
        pytest.param("SELECT B'1';", id="bit_string"),
        pytest.param("SELECT X'ff';", id="hex_string"),
    ],
)
def test_unsupported_quote_syntax_is_refused(text: str) -> None:
    """지원 범위를 **명시적으로** 고정한다.

    `E'...'`는 backslash escape라 지금 주사가 closing quote를 잘못 찾는다. 부분 지원
    상태로 묵시적으로 받으면 그 안에 숨긴 statement를 놓친다(구현리뷰 3차 권장 1).
    """

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.split_statements(text)
    assert caught.value.reason_code == "SQL_SYNTAX_UNSUPPORTED"


def test_supported_quote_syntax_still_parses() -> None:
    """지원하는 것까지 막으면 실물 migration이 통과하지 못한다."""

    v5.assert_migration_files_in_scope()
    # 식별자 안의 `e`·`b`·`x`는 prefix가 아니다.
    assert len(v5.split_statements("SELECT value FROM evaluation;")) == 1
    assert len(v5.split_statements("SELECT 'plain';")) == 1


# ---------------------------------------------------------------------------
# task-scoped migration contract artifact (구현리뷰 5차 필수 1)
# ---------------------------------------------------------------------------


def _artifact(profile: str = "runtime") -> dict[str, Any]:
    return v5.build_migration_contract(
        _registered(profile), profile=profile, stage=REGISTERED_STAGE[profile]
    )


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_the_artifact_records_the_final_r03_columns(profile: str) -> None:
    artifact = _artifact(profile)
    assert artifact["r03_columns"] == [n for n, _t, _l, _x in v5.R03_COLUMNS]
    assert artifact["supersedes_r03_columns"] == v5.V4_R03_COLUMNS
    assert artifact["supersedes_manifest"] == {
        "profile": profile,
        "bootstrap_stage": REGISTERED_STAGE[profile],
    }


def test_the_artifact_is_not_a_bootstrap_manifest() -> None:
    """**이것이 5차 필수 1의 핵심이다.**

    4차까지는 결과가 `db_bootstrap` manifest 그대로라 등록 validator를 통과했고, 그래서
    task-scoped라는 경계가 실제로는 없었다.
    """

    import manifest_v3

    artifact = _artifact()
    assert artifact["artifact_type"] not in manifest_v3.ARTIFACT_TYPES
    for leaked in (
        "tables",
        "profile",
        "bootstrap_stage",
        "schema_stage",
        "applied_migrations",
        "applies_to",
        "correction_version",
        "hash_algorithm",
        "value_normalization_version",
    ):
        assert leaked not in artifact, leaked


@pytest.mark.parametrize(
    "artifact_type",
    ["source_files", "corrected_files", "db_bootstrap", "synthetic_evaluation"],
)
def test_the_registered_validator_refuses_the_artifact(artifact_type: str) -> None:
    """등록 검증기가 **어떤 기대 type으로도** 이 artifact를 받지 않는다."""

    import manifest_v3

    with pytest.raises(manifest_v3.VerificationError):
        manifest_v3.validate_manifest_schema(
            _artifact(), expected_artifact_type=artifact_type
        )


def test_the_artifact_never_lands_in_the_bootstrap_registry() -> None:
    """등록 디렉터리에 쓰면 full verifier가 profile manifest로 집어들 수 있다."""

    import manifest_v3

    for path in manifest_v3.BOOTSTRAP_MANIFEST_REGISTRY.values():
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.assert_outside_bootstrap_registry(path)
        assert caught.value.reason_code == "CONTRACT_BOUNDARY_LEAK"

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_outside_bootstrap_registry(
            manifest_v3.MANIFEST_REGISTRY_ROOT / "runtime.v5_final.json"
        )
    assert caught.value.reason_code == "CONTRACT_BOUNDARY_LEAK"

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_outside_bootstrap_registry(manifest_v3.MANIFEST_REGISTRY_ROOT)
    assert caught.value.reason_code == "CONTRACT_BOUNDARY_LEAK"

    # 저장소 밖 경로는 허용된다 — 검사가 항상 거부하는 것이 아님을 보인다.
    v5.assert_outside_bootstrap_registry(
        REPOSITORY_ROOT / "backend" / "migrations" / "v5" / "contract.json"
    )


def test_the_module_never_writes_a_registered_manifest(tmp_path: Path) -> None:
    """artifact는 쓰지만 **등록 디렉터리에는** 쓰지 않는다.

    묶음 2에서 receipt·marker 저장이 생겼다. 그래서 "파일을 전혀 쓰지 않는다"는 더 이상
    성립하지 않는다 — 대신 경계 검사가 등록 경로를 막는지 본다.
    """

    import manifest_v3

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_outside_bootstrap_registry(
            manifest_v3.MANIFEST_REGISTRY_ROOT / "runtime.v5.json"
        )
    assert caught.value.reason_code == "CONTRACT_BOUNDARY_LEAK"
    # 저장소 밖 경로는 허용된다.
    v5.assert_outside_bootstrap_registry(tmp_path / "marker.json")


def test_the_artifact_carries_the_open_blockers() -> None:
    """artifact를 보면 왜 아직 등록할 수 없는지 알 수 있어야 한다."""

    artifact = _artifact()
    assert artifact["registration_blockers"] == list(v5.final_manifest_blockers())
    # **`V5-CM-1.8`이 5종을 전부 닫았다.** 역방향 회귀다 — 삭제하면 blocker가 다시
    # 생겨도 아무도 지키지 않는다(계획 §3.8).
    assert artifact["registration_blockers"] == []


def test_the_v4_type_registry_would_reject_the_v5_table() -> None:
    """`verify_bootstrap_state`가 R03 logical type을 V4 registry에서 가져온다.

    `applied_migrations`로 분기하지 않으므로, V5를 적용한 실제 DB는 manifest가 무엇이든
    COLUMN_TYPE mismatch가 난다. 네 번째 blocker다(구현리뷰 5차 필수 1).
    """

    import apply_reference_extensions as v4_reference

    # 구 V4 registry 자체는 그대로 남는다 — historical stage가 계속 쓴다.
    assert len(v4_reference.EXPECTED_TABLE_COLUMNS[v5.R03_TABLE]) == 11
    assert len(v5.R03_COLUMNS) == 12
    # **`V5-CM-1.8`이 verifier를 V5 12컬럼으로 전환했다.** 역방향 회귀다.
    assert "V4_R03_TYPE_REGISTRY_STILL_ACTIVE" not in v5.final_manifest_blockers()
    assert not hasattr(v5, "V4_R03_TYPE_REGISTRY_COLUMNS")


# --- artifact 계약 판정 단독 음성 입력 ---


def test_artifact_contract_is_checked_directly() -> None:
    """결과 판정이 내부 호출뿐이면 어떤 입력으로도 못 깨뜨린다(변이 P13·P14)."""

    v5.assert_migration_contract(_artifact())


def test_artifact_rejects_an_extra_key() -> None:
    artifact = _artifact()
    artifact["tables"] = {}
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_SCHEMA_MISMATCH"


def test_artifact_rejects_a_missing_key() -> None:
    artifact = _artifact()
    del artifact["owned_objects"]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_SCHEMA_MISMATCH"


def test_artifact_rejects_a_wrong_format_version() -> None:
    artifact = _artifact()
    artifact["format_version"] = 3
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_SCHEMA_MISMATCH"


def test_artifact_rejects_a_registered_artifact_type() -> None:
    """`db_bootstrap`으로 이름을 바꿔 등록 경로에 끼어드는 것을 막는다."""

    artifact = _artifact()
    artifact["artifact_type"] = "db_bootstrap"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_TYPE_MISMATCH"


@pytest.mark.parametrize("key", ["task_id", "migration_id"])
def test_artifact_rejects_a_foreign_identity(key: str) -> None:
    artifact = _artifact()
    artifact[key] = "V5-CM-3.2"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_IDENTITY_MISMATCH"


def test_artifact_rejects_the_deprecated_epoch() -> None:
    artifact = _artifact()
    artifact["dataset_epoch"] = "kosa_0813"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "MANIFEST_EPOCH_NOT_FINAL"


def test_artifact_rejects_the_deprecated_archive() -> None:
    artifact = _artifact()
    artifact["source_archive_sha256"] = "8b" + "0" * 62
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "MANIFEST_ARCHIVE_NOT_FINAL"


def test_artifact_rejects_a_foreign_view_hash() -> None:
    artifact = _artifact()
    artifact["canonical_view_sha256"] = "0" * 64
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "VIEW_IDENTITY_MISMATCH"


def test_artifact_rejects_a_widened_ownership() -> None:
    """소유하지 않은 table을 artifact가 주장하면 안 된다."""

    artifact = _artifact()
    artifact["owned_objects"] = {
        "tables": [v5.R03_TABLE, "nl_query_log"],
        "views": [v5.ALARM_VIEW],
    }
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_OWNERSHIP_MISMATCH"


def test_artifact_rejects_stale_r03_columns() -> None:
    artifact = _artifact()
    artifact["r03_columns"] = list(v5.V4_R03_COLUMNS)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_COLUMNS_MISMATCH"


def test_artifact_rejects_an_unknown_superseded_lineage() -> None:
    artifact = _artifact()
    artifact["supersedes_r03_columns"] = ["alarm_id"]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_LINEAGE_MISMATCH"


def test_artifact_rejects_a_malformed_supersedes_block() -> None:
    artifact = _artifact()
    artifact["supersedes_manifest"] = {"profile": "runtime"}
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_SCHEMA_MISMATCH"


def test_artifact_rejects_an_unknown_superseded_profile() -> None:
    artifact = _artifact()
    artifact["supersedes_manifest"] = {
        "profile": "corrected",
        "bootstrap_stage": "runtime_clean",
    }
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"


def test_artifact_rejects_a_narrowed_inventory() -> None:
    """CM-2.6이 보존한 23/14를 artifact가 줄여 적으면 안 된다(4차 필수 2)."""

    artifact = _artifact()
    artifact["superseded_profile_inventory_counts"] = {
        "runtime": 10,
        "evaluation": 10,
    }
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_INVENTORY_MISMATCH"


def test_artifact_rejects_stale_blockers() -> None:
    """blocker가 닫히면 artifact도 다시 만들어야 한다."""

    artifact = _artifact()
    artifact["registration_blockers"] = ["MANIFEST_V3_EPOCH_IS_DEPRECATED"]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_BLOCKERS_STALE"


def test_boundary_check_is_callable_on_its_own() -> None:
    """`assert_not_a_bootstrap_manifest()`가 죽은 방어가 되지 않게 공개로 둔다."""

    v5.assert_not_a_bootstrap_manifest(_artifact())
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_not_a_bootstrap_manifest({"artifact_type": "db_bootstrap"})
    assert caught.value.reason_code == "CONTRACT_TYPE_MISMATCH"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_not_a_bootstrap_manifest(
            {"artifact_type": v5.MIGRATION_CONTRACT_TYPE, "tables": {}}
        )
    assert caught.value.reason_code == "CONTRACT_BOUNDARY_LEAK"


# --- builder 입력 검증 ---


def test_builder_rejects_what_only_the_registered_contract_sees() -> None:
    """builder가 입력 계약을 **실제로 부르는지** 본다(변이 Q9)."""

    broken = _registered("runtime")
    broken["tables"]["dim_parameter"]["verification_policy"] = "anything"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.build_migration_contract(broken, profile="runtime", stage="runtime_clean")
    assert caught.value.reason_code == "MANIFEST_CONTRACT_MISMATCH"


# `V5-CM-1.8`이 history manifest의 canonical payload SHA-256을 정본으로 고정하면서,
# 아래 세 경우는 **builder 안이 아니라 경계에서** 막힌다. 변조된 history manifest를
# `supersedes` 입력으로 쓸 수 있으면 계보 재현성(NFR-06)이 무너지기 때문이다
# (구현리뷰 2차 필수 2). 그래서 "builder가 무엇을 거부하는가"에서
# "변조본이 애초에 builder에 닿지 못한다"로 계약을 옮긴다.


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("R03 entry 삭제", lambda m: m["tables"].pop(v5.R03_TABLE)),
        (
            "알 수 없는 R03 계보",
            lambda m: m["tables"][v5.R03_TABLE].__setitem__(
                "columns", ["alarm_id", "something_else"]
            ),
        ),
        (
            "최종 컬럼 목록 주입",
            lambda m: m["tables"][v5.R03_TABLE].__setitem__(
                "columns", [name for name, _t, _l, _n in v5.R03_COLUMNS]
            ),
        ),
    ],
)
def test_a_tampered_history_manifest_never_reaches_the_builder(
    label: str, mutate
) -> None:
    """**경계에서 막는다.**

    `assert_registered_manifest_contract()`가 payload identity를 보므로 변조본은
    builder 본문에 도달하지 못한다. 세 경우 모두 같은 이유로 거부된다.
    """

    tampered = _registered("runtime")
    mutate(tampered)

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_registered_manifest_contract(
            tampered, profile="runtime", stage="runtime_clean"
        )
    assert caught.value.reason_code == "MANIFEST_CONTRACT_MISMATCH"

    with pytest.raises(v5.ReferenceV5Error):
        v5.build_migration_contract(tampered, profile="runtime", stage="runtime_clean")


def test_the_builder_records_the_v4_lineage_from_the_pinned_manifest() -> None:
    """정본 manifest 하나만 통과하며, 그 R03 계보는 V4다."""

    artifact = v5.build_migration_contract(
        _registered("runtime"), profile="runtime", stage="runtime_clean"
    )

    assert artifact["supersedes_r03_columns"] == v5.V4_R03_COLUMNS


def test_builder_revalidates_its_own_output(monkeypatch: Any) -> None:
    """결과 판정을 **내부에서** 부른다(1차 필수 4 · 3차 필수 1 · 변이 Q12)."""

    seen: list[Any] = []
    original = v5.assert_migration_contract

    def spy(artifact: Any) -> None:
        seen.append(artifact)
        original(artifact)

    monkeypatch.setattr(v5, "assert_migration_contract", spy)
    v5.build_migration_contract(
        _registered("runtime"), profile="runtime", stage="runtime_clean"
    )
    assert len(seen) == 1


def test_builder_does_not_mutate_the_registered_manifest() -> None:
    """읽기만 한다 — 등록본을 건드리면 안 된다."""

    registered = _registered("runtime")
    before = json.dumps(registered, sort_keys=True)
    v5.build_migration_contract(registered, profile="runtime", stage="runtime_clean")
    assert json.dumps(registered, sort_keys=True) == before


def test_every_registered_manifest_lives_under_the_registry_root() -> None:
    """`assert_outside_bootstrap_registry()`가 root 하나만 보는 근거다(변이 R36)."""

    import manifest_v3

    root = manifest_v3.MANIFEST_REGISTRY_ROOT.resolve()
    for path in manifest_v3.BOOTSTRAP_MANIFEST_REGISTRY.values():
        assert root in path.resolve().parents


def test_artifact_rejects_an_unregistered_foreign_type() -> None:
    """`db_bootstrap`이 아닌 **낯선** type도 거부한다(변이 R21).

    경계 판정은 등록 type만 보므로, 이 입력은 type 검사 하나만 걸린다.
    """

    import manifest_v3

    artifact = _artifact()
    artifact["artifact_type"] = "v5_migration_contract_v2"
    assert artifact["artifact_type"] not in manifest_v3.ARTIFACT_TYPES
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_TYPE_MISMATCH"


def test_artifact_contract_runs_the_boundary_check(monkeypatch: Any) -> None:
    """경계 판정을 **내부에서** 부른다(변이 R33).

    exact key·type 검사가 이미 같은 입력을 막으므로 음성 입력으로는 이 호출을 관찰할 수
    없다. 호출 자체를 본다.
    """

    # builder도 같은 경로를 타므로 **spy를 걸기 전에** artifact를 만든다.
    artifact = _artifact()
    seen: list[Any] = []
    original = v5.assert_not_a_bootstrap_manifest

    def spy(candidate: Any) -> None:
        seen.append(candidate)
        original(candidate)

    monkeypatch.setattr(v5, "assert_not_a_bootstrap_manifest", spy)
    v5.assert_migration_contract(artifact)
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# migration bundle provenance (구현리뷰 6차 필수 2)
# ---------------------------------------------------------------------------


def test_artifact_pins_the_migration_bundle() -> None:
    """같은 `migration_id`로 DDL이 바뀐 SQL이 와도 여기서 갈린다."""

    artifact = _artifact()
    assert artifact["migration_bundle_sha256"] == v5.migration_bundle_sha256()
    assert len(artifact["migration_bundle_sha256"]) == 64
    assert artifact["migration_bundle_sha256"] != artifact["canonical_view_sha256"]


@pytest.mark.parametrize("which", ["canonical", "successor"])
def test_a_changed_sql_makes_the_artifact_stale(
    which: str, tmp_path: Path, monkeypatch: Any
) -> None:
    """canonical·successor **어느 쪽** 변경이든 stale로 잡는다."""

    artifact = _artifact()
    source = v5.CANONICAL_SQL if which == "canonical" else v5.SUCCESSOR_SQL
    changed = tmp_path / source.name
    changed.write_text(
        source.read_text(encoding="utf-8").replace(
            "recipe_step_no    smallint    NOT NULL",
            "recipe_step_no    integer     NOT NULL",
            1,
        ),
        encoding="utf-8",
    )
    assert changed.read_text(encoding="utf-8") != source.read_text(encoding="utf-8")
    monkeypatch.setattr(
        v5, "CANONICAL_SQL" if which == "canonical" else "SUCCESSOR_SQL", changed
    )
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "MIGRATION_BUNDLE_STALE"


def test_the_view_hash_does_not_stand_in_for_the_bundle_hash() -> None:
    """View identity와 bundle provenance는 서로를 대신하지 않는다."""

    artifact = _artifact()
    artifact["migration_bundle_sha256"] = artifact["canonical_view_sha256"]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "MIGRATION_BUNDLE_STALE"


# ---------------------------------------------------------------------------
# superseded profile/stage 신원과 민감 값 (구현리뷰 6차 필수 1)
# ---------------------------------------------------------------------------


def test_the_pair_table_matches_the_preserved_history_lineage() -> None:
    """자유 문자열을 없앤 근거 — 이 표가 **보존된 구 계보**와 같아야 한다.

    `V5-CM-1.8` 전에는 active `BOOTSTRAP_STAGE_CONTRACTS`와 대조했다. 그런데 이 표가
    가리키는 것은 "무엇을 대체했는가"이므로 active registry가 최종 stage로 재기준화되면
    그 대조는 성립하지 않는다. 계획 §3.8이 "final active registry에서
    `evaluation_mock`이 제거되더라도 과거 lineage는 바뀌지 않는다"고 못박은
    지점이라, 대조 상대를 `HISTORICAL_CONTRACTS`로 옮긴다.
    """

    import manifest_v3

    assert dict(v5.SUPERSEDED_STAGE_BY_PROFILE) == dict(manifest_v3.HISTORICAL_STAGES)
    for profile, stage in v5.SUPERSEDED_STAGE_BY_PROFILE.items():
        assert (profile, stage) in manifest_v3.HISTORICAL_CONTRACTS, (profile, stage)
    assert set(v5.SUPERSEDED_STAGE_BY_PROFILE) == set(v5.PROFILE_MIGRATIONS)

    # 신규 final stage는 대체 대상이 아니다. `runtime_clean`은 계획 §2.2대로 **같은
    # 이름으로 원자 교체**되므로 이름이 겹치는 것이 정상이다 — 구분은 stage 이름이
    # 아니라 epoch·migration이 한다.
    assert "evaluation_reference" not in set(v5.SUPERSEDED_STAGE_BY_PROFILE.values())
    history = manifest_v3.HISTORICAL_CONTRACTS[("runtime", "runtime_clean")]
    active = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[("runtime", "runtime_clean")]
    assert history.applied_migrations != active.applied_migrations


def test_cross_profile_lineage_is_refused() -> None:
    """`runtime`이 `evaluation_mock`을 대체한다고 주장할 수 없다."""

    artifact = _artifact()
    artifact["supersedes_manifest"] = {
        "profile": "runtime",
        "bootstrap_stage": "evaluation_mock",
    }
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_CROSS_PROFILE_LINEAGE"


def test_an_unregistered_stage_is_refused() -> None:
    artifact = _artifact()
    artifact["supersedes_manifest"] = {
        "profile": "runtime",
        "bootstrap_stage": "does_not_exist",
    }
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_STAGE_NOT_REGISTERED"


def test_an_unknown_superseded_profile_is_refused() -> None:
    artifact = _artifact()
    artifact["supersedes_manifest"] = {
        "profile": "corrected",
        "bootstrap_stage": "runtime_clean",
    }
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"


@pytest.mark.parametrize(
    "planted",
    [
        "postgresql://user:pw@host/db",
        "C:\\Users\\name\\secret.json",
        "\\\\server\\share\\secret.json",
        "file:///etc/passwd",
        "/Users/name/secret.json",
    ],
)
def test_a_sensitive_value_is_refused_before_the_pair_check(planted: str) -> None:
    """민감 값은 **다른 reason**으로 드러나야 한다(구현리뷰 6차 필수 1).

    exact 대조가 어차피 거부하더라도, DSN·절대경로가 들어온 사실이 stage 오류로
    뭉뚱그려지면 안 된다.
    """

    artifact = _artifact()
    artifact["supersedes_manifest"] = {
        "profile": "runtime",
        "bootstrap_stage": planted,
    }
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_migration_contract(artifact)
    assert caught.value.reason_code == "CONTRACT_SENSITIVE_VALUE"


def test_the_sensitive_scan_is_the_existing_one() -> None:
    """재구현하지 않는다(4차 필수 1). 공개 검사기를 그대로 부른다."""

    import inspect

    source = inspect.getsource(v5.assert_no_sensitive_values)
    assert "manifest_v3.scan_for_sensitive_values(" in source
    v5.assert_no_sensitive_values({"ok": "plain-text"})


def test_the_sensitive_scan_is_callable_on_its_own() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_no_sensitive_values({"where": "postgresql://user:pw@host/db"})
    assert caught.value.reason_code == "CONTRACT_SENSITIVE_VALUE"


def test_the_pair_check_is_callable_on_its_own() -> None:
    """죽은 방어가 되지 않게 공개로 둔다."""

    v5.assert_superseded_pair(
        {"profile": "evaluation", "bootstrap_stage": "evaluation_mock"}
    )
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_superseded_pair(
            {"profile": "evaluation", "bootstrap_stage": "runtime_clean"}
        )
    assert caught.value.reason_code == "CONTRACT_CROSS_PROFILE_LINEAGE"


# ---------------------------------------------------------------------------
# 다섯 번째 blocker — Text2SQL column allowlist (계획 §7.5-7 · §9.1)
# ---------------------------------------------------------------------------


def test_the_text2sql_allowlist_is_still_the_v4_column_set() -> None:
    """등록 manifest를 직접 읽는 allowlist라 CM-3.1이 고칠 수 없다.

    실물 대조는 **테스트에서만** 한다 — 모듈이 Text2SQL validator를 import하면
    sqlglot까지 딸려온다(계획 §7.5-7).
    """

    manifest = json.loads(
        (MANIFEST_DIR / v5.TEXT2SQL_ALLOWLIST_MANIFEST).read_text(encoding="utf-8")
    )
    columns = manifest["tables"][v5.R03_TABLE]["columns"]

    # **`V5-CM-1.8`이 active manifest를 V5 12컬럼으로 교체했다.** Text2SQL validator는
    # 이 파일을 직접 읽으므로 allowlist도 함께 전환됐다 — 별도 코드 변경이 없다.
    assert columns == [name for name, _t, _l, _x in v5.R03_COLUMNS]
    assert "member_wafer_refs" in columns
    assert "member_alarm_refs" in columns
    assert "member_refs" not in columns
    assert "TEXT2SQL_COLUMN_ALLOWLIST_IS_V4" not in v5.final_manifest_blockers()

    # 구 계보는 history에 그대로 남는다.
    history = json.loads(
        (HISTORY_MANIFEST_DIR / v5.TEXT2SQL_ALLOWLIST_MANIFEST).read_text(
            encoding="utf-8"
        )
    )
    assert history["tables"][v5.R03_TABLE]["columns"] == v5.V4_R03_COLUMNS


def test_the_allowlist_manifest_is_the_one_text2sql_reads() -> None:
    """상수가 가리키는 파일이 실제 validator가 읽는 파일과 같아야 한다."""

    from app.analytics import sql_validator

    assert sql_validator.RUNTIME_MANIFEST_PATH.name == v5.TEXT2SQL_ALLOWLIST_MANIFEST
    assert sql_validator.RUNTIME_MANIFEST_PATH.parent == MANIFEST_DIR


def test_cm31_does_not_touch_the_text2sql_allowlist() -> None:
    """CM-3.1은 blocker로 고정만 한다. 수정은 `V5-CM-1.8` 소관이다."""

    source = (
        REPOSITORY_ROOT / "backend" / "scripts" / "apply_reference_extensions_v5.py"
    ).read_text(encoding="utf-8")
    assert "ALLOWED_OBJECTS" not in source
    assert "sql_validator" not in source


def test_no_static_blocker_remains() -> None:
    """**`V5-CM-1.8`이 정적 blocker를 전부 제거했다.**

    Text2SQL allowlist는 active Runtime manifest가 V5 12컬럼으로 교체되면서 닫혔다.
    실측하려면 sqlglot이 끌려와 순수 계약 모듈이 깨지므로 상수는 비우고, 실물 대조는
    테스트가 한다(계획 §7.5-7).
    """

    assert v5.STATIC_BLOCKERS == ()
    assert v5.GATE2_BLOCKERS == ()
    assert v5.final_manifest_blockers() == ()


# ---------------------------------------------------------------------------
# V5 final SQL의 R03 member 컬럼 계약 (계획 §8 묶음1-6 · §9.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [v5.CANONICAL_SQL, v5.SUCCESSOR_SQL])
def test_final_sql_declares_the_member_columns_and_drops_member_refs(
    path: Path,
) -> None:
    """구 `member_refs` 1컬럼은 final 계보에 남지 않는다.

    이 계약은 **V5 migration SQL**의 것이다. Text2SQL `app.analytics.sql_validator`의
    manifest-backed allowlist와 무관하다 — 그쪽은 `V5-CM-1.8`이 재기준화한다.
    """

    boundary = re.compile(r"(?<![a-z_])member_refs(?![a-z_])")
    created = [
        statement
        for statement in v5.split_statements(path.read_text(encoding="utf-8"))
        if v5.classify_statement(statement)[0].startswith("CREATE")
    ]
    assert created, path.name
    for statement in created:
        # `member_wafer_refs`가 `member_refs`를 부분 문자열로 포함하므로 경계를 본다.
        assert boundary.search(statement) is None, statement[:60]

    # 두 member 컬럼은 **R03 table**이 갖는다. View 17컬럼에는 들어가지 않는다.
    tables = [
        statement
        for statement in created
        if v5.classify_statement(statement) == ("CREATE TABLE", v5.R03_TABLE)
    ]
    assert len(tables) == 1, path.name
    assert "member_wafer_refs" in tables[0]
    assert "member_alarm_refs" in tables[0]
    views = [
        statement
        for statement in created
        if v5.classify_statement(statement) == ("CREATE VIEW", v5.ALARM_VIEW)
    ]
    assert len(views) == 1, path.name
    assert "member_wafer_refs" not in views[0]
    assert "member_alarm_refs" not in views[0]

    # successor의 DO guard는 **읽기**로 구 컬럼을 확인한다. 그건 만드는 것이 아니다.
    guards = [
        statement
        for statement in v5.split_statements(path.read_text(encoding="utf-8"))
        if v5.classify_statement(statement)[0] == "DO"
    ]
    if path == v5.SUCCESSOR_SQL:
        assert guards and any(boundary.search(g) for g in guards)
    else:
        assert not any(boundary.search(g) for g in guards)


def test_the_final_r03_contract_has_both_member_columns() -> None:
    names = [name for name, _t, _l, _n in v5.R03_COLUMNS]
    assert "member_wafer_refs" in names
    assert "member_alarm_refs" in names
    assert "member_refs" not in names
    assert "member_refs" in v5.V4_R03_COLUMNS


# ---------------------------------------------------------------------------
# canonical comment 계약 (계획 §6.5 · 7차 계획리뷰 필수 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [v5.CANONICAL_SQL, v5.SUCCESSOR_SQL])
def test_both_migrations_declare_the_same_canonical_comment(path: Path) -> None:
    """상수와 DDL이 따로 놀면 comment 계약이 의미를 잃는다."""

    assert v5.declared_table_comment(path.read_text(encoding="utf-8")) == v5.R03_COMMENT


def test_no_migration_declares_a_view_comment() -> None:
    """`VIEW_COMMENT is None`의 근거 — canonical DDL이 선언하지 않는다."""

    for path in (v5.CANONICAL_SQL, v5.SUCCESSOR_SQL):
        assert "COMMENT ON VIEW" not in v5.executable_sql(
            path.read_text(encoding="utf-8")
        )
    assert v5.VIEW_COMMENT is None


def test_a_missing_comment_declaration_is_visible() -> None:
    assert v5.declared_table_comment("CREATE TABLE r03_alarm_history (x int);") is None


def test_the_comment_extractor_unescapes_doubled_quotes() -> None:
    text = "COMMENT ON TABLE r03_alarm_history IS 'a''b';"
    assert v5.declared_table_comment(text) == "a'b"


def test_the_comment_extractor_ignores_commented_out_ddl() -> None:
    """실행되지 않는 줄은 선언이 아니다."""

    text = "-- COMMENT ON TABLE r03_alarm_history IS 'ghost';\nSELECT 1;"
    assert v5.declared_table_comment(text) is None


def test_canonical_comments_pass_on_the_final_shape() -> None:
    v5.assert_canonical_comments(r03_comment=v5.R03_COMMENT, view_comment=None)


def test_the_v4_pre_state_comment_is_not_the_contract() -> None:
    """실측한 V4 pre-state는 `None`이었다. 복원하면 이 계약이 깨진다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_canonical_comments(r03_comment=None, view_comment=None)
    assert caught.value.reason_code == "COMMENT_NOT_CANONICAL"


def test_a_view_comment_is_refused() -> None:
    """View는 canonical `NULL`이다. 누가 붙이면 drift다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_canonical_comments(
            r03_comment=v5.R03_COMMENT, view_comment="누가 붙였다"
        )
    assert caught.value.reason_code == "COMMENT_NOT_CANONICAL"


# ---------------------------------------------------------------------------
# owner·ACL security signature (계획 §6.5·§7.3 · 6차 계획리뷰 필수 2)
# ---------------------------------------------------------------------------


_ACL = "{kosa=arwdDxt/kosa,kosa_readonly=r/kosa}"


def _sec(acl: str | None = _ACL, owner: str = "kosa") -> dict[str, dict[str, Any]]:
    return {name: {"owner": owner, "acl": acl} for name in v5.OWNED_BY_CM31}


def test_acl_parsing_matches_the_measured_shape() -> None:
    """`relacl::text` 실측 형태를 그대로 받는다."""

    assert v5.parse_acl(_ACL) == (
        ("kosa", "arwdDxt", "kosa"),
        ("kosa_readonly", "r", "kosa"),
    )
    assert v5.parse_acl(None) == ()
    assert v5.parse_acl("{}") == ()


def test_acl_parsing_is_order_independent() -> None:
    """PostgreSQL은 `aclitem[]` 순서를 보장하지 않는다.

    부여 순서만으로 signature가 갈리면 안 된다.
    """

    forward = v5.security_signature_sha256(_sec(), mode="successor")
    reversed_acl = "{kosa_readonly=r/kosa,kosa=arwdDxt/kosa}"
    assert v5.security_signature_sha256(_sec(reversed_acl), mode="successor") == forward


def test_a_quoted_acl_entry_is_parsed() -> None:
    assert v5.parse_acl('{"kosa=arwdDxt/kosa"}') == (("kosa", "arwdDxt", "kosa"),)


@pytest.mark.parametrize("bad", ["kosa=r/kosa", "{kosa-r-kosa}", "{", "}"])
def test_an_unparsable_acl_is_refused(bad: str) -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.parse_acl(bad)
    assert caught.value.reason_code == "SECURITY_ACL_UNPARSABLE"


def test_a_public_grant_is_refused() -> None:
    """`=r/kosa`는 grantee가 비어 있는 PUBLIC 항목이다(계획 §6.5)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_no_public_grant("{kosa=arwdDxt/kosa,=r/kosa}")
    assert caught.value.reason_code == "SECURITY_PUBLIC_GRANT"
    v5.assert_no_public_grant(_ACL)


def test_a_null_acl_stops_the_successor_path() -> None:
    """CM-2.6이 `v_alarm_event`에서 실제로 만난 상태다. 자동 보정하지 않는다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_relation_security({"owner": "kosa", "acl": None}, mode="successor")
    assert caught.value.reason_code == "SECURITY_ACL_MISSING"


def test_a_null_acl_is_normal_on_the_base_only_path() -> None:
    """격리 rehearsal·fresh bootstrap에는 복원할 pre-state가 없다(계획 §7.2)."""

    v5.assert_relation_security({"owner": "postgres", "acl": None}, mode="base_only")


def test_a_missing_owner_is_refused() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_relation_security({"owner": "", "acl": _ACL}, mode="successor")
    assert caught.value.reason_code == "SECURITY_OWNER_MISSING"


def test_an_unknown_security_mode_is_refused() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_relation_security({"owner": "kosa", "acl": _ACL}, mode="whatever")
    assert caught.value.reason_code == "SECURITY_MODE_NOT_ALLOWED"


def test_the_signature_covers_exactly_the_owned_relations() -> None:
    """CM-3.1이 소유하지 않는 relation을 signature에 섞지 않는다."""

    rows = _sec()
    rows["nl_query_log"] = {"owner": "kosa", "acl": _ACL}
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.security_signature_sha256(rows, mode="successor")
    assert caught.value.reason_code == "SECURITY_RELATION_SET_MISMATCH"

    partial = {v5.R03_TABLE: {"owner": "kosa", "acl": _ACL}}
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.security_signature_sha256(partial, mode="successor")
    assert caught.value.reason_code == "SECURITY_RELATION_SET_MISMATCH"


def test_the_path_does_not_change_the_security_identity() -> None:
    """`mode`는 전제 조건이지 보안 상태가 아니다(변이 T19).

    payload에 넣으면 같은 owner·ACL을 가진 DB가 경로만 달라 다른 signature를 갖고,
    no-op 때 어느 mode로 다시 계산할지가 정해지지 않는다.
    """

    rows = _sec()
    assert v5.security_signature_sha256(rows, mode="base_only") == (
        v5.security_signature_sha256(rows, mode="successor")
    )


def test_each_path_has_its_own_precondition() -> None:
    """base-only는 `relacl IS NULL`을 허용하고 successor는 거부한다(계획 §7.2)."""

    empty = _sec(None, owner="postgres")
    base = v5.security_signature_sha256(empty, mode="base_only")
    assert len(base) == 64
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.security_signature_sha256(empty, mode="successor")
    assert caught.value.reason_code == "SECURITY_ACL_MISSING"


def test_a_public_grant_stops_the_signature_on_both_paths() -> None:
    """직접 호출뿐 아니라 **relation 검사를 거쳐서도** 걸려야 한다(변이 T16)."""

    leaked = _sec("{kosa=arwdDxt/kosa,=r/kosa}")
    for mode in ("successor", "base_only"):
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.assert_relation_security(leaked[v5.R03_TABLE], mode=mode)
        assert caught.value.reason_code == "SECURITY_PUBLIC_GRANT"
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.security_signature_sha256(leaked, mode=mode)
        assert caught.value.reason_code == "SECURITY_PUBLIC_GRANT"


def test_an_owner_change_moves_the_signature() -> None:
    assert v5.security_signature_sha256(
        _sec(owner="someone_else"), mode="successor"
    ) != v5.security_signature_sha256(_sec(), mode="successor")


def test_a_dropped_grant_moves_the_signature() -> None:
    """`DROP TABLE`이 권한을 버린 것을 잡아야 한다 — 이 계약의 존재 이유다."""

    without = v5.security_signature_sha256(
        _sec("{kosa=arwdDxt/kosa}"), mode="successor"
    )
    assert without != v5.security_signature_sha256(_sec(), mode="successor")


def test_the_comment_does_not_move_the_security_signature() -> None:
    """comment는 schema 계약이다. 보안 signature에 섞으면 오판을 만든다."""

    rows = _sec()
    baseline = v5.security_signature_sha256(rows, mode="successor")
    for name in rows:
        rows[name]["comment"] = "무엇이든"
    assert v5.security_signature_sha256(rows, mode="successor") == baseline


def test_three_targets_must_agree() -> None:
    same = {name: _sec() for name in ("kosa_agent_e2e", "kosa_agent", "kosa_text2sql")}
    v5.assert_security_targets_agree(same)


def test_one_drifting_target_stops_the_apply() -> None:
    """CM-2.6에서 `kosa_agent_e2e`만 ACL이 없었다. 공통값으로 자동 정규화하지 않는다."""

    drifted = {
        name: _sec() for name in ("kosa_agent_e2e", "kosa_agent", "kosa_text2sql")
    }
    drifted["kosa_agent_e2e"] = _sec("{kosa=arwdDxt/kosa}")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_security_targets_agree(drifted)
    assert caught.value.reason_code == "SECURITY_TARGET_DRIFT"


def test_an_empty_target_set_is_refused() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_security_targets_agree({})
    assert caught.value.reason_code == "SECURITY_TARGET_SET_EMPTY"


def test_target_agreement_still_refuses_a_null_acl() -> None:
    """drift 검사가 successor 계약을 우회하지 않는다."""

    rows = {name: _sec(None) for name in v5.PUBLIC_TARGETS}
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_security_targets_agree(rows)
    assert caught.value.reason_code == "SECURITY_ACL_MISSING"


def test_the_security_query_reads_the_same_columns_as_cm26() -> None:
    """두 Task가 같은 값을 다르게 읽으면 대조가 무의미하다."""

    for fragment in (
        "pg_get_userbyid(c.relowner)",
        "c.relacl::text",
        "obj_description",
    ):
        assert fragment in v5.RELATION_SECURITY_SQL
    assert "nspname = 'public'" in v5.RELATION_SECURITY_SQL


# ---------------------------------------------------------------------------
# comment가 schema signature에 실린다 (구현리뷰 7차 필수 1)
# ---------------------------------------------------------------------------


def test_the_comment_moves_the_schema_signature() -> None:
    """comment만 바뀌어도 drift로 드러나야 한다.

    security signature는 owner·ACL만 보므로 여기 없으면 어느 signature에도 안 남는다.
    """

    baseline = _signature()
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _signature(r03_comment="다른 설명")
    assert caught.value.reason_code == "COMMENT_NOT_CANONICAL"
    assert baseline == _signature()


def test_an_erased_r03_comment_cannot_become_a_signature() -> None:
    """`DROP TABLE` 뒤 comment가 복원되지 않은 상태를 그대로 marker로 삼을 수 없다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        _signature(r03_comment=None)
    assert caught.value.reason_code == "COMMENT_NOT_CANONICAL"


def test_a_stray_view_comment_cannot_become_a_signature() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _signature(view_comment="누가 붙였다")
    assert caught.value.reason_code == "COMMENT_NOT_CANONICAL"


def test_the_schema_signature_carries_the_comments() -> None:
    """호출만 하고 payload에 안 넣으면 값이 identity에 반영되지 않는다."""

    import inspect

    source = inspect.getsource(v5.schema_signature_sha256)
    assert '"r03_comment": r03_comment' in source
    assert '"view_comment": view_comment' in source


# ---------------------------------------------------------------------------
# 공용 target set exact (구현리뷰 7차 필수 2)
# ---------------------------------------------------------------------------


def test_the_public_target_set_is_the_cm26_order() -> None:
    assert v5.PUBLIC_TARGETS == ("kosa_agent_e2e", "kosa_agent", "kosa_text2sql")


def test_all_three_targets_pass() -> None:
    v5.assert_security_targets_agree({name: _sec() for name in v5.PUBLIC_TARGETS})


@pytest.mark.parametrize(
    "targets",
    [
        pytest.param(("kosa_agent",), id="단일"),
        pytest.param(("kosa_agent_e2e", "kosa_agent"), id="하나_누락"),
        pytest.param(
            ("kosa_agent_e2e", "kosa_agent", "kosa_text2sql", "kosa_extra"),
            id="모르는_target_추가",
        ),
    ],
)
def test_an_incomplete_target_set_is_refused(targets: tuple[str, ...]) -> None:
    """일부만 통과하면 나머지 DB의 ACL 미확인을 놓친다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_security_targets_agree({name: _sec() for name in targets})
    assert caught.value.reason_code == "SECURITY_TARGET_SET_MISMATCH"


def test_the_target_order_does_not_change_the_verdict() -> None:
    """mapping 순서는 identity가 아니다."""

    forward = {name: _sec() for name in v5.PUBLIC_TARGETS}
    backward = {name: _sec() for name in reversed(v5.PUBLIC_TARGETS)}
    v5.assert_security_targets_agree(forward)
    v5.assert_security_targets_agree(backward)


# ---------------------------------------------------------------------------
# migration route의 exact statement sequence (구현리뷰 7차 필수 3)
# ---------------------------------------------------------------------------


def test_the_real_migrations_match_their_route_sequence() -> None:
    v5.assert_statement_sequence(
        v5.CANONICAL_SQL.read_text(encoding="utf-8"), route="canonical"
    )
    v5.assert_statement_sequence(
        v5.SUCCESSOR_SQL.read_text(encoding="utf-8"), route="successor"
    )


def test_the_route_sequences_are_the_documented_ones() -> None:
    assert v5.CANONICAL_SEQUENCE == (
        ("CREATE TABLE", v5.R03_TABLE),
        ("COMMENT ON TABLE", v5.R03_TABLE),
        ("CREATE VIEW", v5.ALARM_VIEW),
    )
    assert v5.SUCCESSOR_SEQUENCE[:3] == (
        ("DO", ""),
        ("DROP VIEW", v5.ALARM_VIEW),
        ("DROP TABLE", v5.R03_TABLE),
    )
    assert v5.SUCCESSOR_SEQUENCE[3:] == v5.CANONICAL_SEQUENCE


def test_an_unknown_route_is_refused() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_statement_sequence("SELECT 1;", route="whatever")
    assert caught.value.reason_code == "MIGRATION_ROUTE_NOT_ALLOWED"


def test_an_empty_migration_is_refused() -> None:
    """집합 검사만으로는 빈 파일이 통과한다."""

    v5.assert_sql_scope("", allowed=v5.CANONICAL_STATEMENTS)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_statement_sequence("", route="canonical")
    assert caught.value.reason_code == "SQL_SEQUENCE_MISMATCH"


def test_a_partial_migration_is_refused() -> None:
    """`CREATE TABLE`만 있어도 허용 집합 안이다. 순서 계약이 잡는다."""

    only_table = v5.split_statements(v5.CANONICAL_SQL.read_text(encoding="utf-8"))[0]
    v5.assert_sql_scope(only_table + ";", allowed=v5.CANONICAL_STATEMENTS)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_statement_sequence(only_table + ";", route="canonical")
    assert caught.value.reason_code == "SQL_SEQUENCE_MISMATCH"


def test_a_second_comment_declaration_is_refused() -> None:
    """뒤에 붙은 comment가 실제 최종값이다. 첫 값만 보면 오판한다."""

    text = (
        v5.CANONICAL_SQL.read_text(encoding="utf-8")
        + "\nCOMMENT ON TABLE r03_alarm_history IS '나중에 덮어쓴 값';\n"
    )
    v5.assert_sql_scope(text, allowed=v5.CANONICAL_STATEMENTS)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_statement_sequence(text, route="canonical")
    assert caught.value.reason_code == "SQL_SEQUENCE_MISMATCH"

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.declared_table_comment(text)
    assert caught.value.reason_code == "COMMENT_DECLARATION_AMBIGUOUS"


def test_a_duplicated_drop_is_refused() -> None:
    text = v5.SUCCESSOR_SQL.read_text(encoding="utf-8").replace(
        "DROP TABLE r03_alarm_history;",
        "DROP TABLE r03_alarm_history;\nDROP TABLE r03_alarm_history;",
        1,
    )
    v5.assert_sql_scope(text, allowed=v5.SUCCESSOR_STATEMENTS)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_statement_sequence(text, route="successor")
    assert caught.value.reason_code == "SQL_SEQUENCE_MISMATCH"


def test_a_reordered_migration_is_refused() -> None:
    """`DROP TABLE`이 `DROP VIEW`보다 앞서면 실제로는 dependent 때문에 실패한다."""

    statements = list(v5.split_statements(v5.SUCCESSOR_SQL.read_text(encoding="utf-8")))
    statements[1], statements[2] = statements[2], statements[1]
    text = ";\n".join(statements) + ";"
    v5.assert_sql_scope(text, allowed=v5.SUCCESSOR_STATEMENTS)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_statement_sequence(text, route="successor")
    assert caught.value.reason_code == "SQL_SEQUENCE_MISMATCH"


def test_the_canonical_route_refuses_the_successor_body() -> None:
    """route를 헷갈리면 base-only DB에서 없는 table을 drop하게 된다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_statement_sequence(
            v5.SUCCESSOR_SQL.read_text(encoding="utf-8"), route="canonical"
        )
    assert caught.value.reason_code == "SQL_SEQUENCE_MISMATCH"


def test_the_file_scope_check_runs_both_guards() -> None:
    """집합 검사만 부르고 순서를 안 보면 위 입력들이 전부 통과한다."""

    import inspect

    source = inspect.getsource(v5.assert_migration_files_in_scope)
    assert "assert_sql_scope(" in source
    assert "assert_statement_sequence(" in source


# ---------------------------------------------------------------------------
# marker signature 형식 (구현리뷰 7차 권장 1)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CM-2.6 교차검증 — PR #108 merge 후에만 가능하다 (계획 §2.1-3)
# ---------------------------------------------------------------------------


def test_final_row_contract_matches_the_source_manifest() -> None:
    """행 수의 정본은 최종 `project.zip` source manifest다.

    CM-3.1이 자체 pin한 값이 정본과 어긋나면 marker가 잘못된 기준을 고정한다.
    """

    tables = json.loads(
        (REPOSITORY_ROOT / "infra" / "bootstrap" / "source-manifest-v4.json").read_text(
            encoding="utf-8"
        )
    )["tables"]
    for name, rows in v5.FINAL_BASE_ROWS.items():
        assert tables[name]["row_count"] == rows, name
    # evaluation action 12는 mock이 아니라 최종 CSV의 실제 행 수다.
    assert tables["action_history"]["row_count"] == v5.FINAL_ACTION_ROWS["evaluation"]


def test_cm26_and_cm31_agree_on_the_transition_result() -> None:
    """`V5-CM-2.6`이 공용 3 DB에 실제로 만든 값과 같은지 본다.

    두 Task가 같은 상태를 다르게 pin하면 CM-3.1 preflight가 정상 DB를 drift로 본다.
    """

    import postgres_transition as tr

    assert v5.PUBLIC_TARGETS == tr.ORDERED_TARGETS
    assert sorted(v5.BASE_TABLE_NAMES) == sorted(tr.BASE_TABLES)
    assert dict(v5.FINAL_ACTION_ROWS) == dict(tr.FINAL_ACTION_ROWS)
    # CM-2.6 직후 View는 stored alarm 189 · R03 0이다. CM-3.1 적용 직후도 같다.
    assert tr.COMPAT_VIEW_ROWS == 189
    assert tr.COMPAT_VIEW_R03_ROWS == 0
    assert (
        v5.FINAL_BASE_ROWS["trace_alarm_history"]
        + v5.FINAL_BASE_ROWS["summary_alarm_history"]
        == tr.COMPAT_VIEW_ROWS
    )


def test_the_compat_view_hash_matches_cm26() -> None:
    """정적 상수를 실물과 대조한다. 모듈은 `postgres_transition`을 import하지 않는다."""

    import postgres_transition as tr

    assert v5.COMPAT_VIEW_SHA256 == tr.COMPAT_VIEW_SHA256
    assert v5.COMPAT_VIEW_SHA256 != v5.CANONICAL_VIEW_SHA256


def test_the_v5_module_stays_free_of_the_transition_module() -> None:
    """`postgres_transition`은 SQLAlchemy를 끌고 온다.

    순수 계약 모듈이 참조하면 안 된다.
    """

    import ast
    import inspect

    tree = ast.parse(inspect.getsource(v5))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "postgres_transition" not in imported


# ---------------------------------------------------------------------------
# schema 상태 판정 (계획 §4.2)
# ---------------------------------------------------------------------------


def _v4_rows() -> list[dict[str, Any]]:
    """구 R03 catalog 행. **이름만이 아니라 type·길이·nullability까지** 담는다."""

    return [
        {
            "column_name": name,
            "data_type": data_type,
            "character_maximum_length": length,
            "is_nullable": "YES" if nullable else "NO",
        }
        for name, data_type, length, nullable in v5.V4_R03_COLUMN_CONTRACT
    ]


def _v5_rows() -> list[dict[str, Any]]:
    """final R03 catalog 행. 판정기가 type·길이·nullability까지 보므로 전부 담는다."""

    return _rows(v5.R03_COLUMNS)


def test_base_only_is_recognised() -> None:
    assert (
        v5.classify_schema_state(r03_present=False, view_present=False) == "BASE_ONLY"
    )


def test_the_final_shape_is_recognised() -> None:
    assert (
        v5.classify_schema_state(
            r03_present=True,
            view_present=True,
            r03_columns=_v5_rows(),
            view_definition=_canonical_view_definition(),
        )
        == "V5_REFERENCE_FINAL"
    )


def test_a_half_built_schema_is_drift() -> None:
    """한쪽만 있으면 어느 계보도 아니다.

    catalog 정보를 **온전히 준다.** 비워두면 "정보 없음" 분기가 대신 잡아 이 검사가
    가려진다(변이 V2).
    """

    for r03, view in ((True, False), (False, True)):
        assert (
            v5.classify_schema_state(
                r03_present=r03,
                view_present=view,
                r03_columns=_v5_rows(),
                view_definition=_canonical_view_definition(),
            )
            == "PARTIAL_OR_DRIFT"
        )


def test_v4_columns_with_a_final_view_is_drift() -> None:
    """구 R03에 final View가 붙은 조합은 어느 경로로도 만들 수 없다."""

    assert (
        v5.classify_schema_state(
            r03_present=True,
            view_present=True,
            r03_columns=_v4_rows(),
            view_definition=_canonical_view_definition(),
        )
        == "PARTIAL_OR_DRIFT"
    )


def test_missing_catalog_input_is_drift() -> None:
    """정보가 없으면 낙관하지 않는다."""

    assert (
        v5.classify_schema_state(r03_present=True, view_present=True)
        == "PARTIAL_OR_DRIFT"
    )


def test_the_state_list_is_the_planned_four() -> None:
    assert v5.SCHEMA_STATES == (
        "BASE_ONLY",
        "V4_REFERENCE_COMPAT",
        "V5_REFERENCE_FINAL",
        "PARTIAL_OR_DRIFT",
    )


@pytest.mark.parametrize(
    ("state", "route"),
    [("BASE_ONLY", "canonical"), ("V4_REFERENCE_COMPAT", "successor")],
)
def test_each_state_allows_exactly_one_route(state: str, route: str) -> None:
    v5.assert_state_allows_apply(state, route=route)


@pytest.mark.parametrize(
    ("state", "route"),
    [("BASE_ONLY", "successor"), ("V4_REFERENCE_COMPAT", "canonical")],
)
def test_the_wrong_route_is_refused(state: str, route: str) -> None:
    """base-only에 successor를 돌리면 없는 table을 drop한다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_state_allows_apply(state, route=route)
    assert caught.value.reason_code == "MIGRATION_ROUTE_NOT_ALLOWED"


@pytest.mark.parametrize("state", ["V5_REFERENCE_FINAL", "PARTIAL_OR_DRIFT"])
def test_no_route_applies_to_these_states(state: str) -> None:
    """final은 no-op verify, drift는 쓰기 0이다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_state_allows_apply(state, route="canonical")
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"
    assert caught.value.exit_code == v5.EXIT_CONFIRM_REQUIRED


def test_an_unknown_state_is_refused() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_state_allows_apply("WHATEVER", route="canonical")
    assert caught.value.reason_code == "SCHEMA_STATE_NOT_ALLOWED"


def _compat_view_definition() -> str:
    """CM-2.6 호환 View의 실측 정의. 격리 PostgreSQL 16에서 뽑아 고정했다."""

    text = (
        REPOSITORY_ROOT
        / "backend"
        / "tests"
        / "fixtures"
        / "v5_cm_3_1"
        / "compat_view.sql"
    ).read_text(encoding="utf-8")
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("--")
    ).strip()


def test_the_compat_fixture_is_the_pinned_one() -> None:
    """fixture가 흔들리면 상태 판정이 조용히 무의미해진다."""

    assert v5.view_definition_sha256(_compat_view_definition()) == v5.COMPAT_VIEW_SHA256


def test_the_cm26_pre_state_is_recognised() -> None:
    """공용 3 DB의 정상 pre-state다(계획 §4.2)."""

    assert (
        v5.classify_schema_state(
            r03_present=True,
            view_present=True,
            r03_columns=_v4_rows(),
            view_definition=_compat_view_definition(),
        )
        == "V4_REFERENCE_COMPAT"
    )
    v5.assert_state_allows_apply("V4_REFERENCE_COMPAT", route="successor")


def test_a_final_view_over_v4_columns_is_not_the_compat_state() -> None:
    """View만 final이고 R03가 구 계보면 successor를 돌리면 안 된다."""

    assert (
        v5.classify_schema_state(
            r03_present=True,
            view_present=True,
            r03_columns=_v5_rows(),
            view_definition=_compat_view_definition(),
        )
        == "PARTIAL_OR_DRIFT"
    )


def test_the_legacy_view_is_not_the_compat_view() -> None:
    """CM-2.6 이전 15컬럼 legacy View는 CM-3.1의 pre-state가 아니다."""

    import postgres_transition as tr

    assert tr.LEGACY_VIEW_SHA256 != v5.COMPAT_VIEW_SHA256
    assert tr.LEGACY_VIEW_SHA256 != v5.CANONICAL_VIEW_SHA256


# ---------------------------------------------------------------------------
# runner 계약 — 묶음 2 (계획 §7.1·§7.2·§7.3)
# ---------------------------------------------------------------------------


def test_the_lock_namespace_is_shared_with_cm26() -> None:
    """다른 namespace를 쓰면 두 Task runner가 같은 DB에서 동시에 돈다."""

    import postgres_transition as tr

    assert v5.ADVISORY_LOCK_NAMESPACE == tr.ADVISORY_LOCK_NAMESPACE
    for database in v5.TARGET_PROFILE:
        assert v5.advisory_lock_key(database) == tr.advisory_lock_key(database)
    assert dict(v5.TARGET_PROFILE) == dict(tr.TARGET_PROFILE)


def test_each_target_gets_a_distinct_lock_key() -> None:
    keys = {v5.advisory_lock_key(d) for d in v5.TARGET_PROFILE}
    assert len(keys) == len(v5.TARGET_PROFILE)
    assert all(0 <= k <= 0x7FFF_FFFF for k in keys)


def test_an_unknown_database_has_no_lock_key() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.advisory_lock_key("postgres")
    assert caught.value.reason_code == "TARGET_NOT_ALLOWED"


def test_isolation_is_checked_before_timeouts() -> None:
    """timeout을 걸어놓고 isolation이 다르면 이미 잘못된 transaction 안이다."""

    assert v5.SESSION_PROLOGUE[0] == v5.ISOLATION_SQL
    assert v5.SESSION_PROLOGUE[1:] == (
        v5.LOCK_TIMEOUT_SQL,
        v5.STATEMENT_TIMEOUT_SQL,
        v5.IDLE_TIMEOUT_SQL,
    )
    assert all("SET LOCAL" in s for s in v5.SESSION_PROLOGUE[1:])


def test_exactly_one_cli_mode_is_required() -> None:
    assert v5.assert_single_mode(["apply"]) == "apply"
    for selected in ([], ["apply", "verify"]):
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.assert_single_mode(selected)
        assert caught.value.reason_code == "CLI_MODE_CONFLICT"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_single_mode(["drop-everything"])
    assert caught.value.reason_code == "CLI_MODE_NOT_ALLOWED"


def test_the_target_must_be_confirmed() -> None:
    assert v5.assert_target_allowed("kosa_agent", confirm_target="kosa_agent") == (
        "runtime"
    )
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_target_allowed("kosa_agent", confirm_target="kosa_agent_e2e")
    assert caught.value.reason_code == "TARGET_CONFIRM_REQUIRED"
    assert caught.value.exit_code == v5.EXIT_CONFIRM_REQUIRED
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_target_allowed("kosa_agent", confirm_target=None)
    assert caught.value.reason_code == "TARGET_CONFIRM_REQUIRED"


def test_an_unknown_target_is_refused_before_confirmation() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_target_allowed("postgres", confirm_target="postgres")
    assert caught.value.reason_code == "TARGET_NOT_ALLOWED"


def _lock_names(profile: str) -> list[str]:
    return [
        s.split("public.")[1].split(" ")[0].strip('"')
        for s in v5.share_lock_statements(profile)
    ]


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_share_locks_are_name_ordered_and_cover_base_nine(profile: str) -> None:
    """두 runner가 다른 순서로 잡으면 서로 기다린다."""

    names = _lock_names(profile)
    assert names == sorted(names)
    assert set(v5.BASE_TABLE_NAMES) <= set(names)
    assert all(
        '"' in s and "IN SHARE MODE" in s for s in v5.share_lock_statements(profile)
    )


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_share_locks_do_not_include_the_owned_objects(profile: str) -> None:
    """R03·View는 바꾸는 대상이라 `SHARE`가 아니다."""

    names = _lock_names(profile)
    assert v5.R03_TABLE not in names
    assert v5.ALARM_VIEW not in names


def test_the_preserved_allowlist_matches_the_registered_inventory() -> None:
    """등록 inventory에서 base 9·R03·**legacy handoff**를 뺀 나머지다.

    legacy handoff를 빼지 않으면 runner가 실재하지 않는 table을 읽는다
    (Gate 0 조사 §3).
    """

    for profile in REGISTERED_STAGE:
        tables = set(_registered(profile)["tables"])
        expected = (
            tables
            - set(v5.BASE_TABLE_NAMES)
            - {v5.R03_TABLE}
            - v5.LEGACY_HANDOFF_TABLES
        )
        assert set(v5.PRESERVED_TABLES_BY_PROFILE[profile]) == expected, profile
        assert len(tables) == v5.SUPERSEDED_PROFILE_TABLE_COUNTS[profile]


def test_the_derivation_source_is_a_deprecated_epoch_inventory() -> None:
    """**구·신 데이터 경계를 고정한다.**

    보존 목록의 유도 근거인 등록 manifest 3종은 전부 폐기 epoch `kosa_0813`이다.
    여기서는 그것을 **table 이름의 물리 inventory로만** 쓴다 — epoch·행 수·hash는
    근거로 쓰지 않는다. 그 경계가 흐려져 구 epoch 전용 table을 보존 대상으로 잡은 것이
    `document_corpus` 결함이었다(Gate 0 조사 §3).
    """

    for profile in REGISTERED_STAGE:
        registered = _registered(profile)
        # 유도 근거는 **구 epoch**다. 이 사실 자체를 고정한다.
        assert registered["dataset_epoch"] != v5.FINAL_DATASET_EPOCH, profile
        # 그래서 이 manifest는 CM-3.1의 데이터 계약을 통과하지 못한다.
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.assert_final_epoch_contract(registered, profile=profile)
        assert caught.value.reason_code == "MANIFEST_EPOCH_NOT_FINAL", profile


def test_the_inventory_counts_name_their_stage() -> None:
    """**23/14는 구 등록 manifest 값이지 현재도 final도 아니다**(구현리뷰 17차 필수 2).

    단계마다 다르다 — 구 manifest 23/14, 현재 공용 22/14, `V5-B-1.1` 이후 22/13.
    artifact field 이름이 `superseded_…`인 것도 그 뜻이다. final 발급은 `V5-CM-1.8`이
    하고 그 선행에 B-1.1이 있다.
    """

    assert v5.SUPERSEDED_PROFILE_TABLE_COUNTS == {"runtime": 23, "evaluation": 14}
    assert v5.FINAL_PROFILE_TABLE_COUNTS == {"runtime": 22, "evaluation": 13}
    # 구 값이 곧 final이라는 해석을 막는다.
    assert v5.SUPERSEDED_PROFILE_TABLE_COUNTS != v5.FINAL_PROFILE_TABLE_COUNTS
    # final 개수는 legacy handoff를 뺀 구 inventory와 같아야 한다.
    for profile, count in v5.FINAL_PROFILE_TABLE_COUNTS.items():
        registered = set(_registered(profile)["tables"])
        assert count == len(registered - v5.LEGACY_HANDOFF_TABLES), profile


def test_the_artifact_field_says_superseded() -> None:
    """artifact가 구 inventory를 **final이라고 주장하지 않는다**."""

    artifact = _artifact()
    assert "profile_inventory_counts" not in artifact
    assert artifact["superseded_profile_inventory_counts"] == dict(
        v5.SUPERSEDED_PROFILE_TABLE_COUNTS
    )


def test_the_legacy_handoff_is_not_preserved() -> None:
    """`V5-B-1.1`이 채택하지 않고 B가 지운다 — 보존 대상으로 잡지 않는다.

    등록 manifest에는 남아 있으므로 **inventory와 보존 목록이 다르다는 것**을
    명시적으로 고정한다. 잡으면 공용 runtime 2 DB에서 `UndefinedTable`로 죽고,
    B가 `kosa_text2sql`에서 정리하면 evaluation 쪽도 깨진다(Gate 0 조사 §3).
    """

    assert v5.LEGACY_HANDOFF_TABLES == {"document_corpus"}
    for profile in ("runtime", "evaluation"):
        preserved = set(v5.PRESERVED_TABLES_BY_PROFILE[profile])
        assert not (preserved & v5.LEGACY_HANDOFF_TABLES), profile
        # 등록 inventory에는 여전히 있다 — 우리가 의도적으로 뺀 것이다.
        assert v5.LEGACY_HANDOFF_TABLES <= set(_registered(profile)["tables"]), profile
    assert len(v5.PRESERVED_TABLES_BY_PROFILE["runtime"]) == 12
    assert len(v5.PRESERVED_TABLES_BY_PROFILE["evaluation"]) == 3


def test_a_caller_supplied_table_name_never_reaches_sql() -> None:
    """호출자가 이름을 주지 못한다 — allowlist에서만 온다(구현리뷰 9차 필수 2)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.share_lock_statements("x IN SHARE MODE; DROP TABLE lot_history; --")
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"


def test_locked_tables_refuse_an_owned_object(monkeypatch: Any) -> None:
    """allowlist에 R03가 섞이면 자기 자신을 `SHARE`로 잡는다."""

    from types import MappingProxyType

    patched = dict(v5.PRESERVED_TABLES_BY_PROFILE)
    patched["runtime"] = (*patched["runtime"], v5.R03_TABLE)
    monkeypatch.setattr(v5, "PRESERVED_TABLES_BY_PROFILE", MappingProxyType(patched))
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.locked_tables("runtime")
    assert caught.value.reason_code == "SECURITY_RELATION_SET_MISMATCH"


def test_the_exclusive_lock_is_only_the_owned_table() -> None:
    statements = v5.exclusive_lock_statements()
    assert statements == (f"LOCK TABLE public.{v5.R03_TABLE} IN ACCESS EXCLUSIVE MODE",)


# --- receipt·marker ---


def _committed_receipt(**overrides: Any) -> dict[str, Any]:
    """`COMMITTED`는 commit 직후 identity 세 값을 반드시 갖는다."""

    payload = {
        "status": "COMMITTED",
        "schema_signature_sha256": _signature(),
        "security_signature_sha256": v5.security_signature_sha256(
            _sec(), mode="successor"
        ),
        "excluded_projection_sha256": "b" * 64,
    }
    payload.update(overrides)
    return _receipt(**payload)


def _receipt(**overrides: Any) -> dict[str, Any]:
    payload = {
        "artifact_type": v5.RECEIPT_ARTIFACT_TYPE,
        "format_version": v5.MIGRATION_CONTRACT_FORMAT_VERSION,
        "dataset_epoch": v5.FINAL_DATASET_EPOCH,
        "database": "kosa_agent",
        "profile": "runtime",
        "status": "STARTED",
        "change_ref": "GH-110",
        "migration_id": v5.MIGRATION_ID,
        "migration_bundle_sha256": v5.migration_bundle_sha256(),
        "route": "successor",
        "recorded_at": "2026-08-23T12:00:00+09:00",
        "schema_signature_sha256": None,
        "security_signature_sha256": None,
        "excluded_projection_sha256": None,
    }
    payload.update(overrides)
    return payload


def _marker(**overrides: Any) -> dict[str, Any]:
    payload = _committed_receipt()
    payload["artifact_type"] = v5.MARKER_ARTIFACT_TYPE
    payload.update(
        {
            "view_definition_sha256": v5.CANONICAL_VIEW_SHA256,
            "view_rows": 189,
            "r03_rows": 0,
            "action_history_rows": 0,
            "committed_at": "2026-08-23T12:00:01+09:00",
        }
    )
    payload.update(overrides)
    return payload


def test_the_receipt_and_marker_contracts_accept_their_own_shape() -> None:
    v5.assert_receipt_contract(_receipt())
    v5.assert_marker_contract(_marker())


def test_a_marker_is_not_a_receipt() -> None:
    """key 집합이 다르므로 서로를 대신할 수 없다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(_marker())
    assert caught.value.reason_code == "RECEIPT_SCHEMA_MISMATCH"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_contract(_receipt())
    assert caught.value.reason_code == "MARKER_SCHEMA_MISMATCH"


@pytest.mark.parametrize("status", ["STARTED", "ABORTED"])
def test_only_a_committed_run_may_leave_a_marker(status: str) -> None:
    """실패한 적용이 marker를 남기면 no-op이 그것을 정본으로 삼는다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_contract(_marker(status=status))
    assert caught.value.reason_code == "MARKER_STATUS_NOT_COMMITTED"
    # receipt에는 정상 status다.
    v5.assert_receipt_contract(_receipt(status=status))


def test_an_unknown_receipt_status_is_refused() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(_receipt(status="PROBABLY_FINE"))
    assert caught.value.reason_code == "RECEIPT_STATUS_NOT_ALLOWED"


def test_a_receipt_with_a_foreign_profile_is_refused() -> None:
    """database와 profile이 어긋나면 다른 DB의 증적이 된다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(_receipt(profile="evaluation"))
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"


def test_a_receipt_without_a_change_ref_is_refused() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(_receipt(change_ref=""))
    assert caught.value.reason_code == "CHANGE_REF_REQUIRED"


def test_a_stale_bundle_invalidates_the_receipt() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(_receipt(migration_bundle_sha256="0" * 64))
    assert caught.value.reason_code == "MIGRATION_BUNDLE_STALE"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("format_version", "v0", "ARTIFACT_VERSION_MISMATCH"),
        # **폐기 epoch가 receipt·marker로 들어오면 안 된다.** artifact 쪽만 막고
        # 있었고 이쪽은 변이가 살아남았다(최종검증 변이 N01–N03).
        ("dataset_epoch", "kosa_0813", "MANIFEST_EPOCH_NOT_FINAL"),
        ("migration_id", "000_something_else", "CONTRACT_IDENTITY_MISMATCH"),
        ("database", "kosa_not_allowed", "TARGET_NOT_ALLOWED"),
    ],
)
def test_a_receipt_identity_drift_is_rejected(
    field: str, value: str, reason: str
) -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(_receipt(**{field: value}))
    assert caught.value.reason_code == reason


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("format_version", "v0", "ARTIFACT_VERSION_MISMATCH"),
        ("dataset_epoch", "kosa_0813", "MANIFEST_EPOCH_NOT_FINAL"),
        ("migration_id", "000_something_else", "CONTRACT_IDENTITY_MISMATCH"),
        ("database", "kosa_not_allowed", "TARGET_NOT_ALLOWED"),
    ],
)
def test_a_marker_identity_drift_is_rejected(
    field: str, value: str, reason: str
) -> None:
    """marker도 같은 신원 계약을 탄다 — no-op이 이걸 정본으로 삼는다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_contract(_marker(**{field: value}))
    assert caught.value.reason_code == reason


def test_a_secret_cannot_hide_in_an_artifact() -> None:
    """host·DSN·절대경로는 receipt에 자리가 없다(계획 §7.3)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(
            _receipt(change_ref="postgresql://user:pw@host/kosa_agent")
        )
    assert caught.value.reason_code == "CONTRACT_SENSITIVE_VALUE"


def test_a_marker_pins_the_final_view() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_contract(_marker(view_definition_sha256=v5.COMPAT_VIEW_SHA256))
    assert caught.value.reason_code == "VIEW_IDENTITY_MISMATCH"


@pytest.mark.parametrize("key", ["view_rows", "r03_rows", "action_history_rows"])
def test_a_negative_row_count_is_refused(key: str) -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_contract(_marker(**{key: -1}))
    assert caught.value.reason_code == "MARKER_SCHEMA_MISMATCH"


def test_a_boolean_is_not_a_row_count() -> None:
    """`True`는 `int`의 부분집합이라 그냥 통과한다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_contract(_marker(r03_rows=True))
    assert caught.value.reason_code == "MARKER_SCHEMA_MISMATCH"


# --- no-op 판정 ---


def test_noop_passes_when_marker_and_live_agree() -> None:
    marker = _marker()
    v5.assert_marker_allows_noop(
        marker,
        schema_signature=marker["schema_signature_sha256"],
        security_signature=marker["security_signature_sha256"],
        excluded_projection=marker["excluded_projection_sha256"],
    )


def test_noop_ignores_row_counts() -> None:
    """`V5-A-1.4`가 3/192로 바꿔도 schema identity는 그대로다(계획 §7.3)."""

    marker = _marker()
    populated = _marker(view_rows=192, r03_rows=3)
    v5.assert_marker_allows_noop(
        populated,
        schema_signature=marker["schema_signature_sha256"],
        security_signature=marker["security_signature_sha256"],
        excluded_projection=populated["excluded_projection_sha256"],
    )
    assert {"view_rows", "r03_rows"} <= v5.MARKER_VOLATILE_KEYS


def test_noop_refuses_a_schema_drift() -> None:
    marker = _marker()
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_allows_noop(
            marker,
            schema_signature="c" * 64,
            security_signature=marker["security_signature_sha256"],
            excluded_projection=marker["excluded_projection_sha256"],
        )
    assert caught.value.reason_code == "SCHEMA_SIGNATURE_MISMATCH"


def test_noop_refuses_a_security_drift() -> None:
    """`DROP TABLE`이 버린 권한이 복원되지 않은 채 no-op이 되면 안 된다."""

    marker = _marker()
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_allows_noop(
            marker,
            schema_signature=marker["schema_signature_sha256"],
            security_signature=v5.security_signature_sha256(
                _sec("{kosa=arwdDxt/kosa}"), mode="successor"
            ),
            excluded_projection=marker["excluded_projection_sha256"],
        )
    assert caught.value.reason_code == "SECURITY_SIGNATURE_MISMATCH"


LIVE_SIGNATURE_KEYS = (
    "schema_signature",
    "security_signature",
    "excluded_projection",
)


@pytest.mark.parametrize("key", LIVE_SIGNATURE_KEYS)
@pytest.mark.parametrize("value", ["oops", "", "G" * 64, "0" * 63])
def test_a_malformed_live_signature_stops_the_noop(key: str, value: str) -> None:
    """**live 쪽** 형식 검증 branch를 고정한다.

    기존 `ARTIFACT_HASH_MALFORMED` 회귀는 receipt·marker **payload**만 봤다.
    두 비교 함수는 `live[key]`도 같은 loop에서 형식을 보는데 그 branch는
    회귀가 없었다(구현리뷰 9차 필수 1).

    production live는 `security_signature_sha256()` 반환값이라 정상 경로에서는
    도달하지 않는다. 그래도 branch가 코드에 있으면 reason 계약을 고정한다 —
    없으면 다음 사람이 그 branch를 지워도 아무도 모른다.
    """

    live = {
        "schema_signature": _signature(),
        "security_signature": v5.security_signature_sha256(_sec(), mode="successor"),
        "excluded_projection": "b" * 64,
    }
    marker = _marker(
        schema_signature_sha256=live["schema_signature"],
        security_signature_sha256=live["security_signature"],
        excluded_projection_sha256=live["excluded_projection"],
    )
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_allows_noop(marker, **{**live, key: value})
    assert caught.value.reason_code == "ARTIFACT_HASH_MALFORMED"


@pytest.mark.parametrize("key", LIVE_SIGNATURE_KEYS)
@pytest.mark.parametrize("value", ["oops", "", "G" * 64, "0" * 63])
def test_a_malformed_live_signature_stops_the_recovery(key: str, value: str) -> None:
    """복구 경로도 같은 loop를 쓴다 — 한쪽만 덮으면 다른 쪽이 남는다."""

    live = {
        "schema_signature": _signature(),
        "security_signature": v5.security_signature_sha256(_sec(), mode="successor"),
        "excluded_projection": "b" * 64,
    }
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_recovery_is_allowed(
            _committed_receipt(),
            **{**live, key: value},
            view_definition_sha256_value=v5.CANONICAL_VIEW_SHA256,
        )
    assert caught.value.reason_code == "ARTIFACT_HASH_MALFORMED"


# --- marker 복구 ---


def test_recovery_needs_a_committed_receipt_and_a_final_live_view() -> None:
    v5.assert_recovery_is_allowed(
        _committed_receipt(),
        schema_signature=_signature(),
        security_signature=v5.security_signature_sha256(_sec(), mode="successor"),
        excluded_projection="b" * 64,
        view_definition_sha256_value=v5.CANONICAL_VIEW_SHA256,
    )


def test_a_started_receipt_cannot_recover_a_marker() -> None:
    """commit 전에 죽은 실행은 복구가 아니라 재적용 대상이다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_recovery_is_allowed(
            _receipt(status="STARTED"),
            schema_signature=_signature(),
            security_signature=v5.security_signature_sha256(_sec(), mode="successor"),
            excluded_projection="b" * 64,
            view_definition_sha256_value=v5.CANONICAL_VIEW_SHA256,
        )
    assert caught.value.reason_code == "RECEIPT_STATUS_NOT_ALLOWED"


def test_recovery_refuses_a_non_final_live_view() -> None:
    """receipt만 믿고 marker를 만들면 실제와 다른 정본이 생긴다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_recovery_is_allowed(
            _committed_receipt(),
            schema_signature=_signature(),
            security_signature=v5.security_signature_sha256(_sec(), mode="successor"),
            excluded_projection="b" * 64,
            view_definition_sha256_value=v5.COMPAT_VIEW_SHA256,
        )
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_an_extra_receipt_key_is_refused() -> None:
    """artifact_type은 맞는데 key가 하나 더 있는 입력. exact key만 잡는다(변이 W10)."""

    payload = _receipt()
    payload["operator"] = "누군가"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(payload)
    assert caught.value.reason_code == "RECEIPT_SCHEMA_MISMATCH"


def test_a_missing_receipt_key_is_refused() -> None:
    payload = _receipt()
    del payload["route"]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(payload)
    assert caught.value.reason_code == "RECEIPT_SCHEMA_MISMATCH"


def test_an_extra_marker_key_is_refused() -> None:
    """변이 W12 — artifact_type 검사가 가리지 않도록 type은 맞춰 둔다."""

    payload = _marker()
    payload["host"] = "elsewhere"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_contract(payload)
    assert caught.value.reason_code == "MARKER_SCHEMA_MISMATCH"


def test_a_missing_marker_key_is_refused() -> None:
    payload = _marker()
    del payload["excluded_projection_sha256"]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_contract(payload)
    assert caught.value.reason_code == "MARKER_SCHEMA_MISMATCH"


def test_noop_runs_the_marker_contract_first() -> None:
    """계약을 건너뛰면 실패한 적용의 marker로도 no-op이 된다(변이 W22)."""

    marker = _marker(status="STARTED")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_allows_noop(
            marker,
            schema_signature=marker["schema_signature_sha256"],
            security_signature=marker["security_signature_sha256"],
            excluded_projection=marker["excluded_projection_sha256"],
        )
    assert caught.value.reason_code == "MARKER_STATUS_NOT_COMMITTED"


def test_recovery_runs_the_receipt_contract_first() -> None:
    """계약을 건너뛰면 아무 payload로도 marker를 되살릴 수 있다(변이 W25)."""

    receipt = _committed_receipt()
    receipt["operator"] = "누군가"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_recovery_is_allowed(
            receipt,
            schema_signature=_signature(),
            security_signature=v5.security_signature_sha256(_sec(), mode="successor"),
            excluded_projection="b" * 64,
            view_definition_sha256_value=v5.CANONICAL_VIEW_SHA256,
        )
    assert caught.value.reason_code == "RECEIPT_SCHEMA_MISMATCH"


# ---------------------------------------------------------------------------
# owner·ACL 복원 statement (계획 §6.5·§7.2)
# ---------------------------------------------------------------------------


def test_the_restore_statements_are_owner_then_grants() -> None:
    statements = v5.restore_security_statements(_sec(), mode="successor")
    assert statements == (
        f'ALTER TABLE public.{v5.R03_TABLE} OWNER TO "kosa"',
        f'GRANT SELECT ON public.{v5.R03_TABLE} TO "kosa_readonly"',
        f'ALTER VIEW public.{v5.ALARM_VIEW} OWNER TO "kosa"',
        f'GRANT SELECT ON public.{v5.ALARM_VIEW} TO "kosa_readonly"',
    )


def test_base_only_issues_no_acl_statement() -> None:
    """격리 rehearsal·fresh bootstrap에는 복원할 pre-state가 없다(계획 §7.2)."""

    assert v5.restore_security_statements(_sec(), mode="base_only") == ()
    assert v5.restore_security_statements(_sec(None), mode="base_only") == ()


def test_the_owner_is_not_regranted_to_itself() -> None:
    """재생성 직후 owner는 이미 모든 권한을 갖는다."""

    statements = v5.restore_security_statements(_sec(), mode="successor")
    assert not any('TO "kosa"' in s and s.startswith("GRANT") for s in statements)


def test_multiple_privileges_become_one_grant() -> None:
    rows = _sec("{kosa=arwdDxt/kosa,kosa_app=arw/kosa}")
    statements = v5.restore_security_statements(rows, mode="successor")
    grant = next(s for s in statements if "kosa_app" in s)
    assert grant.startswith("GRANT INSERT, SELECT, UPDATE ON public.")


@pytest.mark.parametrize(
    "role", ['kosa"; DROP TABLE lot_history; --', "kosa-readonly", "1kosa"]
)
def test_an_unsafe_role_name_never_reaches_sql(role: str) -> None:
    """DB에서 읽은 값을 그대로 SQL에 넣기 전에 본다.

    빈 grantee(`=r/kosa`)는 `PUBLIC`이라 앞선 검사가 먼저 잡는다 — 별도 회귀가 있다.
    """

    rows = _sec(f"{{kosa=arwdDxt/kosa,{role}=r/kosa}}")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.restore_security_statements(rows, mode="successor")
    assert caught.value.reason_code in {
        "SECURITY_IDENTIFIER_UNSAFE",
        "SECURITY_ACL_UNPARSABLE",
    }


def test_an_unsafe_owner_name_is_refused() -> None:
    rows = _sec(owner='kosa"; DROP TABLE lot_history; --')
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.restore_security_statements(rows, mode="successor")
    assert caught.value.reason_code == "SECURITY_IDENTIFIER_UNSAFE"


def test_an_unsupported_privilege_letter_is_refused() -> None:
    """모르는 권한을 조용히 버리면 복원이 불완전해진다."""

    rows = _sec("{kosa=arwdDxt/kosa,kosa_app=rZ/kosa}")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.restore_security_statements(rows, mode="successor")
    assert caught.value.reason_code == "SECURITY_ACL_UNSUPPORTED"


def test_a_public_grant_never_becomes_a_restore_statement() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.restore_security_statements(
            _sec("{kosa=arwdDxt/kosa,=r/kosa}"), mode="successor"
        )
    assert caught.value.reason_code == "SECURITY_PUBLIC_GRANT"


def test_restore_refuses_a_null_acl_on_the_successor_path() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.restore_security_statements(_sec(None), mode="successor")
    assert caught.value.reason_code == "SECURITY_ACL_MISSING"


# ---------------------------------------------------------------------------
# transaction 계획 (계획 §7.2)
# ---------------------------------------------------------------------------


def _plan(route: str = "successor", **kwargs: Any) -> tuple[str, ...]:
    return v5.apply_plan(
        route=route,
        profile=kwargs.pop("profile", "runtime"),
        security=kwargs.pop("security", _sec() if route == "successor" else {}),
    )


def test_the_successor_plan_follows_the_documented_order() -> None:
    plan = _plan("successor")
    v5.assert_plan_invariants(plan, route="successor")
    body = list(plan[len(v5.SESSION_PROLOGUE) :])
    drops = [s.strip() for s in body if s.lstrip().upper().startswith("DROP ")]
    assert len(drops) == 2
    # View를 먼저 내려야 `DROP TABLE`이 dependent에 막히지 않는다.
    assert drops[0].endswith(v5.ALARM_VIEW)
    assert drops[1].endswith(v5.R03_TABLE)


def test_the_canonical_plan_has_no_drop_or_grant() -> None:
    plan = _plan("canonical")
    v5.assert_plan_invariants(plan, route="canonical")
    assert not [s for s in plan if s.lstrip().upper().startswith("DROP ")]
    assert not [s for s in plan if s.startswith(("GRANT ", "ALTER "))]
    assert not [s for s in plan if "ACCESS EXCLUSIVE" in s]


def test_the_exclusive_lock_comes_after_every_share() -> None:
    """drift인 DB에서 먼저 잡으면 쓰기 0으로 끝내면서도 남의 읽기를 막는다."""

    body = list(_plan("successor")[len(v5.SESSION_PROLOGUE) :])
    last_share = max(i for i, s in enumerate(body) if "IN SHARE MODE" in s)
    first_exclusive = min(i for i, s in enumerate(body) if "ACCESS EXCLUSIVE" in s)
    assert last_share < first_exclusive


def test_the_acl_restore_comes_after_the_ddl() -> None:
    """재생성 전에 복원하면 곧바로 지워진다."""

    body = list(_plan("successor")[len(v5.SESSION_PROLOGUE) :])
    last_ddl = max(i for i, s in enumerate(body) if "CREATE TABLE" in s)
    first_grant = min(
        i for i, s in enumerate(body) if s.startswith(("GRANT ", "ALTER "))
    )
    assert last_ddl < first_grant


def test_an_unknown_route_has_no_plan() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.apply_plan(route="whatever", profile="runtime", security=_sec())
    assert caught.value.reason_code == "MIGRATION_ROUTE_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        pytest.param(lambda p: p[1:], "prologue 훼손", id="prologue"),
        pytest.param(
            lambda p: tuple(reversed(p[len(v5.SESSION_PROLOGUE) :])),
            "본문 역순",
            id="역순",
        ),
    ],
)
def test_a_broken_plan_is_refused(mutate: Any, why: str) -> None:
    plan = _plan("successor")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_plan_invariants(mutate(plan), route="successor")
    assert caught.value.reason_code == "PLAN_ORDER_MISMATCH", why


def test_a_canonical_plan_is_not_a_successor_plan() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_plan_invariants(_plan("canonical"), route="successor")
    assert caught.value.reason_code == "PLAN_ORDER_MISMATCH"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_plan_invariants(_plan("successor"), route="canonical")
    assert caught.value.reason_code == "PLAN_ORDER_MISMATCH"


# ---------------------------------------------------------------------------
# catalog 조회와 postcheck — driver 없이 (묶음 2)
# ---------------------------------------------------------------------------


def _fake_execute(rows_by_sql: dict[str, list[dict[str, Any]]]) -> Any:
    """`(sql, params) -> rows`. **module이 driver를 고르지 않는다**는 계약을 쓴다."""

    seen: list[str] = []

    def run(sql: str, params: Any = None) -> list[dict[str, Any]]:
        seen.append(sql)
        return rows_by_sql.get(sql, [])

    run.seen = seen  # type: ignore[attr-defined]
    return run


def _live() -> dict[str, Any]:
    return {
        "r03_columns": _rows(v5.R03_COLUMNS),
        "r03_constraints": _constraint_rows(),
        "view_columns": _view_rows(),
        "view_definition": _canonical_view_definition(),
        "security": {
            v5.R03_TABLE: {
                "owner": "kosa",
                "acl": _ACL,
                "comment": v5.R03_COMMENT,
            },
            v5.ALARM_VIEW: {"owner": "kosa", "acl": _ACL, "comment": None},
        },
    }


def test_presence_checks_the_relation_kind() -> None:
    """View 자리에 같은 이름의 table이 있으면 '있다'로 세면 안 된다."""

    execute = _fake_execute(
        {
            v5.RELATION_PRESENCE_SQL: [
                {"relname": v5.R03_TABLE, "relkind": "r"},
                {"relname": v5.ALARM_VIEW, "relkind": "r"},
            ]
        }
    )
    assert v5.read_relation_presence(execute) == (True, False)


def test_presence_reports_an_empty_schema() -> None:
    assert v5.read_relation_presence(_fake_execute({})) == (False, False)


def test_reading_the_live_schema_asks_for_every_contract_input() -> None:
    execute = _fake_execute(
        {
            v5.VIEW_DEFINITION_SQL: [{"definition": _canonical_view_definition()}],
            v5.RELATION_SECURITY_SQL: [
                {"relname": name, "owner": "kosa", "acl": _ACL, "comment": None}
                for name in v5.OWNED_BY_CM31
            ],
            v5.R03_COLUMNS_SQL: _rows(v5.R03_COLUMNS),
            v5.R03_CONSTRAINTS_SQL: _constraint_rows(),
            v5.VIEW_COLUMNS_SQL: _view_rows(),
        }
    )
    live = v5.read_live_schema(execute)
    assert set(live) == {
        "r03_columns",
        "r03_constraints",
        "view_columns",
        "view_definition",
        "security",
    }
    assert set(live["security"]) == set(v5.OWNED_BY_CM31)


def test_live_signatures_refuse_a_non_canonical_catalog() -> None:
    """잘못된 snapshot이 최초 marker의 identity가 되면 안 된다(2차 필수 3)."""

    live = _live()
    live["r03_columns"] = _rows(v5.R03_COLUMNS[:-1])
    with pytest.raises(v5.ReferenceV5Error):
        v5.live_signatures(live, mode="successor")


def test_live_signatures_refuse_an_erased_comment() -> None:
    live = _live()
    live["security"][v5.R03_TABLE]["comment"] = None
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.live_signatures(live, mode="successor")
    assert caught.value.reason_code == "COMMENT_NOT_CANONICAL"


def test_postcheck_passes_on_the_final_shape() -> None:
    """container fixture는 형상 재현용이라 최종 행 수를 갖지 않는다. 여기서 덮는다."""

    assert (
        v5.assert_postcheck(
            _live(),
            profile="runtime",
            mode="successor",
            view_rows=189,
            r03_rows=0,
            action_rows=0,
        )
        == "REFERENCE_EMPTY"
    )
    assert (
        v5.assert_postcheck(
            _live(),
            profile="evaluation",
            mode="successor",
            view_rows=192,
            r03_rows=3,
            action_rows=12,
        )
        == "R03_POPULATED"
    )


def test_postcheck_refuses_a_changed_action_count() -> None:
    """CM-3.1은 `action_history`를 건드리지 않는다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_postcheck(
            _live(),
            profile="runtime",
            mode="successor",
            view_rows=189,
            r03_rows=0,
            action_rows=1,
        )
    assert caught.value.reason_code == "ACTION_ROWS_CHANGED"


def test_postcheck_refuses_an_unknown_profile() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_postcheck(
            _live(),
            profile="corrected",
            mode="successor",
            view_rows=189,
            r03_rows=0,
            action_rows=0,
        )
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"


def test_postcheck_runs_the_signature_contracts_first() -> None:
    """행 수만 맞으면 통과하는 구조면 catalog drift가 marker가 된다."""

    live = _live()
    live["view_definition"] = "SELECT 1"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_postcheck(
            live,
            profile="runtime",
            mode="successor",
            view_rows=189,
            r03_rows=0,
            action_rows=0,
        )
    assert caught.value.reason_code != "ACTION_ROWS_CHANGED"


def test_an_intermediate_row_count_is_not_a_phase() -> None:
    """`1/190`은 schema가 final이어도 data health 실패다(계획 §4.2)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_postcheck(
            _live(),
            profile="runtime",
            mode="successor",
            view_rows=190,
            r03_rows=1,
            action_rows=0,
        )
    assert caught.value.reason_code == "DATA_PHASE_UNKNOWN"


def test_restore_refuses_an_unknown_mode() -> None:
    """바깥 allowlist를 지운 근거 — 안쪽 계약이 같은 reason으로 거부한다(변이 X22)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.restore_security_statements(_sec(), mode="whatever")
    assert caught.value.reason_code == "SECURITY_MODE_NOT_ALLOWED"


def test_restore_refuses_an_extra_relation() -> None:
    """CM-3.1이 소유하지 않는 relation에 권한을 발행하면 안 된다(변이 X6)."""

    rows = _sec()
    rows["nl_query_log"] = {"owner": "kosa", "acl": _ACL}
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.restore_security_statements(rows, mode="successor")
    assert caught.value.reason_code == "SECURITY_RELATION_SET_MISMATCH"


def test_privileges_are_emitted_in_a_stable_order() -> None:
    """`aclitem` 문자 순서가 그대로 나오면 같은 권한이 다른 SQL이 된다(변이 X7)."""

    rows = _sec("{kosa=arwdDxt/kosa,kosa_app=war/kosa}")
    grant = next(
        s
        for s in v5.restore_security_statements(rows, mode="successor")
        if "kosa_app" in s
    )
    assert grant.startswith("GRANT INSERT, SELECT, UPDATE ON public.")


def test_apply_plan_checks_the_statement_sequence(monkeypatch: Any) -> None:
    """실물 SQL은 늘 맞으므로 음성 입력으로는 이 호출을 볼 수 없다(변이 X9)."""

    seen: list[str] = []
    original = v5.assert_statement_sequence

    def spy(text: str, *, route: str) -> None:
        seen.append(route)
        original(text, route=route)

    monkeypatch.setattr(v5, "assert_statement_sequence", spy)
    v5.apply_plan(route="successor", profile="runtime", security=_sec())
    assert seen == ["successor"]


def _body(route: str = "successor") -> list[str]:
    return list(_plan(route)[len(v5.SESSION_PROLOGUE) :])


def _replan(body: list[str]) -> tuple[str, ...]:
    return (*v5.SESSION_PROLOGUE, *body)


def test_a_gap_between_share_locks_is_refused() -> None:
    """`SHARE`가 흩어지면 잠그기 전에 읽는 구간이 생긴다(변이 X13).

    **연속성만** 깨뜨린다. 마지막 `SHARE`를 뒤로 옮기면 "모든 `SHARE`가 배타 lock보다
    앞" 검사가 같은 reason code로 먼저 잡아 이 검사가 가려진다.
    """

    body = _body()
    shares = [i for i, s in enumerate(body) if "IN SHARE MODE" in s]
    body.insert(shares[1], "SELECT 1")
    still = [i for i, s in enumerate(body) if "IN SHARE MODE" in s]
    exclusive = min(i for i, s in enumerate(body) if "ACCESS EXCLUSIVE" in s)
    assert max(still) < exclusive, "다른 검사가 가리지 않는 입력이어야 한다"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_plan_invariants(_replan(body), route="successor")
    assert caught.value.reason_code == "PLAN_ORDER_MISMATCH"


def test_a_grant_before_the_drop_is_refused() -> None:
    """재생성 전에 복원하면 곧바로 지워진다(변이 X14)."""

    body = _body()
    grant = body.pop(next(i for i, s in enumerate(body) if s.startswith("ALTER ")))
    body.insert(next(i for i, s in enumerate(body) if "ACCESS EXCLUSIVE" in s), grant)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_plan_invariants(_replan(body), route="successor")
    assert caught.value.reason_code == "PLAN_ORDER_MISMATCH"


def test_dropping_the_table_before_the_view_is_refused() -> None:
    """View가 R03를 참조하므로 순서가 뒤집히면 실제로도 막힌다(변이 X15)."""

    body = _body()
    drops = [i for i, s in enumerate(body) if s.lstrip().upper().startswith("DROP ")]
    body[drops[0]], body[drops[1]] = body[drops[1]], body[drops[0]]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_plan_invariants(_replan(body), route="successor")
    assert caught.value.reason_code == "PLAN_ORDER_MISMATCH"


def test_a_stray_view_comment_in_the_live_catalog_is_refused() -> None:
    """live 값을 쓰지 않고 상수를 쓰면 붙은 comment를 못 본다(변이 X21)."""

    live = _live()
    live["security"][v5.ALARM_VIEW]["comment"] = "누가 붙였다"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.live_signatures(live, mode="successor")
    assert caught.value.reason_code == "COMMENT_NOT_CANONICAL"


def test_plan_assembly_refuses_a_forbidden_object_in_the_sql(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """순서만 보면 `FROM trace_alarm_history`를 `FROM document`로 바꿔도 통과한다.

    operation/target 열이 그대로이기 때문이다(구현리뷰 9차 필수 2).
    """

    tampered = tmp_path / "001_reference_extensions_final.sql"
    original = v5.CANONICAL_SQL.read_text(encoding="utf-8")
    tampered.write_text(
        original.replace("FROM trace_alarm_history", "FROM document", 1),
        encoding="utf-8",
    )
    assert tampered.read_text(encoding="utf-8") != original
    monkeypatch.setattr(v5, "CANONICAL_SQL", tampered)
    # 순서 계약만으로는 통과한다는 것을 먼저 보인다.
    v5.assert_statement_sequence(
        tampered.read_text(encoding="utf-8"), route="canonical"
    )
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.apply_plan(route="canonical", profile="runtime", security={})
    assert caught.value.reason_code == "SQL_SCOPE_VIOLATION"


def test_a_plan_missing_share_locks_is_refused() -> None:
    """조각 검사로는 '빠진 것'을 잡을 수 없다(구현리뷰 9차 필수 2)."""

    plan = list(_plan("successor"))
    shares = [i for i, s in enumerate(plan) if "IN SHARE MODE" in s]
    trimmed = [s for i, s in enumerate(plan) if i not in set(shares[1:])]
    # 순서 규칙만 보는 판정은 이걸 통과시킨다.
    v5.assert_plan_invariants(trimmed, route="successor")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_plan_shape(
            trimmed, route="successor", profile="runtime", security=_sec()
        )
    assert caught.value.reason_code == "PLAN_ORDER_MISMATCH"


def test_the_expected_plan_matches_itself() -> None:
    for route, profile in (("successor", "runtime"), ("canonical", "evaluation")):
        security = _sec() if route == "successor" else {}
        plan = v5.apply_plan(route=route, profile=profile, security=security)
        v5.assert_plan_shape(plan, route=route, profile=profile, security=security)
        v5.assert_plan_invariants(plan, route=route)


def test_a_plan_from_another_profile_is_refused() -> None:
    """보존 projection은 runtime **12** · evaluation **3**이다. 섞이면 안 된다.

    구 등록 manifest 23/14에서 base 9·R03·legacy handoff를 뺀 값이다
    (구현리뷰 19차 권장 1 — 전에는 13/4로 적혀 있었다).
    """

    plan = _plan("successor", profile="runtime")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_plan_shape(
            plan, route="successor", profile="evaluation", security=_sec()
        )
    assert caught.value.reason_code == "PLAN_ORDER_MISMATCH"


# ---------------------------------------------------------------------------
# 파괴적 drop 앞의 exact pre-state (구현리뷰 9차 필수 3)
# ---------------------------------------------------------------------------


def _v4_constraint_rows() -> list[dict[str, Any]]:
    """구 R03 constraint catalog 행. **definition까지** 담는다.

    개수만 맞추면 전부 `CHECK (false)`인 catalog도 exact V4로 취급해 drop한다
    (구현리뷰 10차 필수 3).
    """

    return [
        {"conname": name, "contype": kind, "definition": definition}
        for name, (kind, definition) in v5.V4_R03_CONSTRAINT_DEFINITIONS.items()
    ]


def _pre_state(**overrides: Any) -> dict[str, Any]:
    payload = {
        "r03_columns": _v4_rows(),
        "r03_constraints": _v4_constraint_rows(),
        "view_definition": _compat_view_definition(),
        "view_security": {"owner": "kosa", "acl": _ACL},
        "r03_rows": 0,
        "dependents": [],
    }
    payload.update(overrides)
    return payload


def test_the_v4_column_contract_matches_the_frozen_migration() -> None:
    """상수와 구 001 DDL이 따로 놀면 pre-state 판정이 무의미해진다."""

    ddl = (
        REPOSITORY_ROOT / "backend" / "migrations" / "001_reference_extensions.sql"
    ).read_text(encoding="utf-8")
    body = ddl.split("CREATE TABLE r03_alarm_history (", 1)[1].split(");", 1)[0]
    for name, _type, length, nullable in v5.V4_R03_COLUMN_CONTRACT:
        assert name in body
        assert not nullable
        if length is not None:
            assert f"varchar({length})" in body
    assert [n for n, _t, _l, _x in v5.V4_R03_COLUMN_CONTRACT] == v5.V4_R03_COLUMNS


def test_the_cm26_pre_state_passes() -> None:
    v5.assert_successor_pre_state(**_pre_state())


def test_a_type_swapped_v4_table_is_not_the_compat_state() -> None:
    """이름만 보면 모든 컬럼이 `integer NULL`이어도 통과했다(구현리뷰 9차 필수 3)."""

    broken = [
        {**row, "data_type": "integer", "character_maximum_length": None}
        for row in _v4_rows()
    ]
    assert (
        v5.classify_schema_state(
            r03_present=True,
            view_present=True,
            r03_columns=broken,
            view_definition=_compat_view_definition(),
        )
        == "PARTIAL_OR_DRIFT"
    )
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(**_pre_state(r03_columns=broken))
    assert caught.value.reason_code == "PRE_STATE_COLUMNS_MISMATCH"


def test_a_nullable_v4_column_is_refused() -> None:
    rows = _v4_rows()
    rows[3]["is_nullable"] = "YES"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(**_pre_state(r03_columns=rows))
    assert caught.value.reason_code == "PRE_STATE_COLUMNS_MISMATCH"


def test_a_shortened_varchar_is_refused() -> None:
    rows = _v4_rows()
    rows[0]["character_maximum_length"] = 12
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(**_pre_state(r03_columns=rows))
    assert caught.value.reason_code == "PRE_STATE_COLUMNS_MISMATCH"


def test_a_missing_v4_constraint_is_refused() -> None:
    """구 001은 PK 1 · unique 1 · FK 2 · CHECK 3이다."""

    rows = _v4_constraint_rows()[:-1]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(**_pre_state(r03_constraints=rows))
    assert caught.value.reason_code == "PRE_STATE_CONSTRAINTS_MISMATCH"


def test_a_non_compat_view_is_refused_before_the_drop() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(
            **_pre_state(view_definition=_canonical_view_definition())
        )
    assert caught.value.reason_code == "PRE_STATE_VIEW_MISMATCH"


def test_a_view_without_acl_is_refused_before_the_drop() -> None:
    """CM-2.6이 실제로 만난 상태다. 복원할 값이 없는 채로 지우면 안 된다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(
            **_pre_state(view_security={"owner": "kosa", "acl": None})
        )
    assert caught.value.reason_code == "SECURITY_ACL_MISSING"


@pytest.mark.parametrize("rows", [1, 3, True])
def test_a_non_empty_v4_table_is_refused(rows: Any) -> None:
    """행이 있으면 내용을 추측하지 않는다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(**_pre_state(r03_rows=rows))
    assert caught.value.reason_code == "PRE_STATE_NOT_EMPTY"


def test_an_unexpected_dependent_is_refused() -> None:
    """`DROP VIEW`가 무엇을 함께 끊을지 모르는 상태다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(
            **_pre_state(dependents=[{"relname": "some_report", "relkind": "v"}])
        )
    assert caught.value.reason_code == "PRE_STATE_DEPENDENTS_PRESENT"


def test_the_three_targets_must_share_one_route() -> None:
    same = dict.fromkeys(v5.PUBLIC_TARGETS, "successor")
    assert v5.assert_targets_share_one_route(same) == "successor"


def test_a_route_drift_across_targets_is_refused() -> None:
    """하나만 다른 상태면 순서대로 적용하다 중간에서 멈춘다. 시작 전에 안다."""

    drifted = dict.fromkeys(v5.PUBLIC_TARGETS, "successor")
    drifted["kosa_text2sql"] = "canonical"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_targets_share_one_route(drifted)
    assert caught.value.reason_code == "TARGET_ROUTE_DRIFT"


def test_a_partial_target_set_has_no_route_verdict() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_targets_share_one_route({"kosa_agent": "successor"})
    assert caught.value.reason_code == "SECURITY_TARGET_SET_MISMATCH"


def test_an_unknown_route_across_targets_is_refused() -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_targets_share_one_route(dict.fromkeys(v5.PUBLIC_TARGETS, "whatever"))
    assert caught.value.reason_code == "MIGRATION_ROUTE_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# CLI 진입점 (계획 §7.1 · 구현리뷰 9차 필수 1)
# ---------------------------------------------------------------------------


class _FakeConnection:
    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database
        self.closed = 0

    def commit(self) -> None:
        self.database.commit()

    def rollback(self) -> None:
        self.database.rollback()

    def close(self) -> None:
        self.closed += 1


def _cli(*argv: str, database: Any = None) -> tuple[int, str, str]:
    """CLI를 **실제 handler까지** 돌린다. session만 가짜다.

    이전에는 인자 판정만 하고 `MODE_ACCEPTED`를 찍고 끝났는데, 그러면 아무 일도 하지
    않는 mode가 exit 0이 된다(구현리뷰 10차 필수 1).
    """

    import contextlib
    import io

    fake = database if database is not None else _FakeDatabase()
    connection = _FakeConnection(fake)

    def opener(name: str) -> tuple[Any, Any]:
        return connection, fake

    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
        code = v5.main(list(argv), opener=opener)
    return code, stderr.getvalue().strip(), stdout.getvalue().strip()


class _DriverError(RuntimeError):
    """SQLSTATE를 달고 오는 driver 예외 흉내."""

    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def _cli_raising(error: BaseException, *, on_open: bool = False) -> tuple[int, str]:
    """`open_session` 또는 handler에서 계약 밖 예외가 났을 때의 CLI 표면."""

    import contextlib
    import io

    fake = _FakeDatabase(state="compat")
    connection = _FakeConnection(fake)

    def opener(name: str) -> tuple[Any, Any]:
        if on_open:
            raise error
        return connection, fake

    def failing(sql: str, params: Any = None) -> Any:
        raise error

    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
        code = v5.main(
            [
                "--preflight",
                "--database",
                "kosa_agent",
                "--confirm-target",
                "kosa_agent",
            ],
            opener=(lambda name: (connection, failing)) if not on_open else opener,
        )
    return code, stderr.getvalue().strip()


def test_an_unexpected_driver_error_never_becomes_a_traceback() -> None:
    """계약 밖 예외가 reason code로 나온다 — Gate 0가 실제 공용 DB에서 잡았다.

    `main()`이 `ReferenceV5Error`만 잡던 동안 `UndefinedTable`이 그대로 나가
    traceback에 **로컬 절대경로**가 찍혔다(Gate 0 조사 §5).
    """

    code, err = _cli_raising(
        _DriverError('relation "public.document_corpus" does not exist', "42P01")
    )
    assert code == v5.EXIT_MISMATCH
    assert err == f"{v5.UNEXPECTED_REASON} 42P01"


def test_an_unexpected_error_without_a_sqlstate_reports_its_type() -> None:
    code, err = _cli_raising(_DriverError("무언가 잘못됐다"))
    assert code == v5.EXIT_MISMATCH
    assert err == f"{v5.UNEXPECTED_REASON} _DriverError"


def test_a_connection_failure_does_not_leak_the_endpoint() -> None:
    """연결 실패 메시지에는 host·port가 들어간다. **그대로 새면 안 된다.**"""

    code, err = _cli_raising(
        _DriverError(
            'connection to server at "db.example.org" (10.0.0.9), port 55432 failed:'
            ' password authentication failed for user "kosa"',
            "08006",
        ),
        on_open=True,
    )
    assert code == v5.EXIT_MISMATCH
    assert err == f"{v5.UNEXPECTED_REASON} 08006"
    for secret in ("db.example.org", "10.0.0.9", "55432", "password", 'kosa"'):
        assert secret not in err, secret


@pytest.mark.parametrize(
    "polluted",
    [
        "08006 host=db.example.org password=hunter2",
        "08006 user=kosa port=55432",
        "55p03",
        "ABCDEF",
        "1234",
        " 08006",
        "08006\npassword=hunter2",
    ],
)
def test_a_malformed_sqlstate_never_reaches_stderr(polluted: str) -> None:
    """**5자 영숫자만 출력한다**(구현리뷰 17차 필수 1).

    전에는 driver가 준 non-empty 문자열을 그대로 믿었다. wrapper가 오염된 속성을 주면
    누출을 막으려 만든 코드가 누출 경로가 됐다.
    """

    code, err = _cli_raising(_DriverError("무언가", polluted))
    assert code == v5.EXIT_MISMATCH
    assert err == f"{v5.UNEXPECTED_REASON} _DriverError"
    for secret in ("db.example.org", "hunter2", "55432", "password", "user=", "host="):
        assert secret not in err, secret


@pytest.mark.parametrize("attribute", ["orig", "__cause__"])
def test_a_malformed_sqlstate_is_dropped_through_wrappers(attribute: str) -> None:
    """`.orig`·`__cause__`로 감싸 들어와도 같다."""

    inner = _DriverError("안쪽", "08006 password=hunter2")
    outer = RuntimeError("바깥")
    if attribute == "orig":
        outer.orig = inner  # type: ignore[attr-defined]
    else:
        outer.__cause__ = inner
    assert v5._sqlstate(outer) is None
    assert v5._safe_detail(outer) == "RuntimeError"


def test_a_valid_sqlstate_still_passes_through() -> None:
    """정상 코드는 그대로 나온다 — 검증이 기능을 죽이지 않는다."""

    code, err = _cli_raising(_DriverError("없는 table", "42P01"))
    assert err == f"{v5.UNEXPECTED_REASON} 42P01"
    assert code == v5.EXIT_MISMATCH


def test_lock_classification_survives_the_sqlstate_validation() -> None:
    """`55P03`·`40P01` 분류는 그대로다(구현리뷰 13차 필수 1 회귀 유지)."""

    for state in v5.LOCK_CONTENTION_SQLSTATES:
        assert v5.is_lock_contention(_DriverError("x", state))
    assert not v5.is_lock_contention(_DriverError("x", "55P03 evil"))
    assert not v5.is_lock_contention(_DriverError("x", "42P01"))


def test_an_untrustworthy_exception_name_falls_back_to_a_literal() -> None:
    """동적 class 이름도 그대로 믿지 않는다."""

    hostile = type("Bad name password=hunter2", (RuntimeError,), {})
    assert v5._safe_detail(hostile("x")) == v5.UNKNOWN_DETAIL


def test_a_contract_error_still_wins_over_the_unexpected_handler() -> None:
    """`ReferenceV5Error`는 그대로 자기 reason code·exit code를 쓴다."""

    code, err = _cli_raising(
        v5.ReferenceV5Error("TARGET_BUSY", v5.EXIT_CONFIRM_REQUIRED)
    )
    assert code == v5.EXIT_CONFIRM_REQUIRED
    assert err.splitlines()[0] == "TARGET_BUSY"


def test_the_unexpected_handler_reports_only_a_code(tmp_path: Path) -> None:
    """`report_unexpected()`가 예외 메시지를 절대 찍지 않는다."""

    import contextlib
    import io

    stderr = io.StringIO()
    error = _DriverError(f"공백 경로 {tmp_path} 와 비밀 pw=hunter2", "23505")
    with contextlib.redirect_stderr(stderr):
        assert v5.report_unexpected(error) == v5.EXIT_MISMATCH
    out = stderr.getvalue().strip()
    assert out == f"{v5.UNEXPECTED_REASON} 23505"
    assert str(tmp_path) not in out
    assert "hunter2" not in out


def test_the_cli_apply_actually_applies(tmp_path: Path) -> None:
    """mode가 실제 handler에 연결돼야 한다(구현리뷰 10차 필수 1).

    최종 dataset 행 수를 갖춘 가짜 DB를 쓴다 — 비-final 우회는 `kosa_agent_e2e`의
    `rehearse`에만 열려 있다(구현리뷰 11차 필수 2).
    """

    import json

    fake = _FakeDatabase()
    code, _err, out = _cli(
        "--apply",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        "--change-ref",
        "GH-110",
        "--artifact-root",
        str(tmp_path),
        database=fake,
    )
    assert code == v5.EXIT_OK
    payload = json.loads(out)
    assert payload["mode"] == "apply"
    assert payload["status"] == "COMMITTED"
    # 실제로 DDL을 돌리고 marker를 남긴다.
    assert fake.committed == 1
    assert [s for s in fake.seen if s.lstrip().upper().startswith("DROP ")]
    assert (tmp_path / v5.marker_name("kosa_agent", "GH-110")).is_file()


def test_the_cli_refuses_two_modes() -> None:
    code, err, _out = _cli(
        "--apply",
        "--verify",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
    )
    assert (code, err) == (v5.EXIT_USAGE, "CLI_MODE_CONFLICT")


def test_the_cli_refuses_no_mode() -> None:
    code, err, _out = _cli("--database", "kosa_agent", "--confirm-target", "kosa_agent")
    assert (code, err) == (v5.EXIT_USAGE, "CLI_MODE_CONFLICT")


def test_the_cli_refuses_an_unknown_database() -> None:
    code, err, _out = _cli(
        "--verify", "--database", "postgres", "--confirm-target", "postgres"
    )
    assert (code, err) == (v5.EXIT_USAGE, "TARGET_NOT_ALLOWED")


def test_the_cli_requires_confirmation() -> None:
    code, err, _out = _cli("--verify", "--database", "kosa_agent")
    assert (code, err) == (v5.EXIT_CONFIRM_REQUIRED, "TARGET_CONFIRM_REQUIRED")


@pytest.mark.parametrize("mode", ["--apply", "--rehearse"])
def test_a_mutating_mode_requires_a_change_ref(mode: str) -> None:
    code, err, _out = _cli(
        mode, "--database", "kosa_agent", "--confirm-target", "kosa_agent"
    )
    assert (code, err) == (v5.EXIT_USAGE, "CHANGE_REF_REQUIRED")


def test_a_mutating_mode_requires_an_artifact_root() -> None:
    code, err, _out = _cli(
        "--apply",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        "--change-ref",
        "GH-110",
    )
    assert (code, err) == (v5.EXIT_USAGE, "ARTIFACT_ROOT_REQUIRED")


def test_the_cli_refuses_the_registry_directory_as_artifact_root() -> None:
    """marker를 등록 manifest 경로에 쓰면 full verifier가 집어든다."""

    import manifest_v3

    code, err, _out = _cli(
        "--apply",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        "--change-ref",
        "GH-110",
        "--artifact-root",
        str(manifest_v3.MANIFEST_REGISTRY_ROOT),
    )
    assert (code, err) == (v5.EXIT_USAGE, "CONTRACT_BOUNDARY_LEAK")


def test_a_path_traversing_change_ref_is_refused(tmp_path: Path) -> None:
    """artifact 이름에 들어가는 값이다."""

    code, err, _out = _cli(
        "--apply",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        "--change-ref",
        "../../etc/passwd",
        "--artifact-root",
        str(tmp_path),
    )
    assert (code, err) == (v5.EXIT_USAGE, "ARTIFACT_NAME_UNSAFE")


def test_verify_needs_neither_change_ref_nor_root() -> None:
    """읽기 mode는 쓰기 인자를 요구하지 않는다. 대신 실제로 검증한다."""

    import json

    # `kosa_text2sql`은 evaluation profile이라 action 12행이 최종 계약이다.
    fake = _FakeDatabase(state="final", action_rows=12)
    code, _err, out = _cli(
        "--verify",
        "--database",
        "kosa_text2sql",
        "--confirm-target",
        "kosa_text2sql",
        database=fake,
    )
    assert code == v5.EXIT_OK
    payload = json.loads(out)
    assert payload["state"] == "V5_REFERENCE_FINAL"
    assert payload["noop"] is False  # marker를 주지 않았다
    assert fake.committed == 0


def test_verify_refuses_a_non_final_target() -> None:
    """final이 아닌 DB에 `verify`를 부르면 성공이 아니다."""

    code, err, _out = _cli(
        "--verify",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        database=_FakeDatabase(state="V4_REFERENCE_COMPAT"),
    )
    assert (code, err) == (v5.EXIT_CONFIRM_REQUIRED, "TARGET_STATE_UNSUPPORTED")


def test_the_cli_preflight_reads_without_writing() -> None:
    import json

    fake = _FakeDatabase()
    code, _err, out = _cli(
        "--preflight",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        database=fake,
    )
    assert code == v5.EXIT_OK
    payload = json.loads(out)
    assert payload["state"] == "V4_REFERENCE_COMPAT"
    assert payload["route"] == "successor"
    assert fake.committed == 0
    assert not [s for s in fake.seen if s.lstrip().upper().startswith("DROP ")]


def test_rehearse_through_the_cli_leaves_no_marker(tmp_path: Path) -> None:
    import json

    fake = _FakeDatabase()
    code, _err, out = _cli(
        "--rehearse",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        "--change-ref",
        "GH-110",
        "--artifact-root",
        str(tmp_path),
        database=fake,
    )
    assert code == v5.EXIT_OK
    assert json.loads(out)["status"] == "ABORTED"
    assert fake.committed == 0
    assert fake.rolled_back == 1
    assert not (tmp_path / v5.marker_name("kosa_agent", "GH-110")).exists()


def test_rehearse_is_a_known_mode() -> None:
    assert "rehearse" in v5.CLI_MODES
    assert v5.assert_single_mode(["rehearse"]) == "rehearse"


# ---------------------------------------------------------------------------
# artifact 저장 (구현리뷰 9차 필수 4)
# ---------------------------------------------------------------------------


def test_an_artifact_is_written_atomically_and_privately(tmp_path: Path) -> None:
    path = tmp_path / "nested" / v5.marker_name("kosa_agent", "GH-110")
    digest = v5.write_artifact(path, _marker())
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == v5.ARTIFACT_FILE_MODE
    assert path.parent.stat().st_mode & 0o777 == v5.ARTIFACT_DIR_MODE
    assert len(digest) == 64
    # 임시 파일이 남지 않는다.
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    assert v5.read_artifact(path) == _marker()


def test_an_artifact_never_carries_a_secret(tmp_path: Path) -> None:
    payload = {**_marker(), "change_ref": "postgresql://user:pw@host/db"}
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.write_artifact(tmp_path / "m.json", payload)
    assert caught.value.reason_code == "CONTRACT_SENSITIVE_VALUE"
    assert not (tmp_path / "m.json").exists()


def test_a_broken_artifact_is_a_typed_failure(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.read_artifact(path)
    assert caught.value.reason_code == "ARTIFACT_UNREADABLE"

    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.read_artifact(path)
    assert caught.value.reason_code == "ARTIFACT_UNREADABLE"


@pytest.mark.parametrize("value", ["../escape", "a/b", "", ".hidden"])
def test_an_unsafe_artifact_name_component_is_refused(value: str) -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.receipt_name("kosa_agent", value)
    assert caught.value.reason_code == "ARTIFACT_NAME_UNSAFE"


def test_receipt_and_marker_names_differ() -> None:
    assert v5.receipt_name("kosa_agent", "GH-110") != v5.marker_name(
        "kosa_agent", "GH-110"
    )
    assert "kosa_agent" in v5.marker_name("kosa_agent", "GH-110")


# ---------------------------------------------------------------------------
# runner lifecycle을 driver 없이 (구현리뷰 9차 필수 1)
# ---------------------------------------------------------------------------


class _FakeDatabase:
    """`execute` 계약만 만족하는 가짜 DB.

    `apply_to_target()`이 무엇을 어떤 순서로 실행하는지 DB 없이 본다. container 회귀는
    실제 PostgreSQL에서 같은 경로를 돌린다.
    """

    def __init__(self, **overrides: Any) -> None:
        self.seen: list[str] = []
        self.committed = 0
        self.rolled_back = 0
        self.state = overrides.pop("state", "V4_REFERENCE_COMPAT")
        self.excluded = overrides.pop("excluded", ["e" * 64, "e" * 64])
        self.overrides = overrides

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def __call__(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        self.seen.append(sql)
        if "CREATE TABLE" in sql and v5.R03_TABLE in sql:
            # DDL이 돌면 catalog가 final이 된다. 그 뒤 조회는 final을 돌려준다.
            self.state = "final"
        if sql == v5.ADVISORY_UNLOCK_SQL:
            return [{"pg_advisory_unlock": self.overrides.get("unlock", True)}]
        if sql == v5.ADVISORY_LOCK_SQL:
            return [{"pg_try_advisory_lock": self.overrides.get("lock", True)}]
        if sql == v5.ISOLATION_SQL:
            return [
                {
                    "transaction_isolation": self.overrides.get(
                        "isolation", v5.EXPECTED_ISOLATION
                    )
                }
            ]
        if sql == v5.RELATION_PRESENCE_SQL:
            if self.state == "BASE_ONLY":
                return []
            return [
                {"relname": v5.R03_TABLE, "relkind": "r"},
                {"relname": v5.ALARM_VIEW, "relkind": "v"},
            ]
        if sql == v5.R03_COLUMNS_SQL:
            return _v5_rows() if self.state == "final" else _v4_rows()
        if sql == v5.R03_CONSTRAINTS_SQL:
            return (
                _constraint_rows() if self.state == "final" else _v4_constraint_rows()
            )
        if sql == v5.VIEW_COLUMNS_SQL:
            return _view_rows()
        if sql == v5.VIEW_DEFINITION_SQL:
            definition = (
                _canonical_view_definition()
                if self.state in {"final", "applied"}
                else _compat_view_definition()
            )
            return [{"definition": definition}]
        if sql == v5.RELATION_SECURITY_SQL:
            return [
                {
                    "relname": name,
                    "owner": "kosa",
                    "acl": self.overrides.get("acl", _ACL),
                    "comment": v5.R03_COMMENT if name == v5.R03_TABLE else None,
                }
                for name in v5.OWNED_BY_CM31
            ]
        if sql == v5.EXCLUDED_CONSTRAINTS_SQL:
            return [
                {
                    "relname": "lot_history",
                    "conname": "lot_history_pkey",
                    "contype": "p",
                    "definition": "PRIMARY KEY (lot_hist_id)",
                }
            ]
        if sql == v5.EXCLUDED_INDEXES_SQL:
            return [
                {
                    "relname": "lot_history",
                    "indexname": "lot_history_pkey",
                    "indexdef": "CREATE UNIQUE INDEX lot_history_pkey ON lot_history",
                }
            ]
        if sql.startswith("SELECT md5("):
            return [{"digest": "0" * 32}]
        if sql == v5.EXCLUDED_COLUMNS_SQL:
            return [
                {
                    "table_name": "lot_history",
                    "column_name": "lot_hist_id",
                    "data_type": "character varying",
                    "is_nullable": "NO",
                }
            ]
        if sql == v5.DEPENDENTS_SQL:
            return list(self.overrides.get("dependents", []))
        if sql == v5.VIEW_BRANCH_SQL:
            return [
                {"source": "TRACE", "n": 138, "null_owner": 0},
                {"source": "SUMMARY", "n": 51, "null_owner": 0},
            ]
        if sql == v5.VIEW_DUPLICATE_SQL:
            return [{"n": 0}]
        if sql.startswith("SELECT count(*)"):
            if v5.ALARM_VIEW in sql:
                return [{"n": 189}]
            if v5.R03_TABLE in sql:
                return [{"n": 0}]
            if "action_history" in sql:
                return [{"n": self.overrides.get("action_rows", 0)}]
            return [{"n": 0}]
        return []


def _apply(database: _FakeDatabase, root: Path, **kwargs: Any) -> Any:
    return v5.apply_to_target(
        database,
        database=kwargs.pop("target", "kosa_agent"),
        confirm_target=kwargs.pop("confirm_target", "kosa_agent"),
        change_ref=kwargs.pop("change_ref", "GH-110"),
        artifact_root=root,
        commit=database.commit,
        rollback=database.rollback,
        **kwargs,
    )


def _fake_excluded(monkeypatch: Any, values: list[str]) -> None:
    """excluded fingerprint는 실측 함수라 가짜 DB에서 값을 정해준다."""

    pending = list(values)

    def stub(execute: Any, *, profile: str) -> str:
        return pending.pop(0) if pending else values[-1]

    monkeypatch.setattr(v5, "excluded_projection_sha256", stub)


def test_the_runner_takes_the_advisory_lock_before_anything(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """lock을 늦게 잡으면 두 실행이 같은 DB에서 겹친다(변이 Y22)."""

    fake = _FakeDatabase()
    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])
    _apply(fake, tmp_path)
    assert fake.seen[0] == v5.ADVISORY_LOCK_SQL
    assert fake.seen[1] == v5.ISOLATION_SQL
    assert fake.seen[-1] == v5.ADVISORY_UNLOCK_SQL


def test_the_runner_stops_on_an_unexpected_isolation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """isolation이 다르면 이미 잘못된 transaction 안이다(변이 Y23)."""

    fake = _FakeDatabase(isolation="repeatable read")
    _fake_excluded(monkeypatch, ["e" * 64])
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _apply(fake, tmp_path)
    assert caught.value.reason_code == "ISOLATION_UNEXPECTED"
    assert fake.committed == 0
    assert fake.rolled_back == 1


def test_the_runner_verifies_the_pre_state_before_dropping(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """dependent가 있으면 `DROP VIEW`가 무엇을 끊을지 모른다(변이 Y25)."""

    fake = _FakeDatabase(dependents=[{"relname": "report", "relkind": "v"}])
    _fake_excluded(monkeypatch, ["e" * 64])
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _apply(fake, tmp_path)
    assert caught.value.reason_code == "PRE_STATE_DEPENDENTS_PRESENT"
    assert not [s for s in fake.seen if s.lstrip().upper().startswith("DROP ")]
    assert fake.committed == 0


def test_the_runner_refuses_a_changed_excluded_projection(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """CM-3.1은 base 9·Runtime·RAG·D를 건드리지 않는다(변이 Y24)."""

    fake = _FakeDatabase()
    _fake_excluded(monkeypatch, ["e" * 64, "f" * 64])
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _apply(fake, tmp_path)
    assert caught.value.reason_code == "EXCLUDED_PROJECTION_MISMATCH"
    assert fake.committed == 0
    assert fake.rolled_back == 1


def test_rehearse_rolls_back_and_leaves_no_marker(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """실제 DDL을 돌려보되 아무것도 남기지 않는다(변이 Y27)."""

    fake = _FakeDatabase()
    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])
    result = _apply(fake, tmp_path, dry_run=True)
    assert result["status"] == "ABORTED"
    assert fake.committed == 0
    assert fake.rolled_back == 1
    assert not (tmp_path / v5.marker_name("kosa_agent", "GH-110")).exists()
    # DDL은 실제로 돌았다.
    assert [s for s in fake.seen if s.lstrip().upper().startswith("DROP ")]


def test_the_runner_writes_marker_last_and_validates_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """commit·postcheck·receipt 저장이 모두 끝난 뒤에만 marker를 쓴다(변이 Y26)."""

    fake = _FakeDatabase()
    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])
    seen: list[Any] = []
    original = v5.assert_marker_contract
    monkeypatch.setattr(
        v5,
        "assert_marker_contract",
        lambda payload: (seen.append(payload), original(payload))[1],
    )
    marker = _apply(fake, tmp_path)
    assert fake.committed == 1
    assert len(seen) == 1
    assert marker["status"] == "COMMITTED"
    assert (tmp_path / v5.marker_name("kosa_agent", "GH-110")).is_file()


def test_the_runner_checks_the_view_branches(tmp_path: Path, monkeypatch: Any) -> None:
    """branch 합·중복 판정을 실제로 부른다(변이 Y28)."""

    fake = _FakeDatabase()
    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])
    seen: list[int] = []
    original = v5.assert_view_branches

    def spy(execute: Any, **kwargs: Any) -> None:
        seen.append(kwargs["view_rows"])
        original(execute, **kwargs)

    monkeypatch.setattr(v5, "assert_view_branches", spy)
    _apply(fake, tmp_path)
    assert seen == [189]


def test_a_failed_run_leaves_an_aborted_receipt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    fake = _FakeDatabase(dependents=[{"relname": "report", "relkind": "v"}])
    _fake_excluded(monkeypatch, ["e" * 64])
    with pytest.raises(v5.ReferenceV5Error):
        _apply(fake, tmp_path)
    receipt = v5.read_artifact(tmp_path / v5.receipt_name("kosa_agent", "GH-110"))
    assert receipt["status"] == "ABORTED"
    for key in v5.POST_COMMIT_IDENTITY_KEYS:
        assert receipt[key] is None
    assert not (tmp_path / v5.marker_name("kosa_agent", "GH-110")).exists()


def test_an_unfinished_receipt_may_not_carry_identity() -> None:
    """자리만 비워두면 나중에 아무 값이나 채워도 계약이 모른다(변이 Y14)."""

    payload = _receipt(schema_signature_sha256="a" * 64)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(payload)
    assert caught.value.reason_code == "RECEIPT_IDENTITY_UNEXPECTED"


@pytest.mark.parametrize("key", list(v5.POST_COMMIT_IDENTITY_KEYS))
def test_a_committed_receipt_needs_a_real_hash(key: str) -> None:
    """`NOT-A-SHA`도 통과했다(구현리뷰 9차 필수 4 · 변이 Y15)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_receipt_contract(_committed_receipt(**{key: "NOT-A-SHA"}))
    assert caught.value.reason_code == "ARTIFACT_HASH_MALFORMED"


@pytest.mark.parametrize("key", list(v5.POST_COMMIT_IDENTITY_KEYS))
def test_a_marker_needs_a_real_hash(key: str) -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_marker_contract(_marker(**{key: "G" * 64}))
    assert caught.value.reason_code == "ARTIFACT_HASH_MALFORMED"


@pytest.mark.parametrize(
    ("key", "reason"),
    [
        ("schema_signature_sha256", "SCHEMA_SIGNATURE_MISMATCH"),
        ("security_signature_sha256", "SECURITY_SIGNATURE_MISMATCH"),
        ("excluded_projection_sha256", "EXCLUDED_PROJECTION_MISMATCH"),
    ],
)
def test_recovery_compares_each_identity_separately(key: str, reason: str) -> None:
    """셋 중 하나만 달라도 잡아야 한다(변이 Y17)."""

    receipt = _committed_receipt()
    live = {
        "schema_signature": receipt["schema_signature_sha256"],
        "security_signature": receipt["security_signature_sha256"],
        "excluded_projection": receipt["excluded_projection_sha256"],
    }
    live[key.replace("_sha256", "").replace("schema_signature", "schema_signature")] = (
        "d" * 64
    )
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_recovery_is_allowed(
            receipt,
            schema_signature=live["schema_signature"],
            security_signature=live["security_signature"],
            excluded_projection=live["excluded_projection"],
            view_definition_sha256_value=v5.CANONICAL_VIEW_SHA256,
        )
    assert caught.value.reason_code == reason


def test_the_artifact_file_is_private_even_without_chmod(tmp_path: Path) -> None:
    """`mkstemp`가 이미 0600을 준다.

    그래도 명시한다 — umask에 기대지 않는다(변이 Y20).
    """

    import inspect

    source = inspect.getsource(v5.write_artifact)
    assert "os.chmod(temporary, ARTIFACT_FILE_MODE)" in source
    assert "os.replace(" in source, "os.rename은 Windows에서 기존 파일을 못 덮는다"
    path = tmp_path / "m.json"
    v5.write_artifact(path, _marker())
    assert path.stat().st_mode & 0o777 == v5.ARTIFACT_FILE_MODE


# ---------------------------------------------------------------------------
# 10차 필수 보완 회귀
# ---------------------------------------------------------------------------


def test_the_v4_constraint_contract_is_definition_exact() -> None:
    """개수만 보면 전부 `CHECK (false)`인 catalog도 exact V4가 된다(필수 3)."""

    rows = [
        {"conname": name, "contype": kind, "definition": "CHECK (false)"}
        for name, (kind, _definition) in v5.V4_R03_CONSTRAINT_DEFINITIONS.items()
    ]
    from collections import Counter

    assert Counter(r["contype"] for r in rows) == dict(v5.V4_R03_CONSTRAINT_COUNTS)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(**_pre_state(r03_constraints=rows))
    assert caught.value.reason_code == "PRE_STATE_CONSTRAINTS_MISMATCH"


def test_a_renamed_v4_constraint_is_refused() -> None:
    rows = _v4_constraint_rows()
    rows[0] = {**rows[0], "conname": "somebody_elses_pkey"}
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(**_pre_state(r03_constraints=rows))
    assert caught.value.reason_code == "PRE_STATE_CONSTRAINTS_MISMATCH"


def test_a_user_trigger_stops_the_successor() -> None:
    """`DROP TABLE`이 함께 지워버린다(계획 §6.5 · 필수 3)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_successor_pre_state(
            **_pre_state(triggers=[{"relname": v5.R03_TABLE, "tgname": "audit_trg"}])
        )
    assert caught.value.reason_code == "PRE_STATE_TRIGGERS_PRESENT"


def test_the_trigger_query_ignores_internal_triggers() -> None:
    """FK가 만드는 internal trigger까지 잡으면 정상 DB가 멈춘다."""

    assert "NOT t.tgisinternal" in v5.TRIGGERS_SQL
    assert "nspname = 'public'" in v5.TRIGGERS_SQL


def _branch_execute(rows: list[dict[str, Any]], duplicates: int = 0) -> Any:
    def run(sql: str, params: Any = None) -> list[dict[str, Any]]:
        if sql == v5.VIEW_BRANCH_SQL:
            return rows
        if sql == v5.VIEW_DUPLICATE_SQL:
            return [{"n": duplicates}]
        return []

    return run


def test_the_final_branch_distribution_is_exact() -> None:
    """합계만 보면 `TRACE 137 / SUMMARY 52`도 통과한다(필수 4)."""

    shifted = [
        {"source": "TRACE", "n": 137, "null_owner": 0},
        {"source": "SUMMARY", "n": 52, "null_owner": 0},
    ]
    execute = _branch_execute(shifted)
    # 구조 검사만으로는 통과한다.
    v5.assert_view_branches(
        execute, r03_rows=0, view_rows=189, require_final_dataset=False
    )
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_view_branches(execute, r03_rows=0, view_rows=189)
    assert caught.value.reason_code == "VIEW_BRANCH_MISMATCH"


def test_the_documented_branch_rows_are_used() -> None:
    good = [
        {"source": "TRACE", "n": v5.BRANCH_ROWS_EMPTY["TRACE"], "null_owner": 0},
        {"source": "SUMMARY", "n": v5.BRANCH_ROWS_EMPTY["SUMMARY"], "null_owner": 0},
    ]
    v5.assert_view_branches(_branch_execute(good), r03_rows=0, view_rows=189)


def test_the_populated_phase_expects_three_r03_rows() -> None:
    good = [
        {"source": "TRACE", "n": 138, "null_owner": 0},
        {"source": "SUMMARY", "n": 51, "null_owner": 0},
        {"source": "R03", "n": 3, "null_owner": 0},
    ]
    v5.assert_view_branches(_branch_execute(good), r03_rows=3, view_rows=192)
    bad = [*good[:2], {"source": "R03", "n": 2, "null_owner": 0}]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_view_branches(_branch_execute(bad), r03_rows=3, view_rows=191)
    assert caught.value.reason_code == "VIEW_BRANCH_MISMATCH"


def test_a_missing_branch_is_refused() -> None:
    only_trace = [{"source": "TRACE", "n": 189, "null_owner": 0}]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_view_branches(_branch_execute(only_trace), r03_rows=0, view_rows=189)
    assert caught.value.reason_code == "VIEW_BRANCH_MISMATCH"


def test_the_excluded_fingerprint_covers_more_than_shape() -> None:
    """행 수를 유지한 값 변경·constraint 제거·owner 변경을 잡아야 한다(필수 5)."""

    import inspect

    source = inspect.getsource(v5.excluded_projection_sha256)
    for fragment in ("constraints", "indexes", "security", "base_content"):
        assert f'"{fragment}"' in source, fragment
    assert "EXCLUDED_CONTENT_SQL" in source


def test_the_content_digest_covers_base_nine_only() -> None:
    """보존 Runtime·RAG·D는 운영 중 값이 바뀐다. CM-3.1이 막을 일이 아니다."""

    import inspect

    source = inspect.getsource(v5.excluded_projection_sha256)
    assert "if name in BASE_TABLE_NAMES:" in source


def test_a_failed_commit_is_an_unknown_outcome(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """**commit 실패를 "DB 미변경"으로 단정하지 않는다**(구현리뷰 12차 필수 5).

    COMMIT은 성공했는데 응답이 유실됐을 수 있다. `ABORTED`를 쓰면 DB는 final인데
    artifact는 재적용 대상으로 표시된다.
    """

    fake = _FakeDatabase()
    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])

    def boom() -> None:
        raise RuntimeError("commit 응답 유실")

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.apply_to_target(
            fake,
            database="kosa_agent",
            confirm_target="kosa_agent",
            change_ref="GH-110",
            artifact_root=tmp_path,
            commit=boom,
            rollback=fake.rollback,
        )
    assert caught.value.reason_code == "COMMIT_OUTCOME_UNKNOWN"
    assert caught.value.exit_code == v5.EXIT_CONFIRM_REQUIRED
    # lock은 놓았고 receipt는 `STARTED` 그대로다 — 사람이 `--verify`로 결정한다.
    assert fake.seen[-1] == v5.ADVISORY_UNLOCK_SQL
    receipt = v5.read_artifact(tmp_path / v5.receipt_name("kosa_agent", "GH-110"))
    assert receipt["status"] == "STARTED"


def _flaky_write(monkeypatch: Any, fail_on: int) -> None:
    """`write_artifact()`의 n번째 호출만 실패시킨다.

    1 = STARTED receipt, 2 = COMMITTED receipt, 3 = marker.
    """

    calls = {"n": 0}
    original = v5.write_artifact

    def flaky(path: Path, payload: Any) -> str:
        calls["n"] += 1
        if calls["n"] == fail_on:
            raise OSError("디스크 가득 참")
        return original(path, payload)

    monkeypatch.setattr(v5, "write_artifact", flaky)


def test_a_committed_receipt_write_failure_leaves_started(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """receipt가 `STARTED`로 남는다 → 승격 후 marker(구현리뷰 14차 필수 1)."""

    fake = _FakeDatabase()
    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])
    _flaky_write(monkeypatch, fail_on=2)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _apply(fake, tmp_path)
    assert caught.value.reason_code == "RECEIPT_WRITE_FAILED"
    assert fake.committed == 1
    assert fake.seen[-1] == v5.ADVISORY_UNLOCK_SQL
    stored = v5.read_artifact(tmp_path / v5.receipt_name("kosa_agent", "GH-110"))
    assert stored["status"] == "STARTED"
    assert not (tmp_path / v5.marker_name("kosa_agent", "GH-110")).exists()


def test_a_marker_write_failure_leaves_a_committed_receipt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """receipt는 `COMMITTED`다.

    **승격하면 안 되고** 바로 marker 복구다(구현리뷰 14차 필수 1).
    """

    fake = _FakeDatabase()
    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])
    _flaky_write(monkeypatch, fail_on=3)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _apply(fake, tmp_path)
    assert caught.value.reason_code == "MARKER_WRITE_FAILED"
    stored = v5.read_artifact(tmp_path / v5.receipt_name("kosa_agent", "GH-110"))
    assert stored["status"] == "COMMITTED"
    assert not (tmp_path / v5.marker_name("kosa_agent", "GH-110")).exists()


def test_the_runbook_matches_the_receipt_state(tmp_path: Path) -> None:
    """하나의 안내를 항상 내면 상태에 따라 그대로 실행했을 때 실패한다.

    substring이 아니라 **argv의 mode flag**로 고정한다(구현리뷰 15차 필수 1).
    """

    def modes(receipt_status: str | None) -> list[str]:
        return [
            argv[2]
            for argv in v5.recovery_commands(
                database="kosa_agent",
                change_ref="GH-110",
                artifact_root=tmp_path,
                receipt_status=receipt_status,
            )
        ]

    assert modes("COMMITTED") == ["--recover-marker"]
    # **standalone verify를 선행 필수로 두지 않는다** — route를 모른 채 실행하면
    # base-only target에서 실패한다(구현리뷰 14차 필수 1).
    assert modes("STARTED") == ["--promote-receipt", "--recover-marker"]
    assert modes(None) == ["--verify"]
    assert modes(v5.RECEIPT_UNTRUSTED) == ["--verify"]

    started = v5.recovery_commands(
        database="kosa_agent",
        change_ref="GH-110",
        artifact_root=tmp_path,
        receipt_status="STARTED",
    )
    assert "--confirm-recovery" in started[0]
    assert "--confirm-recovery" not in started[1]
    # receipt 없음 안내는 change ref·artifact root를 요구하지 않는 mode다.
    missing = v5.recovery_commands(
        database="kosa_agent",
        change_ref="GH-110",
        artifact_root=tmp_path,
        receipt_status=None,
    )[0]
    assert "--artifact-root" not in missing and "--change-ref" not in missing


def test_the_runbook_commands_are_executable() -> None:
    """**실제로 실행되는 명령인지 본다.** substring 검사가 아니다.

    전에는 `apply_reference_extensions_v5 --promote-receipt ...`를 냈다. 그 이름은
    `PATH`에 없고 이 파일에는 shebang도 실행 권한도 없어 그대로는 안 돌았다
    (구현리뷰 15차 필수 1). 지금은 argv[0]이 지금 interpreter다.
    """

    import shlex
    import subprocess

    commands = v5.recovery_commands(
        database="kosa_agent",
        change_ref="GH-110",
        artifact_root=Path("/tmp/artifacts"),
        receipt_status="STARTED",
    )
    assert [argv[2] for argv in commands] == ["--promote-receipt", "--recover-marker"]

    lines = v5.recovery_runbook(
        database="kosa_agent",
        change_ref="GH-110",
        artifact_root=Path("/tmp/artifacts"),
        receipt_status="STARTED",
    )
    assert lines[1:] == tuple(v5.format_command(argv) for argv in commands)

    parser = v5.build_parser()
    for argv, display in zip(commands, lines[1:], strict=True):
        assert argv[0] == sys.executable
        assert Path(argv[1]) == v5.RECOVERY_SCRIPT
        assert v5.RECOVERY_SCRIPT.is_file()
        # 표시 문자열이 argv 하나하나로 정확히 되돌아온다.
        assert shlex.split(display) == list(argv)

        # **진짜 parser가 받는다.**
        args = parser.parse_args(list(argv[2:]))
        assert v5.selected_modes(args) == [argv[2].removeprefix("--")]
        assert args.database == "kosa_agent"
        assert args.confirm_target == "kosa_agent"
        assert args.change_ref == "GH-110"
        assert args.artifact_root == "/tmp/artifacts"
    assert parser.parse_args(list(commands[0][2:])).confirm_recovery is True

    # **진입점이 실제로 뜨는지 subprocess로 확인한다.**
    done = subprocess.run(
        [*v5.recovery_entrypoint(), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "apply_reference_extensions_v5" in done.stdout
    for flag in ("--promote-receipt", "--recover-marker", "--confirm-recovery"):
        assert flag in done.stdout, flag

    # **exit 계약까지 본다.** allowlist 밖 target은 session을 열기 전에 거절되므로
    # DB 없이도 안전하게 진입점→parser→검증→exit code 경로를 통과시킨다.
    safe = list(commands[0])
    safe[safe.index("kosa_agent")] = "kosa_not_allowed"
    safe[safe.index("kosa_agent")] = "kosa_not_allowed"
    refused = subprocess.run(safe, capture_output=True, text=True, timeout=60)
    assert refused.returncode == v5.EXIT_USAGE
    assert refused.stderr.strip() == "TARGET_NOT_ALLOWED"


def test_the_runbook_round_trips_spaces_and_shell_metacharacters() -> None:
    """공백·특수문자가 든 경로와 change ref가 **인자 하나로** 되돌아온다."""

    import shlex

    root = Path("/tmp/artifacts with space/$(whoami);rm -rf")
    commands = v5.recovery_commands(
        database="kosa_agent",
        change_ref="GH-110 review&deploy",
        artifact_root=root,
        receipt_status="STARTED",
    )
    parser = v5.build_parser()
    for argv in commands:
        display = v5.format_command(argv)
        assert shlex.split(display) == list(argv)
        args = parser.parse_args(shlex.split(display)[2:])
        assert args.artifact_root == str(root)
        assert args.change_ref == "GH-110 review&deploy"


@pytest.mark.windows_contract
def test_the_runbook_uses_windows_quoting_on_windows(monkeypatch: Any) -> None:
    """Windows에서는 `list2cmdline` 규칙으로 직렬화한다."""

    import os
    import subprocess

    argv = v5.recovery_commands(
        database="kosa_agent",
        change_ref="GH-110",
        artifact_root=Path(r"C:\artifacts with space"),
        receipt_status="COMMITTED",
    )[0]
    monkeypatch.setattr(os, "name", "nt")
    display = v5.format_command(argv)
    assert display == subprocess.list2cmdline(list(argv))
    assert '"C:\\artifacts with space"' in display


def test_the_displayed_command_survives_a_posix_shell(tmp_path: Path) -> None:
    """표시된 **한 줄을 그대로 shell에 넣어도** 특수문자가 실행되지 않는다.

    argv 검증만으로는 "복사해서 붙여넣는" 실제 사용을 덮지 못한다. allowlist 밖
    target이라 DB session을 열기 전에 끝나므로 안전하다(구현리뷰 16차 권장 1).
    """

    import os
    import subprocess

    if os.name == "nt":
        pytest.skip("POSIX shell 전용")

    sentinel = tmp_path / "pwned"
    argv = v5.recovery_commands(
        database="kosa_not_allowed",
        change_ref="GH-110",
        artifact_root=tmp_path / f"artifacts with space $(touch {sentinel})",
        receipt_status="STARTED",
    )[0]
    done = subprocess.run(
        ["/bin/sh", "-c", v5.format_command(argv)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == v5.EXIT_USAGE
    assert done.stderr.strip() == "TARGET_NOT_ALLOWED"
    assert not sentinel.exists(), "artifact 경로가 shell에서 실행됐다"

    # **이 검사가 힘을 갖는지 같이 고정한다.** 15차 이전의 f-string 보간 방식이면
    # 같은 경로가 실제로 실행된다 — `;&`처럼 sh 구문 오류를 내는 조각을 쓰면
    # 아무것도 실행되지 않아 검사가 조용히 무력해진다.
    sentinel.unlink(missing_ok=True)
    subprocess.run(["/bin/sh", "-c", " ".join(argv)], capture_output=True, timeout=60)
    assert sentinel.exists(), "주입 문자열이 보간 방식에서도 실행되지 않는다"
    sentinel.unlink()


@pytest.mark.windows_contract
def test_the_displayed_command_survives_cmd_exe(tmp_path: Path) -> None:
    """Windows에서도 표시된 한 줄을 `cmd.exe`에 그대로 넣어 확인한다.

    `list2cmdline()`은 MSVCRT argv 규칙의 역이고 `cmd.exe`는 그 위에서 인용 안의
    `&`를 literal로 다룬다. PowerShell 5.1은 native 인자 전달 규칙이 달라 여기서
    다루지 않는다(구현리뷰 16차 권장 1).
    """

    import os
    import subprocess

    if os.name != "nt":
        pytest.skip("실제 Windows shell에서만 의미가 있다")

    sentinel = tmp_path / "pwned.txt"
    argv = v5.recovery_commands(
        database="kosa_not_allowed",
        change_ref="GH-110",
        artifact_root=tmp_path / f"artifacts with space & echo x> {sentinel}",
        receipt_status="STARTED",
    )[0]
    done = subprocess.run(
        v5.format_command(argv),
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == v5.EXIT_USAGE
    assert done.stderr.strip().splitlines()[-1] == "TARGET_NOT_ALLOWED"
    assert not sentinel.exists(), "artifact 경로가 cmd.exe에서 실행됐다"

    # POSIX 쪽과 같은 이유로 이 검사의 힘도 함께 고정한다. 인용이 없으면 `cmd.exe`가
    # `&`에서 명령을 끊어 뒤쪽 `echo`가 실제로 돈다.
    subprocess.run(" ".join(argv), shell=True, capture_output=True, timeout=120)
    assert sentinel.exists(), "주입 문자열이 보간 방식에서도 실행되지 않는다"


def test_a_receipt_that_fails_its_contract_is_not_used_as_state(tmp_path: Path) -> None:
    """손상된 receipt로 절차를 고르지 않는다(구현리뷰 15차 권장 1)."""

    path = tmp_path / v5.receipt_name("kosa_agent", "GH-110")
    v5.write_artifact(path, _receipt(status="STARTED", route="nonsense"))
    assert (
        v5.read_receipt_status(tmp_path, "kosa_agent", "GH-110") == v5.RECEIPT_UNTRUSTED
    )
    lines = v5.recovery_runbook(
        database="kosa_agent",
        change_ref="GH-110",
        artifact_root=tmp_path,
        receipt_status=v5.RECEIPT_UNTRUSTED,
    )
    assert not any("--promote-receipt" in line for line in lines)
    assert any("--verify" in line for line in lines)


def test_another_targets_receipt_is_not_used_as_state(tmp_path: Path) -> None:
    """같은 파일명에 다른 target의 receipt가 놓여도 그 상태를 믿지 않는다."""

    path = tmp_path / v5.receipt_name("kosa_agent", "GH-110")
    v5.write_artifact(path, _receipt(database="kosa_agent_e2e", profile="runtime"))
    assert (
        v5.read_receipt_status(tmp_path, "kosa_agent", "GH-110") == v5.RECEIPT_UNTRUSTED
    )


def test_a_valid_receipt_is_still_read(tmp_path: Path) -> None:
    """계약·identity를 통과한 receipt는 그대로 상태 근거다."""

    _started_receipt(tmp_path)
    assert v5.read_receipt_status(tmp_path, "kosa_agent", "GH-110") == "STARTED"


@pytest.mark.parametrize(
    ("fail_on", "reason", "expected"),
    [
        (2, "RECEIPT_WRITE_FAILED", "--promote-receipt"),
        (3, "MARKER_WRITE_FAILED", "--recover-marker"),
    ],
)
def test_the_cli_prints_the_matching_runbook(
    tmp_path: Path, monkeypatch: Any, fail_on: int, reason: str, expected: str
) -> None:
    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])
    _flaky_write(monkeypatch, fail_on=fail_on)
    code, err, _out = _cli(
        "--apply",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        "--change-ref",
        "GH-110",
        "--artifact-root",
        str(tmp_path),
    )
    assert code == v5.EXIT_CONFIRM_REQUIRED
    assert reason in err
    assert expected in err
    if reason == "MARKER_WRITE_FAILED":
        assert "--promote-receipt" not in err


def test_the_cli_prints_a_runbook_on_an_unknown_commit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class _CommitBreaks(_FakeDatabase):
        def commit(self) -> None:
            raise RuntimeError("commit 응답 유실")

    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])
    code, err, _out = _cli(
        "--apply",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        "--change-ref",
        "GH-110",
        "--artifact-root",
        str(tmp_path),
        database=_CommitBreaks(),
    )
    assert code == v5.EXIT_CONFIRM_REQUIRED
    assert "COMMIT_OUTCOME_UNKNOWN" in err
    assert "--promote-receipt --confirm-recovery" in err


def test_an_unlock_failure_does_not_mask_the_original_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """unlock 오류가 원래 예외를 가리면 무엇이 실패했는지 알 수 없다."""

    class _UnlockBreaks(_FakeDatabase):
        def __call__(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
            if sql == v5.ADVISORY_UNLOCK_SQL:
                self.seen.append(sql)
                raise RuntimeError("unlock 실패")
            return super().__call__(sql, params)

    fake = _UnlockBreaks(isolation="repeatable read")
    _fake_excluded(monkeypatch, ["e" * 64])
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _apply(fake, tmp_path)
    assert caught.value.reason_code == "ISOLATION_UNEXPECTED"
    assert fake.seen[-1] == v5.ADVISORY_UNLOCK_SQL


def test_the_pre_state_is_rechecked_after_the_exclusive_lock() -> None:
    """lock 대기 중 바뀐 schema를 다시 본다(필수 3)."""

    import inspect

    source = inspect.getsource(v5._apply_locked)
    assert "ACCESS EXCLUSIVE" in source
    assert "read_successor_pre_state(execute)" in source


def test_the_directory_fsync_is_posix_only() -> None:
    """Windows에서는 디렉터리 open/fsync가 POSIX와 같지 않다(권장 1)."""

    import inspect

    source = inspect.getsource(v5.write_artifact)
    assert 'os.name == "posix"' in source
    assert "os.replace(" in source


def test_a_null_owner_key_is_refused_on_the_final_distribution() -> None:
    """분포는 맞고 owner만 안 풀린 상태 — 이 검사만 걸린다."""

    rows = [
        {"source": "TRACE", "n": 138, "null_owner": 1},
        {"source": "SUMMARY", "n": 51, "null_owner": 0},
    ]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_view_branches(_branch_execute(rows), r03_rows=0, view_rows=189)
    assert caught.value.reason_code == "VIEW_NULL_OWNER_KEY"


def test_a_duplicate_alarm_ref_is_refused() -> None:
    rows = [
        {"source": "TRACE", "n": 138, "null_owner": 0},
        {"source": "SUMMARY", "n": 51, "null_owner": 0},
    ]
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_view_branches(
            _branch_execute(rows, duplicates=2), r03_rows=0, view_rows=189
        )
    assert caught.value.reason_code == "VIEW_ALARM_REF_DUPLICATE"


# ---------------------------------------------------------------------------
# 11차 필수 보완 회귀
# ---------------------------------------------------------------------------


def test_the_production_adapter_uses_the_real_target_api() -> None:
    """`BootstrapTarget`에는 URL 필드가 없다. 전에는 첫 연결 전에 죽었다(필수 1)."""

    import inspect

    import db_target

    assert not hasattr(db_target.BootstrapTarget, "url")
    source = inspect.getsource(v5.open_session)
    assert "target.create_url()" in source
    assert "validate_url_components" in source
    assert "engine.dispose()" in source


def test_a_bad_environment_becomes_a_typed_reason(monkeypatch: Any) -> None:
    """`TargetValidationError`가 traceback으로 새면 안 된다(필수 1)."""

    import db_target

    def boom(**kwargs: Any) -> Any:
        raise db_target.TargetValidationError("환경 변수가 없다")

    monkeypatch.setattr(db_target, "load_bootstrap_target", boom)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.open_session("kosa_agent")
    assert caught.value.reason_code == "TARGET_ENV_INVALID"
    assert caught.value.exit_code == v5.EXIT_USAGE


def test_the_session_owner_closes_both(monkeypatch: Any) -> None:
    closed: list[str] = []

    class _Conn:
        def close(self) -> None:
            closed.append("connection")

    class _Engine:
        def dispose(self) -> None:
            closed.append("engine")

    v5._SessionOwner(_Conn(), _Engine()).close()
    assert closed == ["connection", "engine"]


@pytest.mark.parametrize(
    ("database", "mode"),
    [
        ("kosa_agent", "rehearse"),
        ("kosa_agent_e2e", "apply"),
        ("kosa_text2sql", "verify"),
    ],
)
def test_the_non_final_bypass_is_refused_elsewhere(database: str, mode: str) -> None:
    """공용 apply에서 189/192·null owner·138/51을 끌 수 있었다(필수 2)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.assert_non_final_dataset_allowed(database=database, mode=mode)
    assert caught.value.reason_code == "NON_FINAL_DATASET_NOT_ALLOWED"


def test_the_non_final_bypass_is_only_e2e_rehearse() -> None:
    v5.assert_non_final_dataset_allowed(database="kosa_agent_e2e", mode="rehearse")
    assert v5.NON_FINAL_DATASET_TARGET == "kosa_agent_e2e"
    assert v5.NON_FINAL_DATASET_MODE == "rehearse"


def test_the_cli_refuses_the_bypass_on_a_public_apply(tmp_path: Path) -> None:
    code, err, _out = _cli(
        "--apply",
        "--database",
        "kosa_agent",
        "--confirm-target",
        "kosa_agent",
        "--change-ref",
        "GH-110",
        "--artifact-root",
        str(tmp_path),
        "--allow-non-final-dataset",
    )
    assert (code, err) == (v5.EXIT_USAGE, "NON_FINAL_DATASET_NOT_ALLOWED")


def test_the_runner_refuses_the_bypass_when_called_directly(tmp_path: Path) -> None:
    """CLI를 거치지 않고 불러도 같은 제한을 받는다(필수 2)."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.apply_to_target(
            _FakeDatabase(),
            database="kosa_agent",
            confirm_target="kosa_agent",
            change_ref="GH-110",
            artifact_root=tmp_path,
            commit=lambda: None,
            rollback=lambda: None,
            require_final_dataset=False,
        )
    assert caught.value.reason_code == "NON_FINAL_DATASET_NOT_ALLOWED"


def test_the_security_mode_follows_the_route() -> None:
    """base-only는 `relacl IS NULL`이 정상이다(필수 3)."""

    assert v5._security_mode_for("successor") == "successor"
    assert v5._security_mode_for("canonical") == "base_only"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5._security_mode_for("whatever")
    assert caught.value.reason_code == "MIGRATION_ROUTE_NOT_ALLOWED"


def test_verify_of_a_canonical_target_uses_base_only_mode() -> None:
    """`successor`로 고정하면 base-only target이 실패한다(필수 3)."""

    fake = _FakeDatabase(state="final", acl=None)
    result = v5.verify_target(
        fake,
        database="kosa_agent",
        confirm_target="kosa_agent",
        route="canonical",
        require_final_dataset=False,
    )
    assert result["route"] == "canonical"
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.verify_target(
            _FakeDatabase(state="final", acl=None),
            database="kosa_agent",
            confirm_target="kosa_agent",
            route="successor",
            require_final_dataset=False,
        )
    assert caught.value.reason_code == "SECURITY_ACL_MISSING"


def test_verify_takes_share_locks_before_reading() -> None:
    """검사 중 live가 바뀌면 판정이 무의미하다(필수 3)."""

    fake = _FakeDatabase(state="final")
    v5.verify_target(
        fake,
        database="kosa_agent",
        confirm_target="kosa_agent",
        require_final_dataset=False,
    )
    assert [s for s in fake.seen if "IN SHARE MODE" in s]


def test_recover_marker_runs_the_full_postcheck() -> None:
    """schema/security/excluded만 보면 잘못된 DB도 복구 성공이 된다(필수 3)."""

    import inspect

    source = inspect.getsource(v5.recover_marker)
    assert "verify_target(" in source


def test_the_unlock_result_is_checked() -> None:
    """`pg_advisory_unlock()`이 `False`면 lock을 갖고 있지 않았다는 뜻이다(필수 4)."""

    assert v5._release_lock(_FakeDatabase(), {"namespace": 1, "key": 2}) is True
    assert v5._release_lock(
        _FakeDatabase(unlock=False), {"namespace": 1, "key": 2}
    ) is (False)


def test_a_success_path_unlock_failure_is_not_hidden(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """lock이 남은 채 COMMITTED를 돌려주면 다음 실행이 이유 없이 막힌다(필수 4)."""

    fake = _FakeDatabase(unlock=False)
    _fake_excluded(monkeypatch, ["e" * 64, "e" * 64])
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _apply(fake, tmp_path)
    assert caught.value.reason_code == "ADVISORY_UNLOCK_FAILED"
    assert fake.committed == 1


# ---------------------------------------------------------------------------
# 12차 필수 보완 회귀
# ---------------------------------------------------------------------------


def test_the_module_never_writes_outside_its_owned_objects() -> None:
    """`COMMENT ON SCHEMA public`은 CM-3.1 소유 밖이다(구현리뷰 12차 필수 1)."""

    source = (
        REPOSITORY_ROOT / "backend" / "scripts" / "apply_reference_extensions_v5.py"
    ).read_text(encoding="utf-8")
    # 실행 statement에 없어야 한다. 왜 안 쓰는지는 주석에 남아 있다.
    executable = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "COMMENT ON SCHEMA" not in executable
    assert "COMMIT_RECORD" not in executable
    assert not hasattr(v5, "recover_receipt")
    assert "recover-receipt" not in v5.CLI_MODES


def test_the_advisory_lock_never_waits() -> None:
    """blocking `pg_advisory_lock()`은 무제한 대기한다(구현리뷰 12차 필수 2)."""

    assert "pg_try_advisory_lock" in v5.ADVISORY_LOCK_SQL
    assert "pg_advisory_lock(" not in v5.ADVISORY_LOCK_SQL


def test_a_busy_target_stops_with_zero_writes(tmp_path: Path) -> None:
    """다른 실행이 잡고 있으면 쓰기 0으로 끝난다(계획 §10)."""

    fake = _FakeDatabase(lock=False)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _apply(fake, tmp_path)
    assert caught.value.reason_code == "TARGET_BUSY"
    assert caught.value.exit_code == v5.EXIT_CONFIRM_REQUIRED
    assert fake.committed == 0
    assert not [s for s in fake.seen if s.lstrip().upper().startswith("DROP ")]
    assert not list(tmp_path.iterdir())


def test_verify_locks_with_nowait_and_leaves_no_session_state() -> None:
    """`SET`으로 timeout을 걸면 caller connection에 남는다(구현리뷰 13차 필수 1)."""

    fake = _FakeDatabase(state="final")
    v5.verify_target(
        fake,
        database="kosa_agent",
        confirm_target="kosa_agent",
        require_final_dataset=False,
    )
    locks = [sql for sql in fake.seen if "IN SHARE MODE" in sql]
    assert locks and all(sql.endswith("NOWAIT") for sql in locks)
    assert not [sql for sql in fake.seen if sql.startswith("SET ")]


class _LockError(RuntimeError):
    """driver 예외 흉내. SQLSTATE로 분류된다."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _locking(sqlstate: str) -> Any:
    class _Busy(_FakeDatabase):
        def __call__(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
            if "IN SHARE MODE" in sql:
                raise _LockError(sqlstate)
            return super().__call__(sql, params)

    return _Busy(state="final")


@pytest.mark.parametrize("sqlstate", ["55P03", "40P01"])
def test_a_lock_contention_reports_target_busy(sqlstate: str) -> None:
    """`lock_not_available`·`deadlock_detected`만 잠금 경쟁이다."""

    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.verify_target(
            _locking(sqlstate),
            database="kosa_agent",
            confirm_target="kosa_agent",
            require_final_dataset=False,
        )
    assert caught.value.reason_code == "TARGET_BUSY"


@pytest.mark.parametrize(
    ("sqlstate", "why"),
    [
        ("42501", "권한 없음"),
        ("42P01", "table 없음"),
        ("08006", "연결 끊김"),
        ("42601", "SQL 오류"),
    ],
)
def test_a_non_lock_error_is_not_disguised_as_busy(sqlstate: str, why: str) -> None:
    """모든 예외를 `TARGET_BUSY`로 바꾸면 원인이 재시도 대상으로 위장된다(필수 1)."""

    with pytest.raises(_LockError) as caught:
        v5.verify_target(
            _locking(sqlstate),
            database="kosa_agent",
            confirm_target="kosa_agent",
            require_final_dataset=False,
        )
    assert caught.value.sqlstate == sqlstate, why


def test_an_exception_without_a_sqlstate_is_not_busy() -> None:
    """분류할 수 없으면 잠금 경쟁으로 단정하지 않는다."""

    class _Plain(_FakeDatabase):
        def __call__(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
            if "IN SHARE MODE" in sql:
                raise RuntimeError("무엇인지 모른다")
            return super().__call__(sql, params)

    with pytest.raises(RuntimeError):
        v5.verify_target(
            _Plain(state="final"),
            database="kosa_agent",
            confirm_target="kosa_agent",
            require_final_dataset=False,
        )


def test_the_sqlstate_is_read_through_a_wrapper() -> None:
    """SQLAlchemy는 driver 예외를 `.orig`로 감싼다."""

    class _Wrapped(RuntimeError):
        def __init__(self) -> None:
            super().__init__("wrapped")
            self.orig = _LockError("55P03")

    assert v5.is_lock_contention(_Wrapped()) is True
    assert v5.is_lock_contention(RuntimeError("plain")) is False


# ---------------------------------------------------------------------------
# platform 경계 (구현리뷰 12차 권장 1)
# ---------------------------------------------------------------------------


@pytest.mark.windows_contract
def test_an_artifact_write_survives_the_platform(tmp_path: Path) -> None:
    """`mkstemp → fsync → chmod → os.replace`가 실제로 도는지 본다.

    디렉터리 fsync는 POSIX에서만 하고, 0600도 POSIX 의미다 — Windows ACL은 같지 않다.
    그래도 **쓰기·덮어쓰기 자체**는 양쪽에서 동작해야 한다(구현리뷰 12차 권장 1).
    """

    import os

    path = tmp_path / "nested" / v5.marker_name("kosa_agent", "GH-110")
    first = v5.write_artifact(path, _marker())
    assert path.is_file()
    assert v5.read_artifact(path)["status"] == "COMMITTED"

    # 기존 파일 덮어쓰기 — Windows에서 `os.rename`이면 여기서 실패한다.
    second = v5.write_artifact(path, _marker(view_rows=192, r03_rows=3))
    assert second != first
    assert v5.read_artifact(path)["r03_rows"] == 3
    # 임시 파일이 남지 않는다.
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == v5.ARTIFACT_FILE_MODE


@pytest.mark.windows_contract
def test_the_artifact_name_contract_holds_on_every_platform() -> None:
    """경로 조각 검사는 platform과 무관하다."""

    assert v5.receipt_name("kosa_agent", "GH-110").endswith(".json")
    for bad in ("../escape", "a/b", "a\\b"):
        with pytest.raises(v5.ReferenceV5Error) as caught:
            v5.receipt_name("kosa_agent", bad)
        assert caught.value.reason_code == "ARTIFACT_NAME_UNSAFE"


# ---------------------------------------------------------------------------
# artifact-only 복구 경로 (구현리뷰 13차 필수 2)
# ---------------------------------------------------------------------------


def _started_receipt(tmp_path: Path, **overrides: Any) -> Path:
    path = tmp_path / v5.receipt_name("kosa_agent", "GH-110")
    v5.write_artifact(path, _receipt(**overrides))
    return path


def _promote(tmp_path: Path, database: Any = None, **kwargs: Any) -> Any:
    return v5.promote_receipt(
        database if database is not None else _FakeDatabase(state="final"),
        database=kwargs.pop("target", "kosa_agent"),
        confirm_target=kwargs.pop("confirm_target", "kosa_agent"),
        change_ref=kwargs.pop("change_ref", "GH-110"),
        artifact_root=tmp_path,
        confirm_recovery=kwargs.pop("confirm_recovery", True),
        require_final_dataset=kwargs.pop("require_final_dataset", False),
    )


def test_a_started_receipt_is_promoted_after_a_full_verify(tmp_path: Path) -> None:
    """commit 응답 유실 뒤 유일한 복구 경로다(구현리뷰 13차 필수 2)."""

    _started_receipt(tmp_path)
    promoted = _promote(tmp_path)
    assert promoted["status"] == "COMMITTED"
    for key in v5.POST_COMMIT_IDENTITY_KEYS:
        assert v5._SHA256.fullmatch(promoted[key])
    # 파일에도 반영된다 — 이어서 `recover-marker`가 받을 수 있다.
    stored = v5.read_artifact(tmp_path / v5.receipt_name("kosa_agent", "GH-110"))
    assert stored["status"] == "COMMITTED"
    v5.assert_receipt_contract(stored)


def test_promotion_needs_an_explicit_confirmation(tmp_path: Path) -> None:
    """자동으로 승격하지 않는다."""

    _started_receipt(tmp_path)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _promote(tmp_path, confirm_recovery=False)
    assert caught.value.reason_code == "RECOVERY_CONFIRM_REQUIRED"
    assert (
        v5.read_artifact(tmp_path / v5.receipt_name("kosa_agent", "GH-110"))["status"]
        == "STARTED"
    )


def test_promotion_refuses_when_live_is_not_final(tmp_path: Path) -> None:
    """live가 final이 아니면 재적용 대상이다."""

    _started_receipt(tmp_path)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _promote(tmp_path, database=_FakeDatabase(state="V4_REFERENCE_COMPAT"))
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_promotion_refuses_a_missing_receipt(tmp_path: Path) -> None:
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _promote(tmp_path)
    assert caught.value.reason_code == "RECEIPT_MISSING"


def test_promotion_refuses_an_already_committed_receipt(tmp_path: Path) -> None:
    v5.write_artifact(
        tmp_path / v5.receipt_name("kosa_agent", "GH-110"), _committed_receipt()
    )
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _promote(tmp_path)
    assert caught.value.reason_code == "RECEIPT_STATUS_NOT_ALLOWED"


def test_promotion_refuses_a_bundle_drift(tmp_path: Path) -> None:
    """SQL이 바뀐 뒤의 receipt를 승격하면 안 된다."""

    _started_receipt(tmp_path, migration_bundle_sha256="0" * 64)
    with pytest.raises(v5.ReferenceV5Error) as caught:
        _promote(tmp_path)
    assert caught.value.reason_code == "MIGRATION_BUNDLE_STALE"


def test_promotion_refuses_a_target_mismatch(tmp_path: Path) -> None:
    path = tmp_path / v5.receipt_name("kosa_agent_e2e", "GH-110")
    v5.write_artifact(path, _receipt(database="kosa_agent_e2e", profile="runtime"))
    with pytest.raises(v5.ReferenceV5Error) as caught:
        v5.promote_receipt(
            _FakeDatabase(state="final"),
            database="kosa_agent",
            confirm_target="kosa_agent",
            change_ref="GH-110",
            artifact_root=tmp_path,
            confirm_recovery=True,
            require_final_dataset=False,
        )
    assert caught.value.reason_code == "RECEIPT_MISSING"


def test_promote_receipt_is_a_cli_mode() -> None:
    assert "promote-receipt" in v5.CLI_MODES
    assert v5.assert_single_mode(["promote-receipt"]) == "promote-receipt"


def test_the_module_still_writes_no_db_record() -> None:
    """복구 경로가 생겼다고 DB object를 늘리지 않는다(구현리뷰 12차 필수 1)."""

    source = (
        REPOSITORY_ROOT / "backend" / "scripts" / "apply_reference_extensions_v5.py"
    ).read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "COMMENT ON SCHEMA" not in executable


# ---------------------------------------------------------------------------
# blocker gate — 각 profile의 final 계약을 직접 실측한다
# ---------------------------------------------------------------------------


def _swap_stage(monkeypatch, key, contract) -> None:
    """`BOOTSTRAP_STAGE_CONTRACTS` 한 항목만 바꾸고 원복은 monkeypatch가 한다."""

    import manifest_v3

    replaced = dict(manifest_v3.BOOTSTRAP_STAGE_CONTRACTS)
    if contract is None:
        replaced.pop(key, None)
    else:
        replaced[key] = contract
    monkeypatch.setattr(manifest_v3, "BOOTSTRAP_STAGE_CONTRACTS", replaced)


def test_the_final_stage_table_matches_the_registry() -> None:
    """`FINAL_STAGE_BY_PROFILE`이 실제 등록 key와 같아야 한다."""

    import manifest_v3

    for profile, stage in v5.FINAL_STAGE_BY_PROFILE.items():
        assert (profile, stage) in manifest_v3.BOOTSTRAP_STAGE_CONTRACTS
    assert set(v5.FINAL_STAGE_BY_PROFILE) == set(v5.PROFILE_MIGRATIONS)


def test_removing_the_final_evaluation_stage_reopens_both_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**stage가 통째로 없어도 blocker가 닫혔다**(구현리뷰 4차 필수 2).

    구 계약은 "구 `evaluation_mock`이 존재하면 blocker"라 둘 다 없으면 통과했다.
    """

    _swap_stage(monkeypatch, ("evaluation", "evaluation_reference"), None)
    blockers = v5.final_manifest_blockers()

    assert "EVALUATION_MOCK_PINS_48_ACTION_ROWS" in blockers
    assert "NO_STAGE_REGISTERS_THE_FINAL_MIGRATION" in blockers


def test_reregistering_the_retired_mock_stage_reopens_the_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import manifest_v3

    _swap_stage(
        monkeypatch,
        ("evaluation", "evaluation_mock"),
        manifest_v3.BootstrapStageContract(
            "reference_extensions",
            ("001_reference_extensions",),
            action_policy="immutable_content",
            action_rows=48,
            action_fixture_type="MOCK",
        ),
    )

    assert "EVALUATION_MOCK_PINS_48_ACTION_ROWS" in v5.final_manifest_blockers()


@pytest.mark.parametrize(
    ("label", "migrations"),
    [
        ("비움", ()),
        ("final migration 삭제", ("002_agent_runtime_clean",)),
        ("순서 변경", ("002_agent_runtime_clean", v5.MIGRATION_ID)),
    ],
)
@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_a_broken_migration_list_reopens_the_blocker(
    monkeypatch: pytest.MonkeyPatch, profile: str, label: str, migrations: tuple
) -> None:
    """**Runtime 하나만 성해도 통과했다.**

    `any(... for stage in 전체)`라서 Evaluation migration이 비어도 닫혔다.
    """

    import manifest_v3

    stage = v5.FINAL_STAGE_BY_PROFILE[profile]
    current = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[(profile, stage)]
    if tuple(migrations) == tuple(current.applied_migrations):
        pytest.skip(f"{profile}는 이 변이가 no-op이다")

    _swap_stage(
        monkeypatch,
        (profile, stage),
        manifest_v3.BootstrapStageContract(
            current.schema_stage,
            tuple(migrations),
            action_policy=current.action_policy,
            action_rows=current.action_rows,
            action_fixture_type=current.action_fixture_type,
        ),
    )

    assert "NO_STAGE_REGISTERS_THE_FINAL_MIGRATION" in v5.final_manifest_blockers()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_stage", "wrong_stage"),
        ("applied_migrations", ()),
        ("applied_migrations", ("001_reference_extensions",)),
        ("action_policy", "schema_only"),
        ("action_rows", 99),
        ("action_fixture_type", "MOCK"),
        ("action_fixture_type", None),
        ("action_fixture_type", "REFERENCE"),
    ],
)
@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_any_final_contract_drift_reopens_a_blocker(
    monkeypatch: pytest.MonkeyPatch, profile: str, field: str, value: object
) -> None:
    """**두 profile을 같은 강도로 본다**(구현리뷰 5차 필수 1).

    Evaluation만 schema·action까지 보고 Runtime은 migration만 보던 비대칭이 있었다.
    그래서 Runtime을 `action_rows=1`·`fixture_type=REFERENCE`로 잘못 등록해도 blocker가
    늘지 않았고, 정적 2종을 마저 지우면 빈 tuple이 될 수 있었다.
    """

    import manifest_v3

    stage = v5.FINAL_STAGE_BY_PROFILE[profile]
    current = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[(profile, stage)]
    kwargs = {
        "schema_stage": current.schema_stage,
        "applied_migrations": current.applied_migrations,
        "action_policy": current.action_policy,
        "action_rows": current.action_rows,
        "action_fixture_type": current.action_fixture_type,
    }
    if kwargs[field] == value:
        pytest.skip(f"{profile}.{field}는 이 변이가 no-op이다")
    kwargs[field] = value

    _swap_stage(
        monkeypatch,
        (profile, stage),
        manifest_v3.BootstrapStageContract(**kwargs),
    )
    blockers = v5.final_manifest_blockers()

    # **어느 blocker가 떠야 하는지까지 본다.** "정적 2종 외 아무거나"로 두면 evaluation
    # 전용 검사를 지워도 migration blocker가 대신 떠서 통과한다.
    assert "NO_STAGE_REGISTERS_THE_FINAL_MIGRATION" in blockers, (profile, field)
    if profile == "evaluation":
        # 최종 evaluation stage가 12행 REFERENCE를 표현하지 못한다는 사실은 migration
        # blocker와 다른 의미다.
        assert "EVALUATION_MOCK_PINS_48_ACTION_ROWS" in blockers, (field, value)
    else:
        assert "EVALUATION_MOCK_PINS_48_ACTION_ROWS" not in blockers, (field, value)


def test_the_expectation_table_matches_the_registered_contracts() -> None:
    """기대 표와 실제 등록 계약이 **전체**로 같아야 한다."""

    import manifest_v3

    for profile, expected in v5.FINAL_STAGE_EXPECTATIONS.items():
        stage = v5.FINAL_STAGE_BY_PROFILE[profile]
        contract = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[(profile, stage)]
        assert v5._stage_shape(contract) == expected, profile

    assert set(v5.FINAL_STAGE_EXPECTATIONS) == set(v5.FINAL_STAGE_BY_PROFILE)
    assert set(v5.FINAL_STAGE_EXPECTATIONS) == set(v5.PROFILE_MIGRATIONS)


def test_the_expectation_table_covers_every_contract_field() -> None:
    """**field를 나열하지 않았는지 확인한다.**

    `_stage_shape()`가 dataclass의 모든 field를 펴야 새 field가 생겨도 비교에서
    빠지지 않는다.
    """

    import dataclasses

    import manifest_v3

    contract = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[("runtime", "runtime_clean")]
    field_count = len(dataclasses.fields(contract))

    assert len(v5._stage_shape(contract)) == field_count
    for expected in v5.FINAL_STAGE_EXPECTATIONS.values():
        assert len(expected) == field_count
