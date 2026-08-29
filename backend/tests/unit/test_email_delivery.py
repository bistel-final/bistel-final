from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.agent import email_delivery as email
from app.agent.approval_store import EmailTransportError, approval_email_port
from app.agent.graph import AgentGraphDependencies, build_agent_graph
from app.agent.repository import ActionDeliveryRow, ActionHistoryRow
from app.agent.routing import GraphBoundary
from app.common.enums import (
    ActionCode,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
)
from tests.unit.test_n8n_workflows import (
    _load_workflows,
    _result_json,
    _run_code,
    _signed_webhook_item,
)

NOW = datetime(2026, 8, 29, tzinfo=UTC)
HASH = "a" * 64


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "N8N_WEBHOOK_URL": "http://localhost:5678/webhook/fdc-notify-email",
        "N8N_WEBHOOK_TIMEOUT_SEC": 30,
        "N8N_WEBHOOK_SECRET": " unit-test-secret ",
        "AGENT_EMAIL_RECIPIENTS": "operator@example.invalid",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _action(code: ActionCode = ActionCode.WARNING) -> ActionHistoryRow:
    return ActionHistoryRow(
        action_id="ACT-C43000000000001",
        lot_id="LOT-C43",
        chamber_id="EQP01-PM-C01",
        action_code=code,
        reason="pressure drift",
        approval_required=code is ActionCode.EQP_HOLD,
        approval_status=(
            ApprovalStatus.PENDING
            if code is ActionCode.EQP_HOLD
            else ApprovalStatus.AUTO
        ),
        approved_by=None if code is ActionCode.EQP_HOLD else "system",
        approved_at=None if code is ActionCode.EQP_HOLD else NOW,
        created_at=NOW,
    )


def _delivery(status: DeliveryStatus = DeliveryStatus.WAITING) -> ActionDeliveryRow:
    return ActionDeliveryRow(
        action_id="ACT-C43000000000001",
        channel=DeliveryChannel.EMAIL,
        status=status,
        request_hash=HASH,
        attempt_count=0 if status is DeliveryStatus.WAITING else 1,
        provider_message_id=None,
        started_at=None if status is DeliveryStatus.WAITING else NOW,
        completed_at=None,
        last_error=None,
        result=None,
    )


class _Transactions:
    def __init__(self) -> None:
        self.open = 0
        self.calls = 0

    @contextmanager
    def __call__(self):
        self.calls += 1
        self.open += 1
        try:
            yield object()
        finally:
            self.open -= 1


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transactions: _Transactions,
    action: ActionHistoryRow | None = None,
    claimed: ActionDeliveryRow | None = None,
    settled_status: DeliveryStatus = DeliveryStatus.SENDING,
) -> dict[str, Any]:
    action = action or _action()
    initial = _delivery()
    claimed = _delivery(DeliveryStatus.SENDING) if claimed is None else claimed
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        email, "get_action_history", lambda connection, action_id: action
    )
    monkeypatch.setattr(
        email,
        "get_action_delivery",
        lambda connection, **kwargs: initial,
    )
    monkeypatch.setattr(
        email, "begin_email_delivery", lambda connection, **kwargs: claimed
    )
    monkeypatch.setattr(
        email,
        "get_approval_request",
        lambda connection, approval_id: SimpleNamespace(
            action_id=action.action_id,
            agent_run_id="RUN-C43000000000001",
            status=ApprovalStatus.PENDING,
        ),
    )
    monkeypatch.setattr(
        email,
        "get_run_action",
        lambda connection, agent_run_id: SimpleNamespace(action_id=action.action_id),
    )

    def settle(connection: Any, **kwargs: Any) -> ActionDeliveryRow:
        observed["settle"] = kwargs
        return _delivery(settled_status)

    monkeypatch.setattr(email, "settle_email_delivery", settle)
    observed["transactions"] = transactions
    return observed


@pytest.mark.parametrize(
    "overrides",
    [
        {"N8N_WEBHOOK_URL": "http://localhost:5678/webhook/other"},
        {"N8N_WEBHOOK_URL": "http://user@localhost:5678/webhook/fdc-notify-email"},
        {"N8N_WEBHOOK_URL": "http:///webhook/fdc-notify-email"},
        {"N8N_WEBHOOK_URL": "http://localhost:5678/webhook/fdc-notify-email?q=1"},
        {"N8N_WEBHOOK_URL": "http://localhost:bad/webhook/fdc-notify-email"},
        {"N8N_WEBHOOK_URL": "http://local host/webhook/fdc-notify-email"},
        {"N8N_WEBHOOK_URL": "http://localhost/webhook/fdc-notify-email?"},
        {"N8N_WEBHOOK_TIMEOUT_SEC": 24},
        {"N8N_WEBHOOK_SECRET": "   "},
        {"AGENT_EMAIL_RECIPIENTS": "bad-address"},
        {"AGENT_EMAIL_RECIPIENTS": "good@example.com\r\nBcc:x@example.com"},
        {
            "AGENT_EMAIL_RECIPIENTS": ",".join(
                f"user{index}@example.com" for index in range(11)
            )
        },
    ],
)
def test_invalid_config_stops_before_db_and_http(
    overrides: dict[str, Any],
) -> None:
    transactions = _Transactions()
    http_calls = 0

    def post(*args: Any, **kwargs: Any) -> Any:
        nonlocal http_calls
        http_calls += 1
        return SimpleNamespace(status_code=200)

    with pytest.raises(email.EmailDeliveryConfigError) as exc:
        email.production_ports(_settings(**overrides), transactions, http_post=post)
    assert exc.value.code == "EMAIL_DELIVERY_CONFIG_INVALID"
    assert transactions.calls == 0
    assert http_calls == 0


def test_recipient_boundary_deduplicates_and_allows_ten() -> None:
    raw = ",".join(f"user{index}@example.com" for index in range(10))
    config = email.load_email_delivery_config(
        _settings(AGENT_EMAIL_RECIPIENTS=raw + ",USER0@example.com")
    )
    assert len(config.recipients) == 10
    assert "unit-test-secret" not in repr(config)
    assert "user0@example.com" not in repr(config)
    assert "fdc-notify-email" not in repr(config)


def test_backend_raw_body_and_signature_pass_the_real_wf2_javascript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transactions = _Transactions()
    observed = _wire(monkeypatch, transactions=transactions)

    def post(url: str, **kwargs: Any) -> Any:
        assert transactions.open == 0
        observed.update(url=url, **kwargs)
        return SimpleNamespace(status_code=200)

    service = email.production_ports(
        _settings(), transactions, http_post=post, clock=lambda: 1_800_000_000
    ).service
    result = service.send_warning("ACT-C43000000000001")
    assert result.outcome is email.EmailDeliveryOutcome.ACCEPTED
    assert transactions.calls == 2

    raw = observed["content"]
    payload = json.loads(raw)
    assert set(payload) == {
        "action_code",
        "action_id",
        "approval_id",
        "chamber_id",
        "channel",
        "email_kind",
        "lot_id",
        "recipients",
        "request_hash",
        "schema",
        "summary",
    }
    assert payload["request_hash"] == HASH
    assert payload["email_kind"] == "WARNING_NOTIFY"
    assert payload["approval_id"] is None

    workflow = _load_workflows()["WF2-notify-email.json"]
    webhook_item = _signed_webhook_item(
        raw,
        secret="unit-test-secret",
        timestamp=1_800_000_000,
    )
    verified = _result_json(
        _run_code(
            workflow,
            "Verify Email Auth",
            input_item=webhook_item,
            env={"N8N_WEBHOOK_SECRET": "unit-test-secret"},
        )
    )
    assert verified["auth_ok"] is True
    validated = _result_json(
        _run_code(
            workflow,
            "Validate Email Payload",
            input_item={"json": verified},
        )
    )
    assert validated == {"schema_ok": True, "payload": payload}


def test_approval_summary_contains_decision_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transactions = _Transactions()
    _wire(monkeypatch, transactions=transactions, action=_action(ActionCode.EQP_HOLD))
    captured: dict[str, Any] = {}

    def post(url: str, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    service = email.production_ports(_settings(), transactions, http_post=post).service
    service.send_approval("ACT-C43000000000001", "APR-C43000000000001")
    payload = json.loads(captured["content"])
    assert payload["approval_id"] == "APR-C43000000000001"
    assert payload["email_kind"] == "APPROVAL_REQUEST"
    for expected in (
        "APR-C43000000000001",
        "LOT-C43",
        "EQP01-PM-C01",
        "pressure drift",
    ):
        assert expected in payload["summary"]


@pytest.mark.parametrize(
    ("approval_action_id", "run_action_id"),
    [
        ("ACT-C43000000000099", "ACT-C43000000000001"),
        ("ACT-C43000000000001", "ACT-C43000000000099"),
    ],
)
def test_approval_identity_fails_before_claim_and_http(
    monkeypatch: pytest.MonkeyPatch,
    approval_action_id: str,
    run_action_id: str,
) -> None:
    transactions = _Transactions()
    _wire(monkeypatch, transactions=transactions, action=_action(ActionCode.EQP_HOLD))
    monkeypatch.setattr(
        email,
        "get_approval_request",
        lambda connection, approval_id: SimpleNamespace(
            action_id=approval_action_id,
            agent_run_id="RUN-C43000000000001",
            status=ApprovalStatus.APPROVED,
        ),
    )
    monkeypatch.setattr(
        email,
        "get_run_action",
        lambda connection, agent_run_id: SimpleNamespace(action_id=run_action_id),
    )
    monkeypatch.setattr(
        email,
        "begin_email_delivery",
        lambda *args, **kwargs: pytest.fail("invalid identity must not claim"),
    )
    service = email.production_ports(
        _settings(),
        transactions,
        http_post=lambda *args, **kwargs: pytest.fail("must not call HTTP"),
    ).service
    with pytest.raises(email.EmailDeliveryContractError) as exc:
        service.send_approval("ACT-C43000000000001", "APR-C43000000000001")
    assert exc.value.code == "APPROVAL_IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    "approval_status",
    [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED],
)
def test_terminal_approval_is_noop_before_claim_and_http(
    monkeypatch: pytest.MonkeyPatch,
    approval_status: ApprovalStatus,
) -> None:
    transactions = _Transactions()
    _wire(monkeypatch, transactions=transactions, action=_action(ActionCode.EQP_HOLD))
    monkeypatch.setattr(
        email,
        "get_approval_request",
        lambda connection, approval_id: SimpleNamespace(
            action_id="ACT-C43000000000001",
            agent_run_id="RUN-C43000000000001",
            status=approval_status,
        ),
    )
    monkeypatch.setattr(
        email,
        "begin_email_delivery",
        lambda *args, **kwargs: pytest.fail("terminal approval must not claim"),
    )
    service = email.production_ports(
        _settings(),
        transactions,
        http_post=lambda *args, **kwargs: pytest.fail("must not call HTTP"),
    ).service

    result = service.send_approval("ACT-C43000000000001", "APR-C43000000000001")

    assert result.outcome is email.EmailDeliveryOutcome.NOOP
    assert transactions.calls == 1


@pytest.mark.parametrize(
    ("status", "failure_code", "terminal"),
    [
        (401, "WEBHOOK_401", True),
        (422, "WEBHOOK_422", True),
        (502, "WEBHOOK_502", False),
        (429, "WEBHOOK_429", False),
    ],
)
def test_http_status_matrix_is_passed_to_repository(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    failure_code: str,
    terminal: bool,
) -> None:
    transactions = _Transactions()
    observed = _wire(
        monkeypatch,
        transactions=transactions,
        settled_status=(DeliveryStatus.FAILED if terminal else DeliveryStatus.SENDING),
    )
    service = email.production_ports(
        _settings(),
        transactions,
        http_post=lambda *args, **kwargs: SimpleNamespace(status_code=status),
    ).service
    result = service.send_warning("ACT-C43000000000001")
    assert observed["settle"]["failure_code"] == failure_code
    assert observed["settle"]["terminal_failure"] is terminal
    expected = (
        email.EmailDeliveryOutcome.FAILED
        if terminal
        else email.EmailDeliveryOutcome.UNCERTAIN
    )
    assert result.outcome is expected


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (httpx.ReadTimeout("slow"), "WEBHOOK_TIMEOUT"),
        (httpx.ConnectError("down"), "WEBHOOK_UNREACHABLE"),
    ],
)
def test_transport_exception_is_sanitized_after_second_commit(
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    code: str,
) -> None:
    transactions = _Transactions()
    observed = _wire(monkeypatch, transactions=transactions)

    def post(*args: Any, **kwargs: Any) -> Any:
        raise raised

    ports = email.production_ports(_settings(), transactions, http_post=post)
    with pytest.raises(EmailTransportError) as exc:
        ports.notify_email("ACT-C43000000000001")
    assert exc.value.code == code
    assert observed["settle"]["failure_code"] == code
    assert transactions.calls == 2
    assert transactions.open == 0


@pytest.mark.parametrize("terminal", [DeliveryStatus.SENT, DeliveryStatus.FAILED])
@pytest.mark.parametrize("transport_result", [502, "timeout"])
def test_callback_terminal_status_wins_over_http_result(
    monkeypatch: pytest.MonkeyPatch,
    terminal: DeliveryStatus,
    transport_result: int | str,
) -> None:
    transactions = _Transactions()
    _wire(
        monkeypatch,
        transactions=transactions,
        settled_status=terminal,
    )

    def post(*args: Any, **kwargs: Any) -> Any:
        if transport_result == "timeout":
            raise httpx.ReadTimeout("late callback")
        return SimpleNamespace(status_code=transport_result)

    ports = email.production_ports(
        _settings(),
        transactions,
        http_post=post,
    )
    if terminal is DeliveryStatus.SENT:
        assert ports.notify_email("ACT-C43000000000001") is None
    else:
        with pytest.raises(EmailTransportError) as exc:
            ports.notify_email("ACT-C43000000000001")
        assert exc.value.code == "EMAIL_DELIVERY_FAILED"


def test_zero_row_claim_never_calls_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transactions = _Transactions()
    _wire(monkeypatch, transactions=transactions, claimed=None)
    monkeypatch.setattr(
        email, "begin_email_delivery", lambda connection, **kwargs: None
    )
    http_calls = 0

    def post(*args: Any, **kwargs: Any) -> Any:
        nonlocal http_calls
        http_calls += 1

    result = email.production_ports(
        _settings(), transactions, http_post=post
    ).service.send_warning("ACT-C43000000000001")
    assert result.outcome is email.EmailDeliveryOutcome.NOOP
    assert transactions.calls == 1
    assert http_calls == 0


def test_request_hash_change_rolls_back_claim_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transactions = _Transactions()
    changed = replace(_delivery(DeliveryStatus.SENDING), request_hash="b" * 64)
    _wire(monkeypatch, transactions=transactions, claimed=changed)
    http_calls = 0

    def post(*args: Any, **kwargs: Any) -> Any:
        nonlocal http_calls
        http_calls += 1

    service = email.production_ports(_settings(), transactions, http_post=post).service
    with pytest.raises(email.EmailDeliveryContractError) as exc:
        service.send_warning("ACT-C43000000000001")
    assert exc.value.code == "DELIVERY_REQUEST_HASH_CHANGED"
    assert transactions.calls == 1
    assert http_calls == 0


def test_production_ports_can_be_injected_into_the_existing_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transactions = _Transactions()
    _wire(monkeypatch, transactions=transactions)
    delivery_ports = email.production_ports(
        _settings(),
        transactions,
        http_post=lambda *args, **kwargs: SimpleNamespace(status_code=200),
    )

    def unused(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("graph assembly must not invoke ports")

    ports = SimpleNamespace(
        generate_hypothesis=unused,
        decide_action=unused,
        persist_action=unused,
        notify_email=delivery_ports.notify_email,
        approval_email=approval_email_port(
            transactions, delivery_ports.approval_sender
        ),
        hitl_interrupt=unused,
        publish_mes=unused,
        writeback_result=unused,
        cancel_mes=unused,
    )
    graph = build_agent_graph(
        AgentGraphDependencies(
            transactions=transactions,
            tools=SimpleNamespace(),
            routing_graph=GraphBoundary(lambda value: None, lambda value: None),
            ports=ports,
        )
    )
    assert {node for node in graph.get_graph().nodes} >= {
        "notify_email",
        "approval_email",
    }
