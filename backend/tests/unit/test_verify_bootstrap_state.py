from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

# V5-CM-1.2 epoch 발급으로 kosa_0813 artifact가 격리돼 깨지는 테스트의 개별 skip.
# 해제 경로는 사유에 적힌 후속 Task가 소유한다(작업계획 §2.5·§6).
SKIP_KOSA_0813 = pytest.mark.skip(
    reason=(
        "kosa_0813 폐기(V5-CM-1.2)"
        " — V5-CM-1.6이 corrected 경로 제거 시 재평가. 모듈은 존속"
    )
)

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
            if table in verifier.reference_extensions.EXPECTED_TABLE_COLUMNS:
                contracts = verifier.reference_extensions.EXPECTED_TABLE_COLUMNS[table]
                return FakeResult(
                    [
                        {"column_name": name, "data_type": data_type}
                        for name, data_type, _nullable in contracts
                    ]
                )
            contracts = verifier.agent_runtime.EXPECTED_TABLE_COLUMNS[table]
            return FakeResult(
                [
                    {"column_name": column.name, "data_type": column.data_type}
                    for column in contracts
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


@SKIP_KOSA_0813
def test_runtime_clean_marker_failure_is_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = json.loads(
        manifest_v3.resolve_bootstrap_manifest_path(
            "runtime", "runtime_clean"
        ).read_text(encoding="utf-8")
    )
    connection = FakeConnection("kosa_agent", manifest)
    monkeypatch.setattr(
        verifier.reference_extensions,
        "postcheck_database",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        verifier.agent_runtime,
        "postcheck_database",
        lambda *args, **kwargs: verifier.agent_runtime.RuntimePostcheck(
            {}, "a" * 64, 0, 173
        ),
    )
    monkeypatch.setattr(
        verifier.agent_runtime,
        "load_marker",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        verifier.agent_runtime,
        "alarm_event_count",
        lambda *args, **kwargs: 173,
    )

    with pytest.raises(verifier.AcceptanceMismatchError) as caught:
        verifier.verify_database(
            "kosa_agent",
            "runtime_clean",
            environ={
                "POSTGRES_BOOTSTRAP_HOST": "shared.example",
                "POSTGRES_BOOTSTRAP_PORT": "5432",
                "POSTGRES_BOOTSTRAP_USER": "reader",
                "POSTGRES_BOOTSTRAP_PASSWORD": "hidden",
                "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(
                    "shared.example", 5432
                ),
            },
            engine_factory=lambda target: FakeEngine(connection),
        )

    mismatch_kinds = {
        item["mismatch_kind"] for item in caught.value.details["mismatches"]
    }
    assert "RUNTIME_MARKER" in mismatch_kinds


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


def test_run_all_preserves_other_targets_on_sqlalchemy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verifier,
        "verify_files",
        lambda **_k: verifier.CheckResult("files", verifier.STATUS_PASS, 0, {}),
    )

    def database_result(database: str, stage: str, **_kwargs: Any):
        if database == "kosa_agent":
            raise SQLAlchemyError("private connection detail")
        return verifier.CheckResult(database, verifier.STATUS_PASS, 0, {})

    monkeypatch.setattr(verifier, "verify_database", database_result)
    monkeypatch.setattr(
        verifier,
        "verify_neo4j",
        lambda **_k: verifier.CheckResult("neo4j", verifier.STATUS_PASS, 0, {}),
    )

    results, exit_code = verifier.run_all(archive_path=Path("unused.zip"), environ={})

    assert len(results) == 5
    assert exit_code == verifier.EXIT_UNVERIFIABLE
    assert results[1].target == "kosa_agent"
    assert results[1].details == {"reason_code": "CONNECT_OR_QUERY_FAILED"}
    assert results[-1].target == "neo4j"


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


@SKIP_KOSA_0813
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


def test_evaluation_mock_stage_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`V5-CM-1.6`이 loader를 삭제했다 — 그 stage를 green으로 오인하지 않는다.

    stage 자체는 남는다. `final_manifest_blockers()`의
    `EVALUATION_MOCK_PINS_48_ACTION_ROWS`가 실제 공백을 세야 하기 때문이다.
    **`V5-CM-1.8`이 `evaluation_reference`를 등록하면 이 분기와 이 테스트를 함께
    제거한다**(계획 §6.3).
    """

    source = (
        Path(verifier.__file__).read_text(encoding="utf-8")
        if hasattr(verifier, "__file__")
        else ""
    )
    assert 'mismatches.append({"mismatch_kind": "EVALUATION_MOCK_RETIRED"})' in source
    # 삭제된 loader를 import하지 않는다.
    assert "import load_evaluation_mock" not in source


def test_the_verifier_no_longer_registers_corrected_artifacts() -> None:
    """corrected 등록 경로 전체가 사라졌다(계획 §6.1)."""

    for name in (
        "register_corrected",
        "_load_active_bundle",
        "_corrected_candidate",
        "_validate_file_roles",
        "_validate_registered_marker",
        "ActiveBundle",
        "CORRECTED_MARKER_PATH",
        "CORRECTED_LOCK_PATH",
    ):
        assert not hasattr(verifier, name), f"corrected 잔재: {name}"

    parser = verifier._parser()
    flags = {
        action.option_strings[0] for action in parser._actions if action.option_strings
    }
    assert "--register-corrected" not in flags
    assert "--confirm" not in flags


def test_files_only_verifies_the_final_intake(monkeypatch: pytest.MonkeyPatch) -> None:
    """파일 gate를 비우지 않았다 — corrected build 대신 최종 ZIP intake를 본다.

    삭제 Task가 커버리지를 조용히 줄이는 것을 막는 계약이다(계획 §0-2 · §6.2).
    """

    import intake_final_zip as intake

    registered = json.loads(intake.INTAKE_ARTIFACT_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(intake, "read_archive", lambda _p: {"scan": True})
    monkeypatch.setattr(intake, "build_payload", lambda _s: registered)
    monkeypatch.setattr(verifier.intake, "read_archive", lambda _p: {"scan": True})
    monkeypatch.setattr(verifier.intake, "build_payload", lambda _s: registered)

    result = verifier.verify_files(archive_path=Path("unused.zip"))

    assert result.status == verifier.STATUS_PASS
    assert result.details["selected_count"] == intake.SELECTED_MEMBER_COUNT
    assert result.details["dataset_epoch"] == registered["declared_target_epoch"]


def test_files_only_rejects_a_mismatched_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """등록 intake와 다른 ZIP은 거부한다."""

    drifted = {"archive": {"sha256": "0" * 64}, "selected_count": 1}
    monkeypatch.setattr(verifier.intake, "read_archive", lambda _p: {})
    monkeypatch.setattr(verifier.intake, "build_payload", lambda _s: drifted)

    with pytest.raises(manifest_v3.ArtifactMismatchError):
        verifier.verify_files(archive_path=Path("unused.zip"))


def test_files_only_rejects_an_archive_that_disagrees_with_the_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**intake와 epoch이 서로 다른 ZIP을 가리키면 어느 쪽도 정본이 아니다.**

    payload 자체는 등록본과 같게 만들어, archive 3자 대조가 없으면 통과하는
    상황을 재현한다(계획 §6.2-4 · §9.3).
    """

    import intake_final_zip as intake

    registered = json.loads(intake.INTAKE_ARTIFACT_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(verifier.intake, "read_archive", lambda _p: {})
    monkeypatch.setattr(verifier.intake, "build_payload", lambda _s: registered)
    monkeypatch.setattr(
        verifier,
        "_read_json",
        lambda path, *, missing: (
            {"archive": {"sha256": "1" * 64}, "dataset_epoch": "other"}
            if path == manifest_v3.DATASET_EPOCH_PATH
            else registered
        ),
    )

    with pytest.raises(manifest_v3.ArtifactMismatchError, match="epoch archive"):
        verifier.verify_files(archive_path=Path("unused.zip"))


def test_files_only_rejects_a_wrong_selected_member_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """selected member 15개 계약이 깨지면 거부한다(계획 §6.2-2 · §9.3).

    payload와 archive SHA는 모두 맞춰, member 수 검사가 없으면 통과하는 상황이다.
    """

    import intake_final_zip as intake

    registered = json.loads(intake.INTAKE_ARTIFACT_PATH.read_text(encoding="utf-8"))
    drifted = dict(registered)
    drifted["selected_count"] = intake.SELECTED_MEMBER_COUNT - 1
    monkeypatch.setattr(verifier.intake, "read_archive", lambda _p: {})
    monkeypatch.setattr(verifier.intake, "build_payload", lambda _s: drifted)
    monkeypatch.setattr(
        verifier,
        "_read_json",
        lambda path, *, missing: (
            {
                "archive": {"sha256": registered["archive"]["sha256"]},
                "dataset_epoch": registered["declared_target_epoch"],
            }
            if path == manifest_v3.DATASET_EPOCH_PATH
            else drifted
        ),
    )

    with pytest.raises(manifest_v3.ArtifactMismatchError, match="selected member"):
        verifier.verify_files(archive_path=Path("unused.zip"))


@pytest.mark.parametrize(
    "argv",
    [
        ["--database", "kosa_agent"],
        ["--files-only", "--stage", "base_schema"],
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
