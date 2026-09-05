from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.agent.diagnostics import build_diagnostic_snapshot
from app.agent.hypothesis_v3 import comparison_matrix, finalize_hypothesis
from app.agent.investigation_models import InvestigationEvidence
from app.agent.state import HypothesisDraftV3
from tests.unit import test_agent_investigation as fixture


def _fdc(**values):
    result = fixture._fdc()
    return result.model_copy(
        update={
            "parameters": [
                result.parameters[0].model_copy(
                    update={
                        "target": 10,
                        "ctrl_lower": 8,
                        "ctrl_upper": 12,
                        "value_min": 9,
                        "value_max": 16,
                        **values,
                    }
                )
            ]
        }
    )


def _draft(**values):
    return HypothesisDraftV3.model_validate(
        {
            "predicted_fault_code": "FOC",
            "confidence": 0.7,
            "cause_summary": "PH_FOCUS 이탈이 확인됨",
            "uncertainty": "범위 제한",
            "supporting_alarms": [fixture.ALARM.model_dump()],
            "supporting_parameter_ids": ["PH_FOCUS"],
            "supporting_lot_hist_ids": ["LH-PHOTO"],
            "parameter_findings_draft": [
                {"parameter_id": "PH_FOCUS", "lot_hist_ids": ["LH-PHOTO"]}
            ],
            "origin_claim": {
                "scope": "CURRENT_CHAMBER",
                "basis_refs": [
                    {"namespace": "PARAMETER", "id": "PH_FOCUS"},
                ],
            },
            **values,
        }
    )


def _finalize(draft=None, *, fdc=None, investigation=None):
    items = [fdc or _fdc()]
    route = fixture._route()
    return finalize_hypothesis(
        draft or _draft(),
        items,
        route,
        build_diagnostic_snapshot(items, route),
        None,
        investigation or InvestigationEvidence(),
    )


@pytest.mark.parametrize(
    ("values", "direction", "ratio"),
    [
        ({}, "ABOVE", 2.0),
        ({"value_max": 11, "value_min": 4}, "BELOW", 2.0),
        ({"value_min": 2}, "BOTH", 3.0),
    ],
)
def test_direction_and_ratio_are_recalculated_from_cited_fdc(values, direction, ratio):
    result = _finalize(fdc=_fdc(**values))
    finding = result.parameter_findings[0]
    assert (finding.direction, finding.excursion_ratio) == (direction, ratio)
    assert finding.wafer_scope == "SINGLE"
    assert result.origin_assessment.compared.downstream == "NOT_CHECKED"
    assert result.origin_assessment.compared.upstream == "NOT_AVAILABLE"


@pytest.mark.parametrize(
    "values",
    [
        {"target": None},
        {"target": 12},
        {"value_max": None},
        {"value_max": 12},
        {"ctrl_upper": None},
    ],
)
def test_invalid_or_absent_excursion_cannot_support_non_oth(values):
    with pytest.raises(ValueError, match="PARAMETER_FINDING_REQUIRED"):
        _finalize(fdc=_fdc(**values))


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"cause_summary": "초점 이상"}, "CAUSE_SUMMARY_PARAMETER_MISSING"),
        (
            {
                "origin_claim": {
                    "scope": "CURRENT_CHAMBER",
                    "basis_refs": [{"namespace": "LOT_HIST", "id": "PH_FOCUS"}],
                }
            },
            "ORIGIN_BASIS_OUTSIDE_EVIDENCE",
        ),
        (
            {"origin_claim": {"scope": "DOWNSTREAM", "basis_refs": []}},
            "ORIGIN_CLAIM_UNSUPPORTED",
        ),
        ({"cause_summary": "PH_FOCUS 하류 영향"}, "ORIGIN_CLAIM_UNSUPPORTED"),
        ({"supporting_lot_hist_ids": []}, "LOT_HISTORY_CITATION_OUTSIDE_EVIDENCE"),
    ],
)
def test_draft_claims_fail_closed(change, code):
    with pytest.raises(ValueError, match=code):
        _finalize(_draft(**change))


def test_draft_cannot_supply_calculated_fields():
    with pytest.raises(ValidationError):
        _draft(compared={"history": "CHECKED"})
    with pytest.raises(ValidationError):
        _draft(parameter_findings=[{"excursion_ratio": 999}])


def test_multiple_wafers_and_recipe_steps_are_calculated_independently():
    route = fixture._route()
    first = _fdc()
    second = first.model_copy(
        update={
            "wafer": first.wafer.model_copy(
                update={"lot_hist_id": "LH-SECOND", "wafer_no": 2}
            ),
            "parameters": [
                first.parameters[0].model_copy(update={"value_max": 20}),
                first.parameters[0].model_copy(update={"recipe_step_no": 2}),
            ],
        }
    )
    route = replace(
        route,
        wafer_routes=(
            *route.wafer_routes,
            replace(
                route.wafer_routes[0],
                wafer_id="LOT001W002",
                steps=(
                    replace(
                        route.wafer_routes[0].steps[0],
                        lot_hist_id="LH-SECOND",
                        wafer_no=2,
                        wafer_id="LOT001W002",
                    ),
                ),
            ),
        ),
    )
    draft = _draft(
        supporting_lot_hist_ids=["LH-PHOTO", "LH-SECOND"],
        parameter_findings_draft=[
            {"parameter_id": "PH_FOCUS", "lot_hist_ids": ["LH-PHOTO", "LH-SECOND"]}
        ],
    )
    result = finalize_hypothesis(
        draft,
        [first, second],
        route,
        build_diagnostic_snapshot([first, second], route),
        None,
        InvestigationEvidence(),
    )
    assert [
        (f.step_no, f.excursion_ratio, f.wafer_scope) for f in result.parameter_findings
    ] == [
        (1, 4.0, "ALL"),
        (2, 2.0, "SINGLE"),
    ]


def test_only_persisted_successes_mark_comparisons_checked():
    route = fixture._route()
    initial = comparison_matrix(route, InvestigationEvidence())
    assert initial.history == initial.metrology == initial.downstream == "NOT_CHECKED"
    evidence = InvestigationEvidence(
        successful_calls=[
            {"tool_name": "get_fdc_summary", "input": {"lot_hist_id": "LH-ETCH"}},
            {
                "tool_name": "get_chamber_parameter_history",
                "input": {"chamber_id": "EQP01-PM1"},
            },
            {
                "tool_name": "get_metrology_result",
                "input": {"lot_id": "LOT001", "step_id": "CT-PHOTO"},
            },
        ]
    )
    actual = comparison_matrix(route, evidence)
    assert actual.history == actual.metrology == actual.downstream == "CHECKED"
    assert actual.upstream == "NOT_AVAILABLE"
    # 成功 호출만으로 인용 의무를 대신하지 않는다.
    with pytest.raises(ValueError, match="ORIGIN_CLAIM_UNSUPPORTED"):
        _finalize(
            _draft(origin_claim={"scope": "DOWNSTREAM", "basis_refs": []}),
            investigation=evidence,
        )
