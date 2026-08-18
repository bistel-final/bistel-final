from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import manifest_v3  # noqa: E402
import verify_bootstrap_state as verifier  # noqa: E402
from db_target import host_fingerprint  # noqa: E402


class FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> FakeMappings:
        return FakeMappings(self._rows)

    def scalar_one(self) -> Any:
        return self._scalar


class FakeTransaction:
    def __enter__(self) -> FakeTransaction:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeConnection:
    def __init__(self, database: str, manifest: dict[str, Any]) -> None:
        self.database = database
        self.manifest = manifest
        self.statements: list[str] = []

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def exec_driver_sql(self, statement: str, parameters: Any = None) -> FakeResult:
        normalized = " ".join(statement.split())
        self.statements.append(normalized)
        if "current_database() AS database_name" in normalized:
            return FakeResult(
                [
                    {
                        "database_name": self.database,
                        "public_exists": True,
                        "can_use": True,
                    }
                ]
            )
        if "FROM information_schema.tables" in normalized:
            return FakeResult(
                [{"table_name": table} for table in self.manifest["tables"]]
            )
        if normalized.startswith("SELECT has_table_privilege"):
            return FakeResult(scalar=True)
        if "format_type(a.atttypid" in normalized and parameters:
            table = parameters[0]
            if table in verifier.base_schema.BASE_COLUMNS:
                contracts = verifier.base_schema.BASE_COLUMNS[table]
                return FakeResult(
                    [
                        {
                            "column_name": column.name,
                            "data_type": column.data_type,
                        }
                        for column in contracts
                    ]
                )
            contracts = verifier.reference_extensions.EXPECTED_TABLE_COLUMNS[table]
            return FakeResult(
                [
                    {"column_name": name, "data_type": data_type}
                    for name, data_type, _nullable in contracts
                ]
            )
        if normalized.startswith("SELECT count(*) FROM"):
            return FakeResult(scalar=0)
        if normalized.startswith("SELECT"):
            return FakeResult([])
        return FakeResult()


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def _manifest(profile: str = "runtime") -> dict[str, Any]:
    path = verifier.manifest_v3.resolve_bootstrap_manifest_path(profile, "base_schema")
    return json.loads(path.read_text(encoding="utf-8"))


class CorrectedConnection(FakeConnection):
    def __init__(
        self,
        database: str,
        manifest: dict[str, Any],
        rows: dict[str, list[dict[str, Any]]],
    ) -> None:
        super().__init__(database, manifest)
        self.rows = rows

    def exec_driver_sql(self, statement: str, parameters: Any = None) -> FakeResult:
        normalized = " ".join(statement.split())
        if normalized.startswith("SELECT count(*) FROM"):
            table = normalized.split('"')[1]
            return FakeResult(scalar=len(self.rows.get(table, [])))
        if normalized.startswith("SELECT ") and ' FROM "' in normalized:
            table = normalized.split(' FROM "', 1)[1].split('"', 1)[0]
            return FakeResult(self.rows.get(table, []))
        return super().exec_driver_sql(statement, parameters)


def _stub_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[verifier.ActiveBundle, dict[str, Any]]:
    candidate = json.loads(
        manifest_v3.CORRECTED_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    tables = {
        table: verifier.corrected_builder.TableData(("id",), ())
        for table in candidate["tables"]
    }
    bundle = verifier.ActiveBundle(
        receipt={
            "build_id": "1" * 64,
            "generator_sha256": "2" * 64,
        },
        receipt_sha256="3" * 64,
        report={},
        tables=tables,
    )
    monkeypatch.setattr(verifier, "_load_active_bundle", lambda **_k: bundle)
    monkeypatch.setattr(
        verifier,
        "_corrected_candidate",
        lambda *_a, **_k: candidate,
    )
    monkeypatch.setattr(
        verifier,
        "_file_contract_details",
        lambda *_a, **_k: {
            "table_count": 9,
            "pk_duplicate_count": 0,
            "fk_violation_count": 0,
        },
    )
    return bundle, candidate


def _file_role_fixture() -> tuple[dict[str, Any], verifier.ActiveBundle]:
    source_tables: dict[str, Any] = {}
    corrected_tables: dict[str, verifier.corrected_builder.TableData] = {}
    classified = (
        verifier.UNCHANGED_TABLES | verifier.CHANGED_TABLES | verifier.NEW_TABLES
    )
    for table in sorted(classified):
        source_rows = ({"id": f"{table}-source"},)
        if table not in verifier.NEW_TABLES:
            source_tables[table] = {
                "columns": ["id"],
                "row_count": 1,
                "content_hash": manifest_v3.hash_canonical_rows(source_rows),
            }
        corrected_rows = (
            ({"id": f"{table}-corrected"},)
            if table in verifier.CHANGED_TABLES | verifier.NEW_TABLES
            else source_rows
        )
        corrected_tables[table] = verifier.corrected_builder.TableData(
            ("id",), corrected_rows
        )
    bundle = verifier.ActiveBundle(
        receipt={},
        receipt_sha256="0" * 64,
        report={},
        tables=corrected_tables,
    )
    return {"tables": source_tables}, bundle


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET value = 1",
        "DELETE FROM t",
        "CREATE TABLE t(id int)",
        "ALTER TABLE t ADD COLUMN value int",
        "DROP TABLE t",
        "SELECT 1; DELETE FROM t",
    ],
)
def test_sql_guard_rejects_mutation(statement: str) -> None:
    with pytest.raises(manifest_v3.VerificationError, match="read-only"):
        verifier._sql(SimpleNamespace(exec_driver_sql=lambda *_: None), statement)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1",
        "SET TRANSACTION READ ONLY",
        "SET LOCAL search_path = public",
        "SET LOCAL statement_timeout = '30s'",
    ],
)
def test_sql_guard_allows_read_only_statements(statement: str) -> None:
    calls: list[str] = []
    connection = SimpleNamespace(exec_driver_sql=lambda sql: calls.append(sql))
    verifier._sql(connection, statement)
    assert calls == [statement]


@pytest.mark.parametrize(
    ("actual", "expected", "counts", "state"),
    [
        (set(), {"a"}, {}, "NO_SCHEMA"),
        ({"a", "x"}, {"a"}, {"a": 0}, "UNKNOWN"),
        ({"a"}, {"a"}, {"a": 0}, "BASE_SCHEMA"),
        ({"a"}, {"a"}, {"a": 1}, "EARLY_DATA"),
    ],
)
def test_inventory_state(
    actual: set[str], expected: set[str], counts: dict[str, int], state: str
) -> None:
    assert verifier._inventory_state(actual, expected, counts) == state


def test_key_duplicate_count_counts_extra_rows() -> None:
    rows = [{"id": "A"}, {"id": "A"}, {"id": "A"}, {"id": "B"}]
    assert verifier._key_duplicate_count(rows, ("id",)) == 2


def test_file_roles_accept_exact_partition_and_expected_differences() -> None:
    source, bundle = _file_role_fixture()
    verifier._validate_file_roles(source, bundle)


@pytest.mark.parametrize("table", sorted(verifier.CHANGED_TABLES))
def test_file_roles_reject_unchanged_correction_target(table: str) -> None:
    source, bundle = _file_role_fixture()
    tables = dict(bundle.tables)
    tables[table] = verifier.corrected_builder.TableData(
        ("id",), ({"id": f"{table}-source"},)
    )
    drifted = verifier.ActiveBundle(
        bundle.receipt,
        bundle.receipt_sha256,
        bundle.report,
        tables,
    )
    with pytest.raises(manifest_v3.ArtifactMismatchError, match="source와 동일"):
        verifier._validate_file_roles(source, drifted)


def test_file_roles_reject_new_table_already_in_source() -> None:
    source, bundle = _file_role_fixture()
    source["tables"]["dim_parameter"] = {
        "columns": ["id"],
        "row_count": 1,
        "content_hash": "0" * 64,
    }
    with pytest.raises(manifest_v3.ArtifactMismatchError, match="신규 table"):
        verifier._validate_file_roles(source, bundle)


def test_file_roles_reject_incomplete_corrected_partition() -> None:
    source, bundle = _file_role_fixture()
    tables = dict(bundle.tables)
    tables.pop("metrology")
    incomplete = verifier.ActiveBundle(
        bundle.receipt,
        bundle.receipt_sha256,
        bundle.report,
        tables,
    )
    with pytest.raises(manifest_v3.ManifestSchemaError, match="분류가 완전하지"):
        verifier._validate_file_roles(source, incomplete)


def test_aggregate_priority() -> None:
    ok = verifier.CheckResult("a", verifier.STATUS_PASS, 0, {})
    unavailable = verifier.CheckResult("b", verifier.STATUS_UNVERIFIABLE, 7, {})
    unregistered = verifier.CheckResult("c", verifier.STATUS_NOT_REGISTERED, 6, {})
    mismatch = verifier.CheckResult("d", verifier.STATUS_FAIL, 1, {})
    assert verifier.aggregate_exit_code([ok]) == 0
    assert verifier.aggregate_exit_code([ok, unavailable]) == 7
    assert verifier.aggregate_exit_code([unavailable, unregistered]) == 6
    assert verifier.aggregate_exit_code([unregistered, mismatch]) == 1


@pytest.mark.parametrize(
    ("results", "expected_status", "expected_exit"),
    [
        (
            [
                verifier.CheckResult(
                    "db", verifier.STATUS_UNVERIFIABLE, verifier.EXIT_UNVERIFIABLE, {}
                )
            ],
            verifier.STATUS_UNVERIFIABLE,
            verifier.EXIT_UNVERIFIABLE,
        ),
        (
            [
                verifier.CheckResult(
                    "db", verifier.STATUS_UNVERIFIABLE, verifier.EXIT_UNVERIFIABLE, {}
                ),
                verifier.CheckResult(
                    "files", verifier.STATUS_FAIL, verifier.EXIT_MISMATCH, {}
                ),
            ],
            verifier.STATUS_FAIL,
            verifier.EXIT_MISMATCH,
        ),
        (
            [
                verifier.CheckResult(
                    "files",
                    verifier.STATUS_NOT_REGISTERED,
                    verifier.EXIT_NOT_REGISTERED,
                    {},
                )
            ],
            verifier.STATUS_NOT_REGISTERED,
            verifier.EXIT_NOT_REGISTERED,
        ),
    ],
)
def test_report_overall_status_matches_aggregate_exit(
    results: list[verifier.CheckResult], expected_status: str, expected_exit: int
) -> None:
    report = verifier._report_payload(results)
    assert report["overall_status"] == expected_status
    assert report["exit_code"] == expected_exit


def test_failure_result_uses_safe_unverifiable_reason_code() -> None:
    result = verifier._failure_result(
        "db",
        verifier.UnverifiableError(
            "hidden connection failure",
            reason_code="CONNECT_OR_QUERY_FAILED",
        ),
    )
    assert result.details == {"reason_code": "CONNECT_OR_QUERY_FAILED"}


def test_target_error_is_normalized_without_original_message() -> None:
    error = verifier._target_unverifiable(
        verifier.TargetValidationError("비밀번호 설정이 비어 있습니다")
    )
    assert error.reason_code == "MISSING_CONFIGURATION"
    assert "비밀번호" not in str(error)


def test_failure_result_keeps_safe_inventory_details() -> None:
    error = verifier.AcceptanceMismatchError(
        "hidden",
        details={
            "inventory": "EARLY_DATA",
            "expected_stage": "base_schema",
        },
    )
    result = verifier._failure_result("kosa_agent", error)
    assert result.details == {
        "reason_code": "MISMATCH",
        "inventory": "EARLY_DATA",
        "expected_stage": "base_schema",
    }


def test_check_result_rejects_sensitive_details() -> None:
    result = verifier.CheckResult(
        "db", verifier.STATUS_FAIL, 1, {"password": "do-not-print"}
    )
    with pytest.raises(manifest_v3.ManifestSchemaError, match="금지된"):
        result.as_dict()


def test_database_base_schema_passes_with_read_only_statements() -> None:
    manifest = _manifest()
    connection = FakeConnection("kosa_agent", manifest)
    engine = FakeEngine(connection)
    result = verifier.verify_database(
        "kosa_agent",
        "base_schema",
        environ={
            "POSTGRES_BOOTSTRAP_HOST": "shared.example",
            "POSTGRES_BOOTSTRAP_PORT": "5432",
            "POSTGRES_BOOTSTRAP_USER": "reader",
            "POSTGRES_BOOTSTRAP_PASSWORD": "hidden",
            "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(
                "shared.example", 5432
            ),
        },
        engine_factory=lambda _: engine,
    )
    assert result.status == verifier.STATUS_PASS
    assert result.details["inventory"] == "BASE_SCHEMA"
    assert connection.statements[:3] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL search_path = public",
        "SET LOCAL statement_timeout = '30s'",
    ]
    assert all(
        statement.upper().startswith(verifier.READ_ONLY_PREFIXES)
        for statement in connection.statements
    )
    assert engine.disposed is True


def test_database_requires_explicit_registered_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(
        manifest_v3.BOOTSTRAP_MANIFEST_REGISTRY,
        ("runtime", "corrected_base"),
        tmp_path / "missing.json",
    )
    with pytest.raises(manifest_v3.NotRegisteredError):
        verifier.verify_database(
            "kosa_agent",
            "corrected_base",
            environ={},
            engine_factory=lambda _: pytest.fail("DB connect must not run"),
        )


def test_database_corrected_base_passes_normalized_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import load_corrected_base as loader

    context = loader._load_input_context()
    manifest = json.loads(
        manifest_v3.resolve_bootstrap_manifest_path(
            "runtime", "corrected_base"
        ).read_text(encoding="utf-8")
    )
    rows = {
        table: [dict(row) for row in context.expected_rows.get(table, ())]
        for table in manifest["tables"]
    }
    connection = CorrectedConnection("kosa_agent", manifest, rows)
    engine = FakeEngine(connection)
    monkeypatch.setattr(
        verifier.reference_extensions,
        "postcheck_database",
        lambda *_a, **_k: SimpleNamespace(),
    )

    result = verifier.verify_database(
        "kosa_agent",
        "corrected_base",
        environ={
            "POSTGRES_BOOTSTRAP_HOST": "shared.example",
            "POSTGRES_BOOTSTRAP_PORT": "5432",
            "POSTGRES_BOOTSTRAP_USER": "reader",
            "POSTGRES_BOOTSTRAP_PASSWORD": "hidden",
            "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(
                "shared.example", 5432
            ),
        },
        engine_factory=lambda _: engine,
    )

    assert result.status == verifier.STATUS_PASS
    assert result.details["expected_stage"] == "corrected_base"
    assert result.details["action_history_rows"] == 0


def test_evaluation_corrected_base_failure_is_scoped_to_action_48(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import load_corrected_base as loader

    context = loader._load_input_context()
    manifest = json.loads(
        manifest_v3.resolve_bootstrap_manifest_path(
            "evaluation", "corrected_base"
        ).read_text(encoding="utf-8")
    )
    rows = {
        table: [dict(row) for row in context.expected_rows.get(table, ())]
        for table in manifest["tables"]
    }
    rows["action_history"] = [
        {
            column.name: (f"ACT-{index:04d}" if column.name == "action_id" else None)
            for column in verifier.base_schema.BASE_COLUMNS["action_history"]
        }
        for index in range(48)
    ]
    connection = CorrectedConnection("kosa_text2sql", manifest, rows)
    monkeypatch.setattr(
        verifier.reference_extensions,
        "postcheck_database",
        lambda *_a, **_k: SimpleNamespace(),
    )

    with pytest.raises(verifier.AcceptanceMismatchError) as captured:
        verifier.verify_database(
            "kosa_text2sql",
            "corrected_base",
            environ={
                "POSTGRES_BOOTSTRAP_HOST": "shared.example",
                "POSTGRES_BOOTSTRAP_PORT": "5432",
                "POSTGRES_BOOTSTRAP_USER": "reader",
                "POSTGRES_BOOTSTRAP_PASSWORD": "hidden",
                "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(
                    "shared.example", 5432
                ),
            },
            engine_factory=lambda _: FakeEngine(connection),
        )

    assert captured.value.details == {
        "profile": "evaluation",
        "expected_stage": "corrected_base",
        "inventory": "EARLY_DATA",
        "table_count": 14,
        "action_history_rows": 48,
        "mismatches": [
            {
                "table": "action_history",
                "mismatch_kind": "ROW_COUNT",
                "expected_row_count": 0,
                "actual_row_count": 48,
                "expected_policy": "bootstrap_empty",
            }
        ],
    }


def test_evaluation_corrected_base_collects_additional_table_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import load_corrected_base as loader

    context = loader._load_input_context()
    manifest = json.loads(
        manifest_v3.resolve_bootstrap_manifest_path(
            "evaluation", "corrected_base"
        ).read_text(encoding="utf-8")
    )
    rows = {
        table: [dict(row) for row in context.expected_rows.get(table, ())]
        for table in manifest["tables"]
    }
    rows["action_history"] = [
        {
            column.name: (f"ACT-{index:04d}" if column.name == "action_id" else None)
            for column in verifier.base_schema.BASE_COLUMNS["action_history"]
        }
        for index in range(48)
    ]
    rows["dim_parameter"][0]["parameter_name"] = "DRIFTED"
    connection = CorrectedConnection("kosa_text2sql", manifest, rows)
    monkeypatch.setattr(
        verifier.reference_extensions,
        "postcheck_database",
        lambda *_a, **_k: SimpleNamespace(),
    )

    with pytest.raises(verifier.AcceptanceMismatchError) as captured:
        verifier.verify_database(
            "kosa_text2sql",
            "corrected_base",
            environ={
                "POSTGRES_BOOTSTRAP_HOST": "shared.example",
                "POSTGRES_BOOTSTRAP_PORT": "5432",
                "POSTGRES_BOOTSTRAP_USER": "reader",
                "POSTGRES_BOOTSTRAP_PASSWORD": "hidden",
                "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(
                    "shared.example", 5432
                ),
            },
            engine_factory=lambda _: FakeEngine(connection),
        )

    assert captured.value.details["mismatches"] == [
        {
            "table": "action_history",
            "mismatch_kind": "ROW_COUNT",
            "expected_row_count": 0,
            "actual_row_count": 48,
            "expected_policy": "bootstrap_empty",
        },
        {
            "table": "dim_parameter",
            "mismatch_kind": "CONTENT_HASH",
            "expected_row_count": 8,
            "actual_row_count": 8,
            "expected_policy": "immutable_content",
        },
    ]


def _evaluation_mock_connection() -> tuple[Any, dict[str, Any], CorrectedConnection]:
    import load_corrected_base as corrected_loader
    import load_evaluation_mock as evaluation_loader

    context = corrected_loader._load_input_context()
    mock_context = evaluation_loader._load_manifest_context(require_registered=True)
    manifest = mock_context.manifest
    rows = {
        table: (
            [dict(row) for row in mock_context.expected_rows]
            if table == "action_history"
            else [dict(row) for row in context.expected_rows.get(table, ())]
        )
        for table in manifest["tables"]
    }
    return (
        evaluation_loader,
        manifest,
        CorrectedConnection("kosa_text2sql", manifest, rows),
    )


def _stub_reference_postcheck(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        verifier.reference_extensions,
        "postcheck_database",
        lambda *_a, **_k: SimpleNamespace(),
    )


def test_evaluation_mock_stage_requires_and_reports_completion_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_loader, _manifest_payload, connection = _evaluation_mock_connection()
    marker = {
        "fixture_type": "MOCK",
        "status": "VERIFIED_EXISTING",
    }
    marker_calls: list[tuple[str, str]] = []
    _stub_reference_postcheck(monkeypatch)
    monkeypatch.setattr(
        evaluation_loader,
        "verify_completion_marker",
        lambda _connection, target, registered: (
            marker_calls.append((target.database, registered["bootstrap_stage"]))
            or marker
        ),
    )

    result = verifier.verify_database(
        "kosa_text2sql",
        "evaluation_mock",
        environ={
            "POSTGRES_BOOTSTRAP_HOST": "shared.example",
            "POSTGRES_BOOTSTRAP_PORT": "5432",
            "POSTGRES_BOOTSTRAP_USER": "reader",
            "POSTGRES_BOOTSTRAP_PASSWORD": "hidden",
            "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(
                "shared.example", 5432
            ),
        },
        engine_factory=lambda _: FakeEngine(connection),
    )

    assert result.status == verifier.STATUS_PASS
    assert result.details["action_history_rows"] == 48
    assert result.details["fixture_type"] == "MOCK"
    assert result.details["fixture_marker_status"] == "VERIFIED_EXISTING"
    assert marker_calls == [("kosa_text2sql", "evaluation_mock")]


@pytest.mark.parametrize(
    "marker_error",
    [
        "artifact",
        "state",
    ],
)
def test_evaluation_mock_marker_failure_is_acceptance_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    marker_error: str,
) -> None:
    evaluation_loader, _manifest_payload, connection = _evaluation_mock_connection()
    _stub_reference_postcheck(monkeypatch)
    error = (
        evaluation_loader.EvaluationMockArtifactError("missing marker path")
        if marker_error == "artifact"
        else evaluation_loader.EvaluationMockStateError("fixture identity drift")
    )
    monkeypatch.setattr(
        evaluation_loader,
        "verify_completion_marker",
        lambda *_a, **_k: (_ for _ in ()).throw(error),
    )

    with pytest.raises(verifier.AcceptanceMismatchError) as captured:
        verifier.verify_database(
            "kosa_text2sql",
            "evaluation_mock",
            environ={
                "POSTGRES_BOOTSTRAP_HOST": "shared.example",
                "POSTGRES_BOOTSTRAP_PORT": "5432",
                "POSTGRES_BOOTSTRAP_USER": "reader",
                "POSTGRES_BOOTSTRAP_PASSWORD": "hidden",
                "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(
                    "shared.example", 5432
                ),
            },
            engine_factory=lambda _: FakeEngine(connection),
        )

    assert captured.value.exit_code == verifier.EXIT_MISMATCH
    assert captured.value.details["mismatches"] == [{"mismatch_kind": "FIXTURE_MARKER"}]
    assert "missing marker path" not in json.dumps(captured.value.details)
    assert "fixture identity drift" not in json.dumps(captured.value.details)


def test_evaluation_mock_collects_table_and_marker_mismatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_loader, _manifest_payload, connection = _evaluation_mock_connection()
    connection.rows["dim_parameter"][0]["parameter_name"] = "DRIFTED"
    _stub_reference_postcheck(monkeypatch)
    monkeypatch.setattr(
        evaluation_loader,
        "verify_completion_marker",
        lambda *_a, **_k: (_ for _ in ()).throw(
            evaluation_loader.EvaluationMockArtifactError("missing marker")
        ),
    )

    with pytest.raises(verifier.AcceptanceMismatchError) as captured:
        verifier.verify_database(
            "kosa_text2sql",
            "evaluation_mock",
            environ={
                "POSTGRES_BOOTSTRAP_HOST": "shared.example",
                "POSTGRES_BOOTSTRAP_PORT": "5432",
                "POSTGRES_BOOTSTRAP_USER": "reader",
                "POSTGRES_BOOTSTRAP_PASSWORD": "hidden",
                "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(
                    "shared.example", 5432
                ),
            },
            engine_factory=lambda _: FakeEngine(connection),
        )

    assert captured.value.details["mismatches"] == [
        {
            "table": "dim_parameter",
            "mismatch_kind": "CONTENT_HASH",
            "expected_row_count": 8,
            "actual_row_count": 8,
            "expected_policy": "immutable_content",
        },
        {"mismatch_kind": "FIXTURE_MARKER"},
    ]


def test_marker_candidate_is_timezone_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, candidate = _stub_registration(monkeypatch)
    source = json.loads(manifest_v3.SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    marker = verifier._marker_candidate(
        candidate,
        bundle,
        verifier.canonical_sha256(source),
        registered_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    assert marker["status"] == "REGISTERED"
    assert marker["verification"] == {
        "action_history_rows": 48,
        "pk_duplicate_count": 0,
        "fk_violation_count": 0,
    }
    assert marker["registered_at"].endswith("+00:00")


def test_registration_preview_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_registration(monkeypatch)
    manifest_path = tmp_path / "corrected.json"
    marker_path = tmp_path / "marker.json"
    result = verifier.register_corrected(
        confirm=False,
        manifest_path=manifest_path,
        marker_path=marker_path,
    )
    assert result.exit_code == verifier.EXIT_CONFIRM_REQUIRED
    assert not manifest_path.exists()
    assert not marker_path.exists()


def test_registration_marker_last_crash_is_not_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_registration(monkeypatch)
    manifest_path = tmp_path / "corrected.json"
    marker_path = tmp_path / "marker.json"

    def crash() -> None:
        raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected"):
        verifier.register_corrected(
            confirm=True,
            manifest_path=manifest_path,
            marker_path=marker_path,
            after_manifest_replace=crash,
        )
    assert manifest_path.exists()
    assert not marker_path.exists()


def test_registration_recovers_marker_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, candidate = _stub_registration(monkeypatch)
    manifest_path = tmp_path / "corrected.json"
    marker_path = tmp_path / "marker.json"
    manifest_v3.atomic_save_json(manifest_path, candidate)
    result = verifier.register_corrected(
        confirm=True,
        manifest_path=manifest_path,
        marker_path=marker_path,
    )
    assert result.status == verifier.STATUS_PASS
    assert marker_path.exists()


def test_registration_noop_requires_manifest_marker_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_registration(monkeypatch)
    manifest_path = tmp_path / "corrected.json"
    marker_path = tmp_path / "marker.json"
    first = verifier.register_corrected(
        confirm=True,
        manifest_path=manifest_path,
        marker_path=marker_path,
    )
    second = verifier.register_corrected(
        confirm=False,
        manifest_path=manifest_path,
        marker_path=marker_path,
    )
    assert first.details["registration"] == "REGISTERED"
    assert second.details["registration"] == "NO_OP"


def test_registered_marker_rejects_manifest_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_registration(monkeypatch)
    manifest_path = tmp_path / "corrected.json"
    marker_path = tmp_path / "marker.json"
    verifier.register_corrected(
        confirm=True,
        manifest_path=manifest_path,
        marker_path=marker_path,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["tables"]["action_history"]["content_hash"] = "0" * 64
    manifest_v3.atomic_save_json(manifest_path, payload)
    with pytest.raises(manifest_v3.ManifestMetadataError, match="marker"):
        verifier.register_corrected(
            confirm=True,
            manifest_path=manifest_path,
            marker_path=marker_path,
        )


def test_registration_replaces_old_marker_only_with_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_registration(monkeypatch)
    manifest_path = tmp_path / "corrected.json"
    marker_path = tmp_path / "marker.json"
    verifier.register_corrected(
        confirm=True,
        manifest_path=manifest_path,
        marker_path=marker_path,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["receipt_sha256"] = "0" * 64
    manifest_v3.atomic_save_json(marker_path, marker)

    preview = verifier.register_corrected(
        confirm=False,
        manifest_path=manifest_path,
        marker_path=marker_path,
    )
    assert preview.exit_code == verifier.EXIT_CONFIRM_REQUIRED
    assert json.loads(marker_path.read_text(encoding="utf-8"))["receipt_sha256"] == (
        "0" * 64
    )

    recovered = verifier.register_corrected(
        confirm=True,
        manifest_path=manifest_path,
        marker_path=marker_path,
    )
    assert recovered.details["registration"] == "REGISTERED"
    assert json.loads(marker_path.read_text(encoding="utf-8"))["receipt_sha256"] != (
        "0" * 64
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--database", "kosa_agent"],
        ["--files-only", "--stage", "base_schema"],
        ["--files-only", "--confirm"],
        ["--register-corrected", "--report", "report.json"],
    ],
)
def test_cli_rejects_invalid_option_combinations(argv: list[str]) -> None:
    args = verifier._parser().parse_args(argv)
    with pytest.raises(manifest_v3.VerificationError):
        verifier._validate_cli(args)


def test_report_contains_no_connection_values() -> None:
    report = verifier._report_payload(
        [verifier.CheckResult("kosa_agent", verifier.STATUS_PASS, 0, {})]
    )
    encoded = json.dumps(report)
    assert "postgresql" not in encoded
    assert "host" not in encoded
    assert report["overall_status"] == verifier.STATUS_PASS


def _neo_fixture(monkeypatch: pytest.MonkeyPatch, *, readiness: bool = True) -> None:
    context = SimpleNamespace(
        target=SimpleNamespace(database="neo4j"),
        manifest={
            "node_count": 1,
            "relationship_count": 1,
            "label_distribution": {"Parameter": 1},
            "relationship_type_distribution": {"MEASURED_ON": 1},
            "expected_graph_fingerprint_sha256": "a" * 64,
        },
    )
    marker = {
        "status": "REPLACED" if readiness else "RESTORED",
        "actual_graph_fingerprint_sha256": "a" * 64,
        "schema_fingerprint_sha256": "b" * 64,
    }
    monkeypatch.setattr(
        verifier.neo4j_bootstrap, "load_context", lambda *_a, **_k: context
    )
    monkeypatch.setattr(
        verifier.neo4j_bootstrap, "load_marker", lambda *_a, **_k: marker
    )
    monkeypatch.setattr(
        verifier.neo4j_bootstrap,
        "validate_marker_for_context",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        verifier.neo4j_bootstrap,
        "marker_is_readiness_success",
        lambda *_a, **_k: readiness,
    )
    monkeypatch.setattr(
        verifier.neo4j_bootstrap,
        "snapshot_fingerprint",
        lambda *_a, **_k: "a" * 64,
    )


def test_neo4j_verification_uses_read_state_and_fingerprints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _neo_fixture(monkeypatch)
    calls: list[bool] = []
    snapshot = SimpleNamespace(
        nodes=(SimpleNamespace(label="Parameter"),),
        relationships=(
            SimpleNamespace(
                relation_type="MEASURED_ON",
                relation_id="REL-1",
            ),
        ),
        node_count=1,
        relationship_count=1,
        relation_id_duplicates=0,
    )

    def read_state(_target: Any, *, require_supported_schema: bool) -> Any:
        calls.append(require_supported_schema)
        return snapshot, "b" * 64

    result = verifier.verify_neo4j(
        archive_path=Path("unused.zip"),
        environ={},
        state_reader=read_state,
    )
    assert result.status == verifier.STATUS_PASS
    assert result.details["relation_id_count"] == 1
    assert calls == [True]


def test_neo4j_restored_marker_is_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _neo_fixture(monkeypatch, readiness=False)
    with pytest.raises(manifest_v3.ArtifactMismatchError, match="readiness"):
        verifier.verify_neo4j(
            archive_path=Path("unused.zip"),
            environ={},
            state_reader=lambda *_a, **_k: pytest.fail("live read must not run"),
        )


def test_neo4j_invalid_marker_is_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _neo_fixture(monkeypatch)
    monkeypatch.setattr(
        verifier.neo4j_bootstrap,
        "validate_marker_for_context",
        lambda *_a, **_k: (_ for _ in ()).throw(
            verifier.neo4j_bootstrap.MarkerError("invalid")
        ),
    )
    with pytest.raises(manifest_v3.ArtifactMismatchError, match="marker"):
        verifier.verify_neo4j(
            archive_path=Path("unused.zip"),
            environ={},
            state_reader=lambda *_a, **_k: pytest.fail("live read must not run"),
        )


def test_neo4j_forbidden_legacy_label_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _neo_fixture(monkeypatch)
    context = SimpleNamespace(
        nodes=(SimpleNamespace(label="Sensor"),),
        relationships=(
            SimpleNamespace(relation_type="MEASURED_ON", relation_id="REL-1"),
        ),
        node_count=1,
        relationship_count=1,
        relation_id_duplicates=0,
    )
    with pytest.raises(manifest_v3.ArtifactMismatchError, match="live graph"):
        verifier.verify_neo4j(
            archive_path=Path("unused.zip"),
            environ={},
            state_reader=lambda *_a, **_k: (context, "b" * 64),
        )


def test_neo4j_unexpected_programming_error_is_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _neo_fixture(monkeypatch)
    with pytest.raises(AttributeError, match="programming bug"):
        verifier.verify_neo4j(
            archive_path=Path("unused.zip"),
            environ={},
            state_reader=lambda *_a, **_k: (_ for _ in ()).throw(
                AttributeError("programming bug")
            ),
        )


def test_run_all_does_not_promote_discovered_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        verifier,
        "verify_files",
        lambda **_k: verifier.CheckResult("files", verifier.STATUS_PASS, 0, {}),
    )

    def database_result(database: str, stage: str, **_kwargs: Any) -> Any:
        stages.append((database, stage))
        if database == "kosa_agent":
            raise verifier.AcceptanceMismatchError(
                "early data",
                details={"inventory": "EARLY_DATA"},
            )
        return verifier.CheckResult(database, verifier.STATUS_PASS, 0, {})

    monkeypatch.setattr(verifier, "verify_database", database_result)
    monkeypatch.setattr(
        verifier,
        "verify_neo4j",
        lambda **_k: verifier.CheckResult("neo4j", verifier.STATUS_PASS, 0, {}),
    )
    results, exit_code = verifier.run_all(
        archive_path=Path("unused.zip"),
        environ={},
    )
    assert stages == list(verifier.EXPECTED_STAGES.items())
    assert exit_code == verifier.EXIT_MISMATCH
    assert results[1].details["inventory"] == "EARLY_DATA"


def test_all_cli_writes_report_and_preserves_targets_for_marker_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker_details = {
        "profile": "evaluation",
        "expected_stage": "evaluation_mock",
        "mismatches": [{"mismatch_kind": "FIXTURE_MARKER"}],
    }
    results = [
        verifier.CheckResult("files", verifier.STATUS_PASS, 0, {}),
        verifier.CheckResult("kosa_agent", verifier.STATUS_PASS, 0, {}),
        verifier.CheckResult("kosa_agent_e2e", verifier.STATUS_PASS, 0, {}),
        verifier.CheckResult(
            "kosa_text2sql",
            verifier.STATUS_FAIL,
            verifier.EXIT_MISMATCH,
            marker_details,
        ),
        verifier.CheckResult("neo4j", verifier.STATUS_PASS, 0, {}),
    ]
    monkeypatch.setattr(verifier, "_archive_path", lambda *_a, **_k: Path("zip"))
    monkeypatch.setattr(
        verifier,
        "run_all",
        lambda **_k: (results, verifier.EXIT_MISMATCH),
    )
    report_path = tmp_path / "bootstrap-report.json"

    exit_code = verifier.main(["--all", "--report", str(report_path)])
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == verifier.EXIT_MISMATCH
    assert report["overall_status"] == verifier.STATUS_FAIL
    assert [target["target"] for target in report["targets"]] == [
        "files",
        "kosa_agent",
        "kosa_agent_e2e",
        "kosa_text2sql",
        "neo4j",
    ]
    assert report["targets"][3]["details"] == marker_details


def test_run_all_aborts_on_manifest_schema_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verifier,
        "verify_files",
        lambda **_k: (_ for _ in ()).throw(manifest_v3.ManifestSchemaError("invalid")),
    )
    with pytest.raises(manifest_v3.ManifestSchemaError):
        verifier.run_all(archive_path=Path("unused.zip"), environ={})


def test_database_cli_does_not_require_archive(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        verifier,
        "_archive_path",
        lambda *_a, **_k: pytest.fail("database mode must not resolve archive"),
    )
    monkeypatch.setattr(
        verifier,
        "verify_database",
        lambda *_a, **_k: verifier.CheckResult(
            "kosa_agent", verifier.STATUS_PASS, 0, {}
        ),
    )
    assert verifier.main(["--database", "kosa_agent", "--stage", "base_schema"]) == 0
    assert '"status": "PASS"' in capsys.readouterr().out
