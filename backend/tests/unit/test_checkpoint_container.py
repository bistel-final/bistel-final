"""`V5-CM-3.4` 격리 PostgreSQL 16 실증.

위 unit 회귀는 판정 로직을 본다. 여기서는 **실제 `PostgresSaver.setup()`을 돌려**
package가 무엇을 만드는지, 그리고 `setup()`이 **해주지 않는 것**이 무엇인지 DB로
고정한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import psycopg
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import apply_severity_pair_guard as severity_guard  # noqa: E402
import checkpoint_contract as contract  # noqa: E402
import manifest_v3  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402
import setup_checkpoint as runner  # noqa: E402

#: 이 module의 모든 테스트가 쓰는 대상 DB.
#: `_target()`과 runner 계약이 이 이름을 요구한다.
TARGET_DATABASE = "kosa_agent_e2e"

pytestmark = pytest.mark.container


@pytest.fixture
def endpoint() -> Any:
    with postgres.one_off_postgres(database="kosa_agent_e2e") as value:
        yield value


def _dsn(endpoint: Any) -> str:
    return (
        f"postgresql://{endpoint.username}:{endpoint.password}"
        f"@{endpoint.host}:{endpoint.port}/kosa_agent_e2e"
    )


def _connect(endpoint: Any, *, autocommit: bool = True) -> Any:
    return psycopg.connect(
        _dsn(endpoint),
        autocommit=autocommit,
        prepare_threshold=0,
        row_factory=psycopg.rows.dict_row,
    )


def _catalog(connection: Any) -> dict[str, Any]:
    return runner.read_catalog(connection.cursor())


# --- autocommit 계약 -------------------------------------------------------


def test_setup_cannot_run_inside_a_transaction(endpoint: Any) -> None:
    """**이 Task가 CM-3.2·CM-3.3과 다른 이유를 DB로 고정한다.**

    `MIGRATIONS` 9개 중 3개가 `CREATE INDEX CONCURRENTLY`다. transaction block
    안에서는 돌 수 없으므로 setup 전체를 하나의 transaction으로 감쌀 수 없다.

    그래서 실패 시 rollback으로 되돌릴 수 없고, 복구는 승인된 backup restore가
    담당한다. 이 전제가 바뀌면 계획 §5.3의 apply 순서가 통째로 바뀐다.
    """

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(endpoint, autocommit=False) as connection:
        with pytest.raises(psycopg.errors.ActiveSqlTransaction):
            PostgresSaver(connection).setup()


def test_the_runner_opens_an_autocommit_connection(endpoint: Any) -> None:
    """runner가 여는 연결이 실제로 autocommit인지 본다."""

    import db_target

    target = db_target.BootstrapTarget(
        host=endpoint.host,
        port=int(endpoint.port),
        username=endpoint.username,
        password=endpoint.password,
        database="kosa_agent_e2e",
        profile="runtime",
    )
    connection = runner._connect(target)
    try:
        assert connection.autocommit is True
    finally:
        connection.close()


# --- apply · postcheck ----------------------------------------------------


def test_setup_creates_the_full_contract(endpoint: Any) -> None:
    """`setup()`이 만드는 것을 실측으로 고정한다."""

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(endpoint) as connection:
        assert contract.classify_state(_catalog(connection)) == "ABSENT"
        PostgresSaver(connection).setup()

        catalog = _catalog(connection)
        assert contract.classify_state(catalog) == "READY"
        assert set(catalog["tables"]) == set(contract.CHECKPOINT_TABLES)
        assert tuple(catalog["versions"]) == contract.expected_versions()
        assert set(catalog["indexes"]) == set(contract.CHECKPOINT_INDEXES)
        assert all(
            item["valid"] and item["ready"] for item in catalog["indexes"].values()
        )
        # operational table은 setup 직후 전부 0행이다.
        assert set(runner.operational_row_counts(connection.cursor()).values()) == {0}


def test_a_dropped_index_is_not_healed_by_rerunning_setup(endpoint: Any) -> None:
    """**이 Task의 존재 이유를 DB로 고정한다.**

    `setup()`은 `checkpoint_migrations`의 최대 `v`만 보고 그 다음부터 실행한다.
    version이 이미 8이면 index가 사라져도 **아무 문장도 실행하지 않는다.**

    따라서 부분 적용은 재실행으로 낫지 않으며, `setup()`이 해주지 않는 postcheck가
    이 runner의 본체다. 이 테스트가 깨지면(= 재실행이 복구하면) 계획 §5.2의
    "자동 resume 금지" 근거가 바뀐 것이다.
    """

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(endpoint) as connection:
        PostgresSaver(connection).setup()
        assert contract.classify_state(_catalog(connection)) == "READY"

        connection.cursor().execute("DROP INDEX checkpoints_thread_id_idx")
        assert contract.classify_state(_catalog(connection)) == "PARTIAL"

        # 재실행해도 낫지 않는다.
        PostgresSaver(connection).setup()
        catalog = _catalog(connection)
        assert contract.classify_state(catalog) == "PARTIAL"
        assert tuple(catalog["versions"]) == contract.expected_versions()


def test_a_version_gap_is_partial(endpoint: Any) -> None:
    """version이 연속이 아니면 이전 migration이 끝났다고 증명할 수 없다."""

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(endpoint) as connection:
        PostgresSaver(connection).setup()
        connection.cursor().execute("DELETE FROM checkpoint_migrations WHERE v = 4")
        assert contract.classify_state(_catalog(connection)) == "PARTIAL"


def test_the_catalog_signature_is_stable_and_sensitive(endpoint: Any) -> None:
    """같은 형상이면 같은 signature, 바뀌면 다른 signature."""

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(endpoint) as connection:
        PostgresSaver(connection).setup()
        first = contract.assert_ready(_catalog(connection))
        assert contract.assert_ready(_catalog(connection)) == first

        # **column을 더하면 exact 계약이 깨진다.**
        #
        # 초판은 signature가 바뀌는 것만 봤다. `assert_ready()`가 실제로 실패하는지는
        # 검사하지 않아 drift가 그대로 통과했다(구현리뷰 필수 2).
        connection.cursor().execute(
            "ALTER TABLE checkpoints ADD COLUMN cm34_probe integer"
        )
        assert contract.classify_state(_catalog(connection)) == "DRIFT"
        with pytest.raises(contract.CheckpointStateError) as caught:
            contract.assert_ready(_catalog(connection))
        assert caught.value.reason_code == "DRIFT"


def test_a_rebuilt_index_on_the_wrong_column_is_drift(endpoint: Any) -> None:
    """**이름만 맞는 index를 통과시키지 않는다.**

    `checkpoints_thread_id_idx`가 `checkpoint_id`를 걸고 있으면 thread 조회를
    살리지 못한다. 이름 집합만 보면 그것이 정상으로 보인다.
    """

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(endpoint) as connection:
        PostgresSaver(connection).setup()
        cursor = connection.cursor()
        cursor.execute("DROP INDEX checkpoints_thread_id_idx")
        cursor.execute(
            "CREATE INDEX checkpoints_thread_id_idx "
            "ON public.checkpoints USING btree (checkpoint_id)"
        )
        assert contract.classify_state(_catalog(connection)) == "DRIFT"


def test_a_changed_column_type_is_drift(endpoint: Any) -> None:
    """column type 변이도 잡는다 — 이름 집합은 그대로다."""

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(endpoint) as connection:
        PostgresSaver(connection).setup()
        connection.cursor().execute(
            "ALTER TABLE checkpoint_writes ALTER COLUMN idx TYPE bigint"
        )
        assert contract.classify_state(_catalog(connection)) == "DRIFT"


def test_a_dropped_column_default_is_drift(endpoint: Any) -> None:
    """default까지 본다 — `CATALOG_SQL`이 그것을 읽지 않던 것이 필수 2였다."""

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(endpoint) as connection:
        PostgresSaver(connection).setup()
        connection.cursor().execute(
            "ALTER TABLE checkpoint_blobs ALTER COLUMN checkpoint_ns DROP DEFAULT"
        )
        assert contract.classify_state(_catalog(connection)) == "DRIFT"


# --- runner end-to-end ----------------------------------------------------


def _target(endpoint: Any) -> Any:
    import db_target

    return db_target.BootstrapTarget(
        host=endpoint.host,
        port=int(endpoint.port),
        username=endpoint.username,
        password=endpoint.password,
        database="kosa_agent_e2e",
        profile="runtime",
    )


def test_the_runner_applies_then_reports_no_op(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """**runner 자신을 돌린다.**

    공용에서 실행할 코드가 CI에서도 같은 순서로 검증된다.
    """

    target = _target(guarded_endpoint)
    assert runner.run_preflight(target, marker_root=artifact_roots["markers"]) == (
        "ABSENT"
    )

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"
    assert runner.run_preflight(target, marker_root=artifact_roots["markers"]) == (
        "READY_MARKED"
    )
    assert runner.run_verify(target, marker_root=artifact_roots["markers"]) == (
        "READY_MARKED"
    )

    # **재실행은 setup()을 다시 부르지 않는다.**
    assert _apply(guarded_endpoint, artifact_roots) == "NO_OP"


def test_the_runner_refuses_a_partial_state(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """부분 상태에서 apply를 이어 붙이지 않는다 — 자동 보정 금지."""

    _apply(guarded_endpoint, artifact_roots)
    with _connect(guarded_endpoint) as connection:
        connection.cursor().execute("DROP INDEX checkpoint_blobs_thread_id_idx")

    target = _target(guarded_endpoint)
    assert runner.run_preflight(target, marker_root=artifact_roots["markers"]) == (
        "PARTIAL"
    )
    with pytest.raises(contract.CheckpointStateError) as caught:
        _apply(guarded_endpoint, artifact_roots)
    assert caught.value.reason_code == "PARTIAL"

    # verify도 같은 판정을 쓴다 — 상태 보고가 apply보다 느슨하면 안 된다.
    with pytest.raises(contract.CheckpointStateError):
        runner.run_verify(target, marker_root=artifact_roots["markers"])


def test_the_no_op_path_writes_nothing(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """**확인 없는 no-op은 "아무것도 보지 않았다"이다.**

    `NO_OP`도 catalog 전체를 다시 확인하고, operational row와 marker를 건드리지
    않는다.
    """

    _apply(guarded_endpoint, artifact_roots)
    marker_file = runner.marker_path("kosa_agent_e2e", root=artifact_roots["markers"])
    before_bytes = marker_file.read_bytes()
    before_mtime = marker_file.stat().st_mtime_ns
    with _connect(guarded_endpoint) as connection:
        before = contract.assert_ready(_catalog(connection))
        counts_before = runner.operational_row_counts(connection.cursor())

    assert _apply(guarded_endpoint, artifact_roots) == "NO_OP"

    with _connect(guarded_endpoint) as connection:
        assert contract.assert_ready(_catalog(connection)) == before
        assert runner.operational_row_counts(connection.cursor()) == counts_before
    # marker를 재기록조차 하지 않는다.
    assert marker_file.read_bytes() == before_bytes
    assert marker_file.stat().st_mtime_ns == before_mtime


# --- full verifier — guarded → setup → PASS ---------------------------------

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"
SQL_002 = REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
SQL_003 = REPOSITORY_ROOT / "backend" / "migrations" / "003_agent_run_severity_pair.sql"


def _fixture_table_ddl(table: str) -> str:
    """계약 registry에서 DDL을 **생성한다.** 손으로 적은 스텁을 쓰지 않는다.

    초판은 `(stub_id integer PRIMARY KEY, note text)` 하나로 모든 preserved table을
    만들었다. table 이름 수만 맞으면 됐기 때문인데, 그건 **full verifier를 한 번도
    돌리지 않았을 때만** 성립한다. 실제 `verify_database()`를 태우자마자 `document`·
    `document_chunk`·`nl_query_log`가 `COLUMN_TYPE`으로 걸렸다(구현리뷰 3차 필수 1).

    registry에 계약이 있으면 그것으로 만들고, 없으면 스텁을 유지한다.
    """

    import apply_reference_extensions as reference_extensions

    if table == reference_extensions.NL_QUERY_LOG_TABLE:
        # **소유 migration의 DDL을 그대로 쓴다.**
        #
        # registry에서 컬럼만 만들면 PK·CHECK 4종·bigserial sequence가 없는 채로
        # 이름 계약만 만족한다. RAG와 같은 함정이었다(구현리뷰 6차 필수 2).
        _sql, statements = reference_extensions.load_and_validate_sql()
        for statement in statements:
            if statement.lstrip().upper().startswith("CREATE TABLE NL_QUERY_LOG"):
                return statement
        raise AssertionError("nl_query_log DDL을 migration에서 찾지 못했습니다")

    columns = reference_extensions.EXPECTED_TABLE_COLUMNS.get(table)
    if columns is None:
        return (
            f"CREATE TABLE IF NOT EXISTS public.{table} "
            "(stub_id integer PRIMARY KEY, note text)"
        )
    body = ", ".join(
        f"{name} {data_type}" + ("" if nullable else " NOT NULL")
        for name, data_type, nullable in columns
    )
    return f"CREATE TABLE IF NOT EXISTS public.{table} ({body})"


#: guarded 형상을 한 번만 세워 둘 template database.
#:
#: 테스트마다 컨테이너를 새로 띄우면 기동 + 22-table 스키마 구성이 매번 반복된다.
#: PostgreSQL의 `CREATE DATABASE ... TEMPLATE`은 파일 복사에 가까우므로, **격리는
#: 그대로 두고** 비싼 두 단계만 1회로 줄인다.
TEMPLATE_DATABASE = "cm34_guarded_tpl"

#: 테스트가 만드는 role. cluster 단위라 DB를 지워도 남는다 — 매 테스트 앞에서 치운다.
TEST_ROLE_PREFIX = "cm34_"


def _build_guarded_schema(endpoint: Any, database: str) -> None:
    """`V5-CM-3.3` guarded 형상 22 table을 세운다.

    checkpoint를 얹기 전 predecessor 상태다. 이 위에 `setup()`을 돌려야
    `runtime_checkpointed` 26 table이 된다.
    """

    import apply_agent_runtime as agent_runtime
    import apply_rag_schema as rag
    import apply_reference_extensions_v5 as v5
    import postgres_transition as transition

    wafer_alter = "\n".join(
        f"ALTER TABLE {table} ALTER COLUMN wafer TYPE varchar(24) "
        f"USING wafer::varchar(24);"
        for table in transition.WAFER_ALTER_TABLES
    )
    with psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=database,
        user=endpoint.username,
        password=endpoint.password,
    ) as connection:
        cursor = connection.cursor()
        cursor.execute(
            (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
        )
        cursor.execute(wafer_alter)
        cursor.execute(v5.CANONICAL_SQL.read_text(encoding="utf-8"))
        # **RAG table은 소유 모듈의 DDL로 세운다.**
        #
        # registry에서 컬럼만 만들면 PK·FK·UNIQUE·CHECK·default가 없는 채로 22-table
        # 이름 계약만 만족한다. 실제로 그 상태였고, 선행 확인을 그 계약까지 넓히자마자
        # 정상 fixture가 red가 됐다(구현리뷰 5차 필수 2).
        skip = {
            v5.R03_TABLE,
            *agent_runtime.RUNTIME_TABLES,
            *rag.RAG_TABLES_TO_REPLACE,
        }
        for name in v5.PRESERVED_TABLES_BY_PROFILE["runtime"]:
            if name in skip:
                continue
            cursor.execute(_fixture_table_ddl(name))
        cursor.execute(rag.RAG_SCHEMA_SQL)
        cursor.execute(SQL_002.read_text(encoding="utf-8"))
        cursor.execute(SQL_003.read_text(encoding="utf-8"))
        connection.commit()


def _terminate_connections(cursor: Any, databases: list[str]) -> None:
    """남은 연결이 있으면 `DROP DATABASE`도 `TEMPLATE` 복제도 막힌다."""

    cursor.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = ANY(%s) AND pid <> pg_backend_pid()",
        (databases,),
    )


def _maintenance(endpoint: Any) -> Any:
    """`postgres` DB에 붙는다 — 대상 DB를 만들거나 지우려면 밖에 있어야 한다."""

    return psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname="postgres",
        user=endpoint.username,
        password=endpoint.password,
        autocommit=True,
    )


@pytest.fixture(scope="module")
def guarded_cluster() -> Any:
    """컨테이너 **1개**를 띄우고 template에 guarded 형상을 1회 구성한다.

    **pgvector image를 쓴다.** `document_chunk.embedding`은 계약이 `vector(1024)`라
    순정 `postgres:16`으로는 계약대로 세울 수 없다.
    """

    with postgres.one_off_postgres(
        database=TARGET_DATABASE, image=postgres.POSTGRES_RAG_IMAGE
    ) as value:
        # **실제 이름으로 세운 뒤 스냅샷을 뜬다.**
        #
        # `002_agent_runtime_clean.sql`은 runtime database 이름이 아니면 `RAISE`한다.
        # 그래서 template에 직접 세울 수 없고, `kosa_agent_e2e`에 세운 것을 복제한다.
        _build_guarded_schema(value, TARGET_DATABASE)
        connection = _maintenance(value)
        try:
            cursor = connection.cursor()
            _terminate_connections(cursor, [TARGET_DATABASE])
            cursor.execute(
                f'CREATE DATABASE "{TEMPLATE_DATABASE}" '
                f'TEMPLATE "{TARGET_DATABASE}"'
            )
        finally:
            connection.close()
        yield value


@pytest.fixture
def guarded_endpoint(guarded_cluster: Any) -> Any:
    """테스트마다 **pristine한 `kosa_agent_e2e`**를 template에서 복제한다.

    컨테이너를 공유하되 DB와 role은 매번 새로 만든다. 격리를 잃으면 이 Task에서
    결함을 드러냈던 신호(부분 상태·ACL 변조·PARTIAL)가 서로 새어 무의미해진다.
    """

    connection = _maintenance(guarded_cluster)
    try:
        cursor = connection.cursor()
        _terminate_connections(cursor, [TARGET_DATABASE, TEMPLATE_DATABASE])
        cursor.execute(f'DROP DATABASE IF EXISTS "{TARGET_DATABASE}"')
        # **role은 cluster 단위다.** DB를 지워도 남아 다음 테스트의 `CREATE ROLE`이
        # 중복으로 실패한다.
        cursor.execute(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE %s",
            (f"{TEST_ROLE_PREFIX}%",),
        )
        # `_maintenance()`는 dict_row가 아니다 — tuple로 받는다.
        for (rolname,) in cursor.fetchall():
            cursor.execute(f'DROP ROLE IF EXISTS "{rolname}"')
        cursor.execute(
            f'CREATE DATABASE "{TARGET_DATABASE}" TEMPLATE "{TEMPLATE_DATABASE}"'
        )
    finally:
        connection.close()
    yield guarded_cluster


def _table_count(endpoint: Any) -> int:
    with _connect(endpoint) as connection:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT count(*) AS c FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        )
        return int(cursor.fetchone()["c"])


def test_the_guarded_baseline_becomes_the_checkpointed_stage(
    guarded_endpoint: Any,
) -> None:
    """**guarded 22 → setup → checkpointed 26**을 실증한다.

    `runtime_guarded` manifest는 정확히 22 table을 기대한다. checkpoint 4개를
    만들고 stage를 그대로 두면 정상 DB가 unexpected table drift로 판정된다.
    """

    from langgraph.checkpoint.postgres import PostgresSaver

    assert _table_count(guarded_endpoint) == 22
    with _connect(guarded_endpoint) as connection:
        assert contract.classify_state(_catalog(connection)) == "ABSENT"
        PostgresSaver(connection).setup()
        assert contract.classify_state(_catalog(connection)) == "READY"
    assert _table_count(guarded_endpoint) == 26


def test_the_runtime_postcheck_accepts_the_successor(guarded_endpoint: Any) -> None:
    """**checkpoint를 얹어도 Runtime postcheck가 통과한다**(구현리뷰 필수 1).

    `postcheck_database()`에 `extra_tables`를 넘기지 않으면 정상 26-table DB가
    22-table allowlist와 비교되어 `RUNTIME_SCHEMA`가 된다. guarded constraint도
    successor에서 유지되어야 한다 — baseline을 쓰면
    `-agent_run_check1 +ck_...pair`로 반드시 실패한다.
    """

    import apply_agent_runtime as agent_runtime
    import apply_severity_pair_guard as severity_guard
    import verify_bootstrap_state as verifier
    from langgraph.checkpoint.postgres import PostgresSaver
    from sqlalchemy import create_engine

    with _connect(guarded_endpoint) as connection:
        PostgresSaver(connection).setup()

    url = (
        f"postgresql+psycopg://{guarded_endpoint.username}:"
        f"{guarded_endpoint.password}@{guarded_endpoint.host}:"
        f"{guarded_endpoint.port}/kosa_agent_e2e"
    )
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin():
            result = agent_runtime.postcheck_database(
                connection,
                alarm_rows_before=agent_runtime.alarm_event_count(connection),
                expected_constraints=severity_guard.GUARDED_CONSTRAINTS,
                extra_tables=contract.CHECKPOINT_TABLES,
            )
            assert result.schema_signature_sha256

            # **checkpoint table을 빼면 실패한다** — 확장이 실제로 필요한지 증명.
            with pytest.raises(agent_runtime.AgentRuntimeError):
                agent_runtime.postcheck_database(
                    connection,
                    alarm_rows_before=agent_runtime.alarm_event_count(connection),
                    expected_constraints=severity_guard.GUARDED_CONSTRAINTS,
                )

            # **baseline constraint를 쓰면 실패한다** — guard가 유지됨을 증명.
            with pytest.raises(agent_runtime.AgentRuntimeError):
                agent_runtime.postcheck_database(
                    connection,
                    alarm_rows_before=agent_runtime.alarm_event_count(connection),
                    expected_constraints=agent_runtime.EXPECTED_CONSTRAINTS,
                    extra_tables=contract.CHECKPOINT_TABLES,
                )
    finally:
        engine.dispose()

    # verifier가 successor stage에서 두 값을 실제로 고르는지 본다.
    assert verifier.CHECKPOINT_STAGE == "runtime_checkpointed"
    assert verifier.CHECKPOINT_STAGE in verifier.GUARDED_CONSTRAINT_STAGES


def test_an_unrelated_table_still_fails_the_postcheck(guarded_endpoint: Any) -> None:
    """**임의 table을 받지 않는다.** checkpoint 4종만 허용한다."""

    import apply_agent_runtime as agent_runtime
    import apply_severity_pair_guard as severity_guard
    from langgraph.checkpoint.postgres import PostgresSaver
    from sqlalchemy import create_engine

    with _connect(guarded_endpoint) as connection:
        PostgresSaver(connection).setup()
        connection.cursor().execute("CREATE TABLE public.cm34_stray (id integer)")

    url = (
        f"postgresql+psycopg://{guarded_endpoint.username}:"
        f"{guarded_endpoint.password}@{guarded_endpoint.host}:"
        f"{guarded_endpoint.port}/kosa_agent_e2e"
    )
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin():
            with pytest.raises(agent_runtime.AgentRuntimeError):
                agent_runtime.postcheck_database(
                    connection,
                    alarm_rows_before=agent_runtime.alarm_event_count(connection),
                    expected_constraints=severity_guard.GUARDED_CONSTRAINTS,
                    extra_tables=contract.CHECKPOINT_TABLES,
                )
    finally:
        engine.dispose()


# --- marker · receipt · smoke ----------------------------------------------


def _live_guarded_signature(endpoint: Any) -> str:
    """**실제 guarded DB에서** 선행 signature를 읽는다.

    fixture에 임의 hash를 적으면 `predecessor_postcheck()`가 무엇을 비교하는지
    검증되지 않는다 — 그건 통과하는 테스트일 뿐 증명이 아니다(구현리뷰 2차 필수 3).
    """

    connection = runner._connect(_target(endpoint))
    try:
        return runner.guarded_signature(connection.cursor())
    finally:
        connection.close()


def _write_backup_evidence(root: Path, endpoint: Any, change_ref: str) -> Path:
    """checkpoint 전용 증적 3본 + change approval을 만든다.

    **게이트를 우회하지 않는다.** 이전에는 `require_backup=False`로 껐다 —
    전환 receipt 계약이 `read_inventory(with_content=True)`를 요구했고 그것은 legacy
    base epoch 컬럼을 읽어 V5 fixture DB에서 성립하지 않았기 때문이다.

    새 계약은 `observe_shape()`(guarded signature + 행 수 hash)만 요구하므로 여기서
    **live container 값**으로 만들 수 있다. 그래서 apply가 지나는 경로를 그대로 지난다.
    """

    import checkpoint_backup as cbackup
    import db_target
    import postgres_backup as backup

    database = "kosa_agent_e2e"
    root.mkdir(parents=True, exist_ok=True)

    connection = runner._connect(_target(endpoint))
    try:
        shape = cbackup.observe_shape(connection.cursor())
    finally:
        connection.close()

    archive = root / cbackup.archive_name(database, change_ref)
    archive.write_bytes(b"cm34-archive")
    receipt = {
        "artifact_type": cbackup.RECEIPT_ARTIFACT_TYPE,
        "format_version": cbackup.FORMAT_VERSION,
        "task_id": cbackup.TASK_ID,
        "dataset_epoch": runner._dataset_epoch()["dataset_epoch"],
        "database": database,
        "profile": "runtime",
        "change_reference": change_ref,
        "server_major": 16,
        "client_major": 16,
        "backup_image_digest": backup.expected_client_image(16),
        "backup_tool_version": "pg_dump (PostgreSQL) 16.15",
        "archive_sha256": backup.archive_digest(archive),
        "target_host_fingerprint": db_target.host_fingerprint(
            endpoint.host, int(endpoint.port)
        ),
        "predecessor_stage": runner.GUARDED_STAGE,
        # 실제 dump→restore는 `test_run_backup_proves_the_restore` 하나가 돌린다.
        # 나머지 회귀는 그 결과 모양만 재현해 apply 경로를 태운다.
        "source_projection": dict(shape),
        "restored_projection": dict(shape),
        "restore_verified": True,
        "created_at": "2026-08-25T00:00:00Z",
    }
    receipt_path = root / cbackup.receipt_name(database, change_ref)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    (root / cbackup.completion_name(database, change_ref)).write_text(
        json.dumps(
            {
                "artifact_type": cbackup.COMPLETION_ARTIFACT_TYPE,
                "format_version": cbackup.FORMAT_VERSION,
                "dataset_epoch": receipt["dataset_epoch"],
                "database": database,
                "change_reference": change_ref,
                "archive_sha256": receipt["archive_sha256"],
                "receipt_sha256": backup.archive_digest(receipt_path),
            }
        ),
        encoding="utf-8",
    )

    approval = root / "change_approval.json"
    approval.write_text(
        json.dumps(
            {
                "artifact_type": runner.APPROVAL_ARTIFACT_TYPE,
                "format_version": runner.MARKER_FORMAT_VERSION,
                "task_id": runner.TASK_ID,
                "dataset_epoch": runner._dataset_epoch()["dataset_epoch"],
                "change_reference": change_ref,
                "status": "APPROVED",
                "targets": ["kosa_agent_e2e", "kosa_agent"],
                "from_stage": runner.GUARDED_STAGE,
                "to_stage": runner.CHECKPOINT_STAGE,
                "package_name": contract.PACKAGE_NAME,
                "package_version": contract.PACKAGE_VERSION,
                "migration_digest_sha256": contract.MIGRATION_DIGEST_SHA256,
                "recovery_approved": False,
                "approved_at": "2026-08-25T12:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )
    return approval


def guard_migration_sha() -> str:
    sql, _ = severity_guard.load_and_validate_sql()
    return severity_guard.migration_sha256(sql)


@pytest.fixture
def guard_marker_root(guarded_endpoint: Any, tmp_path: Path) -> Path:
    """CM-3.3 marker를 **파일로** 둔다. 저장소 실물은 건드리지 않는다.

    저장소의 실제 marker는 공용 DB를 서술하므로 signature·행 수만 이 container 사실로
    옮겨 적는다. 계보 필드(`dataset_epoch`·manifest·CM-3.2 hash)는 그대로다 —
    `severity_guard.guarded_identity()`가 같은 manifest·CM-3.2 marker에서 내는 값이라
    그대로 두어야 실제 판정이 돈다.
    """

    root = tmp_path / "guard-markers"
    root.mkdir()
    source = (
        REPOSITORY_ROOT
        / "infra"
        / "bootstrap"
        / "markers"
        / f"{severity_guard.FINAL_ARTIFACT_TYPE}.{TARGET_DATABASE}.json"
    )
    connection = runner._connect(_target(guarded_endpoint))
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT count(*) AS c FROM agent_run")
        rows = int(cursor.fetchone()["c"])
    finally:
        connection.close()
    payload = {
        **manifest_v3._load_json(source),
        "guarded_schema_signature_sha256": _live_guarded_signature(guarded_endpoint),
        "agent_run_rows": rows,
    }
    path = root / f"{severity_guard.FINAL_ARTIFACT_TYPE}.{TARGET_DATABASE}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return root


@pytest.fixture
def guarded_identity(
    guarded_endpoint: Any, guard_marker_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Any:
    """CM-3.3 계보를 **marker 파일에서 매번 다시 읽는다.**

    이전 판은 frozen dict였다. 그래서 복구 뒤 raw signature가 달라지면 테스트가 그
    dict를 직접 고쳐야 했고, 그건 **아직 없던 재발급 경로를 테스트가 대신하는 것**이라
    운영 lifecycle을 증명하지 못했다(구현리뷰 16차 필수 1). 파일에서 읽으면
    production 재발급이 쓴 값을 다음 호출이 그대로 본다.

    실제 `predecessor_identity()`와 같은 필드를 같은 방식으로 유도한다 — 바꾸는 것은
    marker가 놓인 위치뿐이다.
    """

    import db_target

    path = (
        guard_marker_root
        / f"{severity_guard.FINAL_ARTIFACT_TYPE}.{TARGET_DATABASE}.json"
    )

    def _identity(target: Any) -> dict[str, Any]:
        marker = manifest_v3._load_json(path)
        return {
            "predecessor_stage": severity_guard.GUARDED_STAGE,
            "predecessor_marker_sha256": manifest_v3.canonical_payload_sha256(marker),
            "predecessor_schema_signature_sha256": str(
                marker["guarded_schema_signature_sha256"]
            ),
            "target_host_fingerprint": db_target.host_fingerprint(
                target.host, int(target.port)
            ),
        }

    monkeypatch.setattr(runner, "predecessor_identity", _identity)
    return _identity(_target(guarded_endpoint))


@pytest.fixture
def artifact_roots(tmp_path: Path, guarded_endpoint: Any, guarded_identity: Any) -> Any:
    """marker·receipt를 tmp로 보낸다. 저장소 실물을 건드리지 않는다."""

    markers = tmp_path / "markers"
    reports = tmp_path / "reports"
    backups = tmp_path / "backups"
    markers.mkdir()
    reports.mkdir()

    identity = guarded_identity
    approval = _write_backup_evidence(backups, guarded_endpoint, "GH-130")
    # **backup 게이트를 켠 채로 돈다.** 전에는 껐다 — 전환 receipt 계약이
    # `read_inventory(with_content=True)`를 요구했고 그것은 legacy base epoch 컬럼을
    # 읽어 V5 fixture DB에서 성립하지 않았기 때문이다(구현리뷰 3차 필수 3의 한계로
    # 명시했던 부분). checkpoint 전용 계약으로 바꾸면서 그 제약이 사라졌다.
    return {
        "markers": markers,
        "reports": reports,
        "backups": backups,
        "approval": approval,
        "require_backup": True,
        "identity": identity,
    }


def _apply(endpoint: Any, roots: Any, ref: str = "GH-130") -> str:
    return runner.run_apply(
        _target(endpoint),
        change_reference=ref,
        marker_root=roots["markers"],
        report_root=roots["reports"],
        backup_root=roots["backups"],
        approval_path=roots["approval"],
        require_backup=roots["require_backup"],
    )


def test_apply_writes_the_receipt_then_the_marker(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """**marker-last.** receipt가 먼저 COMMITTED가 되어야 복구가 성립한다."""

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"

    receipt = runner.load_receipt("kosa_agent_e2e", root=artifact_roots["reports"])
    marker = runner.load_marker("kosa_agent_e2e", root=artifact_roots["markers"])
    assert receipt is not None and marker is not None
    assert receipt["status"] == "COMMITTED"
    assert marker["status"] == "APPLIED"
    # marker가 증명하는 시각은 receipt가 commit한 시각이다.
    assert marker["applied_at"] == receipt["committed_at"]
    assert marker["catalog_signature_sha256"] == receipt["catalog_signature_sha256"]
    assert (
        marker["predecessor_marker_sha256"]
        == (artifact_roots["identity"]["predecessor_marker_sha256"])
    )


def test_verify_refuses_an_unmarked_database(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """**물리적으로 맞아도 marker가 없으면 완료가 아니다**(구현리뷰 필수 3).

    외부에서 우연히 만든 catalog나 marker 발급 전에 멈춘 상태를 Task 완료로 보면
    안 된다.
    """

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(guarded_endpoint) as connection:
        PostgresSaver(connection).setup()

    target = _target(guarded_endpoint)
    assert (
        runner.run_preflight(target, marker_root=artifact_roots["markers"])
        == "READY_UNMARKED"
    )
    with pytest.raises(runner.CheckpointArtifactError) as caught:
        runner.run_verify(target, marker_root=artifact_roots["markers"])
    assert caught.value.reason_code == "MARKER_MISSING"
    assert caught.value.exit_code == 1


def test_the_marked_state_verifies(guarded_endpoint: Any, artifact_roots: Any) -> None:
    _apply(guarded_endpoint, artifact_roots)
    target = _target(guarded_endpoint)
    assert (
        runner.run_preflight(target, marker_root=artifact_roots["markers"])
        == "READY_MARKED"
    )
    assert (
        runner.run_verify(target, marker_root=artifact_roots["markers"])
        == "READY_MARKED"
    )
    assert _apply(guarded_endpoint, artifact_roots) == "NO_OP"


def test_a_marker_failure_is_recovered_from_the_receipt(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """commit은 됐는데 marker 쓰기가 실패한 경로를 실제로 밟는다."""

    def _boom(*_a: Any, **_k: Any) -> None:
        raise runner.CheckpointArtifactError("marker 저장에 실패했습니다")

    monkeypatch.setattr(runner, "save_marker", _boom)
    with pytest.raises(runner.CheckpointArtifactError):
        _apply(guarded_endpoint, artifact_roots)
    monkeypatch.undo()
    monkeypatch.setattr(
        runner, "predecessor_identity", lambda _db: dict(artifact_roots["identity"])
    )

    receipt = runner.load_receipt("kosa_agent_e2e", root=artifact_roots["reports"])
    assert receipt is not None and receipt["status"] == "COMMITTED"
    assert runner.load_marker("kosa_agent_e2e", root=artifact_roots["markers"]) is None

    assert (
        runner.run_recover_marker(
            _target(guarded_endpoint),
            change_reference="GH-130",
            marker_root=artifact_roots["markers"],
            report_root=artifact_roots["reports"],
        )
        == "RECOVERED"
    )
    marker = runner.load_marker("kosa_agent_e2e", root=artifact_roots["markers"])
    assert marker is not None
    # **receipt가 증명한 값을 그대로 쓴다.**
    assert marker["applied_at"] == receipt["committed_at"]
    assert marker["catalog_signature_sha256"] == receipt["catalog_signature_sha256"]


def test_recovery_refuses_a_drifted_database(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**확인이 marker 쓰기보다 앞이다.** drift가 있으면 증명서를 발급하지 않는다."""

    def _boom(*_a: Any, **_k: Any) -> None:
        raise runner.CheckpointArtifactError("marker 저장에 실패했습니다")

    monkeypatch.setattr(runner, "save_marker", _boom)
    with pytest.raises(runner.CheckpointArtifactError):
        _apply(guarded_endpoint, artifact_roots)
    monkeypatch.undo()
    monkeypatch.setattr(
        runner, "predecessor_identity", lambda _db: dict(artifact_roots["identity"])
    )

    with _connect(guarded_endpoint) as connection:
        connection.cursor().execute(
            "ALTER TABLE checkpoints ADD COLUMN cm34_drift integer"
        )

    with pytest.raises(contract.CheckpointStateError) as caught:
        runner.run_recover_marker(
            _target(guarded_endpoint),
            change_reference="GH-130",
            marker_root=artifact_roots["markers"],
            report_root=artifact_roots["reports"],
        )
    assert caught.value.reason_code == "DRIFT"
    assert runner.load_marker("kosa_agent_e2e", root=artifact_roots["markers"]) is None


def test_apply_leaves_an_aborted_receipt_and_no_marker(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**setup 실패는 ABORTED·marker 0건이다.**

    non-atomic이라 rollback이 없으므로 증적이 무엇을 말하는지가 중요하다.
    """

    from langgraph.checkpoint.postgres import PostgresSaver

    def _boom(self: Any) -> None:
        raise RuntimeError("setup 중단")

    monkeypatch.setattr(PostgresSaver, "setup", _boom)
    with pytest.raises(RuntimeError):
        _apply(guarded_endpoint, artifact_roots)

    receipt = runner.load_receipt("kosa_agent_e2e", root=artifact_roots["reports"])
    assert receipt is not None and receipt["status"] == "ABORTED"
    assert runner.load_marker("kosa_agent_e2e", root=artifact_roots["markers"]) is None


def test_apply_refuses_a_bad_change_reference(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """change ref 형식은 **연결 전에** 본다."""

    for value in ("", "gh-130", "GH130", "GH-"):
        with pytest.raises(runner.CheckpointSetupError):
            runner.run_apply(
                _target(guarded_endpoint),
                change_reference=value,
                backup_root=artifact_roots["backups"],
                approval_path=artifact_roots["approval"],
            )


def test_the_smoke_writes_reads_after_reopen_and_cleans_up(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """**연결을 닫았다 다시 열어 읽는다**(구현리뷰 필수 5).

    같은 연결에서 읽으면 saver 캐시가 답할 수 있어 "PostgreSQL에 남았다"를 증명하지
    못한다.
    """

    _apply(guarded_endpoint, artifact_roots)
    with _connect(guarded_endpoint) as connection:
        before = runner.operational_row_counts(connection.cursor())

    assert (
        runner.run_smoke(
            _target(guarded_endpoint),
            change_reference="GH-130",
            marker_root=artifact_roots["markers"],
        )
        == "OK"
    )

    with _connect(guarded_endpoint) as connection:
        assert runner.operational_row_counts(connection.cursor()) == before


def test_the_smoke_refuses_an_unmarked_database(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """증명서 없는 DB에 쓰기를 하지 않는다."""

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(guarded_endpoint) as connection:
        PostgresSaver(connection).setup()

    with pytest.raises(runner.CheckpointArtifactError):
        runner.run_smoke(
            _target(guarded_endpoint),
            change_reference="GH-130",
            marker_root=artifact_roots["markers"],
        )


def test_a_second_apply_cannot_take_the_lock(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """**session advisory lock**이 동시 실행을 막는다.

    transaction lock은 autocommit에서 매 문장마다 풀려 non-atomic migration 전체를
    덮지 못한다.
    """

    holder = _connect(guarded_endpoint)
    try:
        runner._acquire_session_lock(holder.cursor())
        with pytest.raises(contract.CheckpointStateError) as caught:
            _apply(guarded_endpoint, artifact_roots)
        assert caught.value.reason_code == "LOCK_BUSY"
    finally:
        holder.close()


def test_the_verifier_reads_the_checkpoint_marker(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """full verifier가 `runtime_checkpointed`에서 **무엇을 대조하는지** 고정한다.

    `verify_database()`가 이 helper를 소비한다는 배선은 unit이 AST로 본다. 여기서는
    helper가 live DB에서 판정하는 축을 하나씩 변이해 확인한다 — 3차 필수 1이 지적한
    target fingerprint·predecessor marker hash·`PUBLIC` ACL을 포함한다.
    """

    import verify_bootstrap_state as verifier
    from sqlalchemy import create_engine

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"
    marker = runner.load_marker("kosa_agent_e2e", root=artifact_roots["markers"])
    assert marker is not None
    identity = dict(artifact_roots["identity"])
    monkeypatch.setattr(runner, "load_marker", lambda *a, **k: dict(marker))

    url = (
        f"postgresql+psycopg://{guarded_endpoint.username}:"
        f"{guarded_endpoint.password}@{guarded_endpoint.host}:"
        f"{guarded_endpoint.port}/kosa_agent_e2e"
    )
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            target = _target(guarded_endpoint)

            # marker 없이 물리 상태만 보는 경로는 통과한다.
            assert (
                verifier._checkpoint_mismatches(
                    connection, target, require_marker=False
                )
                == []
            )
            # **정상 marker + 정상 계보는 통과한다.** 실패만 고정하면 "항상 mismatch"
            # 변이가 그대로 green이 된다.
            assert (
                verifier._checkpoint_mismatches(connection, target, require_marker=True)
                == []
            )

            # **identity의 각 축을 변이하면 mismatch다.**
            #
            # 2차는 `target_host_fingerprint`와 선행 schema를 보지 않아서, 같은 이름
            # DB의 **다른 host** marker가 통과했다(3차 필수 1).
            for key in runner.IDENTITY_BOUND_KEYS:
                drifted = {**identity, key: "d" * 64}
                monkeypatch.setattr(
                    runner, "predecessor_identity", lambda _t, d=drifted: dict(d)
                )
                assert verifier._checkpoint_mismatches(
                    connection, target, require_marker=True
                ) == [{"mismatch_kind": "CHECKPOINT_MARKER"}], key
            monkeypatch.setattr(
                runner, "predecessor_identity", lambda _t: dict(identity)
            )

            # **marker가 없으면 stage 도달로 보지 않는다.**
            monkeypatch.setattr(runner, "load_marker", lambda *a, **k: None)
            assert verifier._checkpoint_mismatches(
                connection, target, require_marker=True
            ) == [{"mismatch_kind": "CHECKPOINT_MARKER"}]

            # **marker가 live catalog와 어긋나면 mismatch다.**
            stale = {**marker, "catalog_signature_sha256": "c" * 64}
            monkeypatch.setattr(runner, "load_marker", lambda *a, **k: dict(stale))
            assert verifier._checkpoint_mismatches(
                connection, target, require_marker=True
            ) == [{"mismatch_kind": "CHECKPOINT_MARKER"}]
            monkeypatch.setattr(runner, "load_marker", lambda *a, **k: dict(marker))

        # **사후 `GRANT ... TO PUBLIC`은 schema 단계에서 막힌다.**
        with _connect(guarded_endpoint) as raw:
            raw.cursor().execute("GRANT SELECT ON checkpoints TO PUBLIC")
        with engine.connect() as connection:
            assert verifier._checkpoint_mismatches(
                connection, _target(guarded_endpoint), require_marker=True
            ) == [{"mismatch_kind": "CHECKPOINT_SCHEMA"}]
        with _connect(guarded_endpoint) as raw:
            raw.cursor().execute("REVOKE SELECT ON checkpoints FROM PUBLIC")

        # **catalog가 깨지면 marker를 보기도 전에 schema mismatch다.**
        with _connect(guarded_endpoint) as raw:
            raw.cursor().execute("DROP INDEX checkpoints_thread_id_idx")
        with engine.connect() as connection:
            assert verifier._checkpoint_mismatches(
                connection, _target(guarded_endpoint), require_marker=True
            ) == [{"mismatch_kind": "CHECKPOINT_SCHEMA"}]
    finally:
        engine.dispose()


def test_recovery_holds_the_lock_across_the_marker_write(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**marker를 쓰는 순간에도 lock을 쥐고 있다**(구현리뷰 3차 필수 5).

    2차는 `finally`에서 lock을 푼 **뒤에** `save_marker()`를 했다. 그때 unlock과 save
    사이에 다른 실행이 끼어들 수 있었다. 순서 문자열 검사는 그 결함을 통과시켰으므로
    — docstring에 같은 식별자를 적기만 해도 뒤집힌다 — 여기서는 **저장 시점에 두 번째
    connection이 lock을 잡지 못하는 것**을 본다.
    """

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"
    marker_path = runner.marker_path("kosa_agent_e2e", root=artifact_roots["markers"])
    marker_path.unlink()

    observed: list[bool] = []
    original = runner.save_marker

    def _watch(payload: Any, **kwargs: Any) -> None:
        probe = _connect(guarded_endpoint)
        try:
            # 저장 **직전** 시점에 다른 session이 같은 lock을 잡을 수 있는가.
            observed.append(runner._try_session_lock(probe.cursor()))
        finally:
            probe.close()
        original(payload, **kwargs)

    monkeypatch.setattr(runner, "save_marker", _watch)
    assert (
        runner.run_recover_marker(
            _target(guarded_endpoint),
            change_reference="GH-130",
            marker_root=artifact_roots["markers"],
            report_root=artifact_roots["reports"],
        )
        == "RECOVERED"
    )
    assert observed == [False], "marker 저장 시점에 lock이 풀려 있었다"


def test_recovery_refuses_when_a_marker_already_exists(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """이미 marker가 있으면 두 번째 증적을 만들지 않는다."""

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"
    with pytest.raises(runner.CheckpointArtifactError) as caught:
        runner.run_recover_marker(
            _target(guarded_endpoint),
            change_reference="GH-130",
            marker_root=artifact_roots["markers"],
            report_root=artifact_roots["reports"],
        )
    assert caught.value.reason_code == "MARKER_EXISTS"


def test_smoke_cleans_up_when_put_writes_then_raises(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**`put()`이 쓰고 나서 raise해도 row가 남지 않는다**(구현리뷰 3차 필수 6).

    2차는 `written=True`를 `put()` **반환 뒤에** 세웠다. 그래서 이 경로에서 cleanup을
    건너뛰었고, 회귀는 handler 안의 문자열만 봐서 통과시켰다. 여기서는 실제 saver
    seam에 write-then-raise를 주입하고 **행 수 delta**를 본다.
    """

    from langgraph.checkpoint.postgres import PostgresSaver

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"

    def _counts() -> dict[str, int]:
        connection = _connect(guarded_endpoint)
        try:
            return runner.operational_row_counts(connection.cursor())
        finally:
            connection.close()

    before = _counts()
    original_put = PostgresSaver.put

    def _write_then_raise(self: Any, *args: Any, **kwargs: Any) -> Any:
        original_put(self, *args, **kwargs)
        raise RuntimeError("connection lost after write")

    monkeypatch.setattr(PostgresSaver, "put", _write_then_raise)
    with pytest.raises(RuntimeError, match="connection lost"):
        runner.run_smoke(
            _target(guarded_endpoint),
            change_reference="GH-130",
            marker_root=artifact_roots["markers"],
        )

    assert _counts() == before, "write-then-raise 뒤 smoke row가 남았다"


def test_smoke_reports_both_errors_when_cleanup_also_fails(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cleanup 실패가 원래 원인을 덮지 않는다 — **둘 다 남긴다**."""

    from langgraph.checkpoint.postgres import PostgresSaver

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"

    failed: list[bool] = []

    def _failing_put(*_a: Any, **_k: Any) -> Any:
        # **cleanup 차례가 왔다는 신호다.** 호출 횟수를 세면 run_verify의 연결까지
        # 함께 세어져 어느 연결을 끊는지가 구현 세부에 묶인다.
        failed.append(True)
        raise RuntimeError("put failed")

    monkeypatch.setattr(PostgresSaver, "put", _failing_put)

    def _broken_connect(target: Any, **kwargs: Any) -> Any:
        if failed:
            raise RuntimeError("cleanup connection refused")
        return runner._connect(target, **kwargs)

    with pytest.raises(contract.CheckpointStateError) as caught:
        runner.run_smoke(
            _target(guarded_endpoint),
            change_reference="GH-130",
            connect=_broken_connect,
            marker_root=artifact_roots["markers"],
        )
    assert caught.value.reason_code == "SMOKE_CLEANUP_FAILED"
    # **원래 원인이 chain에 남아 있다.**
    assert caught.value.__cause__ is not None


def _point_verifier_at_the_container(
    endpoint: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """target 해석**만** container로 돌린다.

    `db_target`은 localhost를 공용 bootstrap target으로 **의도적으로 거부한다**(설계
    13.2). 일회용 container는 정의상 localhost이므로 이 한 경계는 대체할 수밖에 없다.

    그 밖의 완료 경로 — stage routing, manifest 대조, marker flag, mismatch 수집,
    예외 변환 — 는 전부 실제 코드가 돈다. 대체 범위를 여기로 좁힌 이유다.
    """

    import verify_bootstrap_state as verifier

    monkeypatch.setattr(
        verifier,
        "load_bootstrap_target",
        lambda database, environ=None: _target(endpoint),
    )


def _checkpoint_candidate_manifest(endpoint: Any) -> dict[str, Any]:
    """등록본의 **checkpoint 계약은 그대로 두고** 데이터 기대만 container에 맞춘다.

    등록본은 공용 DB를 서술한다 — `fdc_trace` 14,400행 같은 값이다. 일회용 container는
    스키마만 세운 빈 DB라 그 기대와 어긋나고, 그 실패는 CM-1.8 데이터 계약의 것이지
    이 Task가 증명하려는 것이 아니다.

    그래서 base·reference table은 live 컬럼 순서와 0행으로 바꾸고, **checkpoint 4종의
    컬럼·정책은 등록본 그대로** 남긴다. 이 테스트가 red가 되어야 하는 이유는 checkpoint
    계약이 깨졌을 때뿐이다.
    """

    import manifest_v3

    registered = manifest_v3._load_json(
        REPOSITORY_ROOT
        / "infra"
        / "bootstrap"
        / "manifests"
        / f"runtime.{runner.CHECKPOINT_STAGE}.json"
    )
    connection = runner._connect(_target(endpoint))
    try:
        cursor = connection.cursor()
        tables: dict[str, Any] = {}
        for name, entry in registered["tables"].items():
            if name in contract.CHECKPOINT_TABLES:
                tables[name] = entry
                continue
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s "
                "ORDER BY ordinal_position",
                (name,),
            )
            columns = [str(row["column_name"]) for row in cursor.fetchall()]
            if entry["verification_policy"] == "schema_only":
                # schema_only entry는 행 수·content hash를 갖지 않는다.
                tables[name] = {
                    "columns": columns,
                    "verification_policy": "schema_only",
                }
                continue
            cursor.execute(f'SELECT count(*) AS c FROM "{name}"')
            tables[name] = {
                "columns": columns,
                "verification_policy": "bootstrap_empty",
                "row_count": int(cursor.fetchone()["c"]),
                "content_hash": manifest_v3.hash_canonical_rows([]),
            }
    finally:
        connection.close()
    return {**registered, "tables": tables}


def test_the_full_verifier_passes_on_the_checkpointed_stage(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**실제 `verify_database()`를 호출해** PASS를 확인한다(구현리뷰 3차 필수 1).

    2차 보완은 helper를 직접 부르는 container 회귀와 AST 배선 회귀를 나눠 두었다. 두
    축을 논리적으로 합치는 방식은 stage routing·manifest·marker flag·예외 변환이 함께
    도는 **완료 경로**를 한 번도 실행하지 않는다. CM-2.7 필수 I-3과 같은 지적이다.

    routing 또는 helper 소비 한 줄을 지우면 red여야 한다 — 그것을 마지막에 확인한다.
    """

    import verify_bootstrap_state as verifier

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"
    marker = runner.load_marker("kosa_agent_e2e", root=artifact_roots["markers"])
    assert marker is not None
    monkeypatch.setattr(runner, "load_marker", lambda *a, **k: dict(marker))

    _point_verifier_at_the_container(guarded_endpoint, monkeypatch)
    candidate = _checkpoint_candidate_manifest(guarded_endpoint)
    try:
        result = verifier.verify_database(
            "kosa_agent_e2e",
            verifier.CHECKPOINT_STAGE,
            environ={},
            candidate=candidate,
            require_runtime_marker=True,
        )
    except verifier.AcceptanceMismatchError as exc:  # pragma: no cover - 진단용
        raise AssertionError(exc.details["mismatches"]) from exc
    assert result.status == verifier.STATUS_PASS

    # **helper 소비를 끊으면 red다.** 배선이 살아 있다는 것을 결과로 증명한다.
    monkeypatch.setattr(runner, "load_marker", lambda *a, **k: None)
    with pytest.raises(verifier.AcceptanceMismatchError) as caught:
        verifier.verify_database(
            "kosa_agent_e2e",
            verifier.CHECKPOINT_STAGE,
            environ={},
            candidate=candidate,
            require_runtime_marker=True,
        )
    assert {"mismatch_kind": "CHECKPOINT_MARKER"} in caught.value.details["mismatches"]


def test_a_stale_marker_is_refused_instead_of_reported_as_no_op(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**형식만 유효한 marker로 `NO_OP`이 되지 않는다**(구현리뷰 2·3차 필수 2).

    초판은 marker 존재만 보고 `READY_MARKED`를 냈고, 2차 보완도 catalog signature와
    predecessor marker hash까지만 봤다. `target_host_fingerprint`가 다른 marker —
    즉 **같은 이름의 다른 서버**를 가리키는 증적 — 는 여전히 통과했다.

    회귀도 소스 문자열이었다. 여기서는 축을 하나씩 변조해 `run_apply`와
    `run_preflight`가 실제로 무엇을 내는지 본다.
    """

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"
    target = _target(guarded_endpoint)
    assert _apply(guarded_endpoint, artifact_roots) == "NO_OP"

    marker_file = runner.marker_path("kosa_agent_e2e", root=artifact_roots["markers"])
    original = manifest_v3._load_json(marker_file)
    identity = dict(artifact_roots["identity"])

    for key in (*runner.IDENTITY_BOUND_KEYS, "catalog_signature_sha256"):
        monkeypatch.setattr(
            runner, "load_marker", lambda *a, k=key, **kw: {**original, k: "d" * 64}
        )
        assert (
            runner.run_preflight(target, marker_root=artifact_roots["markers"])
            == "MARKER_DRIFT"
        )
        with pytest.raises(contract.CheckpointStateError) as caught:
            _apply(guarded_endpoint, artifact_roots)
        assert caught.value.reason_code == "MARKER_DRIFT", key
    assert identity["predecessor_stage"] == "runtime_guarded"


@pytest.mark.parametrize(
    ("label", "statement"),
    [
        # reference column type — R03 컬럼 계약
        (
            "r03_column",
            "ALTER TABLE r03_alarm_history DROP COLUMN member_wafer_refs",
        ),
        # RAG column type — `vector(1024)`가 계약이다
        (
            "rag_embedding",
            "ALTER TABLE document_chunk ALTER COLUMN embedding TYPE text",
        ),
        # nullability
        ("rag_nullability", "ALTER TABLE document ALTER COLUMN title DROP NOT NULL"),
        # final View — `V5-CM-3.1`의 `v_alarm_event`
        ("final_view", "DROP VIEW IF EXISTS v_alarm_event CASCADE"),
        # 계약 밖 table
        ("stray_table", "CREATE TABLE public.cm34_stray (id integer PRIMARY KEY)"),
        # --- 5차 필수 1 — base 9 물리 계약 -------------------------------------
        ("base_type", "ALTER TABLE fdc_trace ALTER COLUMN value TYPE text"),
        (
            "base_nullability",
            "ALTER TABLE lot_history ALTER COLUMN wafer_no DROP NOT NULL",
        ),
        ("base_order", "ALTER TABLE dim_parameter ADD COLUMN cm34_tail integer"),
        ("base_primary_key", "ALTER TABLE metrology DROP CONSTRAINT metrology_pkey"),
        # --- 5차 필수 2 — reference/RAG object 계약 -----------------------------
        (
            "rag_unique",
            "ALTER TABLE document_chunk "
            "DROP CONSTRAINT document_chunk_doc_id_chunk_seq_key",
        ),
        (
            "rag_foreign_key",
            "ALTER TABLE document_chunk DROP CONSTRAINT document_chunk_doc_id_fkey",
        ),
        (
            "rag_primary_key",
            "ALTER TABLE document_chunk DROP CONSTRAINT document_chunk_pkey CASCADE",
        ),
        (
            "document_check",
            "ALTER TABLE document DROP CONSTRAINT document_doc_type_check",
        ),
        (
            "rag_default",
            "ALTER TABLE document ALTER COLUMN created_at DROP DEFAULT",
        ),
        # dict 비교였을 때 **순서를 잃던** 자리
        (
            "nl_query_log_order",
            "ALTER TABLE nl_query_log ADD COLUMN cm34_tail integer",
        ),
        # --- 6차 필수 1 — base 9 object 계약 -----------------------------------
        (
            "base_foreign_key",
            "ALTER TABLE fdc_trace DROP CONSTRAINT fdc_trace_parameter_id_fkey",
        ),
        (
            "base_check",
            "ALTER TABLE metrology DROP CONSTRAINT metrology_alarm_result_check",
        ),
        ("base_index_drop", "DROP INDEX ix_evaluation_type"),
        # **같은 이름으로 잘못 재생성**해도 걸려야 한다 — 이름만 세면 통과한다.
        (
            "base_index_redefined",
            "DROP INDEX ix_evaluation_type; "
            "CREATE INDEX ix_evaluation_type ON evaluation (lot_hist_id)",
        ),
        # --- 6차 필수 2 — nl_query_log object 계약 ------------------------------
        (
            "nl_query_log_primary_key",
            "ALTER TABLE nl_query_log DROP CONSTRAINT nl_query_log_pkey CASCADE",
        ),
        (
            "nl_query_log_check",
            "ALTER TABLE nl_query_log DROP CONSTRAINT nl_query_log_row_cnt_check",
        ),
        (
            "nl_query_log_default",
            "ALTER TABLE nl_query_log ALTER COLUMN nl_query_log_id DROP DEFAULT",
        ),
        # --- 7차 필수 1 — 개수·조각이 아니라 **정의**를 본다 ---------------------
        #
        # 아래 넷은 전부 `p:1, c:4`와 outcome 문자열 4개를 그대로 유지한다. 개수와
        # 조각만 세던 초판은 넷 다 통과시켰다.
        (
            "nl_query_log_pk_wrong_column",
            "ALTER TABLE nl_query_log DROP CONSTRAINT nl_query_log_pkey CASCADE; "
            "ALTER TABLE nl_query_log "
            "ADD CONSTRAINT nl_query_log_pkey PRIMARY KEY (question)",
        ),
        (
            "nl_query_log_row_cnt_inverted",
            "ALTER TABLE nl_query_log DROP CONSTRAINT nl_query_log_row_cnt_check; "
            "ALTER TABLE nl_query_log "
            "ADD CONSTRAINT nl_query_log_row_cnt_check CHECK (row_cnt <= 0)",
        ),
        (
            "nl_query_log_latency_replaced",
            "ALTER TABLE nl_query_log DROP CONSTRAINT nl_query_log_latency_ms_check; "
            "ALTER TABLE nl_query_log ADD CONSTRAINT "
            "nl_query_log_latency_ms_check CHECK (latency_ms IS NOT NULL)",
        ),
        (
            # **`OWNED BY NONE`만 한다.** default를 함께 지우면 기존 default 분기에서
            # 이미 red가 되어, 새 ownership 검사가 Gate라는 증명이 못 된다
            # (구현리뷰 8차 검증 필수 1).
            "nl_query_log_sequence_disowned",
            "ALTER SEQUENCE nl_query_log_nl_query_log_id_seq OWNED BY NONE",
        ),
        (
            # **이름과 개수는 그대로 두고 의미만 바꾼다.** `c:4`도 outcome 문자열도
            # 유지되므로, 정의 전체를 보지 않으면 통과한다.
            "nl_query_log_combination_check_replaced",
            "ALTER TABLE nl_query_log DROP CONSTRAINT nl_query_log_check; "
            "ALTER TABLE nl_query_log ADD CONSTRAINT nl_query_log_check "
            "CHECK (outcome IN ('SUCCESS','POLICY_REJECTED',"
            "'VALIDATION_FAILED','DB_ERROR'))",
        ),
    ],
)
def test_setup_is_not_called_when_the_predecessor_has_drifted(
    guarded_endpoint: Any,
    artifact_roots: Any,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    statement: str,
) -> None:
    """**drift한 DB에서는 `setup()`을 시작조차 하지 않는다**(구현리뷰 4차 필수 1).

    3차까지의 선행 확인은 Runtime 9종과 22-table **이름** allowlist만 봤다. 그래서
    `document_chunk.embedding` type이나 R03 컬럼, final View가 drift해도 non-atomic
    `PostgresSaver.setup()`이 시작될 수 있었다. full verifier가 그것을 잡는 시점은
    **rollback이 불가능해진 뒤**다.

    호출 여부를 세는 것이 핵심이다 — "실패했다"만 보면 setup이 절반 돌고 실패한
    경우와 구분되지 않는다.
    """

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(guarded_endpoint) as raw:
        cursor = raw.cursor()
        # psycopg는 prepared statement에 여러 명령을 넣지 못한다 — `;`로 나눈다.
        for part in filter(None, (s.strip() for s in statement.split(";"))):
            cursor.execute(part)

    calls: list[int] = []
    monkeypatch.setattr(
        PostgresSaver,
        "setup",
        lambda self: calls.append(1),  # noqa: ARG005
    )
    with pytest.raises(contract.CheckpointStateError) as caught:
        _apply(guarded_endpoint, artifact_roots)
    assert caught.value.reason_code == "PREDECESSOR_DRIFT", label
    assert calls == [], f"{label}: setup()이 호출됐다"


def test_public_grants_are_refused_beyond_the_crud_four(
    guarded_endpoint: Any, artifact_roots: Any
) -> None:
    """**PUBLIC 권한을 열거하지 않는다**(구현리뷰 4차 필수 2).

    초판은 `SELECT·INSERT·UPDATE·DELETE` 넷만 OR로 봤다. `TRUNCATE`·`REFERENCES`·
    `TRIGGER`와 column 단위 grant는 남아도 통과했다 — 문서가 말하는 "PUBLIC 0건"보다
    좁은 검사였다.
    """

    import verify_bootstrap_state as verifier
    from sqlalchemy import create_engine

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"
    url = (
        f"postgresql+psycopg://{guarded_endpoint.username}:"
        f"{guarded_endpoint.password}@{guarded_endpoint.host}:"
        f"{guarded_endpoint.port}/kosa_agent_e2e"
    )
    engine = create_engine(url)
    try:
        for grant, revoke in (
            (
                "GRANT TRUNCATE ON checkpoints TO PUBLIC",
                "REVOKE TRUNCATE ON checkpoints FROM PUBLIC",
            ),
            (
                "GRANT REFERENCES ON checkpoint_blobs TO PUBLIC",
                "REVOKE REFERENCES ON checkpoint_blobs FROM PUBLIC",
            ),
            (
                "GRANT TRIGGER ON checkpoint_writes TO PUBLIC",
                "REVOKE TRIGGER ON checkpoint_writes FROM PUBLIC",
            ),
            (
                "GRANT SELECT (thread_id) ON checkpoints TO PUBLIC",
                "REVOKE SELECT (thread_id) ON checkpoints FROM PUBLIC",
            ),
        ):
            with _connect(guarded_endpoint) as raw:
                raw.cursor().execute(grant)

            connection = runner._connect(_target(guarded_endpoint))
            try:
                cursor = connection.cursor()
                # **소비 경로가 쓰는 그 판정을 그대로 부른다.** 전용 wrapper를 두면
                # 회귀만 통과하는 두 번째 계약이 생긴다.
                catalog = runner.read_catalog(cursor)
                with pytest.raises(contract.CheckpointStateError) as caught:
                    contract.assert_checkpoint_acl(catalog)
                assert caught.value.reason_code == "ACL_PUBLIC", grant
                # 상태 판정 자체가 이미 이것을 `DRIFT`로 접는다.
                assert contract.classify_state(catalog) == "DRIFT", grant
            finally:
                connection.close()

            # **full verifier도 같이 red다.**
            with engine.connect() as connection:
                assert verifier._checkpoint_mismatches(
                    connection, _target(guarded_endpoint), require_marker=True
                ) == [{"mismatch_kind": "CHECKPOINT_SCHEMA"}], grant

            with _connect(guarded_endpoint) as raw:
                raw.cursor().execute(revoke)
    finally:
        engine.dispose()


def test_a_clean_guarded_fixture_passes_the_exact_projection(
    guarded_endpoint: Any,
) -> None:
    """**정상 상태는 `[]`다**(구현리뷰 5차 필수 1·2 양성 회귀).

    실패만 고정하면 "항상 mismatch" 변이가 green이 된다. 실제로 이 계약을 넓히는
    동안 두 번 오탐이 났다 — R03 registry가 V4 11컬럼이라 정상 DB가 걸렸고, 기본값이
    `'OOC'`와 `'OOC'::character varying`으로 갈려 또 걸렸다. **양성 회귀가 없었으면
    변이 테스트가 엉뚱한 이유로 통과하는 것을 못 봤다.**
    """

    connection = runner._connect(_target(guarded_endpoint))
    try:
        assert runner.reference_physical_mismatches(connection.cursor()) == []
    finally:
        connection.close()


def test_the_default_canonicalization_folds_only_representation() -> None:
    """표현만 접고 **값이 다른 것은 그대로 둔다**.

    구현리뷰 6차 권장 1. 초판은 첫 `::`부터 잘라
    `nextval('nl_query_log_id_seq'::regclass)`를 `nextval('nl_query_log_id_seq'`로
    **손상시켰다.** 회귀가 `startswith("nextval(")`만 봐서 그 손상을 통과시켰다 —
    문자열의 일부만 확인하는 단언은 손상을 못 잡는다.
    """

    fold = runner._canonical_default

    # quoted literal(+선택적 cast)만 접는다.
    assert fold("'OOC'::character varying") == "OOC"
    assert fold("'OOC'") == "OOC"
    assert fold("'it''s'::text") == "it's"
    # 리터럴 **안의** `::`는 값의 일부다.
    assert fold("'a::b'::text") == "a::b"
    assert fold(None) is None

    # **함수 표현은 입력과 exact 동일하다.** startswith가 아니라 전체를 본다.
    for expression in (
        "now()",
        "nextval('nl_query_log_nl_query_log_id_seq'::regclass)",
        "(now() AT TIME ZONE 'utc')",
        "0",
    ):
        assert fold(expression) == expression, expression

    # 서로 다른 값은 접힌 뒤에도 달라야 한다.
    assert fold("'OOC'::character varying") != fold("'OOS'::character varying")


def test_the_rag_runner_verifies_its_own_objects() -> None:
    """DDL 소유 모듈의 `run_apply()`가 **두 verifier를 모두** 부른다.

    구현리뷰 6차 권장 2. CM-3.4는 `verify_rag_objects()`를 직접 부르므로 checkpoint
    gate는 동작했지만, B의 production runner는 여전히 column만 봤다 — 구현보고가 말한
    "소유 모듈이 자기 산출물을 검증한다"와 실제가 갈려 있었다.
    """

    import inspect

    import apply_rag_schema as rag

    source = inspect.getsource(rag.run_apply)
    assert "verify_rag_schema(connection)" in source
    assert "verify_rag_objects(connection)" in source
    # 두 호출 모두 `engine.begin()` 안이어야 실패가 rollback된다.
    assert source.index("engine.begin()") < source.index("verify_rag_objects")


def test_the_rag_object_verifier_fails_the_transaction(
    guarded_endpoint: Any,
) -> None:
    """constraint가 빠지면 `verify_rag_objects()`가 실제로 막는다."""

    import apply_rag_schema as rag

    connection = runner._connect(_target(guarded_endpoint))
    try:
        adapter = runner._SignatureConnection(connection.cursor())
        rag.verify_rag_objects(adapter)  # 정상 fixture는 통과한다.

        connection.cursor().execute(
            "ALTER TABLE document_chunk "
            "DROP CONSTRAINT document_chunk_doc_id_chunk_seq_key"
        )
        with pytest.raises(rag.RagSchemaError, match="constraint"):
            rag.verify_rag_objects(adapter)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        # --- nl_query_log — 각 축이 **자기 code로** 걸린다 ---------------------
        (
            "ALTER SEQUENCE nl_query_log_nl_query_log_id_seq OWNED BY NONE",
            ["nl_query_log_sequence_ownership"],
        ),
        (
            "ALTER TABLE nl_query_log ALTER COLUMN nl_query_log_id DROP DEFAULT",
            ["nl_query_log_default"],
        ),
        (
            "ALTER TABLE nl_query_log DROP CONSTRAINT nl_query_log_row_cnt_check; "
            "ALTER TABLE nl_query_log "
            "ADD CONSTRAINT nl_query_log_row_cnt_check CHECK (row_cnt <= 0)",
            ["nl_query_log_constraints"],
        ),
        (
            "ALTER TABLE nl_query_log DROP CONSTRAINT nl_query_log_check; "
            "ALTER TABLE nl_query_log ADD CONSTRAINT nl_query_log_check "
            "CHECK (outcome IN ('SUCCESS','POLICY_REJECTED',"
            "'VALIDATION_FAILED','DB_ERROR'))",
            ["nl_query_log_constraints"],
        ),
        # --- base·RAG — 인접 축이 함께 울리지 않는다 ---------------------------
        (
            "ALTER TABLE fdc_trace DROP CONSTRAINT fdc_trace_parameter_id_fkey",
            ["base_constraint:fdc_trace"],
        ),
        ("DROP INDEX ix_evaluation_type", ["base_index:evaluation"]),
        (
            "ALTER TABLE document_chunk "
            "DROP CONSTRAINT document_chunk_doc_id_chunk_seq_key",
            ["rag_schema"],
        ),
        (
            "ALTER TABLE document ALTER COLUMN created_at DROP DEFAULT",
            ["rag_schema"],
        ),
    ],
)
def test_each_drift_reports_its_own_mismatch_code(
    guarded_endpoint: Any, statement: str, expected: list[str]
) -> None:
    """변이마다 **어느 검사가 잡았는지**까지 고정한다(구현리뷰 8차 검증 필수 1).

    `setup()` 호출 0회만 보면 "무언가가 막았다"까지만 알 수 있다. 그러면 새로 넣은
    검사를 통째로 지워도 **다른 선행 분기가 대신 red를 내서** 회귀가 계속 green이다.
    실제로 8차가 그것을 지적했다 — `OWNED BY NONE`과 `DROP DEFAULT`를 함께 걸어서,
    ownership 검사를 지워도 default 분기가 대신 울렸다.

    깨끗한 상태에서 시작해 한 문장만 바꾸고, 결과 code 집합이 **정확히** 기대와 같은지
    본다. 인접 축이 함께 울리면 그것도 실패다.
    """

    connection = runner._connect(_target(guarded_endpoint))
    try:
        cursor = connection.cursor()
        assert (
            runner.reference_physical_mismatches(cursor) == []
        ), "fixture가 이미 drift"
        for part in filter(None, (s.strip() for s in statement.split(";"))):
            cursor.execute(part)
        assert runner.reference_physical_mismatches(cursor) == expected
    finally:
        connection.close()


def _write_recovery_approval(
    root: Path, change_ref: str, *, recovery_approved: bool = True
) -> Path:
    """복구 승인. 적용 승인과 **같은 파일 형식**이되 flag가 다르다."""

    import json

    path = root / "recovery_approval.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": runner.APPROVAL_ARTIFACT_TYPE,
                "format_version": runner.MARKER_FORMAT_VERSION,
                "task_id": runner.TASK_ID,
                "dataset_epoch": runner._dataset_epoch()["dataset_epoch"],
                "change_reference": change_ref,
                "status": "APPROVED",
                "targets": ["kosa_agent_e2e", "kosa_agent"],
                "from_stage": runner.GUARDED_STAGE,
                "to_stage": runner.CHECKPOINT_STAGE,
                "package_name": contract.PACKAGE_NAME,
                "package_version": contract.PACKAGE_VERSION,
                "migration_digest_sha256": contract.MIGRATION_DIGEST_SHA256,
                "recovery_approved": recovery_approved,
                "approved_at": "2026-08-25T12:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )
    return path


def _grant_distinct_security(endpoint: Any) -> None:
    """원본에 **다른 owner와 비-PUBLIC GRANT**를 만든다.

    이전 회귀는 source와 restore가 같은 단일 role만 썼고 별도 GRANT가 없어,
    `--no-owner --no-privileges` archive가 보안 상태를 통째로 버려도 4축이 같았다
    (구현리뷰 11차 필수 1). 그 결함을 드러내려면 원본이 먼저 달라야 한다.
    """

    with _connect(endpoint) as raw:
        cursor = raw.cursor()
        for statement in (
            "CREATE ROLE cm34_owner NOLOGIN",
            "CREATE ROLE cm34_reader NOLOGIN",
            # **View는 소유자 권한으로 실행된다.** 소유자를 옮기기 전에 하위
            # table SELECT를 주지 않으면 `v_alarm_event`가 누구에게도 열리지 않아
            # guarded 계약 자체가 깨진다 — 보안 drift fixture가 만들려던 상태가
            # 아니다. `ALL TABLES`는 View까지 포함하므로 base table만 지명한다.
            "GRANT SELECT ON TABLE trace_alarm_history, summary_alarm_history, "
            "fdc_trace, summary_data, lot_history, metrology, evaluation, "
            "dim_parameter TO cm34_owner",
            # R03·View는 CM-3.1이 owner·ACL을 고정한 대상이다.
            "ALTER TABLE r03_alarm_history OWNER TO cm34_owner",
            "ALTER VIEW v_alarm_event OWNER TO cm34_owner",
            "GRANT SELECT ON r03_alarm_history TO cm34_reader",
            "GRANT SELECT (agent_run_id) ON agent_run TO cm34_reader",
            "GRANT USAGE ON SEQUENCE nl_query_log_nl_query_log_id_seq TO cm34_reader",
        ):
            cursor.execute(statement)


def test_run_backup_preserves_owner_and_acl(
    guarded_endpoint: Any, tmp_path: Path, guarded_identity: Any
) -> None:
    """**owner·비-PUBLIC ACL까지 복원된다**(구현리뷰 11차 필수 1).

    `PARTIAL`의 유일한 복구 수단이라면 복원본이 스키마·데이터뿐 아니라 **원래 주인과
    원래 권한**까지 원본이어야 한다.
    """

    import checkpoint_backup as cbackup

    _grant_distinct_security(guarded_endpoint)
    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)

    receipt = cbackup.run_backup(
        _target(guarded_endpoint),
        change_reference="GH-130",
        backup_root=root,
    )
    assert receipt["restore_verified"] is True
    # security 축이 실제로 값을 담고 있고 양쪽이 같다.
    assert (
        receipt["source_projection"]["security_sha256"]
        == receipt["restored_projection"]["security_sha256"]
    )


@pytest.mark.parametrize(
    "statement",
    [
        # owner 변조
        "ALTER TABLE r03_alarm_history OWNER TO cm34_reader",
        # 비-PUBLIC GRANT 제거
        "REVOKE SELECT ON r03_alarm_history FROM cm34_reader",
        # column ACL 제거
        "REVOKE SELECT (agent_run_id) ON agent_run FROM cm34_reader",
        # sequence ACL 제거
        "REVOKE USAGE ON SEQUENCE nl_query_log_nl_query_log_id_seq FROM cm34_reader",
    ],
)
def test_security_drift_changes_the_projection(
    guarded_endpoint: Any, statement: str
) -> None:
    """owner·ACL을 하나라도 바꾸면 security 축이 달라진다.

    복원본 음성 변이의 근거다 — 이 축이 둔하면 보안 상태가 사라져도 `restore_verified`가
    참이 된다.
    """

    import checkpoint_backup as cbackup

    _grant_distinct_security(guarded_endpoint)
    connection = runner._connect(_target(guarded_endpoint))
    try:
        cursor = connection.cursor()
        before = cbackup.observe_shape(cursor)
        cursor.execute(statement)
        after = cbackup.observe_shape(cursor)
    finally:
        connection.close()

    assert before["security_sha256"] != after["security_sha256"], statement
    # **다른 축은 흔들리지 않는다** — 어느 축이 달라졌는지가 값으로 드러나야 한다.
    for axis in cbackup.SHAPE_KEYS:
        if axis != "security_sha256":
            assert before[axis] == after[axis], axis


def test_run_backup_proves_the_restore(
    guarded_endpoint: Any, tmp_path: Path, guarded_identity: Any
) -> None:
    """**production 경로를 실제로 돌린다**(구현리뷰 10차 필수 2).

    지금까지 `run_backup()`을 호출하는 테스트가 저장소에 **0개**였다. container 72건
    통과는 setup·marker·smoke의 실증이지, pinned client image·mount·`pg_dump`·
    `pg_restore`·두 endpoint 연결·관측 함수가 함께 도는 증명이 아니었다.

    여기서는 guarded fixture를 원본으로 두고 archive 생성 → 별도 PostgreSQL 복원 →
    **4축 projection 일치** → completion-last → `load_evidence()` 재검산까지 한 번에
    확인한다.
    """

    import checkpoint_backup as cbackup

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)

    receipt = cbackup.run_backup(
        _target(guarded_endpoint),
        change_reference="GH-130",
        backup_root=root,
    )

    # **관측 결과다.** 4축이 전부 같을 때만 True가 된다.
    assert receipt["restore_verified"] is True
    assert receipt["source_projection"] == receipt["restored_projection"]
    assert set(receipt["source_projection"]) == set(cbackup.SHAPE_KEYS)

    # completion-last — 실물 3본이 모두 있고 digest가 맞는다.
    reloaded = cbackup.load_evidence("kosa_agent_e2e", "GH-130", backup_root=root)
    assert reloaded["archive_sha256"] == receipt["archive_sha256"]
    assert len(reloaded["_verified"]["receipt_sha256"]) == 64

    # 임시 파일이 남지 않는다.
    assert not list(root.glob(".*partial"))

    # **적용 직전 형상과 결속한다.** 같은 함수로 잰 값이어야 통과한다.
    connection = runner._connect(_target(guarded_endpoint))
    try:
        shape = cbackup.observe_shape(connection.cursor())
    finally:
        connection.close()
    cbackup.assert_receipt_matches(
        reloaded,
        target=_target(guarded_endpoint),
        shape=shape,
        host_fingerprint=receipt["target_host_fingerprint"],
    )


def test_run_backup_leaves_no_residue_when_the_dump_fails(
    guarded_endpoint: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guarded_identity: Any,
) -> None:
    """`pg_dump`가 **partial 파일을 남기고 실패**해도 재시도가 막히지 않는다.

    구현리뷰 10차 필수 3. 초판은 dump가 정리 경계 **밖**이라 partial archive가 남고
    다음 실행이 `archive.exists()`에서 영구히 거부됐다.
    """

    import checkpoint_backup as cbackup
    import postgres_backup as backup

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)

    original = backup.run_command

    def _fail_on_dump(argv: Any, **kwargs: Any) -> Any:
        if any("pg_dump" == part for part in argv) and "--file" in argv:
            # 실제 dump처럼 파일을 만든 **뒤** 실패한다.
            (root / argv[argv.index("--file") + 1].rsplit("/", 1)[-1]).write_bytes(
                b"partial"
            )
            raise backup.BackupError("BACKUP_FAILED", backup.EXIT_MISMATCH)
        return original(argv, **kwargs)

    monkeypatch.setattr(backup, "run_command", _fail_on_dump)
    with pytest.raises(backup.BackupError):
        cbackup.run_backup(
            _target(guarded_endpoint),
            change_reference="GH-130",
            backup_root=root,
        )

    # **잔재 0건** — 임시도 최종도 남지 않는다.
    assert list(root.iterdir()) == [], sorted(p.name for p in root.iterdir())


def test_recovery_returns_a_partial_target_to_the_predecessor(
    guarded_endpoint: Any, tmp_path: Path, guarded_identity: Any
) -> None:
    """**의도적 `PARTIAL`을 실제로 원복한다**(구현리뷰 11차 필수 2).

    "자동 resume하지 않고 backup restore로 복구한다"는 계획은, 그 restore를 수행하는
    경로가 없으면 장애 시 수행할 수 없는 문장이다. 여기서는 backup → PARTIAL 유도 →
    복구 → `preflight=ABSENT`까지 한 번에 확인한다.

    `pg_restore --clean`만으로는 부족하다 — predecessor archive에 checkpoint 4종이
    없으므로 그 object는 지워지지 않고 `PARTIAL`이 남는다.
    """

    import checkpoint_backup as cbackup

    _grant_distinct_security(guarded_endpoint)
    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    target = _target(guarded_endpoint)

    receipt = cbackup.run_backup(target, change_reference="GH-130", backup_root=root)
    assert receipt["restore_verified"] is True

    # --- 의도적으로 PARTIAL을 만든다 -------------------------------------------
    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(guarded_endpoint) as raw:
        PostgresSaver(raw).setup()
        # 부분 상태 — index 하나를 지워 `READY`가 아니게 만든다.
        raw.cursor().execute("DROP INDEX checkpoints_thread_id_idx")
    assert runner.run_preflight(target, marker_root=tmp_path / "m") == "PARTIAL"

    # --- 복구 --------------------------------------------------------------
    approval = _write_recovery_approval(root, "GH-130")
    result = cbackup.run_recover(
        target,
        change_reference="GH-130",
        backup_root=root,
        approval_path=approval,
    )
    assert result["archive_sha256"] == receipt["archive_sha256"]

    # --- 원복 확인 ----------------------------------------------------------
    assert runner.run_preflight(target, marker_root=tmp_path / "m") == "ABSENT"
    connection = runner._connect(target)
    try:
        shape = cbackup.observe_shape(connection.cursor())
    finally:
        connection.close()
    # **owner·ACL 포함 5축이 backup 시점과 같다.**
    assert shape == receipt["source_projection"]


def test_recovery_refuses_without_a_recovery_approval(
    guarded_endpoint: Any, tmp_path: Path, guarded_identity: Any
) -> None:
    """적용 승인이 자동으로 복구 승인이 되지 않는다."""

    import checkpoint_backup as cbackup

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    cbackup.run_backup(
        _target(guarded_endpoint), change_reference="GH-130", backup_root=root
    )
    approval = _write_recovery_approval(root, "GH-130", recovery_approved=False)
    with pytest.raises(cbackup.CheckpointBackupError) as caught:
        cbackup.run_recover(
            _target(guarded_endpoint),
            change_reference="GH-130",
            backup_root=root,
            approval_path=approval,
        )
    assert caught.value.reason_code == "RECOVERY_NOT_APPROVED"


def test_run_backup_prepares_a_column_only_role(
    guarded_endpoint: Any, tmp_path: Path, guarded_identity: Any
) -> None:
    """**relation GRANT가 없는 column-only role**도 복원 환경에 준비된다.

    구현리뷰 12차 필수 1. 이전 회귀의 `cm34_reader`는 relation GRANT와 column GRANT를
    둘 다 받아 relation 쪽에서 **우연히** role이 만들어졌다. column GRANT만 가진 role은
    inventory에서 빠져 `pg_restore`가 실패한다.
    """

    import checkpoint_backup as cbackup

    with _connect(guarded_endpoint) as raw:
        cursor = raw.cursor()
        cursor.execute("CREATE ROLE cm34_col_only NOLOGIN")
        # **relation GRANT는 주지 않는다.** column 하나뿐이다.
        cursor.execute("GRANT SELECT (lot_id) ON agent_run TO cm34_col_only")

    connection = runner._connect(_target(guarded_endpoint))
    try:
        roles = cbackup.source_roles(connection.cursor())
    finally:
        connection.close()
    assert "cm34_col_only" in roles, "column-only role이 inventory에 없다"

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    receipt = cbackup.run_backup(
        _target(guarded_endpoint), change_reference="GH-130", backup_root=root
    )
    assert receipt["restore_verified"] is True


@pytest.mark.parametrize(
    "statement",
    [
        # grant option — 이전 projection은 이것을 잃었다.
        "GRANT SELECT ON r03_alarm_history TO cm34_reader WITH GRANT OPTION",
        # 같은 privilege를 다른 grantor가 준 경우
        "REVOKE SELECT ON r03_alarm_history FROM cm34_reader",
    ],
)
def test_grant_option_changes_the_security_axis(
    guarded_endpoint: Any, statement: str
) -> None:
    """`WITH GRANT OPTION` 차이가 security 축을 바꾼다(구현리뷰 12차 필수 1).

    grantee·privilege만 담으면 아래 둘이 같은 projection이 된다.

    ```sql
    GRANT SELECT ON x TO r;
    GRANT SELECT ON x TO r WITH GRANT OPTION;
    ```
    """

    import checkpoint_backup as cbackup

    _grant_distinct_security(guarded_endpoint)
    connection = runner._connect(_target(guarded_endpoint))
    try:
        cursor = connection.cursor()
        before = cbackup.observe_shape(cursor)
        cursor.execute(statement)
        after = cbackup.observe_shape(cursor)
    finally:
        connection.close()

    assert before["security_sha256"] != after["security_sha256"], statement
    for axis in cbackup.SHAPE_KEYS:
        if axis != "security_sha256":
            assert before[axis] == after[axis], axis


@pytest.mark.parametrize("prepare", ["absent", "ready"])
def test_recovery_refuses_a_non_partial_target(
    guarded_endpoint: Any, tmp_path: Path, prepare: str, guarded_identity: Any
) -> None:
    """**`PARTIAL`이 아니면 DB를 건드리지 않는다**(구현리뷰 12차 필수 2).

    초판은 상태 판정 없이 바로 `DROP ... CASCADE`했다. `ABSENT`는 되돌릴 것이 없고
    `READY_MARKED`는 정상 적용본이라, 그것을 복구하면 operational checkpoint를 지우는
    파괴다.
    """

    import checkpoint_backup as cbackup

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    target = _target(guarded_endpoint)
    cbackup.run_backup(target, change_reference="GH-130", backup_root=root)

    if prepare == "ready":
        from langgraph.checkpoint.postgres import PostgresSaver

        with _connect(guarded_endpoint) as raw:
            PostgresSaver(raw).setup()

    before = _table_count(guarded_endpoint)
    approval = _write_recovery_approval(root, "GH-130")
    with pytest.raises(cbackup.CheckpointBackupError) as caught:
        cbackup.run_recover(
            target,
            change_reference="GH-130",
            backup_root=root,
            approval_path=approval,
        )
    assert caught.value.reason_code == "RECOVERY_STATE_INVALID"
    # **catalog가 그대로다** — 거부는 판정이지 부분 실행이 아니다.
    assert _table_count(guarded_endpoint) == before


def test_recovery_holds_the_lock_until_the_postcheck(
    guarded_endpoint: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guarded_identity: Any,
) -> None:
    """restore가 도는 동안 다른 session이 lock을 잡지 못한다.

    초판은 DROP 뒤 lock을 풀고 그다음에 `pg_restore`를 돌렸다. 그 구간에 다른
    apply/recover가 끼어들 수 있었다(구현리뷰 12차 필수 2).
    """

    import checkpoint_backup as cbackup
    import postgres_backup as backup

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    target = _target(guarded_endpoint)
    cbackup.run_backup(target, change_reference="GH-130", backup_root=root)

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(guarded_endpoint) as raw:
        PostgresSaver(raw).setup()
        raw.cursor().execute("DROP INDEX checkpoints_thread_id_idx")

    observed: list[bool] = []
    original = backup.run_command

    def _watch(argv: Any, **kwargs: Any) -> Any:
        if "--clean" in argv:
            probe = _connect(guarded_endpoint)
            try:
                observed.append(runner._try_session_lock(probe.cursor()))
            finally:
                probe.close()
        return original(argv, **kwargs)

    monkeypatch.setattr(backup, "run_command", _watch)
    cbackup.run_recover(
        target,
        change_reference="GH-130",
        backup_root=root,
        approval_path=_write_recovery_approval(root, "GH-130"),
    )
    assert observed == [False], "restore 중에 lock이 풀려 있었다"


def test_recovery_checks_the_client_before_touching_the_database(
    guarded_endpoint: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guarded_identity: Any,
) -> None:
    """복원 도구를 쓸 수 없으면 **DB를 바꾸기 전에** 멈춘다."""

    import checkpoint_backup as cbackup
    import postgres_backup as backup

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    target = _target(guarded_endpoint)
    cbackup.run_backup(target, change_reference="GH-130", backup_root=root)

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(guarded_endpoint) as raw:
        PostgresSaver(raw).setup()
        raw.cursor().execute("DROP INDEX checkpoints_thread_id_idx")
    before = _table_count(guarded_endpoint)

    original = backup.run_command

    def _fail_version(argv: Any, **kwargs: Any) -> Any:
        if "--version" in argv:
            raise backup.BackupError(
                "BACKUP_CLIENT_UNAVAILABLE", backup.EXIT_CONFIRM_REQUIRED
            )
        return original(argv, **kwargs)

    monkeypatch.setattr(backup, "run_command", _fail_version)
    with pytest.raises(backup.BackupError):
        cbackup.run_recover(
            target,
            change_reference="GH-130",
            backup_root=root,
            approval_path=_write_recovery_approval(root, "GH-130"),
        )
    # **catalog 무변경** — checkpoint table이 아직 그대로다.
    assert _table_count(guarded_endpoint) == before


def test_each_test_gets_a_pristine_database(guarded_endpoint: Any) -> None:
    """**컨테이너를 공유해도 DB와 role은 매번 새것이다.**

    template 복제로 기동·스키마 구성을 1회로 줄였다. 그 대가로 격리를 잃으면 이 Task에서
    결함을 드러냈던 신호(부분 상태·ACL 변조·`PARTIAL`)가 서로 새어 무의미해진다.

    이 회귀는 **앞선 테스트가 남긴 것이 없는지**를 본다 — checkpoint object 0건,
    `cm34_` role 0건, guarded 22 table 그대로.
    """

    connection = runner._connect(_target(guarded_endpoint))
    try:
        cursor = connection.cursor()
        assert runner.read_catalog(cursor)["tables"] == [], "checkpoint object가 남았다"
        cursor.execute(
            "SELECT count(*) AS c FROM pg_roles WHERE rolname LIKE %s",
            (f"{TEST_ROLE_PREFIX}%",),
        )
        assert int(cursor.fetchone()["c"]) == 0, "테스트 role이 남았다"
        cursor.execute(
            "SELECT count(*) AS c FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        )
        assert int(cursor.fetchone()["c"]) == 22
        assert cbackup_reference_clean(cursor)
    finally:
        connection.close()


def cbackup_reference_clean(cursor: Any) -> bool:
    """복제본이 계약을 그대로 만족하는지 — template이 낡으면 여기서 걸린다."""

    return runner.reference_physical_mismatches(cursor) == []


# ---------------------------------------------------------------------------
# 13차 필수 3 — checkpoint ACL은 READY 판정 자체에 들어 있다
# ---------------------------------------------------------------------------


def _operational_counts(endpoint: Any) -> dict[str, int]:
    connection = runner._connect(_target(endpoint))
    try:
        return runner.operational_row_counts(connection.cursor())
    finally:
        connection.close()


def _live_state(endpoint: Any) -> str:
    connection = runner._connect(_target(endpoint))
    try:
        return contract.classify_state(runner.read_catalog(connection.cursor()))
    finally:
        connection.close()


def _full_verifier_mismatches(endpoint: Any, *, require_marker: bool = True) -> Any:
    import verify_bootstrap_state as verifier
    from sqlalchemy import create_engine

    engine = create_engine(
        f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
        f"@{endpoint.host}:{endpoint.port}/{TARGET_DATABASE}"
    )
    try:
        with engine.connect() as connection:
            return verifier._checkpoint_mismatches(
                connection, _target(endpoint), require_marker=require_marker
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("GRANT SELECT ON checkpoints TO PUBLIC", "ACL_PUBLIC"),
        ("GRANT TRUNCATE ON checkpoint_blobs TO PUBLIC", "ACL_PUBLIC"),
        ("GRANT SELECT (thread_id) ON checkpoints TO PUBLIC", "ACL_PUBLIC"),
        ("ALTER TABLE checkpoints OWNER TO cm34_owner", "ACL_OWNER"),
    ],
)
def test_every_ready_consumer_refuses_checkpoint_acl_drift(
    guarded_endpoint: Any, artifact_roots: Any, mutation: str, reason: str
) -> None:
    """**READY를 소비하는 여섯 경로가 전부 거부한다**(구현리뷰 13차 필수 3).

    이전에는 ACL 판정이 apply 직후와 full verifier에서만 돌았다.
    그래서 `GRANT SELECT ON checkpoints TO PUBLIC` 뒤에도 preflight가 `READY_MARKED`,
    재실행이 `NO_OP`, verify가 성공하고 smoke가 **쓰기를 시작**할 수 있었다.

    이제 `read_catalog()`가 ACL을 함께 읽고 `classify_state()`가 하나의 상태로
    접는다. 여섯 경로가 같은 판정을 소비하는지 실제 DB로 고정한다.
    """

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"
    with _connect(guarded_endpoint) as raw:
        cursor = raw.cursor()
        cursor.execute("CREATE ROLE cm34_owner NOLOGIN")
        cursor.execute(mutation)

    target = _target(guarded_endpoint)
    before = _operational_counts(guarded_endpoint)
    tables_before = _table_count(guarded_endpoint)

    # 1. preflight — `READY_MARKED`가 아니다.
    assert (
        runner.run_preflight(target, marker_root=artifact_roots["markers"]) == "DRIFT"
    )

    # 2. 재실행 no-op — "아무 일도 없었다"가 아니라 거부다.
    with pytest.raises(contract.CheckpointStateError) as caught:
        _apply(guarded_endpoint, artifact_roots)
    assert caught.value.reason_code == "DRIFT"

    # 3. verify — **어느 축인지** 말한다.
    with pytest.raises(contract.CheckpointStateError) as caught:
        runner.run_verify(target, marker_root=artifact_roots["markers"])
    assert caught.value.reason_code == reason

    # 4. smoke — 쓰기를 시작하지 않는다.
    with pytest.raises(contract.CheckpointStateError) as caught:
        runner.run_smoke(
            target,
            change_reference="GH-130",
            marker_root=artifact_roots["markers"],
        )
    assert caught.value.reason_code == reason

    # 5. full verifier도 같이 red다.
    assert _full_verifier_mismatches(guarded_endpoint) == [
        {"mismatch_kind": "CHECKPOINT_SCHEMA"}
    ]

    # 6. **복구의 `DRIFT` 허용 경로에 들어온다.** 이전에는 `READY`로 보여 못 들어왔다.
    import checkpoint_backup as cbackup

    assert _live_state(guarded_endpoint) in cbackup.RECOVERABLE_STATES

    # 거부한 경로들은 DB를 바꾸지 않았다.
    assert _operational_counts(guarded_endpoint) == before
    assert _table_count(guarded_endpoint) == tables_before


def test_the_full_recovery_lifecycle_including_reapply(
    guarded_endpoint: Any,
    tmp_path: Path,
    guarded_identity: Any,
    guard_marker_root: Path,
) -> None:
    """**네 개를 함께 옮겨도 drift다**(구현리뷰 14차 필수 1).

    13차 보완은 "네 owner가 서로 같은가"만 봤다. 그래서 4종을 한꺼번에 다른 role로
    넘기면 `classify_state()`가 `READY`였다. marker를 읽는 preflight·no-op·verify·
    full verifier는 signature 차이로 `MARKER_DRIFT`를 냈지만, **복구는 marker를 읽지
    않는다** — `PARTIAL|DRIFT`만 허용하므로 `RECOVERY_STATE_INVALID`가 됐다. 계획이
    정한 유일한 복구 경로가 닫힌 것이다.

    하나만 바꾸면 복구 대상이고 넷을 바꾸면 복구 거부라는 차이는 보안 계약상 의미가
    없다. 이제 기대 owner를 **catalog를 읽은 연결의 관리 계정**으로 두어 여섯 경로가
    같은 기준을 쓴다.

    여기서는 실제 backup → apply → 4종 owner 일괄 이전 → 여섯 경로 거부 → 승인된
    복구 → `ABSENT`까지 한 번에 확인한다.
    """

    import checkpoint_backup as cbackup

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    markers = tmp_path / "markers"
    reports = tmp_path / "reports"
    markers.mkdir()
    reports.mkdir()
    target = _target(guarded_endpoint)
    guard_marker_before = severity_guard.load_marker(
        target, migration_sha=guard_migration_sha(), root=guard_marker_root
    )
    assert guard_marker_before is not None

    # **진짜 archive를 만든다.** 복구까지 돌려야 하므로 fixture 더미로는 안 된다.
    receipt = cbackup.run_backup(target, change_reference="GH-130", backup_root=root)
    assert receipt["restore_verified"] is True
    approval = _write_recovery_approval(root, "GH-130")

    assert (
        runner.run_apply(
            target,
            change_reference="GH-130",
            marker_root=markers,
            report_root=reports,
            backup_root=root,
            approval_path=approval,
        )
        == "APPLIED"
    )

    with _connect(guarded_endpoint) as raw:
        cursor = raw.cursor()
        cursor.execute("CREATE ROLE cm34_owner NOLOGIN")
        for table in sorted(contract.CHECKPOINT_TABLES):
            cursor.execute(f'ALTER TABLE "{table}" OWNER TO cm34_owner')

    # 물리 계약(컬럼·PK·index·version)은 그대로다. 그래도 drift다.
    assert _live_state(guarded_endpoint) == "DRIFT"

    # --- READY 소비 경로 다섯이 전부 거부한다 --------------------------------
    assert runner.run_preflight(target, marker_root=markers) == "DRIFT"
    with pytest.raises(contract.CheckpointStateError) as caught:
        runner.run_apply(
            target,
            change_reference="GH-130",
            marker_root=markers,
            report_root=reports,
            backup_root=root,
            approval_path=approval,
        )
    assert caught.value.reason_code == "DRIFT"
    with pytest.raises(contract.CheckpointStateError) as caught:
        runner.run_verify(target, marker_root=markers)
    assert caught.value.reason_code == "ACL_OWNER"
    with pytest.raises(contract.CheckpointStateError) as caught:
        runner.run_smoke(target, change_reference="GH-130", marker_root=markers)
    assert caught.value.reason_code == "ACL_OWNER"
    assert _full_verifier_mismatches(guarded_endpoint) == [
        {"mismatch_kind": "CHECKPOINT_SCHEMA"}
    ]

    # --- 여섯 번째 경로: 승인된 복구가 **실제로 실행된다** --------------------
    record = cbackup.run_recover(
        target,
        change_reference="GH-130",
        backup_root=root,
        approval_path=approval,
    )
    assert record["state_before"] == "DRIFT"
    assert record["status"] == "COMMITTED"
    assert runner.run_preflight(target, marker_root=markers) == "ABSENT"

    # 복구 증적을 **다시 읽는 Gate**도 통과한다(14차 권장 1).
    verified = cbackup.run_verify_recovery(
        target, change_reference="GH-130", backup_root=root, marker_root=markers
    )
    assert verified["archive_sha256"] == receipt["archive_sha256"]
    assert verified["_observed_state"] == "ABSENT"

    # --- 정본 §3.6이 요구하는 재적용, 그리고 §3.7 closure ---------------------
    #
    # **여기가 15차 필수 1이다.** 이전 판은 backup 발급용 helper를 그대로 써서
    # `ABSENT` 하나만 허용했다. 그래서 복구 직후에는 통과하는데 정상적으로 재적용한
    # closure 시점에는 `SOURCE_STATE_INVALID`로 반드시 실패했다 — 정본이 적은 절차가
    # 코드상 성립하지 않았다.
    #
    # ## 재적용 전에 CM-3.3 marker를 **실제 production 경로로** 재발급한다
    #
    # `pg_restore`는 같은 predicate를 다르게 재출력하므로 복구 뒤 raw
    # `schema_signature_sha256`가 달라진다. 정규화 계약과 5축은 전부 동일한데도
    # CM-3.3 marker와의 raw 대조는 실패해 재적용이 `PREDECESSOR_DRIFT`에서 멈춘다.
    #
    # 16차 필수 1이 여기다. 이전 판은 fixture의 identity를 직접 갈아 끼워 통과시켰고,
    # 그건 **아직 없는 재발급 경로를 테스트가 대신한 것**이라 운영 lifecycle을
    # 증명하지 못했다. 이제 `--reissue-marker-after-restore`가 그 자리를 채운다.
    assert (
        severity_guard.run_reissue_marker_after_restore(
            target,
            change_reference="GH-130",
            backup_root=root,
            marker_root=guard_marker_root,
            report_root=reports,
        )
        == "REISSUED"
    )
    # 재발급 뒤에도 계보는 그대로다 — raw signature 하나만 바뀐다.
    reissued = severity_guard.load_marker(
        target, migration_sha=guard_migration_sha(), root=guard_marker_root
    )
    assert reissued is not None
    assert (
        reissued["guarded_schema_signature_sha256"]
        != guard_marker_before["guarded_schema_signature_sha256"]
    )
    for key in sorted(severity_guard._IDENTITY_KEYS):
        assert reissued[key] == guard_marker_before[key], key
    assert reissued["applied_at"] == guard_marker_before["applied_at"]
    # 같은 명령을 다시 돌리면 바꿀 것이 없다.
    assert (
        severity_guard.run_reissue_marker_after_restore(
            target,
            change_reference="GH-130",
            backup_root=root,
            marker_root=guard_marker_root,
            report_root=reports,
        )
        == "NO_OP"
    )

    assert (
        runner.run_apply(
            target,
            change_reference="GH-130",
            marker_root=markers,
            report_root=reports,
            backup_root=root,
            approval_path=approval,
        )
        == "APPLIED"
    )
    assert runner.run_preflight(target, marker_root=markers) == "READY_MARKED"
    closure = cbackup.run_verify_recovery(
        target, change_reference="GH-130", backup_root=root, marker_root=markers
    )
    assert closure["_observed_state"] == "READY_MARKED"
    assert closure["archive_sha256"] == receipt["archive_sha256"]

    # 5축은 checkpoint 4종을 제외하므로 재적용 전후가 같다 — 그것이 이 대조가 두
    # 시점에서 함께 성립하는 이유다.
    assert closure["recovered_projection"] == verified["recovered_projection"]


def test_the_recovery_evidence_gate_refuses_a_drifted_target(
    guarded_endpoint: Any, tmp_path: Path, guarded_identity: Any
) -> None:
    """복구 증적이 서술하는 형상과 지금이 다르면 완료 증적으로 인정하지 않는다.

    파일이 있다는 사실은 "복구했다"를 뜻하지 않는다 — 그 파일이 서술하는 상태가 현재
    DB와 같아야 한다(구현리뷰 14차 권장 1).
    """

    import checkpoint_backup as cbackup

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    target = _target(guarded_endpoint)

    cbackup.run_backup(target, change_reference="GH-130", backup_root=root)

    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(guarded_endpoint) as raw:
        PostgresSaver(raw).setup()
        raw.cursor().execute("DROP INDEX checkpoints_thread_id_idx")

    cbackup.run_recover(
        target,
        change_reference="GH-130",
        backup_root=root,
        approval_path=_write_recovery_approval(root, "GH-130"),
    )
    verified = cbackup.run_verify_recovery(
        target, change_reference="GH-130", backup_root=root, marker_root=tmp_path / "m"
    )
    assert verified["status"] == "COMMITTED"
    assert verified["_observed_state"] == "ABSENT"

    # 복구 뒤 형상을 바꾸면 그 증적은 더 이상 현재를 증명하지 않는다.
    with _connect(guarded_endpoint) as raw:
        raw.cursor().execute("CREATE ROLE cm34_after NOLOGIN")
        raw.cursor().execute("GRANT SELECT ON action_history TO cm34_after")
    with pytest.raises(cbackup.CheckpointBackupError) as caught:
        cbackup.run_verify_recovery(
            target,
            change_reference="GH-130",
            backup_root=root,
            marker_root=tmp_path / "m",
        )
    assert caught.value.reason_code == "RECOVERY_DRIFT"


def test_a_clean_checkpoint_passes_every_ready_consumer(
    guarded_endpoint: Any, artifact_roots: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**양성 대조군.** ACL 축을 넣고도 정상 적용본은 여섯 경로를 전부 통과한다.

    실패만 고정하면 "항상 거부" 변이가 green이 된다.
    """

    assert _apply(guarded_endpoint, artifact_roots) == "APPLIED"
    applied = runner.load_marker(TARGET_DATABASE, root=artifact_roots["markers"])
    assert applied is not None
    monkeypatch.setattr(runner, "load_marker", lambda *a, **k: dict(applied))
    target = _target(guarded_endpoint)
    assert (
        runner.run_preflight(target, marker_root=artifact_roots["markers"])
        == "READY_MARKED"
    )
    assert _apply(guarded_endpoint, artifact_roots) == "NO_OP"
    assert (
        runner.run_verify(target, marker_root=artifact_roots["markers"])
        == "READY_MARKED"
    )
    assert (
        runner.run_smoke(
            target,
            change_reference="GH-130",
            marker_root=artifact_roots["markers"],
        )
        == "OK"
    )
    assert _full_verifier_mismatches(guarded_endpoint) == []
    assert _live_state(guarded_endpoint) == "READY"


# ---------------------------------------------------------------------------
# 13차 필수 2 — predecessor archive에는 checkpoint가 없다
# ---------------------------------------------------------------------------


def _archive_toc(root: Path, archive: Path) -> str:
    """archive의 목차를 **실제 `pg_restore --list`로** 읽는다. 아무것도 바꾸지 않는다.

    5축 projection은 checkpoint 4종을 제외하므로 archive에 그것이 들어가도 조용하다.
    """

    import subprocess

    import postgres_backup as backup

    client = backup.select_backup_client(16)
    child_env = backup.child_environment(
        "unused", host="127.0.0.1", port=5432, user="unused", database="unused"
    )
    completed = subprocess.run(
        list(
            backup.pinned_client_argv(
                ("pg_restore", "--list", f"/backups/{archive.name}"),
                image=client.image,
                child_env=child_env,
                mounts={str(root.resolve()): "/backups"},
            )
        ),
        capture_output=True,
        text=True,
        check=True,
        env=dict(child_env),
    )
    return completed.stdout


def test_the_predecessor_archive_carries_no_checkpoint_object(
    guarded_endpoint: Any, tmp_path: Path, guarded_identity: Any
) -> None:
    """**archive 목차에 checkpoint object가 0건이다**(구현리뷰 13차 필수 2).

    5축 projection의 inventory·security는 checkpoint 4종을 의도적으로 제외하므로,
    archive에 그것이 들어가도 `restore_verified=true`가 찍힌다. 그래서 projection이
    아니라 **archive 자체**를 읽어 확인한다.
    """

    import checkpoint_backup as cbackup

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    receipt = cbackup.run_backup(
        _target(guarded_endpoint), change_reference="GH-130", backup_root=root
    )
    assert receipt["restore_verified"] is True

    toc = _archive_toc(root, root / cbackup.archive_name(TARGET_DATABASE, "GH-130"))
    for name in sorted(contract.CHECKPOINT_TABLES):
        assert name not in toc, name
    # 대조군 — 업무 table은 실제로 들어 있다.
    assert "action_history" in toc


def test_run_backup_refuses_a_target_that_already_has_checkpoints(
    guarded_endpoint: Any, tmp_path: Path, guarded_identity: Any
) -> None:
    """적용된 DB에서는 predecessor archive를 뜰 수 없다.

    그 archive로 복구하면 checkpoint 4종을 지운 직후 restore가 다시 만들어
    `RECOVERY_INCOMPLETE`로 끝난다 — 복구 수단이 아니라 복구를 막는 파일이다.
    """

    import checkpoint_backup as cbackup
    from langgraph.checkpoint.postgres import PostgresSaver

    with _connect(guarded_endpoint) as raw:
        PostgresSaver(raw).setup()

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    with pytest.raises(cbackup.CheckpointBackupError) as caught:
        cbackup.run_backup(
            _target(guarded_endpoint), change_reference="GH-130", backup_root=root
        )
    assert caught.value.reason_code == "SOURCE_STATE_INVALID"
    # 잔재 0건 — 임시도 최종도 남지 않는다.
    assert list(root.iterdir()) == []


def test_run_backup_refuses_a_drifted_predecessor(
    guarded_endpoint: Any, tmp_path: Path, guarded_identity: Any
) -> None:
    """guarded 계약을 통과하지 못한 형상은 **predecessor archive가 아니다.**

    복원해도 apply의 선행 확인을 통과하지 못하므로 복구 수단이 되지 못한다.
    """

    import checkpoint_backup as cbackup

    with _connect(guarded_endpoint) as raw:
        # CM-3.2가 고정한 22-table allowlist를 깬다.
        raw.cursor().execute("CREATE TABLE cm34_stray (id integer)")

    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    with pytest.raises(contract.CheckpointStateError) as caught:
        cbackup.run_backup(
            _target(guarded_endpoint), change_reference="GH-130", backup_root=root
        )
    assert caught.value.reason_code == "PREDECESSOR_DRIFT"
    assert list(root.iterdir()) == []


# ---------------------------------------------------------------------------
# 13차 권장 2 — 복원본 쪽 ACL 변이가 실제 restore lifecycle에서 걸린다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "statements"),
    [
        (
            "grant option",
            ("GRANT SELECT ON r03_alarm_history TO cm34_reader WITH GRANT OPTION",),
        ),
        (
            "다른 grantor",
            (
                "CREATE ROLE cm34_broker NOLOGIN",
                "GRANT SELECT ON r03_alarm_history TO cm34_broker WITH GRANT OPTION",
                "SET ROLE cm34_broker",
                "GRANT SELECT ON r03_alarm_history TO cm34_reader",
                "RESET ROLE",
            ),
        ),
    ],
)
def test_restore_side_acl_drift_issues_no_evidence(
    guarded_endpoint: Any,
    tmp_path: Path,
    guarded_identity: Any,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    statements: tuple[str, ...],
) -> None:
    """**복원본 쪽을 실제로 변조한다**(구현리뷰 13차 권장 2).

    이전 회귀는 live source에서 projection 전후만 비교했다. 그것은 "축이 민감하다"는
    증명이지 "restore lifecycle이 그것을 잡는다"는 증명이 아니다. 여기서는
    `pg_restore` 직후 복원본에 grant option·grantor 변이를 넣고, receipt·completion·
    archive가 **하나도 발급되지 않는지** 본다.
    """

    import checkpoint_backup as cbackup
    import postgres_backup as backup
    import rehearsal_postgres as rehearsal

    _grant_distinct_security(guarded_endpoint)
    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)

    restored: dict[str, Any] = {}
    original = backup.run_command

    def _lifecycle(**kwargs: Any) -> Any:
        import contextlib

        @contextlib.contextmanager
        def _wrapped() -> Any:
            with rehearsal.one_off_postgres(**kwargs) as value:
                restored["endpoint"] = value
                yield value

        return _wrapped()

    def _mutate_after_restore(argv: Any, **kwargs: Any) -> Any:
        completed = original(argv, **kwargs)
        if "pg_restore" in argv and "--version" not in argv:
            endpoint = restored["endpoint"]
            connection = psycopg.connect(
                f"postgresql://{endpoint.username}:{endpoint.password}"
                f"@{endpoint.host}:{endpoint.port}/{TARGET_DATABASE}",
                autocommit=True,
                prepare_threshold=0,
                row_factory=psycopg.rows.dict_row,
            )
            try:
                cursor = connection.cursor()
                for statement in statements:
                    cursor.execute(statement)
            finally:
                connection.close()
        return completed

    monkeypatch.setattr(backup, "run_command", _mutate_after_restore)
    with pytest.raises(cbackup.CheckpointBackupError) as caught:
        cbackup.run_backup(
            _target(guarded_endpoint),
            change_reference="GH-130",
            backup_root=root,
            lifecycle=_lifecycle,
        )
    assert caught.value.reason_code == "RESTORE_NOT_VERIFIED", label
    assert list(root.iterdir()) == [], label
