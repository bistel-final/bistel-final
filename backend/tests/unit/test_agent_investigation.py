"""V5-C-7.1 U7 candidate token·이력 추세·계측 계약 회귀."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import investigation, react
from app.agent.incident import ResolvedIncident
from app.agent.routing import ResolvedIncidentRoute, WaferRoute
from app.agent.routing_repository import RouteStep
from app.common.enums import AlarmSource
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    ParameterSummaryItem,
    WaferContext,
)

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)
ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TRACE-1")


def _step(
    lot_hist_id: str,
    *,
    step_id: str,
    chamber_id: str,
    offset: int,
) -> RouteStep:
    return RouteStep(
        lot_hist_id=lot_hist_id,
        lot_id="LOT001",
        wafer_id="LOT001W001",
        wafer_no=1,
        step_id=step_id,
        area_id=None,
        equipment_id=chamber_id.split("-")[0],
        chamber_id=chamber_id,
        recipe_id="RECIPE01",
        track_in_at=NOW + timedelta(minutes=offset),
        track_out_at=NOW + timedelta(minutes=offset + 1),
    )


def _route() -> ResolvedIncidentRoute:
    incident = ResolvedIncident(
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        requested_alarm=ALARM,
        representative_alarm=ALARM,
        member_alarms=(ALARM,),
    )
    return ResolvedIncidentRoute(
        incident=incident,
        wafer_routes=(
            WaferRoute(
                wafer_id="LOT001W001",
                member_alarms=(ALARM,),
                steps=(
                    _step(
                        "LH-PHOTO",
                        step_id="CT-PHOTO",
                        chamber_id="EQP01-PM1",
                        offset=0,
                    ),
                    _step(
                        "LH-ETCH",
                        step_id="CT-ETCH",
                        chamber_id="EQP04-PM1",
                        offset=10,
                    ),
                ),
            ),
        ),
        graph_evidence=(),
        route_consistency=True,
        mismatches=(),
    )


def _fdc() -> FdcSummaryToolResult:
    return FdcSummaryToolResult(
        ok=True,
        wafer=WaferContext(
            lot_hist_id="LH-PHOTO",
            lot_id="LOT001",
            wafer_no=1,
            chamber_id="EQP01-PM1",
            equipment_id="EQP01",
            step_id="CT-PHOTO",
        ),
        parameters=[
            ParameterSummaryItem(
                parameter_id="PH_FOCUS",
                parameter_name="Focus",
                recipe_step_no=1,
                point_cnt=3,
                ooc_point_cnt=1,
                oos_point_cnt=0,
                alarm_type="OOC",
            )
        ],
    )


@pytest.mark.parametrize(
    ("current", "prior", "expected"),
    [
        (10.0, [9.0], "INSUFFICIENT"),
        (10.0, [10.0, 10.0], "STABLE"),
        (11.0, [10.0, 10.0], "SUDDEN"),
        (20.0, [12.0, 11.0, 10.0], "DRIFT_UP"),
        (0.0, [8.0, 9.0, 10.0], "DRIFT_DOWN"),
        (20.0, [10.0, 12.0, 11.0], "SUDDEN"),
    ],
)
def test_history_trend_truth_table(
    current: float,
    prior: list[float],
    expected: str,
) -> None:
    trend, _mean, _sd = investigation.classify_history_trend(current, prior)
    assert trend == expected


def test_photo_and_etch_incidents_only_offer_existing_route_directions() -> None:
    photo = react.build_initial_candidates(
        run_id="RUN-PHOTO",
        route=_route(),
        current_lot_hist_ids=("LH-PHOTO",),
    )
    assert [(item.candidate_id, item.relation) for item in photo.fdc] == [
        ("F1", "CURRENT"),
        ("F2", "DOWNSTREAM"),
    ]

    etch = react.build_initial_candidates(
        run_id="RUN-ETCH",
        route=_route(),
        current_lot_hist_ids=("LH-ETCH",),
    )
    assert [(item.candidate_id, item.relation) for item in etch.fdc] == [
        ("F1", "CURRENT"),
        ("F2", "UPSTREAM"),
    ]


def test_history_tokens_are_derived_only_after_observation_and_sibling_resolution() -> (
    None
):
    initial = react.build_initial_candidates(
        run_id="RUN-1",
        route=_route(),
        current_lot_hist_ids=("LH-PHOTO",),
    )
    assert initial.history == ()

    current = react.refresh_history_candidates(
        initial,
        fdc_results=(_fdc(),),
        equipment=None,
    )
    assert [(item.candidate_id, item.scope) for item in current.history] == [
        ("H1", "CURRENT")
    ]

    equipment = EquipmentContextToolResult(
        ok=True,
        chamber_id="EQP01-PM1",
        equipment_id="EQP01",
        sibling_chamber_ids=["EQP01-PM2"],
        area="PHOTO",
        model_code="MODEL-1",
        process_step_id="CT-PHOTO",
        graph_revision="rev-1",
    )
    with_sibling = react.refresh_history_candidates(
        initial,
        fdc_results=(_fdc(),),
        equipment=equipment,
    )
    assert [(item.candidate_id, item.scope) for item in with_sibling.history] == [
        ("H1", "CURRENT"),
        ("H2", "SIBLING"),
    ]


def test_history_token_identity_is_stable_when_earlier_parameter_is_added():
    initial = react.build_initial_candidates(
        run_id="RUN-1",
        route=_route(),
        current_lot_hist_ids=("LH-PHOTO",),
    )
    first = react.refresh_history_candidates(
        initial, fdc_results=(_fdc(),), equipment=None
    )
    added = _fdc().model_copy(
        update={
            "parameters": [
                _fdc()
                .parameters[0]
                .model_copy(update={"parameter_id": "AAA_NEW_PARAMETER"})
            ]
        }
    )
    refreshed = react.refresh_history_candidates(
        first,
        fdc_results=(_fdc(), added),
        equipment=None,
    )
    assert refreshed.history[0] == first.history[0]
    assert {item.candidate_id for item in refreshed.history} == {"H1", "H2"}


def test_history_cutoff_uses_whole_lot_first_track_in_not_member_wafer_time():
    route = _route()
    wafer = route.wafer_routes[0]
    first = NOW - timedelta(hours=1)
    route = replace(
        route,
        wafer_routes=(
            replace(
                wafer,
                steps=(
                    replace(wafer.steps[0], lot_first_track_in_at=first),
                    wafer.steps[1],
                ),
            ),
        ),
    )
    initial = react.build_initial_candidates(
        run_id="RUN-1",
        route=route,
        current_lot_hist_ids=("LH-PHOTO",),
    )
    candidates = react.refresh_history_candidates(
        initial, fdc_results=(_fdc(),), equipment=None
    )
    assert candidates.history[0].before == first


def test_selector_prompt_exposes_tokens_but_not_lot_history_ids() -> None:
    candidates = react.build_initial_candidates(
        run_id="RUN-1",
        route=_route(),
        current_lot_hist_ids=("LH-PHOTO",),
    )
    context = react.build_context(
        run_id="RUN-1",
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        representative_alarm=ALARM,
        member_alarms=(ALARM,),
        route=_route(),
        candidates=candidates,
        fdc_results=(),
        equipment=None,
        documents=(),
        remaining_tool_calls=8,
        remaining_steps=10,
        guard_rejections=0,
    )
    payload = react.build_react_select_messages(context)[1]["content"]
    parsed = json.loads(payload)
    assert parsed["candidates"]["fdc"][0]["id"] == "F1"
    assert "LH-PHOTO" not in payload
    assert "LH-ETCH" not in payload


def test_argument_matrix_and_unknown_candidate_are_rejected_before_tool_call() -> None:
    candidates = react.build_initial_candidates(
        run_id="RUN-1",
        route=_route(),
        current_lot_hist_ids=("LH-PHOTO",),
    )
    context = react.build_context(
        run_id="RUN-1",
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        representative_alarm=ALARM,
        member_alarms=(ALARM,),
        route=_route(),
        candidates=candidates,
        fdc_results=(),
        equipment=None,
        documents=(),
        remaining_tool_calls=8,
        remaining_steps=10,
        guard_rejections=0,
    )
    wrong_matrix = react.ReactSelection(
        rationale_summary="fixture",
        next="get_metrology_result",
        arguments=react.ReactArguments(
            metrology_candidate_id="M1",
            query="not allowed",
        ),
    )
    assert (
        react.guard_selection(
            wrong_matrix,
            context,
            equipment_fetched=False,
        )
        == "REACT_GUARD_ARGUMENT_MATRIX"
    )

    unknown = react.ReactSelection(
        rationale_summary="fixture",
        next="get_metrology_result",
        arguments=react.ReactArguments(metrology_candidate_id="M999"),
    )
    assert (
        react.guard_selection(
            unknown,
            context,
            equipment_fetched=False,
        )
        == "REACT_GUARD_CANDIDATE_UNKNOWN"
    )


def test_metrology_disclaimer_is_not_a_fault_label() -> None:
    assert investigation.METROLOGY_DISCLAIMER == (
        "계측 PASS/FAIL은 제품 품질 근거이며 Fault Mode 정답이 아니다"
    )


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(mappings=lambda: iter(self.rows))


class _FakeEngine:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self.rows)


def _aggregate_row(kind: str, ordinal: int, value: float) -> dict[str, Any]:
    return {
        "row_kind": kind,
        "ordinal": ordinal,
        "lot_id": f"LOT-{ordinal}",
        "lot_mean": value,
        "lot_std": 0.1,
        "lot_min": value - 0.2,
        "lot_max": value + 0.2,
        "wafer_count": 2,
        "ooc_wafers": 1,
        "oos_wafers": 0,
        "evaluation_missing": 0,
        "track_in_from": NOW - timedelta(days=ordinal),
        "track_in_to": NOW - timedelta(days=ordinal, minutes=-1),
    }


def test_history_tool_returns_typed_drift_and_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _aggregate_row("CURRENT", 0, 20.0),
        _aggregate_row("PRIOR", 1, 12.0),
        _aggregate_row("PRIOR", 2, 11.0),
        _aggregate_row("PRIOR", 3, 10.0),
    ]
    monkeypatch.setattr(investigation, "get_readonly_engine", lambda: _FakeEngine(rows))
    monkeypatch.setattr(
        investigation,
        "apply_postgres_statement_timeout",
        lambda *_args, **_kwargs: None,
    )
    result = investigation.get_chamber_parameter_history(
        {
            "chamber_id": "EQP01-PM1",
            "parameter_id": "PH_FOCUS",
            "step_no": 1,
            "before": NOW.isoformat(),
            "n_lots": 3,
            "_context": {
                "current_lot_id": "LOT001",
                "incident_step_id": "CT-PHOTO",
                "scope": "CURRENT",
            },
        }
    )
    assert result.ok
    assert result.trend == "DRIFT_UP"
    assert result.baseline is not None and result.baseline.prior_lot_count == 3
    assert result.sample_count == 2


def test_metrology_tool_returns_fail_count_and_fixed_disclaimer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "wafer_id": "LOT001W001",
            "measure_type": "CD_ADI",
            "measured_value": 10.0,
            "spec_lower": 9.0,
            "spec_upper": 11.0,
            "alarm_result": "PASS",
            "measured_at": NOW,
        },
        {
            "wafer_id": "LOT001W002",
            "measure_type": "CD_ADI",
            "measured_value": 12.0,
            "spec_lower": 9.0,
            "spec_upper": 11.0,
            "alarm_result": "FAIL",
            "measured_at": NOW + timedelta(seconds=1),
        },
    ]
    monkeypatch.setattr(investigation, "get_readonly_engine", lambda: _FakeEngine(rows))
    monkeypatch.setattr(
        investigation,
        "apply_postgres_statement_timeout",
        lambda *_args, **_kwargs: None,
    )
    result = investigation.get_metrology_result(
        {"lot_id": "LOT001", "step_id": "CT-PHOTO"}
    )
    assert result.ok
    assert result.fail_count == 1
    assert result.disclaimer == investigation.METROLOGY_DISCLAIMER
