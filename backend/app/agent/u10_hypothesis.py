"""U10 observation -> production hypothesis v3 -> measured citation boundary.

No IO on import. The generator is mandatory: actual execution and data-export
approval belong to the future runner, not this locally testable adapter.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agent.release_artifacts import EvidenceError
from app.agent.u10_comparison import EvidenceIds, Tokens
from app.agent.u10_evidence import project_hypothesis_citations
from app.agent.u10_observations import ObservationContext

if TYPE_CHECKING:
    from app.agent.state import HypothesisOutcome, LlmUsage


@dataclass(frozen=True)
class HypothesisResult:
    outcome: HypothesisOutcome | None
    usage: LlmUsage | None
    cited_evidence_ids: EvidenceIds | None
    latency_ms: int
    error_code: str | None

    def measured_tokens(self) -> Tokens:
        if self.usage is None:
            raise EvidenceError("METRIC_PRECONDITION_INVALID")
        return Tokens(input=self.usage.input_tokens, output=self.usage.output_tokens)


def production_hypothesis_port() -> Callable[..., HypothesisOutcome]:
    """Select the existing v3 implementation without calling its provider."""
    from app.agent.hypothesis import production_port

    return production_port()


def execute_hypothesis(
    context: ObservationContext,
    generate: Callable[..., HypothesisOutcome],
    *,
    expected_model: str,
    seed: int,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> HypothesisResult:
    """One production generation (which owns correction retries and usage).

    Call after the read loop. This neither declares attempt completion nor
    derives U10's inventory-bound compared matrix or an action/delivery.
    """
    from app.agent.hypothesis import HypothesisGenerationError
    from app.agent.hypothesis_v3 import comparison_matrix
    from app.agent.prompts import PROMPT_VERSION
    from app.agent.state import HypothesisOutcome, LlmUsage

    if (
        type(expected_model) is not str
        or not expected_model.strip()
        or expected_model != expected_model.strip()
        or len(expected_model) > 64
        or type(seed) is not int
        or seed < 0
        or PROMPT_VERSION != "agent-hypothesis-v3-ko1"
    ):
        raise EvidenceError("U10_HYPOTHESIS_CONFIG_INVALID")
    inputs = context.hypothesis_inputs()
    if not inputs["fdc_evidence"]:
        return HypothesisResult(None, None, None, 0, "HYPOTHESIS_EVIDENCE_INSUFFICIENT")
    # Preserve expected code-owned matrix before an injected port can mutate its
    # detached input. Production generation already computes this same matrix.
    compared = comparison_matrix(inputs["route"], inputs["investigation"])

    def usage_value(raw: Any) -> LlmUsage | None:
        if raw is None:
            return None
        if not isinstance(raw, LlmUsage):
            raise EvidenceError("METRIC_PRECONDITION_INVALID")
        value = LlmUsage.model_validate(raw.model_dump()).model_copy(deep=True)
        if value.model != expected_model or value.prompt_version != PROMPT_VERSION:
            raise EvidenceError("LLM_CONFIG_MISMATCH")
        return value

    start = clock_ns()
    raw_outcome, raw_usage, error = None, None, None
    try:
        raw_outcome = generate(**inputs, seed=seed)
    except HypothesisGenerationError as exc:
        raw_usage, error = exc.usage_or_none, exc.code
    except Exception:
        # Unknown exceptions cannot prove usage. Never fabricate zero tokens or
        # retain provider text/URLs/credentials in a comparison result.
        error = "LLM_DEPENDENCY"
    elapsed = clock_ns() - start
    if elapsed < 0:
        raise EvidenceError("MONOTONIC_CLOCK_INVALID")
    latency = elapsed // 1_000_000
    if error is not None:
        return HypothesisResult(None, usage_value(raw_usage), None, latency, error)
    if not isinstance(raw_outcome, HypothesisOutcome):
        raise EvidenceError("U10_HYPOTHESIS_RESULT_INVALID")
    outcome = HypothesisOutcome.model_validate(raw_outcome.model_dump()).model_copy(
        deep=True
    )
    origin = outcome.hypothesis.origin_assessment
    if origin is None or origin.compared != compared:
        raise EvidenceError("U10_HYPOTHESIS_RESULT_INVALID")
    usage = usage_value(outcome.llm_usage)
    return HypothesisResult(
        outcome,
        usage,
        project_hypothesis_citations(outcome.hypothesis),
        latency,
        None,
    )
