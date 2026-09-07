"""Actual failure metadata reaches selection without changing U10 artifact rows."""

import json

import pytest

from app.agent import react
from app.agent.release_artifacts import canonical_json
from app.agent.u10_read_adapter import ReadAdapter
from app.agent.u10_read_execution import ReadObservation, ReadRequest, ReadSession
from app.common import tool_contracts as dto
from tests.unit.test_agent_u10_comparison import ids
from tests.unit.test_agent_u10_observations import context
from tests.unit.test_agent_u10_react_execution import outcome, run
from tests.unit.test_agent_u10_read_adapter import Immediate, ports
from tests.unit.test_agent_u10_read_execution import Clock, inventory, success


def absent():
    return ReadObservation(status="ERROR", evidence_ids=ids(), reason_code="NOT_FOUND")


def test_no_data_reselection_is_blocked_after_unchanged_fixed_retry():
    seen = []

    def select(context):
        seen.append(context)
        if len(seen) <= 2:
            return outcome("get_metrology_result", metrology_candidate_id="M1")
        return outcome("stop")

    result = run(select, lambda *_: absent())
    assert result.stop_reason == "LLM_STOP"
    assert [call.retry for call in result.calls] == [0, 1]
    assert all("reason_code" not in call.model_dump() for call in result.calls)
    assert any(
        step.guard_code == "REACT_GUARD_TARGET_REPEATED" for step in result.trace
    )
    feedback = seen[1].read_feedback[0]
    assert feedback.attempts == 2 and feedback.reason_code == "NOT_FOUND"
    assert feedback.retryable is False
    assert "NOT_FOUND" in json.dumps(react.build_react_select_messages(seen[1]))


def test_unknown_error_does_not_mean_no_data_and_recovery_remains_allowed():
    responses = iter([ReadObservation(status="ERROR", evidence_ids=ids()), success()])
    seen = []

    def select(context):
        seen.append(context)
        return (
            outcome("get_metrology_result", metrology_candidate_id="M1")
            if len(seen) == 1
            else outcome("stop")
        )

    result = run(select, lambda *_: next(responses))
    assert [call.status for call in result.calls] == ["ERROR", "SUCCESS"]
    assert seen[1].read_feedback[0].last_status == "SUCCESS"
    assert seen[1].read_feedback[0].reason_code is None


@pytest.mark.parametrize("reason", ("NOT_FOUND", "TIMEOUT", "DEPENDENCY_ERROR"))
def test_adapter_keeps_scoped_failure_outside_success_evidence(reason):
    state = context()
    adapter = ReadAdapter(
        state,
        ports(
            lambda _: dto.fail(
                dto.FdcSummaryToolResult, f"{reason}: secret-must-not-leak"
            )
        ),
        Immediate(),
    )
    response = adapter("get_fdc_summary", {"lot_hist_id": "LH-REP"})
    assert response.reason_code == reason
    assert state.results("get_fdc_summary") == []
    feedback = state.hypothesis_inputs()["investigation"].read_feedback
    assert feedback[0].request == {"lot_hist_id": "LH-REP"}
    assert feedback[0].reason_code == reason
    assert b"secret-must-not-leak" not in canonical_json(feedback[0])


def test_private_session_history_cannot_be_mutated_and_old_artifact_keys_remain():
    session = ReadSession(inventory(), lambda *_: absent(), clock_ns=Clock())
    calls = session.execute(
        ReadRequest(slot="METROLOGY", arguments={"lot_id": "L", "step_id": "S"})
    )
    history = session.tool_history
    history[0].input["lot_id"] = "changed"
    assert session.tool_history[0].input["lot_id"] == "L"
    assert calls[0].model_dump().keys() == {
        "slot",
        "tool",
        "selection",
        "retry",
        "input_digest",
        "status",
        "latency_ms",
        "evidence_ids",
    }
