"""V5-C-7.1: bounded hypothesis inputs retain contrasts and sample gaps."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.agent import prompts
from app.agent.diagnostics import (
    DiagnosticSourceIds,
    DirectScope,
    IncidentDiagnosticSnapshot,
    WaferParameterObservation,
)
from app.agent.investigation_models import InvestigationEvidence
from app.common.enums import AlarmType
from app.common.tool_contracts import (
    ChamberParameterHistoryToolResult,
    HistoryBaseline,
    LotAggregate,
)
from tests.unit.test_agent_prompts import _route


def _observation(index: int, *, abnormal: bool, parameter: str = "PH_FOCUS"):
    return WaferParameterObservation(
        lot_hist_id=f"LH-{index:02d}",
        wafer_id=f"W{index:02d}",
        wafer_no=index,
        step_id="CT-PHOTO",
        recipe_step_no=1,
        recipe_step_name="Exposure",
        parameter_id=parameter,
        parameter_name=parameter,
        alarm_type=AlarmType.OOS if abnormal else AlarmType.IN,
        point_count=6,
        ooc_point_count=0,
        oos_point_count=1 if abnormal else 0,
        value_mean=12.0 if abnormal else 10.0,
        value_min=10.0,
        value_max=15.0 if abnormal else 10.5,
        deviation=None,
    )


def _snapshot(observations):
    return IncidentDiagnosticSnapshot(
        lot_id="LOT-1",
        chamber_id="EQP-1-PM1",
        representative_alarm_ref="TRACE:TA-01",
        member_alarm_count=1,
        target_wafer_count=8,
        observed_wafer_count=8,
        wafer_observations=tuple(observations),
        parameter_patterns=(),
        step_patterns=(),
        direct_scope=DirectScope(
            lot_ids=("LOT-1",),
            wafer_ids=tuple(item.wafer_id for item in observations),
            chamber_ids=("EQP-1-PM1",),
            parameter_ids=tuple(sorted({item.parameter_id for item in observations})),
            model_codes=("PH-9000",),
        ),
        data_gaps=(),
        source_ids=DiagnosticSourceIds(
            alarm_refs=("TRACE:TA-01",),
            lot_hist_ids=tuple(item.lot_hist_id for item in observations),
            parameter_ids=tuple(sorted({item.parameter_id for item in observations})),
            relation_ids=(),
            graph_revisions=(),
        ),
    )


def _payload(*, observations=(), history=()):
    messages = prompts.build_hypothesis_messages(
        None,
        None,
        None,
        _route(),
        diagnostic_snapshot=_snapshot(observations) if observations else None,
        investigation=InvestigationEvidence(history=tuple(history)),
    )
    assert sum(len(message["content"]) for message in messages) < 12_000
    return json.loads(messages[1]["content"].removeprefix("Evidence JSON:\n"))


def _lot(lot_id: str, *, count=12, missing=0):
    now = datetime(2026, 9, 7, tzinfo=UTC)
    return LotAggregate(
        lot_id=lot_id,
        lot_mean=10.0,
        wafer_count=count,
        ooc_wafers=0,
        oos_wafers=0,
        evaluation_missing=missing,
        track_in_from=now,
        track_in_to=now,
    )


def _history(*, prior_count=3):
    return ChamberParameterHistoryToolResult(
        ok=True,
        scope="CURRENT",
        chamber_id="EQP-1-PM1",
        parameter_id="PH_FOCUS",
        step_no=1,
        current=_lot("LOT-1", count=13, missing=2),
        prior=[
            _lot(f"LOT-P{index}", count=index + 1, missing=index % 2)
            for index in range(prior_count)
        ],
        baseline=HistoryBaseline(
            mean_hist=10.0, sd_hist=0.0, prior_lot_count=prior_count
        ),
        trend="STABLE",
        comparison="CURRENT",
        sample_count=13,
    )


def test_six_abnormal_wafers_do_not_hide_all_normal_controls():
    observations = [
        *(_observation(index, abnormal=True) for index in range(1, 7)),
        _observation(7, abnormal=False),
        _observation(8, abnormal=False),
    ]
    before = _snapshot(observations).model_dump()
    diagnostic = _payload(observations=observations)["diagnostic_snapshot"]
    selected = diagnostic["wafer_observations"]
    assert len(selected) == prompts.MAX_PROMPT_WAFER_OBSERVATIONS == 6
    assert any(item["alarm_type"] == "IN" for item in selected)
    assert any(item["alarm_type"] == "OOS" for item in selected)
    assert diagnostic["wafer_observation_count"] == 8
    assert diagnostic["wafer_observations_omitted_count"] == 2
    assert _snapshot(observations).model_dump() == before
    assert (
        selected
        == _payload(observations=list(reversed(observations)))["diagnostic_snapshot"][
            "wafer_observations"
        ]
    )


def test_normal_control_prefers_matching_parameter_and_step():
    observations = [
        *(_observation(index, abnormal=True) for index in range(1, 7)),
        _observation(7, abnormal=False, parameter="PH_DOSE"),
        _observation(8, abnormal=False),
    ]
    selected = _payload(observations=observations)["diagnostic_snapshot"][
        "wafer_observations"
    ]
    assert any(item["lot_hist_id"] == "LH-08" for item in selected)


def test_absent_measurements_are_not_promoted_as_normal_controls():
    missing = _observation(7, abnormal=False).model_copy(
        update={
            "point_count": 0,
            "value_mean": None,
            "value_min": None,
            "value_max": None,
        }
    )
    observations = [
        *(_observation(index, abnormal=True) for index in range(1, 7)),
        missing,
        _observation(8, abnormal=False),
    ]
    selected = _payload(observations=observations)["diagnostic_snapshot"][
        "wafer_observations"
    ]
    assert any(item["lot_hist_id"] == "LH-08" for item in selected)
    assert all(item["lot_hist_id"] != "LH-07" for item in selected)


def test_sampling_never_invents_a_control_or_changes_observations():
    observations = [_observation(index, abnormal=True) for index in range(1, 9)]
    snapshot = _snapshot(observations)
    before = snapshot.model_dump()
    selected = prompts._prompt_wafer_observations(snapshot)
    assert len(selected) == 6
    assert all(item.alarm_type == AlarmType.OOS for item in selected)
    assert all(item in observations for item in selected)
    assert snapshot.model_dump() == before


def test_history_preserves_current_and_prior_sample_gaps_without_full_rows():
    history = _history()
    before = history.model_dump()
    observed = _payload(history=(history,))["investigation"]["history"][0]
    assert observed["chamber_id"] == history.chamber_id
    assert observed["current"]["lot_id"] == "LOT-1"
    assert observed["current"]["wafer_count"] == 13
    assert observed["current"]["evaluation_missing"] == 2
    assert observed["baseline"] == {
        "mean_hist": 10.0,
        "sd_hist": 0.0,
        "prior_lot_count": 3,
    }
    assert observed["prior_samples"] == [
        {
            "lot_id": f"LOT-P{index}",
            "wafer_count": index + 1,
            "evaluation_missing": index % 2,
        }
        for index in range(3)
    ]
    assert "track_in_from" not in json.dumps(observed)
    assert history.model_dump() == before


def test_sibling_contrast_keeps_empty_baseline_distinct_from_zero_variance():
    sibling = _history(prior_count=0).model_copy(
        update={
            "scope": "SIBLING",
            "chamber_id": "EQP-1-PM2",
            "comparison": "SIBLING",
            "baseline": HistoryBaseline(prior_lot_count=0),
            "trend": "INSUFFICIENT",
        }
    )
    current, other = _payload(history=(_history(), sibling))["investigation"]["history"]
    assert current["baseline"]["sd_hist"] == 0.0
    assert other["baseline"]["sd_hist"] is None
    assert other["prior_samples"] == other["prior_means"] == []
    assert other["current"]["wafer_count"] == 13
    assert other["current"]["ooc_wafers"] == other["current"]["oos_wafers"] == 0
    assert other["current"]["evaluation_missing"] == 2
    assert other["chamber_id"] != current["chamber_id"]


def test_history_projection_is_bounded_and_reports_omissions():
    investigation = _payload(history=(_history(prior_count=5),) * 6)["investigation"]
    assert len(investigation["history"]) == 4
    assert investigation["history_count"] == 6
    assert investigation["history_omitted_count"] == 2
    for item in investigation["history"]:
        assert len(item["prior_means"]) == len(item["prior_samples"]) == 3
        assert item["prior_count"] == 5
        assert item["prior_omitted_count"] == 2


def test_normal_contrast_and_four_history_results_share_existing_message_cap():
    observations = [
        *(_observation(index, abnormal=True) for index in range(1, 7)),
        _observation(7, abnormal=False),
        _observation(8, abnormal=False),
    ]
    payload = _payload(observations=observations, history=(_history(),) * 4)
    assert payload["diagnostic_snapshot"]["wafer_observations_omitted_count"] == 2
    assert payload["investigation"]["history_omitted_count"] == 0


def test_payload_enrichment_keeps_existing_prompt_and_size_contract():
    assert prompts.PROMPT_VERSION == "agent-hypothesis-v3-ko4"
    assert prompts.MAX_PROMPT_CHARS == 48_000
    assert prompts.MAX_PROMPT_WAFER_OBSERVATIONS == 6
