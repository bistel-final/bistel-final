"""source-aware incident 해석 (`V5-C-1.1`).

요청 `AlarmRef` 하나를 실행 문맥으로 넓힌다. 결과는 C-0.1 `CreateAgentRunCommand`가
요구하는 다섯 값이며, run을 만들지는 않는다.

## 대표 선정이 이 모듈의 핵심이다

설계서 §6.1은 순서를 정확히 정한다 — `occurred_at ASC`, source priority, `alarm_id ASC`.
그리고 **source priority는 "대표 선택 안정성만 위한 값"**이다(설계서 775행). 시간보다
먼저 적용하면 더 최신 R03가 더 오래된 SUMMARY를 앞서고, 그것은 대표 선정이 아니라
조치 우선순위를 미리 계산하는 것이 된다.

## 이 모듈이 하지 않는 것

- run 생성·감사 append·중복 실행 판정 (`V5-C-1.3`)
- WAFER routing·Neo4j Process Step 결합 (`V5-C-1.2`)
- public request mapping (`V5-C-5.1`)
- anomaly score·Fault label·metrology·LLM·RAG 사용
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from sqlalchemy.engine import Connection

from app.agent.incident_repository import (
    IncidentAlarmEvent,
    fetch_incident_snapshot,
)
from app.agent.repository import RepositoryContractError
from app.common.enums import AlarmSource
from app.common.schemas import AlarmRef

__all__ = [
    "SOURCE_PRIORITY",
    "ResolvedIncident",
    "incident_sort_key",
    "resolve_incident",
]

#: 동시각 tie-breaker. **명시 상수다.**
#:
#: Enum 선언 순서·Enum 문자열·SQL 알파벳 순서에 기대지 않는다. 알파벳이면
#: `R03 < SUMMARY < TRACE`가 되어 설계서와 다른 순서가 조용히 자리 잡는다.
SOURCE_PRIORITY: Final[Mapping[AlarmSource, int]] = {
    AlarmSource.R03: 0,
    AlarmSource.TRACE: 1,
    AlarmSource.SUMMARY: 2,
}

_OCCURRED_AT_MISSING: Final = "ALARM_OCCURRED_AT_MISSING"
_DUPLICATE_ALARM_REF: Final = "DUPLICATE_ALARM_REF"


@dataclass(frozen=True, slots=True)
class ResolvedIncident:
    """C-0.1 command 조립용 domain value. public response가 아니다."""

    lot_id: str
    chamber_id: str
    requested_alarm: AlarmRef
    representative_alarm: AlarmRef
    member_alarms: tuple[AlarmRef, ...]


def incident_sort_key(event: IncidentAlarmEvent) -> tuple[object, int, str]:
    """`occurred_at ASC → source priority → alarm_id ASC`.

    `occurred_at`이 첫 항목이라 **서로 다른 시각에서는 source priority가 결과를
    뒤집지 못한다.** NULL은 여기 오지 않는다 — `resolve_incident()`가 먼저 거른다.
    """

    return (
        event.occurred_at,
        SOURCE_PRIORITY[AlarmSource(event.alarm.source)],
        event.alarm.alarm_id,
    )


def resolve_incident(
    connection: Connection, requested_alarm: AlarmRef
) -> ResolvedIncident:
    """요청 AlarmRef가 속한 incident를 결정론적으로 해석한다.

    같은 DB snapshot에서 반복하면 member 순서까지 같은 결과가 나온다. 대표와 member가
    **같은 sort key**를 쓰므로 두 정렬 규칙이 갈라질 자리가 없다.

    요청 알람이 대표가 아니어도 `requested_alarm`은 입력 그대로 보존된다.
    """

    snapshot = fetch_incident_snapshot(connection, requested_alarm)

    missing_time = [event for event in snapshot.members if event.occurred_at is None]
    if missing_time:
        # NULL을 최솟값·최댓값으로 임의 배치하면 대표가 데이터가 아니라 구현 선택으로
        # 정해진다. 보정하지 않고 실패한다.
        raise RepositoryContractError(_OCCURRED_AT_MISSING)

    ordered = sorted(snapshot.members, key=incident_sort_key)
    members = tuple(event.alarm for event in ordered)

    tokens = [alarm.to_token() for alarm in members]
    if len(set(tokens)) != len(tokens):
        # View join fan-out이든 drift든, 어느 행을 버릴지 이 계층이 정하지 않는다.
        raise RepositoryContractError(_DUPLICATE_ALARM_REF)

    # 요청 행은 canonical key가 곧 필터 조건이라 **항상** member에 포함된다.
    # 그래서 "요청이 member에 없다" 분기를 두지 않는다 — 도달할 수 없는 code다.
    return ResolvedIncident(
        lot_id=snapshot.lot_id,
        chamber_id=snapshot.chamber_id,
        requested_alarm=requested_alarm,
        representative_alarm=members[0],
        member_alarms=members,
    )
