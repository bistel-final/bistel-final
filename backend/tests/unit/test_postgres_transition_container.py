"""`V5-CM-2.6` 전환을 실제 PostgreSQL에서 실증한다.

Gate 0이 관측한 공용 DB 형상을 격리 container에 그대로 세우고, 계획 §8 transition을
같은 순서로 돌린다. 공용 DB에는 접근하지 않는다.

legacy 형상은 **2.6 전용 vendored fixture**로 만든다. 원본은 저장소에 커밋된
`infra/bootstrap/001_base_schema.sql`과 `001_reference_extensions.sql`이지만,
`V5-CM-1.7`이 전자를 삭제하면 이 회귀가 깨진다(구현리뷰 1차 권장 1). 그래서
필요한 최소본을 `tests/fixtures/v5_cm_2_6/`에 복사하고 hash로 고정한다.

fixture는 **격리 fingerprint 재현 전용**이다. 공용 DB 적용·COPY·복구 입력으로 쓰지 않고,
폐기된 `kosa_0813` 패키지도 입력으로 쓰지 않는다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import psycopg
import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import postgres_transition as transition  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402
import rehearsal_profile_loader as loader  # noqa: E402

pytestmark = pytest.mark.container

REPOSITORY_ROOT = SCRIPTS_ROOT.parents[1]
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "v5_cm_2_6"
BASE_SQL = FIXTURE_ROOT / "legacy_base_schema.sql"
REFERENCE_SQL = FIXTURE_ROOT / "legacy_reference.sql"

#: vendored fixture가 조용히 바뀌면 legacy identity 재현이 의미를 잃는다.
FIXTURE_SHA256 = {
    "legacy_base_schema.sql": (
        "ae2a930cde41e3acc5f4dd73411804bb0317d591570c553fa304fe3999d1e1d6"
    ),
    "legacy_reference.sql": (
        "0de3dd295bf7f070b2fe4b45e2a1964cae2b8e0038ef5e772d310b546f8f59d2"
    ),
}


def _block(pattern: str) -> str:
    match = re.search(pattern, REFERENCE_SQL.read_text(encoding="utf-8"), re.S | re.I)
    assert match is not None, pattern
    return match.group(0)


def _build_legacy(cursor: Any) -> None:
    """Gate 0 형상 재현: legacy base 9 + r03 + legacy view."""

    cursor.execute(BASE_SQL.read_text(encoding="utf-8"))
    cursor.execute(_block(r"CREATE TABLE r03_alarm_history\s*\(.*?\);"))
    cursor.execute(_block(r"CREATE VIEW v_alarm_event AS.*?;"))


def _connect(endpoint: postgres.RehearsalEndpoint) -> psycopg.Connection:
    return psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=endpoint.database,
        user=endpoint.username,
        password=endpoint.password,
    )


def _column_shape(cursor: Any, relation: str) -> list[tuple[str, str]]:
    cursor.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (relation,),
    )
    return [(str(r[0]), str(r[1])) for r in cursor.fetchall()]


@pytest.mark.parametrize("name", sorted(FIXTURE_SHA256))
def test_vendored_fixture_hash_is_pinned(name: str) -> None:
    """fixture가 바뀌면 legacy identity 재현이 의미를 잃는다."""

    import hashlib

    digest = hashlib.sha256((FIXTURE_ROOT / name).read_bytes()).hexdigest()
    assert digest == FIXTURE_SHA256[name]


def test_legacy_view_identity_matches_pinned_constant() -> None:
    """공용 DB의 legacy View가 커밋된 migration에서 나온 것임을 재현한다.

    live 값을 기대값으로 삼으면 "지금 상태가 곧 정답"이 되어 drift를 못 잡는다.
    커밋 SQL을 격리 DB에 적용해 독립 산출한다(6차 계획리뷰 필수 1).
    """

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                _build_legacy(cursor)
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_get_viewdef('public.v_alarm_event'::regclass, true)"
                )
                definition = cursor.fetchone()[0]
    assert transition.view_fingerprint(definition) == transition.LEGACY_VIEW_SHA256


def test_raw_sql_hash_is_rejected_as_negative_fixture() -> None:
    """커밋 SQL **원문 문자열** hash는 기대값이 아니다.

    PostgreSQL이 `pg_get_viewdef`에서 정의를 재작성하므로 두 값은 원리적으로 다르다.
    이 negative fixture가 없으면 같은 오해가 다시 생긴다.
    """

    raw = transition.view_fingerprint(_block(r"CREATE VIEW v_alarm_event AS.*?;"))
    assert raw != transition.LEGACY_VIEW_SHA256


def test_transition_sequence_preserves_view_shape_and_alters_wafer() -> None:
    """계획 §8의 4→6→8단계를 그대로 돌린다.

    `v_alarm_event`가 `wafer` ALTER를 물리적으로 막으므로 순서가 곧 성립 조건이다.
    호환 View는 consumer가 보는 column을 한 칸도 바꾸지 않아야 한다.
    """

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                _build_legacy(cursor)
            connection.commit()

            with connection.cursor() as cursor:
                before = _column_shape(cursor, transition.LEGACY_VIEW)
                cursor.execute(
                    "SELECT pg_get_viewdef('public.v_alarm_event'::regclass, true)"
                )
                legacy_definition = cursor.fetchone()[0]
                for name in transition.WAFER_ALTER_TABLES:
                    assert _wafer_type(cursor, name) == transition.LEGACY_WAFER_TYPE

            compat = transition.build_compatibility_view_sql(legacy_definition)

            # ALTER는 view가 살아 있는 동안 반드시 실패한다.
            with connection.cursor() as cursor:
                with pytest.raises(psycopg.errors.Error):
                    cursor.execute(
                        'ALTER TABLE public."trace_alarm_history" '
                        "ALTER COLUMN wafer TYPE varchar(24)"
                    )
            connection.rollback()

            with connection.cursor() as cursor:
                cursor.execute(f"DROP VIEW public.{transition.LEGACY_VIEW}")
                for name in transition.WAFER_ALTER_TABLES:
                    cursor.execute(
                        f'ALTER TABLE public."{name}" ALTER COLUMN wafer '
                        "TYPE varchar(24) USING wafer::varchar(24)"
                    )
                cursor.execute(compat)
            connection.commit()

            with connection.cursor() as cursor:
                after = _column_shape(cursor, transition.LEGACY_VIEW)
                for name in transition.WAFER_ALTER_TABLES:
                    assert _wafer_type(cursor, name) == transition.FINAL_WAFER_TYPE
                cursor.execute("SELECT count(*) FROM public.v_alarm_event")
                assert cursor.fetchone()[0] == 0  # 빈 base라 0이 정상이다

    assert after == before
    assert ("wafer_no", "smallint") in after


def _wafer_type(cursor: Any, relation: str) -> str:
    cursor.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND column_name='wafer'",
        (relation,),
    )
    row = cursor.fetchone()
    return "" if row is None else str(row[0])


def test_rollback_restores_legacy_view_and_wafer_types() -> None:
    """View drop 이후 어떤 실패든 rollback되면 legacy 상태로 돌아온다(계획 §12.1)."""

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                _build_legacy(cursor)
            connection.commit()

            with connection.cursor() as cursor:
                cursor.execute(f"DROP VIEW public.{transition.LEGACY_VIEW}")
                cursor.execute(
                    'ALTER TABLE public."trace_alarm_history" ALTER COLUMN wafer '
                    "TYPE varchar(24) USING wafer::varchar(24)"
                )
            connection.rollback()

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_get_viewdef('public.v_alarm_event'::regclass, true)"
                )
                assert (
                    transition.view_fingerprint(cursor.fetchone()[0])
                    == transition.LEGACY_VIEW_SHA256
                )
                assert (
                    _wafer_type(cursor, "trace_alarm_history")
                    == transition.LEGACY_WAFER_TYPE
                )


def _inventory_from(endpoint: postgres.RehearsalEndpoint) -> Any:
    from sqlalchemy import create_engine

    url = (
        f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
        f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
    )
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return transition.read_inventory(
                connection, database="kosa_agent", profile="runtime"
            )
    finally:
        engine.dispose()


def test_inventory_collects_constraint_definitions_and_column_attributes() -> None:
    """catalog 수집이 실제로 정의·속성을 담는지 실물로 확인한다.

    projection이 값을 쓰더라도 **수집 단계**가 비어 있으면 drift를 못 잡는다.
    fixture로 만든 inventory만 검사하면 이 결함이 보이지 않는다.
    """

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                _build_legacy(cursor)
            connection.commit()
        inventory = _inventory_from(endpoint)

    constraints = inventory.constraints.get("lot_history", ())
    assert constraints, "constraint를 하나도 읽지 못했다"
    for name, kind, definition in constraints:
        assert name and kind
        assert definition, f"{name}의 정의가 비어 있다"
    assert any("PRIMARY KEY" in definition for _n, _k, definition in constraints)

    columns = inventory.column_details.get("lot_history", {})
    assert columns, "column 상세를 읽지 못했다"
    wafer_no = columns["wafer_no"]
    assert wafer_no["data_type"] == "smallint"
    assert wafer_no["is_nullable"] == "NO"
    assert set(wafer_no) >= {
        "data_type",
        "udt_name",
        "is_nullable",
        "column_default",
        "character_maximum_length",
        "numeric_precision",
        "numeric_scale",
    }


def test_base_catalog_hash_matches_pinned_legacy_constant() -> None:
    """pinned base catalog hash가 커밋된 legacy DDL에서 재현된다."""

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                cursor.execute(BASE_SQL.read_text(encoding="utf-8"))
            connection.commit()
        legacy = _inventory_from(endpoint)
        assert (
            transition.base_catalog_sha256(legacy)
            == transition.LEGACY_BASE_CATALOG_SHA256
        )

        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                for name in transition.WAFER_ALTER_TABLES:
                    cursor.execute(
                        f'ALTER TABLE public."{name}" ALTER COLUMN wafer '
                        "TYPE varchar(24) USING wafer::varchar(24)"
                    )
            connection.commit()
        final = _inventory_from(endpoint)
    assert transition.base_catalog_sha256(final) == transition.FINAL_BASE_CATALOG_SHA256


def test_compatibility_view_hash_matches_pinned_constant() -> None:
    """호환 View hash가 legacy와 다르고 pin과 exact인지 실물로 확인한다."""

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                _build_legacy(cursor)
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_get_viewdef('public.v_alarm_event'::regclass, true)"
                )
                legacy_definition = cursor.fetchone()[0]
            compat = transition.build_compatibility_view_sql(legacy_definition)
            with connection.cursor() as cursor:
                cursor.execute(f"DROP VIEW public.{transition.LEGACY_VIEW}")
                for name in transition.WAFER_ALTER_TABLES:
                    cursor.execute(
                        f'ALTER TABLE public."{name}" ALTER COLUMN wafer '
                        "TYPE varchar(24) USING wafer::varchar(24)"
                    )
                cursor.execute(compat)
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_get_viewdef('public.v_alarm_event'::regclass, true)"
                )
                compat_definition = cursor.fetchone()[0]

    assert transition.view_fingerprint(compat_definition) == (
        transition.COMPAT_VIEW_SHA256
    )
    assert transition.COMPAT_VIEW_SHA256 != transition.LEGACY_VIEW_SHA256


# ---------------------------------------------------------------------------
# 구현리뷰 4차 필수 3 — version pin이 image digest와 함께 유지되는가
# ---------------------------------------------------------------------------


def test_pinned_backup_client_reports_the_pinned_version() -> None:
    """`POSTGRES_BACKUP_CLIENT_VERSIONS`가 image digest와 갈라지면 여기서 깨진다.

    두 상수를 손으로 따로 유지하면 image를 올리고 version pin을 잊는 순간 receipt
    대조가 늘 실패하거나(운영 중단) 낡은 값을 승인하게 된다(구현리뷰 4차 필수 3).
    """

    import subprocess

    import postgres_backup as backup

    image = backup.expected_client_image(16)
    for tool in ("pg_dump", "pg_restore"):
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", tool, image, "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        observed = result.stdout.strip()
        assert backup.parse_client_major(observed) == 16
        # pg_dump 문자열이 곧 pin 값이고, pg_restore는 같은 major·patch여야 한다.
        expected = backup.expected_client_version(16)
        assert observed == expected.replace("pg_dump", tool)


# ---------------------------------------------------------------------------
# 구현리뷰 5차 필수 3 — lock·snapshot 계약이 실제 서버에서 성립하는가
# ---------------------------------------------------------------------------


STUB_TABLES = "PRESERVED+RAG+BASE"


def _stub_missing_tables(connection: Any, text: Any) -> None:
    """축소 fixture에 없는 lock 대상만 빈 stub으로 세운다.

    이 회귀가 보는 것은 catalog identity가 아니라 lock 순서와 snapshot 계약이다.
    """

    for name in sorted(
        {
            *transition.PRESERVED_TABLES_BY_PROFILE["runtime"],
            *transition.RAG_TABLES,
            *transition.BASE_TABLES,
        }
    ):
        if not connection.execute(
            text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}
        ).scalar():
            connection.execute(text(f"CREATE TABLE public.{name} (id integer)"))


def _granted_modes(connection: Any, text: Any) -> dict[str, str]:
    return {
        str(row[0]): str(row[1])
        for row in connection.execute(
            text(
                "SELECT c.relname, l.mode FROM pg_locks l "
                "JOIN pg_class c ON c.oid = l.relation "
                "WHERE l.pid = pg_backend_pid() AND l.granted"
            )
        )
    }


def _advisory_count(connection: Any, text: Any) -> int:
    return int(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid() AND granted"
            )
        ).scalar()
    )


def test_lock_and_snapshot_contract_holds_on_a_real_server() -> None:
    """`acquire_target_locks()`가 실제 PostgreSQL 16에서 그대로 실행된다.

    가짜 connection만으로는 SQL이 파싱되는지, `SET LOCAL`이 transaction 안에서만
    유효한지, table lock이 실제로 잡히는지 알 수 없다(구현리뷰 5차 필수 3).

    mutex는 transaction **밖에서** 잡고 commit 뒤에도 살아 있어야 한다. session lock이
    아니라면 그 성질이 사라진다(구현리뷰 7차 필수 1).
    """

    from sqlalchemy import create_engine, text

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as raw:
            with raw.cursor() as cursor:
                _build_legacy(cursor)
            raw.commit()

        url = (
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
        )
        engine = create_engine(url, isolation_level="REPEATABLE READ")
        try:
            with engine.begin() as setup:
                _stub_missing_tables(setup, text)

            with engine.connect() as session:
                transition.acquire_target_mutex(session, database="kosa_agent")
                session.commit()
                assert (
                    _advisory_count(session, text) == 1
                ), "session mutex가 commit에서 사라졌다"
                session.commit()

                with session.begin():
                    transition.acquire_target_locks(session, database="kosa_agent")
                    modes = _granted_modes(session, text)
                    # 상태 확인 전에는 base 9도 `SHARE`다 — 공용 조회를 막지 않는다.
                    for name in transition.BASE_TABLES:
                        assert modes.get(name) == "ShareLock", name
                    assert modes.get("nl_query_log") == "ShareLock"

                    assert session.execute(text("SHOW lock_timeout")).scalar() == "5s"
                    assert transition.require_snapshot_isolation(session) == (
                        "repeatable read"
                    )

                    # 전환이 실제로 필요할 때만 승격한다.
                    transition.escalate_base_locks(session, database="kosa_agent")
                    escalated = {
                        name
                        for name, mode in _granted_modes(session, text).items()
                        if mode == "AccessExclusiveLock"
                    }
                    assert escalated == set(transition.BASE_TABLES)

                transition.release_target_mutex(session, database="kosa_agent")
                assert _advisory_count(session, text) == 0
                session.commit()
        finally:
            engine.dispose()


def test_locks_without_the_mutex_are_refused() -> None:
    """mutex 없이 들어온 배선을 transaction 안에서 잡는다(구현리뷰 7차 필수 1)."""

    from sqlalchemy import create_engine, text

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as raw:
            with raw.cursor() as cursor:
                _build_legacy(cursor)
            raw.commit()

        url = (
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
        )
        engine = create_engine(url, isolation_level="REPEATABLE READ")
        try:
            with engine.begin() as setup:
                _stub_missing_tables(setup, text)
            with engine.begin() as connection:
                with pytest.raises(transition.TransitionError) as caught:
                    transition.acquire_target_locks(connection, database="kosa_agent")
                assert caught.value.reason_code == "TARGET_MUTEX_MISSING"
        finally:
            engine.dispose()


def test_two_concurrent_transitions_serialize_without_deadlock() -> None:
    """같은 target에 두 실행이 들어오면 mutex가 **table lock 전에** 직렬화한다.

    예전 순서(`SHARE` → advisory)는 T1이 T2의 SHARE를, T2가 T1의 advisory를 기다리는
    inversion을 만들었다(구현리뷰 7차 필수 1). 이제 두 번째 실행은 table lock을 하나도
    잡지 않은 채 mutex에서 기다린다.
    """

    import threading

    from sqlalchemy import create_engine, text

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as raw:
            with raw.cursor() as cursor:
                _build_legacy(cursor)
            raw.commit()

        url = (
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
        )
        engine = create_engine(url, isolation_level="REPEATABLE READ")
        first_locked = threading.Event()
        release_first = threading.Event()
        order: list[str] = []
        failures: list[BaseException] = []
        try:
            with engine.begin() as setup:
                _stub_missing_tables(setup, text)

            def run(label: str, wait: bool) -> None:
                try:
                    with engine.connect() as session:
                        transition.acquire_target_mutex(session, database="kosa_agent")
                        session.commit()
                        order.append(f"mutex:{label}")
                        session.commit()
                        with session.begin():
                            transition.acquire_target_locks(
                                session, database="kosa_agent"
                            )
                            transition.escalate_base_locks(
                                session, database="kosa_agent"
                            )
                            order.append(f"escalated:{label}")
                            if wait:
                                first_locked.set()
                                release_first.wait(timeout=15)
                        transition.release_target_mutex(session, database="kosa_agent")
                        order.append(f"done:{label}")
                except BaseException as exc:  # noqa: BLE001
                    failures.append(exc)

            first = threading.Thread(target=run, args=("A", True))
            first.start()
            assert first_locked.wait(timeout=20)

            second = threading.Thread(target=run, args=("B", False))
            second.start()
            # B는 mutex에서 기다린다 — table lock을 하나도 잡지 않는다.
            second.join(timeout=2)
            assert second.is_alive(), "B가 mutex를 기다리지 않았다"
            assert "mutex:B" not in order

            release_first.set()
            first.join(timeout=20)
            second.join(timeout=20)
        finally:
            release_first.set()
            engine.dispose()

        assert not failures, f"동시 실행이 실패했다: {failures}"
        assert order == [
            "mutex:A",
            "escalated:A",
            "done:A",
            "mutex:B",
            "escalated:B",
            "done:B",
        ]


def test_autocommit_connection_is_refused_by_the_snapshot_contract() -> None:
    """autocommit이면 `SHOW transaction_isolation`이 무엇을 답하든 거부한다."""

    from sqlalchemy import create_engine

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                _build_legacy(cursor)
            connection.commit()

        url = (
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
        )
        engine = create_engine(url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                with pytest.raises(transition.TransitionError) as caught:
                    transition.acquire_target_locks(connection, database="kosa_agent")
                assert caught.value.reason_code == "SNAPSHOT_NOT_ISOLATED"
        finally:
            engine.dispose()


def test_snapshot_is_taken_after_the_locks_not_before() -> None:
    """lock을 기다리는 사이 commit된 변경이 **보여야** 한다.

    REPEATABLE READ는 첫 snapshot-bearing SELECT에서 snapshot을 고정한다. 그 SELECT가
    `LOCK TABLE`보다 앞서면, lock을 잡고 읽어도 lock 대기 중 commit된 변경을 보지 못한다
    — "lock 안 재확인"이 아니라 "lock 이전 과거 재확인"이 된다(구현리뷰 6차 필수 2).

    B가 lock을 쥔 채 값을 바꾸고 commit하는 동안 A는 lock을 기다린다. A가 새 값을 읽으면
    snapshot이 lock 뒤에 고정된 것이다.
    """

    import threading
    import time

    from sqlalchemy import create_engine, text

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                _build_legacy(cursor)
                cursor.execute("CREATE TABLE public.race (v integer)")
                cursor.execute("INSERT INTO public.race VALUES (1)")
            connection.commit()

        url = (
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
        )
        engine = create_engine(url, isolation_level="REPEATABLE READ")
        blocker_ready = threading.Event()
        released = threading.Event()
        observed: list[int] = []

        def blocker() -> None:
            with engine.begin() as connection:
                connection.execute(
                    text("LOCK TABLE public.race IN ACCESS EXCLUSIVE MODE")
                )
                blocker_ready.set()
                released.wait(timeout=20)
                connection.execute(text("UPDATE public.race SET v = 2"))

        def reader() -> None:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '20s'"))
                # 이 시점에 snapshot을 만드는 statement가 없어야 한다.
                connection.execute(
                    text("LOCK TABLE public.race IN ACCESS EXCLUSIVE MODE")
                )
                observed.append(
                    int(connection.execute(text("SELECT v FROM public.race")).scalar())
                )

        def waiting_for_lock() -> bool:
            """reader가 **실제로** lock 대기에 들어갔는지 제3 connection에서 본다.

            바로 release하면 blocker가 먼저 commit하고 reader가 나중에 시작해도 2를
            읽어 통과한다 — 옛 순서를 죽이지 못한다(구현리뷰 7차 권장 2).
            """

            probe = create_engine(url)
            try:
                with probe.connect() as connection:
                    return bool(
                        connection.execute(
                            text(
                                "SELECT count(*) FROM pg_locks l "
                                "JOIN pg_class c ON c.oid = l.relation "
                                "WHERE c.relname = 'race' AND NOT l.granted"
                            )
                        ).scalar()
                    )
            finally:
                probe.dispose()

        thread = threading.Thread(target=blocker)
        waiter = threading.Thread(target=reader)
        try:
            thread.start()
            assert blocker_ready.wait(timeout=10)
            waiter.start()

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not waiting_for_lock():
                time.sleep(0.1)
            assert waiting_for_lock(), "reader가 lock 대기에 들어가지 않았다"

            released.set()
            thread.join(timeout=30)
            waiter.join(timeout=30)
        finally:
            released.set()
            thread.join(timeout=30)
            waiter.join(timeout=30)
            engine.dispose()

        assert observed == [2], "lock 이전 snapshot을 읽었다"


def test_normal_reads_are_not_blocked_while_a_final_target_is_verified() -> None:
    """no-op 검증이 `SHARE`만 잡으므로 공용 조회는 계속 된다(구현리뷰 6차 필수 3)."""

    import threading

    from sqlalchemy import create_engine, text

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as connection:
            with connection.cursor() as cursor:
                _build_legacy(cursor)
            connection.commit()

        url = (
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
        )
        engine = create_engine(url, isolation_level="REPEATABLE READ")
        holding = threading.Event()
        finish = threading.Event()
        read_ok: list[bool] = []
        try:
            with engine.begin() as setup:
                for name in transition.BASE_TABLES:
                    if not setup.execute(
                        text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}
                    ).scalar():
                        setup.execute(text(f"CREATE TABLE public.{name} (id integer)"))

            def holder() -> None:
                with engine.begin() as connection:
                    for name in sorted(transition.BASE_TABLES):
                        connection.execute(
                            text(f"LOCK TABLE public.{name} IN SHARE MODE")
                        )
                    holding.set()
                    finish.wait(timeout=10)

            thread = threading.Thread(target=holder)
            thread.start()
            assert holding.wait(timeout=10)
            reader = create_engine(url)
            try:
                with reader.connect() as connection:
                    connection.execute(text("SET lock_timeout = '3s'"))
                    connection.execute(
                        text("SELECT count(*) FROM public.lot_history")
                    ).scalar()
                    read_ok.append(True)
            finally:
                reader.dispose()
            finish.set()
            thread.join(timeout=10)
        finally:
            finish.set()
            engine.dispose()

        assert read_ok == [True], "SHARE lock이 정상 조회를 막았다"


# ---------------------------------------------------------------------------
# 구현리뷰 8차 필수 3 — 전환 DDL을 실제 PostgreSQL 16에서 실증한다
# ---------------------------------------------------------------------------


def test_transition_statements_apply_on_a_real_legacy_shape() -> None:
    """`transition_statements()`가 실제 legacy 형상에 그대로 적용된다.

    지금까지 전환 DDL은 문서에만 있었고 코드가 만들지 않았다(구현리뷰 8차 필수 3).
    순서가 틀리면 첫 `ALTER`가 View 의존성에 막힌다.
    """

    from sqlalchemy import create_engine, text

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as raw:
            with raw.cursor() as cursor:
                _build_legacy(cursor)
            raw.commit()

        url = (
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
        )
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                legacy = connection.execute(
                    text(
                        "SELECT pg_get_viewdef(c.oid, true) AS body FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relname = :v"
                    ),
                    {"v": transition.LEGACY_VIEW},
                ).scalar()
                assert legacy

            # 전제 — View가 살아 있으면 그 View가 물고 있는 table의 ALTER가 막힌다.
            from sqlalchemy.exc import DatabaseError

            with pytest.raises(DatabaseError):
                with engine.begin() as blocked:
                    blocked.execute(
                        text(
                            transition.ALTER_WAFER_SQL_TEMPLATE.format(
                                table="trace_alarm_history",
                                column=transition.WAFER_COLUMN,
                                type=transition.FINAL_WAFER_DDL_TYPE,
                            )
                        )
                    )

            with engine.begin() as connection:
                for statement in transition.transition_statements(str(legacy)):
                    connection.execute(text(statement))

            with engine.begin() as connection:
                types = {
                    str(row[0]): (str(row[1]), row[2])
                    for row in connection.execute(
                        text(
                            "SELECT table_name, data_type, character_maximum_length "
                            "FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND column_name = :c"
                        ),
                        {"c": transition.WAFER_COLUMN},
                    )
                }
                for table in transition.WAFER_ALTER_TABLES:
                    assert types[table] == (
                        transition.FINAL_WAFER_TYPE,
                        transition.FINAL_WAFER_MAX_LENGTH,
                    ), table

                body = connection.execute(
                    text(
                        "SELECT pg_get_viewdef(c.oid, true) AS body FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relname = :v"
                    ),
                    {"v": transition.LEGACY_VIEW},
                ).scalar()
                assert transition.view_fingerprint(str(body)) == (
                    transition.COMPAT_VIEW_SHA256
                )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 구현리뷰 12차 필수 1·4 — 격리 target에서 handler 전체를 실제로 돌린다
# ---------------------------------------------------------------------------


def _prepare_target(endpoint: Any, text: Any, engine: Any) -> None:
    """legacy 형상 + ACL 확인에 필요한 role을 세운다."""

    with engine.begin() as setup:
        setup.execute(text(f'CREATE ROLE "{transition.READONLY_ROLE}" NOLOGIN'))
        setup.execute(text(f'CREATE ROLE "{transition.LEGACY_VIEW_OWNER}" NOLOGIN'))
        setup.execute(
            text(
                f"ALTER VIEW public.{transition.LEGACY_VIEW} "
                f'OWNER TO "{transition.LEGACY_VIEW_OWNER}"'
            )
        )
        setup.execute(
            text(f"REVOKE ALL ON public.{transition.LEGACY_VIEW} FROM PUBLIC")
        )
        setup.execute(
            text(
                f"GRANT SELECT ON public.{transition.LEGACY_VIEW} "
                f'TO "{transition.READONLY_ROLE}"'
            )
        )


def test_handler_transitions_a_real_target_and_restores_the_view_acl() -> None:
    """handler 전체를 실제 PostgreSQL에서 돌린다.

    지금까지 회귀는 fake connection의 SQL 문자열까지였다. View를 DROP하면 ACL이
    사라지므로, 계획 §8.1의 명시 복원이 실제로 동작하는지 실물로 확인해야 한다
    (구현리뷰 12차 필수 4).
    """

    import transition_sessions as ts
    from sqlalchemy import create_engine, text

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as raw:
            with raw.cursor() as cursor:
                _build_legacy(cursor)
            raw.commit()

        url = (
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
        )
        engine = create_engine(url)
        try:
            _prepare_target(endpoint, text, engine)

            # 이 fixture에 있는 base table만 대상으로 삼는다.
            with engine.begin() as probe:
                present = {
                    str(row[0])
                    for row in probe.execute(
                        text(
                            "SELECT c.relname FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                        )
                    )
                }
            tables = tuple(name for name in loader.LOAD_ORDER if name in present)
            assert tables, "fixture에 base table이 없다"

            columns = {}
            with engine.begin() as probe:
                for table in tables:
                    columns[table] = tuple(
                        str(row[0])
                        for row in probe.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema='public' AND table_name=:t "
                                "ORDER BY ordinal_position"
                            ),
                            {"t": table},
                        )
                    )

            handler = ts.make_transition_handler(
                csv_bodies={
                    table: (",".join(columns[table]) + "\n").encode("utf-8")
                    for table in tables
                },
                columns_by_table=columns,
                tables=tables,
                profile="runtime",
            )

            original = transition.LEGACY_VIEW_SHA256
            with engine.begin() as probe:
                definition = probe.execute(
                    text(
                        "SELECT pg_get_viewdef(c.oid, true) FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname='public' AND c.relname=:v"
                    ),
                    {"v": transition.LEGACY_VIEW},
                ).scalar()
            transition.LEGACY_VIEW_SHA256 = transition.view_fingerprint(str(definition))
            try:
                with engine.begin() as connection:
                    handler(
                        connection,
                        "kosa_agent",
                        _inventory_from(endpoint),
                    )
            finally:
                transition.LEGACY_VIEW_SHA256 = original

            with engine.begin() as check:
                # wafer 4종이 varchar(24)가 됐다.
                types = {
                    str(row[0]): (str(row[1]), row[2])
                    for row in check.execute(
                        text(
                            "SELECT table_name, data_type, "
                            "character_maximum_length "
                            "FROM information_schema.columns "
                            "WHERE table_schema='public' AND column_name=:c"
                        ),
                        {"c": transition.WAFER_COLUMN},
                    )
                }
                for table in transition.WAFER_ALTER_TABLES:
                    assert types[table] == (
                        transition.FINAL_WAFER_TYPE,
                        transition.FINAL_WAFER_MAX_LENGTH,
                    ), table

                # **View ACL이 계획대로 복원됐다.**
                row = (
                    check.execute(
                        text(
                            "SELECT pg_get_userbyid(c.relowner) AS owner, "
                            "c.relacl::text AS acl FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname='public' AND c.relname=:v"
                        ),
                        {"v": transition.LEGACY_VIEW},
                    )
                    .mappings()
                    .first()
                )
                assert row["owner"] == transition.LEGACY_VIEW_OWNER
                assert row["acl"] == transition.LEGACY_VIEW_ACL
        finally:
            engine.dispose()


def test_handler_rolls_back_completely_on_failure() -> None:
    """중간 실패는 schema·데이터·ACL을 하나도 남기지 않는다."""

    import transition_sessions as ts
    from sqlalchemy import create_engine, text

    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with _connect(endpoint) as raw:
            with raw.cursor() as cursor:
                _build_legacy(cursor)
            raw.commit()

        url = (
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}"
        )
        engine = create_engine(url)
        try:
            _prepare_target(endpoint, text, engine)
            before = _inventory_from(endpoint)

            def failing(connection: Any, database: str, inventory: Any) -> None:
                connection.execute(text(transition.DROP_VIEW_SQL))
                raise ts.SessionError("MODE_CONTRACT_ERROR", 1)

            with pytest.raises(ts.SessionError):
                with engine.begin() as connection:
                    failing(connection, "kosa_agent", before)

            after = _inventory_from(endpoint)
            assert after.view_sha256 == before.view_sha256
            assert after.column_types == before.column_types
        finally:
            engine.dispose()
