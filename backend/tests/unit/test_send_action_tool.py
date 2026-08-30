"""`V5-C-4.6-1` 저장 plan 실행 Tool의 단위 계약."""

from __future__ import annotations

import ast
import inspect
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import graph as graph_module
from app.agent import send_action as subject
from app.agent.email_delivery import EmailDeliveryConfigError
from app.agent.mes_delivery import MesDeliveryConfigError
from app.agent.repository import (
    ActionBundle,
    ActionDeliveryRow,
    RepositoryConflict,
    RepositoryNotFound,
)
from app.agent.send_action import SendActionService
from app.common.enums import (
    ActionCode,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
)
from app.common.tool_contracts import SendActionToolResult

HASH = "a" * 64


def _row(channel: DeliveryChannel, status: DeliveryStatus) -> ActionDeliveryRow:
    return ActionDeliveryRow(
        action_id="ACT-1",
        channel=channel,
        status=status,
        request_hash=HASH,
        attempt_count=0,
        provider_message_id=None,
        started_at=None,
        completed_at=None,
        last_error=None,
        result=None,
    )


def _bundle(
    action: ActionCode,
    *,
    approval: ApprovalStatus | None = None,
) -> ActionBundle:
    hold = action is ActionCode.EQP_HOLD
    return ActionBundle(
        action_id="ACT-1",
        action_code=action,
        approval_id="APR-1" if hold else None,
        approval_status=approval,
        approval_agent_run_id="RUN-1" if hold else None,
        delivery_channels=(
            (DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK)
            if hold
            else (DeliveryChannel.EMAIL,)
        ),
    )


class _Email:
    def __init__(self, rows: list[ActionDeliveryRow]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str | None]] = []
        self.result: Any = None
        self.error: Exception | None = None

    def _send(self, kind: str, approval_id: str | None) -> Any:
        self.calls.append((kind, approval_id))
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        self.rows[0] = replace(self.rows[0], status=DeliveryStatus.SENT)
        return subject.EmailDeliveryResult(
            "ACT-1",
            subject.EmailDeliveryOutcome.SENT,
        )

    def send_warning(self, action_id: str) -> Any:
        return self._send("warning", None)

    def send_approval(self, action_id: str, approval_id: str) -> Any:
        return self._send("approval", approval_id)


class _Mes:
    def __init__(self, rows: list[ActionDeliveryRow]) -> None:
        self.rows = rows
        self.calls: list[str] = []
        self.result: Any = None

    def publish(self, action_id: str) -> Any:
        self.calls.append(action_id)
        if self.result is not None:
            return self.result
        self.rows[1] = replace(self.rows[1], status=DeliveryStatus.SENT)
        return subject.MesDeliveryResult(
            "ACT-1",
            subject.MesDeliveryOutcome.SENT,
            published=True,
        )


def _service(
    monkeypatch: pytest.MonkeyPatch,
    bundle: ActionBundle,
    rows: list[ActionDeliveryRow],
) -> tuple[SendActionService, _Email, _Mes, list[str]]:
    transactions: list[str] = []

    @contextmanager
    def transaction() -> Any:
        transactions.append("begin")
        yield object()
        transactions.append("commit")

    monkeypatch.setattr(subject, "get_action_bundle", lambda *_args: bundle)
    monkeypatch.setattr(subject, "list_action_deliveries", lambda *_args: list(rows))
    email = _Email(rows)
    mes = _Mes(rows)
    return (
        SendActionService(
            transactions=transaction,
            email=email,  # type: ignore[arg-type]
            mes=mes,  # type: ignore[arg-type]
        ),
        email,
        mes,
        transactions,
    )


def test_warning_waiting_executes_email_and_returns_full_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING)]
    service, email, mes, transactions = _service(
        monkeypatch,
        _bundle(ActionCode.WARNING),
        rows,
    )

    result = service.invoke({"action_id": "ACT-1"})

    assert result == SendActionToolResult.model_validate(
        {
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
        }
    )
    assert email.calls == [("warning", None)]
    assert mes.calls == []
    assert transactions == ["begin", "commit", "begin", "commit"]


def test_pending_hold_sends_approval_and_keeps_blocked_mes_in_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING),
        _row(DeliveryChannel.MES_MOCK, DeliveryStatus.BLOCKED),
    ]
    service, email, mes, _ = _service(
        monkeypatch,
        _bundle(ActionCode.EQP_HOLD, approval=ApprovalStatus.PENDING),
        rows,
    )

    result = service.invoke({"action_id": "ACT-1"})

    assert result.ok is True
    assert [(item.channel, item.status) for item in result.deliveries] == [
        (DeliveryChannel.EMAIL, DeliveryStatus.SENT),
        (DeliveryChannel.MES_MOCK, DeliveryStatus.BLOCKED),
    ]
    assert email.calls == [("approval", "APR-1")]
    assert mes.calls == []
    assert result.effect_attempted is True
    assert result.effect_channel is DeliveryChannel.EMAIL


def test_approved_hold_sends_mes_and_marks_preexisting_email_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _row(DeliveryChannel.EMAIL, DeliveryStatus.SENT),
        _row(DeliveryChannel.MES_MOCK, DeliveryStatus.WAITING),
    ]
    service, email, mes, _ = _service(
        monkeypatch,
        _bundle(ActionCode.EQP_HOLD, approval=ApprovalStatus.APPROVED),
        rows,
    )

    result = service.invoke({"action_id": "ACT-1"})

    assert result.ok is True
    assert result.deliveries[0].duplicate is True
    assert result.deliveries[0].sent is False
    assert result.deliveries[1].sent is True
    assert result.effect_attempted is True
    assert result.effect_channel is DeliveryChannel.MES_MOCK
    assert email.calls == []
    assert mes.calls == ["ACT-1"]


@pytest.mark.parametrize(
    ("outcome", "stored_status", "sent", "duplicate"),
    [
        (
            subject.EmailDeliveryOutcome.ACCEPTED,
            DeliveryStatus.SENDING,
            False,
            False,
        ),
        (
            subject.EmailDeliveryOutcome.FAILED,
            DeliveryStatus.FAILED,
            False,
            False,
        ),
        (
            subject.EmailDeliveryOutcome.NOOP,
            DeliveryStatus.SENT,
            False,
            True,
        ),
        (
            subject.EmailDeliveryOutcome.NOOP,
            DeliveryStatus.SENDING,
            False,
            False,
        ),
    ],
)
def test_adapter_outcome_projects_exact_effect_flags(
    monkeypatch: pytest.MonkeyPatch,
    outcome: subject.EmailDeliveryOutcome,
    stored_status: DeliveryStatus,
    sent: bool,
    duplicate: bool,
) -> None:
    rows = [_row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING)]
    service, email, _, _ = _service(
        monkeypatch,
        _bundle(ActionCode.WARNING),
        rows,
    )

    def invoke(_action_id: str) -> subject.EmailDeliveryResult:
        email.calls.append(("warning", None))
        rows[0] = replace(rows[0], status=stored_status)
        return subject.EmailDeliveryResult("ACT-1", outcome)

    email.send_warning = invoke  # type: ignore[method-assign]

    result = service.invoke({"action_id": "ACT-1"})

    assert result.ok is True
    assert result.deliveries[0].status is stored_status
    assert result.deliveries[0].sent is sent
    assert result.deliveries[0].duplicate is duplicate
    assert result.effect_attempted is (outcome is not subject.EmailDeliveryOutcome.NOOP)
    assert result.effect_channel is (
        None if outcome is subject.EmailDeliveryOutcome.NOOP else DeliveryChannel.EMAIL
    )


@pytest.mark.parametrize(
    "status",
    [
        DeliveryStatus.SENDING,
        DeliveryStatus.SENT,
        DeliveryStatus.FAILED,
        DeliveryStatus.UNKNOWN,
    ],
)
def test_warning_existing_state_is_no_call_success(
    monkeypatch: pytest.MonkeyPatch,
    status: DeliveryStatus,
) -> None:
    rows = [_row(DeliveryChannel.EMAIL, status)]
    service, email, mes, transactions = _service(
        monkeypatch,
        _bundle(ActionCode.WARNING),
        rows,
    )

    result = service.invoke({"action_id": "ACT-1"})

    assert result.ok is True
    assert result.deliveries[0].status is status
    assert result.deliveries[0].sent is False
    assert result.deliveries[0].duplicate is (status is DeliveryStatus.SENT)
    assert result.effect_attempted is False
    assert result.effect_channel is None
    assert email.calls == [] and mes.calls == []
    assert transactions == ["begin", "commit"]


@pytest.mark.parametrize(
    ("approval", "email_status", "mes_status"),
    [
        (ApprovalStatus.PENDING, DeliveryStatus.UNKNOWN, DeliveryStatus.BLOCKED),
        (ApprovalStatus.APPROVED, DeliveryStatus.SENT, DeliveryStatus.SENDING),
        (ApprovalStatus.APPROVED, DeliveryStatus.FAILED, DeliveryStatus.FAILED),
        (ApprovalStatus.APPROVED, DeliveryStatus.UNKNOWN, DeliveryStatus.UNKNOWN),
        (ApprovalStatus.REJECTED, DeliveryStatus.WAITING, DeliveryStatus.CANCELED),
        (ApprovalStatus.REJECTED, DeliveryStatus.SENDING, DeliveryStatus.CANCELED),
        (ApprovalStatus.REJECTED, DeliveryStatus.SENT, DeliveryStatus.CANCELED),
    ],
)
def test_hold_existing_states_are_no_call_full_plan_success(
    monkeypatch: pytest.MonkeyPatch,
    approval: ApprovalStatus,
    email_status: DeliveryStatus,
    mes_status: DeliveryStatus,
) -> None:
    rows = [
        _row(DeliveryChannel.EMAIL, email_status),
        _row(DeliveryChannel.MES_MOCK, mes_status),
    ]
    service, email, mes, _ = _service(
        monkeypatch,
        _bundle(ActionCode.EQP_HOLD, approval=approval),
        rows,
    )

    result = service.invoke({"action_id": "ACT-1"})

    assert result.ok is True
    assert len(result.deliveries) == 2
    assert result.effect_attempted is False
    assert result.effect_channel is None
    assert email.calls == [] and mes.calls == []


@pytest.mark.parametrize(
    ("bundle", "rows"),
    [
        (
            _bundle(ActionCode.EQP_HOLD, approval=ApprovalStatus.PENDING),
            [
                _row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING),
                _row(DeliveryChannel.MES_MOCK, DeliveryStatus.WAITING),
            ],
        ),
        (
            _bundle(ActionCode.EQP_HOLD, approval=ApprovalStatus.APPROVED),
            [
                _row(DeliveryChannel.EMAIL, DeliveryStatus.SENDING),
                _row(DeliveryChannel.MES_MOCK, DeliveryStatus.WAITING),
            ],
        ),
        (
            replace(
                _bundle(ActionCode.WARNING),
                approval_id="APR-1",
                approval_status=ApprovalStatus.PENDING,
                approval_agent_run_id="RUN-1",
            ),
            [_row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING)],
        ),
    ],
)
def test_invalid_plan_is_rejected_before_adapter(
    monkeypatch: pytest.MonkeyPatch,
    bundle: ActionBundle,
    rows: list[ActionDeliveryRow],
) -> None:
    service, email, mes, _ = _service(monkeypatch, bundle, rows)

    result = service.invoke({"action_id": "ACT-1"})

    assert result.ok is False
    assert result.reason.startswith("POLICY_REJECTED:")
    assert result.action_id is None and result.deliveries == []
    assert email.calls == [] and mes.calls == []


def test_missing_action_and_zero_plan_use_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING)]
    service, *_ = _service(monkeypatch, _bundle(ActionCode.WARNING), rows)
    monkeypatch.setattr(
        subject,
        "get_action_bundle",
        lambda *_args: (_ for _ in ()).throw(RepositoryNotFound("ACTION_NOT_FOUND")),
    )
    missing = service.invoke({"action_id": "ACT-1"})
    assert missing.reason.startswith("NOT_FOUND:")

    service, *_ = _service(monkeypatch, _bundle(ActionCode.WARNING), [])
    empty = service.invoke({"action_id": "ACT-1"})
    assert empty.reason.startswith("NOT_FOUND:")


@pytest.mark.parametrize(
    ("reason_code", "prefix"),
    [
        ("WEBHOOK_TIMEOUT", "TIMEOUT:"),
        ("WEBHOOK_UNREACHABLE", "DEPENDENCY_ERROR:"),
        ("WEBHOOK_502", "DEPENDENCY_ERROR:"),
    ],
)
def test_uncertain_outcome_returns_empty_failure_payload(
    monkeypatch: pytest.MonkeyPatch,
    reason_code: str,
    prefix: str,
) -> None:
    rows = [_row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING)]
    service, email, _, _ = _service(
        monkeypatch,
        _bundle(ActionCode.WARNING),
        rows,
    )
    email.result = subject.EmailDeliveryResult(
        "ACT-1",
        subject.EmailDeliveryOutcome.UNCERTAIN,
        reason_code=reason_code,
    )

    result = service.invoke({"action_id": "ACT-1"})

    assert result.reason.startswith(prefix)
    assert result.action_id is None and result.deliveries == []


def test_repository_hash_race_is_idempotency_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING)]
    service, email, _, _ = _service(
        monkeypatch,
        _bundle(ActionCode.WARNING),
        rows,
    )
    email.error = RepositoryConflict("DELIVERY_REQUEST_HASH_MISMATCH")

    result = service.invoke({"action_id": "ACT-1"})

    assert result.reason == "IDEMPOTENCY_CONFLICT: DELIVERY_REQUEST_HASH_MISMATCH"
    assert result.deliveries == []


def test_unexpected_repository_bug_is_not_masked_as_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING)]
    service, *_ = _service(monkeypatch, _bundle(ActionCode.WARNING), rows)
    monkeypatch.setattr(
        subject,
        "get_action_bundle",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("programming bug")),
    )

    with pytest.raises(RuntimeError, match="programming bug"):
        service.invoke({"action_id": "ACT-1"})


def test_unexpected_adapter_bug_is_not_masked_as_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(DeliveryChannel.EMAIL, DeliveryStatus.WAITING)]
    service, email, *_ = _service(
        monkeypatch,
        _bundle(ActionCode.WARNING),
        rows,
    )
    email.error = RuntimeError("programming bug")

    with pytest.raises(RuntimeError, match="programming bug"):
        service.invoke({"action_id": "ACT-1"})


def test_invalid_factory_config_fails_fast_without_db() -> None:
    transaction_calls = 0

    @contextmanager
    def transactions() -> Any:
        nonlocal transaction_calls
        transaction_calls += 1
        yield object()

    with pytest.raises((EmailDeliveryConfigError, MesDeliveryConfigError)):
        subject.build_send_action_tool(SimpleNamespace(), transactions)
    assert transaction_calls == 0


def test_graph_delivery_nodes_do_not_call_legacy_delivery_ports() -> None:
    tree = ast.parse(inspect.getsource(graph_module.build_agent_graph))
    attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ports"
    }
    assert {"notify_email", "approval_email", "publish_mes"}.isdisjoint(attributes)


def test_send_action_service_does_not_reserve_audit_rows_directly() -> None:
    tree = ast.parse(inspect.getsource(subject))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "reserve_tool_call" not in names | attributes
