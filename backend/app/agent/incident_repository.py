"""incident 해석용 **읽기 전용** 저장소 (`V5-C-1.1`).

## 왜 `repository.py`가 아닌가

`repository.py`는 C-0.1의 **write 경계**다. `_require_transaction()`·`_write()`·
`_insert_one()`·감사 append가 그 파일의 계약인데 여기에는 하나도 해당하지 않는다. 같은
파일에 두면 다음 구현자가 그 관례를 따라 읽기에도 transaction을 요구할 유인이 생긴다.

예외 계층과 `_translate()`는 **재사용한다.** 평행한 오류 계층·SQLSTATE 표를 새로 만들면
같은 DB 오류가 두 이름을 갖게 된다.

## 왜 한 statement인가

요청 유무·중복·owner 상태·drift·member를 **같은 snapshot**에서 봐야 한다. 나눠 실행하면
그 사이에 데이터가 바뀌어 "요청은 있는데 member는 없다" 같은 존재할 수 없는 조합을
보고하게 된다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import SQLAlchemyError

from app.agent.repository import (
    RepositoryContractError,
    RepositoryNotFound,
    _translate,
)
from app.common.enums import AlarmSource
from app.common.schemas import AlarmRef

__all__ = [
    "IncidentAlarmEvent",
    "IncidentSnapshot",
    "fetch_incident_snapshot",
]


@dataclass(frozen=True, slots=True)
class IncidentAlarmEvent:
    """incident 해석에 필요한 최소 alarm row.

    `AlarmItem` DTO 전체를 재사용하지 않는다. parameter·recipe·wafer·value는 이 Task의
    판단 입력이 아니고, 넓은 DTO를 끌어오면 label·score가 딸려 들어올 통로가 생긴다.
    """

    alarm: AlarmRef
    occurred_at: datetime | None
    lot_hist_id: str


@dataclass(frozen=True, slots=True)
class IncidentSnapshot:
    """한 statement가 본 incident 전체."""

    lot_id: str
    chamber_id: str
    members: tuple[IncidentAlarmEvent, ...]


#: 요청 resolve·scoped unresolved·raw/canonical drift·member를 한 snapshot에 담는다.
#:
#: `candidate`는 raw key와 canonical key **둘 중 하나라도** 요청 incident를 가리키는
#: 행이다. 한쪽만 보면 drift 행이 반대편에서 조용히 성공한다.
#:
#: `drift_count`는 `<>`를 쓴다. owner 미해결 행은 canonical이 NULL이라 `<>`가 NULL이
#: 되어 여기 잡히지 않는데, 그 상태는 `unresolved_count`가 먼저 잡는다(계획리뷰 2차
#: 편집 2). `IS DISTINCT FROM`이면 같은 행이 두 count에 들어가고 최종 code는 같다.
_SNAPSHOT = text(
    """
    WITH resolved AS (
        SELECT
            v.source                AS source,
            v.alarm_id              AS alarm_id,
            v.occurred_at           AS occurred_at,
            v.lot_hist_id           AS lot_hist_id,
            v.lot_id                AS raw_lot_id,
            v.chamber_id            AS raw_chamber_id,
            h.lot_id                AS canonical_lot_id,
            h.chamber_id            AS canonical_chamber_id
        FROM v_alarm_event AS v
        LEFT JOIN lot_history AS h ON h.lot_hist_id = v.lot_hist_id
    ),
    requested AS (
        SELECT * FROM resolved
        WHERE source = :source AND alarm_id = :alarm_id
    ),
    status AS (
        SELECT
            (SELECT count(*) FROM requested)                    AS requested_count,
            (SELECT canonical_lot_id FROM requested LIMIT 1)    AS lot_id,
            (SELECT canonical_chamber_id FROM requested LIMIT 1) AS chamber_id
    ),
    candidate AS (
        SELECT r.*
        FROM resolved AS r, status AS s
        WHERE s.lot_id IS NOT NULL
          AND (
                (r.raw_lot_id = s.lot_id AND r.raw_chamber_id = s.chamber_id)
             OR (r.canonical_lot_id = s.lot_id
                 AND r.canonical_chamber_id = s.chamber_id)
          )
    )
    SELECT
        s.requested_count   AS requested_count,
        s.lot_id            AS lot_id,
        s.chamber_id        AS chamber_id,
        (SELECT count(*) FROM candidate WHERE canonical_lot_id IS NULL)
                            AS unresolved_count,
        (SELECT count(*) FROM candidate
          WHERE raw_lot_id <> canonical_lot_id
             OR raw_chamber_id <> canonical_chamber_id)
                            AS drift_count,
        m.source            AS member_source,
        m.alarm_id          AS member_alarm_id,
        m.occurred_at       AS member_occurred_at,
        m.lot_hist_id       AS member_lot_hist_id
    FROM status AS s
    LEFT JOIN candidate AS m
      ON m.canonical_lot_id = s.lot_id
     AND m.canonical_chamber_id = s.chamber_id
    """
)

#: 분기 순서. **앞 단계가 확정하지 않은 값을 뒤 단계가 쓰지 않는다** —
#: `lot_id`는 요청이 정확히 1건일 때만 의미가 있다.
_REQUESTED_MISSING: Final = "ALARM_NOT_FOUND"
_REQUESTED_DUPLICATE: Final = "DUPLICATE_ALARM_REF"
_OWNER_UNRESOLVED: Final = "ALARM_OWNER_UNRESOLVED"
_KEY_MISMATCH: Final = "INCIDENT_KEY_MISMATCH"


def fetch_incident_snapshot(
    connection: Connection, requested_alarm: AlarmRef
) -> IncidentSnapshot:
    """요청 AlarmRef가 속한 incident 전체를 한 번에 읽는다.

    `alarm_id` 단독 조회 경로를 만들지 않는다. 알람 ID 공간이 source별이라
    (요구사항 §8.1) source 없는 조회는 다른 source의 동명 ID를 집어올 수 있다.
    """

    rows = _execute(
        connection,
        {
            "source": AlarmSource(requested_alarm.source).value,
            "alarm_id": requested_alarm.alarm_id,
        },
    )
    # LEFT JOIN이라 member가 없어도 status 1행은 반드시 온다.
    head = rows[0]

    if int(head.requested_count) == 0:
        raise RepositoryNotFound(_REQUESTED_MISSING)
    if int(head.requested_count) > 1:
        # `.first()`로 임의 한 행을 고르지 않는다 — 어느 incident인지 알 수 없다.
        raise RepositoryContractError(_REQUESTED_DUPLICATE)
    if head.lot_id is None or head.chamber_id is None:
        raise RepositoryContractError(_OWNER_UNRESOLVED)
    if int(head.unresolved_count) > 0:
        # **요청 incident 안의** 결손만 본다. 무관한 incident의 결손은 이 요청을
        # 막지 않는다 — 전역 불변식은 A-1.5 전수 검증의 몫이다.
        raise RepositoryContractError(_OWNER_UNRESOLVED)
    if int(head.drift_count) > 0:
        # R03만 raw key와 owner key가 갈릴 수 있다. canonical로 조용히 덮지 않는다.
        raise RepositoryContractError(_KEY_MISMATCH)

    return IncidentSnapshot(
        lot_id=head.lot_id,
        chamber_id=head.chamber_id,
        members=tuple(_member(row) for row in rows if row.member_alarm_id is not None),
    )


def _member(row: Row[Any]) -> IncidentAlarmEvent:
    return IncidentAlarmEvent(
        alarm=AlarmRef(
            source=AlarmSource(row.member_source), alarm_id=row.member_alarm_id
        ),
        occurred_at=row.member_occurred_at,
        lot_hist_id=row.member_lot_hist_id,
    )


def _execute(connection: Connection, params: dict[str, Any]) -> Sequence[Row[Any]]:
    """driver 오류를 **C-0.1과 같은 계층으로** 옮긴다.

    `_translate()`를 재사용하므로 lock 경합은 `RepositoryRetryable`, 접속·가용성은
    `RepositoryUnavailable`로 같은 이름을 갖는다. 이 Task는 재시도하지 않는다 —
    분류만 보존하고 정책은 caller가 정한다.
    """

    try:
        return connection.execute(_SNAPSHOT, params).all()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
