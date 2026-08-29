"""승인된 EQP_HOLD를 n8n WF3로 발행하는 MES delivery adapter."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import ModuleType
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from app.agent.delivery_signing import signed_delivery_headers
from app.agent.repository import (
    MesDeliveryClaim,
    begin_mes_delivery,
    list_action_deliveries,
    settle_mes_webhook,
)
from app.agent.state import DeliveryPlan
from app.agent.tools import TransactionFactory
from app.common.enums import DeliveryStatus

WEBHOOK_PATH = "/webhook/fdc-mes-hold"
PAYLOAD_SCHEMA = "mes-hold-request-v1"
EVENT_ID_PATTERN = re.compile(r"^MES:[0-9a-f]{64}$")


class MesDeliveryError(RuntimeError):
    """raw 설정·HTTP·DB 값을 노출하지 않는 MES 경계 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MesDeliveryConfigError(MesDeliveryError):
    def __init__(self) -> None:
        super().__init__("MES_DELIVERY_CONFIG_INVALID")


class MesDeliveryContractError(MesDeliveryError):
    pass


class MesTransportError(MesDeliveryError):
    def __init__(self, code: str = "MES_TRANSPORT_ERROR") -> None:
        super().__init__(code)


class MesDeliveryOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    SENT = "SENT"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    NOOP = "NOOP"


@dataclass(frozen=True, slots=True, repr=False)
class MesDeliveryConfig:
    webhook_url: str
    timeout_seconds: int
    secret: bytes


@dataclass(frozen=True, slots=True)
class MesDeliveryResult:
    action_id: str
    outcome: MesDeliveryOutcome
    response_status: int | None = None
    published: bool | None = None
    reason_code: str | None = None


class HttpPost(Protocol):
    def __call__(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response: ...


@dataclass(frozen=True, slots=True)
class MesDeliveryPorts:
    service: MesDeliveryService
    publish_mes: Callable[[str], None]
    writeback_result: Callable[[str], tuple[DeliveryPlan, ...]]


def _config_value(settings: ModuleType | Any, name: str) -> Any:
    return getattr(settings, name, None)


def load_mes_delivery_config(settings: ModuleType | Any) -> MesDeliveryConfig:
    """WF3 URL·timeout·shared secret을 DB 접근 전에 fail-closed 검증한다."""

    raw_url = _config_value(settings, "N8N_WF3_URL")
    raw_timeout = _config_value(settings, "N8N_WEBHOOK_TIMEOUT_SEC")
    raw_secret = _config_value(settings, "N8N_WEBHOOK_SECRET")
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise MesDeliveryConfigError
    normalized_url = raw_url.strip()
    try:
        parsed = urlsplit(normalized_url)
        hostname = parsed.hostname
        port = parsed.port
        httpx.URL(normalized_url)
    except (ValueError, httpx.InvalidURL) as exc:
        raise MesDeliveryConfigError from exc
    if (
        any(character.isspace() for character in normalized_url)
        or "?" in normalized_url
        or "#" in normalized_url
        or parsed.scheme not in {"http", "https"}
        or not hostname
        or (port is not None and port == 0)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != WEBHOOK_PATH
    ):
        raise MesDeliveryConfigError
    if type(raw_timeout) is not int or raw_timeout < 25:
        raise MesDeliveryConfigError
    if not isinstance(raw_secret, str) or not raw_secret.strip():
        raise MesDeliveryConfigError
    return MesDeliveryConfig(
        webhook_url=normalized_url,
        timeout_seconds=raw_timeout,
        secret=raw_secret.strip().encode("utf-8"),
    )


def _utc_iso(value: datetime, code: str) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise MesDeliveryContractError(code)
    return value.astimezone(UTC).isoformat()


def event_id_for(action_id: str, request_hash: str) -> str:
    """계획 v6의 UTF-8/NUL 직렬화로 stable MES event identity를 만든다."""

    if not isinstance(action_id, str) or not action_id.strip():
        raise MesDeliveryContractError("MES_ACTION_ID_INVALID")
    if (
        not isinstance(request_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", request_hash) is None
    ):
        raise MesDeliveryContractError("MES_REQUEST_HASH_INVALID")
    identity_raw = action_id + "\0MES_MOCK\0" + request_hash
    digest = hashlib.sha256(identity_raw.encode("utf-8")).hexdigest()
    event_id = f"MES:{digest}"
    if EVENT_ID_PATTERN.fullmatch(event_id) is None:  # pragma: no cover - 방어 불변식
        raise MesDeliveryContractError("MES_EVENT_ID_INVALID")
    return event_id


def raw_mes_payload(claim: MesDeliveryClaim) -> bytes:
    """WF3 exact 11-field schema를 key-sort·compact JSON으로 직렬화한다."""

    delivery = claim.delivery
    if delivery.started_at is None:
        raise MesDeliveryContractError("MES_STARTED_AT_MISSING")
    if claim.approval.decided_by is None or claim.approval.decided_at is None:
        raise MesDeliveryContractError("MES_DECISION_MISSING")
    payload = {
        "action_code": claim.action.action_code.value,
        "action_id": claim.action.action_id,
        "chamber_id": claim.action.chamber_id,
        "command": "HOLD",
        "decided_at": _utc_iso(claim.approval.decided_at, "MES_DECIDED_AT_INVALID"),
        "decided_by": claim.approval.decided_by,
        "equipment_id": claim.equipment_id,
        "event_id": event_id_for(claim.action.action_id, delivery.request_hash),
        "occurred_at": _utc_iso(delivery.started_at, "MES_STARTED_AT_INVALID"),
        "request_hash": delivery.request_hash,
        "schema": PAYLOAD_SCHEMA,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _published_response(response: Any) -> bool | None:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"ok", "published"}
        or payload.get("ok") is not True
        or type(payload.get("published")) is not bool
    ):
        return None
    return payload["published"]


class MesDeliveryService:
    def __init__(
        self,
        *,
        config: MesDeliveryConfig,
        transactions: TransactionFactory,
        http_post: HttpPost,
        clock: Callable[[], float],
    ) -> None:
        self._config = config
        self._transactions = transactions
        self._http_post = http_post
        self._clock = clock

    def publish(self, action_id: str) -> MesDeliveryResult:
        with self._transactions() as connection:
            claim = begin_mes_delivery(connection, action_id=action_id)
            if claim is None:
                return MesDeliveryResult(action_id, MesDeliveryOutcome.NOOP)
            raw = raw_mes_payload(claim)

        headers = signed_delivery_headers(
            raw,
            self._config.secret,
            int(self._clock()),
        )
        status: int | None = None
        published: bool | None = None
        failure_code: str | None = None
        terminal_failure = False
        try:
            response = self._http_post(
                self._config.webhook_url,
                content=raw,
                headers=headers,
                timeout=float(self._config.timeout_seconds),
            )
            status = int(response.status_code)
            if status == 200:
                published = _published_response(response)
                if published is False:
                    failure_code = "WF3_CALLBACK_STATE_MISSING"
                elif published is None:
                    failure_code = "WF3_RESPONSE_INVALID"
            elif status in {401, 422}:
                failure_code = f"WEBHOOK_{status}"
                terminal_failure = True
            else:
                failure_code = f"WEBHOOK_{status}"
        except httpx.TimeoutException:
            failure_code = "WEBHOOK_TIMEOUT"
        except httpx.TransportError:
            failure_code = "WEBHOOK_UNREACHABLE"

        with self._transactions() as connection:
            settled = settle_mes_webhook(
                connection,
                action_id=action_id,
                request_hash=claim.delivery.request_hash,
                failure_code=failure_code,
                terminal_failure=terminal_failure,
            )

        if settled.status is DeliveryStatus.SENT:
            return MesDeliveryResult(
                action_id,
                MesDeliveryOutcome.SENT,
                response_status=status,
                published=published,
            )
        if settled.status is DeliveryStatus.FAILED:
            return MesDeliveryResult(
                action_id,
                MesDeliveryOutcome.FAILED,
                response_status=status,
                published=published,
                reason_code=(
                    failure_code if terminal_failure else "MES_DELIVERY_FAILED"
                ),
            )
        if failure_code is not None:
            return MesDeliveryResult(
                action_id,
                MesDeliveryOutcome.UNCERTAIN,
                response_status=status,
                published=published,
                reason_code=failure_code,
            )
        return MesDeliveryResult(
            action_id,
            MesDeliveryOutcome.ACCEPTED,
            response_status=status,
            published=published,
        )

    def projection(self, action_id: str) -> tuple[DeliveryPlan, ...]:
        with self._transactions() as connection:
            deliveries = list_action_deliveries(connection, action_id)
        return tuple(
            DeliveryPlan(channel=item.channel, status=item.status)
            for item in deliveries
        )


def production_ports(
    settings: ModuleType | Any,
    transactions: TransactionFactory,
    *,
    http_post: HttpPost = httpx.post,
    clock: Callable[[], float] = time.time,
) -> MesDeliveryPorts:
    """설정을 먼저 검증하고 graph ``publish_mes/writeback_result``를 만든다."""

    config = load_mes_delivery_config(settings)
    service = MesDeliveryService(
        config=config,
        transactions=transactions,
        http_post=http_post,
        clock=clock,
    )

    def publish(action_id: str) -> None:
        result = service.publish(action_id)
        if result.outcome in {MesDeliveryOutcome.FAILED, MesDeliveryOutcome.UNCERTAIN}:
            raise MesTransportError(result.reason_code or "MES_TRANSPORT_ERROR")

    return MesDeliveryPorts(
        service=service,
        publish_mes=publish,
        writeback_result=service.projection,
    )


__all__ = [
    "EVENT_ID_PATTERN",
    "MesDeliveryConfig",
    "MesDeliveryConfigError",
    "MesDeliveryContractError",
    "MesDeliveryError",
    "MesDeliveryOutcome",
    "MesDeliveryPorts",
    "MesDeliveryResult",
    "MesDeliveryService",
    "MesTransportError",
    "event_id_for",
    "load_mes_delivery_config",
    "production_ports",
    "raw_mes_payload",
]
