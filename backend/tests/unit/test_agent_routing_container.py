"""`V5-C-1.2` WAFER routing — PostgreSQL 16 격리 컨테이너 회귀.

## 왜 실제 DB여야 하나

- `unnest` 두 배열이 순서쌍으로 대응하는지는 실제 PostgreSQL에서만 확인된다.
- `v_alarm_event`의 source별 join 형상(TRACE·SUMMARY는 LEFT JOIN, R03는 INNER)이 member
  owner resolve 결과를 만든다.
- `lot_history`에 `(lot_id, wafer_id, chamber_id)` unique 제약이 없어 생기는 join
  fan-out은 실제 View로만 재현된다.

graph 결합·mismatch·의존성 실패는 `test_agent_routing.py`가 소유한다. 여기서는 실제
SQL이 만드는 member↔WAFER 대응과 route 범위를 본다.
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

from app.agent import repository as repo  # noqa: E402
from app.agent import routing as rt  # noqa: E402
from app.agent import routing_repository as rt_repo  # noqa: E402
from app.agent.incident import ResolvedIncident  # noqa: E402
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

LOT = "LOT001"
PHOTO = "EQP01-PM1"
ETCH = "EQP04-PM2"

T0 = datetime(2026, 8, 1, 10, 0, 0)
T1 = datetime(2026, 8, 1, 11, 0, 0)
T2 = datetime(2026, 8, 1, 12, 0, 0)

_WAFER_ALTER = "\n".join(
    f"ALTER TABLE {table} ALTER COLUMN wafer TYPE varchar(24) "
    f"USING wafer::varchar(24);"
    for table in transition.WAFER_ALTER_TABLES
)

_TABLES = (
    "r03_alarm_history",
    "trace_alarm_history",
    "summary_alarm_history",
    "lot_history",
)


def _ref(source: AlarmSource, alarm_id: str) -> AlarmRef:
    return AlarmRef(source=source, alarm_id=alarm_id)


@pytest.fixture(scope="module")
def engine() -> Any:
    with postgres.one_off_postgres(database="c12") as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname="c12",
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
            f"@{endpoint.host}:{endpoint.port}/c12"
        )
        try:
            yield value
        finally:
            value.dispose()


@pytest.fixture
def db(engine: Any) -> Any:
    with engine.begin() as connection:
        for table in _TABLES:
            connection.execute(text(f"TRUNCATE {table} CASCADE"))
        connection.execute(
            text(
                "INSERT INTO dim_parameter (parameter_id, parameter_name, area) "
                "VALUES ('PARAM01', 'p', 'etch') ON CONFLICT DO NOTHING"
            )
        )
    return engine


def _lot(
    connection: Any,
    lot_hist_id: str,
    *,
    lot_id: str = LOT,
    wafer_no: int = 1,
    wafer_id: str | None = None,
    chamber_id: str = PHOTO,
    step_id: str = "CT-PHOTO",
    equipment_id: str = "EQP01",
    track_in_at: datetime | None = T0,
) -> str:
    connection.execute(
        text(
            "INSERT INTO lot_history "
            " (lot_hist_id, lot_id, wafer_no, wafer_id, chamber_id, step_id,"
            "  area_id, equipment_id, recipe_id, track_in_at) "
            "VALUES (:h, :l, :n, :w, :c, :s, 'etch', :e, 'RECIPE01', :t)"
        ),
        {
            "h": lot_hist_id,
            "l": lot_id,
            "n": wafer_no,
            "w": f"{lot_id}W{wafer_no:03d}" if wafer_id is None else wafer_id,
            "c": chamber_id,
            "s": step_id,
            "e": equipment_id,
            "t": track_in_at,
        },
    )
    return lot_hist_id


def _alarm(
    connection: Any,
    table: str,
    alarm_id: str,
    *,
    lot_id: str = LOT,
    wafer_no: int = 1,
    chamber_id: str = PHOTO,
    occurred_at: datetime = T0,
) -> str:
    connection.execute(
        text(
            f"INSERT INTO {table} "
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


def _fetch(db: Any, *members: AlarmRef, chamber_id: str = PHOTO) -> Any:
    """**transaction 없이** 읽는다 — 이 경로가 UoW를 요구하지 않는다는 증거다."""

    with db.connect() as connection:
        return rt_repo.fetch_route_snapshot(
            connection,
            lot_id=LOT,
            chamber_id=chamber_id,
            member_alarms=members,
        )


# --- member 대응 -----------------------------------------------------------


def test_the_same_alarm_id_in_two_sources_resolves_to_its_own_owner(db: Any) -> None:
    """`unnest` 두 배열이 순서쌍으로 대응한다 — source가 섞이지 않는다."""

    with db.begin() as connection:
        _lot(connection, "LH-W1", wafer_no=1)
        _lot(connection, "LH-W2", wafer_no=2)
        _alarm(connection, "trace_alarm_history", "A-001", wafer_no=1)
        _alarm(connection, "summary_alarm_history", "A-001", wafer_no=2)

    snapshot = _fetch(db, _ref(TRACE, "A-001"), _ref(SUMMARY, "A-001"))
    assert snapshot.wafer_of_member == {
        (TRACE, "A-001"): "LOT001W001",
        (SUMMARY, "A-001"): "LOT001W002",
    }
    assert snapshot.lot_hist_id_of_member == {
        (TRACE, "A-001"): "LH-W1",
        (SUMMARY, "A-001"): "LH-W2",
    }


def test_several_alarms_on_one_wafer_share_a_single_route(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-W1")
        _alarm(connection, "trace_alarm_history", "TA-01")
        _alarm(connection, "summary_alarm_history", "SA-01", occurred_at=T1)

    snapshot = _fetch(db, _ref(TRACE, "TA-01"), _ref(SUMMARY, "SA-01"))
    assert set(snapshot.wafer_of_member.values()) == {"LOT001W001"}
    assert set(snapshot.lot_hist_id_of_member.values()) == {"LH-W1"}
    assert [s.lot_hist_id for s in snapshot.steps] == ["LH-W1"]


def test_two_wafers_give_two_routes(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-W1", wafer_no=1)
        _lot(connection, "LH-W2", wafer_no=2)
        _alarm(connection, "trace_alarm_history", "TA-01", wafer_no=1)
        _alarm(connection, "trace_alarm_history", "TA-02", wafer_no=2)

    snapshot = _fetch(db, _ref(TRACE, "TA-01"), _ref(TRACE, "TA-02"))
    assert {s.wafer_id for s in snapshot.steps} == {"LOT001W001", "LOT001W002"}


# --- route 범위 -------------------------------------------------------------


def test_the_route_follows_the_wafer_beyond_the_incident_chamber(db: Any) -> None:
    """route는 **WAFER의 경로**다. incident chamber에서 멈추지 않는다."""

    with db.begin() as connection:
        _lot(connection, "LH-PHOTO", chamber_id=PHOTO, track_in_at=T0)
        _lot(
            connection,
            "LH-ETCH",
            chamber_id=ETCH,
            step_id="CT-ETCH",
            equipment_id="EQP04",
            track_in_at=T1,
        )
        _alarm(connection, "trace_alarm_history", "TA-01", chamber_id=PHOTO)

    snapshot = _fetch(db, _ref(TRACE, "TA-01"))
    assert [s.chamber_id for s in snapshot.steps] == [PHOTO, ETCH] or [
        s.chamber_id for s in snapshot.steps
    ] == [ETCH, PHOTO]
    assert {s.lot_hist_id for s in snapshot.steps} == {"LH-PHOTO", "LH-ETCH"}


def test_another_lot_with_the_same_wafer_number_is_excluded(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-W1")
        _lot(connection, "LH-OTHER", lot_id="LOT002")
        _alarm(connection, "trace_alarm_history", "TA-01")

    snapshot = _fetch(db, _ref(TRACE, "TA-01"))
    assert {s.lot_id for s in snapshot.steps} == {LOT}
    assert "LH-OTHER" not in {s.lot_hist_id for s in snapshot.steps}


def test_two_lots_sharing_a_wafer_id_do_not_merge(db: Any) -> None:
    """**`lot_id` 조건이 실제로 일한다.**

    `wafer_id`가 보통 `LOT001W001`처럼 LOT을 담고 있어 두 LOT이 같은 `wafer_id`를 갖는
    fixture가 없으면 route join에서 `lot_id`를 지워도 통과한다. `lot_history.wafer_id`에
    형식·유일성 제약이 없으므로 이 상태가 schema상 가능하다.
    """

    with db.begin() as connection:
        _lot(connection, "LH-MINE", wafer_id="W001")
        _lot(connection, "LH-THEIRS", lot_id="LOT002", wafer_id="W001")
        connection.execute(
            text(
                "INSERT INTO trace_alarm_history "
                " (alarm_id, occurred_at, chamber, lot, wafer, step_no) "
                "VALUES ('TA-01', :t, :c, :l, 'W001', 1)"
            ),
            {"t": T0, "c": PHOTO, "l": LOT},
        )

    snapshot = _fetch(db, _ref(TRACE, "TA-01"))
    assert [s.lot_hist_id for s in snapshot.steps] == ["LH-MINE"]


def test_step_order_is_stable_regardless_of_insert_order(db: Any) -> None:
    with db.begin() as connection:
        # 같은 (lot, wafer, chamber)가 두 행이면 View가 fan-out한다. chamber를 나눈다.
        _lot(
            connection,
            "LH-C",
            chamber_id="EQP05-PM1",
            step_id="CT-CLEAN",
            track_in_at=T2,
        )
        _lot(connection, "LH-B", chamber_id=ETCH, step_id="CT-ETCH", track_in_at=T1)
        _lot(connection, "LH-A", track_in_at=T0)
        _alarm(connection, "trace_alarm_history", "TA-01")

    snapshot = _fetch(db, _ref(TRACE, "TA-01"))
    ordered = sorted(snapshot.steps, key=lambda s: (s.track_in_at, s.lot_hist_id))
    assert [s.lot_hist_id for s in ordered] == ["LH-A", "LH-B", "LH-C"]


# --- fail-closed ------------------------------------------------------------


def test_a_member_that_no_longer_exists_is_not_found(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-W1")
        _alarm(connection, "trace_alarm_history", "TA-01")

    with pytest.raises(repo.RepositoryNotFound) as exc:
        _fetch(db, _ref(TRACE, "TA-01"), _ref(TRACE, "GONE"))
    assert exc.value.code == "ROUTE_MEMBER_NOT_FOUND"


def test_a_lot_history_fan_out_is_refused(db: Any) -> None:
    """`(lot_id, wafer_id, chamber_id)` unique 제약이 없어 View가 fan-out할 수 있다."""

    with db.begin() as connection:
        _lot(connection, "LH-W1")
        _lot(connection, "LH-DUP")  # 같은 lot·wafer·chamber
        _alarm(connection, "trace_alarm_history", "TA-01")

    with pytest.raises(repo.RepositoryContractError) as exc:
        _fetch(db, _ref(TRACE, "TA-01"))
    assert exc.value.code == "ROUTE_MEMBER_DUPLICATE"


def test_an_unresolved_member_owner_is_refused(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-W1")
        _alarm(connection, "trace_alarm_history", "TA-01")
        # lot_history가 없는 WAFER의 알람 — View LEFT JOIN이라 행은 남는다.
        _alarm(connection, "trace_alarm_history", "TA-ORPHAN", wafer_no=9)

    with pytest.raises(repo.RepositoryContractError) as exc:
        _fetch(db, _ref(TRACE, "TA-01"), _ref(TRACE, "TA-ORPHAN"))
    assert exc.value.code == "ROUTE_MEMBER_OWNER_UNRESOLVED"


def test_an_owner_outside_the_incident_is_refused(db: Any) -> None:
    """**정상 C-1.1 경로로는 도달하지 않는다.**

    C-1.1이 이미 incident 밖 member를 만들지 않으므로, 이 guard가 지키는 것은 (1) 두
    read 사이 데이터 변경과 (2) caller가 손으로 만든 incident다. 여기서는 2번을
    재현한다 — incident key를 다른 chamber로 지정해 직접 호출한다.
    """

    with db.begin() as connection:
        _lot(connection, "LH-W1", chamber_id=PHOTO)
        _alarm(connection, "trace_alarm_history", "TA-01", chamber_id=PHOTO)

    with pytest.raises(repo.RepositoryContractError) as exc:
        _fetch(db, _ref(TRACE, "TA-01"), chamber_id=ETCH)
    assert exc.value.code == "ROUTE_INCIDENT_MISMATCH"


def test_a_missing_wafer_id_is_refused(db: Any) -> None:
    """**R03로만 재현된다.**

    `lot_history.wafer_id`는 nullable이지만, TRACE·SUMMARY는 View join 조건이
    `h.wafer_id = a.wafer`라 owner가 매칭된 순간 `wafer_id`가 NULL일 수 없다. R03는
    `h.lot_hist_id = a.lot_hist_id`로만 붙으므로 owner는 resolve되고 `wafer_id`만 비는
    상태가 가능하다.
    """

    with db.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO lot_history "
                " (lot_hist_id, lot_id, wafer_no, wafer_id, chamber_id, step_id,"
                "  equipment_id, area_id, recipe_id, track_in_at) "
                "VALUES ('LH-NOWAFER', :l, 1, NULL, :c, 'CT-PHOTO', 'EQP01',"
                "        'etch', 'RECIPE01', :t)"
            ),
            {"l": LOT, "c": PHOTO, "t": T0},
        )
        connection.execute(
            text(
                "INSERT INTO r03_alarm_history "
                " (alarm_id, occurred_at, lot_hist_id, lot_id, equipment_id,"
                "  chamber_id, parameter_id, recipe_step_no, trigger_wafer_no,"
                "  member_wafer_refs, member_alarm_refs, policy_version) "
                "VALUES (:a, :t, 'LH-NOWAFER', :l, 'EQP01', :c, 'PARAM01', 1, 1,"
                "        '[1,2,3]'::jsonb, '[]'::jsonb, 'R03_CONSEC_V1')"
            ),
            {"a": "R03-" + "1".rjust(20, "0"), "t": T0, "l": LOT, "c": PHOTO},
        )

    with pytest.raises(repo.RepositoryContractError) as exc:
        _fetch(db, _ref(AlarmSource.R03, "R03-" + "1".rjust(20, "0")))
    assert exc.value.code == "ROUTE_WAFER_ID_MISSING"


def test_an_owner_without_a_chamber_is_refused(db: Any) -> None:
    """**canonical key의 두 field를 모두 본다**(구현리뷰 PR #155 필수 2).

    `lot_history.chamber_id`는 nullable이다(`001_base_schema.sql` 기준 `lot_id`만
    NOT NULL). owner 결손을 `owner_lot_id IS NULL`로만 판정하면 "lot은 있는데 chamber가
    없는" owner가 다섯 검사를 전부 빠져나간다 — drift 검사도 `<>` 비교가
    `FALSE OR NULL` = `NULL`이라 `FILTER`가 세지 않는다. 그 member는 mapping까지 들어간
    뒤 `_step()`의 NULL 검사에 걸려 `WAFER_ROUTE_INCOMPLETE`로 끝난다. **fail-closed는
    유지되지만 reason이 원인을 가리키지 않는다.**

    `test_a_missing_wafer_id_is_refused`와 같은 이유로 **R03로만 재현된다.**
    TRACE·SUMMARY는 View join 조건이 `h.chamber_id = a.chamber`라 owner가 매칭된 순간
    `chamber_id`가 NULL일 수 없다.

    raw는 incident와 같은 chamber를 가리킨다(`r03_alarm_history.chamber_id = PHOTO`).
    즉 "raw만 보면 정상"이고 canonical만 비어 있는 상태다.
    """

    alarm_id = "R03-" + "2".rjust(20, "0")
    with db.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO lot_history "
                " (lot_hist_id, lot_id, wafer_no, wafer_id, chamber_id, step_id,"
                "  equipment_id, area_id, recipe_id, track_in_at) "
                "VALUES ('LH-NOCHAMBER', :l, 1, :w, NULL, 'CT-PHOTO', 'EQP01',"
                "        'etch', 'RECIPE01', :t)"
            ),
            {"l": LOT, "w": f"{LOT}W001", "t": T0},
        )
        connection.execute(
            text(
                "INSERT INTO r03_alarm_history "
                " (alarm_id, occurred_at, lot_hist_id, lot_id, equipment_id,"
                "  chamber_id, parameter_id, recipe_step_no, trigger_wafer_no,"
                "  member_wafer_refs, member_alarm_refs, policy_version) "
                "VALUES (:a, :t, 'LH-NOCHAMBER', :l, 'EQP01', :c, 'PARAM01', 1, 1,"
                "        '[1,2,3]'::jsonb, '[]'::jsonb, 'R03_CONSEC_V1')"
            ),
            {"a": alarm_id, "t": T0, "l": LOT, "c": PHOTO},
        )

    with pytest.raises(repo.RepositoryContractError) as exc:
        _fetch(db, _ref(AlarmSource.R03, alarm_id))
    assert exc.value.code == "ROUTE_MEMBER_OWNER_UNRESOLVED"


def test_an_owner_without_a_chamber_is_not_read_as_drift(db: Any) -> None:
    """chamber가 **없는 것**과 chamber가 **다른 것**은 다른 사실이다.

    `NULL`을 "다른 chamber"로 세면 `ROUTE_INCIDENT_MISMATCH`가 나온다. 그건 "두 read
    사이에 데이터가 바뀌었다"는 뜻이라 대응이 달라진다. 결손은 결손으로 보고해야 한다.
    """

    alarm_id = "R03-" + "3".rjust(20, "0")
    with db.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO lot_history "
                " (lot_hist_id, lot_id, wafer_no, wafer_id, chamber_id, step_id,"
                "  equipment_id, area_id, recipe_id, track_in_at) "
                "VALUES ('LH-NOCHAMBER2', :l, 1, :w, NULL, 'CT-PHOTO', 'EQP01',"
                "        'etch', 'RECIPE01', :t)"
            ),
            {"l": LOT, "w": f"{LOT}W001", "t": T0},
        )
        connection.execute(
            text(
                "INSERT INTO r03_alarm_history "
                " (alarm_id, occurred_at, lot_hist_id, lot_id, equipment_id,"
                "  chamber_id, parameter_id, recipe_step_no, trigger_wafer_no,"
                "  member_wafer_refs, member_alarm_refs, policy_version) "
                "VALUES (:a, :t, 'LH-NOCHAMBER2', :l, 'EQP01', :c, 'PARAM01', 1, 1,"
                "        '[1,2,3]'::jsonb, '[]'::jsonb, 'R03_CONSEC_V1')"
            ),
            {"a": alarm_id, "t": T0, "l": LOT, "c": PHOTO},
        )

    with pytest.raises(repo.RepositoryContractError) as exc:
        _fetch(db, _ref(AlarmSource.R03, alarm_id))
    assert exc.value.code != "ROUTE_INCIDENT_MISMATCH"
    assert exc.value.code != "WAFER_ROUTE_INCOMPLETE"


def test_a_null_track_in_at_is_not_corrected(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-W1", track_in_at=T0)
        _lot(
            connection,
            "LH-NOTIME",
            chamber_id=ETCH,
            step_id="CT-ETCH",
            track_in_at=None,
        )
        _alarm(connection, "trace_alarm_history", "TA-01")

    with pytest.raises(repo.RepositoryContractError) as exc:
        _fetch(db, _ref(TRACE, "TA-01"))
    assert exc.value.code == "WAFER_ROUTE_INCOMPLETE"


# --- 읽기 전용 --------------------------------------------------------------


def test_reading_a_route_writes_nothing(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-W1")
        _alarm(connection, "trace_alarm_history", "TA-01")

    def _counts() -> dict[str, int]:
        with db.connect() as connection:
            return {
                table: connection.execute(
                    text(f"SELECT count(*) FROM {table}")
                ).scalar_one()
                for table in _TABLES
            }

    before = _counts()
    _fetch(db, _ref(TRACE, "TA-01"))
    assert _counts() == before


def test_no_fault_code_reaches_the_route_step(db: Any) -> None:
    """`lot_history.fault_code`는 `NRM` placeholder이고 판단 입력이 아니다."""

    with db.begin() as connection:
        _lot(connection, "LH-W1")
        connection.execute(
            text(
                "UPDATE lot_history SET fault_code = 'FOC' WHERE lot_hist_id = 'LH-W1'"
            )
        )
        _alarm(connection, "trace_alarm_history", "TA-01")

    snapshot = _fetch(db, _ref(TRACE, "TA-01"))
    assert "fault_code" not in rt_repo.RouteStep.__dataclass_fields__
    assert "FOC" not in str(snapshot.steps)


def test_the_same_snapshot_repeats(db: Any) -> None:
    with db.begin() as connection:
        _lot(connection, "LH-A", track_in_at=T0)
        _lot(connection, "LH-B", chamber_id=ETCH, step_id="CT-ETCH", track_in_at=T1)
        _alarm(connection, "trace_alarm_history", "TA-01")

    assert _fetch(db, _ref(TRACE, "TA-01")) == _fetch(db, _ref(TRACE, "TA-01"))


def test_the_db_stage_entry_point_works_on_real_sql(db: Any) -> None:
    """`read_route_snapshot()`이 C-1.1 결과를 그대로 받는다.

    graph 단계와 나뉜 뒤에도 DB 단계가 incident key 결속을 유지하는지 실제 SQL로 본다.
    """

    with db.begin() as connection:
        _lot(connection, "LH-PHOTO", chamber_id=PHOTO, track_in_at=T0)
        _lot(
            connection,
            "LH-ETCH",
            chamber_id=ETCH,
            step_id="CT-ETCH",
            equipment_id="EQP04",
            track_in_at=T1,
        )
        _alarm(connection, "trace_alarm_history", "TA-01", chamber_id=PHOTO)

    requested = _ref(TRACE, "TA-01")
    incident = ResolvedIncident(
        lot_id=LOT,
        chamber_id=PHOTO,
        requested_alarm=requested,
        representative_alarm=requested,
        member_alarms=(requested,),
    )
    with db.connect() as connection:
        bound = rt.read_route_snapshot(connection, incident)

    # **envelope가 incident와 snapshot을 함께 들고 온다.**
    assert bound.incident is incident
    assert dict(bound.snapshot.wafer_of_member) == {(TRACE, "TA-01"): "LOT001W001"}
    assert {s.lot_hist_id for s in bound.snapshot.steps} == {"LH-PHOTO", "LH-ETCH"}
    assert (bound.snapshot.lot_id, bound.snapshot.chamber_id) == (LOT, PHOTO)
    assert bound.snapshot.member_keys == ((TRACE, "TA-01"),)
