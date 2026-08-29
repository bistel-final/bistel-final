"""V5-C-4.4 signed callback HTTP/service contract."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.agent import delivery_callback as subject
from app.agent.delivery_signing import signed_delivery_headers
from app.agent.repository import (
    ActionDeliveryRow,
    DeliveryCallbackTransition,
    RepositoryConflict,
    RepositoryContractError,
    RepositoryNotFound,
    RepositoryRetryable,
    RepositoryUnavailable,
)
from app.common.enums import DeliveryChannel, DeliveryStatus
from app.common.exceptions import (
    DependencyNotReadyError,
    IdempotencyConflictError,
    InvalidRequestError,
    NotFoundError,
)
from app.main import app
from tests.unit.test_n8n_workflows import (
    _load_workflows,
    _result_json,
    _run_code,
    _valid_email_payload,
    _valid_mes_payload,
    _valid_result_record,
)

SECRET = b"callback-unit-secret"
NOW = 1_800_000_000
ACTION_ID = "ACT-c44000000000001"
HASH = "c" * 64
COMPLETED = datetime.fromtimestamp(NOW, tz=UTC)


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


class _ReceiveCounter:
    """Count only ASGI request-body messages consumed by the application."""

    def __init__(self, wrapped: Any) -> None:
        self.wrapped = wrapped
        self.body_messages = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def counted_receive() -> Any:
            message = await receive()
            if message["type"] == "http.request":
                self.body_messages += 1
            return message

        await self.wrapped(scope, counted_receive, send)


def _payload(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "event_id": "WF2:event-1",
        "channel": "EMAIL",
        "status": "SENT",
        "provider_message_id": "smtp-message-1",
        "request_hash": HASH,
        "completed_at": COMPLETED.isoformat(),
        "error_code": None,
    }
    values.update(overrides)
    return values


def _raw(**overrides: Any) -> bytes:
    return json.dumps(
        _payload(**overrides),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _headers(raw: bytes, *, timestamp: int = NOW) -> dict[str, str]:
    signature = hmac.new(
        SECRET,
        str(timestamp).encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return {
        "content-type": "application/json",
        "x-delivery-timestamp": str(timestamp),
        "x-delivery-signature": f"sha256={signature}",
    }


def _row(
    *,
    channel: DeliveryChannel = DeliveryChannel.EMAIL,
    status: DeliveryStatus = DeliveryStatus.SENT,
    provider_message_id: str | None = "smtp-message-1",
    last_error: str | None = None,
) -> ActionDeliveryRow:
    return ActionDeliveryRow(
        action_id=ACTION_ID,
        channel=channel,
        status=status,
        request_hash=HASH,
        attempt_count=1,
        provider_message_id=provider_message_id,
        started_at=datetime.fromtimestamp(NOW - 1, tz=UTC),
        completed_at=COMPLETED,
        last_error=last_error,
        result={"event_id": "WF2:event-1", "transport": "N8N_WEBHOOK"},
    )


@pytest.fixture
def http_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, _Transactions, _ReceiveCounter, dict[str, Any]]:
    transactions = _Transactions()
    service = subject.DeliveryCallbackService(
        secret=SECRET,
        transactions=transactions,
        clock=lambda: NOW,
    )
    observed: dict[str, Any] = {}

    def settle(_connection: Any, **kwargs: Any) -> DeliveryCallbackTransition:
        observed.update(kwargs)
        return DeliveryCallbackTransition(delivery=_row(), duplicate=False)

    monkeypatch.setattr(subject, "settle_delivery_callback", settle)
    app.dependency_overrides[subject.get_delivery_callback_service] = lambda: service
    wrapped = _ReceiveCounter(app)
    with TestClient(wrapped, raise_server_exceptions=False) as client:
        yield client, transactions, wrapped, observed
    app.dependency_overrides.pop(subject.get_delivery_callback_service, None)


def test_signed_request_reaches_exact_path_and_returns_eight_fields(
    http_boundary: tuple[TestClient, _Transactions, _ReceiveCounter, dict[str, Any]],
) -> None:
    client, transactions, reads, observed = http_boundary
    raw = _raw()
    response = client.post(
        f"/internal/actions/{ACTION_ID}/delivery",
        content=raw,
        headers=_headers(raw),
    )

    assert response.status_code == 200
    assert response.json() == {
        "action_id": ACTION_ID,
        "channel": "EMAIL",
        "status": "SENT",
        "request_hash": HASH,
        "provider_message_id": "smtp-message-1",
        "completed_at": COMPLETED.isoformat().replace("+00:00", "Z"),
        "error_code": None,
        "duplicate": False,
    }
    assert observed["action_id"] == ACTION_ID
    assert transactions.calls == 1
    assert reads.body_messages == 1


def test_verified_first_duplicate_and_conflict_are_recorded_at_http_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: list[dict[str, Any]] = []

    class Trail:
        def append(self, **values: Any) -> bool:
            records.append(values)
            return True

    transactions = _Transactions()
    service = subject.DeliveryCallbackService(
        secret=SECRET,
        transactions=transactions,
        clock=lambda: NOW,
        trail=Trail(),  # type: ignore[arg-type]
    )
    calls = 0

    def settle(_connection: Any, **_kwargs: Any) -> DeliveryCallbackTransition:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RepositoryConflict("DELIVERY_REQUEST_HASH_MISMATCH")
        return DeliveryCallbackTransition(delivery=_row(), duplicate=calls == 2)

    monkeypatch.setattr(subject, "settle_delivery_callback", settle)
    app.dependency_overrides[subject.get_delivery_callback_service] = lambda: service
    raw = _raw()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            responses = [
                client.post(
                    f"/internal/actions/{ACTION_ID}/delivery",
                    content=raw,
                    headers=_headers(raw),
                )
                for _ in range(3)
            ]
    finally:
        app.dependency_overrides.pop(subject.get_delivery_callback_service, None)

    assert [response.status_code for response in responses] == [200, 200, 409]
    assert records == [
        {
            "action_id": ACTION_ID,
            "channel": DeliveryChannel.EMAIL,
            "status": DeliveryStatus.SENT,
            "duplicate": False,
            "http_status": 200,
        },
        {
            "action_id": ACTION_ID,
            "channel": DeliveryChannel.EMAIL,
            "status": DeliveryStatus.SENT,
            "duplicate": True,
            "http_status": 200,
        },
        {
            "action_id": ACTION_ID,
            "channel": DeliveryChannel.EMAIL,
            "status": None,
            "duplicate": None,
            "http_status": 409,
        },
    ]


def test_trail_write_failure_does_not_change_success_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingTrail:
        def append(self, **_values: Any) -> bool:
            return False

    service = subject.DeliveryCallbackService(
        secret=SECRET,
        transactions=_Transactions(),
        clock=lambda: NOW,
        trail=FailingTrail(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        subject,
        "settle_delivery_callback",
        lambda *_args, **_kwargs: DeliveryCallbackTransition(
            delivery=_row(), duplicate=False
        ),
    )
    app.dependency_overrides[subject.get_delivery_callback_service] = lambda: service
    raw = _raw()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/internal/actions/{ACTION_ID}/delivery",
                content=raw,
                headers=_headers(raw),
            )
    finally:
        app.dependency_overrides.pop(subject.get_delivery_callback_service, None)

    assert response.status_code == 200
    assert response.json()["duplicate"] is False


@pytest.mark.parametrize(
    ("headers", "expected_reads"),
    [
        ({"content-type": "application/json"}, 0),
        (
            {
                "content-type": "application/json",
                "x-delivery-timestamp": "not-integer",
                "x-delivery-signature": "sha256=" + "0" * 64,
            },
            0,
        ),
        (
            {
                "content-type": "application/json",
                "x-delivery-timestamp": str(NOW - 301),
                "x-delivery-signature": "sha256=" + "0" * 64,
            },
            0,
        ),
        (
            {
                "content-type": "application/json",
                "x-delivery-timestamp": str(NOW),
                "x-delivery-signature": "sha256=" + "A" * 64,
            },
            0,
        ),
    ],
)
def test_header_precheck_is_401_without_consuming_body(
    http_boundary: tuple[TestClient, _Transactions, _ReceiveCounter, dict[str, Any]],
    headers: dict[str, str],
    expected_reads: int,
) -> None:
    client, transactions, reads, _ = http_boundary
    before = reads.body_messages
    response = client.post(
        f"/internal/actions/{ACTION_ID}/delivery",
        content=_raw(),
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": "UNAUTHORIZED",
        "message": "인증 정보가 올바르지 않습니다.",
        "details": {},
    }
    assert reads.body_messages - before == expected_reads
    assert transactions.calls == 0


def test_hmac_mismatch_reads_raw_once_then_returns_sanitized_401(
    http_boundary: tuple[TestClient, _Transactions, _ReceiveCounter, dict[str, Any]],
) -> None:
    client, transactions, reads, _ = http_boundary
    raw = _raw()
    headers = _headers(raw)
    headers["x-delivery-signature"] = "sha256=" + "0" * 64
    before = reads.body_messages

    response = client.post(
        f"/internal/actions/{ACTION_ID}/delivery",
        content=raw,
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert reads.body_messages - before == 1
    assert transactions.calls == 0


@pytest.mark.parametrize("age", [-300, 300])
def test_replay_window_includes_exact_boundary(age: int) -> None:
    service = subject.DeliveryCallbackService(
        secret=SECRET,
        transactions=_Transactions(),
        clock=lambda: NOW,
    )
    timestamp = NOW - age
    raw = _raw()
    headers = _headers(raw, timestamp=timestamp)
    verified = service.verify_headers(
        headers["x-delivery-timestamp"],
        headers["x-delivery-signature"],
    )
    service.verify_signature(verified, raw)


def test_signature_verification_uses_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = subject.DeliveryCallbackService(
        secret=SECRET,
        transactions=_Transactions(),
        clock=lambda: NOW,
    )
    raw = _raw()
    headers = _headers(raw)
    verified = service.verify_headers(
        headers["x-delivery-timestamp"],
        headers["x-delivery-signature"],
    )
    calls: list[tuple[str, str]] = []

    def compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(subject.hmac, "compare_digest", compare)
    service.verify_signature(verified, raw)
    assert len(calls) == 1


def test_signature_uses_the_exact_timestamp_header_text() -> None:
    service = subject.DeliveryCallbackService(
        secret=SECRET,
        transactions=_Transactions(),
        clock=lambda: NOW,
    )
    raw = _raw()
    timestamp_text = f"0{NOW}"
    signature = (
        "sha256="
        + hmac.new(
            SECRET,
            timestamp_text.encode("ascii") + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
    )
    verified = service.verify_headers(timestamp_text, signature)
    service.verify_signature(verified, raw)


@pytest.mark.parametrize("bad_action_id", ["   ", "A" * 21])
def test_invalid_path_id_is_422_after_auth_before_transaction(
    http_boundary: tuple[TestClient, _Transactions, _ReceiveCounter, dict[str, Any]],
    bad_action_id: str,
) -> None:
    client, transactions, _, _ = http_boundary
    raw = _raw()
    response = client.post(
        f"/internal/actions/{quote(bad_action_id, safe='')}/delivery",
        content=raw,
        headers=_headers(raw),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert transactions.calls == 0


def test_valid_path_id_is_trimmed_after_auth(
    http_boundary: tuple[TestClient, _Transactions, _ReceiveCounter, dict[str, Any]],
) -> None:
    client, transactions, _, observed = http_boundary
    raw = _raw()
    encoded = quote(f"  {ACTION_ID}  ", safe="")
    response = client.post(
        f"/internal/actions/{encoded}/delivery",
        content=raw,
        headers=_headers(raw),
    )
    assert response.status_code == 200
    assert observed["action_id"] == ACTION_ID
    assert transactions.calls == 1


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/xml"])
def test_non_json_content_type_is_422_before_transaction(
    http_boundary: tuple[TestClient, _Transactions, _ReceiveCounter, dict[str, Any]],
    content_type: str | None,
) -> None:
    client, transactions, _, _ = http_boundary
    raw = _raw()
    headers = _headers(raw)
    if content_type is None:
        headers.pop("content-type")
    else:
        headers["content-type"] = content_type
    response = client.post(
        f"/internal/actions/{ACTION_ID}/delivery",
        content=raw,
        headers=headers,
    )
    assert response.status_code == 422
    assert transactions.calls == 0


def test_authenticated_invalid_json_is_422_after_one_read_before_transaction(
    http_boundary: tuple[TestClient, _Transactions, _ReceiveCounter, dict[str, Any]],
) -> None:
    client, transactions, reads, _ = http_boundary
    raw = b"{invalid-json"
    before = reads.body_messages
    response = client.post(
        f"/internal/actions/{ACTION_ID}/delivery",
        content=raw,
        headers=_headers(raw),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert reads.body_messages - before == 1
    assert transactions.calls == 0


@pytest.mark.parametrize("missing", sorted(_payload()))
def test_each_of_seven_fields_is_required(missing: str) -> None:
    payload = _payload()
    payload.pop(missing)
    raw = json.dumps(payload, separators=(",", ":")).encode()
    with pytest.raises(InvalidRequestError):
        subject.parse_callback_body(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff\xfe",
        b"{bad-json",
        b"[]",
        b'"scalar"',
        json.dumps({**_payload(), "extra": True}).encode(),
        _raw(completed_at="2026-08-29T10:00:00"),
        _raw(request_hash="A" * 64),
        _raw(event_id="   "),
        _raw(status="SENT", error_code="unexpected"),
        _raw(status="FAILED", provider_message_id=None, error_code=None),
        _raw(status="FAILED", provider_message_id="   ", error_code="failed-lower"),
    ],
)
def test_invalid_body_shapes_fail_before_transaction(raw: bytes) -> None:
    with pytest.raises(InvalidRequestError):
        subject.parse_callback_body(raw)


def test_failed_allows_nullable_or_nonblank_provider_and_nonempty_external_code() -> (
    None
):
    nullable = subject.parse_callback_body(
        _raw(
            status="FAILED",
            provider_message_id=None,
            error_code="provider-rejected-lowercase",
        )
    )
    nonblank = subject.parse_callback_body(
        _raw(
            status="FAILED",
            provider_message_id=" provider-1 ",
            error_code="provider-rejected-lowercase",
        )
    )
    assert nullable.provider_message_id is None
    assert nonblank.provider_message_id == "provider-1"
    assert nonblank.error_code == "provider-rejected-lowercase"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RepositoryNotFound("DELIVERY_NOT_FOUND"), NotFoundError),
        (RepositoryConflict("DELIVERY_NOT_SENDING"), IdempotencyConflictError),
        (RepositoryRetryable("LOCK_TIMEOUT"), DependencyNotReadyError),
        (RepositoryUnavailable("DATABASE_UNAVAILABLE"), DependencyNotReadyError),
        (RepositoryContractError("DATA_CONTRACT"), DependencyNotReadyError),
    ],
)
def test_repository_errors_map_to_exact_sanitized_http_contract(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: type[Exception],
) -> None:
    transactions = _Transactions()
    service = subject.DeliveryCallbackService(
        secret=SECRET,
        transactions=transactions,
        clock=lambda: NOW,
    )

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(subject, "settle_delivery_callback", fail)
    callback = subject.parse_callback_body(_raw())
    with pytest.raises(expected) as caught:
        service.settle(action_id=ACTION_ID, callback=callback)
    assert str(caught.value) not in {str(error), error.args[0]}


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (RepositoryNotFound("DELIVERY_NOT_FOUND"), 404, "RESOURCE_NOT_FOUND"),
        (RepositoryConflict("DELIVERY_NOT_SENDING"), 409, "IDEMPOTENCY_CONFLICT"),
        (RepositoryRetryable("LOCK_TIMEOUT"), 503, "DEPENDENCY_NOT_READY"),
        (
            RepositoryUnavailable("DATABASE_UNAVAILABLE"),
            503,
            "DEPENDENCY_NOT_READY",
        ),
        (RepositoryContractError("DATA_CONTRACT"), 503, "DEPENDENCY_NOT_READY"),
    ],
)
def test_repository_mapping_crosses_the_real_fastapi_handler(
    http_boundary: tuple[TestClient, _Transactions, _ReceiveCounter, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
    code: str,
) -> None:
    client, _, _, _ = http_boundary

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(subject, "settle_delivery_callback", fail)
    raw = _raw()
    response = client.post(
        f"/internal/actions/{ACTION_ID}/delivery",
        content=raw,
        headers=_headers(raw),
    )
    assert response.status_code == status
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message", "details"}
    assert error.args[0] not in response.text


def test_missing_callback_secret_fails_closed_without_secret_echo() -> None:
    with pytest.raises(subject.DeliveryCallbackConfigError) as caught:
        subject.load_callback_secret(SimpleNamespace(N8N_WEBHOOK_SECRET="  "))
    assert "secret" not in str(caught.value).lower()


def test_missing_callback_secret_returns_503_without_reading_body_or_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.dependency_overrides.pop(subject.get_delivery_callback_service, None)
    monkeypatch.setattr(subject.config, "N8N_WEBHOOK_SECRET", None)
    wrapped = _ReceiveCounter(app)
    raw = _raw()
    with TestClient(wrapped, raise_server_exceptions=False) as client:
        response = client.post(
            f"/internal/actions/{ACTION_ID}/delivery",
            content=raw,
            headers=_headers(raw),
        )
    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_NOT_READY"
    assert wrapped.body_messages == 0


def _verify_builder_result(
    service: subject.DeliveryCallbackService,
    result: dict[str, Any],
) -> subject.DeliveryCallbackRequest:
    raw = result["callback_raw_body"].encode("utf-8")
    verified = service.verify_headers(
        result["callback_timestamp"],
        result["callback_signature"],
    )
    service.verify_signature(verified, raw)
    path_action_id = unquote(
        urlsplit(result["callback_url"])
        .path.split("/internal/actions/", 1)[1]
        .rsplit("/delivery", 1)[0]
    )
    assert subject.normalize_action_id(path_action_id)
    return subject.parse_callback_body(raw)


def test_real_wf2_wf3_wf4_builders_pass_verifier_and_exact_dto() -> None:
    workflows = _load_workflows()
    service = subject.DeliveryCallbackService(
        secret=b"unit-test-secret",
        transactions=_Transactions(),
        clock=lambda: NOW,
    )
    env = {
        "N8N_WEBHOOK_SECRET": "unit-test-secret",
        "BACKEND_BASE_URL": "https://backend.example.invalid",
    }

    email = workflows["WF2-notify-email.json"]
    email_payload = _valid_email_payload()
    for result_item, expected_status in (
        ({"json": {"messageId": "smtp-message-1"}}, DeliveryStatus.SENT),
        ({"json": {}}, DeliveryStatus.FAILED),
    ):
        built = _result_json(
            _run_code(
                email,
                "Build Email Callback",
                input_item=result_item,
                env=env,
                nodes={"Validate Email Payload": {"json": {"payload": email_payload}}},
                now_seconds=NOW,
            )
        )
        callback = _verify_builder_result(service, built)
        assert callback.channel is DeliveryChannel.EMAIL
        assert callback.status is expected_status

    mes = workflows["WF3-mes-hold.json"]
    mes_payload = _valid_mes_payload()
    built_mes_failure = _result_json(
        _run_code(
            mes,
            "Build Kafka Failure Callback",
            input_item={"json": {"error": "publish failed"}},
            env=env,
            nodes={"Prepare Kafka Event": {"json": mes_payload}},
            now_seconds=NOW,
        )
    )
    mes_failure = _verify_builder_result(service, built_mes_failure)
    assert mes_failure.channel is DeliveryChannel.MES_MOCK
    assert mes_failure.status is DeliveryStatus.FAILED
    assert mes_failure.provider_message_id is None

    result_workflow = workflows["WF4-result-writeback.json"]
    for status in ("SENT", "FAILED"):
        validated = _result_json(
            _run_code(
                result_workflow,
                "Validate MES Result",
                input_item=_valid_result_record(status),
            )
        )
        built = _result_json(
            _run_code(
                result_workflow,
                "Build Result Callback",
                input_item={"json": validated},
                env=env,
                now_seconds=NOW,
            )
        )
        callback = _verify_builder_result(service, built)
        assert callback.channel is DeliveryChannel.MES_MOCK
        assert callback.status.value == status


def test_c43_signer_uses_the_same_hmac_algorithm() -> None:
    raw = _raw()
    outbound = signed_delivery_headers(raw, SECRET, NOW)
    service = subject.DeliveryCallbackService(
        secret=SECRET,
        transactions=_Transactions(),
        clock=lambda: NOW,
    )
    verified = service.verify_headers(
        outbound["x-delivery-timestamp"],
        outbound["x-delivery-signature"],
    )
    service.verify_signature(verified, raw)


def test_app_openapi_contains_exact_internal_callback_path() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/internal/actions/{action_id}/delivery"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DeliveryResult"
    }
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["required"] == [
        "event_id",
        "channel",
        "status",
        "provider_message_id",
        "request_hash",
        "completed_at",
        "error_code",
    ]
    assert set(request_schema["properties"]) == set(request_schema["required"])
    assert request_schema["properties"]["status"]["enum"] == ["SENT", "FAILED"]
    assert "$defs" not in request_schema
    assert "$ref" not in json.dumps(request_schema)
