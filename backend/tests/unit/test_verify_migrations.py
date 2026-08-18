"""001 reference migration read-only verifier를 검증한다."""

from __future__ import annotations

import sys
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_reference_extensions as migration  # noqa: E402
import db_target  # noqa: E402
import verify_migrations as verifier  # noqa: E402


def _environment() -> dict[str, str]:
    host = "db.example.internal"
    port = 5432
    return {
        "POSTGRES_BOOTSTRAP_HOST": host,
        "POSTGRES_BOOTSTRAP_PORT": str(port),
        "POSTGRES_BOOTSTRAP_USER": "bootstrap_ddl",
        "POSTGRES_BOOTSTRAP_PASSWORD": "private-password",
        "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": db_target.host_fingerprint(
            host, port
        ),
    }


def _target(database: str = "kosa_agent") -> db_target.BootstrapTarget:
    return db_target.load_bootstrap_target(database, environ=_environment())


class _Context(AbstractContextManager[Any]):
    def __init__(self, value: Any) -> None:
        self.value = value

    def __enter__(self) -> Any:
        return self.value

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False


class _Connection:
    def begin(self) -> _Context:
        return _Context(None)


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()
        self.disposed = False

    def connect(self) -> _Context:
        return _Context(self.connection)

    def dispose(self) -> None:
        self.disposed = True


def _patch_success(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: str = "PRESENT",
    violations: list[tuple[str, str]] | None = None,
) -> None:
    monkeypatch.setattr(verifier, "_prepare_transaction", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        verifier,
        "inspect_database",
        lambda connection: migration.ReferenceInspection(
            state,
            (),
            None,
            {},
            "a" * 64,
        ),
    )
    monkeypatch.setattr(
        verifier,
        "load_marker",
        lambda *args, **kwargs: {
            "schema_signature_sha256": "a" * 64,
            "vector_extension_version": "0.8.6",
        },
    )
    monkeypatch.setattr(verifier, "action_history_count", lambda connection: 48)
    monkeypatch.setattr(
        verifier,
        "postcheck_database",
        lambda *args, **kwargs: migration.PostcheckResult(
            signature={},
            schema_signature_sha256="a" * 64,
            vector_extension_version="0.8.6",
            action_history_rows=48,
            alarm_event_rows=173,
        ),
    )
    monkeypatch.setattr(
        verifier,
        "public_privilege_violations",
        lambda connection: [] if violations is None else violations,
    )


def test_verify_database_returns_observation_and_not_ready_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_success(monkeypatch)
    engine = _Engine()

    result = verifier.verify_database(_target(), engine_factory=lambda target: engine)

    assert result == {
        "database": "kosa_agent",
        "profile": "runtime",
        "schema_signature_sha256": "a" * 64,
        "vector_extension_version": "0.8.6",
        "action_history_rows": 48,
        "alarm_event_rows": 173,
        "role_matrix": "NOT_READY(V4-CM-2.6)",
    }
    assert engine.disposed is True


def test_non_present_schema_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_success(monkeypatch, state="ABSENT")

    with pytest.raises(migration.ReferenceStateError, match="ABSENT") as caught:
        verifier.verify_database(_target(), engine_factory=lambda target: _Engine())
    assert caught.value.reason_code == "MIGRATION_NOT_APPLIED"


def test_marker_signature_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_success(monkeypatch)
    monkeypatch.setattr(
        verifier,
        "load_marker",
        lambda *args, **kwargs: {
            "schema_signature_sha256": "b" * 64,
            "vector_extension_version": "0.8.6",
        },
    )

    with pytest.raises(migration.ReferenceStateError, match="signature"):
        verifier.verify_database(_target(), engine_factory=lambda target: _Engine())


def test_public_privilege_violation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_success(monkeypatch, violations=[("document", "SELECT")])

    with pytest.raises(
        migration.ReferenceStateError, match="document:SELECT"
    ) as caught:
        verifier.verify_database(_target(), engine_factory=lambda target: _Engine())
    assert caught.value.reason_code == "PUBLIC_PRIVILEGE_DETECTED"


def test_main_normalizes_missing_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(verifier, "load_dotenv", lambda *args, **kwargs: None)

    def fail(database: str) -> db_target.BootstrapTarget:
        raise db_target.TargetValidationError(
            "bootstrap 설정이 비어 있습니다: POSTGRES_BOOTSTRAP_HOST"
        )

    monkeypatch.setattr(verifier, "load_bootstrap_target", fail)

    assert verifier.main(["--database", "kosa_agent"]) == 1
    output = capsys.readouterr()
    assert "reason=MISSING_CONFIGURATION" in output.err
    assert "POSTGRES_BOOTSTRAP_HOST" not in output.err


def test_main_preserves_specific_state_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(verifier, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(verifier, "load_bootstrap_target", lambda database: _target())

    def fail(target: db_target.BootstrapTarget) -> dict[str, Any]:
        raise migration.ReferenceStateError(
            "schema detail", reason_code="SCHEMA_SIGNATURE_MISMATCH"
        )

    monkeypatch.setattr(verifier, "verify_database", fail)

    assert verifier.main(["--database", "kosa_agent"]) == 1
    output = capsys.readouterr()
    assert "reason=SCHEMA_SIGNATURE_MISMATCH" in output.err
    assert "schema detail" not in output.err


def test_main_redacts_connection_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(verifier, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(verifier, "load_bootstrap_target", lambda database: _target())

    def fail(target: db_target.BootstrapTarget) -> dict[str, Any]:
        raise SQLAlchemyError("private-password db.example.internal bootstrap_ddl")

    monkeypatch.setattr(verifier, "verify_database", fail)

    assert verifier.main(["--database", "kosa_agent"]) == 1
    output = capsys.readouterr()
    assert "reason=CONNECT_OR_QUERY_FAILED" in output.err
    assert "private-password" not in output.err
    assert "db.example.internal" not in output.err
    assert "bootstrap_ddl" not in output.err
