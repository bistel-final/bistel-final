"""`V5-C-2.2` Tool 호출 예산·전송 예약 정책 회귀."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import repository as repo
from app.agent import tools as subject
from app.agent.repository import (
    ToolBudgetCounts,
    ToolCallRow,
    tool_call_consumes_budget,
)
from app.agent.tools import AuditedToolExecutor, ToolBoundary, ToolBudgetBlocked
from app.common.enums import ToolCallStatus


def _counts(
    by_tool: dict[str, int],
    *,
    pending: int = 0,
    autonomy_level: int = 2,
) -> ToolBudgetCounts:
    return ToolBudgetCounts(
        total=sum(by_tool.values()),
        by_tool=by_tool,
        pending_reservations=pending,
        autonomy_level=autonomy_level,
    )


def _call(
    *,
    tool_name: str = "send_action",
    status: ToolCallStatus = ToolCallStatus.SUCCESS,
    output: dict[str, Any] | None = None,
    error_msg: str | None = None,
) -> ToolCallRow:
    return ToolCallRow(
        tool_call_id="TOOL-1",
        agent_run_id="RUN-1",
        call_seq=1,
        tool_name=tool_name,
        input=None,
        output=output,
        status=status,
        latency_ms=0,
        called_at=datetime.now(UTC),
        error_msg=error_msg,
    )


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (
            _call(
                output={
                    "ok": True,
                    "effect_attempted": False,
                    "effect_channel": None,
                }
            ),
            False,
        ),
        (
            _call(
                output={
                    "ok": True,
                    "effect_attempted": True,
                    "effect_channel": "EMAIL",
                }
            ),
            True,
        ),
        (_call(output={"ok": True}), True),
        (
            _call(
                status=ToolCallStatus.ERROR,
                output=None,
                error_msg="CALL_RESERVED_NOT_COMPLETED",
            ),
            True,
        ),
        (_call(tool_name="search_documents", output={"ok": True}), True),
    ],
)
def test_only_explicit_successful_send_action_no_call_is_budget_free(
    call: ToolCallRow,
    expected: bool,
) -> None:
    assert tool_call_consumes_budget(call) is expected


@pytest.mark.parametrize(
    ("counts", "tool_name", "expected"),
    [
        (
            _counts(
                {
                    "get_fdc_summary": 3,
                    "get_equipment_context": 3,
                    "send_action": 2,
                }
            ),
            "send_action",
            "TOOL_BUDGET_EXHAUSTED",
        ),
        (
            _counts({"get_fdc_summary": 4}),
            "get_fdc_summary",
            "TOOL_RETRY_EXHAUSTED",
        ),
        (
            _counts(
                {
                    "get_fdc_summary": 2,
                    "get_equipment_context": 2,
                    "search_documents": 2,
                }
            ),
            "search_documents",
            "TOOL_BUDGET_RESERVED",
        ),
        (
            _counts({"get_fdc_summary": 2, "send_action": 2}),
            "send_action",
            "TOOL_SEND_ACTION_LIMIT",
        ),
    ],
)
def test_budget_policy_uses_fixed_precedence_and_limits(
    counts: ToolBudgetCounts,
    tool_name: str,
    expected: str,
) -> None:
    assert subject._budget_block_code(counts, tool_name) == expected


def test_send_usage_never_releases_a_non_send_slot() -> None:
    counts = _counts(
        {
            "get_fdc_summary": 2,
            "get_equipment_context": 2,
            "search_documents": 2,
            "send_action": 1,
        }
    )
    assert (
        subject._budget_block_code(counts, "search_documents") == "TOOL_BUDGET_RESERVED"
    )
    assert subject._budget_block_code(counts, "send_action") is None


def test_level_three_uses_eight_read_and_two_send_slots() -> None:
    reads = _counts(
        {
            "get_fdc_summary": 2,
            "get_equipment_context": 2,
            "search_documents": 4,
        },
        autonomy_level=3,
    )
    assert subject._budget_block_code(reads, "search_documents") == (
        "TOOL_BUDGET_RESERVED"
    )
    assert subject._budget_block_code(reads, "send_action") is None
    snapshot = subject._budget_snapshot(reads)
    assert snapshot.max_calls == 10
    assert snapshot.send_budget == 2


@pytest.mark.parametrize("autonomy_level", [1, 2])
def test_level_one_and_two_keep_six_read_and_two_send_slots(
    autonomy_level: int,
) -> None:
    reads = _counts(
        {
            "get_fdc_summary": 2,
            "get_equipment_context": 2,
            "search_documents": 2,
        },
        autonomy_level=autonomy_level,
    )
    assert subject._budget_block_code(reads, "search_documents") == (
        "TOOL_BUDGET_RESERVED"
    )
    assert subject._budget_snapshot(reads).max_calls == 8


def test_unknown_run_autonomy_level_fails_closed() -> None:
    counts = _counts({}, autonomy_level=4)
    with pytest.raises(subject.ToolBoundaryError, match="AUTONOMY_LEVEL_INVALID"):
        subject._budget_snapshot(counts)


@pytest.mark.parametrize("level", [None, True, 2.0, "2", 0, 4, "MISSING"])
def test_budget_repository_rejects_schema_drift_before_reading_calls(level):
    row = (
        SimpleNamespace()
        if level == "MISSING"
        else SimpleNamespace(autonomy_level=level)
    )
    calls = []

    def execute(statement, parameters):
        calls.append(statement)
        return SimpleNamespace(one_or_none=lambda: row)

    connection = SimpleNamespace(in_transaction=lambda: True, execute=execute)
    with pytest.raises(repo.RepositoryContractError, match="AUTONOMY_LEVEL_INVALID"):
        repo.count_tool_calls_for_budget(connection, "RUN-1")
    assert calls == [repo._LOCK_RUN]


@pytest.mark.parametrize("level", [1, 2, 3])
def test_budget_repository_uses_the_locked_run_level(level):
    calls = []

    def execute(statement, parameters):
        calls.append(statement)
        return SimpleNamespace(
            one_or_none=lambda: SimpleNamespace(autonomy_level=level), all=lambda: []
        )

    connection = SimpleNamespace(in_transaction=lambda: True, execute=execute)
    counts = repo.count_tool_calls_for_budget(connection, "RUN-1")
    assert counts.autonomy_level == level
    assert calls == [repo._LOCK_RUN, repo._SELECT_TOOL_CALLS]


def test_budget_snapshot_has_no_implicit_level_two_default():
    with pytest.raises(TypeError, match="autonomy_level"):
        ToolBudgetCounts(total=0, by_tool={}, pending_reservations=0)


def test_sentinel_is_preserved_in_the_snapshot_and_total_policy() -> None:
    counts = _counts(
        {
            "get_fdc_summary": 3,
            "get_equipment_context": 3,
            "send_action": 2,
        },
        pending=2,
    )
    snapshot = subject._budget_snapshot(counts)
    assert snapshot.pending_reservations == 2
    assert snapshot.used == 8
    assert subject._budget_block_code(counts, "send_action") == (
        "TOOL_BUDGET_EXHAUSTED"
    )


def test_blocked_call_never_reaches_the_reservation_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()
    events: list[str] = []

    @contextmanager
    def transactions() -> Any:
        events.append("begin")
        yield connection

    counts = _counts(
        {
            "get_fdc_summary": 2,
            "get_equipment_context": 2,
            "search_documents": 2,
        }
    )
    monkeypatch.setattr(
        subject,
        "count_tool_calls_for_budget",
        lambda _connection, _run_id: counts,
    )

    def reserve(*_args: Any, **_kwargs: Any) -> Any:
        events.append("reserve")
        return SimpleNamespace(tool_call_id="TOOL-NEVER")

    monkeypatch.setattr(subject, "reserve_tool_call", reserve)
    executor = AuditedToolExecutor(
        transactions=transactions,
        boundary=ToolBoundary(
            fdc_summary=lambda payload: None,
            equipment_context=lambda payload: None,
            document_search=lambda payload: None,
        ),
        deadline_runner=None,
    )

    with pytest.raises(ToolBudgetBlocked) as exc:
        executor._reserve_within_budget(
            agent_run_id="RUN-1",
            tool_name="search_documents",
            request={"query": "fixture"},
        )

    assert exc.value.code == "TOOL_BUDGET_RESERVED"
    assert exc.value.budget.used == 6
    assert events == ["begin"]
