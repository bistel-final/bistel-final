from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.agent import mes_delivery as mes
from app.agent.repository import (
    ActionDeliveryRow,
    ActionHistoryRow,
    ApprovalRequestRow,
    MesDeliveryClaim,
)
from app.common.enums import (
    ActionCode,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
)
from app.mes_mock import consumer as mock
from tests.unit.test_n8n_workflows import _load_workflows, _result_json, _run_code

NOW = datetime(2026, 8, 29, 12, 34, 56, 123456, tzinfo=UTC)
ACTION_ID = "ACT-c45000000000001"
HASH = "a" * 64


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "N8N_WF3_URL": "http://localhost:5678/webhook/fdc-mes-hold",
        "N8N_WEBHOOK_TIMEOUT_SEC": 30,
        "N8N_WEBHOOK_SECRET": " unit-test-secret ",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _action() -> ActionHistoryRow:
    return ActionHistoryRow(
        action_id=ACTION_ID,
        lot_id="LOT-C45",
        chamber_id="EQP01-PM-C01",
        action_code=ActionCode.EQP_HOLD,
        reason="strict R03",
        approval_required=True,
        approval_status=ApprovalStatus.APPROVED,
        approved_by="operator-1",
        approved_at=NOW,
        created_at=NOW,
    )


def _delivery(status: DeliveryStatus = DeliveryStatus.SENDING) -> ActionDeliveryRow:
    return ActionDeliveryRow(
        action_id=ACTION_ID,
        channel=DeliveryChannel.MES_MOCK,
        status=status,
        request_hash=HASH,
        attempt_count=1,
        provider_message_id=None,
        started_at=NOW,
        completed_at=None,
        last_error=None,
        result=None,
    )


def _claim() -> MesDeliveryClaim:
    return MesDeliveryClaim(
        delivery=_delivery(),
        action=_action(),
        approval=ApprovalRequestRow(
            approval_id="APR-c45000000000001",
            action_id=ACTION_ID,
            agent_run_id="RUN-c45000000000001",
            status=ApprovalStatus.APPROVED,
            requested_at=NOW,
            decided_by="operator-1",
            decided_at=NOW,
            decision_comment=None,
        ),
        equipment_id="EQP01",
    )


class _Transactions:
    def __init__(self) -> None:
        self.calls = 0
        self.open = 0

    @contextmanager
    def __call__(self):
        self.calls += 1
        self.open += 1
        try:
            yield object()
        finally:
            self.open -= 1


class _Response:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    transactions: _Transactions,
    *,
    claimed: MesDeliveryClaim | None = None,
    settled_status: DeliveryStatus = DeliveryStatus.SENDING,
) -> dict[str, Any]:
    observed: dict[str, Any] = {}
    resolved_claim = _claim() if claimed is None else claimed
    monkeypatch.setattr(
        mes,
        "begin_mes_delivery",
        lambda connection, **kwargs: resolved_claim,
    )

    def settle(connection: Any, **kwargs: Any) -> ActionDeliveryRow:
        observed["settle"] = kwargs
        return _delivery(settled_status)

    monkeypatch.setattr(mes, "settle_mes_webhook", settle)
    monkeypatch.setattr(
        mes,
        "list_action_deliveries",
        lambda connection, action_id: [_delivery(settled_status)],
    )
    observed["transactions"] = transactions
    return observed


@pytest.mark.parametrize(
    "overrides",
    [
        {"N8N_WF3_URL": "http://localhost:5678/webhook/other"},
        {"N8N_WF3_URL": "http://user@localhost:5678/webhook/fdc-mes-hold"},
        {"N8N_WF3_URL": "http://localhost:bad/webhook/fdc-mes-hold"},
        {"N8N_WF3_URL": "http://localhost/webhook/fdc-mes-hold?q=1"},
        {"N8N_WEBHOOK_TIMEOUT_SEC": 24},
        {"N8N_WEBHOOK_SECRET": "   "},
    ],
)
def test_invalid_config_stops_before_db_and_http(overrides: dict[str, Any]) -> None:
    transactions = _Transactions()
    calls = 0

    def post(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1

    with pytest.raises(mes.MesDeliveryConfigError) as exc:
        mes.production_ports(_settings(**overrides), transactions, http_post=post)
    assert exc.value.code == "MES_DELIVERY_CONFIG_INVALID"
    assert transactions.calls == 0
    assert calls == 0


def test_exact_payload_signature_and_wf3_calculation_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transactions = _Transactions()
    captured: dict[str, Any] = {}
    _wire(monkeypatch, transactions)

    def post(url: str, **kwargs: Any) -> _Response:
        assert transactions.open == 0
        captured.update(url=url, **kwargs)
        return _Response(200, {"ok": True, "published": True})

    result = mes.production_ports(
        _settings(), transactions, http_post=post, clock=lambda: 1_800_000_000
    ).service.publish(ACTION_ID)
    assert result.outcome is mes.MesDeliveryOutcome.ACCEPTED
    payload = json.loads(captured["content"])
    assert set(payload) == {
        "action_code",
        "action_id",
        "chamber_id",
        "command",
        "decided_at",
        "decided_by",
        "equipment_id",
        "event_id",
        "occurred_at",
        "request_hash",
        "schema",
    }
    assert payload["event_id"] == mes.event_id_for(ACTION_ID, HASH)
    assert payload["occurred_at"] == "2026-08-29T12:34:56.123456+00:00"

    workflow = _load_workflows()["WF3-mes-hold.json"]
    item = {
        "json": {"headers": captured["headers"]},
        "binary": {"data": {"data": base64.b64encode(captured["content"]).decode()}},
    }
    verified = _result_json(
        _run_code(
            workflow,
            "Verify MES Auth",
            input_item=item,
            env={"N8N_WEBHOOK_SECRET": "unit-test-secret"},
            now_seconds=1_800_000_000,
        )
    )
    validated = _result_json(
        _run_code(
            workflow,
            "Validate MES Payload",
            input_item={"json": verified},
        )
    )
    assert validated == {"schema_ok": True, "payload": payload}


def test_event_identity_is_stable_and_tuple_sensitive() -> None:
    first = mes.event_id_for(ACTION_ID, HASH)
    assert first == mes.event_id_for(ACTION_ID, HASH)
    assert first != mes.event_id_for("ACT-c45000000000002", HASH)
    assert first != mes.event_id_for(ACTION_ID, "b" * 64)
    assert mes.EVENT_ID_PATTERN.fullmatch(first)


@pytest.mark.parametrize(
    ("status", "payload", "settled", "outcome", "failure", "terminal"),
    [
        (
            200,
            {"ok": True, "published": True},
            DeliveryStatus.SENDING,
            mes.MesDeliveryOutcome.ACCEPTED,
            None,
            False,
        ),
        (
            200,
            {"ok": True, "published": False},
            DeliveryStatus.FAILED,
            mes.MesDeliveryOutcome.FAILED,
            "WF3_CALLBACK_STATE_MISSING",
            False,
        ),
        (
            200,
            {"ok": True},
            DeliveryStatus.SENDING,
            mes.MesDeliveryOutcome.UNCERTAIN,
            "WF3_RESPONSE_INVALID",
            False,
        ),
        (
            502,
            {"error": "CALLBACK_FAILED"},
            DeliveryStatus.SENDING,
            mes.MesDeliveryOutcome.UNCERTAIN,
            "WEBHOOK_502",
            False,
        ),
        (
            401,
            {"error": "UNAUTHORIZED"},
            DeliveryStatus.FAILED,
            mes.MesDeliveryOutcome.FAILED,
            "WEBHOOK_401",
            True,
        ),
        (
            422,
            {"error": "INVALID_PAYLOAD"},
            DeliveryStatus.FAILED,
            mes.MesDeliveryOutcome.FAILED,
            "WEBHOOK_422",
            True,
        ),
    ],
)
def test_wf3_response_matrix_converges_through_repository(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    payload: dict[str, Any],
    settled: DeliveryStatus,
    outcome: mes.MesDeliveryOutcome,
    failure: str | None,
    terminal: bool,
) -> None:
    transactions = _Transactions()
    observed = _wire(monkeypatch, transactions, settled_status=settled)
    result = mes.production_ports(
        _settings(),
        transactions,
        http_post=lambda *args, **kwargs: _Response(status, payload),
    ).service.publish(ACTION_ID)
    assert result.outcome is outcome
    assert observed["settle"]["failure_code"] == failure
    assert observed["settle"]["terminal_failure"] is terminal


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (httpx.ReadTimeout("slow"), "WEBHOOK_TIMEOUT"),
        (httpx.ConnectError("down"), "WEBHOOK_UNREACHABLE"),
    ],
)
def test_transport_error_keeps_sending_and_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    code: str,
) -> None:
    transactions = _Transactions()
    observed = _wire(monkeypatch, transactions)

    def post(*args: Any, **kwargs: Any) -> Any:
        raise raised

    ports = mes.production_ports(_settings(), transactions, http_post=post)
    with pytest.raises(mes.MesTransportError) as exc:
        ports.publish_mes(ACTION_ID)
    assert exc.value.code == code
    assert observed["settle"]["failure_code"] == code
    assert transactions.open == 0


def test_terminal_callback_wins_over_transport_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transactions = _Transactions()
    _wire(monkeypatch, transactions, settled_status=DeliveryStatus.SENT)
    result = mes.production_ports(
        _settings(),
        transactions,
        http_post=lambda *args, **kwargs: _Response(502, {}),
    ).service.publish(ACTION_ID)
    assert result.outcome is mes.MesDeliveryOutcome.SENT


def test_zero_claim_calls_neither_http_nor_second_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transactions = _Transactions()
    monkeypatch.setattr(
        mes,
        "begin_mes_delivery",
        lambda connection, **kwargs: None,
    )
    calls = 0

    def post(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1

    result = mes.production_ports(
        _settings(), transactions, http_post=post
    ).service.publish(ACTION_ID)
    assert result.outcome is mes.MesDeliveryOutcome.NOOP
    assert transactions.calls == 1
    assert calls == 0


class _Message:
    def __init__(
        self,
        *,
        value: bytes,
        key: bytes = ACTION_ID.encode(),
        error: Any = None,
    ) -> None:
        self._value = value
        self._key = key
        self._error = error

    def value(self) -> bytes:
        return self._value

    def key(self) -> bytes:
        return self._key

    def error(self) -> Any:
        return self._error


class _Consumer:
    def __init__(
        self,
        messages: list[_Message],
        events: list[str],
        *,
        commit_error: Exception | None = None,
    ) -> None:
        self.messages = messages
        self.events = events
        self.commit_error = commit_error
        self.commits = 0
        self.closed = False

    def poll(self, timeout: float) -> _Message | None:
        return self.messages.pop(0) if self.messages else None

    def commit(self, *, message: _Message, asynchronous: bool) -> None:
        assert asynchronous is False
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1
        self.events.append("commit")

    def subscribe(self, topics: list[str]) -> None:
        assert topics == [mock.ACTIONS_TOPIC]

    def close(self) -> None:
        self.closed = True


class _Producer:
    def __init__(
        self,
        events: list[str],
        *,
        ack_error: Any = None,
        remaining: int = 0,
        callback: bool = True,
        produce_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.ack_error = ack_error
        self.remaining = remaining
        self.callback = callback
        self.produce_error = produce_error
        self.records: list[tuple[str, bytes, bytes]] = []

    def produce(
        self,
        topic: str,
        *,
        key: bytes,
        value: bytes,
        on_delivery: Any,
    ) -> None:
        if self.produce_error is not None:
            raise self.produce_error
        self.events.append("produce")
        self.records.append((topic, key, value))
        if self.callback:
            self.events.append("ack")
            on_delivery(self.ack_error, object())

    def flush(self, timeout: float | None = None) -> int:
        return self.remaining


def _command_raw() -> bytes:
    return mes.raw_mes_payload(_claim())


def test_mes_mock_commits_only_after_result_broker_ack() -> None:
    events: list[str] = []
    consumer = _Consumer([_Message(value=_command_raw())], events)
    producer = _Producer(events)

    def handler(command: mock.MesCommand) -> mock.MesResult:
        events.append("effect")
        return mock.successful_hold(command)

    outcome = mock.MesMockConsumer(
        consumer=consumer,
        producer=producer,
        handler=handler,
    ).process_once()
    assert outcome is mock.MesProcessOutcome.PUBLISHED
    assert events == ["effect", "produce", "ack", "commit"]
    topic, key, raw = producer.records[0]
    assert topic == "fdc.actions.result"
    assert key == ACTION_ID.encode()
    assert json.loads(raw) == {
        "action_id": ACTION_ID,
        "error_code": None,
        "request_hash": HASH,
        "status": "SENT",
    }


@pytest.mark.parametrize(
    "producer",
    [
        _Producer([], ack_error=RuntimeError("broker")),
        _Producer([], remaining=1, callback=False),
    ],
)
def test_ack_failure_leaves_input_uncommitted(producer: _Producer) -> None:
    events: list[str] = []
    producer.events = events
    consumer = _Consumer([_Message(value=_command_raw())], events)
    outcome = mock.MesMockConsumer(
        consumer=consumer,
        producer=producer,
    ).process_once()
    assert outcome is mock.MesProcessOutcome.RETRY
    assert consumer.commits == 0


def test_result_publish_exception_is_sanitized_and_uncommitted() -> None:
    events: list[str] = []
    consumer = _Consumer([_Message(value=_command_raw())], events)
    producer = _Producer(events, produce_error=RuntimeError("broker detail"))
    with pytest.raises(mock.MesMockError) as exc:
        mock.MesMockConsumer(consumer=consumer, producer=producer).process_once()
    assert exc.value.code == "MES_RESULT_PUBLISH_FAILED"
    assert consumer.commits == 0


def test_reconsumed_command_emits_byte_identical_result() -> None:
    events: list[str] = []
    consumer = _Consumer(
        [_Message(value=_command_raw()), _Message(value=_command_raw())],
        events,
    )
    producer = _Producer(events)
    service = mock.MesMockConsumer(consumer=consumer, producer=producer)

    assert service.process_once() is mock.MesProcessOutcome.PUBLISHED
    assert service.process_once() is mock.MesProcessOutcome.PUBLISHED
    assert producer.records[0] == producer.records[1]
    assert consumer.commits == 2


def test_malformed_input_is_sanitized_discard_and_commit() -> None:
    events: list[str] = []
    consumer = _Consumer([_Message(value=b'{"secret":"must-not-log"}')], events)
    producer = _Producer(events)
    outcome = mock.MesMockConsumer(
        consumer=consumer,
        producer=producer,
    ).process_once()
    assert outcome is mock.MesProcessOutcome.DISCARDED
    assert consumer.commits == 1
    assert producer.records == []


def test_commit_failure_is_sanitized_and_never_reported_as_published() -> None:
    events: list[str] = []
    consumer = _Consumer(
        [_Message(value=_command_raw())],
        events,
        commit_error=RuntimeError("broker details"),
    )
    producer = _Producer(events)
    with pytest.raises(mock.MesMockError) as exc:
        mock.MesMockConsumer(consumer=consumer, producer=producer).process_once()
    assert exc.value.code == "MES_OFFSET_COMMIT_FAILED"
    assert consumer.commits == 0


def test_handler_failure_has_no_result_and_no_commit() -> None:
    events: list[str] = []
    consumer = _Consumer([_Message(value=_command_raw())], events)
    producer = _Producer(events)

    def fail(_command: mock.MesCommand) -> mock.MesResult:
        raise RuntimeError("private effect failure")

    with pytest.raises(mock.MesMockError) as exc:
        mock.MesMockConsumer(
            consumer=consumer,
            producer=producer,
            handler=fail,
        ).process_once()
    assert exc.value.code == "MES_HANDLER_FAILED"
    assert consumer.commits == 0
    assert producer.records == []


def test_config_factory_keys_are_exact_and_repr_hides_values() -> None:
    config = mock.MesMockConfig.from_mapping(
        {
            "KAFKA_BOOTSTRAP_INTERNAL": "kafka:9092",
            "KAFKA_CLIENT_USER": "client-user",
            "KAFKA_CLIENT_PASSWORD": "client-password-value",
            "MES_CONSUMER_GROUP": "kosa-fdc-mes-mock",
        }
    )
    assert set(config.consumer_settings()) == {
        "bootstrap.servers",
        "security.protocol",
        "sasl.mechanism",
        "sasl.username",
        "sasl.password",
        "group.id",
        "enable.auto.commit",
        "auto.offset.reset",
    }
    assert set(config.producer_settings()) == {
        "bootstrap.servers",
        "security.protocol",
        "sasl.mechanism",
        "sasl.username",
        "sasl.password",
        "acks",
    }
    assert "client-password-value" not in repr(config)
    assert "client-user" not in repr(config)


def test_changed_event_identity_is_discarded_without_result() -> None:
    payload = json.loads(_command_raw())
    payload["event_id"] = mes.event_id_for(ACTION_ID, "b" * 64)
    events: list[str] = []
    consumer = _Consumer(
        [_Message(value=json.dumps(payload).encode())],
        events,
    )
    producer = _Producer(events)
    outcome = mock.MesMockConsumer(
        consumer=consumer,
        producer=producer,
    ).process_once()
    assert outcome is mock.MesProcessOutcome.DISCARDED
    assert consumer.commits == 1
    assert producer.records == []
