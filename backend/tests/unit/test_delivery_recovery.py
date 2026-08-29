"""V5-C-4.6 recovery CLI·callback trail·artifact verifier 단위 계약."""

from __future__ import annotations

import ast
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.delivery_callback import (
    DeliveryCallbackTrail,
    DeliveryCallbackTrailConfigError,
)
from app.agent.repository import (
    ActionDeliveryRow,
    DeliveryRecoveryReason,
    DeliveryRecoveryResult,
)
from app.common.enums import DeliveryChannel, DeliveryStatus
from scripts import manage_delivery_recovery as recovery
from scripts import verify_delivery_artifact as verifier

ACTION_ID = "ACT-c46000000000001"
NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _row(status: DeliveryStatus = DeliveryStatus.UNKNOWN) -> ActionDeliveryRow:
    return ActionDeliveryRow(
        action_id=ACTION_ID,
        channel=DeliveryChannel.EMAIL,
        status=status,
        request_hash="a" * 64,
        attempt_count=1,
        provider_message_id=None,
        started_at=NOW,
        completed_at=NOW if status is not DeliveryStatus.SENDING else None,
        last_error="DELIVERY_RESULT_UNKNOWN",
        result={"operator_decision": "UNKNOWN_CONFIRMED", "transport": "N8N_WEBHOOK"},
    )


def _trail_settings(root: Path, run_id: str = "run_20260829") -> SimpleNamespace:
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return SimpleNamespace(
        DELIVERY_CALLBACK_TRAIL_DIR=str(root),
        DELIVERY_CALLBACK_TRAIL_RUN_ID=run_id,
    )


def test_trail_disabled_pair_creates_no_file(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        DELIVERY_CALLBACK_TRAIL_DIR=None,
        DELIVERY_CALLBACK_TRAIL_RUN_ID=None,
    )

    assert DeliveryCallbackTrail.from_settings(settings) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("directory", "run_id"),
    [(None, "run"), ("/tmp", None), ("/tmp", "../escape")],
)
def test_trail_settings_pair_and_run_id_fail_closed(
    directory: str | None,
    run_id: str | None,
) -> None:
    settings = SimpleNamespace(
        DELIVERY_CALLBACK_TRAIL_DIR=directory,
        DELIVERY_CALLBACK_TRAIL_RUN_ID=run_id,
    )

    with pytest.raises(DeliveryCallbackTrailConfigError):
        DeliveryCallbackTrail.from_settings(settings)


def test_trail_refuses_existing_file_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "trail"
    settings = _trail_settings(root)
    first = DeliveryCallbackTrail.from_settings(settings)
    assert first is not None
    first.close()

    with pytest.raises(DeliveryCallbackTrailConfigError):
        DeliveryCallbackTrail.from_settings(settings)


def test_trail_refuses_symlink_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    settings = SimpleNamespace(
        DELIVERY_CALLBACK_TRAIL_DIR=str(linked),
        DELIVERY_CALLBACK_TRAIL_RUN_ID="run",
    )

    with pytest.raises(DeliveryCallbackTrailConfigError):
        DeliveryCallbackTrail.from_settings(settings)


def test_trail_refuses_group_or_world_accessible_directory(tmp_path: Path) -> None:
    root = tmp_path / "trail"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    settings = SimpleNamespace(
        DELIVERY_CALLBACK_TRAIL_DIR=str(root),
        DELIVERY_CALLBACK_TRAIL_RUN_ID="run",
    )

    with pytest.raises(DeliveryCallbackTrailConfigError):
        DeliveryCallbackTrail.from_settings(settings)


def test_trail_refuses_symlink_output_file(tmp_path: Path) -> None:
    root = tmp_path / "trail"
    settings = _trail_settings(root, "run")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("do-not-touch", encoding="utf-8")
    (root / "trail-run.jsonl").symlink_to(outside)

    with pytest.raises(DeliveryCallbackTrailConfigError):
        DeliveryCallbackTrail.from_settings(settings)
    assert outside.read_text(encoding="utf-8") == "do-not-touch"


def test_trail_concurrent_single_append_preserves_every_record(tmp_path: Path) -> None:
    trail = DeliveryCallbackTrail.from_settings(
        _trail_settings(tmp_path / "trail"),
        clock=lambda: NOW,
    )
    assert trail is not None

    def append(index: int) -> bool:
        return trail.append(
            action_id=f"ACT-{index:016d}",
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.SENT,
            duplicate=False,
            http_status=200,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(executor.map(append, range(40)))
    trail.close()

    lines = trail.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 40
    records = [json.loads(line) for line in lines]
    assert {record["action_id"] for record in records} == {
        f"ACT-{index:016d}" for index in range(40)
    }
    assert all(len(line.encode("utf-8")) + 1 <= 512 for line in lines)


def test_trail_write_failure_is_sanitized_and_nonthrowing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    trail = DeliveryCallbackTrail.from_settings(_trail_settings(tmp_path / "trail"))
    assert trail is not None
    monkeypatch.setattr(os, "write", lambda *_args: 0)

    assert (
        trail.append(
            action_id=ACTION_ID,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.SENT,
            duplicate=False,
            http_status=200,
        )
        is False
    )
    assert "WRITE_FAILED" in caplog.text
    assert ACTION_ID not in caplog.text
    trail.close()


class _Connection:
    def __init__(self, database: str) -> None:
        self.database = database

    def execute(self, _statement: Any) -> Any:
        return SimpleNamespace(scalar_one=lambda: self.database)


class _Engine:
    def __init__(self, database: str) -> None:
        self.connection = _Connection(database)

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        yield self.connection


def test_cli_confirmation_and_target_mismatch_precede_mutation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called = 0

    def mutate(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal called
        called += 1
        return None

    monkeypatch.setattr(recovery, "mark_delivery_unknown", mutate)
    base = [
        "--target",
        "kosa_agent_e2e",
        "confirm-unknown",
        "--action-id",
        ACTION_ID,
        "--channel",
        "EMAIL",
        "--provider-checked",
    ]
    assert recovery.main([*base, "--confirm", "wrong"]) == recovery.EXIT_NO_CHANGE
    assert called == 0

    confirmation = f"confirm-unknown kosa_agent_e2e {ACTION_ID} EMAIL"
    assert (
        recovery.main(
            [*base, "--confirm", confirmation],
            engine_factory=lambda: _Engine("kosa_agent"),
        )
        == recovery.EXIT_NO_CHANGE
    )
    assert called == 0
    output = capsys.readouterr().out
    assert "CONFIRMATION_MISMATCH" in output
    assert "TARGET_DB_MISMATCH" in output


def test_cli_rejects_disallowed_target_and_missing_provider_check_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = 0

    def mutate(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal called
        called += 1
        return None

    monkeypatch.setattr(recovery, "mark_delivery_unknown", mutate)
    assert recovery.main(["--target", "postgres", "list-stale"]) == recovery.EXIT_USAGE

    confirmation = f"confirm-unknown kosa_agent_e2e {ACTION_ID} EMAIL"
    assert (
        recovery.main(
            [
                "--target",
                "kosa_agent_e2e",
                "confirm-unknown",
                "--action-id",
                ACTION_ID,
                "--channel",
                "EMAIL",
                "--confirm",
                confirmation,
            ],
            engine_factory=lambda: _Engine("kosa_agent_e2e"),
        )
        == recovery.EXIT_NO_CHANGE
    )
    assert called == 0


@pytest.mark.parametrize(
    "reason",
    [
        DeliveryRecoveryReason.NO_TARGET,
        DeliveryRecoveryReason.STILL_FRESH,
        DeliveryRecoveryReason.CALLBACK_WON,
        DeliveryRecoveryReason.STATE_NOT_ALLOWED,
    ],
)
def test_cli_preserves_each_unknown_no_change_reason(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reason: DeliveryRecoveryReason,
) -> None:
    monkeypatch.setattr(
        recovery,
        "mark_delivery_unknown",
        lambda *_args, **_kwargs: DeliveryRecoveryResult(
            reason,
            None,
            None,
        ),
    )
    confirmation = f"confirm-unknown kosa_agent_e2e {ACTION_ID} EMAIL"

    assert (
        recovery.main(
            [
                "--target",
                "kosa_agent_e2e",
                "confirm-unknown",
                "--action-id",
                ACTION_ID,
                "--channel",
                "EMAIL",
                "--confirm",
                confirmation,
                "--provider-checked",
            ],
            engine_factory=lambda: _Engine("kosa_agent_e2e"),
        )
        == recovery.EXIT_NO_CHANGE
    )
    assert json.loads(capsys.readouterr().out)["reason_code"] == reason.value


def test_cli_applies_unknown_and_emits_only_safe_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        recovery,
        "mark_delivery_unknown",
        lambda *_args, **_kwargs: DeliveryRecoveryResult(
            DeliveryRecoveryReason.APPLIED,
            _row(),
            DeliveryStatus.SENDING,
        ),
    )
    confirmation = f"confirm-unknown kosa_agent_e2e {ACTION_ID} EMAIL"
    exit_code = recovery.main(
        [
            "--target",
            "kosa_agent_e2e",
            "confirm-unknown",
            "--action-id",
            ACTION_ID,
            "--channel",
            "EMAIL",
            "--confirm",
            confirmation,
            "--provider-checked",
        ],
        engine_factory=lambda: _Engine("kosa_agent_e2e"),
    )

    assert exit_code == recovery.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "action_id": ACTION_ID,
        "channel": "EMAIL",
        "delivery_status": "UNKNOWN",
        "reason_code": "APPLIED",
        "status": "PASSED",
    }


def test_recovery_mutators_have_no_automatic_callers() -> None:
    """retry·UNKNOWN 확정은 운영 CLI 외 timer/startup/catch-up에서 호출되지 않는다."""

    called_from: dict[str, set[str]] = {
        "mark_delivery_unknown": set(),
        "retry_failed_delivery": set(),
    }
    for root in (
        REPOSITORY_ROOT / "backend" / "app",
        REPOSITORY_ROOT / "backend" / "scripts",
    ):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in called_from:
                        called_from[node.func.id].add(
                            path.relative_to(REPOSITORY_ROOT).as_posix()
                        )

    assert called_from == {
        "mark_delivery_unknown": {"backend/scripts/manage_delivery_recovery.py"},
        "retry_failed_delivery": {"backend/scripts/manage_delivery_recovery.py"},
    }


def _artifact(channel: str = "EMAIL") -> dict[str, Any]:
    mes = channel == "MES_MOCK"
    return {
        "schema": verifier.SCHEMA,
        "run_id": "run_20260829",
        "action_id": ACTION_ID,
        "channel": channel,
        "injected_count": 1,
        "expected_first_count": 1,
        "expected_duplicate_count": 1,
        "expected_conflict_count": 1,
        "smtp_received_count": None if mes else 1,
        "actions_offset_before": 10 if mes else None,
        "actions_offset_after": 11 if mes else None,
        "result_offset_before": 20 if mes else None,
        "result_offset_after": 21 if mes else None,
        "result_key": ACTION_ID if mes else None,
        "wf4_lag_before": 1 if mes else None,
        "wf4_lag_after": 0 if mes else None,
    }


def _trail_lines(channel: str = "EMAIL") -> list[dict[str, Any]]:
    base = {
        "ts": NOW.isoformat(),
        "action_id": ACTION_ID,
        "channel": channel,
    }
    return [
        {**base, "status": "SENT", "duplicate": False, "http_status": 200},
        {**base, "status": "SENT", "duplicate": True, "http_status": 200},
        {**base, "status": None, "duplicate": None, "http_status": 409},
    ]


def _write_artifact_files(
    tmp_path: Path,
    payload: dict[str, Any],
    trail_rows: list[dict[str, Any]],
) -> tuple[Path, Path]:
    artifact = tmp_path / "artifact.json"
    trail = tmp_path / f"trail-{payload['run_id']}.jsonl"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    trail.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in trail_rows),
        encoding="utf-8",
    )
    return artifact, trail


@pytest.mark.parametrize("channel", ["EMAIL", "MES_MOCK"])
def test_artifact_verifier_accepts_exact_channel_evidence(
    tmp_path: Path,
    channel: str,
) -> None:
    artifact, trail = _write_artifact_files(
        tmp_path,
        _artifact(channel),
        _trail_lines(channel),
    )

    assert verifier.verify(artifact, trail) == {
        "run_id": "run_20260829",
        "channel": channel,
        "first_count": 1,
        "duplicate_count": 1,
        "conflict_count": 1,
    }


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("result_offset_after", 22, "MES_EFFECT_COUNT_MISMATCH"),
        ("result_key", "ACT-other", "MES_RESULT_KEY_MISMATCH"),
        ("wf4_lag_after", 1, "WF4_LAG_NOT_CONVERGED"),
    ],
)
def test_artifact_verifier_blocks_incomplete_mes_evidence(
    tmp_path: Path,
    key: str,
    value: Any,
    reason: str,
) -> None:
    payload = _artifact("MES_MOCK")
    payload[key] = value
    artifact, trail = _write_artifact_files(
        tmp_path,
        payload,
        _trail_lines("MES_MOCK"),
    )

    with pytest.raises(verifier.ArtifactBlocked, match=reason):
        verifier.verify(artifact, trail)


def test_artifact_verifier_blocks_missing_or_unexpected_fields(tmp_path: Path) -> None:
    payload = _artifact()
    payload.pop("smtp_received_count")
    artifact, trail = _write_artifact_files(tmp_path, payload, _trail_lines())

    with pytest.raises(verifier.ArtifactBlocked, match="ARTIFACT_FIELDS_INVALID"):
        verifier.verify(artifact, trail)


@pytest.mark.parametrize(
    "key",
    [
        "injected_count",
        "expected_first_count",
        "expected_duplicate_count",
        "expected_conflict_count",
    ],
)
def test_artifact_verifier_requires_each_acceptance_probe_once(
    tmp_path: Path,
    key: str,
) -> None:
    payload = _artifact()
    payload[key] = 0
    artifact, trail = _write_artifact_files(tmp_path, payload, _trail_lines())

    with pytest.raises(verifier.ArtifactBlocked, match="ARTIFACT_COUNT_INVALID"):
        verifier.verify(artifact, trail)


def test_artifact_verifier_rejects_trail_from_another_run(tmp_path: Path) -> None:
    artifact, trail = _write_artifact_files(tmp_path, _artifact(), _trail_lines())
    mismatched = trail.with_name("trail-another_run.jsonl")
    trail.rename(mismatched)

    with pytest.raises(verifier.ArtifactBlocked, match="TRAIL_RUN_MISMATCH"):
        verifier.verify(artifact, mismatched)


def test_artifact_verifier_requires_utc_trail_timestamp(tmp_path: Path) -> None:
    trail_rows = _trail_lines()
    trail_rows[0]["ts"] = "2026-08-29T21:00:00+09:00"
    artifact, trail = _write_artifact_files(tmp_path, _artifact(), trail_rows)

    with pytest.raises(verifier.ArtifactBlocked, match="TRAIL_TYPE_INVALID"):
        verifier.verify(artifact, trail)
