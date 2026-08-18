"""V4-D-1.1 preflight 단위 테스트.

실물 manifest 로 계약을 확인하고, 조작된 manifest 로 각 실패 경로를 검증한다.
DB 접속은 필요 없다. preflight 는 등록된 manifest 만 읽는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analytics import preflight
from app.analytics.db_pool import LogicalDb
from app.analytics.preflight import PreflightError, read_state, run_preflight

SOURCE_A = "a" * 64
SOURCE_B = "b" * 64


def _manifest(
    *,
    profile: str,
    stage: str,
    source: str = SOURCE_A,
    correction: str = "v1",
    applies_to: list[str],
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
    runtime_applies_to: list[str] | None = None,
    evaluation_applies_to: list[str] | None = None,
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
        stage="evaluation_mock",
        source=evaluation_source,
        correction=evaluation_correction,
        applies_to=evaluation_applies_to or ["kosa_text2sql"],
        policy=evaluation_policy,
        row_count=48 if evaluation_policy == "immutable_content" else 0,
    )

    (root / "runtime.runtime_clean.json").write_text(
        json.dumps(runtime), encoding="utf-8"
    )
    (root / "evaluation.evaluation_mock.json").write_text(
        json.dumps(evaluation), encoding="utf-8"
    )


@pytest.fixture()
def manifest_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(preflight, "MANIFEST_ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# 실물 manifest 계약
# ---------------------------------------------------------------------------


def test_registered_manifests_pass_preflight() -> None:
    """저장소에 등록된 실제 manifest 가 계약을 만족해야 한다."""
    result = run_preflight()

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
# 실패 경로
# ---------------------------------------------------------------------------


def test_different_source_archive_is_rejected(manifest_root: Path) -> None:
    _write_pair(manifest_root, evaluation_source=SOURCE_B)

    result = run_preflight()

    assert result.ok is False
    assert "source archive" in result.reason


def test_different_correction_version_is_rejected(manifest_root: Path) -> None:
    _write_pair(manifest_root, evaluation_correction="v2")

    result = run_preflight()

    assert result.ok is False
    assert "correction_version" in result.reason


def test_same_mutability_is_rejected(manifest_root: Path) -> None:
    """평가가 Runtime 처럼 쓰기 가능하면 재현성이 깨진다."""
    _write_pair(manifest_root, evaluation_policy="bootstrap_empty")

    result = run_preflight()

    assert result.ok is False


def test_overlapping_physical_database_is_rejected(manifest_root: Path) -> None:
    """두 논리 DB 가 같은 물리 DB 를 가리키면 평가가 Runtime 을 오염시킨다."""
    _write_pair(manifest_root, evaluation_applies_to=["kosa_agent"])

    result = run_preflight()

    assert result.ok is False
    assert "kosa_agent" in result.reason


def test_missing_manifest_is_reported(manifest_root: Path) -> None:
    result = run_preflight()

    assert result.ok is False
    assert "manifest" in result.reason


def test_malformed_manifest_raises(manifest_root: Path) -> None:
    (manifest_root / "runtime.runtime_clean.json").write_text(
        "{not json", encoding="utf-8"
    )

    with pytest.raises(PreflightError):
        read_state(LogicalDb.RUNTIME)


def test_preflight_returns_reason_instead_of_raising(manifest_root: Path) -> None:
    """호출부가 Tool 계약 {ok, ..., reason} 으로 그대로 옮길 수 있어야 한다."""
    result = run_preflight()

    assert result.ok is False
    assert isinstance(result.reason, str)
    assert result.reason
