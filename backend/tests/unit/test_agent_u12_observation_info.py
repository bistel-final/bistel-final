"""Result information must survive private selector projection, without live LLM."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agent import react
from app.common.enums import ToolCallStatus
from app.common.tool_contracts import (
    ChamberParameterHistoryToolInput,
    MetrologyResultItem,
    MetrologyResultToolResult,
)
from tests.unit import test_agent_react as fixture
from tests.unit.test_agent_graph import _fdc
from tests.unit.test_agent_u10_observations import context, history_result
from tests.unit.test_agent_u12_selector import document, payload


def test_sibling_normal_and_abnormal_are_different_private_observations():
    ctx = fixture._context()
    candidate = ctx.candidates.history[0]
    candidate.scope = "SIBLING"
    candidate.chamber_id = "EQP01-PM2"
    selection = fixture._selection(
        "get_chamber_parameter_history", history_candidate_id="H1"
    )
    ctx.successful_inputs = (react.resolve_call(selection, ctx),)
    result = history_result().model_copy(
        update={"scope": "SIBLING", "chamber_id": "EQP01-PM2", "parameter_id": "P1"}
    )
    normal = result.model_copy(deep=True)
    normal.current.lot_mean = 5
    normal.current.ooc_wafers = normal.current.oos_wafers = 0
    abnormal = result.model_copy(deep=True)
    abnormal.current.lot_mean = 12
    abnormal.current.ooc_wafers = abnormal.current.oos_wafers = 1
    assert react.summarize_history(normal) == react.summarize_history(abnormal)
    details = [react._history_details(row, ctx) for row in (normal, abnormal)]
    assert details[0] != details[1]
    assert "mean=5" in details[0] and "ooc=0,oos=0" in details[0]
    assert "mean=12" in details[1] and "ooc=1,oos=1" in details[1]
    assert all(text.startswith("H1(P1, step_no=1, SIBLING)") for text in details)
    assert all("trend=INSUFFICIENT" in text for text in details)


def test_fdc_private_projection_keeps_normal_contrast_and_recipe_step():
    result = _fdc()
    base = result.parameters[0]
    result.parameters = [
        base.model_copy(update={"parameter_id": f"P{i}"}) for i in range(6)
    ]
    result.parameters.append(
        base.model_copy(
            update={
                "parameter_id": "NORMAL_CONTROL",
                "recipe_step_no": 2,
                "ooc_point_cnt": 0,
                "oos_point_cnt": 0,
                "value_mean": 5,
            }
        )
    )
    state = context()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, result)
    observed = payload(state.build_context())["observations"]["fdc"][0]
    assert observed.startswith("F1:")
    assert "NORMAL_CONTROL(step=2,mean=5" in observed
    assert "P0(step=1" in observed and "omitted_parameters=3" in observed
    assert len(observed) <= react._SELECTOR_OBSERVATION_MAX + 4


def test_metrology_identity_and_missing_numeric_values_are_preserved():
    state = context()
    result = MetrologyResultToolResult(
        ok=True,
        lot_id="LOT001",
        step_id="CT-PHOTO",
        fail_count=1,
        disclaimer="계측 품질 근거",
        results=[
            MetrologyResultItem(
                wafer_id="LOT001W001",
                measure_type="CD_ADI",
                measured_value=None,
                alarm_result="FAIL",
                measured_at=fixture.NOW,
            )
        ],
    )
    state.record(
        "get_metrology_result", {"lot_id": "LOT001", "step_id": "CT-PHOTO"}, result
    )
    observed = payload(state.build_context())["observations"]["metrology"][0]
    assert observed.startswith("M1(CURRENT,step=CT-PHOTO)")
    assert "CD_ADI(n=1,missing=1,min=unknown,max=unknown,fail=1)" in observed
    assert "step=" not in react.summarize_metrology(result)


def test_latest_search_results_do_not_disappear_behind_duplicate_old_hits():
    state = context()
    first = document("이전 조회 근거")
    first.hits *= 3
    state.record(
        "search_documents", {"query": "첫 질문", "model_code": "MODEL-1"}, first
    )
    second = document("새 질문의 새로운 근거")
    second.hits[0].chunk_id = "chunk-2"
    state.record(
        "search_documents", {"query": "다음 질문", "model_code": "MODEL-1"}, second
    )
    observed = payload(state.build_context())["observations"]["documents"]
    assert [item["chunk_id"] for item in observed] == ["chunk-2", "chunk-1"]
    assert observed[0]["excerpt"] == "새 질문의 새로운 근거"


@pytest.mark.parametrize("tz", [None, UTC, timezone(timedelta(hours=9))])
def test_history_resolver_matches_persisted_dto_encoding_and_duplicate_guard(tz):
    ctx = fixture._context()
    ctx.candidates.history[0].before = datetime(2026, 1, 1, tzinfo=tz)
    selection = fixture._selection(
        "get_chamber_parameter_history", history_candidate_id="H1"
    )
    resolved = react.resolve_call(selection, ctx)
    persisted = ChamberParameterHistoryToolInput.model_validate(
        resolved["request"]
    ).model_dump(mode="json")
    assert resolved["request"] == persisted
    row = SimpleNamespace(
        tool_name=selection.next, input=persisted, status=ToolCallStatus.SUCCESS
    )
    assert (
        react.guard_selection(
            selection, ctx, equipment_fetched=False, tool_history=[row]
        )
        == "REACT_GUARD_TARGET_REPEATED"
    )
    ctx.successful_inputs = ({"tool": selection.next, "request": persisted},)
    assert payload(ctx)["candidates"]["history"][0]["observed"]
