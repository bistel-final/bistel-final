"""Signed n8n/Kafka delivery write-back boundary (``V5-C-4.4``).

Authentication is completed over the exact raw request bytes before JSON parsing or
database access.  The repository owns the terminal row-lock transition; this module
owns HTTP-facing validation and sanitized error mapping.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
_TRAIL_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TRAIL_FIELDS = frozenset(
    {"ts", "action_id", "channel", "status", "duplicate", "http_status"}
)
_TRAIL_LINE_MAX_BYTES = 512
logger = logging.getLogger(__name__)

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


class DeliveryCallbackTrailConfigError(RuntimeError):
    """Trail path/run 설정의 실값을 노출하지 않는 기동 오류."""

    def __init__(self, code: str = "DELIVERY_CALLBACK_TRAIL_CONFIG_INVALID") -> None:
        super().__init__(code)
        self.code = code


class DeliveryCallbackTrail:
    """Run-scoped append-only callback 증적.

    파일은 애플리케이션 조립 때 exclusive create하고 같은 프로세스가 가진 fd에만
    append한다. 각 JSONL record는 512B 이하의 단일 ``os.write``라 concurrent callback도
    서로 덮어쓰지 않는다.
    """

    def __init__(
        self,
        *,
        file_descriptor: int,
        path: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._file_descriptor = file_descriptor
        self.path = path
        self._clock = clock

    @classmethod
    def from_settings(
        cls,
        settings: ModuleType | Any = config,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> DeliveryCallbackTrail | None:
        raw_dir = getattr(settings, "DELIVERY_CALLBACK_TRAIL_DIR", None)
        raw_run_id = getattr(settings, "DELIVERY_CALLBACK_TRAIL_RUN_ID", None)
        directory = raw_dir.strip() if isinstance(raw_dir, str) else None
        run_id = raw_run_id.strip() if isinstance(raw_run_id, str) else None
        directory = directory or None
        run_id = run_id or None
        if directory is None and run_id is None:
            return None
        if (
            directory is None
            or run_id is None
            or _TRAIL_RUN_ID.fullmatch(run_id) is None
        ):
            raise DeliveryCallbackTrailConfigError()

        root = Path(directory)
        try:
            metadata = root.lstat()
            if (
                not root.is_absolute()
                or stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or root.resolve(strict=True) != root
                or (os.geteuid() != 0 and metadata.st_uid != os.geteuid())
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise DeliveryCallbackTrailConfigError()
        except (OSError, RuntimeError) as exc:
            raise DeliveryCallbackTrailConfigError() from exc

        path = root / f"trail-{run_id}.jsonl"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid():
                raise DeliveryCallbackTrailConfigError()
            os.fchmod(descriptor, 0o600)
        except (OSError, DeliveryCallbackTrailConfigError) as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise DeliveryCallbackTrailConfigError() from exc
        return cls(file_descriptor=descriptor, path=path, clock=clock)

    def append(
        self,
        *,
        action_id: str,
        channel: DeliveryChannel,
        status: DeliveryStatus | None,
        duplicate: bool | None,
        http_status: int,
    ) -> bool:
        record = {
            "ts": self._clock().astimezone(UTC).isoformat(),
            "action_id": action_id,
            "channel": channel.value,
            "status": None if status is None else status.value,
            "duplicate": duplicate,
            "http_status": http_status,
        }
        if set(record) != _TRAIL_FIELDS:  # pragma: no cover - local invariant
            return False
        encoded = (
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > _TRAIL_LINE_MAX_BYTES:
            logger.error("delivery callback trail write failed code=LINE_TOO_LARGE")
            return False
        try:
            written = os.write(self._file_descriptor, encoded)
            if written != len(encoded):
                raise OSError("partial callback trail write")
        except OSError:
            logger.error("delivery callback trail write failed code=WRITE_FAILED")
            return False
        return True

    def close(self) -> None:
        os.close(self._file_descriptor)


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
        trail: DeliveryCallbackTrail | None = None,
    ) -> None:
        self._secret = secret
        self._transactions = transactions
        self._clock = clock
        self._trail = trail

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

    def record_http_result(
        self,
        *,
        action_id: str,
        channel: DeliveryChannel,
        result: DeliveryResult | None,
        http_status: int,
    ) -> None:
        if self._trail is None:
            return
        self._trail.append(
            action_id=action_id,
            channel=channel,
            status=None if result is None else DeliveryStatus(result.status),
            duplicate=None if result is None else result.duplicate,
            http_status=http_status,
        )


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
        trail=_PRODUCTION_CALLBACK_TRAIL,
    )


# 설정 pair·run ID·경로·기존 파일을 요청 처리 전, 애플리케이션 import 단계에서
# fail-closed한다. 기본 미설정이면 파일도 코드 경로도 생기지 않는다.
_PRODUCTION_CALLBACK_TRAIL = DeliveryCallbackTrail.from_settings(config)
