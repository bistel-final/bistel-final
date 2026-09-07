"""Candidate-specific failed-read feedback; no live model or source dataset."""

import json
from types import SimpleNamespace

import pytest

from app.agent import react
from app.agent.read_feedback import summarize_read_history
from app.common.enums import ToolCallStatus
from tests.unit import test_agent_react as fixture


def row(tool, request, reason=None, status=ToolCallStatus.ERROR):
    return SimpleNamespace(
        tool_name=tool,
        input=request,
        status=status,
        output=None if reason is None else {"ok": False, "reason": reason},
        error_msg=reason,
    )


def payload(context):
    return json.loads(react.build_react_select_messages(context)[1]["content"])


def context_with_second_metrology(history=()):
    base = fixture._context()
    other = base.candidates.metrology[0].model_copy(
        update={
            "candidate_id": "M2",
            "step_id": "STEP-UPSTREAM",
            "relation": "UPSTREAM",
        }
    )
    upstream = base.candidates.fdc[1].model_copy(
        update={
            "candidate_id": "F3",
            "lot_hist_id": "LH-UPSTREAM",
            "step_id": "STEP-UPSTREAM",
            "relation": "UPSTREAM",
        }
    )
    return base.model_copy(
        update={
            "candidates": base.candidates.model_copy(
                update={
                    "metrology": (*base.candidates.metrology, other),
                    "fdc": (*base.candidates.fdc, upstream),
                }
            ),
            "read_feedback": summarize_read_history(history),
        }
    )


def candidate_rows(context, kind):
    table = payload(context)["candidates"][kind]
    return [
        dict(zip(table["columns"], values, strict=True)) for values in table["rows"]
    ]


@pytest.mark.parametrize("reason", ["NOT_FOUND: no sample", "POLICY_REJECTED: scope"])
def test_terminal_failure_is_target_specific_and_does_not_claim_success(reason):
    attempt = row(
        "get_metrology_result", {"lot_id": "LOT001", "step_id": "STEP-UPSTREAM"}, reason
    )
    context = context_with_second_metrology((attempt,))
    before = context.model_dump()
    candidates = candidate_rows(context, "metrology")
    assert candidates == [
        {"id": "M1", "relation": "CURRENT", "observed": False, "available": True},
        {"id": "M2", "relation": "UPSTREAM", "observed": False, "available": False},
    ]
    feedback = payload(context)["observations"]["read_feedback"]
    assert feedback["columns"] == ["tool", "target", "attempts", "outcome", "retryable"]
    assert feedback["rows"] == [
        ["get_metrology_result", "M2", 1, reason.split(":")[0], False]
    ]
    assert context.model_dump() == before
    assert "get_metrology_result" in payload(context)["budget"]["available_tools"]
    assert (
        react.guard_selection(
            fixture._selection("get_metrology_result", metrology_candidate_id="M2"),
            context,
            equipment_fetched=False,
            tool_history=(attempt,),
        )
        == "REACT_GUARD_TARGET_REPEATED"
    )
    assert (
        react.guard_selection(
            fixture._selection("get_metrology_result", metrology_candidate_id="M1"),
            context,
            equipment_fetched=False,
            tool_history=(attempt,),
        )
        is None
    )


@pytest.mark.parametrize(
    "reason,status",
    [
        ("TIMEOUT: temporary dependency", ToolCallStatus.TIMEOUT),
        ("DEPENDENCY_ERROR: temporary read", ToolCallStatus.ERROR),
        (None, ToolCallStatus.ERROR),
    ],
)
def test_transient_or_unknown_failure_can_recover_without_forced_next_tool(
    reason, status
):
    attempt = row(
        "get_metrology_result",
        {"lot_id": "LOT001", "step_id": "STEP-UPSTREAM"},
        reason,
        status,
    )
    context = context_with_second_metrology((attempt,))
    assert candidate_rows(context, "metrology")[1]["available"]
    assert payload(context)["observations"]["read_feedback"]["rows"][0][-1] is True
    assert (
        react.guard_selection(
            fixture._selection("get_metrology_result", metrology_candidate_id="M2"),
            context,
            equipment_fetched=False,
            tool_history=(attempt,),
        )
        is None
    )
    assert (
        react.guard_selection(
            fixture._selection("stop"), context, equipment_fetched=False
        )
        is None
    )


def test_execution_guard_ignores_forged_or_omitted_model_facing_feedback():
    failed = row(
        "get_metrology_result",
        {"lot_id": "LOT001", "step_id": "STEP-UPSTREAM"},
        "NOT_FOUND: no sample",
    )
    transient = row(
        "get_metrology_result", failed.input, "TIMEOUT: retry", ToolCallStatus.TIMEOUT
    )
    selection = fixture._selection("get_metrology_result", metrology_candidate_id="M2")
    for context in (
        context_with_second_metrology(),
        context_with_second_metrology((transient,)),
    ):
        assert (
            react.guard_selection(
                selection, context, equipment_fetched=False, tool_history=(failed,)
            )
            == "REACT_GUARD_TARGET_REPEATED"
        )
    # Conversely, a claimed terminal failure cannot change the authoritative log.
    assert (
        react.guard_selection(
            selection,
            context_with_second_metrology((failed,)),
            equipment_fetched=False,
            tool_history=(transient,),
        )
        is None
    )


def test_all_failed_candidates_remove_only_their_tool_from_available_choices():
    history = tuple(
        row(
            "get_metrology_result",
            {"lot_id": "LOT001", "step_id": step},
            "NOT_FOUND: no sample",
        )
        for step in ("CT-PHOTO", "STEP-UPSTREAM")
    )
    context = context_with_second_metrology(history)
    choices = payload(context)["budget"]["available_tools"]
    assert "get_metrology_result" not in choices
    assert {"get_fdc_summary", "search_documents", "stop"} <= set(choices)
    assert len(candidate_rows(context, "metrology")) == 2


def test_feedback_binds_to_canonical_history_defaults_and_preserves_other_target():
    context = fixture._context()
    selection = fixture._selection(
        "get_chamber_parameter_history", history_candidate_id="H1"
    )
    request = react.resolve_call(selection, context)["request"]
    attempt = row(selection.next, request, "POLICY_REJECTED: scope")
    context = context.model_copy(
        update={"read_feedback": summarize_read_history((attempt,))}
    )
    assert candidate_rows(context, "history")[0]["available"] is False
    assert payload(context)["observations"]["read_feedback"]["rows"][0][1] == "H1"


def test_failed_document_query_is_guarded_exactly_without_exposing_raw_input():
    request = {"query": "P1 missing document marker", "model_code": "MODEL-P1"}
    attempt = row(
        "search_documents", request, "NOT_FOUND: raw private path /tmp/do-not-copy"
    )
    context = fixture._context(read_feedback=summarize_read_history((attempt,)))
    raw = react.build_react_select_messages(context)[1]["content"]
    assert "P1 missing document marker" not in raw
    assert "do-not-copy" not in raw and "request_digest" not in raw
    assert payload(context)["observations"]["read_feedback"]["rows"] == [
        ["search_documents", None, 1, "NOT_FOUND", False]
    ]
    selection = fixture._selection("search_documents", query=request["query"])
    assert (
        react.guard_selection(
            selection,
            context,
            equipment_fetched=False,
            tool_history=(attempt,),
            document_model_code="MODEL-P1",
        )
        == "REACT_GUARD_TARGET_REPEATED"
    )
    assert (
        react.guard_selection(
            selection,
            context,
            equipment_fetched=False,
            tool_history=(attempt,),
            document_model_code="MODEL-P2",
        )
        is None
    )
    assert (
        react.guard_selection(
            fixture._selection("search_documents", query="P1 another question"),
            context,
            equipment_fetched=False,
            tool_history=(attempt,),
            document_model_code="MODEL-P1",
        )
        is None
    )


def test_empty_success_is_not_a_terminal_failure_for_a_new_document_question():
    success = row(
        "search_documents",
        {"query": "P1 first question", "model_code": None},
        status=ToolCallStatus.SUCCESS,
    )
    success.output = {"ok": True, "reason": "", "hits": []}
    context = fixture._context(read_feedback=summarize_read_history((success,)))
    assert (
        react.guard_selection(
            fixture._selection("search_documents", query="P1 another question"),
            context,
            equipment_fetched=False,
            tool_history=(success,),
        )
        is None
    )


def test_raw_failure_reason_and_business_identifiers_do_not_leak_through_feedback():
    attempt = row(
        "get_metrology_result",
        {"lot_id": "LOT001", "step_id": "STEP-UPSTREAM"},
        "NOT_FOUND: password=do-not-copy /tmp/source",
    )
    context = context_with_second_metrology((attempt,))
    raw = react.build_react_select_messages(context)[1]["content"]
    assert "password" not in raw and "do-not-copy" not in raw
    assert "STEP-UPSTREAM" not in raw and "LOT001" not in raw
    assert "NOT_FOUND" in raw and "M2" in raw


def test_build_context_preserves_typed_feedback_without_mutating_it():
    attempt = row("get_fdc_summary", {"lot_hist_id": "LH-2"}, "NOT_FOUND: no sample")
    feedback = summarize_read_history((attempt,))
    context = react.build_context(
        run_id="RUN-1",
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        representative_alarm=fixture.ALARM,
        member_alarms=(fixture.ALARM,),
        route=fixture._level3_route(),
        candidates=fixture._candidates(),
        fdc_results=(),
        equipment=None,
        documents=(),
        remaining_tool_calls=8,
        remaining_steps=10,
        guard_rejections=0,
        read_feedback=feedback,
    )
    assert context.read_feedback == feedback
    assert candidate_rows(context, "fdc")[1]["available"] is False
    assert "LH-2" not in react.build_react_select_messages(context)[1]["content"]
