"""`V5-C-3.3` HITL service의 DB 밖 계약 회귀."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import approval_store as subject
from app.agent.repository import RepositoryConflict
from app.agent.state import DeliveryPlan
from app.common.enums import (
    ApprovalStatus,
    Decision,
    DeliveryChannel,
    DeliveryStatus,
)

THREAD_ID = "11111111-2222-3333-4444-555555555555"


@contextmanager
def _transactions() -> Any:
    yield object()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ApprovalStatus.APPROVED, Decision.APPROVE),
        (ApprovalStatus.REJECTED, Decision.REJECT),
    ],
)
def test_hitl_decision_port_reads_only_terminal_decisions(
    monkeypatch: pytest.MonkeyPatch,
    status: ApprovalStatus,
    expected: Decision,
) -> None:
    monkeypatch.setattr(
        subject,
        "get_approval_request",
        lambda *_args: SimpleNamespace(status=status),
    )
    assert subject.hitl_decision_port(_transactions)("APR-1") is expected


def test_pending_decision_never_becomes_an_implicit_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject,
        "get_approval_request",
        lambda *_args: SimpleNamespace(status=ApprovalStatus.PENDING),
    )
    with pytest.raises(RepositoryConflict) as caught:
        subject.hitl_decision_port(_transactions)("APR-1")
    assert caught.value.code == "APPROVAL_STILL_PENDING"


def test_cancel_mes_port_returns_the_database_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject,
        "list_action_deliveries",
        lambda *_args: [
            SimpleNamespace(
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.WAITING,
            ),
            SimpleNamespace(
                channel=DeliveryChannel.MES_MOCK,
                status=DeliveryStatus.CANCELED,
            ),
        ],
    )
    assert subject.cancel_mes_port(_transactions)("ACT-1") == (
        DeliveryPlan(
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.WAITING,
        ),
        DeliveryPlan(
            channel=DeliveryChannel.MES_MOCK,
            status=DeliveryStatus.CANCELED,
        ),
    )


@pytest.mark.parametrize("status", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
def test_late_approval_email_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    status: ApprovalStatus,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        subject,
        "get_approval_request",
        lambda *_args: SimpleNamespace(
            action_id="ACT-1",
            status=status,
        ),
    )
    port = subject.approval_email_port(
        _transactions,
        lambda action_id, approval_id: calls.append((action_id, approval_id)),
    )
    port("ACT-1", "APR-1")
    assert calls == []


def test_pending_approval_email_calls_the_sender_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        subject,
        "get_approval_request",
        lambda *_args: SimpleNamespace(
            action_id="ACT-1",
            status=ApprovalStatus.PENDING,
        ),
    )
    port = subject.approval_email_port(
        _transactions,
        lambda action_id, approval_id: calls.append((action_id, approval_id)),
    )
    port("ACT-1", "APR-1")
    assert calls == [("ACT-1", "APR-1")]


class _Scalar:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value


class _LockConnection:
    def __init__(
        self,
        *,
        acquire: bool = True,
        release: bool = True,
        release_error: bool = False,
    ) -> None:
        self.acquire = acquire
        self.release = release
        self.release_error = release_error
        self.invalidated = False
        self.isolations: list[str] = []
        self.statements: list[str] = []

    def execution_options(self, *, isolation_level: str) -> _LockConnection:
        self.isolations.append(isolation_level)
        return self

    def execute(self, statement: Any, params: Any) -> _Scalar:
        sql = str(statement)
        self.statements.append(sql)
        if "pg_advisory_unlock" in sql:
            if self.release_error:
                raise RuntimeError("fixture raw error")
            return _Scalar(self.release)
        return _Scalar(self.acquire)

    def invalidate(self) -> None:
        self.invalidated = True


def _factory(connection: _LockConnection) -> Any:
    @contextmanager
    def open_connection() -> Any:
        yield connection

    return open_connection


def test_resume_mutex_uses_autocommit_and_releases_the_session_lock() -> None:
    connection = _LockConnection()
    with subject._resume_mutex(_factory(connection), THREAD_ID):
        pass
    assert connection.isolations == ["AUTOCOMMIT"]
    assert len(connection.statements) == 2
    assert connection.invalidated is False


def test_resume_mutex_refuses_a_concurrent_owner_without_invoking() -> None:
    connection = _LockConnection(acquire=False)
    with pytest.raises(subject.HitlResumeError) as caught:
        with subject._resume_mutex(_factory(connection), THREAD_ID):
            pytest.fail("mutex를 얻지 못했는데 body를 실행했습니다")
    assert caught.value.code == "RESUME_ALREADY_RUNNING"


@pytest.mark.parametrize("release_error", [False, True])
def test_resume_mutex_invalidates_an_uncertain_unlock(release_error: bool) -> None:
    connection = _LockConnection(release=False, release_error=release_error)
    with pytest.raises(subject.HitlResumeError) as caught:
        with subject._resume_mutex(_factory(connection), THREAD_ID):
            pass
    assert caught.value.code == "RESUME_LOCK_LEAKED"
    assert connection.invalidated is True


def test_interrupted_predicate_requires_phase_approval_and_no_terminal_error() -> None:
    class Graph:
        def get_state(self, _config: Any) -> Any:
            return SimpleNamespace(
                values={"approval_id": "APR-1", "terminal_error": None},
                next=("hitl_interrupt",),
            )

    assert subject.is_approval_interrupted(Graph(), THREAD_ID) is True


@pytest.mark.parametrize(
    ("values", "next_nodes"),
    [
        ({"approval_id": "APR-1", "terminal_error": None}, ("approval_email",)),
        ({"approval_id": None, "terminal_error": None}, ("hitl_interrupt",)),
        ({"approval_id": "APR-1", "terminal_error": object()}, ("hitl_interrupt",)),
    ],
)
def test_interrupted_predicate_rejects_partial_signals(
    values: dict[str, Any], next_nodes: tuple[str, ...]
) -> None:
    graph = SimpleNamespace(
        get_state=lambda _config: SimpleNamespace(values=values, next=next_nodes)
    )
    assert subject.is_approval_interrupted(graph, THREAD_ID) is False
