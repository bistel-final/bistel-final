"""Synthetic input/ledger contracts; never claim live-model quality from mocks."""

import json
from datetime import timedelta

import pytest

from app.agent import react
from app.agent.investigation_budget import DEVELOPMENT_WIDE
from app.common.enums import ToolCallStatus
from app.common.tool_contracts import (
    ChamberParameterHistoryToolInput,
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolInput,
    FdcSummaryToolInput,
    MetrologyResultToolInput,
)
from tests.support.agent_quality_development_cases import (
    CASES,
    CHAMBER,
    LOT,
    MODEL,
    NOW,
    SIBLING,
    STEP,
    SyntheticInvestigationTools,
)
from tests.unit import test_agent_react as fixture


def _history(tools, scope="CURRENT", **changes):
    request = ChamberParameterHistoryToolInput(
        chamber_id=CHAMBER if scope == "CURRENT" else SIBLING,
        parameter_id="P1",
        step_no=1,
        before=NOW + timedelta(minutes=1),
        n_lots=3,
    )
    context = {"scope": scope, "current_lot_id": LOT, "incident_step_id": STEP}
    context.update(changes)
    return tools.chamber_parameter_history("RUN-1", request, **context)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_synthetic_case_dtos_have_consistent_route_values_counts_and_limits(case):
    tools = SyntheticInvestigationTools(case)
    route = case.route()
    for wafer in route.wafer_routes:
        assert (
            tuple(sorted(wafer.steps, key=lambda step: step.track_in_at)) == wafer.steps
        )
        for step in wafer.steps:
            result = tools.fdc_summary(
                "RUN-1", FdcSummaryToolInput(lot_hist_id=step.lot_hist_id)
            )
            assert result.ok and result.wafer.lot_hist_id == step.lot_hist_id
            assert result.wafer.chamber_id == step.chamber_id
            assert result.wafer.step_id == step.step_id
            p = result.parameters[0]
            assert p.value_min == p.value_mean == p.value_max
            assert p.spec_lower < p.ctrl_lower < p.target < p.ctrl_upper < p.spec_upper
            assert 0 <= p.oos_point_cnt <= p.ooc_point_cnt <= p.point_cnt == 6
            assert (p.oos_point_cnt > 0) == (
                p.value_mean < p.spec_lower or p.value_mean > p.spec_upper
            )
    current = _history(tools)
    assert current.ok and current.scope == "CURRENT"
    assert current.current.lot_mean == sum(case.current_means) / len(case.current_means)
    assert (
        current.current.wafer_count == current.sample_count == len(case.current_means)
    )
    assert current.baseline.prior_lot_count == len(current.prior) == 3
    assert current.current.track_in_from > current.prior[0].track_in_to
    assert [row.track_in_to for row in current.prior] == sorted(
        (row.track_in_to for row in current.prior),
        reverse=True,
    )
    sibling = _history(tools, "SIBLING")
    assert sibling.ok and sibling.comparison == sibling.scope == "SIBLING"
    assert sibling.chamber_id == SIBLING and sibling.current.lot_mean == 5
    metrology = tools.metrology_result(
        "RUN-1", MetrologyResultToolInput(lot_id=LOT, step_id=STEP)
    )
    assert metrology.ok and metrology.fail_count == 0
    assert {row.wafer_id for row in metrology.results} == {
        wafer.wafer_id for wafer in route.wafer_routes
    }
    assert all(
        row.spec_lower <= row.measured_value <= row.spec_upper
        for row in metrology.results
    )


def test_eight_patterns_are_not_same_inputs_or_final_cf_population():
    assert len(CASES) == 8 and len({case.case_id for case in CASES}) == 8
    assert all(case.case_id.startswith("SYN-") for case in CASES)
    assert CASES[0].current_means == (5, 5)
    assert CASES[2].upstream_mean > 10 and CASES[2].current_means == (5,)
    assert _history(SyntheticInvestigationTools(CASES[4])).trend == "DRIFT_UP"
    assert CASES[6].current_means[0] == 5 and CASES[6].current_means[1] > 10
    assert all(case.acceptance for case in CASES)


def test_actual_request_ledger_records_timeout_error_success_once_each_in_order():
    tools = SyntheticInvestigationTools(CASES[5])
    query = DocumentSearchToolInput(query="P1 상한 이탈", model_code=MODEL)
    assert not tools.document_search("RUN-1", query).ok
    assert tools.document_search("RUN-1", query).ok
    assert not tools.fdc_summary("RUN-1", FdcSummaryToolInput(lot_hist_id="MISSING")).ok
    assert [row.status for row in tools.history("RUN-1")] == [
        ToolCallStatus.TIMEOUT,
        ToolCallStatus.SUCCESS,
        ToolCallStatus.ERROR,
    ]
    assert [row.input for row in tools.history("RUN-1")][:2] == [
        query.model_dump(mode="json")
    ] * 2
    assert tools.budget("RUN-1").used == 3
    assert tools.budget("RUN-1").by_tool == {
        "search_documents": 2,
        "get_fdc_summary": 1,
    }
    assert tools.history("UNRELATED") == () and tools.budget("UNRELATED").used == 0
    leaked = tools.history("RUN-1")
    leaked[0].input["query"] = "modified"
    assert tools.history("RUN-1")[0].input["query"] == query.query


def test_scope_failure_is_error_not_success_and_has_no_observation():
    tools = SyntheticInvestigationTools(CASES[3])
    result = _history(tools, "SIBLING", current_lot_id="UNRELATED")
    assert not result.ok and result.current is None
    assert tools.history("RUN-1")[0].status is ToolCallStatus.ERROR


def test_history_standard_deviation_matches_sample_aggregate_contract():
    one = _history(SyntheticInvestigationTools(CASES[1]))
    two = _history(SyntheticInvestigationTools(CASES[6]))
    assert one.current.lot_std is None
    assert two.current.lot_std == pytest.approx(24.5**0.5)


def test_no_upstream_metrology_sample_is_an_observed_absence_not_a_fake_pass():
    tools = SyntheticInvestigationTools(CASES[2])
    result = tools.metrology_result(
        "RUN-1", MetrologyResultToolInput(lot_id=LOT, step_id="STEP-UPSTREAM")
    )
    assert not result.ok and result.results == [] and result.fail_count is None
    assert tools.history("RUN-1")[0].status is ToolCallStatus.ERROR


def test_query_sensitive_documents_and_repeated_chunk_do_not_invent_new_ids():
    tools = SyntheticInvestigationTools(CASES[1])
    observed = []
    for query in ("P1 기준", "P1 상한", "P1 과거 추세", "P1 상류", "P1 형제"):
        observed.append(
            tools.document_search(
                "RUN-1", DocumentSearchToolInput(query=query, model_code=MODEL)
            )
        )
    assert len({result.hits[0].chunk_id for result in observed}) == 5
    history = tools.document_search(
        "RUN-1", DocumentSearchToolInput(query="P1 현재 chamber의 과거 추세")
    )
    assert history.hits[0].chunk_id == "DOC-P1-HISTORY"
    repeated = SyntheticInvestigationTools(CASES[7])
    first = repeated.document_search("RUN-1", DocumentSearchToolInput(query="P1 상한"))
    second = repeated.document_search(
        "RUN-1", DocumentSearchToolInput(query="P1 다른 점검")
    )
    assert first == second
    assert len(repeated.history("RUN-1")) == 2
    assert repeated.history("RUN-1")[0].input != repeated.history("RUN-1")[1].input


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_metadata_is_not_in_selector_inputs_and_second_wafer_is_offered(case):
    tools = SyntheticInvestigationTools(case)
    first = tools.fdc_summary(
        "RUN-1", FdcSummaryToolInput(lot_hist_id=case.current_ids[0])
    )
    equipment = tools.equipment_context(
        "RUN-1", EquipmentContextToolInput(chamber_id=CHAMBER)
    )
    candidates = react.build_initial_candidates(
        run_id="RUN-1", route=case.route(), current_lot_hist_ids=case.current_ids
    )
    candidates = react.refresh_history_candidates(
        candidates, fdc_results=(first,), equipment=equipment
    )
    context = react.build_context(
        run_id="RUN-1",
        lot_id=LOT,
        chamber_id=CHAMBER,
        representative_alarm=case.route().incident.representative_alarm,
        member_alarms=case.route().incident.member_alarms,
        route=case.route(),
        candidates=candidates,
        fdc_results=(first,),
        equipment=equipment,
        documents=(),
        remaining_tool_calls=22,
        remaining_steps=28,
        guard_rejections=0,
    )
    messages = json.dumps(
        react.build_react_select_messages(context), ensure_ascii=False
    )
    assert case.case_id not in messages and case.title not in messages
    assert all(note not in messages for note in case.acceptance)
    assert {
        c.lot_hist_id for c in context.candidates.fdc if c.relation == "CURRENT"
    } == set(case.current_ids)
    assert any(c.scope == "SIBLING" for c in context.candidates.history)
    assert any(c.relation == "UPSTREAM" for c in context.candidates.fdc) == (
        case.upstream_mean is not None
    )


def test_graph_harness_stops_before_effects_and_uses_synthetic_ports(monkeypatch):
    # The synthetic ledger is a development profile, never an unmarked
    # production run whose numeric cap alone can grant wider access.
    dependencies = fixture.harness.AgentGraphDependencies
    monkeypatch.setattr(
        fixture.harness,
        "AgentGraphDependencies",
        lambda **kwargs: dependencies(
            **kwargs, experimental_investigation_budget=DEVELOPMENT_WIDE
        ),
    )
    case = CASES[6]
    tools = SyntheticInvestigationTools(case)
    port = fixture.ScriptedReactPort(
        fixture._selection("get_fdc_summary", fdc_candidate_id="F2")
    )
    (graph, _, _, _, _), _ = fixture._level3(
        monkeypatch,
        port,
        tools=tools,
        level_route=case.route(),
        diagnostic_wafer_refs=case.diagnostic_wafer_refs,
        interrupt_after=("decide_action",),
    )
    state = fixture.harness._invoke(graph, level=3)
    assert tools.send_count == 0
    assert {row.input["lot_hist_id"] for row in tools.history("RUN-1")} == set(
        case.current_ids
    )
    assert state["fdc_evidence_set"][1].parameters[0].value_mean == 12


def test_read_budget_and_external_effects_fail_closed():
    tools = SyntheticInvestigationTools(CASES[1], budget_limit=3, read_limit=1)
    request = FdcSummaryToolInput(lot_hist_id="LH-REP")
    tools.fdc_summary("RUN-1", request)
    with pytest.raises(RuntimeError, match="SYNTHETIC_READ_BUDGET_EXHAUSTED"):
        tools.fdc_summary("RUN-1", request)
    assert len(tools.history("RUN-1")) == 1
    with pytest.raises(RuntimeError, match="SYNTHETIC_EXTERNAL_EFFECT_FORBIDDEN"):
        tools.send_action("RUN-1", None)


def test_ninth_same_tool_attempt_is_blocked_before_reservation_for_all_statuses():
    tools = SyntheticInvestigationTools(CASES[1])
    request = DocumentSearchToolInput(query="P1 점검")
    effects = []

    def invoke(index):
        effects.append(index)
        if index == 3:
            raise RuntimeError("READ_INTERRUPTED")
        if index < 2:
            return DocumentSearchToolResult(
                ok=False,
                reason="TIMEOUT: temporary" if index == 0 else "DEPENDENCY_ERROR: read",
            )
        return DocumentSearchToolResult(ok=True, hits=[])

    for index in range(8):
        if index == 3:
            with pytest.raises(RuntimeError, match="READ_INTERRUPTED"):
                tools._call(
                    "RUN-1",
                    "search_documents",
                    "documents",
                    request,
                    lambda index=index: invoke(index),
                )
        else:
            tools._call(
                "RUN-1",
                "search_documents",
                "documents",
                request,
                lambda index=index: invoke(index),
            )
    with pytest.raises(RuntimeError, match="SYNTHETIC_SAME_TOOL_BUDGET_EXHAUSTED"):
        tools._call(
            "RUN-1", "search_documents", "documents", request, lambda: invoke(8)
        )
    assert effects == list(range(8))
    assert len(tools.history("RUN-1")) == len(tools.calls) == 8
    assert tools.budget("RUN-1").pending_reservations == 1
    assert {row.status for row in tools.history("RUN-1")} == {
        ToolCallStatus.SUCCESS,
        ToolCallStatus.ERROR,
        ToolCallStatus.TIMEOUT,
    }
    assert tools.equipment_context(
        "RUN-1", EquipmentContextToolInput(chamber_id=CHAMBER)
    ).ok
