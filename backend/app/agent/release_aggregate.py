"""Offline single-batch qualification, not runtime/deployment authorization.

Only round1 and round1_completion are aggregate components. Follow their private
SHA links all the way to approval and lifecycle evidence; never accept a stored
PASS as proof. Live capture authenticity remains the Stage2 collector's duty.
"""

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    Sha256,
    canonical_json,
    component_ref,
    digest,
    parse_json,
    read_private,
    resolve_component,
    validate_report_root,
    write_private,
)
from app.agent.release_completion import verify_completion
from app.agent.release_delivery import verify_delivery
from app.agent.release_lifecycle import (
    LifecycleClaim,
    lifecycle_lock,
    parse_completion,
    read_lifecycle,
)
from app.agent.release_prepared import (
    Attempt,
    Revision,
    parse_prepared,
)
from app.agent.release_round import RoundAssessment, verify_round

PAYLOAD_NAMES = (
    "prepared-attempt.json",
    "smtp-approval-grant.json",
    "delivery-receipts.round1.json",
    "round1.json",
    "lifecycle-claim.resume_workload.json",
    "lifecycle-outcome.resume_workload.json",
    "lifecycle-claim.publish.json",
    "round1-completion.json",
)
PUBLICATION_NAMES = ("attempt.json", "golden-flow.json", "fault-5class.json")


class Aggregate(EvidenceModel):
    schema_version: Literal["level3-aggregate-v1"]
    R: Revision
    attempt_id: Attempt
    qualification_scope: Literal["SINGLE_STAGE2_BATCH"]
    round_count: Literal[1]
    run_count: Literal[12]
    repeatability: Literal["NOT_MEASURED"]
    round1: Component
    round1_completion: Component
    robustness_verdict: Literal["PASS", "FAIL"]
    delivery_integrity: Literal["PASS", "FAIL"]
    delivery_failed_checks: list[str]
    provider_acceptances: int | None = Field(ge=0, le=7)
    recipient_hash: Sha256
    recipient_hash_version: Literal[2]
    recipient_count: int = Field(ge=1)
    batch_summary: RoundAssessment


class AggregateV2(Aggregate):
    schema_version: Literal["level3-aggregate-v2"]


def parse_aggregate(value):
    model = (
        AggregateV2
        if value.get("schema_version") == "level3-aggregate-v2"
        else Aggregate
    )
    return model.model_validate(value)


def payload_names(root):
    prepared = parse_prepared(parse_json(read_private(root, "prepared-attempt.json")))
    return (
        (*PAYLOAD_NAMES, "mock-sources.round1.json", "mock-results.round1.json")
        if prepared.schema_version.endswith("-v2")
        else PAYLOAD_NAMES
    )


class AttemptReceipt(EvidenceModel):
    # Existing CM52 step 1 receipt; do not invent another deployment receipt.
    attempt: Attempt
    revision: Revision
    backend_image: str
    frontend_image: str
    project: Literal["bistel-team-e2e"]
    host: str = Field(min_length=1, max_length=256)


def _require(condition, code):
    if not condition:
        raise EvidenceError(code)


def _snapshot(root, *, tolerate_delivery_unreadable=False):
    # Remember absence too: adding a formerly missing receipt is drift.
    snapshot = {}
    for name in payload_names(root):
        try:
            snapshot[name] = (
                read_private(root, name) if os.path.lexists(root / name) else None
            )
        except (EvidenceError, OSError):
            if not tolerate_delivery_unreadable or name not in {
                "smtp-approval-grant.json",
                "delivery-receipts.round1.json",
            }:
                raise
            # Only the independent delivery axis can fail on these paths. The
            # actual verifier below still resolves them and cannot return PASS.
            snapshot[name] = ("UNREADABLE_DELIVERY_COMPONENT",)
    snapshot.update(read_lifecycle(root))
    return snapshot


def _publications(published_root, round_artifact):
    """Reuse the CM52 publication schemas, then bind their shared provenance."""
    from app.agent.decision import POLICY_VERSION
    from app.agent.golden_summary import validate_golden_summary
    from app.evaluation.fault_5class import validate_artifact

    raw = {name: read_private(published_root, name) for name in PUBLICATION_NAMES}
    try:
        attempt = AttemptReceipt.model_validate(parse_json(raw["attempt.json"]))
        golden = validate_golden_summary(parse_json(raw["golden-flow.json"]))
        fault = parse_json(raw["fault-5class.json"])
        validate_artifact(fault)
    except (ValueError, TypeError, KeyError, AttributeError):
        # The legacy validators may include offending values in errors.
        raise EvidenceError("AGGREGATE_PUBLICATION_SCHEMA_INVALID") from None
    _require(
        attempt.attempt == round_artifact.reset_attempt_id
        and attempt.revision == round_artifact.R
        and all(
            getattr(attempt, f"{role}_image")
            == f"{getattr(round_artifact.images, role).image_id} {round_artifact.R}"
            for role in ("backend", "frontend")
        ),
        "AGGREGATE_ATTEMPT_BINDING_MISMATCH",
    )
    _require(
        golden["status"] == "PASS"
        and (
            golden.get("protocol") == "MOCK-NOTIFY-V1"
            if getattr(round_artifact, "action_policy_version", None)
            == "MOCK-NOTIFY-V1"
            else "protocol" not in golden
        )
        and golden["dataset_epoch"] == round_artifact.dataset_epoch
        and fault["hard_gate_passed"] is True
        and fault["code_revision"] == round_artifact.R
        and fault["model_version"] == round_artifact.llm.hypothesis_model_revision
        and fault["prompt_version"] == round_artifact.llm.hypothesis_prompt_version
        and fault["policy_version"]
        == getattr(round_artifact, "action_policy_version", POLICY_VERSION)
        and fault["golden_evidence_sha256"] == golden["evidence_manifest_sha256"],
        "AGGREGATE_PUBLICATION_BINDING_MISMATCH",
    )
    return raw


def assess_aggregate(
    *,
    root: Path,
    published_root: Path,
    round1: Component,
    round1_completion: Component,
    expected_revision: str,
    expected_attempt_id: str,
    tolerate_delivery_unreadable: bool = False,
) -> Aggregate:
    """Recount two independent axes; incomplete/invalid completion cannot qualify.

    The caller supplies expected revision/ACK and publication directory, never an
    artifact-controlled absolute path. No current-time TTL or live calls occur.
    """
    try:
        before = _snapshot(
            root, tolerate_delivery_unreadable=tolerate_delivery_unreadable
        )
        artifact, summary, targets = verify_round(root, round1)
        _require(
            artifact.R == expected_revision
            and artifact.reset_attempt_id == expected_attempt_id,
            "AGGREGATE_EXPECTED_BINDING_MISMATCH",
        )
        prepared = parse_prepared(resolve_component(root, artifact.prepared_attempt))
        _require(
            prepared.R == artifact.R
            and prepared.attempt_id == artifact.reset_attempt_id
            and prepared.images == artifact.images
            and prepared.effective_env.investigation_budget_profile
            == artifact.investigation_budget_profile
            and prepared.e2e_level3_preflight_output_sha256
            == artifact.preflight_output_sha256,
            "AGGREGATE_PREPARED_BINDING_MISMATCH",
        )
        verify_completion(
            root=root,
            published_root=published_root,
            round1=round1,
            round1_completion=round1_completion,
            prepared_attempt=artifact.prepared_attempt,
            evaluated_revision=expected_revision,
            expected_attempt_id=expected_attempt_id,
            captured_at=artifact.captured_at,
        )
        publications = _publications(published_root, artifact)
        completion = parse_completion(parse_json(before["round1-completion.json"]))
        _require(
            all(
                digest(publications[name]) == getattr(completion, field)
                for name, field in (
                    ("attempt.json", "attempt_artifact_sha256"),
                    ("golden-flow.json", "golden_flow_sha256"),
                    ("fault-5class.json", "fault_5class_sha256"),
                )
            ),
            "AGGREGATE_PUBLICATION_SHA_MISMATCH",
        )
        resume = LifecycleClaim.model_validate(
            parse_json(read_private(root, "lifecycle-claim.resume_workload.json"))
        )
        delivery_failed = list(summary.delivery_failed_checks)
        acceptance = None
        try:
            delivery = verify_delivery(
                root=root,
                delivery_receipts=artifact.delivery_receipts,
                prepared_attempt=artifact.prepared_attempt,
                smtp_approval=artifact.smtp_approval,
                targets=targets,
                evaluated_revision=expected_revision,
                expected_attempt_id=expected_attempt_id,
                resume_at=resume.claimed_at,
                captured_at=artifact.captured_at,
            )
            acceptance = delivery.provider_acceptances
        except EvidenceError as exc:
            delivery_failed.append(str(exc))
        _require(
            before
            == _snapshot(
                root, tolerate_delivery_unreadable=tolerate_delivery_unreadable
            ),
            "AGGREGATE_EVIDENCE_DRIFT",
        )
        _require(
            all(
                read_private(published_root, name) == raw
                for name, raw in publications.items()
            ),
            "AGGREGATE_PUBLICATION_DRIFT",
        )
        is_mock = artifact.schema_version == "level3-round1-v2"
        return (AggregateV2 if is_mock else Aggregate)(
            schema_version="level3-aggregate-v2" if is_mock else "level3-aggregate-v1",
            R=artifact.R,
            attempt_id=artifact.reset_attempt_id,
            qualification_scope="SINGLE_STAGE2_BATCH",
            round_count=1,
            run_count=len(artifact.runs),
            repeatability="NOT_MEASURED",
            round1=round1,
            round1_completion=round1_completion,
            robustness_verdict=summary.robustness_verdict,
            delivery_integrity="FAIL" if delivery_failed else "PASS",
            delivery_failed_checks=sorted(set(delivery_failed)),
            provider_acceptances=acceptance,
            recipient_hash=prepared.recipient.canonical_hash,
            recipient_hash_version=2,
            recipient_count=prepared.recipient.count,
            batch_summary=summary,
        )
    except ValidationError:
        raise EvidenceError("AGGREGATE_SCHEMA_INVALID") from None


def verify_aggregate(
    path: Path,
    *,
    published_root: Path,
    expected_revision: str,
    expected_attempt_id: str,
) -> Aggregate:
    _require(path.name == "aggregate.json", "AGGREGATE_COMPONENT_INVALID")
    root = path.parent
    reference = component_ref(root, path.name)
    try:
        declared = parse_aggregate(resolve_component(root, reference))
    except ValidationError:
        raise EvidenceError("AGGREGATE_SCHEMA_INVALID") from None
    actual = assess_aggregate(
        root=root,
        published_root=published_root,
        round1=declared.round1,
        round1_completion=declared.round1_completion,
        expected_revision=expected_revision,
        expected_attempt_id=expected_attempt_id,
    )
    _require(actual == declared, "AGGREGATE_RECOUNT_MISMATCH")
    _require(
        canonical_json(resolve_component(root, reference)) == canonical_json(declared),
        "AGGREGATE_EVIDENCE_DRIFT",
    )
    return actual


def emit_aggregate(
    *,
    root: Path,
    repository: Path,
    published_root: Path,
    expected_revision: str,
    expected_attempt_id: str,
    lifecycle_lock_fd: int | None = None,
) -> tuple[Component, Aggregate]:
    """No-clobber private aggregate only; no grant, round capture, seal or deploy."""
    validate_report_root(root, root, repository)
    with lifecycle_lock(root, inherited_fd=lifecycle_lock_fd):
        _require(
            not os.path.lexists(root / "MANIFEST.sha256"), "AGGREGATE_BUNDLE_SEALED"
        )
        _require(
            not os.path.lexists(root / "aggregate.json"), "AGGREGATE_ALREADY_EXISTS"
        )
        artifact = assess_aggregate(
            root=root,
            published_root=published_root,
            round1=component_ref(root, "round1.json"),
            round1_completion=component_ref(root, "round1-completion.json"),
            expected_revision=expected_revision,
            expected_attempt_id=expected_attempt_id,
        )
        reference = write_private(root, "aggregate.json", artifact)
    return reference, artifact
