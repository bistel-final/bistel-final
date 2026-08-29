"""Signed n8n/Kafka delivery write-back boundary (``V5-C-4.4``).

Authentication is completed over the exact raw request bytes before JSON parsing or
database access.  The repository owns the terminal row-lock transition; this module
owns HTTP-facing validation and sanitized error mapping.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import Annotated, Any, Literal, NoReturn

from pydantic import StringConstraints, ValidationError, model_validator
from sqlalchemy.exc import SQLAlchemyError

from app.agent.repository import (
    AgentRepositoryError,
    DeliveryCallbackTransition,
    RepositoryConflict,
    RepositoryContractError,
    RepositoryNotFound,
    RepositoryRetryable,
    RepositoryUnavailable,
    settle_delivery_callback,
)
from app.agent.tools import TransactionFactory
from app.common import config
from app.common.db import get_app_engine
from app.common.enums import DeliveryChannel, DeliveryStatus
from app.common.exceptions import (
    DependencyNotReadyError,
    IdempotencyConflictError,
    InvalidRequestError,
    NotFoundError,
    UnauthorizedError,
)
from app.common.ids import ACTION_ID_MAX_LENGTH
from app.common.schemas import ApiModel

REPLAY_WINDOW_SECONDS = 300
_TIMESTAMP = re.compile(r"^[0-9]{1,20}$")
_SIGNATURE = re.compile(r"^sha256=[0-9a-f]{64}$")
_HASH64 = re.compile(r"^[0-9a-f]{64}$")

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
ActionId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=ACTION_ID_MAX_LENGTH,
    ),
]
RequestHash = Annotated[
    str,
    StringConstraints(min_length=64, max_length=64, pattern=_HASH64.pattern),
]
TerminalDeliveryStatus = Literal[DeliveryStatus.SENT, DeliveryStatus.FAILED]


class DeliveryCallbackRequest(ApiModel):
    """Exact seven-field callback body from WF2/WF3/WF4."""

    event_id: NonEmptyText
    channel: DeliveryChannel
    status: TerminalDeliveryStatus
    provider_message_id: NonEmptyText | None
    request_hash: RequestHash
    completed_at: datetime
    error_code: NonEmptyText | None

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> DeliveryCallbackRequest:
        if self.completed_at.utcoffset() is None:
            raise ValueError("completed_at requires a UTC offset")
        if self.status is DeliveryStatus.SENT:
            if self.provider_message_id is None or self.error_code is not None:
                raise ValueError("SENT callback shape is invalid")
            return self
        if self.status is DeliveryStatus.FAILED:
            if self.error_code is None:
                raise ValueError("FAILED callback requires error_code")
            return self
        raise ValueError("callback status must be SENT or FAILED")


class DeliveryResult(ApiModel):
    action_id: ActionId
    channel: DeliveryChannel
    status: TerminalDeliveryStatus
    request_hash: RequestHash
    provider_message_id: NonEmptyText | None
    completed_at: datetime
    error_code: NonEmptyText | None
    duplicate: bool

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> DeliveryResult:
        if self.completed_at.utcoffset() is None:
            raise ValueError("completed_at requires a UTC offset")
        if self.status is DeliveryStatus.SENT:
            if self.provider_message_id is None or self.error_code is not None:
                raise ValueError("stored SENT delivery shape is invalid")
            return self
        if self.status is DeliveryStatus.FAILED:
            if self.error_code is None:
                raise ValueError("stored FAILED delivery shape is invalid")
            return self
        raise ValueError("stored delivery is not terminal")


def callback_request_openapi_schema() -> dict[str, Any]:
    """Inline Pydantic local refs for a valid raw-body OpenAPI schema.

    The endpoint cannot declare a normal FastAPI body parameter because FastAPI would
    parse it before HMAC authentication.  Pydantic emits ``#/$defs`` references, but
    nesting that document under ``openapi_extra.requestBody`` would make those refs
    point at the OpenAPI root.  Inline the small local enum definitions instead.
    """

    schema = DeliveryCallbackRequest.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.removeprefix("#/$defs/")
            target = definitions.get(name)
            if not isinstance(target, dict):
                raise RuntimeError("DELIVERY_CALLBACK_OPENAPI_REF_INVALID")
            siblings = {key: nested for key, nested in value.items() if key != "$ref"}
            return expand({**target, **siblings})
        return {key: expand(nested) for key, nested in value.items()}

    expanded = expand(schema)
    if not isinstance(expanded, dict):
        raise RuntimeError("DELIVERY_CALLBACK_OPENAPI_SCHEMA_INVALID")
    return expanded


class DeliveryCallbackConfigError(RuntimeError):
    """Secret value is never included in the error text or representation."""

    def __init__(self) -> None:
        super().__init__("DELIVERY_CALLBACK_CONFIG_INVALID")


class DeliveryCallbackStorageConfigError(RuntimeError):
    """Runtime DB configuration failed before a connection was opened."""

    def __init__(self) -> None:
        super().__init__("DELIVERY_CALLBACK_STORAGE_NOT_READY")


@dataclass(frozen=True, slots=True)
class VerifiedDeliveryHeaders:
    timestamp_text: str
    timestamp: int
    signature: str


def load_callback_secret(settings: ModuleType | Any = config) -> bytes:
    """Read only ``N8N_WEBHOOK_SECRET`` and fail closed before body/DB access."""

    raw = getattr(settings, "N8N_WEBHOOK_SECRET", None)
    if not isinstance(raw, str) or not raw.strip():
        raise DeliveryCallbackConfigError()
    return raw.strip().encode("utf-8")


def normalize_action_id(action_id: str) -> str:
    if not isinstance(action_id, str):
        raise InvalidRequestError()
    normalized = action_id.strip()
    if not normalized or len(normalized) > ACTION_ID_MAX_LENGTH:
        raise InvalidRequestError()
    return normalized


def validate_json_content_type(value: str | None) -> None:
    if not isinstance(value, str):
        raise InvalidRequestError()
    media_type = value.partition(";")[0].strip().lower()
    if media_type != "application/json":
        raise InvalidRequestError()


def parse_callback_body(raw: bytes) -> DeliveryCallbackRequest:
    """Decode one exact UTF-8 JSON object without echoing invalid input."""

    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded)
        if not isinstance(payload, dict):
            raise ValueError("callback body is not an object")
        return DeliveryCallbackRequest.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
        raise InvalidRequestError() from None


def _delivery_result(
    transition: DeliveryCallbackTransition,
) -> DeliveryResult:
    row = transition.delivery
    try:
        return DeliveryResult(
            action_id=row.action_id,
            channel=row.channel,
            status=row.status,
            request_hash=row.request_hash,
            provider_message_id=row.provider_message_id,
            completed_at=row.completed_at,
            error_code=row.last_error,
            duplicate=transition.duplicate,
        )
    except ValidationError:
        # DTO input already passed. An invalid stored terminal projection is a server
        # invariant failure, never a caller 422.
        raise DependencyNotReadyError() from None


def _raise_repository_error(error: AgentRepositoryError) -> NoReturn:
    if isinstance(error, RepositoryNotFound):
        raise NotFoundError() from None
    if isinstance(error, RepositoryConflict):
        raise IdempotencyConflictError() from None
    if isinstance(
        error,
        RepositoryUnavailable | RepositoryRetryable | RepositoryContractError,
    ):
        raise DependencyNotReadyError() from None
    raise DependencyNotReadyError() from None


class DeliveryCallbackService:
    def __init__(
        self,
        *,
        secret: bytes,
        transactions: TransactionFactory,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._secret = secret
        self._transactions = transactions
        self._clock = clock

    def verify_headers(
        self,
        timestamp_text: str | None,
        signature: str | None,
    ) -> VerifiedDeliveryHeaders:
        if (
            not isinstance(timestamp_text, str)
            or _TIMESTAMP.fullmatch(timestamp_text) is None
            or not isinstance(signature, str)
            or _SIGNATURE.fullmatch(signature) is None
        ):
            raise UnauthorizedError()
        timestamp = int(timestamp_text)
        if abs(float(self._clock()) - timestamp) > REPLAY_WINDOW_SECONDS:
            raise UnauthorizedError()
        return VerifiedDeliveryHeaders(
            timestamp_text=timestamp_text,
            timestamp=timestamp,
            signature=signature,
        )

    def verify_signature(
        self,
        headers: VerifiedDeliveryHeaders,
        raw: bytes,
    ) -> None:
        signed = headers.timestamp_text.encode("ascii") + b"." + raw
        expected = (
            "sha256="
            + hmac.new(
                self._secret,
                signed,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, headers.signature):
            raise UnauthorizedError()

    def settle(
        self,
        *,
        action_id: str,
        callback: DeliveryCallbackRequest,
    ) -> DeliveryResult:
        try:
            with self._transactions() as connection:
                transition = settle_delivery_callback(
                    connection,
                    action_id=action_id,
                    channel=callback.channel,
                    status=callback.status,
                    provider_message_id=callback.provider_message_id,
                    request_hash=callback.request_hash,
                    completed_at=callback.completed_at,
                    error_code=callback.error_code,
                    event_id=callback.event_id,
                )
        except AgentRepositoryError as exc:
            _raise_repository_error(exc)
        except (DeliveryCallbackStorageConfigError, SQLAlchemyError):
            raise DependencyNotReadyError() from None
        return _delivery_result(transition)


@contextmanager
def _production_transactions() -> Iterator[Any]:
    try:
        engine = get_app_engine()
    except RuntimeError as exc:
        raise DeliveryCallbackStorageConfigError() from exc
    with engine.begin() as connection:
        yield connection


def get_delivery_callback_service() -> DeliveryCallbackService:
    """FastAPI dependency; config failure occurs before request body consumption."""

    try:
        secret = load_callback_secret()
    except DeliveryCallbackConfigError:
        raise DependencyNotReadyError() from None
    return DeliveryCallbackService(
        secret=secret,
        transactions=_production_transactions,
    )
