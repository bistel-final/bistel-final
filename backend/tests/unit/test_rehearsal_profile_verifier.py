"""`V5-CM-2.4` 격리 적재 검증 단위 테스트.

DB에 붙지 않는다. `connection.execute()`만 흉내 내는 fake로 verifier가 실제로 무엇을
질의하고 무엇을 근거로 실패시키는지 고정한다. 실제 PostgreSQL transaction 경로는
`test_rehearsal_container.py`가 `container` marker로 따로 검증한다.
"""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import manifest_v3  # noqa: E402
import rebuild_runner as runner  # noqa: E402
import rehearsal_profile_verifier as verifier  # noqa: E402
import value_normalization  # noqa: E402

EXPECTED_TABLES = (
    "action_history",
    "dim_parameter",
    "evaluation",
    "fdc_trace",
    "lot_history",
    "metrology",
    "summary_alarm_history",
    "summary_data",
    "trace_alarm_history",
)


class _Fail(RuntimeError):
    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


def _fail(reason_code: str, exit_code: int) -> _Fail:
    return _Fail(reason_code, exit_code)


class _Result:
    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return self._rows

    def scalar(self) -> Any:
        if not self._rows:
            return None
        return next(iter(self._rows[0].values()))


class FakeConnection:
    """substring 우선순위로 응답을 돌려주는 read-only fake."""

    def __init__(self, responses: Sequence[tuple[str, Sequence[Mapping[str, Any]]]]):
        self._responses = list(responses)
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement: Any) -> _Result:
        rendered = str(statement)
        self.statements.append(rendered)
        for needle, rows in self._responses:
            if needle in rendered:
                return _Result(rows)
        raise AssertionError(f"예상하지 못한 SQL: {rendered}")

    def commit(self) -> None:  # pragma: no cover - 호출되면 테스트가 실패한다
        self.commits += 1

    def rollback(self) -> None:  # pragma: no cover
        self.rollbacks += 1


def _acceptance(
    name: str,
    column_types: Mapping[str, str],
    primary_key: Sequence[str],
    row_count: int,
    content_hash: str,
    profiles: Sequence[str] = ("runtime", "evaluation"),
) -> verifier.TableAcceptance:
    return verifier.TableAcceptance(
        name=name,
        columns=tuple(column_types),
        column_types=MappingProxyType(dict(column_types)),
        primary_key=tuple(primary_key),
        source_row_count=row_count,
        source_content_hash=content_hash,
        included_by_profile=tuple(profiles),
    )


def _hash(rows: Sequence[Mapping[str, Any]], types: Mapping[str, str]) -> str:
    return manifest_v3.hash_canonical_rows(
        [value_normalization.normalize_db_row(row, types) for row in rows]
    )


# --------------------------------------------------------------------------
# 6.1 manifest·profile 계약
# --------------------------------------------------------------------------


@pytest.fixture
def real_manifest() -> dict[str, Any]:
    return json.loads(runner.SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))


def test_real_manifest_yields_nine_acceptances(real_manifest: dict[str, Any]) -> None:
    acceptances = verifier.build_acceptances(real_manifest, EXPECTED_TABLES, _fail)
    assert tuple(entry.name for entry in acceptances) == EXPECTED_TABLES
    assert len(acceptances) == 9
    assert {entry.name for entry in acceptances} == set(EXPECTED_TABLES)


def test_producer_and_verifier_share_hash_and_normalization_version() -> None:
    """producer artifact와 verifier가 같은 정규화·hash 계약을 쓴다(계획 §6.1)."""

    manifest = json.loads(runner.SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["hash_algorithm"] == manifest_v3.HASH_ALGORITHM
    assert (
        manifest["value_normalization_version"]
        == value_normalization.VALUE_NORMALIZATION_VERSION
    )
    assert manifest["format_version"] == verifier.FORMAT_VERSION
    assert manifest["dataset_epoch"] == verifier.DATASET_EPOCH


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("format_version", 3),
        ("dataset_epoch", "kosa_0813"),
        ("hash_algorithm", "sha256"),
        ("value_normalization_version", "db-value-v0"),
    ],
)
def test_header_contract_is_exact(
    real_manifest: dict[str, Any], key: str, value: Any
) -> None:
    real_manifest[key] = value
    with pytest.raises(_Fail) as caught:
        verifier.build_acceptances(real_manifest, EXPECTED_TABLES, _fail)
    assert caught.value.reason_code == "ARCHIVE_INVALID"
    assert caught.value.exit_code == verifier.EXIT_USAGE


def test_table_set_must_match_expected(real_manifest: dict[str, Any]) -> None:
    real_manifest["tables"].pop("metrology")
    with pytest.raises(_Fail):
        verifier.build_acceptances(real_manifest, EXPECTED_TABLES, _fail)


def test_entry_keys_are_exact_seven(real_manifest: dict[str, Any]) -> None:
    assert verifier.TABLE_ENTRY_KEYS == frozenset(
        {
            "file_id",
            "columns",
            "column_types",
            "primary_key",
            "row_count",
            "content_hash",
            "included_by_profile",
        }
    )
    real_manifest["tables"]["metrology"]["extra_key"] = 1
    with pytest.raises(_Fail):
        verifier.build_acceptances(real_manifest, EXPECTED_TABLES, _fail)


def test_missing_entry_key_is_rejected(real_manifest: dict[str, Any]) -> None:
    real_manifest["tables"]["metrology"].pop("primary_key")
    with pytest.raises(_Fail):
        verifier.build_acceptances(real_manifest, EXPECTED_TABLES, _fail)


def _mutate(manifest: dict[str, Any], table: str, **changes: Any) -> dict[str, Any]:
    out = copy.deepcopy(manifest)
    out["tables"][table].update(changes)
    return out


@pytest.mark.parametrize(
    "changes",
    [
        {"column_types": {}},
        {"primary_key": []},
        {"primary_key": ["not_a_column"]},
        {"row_count": True},
        {"row_count": -1},
        {"row_count": "48"},
        {"content_hash": "ABC"},
        {"content_hash": "0" * 63},
        {"content_hash": "0" * 64 + "0"},
        {"included_by_profile": []},
        {"included_by_profile": ["runtime", "runtime"]},
        {"included_by_profile": ["staging"]},
        {"columns": []},
    ],
)
def test_entry_field_contracts(
    real_manifest: dict[str, Any], changes: dict[str, Any]
) -> None:
    mutated = _mutate(real_manifest, "metrology", **changes)
    with pytest.raises(_Fail):
        verifier.build_acceptances(mutated, EXPECTED_TABLES, _fail)


def test_column_types_keys_must_match_columns(real_manifest: dict[str, Any]) -> None:
    entry = real_manifest["tables"]["metrology"]
    types = dict(entry["column_types"])
    types["ghost_column"] = "text"
    mutated = _mutate(real_manifest, "metrology", column_types=types)
    with pytest.raises(_Fail):
        verifier.build_acceptances(mutated, EXPECTED_TABLES, _fail)


def test_unknown_logical_type_is_rejected(real_manifest: dict[str, Any]) -> None:
    entry = real_manifest["tables"]["metrology"]
    types = dict(entry["column_types"])
    types[entry["columns"][0]] = "json"
    mutated = _mutate(real_manifest, "metrology", column_types=types)
    with pytest.raises(_Fail):
        verifier.build_acceptances(mutated, EXPECTED_TABLES, _fail)


def test_duplicate_primary_key_is_rejected(real_manifest: dict[str, Any]) -> None:
    entry = real_manifest["tables"]["metrology"]
    first = entry["primary_key"][0]
    mutated = _mutate(real_manifest, "metrology", primary_key=[first, first])
    with pytest.raises(_Fail):
        verifier.build_acceptances(mutated, EXPECTED_TABLES, _fail)


def test_runtime_projects_evaluation_only_table_to_empty(
    real_manifest: dict[str, Any],
) -> None:
    """runtime에서 action_history는 12행이 아니라 0행·빈 hash가 기대값이다."""

    acceptances = verifier.build_acceptances(real_manifest, EXPECTED_TABLES, _fail)
    action = next(e for e in acceptances if e.name == verifier.EVALUATION_ONLY_TABLE)
    assert action.included_by_profile == ("evaluation",)
    assert action.source_row_count == 12
    assert action.expected_for("runtime") == (0, verifier.EMPTY_ROWS_HASH)
    assert action.expected_for("evaluation") == (12, action.source_content_hash)
    assert verifier.EMPTY_ROWS_HASH == manifest_v3.hash_canonical_rows([])

    for entry in acceptances:
        if entry.name == verifier.EVALUATION_ONLY_TABLE:
            continue
        assert entry.expected_for("runtime") == (
            entry.source_row_count,
            entry.source_content_hash,
        )


def test_acceptance_is_deeply_immutable(real_manifest: dict[str, Any]) -> None:
    import dataclasses

    entry = verifier.build_acceptances(real_manifest, EXPECTED_TABLES, _fail)[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.name = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        entry.column_types[entry.columns[0]] = "text"  # type: ignore[index]
    assert isinstance(entry.columns, tuple)
    assert isinstance(entry.primary_key, tuple)
    assert isinstance(entry.included_by_profile, tuple)


# --------------------------------------------------------------------------
# 6.2 DB verifier
# --------------------------------------------------------------------------

CATALOG = "information_schema.columns"


def _catalog_rows(spec: Mapping[str, Mapping[str, str]]) -> list[dict[str, str]]:
    return [
        {"table_name": table, "column_name": column, "data_type": data_type}
        for table, columns in spec.items()
        for column, data_type in columns.items()
    ]


ONE = _acceptance("metrology", {"id": "numeric", "note": "text"}, ["id"], 0, "0" * 64)


@pytest.mark.parametrize(
    ("data_type", "logical"),
    [
        ("character varying", "text"),
        ("character", "text"),
        ("text", "text"),
        ("smallint", "numeric"),
        ("integer", "numeric"),
        ("bigint", "numeric"),
        ("numeric", "numeric"),
        ("boolean", "boolean"),
        ("timestamp without time zone", "timestamp"),
    ],
)
def test_db_type_map_covers_every_real_ddl_type(data_type: str, logical: str) -> None:
    assert verifier.DB_TYPE_MAP[data_type] == logical
    entry = _acceptance("metrology", {"id": logical}, ["id"], 0, "0" * 64)
    connection = FakeConnection(
        [(CATALOG, _catalog_rows({"metrology": {"id": data_type}}))]
    )
    verifier.check_db_types(connection, (entry,))
    assert len(verifier.DB_TYPE_MAP) == 9


@pytest.mark.parametrize(
    "catalog",
    [
        {"metrology": {"id": "integer"}},  # column 누락
        {"metrology": {"id": "integer", "note": "text", "extra": "text"}},  # 추가
        {"metrology": {"id": "integer", "note": "uuid"}},  # 미지원 type
        {"metrology": {"id": "text", "note": "text"}},  # logical mismatch
        {},  # table 누락
        {"metrology": {"id": "integer", "note": "text"}, "ghost": {"id": "integer"}},
    ],
)
def test_db_type_mismatch_fails(catalog: dict[str, dict[str, str]]) -> None:
    connection = FakeConnection([(CATALOG, _catalog_rows(catalog))])
    with pytest.raises(verifier.AcceptanceError) as caught:
        verifier.check_db_types(connection, (ONE,))
    assert caught.value.check is verifier.AcceptanceCheck.DB_TYPE


def test_row_count_and_typed_hash_match() -> None:
    types = {"id": "numeric", "note": "text"}
    rows = [{"id": 1, "note": "a"}, {"id": 2, "note": "b"}]
    entry = _acceptance("metrology", types, ["id"], 2, _hash(rows, types))
    connection = FakeConnection(
        [
            ('count(*) FROM "public"."metrology"', [{"n": 2}]),
            ('FROM "public"."metrology"', rows),
        ]
    )
    verifier.check_row_counts_and_hashes(connection, (entry,), "runtime")
    assert all(statement.startswith("SELECT") for statement in connection.statements)
    assert "*" not in connection.statements[-1]
    assert connection.commits == 0 and connection.rollbacks == 0


def test_row_count_checked_before_hash() -> None:
    types = {"id": "numeric"}
    entry = _acceptance("metrology", types, ["id"], 2, _hash([{"id": 1}], types))
    connection = FakeConnection([('count(*) FROM "public"."metrology"', [{"n": 1}])])
    with pytest.raises(verifier.AcceptanceError) as caught:
        verifier.check_row_counts_and_hashes(connection, (entry,), "runtime")
    assert caught.value.check is verifier.AcceptanceCheck.ROW_COUNT
    # 행 수가 어긋나면 hash SELECT는 실행하지 않는다.
    assert len(connection.statements) == 1


def test_same_count_non_key_cell_change_breaks_hash() -> None:
    types = {"id": "numeric", "note": "text"}
    source = [{"id": 1, "note": "a"}, {"id": 2, "note": "b"}]
    drifted = [{"id": 1, "note": "a"}, {"id": 2, "note": "B"}]
    entry = _acceptance("metrology", types, ["id"], 2, _hash(source, types))
    connection = FakeConnection(
        [
            ('count(*) FROM "public"."metrology"', [{"n": 2}]),
            ('FROM "public"."metrology"', drifted),
        ]
    )
    with pytest.raises(verifier.AcceptanceError) as caught:
        verifier.check_row_counts_and_hashes(connection, (entry,), "runtime")
    assert caught.value.check is verifier.AcceptanceCheck.CONTENT_HASH


@pytest.mark.parametrize(
    ("stored", "declared"),
    [
        (1, "1"),
        (1, "1.000"),
        (1.5, "1.50"),
        (0, "-0.0"),
    ],
)
def test_equal_numeric_representations_hash_identically(
    stored: Any, declared: str
) -> None:
    from decimal import Decimal

    types = {"id": "numeric"}
    expected = _hash([{"id": Decimal(declared)}], types)
    entry = _acceptance("metrology", types, ["id"], 1, expected)
    connection = FakeConnection(
        [
            ('count(*) FROM "public"."metrology"', [{"n": 1}]),
            ('FROM "public"."metrology"', [{"id": stored}]),
        ]
    )
    verifier.check_row_counts_and_hashes(connection, (entry,), "runtime")


def test_runtime_empty_projection_uses_empty_hash() -> None:
    entry = _acceptance(
        "action_history", {"id": "numeric"}, ["id"], 12, "a" * 64, ("evaluation",)
    )
    connection = FakeConnection(
        [
            ('count(*) FROM "public"."action_history"', [{"n": 0}]),
            ('FROM "public"."action_history"', []),
        ]
    )
    verifier.check_row_counts_and_hashes(connection, (entry,), "runtime")

    loaded = FakeConnection([('count(*) FROM "public"."action_history"', [{"n": 12}])])
    with pytest.raises(verifier.AcceptanceError) as caught:
        verifier.check_row_counts_and_hashes(loaded, (entry,), "runtime")
    assert caught.value.check is verifier.AcceptanceCheck.ROW_COUNT


def test_primary_key_duplicates_fail() -> None:
    entry = _acceptance("metrology", {"id": "numeric"}, ["id"], 0, "0" * 64)
    ok = FakeConnection([("AS duplicates", [{"n": 0}])])
    verifier.check_primary_keys(ok, (entry,))
    assert 'GROUP BY "id"' in ok.statements[0]

    duplicated = FakeConnection([("AS duplicates", [{"n": 1}])])
    with pytest.raises(verifier.AcceptanceError) as caught:
        verifier.check_primary_keys(duplicated, (entry,))
    assert caught.value.check is verifier.AcceptanceCheck.PK


def test_foreign_key_anti_join_covers_four_declared_links() -> None:
    assert verifier.DECLARED_FOREIGN_KEYS == (
        ("fdc_trace", "lot_hist_id", "lot_history", "lot_hist_id"),
        ("fdc_trace", "parameter_id", "dim_parameter", "parameter_id"),
        ("summary_data", "lot_hist_id", "lot_history", "lot_hist_id"),
        ("evaluation", "lot_hist_id", "lot_history", "lot_hist_id"),
    )
    loaded = ["fdc_trace", "lot_history", "dim_parameter", "summary_data", "evaluation"]
    ok = FakeConnection([("LEFT JOIN", [{"n": 0}])])
    verifier.check_foreign_keys(ok, loaded)
    assert len(ok.statements) == 4

    broken = FakeConnection([("LEFT JOIN", [{"n": 3}])])
    with pytest.raises(verifier.AcceptanceError) as caught:
        verifier.check_foreign_keys(broken, loaded)
    assert caught.value.check is verifier.AcceptanceCheck.FK


def test_foreign_key_skips_unloaded_tables() -> None:
    """metrology.lot_hist_id는 선언 FK가 아니므로 검사 대상이 아니다."""

    connection = FakeConnection([("LEFT JOIN", [{"n": 0}])])
    verifier.check_foreign_keys(connection, ["metrology"])
    assert connection.statements == []


def _reference_responses(
    alarm_types: Sequence[tuple[str, int]],
    trace_alarms: int,
    summary_alarms: int,
    seq_values: Sequence[str],
) -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        (
            'GROUP BY "alarm_type"',
            [{"alarm_type": name, "n": count} for name, count in alarm_types],
        ),
        ('count(*) FROM "public"."trace_alarm_history"', [{"n": trace_alarms}]),
        ('count(*) FROM "public"."summary_alarm_history"', [{"n": summary_alarms}]),
        ('DISTINCT "seq_no"', [{"seq_no": value} for value in seq_values]),
    ]


REFERENCE_TABLES = [
    "evaluation",
    "trace_alarm_history",
    "summary_alarm_history",
    "fdc_trace",
]


def test_final_reference_constants_are_pinned() -> None:
    assert dict(verifier.FINAL_REFERENCE.evaluation_alarm_types) == {
        "IN": 4538,
        "OOC": 216,
        "OOS": 46,
    }
    assert verifier.FINAL_REFERENCE.trace_alarm_rows == 138
    assert verifier.FINAL_REFERENCE.summary_alarm_rows == 51
    assert verifier.FINAL_REFERENCE.trace_seq_values == ("0", "1", "2", "3", "4", "5")


def test_matching_reference_passes() -> None:
    connection = FakeConnection(
        _reference_responses(
            [("IN", 4538), ("OOC", 216), ("OOS", 46)],
            138,
            51,
            ["0", "1", "2", "3", "4", "5"],
        )
    )
    verifier.check_reference_values(
        connection, verifier.FINAL_REFERENCE, REFERENCE_TABLES
    )
    assert len(connection.statements) == 4


@pytest.mark.parametrize(
    "responses",
    [
        _reference_responses(
            [("IN", 4538), ("OOC", 216), ("OOS", 42)], 138, 51, list("012345")
        ),
        _reference_responses([("IN", 4538), ("OOC", 216)], 138, 51, list("012345")),
        _reference_responses(
            [("IN", 4538), ("OOC", 216), ("OOS", 46), ("OOD", 1)],
            138,
            51,
            list("012345"),
        ),
        _reference_responses(
            [("IN", 4538), ("OOC", 216), ("OOS", 46)], 126, 51, list("012345")
        ),
        _reference_responses(
            [("IN", 4538), ("OOC", 216), ("OOS", 46)], 138, 47, list("012345")
        ),
        _reference_responses(
            [("IN", 4538), ("OOC", 216), ("OOS", 46)], 138, 51, list("01234")
        ),
        _reference_responses(
            [("IN", 4538), ("OOC", 216), ("OOS", 46)], 138, 51, list("0123456")
        ),
    ],
)
def test_reference_drift_fails(
    responses: list[tuple[str, list[dict[str, Any]]]],
) -> None:
    connection = FakeConnection(responses)
    with pytest.raises(verifier.AcceptanceError) as caught:
        verifier.check_reference_values(
            connection, verifier.FINAL_REFERENCE, REFERENCE_TABLES
        )
    assert caught.value.check is verifier.AcceptanceCheck.REFERENCE


def test_reference_skips_tables_not_loaded() -> None:
    connection = FakeConnection([])
    verifier.check_reference_values(connection, verifier.FINAL_REFERENCE, ["metrology"])
    assert connection.statements == []


TS_ENTRY = _acceptance(
    "lot_history", {"id": "numeric", "event_dtts": "timestamp"}, ["id"], 0, "0" * 64
)


def test_naive_timestamp_projects_to_kst() -> None:
    rows = [
        {"event_dtts": datetime(2026, 8, 18, 9, 30, 0)},
        {"event_dtts": datetime(2026, 1, 1, 0, 0, 0)},
        {"event_dtts": None},
    ]
    connection = FakeConnection([('FROM "public"."lot_history"', rows)])
    verifier.check_timestamp_projection(connection, (TS_ENTRY,), ["lot_history"])
    assert '"event_dtts"' in connection.statements[0]
    assert '"id"' not in connection.statements[0]


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 8, 18, 9, 30, tzinfo=UTC),
        "2026-08-18 09:30:00",
        20260818093000,
    ],
)
def test_non_naive_or_wrong_dbapi_type_fails(value: Any) -> None:
    connection = FakeConnection(
        [('FROM "public"."lot_history"', [{"event_dtts": value}])]
    )
    with pytest.raises(verifier.AcceptanceError) as caught:
        verifier.check_timestamp_projection(connection, (TS_ENTRY,), ["lot_history"])
    assert caught.value.check is verifier.AcceptanceCheck.TIMESTAMP


def test_timestamp_check_skips_tables_without_timestamp_columns() -> None:
    connection = FakeConnection([])
    verifier.check_timestamp_projection(connection, (ONE,), ["metrology"])
    assert connection.statements == []


# --------------------------------------------------------------------------
# postcheck 경계
# --------------------------------------------------------------------------


def test_postcheck_maps_every_category_to_one_reason_code() -> None:
    """내부 범주는 wrapper 경계에서 전부 `MODE_CONTRACT_ERROR` exit 1이다."""

    entry = _acceptance("metrology", {"id": "numeric"}, ["id"], 1, "0" * 64)
    connection = FakeConnection(
        [(CATALOG, _catalog_rows({"metrology": {"id": "text"}}))]
    )
    postcheck = verifier.make_acceptance_postcheck(
        (entry,), ["metrology"], "runtime", _fail
    )
    with pytest.raises(_Fail) as caught:
        postcheck(connection, object())
    assert caught.value.reason_code == "MODE_CONTRACT_ERROR"
    assert caught.value.exit_code == verifier.EXIT_MISMATCH
    assert verifier.AcceptanceCheck.DB_TYPE.value not in str(caught.value)


def test_postcheck_returns_none_on_success() -> None:
    types = {"id": "numeric"}
    rows = [{"id": 1}]
    entry = _acceptance("metrology", types, ["id"], 1, _hash(rows, types))
    connection = FakeConnection(
        [
            (CATALOG, _catalog_rows({"metrology": {"id": "integer"}})),
            ('count(*) FROM "public"."metrology"', [{"n": 1}]),
            ("AS duplicates", [{"n": 0}]),
            ('FROM "public"."metrology"', rows),
        ]
    )
    postcheck = verifier.make_acceptance_postcheck(
        (entry,), ["metrology"], "runtime", _fail
    )
    assert postcheck(connection, object()) is None
    assert all(statement.startswith("SELECT") for statement in connection.statements)
    assert connection.commits == 0 and connection.rollbacks == 0


def test_runtime_profile_still_covers_all_four_foreign_keys() -> None:
    """runtime이 빼는 table은 `action_history` 하나이고 FK 4종과 무관하다.

    `check_foreign_keys()`가 미적재 table을 건너뛰므로, profile projection이 FK 검증
    구멍을 만들지 않는지 상수 수준에서 고정한다(구현보고 §8.3).
    """

    runtime_tables = [t for t in EXPECTED_TABLES if t != verifier.EVALUATION_ONLY_TABLE]
    involved = {
        table
        for child, _cc, parent, _pc in verifier.DECLARED_FOREIGN_KEYS
        for table in (child, parent)
    }
    assert verifier.EVALUATION_ONLY_TABLE not in involved
    assert involved <= set(runtime_tables)

    connection = FakeConnection([("LEFT JOIN", [{"n": 0}])])
    verifier.check_foreign_keys(connection, runtime_tables)
    assert len(connection.statements) == len(verifier.DECLARED_FOREIGN_KEYS)


def test_db_type_check_runs_before_any_hash_query() -> None:
    """type 대조가 typed hash보다 먼저다. 순서가 뒤집히면 이 테스트가 깨진다."""

    entry = _acceptance("metrology", {"id": "numeric"}, ["id"], 1, "0" * 64)
    connection = FakeConnection(
        [(CATALOG, _catalog_rows({"metrology": {"id": "text"}}))]
    )
    postcheck = verifier.make_acceptance_postcheck(
        (entry,), ["metrology"], "runtime", _fail
    )
    with pytest.raises(_Fail):
        postcheck(connection, object())
    # catalog 질의 1건만 나가고 count·hash SELECT는 실행되지 않았다.
    assert len(connection.statements) == 1
    assert CATALOG in connection.statements[0]


def test_reference_count_uses_identifier_composition() -> None:
    """reference SQL도 f-string이 아닌 Identifier 조립이다(구현리뷰 1차 권장 1)."""

    connection = FakeConnection(
        _reference_responses(
            [("IN", 4538), ("OOC", 216), ("OOS", 46)],
            138,
            51,
            ["0", "1", "2", "3", "4", "5"],
        )
    )
    verifier.check_reference_values(
        connection, verifier.FINAL_REFERENCE, REFERENCE_TABLES
    )
    assert not any("public." in s for s in connection.statements)
    counted = [s for s in connection.statements if s.startswith("SELECT count(*)")]
    assert counted == [
        'SELECT count(*) FROM "public"."trace_alarm_history"',
        'SELECT count(*) FROM "public"."summary_alarm_history"',
    ]


@pytest.mark.parametrize("bad", [[], {"nested": 1}, 7, None, True])
def test_non_string_logical_type_is_archive_invalid(
    real_manifest: dict[str, Any], bad: Any
) -> None:
    """list/object logical type이 raw TypeError로 새지 않는다(구현리뷰 1차 필수 1)."""

    entry = real_manifest["tables"]["metrology"]
    entry["column_types"][entry["columns"][0]] = bad
    with pytest.raises(_Fail) as caught:
        verifier.build_acceptances(real_manifest, EXPECTED_TABLES, _fail)
    assert caught.value.reason_code == "ARCHIVE_INVALID"
    assert caught.value.exit_code == verifier.EXIT_USAGE


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_unhashable_numeric_is_content_mismatch_not_internal_error(value: str) -> None:
    """numeric NaN/Infinity는 exit 1 내용 불일치다(구현리뷰 1차 필수 2)."""

    from decimal import Decimal

    entry = _acceptance("metrology", {"id": "numeric"}, ["id"], 1, "0" * 64)
    responses = [
        (CATALOG, _catalog_rows({"metrology": {"id": "numeric"}})),
        ('count(*) FROM "public"."metrology"', [{"n": 1}]),
        ('FROM "public"."metrology"', [{"id": Decimal(value)}]),
    ]

    direct = FakeConnection(responses)
    with pytest.raises(verifier.AcceptanceError) as raw:
        verifier.check_row_counts_and_hashes(direct, (entry,), "runtime")
    assert raw.value.check is verifier.AcceptanceCheck.CONTENT_HASH

    connection = FakeConnection(responses)
    postcheck = verifier.make_acceptance_postcheck(
        (entry,), ["metrology"], "runtime", _fail
    )
    with pytest.raises(_Fail) as caught:
        postcheck(connection, object())
    assert caught.value.reason_code == "MODE_CONTRACT_ERROR"
    assert caught.value.exit_code == verifier.EXIT_MISMATCH
    rendered = str(caught.value)
    assert value not in rendered
    assert "metrology" not in rendered
    assert verifier.AcceptanceCheck.CONTENT_HASH.value not in rendered
