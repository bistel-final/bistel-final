"""Origin-claim consistency: local DTOs and completion ports, no live IO."""

import json
from dataclasses import replace

import pytest

from app.agent.diagnostics import build_diagnostic_snapshot
from app.agent.hypothesis import generate_hypothesis
from app.agent.hypothesis_v3 import finalize_hypothesis
from app.agent.investigation_models import InvestigationEvidence
from app.agent.routing import GraphRouteEvidence
from tests.unit import test_agent_hypothesis as generation
from tests.unit import test_agent_hypothesis_v3 as fixture


@pytest.mark.parametrize(
    "statement",
    [
        "상류는 아직 조사하지 않아 원인을 확정할 수 없음",
        "상류 원인은 미확인이며 추가 조사가 필요함",
        "하류 영향은 확인되지 않아 판단할 수 없음",
        "상류 원인 여부를 판단할 근거가 부족함",
        "상류 영향 가능성은 추가 확인 대상임",
    ],
)
def test_unchecked_direction_can_be_reported_as_a_limitation(statement):
    result = fixture._finalize(
        fixture._draft(cause_summary=f"PH_FOCUS 이탈이 관찰됨. {statement}")
    )
    assert result.origin_assessment.scope == "CURRENT_CHAMBER"
    assert not result.origin_assessment.degraded


@pytest.mark.parametrize(
    "field",
    ["cause_summary", "evidence_synthesis", "observations", "impact_summary"],
)
def test_unsupported_direction_cannot_move_to_another_assertion_field(field):
    statement = "상류 설비 이상이 현재 PH_FOCUS 이탈의 원인으로 확인됨"
    value = [statement] if field == "observations" else statement
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        fixture._finalize(fixture._draft(**{field: value}))


@pytest.mark.parametrize(
    "statement",
    [
        "상류는 미조사임. 상류 설비 이상이 PH_FOCUS 이탈의 원인으로 확인됨",
        "상류는 미조사이지만 상류 설비 이상이 PH_FOCUS 이탈의 원인으로 확인됨",
        "상류는 미확인이고 하류 설비 이상이 PH_FOCUS 이탈의 원인으로 확인됨",
    ],
)
def test_a_caveat_does_not_mask_a_separate_unsupported_assertion(statement):
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        fixture._finalize(fixture._draft(evidence_synthesis=statement))


def test_alternatives_and_followup_questions_do_not_claim_completed_checks():
    result = fixture._finalize(
        fixture._draft(
            alternative_hypotheses=[
                {"summary": "상류 원인 가능성", "lower_rank_reason": "상류 미조사"}
            ],
            verification_steps=["상류 FDC를 조회해 원인 여부를 확인한다"],
            limitations=["상류는 아직 확인하지 않음"],
        )
    )
    assert result.origin_assessment.compared.upstream == "NOT_AVAILABLE"


def test_confirmed_claim_in_a_followup_field_is_still_a_claim():
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        fixture._finalize(
            fixture._draft(
                verification_steps=["상류 설비가 PH_FOCUS 이탈 원인으로 확인됨"]
            )
        )


def test_observed_and_cited_direction_is_not_rejected_by_narrative_checks():
    route = fixture.fixture._route()
    current = fixture._fdc()
    adjacent = current.model_copy(
        update={
            "wafer": current.wafer.model_copy(
                update={
                    "lot_hist_id": "LH-ETCH",
                    "chamber_id": "EQP04-PM1",
                    "equipment_id": "EQP04",
                    "step_id": "CT-ETCH",
                }
            )
        }
    )
    fdc = [current, adjacent]
    result = finalize_hypothesis(
        fixture._draft(
            supporting_lot_hist_ids=["LH-PHOTO", "LH-ETCH"],
            evidence_synthesis="하류에서도 PH_FOCUS 이탈이 확인됨",
        ),
        fdc,
        route,
        build_diagnostic_snapshot(fdc, route),
        None,
        InvestigationEvidence(
            successful_calls=[
                {"tool_name": "get_fdc_summary", "input": {"lot_hist_id": "LH-ETCH"}}
            ]
        ),
    )
    assert result.origin_assessment.compared.downstream == "CHECKED"


def test_common_origin_cannot_be_asserted_only_in_prose_without_comparison():
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        fixture._finalize(fixture._draft(evidence_synthesis="설비 공통 원인이 확인됨"))
    limited = fixture._finalize(
        fixture._draft(
            evidence_synthesis="설비 공통 원인은 미확인으로 추가 확인 대상임"
        )
    )
    assert limited.origin_assessment.scope == "CURRENT_CHAMBER"


def _common(*, checked, basis):
    route = replace(
        fixture.fixture._route(),
        graph_evidence=(
            GraphRouteEvidence(
                chamber_id="EQP01-PM1",
                equipment_id="EQP01",
                model_code="MODEL-1",
                process_step_id="CT-PHOTO",
                upstream_process_step_ids=(),
                downstream_process_step_ids=("CT-ETCH",),
                sibling_chamber_ids=("EQP01-PM2",),
                relation_ids=("REL-1",),
                graph_revision="revision",
            ),
        ),
    )
    fdc = [fixture._fdc()]
    return finalize_hypothesis(
        fixture._draft(origin_claim={"scope": "EQUIPMENT_COMMON", "basis_refs": basis}),
        fdc,
        route,
        build_diagnostic_snapshot(fdc, route),
        None,
        InvestigationEvidence(
            successful_calls=[
                {
                    "tool_name": "get_chamber_parameter_history",
                    "input": {"chamber_id": "EQP01-PM2"},
                }
            ]
            if checked
            else []
        ),
    )


@pytest.mark.parametrize(
    "checked,basis",
    [
        (False, []),
        (True, []),
        (False, [{"namespace": "PARAMETER", "id": "PH_FOCUS"}]),
    ],
)
def test_equipment_common_requires_observed_sibling_and_valid_basis(checked, basis):
    with pytest.raises(ValueError, match="^ORIGIN_CLAIM_UNSUPPORTED$"):
        _common(checked=checked, basis=basis)


def test_equipment_common_with_sibling_check_and_basis_remains_accepted():
    result = _common(checked=True, basis=[{"namespace": "PARAMETER", "id": "PH_FOCUS"}])
    assert result.origin_assessment.scope == "EQUIPMENT_COMMON"
    assert result.origin_assessment.compared.sibling == "CHECKED"


def test_narrative_rejection_uses_existing_bounded_correction_and_usage():
    good = json.loads(generation._content())
    bad = {
        **good,
        "evidence_synthesis": "상류 설비 이상이 현재 압력 이탈의 원인으로 확인됨",
    }
    responses = iter([bad, good])
    captured = []

    def complete(messages, **kwargs):
        captured.append(messages)
        return generation._completion(json.dumps(next(responses)))

    outcome = generate_hypothesis(
        None,
        None,
        generation._docs(),
        generation._route(),
        completion_port=complete,
    )
    assert len(captured) == 2
    assert "ORIGIN_CLAIM_UNSUPPORTED" in captured[1][1]["content"]
    assert outcome.llm_usage.input_tokens == 20
    assert outcome.llm_usage.output_tokens == 8
