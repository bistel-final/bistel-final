"""V5-C-4.5 MES claim/settle의 격리 PostgreSQL 회귀."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.agent import approval_store
from app.agent import repository as repo
from app.common.enums import Decision, DeliveryChannel, DeliveryStatus
from tests.unit.test_agent_approval_store_container import (
    _create_waiting_bundle,
)
from tests.unit.test_agent_approval_store_container import (
    engine as approval_engine,
)

engine = approval_engine
pytestmark = pytest.mark.container


@pytest.fixture(autouse=True)
def clean_mes_state(engine: Any) -> None:
    with engine.begin() as connection:
        for table in ("agent_run", "action_history", "audit_log", "lot_history"):
            connection.execute(text(f"TRUNCATE {table} CASCADE"))


def _equipment(
    engine: Any,
    *,
    lot_id: str = "LOT-HITL",
    chamber_id: str = "EQP01-PM1",
    equipment_ids: tuple[str | None, ...] = ("EQP01",),
) -> None:
    with engine.begin() as connection:
        for index, equipment_id in enumerate(equipment_ids, start=1):
            connection.execute(
                text(
                    "INSERT INTO lot_history "
                    "(lot_hist_id, lot_id, wafer_no, equipment_id, chamber_id) "
                    "VALUES (:history, :lot, :wafer, :equipment, :chamber)"
                ),
                {
                    "history": f"LH-MES-{index}",
                    "lot": lot_id,
                    "wafer": index,
                    "equipment": equipment_id,
                    "chamber": chamber_id,
                },
            )


def _approve(engine: Any, approval_id: str) -> None:
    approval_store.decision_port(engine.begin)(
        approval_id,
        Decision.APPROVE,
        "operator-c45",
        "MES hold approved",
    )


def _delivery(engine: Any, action_id: str) -> repo.ActionDeliveryRow:
    with engine.connect() as connection:
        return repo.get_action_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.MES_MOCK,
        )


def test_approved_hold_claims_once_with_unique_equipment(engine: Any) -> None:
    _equipment(engine)
    _run_id, action_id, approval_id = _create_waiting_bundle(engine)
    _approve(engine, approval_id)

    with engine.begin() as connection:
        claim = repo.begin_mes_delivery(connection, action_id=action_id)
    assert claim is not None
    assert claim.equipment_id == "EQP01"
    assert claim.delivery.status is DeliveryStatus.SENDING
    assert claim.delivery.attempt_count == 1
    assert claim.delivery.started_at is not None

    with engine.begin() as connection:
        assert repo.begin_mes_delivery(connection, action_id=action_id) is None
    assert _delivery(engine, action_id).attempt_count == 1


@pytest.mark.parametrize("decision", [None, Decision.REJECT])
def test_unapproved_or_rejected_hold_never_claims(
    engine: Any,
    decision: Decision | None,
) -> None:
    _equipment(engine)
    _run_id, action_id, approval_id = _create_waiting_bundle(engine)
    if decision is not None:
        approval_store.decision_port(engine.begin)(
            approval_id,
            decision,
            "operator-c45",
        )

    with engine.begin() as connection:
        assert repo.begin_mes_delivery(connection, action_id=action_id) is None
    assert _delivery(engine, action_id).attempt_count == 0


@pytest.mark.parametrize(
    "equipment_ids",
    [(), (None,), ("EQP01", "EQP02")],
)
def test_equipment_must_be_exactly_one_nonblank_value(
    engine: Any,
    equipment_ids: tuple[str | None, ...],
) -> None:
    _equipment(engine, equipment_ids=equipment_ids)
    _run_id, action_id, approval_id = _create_waiting_bundle(engine)
    _approve(engine, approval_id)

    with pytest.raises(repo.RepositoryContractError) as exc:
        with engine.begin() as connection:
            repo.begin_mes_delivery(connection, action_id=action_id)
    assert exc.value.code == "MES_EQUIPMENT_NOT_UNIQUE"
    assert _delivery(engine, action_id).status is DeliveryStatus.WAITING


@pytest.mark.parametrize(
    ("code", "terminal", "status", "audit_count"),
    [
        ("WEBHOOK_401", True, DeliveryStatus.FAILED, 1),
        ("WEBHOOK_502", False, DeliveryStatus.SENDING, 0),
    ],
)
def test_mes_settle_and_failure_audit_are_atomic(
    engine: Any,
    code: str,
    terminal: bool,
    status: DeliveryStatus,
    audit_count: int,
) -> None:
    _equipment(engine)
    _run_id, action_id, approval_id = _create_waiting_bundle(engine)
    _approve(engine, approval_id)
    with engine.begin() as connection:
        claim = repo.begin_mes_delivery(connection, action_id=action_id)
    assert claim is not None

    with engine.begin() as connection:
        settled = repo.settle_mes_webhook(
            connection,
            action_id=action_id,
            request_hash=claim.delivery.request_hash,
            failure_code=code,
            terminal_failure=terminal,
        )
    with engine.connect() as connection:
        audits = connection.execute(
            text(
                "SELECT after_json FROM audit_log "
                "WHERE entity_id = :action_id AND event_type = 'ACTION_SEND_FAILED'"
            ),
            {"action_id": action_id},
        ).all()

    assert settled.status is status
    assert settled.last_error == code
    assert len(audits) == audit_count
    if audits:
        assert audits[0].after_json == {
            "channel": "MES_MOCK",
            "reason_code": code,
            "transport": "KAFKA",
        }
