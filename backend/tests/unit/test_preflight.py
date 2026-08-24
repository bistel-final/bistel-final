"""V4-D-1.1 preflight 단위 테스트.

실물 manifest 로 계약을 확인하고, 조작된 manifest·DSN 으로 각 실패 경로를
검증한다. 실제 DB 접속은 필요 없다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.analytics import preflight
from app.analytics.db_pool import LogicalDb, PoolConfigurationError, PoolRole
from app.analytics.preflight import PreflightError, read_state, run_preflight

SOURCE_A = "a" * 64
SOURCE_B = "b" * 64


@dataclass
class _FakeInfo:
    host: str
    port: int | None
    database: str


class FakeFactory:
    """DSN 이 실제로 가리키는 지점을 흉내내는 가짜 pool factory."""

    def __init__(
        self,
        runtime_database: str = "kosa_agent",
        evaluation_database: str = "kosa_text2sql",
        runtime_host: str = "db.example.com",
        evaluation_host: str = "db.example.com",
        runtime_port: int | None = 53001,
        evaluation_port: int | None = 53001,
        error: PoolConfigurationError | None = None,
    ) -> None:
        self._infos = {
            LogicalDb.RUNTIME: _FakeInfo(
                host=runtime_host, port=runtime_port, database=runtime_database
            ),
            LogicalDb.EVALUATION: _FakeInfo(
                host=evaluation_host,
                port=evaluation_port,
                database=evaluation_database,
            ),
        }
        self._error = error
        self.requested_roles: list[PoolRole] = []

    def get_pool_info(self, logical_db: LogicalDb, role: PoolRole) -> _FakeInfo:
        if self._error is not None:
            raise self._error
        self.requested_roles.append(role)
        return self._infos[logical_db]


def _manifest(
    *,
    profile: str,
    stage: str,
    source: str = SOURCE_A,
    correction: str = "v1",
    applies_to: list,
    policy: str,
    row_count: int,
) -> dict:
    entry: dict = {
        "columns": ["action_id", "lot_id"],
        "verification_policy": policy,
        "row_count": row_count,
        "content_hash": "c" * 64,
    }
    if policy == "immutable_content":
        entry["fixture_type"] = "MOCK"

    return {
        "format_version": 3,
        "artifact_type": "db_bootstrap",
        "profile": profile,
        "bootstrap_stage": stage,
        "source_archive_sha256": source,
        "correction_version": correction,
        "applies_to": applies_to,
        "tables": {"action_history": entry},
    }


def _write_pair(
    root: Path,
    *,
    runtime_source: str = SOURCE_A,
    evaluation_source: str = SOURCE_A,
    runtime_correction: str = "v1",
    evaluation_correction: str = "v1",
    runtime_policy: str = "bootstrap_empty",
    evaluation_policy: str = "immutable_content",
    runtime_applies_to: list | None = None,
    evaluation_applies_to: list | None = None,
) -> None:
    """조작 가능한 manifest 한 쌍을 임시 디렉터리에 쓴다."""
    runtime = _manifest(
        profile="runtime",
        stage="runtime_clean",
        source=runtime_source,
        correction=runtime_correction,
        applies_to=runtime_applies_to or ["kosa_agent", "kosa_agent_e2e"],
        policy=runtime_policy,
        row_count=0 if runtime_policy == "bootstrap_empty" else 48,
    )
    evaluation = _manifest(
        profile="evaluation",
        stage="evaluation_reference",
        source=evaluation_source,
        correction=evaluation_correction,
        applies_to=evaluation_applies_to or ["kosa_text2sql"],
        policy=evaluation_policy,
        row_count=12 if evaluation_policy == "immutable_content" else 0,
    )

    (root / "runtime.runtime_clean.json").write_text(
        json.dumps(runtime), encoding="utf-8"
    )
    (root / "evaluation.evaluation_reference.json").write_text(
        json.dumps(evaluation), encoding="utf-8"
    )


@pytest.fixture()
def manifest_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(preflight, "MANIFEST_ROOT", tmp_path)
    return tmp_path


@pytest.fixture()
def factory() -> FakeFactory:
    """기본 상태는 정상 설정(같은 서버, 서로 다른 DB)이다."""
    return FakeFactory()


# ---------------------------------------------------------------------------
# 실물 manifest 계약
# ---------------------------------------------------------------------------


def test_registered_manifests_pass_preflight(factory: FakeFactory) -> None:
    """저장소에 등록된 실제 manifest 가 계약을 만족해야 한다."""
    result = run_preflight(factory)

    assert result.ok is True, result.reason
    assert result.runtime is not None
    assert result.evaluation is not None


def test_runtime_is_mutable_and_evaluation_is_not() -> None:
    """Runtime 은 write state, 평가는 immutable snapshot 이다."""
    assert read_state(LogicalDb.RUNTIME).is_mutable is True
    assert read_state(LogicalDb.EVALUATION).is_mutable is False


def test_registered_manifests_share_source_archive() -> None:
    """평가 결과를 Runtime 근거로 쓰려면 같은 source 여야 한다."""
    runtime = read_state(LogicalDb.RUNTIME)
    evaluation = read_state(LogicalDb.EVALUATION)

    assert runtime.source_archive_sha256 == evaluation.source_archive_sha256


def test_logical_dbs_point_to_different_physical_databases() -> None:
    runtime = read_state(LogicalDb.RUNTIME)
    evaluation = read_state(LogicalDb.EVALUATION)

    assert not set(runtime.applies_to) & set(evaluation.applies_to)


# ---------------------------------------------------------------------------
# manifest 실패 경로
# ---------------------------------------------------------------------------


def test_different_source_archive_is_rejected(
    manifest_root: Path, factory: FakeFactory
) -> None:
    _write_pair(manifest_root, evaluation_source=SOURCE_B)

    result = run_preflight(factory)

    assert result.ok is False
    assert "source archive" in result.reason


def test_different_correction_version_is_rejected(
    manifest_root: Path, factory: FakeFactory
) -> None:
    _write_pair(manifest_root, evaluation_correction="v2")

    result = run_preflight(factory)

    assert result.ok is False
    assert "correction_version" in result.reason


def test_same_mutability_is_rejected(manifest_root: Path, factory: FakeFactory) -> None:
    """평가가 Runtime 처럼 쓰기 가능하면 재현성이 깨진다."""
    _write_pair(manifest_root, evaluation_policy="bootstrap_empty")

    result = run_preflight(factory)

    assert result.ok is False


def test_overlapping_declaration_is_rejected(
    manifest_root: Path, factory: FakeFactory
) -> None:
    """manifest 선언 단계에서 두 논리 DB 가 겹치면 거부한다."""
    _write_pair(manifest_root, evaluation_applies_to=["kosa_agent"])

    result = run_preflight(factory)

    assert result.ok is False
    assert "kosa_agent" in result.reason


def test_missing_manifest_is_reported(
    manifest_root: Path, factory: FakeFactory
) -> None:
    result = run_preflight(factory)

    assert result.ok is False
    assert "manifest" in result.reason


def test_preflight_returns_reason_instead_of_raising(
    manifest_root: Path, factory: FakeFactory
) -> None:
    """호출부가 Tool 계약 {ok, ..., reason} 으로 그대로 옮길 수 있어야 한다."""
    result = run_preflight(factory)

    assert result.ok is False
    assert isinstance(result.reason, str)
    assert result.reason


# ---------------------------------------------------------------------------
# 손상된 manifest — read_state 는 예외, run_preflight 는 reason
# ---------------------------------------------------------------------------


def test_malformed_json_raises_preflight_error(manifest_root: Path) -> None:
    (manifest_root / "runtime.runtime_clean.json").write_text(
        "{not json", encoding="utf-8"
    )

    with pytest.raises(PreflightError):
        read_state(LogicalDb.RUNTIME)


def test_applies_to_must_be_non_empty_list(manifest_root: Path) -> None:
    manifest = _manifest(
        profile="runtime",
        stage="runtime_clean",
        applies_to=[],
        policy="bootstrap_empty",
        row_count=0,
    )
    (manifest_root / "runtime.runtime_clean.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(PreflightError):
        read_state(LogicalDb.RUNTIME)


def test_applies_to_elements_must_be_strings(manifest_root: Path) -> None:
    """숫자가 섞이면 DSN 대조가 조용히 어긋나면서 방어가 무력화된다."""
    manifest = _manifest(
        profile="runtime",
        stage="runtime_clean",
        applies_to=["kosa_agent", 42],
        policy="bootstrap_empty",
        row_count=0,
    )
    (manifest_root / "runtime.runtime_clean.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(PreflightError):
        read_state(LogicalDb.RUNTIME)


def test_source_archive_must_be_string(manifest_root: Path) -> None:
    manifest = _manifest(
        profile="runtime",
        stage="runtime_clean",
        applies_to=["kosa_agent"],
        policy="bootstrap_empty",
        row_count=0,
    )
    manifest["source_archive_sha256"] = 12345
    (manifest_root / "runtime.runtime_clean.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(PreflightError):
        read_state(LogicalDb.RUNTIME)


def test_verification_policy_must_be_string(manifest_root: Path) -> None:
    manifest = _manifest(
        profile="runtime",
        stage="runtime_clean",
        applies_to=["kosa_agent"],
        policy="bootstrap_empty",
        row_count=0,
    )
    manifest["tables"]["action_history"]["verification_policy"] = 7
    (manifest_root / "runtime.runtime_clean.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(PreflightError):
        read_state(LogicalDb.RUNTIME)


def test_tables_must_be_object(manifest_root: Path) -> None:
    manifest = _manifest(
        profile="runtime",
        stage="runtime_clean",
        applies_to=["kosa_agent"],
        policy="bootstrap_empty",
        row_count=0,
    )
    manifest["tables"] = []
    (manifest_root / "runtime.runtime_clean.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(PreflightError):
        read_state(LogicalDb.RUNTIME)


def test_corrupted_manifest_becomes_reason_in_run_preflight(
    manifest_root: Path, factory: FakeFactory
) -> None:
    """run_preflight 는 손상된 manifest 에도 예외를 던지지 않는다."""
    (manifest_root / "runtime.runtime_clean.json").write_text(
        "{not json", encoding="utf-8"
    )

    result = run_preflight(factory)

    assert result.ok is False
    assert result.reason


# ---------------------------------------------------------------------------
# 실제 DSN 검증
#
# manifest 선언만 보면 .env 설정 실수를 놓친다. 동일성 판정은
# (host, port, database) 세 개가 기준이다. 이름만 비교하면 다른 서버의
# 같은 이름 DB 를 같다고 오판한다.
# ---------------------------------------------------------------------------


def test_identical_dsn_targets_are_rejected(manifest_root: Path) -> None:
    """manifest 선언은 정상이지만 두 DSN 이 같은 지점을 가리키는 경우."""
    _write_pair(manifest_root)
    factory = FakeFactory(
        runtime_database="kosa_agent", evaluation_database="kosa_agent"
    )

    result = run_preflight(factory)

    assert result.ok is False
    assert "같은 물리 DB" in result.reason


def test_same_database_name_on_different_port_is_distinct(
    manifest_root: Path,
) -> None:
    """host·이름이 같아도 port 가 다르면 다른 서버다."""
    _write_pair(manifest_root)
    factory = FakeFactory(
        runtime_port=53001,
        evaluation_port=53002,
    )

    result = run_preflight(factory)

    assert result.ok is True, result.reason


def test_host_case_is_normalized(manifest_root: Path) -> None:
    """DB.EXAMPLE.COM 과 db.example.com 은 같은 호스트다."""
    _write_pair(manifest_root)
    factory = FakeFactory(
        runtime_database="kosa_agent",
        evaluation_database="kosa_agent",
        runtime_host="DB.EXAMPLE.COM",
        evaluation_host="db.example.com",
    )

    result = run_preflight(factory)

    assert result.ok is False
    assert "같은 물리 DB" in result.reason


def test_runtime_dsn_outside_declaration_is_rejected(manifest_root: Path) -> None:
    """DSN 이 manifest applies_to 에 없는 DB 를 가리키는 경우."""
    _write_pair(manifest_root)
    factory = FakeFactory(runtime_database="some_other_db")

    result = run_preflight(factory)

    assert result.ok is False
    assert "some_other_db" in result.reason


def test_evaluation_dsn_outside_declaration_is_rejected(manifest_root: Path) -> None:
    _write_pair(manifest_root)
    factory = FakeFactory(evaluation_database="kosa_agent_e2e")

    result = run_preflight(factory)

    assert result.ok is False
    assert "kosa_agent_e2e" in result.reason


def test_dsn_check_uses_query_pool_only(manifest_root: Path) -> None:
    """logger 계정은 접근 범위가 좁아 검증에 쓰지 않는다."""
    _write_pair(manifest_root)
    factory = FakeFactory()

    run_preflight(factory)

    assert set(factory.requested_roles) == {PoolRole.QUERY}


def test_pool_configuration_error_becomes_reason(manifest_root: Path) -> None:
    """DSN 자체가 잘못된 경우도 예외가 아니라 reason 으로 돌아와야 한다."""
    _write_pair(manifest_root)
    factory = FakeFactory(
        error=PoolConfigurationError("TEXT2SQL_DATABASE_URL 이 설정되지 않았다.")
    )

    result = run_preflight(factory)

    assert result.ok is False
    assert "DSN 설정" in result.reason
