"""`V5-C-2.1` Tool 감사 wrapper와 soft deadline 회귀."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from time import monotonic
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import tools as subject
from app.agent.repository import ToolBudgetCounts
from app.agent.tools import (
    AuditedToolExecutor,
    ThreadDeadlineRunner,
    ToolBoundary,
    ToolBoundaryError,
    ToolDeadlineExceeded,
    ToolRunnerSaturated,
)
from app.common.enums import ToolCallStatus
from app.common.tool_contracts import (
    ChamberParameterHistoryToolInput,
    DocumentSearchToolInput,
    MetrologyResultToolInput,
    SendActionToolInput,
)


class _DirectRunner:
    def call(self, fn: Any, payload: dict[str, Any], *, seconds: float) -> Any:
        assert seconds > 0
        return fn(payload)


class _TimeoutRunner:
    def call(self, fn: Any, payload: dict[str, Any], *, seconds: float) -> Any:
        raise ToolDeadlineExceeded


class _SaturatedRunner:
    def call(self, fn: Any, payload: dict[str, Any], *, seconds: float) -> Any:
        raise ToolRunnerSaturated


def _boundary(document: Any) -> ToolBoundary:
    return ToolBoundary(
        fdc_summary=lambda _payload: None,
        equipment_context=lambda _payload: None,
        document_search=document,
        chamber_parameter_history=document,
        metrology_result=document,
        send_action=document,
    )


def _harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    document: Any,
    runner: Any | None = None,
    finalize_error: Exception | None = None,
) -> tuple[AuditedToolExecutor, list[tuple[str, Any]]]:
    events: list[tuple[str, Any]] = []
    connection = object()

    @contextmanager
    def transactions() -> Any:
        events.append(("begin", connection))
        yield connection
        events.append(("commit", connection))

    def reserve(_connection: Any, **kwargs: Any) -> Any:
        events.append(("reserve", kwargs))
        return SimpleNamespace(tool_call_id="TOOL-1")

    def finalize(_connection: Any, **kwargs: Any) -> Any:
        events.append(("finalize", kwargs))
        if finalize_error is not None:
            raise finalize_error
        return SimpleNamespace()

    monkeypatch.setattr(subject, "reserve_tool_call", reserve)
    monkeypatch.setattr(subject, "finalize_tool_call", finalize)
    monkeypatch.setattr(
        subject,
        "count_tool_calls_for_budget",
        lambda _connection, _run_id: ToolBudgetCounts(
            total=0,
            by_tool={},
            pending_reservations=0,
            autonomy_level=2,
        ),
    )
    return (
        AuditedToolExecutor(
            transactions=transactions,
            boundary=_boundary(document),
            deadline_runner=_DirectRunner() if runner is None else runner,
            deadline_seconds=0.05,
            clock=iter((10.0, 10.125)).__next__,
        ),
        events,
    )


def _finalized(events: list[tuple[str, Any]]) -> dict[str, Any]:
    return next(value for name, value in events if name == "finalize")


def test_success_is_reserved_before_invoke_and_finalized_afterward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: {"ok": True, "reason": "", "hits": []},
    )
    result = executor.document_search(
        "RUN-1", DocumentSearchToolInput(query="EQP01-PM1")
    )
    assert result is not None and result.ok
    assert [name for name, _ in events] == [
        "begin",
        "reserve",
        "commit",
        "begin",
        "finalize",
        "commit",
    ]
    finalized = _finalized(events)
    assert finalized["status"] is ToolCallStatus.SUCCESS
    assert finalized["output"] == {"ok": True, "reason": "", "hits": []}
    assert finalized["error_msg"] is None
    assert finalized["latency_ms"] == 125


def test_history_context_is_not_written_to_the_canonical_audit_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked: list[dict[str, Any]] = []

    def history(payload: dict[str, Any]) -> dict[str, Any]:
        invoked.append(payload)
        return {
            "ok": False,
            "reason": "NOT_FOUND: current lot",
            "scope": None,
            "chamber_id": None,
            "parameter_id": None,
            "step_no": None,
            "current": None,
            "prior": [],
            "baseline": None,
            "trend": None,
            "comparison": None,
            "sample_count": None,
        }

    executor, events = _harness(monkeypatch, document=history)
    request = ChamberParameterHistoryToolInput(
        chamber_id="EQP01-PM1",
        parameter_id="PH_FOCUS",
        step_no=1,
        before="2026-09-04T09:00:00Z",
    )
    result = executor.chamber_parameter_history(
        "RUN-1",
        request,
        current_lot_id="LOT001",
        incident_step_id="CT-PHOTO",
        scope="CURRENT",
    )
    assert result is not None and not result.ok
    reserved = next(value for name, value in events if name == "reserve")
    assert "_context" not in reserved["input"]
    assert invoked[0]["_context"] == {
        "current_lot_id": "LOT001",
        "incident_step_id": "CT-PHOTO",
        "scope": "CURRENT",
    }


def test_metrology_tool_uses_the_common_audit_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: {
            "ok": False,
            "reason": "NOT_FOUND: metrology",
            "lot_id": None,
            "step_id": None,
            "results": [],
            "fail_count": None,
            "disclaimer": None,
        },
    )
    result = executor.metrology_result(
        "RUN-1",
        MetrologyResultToolInput(lot_id="LOT001", step_id="CT-PHOTO"),
    )
    assert result is not None and not result.ok
    reserved = next(value for name, value in events if name == "reserve")
    assert reserved["tool_name"] == "get_metrology_result"


def test_tool_failure_keeps_only_the_reason_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: {
            "ok": False,
            "reason": "NOT_FOUND: postgres://secret-host",
            "hits": [],
        },
    )
    result = executor.document_search("RUN-1", DocumentSearchToolInput(query="missing"))
    assert result is not None and not result.ok
    finalized = _finalized(events)
    assert finalized["status"] is ToolCallStatus.ERROR
    assert finalized["output"]["reason"] == "NOT_FOUND:"
    assert "secret-host" not in repr(finalized)
    assert finalized["error_msg"] == "NOT_FOUND"


def test_timeout_result_is_classified_without_discarding_the_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: {
            "ok": False,
            "reason": "TIMEOUT: dependency detail",
            "hits": [],
        },
    )
    result = executor.document_search("RUN-1", DocumentSearchToolInput(query="slow"))
    assert result is not None and not result.ok
    finalized = _finalized(events)
    assert finalized["status"] is ToolCallStatus.TIMEOUT
    assert finalized["output"]["reason"] == "TIMEOUT:"
    assert finalized["error_msg"] == "TIMEOUT"


def test_deadline_exception_has_no_output_and_a_sanitized_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: {"ok": True, "hits": []},
        runner=_TimeoutRunner(),
    )
    assert (
        executor.document_search("RUN-1", DocumentSearchToolInput(query="slow")) is None
    )
    finalized = _finalized(events)
    assert finalized["status"] is ToolCallStatus.TIMEOUT
    assert finalized["output"] is None
    assert finalized["error_msg"] == "TOOL_DEADLINE_EXCEEDED"


@pytest.mark.parametrize(
    ("runner", "reason", "audit_code"),
    [
        (
            _TimeoutRunner(),
            "TIMEOUT: SEND_ACTION_DEADLINE",
            "TOOL_DEADLINE_EXCEEDED",
        ),
        (
            _SaturatedRunner(),
            "TIMEOUT: TOOL_RUNNER_SATURATED",
            "TOOL_RUNNER_SATURATED",
        ),
    ],
)
def test_send_action_wrapper_timeout_returns_fixed_json_and_timeout_audit(
    monkeypatch: pytest.MonkeyPatch,
    runner: Any,
    reason: str,
    audit_code: str,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: pytest.fail("runner must not invoke the adapter"),
        runner=runner,
    )

    result = executor.send_action(
        "RUN-1",
        SendActionToolInput(action_id="ACT-1"),
    )

    assert result is not None and result.reason == reason
    assert result.action_id is None and result.deliveries == []
    finalized = _finalized(events)
    assert finalized["status"] is ToolCallStatus.TIMEOUT
    assert finalized["output"] is None
    assert finalized["error_msg"] == audit_code


def test_send_action_deadline_is_derived_above_the_webhook_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RecordingRunner:
        def call(
            self,
            fn: Any,
            payload: dict[str, Any],
            *,
            seconds: float,
        ) -> Any:
            captured.append(seconds)
            return fn(payload)

    captured: list[float] = []
    executor, _events = _harness(
        monkeypatch,
        document=lambda payload: {
            "ok": True,
            "action_id": "ACT-1",
            "effect_attempted": True,
            "effect_channel": "EMAIL",
            "deliveries": [
                {
                    "channel": "EMAIL",
                    "status": "SENT",
                    "sent": True,
                    "duplicate": False,
                }
            ],
        },
        runner=_RecordingRunner(),
    )

    executor.send_action("RUN-1", SendActionToolInput(action_id="ACT-1"))

    assert captured == [subject.N8N_WEBHOOK_TIMEOUT_SEC + 5.0]
    assert captured[0] > subject.N8N_WEBHOOK_TIMEOUT_SEC


def test_production_boundary_passes_the_graph_transaction_owner_to_send_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent import send_action as send_action_module

    settings = SimpleNamespace(
        marker="runtime-settings",
        N8N_WEBHOOK_TIMEOUT_SEC=25,
    )
    transactions = object()
    post = object()
    clock = object()
    captured: dict[str, Any] = {}

    def factory(
        actual_settings: Any,
        actual_transactions: Any,
        **kwargs: Any,
    ) -> Any:
        captured.update(
            settings=actual_settings,
            transactions=actual_transactions,
            kwargs=kwargs,
        )
        return lambda payload: payload

    monkeypatch.setattr(send_action_module, "build_send_action_tool", factory)

    boundary = ToolBoundary.production(
        settings=settings,
        transactions=transactions,  # type: ignore[arg-type]
        http_post=post,  # type: ignore[arg-type]
        clock=clock,  # type: ignore[arg-type]
    )

    assert captured == {
        "settings": settings,
        "transactions": transactions,
        "kwargs": {"http_post": post, "clock": clock},
    }
    assert boundary.send_action({"action_id": "ACT-1"}) == {"action_id": "ACT-1"}
    assert boundary.send_action_deadline_seconds == 30.0


def test_send_action_deadline_cannot_be_shorter_than_adapter_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: pytest.fail(
            "invalid deadline must fail before invoke"
        ),
    )
    object.__setattr__(executor, "send_action_deadline_seconds", 20.0)

    with pytest.raises(ToolBoundaryError) as exc:
        executor.send_action("RUN-1", SendActionToolInput(action_id="ACT-1"))

    assert exc.value.code == "SEND_ACTION_DEADLINE_INVALID"
    assert events == []


def test_production_boundary_without_delivery_settings_is_explicitly_unwired() -> None:
    boundary = ToolBoundary.production()

    with pytest.raises(ToolBoundaryError) as exc:
        boundary.send_action({"action_id": "ACT-1"})

    assert exc.value.code == "SEND_ACTION_NOT_WIRED"


def test_production_boundary_rejects_invalid_delivery_config_before_db() -> None:
    transaction_calls = 0

    @contextmanager
    def transactions() -> Any:
        nonlocal transaction_calls
        transaction_calls += 1
        yield object()

    with pytest.raises(ToolBoundaryError) as exc:
        ToolBoundary.production(
            settings=SimpleNamespace(N8N_WEBHOOK_TIMEOUT_SEC=30),
            transactions=transactions,
        )

    assert exc.value.code == "SEND_ACTION_CONFIG_INVALID"
    assert transaction_calls == 0


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (
            lambda payload: {"ok": True, "hits": [], "extra": "bad"},
            "TOOL_RESULT_INVALID",
        ),
        (
            lambda payload: (_ for _ in ()).throw(RuntimeError("dsn secret")),
            "TOOL_INVOCATION_ERROR",
        ),
    ],
)
def test_invalid_or_raised_tool_is_finalized_once(
    monkeypatch: pytest.MonkeyPatch,
    document: Any,
    expected: str,
) -> None:
    executor, events = _harness(monkeypatch, document=document)
    assert (
        executor.document_search("RUN-1", DocumentSearchToolInput(query="query"))
        is None
    )
    finalized = [value for name, value in events if name == "finalize"]
    assert len(finalized) == 1
    assert finalized[0]["error_msg"] == expected
    assert "secret" not in repr(finalized[0])


def test_output_shape_rejection_cannot_leak_a_validated_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: {"ok": True, "reason": "", "hits": []},
    )
    monkeypatch.setattr(subject, "RESERVED_TOOL_OUTPUT_KEYS", ("hits",))
    result = executor.document_search(
        "RUN-1", DocumentSearchToolInput(query="reserved")
    )
    assert result is None
    finalized = _finalized(events)
    assert finalized["status"] is ToolCallStatus.ERROR
    assert finalized["output"] is None
    assert finalized["error_msg"] == "RESERVED_OUTPUT_KEY"


def test_unknown_reason_prefix_is_a_contract_error_not_an_invocation_error() -> None:
    invalid = subject.DocumentSearchToolResult.model_construct(
        ok=False,
        reason="UNKNOWN: detail",
        hits=[],
    )

    with pytest.raises(ToolBoundaryError) as exc:
        subject._classify_result(invalid)

    assert exc.value.code == "REASON_PREFIX_INVALID"


def test_finalize_failure_is_sanitized_and_preserves_the_prior_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: (_ for _ in ()).throw(RuntimeError("dsn secret")),
        finalize_error=RuntimeError("postgresql://secret"),
    )

    with pytest.raises(ToolBoundaryError) as exc:
        executor.document_search("RUN-1", DocumentSearchToolInput(query="query"))

    assert exc.value.code == "TOOL_FINALIZE_FAILED"
    assert exc.value.prior_code == "TOOL_INVOCATION_ERROR"
    assert _finalized(events)["error_msg"] == "TOOL_INVOCATION_ERROR"
    assert "secret" not in repr(exc.value)


def test_missing_runner_fails_before_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: {"ok": True, "hits": []},
    )
    object.__setattr__(executor, "deadline_runner", None)
    with pytest.raises(ToolBoundaryError) as exc:
        executor.document_search("RUN-1", DocumentSearchToolInput(query="query"))
    assert exc.value.code == "RUNNER_NOT_WIRED"
    assert events == []


def test_budget_comes_from_committed_tool_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: {"ok": True, "hits": []},
    )
    monkeypatch.setattr(
        subject,
        "count_tool_calls_for_budget",
        lambda _connection, _run_id: ToolBudgetCounts(
            total=3,
            by_tool={"get_fdc_summary": 1, "search_documents": 2},
            pending_reservations=1,
            autonomy_level=2,
        ),
    )
    budget = executor.budget("RUN-1")
    assert budget.used == 3
    assert budget.by_tool == {"get_fdc_summary": 1, "search_documents": 2}
    assert budget.send_used == 0
    assert budget.pending_reservations == 1
    assert budget.source == "DB"
    assert [name for name, _ in events] == ["begin", "commit"]


def test_real_thread_runner_returns_at_the_soft_deadline_and_keeps_capacity() -> None:
    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=2)
    runner = ThreadDeadlineRunner(pool)
    try:
        started_at = monotonic()
        with pytest.raises(ToolDeadlineExceeded):
            runner.call(lambda payload: stop.wait(30), {}, seconds=0.03)
        assert monotonic() - started_at < 0.5
        assert runner.call(lambda payload: "next", {}, seconds=0.2) == "next"
    finally:
        stop.set()
        pool.shutdown(wait=True)


def test_a_tool_raised_timeout_is_not_mistaken_for_the_wrapper_deadline() -> None:
    pool = ThreadPoolExecutor(max_workers=1)
    runner = ThreadDeadlineRunner(pool)
    try:
        with pytest.raises(TimeoutError) as exc:
            runner.call(
                lambda payload: (_ for _ in ()).throw(TimeoutError("tool timeout")),
                {},
                seconds=0.2,
            )
        assert not isinstance(exc.value, ToolDeadlineExceeded)
    finally:
        pool.shutdown(wait=True)


def test_queued_call_is_distinct_from_an_execution_deadline() -> None:
    release = threading.Event()
    occupied = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    pool.submit(lambda: (occupied.set(), release.wait(30)))
    assert occupied.wait(1)
    runner = ThreadDeadlineRunner(pool)
    try:
        with pytest.raises(ToolRunnerSaturated):
            runner.call(lambda payload: "never", {}, seconds=0.03)
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_cancel_race_is_conservatively_an_execution_deadline() -> None:
    class _RacingFuture:
        def result(self, timeout: float) -> Any:
            raise TimeoutError

        def done(self) -> bool:
            return False

        def cancel(self) -> bool:
            return False

    class _RacingExecutor:
        def submit(self, fn: Any, payload: dict[str, Any]) -> Any:
            return _RacingFuture()

    runner = ThreadDeadlineRunner(_RacingExecutor())
    with pytest.raises(ToolDeadlineExceeded):
        runner.call(lambda payload: "raced", {}, seconds=0.03)


def test_raw_tool_timeout_keeps_the_existing_invocation_error_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events = _harness(
        monkeypatch,
        document=lambda payload: (_ for _ in ()).throw(TimeoutError("raw detail")),
    )
    assert (
        executor.document_search("RUN-1", DocumentSearchToolInput(query="raw-timeout"))
        is None
    )
    finalized = _finalized(events)
    assert finalized["status"] is ToolCallStatus.ERROR
    assert finalized["error_msg"] == "TOOL_INVOCATION_ERROR"
    assert "raw detail" not in repr(finalized)


def test_late_worker_completion_does_not_call_finalize_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        executor, events = _harness(
            monkeypatch,
            document=lambda payload: (
                stop.wait(30) or {"ok": True, "reason": "", "hits": []}
            ),
            runner=ThreadDeadlineRunner(pool),
        )
        assert (
            executor.document_search("RUN-1", DocumentSearchToolInput(query="slow"))
            is None
        )
        stop.set()
        pool.shutdown(wait=True)
        assert len([item for item in events if item[0] == "finalize"]) == 1
    finally:
        stop.set()
