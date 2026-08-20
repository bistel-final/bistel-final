from __future__ import annotations

import json
import sys
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# V5-CM-1.2 epoch 발급으로 kosa_0813 artifact가 격리돼 깨지는 테스트의 개별 skip.
# 해제 경로는 사유에 적힌 후속 Task가 소유한다(작업계획 §2.5·§6).
SKIP_KOSA_0813 = pytest.mark.skip(
    reason="kosa_0813 폐기(V5-CM-1.2) — 모듈은 V5-CM-1.6 삭제 대상"
)


SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import load_corrected_base as loader  # noqa: E402
import manifest_v3  # noqa: E402
from db_target import BootstrapTarget  # noqa: E402
from value_normalization import VALUE_NORMALIZATION_VERSION  # noqa: E402

SHA = "a" * 64
OTHER_SHA = "b" * 64


def _target(database: str = "kosa_agent") -> BootstrapTarget:
    profile = "evaluation" if database == "kosa_text2sql" else "runtime"
    return BootstrapTarget("host", 5432, "user", "pw", database, profile)


def _row(table: str) -> dict[str, Any]:
    return {column.name: None for column in loader.base_schema.BASE_COLUMNS[table]}


def _context() -> loader.InputContext:
    rows: dict[str, tuple[dict[str, Any], ...]] = {}
    for table in loader.LOAD_TABLES:
        if table == "dim_parameter":
            values = []
            names = {
                "ET_ESC": "ESC Temperature",
                "PH_DEV": "Developer Temperature",
                "PH_PEB": "PEB Temperature",
                "ET_CF4": "CF4 Flow",
                "ET_REFL": "Reflectometer",
                "PH_DOSE": "Dose",
                "PH_FOCUS": "Focus",
                "PH_PRES": "Pressure",
            }
            for parameter_id, parameter_name in names.items():
                item = _row(table)
                item.update(
                    {"parameter_id": parameter_id, "parameter_name": parameter_name}
                )
                values.append(item)
            rows[table] = tuple(values)
        else:
            rows[table] = (_row(table),)
    hashes = {
        table: manifest_v3.hash_canonical_rows(table_rows)
        for table, table_rows in rows.items()
    }
    identity = {
        "build_id": SHA,
        "value_normalization_version": VALUE_NORMALIZATION_VERSION,
    }
    bundle = SimpleNamespace(receipt={"build_id": SHA})
    return loader.InputContext(bundle, identity, SHA, rows, hashes)


def _state(
    name: str = "ADOPTED",
    *,
    action_rows: int = 0,
    fixup: dict[str, str] | None = None,
) -> loader.DatabaseState:
    return loader.DatabaseState(
        name,
        action_rows,
        {table: 0 for table in loader.REFERENCE_IMMUTABLE_TABLES},
        ("dim_parameter",) if name == "NEEDS_FIXUP" else (),
        fixup,
    )


def _postcheck(action_rows: int = 0) -> loader.PostcheckResult:
    context = _context()
    return loader.PostcheckResult(
        action_rows,
        173,
        {table: 0 for table in loader.REFERENCE_IMMUTABLE_TABLES},
        {table: len(context.expected_rows[table]) for table in loader.LOAD_TABLES},
        context.expected_hashes,
        OTHER_SHA,
    )


class _Result:
    def __init__(self, *, rowcount: int = 0) -> None:
        self.rowcount = rowcount


class _Connection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, Any]] = []

    def exec_driver_sql(self, sql: str, parameters: Any = None) -> _Result:
        self.statements.append((" ".join(sql.split()), parameters))
        if sql.lstrip().startswith("UPDATE"):
            return _Result(rowcount=1)
        if sql.lstrip().startswith("INSERT"):
            return _Result(rowcount=len(parameters))
        return _Result()


class _Transaction(AbstractContextManager["_Transaction"]):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> _Transaction:
        self.events.append("transaction:start")
        return self

    def __exit__(self, exc_type: Any, *_: object) -> bool:
        self.events.append("transaction:rollback" if exc_type else "transaction:commit")
        return False


class _ManagedConnection(_Connection, AbstractContextManager["_ManagedConnection"]):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def __enter__(self) -> _ManagedConnection:
        self.events.append("connection:open")
        return self

    def __exit__(self, *_: object) -> bool:
        self.events.append("connection:close")
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self.events)


class _Engine:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.connection = _ManagedConnection(self.events)

    def connect(self) -> _ManagedConnection:
        return self.connection

    def dispose(self) -> None:
        self.events.append("engine:dispose")


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
@SKIP_KOSA_0813
def test_corrected_base_manifest_has_exact_14_table_contract(profile: str) -> None:
    payload = loader.build_corrected_base_manifest(profile, _context())

    assert len(payload["tables"]) == 14
    assert payload["value_normalization_version"] == VALUE_NORMALIZATION_VERSION
    assert payload["tables"]["action_history"] == {
        "columns": [
            column.name for column in loader.base_schema.BASE_COLUMNS["action_history"]
        ],
        "verification_policy": "bootstrap_empty",
        "row_count": 0,
        "content_hash": loader.EMPTY_ROWS_SHA256,
    }
    assert payload["tables"]["nl_query_log"] == {
        "columns": [
            column[0]
            for column in loader.reference_extensions.EXPECTED_TABLE_COLUMNS[
                "nl_query_log"
            ]
        ],
        "verification_policy": "schema_only",
    }
    manifest_v3.validate_manifest_schema(
        payload,
        expected_artifact_type="db_bootstrap",
        expected_profile=profile,
        expected_stage="corrected_base",
        expected_archive_sha256=manifest_v3.load_dataset_epoch()["archive"]["sha256"],
    )


@SKIP_KOSA_0813
def test_registered_input_context_pins_active_build_and_normalized_hashes() -> None:
    context = loader._load_input_context()

    assert context.input_identity_sha256 == loader.canonical_sha256(
        context.input_identity
    )
    assert context.input_identity["value_normalization_version"] == (
        VALUE_NORMALIZATION_VERSION
    )
    assert set(context.expected_rows) == set(loader.LOAD_TABLES)
    assert all(
        context.expected_hashes[table]
        == manifest_v3.hash_canonical_rows(context.expected_rows[table])
        for table in loader.LOAD_TABLES
    )


@SKIP_KOSA_0813
def test_manifest_registration_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(loader, "MANIFEST_ROOT", tmp_path)
    context = _context()

    assert loader.register_manifests(confirm=False, context=context) == "PREVIEW"
    assert list(tmp_path.glob("*.json")) == []
    assert loader.register_manifests(confirm=True, context=context) == "REGISTERED"
    assert loader.register_manifests(confirm=False, context=context) == "NO_OP"
    assert {path.name for path in tmp_path.glob("*.json")} == {
        "runtime.corrected_base.json",
        "evaluation.corrected_base.json",
    }


def test_reference_tables_are_not_in_dml_allowlist() -> None:
    assert set(loader.LOAD_TABLES).isdisjoint(loader.FORBIDDEN_DML_TABLES)
    assert "action_history" not in loader.LOAD_TABLES
    assert loader.FIXUP_PARAMETER_IDS == ("ET_ESC", "PH_DEV", "PH_PEB")


def test_insert_uses_fk_order_and_executemany() -> None:
    connection = _Connection()

    inserted = loader._insert_corrected_rows(connection, _context())

    inserts = [sql for sql, _parameters in connection.statements]
    assert [sql.split('"')[1] for sql in inserts] == list(loader.LOAD_TABLES)
    assert inserted == sum(len(rows) for rows in _context().expected_rows.values())
    assert all(table not in " ".join(inserts) for table in loader.FORBIDDEN_DML_TABLES)


def test_fixup_only_updates_three_parameter_names() -> None:
    connection = _Connection()
    fixup = {
        "ET_ESC": "ESC Temperature",
        "PH_DEV": "Developer Temperature",
        "PH_PEB": "PEB Temperature",
    }

    assert loader._apply_fixup(connection, fixup) == 3
    assert len(connection.statements) == 3
    assert all("UPDATE dim_parameter" in sql for sql, _ in connection.statements)
    assert [parameters[1] for _, parameters in connection.statements] == list(
        loader.FIXUP_PARAMETER_IDS
    )


def test_fixup_rejects_extra_or_missing_key() -> None:
    with pytest.raises(loader.CorrectedBaseStateError, match="key"):
        loader._apply_fixup(_Connection(), {"ET_ESC": "name"})


def test_fixup_candidate_accepts_exact_three_name_differences() -> None:
    expected = list(_context().expected_rows["dim_parameter"])
    actual = [dict(row) for row in expected]
    old_names = {
        "ET_ESC": "ESC Temp",
        "PH_DEV": "Developer Temp",
        "PH_PEB": "PEB Temp",
    }
    for row in actual:
        if row["parameter_id"] in old_names:
            row["parameter_name"] = old_names[row["parameter_id"]]

    assert loader._fixup_candidate(actual, expected) == {
        "ET_ESC": "ESC Temperature",
        "PH_DEV": "Developer Temperature",
        "PH_PEB": "PEB Temperature",
    }


def test_fixup_candidate_rejects_other_column_difference() -> None:
    expected = list(_context().expected_rows["dim_parameter"])
    actual = [dict(row) for row in expected]
    actual[0]["unit"] = "different"
    assert loader._fixup_candidate(actual, expected) is None


@pytest.mark.parametrize(
    ("table_rows", "expected_state"),
    [
        ({table: () for table in loader.LOAD_TABLES}, "EMPTY"),
        (_context().expected_rows, "ADOPTED"),
    ],
)
def test_classify_empty_and_adopted(
    monkeypatch: pytest.MonkeyPatch,
    table_rows: dict[str, tuple[dict[str, Any], ...]],
    expected_state: str,
) -> None:
    monkeypatch.setattr(loader, "_preflight_001", lambda *args, **kwargs: None)
    monkeypatch.setattr(loader, "_read_reference_counts", lambda _connection: {})
    monkeypatch.setattr(
        loader,
        "_scalar",
        lambda _result: 0,
    )
    monkeypatch.setattr(
        loader, "_read_table", lambda _connection, table: table_rows[table]
    )

    result = loader.classify_database_state(_Connection(), _target(), _context())

    assert result.name == expected_state


def test_classify_exact_fixup_and_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _context().expected_rows
    actual = {
        table: tuple(dict(row) for row in rows) for table, rows in expected.items()
    }
    for row in actual["dim_parameter"]:
        if row["parameter_id"] in loader.FIXUP_PARAMETER_IDS:
            row["parameter_name"] = row["parameter_name"].replace("erature", "")
    monkeypatch.setattr(loader, "_preflight_001", lambda *args, **kwargs: None)
    monkeypatch.setattr(loader, "_read_reference_counts", lambda _connection: {})
    monkeypatch.setattr(loader, "_scalar", lambda _result: 0)
    monkeypatch.setattr(loader, "_read_table", lambda _connection, table: actual[table])

    result = loader.classify_database_state(_Connection(), _target(), _context())
    assert result.name == "NEEDS_FIXUP"

    actual["metrology"][0]["unit"] = "drift"
    result = loader.classify_database_state(_Connection(), _target(), _context())
    assert result.name == "DRIFT"
    assert set(result.mismatched_tables) == {"dim_parameter", "metrology"}


def test_preflight_rejects_missing_001(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        loader.reference_extensions,
        "load_and_validate_sql",
        lambda: ("sql", []),
    )
    monkeypatch.setattr(
        loader.reference_extensions,
        "inspect_database",
        lambda _connection: SimpleNamespace(state="ABSENT"),
    )
    monkeypatch.setattr(
        loader.reference_extensions, "load_marker", lambda *a, **k: None
    )

    with pytest.raises(loader.CorrectedBaseStateError, match="MISSING_001"):
        loader._preflight_001(_Connection(), _target())


def test_preflight_rejects_base_schema_contract_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        loader.reference_extensions,
        "load_and_validate_sql",
        lambda: ("sql", []),
    )
    monkeypatch.setattr(
        loader.reference_extensions,
        "inspect_database",
        lambda _connection: SimpleNamespace(
            state="PRESENT", schema_signature_sha256=SHA
        ),
    )
    monkeypatch.setattr(
        loader.reference_extensions,
        "load_marker",
        lambda *a, **k: {"schema_signature_sha256": SHA},
    )
    monkeypatch.setattr(
        loader.base_schema,
        "build_actual_signature",
        lambda _connection: ({"tables": {}}, (0, 0, 0)),
    )

    with pytest.raises(loader.CorrectedBaseStateError, match="PK·FK·CHECK"):
        loader._preflight_001(_Connection(), _target())


def test_prepare_transaction_uses_repeatable_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        loader,
        "prepare_transaction",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    connection = _Connection()

    loader._prepare_transaction(connection, _target(), readonly=False)

    assert calls[0]["isolation_level"] == "REPEATABLE READ"
    assert calls[0]["readonly"] is False
    assert connection.statements == [("SET LOCAL statement_timeout = '120s'", None)]


def _started_receipt(
    target: BootstrapTarget, context: loader.InputContext
) -> dict[str, Any]:
    return {
        "artifact_type": "corrected_base_receipt",
        "format_version": 1,
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "attempt": 1,
        "status": "STARTED",
        **loader._artifact_common(target, context, change_reference="GH-50"),
        "started_at": datetime.now(UTC).isoformat(),
        "state_before": "ADOPTED",
        "action_history_rows_before": 48 if target.profile == "evaluation" else 0,
        "reference_rows_before": {
            table: 0 for table in loader.REFERENCE_IMMUTABLE_TABLES
        },
    }


@pytest.mark.parametrize("database", ["kosa_agent", "kosa_text2sql"])
def test_receipt_and_profile_artifact_contract(database: str, tmp_path: Path) -> None:
    target = _target(database)
    context = _context()
    started = _started_receipt(target, context)
    loader.validate_receipt(started, target)
    committed = loader._commit_receipt(
        started,
        target,
        _postcheck(48 if target.profile == "evaluation" else 0),
        inserted_rows=0,
        fixed_rows=0,
        root=tmp_path,
    )

    artifact = loader._build_profile_artifact(committed, target, context)
    loader.validate_profile_artifact(artifact, target)

    if target.profile == "runtime":
        assert artifact["artifact_type"] == "corrected_base"
        assert artifact["status"] == "VERIFIED_EXISTING"
    else:
        assert artifact["artifact_type"] == "corrected_base_alignment"
        assert artifact["stage_acceptance_status"] == "PENDING"
        assert artifact["next_stage_task"] == "V4-CM-2.3"


def test_profile_artifact_rejects_secret_and_naive_time() -> None:
    target = _target("kosa_text2sql")
    context = _context()
    receipt = _started_receipt(target, context)
    receipt.update(
        {
            "status": "COMMITTED",
            "committed_at": datetime.now(UTC).isoformat(),
            "action_history_rows_after": 48,
            "reference_rows_after": receipt["reference_rows_before"],
            "inserted_rows": 0,
            "fixed_rows": 0,
            "alarm_event_rows": 173,
            "schema_signature_sha256": OTHER_SHA,
        }
    )
    artifact = loader._build_profile_artifact(receipt, target, context)
    artifact["recorded_at"] = "2026-08-17T12:00:00"
    with pytest.raises(loader.CorrectedBaseArtifactError, match="timezone-aware"):
        loader.validate_profile_artifact(artifact, target)
    artifact["recorded_at"] = datetime.now(UTC).isoformat()
    artifact["password"] = "secret"
    with pytest.raises(loader.CorrectedBaseArtifactError):
        loader.validate_profile_artifact(artifact, target)


def test_receipt_rejects_invalid_change_reference_and_abort_reason() -> None:
    target = _target()
    receipt = _started_receipt(target, _context())
    receipt["change_reference"] = "free-form"
    with pytest.raises(loader.CorrectedBaseArtifactError, match="change reference"):
        loader.validate_receipt(receipt, target)

    receipt["change_reference"] = "GH-50"
    receipt.update(
        {
            "status": "ABORTED",
            "aborted_at": datetime.now(UTC).isoformat(),
            "abort_reason": "UNKNOWN",
        }
    )
    with pytest.raises(loader.CorrectedBaseArtifactError, match="abort reason"):
        loader.validate_receipt(receipt, target)


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch,
    state: loader.DatabaseState,
    result: loader.PostcheckResult,
) -> _Engine:
    engine = _Engine()
    monkeypatch.setattr(loader, "_prepare_transaction", lambda *a, **k: None)
    monkeypatch.setattr(
        loader,
        "classify_database_state",
        lambda *args, **kwargs: state,
    )
    monkeypatch.setattr(loader, "postcheck_database", lambda *a, **k: result)
    return engine


@pytest.mark.parametrize(
    ("database", "expected_artifact", "action_rows"),
    [
        ("kosa_agent", "corrected_base", 0),
        ("kosa_text2sql", "corrected_base_alignment", 48),
    ],
)
def test_apply_creates_profile_specific_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    database: str,
    expected_artifact: str,
    action_rows: int,
) -> None:
    target = _target(database)
    context = _context()
    engine = _patch_runner(
        monkeypatch, _state(action_rows=action_rows), _postcheck(action_rows)
    )

    status = loader.run_apply_or_recover(
        target,
        context,
        recover_artifact=False,
        change_reference="GH-50",
        engine_factory=lambda _target: engine,
        marker_root=tmp_path / "markers",
        report_root=tmp_path / "reports",
    )

    assert status == "APPLIED"
    artifact_path = loader._profile_artifact_path(
        target,
        marker_root=tmp_path / "markers",
        report_root=tmp_path / "reports",
    )
    assert json.loads(artifact_path.read_text())["artifact_type"] == expected_artifact
    assert engine.events[-2:] == ["connection:close", "engine:dispose"]


def test_noop_does_not_create_second_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target()
    context = _context()
    engine = _patch_runner(monkeypatch, _state(), _postcheck())
    kwargs = {
        "recover_artifact": False,
        "change_reference": "GH-50",
        "engine_factory": lambda _target: engine,
        "marker_root": tmp_path / "markers",
        "report_root": tmp_path / "reports",
    }
    loader.run_apply_or_recover(target, context, **kwargs)
    receipts_before = loader._receipt_files(target.database, root=tmp_path / "reports")

    assert loader.run_apply_or_recover(target, context, **kwargs) == "NO_OP"
    assert (
        loader._receipt_files(target.database, root=tmp_path / "reports")
        == receipts_before
    )


def test_noop_rejects_profile_artifact_without_matching_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target()
    context = _context()
    engine = _patch_runner(monkeypatch, _state(), _postcheck())
    kwargs = {
        "recover_artifact": False,
        "change_reference": "GH-50",
        "engine_factory": lambda _target: engine,
        "marker_root": tmp_path / "markers",
        "report_root": tmp_path / "reports",
    }
    loader.run_apply_or_recover(target, context, **kwargs)
    artifact_path = loader.marker_path(target.database, root=tmp_path / "markers")
    artifact = json.loads(artifact_path.read_text())
    artifact["receipt_sha256"] = "f" * 64
    manifest_v3.atomic_save_json(artifact_path, artifact)

    with pytest.raises(loader.CorrectedBaseArtifactError, match="receipt"):
        loader.run_apply_or_recover(target, context, **kwargs)


@pytest.mark.parametrize("database", ["kosa_agent", "kosa_text2sql"])
@pytest.mark.parametrize("crash_point", ["database", "receipt"])
def test_crash_is_recoverable_from_started_or_committed_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    database: str,
    crash_point: str,
) -> None:
    target = _target(database)
    context = _context()
    action_rows = 48 if target.profile == "evaluation" else 0
    engine = _patch_runner(
        monkeypatch, _state(action_rows=action_rows), _postcheck(action_rows)
    )

    with pytest.raises(RuntimeError, match="crash"):
        loader.run_apply_or_recover(
            target,
            context,
            recover_artifact=False,
            change_reference="GH-50",
            engine_factory=lambda _target: engine,
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
            after_database_commit=(
                (lambda: (_ for _ in ()).throw(RuntimeError("crash")))
                if crash_point == "database"
                else None
            ),
            after_receipt_commit=(
                (lambda: (_ for _ in ()).throw(RuntimeError("crash")))
                if crash_point == "receipt"
                else None
            ),
        )
    receipt = loader._load_receipts(target, root=tmp_path / "reports")[0]
    assert receipt["status"] == (
        "STARTED" if crash_point == "database" else "COMMITTED"
    )

    recovery_engine = _patch_runner(
        monkeypatch, _state(action_rows=action_rows), _postcheck(action_rows)
    )
    assert (
        loader.run_apply_or_recover(
            target,
            context,
            recover_artifact=True,
            change_reference="GH-50",
            engine_factory=lambda _target: recovery_engine,
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
        )
        == "RECOVERED"
    )


def test_rehearsal_rolls_back_without_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target("kosa_agent_e2e")
    context = _context()
    engine = _Engine()
    monkeypatch.setattr(loader, "_prepare_transaction", lambda *a, **k: None)
    monkeypatch.setattr(
        loader,
        "classify_database_state",
        lambda *a, **k: _state("EMPTY"),
    )
    monkeypatch.setattr(
        loader,
        "_insert_corrected_rows",
        lambda *a, **k: sum(len(rows) for rows in context.expected_rows.values()),
    )
    monkeypatch.setattr(loader, "postcheck_database", lambda *a, **k: _postcheck())

    result = loader.run_rehearsal(
        target, context, engine_factory=lambda _target: engine
    )

    assert result.action_history_rows == 0
    assert engine.events.count("transaction:rollback") == 1
    assert engine.events.count("transaction:commit") == 1


def test_recovery_rejects_two_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target()
    context = _context()
    for attempt in (1, 2):
        receipt = _started_receipt(target, context)
        receipt["operation_id"] = f"{attempt:08d}-1111-4111-8111-111111111111"
        receipt["attempt"] = attempt
        loader.validate_receipt(receipt, target)
        loader._save_artifact(
            loader.receipt_path(
                target.database,
                receipt["operation_id"],
                root=tmp_path / "reports",
            ),
            receipt,
        )
    engine = _patch_runner(monkeypatch, _state(), _postcheck())

    with pytest.raises(loader.CorrectedBaseArtifactError, match="정확히 1건"):
        loader.run_apply_or_recover(
            target,
            context,
            recover_artifact=True,
            change_reference="GH-50",
            engine_factory=lambda _target: engine,
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
        )


def test_transaction_failure_aborts_started_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target()
    context = _context()
    engine = _patch_runner(monkeypatch, _state("EMPTY"), _postcheck())
    monkeypatch.setattr(
        loader,
        "_insert_corrected_rows",
        lambda *args: (_ for _ in ()).throw(RuntimeError("FK error")),
    )

    with pytest.raises(RuntimeError, match="FK error"):
        loader.run_apply_or_recover(
            target,
            context,
            recover_artifact=False,
            change_reference="GH-50",
            engine_factory=lambda _target: engine,
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
        )

    receipts = loader._load_receipts(target, root=tmp_path / "reports")
    assert receipts[0]["status"] == "ABORTED"
    assert "transaction:rollback" in engine.events


def test_rolled_back_started_receipt_is_aborted_before_new_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target()
    context = _context()
    prior = _started_receipt(target, context)
    prior["state_before"] = "EMPTY"
    loader.validate_receipt(prior, target)
    loader._save_artifact(
        loader.receipt_path(
            target.database,
            prior["operation_id"],
            root=tmp_path / "reports",
        ),
        prior,
    )
    engine = _patch_runner(monkeypatch, _state("EMPTY"), _postcheck())
    monkeypatch.setattr(
        loader,
        "_insert_corrected_rows",
        lambda *args: sum(len(rows) for rows in context.expected_rows.values()),
    )

    assert (
        loader.run_apply_or_recover(
            target,
            context,
            recover_artifact=False,
            change_reference="GH-50",
            engine_factory=lambda _target: engine,
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
        )
        == "APPLIED"
    )
    receipts = loader._load_receipts(target, root=tmp_path / "reports")
    assert [receipt["status"] for receipt in receipts] == ["ABORTED", "COMMITTED"]
    assert receipts[0]["abort_reason"] == "PREVIOUS_TRANSACTION_NOT_COMMITTED"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--preflight"],
        ["--database", "kosa_agent", "--confirm-target", "wrong"],
        ["--rehearse", "--database", "kosa_agent", "--confirm-target", "kosa_agent"],
        ["--database", "kosa_agent", "--confirm-target", "kosa_agent"],
        ["--register-manifests", "--database", "kosa_agent"],
    ],
)
def test_cli_rejects_unsafe_option_combinations(argv: list[str]) -> None:
    args = loader._parser().parse_args(argv)
    with pytest.raises(loader.CorrectedBaseError):
        loader._validate_cli(args)


@pytest.mark.parametrize(
    "argv",
    [
        ["--database", "kosa_agent", "--preflight"],
        [
            "--database",
            "kosa_agent_e2e",
            "--rehearse",
            "--confirm-target",
            "kosa_agent_e2e",
        ],
        [
            "--database",
            "kosa_agent",
            "--confirm-target",
            "kosa_agent",
            "--change-ref",
            "GH-50",
        ],
        ["--register-manifests"],
        ["--register-manifests", "--confirm"],
    ],
)
def test_cli_accepts_safe_option_combinations(argv: list[str]) -> None:
    loader._validate_cli(loader._parser().parse_args(argv))
