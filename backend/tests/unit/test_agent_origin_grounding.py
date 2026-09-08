"""V5-C-7.1 origin claims require observed, cited location evidence.

This tightens new draft generation only. Fault labels, OTH classification and
historical artifact parsing are not used as shortcuts to decide an origin.
"""

import json
from dataclasses import replace

import pytest

from app.agent.diagnostics import build_diagnostic_snapshot
from app.agent.hypothesis_v3 import finalize_hypothesis
from app.agent.investigation import classify_history_trend
from app.agent.investigation_models import InvestigationEvidence, OriginBasisRef
from app.agent.state import Hypothesis
from app.common.llm import ChatCompletion
from app.common.tool_contracts import (
    ChamberParameterHistoryToolResult,
    HistoryBaseline,
)
from tests.support.agent_quality_development_cases import NOW, _aggregate
from tests.unit import test_agent_hypothesis_v3 as fixture


def _fdc(**values):
    return fixture._fdc(
        **{
            "point_cnt": 6,
            "ooc_point_cnt": 0,
            "oos_point_cnt": 0,
            "alarm_type": "IN",
            "target": None,
            "value_min": 5.0,
            "value_mean": 5.0,
            "value_max": 5.0,
            "ctrl_lower": 1.0,
            "ctrl_upper": 9.0,
            "spec_lower": 0.0,
            "spec_upper": 10.0,
            **values,
        }
    )


def _draft(scope="CURRENT_CHAMBER", **values):
    return fixture._draft(
        predicted_fault_code="OTH",
        cause_summary="PH_FOCUS 관측의 물리적 원인은 아직 미확정입니다.",
        parameter_findings_draft=[],
        origin_claim={
            "scope": scope,
            "basis_refs": [{"namespace": "PARAMETER", "id": "PH_FOCUS"}],
        },
        **values,
    )


def _finalize(draft=None, *, fdc=None, route=None, investigation=None, **kwargs):
    results = [_fdc()] if fdc is None else fdc
    selected_route = route or fixture.fixture._route()
    return finalize_hypothesis(
        draft or _draft(),
        results,
        selected_route,
        build_diagnostic_snapshot(results, selected_route),
        None,
        investigation or InvestigationEvidence(),
        **kwargs,
    )


def _history(prior, **values):
    trend, mean, sd = classify_history_trend(5.0, prior)
    return ChamberParameterHistoryToolResult(
        **{
            "ok": True,
            "scope": "CURRENT",
            "comparison": "CURRENT",
            "chamber_id": "EQP01-PM1",
            "parameter_id": "PH_FOCUS",
            "step_no": 1,
            "current": _aggregate("LOT001", (5.0,), NOW),
            "prior": [
                _aggregate(f"LOT-PRIOR-{index}", (value,), NOW)
                for index, value in enumerate(prior)
            ],
            "baseline": HistoryBaseline(
                mean_hist=mean, sd_hist=sd, prior_lot_count=len(prior)
            ),
            "trend": trend,
            "sample_count": 1,
            **values,
        }
    )


def _investigation(history, *, successful=True):
    return InvestigationEvidence(
        history=(history,),
        successful_calls=(
            {
                "tool_name": "get_chamber_parameter_history",
                "input": {
                    "chamber_id": "EQP01-PM1",
                    "parameter_id": "PH_FOCUS",
                    "step_no": 1,
                    "before": NOW.isoformat(),
                    "n_lots": 3,
                },
            },
        )
        if successful
        else (),
    )


@pytest.mark.parametrize("investigation", [None, _investigation(_history([5, 5, 5]))])
def test_normal_current_and_stable_history_do_not_ground_current_origin(investigation):
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        _finalize(investigation=investigation)


def test_live_generation_never_uses_legacy_recount_or_accepts_legacy_selection(
    monkeypatch,
):
    from app.agent import hypothesis, hypothesis_v3

    def forbidden_recount(*_args, **_kwargs):
        raise AssertionError("live generation must not use historical recount")

    monkeypatch.setattr(hypothesis_v3, "_recount_hypothesis", forbidden_recount)
    calls = []

    def complete(messages, **_kwargs):
        calls.append(messages)
        return ChatCompletion(
            content=json.dumps(_draft().model_dump(mode="json")),
            model="offline-fixture",
            prompt_tokens=10,
            completion_tokens=5,
        )

    arguments = (_fdc(), None, None, fixture.fixture._route())
    outcome = hypothesis.generate_hypothesis(*arguments, completion_port=complete)
    # 근거 없는 소재 주장은 채택되지 않는다. 라운드를 모두 소진한 뒤 강등 완료한다.
    assert outcome.fallback_reason == "ORIGIN_CLAIM_UNSUPPORTED"
    assert outcome.hypothesis.origin_assessment.scope == "UNDETERMINED"
    assert outcome.hypothesis.predicted_fault_code.value == "OTH"
    assert len(calls) == hypothesis.MAX_GENERATION_ROUNDS
    assert "ORIGIN_CLAIM_UNSUPPORTED" in repr(calls[1])
    for override in (
        {"hypothesis_prompt_version": "agent-hypothesis-v3-ko1"},
        {"enforce_current_origin": False},
    ):
        with pytest.raises(TypeError):
            hypothesis.generate_hypothesis(
                *arguments, completion_port=complete, **override
            )
    # TypeError 경로는 추가 호출을 만들지 않는다.
    assert len(calls) == hypothesis.MAX_GENERATION_ROUNDS


@pytest.mark.parametrize(
    "values",
    [
        {"ooc_point_cnt": 1, "value_min": None, "value_mean": None, "value_max": None},
        {"oos_point_cnt": 1, "ctrl_lower": None, "ctrl_upper": None},
        {"value_min": 0.5},
        {"value_max": 9.5},
        {"value_mean": 9.5},
        {"value_max": 12.0, "ctrl_lower": None, "ctrl_upper": None},
        {"value_min": -1.0, "ctrl_lower": None, "ctrl_upper": None},
        {"value_max": 12.0, "ctrl_lower": None, "ctrl_upper": None, "spec_lower": None},
    ],
)
def test_actual_cited_current_departure_allows_oth_without_ratio_or_target(values):
    value = _finalize(fdc=[_fdc(**values)])
    assert value.predicted_fault_code.value == "OTH"
    assert value.parameter_findings == ()
    assert value.origin_assessment.scope == "CURRENT_CHAMBER"


@pytest.mark.parametrize(
    "values",
    [
        {"alarm_type": "OOS"},  # A label alone is not an observed excursion.
        {"value_max": 9.0, "value_min": 1.0},
        {"point_cnt": 0, "ooc_point_cnt": 1, "value_max": 12.0},
        {"ctrl_lower": 10.0, "ctrl_upper": 1.0},  # Inverted bounds are unusable.
        {
            "ctrl_lower": None,
            "ctrl_upper": None,
            "spec_lower": None,
            "spec_upper": None,
        },
    ],
)
def test_labels_empty_samples_touching_or_unusable_bounds_do_not_prove_origin(values):
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        _finalize(fdc=[_fdc(**values)])


def test_upstream_only_departure_does_not_ground_current_origin():
    route = fixture.fixture._route()
    wafer = route.wafer_routes[0]
    upstream = fixture.fixture._step(
        "LH-UPSTREAM", step_id="CT-UPSTREAM", chamber_id="EQP02-PM1", offset=-10
    )
    route = replace(
        route, wafer_routes=(replace(wafer, steps=(upstream, *wafer.steps)),)
    )
    current, upstream_fdc = _fdc(), _fdc(value_max=12.0, oos_point_cnt=1)
    upstream_fdc.wafer = upstream_fdc.wafer.model_copy(
        update={
            "lot_hist_id": "LH-UPSTREAM",
            "chamber_id": "EQP02-PM1",
            "step_id": "CT-UPSTREAM",
        }
    )
    draft = _draft(supporting_lot_hist_ids=["LH-PHOTO", "LH-UPSTREAM"])
    investigation = InvestigationEvidence(
        successful_calls=(
            {"tool_name": "get_fdc_summary", "input": {"lot_hist_id": "LH-UPSTREAM"}},
        )
    )
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        _finalize(
            draft, fdc=[current, upstream_fdc], route=route, investigation=investigation
        )
    draft.origin_claim.scope = "UPSTREAM"
    result = _finalize(
        draft, fdc=[current, upstream_fdc], route=route, investigation=investigation
    )
    assert result.origin_assessment.scope == "UPSTREAM"


@pytest.mark.parametrize("uncited", ["lot_hist", "parameter"])
def test_uncited_current_departure_cannot_backfill_an_origin_claim(uncited):
    current, departure = _fdc(), _fdc(oos_point_cnt=1)
    route = fixture.fixture._route()
    if uncited == "lot_hist":
        departure.wafer = departure.wafer.model_copy(update={"lot_hist_id": "LH-OTHER"})
        wafer = route.wafer_routes[0]
        extra = replace(
            wafer.steps[0], lot_hist_id="LH-OTHER", wafer_id="LOT001W002", wafer_no=2
        )
        route = replace(
            route,
            wafer_routes=(
                *route.wafer_routes,
                replace(wafer, wafer_id="LOT001W002", steps=(extra,)),
            ),
        )
    else:
        departure.parameters[0].parameter_id = "OTHER_PARAMETER"
        current.parameters += departure.parameters
        departure = None
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        _finalize(
            fdc=[current] if departure is None else [current, departure], route=route
        )


@pytest.mark.parametrize(
    "prior,trend",
    [([3, 2, 1], "DRIFT_UP"), ([7, 8, 9], "DRIFT_DOWN"), ([4, 4], "SUDDEN")],
)
def test_observed_current_history_shift_can_ground_location_inside_absolute_limits(
    prior, trend
):
    history = _history(prior)
    assert history.trend == trend
    value = _finalize(investigation=_investigation(history))
    assert value.origin_assessment.scope == "CURRENT_CHAMBER"
    assert value.parameter_findings == ()


@pytest.mark.parametrize(
    "invalid", ["sibling", "sample", "mean", "prior", "ledger", "lot"]
)
def test_unbound_or_unsampled_history_shift_does_not_ground_current_origin(invalid):
    history = _history([3, 2, 1])
    if invalid == "sibling":
        history.scope = history.comparison = "SIBLING"
        history.chamber_id = "EQP01-PM2"
    elif invalid == "sample":
        history.sample_count = 0
    elif invalid == "mean":
        history.current.lot_mean = None
    elif invalid == "prior":
        history.prior = history.prior[:1]
    elif invalid == "lot":
        history.current.lot_id = "OTHER"
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        _finalize(investigation=_investigation(history, successful=invalid != "ledger"))


def test_u11_all_dropped_recovery_and_historical_parsing_remain_unchanged():
    draft = _draft()
    draft.origin_claim.basis_refs = (
        OriginBasisRef(namespace="PARAMETER", id="UNKNOWN"),
    )
    value = _finalize(draft, degrade_origin=True)
    assert value.origin_assessment.scope == "UNDETERMINED"
    assert value.origin_assessment.degraded is True
    assert value.origin_assessment.dropped_basis_count == 1
    legacy = value.model_dump()
    legacy["origin_assessment"].update(
        scope="CURRENT_CHAMBER",
        degraded=False,
        degraded_reasons=[],
        dropped_basis_count=0,
    )
    assert (
        Hypothesis.model_validate(legacy).origin_assessment.scope == "CURRENT_CHAMBER"
    )
    assert _finalize(_draft("UNDETERMINED")).origin_assessment.scope == "UNDETERMINED"
