"""Runtime 이력이 없는 incident의 결정론적 batch plan (`V5-C-5.3`)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Final

from sqlalchemy.engine import Connection

from app.agent.batch_repository import (
    PendingIncidentMemberRow,
    fetch_pending_incident_rows,
)
from app.agent.incident import incident_sort_key
from app.agent.incident_repository import IncidentAlarmEvent
from app.common.schemas import AlarmRef

__all__ = [
    "PendingBatchPlan",
    "PendingIncident",
    "RejectedIncident",
    "build_pending_batch_plan",
]

ALARM_OCCURRED_AT_MISSING: Final = "ALARM_OCCURRED_AT_MISSING"
RESOLVER_REJECTED: Final = "RESOLVER_REJECTED"

IncidentKey = tuple[str, str]
AlarmIdentity = tuple[str, str]


@dataclass(frozen=True, slots=True)
class PendingIncident:
    lot_id: str
    chamber_id: str
    member_count: int
    representative: AlarmRef


@dataclass(frozen=True, slots=True)
class RejectedIncident:
    lot_id: str
    chamber_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class PendingBatchPlan:
    selected: tuple[PendingIncident, ...]
    rejected: tuple[RejectedIncident, ...]
    canonical_null_rows: int


def build_pending_batch_plan(connection: Connection) -> PendingBatchPlan:
    """DB row grouping과 대표 선정을 Python 한 곳에서 수행한다."""

    rows = fetch_pending_incident_rows(connection)
    groups: dict[IncidentKey, list[PendingIncidentMemberRow]] = defaultdict(list)
    invalid_keys: set[IncidentKey] = set()
    identity_keys: dict[AlarmIdentity, set[IncidentKey]] = defaultdict(set)
    canonical_null_rows = 0

    for row in rows:
        raw_key = _key(row.raw_lot_id, row.raw_chamber_id)
        canonical_key = _key(row.canonical_lot_id, row.canonical_chamber_id)
        if canonical_key is None:
            canonical_null_rows += 1
            if raw_key is not None:
                invalid_keys.add(raw_key)
            continue

        if raw_key != canonical_key:
            invalid_keys.add(canonical_key)
            if raw_key is not None:
                invalid_keys.add(raw_key)

        identity_keys[_alarm_identity(row.alarm)].add(canonical_key)
        if not row.is_pending:
            continue
        groups[canonical_key].append(row)

    # 같은 AlarmRef가 여러 canonical incident로 fan-out되면 어느 쪽도
    # 임의 선택하지 않는다.
    for keys in identity_keys.values():
        if len(keys) > 1:
            invalid_keys.update(keys)

    selected: list[tuple[object, PendingIncident]] = []
    rejected: list[RejectedIncident] = []
    for (lot_id, chamber_id), members in groups.items():
        identities = Counter(_alarm_identity(member.alarm) for member in members)
        if (lot_id, chamber_id) in invalid_keys or any(
            count > 1 for count in identities.values()
        ):
            rejected.append(RejectedIncident(lot_id, chamber_id, RESOLVER_REJECTED))
            continue
        if any(member.occurred_at is None for member in members):
            rejected.append(
                RejectedIncident(lot_id, chamber_id, ALARM_OCCURRED_AT_MISSING)
            )
            continue

        events = [
            IncidentAlarmEvent(
                alarm=member.alarm,
                occurred_at=member.occurred_at,
                lot_hist_id=member.lot_hist_id or "",
            )
            for member in members
        ]
        ordered = sorted(events, key=incident_sort_key)
        candidate = PendingIncident(
            lot_id=lot_id,
            chamber_id=chamber_id,
            member_count=len(ordered),
            representative=ordered[0].alarm,
        )
        selected.append((ordered[0].occurred_at, candidate))

    selected.sort(key=lambda item: (item[0], item[1].lot_id, item[1].chamber_id))
    rejected.sort(key=lambda item: (item.lot_id, item.chamber_id, item.reason))
    return PendingBatchPlan(
        selected=tuple(item[1] for item in selected),
        rejected=tuple(rejected),
        canonical_null_rows=canonical_null_rows,
    )


def _key(lot_id: str | None, chamber_id: str | None) -> IncidentKey | None:
    if lot_id is None or chamber_id is None:
        return None
    return lot_id, chamber_id


def _alarm_identity(alarm: AlarmRef) -> AlarmIdentity:
    return alarm.source.value, alarm.alarm_id
