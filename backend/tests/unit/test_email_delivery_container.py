"""V5-C-4.3 EMAIL 상태 전이의 격리 PostgreSQL 회귀."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.agent import email_delivery as email
from app.agent import repository as repo
from app.common.enums import ActionCode, DeliveryChannel, DeliveryStatus
from tests.unit.test_agent_repository_container import (
    engine as repository_engine,
)
from tests.unit.test_agent_repository_container import (
    runtime_engine as repository_runtime_engine,
)

# 기존 module-scope PostgreSQL fixture를 재사용해 컨테이너 기동을 중복하지 않는다.
runtime_engine = repository_runtime_engine
engine = repository_engine

pytestmark = pytest.mark.container

HASH = "c" * 64
ACTION_ID = "ACT-c43000000000001"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        N8N_WEBHOOK_URL="http://localhost:5678/webhook/fdc-notify-email",
        N8N_WEBHOOK_TIMEOUT_SEC=30,
        N8N_WEBHOOK_SECRET="container-secret",
        AGENT_EMAIL_RECIPIENTS="operator@example.invalid",
    )


def _seed(engine: Any) -> None:
    with engine.begin() as connection:
        repo.insert_action_history(
            connection,
            action_id=ACTION_ID,
            lot_id="LOT-C43",
            chamber_id="EQP01-PM-C01",
            action_code=ActionCode.WARNING,
            reason="drift",
            created_at=datetime.now(UTC),
        )
        repo.insert_action_delivery(
            connection,
            action_id=ACTION_ID,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.WAITING,
            request_hash=HASH,
        )


def _read(engine: Any) -> repo.ActionDeliveryRow:
    with engine.connect() as connection:
        return repo.get_action_delivery(
            connection,
            action_id=ACTION_ID,
            channel=DeliveryChannel.EMAIL,
        )


def test_waiting_claim_commits_before_a_second_claim(engine: Any) -> None:
    _seed(engine)
    with engine.begin() as connection:
        claimed = repo.begin_email_delivery(connection, action_id=ACTION_ID)
    assert claimed is not None
    assert claimed.status is DeliveryStatus.SENDING
    assert claimed.attempt_count == 1
    assert claimed.started_at is not None

    with engine.begin() as connection:
        assert repo.begin_email_delivery(connection, action_id=ACTION_ID) is None
    assert _read(engine).attempt_count == 1


def test_service_commits_claim_before_http_and_noop_never_resends(engine: Any) -> None:
    _seed(engine)
    http_calls = 0

    def post(*args: Any, **kwargs: Any) -> Any:
        nonlocal http_calls
        http_calls += 1
        # 별도 connection에 보인다는 것은 claim transaction이 이미 commit됐다는 뜻이다.
        assert _read(engine).status is DeliveryStatus.SENDING
        return SimpleNamespace(status_code=200)

    service = email.production_ports(_settings(), engine.begin, http_post=post).service
    first = service.send_warning(ACTION_ID)
    second = service.send_warning(ACTION_ID)
    assert first.outcome is email.EmailDeliveryOutcome.ACCEPTED
    assert second.outcome is email.EmailDeliveryOutcome.NOOP
    assert http_calls == 1


@pytest.mark.parametrize(
    ("response", "status", "error", "audit_count"),
    [
        (SimpleNamespace(status_code=401), DeliveryStatus.FAILED, "WEBHOOK_401", 1),
        (SimpleNamespace(status_code=502), DeliveryStatus.SENDING, "WEBHOOK_502", 0),
        (httpx.ReadTimeout("slow"), DeliveryStatus.SENDING, "WEBHOOK_TIMEOUT", 0),
    ],
)
def test_service_response_matrix_on_real_postgres(
    engine: Any,
    response: Any,
    status: DeliveryStatus,
    error: str,
    audit_count: int,
) -> None:
    _seed(engine)

    def post(*args: Any, **kwargs: Any) -> Any:
        if isinstance(response, Exception):
            raise response
        return response

    result = email.production_ports(
        _settings(), engine.begin, http_post=post
    ).service.send_warning(ACTION_ID)
    assert result.reason_code == error
    row = _read(engine)
    assert row.status is status
    assert row.last_error == error
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM audit_log")).scalar_one()
            == audit_count
        )


@pytest.mark.parametrize("code", ["WEBHOOK_401", "WEBHOOK_422"])
def test_pre_smtp_rejection_fails_and_audits_atomically(engine: Any, code: str) -> None:
    _seed(engine)
    with engine.begin() as connection:
        repo.begin_email_delivery(connection, action_id=ACTION_ID)
    with engine.begin() as connection:
        row = repo.settle_email_delivery(
            connection,
            action_id=ACTION_ID,
            request_hash=HASH,
            failure_code=code,
            terminal_failure=True,
        )
    assert row.status is DeliveryStatus.FAILED
    assert row.completed_at is not None
    assert row.last_error == code
    with engine.connect() as connection:
        audit = connection.execute(
            text(
                "SELECT entity_id, after_json FROM audit_log "
                "WHERE event_type='ACTION_SEND_FAILED'"
            )
        ).one()
    assert audit.entity_id == ACTION_ID
    assert audit.after_json == {
        "channel": "EMAIL",
        "reason_code": code,
        "transport": "N8N_WEBHOOK",
    }


def test_failed_transition_and_audit_roll_back_together(engine: Any) -> None:
    _seed(engine)
    with engine.begin() as connection:
        repo.begin_email_delivery(connection, action_id=ACTION_ID)
    with pytest.raises(RuntimeError, match="rollback"):
        with engine.begin() as connection:
            repo.settle_email_delivery(
                connection,
                action_id=ACTION_ID,
                request_hash=HASH,
                failure_code="WEBHOOK_401",
                terminal_failure=True,
            )
            raise RuntimeError("rollback")
    assert _read(engine).status is DeliveryStatus.SENDING
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 0
        )


def test_uncertain_transport_keeps_sending_without_audit(engine: Any) -> None:
    _seed(engine)
    with engine.begin() as connection:
        repo.begin_email_delivery(connection, action_id=ACTION_ID)
    with engine.begin() as connection:
        row = repo.settle_email_delivery(
            connection,
            action_id=ACTION_ID,
            request_hash=HASH,
            failure_code="WEBHOOK_TIMEOUT",
            terminal_failure=False,
        )
    assert row.status is DeliveryStatus.SENDING
    assert row.last_error == "WEBHOOK_TIMEOUT"
    assert row.completed_at is None
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 0
        )


def test_accepted_response_keeps_sending_without_error(engine: Any) -> None:
    _seed(engine)
    with engine.begin() as connection:
        repo.begin_email_delivery(connection, action_id=ACTION_ID)
    with engine.begin() as connection:
        row = repo.settle_email_delivery(
            connection,
            action_id=ACTION_ID,
            request_hash=HASH,
            failure_code=None,
            terminal_failure=False,
        )
    assert row.status is DeliveryStatus.SENDING
    assert row.last_error is None
    assert row.completed_at is None


@pytest.mark.parametrize("terminal", [DeliveryStatus.SENT, DeliveryStatus.FAILED])
def test_callback_terminal_is_never_overwritten(
    engine: Any, terminal: DeliveryStatus
) -> None:
    _seed(engine)
    with engine.begin() as connection:
        repo.begin_email_delivery(connection, action_id=ACTION_ID)
        connection.execute(
            text(
                "UPDATE action_delivery SET status=:status, "
                "completed_at=clock_timestamp() "
                "WHERE action_id=:id AND channel='EMAIL'"
            ),
            {"id": ACTION_ID, "status": terminal.value},
        )
    with engine.begin() as connection:
        row = repo.settle_email_delivery(
            connection,
            action_id=ACTION_ID,
            request_hash=HASH,
            failure_code="WEBHOOK_502",
            terminal_failure=False,
        )
    assert row.status is terminal
    assert row.last_error is None
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 0
        )


def test_request_hash_mismatch_is_sanitized(engine: Any) -> None:
    _seed(engine)
    with engine.begin() as connection:
        repo.begin_email_delivery(connection, action_id=ACTION_ID)
    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.settle_email_delivery(
                connection,
                action_id=ACTION_ID,
                request_hash="d" * 64,
                failure_code=None,
                terminal_failure=False,
            )
    assert exc.value.code == "DELIVERY_REQUEST_HASH_MISMATCH"
