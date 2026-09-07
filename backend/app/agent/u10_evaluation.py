"""Preflight checks 1–3: private receipt binding and offline recalculation.

No artifact issuance, command execution or deployment permission. Receipt fields
are audit claims, not proof of an actual LLM run or a clean merged revision.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.agent.release_artifacts import (
    EvidenceError,
    EvidenceModel,
    Sha256,
    digest,
    parse_json,
    read_private,
)
from app.agent.release_prepared import Revision, UtcTime, utc
from app.agent.u10_comparison import U10_VERDICT_RULES, validate_artifact
from app.agent.u10_revision import RevisionTrees

Verdict = Literal[
    "AGENT_JUSTIFICATION_ESTABLISHED_V21",
    "AGENT_JUSTIFICATION_NOT_ESTABLISHED_V21",
]


class EvaluationReceipt(EvidenceModel):
    schema_version: Literal["u10-evaluation-receipt-v1"]
    artifact_sha256: Sha256
    fixture_sha256: Sha256
    oracle_sha256: Sha256
    inventory_sha256: Sha256
    verdict_rules_sha256: Sha256
    tool_contract_sha256: Sha256
    fixed_policy_sha256: Sha256
    effective_budget_policy: dict[str, int]
    validator_command: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=4096)]],
        Field(min_length=1, max_length=32),
    ]
    validator_exit_code: Literal[0]
    agent_justification_verdict: Verdict
    evaluated_revision: Revision
    evaluated_tree_oid: RevisionTrees
    git_object_format: Literal["sha1", "sha256"]
    decided_at: UtcTime

    @field_validator("decided_at")
    @classmethod
    def valid_time(cls, value: str) -> str:
        utc(value)
        return value

    @model_validator(mode="after")
    def valid_trees(self) -> EvaluationReceipt:
        size = 40 if self.git_object_format == "sha1" else 64
        if any(
            not re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value)
            for value in self.evaluated_tree_oid.model_dump().values()
        ):
            raise ValueError("receipt tree format invalid")
        return self


class EvaluationObservation(EvidenceModel):
    receipt: EvaluationReceipt
    artifact_sha256: Sha256
    receipt_sha256: Sha256
    benchmark_sha256: Sha256
    agent_verdict: Verdict
    verdict_reason: str | None


def verify_evaluation(
    *,
    artifact: Path,
    evaluation_receipt: Path,
    benchmark: Path,
    pinned_benchmark_sha256: str,
) -> EvaluationObservation:
    """Rerun the same validator as verify_u10_comparison, not a recorded command.

    Benchmark raw-file SHA must be pinned independently before execution. The
    artifact's benchmark_sha256 remains its canonical DTO hash, not raw-file SHA.
    Negative research verdicts are valid and are returned unchanged.
    """
    if type(pinned_benchmark_sha256) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", pinned_benchmark_sha256
    ):
        raise EvidenceError("U10_BENCHMARK_PIN_INVALID")
    try:
        benchmark_bytes = read_private(benchmark.parent, benchmark.name)
        if digest(benchmark_bytes) != pinned_benchmark_sha256:
            raise EvidenceError("PINNED_BENCHMARK_SHA_MISMATCH")
        receipt_bytes = read_private(evaluation_receipt.parent, evaluation_receipt.name)
        receipt = EvaluationReceipt.model_validate(parse_json(receipt_bytes))
        artifact_bytes = read_private(artifact.parent, artifact.name)
        if digest(artifact_bytes) != receipt.artifact_sha256:
            raise EvidenceError("U10_RECEIPT_ARTIFACT_SHA_MISMATCH")
        artifact_payload: Any = parse_json(artifact_bytes)
        benchmark_payload: Any = parse_json(benchmark_bytes)
        result = validate_artifact(artifact_payload, benchmark_payload)
        if receipt.evaluated_revision != artifact_payload["evaluated_revision"]:
            raise EvidenceError("U10_RECEIPT_REVISION_MISMATCH")
        for key in (
            "fixture_sha256",
            "oracle_sha256",
            "inventory_sha256",
            "tool_contract_sha256",
            "fixed_policy_sha256",
        ):
            if getattr(receipt, key) != benchmark_payload[key]:
                raise EvidenceError("U10_RECEIPT_BENCHMARK_MISMATCH")
        if receipt.verdict_rules_sha256 != artifact_payload["verdict_rules_sha256"]:
            raise EvidenceError("U10_RECEIPT_RULES_MISMATCH")
        if receipt.effective_budget_policy != U10_VERDICT_RULES["budget"]:
            raise EvidenceError("U10_RECEIPT_BUDGET_MISMATCH")
        if receipt.agent_justification_verdict != result["agent_verdict"]:
            raise EvidenceError("U10_RECEIPT_VERDICT_MISMATCH")
        return EvaluationObservation(
            receipt=receipt,
            artifact_sha256=digest(artifact_bytes),
            receipt_sha256=digest(receipt_bytes),
            benchmark_sha256=pinned_benchmark_sha256,
            agent_verdict=result["agent_verdict"],
            verdict_reason=result["verdict_reason"],
        )
    except EvidenceError:
        raise
    except (OSError, ValidationError, TypeError, KeyError, RecursionError):
        raise EvidenceError("U10_EVALUATION_INPUT_INVALID") from None
