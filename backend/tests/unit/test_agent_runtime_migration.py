from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_agent_runtime as migration  # noqa: E402
import manifest_v3  # noqa: E402

from app.common.audit import EVENT_ENTITY_TYPE, AuditEvent  # noqa: E402
from app.common.enums import (  # noqa: E402
    ActionCode,
    AlarmSource,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
)


def _body() -> str:
    return migration.MIGRATION_PATH.read_text(encoding="utf-8")


def _split_table_items(block: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    quoted = False
    index = 0
    while index < len(block):
        character = block[index]
        if character == "'":
            current.append(character)
            if quoted and index + 1 < len(block) and block[index + 1] == "'":
                current.append("'")
                index += 2
                continue
            quoted = not quoted
        elif not quoted and character == "(":
            depth += 1
            current.append(character)
        elif not quoted and character == ")":
            depth -= 1
            current.append(character)
        elif not quoted and character == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    if item := "".join(current).strip():
        items.append(item)
    return items


def _expected_sql_type(catalog_type: str) -> str:
    if catalog_type.startswith("character varying"):
        return catalog_type.replace("character varying", "varchar")
    if catalog_type == "timestamp with time zone":
        return "timestamptz"
    if catalog_type.startswith("character"):
        return catalog_type.replace("character", "char")
    if catalog_type == "bigint":
        return "bigserial"
    return catalog_type


def _declared_default(
    declaration: str, *, table: str, column: migration.ColumnContract
) -> str | None:
    if _expected_sql_type(column.data_type) == "bigserial":
        return f"nextval('{table}_{column.name}_seq'::regclass)"
    match = re.search(
        r"\bDEFAULT\s+('(?:''|[^'])*'|[a-z0-9_]+(?:\(\))?)",
        declaration,
        flags=re.I,
    )
    if match is None:
        return None
    value = match.group(1)
    if value.startswith("'") and column.data_type.startswith("character varying"):
        return f"{value}::character varying"
    return value.lower() if value.lower() in {"false", "true", "now()"} else value


def _assert_sql_columns_match_catalog_contract(
    body: str,
    expected_tables: dict[str, tuple[migration.ColumnContract, ...]] | None = None,
) -> None:
    expected_tables = expected_tables or migration.EXPECTED_TABLE_COLUMNS
    for table, contracts in expected_tables.items():
        block = body.split(f"CREATE TABLE {table} (", 1)[1].split("\n);", 1)[0]
        declarations: dict[str, tuple[str, bool, str | None]] = {}
        for item in _split_table_items(block):
            match = re.match(r"([a-z_]+)\s+([a-z]+(?:\(\d+(?:,\d+)?\))?)", item)
            if match is None:
                continue
            name, declared_type = match.groups()
            contract = next((value for value in contracts if value.name == name), None)
            assert contract is not None, f"{table}: SQL-only column {name}"
            upper = item.upper()
            declarations[name] = (
                declared_type,
                "NOT NULL" not in upper and "PRIMARY KEY" not in upper,
                _declared_default(item, table=table, column=contract),
            )
        assert tuple(declarations) == tuple(contract.name for contract in contracts)
        expected: dict[str, tuple[str, bool, str | None]] = {}
        for contract in contracts:
            expected[contract.name] = (
                _expected_sql_type(contract.data_type),
                contract.nullable,
                contract.default,
            )
        assert declarations == expected


def test_sql_has_exact_runtime_object_inventory() -> None:
    _sql, statements = migration.load_and_validate_sql()
    assert len(statements) == 14
    assert sum(item.upper().startswith("DO ") for item in statements) == 1
    assert sum(item.upper().startswith("CREATE TABLE") for item in statements) == 9
    assert (
        sum(item.upper().startswith("CREATE UNIQUE INDEX") for item in statements) == 4
    )
    assert set(migration.EXPECTED_TABLE_COLUMNS) == set(migration.RUNTIME_TABLES)
    assert len(migration.EXPECTED_INDEX_NAMES) == 15
    assert len(migration.EXPECTED_SEQUENCE_NAMES) == 2


def test_sql_columns_match_python_catalog_contract() -> None:
    _assert_sql_columns_match_catalog_contract(_body())


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("thread_id varchar(36) NOT NULL", "thread_id varchar(36)"),
        (
            "started_at timestamptz NOT NULL DEFAULT now()",
            "started_at timestamptz NOT NULL",
        ),
        (
            "thread_id varchar(36) NOT NULL",
            "sql_only_column varchar(12),\n    thread_id varchar(36) NOT NULL",
        ),
    ],
)
def test_static_contract_rejects_sql_nullable_default_and_extra_column_drift(
    before: str, after: str
) -> None:
    mutated = _body().replace(before, after, 1)
    with pytest.raises(AssertionError):
        _assert_sql_columns_match_catalog_contract(mutated)


def test_static_contract_rejects_python_contract_column_omission() -> None:
    expected_tables = dict(migration.EXPECTED_TABLE_COLUMNS)
    expected_tables["agent_run"] = tuple(
        contract
        for contract in expected_tables["agent_run"]
        if contract.name != "thread_id"
    )
    with pytest.raises(AssertionError, match="SQL-only column thread_id"):
        _assert_sql_columns_match_catalog_contract(_body(), expected_tables)


def test_sql_guard_and_runner_lock_responsibility_are_explicit() -> None:
    body = migration._strip_comments(_body()).lower()
    assert "current_database() not in ('kosa_agent', 'kosa_agent_e2e')" in " ".join(
        body.split()
    )
    assert "select count(*) from action_history" in " ".join(body.split())
    assert "lock table" not in body
    assert "if not exists" not in body
    assert "fdc_alarm" not in body
    assert "insert into" not in body


def test_sql_uses_strict_alarm_refs_and_channel_delivery_key() -> None:
    body = _body().lower()
    assert "primary key (agent_run_id, alarm_source, alarm_id)" in body
    assert "primary key (action_id, channel)" in body
    assert "unique (agent_run_id, call_seq)" in body
    assert "unique (action_id)" not in body
    assert "references trace_alarm_history" not in body
    assert "references summary_alarm_history" not in body
    assert "references r03_alarm_history" not in body


def test_approval_request_excludes_auto_but_action_projection_keeps_it() -> None:
    approval_block = (
        _body()
        .split("CREATE TABLE approval_request", 1)[1]
        .split("CREATE TABLE action_delivery", 1)[0]
    )
    assert "'AUTO'" not in approval_block
    assert {item.value for item in ApprovalStatus} == {
        "AUTO",
        "PENDING",
        "APPROVED",
        "REJECTED",
        "EXPIRED",
    }


def test_runtime_enum_sets_match_canonical_contract() -> None:
    assert {item.value for item in RunStatus} == {
        "RUNNING",
        "WAITING_APPROVAL",
        "COMPLETED",
        "FAILED",
    }
    assert {item.value for item in ActionCode} == {
        "MONITORING",
        "WARNING",
        "EQP_HOLD",
    }
    assert {item.value for item in AlarmSource} == {"TRACE", "SUMMARY", "R03"}
    assert {item.value for item in FaultHypothesis} == {
        "FOC",
        "RFM",
        "MFD",
        "TMD",
        "OTH",
    }
    assert {item.value for item in DeliveryChannel} == {"EMAIL", "MES_MOCK"}
    assert {item.value for item in DeliveryStatus} == {
        "BLOCKED",
        "WAITING",
        "SENDING",
        "SENT",
        "FAILED",
        "CANCELED",
        "UNKNOWN",
    }


def test_audit_event_rename_is_atomic_and_mapping_is_total() -> None:
    assert len(AuditEvent) == 9
    assert set(EVENT_ENTITY_TYPE) == set(AuditEvent)
    assert AuditEvent.HYPOTHESIS_GENERATED.value == "HYPOTHESIS_GENERATED"
    with pytest.raises(ValidationError):
        TypeAdapter(AuditEvent).validate_python("CLASSIFICATION_COMPLETED")


def test_nullable_nonblank_contract_is_not_inverted() -> None:
    body = " ".join(_body().split())
    assert "actor_id IS NULL OR btrim(actor_id) <> ''" in body
    assert "provider_message_id IS NULL OR btrim(provider_message_id) <> ''" in body
    assert "coalesce(btrim(decided_by), '') <> ''" in body
    assert "autonomy_level smallint NOT NULL" in body
    assert "DEFAULT" not in body.split("autonomy_level", 1)[1].split(",", 1)[0]


def test_runtime_manifest_is_registered_from_column_contract() -> None:
    path = manifest_v3.resolve_bootstrap_manifest_path("runtime", "runtime_clean")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = migration.build_runtime_manifest()
    assert payload == expected
    assert payload["bootstrap_stage"] == "runtime_clean"
    assert payload["applied_migrations"] == [
        "001_reference_extensions",
        "002_agent_runtime_clean",
    ]
    assert len(payload["tables"]) == 23
    for table, columns in migration.EXPECTED_TABLE_COLUMNS.items():
        entry = payload["tables"][table]
        assert entry["columns"] == [column.name for column in columns]
        assert entry["verification_policy"] == "bootstrap_empty"
        assert entry["row_count"] == 0


def test_evaluation_runtime_clean_manifest_combination_is_forbidden() -> None:
    with pytest.raises(manifest_v3.ManifestMetadataError):
        manifest_v3.resolve_bootstrap_manifest_path("evaluation", "runtime_clean")


@pytest.mark.parametrize(
    "fragment",
    [
        "ux_agent_run_incident_active",
        "ux_agent_run_action_created",
        "ux_agent_run_action_incident",
        "ux_agent_run_alarm_representative",
    ],
)
def test_partial_unique_indexes_are_declared(fragment: str) -> None:
    assert f"CREATE UNIQUE INDEX {fragment}" in _body()
