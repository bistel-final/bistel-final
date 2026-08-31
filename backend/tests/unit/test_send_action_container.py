"""`V5-C-4.6-1` send_action의 격리 PostgreSQL 조립 회귀."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.agent import action_store
from app.agent import repository as repo
from app.agent.approval_store import decision_port
from app.agent.send_action import build_send_action_tool
from app.common.enums import ActionCode, Decision, DeliveryChannel, DeliveryStatus
from app.common.ids import new_thread_id
from tests.unit.test_agent_approval_store_container import (
    ALARM,
    _hold,
    _seed,
)
from tests.unit.test_agent_approval_store_container import (
    engine as approval_engine,
)

engine = approval_engine

pytestmark = pytest.mark.container


@pytest.fixture(autouse=True)
def clean_send_action_state(engine: Any) -> None:
    with engine.begin() as connection:
        for table in ("agent_run", "action_history", "audit_log", "lot_history"):
            connection.execute(text(f"TRUNCATE {table} CASCADE"))


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        N8N_WEBHOOK_URL="http://localhost:5678/webhook/fdc-notify-email",
        N8N_WF3_URL="http://localhost:5678/webhook/fdc-mes-hold",
        N8N_WEBHOOK_TIMEOUT_SEC=30,
        N8N_WEBHOOK_SECRET="container-secret",
        AGENT_EMAIL_RECIPIENTS="operator@example.invalid",
    )


def _warning(engine: Any) -> str:
    action_id = "ACT-c461warning001"
    with engine.begin() as connection:
        repo.insert_action_history(
            connection,
            action_id=action_id,
            lot_id="LOT-C461-W",
            chamber_id="EQP01-PM1",
            action_code=ActionCode.WARNING,
            reason="trace oos",
            created_at=datetime.now(UTC),
        )
        repo.insert_action_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.WAITING,
            request_hash="a" * 64,
        )
    return action_id


def _hold_bundle(engine: Any) -> tuple[str, str, str]:
    with engine.begin() as connection:
        run = repo.create_agent_run(
            connection,
            repo.CreateAgentRunCommand(
                thread_id=new_thread_id(),
                lot_id="LOT-C461-H",
                chamber_id="EQP01-PM1",
                autonomy_level=2,
                requested_alarm=ALARM,
                representative_alarm=ALARM,
                member_alarms=(ALARM,),
                llm_model="test-model",
            ),
        )
    stored = action_store.production_port(engine.begin)(
        run.agent_run_id,
        _hold(),
        _seed("LOT-C461-H", "EQP01-PM1"),
    )
    assert stored.approval_id is not None
    return run.agent_run_id, stored.action_id, stored.approval_id


def test_warning_recall_keeps_downstream_effect_at_one(engine: Any) -> None:
    action_id = _warning(engine)
    effects = 0

    def post(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal effects
        effects += 1
        return SimpleNamespace(status_code=200)

    tool = build_send_action_tool(_settings(), engine.begin, http_post=post)

    first = tool({"action_id": action_id})
    second = tool({"action_id": action_id})

    assert first.ok is True and second.ok is True
    assert first.deliveries[0].status is DeliveryStatus.SENDING
    assert second.deliveries[0].status is DeliveryStatus.SENDING
    assert effects == 1


def test_warning_response_loss_recall_does_not_resend(engine: Any) -> None:
    action_id = _warning(engine)
    effects = 0

    def post(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal effects
        effects += 1
        raise httpx.ReadTimeout("response lost")

    tool = build_send_action_tool(_settings(), engine.begin, http_post=post)

    uncertain = tool({"action_id": action_id})
    recalled = tool({"action_id": action_id})

    assert uncertain.reason == "TIMEOUT: SEND_ACTION_WEBHOOK_TIMEOUT"
    assert uncertain.action_id is None and uncertain.deliveries == []
    assert recalled.ok is True
    assert recalled.deliveries[0].status is DeliveryStatus.SENDING
    assert recalled.deliveries[0].sent is False
    assert recalled.deliveries[0].duplicate is False
    assert effects == 1


def test_hold_executes_email_then_approved_mes_and_returns_full_plan(
    engine: Any,
) -> None:
    run_id, action_id, approval_id = _hold_bundle(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO lot_history "
                "(lot_hist_id, lot_id, wafer_no, equipment_id, chamber_id) "
                "VALUES ('LH-C461', 'LOT-C461-H', 1, 'EQP01', 'EQP01-PM1')"
            )
        )
    effects: list[str] = []

    def post(url: str, **_kwargs: Any) -> Any:
        if url.endswith("fdc-notify-email"):
            effects.append("EMAIL")
            return SimpleNamespace(status_code=200)
        effects.append("MES_MOCK")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "published": True},
        )

    tool = build_send_action_tool(_settings(), engine.begin, http_post=post)
    pending = tool({"action_id": action_id})
    assert [(item.channel, item.status) for item in pending.deliveries] == [
        (DeliveryChannel.EMAIL, DeliveryStatus.SENDING),
        (DeliveryChannel.MES_MOCK, DeliveryStatus.BLOCKED),
    ]

    with engine.begin() as connection:
        email = repo.get_action_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.EMAIL,
        )
        repo.settle_delivery_callback(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.SENT,
            provider_message_id="smtp-c461",
            request_hash=email.request_hash,
            completed_at=datetime.now(UTC),
            error_code=None,
            event_id="email-c461",
        )
    decision_port(engine.begin)(approval_id, Decision.APPROVE, "operator")

    approved = tool({"action_id": action_id})
    with engine.begin() as connection:
        mes = repo.get_action_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.MES_MOCK,
        )
        repo.settle_delivery_callback(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.MES_MOCK,
            status=DeliveryStatus.SENT,
            provider_message_id="kafka-c461",
            request_hash=mes.request_hash,
            completed_at=datetime.now(UTC),
            error_code=None,
            event_id="mes-c461",
        )
    duplicate = tool({"action_id": action_id})

    assert run_id
    assert [(item.channel, item.status) for item in approved.deliveries] == [
        (DeliveryChannel.EMAIL, DeliveryStatus.SENT),
        (DeliveryChannel.MES_MOCK, DeliveryStatus.SENDING),
    ]
    assert approved.deliveries[0].duplicate is True
    assert approved.deliveries[1].sent is False
    assert all(item.sent is False for item in duplicate.deliveries)
    assert all(item.duplicate is True for item in duplicate.deliveries)
    assert effects == ["EMAIL", "MES_MOCK"]


def test_rejected_hold_never_calls_mes_adapter(engine: Any) -> None:
    _run_id, action_id, approval_id = _hold_bundle(engine)
    mes_effects = 0

    def post(url: str, **_kwargs: Any) -> Any:
        nonlocal mes_effects
        if url.endswith("fdc-mes-hold"):
            mes_effects += 1
        return SimpleNamespace(status_code=200)

    with engine.begin() as connection:
        claimed = repo.begin_email_delivery(connection, action_id=action_id)
        assert claimed is not None
        repo.settle_delivery_callback(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.SENT,
            provider_message_id="smtp-c461-reject",
            request_hash=claimed.request_hash,
            completed_at=datetime.now(UTC),
            error_code=None,
            event_id="email-c461-reject",
        )
    decision_port(engine.begin)(approval_id, Decision.REJECT, "operator")

    result = build_send_action_tool(
        _settings(),
        engine.begin,
        http_post=post,
    )({"action_id": action_id})

    assert result.ok is True
    assert result.deliveries[1].status is DeliveryStatus.CANCELED
    assert mes_effects == 0
