from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import db_target  # noqa: E402
import postgres_transition  # noqa: E402
import verify_public_profiles as verifier  # noqa: E402


def _target(database: str = "kosa_agent") -> db_target.BootstrapTarget:
    return db_target.BootstrapTarget(
        "db.example.internal",
        5432,
        "bootstrap",
        "hidden",
        database,
        db_target.DATABASE_PROFILE[database],
    )


def _promotion_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, str, dict[str, bytes]]:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    change_ref = "GH-108"
    approval_path = root / f"approval.{change_ref}.json"
    approval_path.write_text("{}", encoding="utf-8")
    approval_sha = "a" * 64
    approval = {
        "change_ref": change_ref,
        "preflight_bundle_sha256": "b" * 64,
    }
    bundle: dict[str, bytes] = {}
    for database in verifier.TARGETS:
        payload = {
            "database": database,
            "profile": db_target.DATABASE_PROFILE[database],
            "change_ref": change_ref,
            "approval_sha256": approval_sha,
            "preflight_bundle_sha256": approval["preflight_bundle_sha256"],
        }
        raw = json.dumps(payload, sort_keys=True).encode()
        name = postgres_transition.marker_name(database)
        (root / name).write_bytes(raw)
        bundle[name] = raw

    def collect(*_args: Any, **_kwargs: Any) -> dict[str, dict[str, str]]:
        return {
            database: {
                "archive": "1" * 64,
                "view_sidecar": "2" * 64,
                "receipt": "3" * 64,
                "completion": "4" * 64,
                "committed_marker": hashlib.sha256(
                    (root / postgres_transition.marker_name(database)).read_bytes()
                ).hexdigest(),
            }
            for database in verifier.TARGETS
        }

    def closure_payload() -> dict[str, Any]:
        digests = collect()
        return {
            "change_ref": change_ref,
            "approval_sha256": approval_sha,
            "backup_root_mode": "0700",
            "archive_sha256_by_target": {
                database: digests[database]["archive"] for database in verifier.TARGETS
            },
            "view_sidecar_sha256_by_target": {
                database: digests[database]["view_sidecar"]
                for database in verifier.TARGETS
            },
            "receipt_sha256_by_target": {
                database: digests[database]["receipt"] for database in verifier.TARGETS
            },
            "completion_sha256_by_target": {
                database: digests[database]["completion"]
                for database in verifier.TARGETS
            },
            "committed_marker_sha256_by_target": {
                database: digests[database]["committed_marker"]
                for database in verifier.TARGETS
            },
        }

    closure_path = root / postgres_transition.closure_name(change_ref)
    closure_path.write_text(json.dumps(closure_payload()), encoding="utf-8")

    monkeypatch.setattr(
        verifier.postgres_backup,
        "validate_backup_root",
        lambda path, **_kwargs: path,
    )
    monkeypatch.setattr(
        verifier.postgres_backup,
        "backup_root_trust",
        lambda *_args, **_kwargs: ("0700", None),
    )
    monkeypatch.setattr(
        verifier.public_transition,
        "read_approval",
        lambda _path: (approval, approval_sha),
    )
    monkeypatch.setattr(
        verifier.public_transition,
        "collect_closure_digests",
        collect,
    )
    monkeypatch.setattr(
        verifier.postgres_transition,
        "validate_closure_schema",
        lambda _payload: None,
    )
    monkeypatch.setattr(
        verifier.postgres_transition,
        "validate_marker_schema",
        lambda _payload: None,
    )
    return root, approval_path, change_ref, bundle


def test_expected_marker_matrix_is_exact_7_7_3() -> None:
    counts = {
        database: len(names) for database, names in verifier.EXPECTED_MARKERS.items()
    }
    assert counts == {
        "kosa_agent": 7,
        "kosa_agent_e2e": 7,
        "kosa_text2sql": 3,
    }


def test_marker_matrix_rejects_unexpected_file_before_owner_validation(
    tmp_path: Path,
) -> None:
    database = "kosa_agent"
    for name in verifier.EXPECTED_MARKERS[database]:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / f"unexpected.{database}.json").write_text("{}", encoding="utf-8")

    with pytest.raises(verifier.PublicProfileError, match="MARKER_SET_MISMATCH"):
        verifier.validate_marker_matrix(database, _target(), marker_root=tmp_path)


def test_verify_registry_rejects_mutation_callable() -> None:
    def apply_database() -> None:
        return None

    unsafe = dict(verifier.VERIFY_REGISTRY)
    unsafe["full_database"] = apply_database

    with pytest.raises(verifier.PublicProfileError, match="VERIFY_REGISTRY_INVALID"):
        verifier.assert_verify_registry_safe(unsafe)


def test_role_and_rag_transaction_starts_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    class Context:
        def __enter__(self) -> Context:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class Connection(Context):
        def begin(self) -> Context:
            return Context()

        def exec_driver_sql(self, statement: str) -> None:
            statements.append(" ".join(statement.split()))

    class Engine:
        disposed = False

        def connect(self) -> Connection:
            return Connection()

        def dispose(self) -> None:
            self.disposed = True

    engine = Engine()
    registry = dict(verifier.VERIFY_REGISTRY)
    registry["role_contract"] = lambda *_args: object()
    registry["role_snapshot"] = lambda _connection: {}
    registry["role_inspection"] = lambda *_args: SimpleNamespace(state="READY")
    registry["rag_live"] = lambda *_args: None
    monkeypatch.setattr(verifier, "VERIFY_REGISTRY", registry)

    permission, rag = verifier._verify_role_and_rag(
        "kosa_agent",
        _target(),
        {},
        engine_factory=lambda _target: engine,
    )

    assert permission == {"status": verifier.PASSED}
    assert rag == {"status": verifier.PASSED}
    assert statements[0] == verifier.bootstrap_verifier.READ_ONLY_TRANSACTION_SQL
    assert engine.disposed is True


def test_full_verifier_maps_table_and_stage_mismatch_axes() -> None:
    class Mismatch(Exception):
        details = {
            "mismatches": [
                {"mismatch_kind": "CONTENT_HASH"},
                {"mismatch_kind": "CHECKPOINT_MARKER"},
            ]
        }

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise Mismatch

    checks = verifier._verify_full_database(
        "kosa_agent", environ={}, full_verifier=fail
    )

    assert checks["epoch"] == {"status": verifier.PASSED}
    assert checks["stage"] == {
        "status": verifier.FAILED,
        "reason_code": "CHECKPOINT_MARKER",
    }
    assert checks["table_rows_hash"] == {
        "status": verifier.FAILED,
        "reason_code": "CONTENT_HASH",
    }


def test_target_failure_does_not_erase_next_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_verify(database: str, **_kwargs: Any) -> verifier.TargetReport:
        calls.append(database)
        if database == "kosa_agent":
            raise RuntimeError("boom")
        return verifier.TargetReport(
            database,
            db_target.DATABASE_PROFILE[database],
            verifier.EXPECTED_STAGE[database],
            verifier.PASSED,
            {"full": {"status": verifier.PASSED}},
        )

    monkeypatch.setattr(verifier, "verify_target", fake_verify)
    payload, exit_code = verifier.verify_profiles(
        ("kosa_agent", "kosa_agent_e2e"),
        environ={},
        report_path=tmp_path / "report.json",
    )

    assert calls == ["kosa_agent", "kosa_agent_e2e"]
    assert [target["status"] for target in payload["targets"]] == [
        verifier.FAILED,
        verifier.PASSED,
    ]
    assert exit_code == verifier.EXIT_MISMATCH


def test_load_promotion_bundle_uses_explicit_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, approval, change_ref, expected = _promotion_fixture(tmp_path, monkeypatch)

    assert verifier._load_promotion_bundle(root, approval, change_ref) == expected


def test_promotion_rejects_two_approval_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, approval, change_ref, _ = _promotion_fixture(tmp_path, monkeypatch)
    (root / "approval.GH-999.json").write_text("{}", encoding="utf-8")

    with pytest.raises(verifier.PublicProfileError, match="BUNDLE_AMBIGUOUS"):
        verifier._load_promotion_bundle(root, approval, change_ref)


def test_promotion_rejects_marker_change_ref_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, approval, change_ref, _ = _promotion_fixture(tmp_path, monkeypatch)
    marker_path = root / postgres_transition.marker_name("kosa_agent")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["change_ref"] = "GH-999"
    marker_path.write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")
    closure_path = root / postgres_transition.closure_name(change_ref)
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["committed_marker_sha256_by_target"]["kosa_agent"] = hashlib.sha256(
        marker_path.read_bytes()
    ).hexdigest()
    closure_path.write_text(json.dumps(closure), encoding="utf-8")

    with pytest.raises(verifier.PublicProfileError, match="IDENTITY_SPLIT"):
        verifier._load_promotion_bundle(root, approval, change_ref)


@pytest.mark.parametrize(
    "digest_field",
    (
        "archive_sha256_by_target",
        "view_sidecar_sha256_by_target",
        "receipt_sha256_by_target",
        "completion_sha256_by_target",
        "committed_marker_sha256_by_target",
    ),
)
def test_promotion_rejects_each_closure_digest_tampering(
    digest_field: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, approval, change_ref, _ = _promotion_fixture(tmp_path, monkeypatch)
    closure_path = root / postgres_transition.closure_name(change_ref)
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure[digest_field]["kosa_agent"] = "f" * 64
    closure_path.write_text(json.dumps(closure), encoding="utf-8")

    with pytest.raises(verifier.PublicProfileError, match="CLOSURE_BLOCKED"):
        verifier._load_promotion_bundle(root, approval, change_ref)


def test_promotion_rejects_cli_and_approval_ref_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, approval, change_ref, _ = _promotion_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        verifier.public_transition,
        "read_approval",
        lambda _path: (
            {"change_ref": "GH-999", "preflight_bundle_sha256": "b" * 64},
            "a" * 64,
        ),
    )

    with pytest.raises(verifier.PublicProfileError, match="APPROVAL_IDENTITY"):
        verifier._load_promotion_bundle(root, approval, change_ref)


def test_promote_bytes_is_idempotent_and_rejects_partial_mismatch(
    tmp_path: Path,
) -> None:
    bundle = {
        "postgres_profile.kosa_agent.json": b"one",
        "postgres_profile.kosa_agent_e2e.json": b"two",
        "postgres_profile.kosa_text2sql.json": b"three",
    }

    assert verifier._promote_bytes(bundle, tmp_path) == (3, 0)
    mtimes = {name: (tmp_path / name).stat().st_mtime_ns for name in bundle}
    assert verifier._promote_bytes(bundle, tmp_path) == (0, 3)
    assert mtimes == {name: (tmp_path / name).stat().st_mtime_ns for name in bundle}

    (tmp_path / "postgres_profile.kosa_agent.json").write_bytes(b"different")
    with pytest.raises(verifier.PublicProfileError, match="DESTINATION_MISMATCH"):
        verifier._promote_bytes(bundle, tmp_path)
    assert (tmp_path / "postgres_profile.kosa_agent_e2e.json").read_bytes() == b"two"


def test_verify_mode_never_calls_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        verifier,
        "promote_profile_markers",
        lambda **_kwargs: pytest.fail("verify mode must not access evidence root"),
    )
    monkeypatch.setattr(
        verifier,
        "verify_profiles",
        lambda *_args, **_kwargs: (
            {
                "artifact_type": "public_profile_verification_report",
                "status": verifier.PASSED,
                "targets": [],
            },
            verifier.EXIT_OK,
        ),
    )

    assert verifier.main(["--report", str(tmp_path / "report.json")]) == 0
    assert "evidence" not in capsys.readouterr().out


def test_promotion_mode_has_no_database_connector_or_path_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        verifier,
        "_load_promotion_bundle",
        lambda *_args: {"postgres_profile.kosa_agent.json": b"{}"},
    )
    monkeypatch.setattr(verifier, "_promote_bytes", lambda *_args: (1, 0))
    monkeypatch.setattr(
        verifier,
        "create_engine",
        lambda *_args, **_kwargs: pytest.fail("promotion must not connect to DB"),
    )
    evidence = tmp_path / "private-evidence"
    approval = evidence / "approval.GH-108.json"

    exit_code = verifier.main(
        [
            "--promote-profile-markers",
            "--evidence-root",
            str(evidence),
            "--approval",
            str(approval),
            "--change-ref",
            "GH-108",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert str(evidence) not in output
    assert str(approval) not in output
