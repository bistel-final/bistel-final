"""V5-C-7.1 read feedback is ledger-derived, target-bound and same-run only."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent.read_feedback import (
    ReadFeedback,
    feedback_for_request,
    normalize_read_reason,
    summarize_read_history,
)
from app.agent.repository import RESERVED_ERROR_MSG, ToolCallRow
from app.common.enums import ActionCode, RunStatus, ToolCallStatus
from tests.support.agent_quality_development_cases import (
    CASES,
    SyntheticInvestigationTools,
)
from tests.unit import test_agent_react as fixture

TARGET = {"lot_id": "LOT001", "step_id": "STEP-UPSTREAM"}
TOOL = "get_metrology_result"


def _row(status="ERROR", reason="NOT_FOUND: target", *, request=None, run_id="RUN-1"):
    return ToolCallRow(
        tool_call_id="CALL-1",
        agent_run_id=run_id,
        call_seq=1,
        tool_name=TOOL,
        input=dict(TARGET if request is None else request),
        output={
            "ok": status == "SUCCESS",
            "reason": "" if status == "SUCCESS" else reason,
        },
        status=ToolCallStatus(status),
        latency_ms=1,
        called_at=datetime(2026, 1, 1, tzinfo=UTC),
        error_msg=None,
    )


def test_latest_success_recovers_without_erasing_failed_attempts():
    rows = [_row(), _row("TIMEOUT", "TIMEOUT: temporary"), _row("SUCCESS")]
    (feedback,) = summarize_read_history(rows, run_id="RUN-1")
    assert feedback.attempts == 3 and feedback.failed_attempts == 2
    assert feedback.last_status == "SUCCESS"
    assert feedback.reason_code is None and feedback.retryable is False
    assert feedback.last_outcome == "SUCCESS"
    assert (
        feedback_for_request((feedback,), TOOL, dict(reversed(list(TARGET.items()))))
        == feedback
    )
    (success_only,) = summarize_read_history([_row("SUCCESS"), _row("SUCCESS")])
    assert success_only.attempts == 2 and success_only.failed_attempts == 0


@pytest.mark.parametrize(
    "status,reason,expected,retryable",
    [
        ("ERROR", "NOT_FOUND: target", "NOT_FOUND", False),
        ("ERROR", "POLICY_REJECTED: target", "POLICY_REJECTED", False),
        ("TIMEOUT", "TIMEOUT: temporary", "TIMEOUT", True),
        ("ERROR", "DEPENDENCY_ERROR: temporary", "DEPENDENCY_ERROR", True),
        ("ERROR", "MODEL_NOT_READY: temporary", "MODEL_NOT_READY", True),
        ("ERROR", "unknown failure", None, True),
        ("TIMEOUT", "NOT_FOUND: contradictory", "TIMEOUT", True),
    ],
)
def test_only_verified_absence_or_policy_failure_disables_same_request(
    status, reason, expected, retryable
):
    (feedback,) = summarize_read_history([_row(status, reason)])
    assert feedback.reason_code == expected and feedback.retryable is retryable
    assert feedback.failed_attempts == 1


def test_target_and_run_boundaries_never_blacklist_other_queries():
    current = {"lot_id": "LOT001", "step_id": "STEP-CURRENT"}
    upstream = _row()
    other_run = _row("SUCCESS", run_id="RUN-2")
    with pytest.raises(ValueError, match="READ_FEEDBACK_RUN_SCOPE_MIXED"):
        summarize_read_history([upstream, other_run])
    (one,) = summarize_read_history([upstream, other_run], run_id="RUN-1")
    (two,) = summarize_read_history([upstream, other_run], run_id="RUN-2")
    assert one.reason_code == "NOT_FOUND"
    assert two.last_status == "SUCCESS"
    assert feedback_for_request((one,), TOOL, current) is None
    assert (
        feedback_for_request((one,), "get_fdc_summary", {"lot_hist_id": "LH-REP"})
        is None
    )


def test_normalized_dicts_use_tool_defaults_and_do_not_infer_unknown_error_absence():
    rows = [
        {
            "tool_name": "search_documents",
            "input": {"query": "P1 점검"},
            "status": "ERROR",
        },
        SimpleNamespace(
            tool_name="search_documents",
            input={"query": "P1 점검", "model_code": None, "top_k": 4},
            status=ToolCallStatus.SUCCESS,
        ),
    ]
    (feedback,) = summarize_read_history(rows)
    assert feedback.attempts == 2 and feedback.failed_attempts == 1
    assert feedback.request == {"query": "P1 점검", "model_code": None, "top_k": 4}
    assert (
        feedback_for_request((feedback,), "search_documents", {"query": "P1 점검"})
        == feedback
    )
    (unknown,) = summarize_read_history(rows[:1])
    assert unknown.reason_code is None and unknown.retryable is True
    assert (
        feedback_for_request((feedback,), "search_documents", {"query": "P2 점검"})
        is None
    )


@pytest.mark.parametrize("pending_status", ["PENDING", "ERROR"])
def test_pending_reservations_consume_attempts_but_are_not_completed_failures(
    pending_status,
):
    pending = {
        "tool_name": TOOL,
        "input": dict(TARGET),
        "status": pending_status,
        "output": None,
        "error_msg": RESERVED_ERROR_MSG,
    }
    assert summarize_read_history([pending]) == ()
    (feedback,) = summarize_read_history([_row(), pending])
    assert feedback.attempts == 2 and feedback.failed_attempts == 1
    assert feedback.last_status == "ERROR" and feedback.reason_code == "NOT_FOUND"
    (recovered,) = summarize_read_history([_row("SUCCESS"), pending])
    assert recovered.last_status == "SUCCESS" and recovered.failed_attempts == 0


def test_raw_reason_and_error_details_never_survive_feedback():
    private = "https://private.example sk-do-not-leak Bearer password=hidden"
    (feedback,) = summarize_read_history([_row(reason="NOT_FOUND: " + private)])
    assert private not in feedback.model_dump_json()
    assert "private.example" not in feedback.model_dump_json()
    assert feedback.reason_code == "NOT_FOUND"
    assert normalize_read_reason("ERROR", error_msg="NOT_FOUND") == "NOT_FOUND"
    assert normalize_read_reason("ERROR", error_msg="NOT_FOUND: " + private) is None
    assert (
        normalize_read_reason(
            "ERROR", output={"ok": False, "reason": "NOT_FOUND_EXTRA: " + private}
        )
        is None
    )
    assert (
        normalize_read_reason(
            "ERROR",
            output={"ok": True, "reason": "NOT_FOUND: " + private},
            error_msg="NOT_FOUND",
        )
        is None
    )
    assert (
        normalize_read_reason("TIMEOUT", error_msg="TOOL_RUNNER_SATURATED")
        == "TOOL_RUNNER_SATURATED"
    )
    assert (
        normalize_read_reason(
            "SUCCESS", output={"ok": False, "reason": "NOT_FOUND: target"}
        )
        is None
    )


def test_helper_ignores_non_read_or_invalid_targets_without_mutating_ledger():
    request = dict(TARGET)
    rows = [
        {
            "tool_name": "send_action",
            "input": {"action_id": "ACTION-1"},
            "status": "ERROR",
        },
        {"tool_name": TOOL, "input": {"step_id": "STEP-UPSTREAM"}, "status": "ERROR"},
        {"tool_name": TOOL, "input": request, "status": "ERROR", "output": None},
    ]
    (feedback,) = summarize_read_history(rows)
    feedback.request["step_id"] = "CHANGED"
    assert request == TARGET
    with pytest.raises(ValidationError):
        ReadFeedback(
            tool=TOOL,
            request=TARGET,
            attempts=1,
            last_status="ERROR",
            reason_code="NOT_FOUND",
            retryable=True,
        )
    with pytest.raises(ValidationError):
        ReadFeedback(
            tool=TOOL,
            request=TARGET,
            attempts=1,
            failed_attempts=1,
            last_status="SUCCESS",
            retryable=False,
        )


def test_graph_passes_actual_failed_target_to_selector_and_hypothesis(monkeypatch):
    case = CASES[2]
    tools = SyntheticInvestigationTools(case, budget_limit=10, read_limit=8)
    port = fixture.ScriptedReactPort(
        fixture._selection("get_metrology_result", metrology_candidate_id="M2"),
        fixture._selection("stop"),
    )
    ports = fixture.harness._Ports(action=ActionCode.MONITORING)
    ports.react_select = port
    seen = []
    original_hypothesis = ports.generate_hypothesis

    def hypothesis(*args):
        seen.append(args[-1])
        return original_hypothesis(*args)

    monkeypatch.setattr(ports, "generate_hypothesis", hypothesis)
    graph, _, _, finishes, _ = fixture.harness._build(
        monkeypatch,
        tools=tools,
        ports=ports,
        level_route=case.route(),
        diagnostic_wafer_refs=case.diagnostic_wafer_refs,
    )
    fixture.harness._invoke(graph, level=3)
    selector_feedback = feedback_for_request(
        port.contexts[-1].read_feedback, TOOL, TARGET
    )
    final_feedback = feedback_for_request(seen[0].read_feedback, TOOL, TARGET)
    assert selector_feedback == final_feedback
    assert selector_feedback.reason_code == "NOT_FOUND"
    assert selector_feedback.attempts == 1 and selector_feedback.retryable is False
    assert finishes[0][0] == RunStatus.COMPLETED.value
    assert tools.send_count == 0
