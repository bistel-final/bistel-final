"""incident 해석용 **읽기 전용** 저장소 (`V5-C-1.1`).

## 왜 `repository.py`가 아닌가

`repository.py`는 C-0.1의 **write 경계**다. `_require_transaction()`·`_write()`·
`_insert_one()`·감사 append가 그 파일의 계약인데 여기에는 하나도 해당하지 않는다. 같은
파일에 두면 다음 구현자가 그 관례를 따라 읽기에도 transaction을 요구할 유인이 생긴다.

예외 계층과 공용 `execute_read_all()` seam을 **재사용한다.** 평행한 오류 계층·SQLSTATE
표를 새로 만들면 같은 DB 오류가 두 이름을 갖게 된다.

## 왜 한 statement인가

요청 유무·중복·owner 상태·drift·member를 **같은 snapshot**에서 봐야 한다. 나눠 실행하면
그 사이에 데이터가 바뀌어 "요청은 있는데 member는 없다" 같은 존재할 수 없는 조합을
보고하게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, Row

from app.agent.repository import (
    RepositoryContractError,
    RepositoryNotFound,
    execute_read_all,
)
from app.common.enums import AlarmSource
from app.common.schemas import AlarmRef

__all__ = [
    "IncidentAlarmEvent",
    "IncidentSnapshot",
    "RESOLVED_ALARM_SELECT_SQL",
    "fetch_incident_snapshot",
    "parse_r03_member_contract",
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
    r03_member_wafer_refs: tuple[tuple[str, str], ...] = ()
    r03_member_alarm_refs: tuple[AlarmRef, ...] = ()
    r03_contract_checked: bool = False


#: incident snapshot과 C-5.3 pending batch가 공유하는 **유일한 resolved SELECT**.
#:
#: 이 projection을 복제하면 수동 POST와 자동 batch가 서로 다른 owner·drift 규칙으로
#: 같은 alarm을 해석할 수 있다. 두 caller는 뒤쪽 policy만 달리하고 이 raw/canonical
#: mapping은 반드시 함께 바뀐다.
RESOLVED_ALARM_SELECT_SQL: Final = """
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
"""


#: 요청 resolve·scoped unresolved·raw/canonical drift·member를 한 snapshot에 담는다.
#:
#: `candidate`는 raw key와 canonical key **둘 중 하나라도** 요청 incident를 가리키는
#: 행이다. 한쪽만 보면 drift 행이 반대편에서 조용히 성공한다.
#:
#: **owner 결손은 canonical key 두 필드를 모두 본다.** `lot_history.chamber_id`가
#: nullable이라(`lot_id`만 NOT NULL) `canonical_lot_id IS NULL`만 보면 "lot은 있는데
#: chamber가 없는" owner를 놓친다. 그러면 R03 member가 오류 없이 **조용히 빠진다** —
#: raw는 요청 incident를 가리켜 candidate에 들어오고, NULL 비교가 전부 UNKNOWN이라
#: drift에도 member join에도 걸리지 않기 때문이다(구현리뷰 2차 필수 1).
#:
#: `drift_count`는 canonical이 **둘 다 확정된** 행만 본다. 그 안에서는
#: `IS DISTINCT FROM`으로 NULL-safe하게 비교한다.
#:
#: **이 부분은 변이로 red가 되지 않는다.** `<>`로 되돌려도 회귀가 전부 통과한다 — owner
#: 결손을 먼저 잡으므로 drift 판정에 도달하는 행은 이미 canonical 두 필드가 확정돼 있고,
#: 그때 두 연산자는 같기 때문이다. 의도를 명시하는 방어이며 mapping 순서가 바뀌면 의미가
#: 생긴다. 그 순서는 `test_the_mapping_order_is_fixed`가 고정한다.
_SNAPSHOT = text(
    f"""
    WITH resolved AS (
        {RESOLVED_ALARM_SELECT_SQL}
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
        (SELECT count(*) FROM candidate
          WHERE canonical_lot_id IS NULL OR canonical_chamber_id IS NULL)
                            AS unresolved_count,
        (SELECT count(*) FROM candidate
          WHERE canonical_lot_id IS NOT NULL
            AND canonical_chamber_id IS NOT NULL
            AND (
                raw_lot_id IS DISTINCT FROM canonical_lot_id
                OR raw_chamber_id IS DISTINCT FROM canonical_chamber_id
            ))
                            AS drift_count,
        (SELECT count(*) FROM r03_alarm_history AS r03
          WHERE r03.lot_id = s.lot_id AND r03.chamber_id = s.chamber_id)
                            AS r03_count,
        (SELECT r03.member_wafer_refs FROM r03_alarm_history AS r03
          WHERE r03.lot_id = s.lot_id AND r03.chamber_id = s.chamber_id
          ORDER BY r03.alarm_id LIMIT 1)
                            AS r03_member_wafer_refs,
        (SELECT r03.member_alarm_refs FROM r03_alarm_history AS r03
          WHERE r03.lot_id = s.lot_id AND r03.chamber_id = s.chamber_id
          ORDER BY r03.alarm_id LIMIT 1)
                            AS r03_member_alarm_refs,
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
#: **요청 identity가 여러 행이다.** member 중복과 다른 뜻이다 — 어느 incident인지
#: 알 수 없다는 것이지, incident 안에 같은 알람이 둘이라는 것이 아니다.
_REQUESTED_AMBIGUOUS: Final = "REQUESTED_ALARM_AMBIGUOUS"
_OWNER_UNRESOLVED: Final = "ALARM_OWNER_UNRESOLVED"
_KEY_MISMATCH: Final = "INCIDENT_KEY_MISMATCH"


def parse_r03_member_contract(
    raw_wafers: object,
    raw_alarms: object,
) -> tuple[tuple[tuple[str, str], ...], tuple[AlarmRef, ...]]:
    """final R03의 persisted 3 WAFER·9 TRACE AlarmRef를 exact 검증한다."""

    if not isinstance(raw_wafers, list) or not isinstance(raw_alarms, list):
        raise RepositoryContractError("FINAL_DATASET_CONTRACT_MISMATCH")
    if len(raw_wafers) != 3 or len(raw_alarms) != 9:
        raise RepositoryContractError("FINAL_DATASET_CONTRACT_MISMATCH")
    wafer_refs: list[tuple[str, str]] = []
    alarm_refs: list[AlarmRef] = []
    try:
        for item in raw_wafers:
            if not isinstance(item, dict) or set(item) != {
                "lot_hist_id",
                "wafer_id",
            }:
                raise ValueError("R03_WAFER_REF_INVALID")
            lot_hist_id = item["lot_hist_id"]
            wafer_id = item["wafer_id"]
            if (
                not isinstance(lot_hist_id, str)
                or not lot_hist_id.strip()
                or not isinstance(wafer_id, str)
                or not wafer_id.strip()
            ):
                raise ValueError("R03_WAFER_REF_INVALID")
            wafer_refs.append((lot_hist_id, wafer_id))
        alarm_refs = [AlarmRef.model_validate(item) for item in raw_alarms]
    except (TypeError, ValueError) as exc:
        raise RepositoryContractError("FINAL_DATASET_CONTRACT_MISMATCH") from exc
    if (
        len(set(wafer_refs)) != 3
        or len({item.to_token() for item in alarm_refs}) != 9
        or any(item.source is not AlarmSource.TRACE for item in alarm_refs)
    ):
        raise RepositoryContractError("FINAL_DATASET_CONTRACT_MISMATCH")
    return tuple(wafer_refs), tuple(alarm_refs)


def fetch_incident_snapshot(
    connection: Connection, requested_alarm: AlarmRef
) -> IncidentSnapshot:
    """요청 AlarmRef가 속한 incident 전체를 한 번에 읽는다.

    `alarm_id` 단독 조회 경로를 만들지 않는다. 알람 ID 공간이 source별이라
    (요구사항 §8.1) source 없는 조회는 다른 source의 동명 ID를 집어올 수 있다.
    """

    rows = execute_read_all(
        connection,
        _SNAPSHOT,
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
        raise RepositoryContractError(_REQUESTED_AMBIGUOUS)
    if head.lot_id is None or head.chamber_id is None:
        raise RepositoryContractError(_OWNER_UNRESOLVED)
    if int(head.unresolved_count) > 0:
        # **요청 incident 안의** 결손만 본다. 무관한 incident의 결손은 이 요청을
        # 막지 않는다 — 전역 불변식은 A-1.5 전수 검증의 몫이다.
        raise RepositoryContractError(_OWNER_UNRESOLVED)
    if int(head.drift_count) > 0:
        # R03만 raw key와 owner key가 갈릴 수 있다. canonical로 조용히 덮지 않는다.
        raise RepositoryContractError(_KEY_MISMATCH)

    r03_contract_checked = hasattr(head, "r03_count")
    r03_count = int(getattr(head, "r03_count", 0))
    if r03_count > 1:
        raise RepositoryContractError("FINAL_DATASET_CONTRACT_MISMATCH")
    wafer_refs: tuple[tuple[str, str], ...] = ()
    alarm_refs: tuple[AlarmRef, ...] = ()
    if r03_count == 1:
        raw_wafers = getattr(head, "r03_member_wafer_refs", None)
        raw_alarms = getattr(head, "r03_member_alarm_refs", None)
        wafer_refs, alarm_refs = parse_r03_member_contract(raw_wafers, raw_alarms)

    return IncidentSnapshot(
        lot_id=head.lot_id,
        chamber_id=head.chamber_id,
        members=tuple(_member(row) for row in rows if row.member_alarm_id is not None),
        r03_member_wafer_refs=wafer_refs,
        r03_member_alarm_refs=alarm_refs,
        r03_contract_checked=r03_contract_checked,
    )


def _member(row: Row[Any]) -> IncidentAlarmEvent:
    return IncidentAlarmEvent(
        alarm=AlarmRef(
            source=AlarmSource(row.member_source), alarm_id=row.member_alarm_id
        ),
        occurred_at=row.member_occurred_at,
        lot_hist_id=row.member_lot_hist_id,
    )
