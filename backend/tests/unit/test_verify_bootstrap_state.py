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
import value_normalization  # noqa: E402
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


def test_runtime_database_still_rejects_an_evaluation_only_stage() -> None:
    """**폐기 통보가 profile/stage 입력 계약을 덮지 않는다**(구현리뷰 권장 1).

    `evaluation_mock`은 evaluation profile 전용이다. runtime DB에 그 stage를 넣은
    호출은 "이 stage는 폐기됐다"가 아니라 원래의 조합 오류를 받아야 한다. 조기 분기가
    stage 이름만 봤다면 이 입력이 `EVALUATION_MOCK_RETIRED`로 조용히 흡수된다.
    """

    with pytest.raises(manifest_v3.ManifestMetadataError):
        verifier.verify_database(
            "kosa_agent",
            verifier.EVALUATION_MOCK_STAGE,
            environ={},
            engine_factory=lambda _: pytest.fail("DB connect must not run"),
        )


def _artifact_reader(
    registered: dict,
    *,
    epoch: dict | None = None,
    source: dict | None = None,
):
    """`_read_json`을 epoch·intake·source manifest 3자로 갈라 주는 fake.

    기본값은 **셋이 모두 일치**하는 상태다. 인자로 넘긴 쪽만 어긋나게 만들어
    "그 한 축이 없으면 통과하는가"를 각각 재현한다(계획 §6.2-4 · 구현리뷰 필수 2).
    """

    import build_source_manifest_v4 as source_manifest_v4

    sha = registered["archive"]["sha256"]
    declared = registered["declared_target_epoch"]
    default_epoch = {"archive": {"sha256": sha}, "dataset_epoch": declared}
    default_source = {"source_archive_sha256": sha, "dataset_epoch": declared}
    table = {
        manifest_v3.DATASET_EPOCH_PATH: epoch if epoch is not None else default_epoch,
        source_manifest_v4.MANIFEST_V4_PATH: (
            source if source is not None else default_source
        ),
    }

    def _read(path, *, missing):
        return table.get(path, registered)

    return _read


def test_evaluation_mock_stage_is_fail_closed() -> None:
    """`V5-CM-1.6`이 loader를 삭제했다 — 그 stage를 green으로 오인하지 않는다.

    **connector를 한 번도 열지 않는다.** 분기가 epoch loader·target resolution
    뒤에 있으면 `V5-CM-1.8`이 loader를 v2로 전환하는 순간 폐기 stage가 DB를
    건드리게 된다(구현리뷰 필수 1). 그래서 실제 `verify_database()`를 호출해
    mismatch 종류와 `engine_factory` 호출 0회를 함께 단언한다.

    stage 자체는 남는다. `final_manifest_blockers()`의
    `EVALUATION_MOCK_PINS_48_ACTION_ROWS`가 실제 공백을 세야 하기 때문이다.
    **`V5-CM-1.8`이 `evaluation_reference`를 등록하면 이 분기와 이 테스트를 함께
    제거한다**(계획 §6.3).
    """

    calls: list[object] = []

    result = verifier.verify_database(
        "kosa_text2sql",
        verifier.EVALUATION_MOCK_STAGE,
        environ={},
        engine_factory=lambda target: calls.append(target),
    )

    assert calls == []
    assert result.status == verifier.STATUS_FAIL
    assert result.exit_code == verifier.EXIT_MISMATCH
    assert result.details["mismatches"] == [
        {"mismatch_kind": "EVALUATION_MOCK_RETIRED"}
    ]
    # 삭제된 loader를 import하지 않는다.
    assert not hasattr(verifier, "load_evaluation_mock")


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
        _artifact_reader(
            registered,
            epoch={"archive": {"sha256": "1" * 64}, "dataset_epoch": "other"},
        ),
    )

    with pytest.raises(manifest_v3.ArtifactMismatchError, match="epoch archive"):
        verifier.verify_files(archive_path=Path("unused.zip"))


def test_files_only_rejects_a_source_manifest_that_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**source manifest 하나만 어긋나도 거부한다.**

    `source-manifest-v4.json`은 canonical CSV 9종의 정본 기록이다. intake와 epoch만
    맞춰 보면 이 파일이 다른 ZIP을 가리키는 채로 통과한다 — 3자 대조가 필요한
    이유다(구현리뷰 필수 2). epoch·intake는 일치시켜 이 축만 남긴다.
    """

    import intake_final_zip as intake

    registered = json.loads(intake.INTAKE_ARTIFACT_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(verifier.intake, "read_archive", lambda _p: {})
    monkeypatch.setattr(verifier.intake, "build_payload", lambda _s: registered)
    monkeypatch.setattr(
        verifier,
        "_read_json",
        _artifact_reader(
            registered,
            source={
                "source_archive_sha256": "2" * 64,
                "dataset_epoch": registered["declared_target_epoch"],
            },
        ),
    )

    with pytest.raises(
        manifest_v3.ArtifactMismatchError, match="source manifest archive"
    ):
        verifier.verify_files(archive_path=Path("unused.zip"))


def test_files_only_rejects_a_source_manifest_from_another_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """archive SHA가 같아도 epoch 표기가 다르면 거부한다(구현리뷰 필수 2)."""

    import intake_final_zip as intake

    registered = json.loads(intake.INTAKE_ARTIFACT_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(verifier.intake, "read_archive", lambda _p: {})
    monkeypatch.setattr(verifier.intake, "build_payload", lambda _s: registered)
    monkeypatch.setattr(
        verifier,
        "_read_json",
        _artifact_reader(
            registered,
            source={
                "source_archive_sha256": registered["archive"]["sha256"],
                "dataset_epoch": "kosa_0813",
            },
        ),
    )

    with pytest.raises(
        manifest_v3.ArtifactMismatchError, match="source manifest dataset_epoch"
    ):
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
    monkeypatch.setattr(verifier, "_read_json", _artifact_reader(drifted))

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


def _neo_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    readiness: bool = True,
    status: str | None = None,
) -> None:
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
        "status": status or ("REPLACED" if readiness else "RESTORED"),
        "actual_graph_fingerprint_sha256": "a" * 64,
    }
    # **schema fingerprint는 `REPLACED|RESTORED` marker에만 있다.**
    # 나머지 success status는 이 key 자체가 없다.
    if marker["status"] in {"REPLACED", "RESTORED"}:
        marker["schema_fingerprint_sha256"] = "b" * 64
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


@pytest.mark.parametrize("status", ["APPLIED", "ADOPTED_EXISTING", "VERIFIED_EXISTING"])
def test_neo4j_success_without_schema_field_still_passes(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """**계획 §4.5.** schema field가 없는 success status가 readiness를 통과한다.

    이 3개 marker에는 `schema_fingerprint_sha256`이 없다. 그런데 verifier가
    `marker.get(...)` → `None`을 live sha256과 비교했기 때문에, **graph가 완벽해도
    readiness가 항상 실패**했다. 공용 graph가 empty·legacy exact·exact-without-marker인
    정상 분기가 전부 여기 걸렸다.
    """

    _neo_fixture(monkeypatch, status=status)
    snapshot = SimpleNamespace(
        nodes=(SimpleNamespace(label="Parameter"),),
        relationships=(
            SimpleNamespace(relation_type="MEASURED_ON", relation_id="REL-1"),
        ),
        node_count=1,
        relationship_count=1,
        relation_id_duplicates=0,
    )
    calls: list[bool] = []

    def read_state(_target: Any, *, require_supported_schema: bool) -> Any:
        calls.append(require_supported_schema)
        return snapshot, "b" * 64

    result = verifier.verify_neo4j(
        archive_path=Path("unused.zip"),
        environ={},
        state_reader=read_state,
    )
    assert result.status == verifier.STATUS_PASS
    # **live schema 검증 자체는 건너뛰지 않는다.** marker 대조만 조건부다.
    assert calls == [True]


def test_neo4j_schema_drift_still_fails_when_marker_pins_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """marker가 schema를 고정하는 status에서는 drift가 계속 잡혀야 한다.

    조건부 비교가 `REPLACED|RESTORED`의 방어까지 걷어내면 안 된다.
    """

    _neo_fixture(monkeypatch, status="REPLACED")
    snapshot = SimpleNamespace(
        nodes=(SimpleNamespace(label="Parameter"),),
        relationships=(
            SimpleNamespace(relation_type="MEASURED_ON", relation_id="REL-1"),
        ),
        node_count=1,
        relationship_count=1,
        relation_id_duplicates=0,
    )
    with pytest.raises(manifest_v3.ArtifactMismatchError):
        verifier.verify_neo4j(
            archive_path=Path("unused.zip"),
            environ={},
            # marker는 "b"*64를 고정하는데 live는 다르다.
            state_reader=lambda *_a, **_k: (snapshot, "c" * 64),
        )


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


class TestFinalLogicalTypeRegistry:
    """final stage의 logical type 우선순위(`V5-CM-1.8` 계획 §3.5).

    CM-3.1이 `V4_R03_TYPE_REGISTRY_STILL_ACTIVE`를 정적 blocker로 세어 둔 공백을
    닫는다. 그 blocker의 제거가 정당한지는 **여기서 실물로** 확인한다.
    """

    R03 = "r03_alarm_history"
    WAFER_TABLES = (
        "evaluation",
        "summary_alarm_history",
        "summary_data",
        "trace_alarm_history",
    )

    def test_r03_resolves_to_the_v5_twelve_columns(self) -> None:
        import apply_reference_extensions_v5 as v5

        types = verifier._expected_column_types(self.R03)

        assert list(types) == [name for name, _t, _l, _x in v5.R03_COLUMNS]
        assert len(types) == 12

    def test_r03_carries_the_final_member_columns(self) -> None:
        """**V4 registry로 내려가면 이 둘이 사라진다**(계획 §3.5-3)."""

        types = verifier._expected_column_types(self.R03)

        assert types["member_wafer_refs"] == types["member_alarm_refs"] == "json"
        assert "member_refs" not in types

    def test_r03_does_not_fall_through_to_the_v4_registry(self) -> None:
        import apply_reference_extensions as v4_reference

        v4_names = [
            name for name, _t, _x in v4_reference.EXPECTED_TABLE_COLUMNS[self.R03]
        ]
        resolved = list(verifier._expected_column_types(self.R03))

        assert len(v4_names) == 11
        assert resolved != v4_names

    @pytest.mark.parametrize("table", WAFER_TABLES)
    def test_the_final_wafer_column_is_text_not_numeric(self, table: str) -> None:
        """**구 `BASE_COLUMNS`는 `smallint`로 본다**(계획 §3.5-1).

        최종 DDL은 `varchar(24)`라 logical type이 `text`다. 구 registry를 그대로 두면
        final DB가 base에서 먼저 실패한다.
        """

        import bootstrap_base_schema as base_schema

        assert verifier._expected_column_types(table)["wafer"] == "text"

        legacy = {
            column.name: column.data_type for column in base_schema.BASE_COLUMNS[table]
        }
        assert legacy["wafer"] == "smallint"

    def test_base_types_come_from_the_source_manifest(self) -> None:
        import final_profile_manifests as builder

        tables = builder.load_source_manifest()["tables"]
        for table, entry in tables.items():
            assert (
                verifier._expected_column_types(table) == entry["column_types"]
            ), table

    @pytest.mark.parametrize("table", ["document", "document_chunk", "nl_query_log"])
    def test_reference_tables_keep_the_existing_registry(self, table: str) -> None:
        import apply_reference_extensions as v4_reference

        expected = {
            name: value_normalization.logical_type(data_type)
            for name, data_type, _nullable in v4_reference.EXPECTED_TABLE_COLUMNS[table]
        }

        assert verifier._expected_column_types(table) == expected

    def test_runtime_tables_keep_the_agent_runtime_registry(self) -> None:
        import apply_agent_runtime as agent_runtime

        for table, columns in agent_runtime.EXPECTED_TABLE_COLUMNS.items():
            expected = {
                column.name: value_normalization.logical_type(column.data_type)
                for column in columns
            }
            assert verifier._expected_column_types(table) == expected, table

    def test_a_source_manifest_without_types_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**가드를 직접 겨냥한다.**

        실물 artifact는 9종 모두 `column_types`를 갖고 있어 이 분기가 도달 불가다.
        합성 입력으로만 확인할 수 있다.
        """

        import final_profile_manifests as builder

        broken = {"tables": {"evaluation": {"columns": ["wafer"]}}}
        monkeypatch.setattr(builder, "load_source_manifest", lambda *a, **k: broken)

        with pytest.raises(manifest_v3.ManifestSchemaError, match="column_types"):
            verifier._expected_column_types("evaluation")

    def test_an_unknown_table_has_no_registry(self) -> None:
        with pytest.raises(manifest_v3.ManifestSchemaError):
            verifier._expected_column_types("not_a_table")


class TestFinalPostcheckRouting:
    """final stage는 V4 postcheck를 타지 않는다(`V5-CM-1.8` 계획 §3.5).

    V4 경로는 R03 **11컬럼**·V4 View 계약이라, logical type registry만 V5로 바꿔도
    final DB는 여기서 실패한다(구현리뷰 7차 필수 2).
    """

    def test_the_final_stages_cover_the_whole_final_lineage(self) -> None:
        """**`FINAL_STAGE_BY_PROFILE`에서 파생하지 않는다**(`V5-CM-3.3` 계획 §6.3).

        그 map은 profile당 stage 하나만 담는다. `runtime_guarded`가 추가된 순간
        predecessor `runtime_clean`이 집합에서 빠져 **V4 경로로 잘못 떨어진다** —
        R03 11컬럼·V4 View 계약이라 final DB가 거기서 실패한다.

        "현재 live final stage"와 "final reference stages"는 다른 질문이다.
        """

        import apply_reference_extensions_v5 as v5

        assert verifier.FINAL_STAGES == {
            ("runtime", "runtime_clean"),
            ("runtime", "runtime_guarded"),
            ("evaluation", "evaluation_reference"),
        }
        # live final stage는 부분집합이다 — 같지 않다.
        assert set(v5.FINAL_STAGE_BY_PROFILE.items()) < verifier.FINAL_STAGES

    def test_the_predecessor_stage_still_routes_to_final(self) -> None:
        """CM-3.2 marker가 증명하는 stage가 V4로 떨어지면 안 된다."""

        assert (
            verifier.reference_postcheck_routing("runtime", "runtime_clean") == "final"
        )

    def test_live_stages_are_derived_not_hand_written(self) -> None:
        """`EXPECTED_STAGES`를 DB 이름마다 손으로 적으면 한쪽만 갱신된다.

        실제로 `kosa_text2sql`이 `evaluation_mock`으로 남아 있었다 — `V5-CM-1.8`이
        stage를 교체했는데 이 map만 따라오지 않았다.
        """

        assert verifier.EXPECTED_STAGES == {
            "kosa_agent": "runtime_guarded",
            "kosa_agent_e2e": "runtime_guarded",
            "kosa_text2sql": "evaluation_reference",
        }
        assert "evaluation_mock" not in set(verifier.EXPECTED_STAGES.values())
        for database, stage in verifier.EXPECTED_STAGES.items():
            profile = "evaluation" if database == "kosa_text2sql" else "runtime"
            assert stage == verifier.LIVE_FINAL_STAGE_BY_PROFILE[profile], database

    @pytest.mark.parametrize(
        ("profile", "stage", "expected"),
        [
            ("runtime", "runtime_clean", "final"),
            ("runtime", "runtime_guarded", "final"),
            ("evaluation", "evaluation_reference", "final"),
            ("runtime", "base_schema", "none"),
            ("evaluation", "base_schema", "none"),
            ("evaluation", "evaluation_mock", "v4"),
            ("runtime", "corrected_base", "v4"),
        ],
    )
    def test_the_routing_decision_is_explicit(
        self, profile: str, stage: str, expected: str
    ) -> None:
        """**분기를 함수로 빼서 직접 겨냥한다**(구현리뷰 7차 필수 2 · 변이 M41).

        인라인 `if`로 두면 routing이 잘못돼도 회귀가 잡지 못한다 — 판정기 자체는
        따로 통과하기 때문이다.
        """

        assert verifier.reference_postcheck_routing(profile, stage) == expected

    def test_no_final_stage_falls_through_to_the_v4_route(self) -> None:
        for profile, stage in verifier.FINAL_STAGES:
            assert verifier.reference_postcheck_routing(profile, stage) != "v4"

    def test_the_v4_postcheck_is_not_called_for_final_stages(self) -> None:
        """**AST로 분기를 본다.** 문자열 검색은 주석에도 걸린다."""

        import ast
        import inspect
        import textwrap

        tree = ast.parse(
            textwrap.dedent(inspect.getsource(verifier._final_reference_mismatches))
        )
        called = {
            ast.unparse(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }

        assert not any("postcheck_database" in name for name in called)
        for judge in (
            "assert_r03_columns",
            "assert_r03_constraints",
            "assert_view_columns",
            "assert_view_identity",
            "assert_canonical_comments",
        ):
            assert f"reference_v5.{judge}" in called, judge

    def test_the_final_route_consumes_only_the_final_postcheck(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**소비 경계를 spy로 고정한다**(구현리뷰 8차 필수 2).

        routing 반환값과 판정기 내부를 따로 보면 소비 한 줄을 지워도 green이었다.
        """

        final_calls: list[object] = []
        v4_calls: list[object] = []
        monkeypatch.setattr(
            verifier,
            "_final_reference_mismatches",
            lambda connection: final_calls.append(connection) or [],
        )
        monkeypatch.setattr(
            verifier.reference_extensions,
            "postcheck_database",
            lambda *a, **k: v4_calls.append(k),
        )

        for profile, stage in sorted(verifier.FINAL_STAGES):
            result = verifier.reference_postcheck_mismatches(
                _FakeCatalogConnection(),
                profile=profile,
                stage=stage,
                action_rows_before=0,
            )
            assert result == []

        assert len(final_calls) == len(verifier.FINAL_STAGES)
        assert v4_calls == []

    @pytest.mark.parametrize(
        ("profile", "stage"),
        [("evaluation", "evaluation_mock"), ("runtime", "corrected_base")],
    )
    def test_the_v4_route_consumes_only_the_v4_postcheck(
        self, monkeypatch: pytest.MonkeyPatch, profile: str, stage: str
    ) -> None:
        final_calls: list[object] = []
        v4_calls: list[object] = []
        monkeypatch.setattr(
            verifier,
            "_final_reference_mismatches",
            lambda connection: final_calls.append(connection) or [],
        )
        monkeypatch.setattr(
            verifier.reference_extensions,
            "postcheck_database",
            lambda *a, **k: v4_calls.append(k),
        )

        verifier.reference_postcheck_mismatches(
            _FakeCatalogConnection(),
            profile=profile,
            stage=stage,
            action_rows_before=7,
        )

        assert final_calls == []
        assert v4_calls == [{"action_rows_before": 7}]

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_base_schema_route_consumes_neither(
        self, monkeypatch: pytest.MonkeyPatch, profile: str
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            verifier,
            "_final_reference_mismatches",
            lambda connection: calls.append("final") or [],
        )
        monkeypatch.setattr(
            verifier.reference_extensions,
            "postcheck_database",
            lambda *a, **k: calls.append("v4"),
        )

        result = verifier.reference_postcheck_mismatches(
            _FakeCatalogConnection(),
            profile=profile,
            stage="base_schema",
            action_rows_before=0,
        )

        assert result == []
        assert calls == []

    def test_the_v4_route_reports_its_own_mismatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_k: object) -> None:
            raise verifier.reference_extensions.ReferenceExtensionError("x")

        monkeypatch.setattr(verifier.reference_extensions, "postcheck_database", _boom)

        result = verifier.reference_postcheck_mismatches(
            _FakeCatalogConnection(),
            profile="evaluation",
            stage="evaluation_mock",
            action_rows_before=0,
        )

        assert result == [{"mismatch_kind": "REFERENCE_SIGNATURE_OR_VIEW"}]

    def test_verify_database_consumes_the_routing_helper(self) -> None:
        """`verify_database()`가 이 helper를 실제로 호출한다."""

        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(verifier.verify_database)))
        called = {
            ast.unparse(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }

        assert "reference_postcheck_mismatches" in called
        # 본문에서 직접 V4 postcheck를 부르지 않는다.
        assert not any(
            "reference_extensions.postcheck_database" in name for name in called
        )

        # **결과를 실제로 쓰는지까지 본다.** 호출만 확인하면 반환값을 버리는 변이가
        # 그대로 통과한다(변이 M47).
        consumed = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func).endswith("mismatches.extend")
            and any(
                isinstance(inner, ast.Call)
                and ast.unparse(inner.func) == "reference_postcheck_mismatches"
                for inner in ast.walk(node)
            )
        ]
        assert len(consumed) == 1

    def test_the_final_judges_are_reused_not_reimplemented(self) -> None:
        """CM-3.1의 순수 판정기를 그대로 쓴다 — 규칙을 다시 구현하면 갈린다."""

        import apply_reference_extensions_v5 as v5

        for name in (
            "assert_r03_columns",
            "assert_r03_constraints",
            "assert_view_columns",
            "assert_view_identity",
            "assert_canonical_comments",
        ):
            assert callable(getattr(v5, name)), name

    def test_the_queries_are_read_only(self) -> None:
        import apply_reference_extensions_v5 as v5

        for sql in (
            v5.R03_COLUMNS_SQL,
            v5.R03_CONSTRAINTS_SQL,
            v5.VIEW_COLUMNS_SQL,
            v5.VIEW_DEFINITION_SQL,
        ):
            normalized = " ".join(sql.strip().split()).upper()
            assert normalized.startswith(verifier.READ_ONLY_PREFIXES)
            assert ";" not in normalized

    @pytest.mark.parametrize(
        ("failing", "expected_kind"),
        [
            ("assert_r03_columns", "FINAL_R03_CONTRACT"),
            ("assert_r03_constraints", "FINAL_R03_CONSTRAINTS"),
            ("assert_view_columns", "FINAL_VIEW_CONTRACT"),
            ("assert_view_identity", "FINAL_VIEW_CONTRACT"),
            ("assert_canonical_comments", "FINAL_COMMENT_CONTRACT"),
        ],
    )
    def test_each_final_judge_reports_its_own_mismatch(
        self, monkeypatch: pytest.MonkeyPatch, failing: str, expected_kind: str
    ) -> None:
        """판정기 하나가 실패하면 그 종류의 mismatch가 나온다."""

        import apply_reference_extensions_v5 as v5

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise v5.ReferenceV5Error("R03_CONTRACT_MISMATCH", v5.EXIT_MISMATCH)

        monkeypatch.setattr(v5, failing, _boom)

        connection = _FakeCatalogConnection()
        kinds = [
            entry["mismatch_kind"]
            for entry in verifier._final_reference_mismatches(connection)
        ]

        assert expected_kind in kinds

    def test_a_healthy_catalog_produces_no_mismatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import apply_reference_extensions_v5 as v5

        for name in (
            "assert_r03_columns",
            "assert_r03_constraints",
            "assert_view_columns",
            "assert_view_identity",
            "assert_canonical_comments",
        ):
            monkeypatch.setattr(v5, name, lambda *_a, **_k: None)

        assert verifier._final_reference_mismatches(_FakeCatalogConnection()) == []

    def test_the_comment_query_reuses_the_shared_expression(self) -> None:
        """CM-2.6·CM-3.1과 **같은 query**를 쓴다.

        같은 값을 다르게 읽으면 대조가 무의미하다.
        """

        import apply_reference_extensions_v5 as v5

        assert "obj_description" in v5.RELATION_SECURITY_SQL
        normalized = " ".join(v5.RELATION_SECURITY_SQL.strip().split()).upper()
        assert normalized.startswith(verifier.READ_ONLY_PREFIXES)
        assert ";" not in normalized

    @pytest.mark.parametrize(
        ("r03_comment", "view_comment"),
        [
            (None, None),
            ("wrong", None),
            (None, "금지된 comment"),
        ],
    )
    def test_a_drifted_comment_is_reported(
        self, r03_comment: str | None, view_comment: str | None
    ) -> None:
        """R03 comment 누락·변조와 View comment 추가가 모두 잡힌다."""

        import apply_reference_extensions_v5 as v5

        connection = _FakeCatalogConnection(
            comments={v5.R03_TABLE: r03_comment, v5.ALARM_VIEW: view_comment}
        )
        kinds = [
            entry["mismatch_kind"]
            for entry in verifier._final_reference_mismatches(connection)
        ]

        assert "FINAL_COMMENT_CONTRACT" in kinds

    def test_the_canonical_comments_pass(self) -> None:
        import apply_reference_extensions_v5 as v5

        connection = _FakeCatalogConnection(
            comments={
                v5.R03_TABLE: v5.R03_COMMENT,
                v5.ALARM_VIEW: v5.VIEW_COMMENT,
            }
        )
        kinds = [
            entry["mismatch_kind"]
            for entry in verifier._final_reference_mismatches(connection)
        ]

        assert "FINAL_COMMENT_CONTRACT" not in kinds


class _FakeCatalogConnection:
    """catalog query에 빈 행(또는 지정한 comment 행)을 돌려주는 최소 fake."""

    def __init__(self, comments: dict[str, object] | None = None) -> None:
        self._comments = comments

    def exec_driver_sql(self, statement: str, parameters: object = None) -> object:
        if self._comments is not None and "obj_description" in statement:
            return _FakeResult(
                [
                    {"relname": name, "comment": comment}
                    for name, comment in self._comments.items()
                ]
            )
        return _FakeResult()


class _FakeResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def scalar_one(self) -> str:
        return ""

    def scalar(self) -> str:
        return ""
