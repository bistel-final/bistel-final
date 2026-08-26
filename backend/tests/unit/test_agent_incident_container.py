"""`V5-C-1.1` incident 해석 격리 PostgreSQL 16 회귀.

## 왜 실제 DB여야 하나

이 Task의 판정은 대부분 **`v_alarm_event`의 구조**에서 나온다.

- TRACE·SUMMARY는 `lot_history`에 LEFT JOIN이라 owner 결손 행이 남는다.
- R03는 INNER JOIN이고 `lot_id·chamber_id`를 자기 table에서 가져오므로 **raw key와
  owner key가 갈릴 수 있다.**

단위 fake는 이 차이를 재현하지 못한다. 여기서는 legacy base 9 → wafer 폭 정정 →
`v5/001`을 실제로 적용해 같은 View 위에서 돌린다.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
for candidate in (BACKEND_ROOT, BACKEND_ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import postgres_transition as transition  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402

from app.agent import incident as inc  # noqa: E402
from app.agent import repository as repo  # noqa: E402
from app.common.enums import AlarmSource  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402

pytestmark = pytest.mark.container

FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"
V5_SQL = (
    REPOSITORY_ROOT
    / "backend"
    / "migrations"
    / "v5"
    / "001_reference_extensions_final.sql"
)

TRACE = AlarmSource.TRACE
SUMMARY = AlarmSource.SUMMARY
R03 = AlarmSource.R03

#: 요청 incident. 다른 lot·다른 chamber 대조군을 같은 fixture에 함께 둔다.
LOT = "LOT001"
CHAMBER = "EQP01-PM1"

T0 = datetime(2026, 8, 1, 10, 0, 0)
T1 = datetime(2026, 8, 1, 11, 0, 0)
T2 = datetime(2026, 8, 1, 12, 0, 0)

_WAFER_ALTER = "\n".join(
    f"ALTER TABLE {table} ALTER COLUMN wafer TYPE varchar(24) "
    f"USING wafer::varchar(24);"
    for table in transition.WAFER_ALTER_TABLES
)

_ALARM_TABLES = (
    "r03_alarm_history",
    "trace_alarm_history",
    "summary_alarm_history",
    "lot_history",
)


def _ref(source: AlarmSource, alarm_id: str) -> AlarmRef:
    return AlarmRef(source=source, alarm_id=alarm_id)


def _r03_id(suffix: str) -> str:
    """`^R03-[0-9a-f]{20}$`. CHECK가 강제한다."""

    return "R03-" + suffix.rjust(20, "0")


@pytest.fixture(scope="module")
def engine() -> Any:
    """final base 9 + `v5/001`을 세운 일회용 PostgreSQL 16."""

    with postgres.one_off_postgres(database="c11") as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname="c11",
            user=endpoint.username,
            password=endpoint.password,
            autocommit=True,
        ) as raw:
            cursor = raw.cursor()
            cursor.execute(
                (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
            )
            cursor.execute(_WAFER_ALTER)
            cursor.execute(V5_SQL.read_text(encoding="utf-8"))
        value = create_engine(
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/c11"
        )
        try:
            yield value
        finally:
            value.dispose()


@pytest.fixture
def db(engine: Any) -> Any:
    with engine.begin() as connection:
        for table in _ALARM_TABLES:
            connection.execute(text(f"TRUNCATE {table} CASCADE"))
        connection.execute(
            text(
                "INSERT INTO dim_parameter (parameter_id, parameter_name, area) "
                "VALUES ('PARAM01', 'p', 'etch') ON CONFLICT DO NOTHING"
            )
        )
    return engine


# --- fixture 조립 -----------------------------------------------------------


def _lot(
    connection: Any,
    lot_hist_id: str,
    *,
    lot_id: str = LOT,
    wafer_no: int = 1,
    chamber_id: str | None = CHAMBER,
) -> str:
    connection.execute(
        text(
            "INSERT INTO lot_history "
            " (lot_hist_id, lot_id, wafer_no, wafer_id, chamber_id, area_id,"
            "  equipment_id, recipe_id) "
            "VALUES (:h, :l, :n, :w, :c, 'etch', 'EQP01', 'RECIPE01')"
        ),
        {
            "h": lot_hist_id,
            "l": lot_id,
            "n": wafer_no,
            "w": f"{lot_id}W{wafer_no:03d}",
            "c": chamber_id,
        },
    )
    return lot_hist_id


def _trace(
    connection: Any,
    alarm_id: str,
    *,
    occurred_at: datetime | None = T0,
    lot_id: str = LOT,
    wafer_no: int = 1,
    chamber_id: str = CHAMBER,
) -> str:
    connection.execute(
        text(
            "INSERT INTO trace_alarm_history "
            " (alarm_id, occurred_at, area, equipment, chamber, parameter,"
            "  recipe, lot, wafer, step_no) "
            "VALUES (:a, :t, 'etch', 'EQP01', :c, 'PARAM01', 'RECIPE01',"
            "        :l, :w, 1)"
        ),
        {
            "a": alarm_id,
            "t": occurred_at,
            "c": chamber_id,
            "l": lot_id,
            "w": f"{lot_id}W{wafer_no:03d}",
        },
    )
    return alarm_id


def _summary(
    connection: Any,
    alarm_id: str,
    *,
    occurred_at: datetime | None = T0,
    lot_id: str = LOT,
    wafer_no: int = 1,
    chamber_id: str = CHAMBER,
) -> str:
    connection.execute(
        text(
            "INSERT INTO summary_alarm_history "
            " (alarm_id, occurred_at, area, equipment, chamber, parameter,"
            "  recipe, lot, wafer, step_no) "
            "VALUES (:a, :t, 'etch', 'EQP01', :c, 'PARAM01', 'RECIPE01',"
            "        :l, :w, 1)"
        ),
        {
            "a": alarm_id,
            "t": occurred_at,
            "c": chamber_id,
            "l": lot_id,
            "w": f"{lot_id}W{wafer_no:03d}",
        },
    )
    return alarm_id


def _r03(
    connection: Any,
    alarm_id: str,
    lot_hist_id: str,
    *,
    occurred_at: datetime = T0,
    lot_id: str = LOT,
    chamber_id: str = CHAMBER,
    step_no: int = 1,
) -> str:
    """`lot_id`·`chamber_id`를 인자로 받는다 — **owner와 다르게 넣을 수 있다.**

    그게 R03 raw key drift이고, View가 그것을 숨기지 않는다는 것이 이 파일의 축 하나다.
    """

    connection.execute(
        text(
            "INSERT INTO r03_alarm_history "
            " (alarm_id, occurred_at, lot_hist_id, lot_id, equipment_id,"
            "  chamber_id, parameter_id, recipe_step_no, trigger_wafer_no,"
            "  member_wafer_refs, member_alarm_refs, policy_version) "
            "VALUES (:a, :t, :h, :l, 'EQP01', :c, 'PARAM01', :s, 1,"
            "        '[1,2,3]'::jsonb, '[]'::jsonb, 'R03_CONSEC_V1')"
        ),
        {
            "a": alarm_id,
            "t": occurred_at,
            "h": lot_hist_id,
            "l": lot_id,
            "c": chamber_id,
            "s": step_no,
        },
    )
    return alarm_id


def _resolve(db: Any, requested: AlarmRef) -> inc.ResolvedIncident:
    """**transaction 없이** 읽는다 — 이 경로가 UoW를 요구하지 않는다는 증거다."""

    with db.connect() as connection:
        return inc.resolve_incident(connection, requested)


# --- 대표 선정 --------------------------------------------------------------


def test_an_older_summary_beats_a_newer_r03(db: Any) -> None:
    """**시간이 source priority보다 먼저다**(설계서 775행).

    priority를 앞세우면 R03가 대표가 된다 — 그건 조치 우선순위를 미리 계산하는 것이다.
    """

    with db.begin() as connection:
        owner = _lot(connection, "LH-A1")
        _summary(connection, "SA-01", occurred_at=T0)
        _r03(connection, _r03_id("1"), owner, occurred_at=T2)

    result = _resolve(db, _ref(R03, _r03_id("1")))
    assert result.representative_alarm == _ref(SUMMARY, "SA-01")
    assert result.lot_id == LOT
    assert result.chamber_id == CHAMBER


def test_source_priority_only_breaks_a_time_tie(db: Any) -> None:
    with db.begin() as connection:
        owner = _lot(connection, "LH-A1")
        _summary(connection, "SA-01", occurred_at=T0)
        _trace(connection, "TA-01", occurred_at=T0)
        _r03(connection, _r03_id("1"), owner, occurred_at=T0)

    result = _resolve(db, _ref(TRACE, "TA-01"))
    assert result.representative_alarm == _ref(R03, _r03_id("1"))
    assert [a.source for a in result.member_alarms] == [R03, TRACE, SUMMARY]


def test_alarm_id_is_the_last_tie_breaker(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _lot(connection, "LH-A2", wafer_no=2)
        _trace(connection, "TA-09", occurred_at=T0)
        _trace(connection, "TA-02", occurred_at=T0, wafer_no=2)

    result = _resolve(db, _ref(TRACE, "TA-09"))
    assert result.representative_alarm == _ref(TRACE, "TA-02")
    assert [a.alarm_id for a in result.member_alarms] == ["TA-02", "TA-09"]


def test_all_three_sources_land_in_one_incident(db: Any) -> None:
    """세 source를 **가리지 않고** 모은다.

    기준표상 R03 파생 incident는 12개 중 3개뿐이므로 "모든 incident에 세 source가
    있다"는 뜻이 아니다 — source 필터가 없다는 뜻이다.
    """

    with db.begin() as connection:
        owner = _lot(connection, "LH-A1")
        _trace(connection, "TA-01", occurred_at=T0)
        _summary(connection, "SA-01", occurred_at=T1)
        _r03(connection, _r03_id("1"), owner, occurred_at=T2)

    result = _resolve(db, _ref(TRACE, "TA-01"))
    assert {a.source for a in result.member_alarms} == {TRACE, SUMMARY, R03}


# --- 요청 identity ----------------------------------------------------------


def test_the_request_is_preserved_when_it_is_not_representative(db: Any) -> None:
    requested = _ref(TRACE, "TA-09")
    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _summary(connection, "SA-01", occurred_at=T0)
        _trace(connection, "TA-09", occurred_at=T2)

    result = _resolve(db, requested)
    assert result.requested_alarm == requested
    assert result.representative_alarm == _ref(SUMMARY, "SA-01")
    assert requested in result.member_alarms


def test_the_same_alarm_id_in_two_sources_is_two_members(db: Any) -> None:
    """알람 ID 공간이 source별이라는 것을 실제 두 table로 확인한다."""

    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _trace(connection, "A-001", occurred_at=T0)
        _summary(connection, "A-001", occurred_at=T1)

    result = _resolve(db, _ref(SUMMARY, "A-001"))
    assert len(result.member_alarms) == 2
    assert result.representative_alarm == _ref(TRACE, "A-001")


# --- incident 경계 ----------------------------------------------------------


def test_another_chamber_of_the_same_lot_is_excluded(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _lot(connection, "LH-B1", chamber_id="EQP01-PM2")
        _trace(connection, "TA-01", occurred_at=T0)
        _trace(connection, "TA-OTHER", occurred_at=T0, chamber_id="EQP01-PM2")

    result = _resolve(db, _ref(TRACE, "TA-01"))
    assert [a.alarm_id for a in result.member_alarms] == ["TA-01"]


def test_another_lot_in_the_same_chamber_is_excluded(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _lot(connection, "LH-C1", lot_id="LOT002")
        _trace(connection, "TA-01", occurred_at=T0)
        _trace(connection, "TA-OTHER", occurred_at=T0, lot_id="LOT002")

    result = _resolve(db, _ref(TRACE, "TA-01"))
    assert [a.alarm_id for a in result.member_alarms] == ["TA-01"]


# --- fail-closed ------------------------------------------------------------


def test_a_missing_alarm_ref_is_not_found(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _trace(connection, "TA-01")

    with pytest.raises(repo.RepositoryNotFound) as exc:
        _resolve(db, _ref(TRACE, "NOPE"))
    assert exc.value.code == "ALARM_NOT_FOUND"


def test_a_request_whose_owner_is_missing_fails(db: Any) -> None:
    """`lot_history`에 없는 LOT의 TRACE 알람. View LEFT JOIN이라 행은 남는다."""

    with db.begin() as connection:
        _trace(connection, "TA-ORPHAN", occurred_at=T0)

    with pytest.raises(repo.RepositoryContractError) as exc:
        _resolve(db, _ref(TRACE, "TA-ORPHAN"))
    assert exc.value.code == "ALARM_OWNER_UNRESOLVED"


def test_an_unresolved_member_inside_the_incident_fails(db: Any) -> None:
    """요청 incident에 속했어야 할 행이 owner를 잃으면 조용히 빠지지 않는다."""

    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _trace(connection, "TA-01", occurred_at=T0)
        # 같은 lot·chamber인데 owner가 없는 wafer.
        _trace(connection, "TA-LOST", occurred_at=T1, wafer_no=7)

    with pytest.raises(repo.RepositoryContractError) as exc:
        _resolve(db, _ref(TRACE, "TA-01"))
    assert exc.value.code == "ALARM_OWNER_UNRESOLVED"


def test_an_unresolved_alarm_in_another_incident_does_not_block_this_one(
    db: Any,
) -> None:
    """**계획리뷰 1차 필수 1의 축이다.**

    전역 count로 판정하면 무관한 한 행이 12개 incident 전부를 막는다. 요청 incident로
    한정하면 이 대조가 통과한다.
    """

    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _trace(connection, "TA-01", occurred_at=T0)
        # 전혀 다른 LOT의 owner 결손 알람.
        _trace(connection, "TA-ELSEWHERE", occurred_at=T1, lot_id="LOT099")

    result = _resolve(db, _ref(TRACE, "TA-01"))
    assert [a.alarm_id for a in result.member_alarms] == ["TA-01"]


def test_an_r03_owner_without_a_chamber_is_not_silently_dropped(db: Any) -> None:
    """**owner 결손은 canonical key 두 필드를 모두 봐야 한다**(구현리뷰 2차 필수 1).

    `lot_history.chamber_id`는 nullable이고 `lot_id`만 NOT NULL이다. R03는 raw
    `lot_id·chamber_id`를 자기 table에서 가져오므로 다음 상태가 DDL상 가능하다.

    ```text
    요청 TRACE        raw/canonical = LOT001 / EQP01-PM1
    같은 incident R03 raw           = LOT001 / EQP01-PM1
    R03 owner         canonical     = LOT001 / NULL
    ```

    `canonical_lot_id IS NULL`만 보면 이 행을 놓치고, NULL 비교가 전부 UNKNOWN이라
    drift에도 member join에도 걸리지 않는다. 그러면 R03 member가 **오류 없이 사라지고**
    incident member 수·대표 알람·후속 run 입력이 실제 알람 집합과 달라진다.

    요청 자체의 chamber가 NULL인 경우는 status 단계가 먼저 막으므로 이 경로를 증명하지
    못한다. 그래서 **요청은 정상 TRACE**로 두고 비요청 R03의 owner만 결손시킨다.
    """

    with db.begin() as connection:
        _lot(connection, "LH-A1")
        # owner의 chamber가 없다. lot은 있다.
        _lot(connection, "LH-NOCHAMBER", wafer_no=2, chamber_id=None)
        _trace(connection, "TA-01", occurred_at=T0)
        _r03(connection, _r03_id("9"), "LH-NOCHAMBER", occurred_at=T1)

    with pytest.raises(repo.RepositoryContractError) as exc:
        _resolve(db, _ref(TRACE, "TA-01"))
    assert exc.value.code == "ALARM_OWNER_UNRESOLVED"


def test_an_r03_raw_key_drift_is_not_silently_overwritten(db: Any) -> None:
    """**계획리뷰 1차 필수 2의 축이다.**

    R03만 raw key와 owner key가 갈릴 수 있다 — View가 `lot_id·chamber_id`를 r03 table
    에서 가져오고 owner는 `lot_hist_id`로 따로 붙기 때문이다. canonical 값으로 덮으면
    이 상태가 성공으로 보인다.
    """

    with db.begin() as connection:
        owner = _lot(connection, "LH-A1")
        _trace(connection, "TA-01", occurred_at=T0)
        # owner는 LOT001·EQP01-PM1인데 자기 컬럼은 다른 chamber를 가리킨다.
        _r03(connection, _r03_id("1"), owner, occurred_at=T1, chamber_id="EQP01-PM9")

    with pytest.raises(repo.RepositoryContractError) as exc:
        _resolve(db, _ref(TRACE, "TA-01"))
    assert exc.value.code == "INCIDENT_KEY_MISMATCH"


def test_the_drift_also_fails_from_the_raw_side(db: Any) -> None:
    """raw key가 요청 incident를 가리키는 방향도 막는다."""

    with db.begin() as connection:
        _lot(connection, "LH-A1")
        other = _lot(connection, "LH-D1", lot_id="LOT003", chamber_id="EQP01-PM3")
        _trace(connection, "TA-01", occurred_at=T0)
        # owner는 LOT003·PM3인데 raw는 요청 incident를 가리킨다.
        _r03(connection, _r03_id("2"), other, occurred_at=T1)

    with pytest.raises(repo.RepositoryContractError) as exc:
        _resolve(db, _ref(TRACE, "TA-01"))
    assert exc.value.code == "INCIDENT_KEY_MISMATCH"


@pytest.mark.parametrize("source", ["trace", "summary"])
def test_a_null_occurred_at_is_a_contract_error(db: Any, source: str) -> None:
    """`r03_alarm_history.occurred_at`은 NOT NULL이라 이 fixture를 만들 수 없다."""

    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _lot(connection, "LH-A2", wafer_no=2)
        _trace(connection, "TA-01", occurred_at=T0)
        insert = _trace if source == "trace" else _summary
        insert(connection, "X-NULL", occurred_at=None, wafer_no=2)

    with pytest.raises(repo.RepositoryContractError) as exc:
        _resolve(db, _ref(TRACE, "TA-01"))
    assert exc.value.code == "ALARM_OCCURRED_AT_MISSING"


def test_a_fan_out_on_the_request_makes_the_incident_ambiguous(db: Any) -> None:
    """**요청 알람 자신이 fan-out하면 어느 incident인지 알 수 없다.**

    `lot_history`에는 `(lot_id, wafer_id, chamber_id)` unique 제약이 없다. 같은 조합이
    두 행이면 View의 LEFT JOIN이 fan-out해 **같은 `alarm_id`가 2행**이 된다. 최종
    dataset에서는 A-1.5 집계가 기준표와 일치하므로 이 상태가 아니지만, schema가
    막아 주지는 않는다.

    이 fixture는 fan-out이 **요청 행**에 걸리므로 `requested_count = 2`에서 멈춘다.
    member 중복 검사까지 가지 않는다 — 그 축은 아래 회귀가 따로 탄다.
    """

    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _lot(connection, "LH-DUP")  # 같은 lot·wafer·chamber
        _trace(connection, "TA-01", occurred_at=T0)

    with pytest.raises(repo.RepositoryContractError) as exc:
        _resolve(db, _ref(TRACE, "TA-01"))
    assert exc.value.code == "REQUESTED_ALARM_AMBIGUOUS"


def test_a_fan_out_on_another_member_is_a_duplicate_member(db: Any) -> None:
    """**member 중복 분기를 실제 View로 처음 탄다**(구현리뷰 PR #151 필수 1).

    앞 회귀는 요청 행이 fan-out해 `requested_count`에서 멈춘다. 두 code가 같은 문자열일
    때는 그 사실이 보이지 않았다 — `incident.py`의 member 중복 검사는 단위 fake만
    덮고 있었고, fake는 SQL의 의미를 대신하지 못한다는 것이 이 파일 자신의 전제다.

    요청은 단일 행으로 두고 **다른 member의 owner만** fan-out시키면 그 분기에 도달한다.
    """

    with db.begin() as connection:
        _lot(connection, "LH-A1")  # 요청 owner. 단일
        _lot(connection, "LH-B1", wafer_no=2)
        _lot(connection, "LH-B2", wafer_no=2)  # wafer 2가 fan-out
        _trace(connection, "TA-01", occurred_at=T0)
        _trace(connection, "TA-FAN", occurred_at=T1, wafer_no=2)

    with pytest.raises(repo.RepositoryContractError) as exc:
        _resolve(db, _ref(TRACE, "TA-01"))
    assert exc.value.code == "DUPLICATE_MEMBER_ALARM"


# --- C-0.1 인계 · 읽기 전용 -------------------------------------------------


def test_the_result_builds_a_valid_run_command(db: Any) -> None:
    with db.begin() as connection:
        owner = _lot(connection, "LH-A1")
        _summary(connection, "SA-01", occurred_at=T0)
        _trace(connection, "TA-09", occurred_at=T1)
        _r03(connection, _r03_id("1"), owner, occurred_at=T2)

    result = _resolve(db, _ref(TRACE, "TA-09"))
    command = repo.CreateAgentRunCommand(
        thread_id="8f14e45f-ceea-467a-9c2b-1f0b8f1a0001",
        lot_id=result.lot_id,
        chamber_id=result.chamber_id,
        autonomy_level=2,
        requested_alarm=result.requested_alarm,
        representative_alarm=result.representative_alarm,
        member_alarms=result.member_alarms,
    )
    normalized = repo._validate_create_command(command)
    assert normalized.representative_alarm == _ref(SUMMARY, "SA-01")
    assert len(normalized.member_alarms) == 3


def test_resolving_writes_nothing(db: Any) -> None:
    """읽기 전용을 **행 수로** 확인한다."""

    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _trace(connection, "TA-01", occurred_at=T0)

    def _counts() -> dict[str, int]:
        with db.connect() as connection:
            return {
                table: connection.execute(
                    text(f"SELECT count(*) FROM {table}")
                ).scalar_one()
                for table in _ALARM_TABLES
            }

    before = _counts()
    _resolve(db, _ref(TRACE, "TA-01"))
    assert _counts() == before


def test_the_same_snapshot_gives_the_same_order_twice(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-A1")
        _lot(connection, "LH-A2", wafer_no=2)
        _trace(connection, "TA-03", occurred_at=T0)
        _trace(connection, "TA-01", occurred_at=T0, wafer_no=2)
        _summary(connection, "SA-02", occurred_at=T0)

    first = _resolve(db, _ref(TRACE, "TA-03"))
    second = _resolve(db, _ref(TRACE, "TA-03"))
    assert first == second
