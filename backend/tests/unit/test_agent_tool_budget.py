"""`V5-C-2.2` Tool 호출 예산·전송 예약 정책 회귀."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import tools as subject
from app.agent.repository import ToolBudgetCounts
from app.agent.tools import AuditedToolExecutor, ToolBoundary, ToolBudgetBlocked


def _counts(
    by_tool: dict[str, int],
    *,
    pending: int = 0,
) -> ToolBudgetCounts:
    return ToolBudgetCounts(
        total=sum(by_tool.values()),
        by_tool=by_tool,
        pending_reservations=pending,
    )


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
