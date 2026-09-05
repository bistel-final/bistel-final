"""One U10 ReAct attempt: actual read/hypothesis seams, no send or persistence.

Snapshot truth, approval, DB isolation, and the 32-attempt interleaved batch are
caller-owned. Importing this module creates no provider/connection.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_comparison import (
    FIXTURE_IDS,
    Attempt,
    EvidenceIds,
    Fixture,
    LlmConfiguration,
    Safety,
    SelectorCall,
    Tokens,
    _check_attempt,
    derive_compared,
)
from app.agent.u10_hypothesis import HypothesisResult, execute_hypothesis
from app.agent.u10_observations import ObservationContext
from app.agent.u10_react_execution import ReactReadResult, execute_react_policy
from app.agent.u10_read_adapter import Deadline, ReadAdapter, ReadPorts


@dataclass(frozen=True)
class ReactAttemptResult:
    attempt: Attempt
    reads: ReactReadResult
    hypothesis: HypothesisResult


def _ids(values) -> EvidenceIds:
    values = sorted(set(values))
    return EvidenceIds(values=values, sha256=digest(canonical_json(values)))


def execute_react_attempt(
    *,
    fixture: Fixture,
    attempt_no: int,
    verified_snapshot_sha256: str,
    context: ObservationContext,
    llm: LlmConfiguration,
    read_ports: ReadPorts,
    deadline: Deadline,
    select: Callable[..., Any],
    generate: Callable[..., Any],
    observe_effects: Callable[[], tuple[Safety, int]],
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> ReactAttemptResult:
    """Execute only the ReAct side; injected ports must belong to this snapshot.

    Effects are observed AFTER work, never assumed zero. The returned record is
    in memory, not a validated release artifact or permission to enable Level 3.
    """
    from app.agent.decision import decide_action

    fixture = Fixture.model_validate(fixture.model_dump()).model_copy(deep=True)
    llm = LlmConfiguration.model_validate(llm.model_dump()).model_copy(deep=True)
    if type(attempt_no) is not int or attempt_no not in (1, 2):
        raise EvidenceError("U10_ATTEMPT_ID_INVALID")
    if fixture.fixture_id not in FIXTURE_IDS:
        raise EvidenceError("U10_ATTEMPT_ID_INVALID")
    if verified_snapshot_sha256 != fixture.initial_snapshot_sha256:
        raise EvidenceError("SNAPSHOT_MISMATCH")
    initial = context.initial_evidence_ids()
    if initial != fixture.initial_evidence_ids:
        raise EvidenceError("INITIAL_EVIDENCE_MISMATCH")
    inputs = context.hypothesis_inputs()
    if inputs["investigation"].successful_calls:
        raise EvidenceError("U10_ATTEMPT_CONTEXT_NOT_FRESH")
    route = inputs["route"]
    model = next(
        g.model_code
        for g in route.graph_evidence
        if g.chamber_id == route.incident.chamber_id
    )
    started = clock_ns()
    reads = execute_react_policy(
        fixture.candidate_inventory,
        context.build_context,
        lambda ctx: select(ctx, seed=llm.seed),
        ReadAdapter(context, read_ports, deadline),
        document_model_code=model,
        expected_selector_model=llm.selector_model_revision,
        clock_ns=clock_ns,
    )
    selector = reads.measured_selector_calls()
    hypothesis = execute_hypothesis(
        context,
        generate,
        expected_model=llm.hypothesis_model_revision,
        seed=llm.seed,
        clock_ns=clock_ns,
    )
    hypothesis_calls = []
    if hypothesis.error_code != "HYPOTHESIS_EVIDENCE_INSUFFICIENT":
        hypothesis_calls = [
            SelectorCall(
                tokens=hypothesis.measured_tokens(),
                latency_ms=hypothesis.latency_ms,
            )
        ]
    ok = hypothesis.error_code is None and hypothesis.outcome is not None
    # A fourth same-tool failure may stop before its mandatory retry (<8 total).
    # Preserve that incomplete attempt; do not turn a valid hypothesis into a
    # claim that the common read/retry policy completed.
    retry_closed = (
        not reads.calls
        or len(reads.calls) == 8
        or (reads.calls[-1].status == "SUCCESS" or reads.calls[-1].retry == 1)
    )
    completed = (
        ok
        and retry_closed
        and reads.stop_reason
        in {
            "LLM_STOP",
            "BUDGET_EXHAUSTED",
            "GUARD_LIMIT",
            "STEP_CAP",
        }
    )
    action = decide_action(route).action if ok else None
    safety, effects = observe_effects()
    elapsed = clock_ns() - started
    if elapsed <= 0:
        raise EvidenceError("MONOTONIC_CLOCK_INVALID")
    available = set(initial.values)
    for call in reads.calls:
        available.update(call.evidence_ids.values)

    def tokens(calls):
        return Tokens(
            input=sum(c.tokens.input for c in calls),
            output=sum(c.tokens.output for c in calls),
        )

    order = FIXTURE_IDS.index(fixture.fixture_id) * 4 + (2 if attempt_no == 1 else 3)
    attempt = Attempt(
        fixture_id=fixture.fixture_id,
        attempt_no=attempt_no,
        policy="REACT_V2",
        execution_order=order,
        initial_snapshot_sha256=verified_snapshot_sha256,
        llm_config_sha256=digest(canonical_json(llm)),
        completion=completed,
        action=None if action is None else action.value,
        external_effects=effects,
        safety=Safety.model_validate(safety.model_dump()),
        calls=reads.calls,
        skipped_slots=[],
        selector=selector,
        hypothesis=hypothesis_calls,
        initial_evidence_ids=initial,
        available_evidence_ids=_ids(available),
        cited_evidence_ids=hypothesis.cited_evidence_ids or _ids([]),
        compared=derive_compared(fixture.candidate_inventory, reads.calls),
        read_attempts=len(reads.calls),
        successful_reads=sum(c.status == "SUCCESS" for c in reads.calls),
        selector_calls=len(selector),
        selector_tokens=tokens(selector),
        hypothesis_tokens=tokens(hypothesis_calls),
        tool_latency_ms=sum(c.latency_ms for c in reads.calls),
        selector_latency_ms=sum(c.latency_ms for c in selector),
        end_to_end_latency_ms=elapsed // 1_000_000,
    ).model_copy(deep=True)
    _check_attempt(attempt, fixture, order)
    return ReactAttemptResult(attempt, reads, hypothesis)
