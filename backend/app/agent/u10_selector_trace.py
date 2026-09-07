"""U12 private diagnostic provenance. No runtime config, provider or raw text."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.agent.release_artifacts import EvidenceError, EvidenceModel

GUARD_CODES = frozenset(
    "REACT_GUARD_" + name
    for name in (
        "ARGUMENT_MATRIX",
        "BUDGET_EXHAUSTED",
        "CANDIDATE_UNKNOWN",
        "TARGET_REPEATED",
        "PARAMETER_NOT_OBSERVED",
        "SIBLING_UNRESOLVED",
        "CHAMBER_NOT_ALLOWED",
        "QUERY_EMPTY",
        "QUERY_INVALID",
        "QUERY_REPEATED",
        "EQUIPMENT_REPEATED",
        "TOOL_NOT_ALLOWED",
    )
)
SYSTEM_STOPS = frozenset(
    {
        "BUDGET_EXHAUSTED",
        "GUARD_LIMIT",
        "STEP_CAP",
        "REACT_STRUCTURE_INVALID",
        "LLM_TIMEOUT",
        "LLM_DEPENDENCY",
    }
)


class SelectorStep(EvidenceModel):
    seq: int = Field(ge=1, le=30, strict=True)
    phase: Literal["SELECTED", "OBSERVED", "REJECTED", "STOPPED"]
    tool: (
        Literal[
            "get_fdc_summary",
            "get_equipment_context",
            "get_chamber_parameter_history",
            "get_metrology_result",
            "search_documents",
            "stop",
        ]
        | None
    )
    slot: (
        Literal[
            "CURRENT_FDC",
            "ADJACENT_FDC",
            "EQUIPMENT",
            "HISTORY",
            "SIBLING",
            "METROLOGY",
            "DOCUMENT_1",
            "DOCUMENT_2",
        ]
        | None
    )
    retry: int | None = Field(ge=0, le=1, strict=True)
    guard_code: str | None = Field(max_length=64)
    stop_reason: (
        Literal[
            "LLM_STOP",
            "BUDGET_EXHAUSTED",
            "GUARD_LIMIT",
            "STEP_CAP",
            "REACT_STRUCTURE_INVALID",
            "LLM_TIMEOUT",
            "LLM_DEPENDENCY",
        ]
        | None
    )
    llm_call: bool = Field(strict=True)


def _require(value):
    if not value:
        raise EvidenceError("U10_DIAGNOSTIC_INCONSISTENT")


def project_selector_trace(reads) -> list[SelectorStep]:
    """Join actual SELECTED/OBSERVED events with actual ordered ReadCalls."""
    first_calls = [c for c in reads.calls if c.retry == 0]
    selected = observed = 0
    events = []
    for raw in reads.trace:
        slot = retry = None
        if raw.phase == "SELECTED":
            _require(selected < len(first_calls))
            call = first_calls[selected]
            _require(call.tool == raw.tool and call.selection == selected + 1)
            slot = call.slot
            selected += 1
        elif raw.phase == "OBSERVED":
            _require(observed < len(reads.calls))
            call = reads.calls[observed]
            _require(call.tool == raw.tool)
            slot, retry = call.slot, call.retry
            observed += 1
        events.append(
            SelectorStep(
                seq=raw.seq,
                phase=raw.phase,
                tool=raw.tool,
                slot=slot,
                retry=retry,
                guard_code=raw.guard_code,
                stop_reason=raw.stop_reason,
                llm_call=(
                    raw.phase in {"SELECTED", "REJECTED"}
                    or raw.phase == "STOPPED"
                    and raw.stop_reason in {"LLM_STOP", "LLM_TIMEOUT", "LLM_DEPENDENCY"}
                ),
            )
        )
    _require(selected == len(first_calls) and observed == len(reads.calls))
    return events


def check_selector_trace(attempt) -> None:
    """Semantic diagnostics only. Never changes comparison verdict rules."""
    events = attempt.selector_trace
    if attempt.policy == "FIXED_POLICY_V21":
        _require(events == [] and attempt.selector_calls == 0)
        return
    _require(isinstance(events, list) and 0 < len(events) <= 30)
    _require([s.seq for s in events] == list(range(1, len(events) + 1)))
    _require(sum(s.llm_call for s in events) == attempt.selector_calls <= 10)
    first_calls = [c for c in attempt.calls if c.retry == 0]
    selected = observed = rejected = schema_streak = 0
    active_selection = None
    for index, event in enumerate(events):
        terminal = event.phase == "STOPPED"
        _require(terminal == (index == len(events) - 1))
        _require(terminal or event.stop_reason is None)
        if event.phase == "SELECTED":
            _require(schema_streak < 2 and selected < len(first_calls))
            _require(observed == 0 or attempt.calls[observed - 1].selection == selected)
            _require(
                observed == len(attempt.calls) or attempt.calls[observed].retry == 0
            )
            call = first_calls[selected]
            _require(call.selection == selected + 1)
            _require(
                event.tool == call.tool
                and event.slot == call.slot
                and event.retry is None
            )
            _require(event.llm_call and event.guard_code is None)
            active_selection = call.selection
            selected += 1
            schema_streak = 0
        elif event.phase == "OBSERVED":
            _require(observed < len(attempt.calls))
            call = attempt.calls[observed]
            _require(call.selection == active_selection)
            _require(
                (event.tool, event.slot, event.retry)
                == (call.tool, call.slot, call.retry)
            )
            _require(not event.llm_call and event.guard_code is None)
            observed += 1
        elif event.phase == "REJECTED":
            _require(event.slot is None and event.retry is None and event.llm_call)
            _require(
                observed == len(attempt.calls)
                or attempt.calls[observed].selection > selected
            )
            if event.guard_code == "REACT_SCHEMA_INVALID":
                _require(event.tool is None)
                schema_streak += 1
                _require(schema_streak <= 2)
                if schema_streak == 2:
                    _require(
                        index + 1 == len(events) - 1
                        and events[-1].stop_reason == "REACT_STRUCTURE_INVALID"
                    )
            else:
                _require(
                    event.guard_code in GUARD_CODES
                    and event.tool is not None
                    and schema_streak < 2
                )
                rejected += 1
                schema_streak = 0
                _require(rejected <= 2)
                if rejected == 2:
                    _require(index + 1 == len(events) - 1)
        else:
            _require(
                event.guard_code is None and event.slot is None and event.retry is None
            )
            _require(event.stop_reason == attempt.read_stop_reason)
            if event.stop_reason == "LLM_STOP":
                _require(event.tool == "stop" and event.llm_call and schema_streak < 2)
            else:
                _require(event.stop_reason in SYSTEM_STOPS and event.tool is None)
                _require(
                    event.llm_call
                    == (event.stop_reason in {"LLM_TIMEOUT", "LLM_DEPENDENCY"})
                )
                if event.stop_reason == "REACT_STRUCTURE_INVALID":
                    _require(schema_streak == 2)
                if event.stop_reason == "STEP_CAP":
                    _require(attempt.selector_calls == 10)
                if event.stop_reason == "BUDGET_EXHAUSTED":
                    # The executor stops after the eighth read, or while the
                    # fourth same-tool failed read awaits its mandatory retry.
                    # Neither path can follow an uncompleted selector retry.
                    _require(index > 0 and events[index - 1].phase == "OBSERVED")
                    last = attempt.calls[-1] if attempt.calls else None
                    _require(
                        len(attempt.calls) == 8
                        or (
                            last is not None
                            and last.retry == 0
                            and last.status in {"ERROR", "TIMEOUT"}
                            and sum(c.tool == last.tool for c in attempt.calls) == 4
                        )
                    )
    _require(selected == len(first_calls) and observed == len(attempt.calls))
    _require((attempt.read_stop_reason == "GUARD_LIMIT") == (rejected == 2))
