"""One U10 fixed or ReAct attempt: shared metrics, no send or persistence.

Snapshot truth, approval, DB isolation, and the 32-attempt interleaved batch are
caller-owned. Importing this module creates no provider/connection.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_comparison import (
    FIXTURE_IDS,
    Attempt,
    EvidenceIds,
    Fixture,
    LlmConfiguration,
    ReadCall,
    Safety,
    SelectorCall,
    SkippedSlot,
    Tokens,
    _check_attempt,
    derive_compared,
)
from app.agent.u10_hypothesis import HypothesisResult, execute_hypothesis
from app.agent.u10_observations import ObservationContext
from app.agent.u10_react_execution import ReactReadResult, execute_react_policy
from app.agent.u10_read_adapter import Deadline, ReadAdapter, ReadPorts
from app.agent.u10_read_execution import DocumentContext, execute_fixed_policy


@dataclass(frozen=True)
class ReactAttemptResult:
    attempt: Attempt
    reads: ReactReadResult
    hypothesis: HypothesisResult


@dataclass(frozen=True)
class FixedAttemptResult:
    attempt: Attempt
    calls: list[ReadCall]
    skipped_slots: list[SkippedSlot]
    hypothesis: HypothesisResult


def _ids(values) -> EvidenceIds:
    values = sorted(set(values))
    return EvidenceIds(values=values, sha256=digest(canonical_json(values)))


def _prepare(fixture, attempt_no, verified_snapshot_sha256, context, llm):
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
    return fixture, llm, initial, route, model


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
    fixture, llm, initial, route, model = _prepare(
        fixture, attempt_no, verified_snapshot_sha256, context, llm
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
    attempt = _finish(
        fixture=fixture,
        attempt_no=attempt_no,
        policy="REACT_V2",
        llm=llm,
        initial=initial,
        route=route,
        calls=reads.calls,
        skips=[],
        selector=selector,
        hypothesis=hypothesis,
        read_complete=reads.stop_reason
        in {
            "LLM_STOP",
            "BUDGET_EXHAUSTED",
            "GUARD_LIMIT",
            "STEP_CAP",
        },
        observe_effects=observe_effects,
        started=started,
        clock_ns=clock_ns,
    )
    return ReactAttemptResult(attempt, reads, hypothesis)


def execute_fixed_attempt(
    *,
    fixture: Fixture,
    attempt_no: int,
    verified_snapshot_sha256: str,
    context: ObservationContext,
    llm: LlmConfiguration,
    read_ports: ReadPorts,
    deadline: Deadline,
    bound_inputs: dict[str, dict[str, Any]],
    document_context: DocumentContext,
    generate: Callable[..., Any],
    observe_effects: Callable[[], tuple[Safety, int]],
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> FixedAttemptResult:
    """Fixed path with zero selector calls and the same measurement boundary.

    Non-document inputs and document metadata must be bound to the verified
    fixture by the caller. Each read still passes ObservationContext.authorize.
    """
    fixture, llm, initial, route, model = _prepare(
        fixture, attempt_no, verified_snapshot_sha256, context, llm
    )
    document_context = DocumentContext.model_validate(document_context.model_dump())
    if document_context.model_code != model:
        raise EvidenceError("U10_DOCUMENT_MODEL_REQUIRED")
    bound_inputs = deepcopy(bound_inputs)
    context.validate_fixed_inputs(fixture.candidate_inventory, bound_inputs)
    adapter = ReadAdapter(context, read_ports, deadline)

    def invoke(tool, request):
        internal = (
            context.resolve_history_context(request)
            if tool == "get_chamber_parameter_history"
            else None
        )
        return adapter(tool, request, internal)

    started = clock_ns()
    calls, skips = execute_fixed_policy(
        fixture.candidate_inventory,
        bound_inputs,
        document_context,
        invoke,
        clock_ns=clock_ns,
    )
    hypothesis = execute_hypothesis(
        context,
        generate,
        expected_model=llm.hypothesis_model_revision,
        seed=llm.seed,
        clock_ns=clock_ns,
    )
    attempt = _finish(
        fixture=fixture,
        attempt_no=attempt_no,
        policy="FIXED_POLICY_V21",
        llm=llm,
        initial=initial,
        route=route,
        calls=calls,
        skips=skips,
        selector=[],
        hypothesis=hypothesis,
        read_complete=True,
        observe_effects=observe_effects,
        started=started,
        clock_ns=clock_ns,
    )
    return FixedAttemptResult(attempt, calls, skips, hypothesis)


def _finish(
    *,
    fixture,
    attempt_no,
    policy,
    llm,
    initial,
    route,
    calls,
    skips,
    selector,
    hypothesis,
    read_complete,
    observe_effects,
    started,
    clock_ns,
):
    from app.agent.decision import decide_action

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
        not calls
        or len(calls) == 8
        or (calls[-1].status == "SUCCESS" or calls[-1].retry == 1)
    )
    completed = ok and retry_closed and read_complete
    action = decide_action(route).action if ok else None
    safety, effects = observe_effects()
    elapsed = clock_ns() - started
    if elapsed <= 0:
        raise EvidenceError("MONOTONIC_CLOCK_INVALID")
    available = set(initial.values)
    for call in calls:
        available.update(call.evidence_ids.values)

    def tokens(calls):
        return Tokens(
            input=sum(c.tokens.input for c in calls),
            output=sum(c.tokens.output for c in calls),
        )

    position = (
        (2 if attempt_no == 1 else 3)
        if policy == "REACT_V2"
        else (1 if attempt_no == 1 else 4)
    )
    order = FIXTURE_IDS.index(fixture.fixture_id) * 4 + position
    attempt = Attempt(
        fixture_id=fixture.fixture_id,
        attempt_no=attempt_no,
        policy=policy,
        execution_order=order,
        initial_snapshot_sha256=fixture.initial_snapshot_sha256,
        llm_config_sha256=digest(canonical_json(llm)),
        completion=completed,
        action=None if action is None else action.value,
        external_effects=effects,
        safety=Safety.model_validate(safety.model_dump()),
        calls=calls,
        skipped_slots=skips,
        selector=selector,
        hypothesis=hypothesis_calls,
        initial_evidence_ids=initial,
        available_evidence_ids=_ids(available),
        cited_evidence_ids=hypothesis.cited_evidence_ids or _ids([]),
        compared=derive_compared(fixture.candidate_inventory, calls),
        read_attempts=len(calls),
        successful_reads=sum(c.status == "SUCCESS" for c in calls),
        selector_calls=len(selector),
        selector_tokens=tokens(selector),
        hypothesis_tokens=tokens(hypothesis_calls),
        tool_latency_ms=sum(c.latency_ms for c in calls),
        selector_latency_ms=sum(c.latency_ms for c in selector),
        end_to_end_latency_ms=elapsed // 1_000_000,
    ).model_copy(deep=True)
    _check_attempt(attempt, fixture, order)
    return attempt
