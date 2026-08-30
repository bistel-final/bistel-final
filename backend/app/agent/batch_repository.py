"""incident 일회성 batch의 읽기 전용 PostgreSQL 경계 (`V5-C-5.3`).

수동 실행과 자동 batch는 같은 ``RESOLVED_ALARM_SELECT_SQL``을 사용한다. 이 파일은
run을 만들거나 상태를 바꾸지 않으며, pending selection과 batch-scoped 관측 snapshot만
제공한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, Row

from app.agent.incident_repository import RESOLVED_ALARM_SELECT_SQL
from app.agent.repository import RepositoryContractError, execute_read_all
from app.common.enums import AlarmSource
from app.common.schemas import AlarmRef

__all__ = [
    "BatchObservation",
    "PendingIncidentMemberRow",
    "fetch_incident_observation",
    "fetch_pending_incident_rows",
]


@dataclass(frozen=True, slots=True)
class PendingIncidentMemberRow:
    alarm: AlarmRef
    occurred_at: datetime | None
    lot_hist_id: str | None
    raw_lot_id: str | None
    raw_chamber_id: str | None
    canonical_lot_id: str | None
    canonical_chamber_id: str | None
    is_pending: bool
    has_incomplete_run: bool


@dataclass(frozen=True, slots=True)
class BatchObservation:
    """한 incident에서 관측한 batch 집계 identity 집합."""

    run_ids: frozenset[str]
    created_action_ids: frozenset[str]
    delivery_keys: frozenset[tuple[str, str]]


#: 정상 owner row는 run history가 전혀 없을 때만 pending이다. statement는 non-pending
#: row도 함께 반환한다. 같은 AlarmRef의 cross-incident fan-out과 owner 결손·drift를
#: pending group 관점에서 놓치지 않기 위해서다. SQL은 대표 선정이나 incident grouping을
#: 하지 않는다 — 그 규칙은 ``incident_sort_key`` 한 곳만 사용한다.
_PENDING_MEMBERS: Final = text(
    f"""
    WITH resolved AS (
        {RESOLVED_ALARM_SELECT_SQL}
    ),
    annotated AS (
        SELECT
            r.*,
            CASE
                WHEN r.canonical_lot_id IS NULL
                  OR r.canonical_chamber_id IS NULL
                THEN false
                ELSE NOT EXISTS (
                    SELECT 1
                    FROM agent_run AS existing
                    WHERE existing.lot_id = r.canonical_lot_id
                      AND existing.chamber_id = r.canonical_chamber_id
                )
            END AS is_pending,
            CASE
                WHEN r.canonical_lot_id IS NULL
                  OR r.canonical_chamber_id IS NULL
                THEN false
                ELSE EXISTS (
                    SELECT 1
                    FROM agent_run AS active
                    WHERE active.lot_id = r.canonical_lot_id
                      AND active.chamber_id = r.canonical_chamber_id
                      AND active.status = 'RUNNING'
                )
            END AS has_incomplete_run
        FROM resolved AS r
    )
    SELECT
        source,
        alarm_id,
        occurred_at,
        lot_hist_id,
        raw_lot_id,
        raw_chamber_id,
        canonical_lot_id,
        canonical_chamber_id,
        is_pending,
        has_incomplete_run
    FROM annotated
    ORDER BY source, alarm_id, lot_hist_id NULLS FIRST
    """
)


#: action·delivery는 이 incident의 **CREATED link**에 결속된 것만 본다. REUSED action은
#: 이번 run이 새로 만든 객체가 아니므로 batch 생성량에 포함하지 않는다.
_OBSERVATION: Final = text(
    """
    SELECT
        run.agent_run_id AS agent_run_id,
        linked.action_id AS created_action_id,
        delivery.channel AS delivery_channel
    FROM agent_run AS run
    LEFT JOIN agent_run_action AS linked
      ON linked.agent_run_id = run.agent_run_id
     AND linked.link_role = 'CREATED'
    LEFT JOIN action_delivery AS delivery
      ON delivery.action_id = linked.action_id
    WHERE run.lot_id = :lot_id AND run.chamber_id = :chamber_id
    ORDER BY run.agent_run_id, linked.action_id, delivery.channel
    """
)


def fetch_pending_incident_rows(
    connection: Connection,
) -> tuple[PendingIncidentMemberRow, ...]:
    rows = execute_read_all(connection, _PENDING_MEMBERS, {})
    return tuple(_pending_row(row) for row in rows)


def fetch_incident_observation(
    connection: Connection,
    *,
    lot_id: str,
    chamber_id: str,
) -> BatchObservation:
    rows = execute_read_all(
        connection,
        _OBSERVATION,
        {"lot_id": lot_id, "chamber_id": chamber_id},
    )
    return BatchObservation(
        run_ids=frozenset(str(row.agent_run_id) for row in rows),
        created_action_ids=frozenset(
            str(row.created_action_id)
            for row in rows
            if row.created_action_id is not None
        ),
        delivery_keys=frozenset(
            (str(row.created_action_id), str(row.delivery_channel))
            for row in rows
            if row.created_action_id is not None and row.delivery_channel is not None
        ),
    )


def _pending_row(row: Row[Any]) -> PendingIncidentMemberRow:
    if not isinstance(row.alarm_id, str) or not row.alarm_id.strip():
        raise RepositoryContractError("BATCH_ALARM_IDENTITY_INVALID")
    try:
        alarm = AlarmRef(
            source=AlarmSource(str(row.source)),
            alarm_id=row.alarm_id,
        )
    except (TypeError, ValueError) as exc:
        raise RepositoryContractError("BATCH_ALARM_IDENTITY_INVALID") from exc
    return PendingIncidentMemberRow(
        alarm=alarm,
        occurred_at=row.occurred_at,
        lot_hist_id=None if row.lot_hist_id is None else str(row.lot_hist_id),
        raw_lot_id=None if row.raw_lot_id is None else str(row.raw_lot_id),
        raw_chamber_id=(
            None if row.raw_chamber_id is None else str(row.raw_chamber_id)
        ),
        canonical_lot_id=(
            None if row.canonical_lot_id is None else str(row.canonical_lot_id)
        ),
        canonical_chamber_id=(
            None if row.canonical_chamber_id is None else str(row.canonical_chamber_id)
        ),
        is_pending=row.is_pending is True,
        has_incomplete_run=row.has_incomplete_run is True,
    )
