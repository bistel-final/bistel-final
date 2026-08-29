"""V5-C-4.6 channel idempotency·UNKNOWN/retry PostgreSQL matrix."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from app.agent import approval_store
from app.agent import email_delivery as email
from app.agent import mes_delivery as mes
from app.agent import repository as repo
from app.common.enums import ActionCode, Decision, DeliveryChannel, DeliveryStatus
from scripts import manage_delivery_recovery as recovery
from tests.unit.test_agent_approval_store_container import (
    _create_waiting_bundle,
)
from tests.unit.test_agent_approval_store_container import (
    engine as approval_engine,
)

engine = approval_engine
pytestmark = pytest.mark.container

EMAIL_ACTION_ID = "ACT-c46000000000001"
HASH = "6" * 64


@pytest.fixture(autouse=True)
def clean_state(engine: Any) -> None:
    with engine.begin() as connection:
        for table in ("agent_run", "action_history", "audit_log", "lot_history"):
            connection.execute(text(f"TRUNCATE {table} CASCADE"))


def _seed_email(engine: Any) -> None:
    with engine.begin() as connection:
        repo.insert_action_history(
            connection,
            action_id=EMAIL_ACTION_ID,
            lot_id="LOT-C46",
            chamber_id="EQP01-PM1",
            action_code=ActionCode.WARNING,
            reason="idempotency matrix",
            created_at=datetime.now(UTC),
        )
        repo.insert_action_delivery(
            connection,
            action_id=EMAIL_ACTION_ID,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.WAITING,
            request_hash=HASH,
        )


def _seed_delivery(engine: Any, channel: DeliveryChannel) -> None:
    _seed_email(engine)
    if channel is DeliveryChannel.MES_MOCK:
        with engine.begin() as connection:
            repo.insert_action_delivery(
                connection,
                action_id=EMAIL_ACTION_ID,
                channel=channel,
                status=DeliveryStatus.BLOCKED,
                request_hash=HASH,
            )


def _force_sending(
    engine: Any,
    *,
    action_id: str,
    channel: DeliveryChannel,
    age_seconds: int,
) -> datetime:
    with engine.begin() as connection:
        started_at = connection.execute(
            text(
                "UPDATE action_delivery SET status='SENDING', attempt_count=1, "
                "started_at=clock_timestamp() - make_interval(secs => :age), "
                "completed_at=NULL, last_error='WEBHOOK_TIMEOUT' "
                "WHERE action_id=:action_id AND channel=:channel "
                "RETURNING started_at"
            ),
            {
                "action_id": action_id,
                "channel": channel.value,
                "age": float(age_seconds),
            },
        ).scalar_one()
    return started_at


@pytest.mark.parametrize("channel", list(DeliveryChannel))
def test_stale_cutoff_and_unknown_shape_are_exact(
    engine: Any,
    channel: DeliveryChannel,
) -> None:
    """WBS C-4.6: 두 채널의 stale SENDING만 UNKNOWN으로 확정한다."""

    _seed_delivery(engine, channel)
    _force_sending(
        engine,
        action_id=EMAIL_ACTION_ID,
        channel=channel,
        age_seconds=601,
    )

    with engine.begin() as connection:
        result = repo.mark_delivery_unknown(
            connection,
            action_id=EMAIL_ACTION_ID,
            channel=channel,
            stale_after_seconds=600,
        )

    assert result.reason is repo.DeliveryRecoveryReason.APPLIED
    assert result.previous_status is DeliveryStatus.SENDING
    assert result.delivery is not None
    assert result.delivery.status is DeliveryStatus.UNKNOWN
    assert result.delivery.attempt_count == 1
    assert result.delivery.request_hash == HASH
    assert result.delivery.last_error == "DELIVERY_RESULT_UNKNOWN"
    assert result.delivery.result == {
        "operator_decision": "UNKNOWN_CONFIRMED",
        "transport": "N8N_WEBHOOK" if channel is DeliveryChannel.EMAIL else "KAFKA",
    }
    assert result.delivery.completed_at is not None


@pytest.mark.parametrize("channel", list(DeliveryChannel))
def test_fresh_sending_is_not_listed_or_changed(
    engine: Any,
    channel: DeliveryChannel,
) -> None:
    """WBS C-4.6: 두 채널의 cutoff 안쪽 delivery는 조회·변경 0이다."""

    _seed_delivery(engine, channel)
    _force_sending(
        engine,
        action_id=EMAIL_ACTION_ID,
        channel=channel,
        age_seconds=599,
    )

    with engine.connect() as connection:
        assert repo.list_stale_deliveries(connection, stale_after_seconds=600) == []
    with engine.begin() as connection:
        result = repo.mark_delivery_unknown(
            connection,
            action_id=EMAIL_ACTION_ID,
            channel=channel,
            stale_after_seconds=600,
        )
    assert result.reason is repo.DeliveryRecoveryReason.STILL_FRESH
    assert result.delivery is not None
    assert result.delivery.status is DeliveryStatus.SENDING


def test_confirm_unknown_cli_mutates_the_verified_e2e_database(
    engine: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """WBS C-4.6: CLI는 current_database 대조 뒤에만 UNKNOWN을 적용한다."""

    _seed_email(engine)
    _force_sending(
        engine,
        action_id=EMAIL_ACTION_ID,
        channel=DeliveryChannel.EMAIL,
        age_seconds=601,
    )
    confirmation = f"confirm-unknown kosa_agent_e2e {EMAIL_ACTION_ID} EMAIL"

    exit_code = recovery.main(
        [
            "--target",
            "kosa_agent_e2e",
            "confirm-unknown",
            "--action-id",
            EMAIL_ACTION_ID,
            "--channel",
            "EMAIL",
            "--provider-checked",
            "--confirm",
            confirmation,
        ],
        engine_factory=lambda: engine,
    )

    assert exit_code == recovery.EXIT_OK
    assert json.loads(capsys.readouterr().out)["delivery_status"] == "UNKNOWN"
    with engine.connect() as connection:
        delivery = repo.get_action_delivery(
            connection,
            action_id=EMAIL_ACTION_ID,
            channel=DeliveryChannel.EMAIL,
        )
    assert delivery.status is DeliveryStatus.UNKNOWN


@pytest.mark.parametrize("channel", list(DeliveryChannel))
def test_unknown_winner_rejects_late_callback_without_audit(
    engine: Any,
    channel: DeliveryChannel,
) -> None:
    """WBS C-4.6: 두 채널 모두 UNKNOWN 뒤 callback은 409·감사 0이다."""

    _seed_delivery(engine, channel)
    started_at = _force_sending(
        engine,
        action_id=EMAIL_ACTION_ID,
        channel=channel,
        age_seconds=601,
    )
    with engine.begin() as connection:
        result = repo.mark_delivery_unknown(
            connection,
            action_id=EMAIL_ACTION_ID,
            channel=channel,
            stale_after_seconds=600,
        )
    assert result.reason is repo.DeliveryRecoveryReason.APPLIED

    with pytest.raises(repo.RepositoryConflict) as caught:
        with engine.begin() as connection:
            repo.settle_delivery_callback(
                connection,
                action_id=EMAIL_ACTION_ID,
                channel=channel,
                status=DeliveryStatus.SENT,
                provider_message_id="provider-c46",
                request_hash=HASH,
                completed_at=started_at + timedelta(seconds=1),
                error_code=None,
                event_id="WF2:c46",
            )
    assert caught.value.code == "DELIVERY_NOT_SENDING"
    with engine.connect() as connection:
        audit_count = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()
    assert audit_count == 0


@pytest.mark.parametrize("channel", list(DeliveryChannel))
def test_callback_and_unknown_race_has_exactly_one_winner(
    engine: Any,
    channel: DeliveryChannel,
) -> None:
    """WBS C-4.6: 두 채널 callback↔UNKNOWN 경합은 한쪽만 확정한다."""

    _seed_delivery(engine, channel)
    started_at = _force_sending(
        engine,
        action_id=EMAIL_ACTION_ID,
        channel=channel,
        age_seconds=601,
    )
    barrier = Barrier(2)

    def confirm_unknown() -> str:
        barrier.wait()
        with engine.begin() as connection:
            result = repo.mark_delivery_unknown(
                connection,
                action_id=EMAIL_ACTION_ID,
                channel=channel,
                stale_after_seconds=600,
            )
        return result.reason.value

    def callback() -> str:
        barrier.wait()
        try:
            with engine.begin() as connection:
                repo.settle_delivery_callback(
                    connection,
                    action_id=EMAIL_ACTION_ID,
                    channel=channel,
                    status=DeliveryStatus.SENT,
                    provider_message_id="smtp-race",
                    request_hash=HASH,
                    completed_at=started_at + timedelta(seconds=1),
                    error_code=None,
                    event_id="WF2:race",
                )
        except repo.RepositoryConflict:
            return "CALLBACK_REJECTED"
        return "CALLBACK_APPLIED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = {executor.submit(confirm_unknown), executor.submit(callback)}
        observed = {future.result() for future in outcomes}

    assert observed in (
        {"APPLIED", "CALLBACK_REJECTED"},
        {"CALLBACK_WON", "CALLBACK_APPLIED"},
    )
    with engine.connect() as connection:
        row = repo.get_action_delivery(
            connection,
            action_id=EMAIL_ACTION_ID,
            channel=channel,
        )
        audits = connection.execute(text("SELECT count(*) FROM audit_log")).scalar_one()
    assert row.status in {DeliveryStatus.UNKNOWN, DeliveryStatus.SENT}
    assert audits == (1 if row.status is DeliveryStatus.SENT else 0)


@pytest.mark.parametrize(
    "status",
    [
        DeliveryStatus.WAITING,
        DeliveryStatus.BLOCKED,
        DeliveryStatus.SENDING,
        DeliveryStatus.SENT,
        DeliveryStatus.UNKNOWN,
        DeliveryStatus.CANCELED,
    ],
)
def test_retry_rejects_every_nonfailed_state(
    engine: Any,
    status: DeliveryStatus,
) -> None:
    """WBS C-4.6: 명시 retry도 FAILED 이외 6상태는 바꾸지 않는다."""

    _seed_email(engine)
    started_at = (
        datetime.now(UTC)
        if status
        in {DeliveryStatus.SENDING, DeliveryStatus.SENT, DeliveryStatus.UNKNOWN}
        else None
    )
    completed_at = (
        started_at if status in {DeliveryStatus.SENT, DeliveryStatus.UNKNOWN} else None
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE action_delivery SET status=:status, "
                "started_at=:started_at, completed_at=:completed_at "
                "WHERE action_id=:action_id AND channel='EMAIL'"
            ),
            {
                "action_id": EMAIL_ACTION_ID,
                "status": status.value,
                "started_at": started_at,
                "completed_at": completed_at,
            },
        )

    with pytest.raises(repo.RepositoryConflict) as caught:
        with engine.begin() as connection:
            repo.retry_failed_delivery(
                connection,
                action_id=EMAIL_ACTION_ID,
                channel=DeliveryChannel.EMAIL,
            )
    assert caught.value.code == "DELIVERY_RETRY_STATE_NOT_ALLOWED"
    with engine.connect() as connection:
        unchanged = repo.get_action_delivery(
            connection,
            action_id=EMAIL_ACTION_ID,
            channel=DeliveryChannel.EMAIL,
        )
    assert unchanged.status is status


def test_failed_retry_preserves_identity_and_next_claim_owns_attempt(
    engine: Any,
) -> None:
    """WBS C-4.6: FAILED retry는 stable hash·attempt·오류를 보존한다."""

    _seed_email(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE action_delivery SET status='FAILED', attempt_count=2, "
                "started_at=clock_timestamp() - interval '2 minutes', "
                "completed_at=clock_timestamp(), provider_message_id='smtp-old', "
                "last_error='WEBHOOK_401', result='{\"transport\":\"N8N_WEBHOOK\"}' "
                "WHERE action_id=:action_id AND channel='EMAIL'"
            ),
            {"action_id": EMAIL_ACTION_ID},
        )
        retried = repo.retry_failed_delivery(
            connection,
            action_id=EMAIL_ACTION_ID,
            channel=DeliveryChannel.EMAIL,
        )

    assert retried.status is DeliveryStatus.WAITING
    assert retried.request_hash == HASH
    assert retried.attempt_count == 2
    assert retried.last_error == "WEBHOOK_401"
    assert retried.provider_message_id is None
    assert retried.completed_at is None
    assert retried.result is None

    with engine.begin() as connection:
        claimed = repo.begin_email_delivery(connection, action_id=EMAIL_ACTION_ID)
    assert claimed is not None
    assert claimed.attempt_count == 3
    assert claimed.last_error is None


def test_mes_retry_cannot_bypass_rechecked_approval(engine: Any) -> None:
    """WBS C-4.6: MES retry 뒤 claim은 승인 결속을 다시 검증한다."""

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO lot_history "
                "(lot_hist_id, lot_id, wafer_no, equipment_id, chamber_id) "
                "VALUES ('LH-C46', 'LOT-HITL', 1, 'EQP01', 'EQP01-PM1')"
            )
        )
    _run_id, action_id, approval_id = _create_waiting_bundle(engine)
    approval_store.decision_port(engine.begin)(
        approval_id,
        Decision.APPROVE,
        "operator-c46",
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE action_delivery SET status='FAILED', attempt_count=1, "
                "started_at=clock_timestamp() - interval '1 minute', "
                "completed_at=clock_timestamp(), last_error='WEBHOOK_401' "
                "WHERE action_id=:action_id AND channel='MES_MOCK'"
            ),
            {"action_id": action_id},
        )
        retried = repo.retry_failed_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.MES_MOCK,
        )
    assert retried.status is DeliveryStatus.WAITING

    # 운영 중 승인 projection이 더 이상 APPROVED가 아닌 상황을 재현한다.
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE approval_request SET status='REJECTED' "
                "WHERE approval_id=:approval_id"
            ),
            {"approval_id": approval_id},
        )
    with pytest.raises(repo.RepositoryConflict) as caught:
        with engine.begin() as connection:
            repo.begin_mes_delivery(connection, action_id=action_id)
    assert caught.value.code == "MES_APPROVAL_IDENTITY_MISMATCH"
    with engine.connect() as connection:
        delivery = repo.get_action_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.MES_MOCK,
        )
    assert delivery.status is DeliveryStatus.WAITING


def _email_settings() -> SimpleNamespace:
    return SimpleNamespace(
        N8N_WEBHOOK_URL="http://localhost:5678/webhook/fdc-notify-email",
        N8N_WEBHOOK_TIMEOUT_SEC=30,
        N8N_WEBHOOK_SECRET="matrix-secret",
        AGENT_EMAIL_RECIPIENTS="operator@example.invalid",
    )


def _mes_settings() -> SimpleNamespace:
    return SimpleNamespace(
        N8N_WF3_URL="http://localhost:5678/webhook/fdc-mes-hold",
        N8N_WEBHOOK_TIMEOUT_SEC=30,
        N8N_WEBHOOK_SECRET="matrix-secret",
    )


def _seed_recall_candidate(engine: Any, channel: DeliveryChannel) -> str:
    if channel is DeliveryChannel.EMAIL:
        _seed_email(engine)
        return EMAIL_ACTION_ID

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO lot_history "
                "(lot_hist_id, lot_id, wafer_no, equipment_id, chamber_id) "
                "VALUES ('LH-C46', 'LOT-HITL', 1, 'EQP01', 'EQP01-PM1')"
            )
        )
    _run_id, action_id, approval_id = _create_waiting_bundle(engine)
    approval_store.decision_port(engine.begin)(
        approval_id,
        Decision.APPROVE,
        "operator-c46",
    )
    return action_id


@pytest.mark.parametrize("channel", list(DeliveryChannel))
@pytest.mark.parametrize(
    "status",
    [DeliveryStatus.UNKNOWN, DeliveryStatus.FAILED],
)
def test_unknown_and_failed_are_never_reclaimed_by_adapters(
    engine: Any,
    channel: DeliveryChannel,
    status: DeliveryStatus,
) -> None:
    """C-4.6: terminal·미확정 row의 어댑터 재호출은 claim·HTTP 모두 0이다."""

    action_id = _seed_recall_candidate(engine, channel)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE action_delivery SET status=:status, attempt_count=1, "
                "started_at=clock_timestamp() - interval '1 minute', "
                "completed_at=clock_timestamp(), last_error=:last_error, "
                "result=CAST(:result AS jsonb) "
                "WHERE action_id=:action_id AND channel=:channel"
            ),
            {
                "action_id": action_id,
                "channel": channel.value,
                "status": status.value,
                "last_error": (
                    "DELIVERY_RESULT_UNKNOWN"
                    if status is DeliveryStatus.UNKNOWN
                    else "WEBHOOK_401"
                ),
                "result": json.dumps(
                    {
                        "operator_decision": (
                            "UNKNOWN_CONFIRMED"
                            if status is DeliveryStatus.UNKNOWN
                            else "FAILED_CONFIRMED"
                        ),
                        "transport": (
                            "N8N_WEBHOOK"
                            if channel is DeliveryChannel.EMAIL
                            else "KAFKA"
                        ),
                    }
                ),
            },
        )

    with engine.begin() as connection:
        claimed = (
            repo.begin_email_delivery(connection, action_id=action_id)
            if channel is DeliveryChannel.EMAIL
            else repo.begin_mes_delivery(connection, action_id=action_id)
        )
    assert claimed is None

    http_calls = 0

    def post(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal http_calls
        http_calls += 1
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "published": True},
        )

    outcome = (
        email.production_ports(
            _email_settings(), engine.begin, http_post=post
        ).service.send_warning(action_id)
        if channel is DeliveryChannel.EMAIL
        else mes.production_ports(
            _mes_settings(), engine.begin, http_post=post
        ).service.publish(action_id)
    ).outcome

    assert outcome.value == "NOOP"
    assert http_calls == 0
    with engine.connect() as connection:
        unchanged = repo.get_action_delivery(
            connection,
            action_id=action_id,
            channel=channel,
        )
    assert unchanged.status is status
    assert unchanged.attempt_count == 1


def test_email_concurrent_send_and_recall_have_one_http_effect(engine: Any) -> None:
    """WBS C-4.6 EMAIL: 동시 2회와 재호출에도 HTTP effect는 최대 1이다."""

    _seed_email(engine)
    barrier = Barrier(2)
    lock = Lock()
    http_calls = 0

    def post(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal http_calls
        with lock:
            http_calls += 1
        return SimpleNamespace(status_code=200)

    service = email.production_ports(
        _email_settings(), engine.begin, http_post=post
    ).service

    def send() -> email.EmailDeliveryOutcome:
        barrier.wait()
        return service.send_warning(EMAIL_ACTION_ID).outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(
            result.value for result in executor.map(lambda _: send(), range(2))
        )
    assert outcomes == ["ACCEPTED", "NOOP"]
    assert (
        service.send_warning(EMAIL_ACTION_ID).outcome is email.EmailDeliveryOutcome.NOOP
    )
    assert http_calls == 1


def test_mes_approval_gate_and_concurrent_publish_have_one_http_effect(
    engine: Any,
) -> None:
    """WBS C-4.6 MES: 승인 전 0, 승인 후 동시 2회에도 WF3 effect는 최대 1이다."""

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO lot_history "
                "(lot_hist_id, lot_id, wafer_no, equipment_id, chamber_id) "
                "VALUES ('LH-C46', 'LOT-HITL', 1, 'EQP01', 'EQP01-PM1')"
            )
        )
    _run_id, action_id, approval_id = _create_waiting_bundle(engine)
    http_calls = 0
    lock = Lock()

    def post(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal http_calls
        with lock:
            http_calls += 1
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "published": True},
        )

    service = mes.production_ports(
        _mes_settings(), engine.begin, http_post=post
    ).service
    assert service.publish(action_id).outcome is mes.MesDeliveryOutcome.NOOP
    assert http_calls == 0

    approval_store.decision_port(engine.begin)(
        approval_id,
        Decision.APPROVE,
        "operator-c46",
    )
    barrier = Barrier(2)

    def publish() -> mes.MesDeliveryOutcome:
        barrier.wait()
        return service.publish(action_id).outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(
            result.value for result in executor.map(lambda _: publish(), range(2))
        )
    assert outcomes == ["ACCEPTED", "NOOP"]
    assert service.publish(action_id).outcome is mes.MesDeliveryOutcome.NOOP
    assert http_calls == 1
