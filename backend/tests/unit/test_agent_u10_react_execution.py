"""U10 ReAct wiring uses real guards and fake selector/read ports only."""

import subprocess
import sys
from copy import deepcopy

import pytest

from app.agent import react
from app.agent.release_artifacts import EvidenceError
from app.agent.u10_react_execution import execute_react_policy
from tests.unit.test_agent_react import _context, _selection, _usage
from tests.unit.test_agent_u10_read_execution import Clock, inventory, success


def outcome(tool, **args):
    return react.ReactSelectionOutcome(
        selection=_selection(tool, **args), llm_usage=_usage()
    )


def run(select, invoke=lambda *_: success(), build_context=_context, **kwargs):
    return execute_react_policy(
        inventory(),
        build_context,
        select,
        invoke,
        document_model_code="PH-9000",
        expected_selector_model="fixture-model",
        clock_ns=Clock(),
        **kwargs,
    )


def test_stop_calls_no_read_and_preserves_selector_measurement():
    observed = []
    result = run(lambda _: outcome("stop"), lambda *args: observed.append(args))
    assert result.stop_reason == "LLM_STOP" and result.calls == [] and observed == []
    assert result.measured_selector_calls()[0].tokens.total() == 10
    assert result.selector[0].latency_ms == 2
    assert [s.phase for s in result.trace] == ["STOPPED"]


def test_real_guard_blocks_unknown_candidate_twice_before_any_read():
    observed = []
    result = run(
        lambda _: outcome("get_fdc_summary", fdc_candidate_id="F99"),
        lambda *args: observed.append(args),
    )
    assert result.stop_reason == "GUARD_LIMIT" and observed == []
    assert len(result.selector) == 2
    assert [s.guard_code for s in result.trace[:-1]] == [
        "REACT_GUARD_CANDIDATE_UNKNOWN"
    ] * 2


def test_guard_rejections_are_charged_and_budget_fields_are_code_owned():
    contexts = []
    choices = iter([outcome("search_documents", query="{bad}"), outcome("stop")])

    def select(context):
        contexts.append(context)
        return next(choices)

    result = run(
        select,
        build_context=lambda: _context(
            remaining_tool_calls=999, remaining_steps=999, guard_rejections=999
        ),
    )
    assert result.stop_reason == "LLM_STOP"
    assert [
        (c.remaining_tool_calls, c.remaining_steps, c.guard_rejections)
        for c in contexts
    ] == [(8, 10, 0), (8, 9, 1)]


def test_context_is_refreshed_from_adapter_observations_not_selector_mutation():
    observations = []
    count = 0

    def select(context):
        nonlocal count
        count += 1
        if count == 1:
            context.lot_id = "selector-mutated"
            return outcome("search_documents", query="PH_FOCUS upper")
        assert context.lot_id == "LOT001"
        assert context.document_observations == ("document observed",)
        assert context.remaining_tool_calls == 7
        return outcome("stop")

    def invoke(tool, arguments, internal):
        assert tool == "search_documents" and internal is None
        assert arguments == {"query": "PH_FOCUS upper", "model_code": "PH-9000"}
        observations.append("document observed")
        return success("DOC-1")

    result = run(
        select, invoke, lambda: _context(document_observations=tuple(observations))
    )
    assert len(result.calls) == 1 and result.calls[0].slot == "DOCUMENT_1"
    assert [s.phase for s in result.trace] == ["SELECTED", "OBSERVED", "STOPPED"]


def test_duplicate_successful_query_is_blocked_using_real_call_history():
    observed = []
    result = run(
        lambda _: outcome("search_documents", query="same query"),
        lambda *args: observed.append(args) or success(),
    )
    assert result.stop_reason == "GUARD_LIMIT" and len(observed) == 1
    assert [s.guard_code for s in result.trace if s.phase == "REJECTED"] == [
        "REACT_GUARD_QUERY_REPEATED"
    ] * 2


def test_retry_does_not_reselect_and_history_internal_context_is_isolated():
    choices = iter(
        [
            outcome("get_chamber_parameter_history", history_candidate_id="H1"),
            outcome("stop"),
        ]
    )
    observed = []

    def invoke(tool, args, internal):
        observed.append((tool, deepcopy(args), deepcopy(internal)))
        internal["scope"] = "tampered"
        if len(observed) == 1:
            raise TimeoutError("do-not-publish")
        return success("HISTORY")

    result = run(lambda _: next(choices), invoke)
    assert len(result.selector) == 2 and len(result.calls) == 2
    assert [c.retry for c in result.calls] == [0, 1]
    assert observed[0] == observed[1]
    assert observed[0][2] == {
        "current_lot_id": "LOT001",
        "incident_step_id": "CT-PHOTO",
        "scope": "CURRENT",
    }
    assert observed[0][1]["n_lots"] == 3


def test_structure_retry_usage_and_remaining_steps_are_counted():
    contexts = []

    def select(context):
        contexts.append(context)
        if len(contexts) == 1:
            raise react.ReactSelectionError("REACT_STRUCTURE_INVALID", usage=_usage())
        return outcome("stop")

    result = run(select)
    assert [(c.structure_retry, c.remaining_steps) for c in contexts] == [
        (False, 10),
        (True, 9),
    ]
    assert sum(c.tokens.total() for c in result.measured_selector_calls()) == 20


def test_selector_step_cap_includes_structure_retries():
    counts = 0

    def select(context):
        nonlocal counts
        counts += 1
        if counts % 2:
            raise react.ReactSelectionError("REACT_STRUCTURE_INVALID", usage=_usage())
        return (
            outcome("get_equipment_context")
            if counts == 10
            else outcome("search_documents", query=f"query {counts}")
        )

    result = run(select)
    assert counts == len(result.selector) == 10
    assert len(result.calls) == 5 and result.stop_reason == "STEP_CAP"


@pytest.mark.parametrize(
    "code", ["LLM_TIMEOUT", "LLM_DEPENDENCY", "REACT_STRUCTURE_INVALID"]
)
def test_missing_usage_is_not_reported_as_zero_tokens(code):
    def select(_):
        raise react.ReactSelectionError(code)

    result = run(select)
    assert result.stop_reason == code and result.calls == []
    assert all(item.usage is None for item in result.selector)
    with pytest.raises(EvidenceError, match="^METRIC_PRECONDITION_INVALID$"):
        result.measured_selector_calls()


def test_wrong_model_rejected_before_read():
    selected = outcome("get_equipment_context")
    selected.llm_usage.model = "changed"
    observed = []
    with pytest.raises(EvidenceError, match="^LLM_CONFIG_MISMATCH$"):
        run(lambda _: selected, lambda *args: observed.append(args))
    assert observed == []


@pytest.mark.parametrize(
    "change,code",
    [
        ("identity", "U10_CONTEXT_IDENTITY_DRIFT"),
        ("candidate", "U10_CANDIDATE_REBOUND"),
    ],
)
def test_context_rebinding_fails_before_second_selector(change, code):
    count = 0
    selects = []

    def build():
        nonlocal count
        count += 1
        ctx = _context()
        if count == 2:
            if change == "identity":
                ctx.lot_id = "different"
            else:
                ctx.candidates.fdc[0].lot_hist_id = "different"
        return ctx

    with pytest.raises(EvidenceError, match=f"^{code}$"):
        run(
            lambda ctx: selects.append(ctx)
            or outcome("search_documents", query="test"),
            build_context=build,
        )
    assert len(selects) == 1


def test_inventory_relation_mismatch_never_calls_adapter():
    observed = []
    with pytest.raises(EvidenceError, match="^U10_INVENTORY_SCOPE_MISMATCH$"):
        run(
            lambda _: outcome("get_fdc_summary", fdc_candidate_id="F2"),
            lambda *args: observed.append(args),
        )
    assert observed == []


def test_read_cap_exhaustion_retains_failed_eighth_call_and_observed_trace():
    selections = iter(
        [
            outcome("search_documents", query="first"),
            outcome("search_documents", query="second"),
            outcome("get_equipment_context"),
            outcome("get_chamber_parameter_history", history_candidate_id="H1"),
        ]
    )
    calls = []

    def invoke(*args):
        calls.append(args)
        raise TimeoutError()

    result = run(lambda _: next(selections), invoke)
    assert len(calls) == len(result.calls) == 8
    assert len(result.selector) == 4 and result.stop_reason == "BUDGET_EXHAUSTED"
    assert len([s for s in result.trace if s.phase == "OBSERVED"]) == 8


def test_import_does_not_load_config_or_provider():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from app.agent import u10_react_execution; "
            "assert not {'app.common.config', 'app.common.llm', "
            "'app.agent.react'} & set(sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_partial_selection_cap_keeps_last_failed_observation():
    selected, invoked = [], []

    def select(_):
        selected.append(True)
        return outcome("search_documents", query=f"query {len(selected)}")

    def invoke(*args):
        invoked.append(args)
        if len(invoked) == 4:
            raise TimeoutError()
        return success()

    result = run(select, invoke)
    assert len(selected) == len(invoked) == len(result.calls) == 4
    assert result.stop_reason == "BUDGET_EXHAUSTED"
    assert result.calls[-1].status == "TIMEOUT" and result.calls[-1].retry == 0
    assert result.trace[-2].phase == "OBSERVED"
    assert "TIMEOUT" in result.trace[-2].observation_summary


def test_structure_retry_stops_after_second_invalid_response():
    count = 0

    def select(_):
        nonlocal count
        count += 1
        raise react.ReactSelectionError("REACT_STRUCTURE_INVALID", usage=_usage())

    result = run(select)
    assert count == len(result.selector) == 2 and result.calls == []
    assert result.stop_reason == "REACT_STRUCTURE_INVALID"


def test_selector_rejects_wrong_prompt_pin_before_read():
    selected = outcome("get_equipment_context")
    selected.llm_usage.prompt_version = "agent-react-v1-ko1"
    observed = []
    with pytest.raises(EvidenceError, match="^LLM_CONFIG_MISMATCH$"):
        run(lambda _: selected, lambda *args: observed.append(args))
    assert observed == []
