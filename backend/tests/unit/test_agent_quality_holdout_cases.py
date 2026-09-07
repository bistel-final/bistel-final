"""Validate authored holdout inputs, not model quality or production coverage."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.agent import react
from app.agent.hypothesis_v3 import _excursion
from app.agent.prompts import build_hypothesis_messages
from app.common.enums import ToolCallStatus
from app.common.tool_contracts import (
    ChamberParameterHistoryToolInput,
    DocumentSearchToolInput,
    EquipmentContextToolInput,
    FdcSummaryToolInput,
    MetrologyResultToolInput,
)
from tests.support.agent_quality_development_cases import (
    CASES as BASE_CASES,
)
from tests.support.agent_quality_development_cases import (
    CHAMBER,
    LOT,
    MODEL,
    NOW,
    SIBLING,
    STEP,
    SyntheticInvestigationTools,
)
from tests.support.agent_quality_development_cases import (
    DOCUMENTS as BASE_DOCUMENTS,
)
from tests.support.agent_quality_holdout_cases import (
    CASES,
    DOCUMENTS,
    HOLDOUT_CASES,
    HoldoutCase,
    SyntheticHoldoutInvestigationTools,
    tools_factory_for_case,
)


def _history(tools, scope="CURRENT"):
    return tools.chamber_parameter_history(
        "RUN-1",
        ChamberParameterHistoryToolInput(
            chamber_id=CHAMBER if scope == "CURRENT" else SIBLING,
            parameter_id="P1",
            step_no=1,
            before=NOW + timedelta(minutes=1),
            n_lots=3,
        ),
        scope=scope,
        current_lot_id=LOT,
        incident_step_id=STEP,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_holdout_actual_dtos_keep_scope_limits_counts_and_direction(case):
    tools = tools_factory_for_case(case)(case)
    route = case.route()
    for wafer in route.wafer_routes:
        assert tuple(sorted(wafer.steps, key=lambda step: step.track_in_at)) == (
            wafer.steps
        )
        for step in wafer.steps:
            result = tools.fdc_summary(
                "RUN-1", FdcSummaryToolInput(lot_hist_id=step.lot_hist_id)
            )
            assert result.ok
            assert result.wafer.lot_hist_id == step.lot_hist_id
            assert result.wafer.chamber_id == step.chamber_id
            assert result.wafer.step_id == step.step_id
            value = (
                case.upstream_mean
                if step.step_id == "STEP-UPSTREAM"
                else case.current_means[step.wafer_no - 1]
            )
            parameter = result.parameters[0]
            assert parameter.value_min == parameter.value_mean == value
            assert parameter.value_max == value and parameter.point_cnt == 6
            assert (
                parameter.spec_lower,
                parameter.ctrl_lower,
                parameter.target,
                parameter.ctrl_upper,
                parameter.spec_upper,
            ) == (0, 1, 5, 9, 10)
            expected_oos = 6 if value < 0 else 0
            assert parameter.ooc_point_cnt == parameter.oos_point_cnt == expected_oos
            assert _excursion(parameter) == (("BELOW", 0.75) if value == -2 else None)
    current = _history(tools)
    assert current.ok and current.scope == "CURRENT"
    assert current.current.lot_mean == sum(case.current_means) / len(case.current_means)
    assert current.current.wafer_count == len(case.current_means)
    assert current.current.oos_wafers == sum(value < 0 for value in case.current_means)
    assert [row.lot_mean for row in current.prior] == list(case.prior_means)
    assert current.current.track_in_from > current.prior[0].track_in_to
    assert [row.track_in_to for row in current.prior] == sorted(
        (row.track_in_to for row in current.prior), reverse=True
    )
    sibling = _history(tools, "SIBLING")
    assert sibling.ok and sibling.scope == "SIBLING"
    assert sibling.current.lot_mean == 5 and sibling.current.oos_wafers == 0
    assert sibling.baseline.prior_lot_count == 0 and sibling.trend == "INSUFFICIENT"
    metrology = tools.metrology_result(
        "RUN-1", MetrologyResultToolInput(lot_id=LOT, step_id=STEP)
    )
    assert metrology.ok and len(metrology.results) == len(case.current_means)
    assert all(row.alarm_result == "PASS" for row in metrology.results)
    assert metrology.fail_count == 0


def test_normal_and_downward_trend_are_derived_from_actual_time_ordered_values():
    assert CASES[0].current_means == (4, 4)
    normal = _history(SyntheticHoldoutInvestigationTools(CASES[0]))
    assert normal.trend == "STABLE" and normal.baseline.mean_hist == 4
    downward = _history(SyntheticHoldoutInvestigationTools(CASES[4]))
    assert [row.lot_mean for row in reversed(downward.prior)] == [4, 3, 2]
    assert downward.current.lot_mean == -2 and downward.trend == "DRIFT_DOWN"
    assert CASES[2].current_means == (5,) and CASES[2].upstream_mean == -2
    assert CASES[6].current_means == (5, -2)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_holdout_metadata_not_in_selector_or_hypothesis_and_candidates_stay_open(case):
    original = case
    case = replace(
        case,
        case_id="EVALUATOR_ID_SENTINEL",
        title="EVALUATOR_TITLE_SENTINEL",
        acceptance=("EVALUATOR_ACCEPTANCE_SENTINEL",),
    )
    tools = tools_factory_for_case(case)(case)
    route = case.route()
    first = tools.fdc_summary(
        "RUN-1", FdcSummaryToolInput(lot_hist_id=case.current_ids[0])
    )
    equipment = tools.equipment_context(
        "RUN-1", EquipmentContextToolInput(chamber_id=CHAMBER)
    )
    candidates = react.build_initial_candidates(
        run_id="RUN-1", route=route, current_lot_hist_ids=case.current_ids
    )
    candidates = react.refresh_history_candidates(
        candidates, fdc_results=(first,), equipment=equipment
    )
    context = react.build_context(
        run_id="RUN-1",
        lot_id=LOT,
        chamber_id=CHAMBER,
        representative_alarm=route.incident.representative_alarm,
        member_alarms=route.incident.member_alarms,
        route=route,
        candidates=candidates,
        fdc_results=(first,),
        equipment=equipment,
        documents=(),
        remaining_tool_calls=22,
        remaining_steps=28,
        guard_rejections=0,
    )
    messages = json.dumps(
        [
            react.build_react_select_messages(context),
            build_hypothesis_messages((first,), equipment, None, route),
        ],
        ensure_ascii=False,
    )
    assert case.case_id not in messages and case.title not in messages
    assert all(note not in messages for note in case.acceptance)
    assert original.case_id not in messages and original.title not in messages
    assert all(note not in messages for note in original.acceptance)
    assert isinstance(case, HoldoutCase)
    assert {
        item.lot_hist_id
        for item in context.candidates.fdc
        if item.relation == "CURRENT"
    } == set(case.current_ids)
    assert any(item.scope == "SIBLING" for item in context.candidates.history)
    assert any(item.relation == "UPSTREAM" for item in context.candidates.fdc) == (
        case.upstream_mean is not None
    )
    assert case.diagnostic_wafer_refs == tuple(
        (identity, wafer.wafer_id)
        for identity, wafer in zip(case.current_ids, route.wafer_routes, strict=True)
    )


def test_upstream_missing_metrology_is_not_current_pass_or_retryable_timeout():
    tools = SyntheticHoldoutInvestigationTools(CASES[2])
    missing = tools.metrology_result(
        "RUN-1", MetrologyResultToolInput(lot_id=LOT, step_id="STEP-UPSTREAM")
    )
    present = tools.metrology_result(
        "RUN-1", MetrologyResultToolInput(lot_id=LOT, step_id=STEP)
    )
    assert not missing.ok and missing.reason.startswith("NOT_FOUND:")
    assert missing.results == [] and missing.fail_count is None
    assert present.ok and present.fail_count == 0
    assert [row.status for row in tools.history("RUN-1")] == [
        ToolCallStatus.ERROR,
        ToolCallStatus.SUCCESS,
    ]
    assert [row.input["step_id"] for row in tools.history("RUN-1")] == [
        "STEP-UPSTREAM",
        STEP,
    ]


def test_holdout_documents_match_direction_and_do_not_mutate_base_corpus():
    tools = SyntheticHoldoutInvestigationTools(CASES[1])
    for query in ("P1 하한", "P1 lower", "P1 below", "P1 하한 이탈"):
        result = tools.document_search("RUN-1", DocumentSearchToolInput(query=query))
        assert result.ok and result.hits[0].chunk_id == "DOC-P1-DIRECTION"
        assert "하한" in result.hits[0].content and "상한" not in result.hits[0].content
    trend = tools.document_search(
        "RUN-1", DocumentSearchToolInput(query="P1 과거 감소 추세")
    )
    assert "감소" in trend.hits[0].content
    assert "상한" in BASE_DOCUMENTS["direction"][1]
    assert "감소" not in BASE_DOCUMENTS["history"][1]
    assert DOCUMENTS["general"] == BASE_DOCUMENTS["general"]
    empty = tools.document_search(
        "RUN-1", DocumentSearchToolInput(query="P1 하한", model_code="OTHER")
    )
    assert empty.ok and empty.hits == []


def test_holdout_timeout_recovery_and_repeated_chunk_have_actual_ledgers():
    tools = SyntheticHoldoutInvestigationTools(CASES[5])
    query = DocumentSearchToolInput(query="P1 하한 이탈", model_code=MODEL)
    failure = tools.document_search("RUN-1", query)
    recovery = tools.document_search("RUN-1", query)
    assert not failure.ok and failure.reason.startswith("TIMEOUT:")
    assert recovery.ok and "하한" in recovery.hits[0].content
    assert [row.status for row in tools.history("RUN-1")] == [
        ToolCallStatus.TIMEOUT,
        ToolCallStatus.SUCCESS,
    ]
    assert tools.budget("RUN-1").used == 2 and tools.history("OTHER") == ()
    repeated = SyntheticHoldoutInvestigationTools(CASES[7])
    first = repeated.document_search("RUN-1", query)
    second = repeated.document_search(
        "RUN-1", DocumentSearchToolInput(query="P1 감소 추세")
    )
    assert first == second and first.hits[0].chunk_id == "DOC-P1-GENERAL"
    rows = repeated.history("RUN-1")
    assert len(rows) == 2 and rows[0].input != rows[1].input


def test_numeric_holdouts_are_registered_separately_and_base_values_are_unchanged():
    assert len(CASES) == 8
    assert HOLDOUT_CASES is CASES
    assert {case.case_id for case in CASES} == {f"HOLD-{i:02}" for i in range(1, 9)}
    assert {case.case_id for case in CASES}.isdisjoint(
        {case.case_id for case in BASE_CASES}
    )
    assert BASE_CASES[0].current_means == (5, 5)
    assert BASE_CASES[1].current_means == (12,)
    assert BASE_CASES[2].upstream_mean == 12
    assert BASE_CASES[4].prior_means == (8, 7, 6)
    assert CASES[5].document_timeout_once and CASES[7].repeated_document
    assert all(
        tools_factory_for_case(case) is SyntheticInvestigationTools
        for case in BASE_CASES
    )
    assert all(
        tools_factory_for_case(case) is SyntheticHoldoutInvestigationTools
        for case in CASES
    )
    with pytest.raises(ValueError, match="UNKNOWN_SYNTHETIC_DEVELOPMENT_CASE"):
        tools_factory_for_case(object())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_evaluator_metadata_cannot_change_tool_data_or_corpus_selection(case):
    changed = replace(
        case,
        case_id="NOT_A_HOLD_PREFIX",
        title="EVALUATOR_TITLE_ONLY",
        acceptance=("EVALUATOR_ONLY_NOT_A_FAULT_ANSWER",),
    )
    original_tools = tools_factory_for_case(case)(case)
    changed_tools = tools_factory_for_case(changed)(changed)
    assert (
        type(original_tools)
        is type(changed_tools)
        is SyntheticHoldoutInvestigationTools
    )
    assert case.route() == changed.route()
    for identity in case.current_ids:
        request = FdcSummaryToolInput(lot_hist_id=identity)
        assert original_tools.fdc_summary(
            "RUN-1", request
        ) == changed_tools.fdc_summary("RUN-1", request)
    query = DocumentSearchToolInput(query="P1 하한")
    for _ in range(2):
        assert original_tools.document_search("RUN-1", query) == (
            changed_tools.document_search("RUN-1", query)
        )
