"""V5-CM-4.7 reset·receipt·public orchestrator 단위 계약."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import e2e_reset_evidence as evidence  # noqa: E402
import orchestrate_e2e_reset_evidence as orchestrator  # noqa: E402
import reset_e2e_runtime as reset  # noqa: E402


def _target(database: str = reset.TARGET_DATABASE) -> Any:
    return SimpleNamespace(
        host="public-db.example",
        port=5432,
        username="bootstrap",
        password="not-serialized",
        database=database,
        profile="runtime" if database != "kosa_text2sql" else "evaluation",
    )


def _digest(token: str) -> str:
    return (token * 64)[:64]


def test_reset_allowlist_is_exact_and_never_cascades() -> None:
    assert len(reset.TARGET_TABLES) == 13
    assert len(set(reset.TARGET_TABLES)) == 13
    assert set(reset.TARGET_TABLES) == {
        *reset.agent_runtime.RUNTIME_TABLES,
        "action_history",
        *reset.checkpoint_contract.OPERATIONAL_TABLES,
    }
    assert "RESTART IDENTITY" in reset.TRUNCATE_SQL
    assert "CASCADE" not in reset.TRUNCATE_SQL.upper()
    assert reset.TRUNCATE_SQL.count("TRUNCATE") == 1


@pytest.mark.parametrize("database", ["kosa_agent", "kosa_text2sql", "other"])
def test_direct_cli_rejects_every_non_e2e_target_before_loading_env(
    monkeypatch: pytest.MonkeyPatch, database: str
) -> None:
    monkeypatch.setattr(
        reset.db_target,
        "load_bootstrap_target",
        lambda *_a, **_k: pytest.fail("target guard 뒤에만 호출돼야 한다"),
    )
    assert reset.main(["--target", database]) == 2


def test_confirmation_is_the_only_intent_token() -> None:
    parser = reset._parser()
    with pytest.raises(reset.NoMutationBlocked) as caught:
        reset._validate_args(
            parser.parse_args(
                [
                    "--target",
                    reset.TARGET_DATABASE,
                    "--yes",
                    "--confirm",
                    "wrong",
                    "--run-id",
                    "a" * 32,
                    "--pre-receipt",
                    "pre.json",
                ]
            )
        )
    assert caught.value.reason_code == "CONFIRMATION_MISMATCH"
    assert caught.value.exit_code == 3


def test_dry_run_rejects_apply_only_artifacts() -> None:
    parser = reset._parser()
    with pytest.raises(reset.ResetError) as caught:
        reset._validate_args(
            parser.parse_args(
                [
                    "--target",
                    reset.TARGET_DATABASE,
                    "--run-id",
                    "a" * 32,
                ]
            )
        )
    assert (caught.value.reason_code, caught.value.exit_code) == ("ARG_INVALID", 2)


class _Result:
    def __init__(self, rows: list[Mapping[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return self._rows


class _ProvenanceConnection:
    def __init__(self, values: Mapping[str, int]) -> None:
        self.values = values

    def exec_driver_sql(self, statement: str, *_a: Any) -> _Result:
        assert "missing_created" in statement
        return _Result([self.values])


def test_action_provenance_requires_every_axis_to_be_zero() -> None:
    good = dict.fromkeys(
        (
            "invalid_id",
            "missing_created",
            "orphan_created",
            "identity_mismatch",
            "orphan_approval",
            "orphan_delivery",
        ),
        0,
    )
    reset.assert_action_provenance(_ProvenanceConnection(good))
    bad = dict(good)
    bad["missing_created"] = 1
    with pytest.raises(reset.NoMutationBlocked) as caught:
        reset.assert_action_provenance(_ProvenanceConnection(bad))
    assert caught.value.reason_code == "ACTION_PROVENANCE_MISMATCH"


def test_pre_receipt_is_exclusive_and_unresolved_until_safe_final(
    tmp_path: Path,
) -> None:
    run_id = "a" * 32
    pre = evidence.base_receipt("e2e_reset_pre", run_id)
    pre.update(
        {
            "target_database": reset.TARGET_DATABASE,
            "target_host_fingerprint_sha256": _digest("a"),
        }
    )
    path = evidence.receipt_path(tmp_path, run_id, "pre")
    digest = evidence.write_exclusive_receipt(path, pre)
    assert len(digest) == 64
    with pytest.raises(evidence.EvidenceError) as caught:
        evidence.write_exclusive_receipt(path, pre)
    assert caught.value.reason_code == "RECEIPT_ALREADY_EXISTS"
    assert evidence.unresolved_run_ids(tmp_path) == (run_id,)

    final = evidence.base_receipt("e2e_reset_final", run_id)
    final.update({"status": "PASS", "reason": "PASS"})
    evidence.write_atomic_receipt(
        evidence.receipt_path(tmp_path, run_id, "final"), final
    )
    assert evidence.unresolved_run_ids(tmp_path) == ()


def test_atomic_receipt_never_overwrites_an_existing_stage(tmp_path: Path) -> None:
    run_id = "d" * 32
    path = evidence.receipt_path(tmp_path, run_id, "applied")
    first = evidence.base_receipt("e2e_reset_applied", run_id)
    first["status"] = "RESET_APPLIED"
    evidence.write_atomic_receipt(path, first)

    second = dict(first, status="SHOULD_NOT_REPLACE")
    with pytest.raises(evidence.EvidenceError) as caught:
        evidence.write_atomic_receipt(path, second)
    assert caught.value.reason_code == "RECEIPT_ALREADY_EXISTS"
    loaded, _digest_value = evidence.load_receipt(
        path, artifact_type="e2e_reset_applied", run_id=run_id
    )
    assert loaded["status"] == "RESET_APPLIED"


def _patch_public_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        orchestrator.db_target,
        "load_bootstrap_target",
        lambda database, **_k: _target(database),
    )
    monkeypatch.setattr(
        orchestrator.verify_public_profiles,
        "validate_marker_matrix",
        lambda *_a, **_k: {},
    )


def _successful_child(
    argv: list[str], **_kwargs: Any
) -> subprocess.CompletedProcess[str]:
    run_id = argv[argv.index("--run-id") + 1]
    pre_path = Path(argv[argv.index("--pre-receipt") + 1])
    root = Path(argv[argv.index("--report-root") + 1])
    _payload, pre_sha = evidence.load_receipt(
        pre_path, artifact_type="e2e_reset_pre", run_id=run_id
    )
    applied = evidence.base_receipt("e2e_reset_applied", run_id)
    applied.update(
        {
            "status": "RESET_APPLIED",
            "database": reset.TARGET_DATABASE,
            "pre_receipt_sha256": pre_sha,
            "preserved_before_sha256": _digest("c"),
            "preserved_after_sha256": _digest("c"),
            "tables": sorted(reset.TARGET_TABLES),
        }
    )
    evidence.write_atomic_receipt(
        evidence.receipt_path(root, run_id, "applied"), applied
    )
    post = evidence.base_receipt("e2e_reset_post", run_id)
    post.update(
        {
            "status": "POSTCHECK_PASSED",
            "database": reset.TARGET_DATABASE,
            "pre_receipt_sha256": pre_sha,
            "row_counts": dict.fromkeys(reset.TARGET_TABLES, 0),
            "sequence_state": {
                sequence: {"last_value": 1, "is_called": False}
                for sequence in reset.TARGET_SEQUENCES
            },
            "other_client_backends": 0,
        }
    )
    evidence.write_atomic_receipt(evidence.receipt_path(root, run_id, "post"), post)
    return subprocess.CompletedProcess(argv, 0, '{"reason":"RESET_APPLIED"}\n', "")


def test_orchestrator_is_the_only_public_pass_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_public_preflight(monkeypatch)

    def observer(database: str, **_kwargs: Any) -> dict[str, Any]:
        return {"sha256": _digest("a" if database == "kosa_agent" else "b")}

    payload, code = orchestrator.run_orchestrated_reset(
        environ={},
        report_root=tmp_path,
        observer=observer,
        child_runner=_successful_child,
    )
    assert code == 0
    assert (payload["status"], payload["reason"]) == ("PASS", "PASS")
    assert payload["connector_ledger"] == {
        "reset_child": [reset.TARGET_DATABASE],
        "observer_read_only": list(orchestrator.OBSERVER_DATABASES),
    }
    assert evidence.unresolved_run_ids(tmp_path) == ()


def test_missing_applied_receipt_is_outcome_unknown_and_blocks_next_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_public_preflight(monkeypatch)

    def observer(database: str, **_kwargs: Any) -> dict[str, Any]:
        return {"sha256": _digest("a" if database == "kosa_agent" else "b")}

    def crashed(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "")

    payload, code = orchestrator.run_orchestrated_reset(
        environ={}, report_root=tmp_path, observer=observer, child_runner=crashed
    )
    assert code == 1
    assert payload["status"] == "OUTCOME_UNKNOWN"
    assert payload["reason"] == "RESET_OUTCOME_UNKNOWN"
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.run_orchestrated_reset(
            environ={},
            report_root=tmp_path,
            observer=observer,
            child_runner=_successful_child,
        )
    assert (caught.value.reason_code, caught.value.exit_code) == (
        "RESET_OUTCOME_UNKNOWN",
        1,
    )


def test_observer_drift_is_applied_evidence_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_public_preflight(monkeypatch)
    calls = {database: 0 for database in orchestrator.OBSERVER_DATABASES}

    def observer(database: str, **_kwargs: Any) -> dict[str, Any]:
        calls[database] += 1
        suffix = "c" if database == "kosa_agent" and calls[database] == 2 else "a"
        return {"sha256": _digest(suffix)}

    payload, code = orchestrator.run_orchestrated_reset(
        environ={},
        report_root=tmp_path,
        observer=observer,
        child_runner=_successful_child,
    )
    assert code == 1
    assert (payload["status"], payload["reason"]) == (
        "APPLIED_BLOCKED",
        "RESET_APPLIED_EVIDENCE_BLOCKED",
    )
    assert evidence.unresolved_run_ids(tmp_path) == (payload["run_id"],)


def test_semantically_invalid_child_receipt_can_never_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_public_preflight(monkeypatch)

    def observer(database: str, **_kwargs: Any) -> dict[str, Any]:
        return {"sha256": _digest("a" if database == "kosa_agent" else "b")}

    def invalid_child(
        argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        completed = _successful_child(argv, **kwargs)
        run_id = argv[argv.index("--run-id") + 1]
        root = Path(argv[argv.index("--report-root") + 1])
        post_path = evidence.receipt_path(root, run_id, "post")
        post, _digest_value = evidence.load_receipt(post_path)
        post_path.unlink()
        post["row_counts"][reset.TARGET_TABLES[0]] = 1
        evidence.write_atomic_receipt(post_path, post)
        return completed

    payload, code = orchestrator.run_orchestrated_reset(
        environ={},
        report_root=tmp_path,
        observer=observer,
        child_runner=invalid_child,
    )
    assert code == 1
    assert (payload["status"], payload["reason"]) == (
        "APPLIED_BLOCKED",
        "RESET_APPLIED_EVIDENCE_BLOCKED",
    )


def test_cross_database_access_is_split_between_two_modules() -> None:
    reset_body = (SCRIPTS / "reset_e2e_runtime.py").read_text(encoding="utf-8")
    observer_body = (SCRIPTS / "orchestrate_e2e_reset_evidence.py").read_text(
        encoding="utf-8"
    )
    assert 'TARGET_DATABASE = "kosa_agent_e2e"' in reset_body
    assert 'kosa_agent"' not in reset_body
    assert "kosa_text2sql" not in reset_body
    assert 'OBSERVER_DATABASES = ("kosa_agent", "kosa_text2sql")' in observer_body
    assert not any(token in reset_body.lower() for token in ("dblink", "postgres_fdw"))
    assert '"PASS"' not in reset_body


def test_no_production_cli_localhost_relaxation_exists() -> None:
    for path in (
        SCRIPTS / "reset_e2e_runtime.py",
        SCRIPTS / "orchestrate_e2e_reset_evidence.py",
    ):
        body = path.read_text(encoding="utf-8").lower()
        assert "allow-localhost" not in body
        assert "skip-target" not in body
    orchestrator_body = (SCRIPTS / "orchestrate_e2e_reset_evidence.py").read_text(
        encoding="utf-8"
    )
    assert 'add_argument("--report-root"' not in orchestrator_body
