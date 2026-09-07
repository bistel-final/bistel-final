"""Read-only, independently pinned data-export grant verification.

This is not a grant writer and does not turn implementation/SMTP approval into
LLM authority. The operator must supply a separately approved private record and
its independently held raw SHA. Deletion/replacement/expiry revokes future calls.
It is a local acknowledgement, not a cryptographic human-identity attestation.
"""

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, model_validator

from app.agent.release_artifacts import (
    EvidenceError,
    EvidenceModel,
    Sha256,
    canonical_json,
    digest,
    parse_json,
    read_private,
)
from app.agent.release_prepared import Revision, UtcTime, utc
from app.agent.u10_batch import BatchBinding


class ExportBinding(EvidenceModel):
    evaluated_revision: Revision
    benchmark_sha256: Sha256
    llm_config_sha256: Sha256
    tool_contract_sha256: Sha256
    fixed_policy_sha256: Sha256
    attempt_count: Literal[32]


class ExportGrant(EvidenceModel):
    schema_version: Literal["u10-data-export-grant-v1"]
    purpose: Literal["U10_DATA_EXPORT_GRANT"]
    approved_by: Literal["방대혁"]
    binding: ExportBinding
    issued_at: UtcTime
    expires_at: UtcTime

    @model_validator(mode="after")
    def interval(self):
        if utc(self.issued_at) >= utc(self.expires_at):
            raise ValueError("U10_DATA_EXPORT_GRANT_INVALID")
        return self


class ExportAuthorization:
    def __init__(self, path: Path, expected_sha256: str, *, clock=None):
        self._path = path
        self._pin = expected_sha256
        self._clock = clock or (lambda: datetime.now(UTC))

    def __call__(self, binding: BatchBinding) -> bool:
        try:
            raw = read_private(self._path.parent, self._path.name)
            if digest(raw) != self._pin:
                raise EvidenceError("U10_DATA_EXPORT_NOT_AUTHORIZED")
            grant = ExportGrant.model_validate(parse_json(raw))
            actual = ExportBinding.model_validate(asdict(binding))
            now = self._clock()
            if (
                not isinstance(now, datetime)
                or now.tzinfo is None
                or now.utcoffset() is None
            ):
                raise EvidenceError("U10_DATA_EXPORT_NOT_AUTHORIZED")
            now = now.astimezone(UTC).replace(tzinfo=None)
            if canonical_json(grant.binding) != canonical_json(actual) or not utc(
                grant.issued_at
            ) <= now < utc(grant.expires_at):
                raise EvidenceError("U10_DATA_EXPORT_NOT_AUTHORIZED")
        except (OSError, EvidenceError, ValidationError, TypeError, ValueError):
            raise EvidenceError("U10_DATA_EXPORT_NOT_AUTHORIZED") from None
        return True


def guarded_call(authorize, binding: BatchBinding, operation):
    """Recheck immediately before each provider entry, not just once per batch."""

    def call(*args, **kwargs):
        if authorize(binding) is not True:
            raise EvidenceError("U10_DATA_EXPORT_NOT_AUTHORIZED")
        return operation(*args, **kwargs)

    return call
