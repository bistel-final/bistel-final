"""서명된 n8n WF2 email delivery adapter (`V5-C-4.3`).

DB claim transaction과 webhook I/O를 분리한다. HTTP 응답이 돌아오기 전에 callback이
``SENT|FAILED``를 확정할 수 있으므로 두 번째 transaction은 terminal DB 정본을 항상
우선한다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import ModuleType
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from app.agent.approval_store import EmailTransportError
from app.agent.repository import (
    ActionDeliveryRow,
    begin_email_delivery,
    get_action_delivery,
    get_action_history,
    get_approval_request,
    get_run_action,
    settle_email_delivery,
)
from app.agent.tools import TransactionFactory
from app.common.enums import (
    ActionCode,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
)

WEBHOOK_PATH = "/webhook/fdc-notify-email"
PAYLOAD_SCHEMA = "email-request-v1"
MAX_RECIPIENTS = 10
MAX_SUMMARY_LENGTH = 2_000

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_FORBIDDEN_SUMMARY_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class EmailDeliveryConfigError(RuntimeError):
    """설정 실값을 노출하지 않는 조립 오류."""

    def __init__(self, code: str = "EMAIL_DELIVERY_CONFIG_INVALID") -> None:
        super().__init__(code)
        self.code = code


class EmailDeliveryContractError(RuntimeError):
    """payload identity·본문 계약 위반."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EmailKind(StrEnum):
    WARNING_NOTIFY = "WARNING_NOTIFY"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"


class EmailDeliveryOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    SENT = "SENT"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    NOOP = "NOOP"


@dataclass(frozen=True, slots=True, repr=False)
class EmailDeliveryConfig:
    webhook_url: str
    timeout_seconds: int
    secret: bytes
    recipients: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmailDeliveryResult:
    action_id: str
    outcome: EmailDeliveryOutcome
    response_status: int | None = None
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
class EmailDeliveryPorts:
    service: EmailDeliveryService
    notify_email: Callable[[str], None]
    approval_sender: Callable[[str, str], None]


def _config_value(settings: ModuleType | Any, name: str) -> Any:
    return getattr(settings, name, None)


def _parse_recipients(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw or "\r" in raw or "\n" in raw:
        raise EmailDeliveryConfigError()
    recipients: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        address = part.strip()
        if not address or len(address) > 254 or _EMAIL.fullmatch(address) is None:
            raise EmailDeliveryConfigError()
        key = address.casefold()
        if key not in seen:
            recipients.append(address)
            seen.add(key)
    if not 1 <= len(recipients) <= MAX_RECIPIENTS:
        raise EmailDeliveryConfigError()
    return tuple(recipients)


def load_email_delivery_config(settings: ModuleType | Any) -> EmailDeliveryConfig:
    """config 단일 source 4종을 DB 접근 전에 fail-closed 검증한다."""

    raw_url = _config_value(settings, "N8N_WEBHOOK_URL")
    raw_secret = _config_value(settings, "N8N_WEBHOOK_SECRET")
    raw_timeout = _config_value(settings, "N8N_WEBHOOK_TIMEOUT_SEC")
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise EmailDeliveryConfigError()
    normalized_url = raw_url.strip()
    try:
        parsed = urlsplit(normalized_url)
        hostname = parsed.hostname
        port = parsed.port  # malformed port를 factory에서 거부한다.
        httpx.URL(normalized_url)
    except (ValueError, httpx.InvalidURL) as exc:
        raise EmailDeliveryConfigError() from exc
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
        raise EmailDeliveryConfigError()
    if type(raw_timeout) is not int or raw_timeout < 25:  # bool도 거부한다.
        raise EmailDeliveryConfigError()
    if not isinstance(raw_secret, str) or not raw_secret.strip():
        raise EmailDeliveryConfigError()
    return EmailDeliveryConfig(
        webhook_url=normalized_url,
        timeout_seconds=raw_timeout,
        secret=raw_secret.strip().encode("utf-8"),
        recipients=_parse_recipients(_config_value(settings, "AGENT_EMAIL_RECIPIENTS")),
    )


def _validate_summary(summary: str) -> str:
    if (
        not summary.strip()
        or len(summary) > MAX_SUMMARY_LENGTH
        or _FORBIDDEN_SUMMARY_CONTROL.search(summary) is not None
    ):
        raise EmailDeliveryContractError("EMAIL_SUMMARY_INVALID")
    return summary


def _summary(
    *,
    kind: EmailKind,
    action_id: str,
    lot_id: str,
    chamber_id: str,
    reason: str,
    approval_id: str | None,
) -> str:
    if kind is EmailKind.WARNING_NOTIFY:
        return _validate_summary(
            f"WARNING 알림\naction_id={action_id}\nlot_id={lot_id}\n"
            f"chamber_id={chamber_id}\nreason={reason}"
        )
    return _validate_summary(
        f"EQP_HOLD 승인 요청\napproval_id={approval_id}\naction_id={action_id}\n"
        f"lot_id={lot_id}\nchamber_id={chamber_id}\nreason={reason}"
    )


def _raw_payload(
    *,
    kind: EmailKind,
    action: Any,
    delivery: ActionDeliveryRow,
    approval_id: str | None,
    recipients: tuple[str, ...],
) -> bytes:
    payload = {
        "action_code": action.action_code.value,
        "action_id": action.action_id,
        "approval_id": approval_id,
        "chamber_id": action.chamber_id,
        "channel": DeliveryChannel.EMAIL.value,
        "email_kind": kind.value,
        "lot_id": action.lot_id,
        "recipients": list(recipients),
        "request_hash": delivery.request_hash,
        "schema": PAYLOAD_SCHEMA,
        "summary": _summary(
            kind=kind,
            action_id=action.action_id,
            lot_id=action.lot_id,
            chamber_id=action.chamber_id,
            reason=action.reason,
            approval_id=approval_id,
        ),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _headers(raw: bytes, secret: bytes, timestamp: int) -> dict[str, str]:
    timestamp_text = str(timestamp)
    signed = timestamp_text.encode("ascii") + b"." + raw
    signature = hmac.new(secret, signed, hashlib.sha256).hexdigest()
    return {
        "content-type": "application/json",
        "x-delivery-timestamp": timestamp_text,
        "x-delivery-signature": f"sha256={signature}",
    }


class EmailDeliveryService:
    def __init__(
        self,
        *,
        config: EmailDeliveryConfig,
        transactions: TransactionFactory,
        http_post: HttpPost,
        clock: Callable[[], float],
    ) -> None:
        self._config = config
        self._transactions = transactions
        self._http_post = http_post
        self._clock = clock

    def send_warning(self, action_id: str) -> EmailDeliveryResult:
        return self._send(
            action_id=action_id,
            kind=EmailKind.WARNING_NOTIFY,
            approval_id=None,
        )

    def send_approval(self, action_id: str, approval_id: str) -> EmailDeliveryResult:
        return self._send(
            action_id=action_id,
            kind=EmailKind.APPROVAL_REQUEST,
            approval_id=approval_id,
        )

    def _send(
        self, *, action_id: str, kind: EmailKind, approval_id: str | None
    ) -> EmailDeliveryResult:
        with self._transactions() as connection:
            action = get_action_history(connection, action_id)
            delivery = get_action_delivery(
                connection,
                action_id=action_id,
                channel=DeliveryChannel.EMAIL,
            )
            expected = (
                ActionCode.WARNING
                if kind is EmailKind.WARNING_NOTIFY
                else ActionCode.EQP_HOLD
            )
            if action.action_code is not expected:
                raise EmailDeliveryContractError("EMAIL_KIND_ACTION_MISMATCH")
            if kind is EmailKind.WARNING_NOTIFY:
                if approval_id is not None:
                    raise EmailDeliveryContractError("WARNING_APPROVAL_FORBIDDEN")
            else:
                if approval_id is None:
                    raise EmailDeliveryContractError("APPROVAL_ID_REQUIRED")
                approval = get_approval_request(connection, approval_id)
                if approval.action_id != action_id:
                    raise EmailDeliveryContractError("APPROVAL_IDENTITY_MISMATCH")
                if approval.status is not ApprovalStatus.PENDING:
                    raise EmailDeliveryContractError("APPROVAL_NOT_PENDING")
                run_action = get_run_action(connection, approval.agent_run_id)
                if run_action.action_id != action_id:
                    raise EmailDeliveryContractError("APPROVAL_IDENTITY_MISMATCH")
            raw = _raw_payload(
                kind=kind,
                action=action,
                delivery=delivery,
                approval_id=approval_id,
                recipients=self._config.recipients,
            )
            claimed = begin_email_delivery(connection, action_id=action_id)
            if claimed is not None and claimed.request_hash != delivery.request_hash:
                raise EmailDeliveryContractError("DELIVERY_REQUEST_HASH_CHANGED")

        if claimed is None:
            return EmailDeliveryResult(action_id, EmailDeliveryOutcome.NOOP)

        timestamp = int(self._clock())
        headers = _headers(raw, self._config.secret, timestamp)
        status: int | None = None
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
            if status in {401, 422}:
                failure_code = f"WEBHOOK_{status}"
                terminal_failure = True
            elif status != 200:
                failure_code = f"WEBHOOK_{status}"
        except httpx.TimeoutException:
            failure_code = "WEBHOOK_TIMEOUT"
        except httpx.TransportError:
            failure_code = "WEBHOOK_UNREACHABLE"

        with self._transactions() as connection:
            settled = settle_email_delivery(
                connection,
                action_id=action_id,
                request_hash=claimed.request_hash,
                failure_code=failure_code,
                terminal_failure=terminal_failure,
            )

        if settled.status is DeliveryStatus.SENT:
            return EmailDeliveryResult(
                action_id, EmailDeliveryOutcome.SENT, response_status=status
            )
        if settled.status is DeliveryStatus.FAILED:
            return EmailDeliveryResult(
                action_id,
                EmailDeliveryOutcome.FAILED,
                response_status=status,
                reason_code=(
                    failure_code if terminal_failure else "EMAIL_DELIVERY_FAILED"
                ),
            )
        if failure_code is not None:
            return EmailDeliveryResult(
                action_id,
                EmailDeliveryOutcome.UNCERTAIN,
                response_status=status,
                reason_code=failure_code,
            )
        return EmailDeliveryResult(
            action_id, EmailDeliveryOutcome.ACCEPTED, response_status=status
        )


def _raise_transport_failure(result: EmailDeliveryResult) -> None:
    if result.outcome in {
        EmailDeliveryOutcome.FAILED,
        EmailDeliveryOutcome.UNCERTAIN,
    }:
        raise EmailTransportError(result.reason_code or "EMAIL_TRANSPORT_ERROR")


def production_ports(
    settings: ModuleType | Any,
    transactions: TransactionFactory,
    *,
    http_post: HttpPost = httpx.post,
    clock: Callable[[], float] = time.time,
) -> EmailDeliveryPorts:
    """설정을 먼저 검증하고 graph에 주입할 두 callable을 만든다."""

    config = load_email_delivery_config(settings)
    service = EmailDeliveryService(
        config=config,
        transactions=transactions,
        http_post=http_post,
        clock=clock,
    )

    def notify(action_id: str) -> None:
        _raise_transport_failure(service.send_warning(action_id))

    def approval(action_id: str, approval_id: str) -> None:
        _raise_transport_failure(service.send_approval(action_id, approval_id))

    return EmailDeliveryPorts(
        service=service,
        notify_email=notify,
        approval_sender=approval,
    )


__all__ = [
    "EmailDeliveryConfig",
    "EmailDeliveryConfigError",
    "EmailDeliveryContractError",
    "EmailDeliveryOutcome",
    "EmailDeliveryPorts",
    "EmailDeliveryResult",
    "EmailDeliveryService",
    "EmailKind",
    "load_email_delivery_config",
    "production_ports",
]
