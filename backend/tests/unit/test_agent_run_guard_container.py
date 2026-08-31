"""`V5-C-1.3` 중복 실행 방지 — 격리 PostgreSQL 16 회귀.

## 왜 실제 DB여야 하나

이 Task가 막는 것은 **동시성**이다. 단위 fake로는 다음을 재현할 수 없다.

- `pg_advisory_xact_lock`이 두 transaction을 실제로 직렬화하는가
- lock이 commit·rollback에서 자동 해제되는가
- `ux_agent_run_incident_active` partial unique가 마지막 방어로 발화하는가
- 기본 격리 수준이 `READ COMMITTED`인가, 그리고 `REPEATABLE READ`에서 경로가
  실제로 갈리는가

순차 2회 호출이나 `sleep` 기반 판정으로 대체하지 않는다. 별도 connection·별도
thread를 쓰고, 진행 시점은 `threading.Event`로 **결정론적으로** 맞춘다.
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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
from app.agent import run_guard as guard  # noqa: E402
from app.agent import run_guard_repository as guard_repo  # noqa: E402
from app.common.enums import AlarmSource, RunStatus  # noqa: E402
from app.common.exceptions import (  # noqa: E402
    IncidentAlreadyProcessedError,
    IncidentAlreadyRunningError,
)
from app.common.schemas import AlarmRef  # noqa: E402

pytestmark = pytest.mark.container

#: `002`가 `current_database()`를 검사한다 — runtime DB 이름만 받는다.
TARGET_DATABASE = "kosa_agent_e2e"
FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"
V5_SQL = (
    REPOSITORY_ROOT
    / "backend"
    / "migrations"
    / "v5"
    / "001_reference_extensions_final.sql"
)
SQL_002 = REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
SQL_003 = REPOSITORY_ROOT / "backend" / "migrations" / "003_agent_run_severity_pair.sql"

TRACE = AlarmSource.TRACE
LOT = "LOT001"
CHAMBER = "EQP01-PM1"
OTHER_CHAMBER = "EQP04-PM2"
T0 = datetime(2026, 8, 1, 10, 0, 0)

_WAFER_ALTER = "\n".join(
    f"ALTER TABLE {table} ALTER COLUMN wafer TYPE varchar(24) "
    f"USING wafer::varchar(24);"
    for table in transition.WAFER_ALTER_TABLES
)

_TABLES = (
    "agent_run_alarm",
    "agent_run",
    "audit_log",
    "trace_alarm_history",
    "lot_history",
)


@pytest.fixture(scope="module")
def runtime_engine() -> Any:
    """base 9 + `v5/001` + `002` + `003`을 한 컨테이너에 세운다.

    이 Task는 **두 계층을 동시에** 요구한다 — incident 해석은 `v_alarm_event`(base 9 +
    v5/001)를, run 생성은 `agent_run`(002/003)을 쓴다.
    """

    with postgres.one_off_postgres(database=TARGET_DATABASE) as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=TARGET_DATABASE,
            user=endpoint.username,
            password=endpoint.password,
        ) as raw:
            cursor = raw.cursor()
            cursor.execute(
                (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
            )
            cursor.execute(_WAFER_ALTER)
            cursor.execute(V5_SQL.read_text(encoding="utf-8"))
            cursor.execute(SQL_002.read_text(encoding="utf-8"))
            cursor.execute(SQL_003.read_text(encoding="utf-8"))
            raw.commit()
        engine = create_engine(
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{TARGET_DATABASE}",
            # **동시성 회귀가 실제로 두 연결을 쓴다.** 기본 pool로는 두 번째 thread가
            # 첫 연결을 기다려 lock 경합이 아니라 pool 경합을 측정하게 된다.
            pool_size=5,
            max_overflow=5,
            # **lock 대기를 유한하게 만든다.**
            #
            # session lock(`pg_advisory_lock`)으로 바꾸는 변이는 pool로 반납된 연결에
            # lock을 남기고, 그러면 다음 회귀가 red가 아니라 **영원히 멈춘다.** CI에서
            # 그것은 실패보다 나쁘다 — 원인이 드러나지 않고 job이 죽는다.
            connect_args={"options": "-c lock_timeout=5000"},
        )
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
def db(runtime_engine: Any) -> Any:
    with runtime_engine.begin() as connection:
        for table in _TABLES:
            connection.execute(text(f"TRUNCATE {table} CASCADE"))
        connection.execute(
            text(
                "INSERT INTO dim_parameter (parameter_id, parameter_name, area) "
                "VALUES ('PARAM01', 'p', 'etch') ON CONFLICT DO NOTHING"
            )
        )
        _seed_incident(connection)
    return runtime_engine


def _ref(alarm_id: str) -> AlarmRef:
    return AlarmRef(source=TRACE, alarm_id=alarm_id)


def _seed_incident(connection: Any) -> None:
    """`(LOT001, EQP01-PM1)` incident 하나와 다른 chamber 대조군 하나."""

    for hist, chamber, alarm in (
        ("LH-1", CHAMBER, "TA-01"),
        ("LH-2", OTHER_CHAMBER, "TA-02"),
    ):
        connection.execute(
            text(
                "INSERT INTO lot_history "
                " (lot_hist_id, lot_id, wafer_no, wafer_id, chamber_id, step_id,"
                "  area_id, equipment_id, recipe_id, track_in_at) "
                "VALUES (:h, :l, 1, :w, :c, 'CT-PHOTO', 'etch', 'EQP01',"
                "        'RECIPE01', :t)"
            ),
            {"h": hist, "l": LOT, "w": f"{LOT}W001", "c": chamber, "t": T0},
        )
        connection.execute(
            text(
                "INSERT INTO trace_alarm_history "
                " (alarm_id, occurred_at, area, equipment, chamber, parameter,"
                "  recipe, lot, wafer, step_no) "
                "VALUES (:a, :t, 'etch', 'EQP01', :c, 'PARAM01', 'RECIPE01',"
                "        :l, :w, 1)"
            ),
            {"a": alarm, "t": T0, "c": chamber, "l": LOT, "w": f"{LOT}W001"},
        )


def _start(db: Any, alarm_id: str = "TA-01", **over: Any) -> Any:
    over.setdefault("llm_model", "test-model")
    with db.begin() as connection:
        return guard.start_incident_run(
            connection, _ref(alarm_id), autonomy_level=2, **over
        )


def _counts(db: Any) -> dict[str, int]:
    with db.connect() as connection:
        return {
            name: connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()
            for name in ("agent_run", "agent_run_alarm", "audit_log")
        }


def _set_status(db: Any, run_id: str, status: RunStatus) -> None:
    with db.begin() as connection:
        connection.execute(
            text("UPDATE agent_run SET status = :s WHERE agent_run_id = :r"),
            {"s": status.value, "r": run_id},
        )


# ===========================================================================
# 격리 수준 — 알고리즘의 전제를 실측한다
# ===========================================================================


def test_the_default_isolation_level_is_read_committed(db: Any) -> None:
    """**전제를 문서에만 두지 않는다.**

    `READ COMMITTED`에서만 "lock 뒤 재조회가 앞선 caller의 commit을 본다"가 참이다.
    engine이 `isolation_level`을 지정하지 않으므로 이 값은 PostgreSQL 기본값이고,
    바뀌면 아래 동시성 회귀의 경로가 달라진다.
    """

    with db.connect() as connection:
        level = connection.execute(text("SHOW transaction_isolation")).scalar_one()
    assert level == "read committed"


def test_the_guard_never_sets_an_isolation_level(db: Any) -> None:
    """caller가 정한 수준을 이 계층이 바꾸지 않는다."""

    with db.begin() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        guard.lock_incident(connection, lot_id=LOT, chamber_id=CHAMBER)
        level = connection.execute(text("SHOW transaction_isolation")).scalar_one()
    assert level == "repeatable read"


# ===========================================================================
# 상태 정책 — 실제 table에서
# ===========================================================================


def test_the_first_request_creates_one_run(db: Any) -> None:
    started = _start(db)

    assert started.run.lot_id == LOT
    assert started.run.chamber_id == CHAMBER
    assert started.run.retry_of_run_id is None
    assert _counts(db) == {"agent_run": 1, "agent_run_alarm": 1, "audit_log": 1}


@pytest.mark.parametrize("status", [RunStatus.RUNNING, RunStatus.WAITING_APPROVAL])
def test_an_active_run_blocks_a_second_request(db: Any, status: RunStatus) -> None:
    first = _start(db)
    _set_status(db, first.run.agent_run_id, status)

    with pytest.raises(IncidentAlreadyRunningError):
        _start(db)
    assert _counts(db)["agent_run"] == 1


def test_a_completed_incident_is_not_reselected(db: Any) -> None:
    first = _start(db)
    _set_status(db, first.run.agent_run_id, RunStatus.COMPLETED)

    with pytest.raises(IncidentAlreadyProcessedError):
        _start(db)
    assert _counts(db)["agent_run"] == 1


def test_a_failed_run_allows_a_retry_pointing_at_it(db: Any) -> None:
    first = _start(db)
    _set_status(db, first.run.agent_run_id, RunStatus.FAILED)

    second = _start(db)
    assert second.run.retry_of_run_id == first.run.agent_run_id
    assert _counts(db)["agent_run"] == 2


def test_a_retry_chain_points_at_the_latest_failure(db: Any) -> None:
    """**root로 평탄화하지 않는다.**"""

    first = _start(db)
    _set_status(db, first.run.agent_run_id, RunStatus.FAILED)
    second = _start(db)
    _set_status(db, second.run.agent_run_id, RunStatus.FAILED)

    third = _start(db)
    assert third.run.retry_of_run_id == second.run.agent_run_id


def test_a_refused_request_leaves_no_trace(db: Any) -> None:
    """거부는 run·member·감사 어느 것도 남기지 않는다."""

    _start(db)
    before = _counts(db)
    with pytest.raises(IncidentAlreadyRunningError):
        _start(db)
    assert _counts(db) == before
    assert before["agent_run"] == 1


def test_a_different_chamber_is_a_different_incident(db: Any) -> None:
    """advisory key가 두 ID를 구분한다 — 다른 incident를 막지 않는다."""

    _start(db, "TA-01")
    other = _start(db, "TA-02")

    assert other.run.chamber_id == OTHER_CHAMBER
    assert _counts(db)["agent_run"] == 2


# ===========================================================================
# 동시성 — 이 Task의 본체
# ===========================================================================


def _concurrent_pair(db: Any) -> tuple[list[object], int]:
    """두 caller를 동시에 출발시키고 결과와 **INSERT 진입 횟수**를 함께 준다.

    진입 횟수가 핵심이다. 결과만 보면 "run 1건 · 거부 1건"은 두 경로 어느 쪽으로도
    만들어진다 — advisory lock이 직렬화했든, lock 없이 둘 다 INSERT로 가서 partial
    unique가 하나를 쳐냈든. 그래서 **lock을 지워도 회귀가 green이었다.**
    """

    entries = 0
    guard_lock = threading.Lock()
    real_create = guard.create_agent_run

    def _counting_create(connection: Any, command: Any, **kwargs: Any) -> Any:
        nonlocal entries
        with guard_lock:
            entries += 1
        return real_create(connection, command, **kwargs)

    ready = threading.Barrier(2, timeout=30)

    def _run_one() -> object:
        ready.wait()
        try:
            return _start(db)
        except IncidentAlreadyRunningError as exc:
            return exc

    guard.create_agent_run = _counting_create  # type: ignore[assignment]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                f.result(timeout=60) for f in [pool.submit(_run_one) for _ in range(2)]
            ]
    finally:
        guard.create_agent_run = real_create  # type: ignore[assignment]
    return results, entries


def test_two_concurrent_callers_create_exactly_one_run(db: Any) -> None:
    """**별도 connection·별도 thread.** 순차 2회로 대체하지 않는다."""

    results, _ = _concurrent_pair(db)

    succeeded = [r for r in results if isinstance(r, guard.StartedIncidentRun)]
    refused = [r for r in results if isinstance(r, IncidentAlreadyRunningError)]
    assert len(succeeded) == 1
    assert len(refused) == 1
    assert _counts(db) == {"agent_run": 1, "agent_run_alarm": 1, "audit_log": 1}


def test_the_second_caller_never_reaches_the_insert(db: Any) -> None:
    """**advisory lock이 실제로 직렬화한다는 증거.**

    두 C-1.3 caller 사이에서는 두 번째가 lock을 얻은 뒤 재조회에서 첫 caller의
    `RUNNING`을 보고 history 정책에서 멈춘다. 즉 `create_agent_run()` 진입은
    **정확히 1회**다.

    lock을 지우면 둘 다 history 0건을 보고 INSERT로 가므로 진입이 2회가 되고, 결과는
    partial unique가 하나를 쳐내 겉보기에 같아진다. 그 차이를 여기서 고정한다 —
    "막혔다"가 아니라 "무엇이 막았는가"를 본다.
    """

    _, entries = _concurrent_pair(db)
    assert entries == 1


def test_the_lock_is_released_when_a_transaction_rolls_back(db: Any) -> None:
    """transaction lock은 rollback에서도 풀린다 — session lock이면 남는다."""

    with pytest.raises(RuntimeError):
        with db.begin() as connection:
            guard.lock_incident(connection, lot_id=LOT, chamber_id=CHAMBER)
            raise RuntimeError("의도된 rollback")

    # 같은 key를 즉시 다시 얻을 수 있어야 한다. 남아 있으면 아래가 멈춘다.
    with db.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '3s'"))
        guard.lock_incident(connection, lot_id=LOT, chamber_id=CHAMBER)


def test_the_partial_unique_fallback_actually_fires(db: Any) -> None:
    """**마지막 방어를 진짜로 발화시킨다.**

    두 C-1.3 caller 사이에서는 advisory lock이 직렬화하므로 이 경로에 닿지 않는다.
    그래서 advisory lock을 쓰지 않는 writer를 만든다 — T1이 history 0건을 본 뒤
    INSERT에 들어가기 직전에 멈추고, T2가 Repository를 직접 호출·commit한다.

    T1은 이미 "history 0건"을 본 상태이므로 정책을 통과하고 INSERT까지 가서 실제
    partial unique를 위반한다. 그 결과가 `IncidentAlreadyRunningError`로 정규화되는지
    본다. 증명을 위해 public `details`나 새 오류 code를 추가하지 않는다.
    """

    paused = threading.Event()
    proceed = threading.Event()
    real_create = guard.create_agent_run

    def _pausing_create(connection: Any, command: Any, **kwargs: Any) -> Any:
        paused.set()
        assert proceed.wait(timeout=30), "T2가 끝나지 않았습니다"
        return real_create(connection, command, **kwargs)

    def _direct_writer() -> None:
        """advisory lock을 **쓰지 않고** Repository를 직접 호출한다."""

        assert paused.wait(timeout=30), "T1이 멈추지 않았습니다"
        with db.begin() as connection:
            repo.create_agent_run(
                connection,
                repo.CreateAgentRunCommand(
                    thread_id=guard.new_thread_id(),
                    lot_id=LOT,
                    chamber_id=CHAMBER,
                    autonomy_level=2,
                    requested_alarm=_ref("TA-01"),
                    representative_alarm=_ref("TA-01"),
                    member_alarms=(_ref("TA-01"),),
                    llm_model="test-model",
                ),
            )
        proceed.set()

    guard.create_agent_run = _pausing_create  # type: ignore[assignment]
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(_direct_writer)
            with pytest.raises(IncidentAlreadyRunningError):
                _start(db)
            writer.result(timeout=60)
    finally:
        guard.create_agent_run = real_create  # type: ignore[assignment]

    # T1은 rollback됐고 T2의 run 하나만 남는다.
    assert _counts(db)["agent_run"] == 1


def test_repeatable_read_blocks_through_the_other_defense(db: Any) -> None:
    """**강한 격리에서는 막는 주체가 달라진다. caller가 보는 결과는 같다.**

    `REPEATABLE READ`에서는 transaction 첫 statement가 snapshot을 고정하므로, lock을
    얻은 뒤 재조회해도 앞서 commit된 `RUNNING`이 보이지 않는다. 정책은 history 0건으로
    통과하고 INSERT까지 간다. 중복 run은 여전히 막히되 **막는 주체가 달라진다** —
    history 정책이 아니라 partial unique index다.

    **결과 예외는 하나로 좁힌다.** 초판은 `IncidentAlreadyRunningError`와
    `RepositoryRetryable`을 모두 받아, 경로가 바뀌어도 red가 되지 않았다(구현리뷰
    PR #159 권고 3). 실측 3회 모두 `IncidentAlreadyRunningError`였다 — PostgreSQL은 이미
    commit된 unique key에 INSERT하면 `23505`를 내고, C-0.1이 그것을
    `RepositoryConflict("ACTIVE_RUN_EXISTS")`로 옮기며, 이 계층이 도메인 예외로
    정규화한다. `40001` serialization failure는 이 형상에서 나지 않는다 — 두
    transaction이 **같은 행을 갱신하지 않고** 새 행을 넣기 때문이다.

    이 회귀가 없으면 caller가 나중에 격리 수준을 올릴 때 아무도 이 알고리즘을 다시
    보지 않는다.
    """

    with db.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        # snapshot을 여기서 고정한다 — 아래 외부 commit보다 앞선다.
        connection.execute(text("SELECT count(*) FROM agent_run")).scalar_one()

        _start(db)  # 다른 connection에서 run 하나를 만들고 commit

        with pytest.raises(IncidentAlreadyRunningError):
            guard.start_incident_run(
                connection,
                _ref("TA-01"),
                autonomy_level=2,
                llm_model="test-model",
            )
        transaction.rollback()

    assert _counts(db)["agent_run"] == 1


# ===========================================================================
# Repository 경계
# ===========================================================================


def test_the_history_read_returns_only_the_policy_fields(db: Any) -> None:
    started = _start(db)
    with db.begin() as connection:
        rows = guard_repo.read_incident_runs(connection, lot_id=LOT, chamber_id=CHAMBER)

    assert len(rows) == 1
    assert rows[0].agent_run_id == started.run.agent_run_id
    assert rows[0].status is RunStatus.RUNNING
    assert set(guard_repo.IncidentRunRow.__slots__) == {
        "agent_run_id",
        "status",
        "started_at",
    }


def test_the_history_read_is_scoped_to_the_incident(db: Any) -> None:
    _start(db, "TA-01")
    _start(db, "TA-02")

    with db.begin() as connection:
        rows = guard_repo.read_incident_runs(connection, lot_id=LOT, chamber_id=CHAMBER)
    assert len(rows) == 1


def test_reading_and_locking_write_nothing(db: Any) -> None:
    before = _counts(db)
    with db.begin() as connection:
        guard.lock_incident(connection, lot_id=LOT, chamber_id=CHAMBER)
        guard_repo.read_incident_runs(connection, lot_id=LOT, chamber_id=CHAMBER)
    assert _counts(db) == before


@pytest.mark.parametrize("name", ["lock_incident", "read_incident_runs"])
def test_the_repository_refuses_autocommit(db: Any, name: str) -> None:
    """transaction 밖에서는 advisory lock도 `FOR UPDATE`도 뜻이 없다."""

    with db.connect() as connection:
        with pytest.raises(repo.RepositoryContractError) as exc:
            getattr(guard_repo, name)(connection, lot_id=LOT, chamber_id=CHAMBER)
    assert exc.value.code == "NO_ACTIVE_TRANSACTION"
