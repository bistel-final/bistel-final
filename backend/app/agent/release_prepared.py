"""V5-C-7.1 prepare → human SMTP grant → exact-runtime validation contracts."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    Sha256,
    canonical_json,
    digest,
    relative_parts,
    resolve_component,
)

Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Attempt = Annotated[str, Field(pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")]
ImageId = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
UtcTime = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")]
SMTP_APPROVERS = frozenset({"방대혁"})


def utc(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
            raise ValueError("noncanonical UTC")
        return parsed
    except ValueError as exc:
        raise EvidenceError("EVIDENCE_TIME_INVALID") from exc


def canonical_recipients(addresses: list[str]) -> list[str]:
    values: list[str] = []
    for address in addresses:
        trimmed = address.strip()
        if trimmed.count("@") != 1 or any(char.isspace() for char in trimmed):
            raise EvidenceError("RECIPIENT_INVALID")
        local, domain = trimmed.rsplit("@", 1)
        if not local or not domain or any(ord(char) < 32 for char in trimmed):
            raise EvidenceError("RECIPIENT_INVALID")
        values.append(f"{local}@{domain.casefold()}")
    if not values or len(values) != len(set(values)):
        raise EvidenceError("RECIPIENT_INVALID")
    return sorted(values)


def recipient_hash(addresses: list[str]) -> str:
    return digest("\n".join(canonical_recipients(addresses)).encode("utf-8"))


class SmtpConfigSnapshot(EvidenceModel):
    """Non-secret runtime projection; never pass raw n8n credentials/config."""

    n8n_workflow_versions: dict[str, str] = Field(min_length=1)
    smtp_host: str = Field(min_length=1)
    smtp_port: int = Field(ge=1, le=65535)
    smtp_from: str = Field(min_length=1)
    recipient_allowlist: list[str] = Field(min_length=1)
    wf2_callback_endpoint: str = Field(min_length=1)

    @field_validator("n8n_workflow_versions")
    @classmethod
    def workflow_versions(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            not key.strip() or not version.strip() for key, version in value.items()
        ):
            raise ValueError("CONFIG_WORKFLOW_INVALID")
        return value


def config_digest(payload: dict[str, Any]) -> str:
    """SHA-256 of the exact non-secret projection, with sorted JSON keys.

    Workflow IDs map to versions; recipients use recipient v2 normalization.
    Unknown fields (including credentials) are rejected, never hashed or logged.
    The future Stage2 caller must collect this projection from the live runtime,
    not reconstruct it from the prepared approval allowlist.
    """
    try:
        snapshot = SmtpConfigSnapshot.model_validate(payload).model_dump(mode="json")
        snapshot["recipient_allowlist"] = canonical_recipients(
            snapshot["recipient_allowlist"]
        )
        snapshot["smtp_from"] = canonical_recipients([snapshot["smtp_from"]])[0]
    except (ValidationError, EvidenceError):
        raise EvidenceError("CONFIG_DIGEST_PAYLOAD_INVALID") from None
    return digest(canonical_json(snapshot))


class Recipient(EvidenceModel):
    canonical_addresses: list[str] = Field(min_length=1, max_length=100)
    canonical_hash: Sha256
    recipient_hash_version: Literal[2]
    count: int = Field(ge=1)

    @model_validator(mode="after")
    def binding(self) -> Recipient:
        if (
            self.canonical_addresses != canonical_recipients(self.canonical_addresses)
            or self.count != len(self.canonical_addresses)
            or self.canonical_hash != recipient_hash(self.canonical_addresses)
        ):
            raise ValueError("RECIPIENT_BINDING_INVALID")
        return self


class RuntimeImage(EvidenceModel):
    image_id: ImageId
    label_revision: Revision


class RuntimeContainer(EvidenceModel):
    container_id: Sha256
    started_at: str = Field(min_length=20, max_length=40)

    @field_validator("started_at")
    @classmethod
    def valid_started_at(cls, value: str) -> str:
        # Docker RFC3339Nano is kept verbatim for exact drift comparison.
        if not value.endswith("Z"):
            raise ValueError("CONTAINER_TIME_INVALID")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class RuntimeImages(EvidenceModel):
    backend: RuntimeImage
    frontend: RuntimeImage
    runner: RuntimeImage


class RuntimeContainers(EvidenceModel):
    backend: RuntimeContainer
    frontend: RuntimeContainer
    runner: RuntimeContainer


class EffectiveEnv(EvidenceModel):
    # Only these public controls are persisted; DSN/credentials are not accepted.
    AGENT_AUTONOMY_LEVEL: Literal[3]
    AGENT_LEVEL3_ENABLED: Literal[True]
    AGENT_LEVEL3_DEMO_ACK: str = Field(max_length=64)
    level12_total: Literal[8]
    level3_total: Literal[10]
    send: Literal[2]
    same_tool_attempts: Literal[4]
    selector_steps: Literal[10]


class DbIdentity(EvidenceModel):
    host_alias: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    database: Literal["kosa_agent_e2e"]
    current_database: Literal["kosa_agent_e2e"]
    system_identifier: str = Field(pattern=r"^[0-9]+$")


class N8nEvidenceProbe(EvidenceModel):
    execution_data_retained: Literal[True]
    returns_action_id: Literal[True]
    returns_recipient: Literal[True]
    verdict: Literal["PASS"]


class Stage2Log(EvidenceModel):
    relative_path: Literal["stage2-log.jsonl"]
    prefix_sha256: Sha256
    prefix_bytes: int = Field(gt=0)


class PreparedAttempt(EvidenceModel):
    schema_version: Literal["level3-prepared-attempt-v1"]
    attempt_id: Attempt
    R: Revision
    images: RuntimeImages
    containers: RuntimeContainers
    effective_env: EffectiveEnv
    db_identity: DbIdentity
    n8n_evidence_probe: N8nEvidenceProbe
    recipient: Recipient
    reset_final_receipt_sha256: Sha256
    observer_baseline_sha256: Sha256
    approved_config_digest_allowlist: list[Sha256] = Field(min_length=1)
    max_external_emails: Literal[7]
    e2e_level3_preflight_output_sha256: Sha256
    prepared_at: UtcTime
    expires_at: UtcTime
    prev_state: Literal["empty", "bound"]
    prev_fault_path: str | None
    prev_golden_path: str | None
    prev_fault_sha256: Sha256 | None
    prev_golden_sha256: Sha256 | None
    prev_rev: Revision | None
    prev_attempt: Attempt | None
    running_rev: Revision
    stage2_log: Stage2Log
    last_ok_step: Literal["3b"]

    @model_validator(mode="after")
    def binding(self) -> PreparedAttempt:
        if not self.attempt_id.endswith(self.R[:12]):
            raise ValueError("ATTEMPT_ID_MISMATCH")
        if any(
            image.label_revision != self.R
            for image in (
                self.images.backend,
                self.images.frontend,
                self.images.runner,
            )
        ):
            raise ValueError("IMAGE_REVISION_MISMATCH")
        if (
            len(
                set(
                    item.container_id
                    for item in (
                        self.containers.backend,
                        self.containers.frontend,
                        self.containers.runner,
                    )
                )
            )
            != 3
        ):
            raise ValueError("CONTAINER_ROLE_MISMATCH")
        if utc(self.expires_at) - utc(self.prepared_at) != timedelta(minutes=30):
            raise ValueError("PREPARED_TTL_INVALID")
        previous = (
            self.prev_fault_path,
            self.prev_golden_path,
            self.prev_fault_sha256,
            self.prev_golden_sha256,
            self.prev_rev,
            self.prev_attempt,
        )
        if any((value is None) != (self.prev_state == "empty") for value in previous):
            raise ValueError("PREV_STATE_INVALID")
        if self.prev_state == "bound":
            prefix = f"/reports/cm-5.2/{self.prev_attempt}"
            if (
                self.prev_fault_path != f"{prefix}/fault-5class.json"
                or self.prev_golden_path != f"{prefix}/golden-flow.json"
            ):
                raise ValueError("PREV_STATE_INVALID")
        if len(set(self.approved_config_digest_allowlist)) != len(
            self.approved_config_digest_allowlist
        ):
            raise ValueError("CONFIG_DIGEST_DUPLICATE")
        return self


class SmtpGrant(EvidenceModel):
    schema_version: Literal["smtp-send-grant-v1"]
    grant_type: Literal["SMTP_SEND_GRANT"]
    attempt_id: Attempt
    prepared_attempt: Component
    approval_reference: str = Field(min_length=1, max_length=256)
    approver: str
    recipient_canonical_addresses: list[str]
    recipient_canonical_hash: Sha256
    recipient_hash_version: Literal[2]
    max_external_emails: Literal[7]
    approved_at: UtcTime

    @model_validator(mode="after")
    def binding(self) -> SmtpGrant:
        if self.approver not in SMTP_APPROVERS:
            raise ValueError("SMTP_APPROVER_NOT_ALLOWED")
        if self.prepared_attempt.relative_path != "prepared-attempt.json":
            raise ValueError("PREPARED_COMPONENT_INVALID")
        Recipient(
            canonical_addresses=self.recipient_canonical_addresses,
            canonical_hash=self.recipient_canonical_hash,
            recipient_hash_version=self.recipient_hash_version,
            count=len(self.recipient_canonical_addresses),
        )
        utc(self.approved_at)
        return self


def validate_grant(
    root: Path,
    prepared: PreparedAttempt,
    grant: SmtpGrant,
    *,
    resume_at: str,
) -> None:
    bound = PreparedAttempt.model_validate(
        resolve_component(root, grant.prepared_attempt)
    )
    if bound != prepared or grant.attempt_id != prepared.attempt_id:
        raise EvidenceError("EXTERNAL_EFFECT_APPROVAL_MISSING")
    recipient = prepared.recipient
    if (
        grant.recipient_canonical_addresses != recipient.canonical_addresses
        or grant.recipient_canonical_hash != recipient.canonical_hash
        or grant.recipient_hash_version != recipient.recipient_hash_version
    ):
        raise EvidenceError("EXTERNAL_EFFECT_APPROVAL_MISSING")
    resume = utc(resume_at)
    if resume >= utc(prepared.expires_at):
        raise EvidenceError("PREPARED_ATTEMPT_EXPIRED")
    if not utc(prepared.prepared_at) <= utc(grant.approved_at) <= resume:
        raise EvidenceError("EXTERNAL_EFFECT_APPROVAL_MISSING")


RUNTIME_BINDING_FIELDS = (
    "images",
    "containers",
    "effective_env",
    "db_identity",
    "recipient",
)


def validate_runtime(
    prepared: PreparedAttempt,
    observed: dict[str, Any],
    *,
    observed_config_digest: Sha256,
) -> None:
    expected = prepared.model_dump(mode="json")
    if (
        not isinstance(observed_config_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", observed_config_digest) is None
        or observed_config_digest not in prepared.approved_config_digest_allowlist
    ):
        raise EvidenceError("PREPARED_RUNTIME_DRIFT")
    if set(observed) != set(RUNTIME_BINDING_FIELDS) or any(
        canonical_json(observed[key]) != canonical_json(expected[key])
        for key in RUNTIME_BINDING_FIELDS
    ):
        raise EvidenceError("PREPARED_RUNTIME_DRIFT")


def validate_log_prefix(prepared: PreparedAttempt, attempt_root: Path) -> None:
    from app.agent.release_artifacts import read_private

    relative_parts(prepared.stage2_log.relative_path)
    log = read_private(attempt_root, prepared.stage2_log.relative_path)
    if (
        digest(log[: prepared.stage2_log.prefix_bytes])
        != prepared.stage2_log.prefix_sha256
    ):
        raise EvidenceError("PREPARED_LOG_PREFIX_MISMATCH")
