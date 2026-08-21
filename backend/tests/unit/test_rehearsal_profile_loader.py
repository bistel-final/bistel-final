"""`V5-CM-2.3` profile loader 계약.

DB·Docker 없이 도는 순수 계약이다. 실제 COPY와 rollback은 container 테스트가 본다.
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rehearsal_profile_loader as loader  # noqa: E402

COLUMNS = ["id", "name"]


class ContractError(RuntimeError):
    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


def _fail(reason_code: str, exit_code: int) -> ContractError:
    return ContractError(reason_code, exit_code)


def _manifest(columns: list[str] | None = None) -> dict[str, dict[str, Any]]:
    columns = COLUMNS if columns is None else columns
    tables: dict[str, dict[str, Any]] = {}
    for table in loader.LOAD_ORDER:
        profiles = (
            ["evaluation"]
            if table == loader.EVALUATION_ONLY_TABLE
            else ["runtime", "runtime-e2e", "evaluation"]
        )
        tables[table] = {
            "file_id": f"project/repository/sample/data/{table}.csv",
            "columns": list(columns),
            "included_by_profile": profiles,
        }
    return tables


def _payload(columns: list[str] | None = None, *, bom: bool = True) -> bytes:
    columns = COLUMNS if columns is None else columns
    text = ",".join(columns) + "\n" + ",".join("x" for _ in columns) + "\n"
    return ("﻿" + text).encode() if bom else text.encode()


def _archive(members: dict[str, bytes]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as writer:
        for name, payload in members.items():
            writer.writestr(name, payload)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def _intake(members: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for name, payload in members.items()
    }


def _members(
    manifest: dict[str, dict[str, Any]], **overrides: bytes
) -> dict[str, bytes]:
    members = {
        entry["file_id"]: _payload(entry["columns"]) for entry in manifest.values()
    }
    members.update(overrides)
    return members


# --- profile 선택 ---------------------------------------------------------------


@pytest.mark.parametrize(("profile", "expected"), [("runtime", 8), ("evaluation", 9)])
def test_profile_selects_expected_table_count(profile: str, expected: int) -> None:
    tables = loader.select_tables(_manifest(), profile, _fail)
    assert len(tables) == expected
    assert (loader.EVALUATION_ONLY_TABLE in tables) is (profile == "evaluation")


def test_selection_follows_fixed_fk_order_not_manifest_key_order() -> None:
    manifest = _manifest()
    reversed_manifest = dict(reversed(list(manifest.items())))
    assert list(reversed_manifest) != list(manifest)
    selected = loader.select_tables(reversed_manifest, "evaluation", _fail)
    assert selected == loader.LOAD_ORDER


def test_fk_dependencies_precede_dependents() -> None:
    order = loader.LOAD_ORDER
    for dependent, required in (
        ("fdc_trace", "lot_history"),
        ("fdc_trace", "dim_parameter"),
        ("summary_data", "lot_history"),
        ("evaluation", "lot_history"),
    ):
        assert order.index(required) < order.index(dependent)


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ContractError) as raised:
        loader.select_tables(_manifest(), "public", _fail)
    assert raised.value.reason_code == "PROFILE_MISMATCH"
    assert raised.value.exit_code == 1


def test_table_set_drift_is_archive_mismatch() -> None:
    manifest = _manifest()
    manifest.pop("metrology")
    with pytest.raises(ContractError) as raised:
        loader.select_tables(manifest, "runtime", _fail)
    assert raised.value.reason_code == "ARCHIVE_MISMATCH"


def test_runtime_and_e2e_inclusion_must_match() -> None:
    manifest = _manifest()
    manifest["metrology"]["included_by_profile"] = ["runtime", "evaluation"]
    with pytest.raises(ContractError) as raised:
        loader.select_tables(manifest, "runtime", _fail)
    assert raised.value.reason_code == "PROFILE_MISMATCH"


def test_action_history_must_be_evaluation_only() -> None:
    manifest = _manifest()
    manifest["action_history"]["included_by_profile"] = [
        "runtime",
        "runtime-e2e",
        "evaluation",
    ]
    with pytest.raises(ContractError) as raised:
        loader.select_tables(manifest, "runtime", _fail)
    assert raised.value.reason_code == "PROFILE_MISMATCH"


# --- member 검증 -----------------------------------------------------------------


def test_verified_bodies_strip_leading_bom_only() -> None:
    manifest = _manifest()
    members = _members(manifest)
    tables = loader.select_tables(manifest, "evaluation", _fail)
    bodies = loader.verified_csv_bodies(
        _archive(members), manifest, _intake(members), tables, _fail
    )
    for body in bodies.values():
        assert not body.startswith(loader.UTF8_BOM)
        assert loader.UTF8_BOM not in body


def test_csv_without_bom_is_accepted() -> None:
    manifest = _manifest()
    members = _members(manifest)
    members = {name: _payload(bom=False) for name in members}
    tables = loader.select_tables(manifest, "evaluation", _fail)
    bodies = loader.verified_csv_bodies(
        _archive(members), manifest, _intake(members), tables, _fail
    )
    assert len(bodies) == 9


def test_internal_bom_is_rejected() -> None:
    manifest = _manifest()
    members = _members(manifest)
    target = manifest["dim_parameter"]["file_id"]
    members[target] = _payload() + loader.UTF8_BOM + b"y,y\n"
    tables = loader.select_tables(manifest, "runtime", _fail)
    with pytest.raises(ContractError) as raised:
        loader.verified_csv_bodies(
            _archive(members), manifest, _intake(members), tables, _fail
        )
    assert raised.value.reason_code == "ARCHIVE_INVALID"
    assert raised.value.exit_code == 2


def test_nul_byte_is_rejected() -> None:
    manifest = _manifest()
    members = _members(manifest)
    target = manifest["dim_parameter"]["file_id"]
    members[target] = _payload() + b"a\x00b,c\n"
    tables = loader.select_tables(manifest, "runtime", _fail)
    with pytest.raises(ContractError) as raised:
        loader.verified_csv_bodies(
            _archive(members), manifest, _intake(members), tables, _fail
        )
    assert raised.value.reason_code == "ARCHIVE_INVALID"


def test_invalid_utf8_is_rejected() -> None:
    manifest = _manifest()
    members = _members(manifest)
    target = manifest["dim_parameter"]["file_id"]
    members[target] = b"\xef\xbb\xbfid,name\n\xff\xfe,x\n"
    tables = loader.select_tables(manifest, "runtime", _fail)
    with pytest.raises(ContractError) as raised:
        loader.verified_csv_bodies(
            _archive(members), manifest, _intake(members), tables, _fail
        )
    assert raised.value.reason_code == "ARCHIVE_INVALID"


@pytest.mark.parametrize(
    "header",
    [["id"], ["id", "name", "extra"], ["name", "id"], ["id", "id"]],
)
def test_header_must_match_manifest_columns_exactly(header: list[str]) -> None:
    manifest = _manifest()
    members = _members(manifest)
    target = manifest["dim_parameter"]["file_id"]
    members[target] = _payload(header)
    tables = loader.select_tables(manifest, "runtime", _fail)
    with pytest.raises(ContractError) as raised:
        loader.verified_csv_bodies(
            _archive(members), manifest, _intake(members), tables, _fail
        )
    assert raised.value.reason_code == "ARCHIVE_MISMATCH"
    assert raised.value.exit_code == 1


def test_raw_member_hash_mismatch_is_rejected() -> None:
    manifest = _manifest()
    members = _members(manifest)
    intake = _intake(members)
    intake[manifest["dim_parameter"]["file_id"]]["sha256"] = "0" * 64
    tables = loader.select_tables(manifest, "runtime", _fail)
    with pytest.raises(ContractError) as raised:
        loader.verified_csv_bodies(_archive(members), manifest, intake, tables, _fail)
    assert raised.value.reason_code == "ARCHIVE_MISMATCH"


def test_raw_member_size_mismatch_is_rejected() -> None:
    manifest = _manifest()
    members = _members(manifest)
    intake = _intake(members)
    intake[manifest["dim_parameter"]["file_id"]]["size_bytes"] += 1
    tables = loader.select_tables(manifest, "runtime", _fail)
    with pytest.raises(ContractError) as raised:
        loader.verified_csv_bodies(_archive(members), manifest, intake, tables, _fail)
    assert raised.value.reason_code == "ARCHIVE_MISMATCH"


def test_member_absent_from_intake_is_rejected() -> None:
    manifest = _manifest()
    members = _members(manifest)
    intake = _intake(members)
    del intake[manifest["dim_parameter"]["file_id"]]
    tables = loader.select_tables(manifest, "runtime", _fail)
    with pytest.raises(ContractError) as raised:
        loader.verified_csv_bodies(_archive(members), manifest, intake, tables, _fail)
    assert raised.value.reason_code == "ARCHIVE_INVALID"


def test_duplicate_zip_entry_is_rejected() -> None:
    manifest = _manifest()
    members = _members(manifest)
    target = manifest["dim_parameter"]["file_id"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as writer:
        for name, payload in members.items():
            writer.writestr(name, payload)
        writer.writestr(target, members[target])
    buffer.seek(0)
    tables = loader.select_tables(manifest, "runtime", _fail)
    with pytest.raises(ContractError) as raised:
        loader.verified_csv_bodies(
            zipfile.ZipFile(buffer), manifest, _intake(members), tables, _fail
        )
    assert raised.value.reason_code == "ARCHIVE_INVALID"


def test_directory_entry_is_rejected() -> None:
    manifest = _manifest()
    members = _members(manifest)
    target = manifest["dim_parameter"]["file_id"]
    del members[target]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as writer:
        for name, payload in members.items():
            writer.writestr(name, payload)
        writer.writestr(zipfile.ZipInfo(target + "/"), b"")
    buffer.seek(0)
    intake = _intake(members)
    intake[target] = {"size_bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}
    tables = loader.select_tables(manifest, "runtime", _fail)
    with pytest.raises(ContractError) as raised:
        loader.verified_csv_bodies(
            zipfile.ZipFile(buffer), manifest, intake, tables, _fail
        )
    assert raised.value.reason_code == "ARCHIVE_INVALID"


# --- COPY handler ----------------------------------------------------------------


class _Copy:
    def __init__(self, sink: list[bytes]) -> None:
        self.sink = sink

    def __enter__(self) -> _Copy:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def write(self, payload: bytes) -> None:
        self.sink.append(payload)


class _Cursor:
    def __init__(self, driver: _Driver) -> None:
        self.driver = driver

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def copy(self, statement: Any) -> _Copy:
        # psycopg.sql.Composed 는 connection 없이도 as_string() 으로 펼칠 수 있다.
        rendered = (
            statement.as_string(None)
            if hasattr(statement, "as_string")
            else str(statement)
        )
        self.driver.statements.append(rendered)
        return _Copy(self.driver.payloads)


class _Driver:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.payloads: list[bytes] = []
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _Connection:
    def __init__(self) -> None:
        self.connection = _Driver()


def _copied_tables(driver: _Driver) -> list[str]:
    """`COPY "public"."<table>" (...)` 에서 table 이름만 뽑는다."""

    return [
        re.search(r'COPY "public"\."([^"]+)"', statement).group(1)
        for statement in driver.statements
    ]


def _handlers(profile: str = "evaluation"):
    manifest = _manifest()
    members = _members(manifest)
    tables = loader.select_tables(manifest, profile, _fail)
    bodies = loader.verified_csv_bodies(
        _archive(members), manifest, _intake(members), tables, _fail
    )
    columns_by_table = {table: tuple(manifest[table]["columns"]) for table in tables}
    return loader.make_load_handlers(bodies, columns_by_table, tables, profile, _fail)


def test_copy_uses_fixed_order_quoted_columns_and_returns_none() -> None:
    handler, _ = _handlers()
    connection = _Connection()
    assert handler(connection, object()) is None

    driver = connection.connection
    assert tuple(_copied_tables(driver)) == loader.LOAD_ORDER
    for statement in driver.statements:
        assert '("id", "name")' in statement
        assert "FORMAT CSV, HEADER TRUE" in statement


def test_copy_writes_bom_stripped_original_bytes() -> None:
    handler, _ = _handlers()
    connection = _Connection()
    handler(connection, object())
    for payload in connection.connection.payloads:
        assert not payload.startswith(loader.UTF8_BOM)
        assert payload.startswith(b"id,name\n")


def test_runtime_profile_never_copies_action_history() -> None:
    handler, _ = _handlers("runtime")
    connection = _Connection()
    handler(connection, object())
    loaded = _copied_tables(connection.connection)
    assert loader.EVALUATION_ONLY_TABLE not in loaded
    assert len(loaded) == 8


def test_loader_never_commits_or_rolls_back() -> None:
    handler, _ = _handlers()
    connection = _Connection()
    handler(connection, object())
    assert connection.connection.committed is False
    assert connection.connection.rolled_back is False


def test_no_csv_cell_is_interpolated_into_sql() -> None:
    handler, _ = _handlers()
    connection = _Connection()
    handler(connection, object())
    for statement in connection.connection.statements:
        assert "\n" not in statement
        assert "x" not in statement.replace("FORMAT CSV", "")


# --- postcheck -------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _PostcheckConnection:
    def __init__(self, present: bool = True, action_count: int = 12) -> None:
        self.present = present
        self.action_count = action_count
        self.queries: list[str] = []

    def execute(self, statement: Any) -> _Result:
        text = str(statement)
        self.queries.append(text)
        if "count(*)" in text:
            return _Result([{"n": self.action_count}])
        return _Result([{"present": self.present}])


def test_postcheck_returns_none_when_profile_contract_holds() -> None:
    _, postcheck = _handlers("evaluation")
    assert postcheck(_PostcheckConnection(action_count=12), object()) is None

    _, runtime_postcheck = _handlers("runtime")
    assert runtime_postcheck(_PostcheckConnection(action_count=0), object()) is None


@pytest.mark.parametrize(
    ("profile", "action_count"), [("runtime", 1), ("evaluation", 11)]
)
def test_postcheck_rejects_wrong_action_count(profile: str, action_count: int) -> None:
    _, postcheck = _handlers(profile)
    with pytest.raises(ContractError) as raised:
        postcheck(_PostcheckConnection(action_count=action_count), object())
    assert raised.value.reason_code == "MODE_CONTRACT_ERROR"


def test_postcheck_rejects_empty_non_action_table() -> None:
    _, postcheck = _handlers("evaluation")
    with pytest.raises(ContractError) as raised:
        postcheck(_PostcheckConnection(present=False), object())
    assert raised.value.reason_code == "MODE_CONTRACT_ERROR"


def test_postcheck_does_not_probe_action_history_for_existence() -> None:
    _, postcheck = _handlers("evaluation")
    connection = _PostcheckConnection()
    postcheck(connection, object())
    existence = [query for query in connection.queries if "EXISTS" in query]
    assert len(existence) == 8
    assert all(loader.EVALUATION_ONLY_TABLE not in query for query in existence)


# --- 구현리뷰 1차 필수 회귀 ---------------------------------------------------------


@pytest.mark.parametrize(
    "column",
    [
        'id") FROM STDIN; DROP TABLE audit_log; --',
        "id;drop",
        "id\nname",
        'id"',
        "Id",
        "1id",
    ],
)
def test_malicious_manifest_column_is_rejected_before_sql(column: str) -> None:
    """식별자 변조가 SQL 구조에 닿기 전에 거부된다 (필수 1)."""

    manifest = _manifest([column, "name"])
    with pytest.raises(ContractError) as raised:
        loader.validate_manifest_tables(manifest, _fail)
    assert raised.value.reason_code == "ARCHIVE_INVALID"
    assert raised.value.exit_code == 2


def test_duplicate_columns_are_rejected() -> None:
    with pytest.raises(ContractError) as raised:
        loader.validate_manifest_tables(_manifest(["id", "id"]), _fail)
    assert raised.value.reason_code == "ARCHIVE_INVALID"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m["metrology"].pop("included_by_profile"),
        lambda m: m["metrology"].__setitem__("columns", []),
        lambda m: m["metrology"].__setitem__("columns", "id"),
        lambda m: m["metrology"].__setitem__("file_id", ""),
        lambda m: m["metrology"].__setitem__("included_by_profile", ["public"]),
        lambda m: m.__setitem__("metrology", "not-a-mapping"),
    ],
)
def test_malformed_table_entry_is_archive_invalid(mutate: Any) -> None:
    """구조 오류는 KeyError가 아니라 `ARCHIVE_INVALID`(2)다 (필수 3)."""

    manifest = _manifest()
    mutate(manifest)
    with pytest.raises(ContractError) as raised:
        loader.validate_manifest_tables(manifest, _fail)
    assert raised.value.reason_code == "ARCHIVE_INVALID"
    assert raised.value.exit_code == 2


@pytest.mark.parametrize(
    "selected",
    [
        "not-a-list",
        [{"path": "a", "size_bytes": 1}],
        [{"path": "a", "size_bytes": -1, "sha256": "x"}],
        [{"path": "", "size_bytes": 1, "sha256": "x"}],
        [
            {"path": "a", "size_bytes": 1, "sha256": "x"},
            {"path": "a", "size_bytes": 2, "sha256": "y"},
        ],
    ],
)
def test_malformed_intake_members_are_archive_invalid(selected: Any) -> None:
    with pytest.raises(ContractError) as raised:
        loader.validate_intake_members(selected, _fail)
    assert raised.value.reason_code == "ARCHIVE_INVALID"


def test_valid_manifest_and_intake_pass_validation() -> None:
    manifest = _manifest()
    assert loader.validate_manifest_tables(manifest, _fail) is manifest
    members = loader.validate_intake_members(
        [{"path": "a", "size_bytes": 3, "sha256": "d" * 64}], _fail
    )
    assert set(members) == {"a"}


# --- 구현리뷰 2차 필수 회귀 ---------------------------------------------------------


@pytest.mark.parametrize(
    "profiles",
    [
        [{}],  # unhashable — set() 을 먼저 만들면 raw TypeError
        [[]],
        [1],
        [None],
        ["runtime", "runtime"],  # 중복
        [],  # 빈 list
        "runtime",  # list 아님
    ],
)
def test_malformed_included_by_profile_is_archive_invalid(profiles: Any) -> None:
    """unhashable·중복도 raw TypeError가 아니라 `ARCHIVE_INVALID`(2)다 (2차 필수 1)."""

    manifest = _manifest()
    manifest["metrology"]["included_by_profile"] = profiles
    with pytest.raises(ContractError) as raised:
        loader.validate_manifest_tables(manifest, _fail)
    assert raised.value.reason_code == "ARCHIVE_INVALID"
    assert raised.value.exit_code == 2


@pytest.mark.parametrize(
    "size",
    [True, False, -1, 1.0, "1", None],
)
def test_intake_size_bytes_rejects_bool_and_non_integer(size: Any) -> None:
    """`bool`은 `int`의 하위 타입이므로 명시적으로 배제한다 (2차 필수 1)."""

    entry = {"path": "a", "size_bytes": size, "sha256": "d" * 64}
    with pytest.raises(ContractError) as raised:
        loader.validate_intake_members([entry], _fail)
    assert raised.value.reason_code == "ARCHIVE_INVALID"
    assert raised.value.exit_code == 2


@pytest.mark.parametrize(
    "digest",
    ["x", "d" * 63, "d" * 65, "D" * 64, "g" * 64, "", 1, None],
)
def test_intake_sha256_requires_64_lowercase_hex(digest: Any) -> None:
    entry = {"path": "a", "size_bytes": 1, "sha256": digest}
    with pytest.raises(ContractError) as raised:
        loader.validate_intake_members([entry], _fail)
    assert raised.value.reason_code == "ARCHIVE_INVALID"
    assert raised.value.exit_code == 2


def test_canonical_metadata_still_passes_validation() -> None:
    """정상 artifact는 계속 통과한다 — fail-closed가 과하지 않은지 확인."""

    manifest = _manifest()
    assert loader.validate_manifest_tables(manifest, _fail) is manifest
    members = loader.validate_intake_members(
        [{"path": "a", "size_bytes": 0, "sha256": "0" * 64}], _fail
    )
    assert set(members) == {"a"}
