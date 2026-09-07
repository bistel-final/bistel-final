"""Stage2's under-lock phase operations, not a service/workload controller.

Bash owns the lock and all lifecycle actions. These operations borrow that same
FD, classify before claiming, and issue one terminal only after Bash's cleanup.
"""

import os
import socket
import uuid
from pathlib import Path

from app.agent.release_artifacts import (
    EvidenceError,
    component_ref,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_lifecycle import (
    PHASES,
    ExternalEffects,
    ExternalEffectsV2,
    LifecycleClaim,
    LifecycleOutcome,
    LifecycleOutcomeV2,
    RoundCompletion,
    RoundCompletionV2,
    assert_stale_owner,
    authorize_transition,
    claim_filename,
    classify_state,
    lifecycle_lock,
    owner_identity,
    parse_outcome,
    read_lifecycle,
    resolve_failure_code,
    terminal_filename,
)
from app.agent.release_prepared import (
    parse_prepared,
    parse_smtp_grant,
    validate_grant,
    validate_log_prefix,
    validate_runtime,
)


def _claim(root, phase, owner_pid, now):
    if owner_pid != os.getppid():
        raise EvidenceError("LIFECYCLE_PARENT_REQUIRED")
    boot, start = owner_identity(owner_pid)
    if boot is None or start is None:
        raise EvidenceError("LIFECYCLE_OWNER_UNAVAILABLE")
    value = LifecycleClaim(
        schema_version="level3-lifecycle-claim-v1",
        phase=phase,
        prepared_attempt=component_ref(root, "prepared-attempt.json"),
        invocation_id=str(uuid.uuid4()),
        host=socket.gethostname(),
        pid=owner_pid,
        boot_id=boot,
        process_start_time=start,
        claimed_at=now,
    )
    return write_private(root, claim_filename(phase), value)


def begin_phase(
    *,
    root: Path,
    mode,
    lock_fd,
    owner_pid,
    clock,
    grant_path=None,
    read_runtime=None,
    publish_precondition=None,
):
    """No services here; return the exact authorized phase for the Bash owner.

    Bad grants/TTL/live drift become ABORT *before* a resume claim exists. Denied
    transitions and unproven stale owners create no claim and authorize no cleanup.
    """
    with lifecycle_lock(root, inherited_fd=lock_fd):
        before = read_lifecycle(root)
        state = classify_state(before)
        authorize_transition(state, mode)
        prepared = parse_prepared(parse_json(before["prepared-attempt.json"]))
        reason, primary = None, None
        claimed_at = None
        if mode == "RECOVER":
            unresolved = [
                p
                for p in PHASES
                if claim_filename(p) in before and terminal_filename(p) not in before
            ]
            if len(unresolved) != 1:
                raise EvidenceError("LIFECYCLE_MULTIPLE_UNRESOLVED")
            phase = unresolved[0]
            prior = LifecycleClaim.model_validate(
                parse_json(before[claim_filename(phase)])
            )
            assert_stale_owner(prior)
            reference = component_ref(root, claim_filename(phase))
        else:
            phase = mode
            if mode == "RESUME_WORKLOAD":
                try:
                    if grant_path != root / "smtp-approval-grant.json":
                        raise EvidenceError("EXTERNAL_EFFECT_APPROVAL_MISSING")
                    grant_bytes = read_private(root, "smtp-approval-grant.json")
                    grant = parse_smtp_grant(parse_json(grant_bytes))
                    validate_grant(root, prepared, grant, resume_at=clock())
                except Exception as error:
                    reason = (
                        "PREPARED_ATTEMPT_EXPIRED"
                        if str(error) == "PREPARED_ATTEMPT_EXPIRED"
                        else "EXTERNAL_EFFECT_APPROVAL_MISSING"
                    )
                if reason is None:
                    try:
                        validate_log_prefix(prepared, root.parent)
                        observed, config_sha = read_runtime(prepared)
                        validate_runtime(
                            prepared, observed, observed_config_digest=config_sha
                        )
                        if (
                            read_private(root, "smtp-approval-grant.json")
                            != grant_bytes
                        ):
                            raise ValueError
                    except Exception:
                        reason = "PREPARED_RUNTIME_DRIFT"
                if reason is None:
                    claimed_at = clock()
                    try:
                        validate_grant(root, prepared, grant, resume_at=claimed_at)
                    except EvidenceError as error:
                        reason = str(error)
                if reason is not None:
                    phase = "ABORT"
            elif mode == "ABORT":
                reason = "OPERATOR_ABORT"
            elif mode == "PUBLISH":
                try:
                    if publish_precondition(prepared) is not True:
                        raise ValueError
                except Exception:
                    primary = "PUBLISH_PRECONDITION_FAILED"
            if read_lifecycle(root) != before:
                raise EvidenceError("LIFECYCLE_EVIDENCE_DRIFT")
            reference = _claim(root, phase, owner_pid, claimed_at or clock())
        return {
            "phase": phase,
            "issued_by": "RECOVER" if mode == "RECOVER" else phase,
            "reason_code": reason,
            "primary_failure_code": primary,
            "claim": reference.model_dump(),
            "attempt_id": prepared.attempt_id,
            "R": prepared.R,
            "workload_authorized": phase == "RESUME_WORKLOAD" and mode != "RECOVER",
            "publish_authorized": phase == "PUBLISH"
            and mode != "RECOVER"
            and primary is None,
        }


def finish_phase(
    *,
    root,
    lock_fd,
    owner_pid,
    phase,
    issued_by,
    now,
    cleanup_result,
    restore_result,
    reason=None,
    primary=None,
    effects=None,
    held=False,
    published_root=None,
    post_freeze_callbacks=None,
):
    """O_EXCL terminal after actual cleanup results, using the shared schema."""
    with lifecycle_lock(root, inherited_fd=lock_fd):
        files = read_lifecycle(root)
        prepared = parse_prepared(parse_json(files["prepared-attempt.json"]))
        is_mock = prepared.schema_version == "level3-prepared-attempt-v2"
        if classify_state(files) != "UNRESOLVED":
            raise EvidenceError("LIFECYCLE_TRANSITION_INVALID")
        claim = LifecycleClaim.model_validate(parse_json(files[claim_filename(phase)]))
        if issued_by == "RECOVER":
            assert_stale_owner(claim)
            primary = (
                "PUBLISH_PHASE_RECOVERED"
                if phase == "PUBLISH"
                else "STALE_CLAIM_RECOVERED"
            )
        else:
            if (
                owner_pid != os.getppid()
                or claim.pid != owner_pid
                or claim.host != socket.gethostname()
            ):
                raise EvidenceError("LIFECYCLE_PARENT_REQUIRED")
            if owner_identity(owner_pid) != (claim.boot_id, claim.process_start_time):
                raise EvidenceError("LIFECYCLE_OWNER_CHANGED")
        if is_mock and isinstance(effects, ExternalEffects):
            if effects.state != "INDETERMINATE":
                raise EvidenceError("LIFECYCLE_EFFECT_POLICY_MISMATCH")
            effects = ExternalEffectsV2(
                state="INDETERMINATE",
                email_sent=None,
                mes_sent=None,
                basis=effects.basis,
            )
        if effects is None:
            effects = (ExternalEffectsV2 if is_mock else ExternalEffects)(
                state="INDETERMINATE",
                email_sent=None,
                **({"mes_sent": None} if is_mock else {"mes_blocked": None}),
                basis="No complete live external-effect observation",
            )
        # Preserve the caller's observed no-op cleanup/restore justification
        # when replacing counts with independently verified or fixed counts.
        basis_suffix = " ".join(
            token
            for token in effects.basis.split()
            if token
            in {"CLEANUP_E2E_ABSENT", "RESTORE_PREV_EMPTY", "RESTORE_ALREADY_TARGET"}
        )
        if phase == "PUBLISH" and issued_by == "PUBLISH":
            # A failed publication does not erase the already observed baseline.
            # Its scope is explicitly pre-HITL, not a new live SMTP observation.
            prior = parse_outcome(
                parse_json(files[terminal_filename("RESUME_WORKLOAD")])
            )
            effects = prior.external_effects.model_copy(
                update={
                    "basis": "Recorded POST_MOCK_CONVERGENCE from HELD outcome"
                    if is_mock
                    else "Recorded BATCH_BASELINE_PRE_HITL from HELD outcome"
                }
            )
        if held or (phase == "PUBLISH" and primary is None and issued_by == "PUBLISH"):
            # A positive terminal is never accepted from a caller-supplied 7/3.
            from app.agent.release_delivery import verify_delivery
            from app.agent.release_round import verify_round

            round_artifact, _, targets = verify_round(
                root, component_ref(root, "round1.json")
            )
            prepared = parse_prepared(parse_json(files["prepared-attempt.json"]))
            if (
                round_artifact.R != prepared.R
                or round_artifact.reset_attempt_id != prepared.attempt_id
                or round_artifact.images != prepared.images
                or round_artifact.preflight_output_sha256
                != prepared.e2e_level3_preflight_output_sha256
            ):
                raise EvidenceError("ROUND_PREPARED_BINDING_MISMATCH")
            resume = LifecycleClaim.model_validate(
                parse_json(files[claim_filename("RESUME_WORKLOAD")])
            )
            verify_delivery(
                root=root,
                delivery_receipts=round_artifact.delivery_receipts,
                prepared_attempt=round_artifact.prepared_attempt,
                smtp_approval=round_artifact.smtp_approval,
                targets=targets,
                evaluated_revision=prepared.R,
                expected_attempt_id=prepared.attempt_id,
                resume_at=resume.claimed_at,
                captured_at=round_artifact.captured_at,
            )
            effects = (ExternalEffectsV2 if is_mock else ExternalEffects)(
                state="KNOWN",
                email_sent=7,
                **({"mes_sent": 3} if is_mock else {"mes_blocked": 3}),
                basis="Verified POST_MOCK_CONVERGENCE delivery and Mock receipts"
                if is_mock
                else "Verified BATCH_BASELINE_PRE_HITL delivery receipt",
            )
            if phase == "PUBLISH":
                from app.agent.release_aggregate import _publications

                _publications(published_root, round_artifact)
        if phase == "ABORT" and issued_by == "ABORT":
            effects = (ExternalEffectsV2 if is_mock else ExternalEffects)(
                state="KNOWN",
                email_sent=0,
                **({"mes_sent": 0} if is_mock else {"mes_blocked": 0}),
                basis="Aborted before a resume-workload claim",
            )
        if basis_suffix and basis_suffix not in effects.basis:
            effects = effects.model_copy(
                update={"basis": effects.basis + " " + basis_suffix}
            )
        code = resolve_failure_code(issued_by, primary, cleanup_result, restore_result)
        common = dict(
            issued_by=issued_by,
            reason_code=reason,
            primary_failure_code=primary,
            failure_code=code,
            external_effects=effects,
            cleanup_result=cleanup_result,
            restore_result=restore_result,
        )
        if phase == "PUBLISH":
            prepared = parse_prepared(parse_json(files["prepared-attempt.json"]))
            hashes = {}
            for field, name in (
                ("attempt_artifact_sha256", "attempt.json"),
                ("golden_flow_sha256", "golden-flow.json"),
                ("fault_5class_sha256", "fault-5class.json"),
            ):
                try:
                    hashes[field] = component_ref(published_root, name).sha256
                except Exception:
                    hashes[field] = None
            value = (RoundCompletionV2 if is_mock else RoundCompletion)(
                schema_version="level3-round1-completion-v2"
                if is_mock
                else "level3-round1-completion-v1",
                **({"post_freeze_callbacks": post_freeze_callbacks} if is_mock else {}),
                round1=component_ref(root, "round1.json"),
                cm52_attempt_id=prepared.attempt_id,
                final_status="PASS" if code is None else "FAIL",
                lifecycle_claim_resume_workload=component_ref(
                    root, claim_filename("RESUME_WORKLOAD")
                ),
                lifecycle_outcome_resume_workload=component_ref(
                    root, terminal_filename("RESUME_WORKLOAD")
                ),
                lifecycle_claim_publish=component_ref(root, claim_filename("PUBLISH")),
                completed_at=now,
                **hashes,
                **common,
            )
        else:
            outcome = (
                "HELD"
                if held
                else "ABORTED"
                if phase == "ABORT" and code is None
                else "FAILED"
            )
            value = (LifecycleOutcomeV2 if is_mock else LifecycleOutcome)(
                schema_version="level3-lifecycle-outcome-v2"
                if is_mock
                else "level3-lifecycle-outcome-v1",
                phase=phase,
                outcome=outcome,
                claim=component_ref(root, claim_filename(phase)),
                recovery_started_workload=False,
                finished_at=now,
                **common,
            )
        # Verify the complete proposed state before the marker-last write.
        from app.agent.release_artifacts import canonical_json

        proposed = {**files, terminal_filename(phase): canonical_json(value)}
        classify_state(proposed)
        if read_lifecycle(root) != files:
            raise EvidenceError("LIFECYCLE_EVIDENCE_DRIFT")
        reference = write_private(root, terminal_filename(phase), value)
        return reference, value
