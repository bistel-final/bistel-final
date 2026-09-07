"""C/Common V5-C-7.1: run-bound public limits and legacy trace compatibility."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.agent import public_read_model as subject
from app.agent.investigation_budget import RUN_PROFILE_KEY
from app.agent.public_schemas import PublicInvestigationBudget
from app.agent.repository import PublicToolCallRecord, RepositoryContractError
from app.common.enums import RunStatus, ToolCallStatus
from tests.unit.test_agent_react_public import CASES, _internal
from tests.unit.test_agent_screen_read_model import _run


def _trace(count):
    observed, stopped = _internal(CASES[2]["react_trace"])
    return [
        *({**observed, "seq": seq} for seq in range(1, count)),
        {**stopped, "seq": count},
    ]


def _record(*, level=3, evidence=None, status=RunStatus.COMPLETED):
    return replace(
        _run(),
        autonomy_level=level,
        run_evidence=evidence,
        status=status,
        action_id=None,
        approval_id=None,
        predicted_fault_code=None,
        confidence=None,
        prediction_evidence=None,
    )


@pytest.mark.parametrize(
    "profile,cap",
    [(None, 11), ("PRODUCTION_WIDE_V1", 29)],
)
def test_terminal_trace_bound_belongs_to_saved_profile(profile, cap):
    evidence = {"react_trace": _trace(cap)}
    if profile is not None:
        evidence[RUN_PROFILE_KEY] = profile
    projected = subject._public_trace(_record(evidence=evidence))
    assert projected["trace_state"] == "AVAILABLE"
    assert len(projected["react_trace"]) == cap
    assert projected["react_trace"][-1].seq == cap
    with pytest.raises(RepositoryContractError, match="PUBLIC_REACT_TRACE_INVALID"):
        subject._public_trace(
            _record(evidence={**evidence, "react_trace": _trace(cap + 1)})
        )


def test_old_level_three_does_not_adopt_current_wide_default():
    budget = subject._public_investigation_budget(_record())
    assert budget.model_dump() == {
        "profile_id": "STANDARD",
        "read_cap": 8,
        "selector_cap": 10,
        "same_tool_cap": 4,
        "guard_rejection_cap": 2,
        "send_budget": 2,
        "total_call_cap": 10,
    }


@pytest.mark.parametrize("level", [1, 2])
def test_fixed_levels_keep_no_level_three_budget(level):
    assert subject._public_investigation_budget(_record(level=level)) is None


@pytest.mark.parametrize("value", [None, "UNKNOWN", "STANDARD", "DEVELOPMENT_WIDE", {}])
@pytest.mark.parametrize("status", [RunStatus.RUNNING, RunStatus.COMPLETED])
def test_invalid_persisted_profile_never_falls_back_or_leaks(value, status):
    with pytest.raises(
        RepositoryContractError, match="^PUBLIC_INVESTIGATION_BUDGET_INVALID$"
    ):
        subject._public_trace(_record(evidence={RUN_PROFILE_KEY: value}, status=status))


@pytest.mark.parametrize("level", [1, 2])
def test_wide_marker_on_wrong_level_is_rejected(level):
    with pytest.raises(
        RepositoryContractError, match="PUBLIC_INVESTIGATION_BUDGET_INVALID"
    ):
        subject._public_trace(
            _record(level=level, evidence={RUN_PROFILE_KEY: "PRODUCTION_WIDE_V1"})
        )


def test_pending_reads_persisted_budget_but_never_uncommitted_trace():
    record = _record(
        evidence={
            RUN_PROFILE_KEY: "PRODUCTION_WIDE_V1",
            "react_trace": [{"raw_query": "PRIVATE"}],
        },
        status=RunStatus.RUNNING,
    )
    assert subject._public_investigation_budget(record).read_cap == 24
    assert subject._public_trace(record) == {
        "trace_state": "PENDING",
        "react_trace": [],
    }


@pytest.mark.parametrize(
    "level,profile,expected",
    [(1, None, 3), (2, None, 3), (3, None, 5), (3, "PRODUCTION_WIDE_V1", 21)],
)
def test_public_remaining_counts_success_error_timeout_but_not_send(
    monkeypatch, level, profile, expected
):
    evidence = None if profile is None else {RUN_PROFILE_KEY: profile}
    record = replace(
        _record(level=level, evidence=evidence),
        tools=tuple(
            PublicToolCallRecord(tool_name="get_fdc_summary", status=status)
            for status in (
                ToolCallStatus.SUCCESS,
                ToolCallStatus.ERROR,
                ToolCallStatus.TIMEOUT,
            )
        )
        + (
            PublicToolCallRecord(
                tool_name="send_action", status=ToolCallStatus.SUCCESS
            ),
        ),
    )
    monkeypatch.setattr(subject, "get_agent_run_public", lambda *_: record)
    monkeypatch.setattr(subject, "list_run_alarms", lambda *_: [])
    monkeypatch.setattr(subject, "_stored_tool_evidence", lambda *_, **__: [])
    detail = subject.load_public_agent_run_detail(object(), "RUN-1")
    assert detail.remaining_read_calls == expected
    assert (detail.investigation_budget is None) == (level != 3)
    assert RUN_PROFILE_KEY not in detail.model_dump()


@pytest.mark.parametrize(
    "field,value",
    [
        ("read_cap", 23),
        ("selector_cap", 27),
        ("same_tool_cap", 7),
        ("send_budget", 1),
        ("guard_rejection_cap", 1),
        ("total_call_cap", 25),
    ],
)
def test_public_limits_cannot_be_customized(field, value):
    budget = subject._public_investigation_budget(
        _record(evidence={RUN_PROFILE_KEY: "PRODUCTION_WIDE_V1"})
    )
    with pytest.raises(ValidationError, match="PUBLIC_INVESTIGATION_BUDGET_INVALID"):
        PublicInvestigationBudget.model_validate({**budget.model_dump(), field: value})
