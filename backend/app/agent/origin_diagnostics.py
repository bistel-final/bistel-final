"""U11 private diagnostics: bounded display, lossless duplicate identity, no IO."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ORIGIN_REJECTION = "ORIGIN_BASIS_OUTSIDE_EVIDENCE"
DEGRADED_REASON = "DEGRADED:" + ORIGIN_REJECTION
REJECTION_CODES = frozenset(
    {
        "STRUCTURE_INVALID",
        "JSON_INVALID",
        "KOREAN_OUTPUT_REQUIRED",
        "PARAMETER_FINDING_REQUIRED",
        "CAUSE_SUMMARY_PARAMETER_MISSING",
        "ORIGIN_CLAIM_UNSUPPORTED",
        ORIGIN_REJECTION,
        "ALARM_CITATION_REQUIRED",
        "ALARM_CITATION_OUTSIDE_EVIDENCE",
        "DOCUMENT_CITATION_REQUIRED",
        "DOCUMENT_CITATION_OUTSIDE_EVIDENCE",
        "RELATION_CITATION_OUTSIDE_EVIDENCE",
        "LOT_HISTORY_CITATION_OUTSIDE_EVIDENCE",
        "PARAMETER_CITATION_OUTSIDE_EVIDENCE",
        "LLM_NOT_READY",
        "LLM_TIMEOUT",
        "LLM_DEPENDENCY",
        "HYPOTHESIS_STRUCTURE_INVALID",
        "HYPOTHESIS_PROMPT_BLOCKED",
        "HYPOTHESIS_PROMPT_TOO_LARGE",
        "PREDICTION_CONFLICT",
        "HYPOTHESIS_EVIDENCE_INSUFFICIENT",
        "READ_LOOP_INCOMPLETE",
        DEGRADED_REASON,
    }
)
NAMESPACE_PATTERN = r"(?:ALARM|CHUNK|RELATION|LOT_HIST|PARAMETER)"
DROPPED_TOKEN_PATTERN = NAMESPACE_PATTERN + r":DROPPED#[0-9a-f]{16}"
DISPLAY_ID_PATTERN = r"(?:[A-Za-z0-9:_./\-]{1,64}|<INVALID_FORMAT>)"


def rejection_code(reason: str | None, fallback: str = "STRUCTURE_INVALID") -> str:
    """Never persist field paths, model extra keys or provider exception text."""
    code = (reason or "").split(":", 1)[0]
    return (
        reason
        if reason in REJECTION_CODES
        else (code if code in REJECTION_CODES else fallback)
    )


class DroppedBasisRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Literal["ALARM", "CHUNK", "RELATION", "LOT_HIST", "PARAMETER"]
    id: str = Field(pattern="^" + DISPLAY_ID_PATTERN + "$", max_length=64)


class OriginDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dropped_basis_refs: tuple[DroppedBasisRef, ...] = Field(max_length=16)
    dropped_basis_count: int = Field(ge=1, le=40)
    dropped_evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def identity_consistent(self):
        ids = self.dropped_evidence_ids
        if (
            not 1 <= len(ids) <= self.dropped_basis_count
            or tuple(sorted(set(ids))) != ids
            or not all(re.fullmatch(DROPPED_TOKEN_PATTERN, v) for v in ids)
            or len(self.dropped_basis_refs) != min(self.dropped_basis_count, 16)
        ):
            raise ValueError("ORIGIN_DIAGNOSTIC_INCONSISTENT")
        return self


def capture_dropped(refs) -> OriginDiagnostics:
    """Hash ALL rejected identities before applying the display-only cap."""
    return OriginDiagnostics(
        dropped_basis_count=len(refs),
        dropped_evidence_ids=tuple(
            sorted(
                {
                    ref.namespace
                    + ":DROPPED#"
                    + hashlib.sha256(ref.id.encode()).hexdigest()[:16]
                    for ref in refs
                }
            )
        ),
        dropped_basis_refs=tuple(
            DroppedBasisRef(
                namespace=ref.namespace,
                id=ref.id
                if re.fullmatch(r"[A-Za-z0-9:_./\-]{1,64}", ref.id)
                else "<INVALID_FORMAT>",
            )
            for ref in refs[:16]
        ),
    )
