"""V5-C-4.4 terminal callback transitions on isolated PostgreSQL 16."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import text

from app.agent import repository as repo
from app.common.audit import AuditContractError
from app.common.enums import ActionCode, DeliveryChannel, DeliveryStatus
from tests.unit.test_agent_repository_container import (
    engine as repository_engine,
)
from tests.unit.test_agent_repository_container import (
    runtime_engine as repository_runtime_engine,
)

runtime_engine = repository_runtime_engine
engine = repository_engine

pytestmark = pytest.mark.container

ACTION_ID = "ACT-c44000000000001"
HASH = "d" * 64


def _seed_sending(engine: Any, channel: DeliveryChannel) -> datetime:
    action_code = (
        ActionCode.WARNING if channel is DeliveryChannel.EMAIL else ActionCode.EQP_HOLD
    )
    initial_status = (
        DeliveryStatus.WAITING
        if channel is DeliveryChannel.EMAIL
        else DeliveryStatus.BLOCKED
    )
    with engine.begin() as connection:
        repo.insert_action_history(
            connection,
            action_id=ACTION_ID,
            lot_id="LOT-C44",
            chamber_id="EQP01-PM-C01",
            action_code=action_code,
            reason="callback contract",
            created_at=datetime.now(UTC),
        )
        repo.insert_action_delivery(
            connection,
            action_id=ACTION_ID,
            channel=channel,
            status=initial_status,
            request_hash=HASH,
        )
        if channel is DeliveryChannel.EMAIL:
            claimed = repo.begin_email_delivery(connection, action_id=ACTION_ID)
            assert claimed is not None and claimed.started_at is not None
            started_at = claimed.started_at
        else:
            started_at = connection.execute(
                text(
                    "UPDATE action_delivery SET status='SENDING', "
                    "attempt_count=1, started_at=clock_timestamp() "
                    "WHERE action_id=:id AND channel='MES_MOCK' "
                    "RETURNING started_at"
                ),
                {"id": ACTION_ID},
            ).scalar_one()
    return started_at


def _callback(
    connection: Any,
    *,
    channel: DeliveryChannel,
    status: DeliveryStatus,
    completed_at: datetime,
    request_hash: str = HASH,
    event_id: str = "WF4:event-1",
) -> repo.DeliveryCallbackTransition:
    sent = status is DeliveryStatus.SENT
    return repo.settle_delivery_callback(
        connection,
        action_id=ACTION_ID,
        channel=channel,
        status=status,
        provider_message_id=(
            f"provider:{channel.value}" if sent else "provider:failed"
        ),
        request_hash=request_hash,
        completed_at=completed_at,
        error_code=None if sent else "provider-rejected-lowercase",
        event_id=event_id,
    )


@pytest.mark.parametrize(
    ("channel", "status", "event", "transport"),
    [
        (
            DeliveryChannel.EMAIL,
            DeliveryStatus.SENT,
            "ACTION_SENT",
            "N8N_WEBHOOK",
        ),
        (
            DeliveryChannel.EMAIL,
            DeliveryStatus.FAILED,
            "ACTION_SEND_FAILED",
            "N8N_WEBHOOK",
        ),
        (
            DeliveryChannel.MES_MOCK,
            DeliveryStatus.SENT,
            "ACTION_SENT",
            "KAFKA",
        ),
        (
            DeliveryChannel.MES_MOCK,
            DeliveryStatus.FAILED,
            "ACTION_SEND_FAILED",
            "KAFKA",
        ),
    ],
)
def test_terminal_callback_and_audit_commit_as_one_uow(
    engine: Any,
    channel: DeliveryChannel,
    status: DeliveryStatus,
    event: str,
    transport: str,
) -> None:
    started_at = _seed_sending(engine, channel)
    completed_at = started_at + timedelta(seconds=1)
    with engine.begin() as connection:
        transition = _callback(
            connection,
            channel=channel,
            status=status,
            completed_at=completed_at,
        )

    assert transition.duplicate is False
    row = transition.delivery
    assert row.status is status
    assert row.completed_at == completed_at
    assert row.provider_message_id == (
        f"provider:{channel.value}"
        if status is DeliveryStatus.SENT
        else "provider:failed"
    )
    assert row.last_error == (
        None if status is DeliveryStatus.SENT else "provider-rejected-lowercase"
    )
    assert row.result == {"event_id": "WF4:event-1", "transport": transport}

    with engine.connect() as connection:
        audit = connection.execute(
            text(
                "SELECT event_type, entity_id, after_json FROM audit_log "
                "ORDER BY audit_id"
            )
        ).one()
    assert audit.event_type == event
    assert audit.entity_id == ACTION_ID
    assert audit.after_json == {"channel": channel.value, "transport": transport}


@pytest.mark.parametrize("status", [DeliveryStatus.SENT, DeliveryStatus.FAILED])
def test_same_hash_terminal_replay_returns_stored_row_without_second_audit(
    engine: Any,
    status: DeliveryStatus,
) -> None:
    started_at = _seed_sending(engine, DeliveryChannel.EMAIL)
    completed_at = started_at + timedelta(seconds=1)
    with engine.begin() as connection:
        first = _callback(
            connection,
            channel=DeliveryChannel.EMAIL,
            status=status,
            completed_at=completed_at,
            event_id="first-event",
        )
    with engine.begin() as connection:
        duplicate = _callback(
            connection,
            channel=DeliveryChannel.EMAIL,
            status=status,
            completed_at=completed_at + timedelta(seconds=20),
            event_id="ignored-duplicate-event",
        )

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.delivery == first.delivery
    assert duplicate.delivery.result == {
        "event_id": "first-event",
        "transport": "N8N_WEBHOOK",
    }
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 1
        )


def test_preconfirmed_c43_failed_row_converges_only_as_failed_duplicate(
    engine: Any,
) -> None:
    _seed_sending(engine, DeliveryChannel.EMAIL)
    with engine.begin() as connection:
        preconfirmed = repo.settle_email_delivery(
            connection,
            action_id=ACTION_ID,
            request_hash=HASH,
            failure_code="WEBHOOK_401",
            terminal_failure=True,
        )
    assert preconfirmed.status is DeliveryStatus.FAILED

    completed_at = preconfirmed.completed_at
    assert completed_at is not None
    with engine.begin() as connection:
        duplicate = _callback(
            connection,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.FAILED,
            completed_at=completed_at + timedelta(seconds=1),
        )
    assert duplicate.duplicate is True
    assert duplicate.delivery.last_error == "WEBHOOK_401"

    with pytest.raises(repo.RepositoryConflict) as caught:
        with engine.begin() as connection:
            _callback(
                connection,
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.SENT,
                completed_at=completed_at + timedelta(seconds=1),
            )
    assert caught.value.code == "DELIVERY_TERMINAL_STATUS_CHANGED"
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 1
        )


@pytest.mark.parametrize(
    "status",
    [
        DeliveryStatus.WAITING,
        DeliveryStatus.BLOCKED,
        DeliveryStatus.CANCELED,
    ],
)
def test_callback_cannot_skip_the_sending_state(
    engine: Any,
    status: DeliveryStatus,
) -> None:
    channel = (
        DeliveryChannel.EMAIL
        if status is DeliveryStatus.WAITING
        else DeliveryChannel.MES_MOCK
    )
    action_code = (
        ActionCode.WARNING if channel is DeliveryChannel.EMAIL else ActionCode.EQP_HOLD
    )
    initial_status = (
        DeliveryStatus.WAITING
        if channel is DeliveryChannel.EMAIL
        else DeliveryStatus.BLOCKED
    )
    with engine.begin() as connection:
        repo.insert_action_history(
            connection,
            action_id=ACTION_ID,
            lot_id="LOT-C44",
            chamber_id="EQP01-PM-C01",
            action_code=action_code,
            reason="callback contract",
            created_at=datetime.now(UTC),
        )
        repo.insert_action_delivery(
            connection,
            action_id=ACTION_ID,
            channel=channel,
            status=initial_status,
            request_hash=HASH,
        )
        if status is DeliveryStatus.CANCELED:
            connection.execute(
                text(
                    "UPDATE action_delivery SET status='CANCELED' "
                    "WHERE action_id=:id AND channel='MES_MOCK'"
                ),
                {"id": ACTION_ID},
            )

    with pytest.raises(repo.RepositoryConflict) as caught:
        with engine.begin() as connection:
            _callback(
                connection,
                channel=channel,
                status=DeliveryStatus.FAILED,
                completed_at=datetime.now(UTC) + timedelta(seconds=1),
            )
    assert caught.value.code == "DELIVERY_NOT_SENDING"


def test_audit_failure_rolls_back_terminal_delivery(
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = _seed_sending(engine, DeliveryChannel.EMAIL)

    def fail_audit(*_args: Any, **_kwargs: Any) -> None:
        raise AuditContractError("fixture failure")

    monkeypatch.setattr(repo, "append_audit_log", fail_audit)
    with pytest.raises(repo.RepositoryContractError) as caught:
        with engine.begin() as connection:
            _callback(
                connection,
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.SENT,
                completed_at=started_at + timedelta(seconds=1),
            )
    assert caught.value.code == "AUDIT_CONTRACT_VIOLATION"

    with engine.connect() as connection:
        row = repo.get_action_delivery(
            connection,
            action_id=ACTION_ID,
            channel=DeliveryChannel.EMAIL,
        )
        audit_count = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()
    assert row.status is DeliveryStatus.SENDING
    assert audit_count == 0


def test_concurrent_same_hash_callbacks_serialize_to_one_update_one_duplicate(
    engine: Any,
) -> None:
    started_at = _seed_sending(engine, DeliveryChannel.EMAIL)
    completed_at = started_at + timedelta(seconds=1)
    barrier = Barrier(2)

    def worker(index: int) -> bool:
        with engine.begin() as connection:
            barrier.wait(timeout=10)
            return _callback(
                connection,
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.SENT,
                completed_at=completed_at + timedelta(milliseconds=index),
                event_id=f"concurrent-{index}",
            ).duplicate

    with ThreadPoolExecutor(max_workers=2) as executor:
        duplicates = sorted(executor.map(worker, range(2)))

    assert duplicates == [False, True]
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 1
        )


def test_other_hash_and_other_terminal_are_conflicts_without_changes(
    engine: Any,
) -> None:
    started_at = _seed_sending(engine, DeliveryChannel.EMAIL)
    completed_at = started_at + timedelta(seconds=1)
    with engine.begin() as connection:
        first = _callback(
            connection,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.SENT,
            completed_at=completed_at,
        )

    with pytest.raises(repo.RepositoryConflict) as hash_conflict:
        with engine.begin() as connection:
            _callback(
                connection,
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.SENT,
                completed_at=completed_at,
                request_hash="e" * 64,
            )
    assert hash_conflict.value.code == "DELIVERY_REQUEST_HASH_MISMATCH"

    with pytest.raises(repo.RepositoryConflict) as terminal_conflict:
        with engine.begin() as connection:
            _callback(
                connection,
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.FAILED,
                completed_at=completed_at,
            )
    assert terminal_conflict.value.code == "DELIVERY_TERMINAL_STATUS_CHANGED"

    with engine.connect() as connection:
        stored = repo.get_action_delivery(
            connection,
            action_id=ACTION_ID,
            channel=DeliveryChannel.EMAIL,
        )
        audit_count = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()
    assert stored == first.delivery
    assert audit_count == 1
