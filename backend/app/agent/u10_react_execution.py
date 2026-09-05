"""U10 ReAct policy wiring to the existing selector guard and shared read core.

Providers and snapshot/context builders are mandatory injected ports. Importing
this module never imports runtime configuration or creates a live provider.
This is not the 32-attempt/receipt issuer or the production graph executor.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_comparison import Inventory, ReadCall, SelectorCall, Tokens
from app.agent.u10_read_execution import ReadObservation, ReadRequest, ReadSession

if TYPE_CHECKING:
    from app.agent.react import ReactContext, ReactSelectionOutcome, ReactStep


@dataclass(frozen=True)
class SelectorMeasurement:
    # None is deliberately not a zero-token observation.
    usage: SelectorCall | None
    latency_ms: int


@dataclass(frozen=True)
class ReactReadResult:
    calls: list[ReadCall]
    selector: list[SelectorMeasurement]
    trace: list[ReactStep]
    stop_reason: str

    def measured_selector_calls(self) -> list[SelectorCall]:
        if any(item.usage is None for item in self.selector):
            raise EvidenceError("METRIC_PRECONDITION_INVALID")
        return [
            item.usage.model_copy(deep=True) for item in self.selector if item.usage
        ]


def execute_react_policy(
    inventory: Inventory,
    build_context: Callable[[], ReactContext],
    select: Callable[[ReactContext], ReactSelectionOutcome],
    invoke: Callable[[str, dict[str, Any], dict[str, Any] | None], ReadObservation],
    *,
    document_model_code: str | None,
    expected_selector_model: str,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> ReactReadResult:
    """Investigate only; the caller still owns snapshot truth and hypothesis v3.

    ``build_context`` must rebuild observations from actual adapter results. It
    cannot override budgets, change run identity or rebind an issued token.
    History's incident-derived internal context is passed separately to invoke.
    """
    from app.agent import react
    from app.common.enums import ToolCallStatus

    inventory = Inventory.model_validate(inventory.model_dump()).model_copy(deep=True)
    trace, measurements, history = [], [], []
    binding = None
    tokens = {}
    resolved = None
    document_selections = 0
    rejections = 0

    def adapter(tool, arguments):
        # Only the resolved guarded selection reaches the shared retry executor.
        return invoke(
            tool,
            arguments,
            None if resolved is None else deepcopy(resolved.get("internal_context")),
        )

    session = ReadSession(inventory, adapter, clock_ns=clock_ns)

    def finish(reason):
        if reason != "LLM_STOP":
            trace.append(react.system_stop_entry(seq=len(trace) + 1, reason=reason))
        return ReactReadResult(session.calls, measurements, trace, reason)

    def measure(usage, start):
        elapsed = clock_ns() - start
        if elapsed < 0:
            raise EvidenceError("MONOTONIC_CLOCK_INVALID")
        latency = elapsed // 1_000_000
        if usage is not None and (
            usage.prompt_version != react.REACT_PROMPT_VERSION
            or usage.model != expected_selector_model
        ):
            raise EvidenceError("LLM_CONFIG_MISMATCH")
        measurements.append(
            SelectorMeasurement(
                None
                if usage is None
                else SelectorCall(
                    tokens=Tokens(input=usage.input_tokens, output=usage.output_tokens),
                    latency_ms=latency,
                ),
                latency,
            )
        )

    for _ in range(react.REACT_MAX_STEPS):
        if len(session.calls) >= 8:
            return finish("BUDGET_EXHAUSTED")
        if rejections >= react.REACT_MAX_GUARD_REJECTIONS:
            return finish("GUARD_LIMIT")
        context = react.ReactContext.model_validate(
            build_context().model_dump()
        ).model_copy(deep=True)
        identity = (
            context.candidates.run_id,
            context.lot_id,
            context.chamber_id,
            context.representative_alarm.source,
            context.representative_alarm.alarm_id,
        )
        if binding is not None and identity != binding:
            raise EvidenceError("U10_CONTEXT_IDENTITY_DRIFT")
        binding = identity
        for kind in ("fdc", "history", "metrology"):
            for candidate in getattr(context.candidates, kind):
                key = (kind, candidate.candidate_id)
                sha = digest(canonical_json(candidate))
                if key in tokens and tokens[key] != sha:
                    raise EvidenceError("U10_CANDIDATE_REBOUND")
                tokens[key] = sha
        context = context.model_copy(
            update={
                "remaining_tool_calls": 8 - len(session.calls),
                "remaining_steps": react.REACT_MAX_STEPS - len(measurements),
                "guard_rejections": rejections,
                "structure_retry": False,
            }
        )
        outcome = None
        for structure_retry in (False, True):
            if len(measurements) >= react.REACT_MAX_STEPS:
                return finish("STEP_CAP")
            started = clock_ns()
            try:
                outcome = select(
                    context.model_copy(
                        deep=True,
                        update={
                            "structure_retry": structure_retry,
                            "remaining_steps": react.REACT_MAX_STEPS
                            - len(measurements),
                        },
                    )
                )
            except react.ReactSelectionError as exc:
                measure(exc.usage_or_none, started)
                if exc.code == "REACT_STRUCTURE_INVALID":
                    trace.append(
                        react.structure_rejection_entry(
                            seq=len(trace) + 1, usage=exc.usage_or_none
                        )
                    )
                    if not structure_retry:
                        continue
                return finish(
                    exc.code
                    if exc.code
                    in ("REACT_STRUCTURE_INVALID", "LLM_TIMEOUT", "LLM_DEPENDENCY")
                    else "LLM_DEPENDENCY"
                )
            measure(outcome.llm_usage, started)
            outcome = react.ReactSelectionOutcome.model_validate(
                outcome.model_dump()
            ).model_copy(deep=True)
            break
        selection, usage = outcome.selection, outcome.llm_usage
        guard = react.guard_selection(
            selection,
            context,
            equipment_fetched=context.equipment_observation is not None,
            tool_history=history,
            document_model_code=document_model_code,
        )
        if guard:
            rejections += 1
            trace.append(
                react.trace_entry(
                    seq=len(trace) + 1,
                    selection=selection,
                    usage=usage,
                    phase="REJECTED",
                    guard_code=guard,
                )
            )
            continue
        if selection.next == "stop":
            trace.append(
                react.trace_entry(
                    seq=len(trace) + 1,
                    selection=selection,
                    usage=usage,
                    phase="STOPPED",
                )
            )
            return finish("LLM_STOP")
        resolved = react.resolve_call(
            selection, context, document_model_code=document_model_code
        )
        if resolved is None:
            raise EvidenceError("U10_RESOLUTION_FAILED")
        if selection.next == "get_fdc_summary":
            candidate = next(
                c
                for c in context.candidates.fdc
                if c.candidate_id == selection.arguments.fdc_candidate_id
            )
            slot = "CURRENT_FDC" if candidate.relation == "CURRENT" else "ADJACENT_FDC"
            if (
                candidate.relation != "CURRENT"
                and candidate.relation != inventory.adjacent.relation
            ):
                raise EvidenceError("U10_INVENTORY_SCOPE_MISMATCH")
        elif selection.next == "get_chamber_parameter_history":
            slot = (
                "HISTORY"
                if resolved["internal_context"]["scope"] == "CURRENT"
                else "SIBLING"
            )
            if (
                slot == "SIBLING"
                and resolved["request"]["chamber_id"] != inventory.sibling_chamber_id
            ):
                raise EvidenceError("U10_INVENTORY_SCOPE_MISMATCH")
        elif selection.next == "get_metrology_result":
            candidate = next(
                c
                for c in context.candidates.metrology
                if c.candidate_id == selection.arguments.metrology_candidate_id
            )
            if candidate.relation != "CURRENT":
                raise EvidenceError("U10_INVENTORY_SCOPE_MISMATCH")
            slot = "METROLOGY"
        elif selection.next == "get_equipment_context":
            slot = "EQUIPMENT"
        else:
            document_selections += 1
            slot = "DOCUMENT_1" if document_selections == 1 else "DOCUMENT_2"
        trace.append(
            react.trace_entry(
                seq=len(trace) + 1,
                selection=selection,
                usage=usage,
                phase="SELECTED",
                canonical_arguments=resolved["request"],
                argument_summary=resolved["argument_summary"],
            )
        )
        before_read = len(session.calls)
        budget_stopped = False
        try:
            calls = session.execute(
                ReadRequest(slot=slot, arguments=resolved["request"])
            )
        except EvidenceError as exc:
            if str(exc) not in ("READ_BUDGET_EXHAUSTED", "TOOL_BUDGET_EXCEEDED"):
                raise
            calls = session.calls[before_read:]
            budget_stopped = True
        for call in calls:
            history.append(
                SimpleNamespace(
                    tool_name=call.tool,
                    input=resolved["request"].copy(),
                    status=ToolCallStatus(call.status),
                )
            )
            trace.append(
                react.trace_entry(
                    seq=len(trace) + 1,
                    selection=selection,
                    usage=usage,
                    phase="OBSERVED",
                    canonical_arguments=resolved["request"],
                    argument_summary=resolved["argument_summary"],
                    observation_summary=(
                        f"{call.status} "
                        f"evidence_count={len(call.evidence_ids.values)} "
                        f"retry={call.retry}"
                    ),
                )
            )
        if budget_stopped:
            return finish("BUDGET_EXHAUSTED")
    return finish(
        "GUARD_LIMIT" if rejections >= react.REACT_MAX_GUARD_REJECTIONS else "STEP_CAP"
    )
