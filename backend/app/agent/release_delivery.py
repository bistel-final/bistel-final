"""Offline SMTP acceptance cross-checks for one pre-HITL Stage2 batch.

The caller supplies independently validated round targets and component pins.
This verifies captured DB/WF2 evidence, not the authenticity of its live capture.
No network, grant writer, retry or production enable operation exists here.
"""

from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    Sha256,
    canonical_json,
    resolve_component,
)
from app.agent.release_prepared import (
    Attempt,
    Revision,
    UtcTime,
    canonical_recipients,
    parse_prepared,
    parse_smtp_grant,
    recipient_hash,
    utc,
    validate_grant,
)

Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S+$")]


class EmailTarget(EvidenceModel):
    action_id: Identifier
    action_code: Literal["WARNING", "EQP_HOLD"]
    email_kind: Literal["WARNING_NOTIFY", "APPROVAL_REQUEST"]
    request_hash: Sha256

    @model_validator(mode="after")
    def kind_matches_action(self):
        if (
            self.email_kind
            != {
                "WARNING": "WARNING_NOTIFY",
                "EQP_HOLD": "APPROVAL_REQUEST",
            }[self.action_code]
        ):
            raise ValueError("DELIVERY_TARGET_INVALID")
        return self


class EmailCallback(EvidenceModel):
    """Projection from persisted action_delivery, independent of WF2 results."""

    action_id: Identifier
    request_hash: Sha256
    channel: Literal["EMAIL"]
    status: Literal[
        "WAITING", "SENDING", "SENT", "FAILED", "UNKNOWN", "BLOCKED", "CANCELED"
    ]
    provider_message_id: Identifier | None
    completed_at: UtcTime | None


class ProviderAcceptance(EvidenceModel):
    """WF2 execution projection; execution ID alone is NOT SMTP acceptance."""

    action_id: Identifier
    request_hash: Sha256
    channel: Literal["EMAIL"]
    email_kind: Literal["WARNING_NOTIFY", "APPROVAL_REQUEST"]
    n8n_execution_id: Identifier
    n8n_status: Literal["success", "error", "running", "waiting", "canceled", "new"]
    provider_message_id: Identifier | None
    recipients: list[str] = Field(min_length=1, max_length=100)
    recipient_hash: Sha256
    observed_at: UtcTime


class EmailTargetV2(EmailTarget):
    email_kind: Literal["ACTION_NOTIFY"]

    @model_validator(mode="after")
    def kind_matches_action(self):
        return self


class ProviderAcceptanceV2(ProviderAcceptance):
    email_kind: Literal["ACTION_NOTIFY"]


class DeliveryReceipts(EvidenceModel):
    schema_version: Literal["level3-delivery-receipts-v1"]
    attempt_id: Attempt
    capture_phase: Literal["PRE_HITL"]
    recipient_hash_version: Literal[2]
    smtp_config_digest: Sha256
    callbacks: list[EmailCallback] = Field(max_length=100)
    executions: list[ProviderAcceptance] = Field(max_length=100)
    # IDs independently collected for the three existing approval-mail receipts.
    approval_execution_ids: list[Identifier] = Field(max_length=100)


class DeliveryReceiptsV2(EvidenceModel):
    schema_version: Literal["level3-delivery-receipts-v2"]
    attempt_id: Attempt
    capture_phase: Literal["POST_MOCK_CONVERGENCE"]
    recipient_hash_version: Literal[2]
    smtp_config_digest: Sha256
    callbacks: list[EmailCallback] = Field(max_length=100)
    executions: list[ProviderAcceptanceV2] = Field(max_length=100)
    hold_notify_execution_ids: list[Identifier] = Field(max_length=100)


def parse_delivery_receipts(value):
    model = (
        DeliveryReceiptsV2
        if value.get("schema_version") == "level3-delivery-receipts-v2"
        else DeliveryReceipts
    )
    return model.model_validate(value)


class DeliveryVerification(EvidenceModel):
    delivery_integrity: Literal["PASS"]
    attempt_id: Attempt
    evaluated_revision: Revision
    provider_acceptances: Literal[7]
    recipient_hash: Sha256
    recipient_hash_version: Literal[2]
    recipient_count: int = Field(ge=1)
    delivery_receipts: Component
    prepared_attempt: Component
    smtp_approval: Component


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise EvidenceError(code)


def verify_delivery(
    *,
    root: Path,
    delivery_receipts: Component,
    prepared_attempt: Component,
    smtp_approval: Component,
    targets: list[EmailTarget],
    evaluated_revision: str,
    expected_attempt_id: str,
    resume_at: str,
    captured_at: str,
) -> DeliveryVerification:
    """Resolve SHA → exact schema → cross-binding, including historic consent.

    TTL is checked at the recorded RESUME_WORKLOAD claim time, not at offline
    inspection time. Expiration after a valid resume does not revoke evidence.
    Missing/transient/UNKNOWN delivery raises; the caller must keep this axis
    separate from investigation robustness and must never resend from here.
    """
    try:
        return _verify_delivery(
            root=root,
            delivery_receipts=delivery_receipts,
            prepared_attempt=prepared_attempt,
            smtp_approval=smtp_approval,
            targets=targets,
            evaluated_revision=evaluated_revision,
            expected_attempt_id=expected_attempt_id,
            resume_at=resume_at,
            captured_at=captured_at,
        )
    except ValidationError:
        raise EvidenceError("DELIVERY_EVIDENCE_SCHEMA_INVALID") from None


def _verify_delivery(
    *,
    root,
    delivery_receipts,
    prepared_attempt,
    smtp_approval,
    targets,
    evaluated_revision,
    expected_attempt_id,
    resume_at,
    captured_at,
):
    for component, name in (
        (delivery_receipts, "delivery-receipts.round1.json"),
        (prepared_attempt, "prepared-attempt.json"),
        (smtp_approval, "smtp-approval-grant.json"),
    ):
        _require(component.relative_path == name, "DELIVERY_COMPONENT_INVALID")
    prepared = parse_prepared(resolve_component(root, prepared_attempt))
    grant = parse_smtp_grant(resolve_component(root, smtp_approval))
    receipts = parse_delivery_receipts(resolve_component(root, delivery_receipts))
    is_mock = isinstance(receipts, DeliveryReceiptsV2)
    target_model = EmailTargetV2 if is_mock else EmailTarget
    targets = [target_model.model_validate(t.model_dump()) for t in targets]
    _require(
        is_mock == (prepared.schema_version == "level3-prepared-attempt-v2"),
        "DELIVERY_POLICY_MISMATCH",
    )
    _require(
        receipts.attempt_id == prepared.attempt_id == expected_attempt_id
        and prepared.R == evaluated_revision
        and grant.prepared_attempt == prepared_attempt,
        "DELIVERY_BINDING_MISMATCH",
    )
    validate_grant(root, prepared, grant, resume_at=resume_at)
    _require(utc(resume_at) <= utc(captured_at), "DELIVERY_TIME_INVALID")
    _require(
        receipts.smtp_config_digest in prepared.approved_config_digest_allowlist,
        "DELIVERY_CONFIG_MISMATCH",
    )
    _require(
        len(targets) == 7
        and Counter(t.action_code for t in targets) == {"WARNING": 4, "EQP_HOLD": 3}
        and len({t.action_id for t in targets}) == 7
        and len({t.request_hash for t in targets}) == 7,
        "DELIVERY_TARGET_POPULATION_INVALID",
    )
    expected = {(t.action_id, t.request_hash): t for t in targets}
    callbacks = {(r.action_id, r.request_hash): r for r in receipts.callbacks}
    executions = {(r.action_id, r.request_hash): r for r in receipts.executions}
    _require(
        len(receipts.callbacks) == len(receipts.executions) == 7
        and set(callbacks) == set(executions) == set(expected)
        and len({r.n8n_execution_id for r in receipts.executions}) == 7,
        "DELIVERY_POPULATION_INVALID",
    )
    _require(
        len({r.provider_message_id for r in receipts.executions}) == 7,
        "DELIVERY_PROVIDER_ACCEPTANCE_INVALID",
    )
    for key, target in expected.items():
        callback, execution = callbacks[key], executions[key]
        _require(
            callback.status == "SENT"
            and execution.n8n_status == "success"
            and execution.email_kind == target.email_kind
            and execution.provider_message_id is not None
            and callback.provider_message_id == execution.provider_message_id,
            "DELIVERY_PROVIDER_ACCEPTANCE_INVALID",
        )
        _require(
            callback.completed_at is not None
            and utc(resume_at)
            <= utc(callback.completed_at)
            <= utc(execution.observed_at)
            <= utc(captured_at),
            "DELIVERY_TIME_INVALID",
        )
        _require(
            canonical_recipients(execution.recipients)
            == prepared.recipient.canonical_addresses
            and recipient_hash(execution.recipients)
            == execution.recipient_hash
            == prepared.recipient.canonical_hash,
            "DELIVERY_RECIPIENT_MISMATCH",
        )
    expected_approvals = {
        executions[key].n8n_execution_id
        for key, target in expected.items()
        if target.action_code == "EQP_HOLD"
    }
    evidence_ids = (
        receipts.hold_notify_execution_ids
        if is_mock
        else receipts.approval_execution_ids
    )
    _require(
        len(evidence_ids) == 3 and set(evidence_ids) == expected_approvals,
        "DELIVERY_APPROVAL_RECEIPT_MISMATCH",
    )
    # Files are mutable during inspection; no cached schema result may hide drift.
    for reference, value in (
        (prepared_attempt, prepared),
        (smtp_approval, grant),
        (delivery_receipts, receipts),
    ):
        _require(
            canonical_json(resolve_component(root, reference)) == canonical_json(value),
            "DELIVERY_EVIDENCE_DRIFT",
        )
    return DeliveryVerification(
        delivery_integrity="PASS",
        attempt_id=expected_attempt_id,
        evaluated_revision=evaluated_revision,
        provider_acceptances=7,
        recipient_hash=prepared.recipient.canonical_hash,
        recipient_hash_version=2,
        recipient_count=prepared.recipient.count,
        delivery_receipts=delivery_receipts,
        prepared_attempt=prepared_attempt,
        smtp_approval=smtp_approval,
    )
