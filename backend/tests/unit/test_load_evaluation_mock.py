from __future__ import annotations

import json
import sys
from contextlib import AbstractContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import load_evaluation_mock as loader  # noqa: E402
from db_target import BootstrapTarget  # noqa: E402

SHA = "a" * 64
OTHER_SHA = "b" * 64


def _target() -> BootstrapTarget:
    return BootstrapTarget("host", 5432, "user", "pw", "kosa_text2sql", "evaluation")


def _context() -> loader.ManifestContext:
    return loader._load_manifest_context(require_registered=True)


def _state(name: str = "ADOPTED", *, action_rows: int = 48) -> loader.DatabaseState:
    reference_rows = {table: 0 for table in loader.REFERENCE_TABLES}
    reference_hashes = {
        table: loader.manifest_v3.hash_canonical_rows(())
        for table in loader.REFERENCE_TABLES
    }
    return loader.DatabaseState(
        name,
        action_rows,
        _context().expected_hash
        if action_rows == 48
        else loader.manifest_v3.hash_canonical_rows(()),
        reference_rows,
        reference_hashes,
        173,
        SHA,
    )


def _identities(
    context: loader.ManifestContext | None = None,
) -> loader.IdentityContext:
    context = context or _context()
    adoption = {"manifest": context.manifest_sha256}
    fixture = {
        "artifact_type": "evaluation_mock_fixture_identity",
        "action_history": context.action_entry,
    }
    return loader.IdentityContext(
        adoption,
        loader.canonical_sha256(adoption),
        fixture,
        loader.canonical_sha256(fixture),
    )


class _Result:
    def __init__(self, rowcount: int = 0) -> None:
        self.rowcount = rowcount


class _Connection(AbstractContextManager["_Connection"]):
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.statements: list[tuple[str, Any]] = []

    def __enter__(self) -> _Connection:
        self.events.append("connection:open")
        return self

    def __exit__(self, *_: object) -> bool:
        self.events.append("connection:close")
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self.events)

    def exec_driver_sql(self, sql: str, parameters: Any = None) -> _Result:
        self.statements.append((" ".join(sql.split()), parameters))
        return _Result(len(parameters) if isinstance(parameters, list) else 0)


class _Transaction(AbstractContextManager["_Transaction"]):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> _Transaction:
        self.events.append("transaction:start")
        return self

    def __exit__(self, exc_type: Any, *_: object) -> bool:
        self.events.append("transaction:rollback" if exc_type else "transaction:commit")
        return False


class _Engine:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.connection = _Connection(self.events)

    def connect(self) -> _Connection:
        return self.connection

    def dispose(self) -> None:
        self.events.append("engine:dispose")


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch,
    before: loader.DatabaseState,
    after: loader.DatabaseState | None = None,
) -> tuple[_Engine, loader.IdentityContext]:
    engine = _Engine()
    identities = _identities()
    monkeypatch.setattr(loader, "_prepare_transaction", lambda *a, **k: None)
    monkeypatch.setattr(
        loader,
        "classify_database_state",
        lambda *a, **k: before,
    )
    monkeypatch.setattr(
        loader,
        "postcheck_database",
        lambda *a, **k: after or _state(),
    )
    monkeypatch.setattr(loader, "build_identity_context", lambda *a, **k: identities)
    return engine, identities


def test_manifest_has_mock_48_contract_and_matches_registered_file() -> None:
    context = _context()
    action = context.manifest["tables"]["action_history"]

    assert context.manifest["bootstrap_stage"] == "evaluation_mock"
    assert action == {
        "columns": loader._action_columns(),
        "verification_policy": "immutable_content",
        "row_count": 48,
        "content_hash": context.expected_hash,
        "fixture_type": "MOCK",
    }
    assert len(context.expected_rows) == 48
    assert context.expected_rows[0]["recipe_step_name"] is None
    assert context.expected_rows[0]["notify_status"] is None


def test_manifest_registration_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(loader, "MANIFEST_ROOT", tmp_path)

    assert loader.register_manifest(confirm=False) == "PREVIEW"
    assert list(tmp_path.glob("*.json")) == []
    assert loader.register_manifest(confirm=True) == "REGISTERED"
    assert loader.register_manifest(confirm=False) == "NO_OP"


def test_registered_manifest_preserves_future_reference_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = _context().manifest
    future = json.loads(json.dumps(current))
    future["tables"]["r03_alarm_history"]["verification_policy"] = "immutable_content"
    future["tables"]["r03_alarm_history"]["row_count"] = 3
    future["tables"]["r03_alarm_history"]["content_hash"] = OTHER_SHA
    path = tmp_path / "evaluation.evaluation_mock.json"
    loader.manifest_v3.atomic_save_json(path, future)
    monkeypatch.setattr(loader, "MANIFEST_ROOT", tmp_path)

    loaded = loader._load_manifest_context(require_registered=True)

    assert loaded.manifest == future
    assert loader.register_manifest(confirm=False) == "NO_OP"
    assert json.loads(path.read_text(encoding="utf-8")) == future


def test_action_insert_is_only_dml_target() -> None:
    connection = _Connection([])

    assert loader._insert_action_rows(connection, _context()) == 48
    assert len(connection.statements) == 1
    statement, rows = connection.statements[0]
    assert statement.startswith('INSERT INTO "action_history"')
    assert len(rows) == 48
    assert "action_history" not in loader.FORBIDDEN_DML_TABLES
    assert set(loader.corrected_loader.LOAD_TABLES).issubset(
        loader.FORBIDDEN_DML_TABLES
    )


@pytest.mark.parametrize(
    ("action_rows", "expected_state"),
    [
        ((), "EMPTY"),
        ("EXPECTED", "ADOPTED"),
        (({"action_id": "DRIFT"},), "DRIFT"),
    ],
)
def test_classify_database_state_covers_fixture_states(
    monkeypatch: pytest.MonkeyPatch,
    action_rows: tuple[dict[str, Any], ...] | str,
    expected_state: str,
) -> None:
    context = _context()
    expected = context.expected_rows if action_rows == "EXPECTED" else action_rows
    reference = SimpleNamespace(
        alarm_event_rows=173,
        schema_signature_sha256=SHA,
    )
    monkeypatch.setattr(
        loader.corrected_loader,
        "_preflight_001",
        lambda *_a, **_k: SimpleNamespace(schema_signature_sha256=SHA),
    )
    monkeypatch.setattr(
        loader,
        "_read_table",
        lambda _connection, table, _entry: (
            expected
            if table == "action_history"
            else tuple(context.base.expected_rows.get(table, ()))
        ),
    )
    monkeypatch.setattr(
        loader.reference_extensions,
        "postcheck_database",
        lambda *_a, **_k: reference,
    )

    state = loader.classify_database_state(object(), _target(), context)

    assert state.name == expected_state


def test_classify_database_state_marks_reference_drift_as_missing_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    monkeypatch.setattr(
        loader.corrected_loader,
        "_preflight_001",
        lambda *_a, **_k: SimpleNamespace(schema_signature_sha256=SHA),
    )
    monkeypatch.setattr(
        loader,
        "_read_table",
        lambda _connection, table, _entry: (
            ({"drift": True},)
            if table == "r03_alarm_history"
            else (
                context.expected_rows
                if table == "action_history"
                else tuple(context.base.expected_rows.get(table, ()))
            )
        ),
    )
    monkeypatch.setattr(
        loader.reference_extensions,
        "postcheck_database",
        lambda *_a, **_k: SimpleNamespace(
            alarm_event_rows=173,
            schema_signature_sha256=SHA,
        ),
    )

    state = loader.classify_database_state(object(), _target(), context)

    assert state.name == "MISSING_BASE"
    assert state.mismatched_tables == ("r03_alarm_history",)


def test_fixture_identity_uses_canonical_hash_and_excludes_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    first = loader.build_identity_context
    state = _state()
    marker = {
        "schema_signature_sha256": SHA,
        "migration_sha256": SHA,
    }
    target = _target()

    monkeypatch.setattr(
        loader.reference_extensions, "load_marker", lambda *a, **k: marker
    )
    identity = first(target, context, state)
    reordered = dict(reversed(list(identity.fixture_identity.items())))
    assert loader.canonical_sha256(reordered) == identity.fixture_identity_sha256

    changed_manifest = json.loads(json.dumps(context.manifest))
    changed_manifest["tables"]["r03_alarm_history"]["row_count"] = 3
    changed_context = loader.ManifestContext(
        context.base,
        changed_manifest,
        OTHER_SHA,
        context.expected_rows,
        context.expected_hash,
        context.action_entry,
    )
    changed = first(target, changed_context, state)
    assert changed.fixture_identity_sha256 == identity.fixture_identity_sha256
    assert (
        changed.adoption_input_identity_sha256
        != identity.adoption_input_identity_sha256
    )


def test_apply_adopts_existing_rows_without_dml_and_noop_without_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context()
    engine, _ = _patch_runner(monkeypatch, _state())
    monkeypatch.setattr(
        loader,
        "_insert_action_rows",
        lambda *a, **k: pytest.fail("ADOPTED must not insert"),
    )
    kwargs = {
        "recover_artifact": False,
        "change_reference": "GH-51",
        "engine_factory": lambda _target: engine,
        "marker_root": tmp_path / "markers",
        "report_root": tmp_path / "reports",
        "reference_marker_root": tmp_path / "reference",
    }

    assert loader.run_apply_or_recover(_target(), context, **kwargs) == "APPLIED"
    marker_file = loader.marker_path(root=tmp_path / "markers")
    marker = json.loads(marker_file.read_text())
    assert marker["status"] == "VERIFIED_EXISTING"
    before_bytes = marker_file.read_bytes()
    before_mtime = marker_file.stat().st_mtime_ns
    for receipt in loader._receipt_files(root=tmp_path / "reports"):
        receipt.unlink()

    assert loader.run_apply_or_recover(_target(), context, **kwargs) == "NO_OP"
    assert marker_file.read_bytes() == before_bytes
    assert marker_file.stat().st_mtime_ns == before_mtime
    assert loader._receipt_files(root=tmp_path / "reports") == []


def test_empty_state_inserts_exactly_48(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context()
    engine, _ = _patch_runner(monkeypatch, _state("EMPTY", action_rows=0), _state())

    assert (
        loader.run_apply_or_recover(
            _target(),
            context,
            recover_artifact=False,
            change_reference="GH-51",
            engine_factory=lambda _target: engine,
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
            reference_marker_root=tmp_path / "reference",
        )
        == "APPLIED"
    )
    inserts = [
        sql for sql, _ in engine.connection.statements if sql.startswith("INSERT")
    ]
    assert len(inserts) == 1
    marker = json.loads(loader.marker_path(root=tmp_path / "markers").read_text())
    assert marker["status"] == "APPLIED"


def test_marker_with_empty_database_is_lost_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context()
    engine, _ = _patch_runner(monkeypatch, _state("EMPTY", action_rows=0))
    marker = tmp_path / "markers" / "evaluation_mock.kosa_text2sql.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")

    with pytest.raises(loader.EvaluationMockStateError, match="LOST_DATA"):
        loader.run_apply_or_recover(
            _target(),
            context,
            recover_artifact=False,
            change_reference="GH-51",
            engine_factory=lambda _target: engine,
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
            reference_marker_root=tmp_path / "reference",
        )


def test_marker_rejects_action_or_fixture_identity_drift() -> None:
    context = _context()
    identities = _identities(context)
    receipt = {
        "status": "COMMITTED",
        "state_before": "ADOPTED",
        "schema_signature_sha256": SHA,
        "action_history_rows_after": 48,
        "action_history_hash_after": context.expected_hash,
        "alarm_event_rows": 173,
        "change_reference": "GH-51",
        "committed_at": "2026-08-18T01:00:00+00:00",
    }
    marker = loader._build_marker(receipt, context, identities)
    loader._validate_marker_against_state(marker, _state(), identities, context)

    marker["fixture_identity_sha256"] = OTHER_SHA
    with pytest.raises(loader.EvaluationMockArtifactError, match="fixture/DB"):
        loader._validate_marker_against_state(marker, _state(), identities, context)


def test_recovery_requires_exactly_one_matching_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context()
    engine, identities = _patch_runner(monkeypatch, _state())
    for index in (1, 2):
        receipt = loader._start_receipt(
            identities,
            _state(),
            change_reference="GH-51",
            root=tmp_path / "reports",
        )
        assert receipt["attempt"] == index

    with pytest.raises(loader.EvaluationMockArtifactError, match="정확히 1건"):
        loader.run_apply_or_recover(
            _target(),
            context,
            recover_artifact=True,
            change_reference="GH-51",
            engine_factory=lambda _target: engine,
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
            reference_marker_root=tmp_path / "reference",
        )


def test_recovery_promotes_one_started_receipt_without_dml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context()
    engine, identities = _patch_runner(monkeypatch, _state())
    loader._start_receipt(
        identities,
        _state(),
        change_reference="GH-51",
        root=tmp_path / "reports",
    )
    monkeypatch.setattr(
        loader,
        "_insert_action_rows",
        lambda *_a, **_k: pytest.fail("recovery must not insert"),
    )

    result = loader.run_apply_or_recover(
        _target(),
        context,
        recover_artifact=True,
        change_reference="GH-51",
        engine_factory=lambda _target: engine,
        marker_root=tmp_path / "markers",
        report_root=tmp_path / "reports",
        reference_marker_root=tmp_path / "reference",
    )

    assert result == "RECOVERED"
    marker = json.loads(loader.marker_path(root=tmp_path / "markers").read_text())
    assert marker["status"] == "VERIFIED_EXISTING"


def test_rehearsal_rolls_back_and_writes_no_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    engine, _ = _patch_runner(monkeypatch, _state())

    result = loader.run_rehearsal(
        _target(), context, engine_factory=lambda _target: engine
    )

    assert result.name == "ADOPTED"
    assert "transaction:rollback" in engine.events
    assert engine.events.count("transaction:commit") == 1


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--preflight"],
        ["--database", "kosa_text2sql", "--confirm-target", "wrong"],
        ["--register-manifests", "--database", "kosa_text2sql"],
        [
            "--database",
            "kosa_text2sql",
            "--rehearse",
            "--confirm-target",
            "kosa_text2sql",
            "--change-ref",
            "GH-51",
        ],
    ],
)
def test_cli_rejects_unsafe_combinations(argv: list[str]) -> None:
    with pytest.raises(loader.EvaluationMockError):
        loader._validate_cli(loader._parser().parse_args(argv))


@pytest.mark.parametrize(
    "argv",
    [
        ["--register-manifests"],
        ["--register-manifests", "--confirm"],
        ["--database", "kosa_text2sql", "--preflight"],
        [
            "--database",
            "kosa_text2sql",
            "--rehearse",
            "--confirm-target",
            "kosa_text2sql",
        ],
        [
            "--database",
            "kosa_text2sql",
            "--confirm-target",
            "kosa_text2sql",
            "--change-ref",
            "GH-51",
        ],
    ],
)
def test_cli_accepts_safe_combinations(argv: list[str]) -> None:
    loader._validate_cli(loader._parser().parse_args(argv))


def test_verifier_expected_stage_is_evaluation_mock() -> None:
    assert loader.bootstrap_verifier.EXPECTED_STAGES["kosa_text2sql"] == (
        "evaluation_mock"
    )
    report = loader.bootstrap_verifier._report_payload(
        [loader.bootstrap_verifier.CheckResult("files", "PASS", 0, {})]
    )
    assert "expected_intermediate_state" not in report
