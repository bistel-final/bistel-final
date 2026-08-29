"""CM-4.7 public E2E reset evidence orchestrator.

이 진입점만 ``PASS``를 출력한다. reset child는 ``kosa_agent_e2e``만 연결하고,
이 프로세스의 observer는 ``kosa_agent``·``kosa_text2sql``을 read-only snapshot한다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import db_target
import e2e_reset_evidence as evidence
import reset_e2e_runtime as reset
import verify_public_profiles
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

OBSERVER_DATABASES = ("kosa_agent", "kosa_text2sql")
RESET_SCRIPT = Path(__file__).with_name("reset_e2e_runtime.py")


class OrchestrationError(RuntimeError):
    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


def _engine_for(target: db_target.BootstrapTarget) -> Engine:
    url = target.create_url()
    db_target.validate_url_components(url, target)
    return create_engine(url, pool_pre_ping=True, future=True)


def snapshot_observer(
    database: str,
    *,
    environ: Mapping[str, str],
    engine_factory: Callable[[db_target.BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = verify_public_profiles.MARKER_ROOT,
) -> dict[str, Any]:
    if database not in OBSERVER_DATABASES:
        raise OrchestrationError("TARGET_NOT_ALLOWED", 2)
    try:
        target = db_target.load_bootstrap_target(database, environ=environ)
        verify_public_profiles.validate_marker_matrix(
            database, target, marker_root=marker_root
        )
        engine = engine_factory(target)
        try:
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                connection.exec_driver_sql("SET LOCAL search_path = public")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
                db_target.validate_connected_identity(connection, target)
                db_target.set_and_validate_public_search_path(connection)
                return evidence.snapshot_database_fingerprint(connection)
        finally:
            engine.dispose()
    except OrchestrationError:
        raise
    except Exception as exc:
        raise OrchestrationError("BASELINE_INCOMPLETE", 3) from exc


def _load_child_payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _invoke_child(
    run_id: str,
    pre_path: Path,
    report_root: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        [
            sys.executable,
            str(RESET_SCRIPT),
            "--target",
            reset.TARGET_DATABASE,
            "--yes",
            "--confirm",
            reset.CONFIRMATION,
            "--run-id",
            run_id,
            "--pre-receipt",
            str(pre_path),
            "--report-root",
            str(report_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _terminal_receipt(
    run_id: str,
    *,
    status: str,
    reason: str,
    pre_sha256: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = evidence.base_receipt("e2e_reset_final", run_id)
    payload.update(
        {
            "status": status,
            "reason": reason,
            "pre_receipt_sha256": pre_sha256,
        }
    )
    payload.update(dict(extra or {}))
    return payload


def _save_terminal(report_root: Path, run_id: str, payload: Mapping[str, Any]) -> None:
    evidence.write_atomic_receipt(
        evidence.receipt_path(report_root, run_id, "final"), payload
    )


def _load_stage(
    report_root: Path,
    run_id: str,
    stage: str,
    artifact_type: str,
) -> tuple[dict[str, Any], str] | None:
    path = evidence.receipt_path(report_root, run_id, stage)
    if not path.exists():
        return None
    return evidence.load_receipt(path, artifact_type=artifact_type, run_id=run_id)


def _valid_applied_receipt(payload: Mapping[str, Any], pre_sha256: str) -> bool:
    try:
        before = evidence.assert_sha256(payload.get("preserved_before_sha256"))
        after = evidence.assert_sha256(payload.get("preserved_after_sha256"))
    except evidence.EvidenceError:
        return False
    return (
        payload.get("status") == "RESET_APPLIED"
        and payload.get("database") == reset.TARGET_DATABASE
        and payload.get("pre_receipt_sha256") == pre_sha256
        and payload.get("tables") == sorted(reset.TARGET_TABLES)
        and before == after
    )


def _valid_post_receipt(payload: Mapping[str, Any], pre_sha256: str) -> bool:
    row_counts = payload.get("row_counts")
    sequence_state = payload.get("sequence_state")
    if not isinstance(row_counts, Mapping) or not isinstance(sequence_state, Mapping):
        return False
    return (
        payload.get("status") == "POSTCHECK_PASSED"
        and payload.get("database") == reset.TARGET_DATABASE
        and payload.get("pre_receipt_sha256") == pre_sha256
        and set(row_counts) == set(reset.TARGET_TABLES)
        and all(type(value) is int and value == 0 for value in row_counts.values())
        and set(sequence_state) == set(reset.TARGET_SEQUENCES)
        and all(
            value == {"last_value": 1, "is_called": False}
            for value in sequence_state.values()
        )
        and type(payload.get("other_client_backends")) is int
        and payload.get("other_client_backends") == 0
    )


def run_orchestrated_reset(
    *,
    environ: Mapping[str, str],
    report_root: Path = evidence.DEFAULT_REPORT_ROOT,
    observer: Callable[..., dict[str, Any]] = snapshot_observer,
    child_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[dict[str, Any], int]:
    """before→durable pre→child→post/after→final hash chain."""

    unresolved = evidence.unresolved_run_ids(report_root)
    if unresolved:
        raise OrchestrationError("RESET_OUTCOME_UNKNOWN", 1)

    try:
        target = db_target.load_bootstrap_target(reset.TARGET_DATABASE, environ=environ)
        verify_public_profiles.validate_marker_matrix(reset.TARGET_DATABASE, target)
    except Exception as exc:
        raise OrchestrationError("BASELINE_INCOMPLETE", 3) from exc

    before: dict[str, dict[str, Any]] = {}
    for database in OBSERVER_DATABASES:
        before[database] = observer(database, environ=environ)

    run_id = evidence.new_run_id()
    pre = evidence.base_receipt("e2e_reset_pre", run_id, recorded_at=now().isoformat())
    pre.update(
        {
            "target_database": reset.TARGET_DATABASE,
            "target_host_fingerprint_sha256": db_target.host_fingerprint(
                target.host, target.port
            ),
            "observer_before_sha256": {
                database: evidence.assert_sha256(snapshot["sha256"])
                for database, snapshot in before.items()
            },
            "connector_ledger": {
                "reset_child": [reset.TARGET_DATABASE],
                "observer_read_only": list(OBSERVER_DATABASES),
            },
        }
    )
    pre_path = evidence.receipt_path(report_root, run_id, "pre")
    try:
        pre_sha = evidence.write_exclusive_receipt(pre_path, pre)
    except Exception as exc:
        raise OrchestrationError("PRE_RECEIPT_FAILED", 3) from exc

    completed = _invoke_child(run_id, pre_path, report_root, runner=child_runner)
    child_payload = _load_child_payload(completed)
    child_reason = str(child_payload.get("reason") or "RESET_FAILED")
    try:
        applied = _load_stage(report_root, run_id, "applied", "e2e_reset_applied")
        post = _load_stage(report_root, run_id, "post", "e2e_reset_post")
    except evidence.EvidenceError:
        payload = _terminal_receipt(
            run_id,
            status="OUTCOME_UNKNOWN",
            reason="RESET_OUTCOME_UNKNOWN",
            pre_sha256=pre_sha,
        )
        _save_terminal(report_root, run_id, payload)
        return payload, 1

    if completed.returncode == 3:
        if applied is not None or post is not None:
            payload = _terminal_receipt(
                run_id,
                status="OUTCOME_UNKNOWN",
                reason="RESET_OUTCOME_UNKNOWN",
                pre_sha256=pre_sha,
            )
            _save_terminal(report_root, run_id, payload)
            return payload, 1
        payload = _terminal_receipt(
            run_id,
            status="NO_MUTATION_BLOCKED",
            reason=child_reason,
            pre_sha256=pre_sha,
        )
        _save_terminal(report_root, run_id, payload)
        return payload, 3

    if completed.returncode != 0:
        if applied is None or not _valid_applied_receipt(applied[0], pre_sha):
            payload = _terminal_receipt(
                run_id,
                status="OUTCOME_UNKNOWN",
                reason="RESET_OUTCOME_UNKNOWN",
                pre_sha256=pre_sha,
            )
        else:
            payload = _terminal_receipt(
                run_id,
                status="APPLIED_BLOCKED",
                reason=(
                    child_reason
                    if child_reason
                    in {
                        "RESET_APPLIED_WRITER_REENTRY",
                        "RESET_APPLIED_EVIDENCE_BLOCKED",
                    }
                    else "RESET_APPLIED_EVIDENCE_BLOCKED"
                ),
                pre_sha256=pre_sha,
                extra={"applied_receipt_sha256": applied[1]},
            )
        _save_terminal(report_root, run_id, payload)
        return payload, 1

    if applied is None:
        payload = _terminal_receipt(
            run_id,
            status="OUTCOME_UNKNOWN",
            reason="RESET_OUTCOME_UNKNOWN",
            pre_sha256=pre_sha,
        )
        _save_terminal(report_root, run_id, payload)
        return payload, 1
    if not _valid_applied_receipt(applied[0], pre_sha):
        payload = _terminal_receipt(
            run_id,
            status="OUTCOME_UNKNOWN",
            reason="RESET_OUTCOME_UNKNOWN",
            pre_sha256=pre_sha,
        )
        _save_terminal(report_root, run_id, payload)
        return payload, 1
    if post is None or not _valid_post_receipt(post[0], pre_sha):
        payload = _terminal_receipt(
            run_id,
            status="APPLIED_BLOCKED",
            reason="RESET_APPLIED_EVIDENCE_BLOCKED",
            pre_sha256=pre_sha,
            extra={"applied_receipt_sha256": applied[1]},
        )
        _save_terminal(report_root, run_id, payload)
        return payload, 1

    _applied_payload, applied_sha = applied
    _post_payload, post_sha = post

    after: dict[str, dict[str, Any]] = {}
    try:
        for database in OBSERVER_DATABASES:
            after[database] = observer(database, environ=environ)
    except Exception:
        payload = _terminal_receipt(
            run_id,
            status="APPLIED_BLOCKED",
            reason="RESET_APPLIED_EVIDENCE_BLOCKED",
            pre_sha256=pre_sha,
            extra={
                "applied_receipt_sha256": applied_sha,
                "post_receipt_sha256": post_sha,
            },
        )
        _save_terminal(report_root, run_id, payload)
        return payload, 1
    before_sha = pre["observer_before_sha256"]
    after_sha = {
        database: evidence.assert_sha256(snapshot["sha256"])
        for database, snapshot in after.items()
    }
    unchanged = before_sha == after_sha
    payload = _terminal_receipt(
        run_id,
        status="PASS" if unchanged else "APPLIED_BLOCKED",
        reason="PASS" if unchanged else "RESET_APPLIED_EVIDENCE_BLOCKED",
        pre_sha256=pre_sha,
        extra={
            "applied_receipt_sha256": applied_sha,
            "post_receipt_sha256": post_sha,
            "observer_before_sha256": before_sha,
            "observer_after_sha256": after_sha,
            "connector_ledger": pre["connector_ledger"],
        },
    )
    _save_terminal(report_root, run_id, payload)
    return payload, 0 if unchanged else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V5-CM-4.7 public reset evidence")
    parser.add_argument("--target", required=True)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--list-unresolved", action="store_true")
    return parser


def _emit(payload: Mapping[str, Any]) -> None:
    safe = {
        "status": payload.get("status", "BLOCKED"),
        "reason": payload.get("reason", "RESET_FAILED"),
        "run_id": payload.get("run_id"),
    }
    print(json.dumps(safe, sort_keys=True, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.list_unresolved:
        print(
            json.dumps(
                {
                    "unresolved_run_ids": evidence.unresolved_run_ids(
                        evidence.DEFAULT_REPORT_ROOT
                    )
                },
                separators=(",", ":"),
            )
        )
        return 0
    if (
        args.target != reset.TARGET_DATABASE
        or not args.yes
        or args.confirm != reset.CONFIRMATION
    ):
        _emit({"status": "BLOCKED", "reason": "ARG_INVALID"})
        return 2
    load_dotenv()
    try:
        payload, exit_code = run_orchestrated_reset(
            environ=dict(os.environ), report_root=evidence.DEFAULT_REPORT_ROOT
        )
        _emit(payload)
        return exit_code
    except OrchestrationError as exc:
        _emit({"status": "BLOCKED", "reason": exc.reason_code})
        return exc.exit_code
    except Exception:
        _emit({"status": "BLOCKED", "reason": "RESET_FAILED"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
