"""격리 Kafka 3.9.1에서 MES Mock의 실제 ack→commit 왕복을 검증한다."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from app.common.mes_identity import event_id_for
from app.mes_mock.consumer import (
    ACTIONS_TOPIC,
    RESULT_TOPIC,
    MesMockConfig,
    MesProcessOutcome,
    production_consumer,
)

pytestmark = pytest.mark.container

BOOTSTRAP = os.getenv("MES_KAFKA_TEST_BOOTSTRAP")
if not BOOTSTRAP:
    pytest.skip(
        "MES_KAFKA_TEST_BOOTSTRAP이 지정된 격리 Kafka 전용 회귀다",
        allow_module_level=True,
    )

confluent = pytest.importorskip("confluent_kafka")
CLIENT_USER = "test-client-user"
CLIENT_PASSWORD = "test-client-password"
ACTION_ID = "ACT-kafka-roundtrip01"
SERVICE_ACTION_ID = "ACT-kafka-file-secret01"
REQUEST_HASH = "d" * 64


def _client_settings() -> dict[str, Any]:
    return {
        "bootstrap.servers": BOOTSTRAP,
        "security.protocol": "SASL_PLAINTEXT",
        "sasl.mechanism": "PLAIN",
        "sasl.username": CLIENT_USER,
        "sasl.password": CLIENT_PASSWORD,
    }


def _command_raw() -> bytes:
    timestamp = datetime(2026, 8, 29, 15, 0, tzinfo=UTC).isoformat()
    return json.dumps(
        {
            "action_code": "EQP_HOLD",
            "action_id": ACTION_ID,
            "chamber_id": "EQP01-PM1",
            "command": "HOLD",
            "decided_at": timestamp,
            "decided_by": "container-test",
            "equipment_id": "EQP01",
            "event_id": event_id_for(ACTION_ID, REQUEST_HASH),
            "occurred_at": timestamp,
            "request_hash": REQUEST_HASH,
            "schema": "mes-hold-request-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _service_command_raw() -> bytes:
    payload = json.loads(_command_raw())
    payload["action_id"] = SERVICE_ACTION_ID
    payload["event_id"] = event_id_for(SERVICE_ACTION_ID, REQUEST_HASH)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _produce(value: bytes, key: bytes) -> None:
    producer = confluent.Producer({**_client_settings(), "acks": "all"})
    producer.produce(ACTIONS_TOPIC, key=key, value=value)
    assert producer.flush(10) == 0


def _poll_until_result(consumer: Any, timeout: float = 15.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = consumer.poll(1.0)
        if message is not None and message.error() is None:
            return message
    pytest.fail("MES_RESULT_NOT_RECEIVED")


def test_real_kafka_roundtrip_preserves_identity_and_commits_input() -> None:
    result_consumer = confluent.Consumer(
        {
            **_client_settings(),
            "group.id": "kosa-fdc-mes-result-test",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    result_consumer.subscribe([RESULT_TOPIC])
    service = production_consumer(
        MesMockConfig(
            bootstrap_servers=BOOTSTRAP,
            username=CLIENT_USER,
            password=CLIENT_PASSWORD,
        )
    )
    try:
        _produce(_command_raw(), ACTION_ID.encode())
        deadline = time.monotonic() + 15
        outcome = MesProcessOutcome.EMPTY
        while time.monotonic() < deadline and outcome is MesProcessOutcome.EMPTY:
            outcome = service.process_once()
        assert outcome is MesProcessOutcome.PUBLISHED

        result = _poll_until_result(result_consumer)
        assert result.key() == ACTION_ID.encode()
        assert json.loads(result.value()) == {
            "action_id": ACTION_ID,
            "error_code": None,
            "request_hash": REQUEST_HASH,
            "status": "SENT",
        }

        # 잘못된 payload는 효과/result 없이 discard되고 그 partition은 진행한다.
        _produce(b'{"secret":"not-a-command"}', ACTION_ID.encode())
        deadline = time.monotonic() + 15
        outcome = MesProcessOutcome.EMPTY
        while time.monotonic() < deadline and outcome is MesProcessOutcome.EMPTY:
            outcome = service.process_once()
        assert outcome is MesProcessOutcome.DISCARDED
        assert result_consumer.poll(2.0) is None
    finally:
        service.stop_event.set()
        service.close()
        result_consumer.close()


def test_wrong_sasl_credential_cannot_publish() -> None:
    errors: list[Any] = []
    producer = confluent.Producer(
        {
            **_client_settings(),
            "sasl.password": "intentionally-wrong-test-password",
            "acks": "all",
            "message.timeout.ms": 3000,
        }
    )
    producer.produce(
        ACTIONS_TOPIC,
        key=b"invalid-auth",
        value=b"{}",
        on_delivery=lambda error, _message: errors.append(error),
    )
    remaining = producer.flush(5)
    assert remaining != 0 or any(error is not None for error in errors)


@pytest.mark.skipif(
    os.getenv("MES_MOCK_SERVICE_RUNNING") != "1",
    reason="file-only mes-mock fixture service 전용 smoke",
)
def test_file_only_consumer_service_publishes_result() -> None:
    result_consumer = confluent.Consumer(
        {
            **_client_settings(),
            "group.id": "kosa-fdc-mes-file-secret-test",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    result_consumer.subscribe([RESULT_TOPIC])
    try:
        _produce(_service_command_raw(), SERVICE_ACTION_ID.encode())
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = result_consumer.poll(1.0)
            if result is None or result.error() is not None:
                continue
            if result.key() != SERVICE_ACTION_ID.encode():
                continue
            assert json.loads(result.value()) == {
                "action_id": SERVICE_ACTION_ID,
                "error_code": None,
                "request_hash": REQUEST_HASH,
                "status": "SENT",
            }
            return
        pytest.fail("FILE_ONLY_MES_RESULT_NOT_RECEIVED")
    finally:
        result_consumer.close()
