"""One Stage2-owned operation per invocation under the inherited parent lock.

Not an orchestrator: Bash invokes begin, cleanup, restore, finish in that order.
The CLI never retries or starts work, grants, or a new recovery claim.
"""

import json
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.agent.release_artifacts import (  # noqa: E402
    EvidenceError,
    digest,
    parse_json,
    read_private,
    validate_report_root,
)
from app.agent.release_entry import inspect_entry  # noqa: E402
from app.agent.release_lifecycle import (  # noqa: E402
    PHASES,
    ExternalEffects,
    LifecycleClaim,
    assert_stale_owner,
    claim_filename,
    classify_state,
    lifecycle_lock,
    owner_identity,
    read_lifecycle,
)
from app.agent.release_phase import begin_phase, finish_phase  # noqa: E402
from app.agent.release_prepared import parse_prepared  # noqa: E402
from app.agent.release_stage2_recovery import cleanup_e2e, restore_level2  # noqa: E402
from app.agent.u10_cli import Parser, failure_code  # noqa: E402


def _now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _authorized(root, record, mode, owner_pid):
    files = read_lifecycle(root)
    if classify_state(files) != "UNRESOLVED":
        raise EvidenceError("LIFECYCLE_TRANSITION_INVALID")
    if type(record) is not dict or set(record) != {
        "phase",
        "issued_by",
        "reason_code",
        "primary_failure_code",
        "claim",
        "attempt_id",
        "R",
        "workload_authorized",
        "publish_authorized",
    }:
        raise EvidenceError("STAGE2_PHASE_RECORD_INVALID")
    phase = record["phase"]
    if phase not in PHASES:
        raise EvidenceError("STAGE2_PHASE_RECORD_INVALID")
    claim = LifecycleClaim.model_validate(parse_json(files[claim_filename(phase)]))
    prepared = parse_prepared(parse_json(files["prepared-attempt.json"]))
    if (
        record["claim"]
        != {
            "relative_path": claim_filename(phase),
            "sha256": digest(files[claim_filename(phase)]),
        }
        or record["R"] != prepared.R
        or record["attempt_id"] != prepared.attempt_id
        or record["workload_authorized"] is not False
        or record["publish_authorized"] is not False
        or record["primary_failure_code"] is not None
        or record["issued_by"] != ("RECOVER" if mode == "recover" else "ABORT")
        or record["reason_code"] != (None if mode == "recover" else "OPERATOR_ABORT")
    ):
        raise EvidenceError("STAGE2_PHASE_RECORD_INVALID")
    if mode == "recover":
        assert_stale_owner(claim)
    elif (
        phase != "ABORT"
        or claim.pid != owner_pid
        or owner_pid != os.getppid()
        or claim.host != socket.gethostname()
        or owner_identity(owner_pid) != (claim.boot_id, claim.process_start_time)
    ):
        raise EvidenceError("LIFECYCLE_PARENT_REQUIRED")
    return prepared


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("operation", choices=("begin", "cleanup", "restore", "finish"))
    for key in ("report-root", "repository", "prepared-attempt"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--mode", choices=("abort", "recover"), required=True)
    parser.add_argument("--lifecycle-lock-fd", type=int, required=True)
    parser.add_argument("--owner-pid", type=int, required=True)
    parser.add_argument("--prepared-sha256", required=True)
    parser.add_argument("--phase-record")
    parser.add_argument("--cleanup-result", choices=("OK", "FAILED", "NOT_ATTEMPTED"))
    parser.add_argument("--restore-result", choices=("OK", "FAILED"))
    try:
        args = parser.parse_args(argv)
        if (
            args.repository.resolve() != BACKEND.parent
            or args.owner_pid != os.getppid()
        ):
            raise EvidenceError("LIFECYCLE_PARENT_REQUIRED")
        report = validate_report_root(
            args.report_root, args.report_root, BACKEND.parent
        )
        relative = f"cm-5.2/{args.attempt_id}/robustness"
        root = report / relative
        if args.prepared_attempt != root / "prepared-attempt.json":
            raise EvidenceError("STAGE2_PREPARED_PATH_MISMATCH")
        with lifecycle_lock(
            report, relative_directory=relative, inherited_fd=args.lifecycle_lock_fd
        ):
            raw = read_private(report, relative + "/prepared-attempt.json")
            if digest(raw) != args.prepared_sha256:
                raise EvidenceError("STAGE2_ENTRY_DRIFT")
            prepared = parse_prepared(parse_json(raw))
            if prepared.attempt_id != args.attempt_id:
                raise EvidenceError("ATTEMPT_ID_MISMATCH")
            if args.operation == "begin":
                inspect_entry(
                    report_root=report,
                    repository=BACKEND.parent,
                    attempt_id=args.attempt_id,
                    mode=args.mode,
                    prepared_path=args.prepared_attempt,
                )
                result = begin_phase(
                    root=root,
                    mode=args.mode.upper(),
                    lock_fd=args.lifecycle_lock_fd,
                    owner_pid=args.owner_pid,
                    clock=_now,
                )
            else:
                record = parse_json((args.phase_record or "").encode())
                _authorized(root, record, args.mode, args.owner_pid)
                if (
                    prepared.schema_version == "level3-prepared-attempt-v2"
                    and record["phase"] == "PUBLISH"
                ):
                    from app.agent.release_stage2_publish import cleanup_context

                    prepared = cleanup_context(root.parent, allow_published=True)
                if args.operation == "cleanup":
                    result = cleanup_e2e(prepared)
                    if prepared.schema_version == "level3-prepared-attempt-v2":
                        from app.agent.release_retention import verify_restored

                        verify_restored(root.parent)
                elif args.operation == "restore":
                    keys = {
                        "artifact": "CM52_U10_ARTIFACT",
                        "evaluation-receipt": "CM52_U10_EVALUATION_RECEIPT",
                        "benchmark": "CM52_U10_BENCHMARK",
                        "benchmark-sha256": "CM52_U10_BENCHMARK_SHA256",
                    }
                    base = ["--repository", str(BACKEND.parent)]
                    for key, variable in keys.items():
                        value = os.environ.get(variable)
                        if not value:
                            raise EvidenceError(
                                "STAGE2_RESTORE_PREFLIGHT_INPUTS_REQUIRED"
                            )
                        base += ["--" + key, value]
                    result = restore_level2(
                        repository=BACKEND.parent,
                        report_root=report,
                        env_file=Path(
                            os.environ.get(
                                "CM52_ENV_FILE",
                                str(BACKEND.parent / "deploy/compose/.env.team"),
                            )
                        ),
                        prepared=prepared,
                        preflight_arguments=base,
                    )
                else:
                    if args.cleanup_result is None or args.restore_result is None:
                        parser.error("cleanup and restore results required")
                    effects = ExternalEffects(
                        state="INDETERMINATE",
                        email_sent=None,
                        mes_blocked=None,
                        basis="Prior external effects not re-executed"
                        + (
                            " CLEANUP_E2E_ABSENT"
                            if args.cleanup_result == "NOT_ATTEMPTED"
                            else ""
                        ),
                    )
                    reference, value = finish_phase(
                        root=root,
                        lock_fd=args.lifecycle_lock_fd,
                        owner_pid=args.owner_pid,
                        phase=record["phase"],
                        issued_by=record["issued_by"],
                        now=_now(),
                        reason=record["reason_code"],
                        primary=record["primary_failure_code"],
                        cleanup_result=args.cleanup_result,
                        restore_result=args.restore_result,
                        effects=effects,
                        published_root=root.parent,
                    )
                    result = {
                        "status": "TERMINAL",
                        "artifact": reference.model_dump(),
                        **value.model_dump(),
                    }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        code = (
            "WORKLOAD_ABORTED"
            if isinstance(exc, KeyboardInterrupt)
            else failure_code(exc)
        )
        print(json.dumps({"status": "FAIL", "code": code}, sort_keys=True))
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
