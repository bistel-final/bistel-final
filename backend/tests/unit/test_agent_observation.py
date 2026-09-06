from __future__ import annotations

import json

from app.agent import comparison, react
from app.agent.state import LlmUsage
from app.common.enums import AlarmSource
from app.common.schemas import AlarmRef
from scripts import observe_agent_justification as subject


def _outcome(next_: str) -> react.ReactSelectionOutcome:
    return react.ReactSelectionOutcome(
        selection=react.ReactSelection(
            rationale_summary="실패 뒤 다른 근거를 확인한다",
            next=next_,
            arguments=react.ReactArguments(),
        ),
        llm_usage=LlmUsage(
            model="fixture",
            prompt_version=react.REACT_PROMPT_VERSION,
            input_tokens=1,
            output_tokens=1,
        ),
    )


def test_counterfactual_fixture_does_not_contain_oracle_or_expected_behavior() -> None:
    payload = json.loads(subject.FIXTURE_PATH.read_text(encoding="utf-8"))
    raw = subject.FIXTURE_PATH.read_text(encoding="utf-8")
    assert "required_evidence_ids" not in raw
    assert "expected_action" not in raw
    assert [item["fixture_id"] for item in payload["fixtures"]] == list(
        comparison.FIXTURES
    )


def test_cf3_same_query_times_out_but_changed_query_recovers() -> None:
    boundary = subject.CounterfactualBoundary(engine=None)
    boundary.reset("CF-3")
    assert not boundary.documents({"query": "same"}).ok
    assert not boundary.documents({"query": "same"}).ok
    recovered = boundary.documents({"query": "changed after equipment"})
    assert recovered.ok
    assert recovered.hits[0].chunk_id == "DOC-CF3-RECOVERED"


def test_failure_projection_carries_observed_tool_and_status() -> None:
    context = react.ReactContext(
        lot_id="LOT",
        chamber_id="EQP-PM1",
        representative_alarm=AlarmRef(
            source=AlarmSource.TRACE,
            alarm_id="TA-1",
        ),
        member_alarm_count=1,
        r03_present=False,
        candidates=react.ReactCandidates(run_id="RUN-1"),
        fetched_fdc_candidate_ids=("F1",),
        observed_parameter_keys=(("CF3_BASE", 1),),
        fdc_observations=("CF3_BASE(oos=2,direction=ABOVE)",),
        equipment_observation=None,
        history_observations=(),
        metrology_observations=(),
        document_observations=(),
        remaining_tool_calls=3,
        remaining_steps=4,
        guard_rejections=0,
        recent_tool_events=("search_documents: 실패 TIMEOUT",),
    )
    step = subject._step_projections(
        [(context, _outcome("get_equipment_context"))],
        baseline_parameter_ids={"CF3_BASE"},
    )[0]
    assert step["observation_source"] == "TOOL_FAILURE"
    assert step["observed_tool"] == "search_documents"
    assert step["observed_status"] == "TIMEOUT"
    assert "FAILURE_ALTERNATE" in step["next_query_features"]
