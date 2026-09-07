"""Fixed Stage2 leaves. The same Bash PID owns the inherited lock and ordering.

No caller-supplied PASS can authorize work: claims, saved phase records, byte
bindings and actual collectors are rechecked for each operation.
"""

import json
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.agent import release_stage2_prepare as preparation  # noqa: E402
from app.agent import release_stage2_publish as publication  # noqa: E402
from app.agent import release_stage2_workload as workload  # noqa: E402
from app.agent.release_artifacts import (  # noqa: E402
    EvidenceError,
    component_ref,
    digest,
    parse_json,
    read_private,
    validate_report_root,
    write_private,
)
from app.agent.release_lifecycle import (  # noqa: E402
    ExternalEffectsV2,
    LifecycleClaim,
    claim_filename,
    classify_state,
    lifecycle_lock,
    owner_identity,
    read_lifecycle,
)
from app.agent.release_phase import begin_phase, finish_phase  # noqa: E402
from app.agent.release_prepared import parse_prepared  # noqa: E402
from app.agent.release_stage2_inputs import preflight_arguments  # noqa: E402
from app.agent.release_stage2_recovery import cleanup_e2e, restore_level2  # noqa: E402
from app.agent.u10_cli import Parser, failure_code  # noqa: E402


def now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def authorized(root, record, mode, owner_pid):
    files = read_lifecycle(root)
    if classify_state(files) != "UNRESOLVED":
        raise EvidenceError("LIFECYCLE_TRANSITION_INVALID")
    saved = parse_json(read_private(root.parent, f"stage2-{mode}-record.json"))
    if saved != record:
        raise EvidenceError("STAGE2_PHASE_RECORD_INVALID")
    phase = record["phase"]
    claim = LifecycleClaim.model_validate(parse_json(files[claim_filename(phase)]))
    prepared = parse_prepared(parse_json(files["prepared-attempt.json"]))
    if (
        record["claim"] != component_ref(root, claim_filename(phase)).model_dump()
        or record["R"] != prepared.R
        or record["attempt_id"] != prepared.attempt_id
    ):
        raise EvidenceError("STAGE2_PHASE_RECORD_INVALID")
    if (
        owner_pid != os.getppid()
        or claim.pid != owner_pid
        or claim.host != socket.gethostname()
        or owner_identity(owner_pid) != (claim.boot_id, claim.process_start_time)
    ):
        raise EvidenceError("LIFECYCLE_PARENT_REQUIRED")
    return claim, prepared


def perform(args, report):
    a = report / "cm-5.2" / args.attempt_id
    root = a / "robustness"
    common = dict(
        repository=BACKEND.parent,
        report_root=report,
        env_file=args.env_file,
        attempt_id=args.attempt_id,
    )
    if args.mode == "prepare":
        if args.operation == "prepare":
            return preparation.prepare(**common, lock_fd=args.lifecycle_lock_fd)
        if args.operation not in (
            "prepare-cleanup",
            "prepare-restore",
            "prepare-finish",
        ):
            raise EvidenceError("STAGE2_OPERATION_INVALID")
        if (root / "prepared-attempt.json").exists():
            # If issuance succeeded but response was lost, keep PREPARED intact.
            # Explicit abort admission is required; never invent a fake lineage.
            raise EvidenceError("PREPARED_REQUIRES_ABORT")
        if not (a / "prepare-intent.json").exists():
            return dict(result="NOT_ATTEMPTED", status="NO_SERVICE_CHANGES")
        if args.operation == "prepare-cleanup":
            return preparation.cleanup_prepare(a)
        if args.operation == "prepare-restore":
            return preparation.restore_prepare(
                a=a, **{k: v for k, v in common.items() if k != "attempt_id"}
            )
        result = dict(
            status="FAIL",
            code="RESTORE_FAILED"
            if args.restore_result != "OK"
            else "STAGE2_STEP_FAILED",
            cleanup_result=args.cleanup_result,
            restore_result=args.restore_result,
        )
        cleanup_report = a / "prepare-cleanup.json"
        if cleanup_report.exists():
            value = parse_json(read_private(a, cleanup_report.name))
            if (
                type(value) is not dict
                or value.get("result") != args.cleanup_result
                or type(value.get("retention_restore")) is not dict
            ):
                raise EvidenceError("PREPARATION_CLEANUP_REPORT_INVALID")
            result["retention_restore"] = value["retention_restore"]
        write_private(a, "prepare-failure.json", result)
        return result
    raw = read_private(root, "prepared-attempt.json")
    prepared = parse_prepared(parse_json(raw))
    if (
        digest(raw) != args.prepared_sha256
        or prepared.attempt_id != args.attempt_id
        or prepared.schema_version != "level3-prepared-attempt-v2"
    ):
        raise EvidenceError("STAGE2_ENTRY_DRIFT")
    if args.operation == "begin":
        result = begin_phase(
            root=root,
            mode="RESUME_WORKLOAD" if args.mode == "resume_workload" else "PUBLISH",
            lock_fd=args.lifecycle_lock_fd,
            owner_pid=args.owner_pid,
            clock=now,
            grant_path=args.approval_record,
            read_runtime=lambda _: workload.live_binding(**common),
            publish_precondition=lambda _: publication.precondition(**common),
        )
        write_private(a, f"stage2-{args.mode}-record.json", result)
        return result
    record = parse_json((args.phase_record or "").encode())
    claim, prepared = authorized(root, record, args.mode, args.owner_pid)
    if args.operation in ("execute", "collect", "held"):
        if not record["workload_authorized"] or args.mode != "resume_workload":
            raise EvidenceError("STAGE2_WORKLOAD_NOT_AUTHORIZED")
        if args.operation == "execute":
            return workload.execute_workload(**common)
        if args.operation == "collect":
            return workload.collect_workload(resume_at=claim.claimed_at, **common)
        ref, value = finish_phase(
            root=root,
            lock_fd=args.lifecycle_lock_fd,
            owner_pid=args.owner_pid,
            phase=record["phase"],
            issued_by=record["issued_by"],
            now=now(),
            held=True,
            cleanup_result="NOT_ATTEMPTED",
            restore_result="NOT_ATTEMPTED",
            published_root=a,
        )
        return dict(status="HELD", artifact=ref.model_dump(), **value.model_dump())
    if args.operation == "publish":
        if not record["publish_authorized"] or args.mode != "publish":
            raise EvidenceError("STAGE2_PUBLISH_NOT_AUTHORIZED")
        result = publication.publish_evidence(**common)
        result.update(
            attempt_id=a.name,
            R=prepared.R,
            publications={
                n: component_ref(a, n).sha256
                for n in (
                    "attempt.json",
                    "golden-flow.json",
                    "fault-5class.json",
                )
            },
        )
        write_private(a, "publish-result.json", result)
        return result
    if args.operation == "cleanup":
        context = publication.cleanup_context(
            a, allow_published=record["phase"] == "PUBLISH"
        )
        result = cleanup_e2e(context)
        from app.agent.release_retention import verify_restored

        verify_restored(a)
        return result
    if args.operation == "restore":
        context = (
            publication.restore_context(a, published=args.published)
            if record["phase"] == "PUBLISH"
            else prepared
        )
        return restore_level2(
            prepared=context,
            preflight_arguments=preflight_arguments(BACKEND.parent),
            **{k: v for k, v in common.items() if k != "attempt_id"},
        )
    if (
        args.operation != "finish"
        or args.cleanup_result is None
        or args.restore_result is None
    ):
        raise EvidenceError("STAGE2_OPERATION_INVALID")
    primary = record["primary_failure_code"] or args.primary
    late = None
    if record["phase"] == "PUBLISH" and primary is None:
        publication.restore_context(a, published=True)
        late = parse_json(read_private(a, "publish-result.json"))[
            "post_freeze_callbacks"
        ]
    ref, value = finish_phase(
        root=root,
        lock_fd=args.lifecycle_lock_fd,
        owner_pid=args.owner_pid,
        phase=record["phase"],
        issued_by=record["issued_by"],
        now=now(),
        reason=record["reason_code"],
        primary=primary,
        cleanup_result=args.cleanup_result,
        restore_result=args.restore_result,
        effects=ExternalEffectsV2(
            state="INDETERMINATE",
            email_sent=None,
            mes_sent=None,
            basis="Actual cleanup observation"
            + (" CLEANUP_E2E_ABSENT" if args.cleanup_result == "NOT_ATTEMPTED" else ""),
        ),
        published_root=a,
        post_freeze_callbacks=late,
    )
    # Completion is issued before the aggregate and manifest. A sealing failure
    # is not a deploy grant; the offline seal CLI can finish after a recount.
    if record["phase"] == "PUBLISH" and value.final_status == "PASS":
        from app.agent.release_aggregate import emit_aggregate
        from app.agent.release_seal import seal_bundle

        inputs = dict(
            root=root,
            repository=BACKEND.parent,
            published_root=a,
            expected_revision=prepared.R,
            expected_attempt_id=a.name,
            lifecycle_lock_fd=args.lifecycle_lock_fd,
        )
        emit_aggregate(**inputs)
        seal_bundle(**inputs)
    return dict(status="TERMINAL", artifact=ref.model_dump(), **value.model_dump())


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "prepare",
            "prepare-cleanup",
            "prepare-restore",
            "prepare-finish",
            "begin",
            "execute",
            "collect",
            "held",
            "publish",
            "cleanup",
            "restore",
            "finish",
        ),
    )
    for key in ("report-root", "repository", "env-file"):
        parser.add_argument("--" + key, required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument(
        "--mode", choices=("prepare", "resume_workload", "publish"), required=True
    )
    parser.add_argument("--lifecycle-lock-fd", required=True, type=int)
    parser.add_argument("--owner-pid", required=True, type=int)
    parser.add_argument("--prepared-sha256")
    parser.add_argument("--approval-record", type=Path)
    parser.add_argument("--phase-record")
    parser.add_argument("--cleanup-result", choices=("OK", "FAILED", "NOT_ATTEMPTED"))
    parser.add_argument("--restore-result", choices=("OK", "FAILED"))
    parser.add_argument(
        "--primary",
        choices=("STAGE2_STEP_FAILED", "WORKLOAD_ABORTED", "ARTIFACT_PUBLISH_FAILED"),
    )
    parser.add_argument("--published", action="store_true")
    try:
        args = parser.parse_args(argv)
        import re

        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", args.attempt_id):
            raise EvidenceError("ATTEMPT_ID_MISMATCH")
        if (
            args.repository.resolve() != BACKEND.parent
            or args.owner_pid != os.getppid()
        ):
            raise EvidenceError("LIFECYCLE_PARENT_REQUIRED")
        report = validate_report_root(
            args.report_root, args.report_root, BACKEND.parent
        )
        with lifecycle_lock(
            report,
            relative_directory=f"cm-5.2/{args.attempt_id}/robustness",
            inherited_fd=args.lifecycle_lock_fd,
        ):
            result = perform(args, report)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(
            json.dumps(
                dict(
                    status="FAIL",
                    code="WORKLOAD_ABORTED"
                    if isinstance(exc, KeyboardInterrupt)
                    else failure_code(exc),
                ),
                sort_keys=True,
            )
        )
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
