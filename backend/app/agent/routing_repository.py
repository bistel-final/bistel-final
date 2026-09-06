"""WAFER routing **읽기 전용** 저장소 (`V5-C-1.2`).

C-1.1이 해석한 incident member를 실제 WAFER 경로로 넓힌다. 실제 route의 단일 기준은
PostgreSQL `lot_history`이고, Neo4j는 구조 교차검증 근거일 뿐 이 파일에 들어오지 않는다.

## 왜 한 statement인가

member owner·incident 일치·중복·route step을 **같은 snapshot**에서 봐야 한다. 나눠
실행하면 그 사이에 데이터가 바뀌어 "member는 있는데 route가 없다" 같은 존재할 수 없는
조합을 보고하게 된다. READ COMMITTED에서는 statement마다 snapshot이 새로 잡힌다.

## 왜 `unnest` 두 배열인가

member identity는 `(source, alarm_id)` 쌍이다. `'TRACE:TA-01'`처럼 구분자로 합치면 ID
본문에 그 구분자가 들어오지 않는다는 **계약 밖 가정**을 하게 된다. 같은 길이의 평행
배열이면 순서로 대응하므로 구분자가 필요 없다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, Row

from app.agent.incident_repository import parse_r03_member_contract
from app.agent.repository import (
    RepositoryContractError,
    RepositoryNotFound,
    execute_read_all,
)
from app.common.enums import AlarmSource
from app.common.schemas import AlarmRef

__all__ = [
    "RouteStep",
    "RouteSnapshot",
    "fetch_route_snapshot",
]


@dataclass(frozen=True, slots=True)
class RouteStep:
    """`lot_history` 한 행. **`fault_code`는 없다.**"""

    lot_hist_id: str
    lot_id: str
    wafer_id: str
    wafer_no: int | None
    step_id: str
    area_id: str | None
    equipment_id: str
    chamber_id: str
    recipe_id: str | None
    track_in_at: datetime
    track_out_at: datetime | None
    lot_first_track_in_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RouteSnapshot:
    """한 statement가 본 member↔WAFER 대응과 route step 전체.

    **어떤 incident로 읽었는지를 함께 들고 있는다.** 없으면 이 snapshot을 다른
    incident에 붙여도 결합이 성공한다 — 오류보다 위험한 "일관된 성공 모양"이 나온다
    (구현리뷰 4차 필수 1).

    member mapping 두 개는 모두 **읽기 전용 view**다. `frozen=True`는 속성
    재대입만 막고 안쪽 `dict`의 변경은 막지 못한다. 실제로 초판에서는
    `snapshot.wafer_of_member.clear()`가 성공했고, 그 뒤 결합이 sanitized
    오류가 아니라 raw `KeyError`로 나갔다.

    **provenance도 같은 이유로 값 자체를 담는다.** 참조를 담으면 그 객체를 통해 계약이
    깨진다 — tuple로 감싸는 것만으로는 부족하다.
    """

    #: 이 snapshot을 읽을 때 쓴 canonical incident key.
    lot_id: str
    chamber_id: str
    #: 읽을 때 넘긴 member identity. 순서까지 보존한다.
    #:
    #: **`AlarmRef` 객체가 아니라 canonical key다.** `AlarmRef`는 frozen이 아니어서
    #: incident와 snapshot이 같은 객체를 공유하면 한쪽을 바꿀 때 양쪽이 함께 바뀐다.
    #: 그러면 tuple 비교가 통과하고 mapping key만 예전 값으로 남아 raw `KeyError`가 난다
    #: (구현리뷰 5차 필수 1). key tuple은 그 자체로 불변이다.
    member_keys: tuple[tuple[AlarmSource, str], ...]
    #: `(source, alarm_id)` → owner WAFER. 읽기 전용이다.
    wafer_of_member: Mapping[tuple[AlarmSource, str], str]
    #: `(source, alarm_id)` → 해당 alarm의 `lot_hist_id`. 읽기 전용이다.
    lot_hist_id_of_member: Mapping[tuple[AlarmSource, str], str]
    steps: tuple[RouteStep, ...]
    diagnostic_wafer_refs: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """**불변성을 타입이 보장한다.** factory가 아니라.

        생성 지점 하나에서만 감싸면 다른 지점(테스트 fixture 포함)이 가변 `dict`를 넣어
        계약이 조용히 깨진다. 여기서 감싸면 어떤 경로로 만들어도 읽기 전용이다.
        """

        object.__setattr__(
            self, "wafer_of_member", MappingProxyType(dict(self.wafer_of_member))
        )
        object.__setattr__(
            self,
            "lot_hist_id_of_member",
            MappingProxyType(dict(self.lot_hist_id_of_member)),
        )


_MEMBER_MISSING: Final = "ROUTE_MEMBER_NOT_FOUND"
_MEMBER_DUPLICATE: Final = "ROUTE_MEMBER_DUPLICATE"
_OWNER_UNRESOLVED: Final = "ROUTE_MEMBER_OWNER_UNRESOLVED"
_INCIDENT_MISMATCH: Final = "ROUTE_INCIDENT_MISMATCH"
_WAFER_ID_MISSING: Final = "ROUTE_WAFER_ID_MISSING"
_ROUTE_INCOMPLETE: Final = "WAFER_ROUTE_INCOMPLETE"
_EMPTY_MEMBERS: Final = "EMPTY_MEMBER_ALARMS"

#: member 대응과 route step을 한 statement로 낸다.
#:
#: `row_kind`로 두 종류를 구분한다. status count는 `CROSS JOIN`이라 모든 행에 실린다 —
#: member가 하나도 resolve되지 않아 step이 0건이어도 판정에 필요한 수치는 온다.
#:
#: **명시 column만 SELECT한다.** `SELECT *`를 쓰면 `lot_history.fault_code`가 딸려 온다.
#:
#: `missing_count`는 `v.lot_hist_id`가 아니라 **`v.alarm_id`**로 센다. TRACE·SUMMARY는
#: View가 `lot_history`에 LEFT JOIN이라 owner를 못 찾아도 알람 행은 남는다.
#: `lot_hist_id`로 세면 "알람이 없다"와 "owner를 못 찾았다"가 한 code로 뭉쳐진다.
#:
#: **owner 결손은 canonical key 두 필드를 모두 본다.** `lot_history.chamber_id`가
#: nullable이라(`lot_id`만 NOT NULL) `owner_lot_id IS NULL`만 보면 "lot은 있는데
#: chamber가 없는" owner를 놓친다. 그 행은 drift에도 안 잡히고(`FALSE OR NULL`이
#: `NULL`이라 `FILTER`가 세지 않는다) mapping까지 들어간 뒤 `_step()`의 NULL 검사에
#: 걸려 `WAFER_ROUTE_INCOMPLETE`로 끝난다 — fail-closed는 유지되지만 **reason이
#: 원인을 가리키지 않는다**(구현리뷰 PR #155 필수 2). `V5-C-1.1`이 바로 직전
#: Task에서 같은 결함을 닫았다.
#:
#: `drift_count`는 canonical이 **둘 다 확정된** 행만 보고 `IS DISTINCT FROM`을 쓴다.
_SNAPSHOT = text(
    """
    WITH member_ref AS (
        SELECT t.source, t.alarm_id
        FROM unnest(CAST(:sources AS text[]), CAST(:alarm_ids AS text[]))
             AS t(source, alarm_id)
    ),
    resolved AS (
        SELECT
            m.source        AS source,
            m.alarm_id      AS alarm_id,
            v.alarm_id      AS event_alarm_id,
            v.lot_hist_id   AS lot_hist_id,
            h.lot_id        AS owner_lot_id,
            h.chamber_id    AS owner_chamber_id,
            h.wafer_id      AS owner_wafer_id
        FROM member_ref AS m
        LEFT JOIN v_alarm_event AS v
               ON v.source = m.source AND v.alarm_id = m.alarm_id
        LEFT JOIN lot_history AS h ON h.lot_hist_id = v.lot_hist_id
    ),
    status AS (
        SELECT
            count(*) FILTER (WHERE event_alarm_id IS NULL)       AS missing_count,
            count(*) FILTER (
                WHERE event_alarm_id IS NOT NULL
                  AND (owner_lot_id IS NULL OR owner_chamber_id IS NULL)
            )                                                    AS unresolved_count,
            count(*) FILTER (
                WHERE owner_lot_id IS NOT NULL
                  AND owner_chamber_id IS NOT NULL
                  AND (
                      owner_lot_id IS DISTINCT FROM :lot_id
                      OR owner_chamber_id IS DISTINCT FROM :chamber_id
                  )
            )                                                    AS drift_count,
            count(*) FILTER (
                WHERE owner_lot_id IS NOT NULL AND owner_wafer_id IS NULL
            )                                                    AS wafer_missing_count,
            (
                SELECT count(*) FROM (
                    SELECT source, alarm_id
                    FROM resolved
                    GROUP BY source, alarm_id
                    HAVING count(*) > 1
                ) AS d
            )                                                    AS duplicate_count
        FROM resolved
    ),
    wafer AS (
        SELECT DISTINCT owner_lot_id AS lot_id, owner_wafer_id AS wafer_id
        FROM resolved
        WHERE owner_lot_id IS NOT NULL AND owner_wafer_id IS NOT NULL
    )
    SELECT
        'member'::text AS row_kind,
        s.missing_count, s.unresolved_count, s.drift_count,
        s.wafer_missing_count, s.duplicate_count,
        (SELECT member_wafer_refs FROM r03_alarm_history
          WHERE lot_id = :lot_id AND chamber_id = :chamber_id
          ORDER BY alarm_id LIMIT 1) AS r03_member_wafer_refs,
        (SELECT member_alarm_refs FROM r03_alarm_history
          WHERE lot_id = :lot_id AND chamber_id = :chamber_id
          ORDER BY alarm_id LIMIT 1) AS r03_member_alarm_refs,
        r.source            AS member_source,
        r.alarm_id          AS member_alarm_id,
        r.owner_wafer_id    AS wafer_id,
        r.lot_hist_id       AS member_lot_hist_id,
        NULL::varchar(20)   AS lot_hist_id,
        NULL::varchar(20)   AS lot_id,
        NULL::smallint      AS wafer_no,
        NULL::varchar(20)   AS step_id,
        NULL::varchar(10)   AS area_id,
        NULL::varchar(20)   AS equipment_id,
        NULL::varchar(24)   AS chamber_id,
        NULL::varchar(20)   AS recipe_id,
        NULL::timestamp     AS track_in_at,
        NULL::timestamp     AS track_out_at,
        NULL::timestamp     AS lot_first_track_in_at
    FROM resolved AS r CROSS JOIN status AS s

    UNION ALL

    SELECT
        'step'::text AS row_kind,
        s.missing_count, s.unresolved_count, s.drift_count,
        s.wafer_missing_count, s.duplicate_count,
        (SELECT member_wafer_refs FROM r03_alarm_history
          WHERE lot_id = :lot_id AND chamber_id = :chamber_id
          ORDER BY alarm_id LIMIT 1) AS r03_member_wafer_refs,
        (SELECT member_alarm_refs FROM r03_alarm_history
          WHERE lot_id = :lot_id AND chamber_id = :chamber_id
          ORDER BY alarm_id LIMIT 1) AS r03_member_alarm_refs,
        NULL::varchar(10)   AS member_source,
        NULL::varchar(24)   AS member_alarm_id,
        h.wafer_id,
        NULL::varchar(20)   AS member_lot_hist_id,
        h.lot_hist_id, h.lot_id, h.wafer_no,
        h.step_id, h.area_id, h.equipment_id, h.chamber_id, h.recipe_id,
        h.track_in_at, h.track_out_at,
        (SELECT min(first_history.track_in_at) FROM lot_history AS first_history
         WHERE first_history.lot_id = h.lot_id
           AND first_history.chamber_id = h.chamber_id
           AND first_history.step_id = h.step_id) AS lot_first_track_in_at
    FROM lot_history AS h
    JOIN wafer AS w ON h.lot_id = w.lot_id AND h.wafer_id = w.wafer_id
    CROSS JOIN status AS s
    """
)


def fetch_route_snapshot(
    connection: Connection,
    *,
    lot_id: str,
    chamber_id: str,
    member_alarms: Sequence[AlarmRef],
) -> RouteSnapshot:
    """incident member가 가리키는 WAFER의 실제 route를 한 번에 읽는다.

    `lot_id`·`chamber_id`는 C-1.1이 확정한 incident key다. member owner가 그것과 다르면
    두 read 사이에 데이터가 바뀌었거나 caller가 손으로 만든 incident를 넘긴 것이다.
    """

    if not member_alarms:
        raise RepositoryContractError(_EMPTY_MEMBERS)

    sources = [AlarmSource(alarm.source).value for alarm in member_alarms]
    alarm_ids = [alarm.alarm_id for alarm in member_alarms]
    # 두 배열의 길이가 같아야 `unnest`가 순서쌍으로 대응한다.
    if len(sources) != len(alarm_ids):  # pragma: no cover - 위에서 함께 만든다
        raise RepositoryContractError(_EMPTY_MEMBERS)

    rows = execute_read_all(
        connection,
        _SNAPSHOT,
        {
            "sources": sources,
            "alarm_ids": alarm_ids,
            "lot_id": lot_id,
            "chamber_id": chamber_id,
        },
    )
    head = rows[0]

    # **앞 단계가 확정하지 않은 값을 뒤 단계가 쓰지 않는다.**
    if int(head.missing_count) > 0:
        raise RepositoryNotFound(_MEMBER_MISSING)
    if int(head.duplicate_count) > 0:
        # View join fan-out이든 drift든 어느 행을 버릴지 이 계층이 고르지 않는다.
        raise RepositoryContractError(_MEMBER_DUPLICATE)
    if int(head.unresolved_count) > 0:
        raise RepositoryContractError(_OWNER_UNRESOLVED)
    if int(head.drift_count) > 0:
        raise RepositoryContractError(_INCIDENT_MISMATCH)
    if int(head.wafer_missing_count) > 0:
        raise RepositoryContractError(_WAFER_ID_MISSING)

    wafer_mapping = {
        (AlarmSource(row.member_source), row.member_alarm_id): row.wafer_id
        for row in rows
        if row.row_kind == "member"
    }
    lot_hist_mapping = {
        (AlarmSource(row.member_source), row.member_alarm_id): row.member_lot_hist_id
        for row in rows
        if row.row_kind == "member"
    }
    steps = tuple(_step(row) for row in rows if row.row_kind == "step")
    if not steps:
        # owner가 resolve됐다면 그 owner 행 자체가 route의 최소 1행이다. 여기 오면
        # query·mapping 계약이 깨진 것이지 "route가 없는 정상 상태"가 아니다.
        raise RepositoryContractError(_ROUTE_INCOMPLETE)
    diagnostic_refs: list[tuple[str, str]] = []
    if any(AlarmSource(alarm.source) is AlarmSource.R03 for alarm in member_alarms):
        raw_wafers = getattr(head, "r03_member_wafer_refs", None)
        raw_alarms = getattr(head, "r03_member_alarm_refs", None)
        if raw_wafers is not None or raw_alarms is not None:
            parsed_refs, _alarm_refs = parse_r03_member_contract(
                raw_wafers,
                raw_alarms,
            )
            diagnostic_refs = list(parsed_refs)
            step_refs = {(step.lot_hist_id, step.wafer_id) for step in steps}
            if not set(diagnostic_refs) <= step_refs:
                raise RepositoryContractError("FINAL_DATASET_CONTRACT_MISMATCH")
    return RouteSnapshot(
        lot_id=lot_id,
        chamber_id=chamber_id,
        member_keys=tuple(
            (AlarmSource(alarm.source), alarm.alarm_id) for alarm in member_alarms
        ),
        # `__post_init__`이 읽기 전용으로 감싼다.
        wafer_of_member=wafer_mapping,
        lot_hist_id_of_member=lot_hist_mapping,
        steps=steps,
        diagnostic_wafer_refs=tuple(diagnostic_refs),
    )


def _step(row: Row[Any]) -> RouteStep:
    for required in (
        row.lot_hist_id,
        row.lot_id,
        row.wafer_id,
        row.step_id,
        row.equipment_id,
        row.chamber_id,
        row.track_in_at,
    ):
        if required is None:
            # NULL을 임의 값으로 보정하면 route 순서가 데이터가 아니라 구현 선택이 된다.
            raise RepositoryContractError(_ROUTE_INCOMPLETE)
    return RouteStep(
        lot_hist_id=row.lot_hist_id,
        lot_id=row.lot_id,
        wafer_id=row.wafer_id,
        wafer_no=None if row.wafer_no is None else int(row.wafer_no),
        step_id=row.step_id,
        area_id=row.area_id,
        equipment_id=row.equipment_id,
        chamber_id=row.chamber_id,
        recipe_id=row.recipe_id,
        track_in_at=row.track_in_at,
        track_out_at=row.track_out_at,
        lot_first_track_in_at=getattr(row, "lot_first_track_in_at", None),
    )
