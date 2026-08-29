"""`fdc.actions`를 처리하고 broker ack 뒤에만 offset을 commit하는 MES Mock."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from threading import Event
from typing import Any, Protocol

from app.agent.mes_delivery import EVENT_ID_PATTERN, event_id_for

ACTIONS_TOPIC = "fdc.actions"
RESULT_TOPIC = "fdc.actions.result"
DEFAULT_GROUP = "kosa-fdc-mes-mock"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
_EXPECTED_KEYS = frozenset(
    {
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
)
logger = logging.getLogger(__name__)


class MesMockError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MesMockConfigError(MesMockError):
    def __init__(self) -> None:
        super().__init__("MES_MOCK_CONFIG_INVALID")


class MesProcessOutcome(StrEnum):
    PUBLISHED = "PUBLISHED"
    DISCARDED = "DISCARDED"
    RETRY = "RETRY"
    EMPTY = "EMPTY"


@dataclass(frozen=True, slots=True, repr=False)
class MesMockConfig:
    bootstrap_servers: str
    username: str
    password: str
    group_id: str = DEFAULT_GROUP

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> MesMockConfig:
        bootstrap = values.get("KAFKA_BOOTSTRAP_INTERNAL", "").strip()
        username = values.get("KAFKA_CLIENT_USER", "").strip()
        password = values.get("KAFKA_CLIENT_PASSWORD", "").strip()
        group_id = values.get("MES_CONSUMER_GROUP", "").strip()
        if (
            not bootstrap
            or any(character.isspace() for character in bootstrap)
            or ":" not in bootstrap
            or not username
            or not password
            or group_id != DEFAULT_GROUP
        ):
            raise MesMockConfigError
        return cls(bootstrap, username, password, group_id)

    def consumer_settings(self) -> dict[str, Any]:
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "security.protocol": "SASL_PLAINTEXT",
            "sasl.mechanism": "PLAIN",
            "sasl.username": self.username,
            "sasl.password": self.password,
            "group.id": self.group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }

    def producer_settings(self) -> dict[str, Any]:
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "security.protocol": "SASL_PLAINTEXT",
            "sasl.mechanism": "PLAIN",
            "sasl.username": self.username,
            "sasl.password": self.password,
            "acks": "all",
        }


@dataclass(frozen=True, slots=True)
class MesCommand:
    action_id: str
    request_hash: str
    event_id: str
    equipment_id: str
    chamber_id: str


@dataclass(frozen=True, slots=True)
class MesResult:
    action_id: str
    request_hash: str
    status: str
    error_code: str | None

    def raw(self) -> bytes:
        if self.status == "SENT":
            if self.error_code is not None:
                raise MesMockError("MES_RESULT_INVALID")
        elif self.status == "FAILED":
            if (
                self.error_code is None
                or _ERROR_CODE.fullmatch(self.error_code) is None
            ):
                raise MesMockError("MES_RESULT_INVALID")
        else:
            raise MesMockError("MES_RESULT_INVALID")
        return json.dumps(
            {
                "action_id": self.action_id,
                "error_code": self.error_code,
                "request_hash": self.request_hash,
                "status": self.status,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


class Message(Protocol):
    def key(self) -> bytes | str | None: ...

    def value(self) -> bytes | str | None: ...

    def error(self) -> Any | None: ...


class Consumer(Protocol):
    def subscribe(self, topics: list[str]) -> None: ...

    def poll(self, timeout: float) -> Message | None: ...

    def commit(self, *, message: Message, asynchronous: bool) -> Any: ...

    def close(self) -> None: ...


class Producer(Protocol):
    def produce(
        self,
        topic: str,
        *,
        key: bytes,
        value: bytes,
        on_delivery: Callable[[Any | None, Any], None],
    ) -> None: ...

    def flush(self, timeout: float | None = None) -> int: ...


Handler = Callable[[MesCommand], MesResult]


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _offset_iso(value: Any) -> bool:
    if not _nonblank(value):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def parse_command(message: Message) -> MesCommand:
    """record key와 exact payload를 검증한다. raw value를 오류에 포함하지 않는다."""

    raw = message.value()
    try:
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, bytes):
            raise ValueError
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise MesMockError("MES_COMMAND_INVALID") from exc
    if not isinstance(payload, dict) or set(payload) != _EXPECTED_KEYS:
        raise MesMockError("MES_COMMAND_INVALID")
    if (
        payload.get("schema") != "mes-hold-request-v1"
        or payload.get("action_code") != "EQP_HOLD"
        or payload.get("command") != "HOLD"
        or not all(
            _nonblank(payload.get(key))
            for key in (
                "action_id",
                "chamber_id",
                "decided_by",
                "equipment_id",
            )
        )
        or not _offset_iso(payload.get("decided_at"))
        or not _offset_iso(payload.get("occurred_at"))
        or not isinstance(payload.get("request_hash"), str)
        or _HEX64.fullmatch(payload["request_hash"]) is None
        or not isinstance(payload.get("event_id"), str)
        or EVENT_ID_PATTERN.fullmatch(payload["event_id"]) is None
        or payload["event_id"]
        != event_id_for(payload["action_id"], payload["request_hash"])
    ):
        raise MesMockError("MES_COMMAND_INVALID")
    key = message.key()
    if isinstance(key, bytes):
        try:
            key = key.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise MesMockError("MES_COMMAND_IDENTITY_MISMATCH") from exc
    if key != payload["action_id"]:
        raise MesMockError("MES_COMMAND_IDENTITY_MISMATCH")
    return MesCommand(
        action_id=payload["action_id"],
        request_hash=payload["request_hash"],
        event_id=payload["event_id"],
        equipment_id=payload["equipment_id"],
        chamber_id=payload["chamber_id"],
    )


def successful_hold(command: MesCommand) -> MesResult:
    """실제 MES 부작용 없이 결정론적 성공 결과만 만드는 기본 handler."""

    return MesResult(
        action_id=command.action_id,
        request_hash=command.request_hash,
        status="SENT",
        error_code=None,
    )


class MesMockConsumer:
    def __init__(
        self,
        *,
        consumer: Consumer,
        producer: Producer,
        handler: Handler = successful_hold,
        stop: Event | None = None,
    ) -> None:
        self._consumer = consumer
        self._producer = producer
        self._handler = handler
        self._stop = stop or Event()
        self._subscribed = False

    @property
    def stop_event(self) -> Event:
        return self._stop

    def process_once(self, timeout: float = 1.0) -> MesProcessOutcome:
        self.start()
        message = self._consumer.poll(timeout)
        if message is None:
            return MesProcessOutcome.EMPTY
        if message.error() is not None:
            logger.warning("MES_MOCK_BROKER_RECORD_ERROR")
            return MesProcessOutcome.RETRY
        try:
            command = parse_command(message)
        except MesMockError as exc:
            logger.warning("MES_MOCK_DISCARDED code=%s", exc.code)
            self._commit(message)
            return MesProcessOutcome.DISCARDED

        try:
            result = self._handler(command)
        except Exception as exc:  # noqa: BLE001 - injected effect 오류를 sanitize한다
            raise MesMockError("MES_HANDLER_FAILED") from exc
        if (
            result.action_id != command.action_id
            or result.request_hash != command.request_hash
        ):
            raise MesMockError("MES_RESULT_IDENTITY_MISMATCH")
        receipt: dict[str, Any] = {"called": False, "error": None}

        def delivered(error: Any | None, _message: Any) -> None:
            receipt["called"] = True
            receipt["error"] = error

        try:
            self._producer.produce(
                RESULT_TOPIC,
                key=command.action_id.encode("utf-8"),
                value=result.raw(),
                on_delivery=delivered,
            )
            remaining = self._producer.flush(10.0)
        except Exception as exc:  # noqa: BLE001 - third-party error를 sanitized code로 경계
            raise MesMockError("MES_RESULT_PUBLISH_FAILED") from exc
        if remaining != 0 or not receipt["called"] or receipt["error"] is not None:
            return MesProcessOutcome.RETRY

        self._commit(message)
        return MesProcessOutcome.PUBLISHED

    def run(self) -> None:
        self.start()
        try:
            while not self._stop.is_set():
                self.process_once()
        finally:
            self.close()

    def close(self) -> None:
        """처리 중 record의 ack→commit 경계를 건드리지 않고 client를 닫는다."""

        self._producer.flush(10.0)
        self._consumer.close()

    def start(self) -> None:
        """poll 경계에서 누락되지 않도록 input topic을 정확히 한 번 subscribe한다."""

        if not self._subscribed:
            try:
                self._consumer.subscribe([ACTIONS_TOPIC])
            except Exception as exc:  # noqa: BLE001 - client 오류 메시지는 외부로 안 보낸다
                raise MesMockError("MES_SUBSCRIBE_FAILED") from exc
            self._subscribed = True

    def _commit(self, message: Message) -> None:
        try:
            self._consumer.commit(message=message, asynchronous=False)
        except Exception as exc:  # noqa: BLE001 - broker/client 상세를 sanitize한다
            raise MesMockError("MES_OFFSET_COMMIT_FAILED") from exc


def production_consumer(config: MesMockConfig) -> MesMockConsumer:
    """confluent-kafka import와 client 생성을 config 검증 뒤에 수행한다."""

    try:
        from confluent_kafka import Consumer as KafkaConsumer
        from confluent_kafka import Producer as KafkaProducer
    except ImportError as exc:  # image dependency drift
        raise MesMockError("KAFKA_CLIENT_NOT_INSTALLED") from exc
    return MesMockConsumer(
        consumer=KafkaConsumer(config.consumer_settings()),
        producer=KafkaProducer(config.producer_settings()),
    )


__all__ = [
    "ACTIONS_TOPIC",
    "RESULT_TOPIC",
    "MesCommand",
    "MesMockConfig",
    "MesMockConfigError",
    "MesMockConsumer",
    "MesMockError",
    "MesProcessOutcome",
    "MesResult",
    "parse_command",
    "production_consumer",
    "successful_hold",
]
