"""`V5-C-0.2` thread·checkpoint 격리 PostgreSQL 16 회귀.

## 왜 실제 DB여야 하나

이 Task가 증명하려는 것은 "interrupt 뒤 **프로세스가 끝나도** 같은 thread로 재개된다"
이다. 같은 connection·같은 saver·같은 graph instance를 재사용하면 in-memory 상태가
답할 수 있어 증명이 되지 않는다. 그래서 매 재개마다 UoW를 새로 만든다.

**direct connection과 pool 양쪽을 같은 흐름으로 돈다.** pool은 "연결 반납"이 아니라
**pool 전체 종료** 뒤 새 pool로 재개한다 — 생산 조립이 pool을 쓸 때 깨지는 자리가
거기이기 때문이다(구현리뷰 1차 필수 1).

## fixture의 `setup()`은 runtime 허용이 아니다

여기서 부르는 `PostgresSaver.setup()`은 **빈 컨테이너에 schema를 세우는 fixture**다.
생산 경로가 부르지 않는다는 것은 `test_agent_checkpoint.py`의 정적 회귀가 고정한다.
"""

from __future__ import annotations

import contextlib
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TypedDict

import psycopg
import psycopg_pool
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
for candidate in (BACKEND_ROOT, BACKEND_ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import rehearsal_postgres as postgres  # noqa: E402
from langgraph.checkpoint.postgres import PostgresSaver  # noqa: E402

from app.agent import checkpoint as ck  # noqa: E402
from app.agent import repository as repo  # noqa: E402
from app.common.enums import AlarmSource  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402
from tests.unit import checkpoint_state_guard as guard  # noqa: E402

pytestmark = pytest.mark.container

TARGET_DATABASE = "kosa_agent_e2e"
FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"
SQL_002 = REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
SQL_003 = REPOSITORY_ROOT / "backend" / "migrations" / "003_agent_run_severity_pair.sql"

#: fixture graph의 node 이름. 내부 채널 제외 목록을 여기서 파생한다.
NODE_NAMES = ("before_interrupt", "wait", "finish")


class FixtureState(TypedDict, total=False):
    agent_run_id: str
    thread_id: str
    pre_interrupt_count: int
    resume_value: str
    phase: str


def _before_interrupt(state: FixtureState) -> dict[str, Any]:
    return {"pre_interrupt_count": state.get("pre_interrupt_count", 0) + 1}


def _wait(state: FixtureState) -> dict[str, Any]:
    return {"resume_value": interrupt({"kind": "fixture_wait"})}


def _finish(state: FixtureState) -> dict[str, Any]:
    return {"phase": "COMPLETED"}


def _build_graph(saver: Any) -> Any:
    """테스트 파일 안에서만 만든다. 생산 `graph.py`는 건드리지 않는다."""

    builder = StateGraph(FixtureState)
    builder.add_node("before_interrupt", _before_interrupt)
    builder.add_node("wait", _wait)
    builder.add_node("finish", _finish)
    builder.add_edge(START, "before_interrupt")
    builder.add_edge("before_interrupt", "wait")
    builder.add_edge("wait", "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=saver)


def _action_history_ddl() -> str:
    body = (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
    start = body.index("CREATE TABLE action_history (")
    return body[start : body.index(");", start) + 2]


@pytest.fixture(scope="module")
def endpoint() -> Any:
    """001→002→003 + checkpoint schema를 세운 일회용 PostgreSQL 16."""

    with postgres.one_off_postgres(database=TARGET_DATABASE) as value:
        with psycopg.connect(
            host=value.host,
            port=value.port,
            dbname=TARGET_DATABASE,
            user=value.username,
            password=value.password,
            autocommit=True,
            row_factory=dict_row,
        ) as raw:
            cursor = raw.cursor()
            cursor.execute(_action_history_ddl())
            cursor.execute(SQL_002.read_text(encoding="utf-8"))
            cursor.execute(SQL_003.read_text(encoding="utf-8"))
            # **fixture setup이다.** 생산 경로의 setup 0건은 단위 회귀가 고정한다.
            PostgresSaver(raw).setup()
        yield value


def _dsn(endpoint: Any) -> str:
    return (
        f"postgresql://{endpoint.username}:{endpoint.password}"
        f"@{endpoint.host}:{endpoint.port}/{TARGET_DATABASE}"
    )


def _connect(endpoint: Any, *, autocommit: bool = True) -> Any:
    return psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=TARGET_DATABASE,
        user=endpoint.username,
        password=endpoint.password,
        autocommit=autocommit,
        prepare_threshold=0,
        row_factory=dict_row,
    )


@pytest.fixture
def clean(endpoint: Any) -> Any:
    with _connect(endpoint) as raw:
        cursor = raw.cursor()
        for table in (
            "checkpoint_writes",
            "checkpoint_blobs",
            "checkpoints",
            "agent_run_alarm",
            "agent_run",
            "audit_log",
        ):
            cursor.execute(f"TRUNCATE {table} CASCADE")
    return endpoint


# --- UoW factory — direct와 pool을 같은 흐름으로 돌린다 ----------------------


@contextlib.contextmanager
def _direct_unit(endpoint: Any) -> Iterator[Any]:
    """한 UoW = 새 connection. 끝나면 **닫는다.**"""

    connection = _connect(endpoint)
    try:
        yield ck.build_postgres_saver(connection)
    finally:
        connection.close()


@contextlib.contextmanager
def _pool_unit(endpoint: Any) -> Iterator[Any]:
    """한 UoW = 새 pool. 끝나면 **pool 전체를 닫는다.**

    연결 반납이 아니라 pool 종료다. 생산 조립이 재시작될 때 일어나는 일이 이것이고,
    checkpoint를 못 읽거나 처음부터 다시 실행되는 회귀가 여기서 드러난다.
    """

    pool = psycopg_pool.ConnectionPool(
        _dsn(endpoint),
        min_size=1,
        max_size=2,
        open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    try:
        saver = ck.build_postgres_saver(pool)
        # **선언이 아니라 관측이다.** 두 UoW 모두에서 확인한다.
        with pool.connection() as connection:
            assert connection.autocommit is True, "선언과 관측이 다르다"
        yield saver
    finally:
        pool.close()
        # **후위 조건이다.** 다음 UoW가 "새 pool로 재개"인 것을 코드 읽기가 아니라
        # 단언으로 보장한다.
        assert pool.closed is True, "pool이 실제로 닫히지 않았다"


UNITS = pytest.mark.parametrize(
    "unit", [_direct_unit, _pool_unit], ids=["direct", "pool"]
)


def _checkpoint_rows(endpoint: Any, thread_id: str) -> int:
    with _connect(endpoint) as raw:
        return (
            raw.cursor()
            .execute(
                "SELECT count(*) AS c FROM checkpoints WHERE thread_id = %s",
                (thread_id,),
            )
            .fetchone()["c"]
        )


def _create_run(endpoint: Any, thread_id: str) -> str:
    """**C-0.1 Repository를 통해** run을 만든다. 직접 SQL로 우회하지 않는다.

    그래야 `agent_run.thread_id`와 checkpoint의 결속이 실제 저장 경로로 증명된다.
    """

    from sqlalchemy import create_engine

    engine = create_engine(f"postgresql+psycopg://{_dsn(endpoint).split('://', 1)[1]}")
    try:
        alarm = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-C02")
        with engine.begin() as connection:
            run = repo.create_agent_run(
                connection,
                repo.CreateAgentRunCommand(
                    thread_id=thread_id,
                    lot_id="LOT-C02",
                    chamber_id="EQP01-PM-C02",
                    autonomy_level=2,
                    requested_alarm=alarm,
                    representative_alarm=alarm,
                    member_alarms=(alarm,),
                    llm_model="test-model",
                ),
            )
        return run.agent_run_id
    finally:
        engine.dispose()


def _stored_thread(endpoint: Any, run_id: str) -> str:
    from sqlalchemy import create_engine

    engine = create_engine(f"postgresql+psycopg://{_dsn(endpoint).split('://', 1)[1]}")
    try:
        with engine.connect() as connection:
            return repo.get_agent_run(connection, run_id).thread_id
    finally:
        engine.dispose()


# --- durability 계약 --------------------------------------------------------


def test_a_non_autocommit_connection_loses_the_checkpoint(clean: Any) -> None:
    """**계약이 취향이 아니라 사실임을 실제 DB로 고정한다.**

    생산 helper가 이 연결을 거부하는 이유가 여기 있다. 거부를 없애면 checkpoint가
    조용히 사라진다 — 어떤 단위 테스트도 그것을 red로 만들지 못한다.
    """

    thread = ck.new_thread_id()
    config = ck.build_thread_config(thread)
    connection = _connect(clean, autocommit=False)
    try:
        _build_graph(PostgresSaver(connection)).invoke(
            {"pre_interrupt_count": 0}, config=config
        )
    finally:
        connection.close()  # commit 없이 닫는다
    assert _checkpoint_rows(clean, thread) == 0

    # 생산 경로는 그 연결을 애초에 받지 않는다.
    other = _connect(clean, autocommit=False)
    try:
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(other)
        assert exc.value.reason_code == "CHECKPOINT_AUTOCOMMIT_REQUIRED"
    finally:
        other.close()


@UNITS
def test_a_declared_unit_persists_the_checkpoint(clean: Any, unit: Any) -> None:
    """양성 대조군. direct·pool 모두 UoW를 끝내도 행이 남는다."""

    thread = ck.new_thread_id()
    with unit(clean) as saver:
        _build_graph(saver).invoke(
            {"pre_interrupt_count": 0}, config=ck.build_thread_config(thread)
        )
    assert _checkpoint_rows(clean, thread) >= 1


def test_a_configure_override_is_refused_before_any_write(clean: Any) -> None:
    """**선언이 참인데 관측이 거짓인 pool을 거부한다**(구현리뷰 1차 필수 2).

    `psycopg_pool`은 checkout 직전에 `configure`를 부른다. 그래서
    `kwargs={"autocommit": True}`로 선언해도 실제 연결은 `False`일 수 있다. 선언만
    보는 guard는 이 pool을 통과시키고, checkpoint는 반납 시점에 사라진다.

    거부는 **graph write보다 앞**이어야 한다. 그래서 대상 thread의 행이 0이다.
    """

    def _flip(connection: Any) -> None:
        connection.autocommit = False

    pool = psycopg_pool.ConnectionPool(
        _dsn(clean),
        min_size=1,
        max_size=2,
        open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        configure=_flip,
    )
    thread = ck.new_thread_id()
    try:
        # 선언은 통과한다.
        assert pool.kwargs["autocommit"] is True
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(pool)
        assert exc.value.reason_code == "CHECKPOINT_POOL_NOT_DURABLE"
    finally:
        pool.close()

    assert _checkpoint_rows(clean, thread) == 0


def test_a_reset_override_is_refused_before_any_write(clean: Any) -> None:
    """**관측으로는 결정론이 되지 않아 거부한다**(구현리뷰 4차 필수 1).

    `_putconn()`이 reset을 worker task로 보내므로 `with pool.connection()` 블록의 끝은
    reset 완료 barrier가 아니다. 두 번 관측하는 방식은 이 파일 전체를 6회 돌렸을 때
    **2회 실패**했다 — 통과가 증거가 아니라 비결정성이 증거다.

    지금은 빌려 보기 전에 거부하므로 pool 크기·worker scheduling과 무관하다.
    """

    def _flip(connection: Any) -> None:
        connection.autocommit = False

    pool = psycopg_pool.ConnectionPool(
        _dsn(clean),
        min_size=3,
        max_size=3,
        open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        reset=_flip,
    )
    thread = ck.new_thread_id()
    try:
        assert pool.kwargs["autocommit"] is True  # 선언은 통과한다
        # 반복해도 같은 code다 — timing에 기대지 않는다.
        for _ in range(5):
            with pytest.raises(ck.AgentCheckpointError) as exc:
                ck.build_postgres_saver(pool)
            assert exc.value.reason_code == "CHECKPOINT_POOL_RESET_UNSUPPORTED"
    finally:
        pool.close()

    assert _checkpoint_rows(clean, thread) == 0


def test_a_non_autocommit_pool_does_not_lose_the_checkpoint(clean: Any) -> None:
    """**초판이 적은 근거를 정정한다**(구현리뷰 1차 필수 1 추적).

    connection과 달리 pool은 non-autocommit이어도 checkpoint를 잃지 않는다.
    `pool.connection()`이 `with conn:`으로 감싸 반납 시 commit하기 때문이다.

    그래서 pool guard의 이유는 "유실"이 아니라 **두 경로의 실패 모드를 같게 두는
    것**이다. 이 대조군이 없으면 누군가 다시 "pool도 잃는다"로 읽는다.
    """

    pool = psycopg_pool.ConnectionPool(
        _dsn(clean),
        min_size=1,
        max_size=1,
        open=True,
        kwargs={"autocommit": False, "row_factory": dict_row},
    )
    thread = ck.new_thread_id()
    try:
        # 생산 helper는 이 pool을 거부한다 — 계약이기 때문이다.
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(pool)
        assert exc.value.reason_code == "CHECKPOINT_POOL_CONFIG_INVALID"
        # 그러나 실제로 돌려 보면 행은 남는다.
        _build_graph(PostgresSaver(pool)).invoke(
            {"pre_interrupt_count": 0}, config=ck.build_thread_config(thread)
        )
    finally:
        pool.close()

    assert _checkpoint_rows(clean, thread) >= 1


def test_autocommit_alone_is_enough_for_the_saver(clean: Any) -> None:
    """**계약이 요구하는 것은 `autocommit` 하나다**(구현리뷰 1차 필수 2).

    다른 fixture는 `prepare_threshold=0`·`row_factory=dict_row`를 함께 넣는다.
    `from_conn_string()`이 그렇게 하기 때문인데, 그 셋이 다 필요하다는 근거는 없었다.
    여기서 **autocommit만 준 연결**로 같은 흐름을 돌려 계약과 회귀를 일치시킨다.
    """

    thread = ck.new_thread_id()
    connection = psycopg.connect(
        host=clean.host,
        port=clean.port,
        dbname=TARGET_DATABASE,
        user=clean.username,
        password=clean.password,
        autocommit=True,
    )
    try:
        graph = _build_graph(ck.build_postgres_saver(connection))
        config = ck.build_thread_config(thread)
        graph.invoke({"pre_interrupt_count": 0}, config=config)
        assert graph.get_state(config).next == ("wait",)
    finally:
        connection.close()

    assert _checkpoint_rows(clean, thread) >= 1

    # 새 연결에서도 같은 구성으로 읽힌다.
    reader = psycopg.connect(
        host=clean.host,
        port=clean.port,
        dbname=TARGET_DATABASE,
        user=clean.username,
        password=clean.password,
        autocommit=True,
    )
    try:
        assert (
            ck.build_postgres_saver(reader).get_tuple(ck.build_thread_config(thread))
            is not None
        )
    finally:
        reader.close()


def test_an_unobservable_pool_is_refused(clean: Any) -> None:
    """관측 자체가 불가능하면 통과시키지 않는다 — 판정 불가는 허용이 아니다."""

    pool = psycopg_pool.ConnectionPool(
        _dsn(clean),
        min_size=1,
        max_size=1,
        open=False,
        kwargs={"autocommit": True},
    )
    try:
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(pool)
        assert exc.value.reason_code == "CHECKPOINT_POOL_UNOBSERVABLE"
    finally:
        with contextlib.suppress(Exception):
            pool.close()


# --- interrupt · UoW 종료 · 재개 --------------------------------------------


@UNITS
def test_interrupt_survives_a_new_unit_and_resumes_once(clean: Any, unit: Any) -> None:
    """계획 §3.3의 8단계를 direct·pool 양쪽으로 돈다.

    `pre_interrupt_count == 1`이 핵심 변이다. 재개가 graph를 처음부터 다시 실행하면
    2가 되는데, 최종 `phase`만 보면 그 차이를 놓친다.
    """

    thread = ck.new_thread_id()
    config = ck.build_thread_config(thread)
    run_id = _create_run(clean, thread)
    assert run_id != thread

    # --- UoW 1 ------------------------------------------------------------
    with unit(clean) as saver:
        graph = _build_graph(saver)
        result = graph.invoke(
            {"agent_run_id": run_id, "thread_id": thread, "pre_interrupt_count": 0},
            config=config,
        )
        assert "phase" not in result, "interrupt 전에 끝났다"
        snapshot = graph.get_state(config)
        # 반환 dict가 아니라 snapshot에서 확인한다(계획리뷰 1차 권장 1).
        assert snapshot.next == ("wait",)
        assert any(task.interrupts for task in snapshot.tasks)

    assert _checkpoint_rows(clean, thread) >= 1

    # --- UoW 2 — 앞의 UoW는 완전히 끝났다 ---------------------------------
    stored = _stored_thread(clean, run_id)
    with unit(clean) as saver:
        resumed = _build_graph(saver)
        final = resumed.invoke(
            Command(resume="APPROVED"), config=ck.build_thread_config(stored)
        )
        terminal = resumed.get_state(ck.build_thread_config(stored))

    assert final["phase"] == "COMPLETED"
    assert final["resume_value"] == "APPROVED"
    assert final["pre_interrupt_count"] == 1, "재개가 graph를 처음부터 다시 실행했다"
    assert terminal.next == ()


def test_the_run_row_and_checkpoint_share_the_thread(clean: Any) -> None:
    """`agent_run.thread_id`와 checkpoint `thread_id`가 같고 run ID와는 다르다."""

    thread = ck.new_thread_id()
    run_id = _create_run(clean, thread)
    with _direct_unit(clean) as saver:
        _build_graph(saver).invoke(
            {"agent_run_id": run_id, "thread_id": thread, "pre_interrupt_count": 0},
            config=ck.build_thread_config(thread),
        )

    assert _stored_thread(clean, run_id) == thread
    with _connect(clean) as raw:
        rows = (
            raw.cursor()
            .execute("SELECT DISTINCT thread_id FROM checkpoints")
            .fetchall()
        )
    assert [r["thread_id"] for r in rows] == [thread]
    assert run_id not in {r["thread_id"] for r in rows}


# --- 음성 대조 --------------------------------------------------------------


def test_another_thread_sees_nothing(clean: Any) -> None:
    """다른 UUID는 원래 thread의 state·checkpoint를 얻지 못한다."""

    thread = ck.new_thread_id()
    with _direct_unit(clean) as saver:
        graph = _build_graph(saver)
        graph.invoke({"pre_interrupt_count": 0}, config=ck.build_thread_config(thread))
        snapshot = graph.get_state(ck.build_thread_config(ck.new_thread_id()))

    assert snapshot.values == {}
    assert snapshot.next == ()
    assert _checkpoint_rows(clean, str(uuid.uuid4())) == 0


def test_resuming_an_unstarted_thread_does_not_continue_the_original(
    clean: Any,
) -> None:
    """시작 안 한 thread에 resume만 보내도 원 thread가 이어진 것처럼 보이지 않는다."""

    started = ck.new_thread_id()
    with _direct_unit(clean) as saver:
        _build_graph(saver).invoke(
            {"pre_interrupt_count": 0}, config=ck.build_thread_config(started)
        )

    fresh_config = ck.build_thread_config(ck.new_thread_id())
    with _direct_unit(clean) as saver:
        graph = _build_graph(saver)
        result = graph.invoke(Command(resume="APPROVED"), config=fresh_config)
        snapshot = graph.get_state(fresh_config)
        original = _build_graph(saver).get_state(ck.build_thread_config(started))

    # 반환이 dict가 아닐 수도 있으므로 형태를 단정하지 않는다.
    leaked = result if isinstance(result, dict) else {}
    assert leaked.get("resume_value") is None
    assert leaked.get("pre_interrupt_count") in (None, 0)
    assert snapshot.values.get("resume_value") is None
    assert snapshot.values.get("pre_interrupt_count") in (None, 0)

    assert _checkpoint_rows(clean, started) >= 1
    assert original.values["pre_interrupt_count"] == 1


def test_a_closed_connection_is_not_a_success_path(clean: Any) -> None:
    """닫힌 연결의 saver를 재사용하는 경로를 성공으로 인정하지 않는다."""

    thread = ck.new_thread_id()
    connection = _connect(clean)
    graph = _build_graph(ck.build_postgres_saver(connection))
    graph.invoke({"pre_interrupt_count": 0}, config=ck.build_thread_config(thread))
    connection.close()

    with pytest.raises(psycopg.Error):
        graph.invoke(Command(resume="APPROVED"), config=ck.build_thread_config(thread))


# --- State 안전 -------------------------------------------------------------


def test_the_persisted_state_carries_exactly_the_allowed_fields(clean: Any) -> None:
    """saver에서 **다시 읽은** checkpoint를 exact로 본다.

    수명주기를 끝까지 돌린 뒤 읽는다. 중간 checkpoint를 보면 아직 없는 field 때문에
    exact 비교가 성립하지 않아 부분집합 비교로 물러서게 된다 — 그러면 field가
    사라지는 변이를 놓친다.

    판정은 `checkpoint_state_guard`가 소유하고 단위 회귀가 그 판정을 직접 검증한다.
    """

    thread = ck.new_thread_id()
    run_id = _create_run(clean, thread)
    config = ck.build_thread_config(thread)

    with _direct_unit(clean) as saver:
        _build_graph(saver).invoke(
            {"agent_run_id": run_id, "thread_id": thread, "pre_interrupt_count": 0},
            config=config,
        )
    with _direct_unit(clean) as saver:
        _build_graph(saver).invoke(Command(resume="APPROVED"), config=config)

    with _direct_unit(clean) as saver:
        loaded = saver.get_tuple(config)

    assert loaded is not None
    values = loaded.checkpoint["channel_values"]
    domain = guard.domain_fields(values, NODE_NAMES)
    assert domain == guard.ALLOWED_STATE_FIELDS, sorted(
        domain ^ guard.ALLOWED_STATE_FIELDS
    )
    assert guard.find_sensitive(values) == []
