"""V5-CM-4.7 reset core의 격리 PostgreSQL 실증.

공용 DB에는 접근하지 않는다. 일회성 pgvector/PostgreSQL 16에 Runtime 002→003과
checkpoint 0..8을 세운 뒤 transaction·FK·writer race를 실제로 실행한다.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import checkpoint_contract  # noqa: E402
import e2e_reset_evidence as evidence  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402
import reset_e2e_runtime as reset  # noqa: E402

pytestmark = pytest.mark.container

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"
SQL_002 = REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
SQL_003 = REPOSITORY_ROOT / "backend" / "migrations" / "003_agent_run_severity_pair.sql"


def _action_history_ddl() -> str:
    body = (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
    start = body.index("CREATE TABLE action_history (")
    return body[start : body.index(");", start) + 2]


def _dsn(endpoint: Any) -> str:
    return (
        f"postgresql://{endpoint.username}:{endpoint.password}"
        f"@{endpoint.host}:{endpoint.port}/{reset.TARGET_DATABASE}"
    )


def _engine(endpoint: Any) -> Any:
    return create_engine(
        URL.create(
            "postgresql+psycopg",
            username=endpoint.username,
            password=endpoint.password,
            host=endpoint.host,
            port=endpoint.port,
            database=reset.TARGET_DATABASE,
        ),
        future=True,
    )


@pytest.fixture(scope="module")
def endpoint() -> Any:
    from langgraph.checkpoint.postgres import PostgresSaver

    with postgres.one_off_postgres(
        database=reset.TARGET_DATABASE, image=postgres.POSTGRES_RAG_IMAGE
    ) as value:
        with psycopg.connect(_dsn(value), autocommit=True) as raw:
            raw.execute(_action_history_ddl())
            raw.execute(SQL_002.read_text(encoding="utf-8"))
            raw.execute(SQL_003.read_text(encoding="utf-8"))
            PostgresSaver(raw).setup()
            raw.execute(
                "CREATE TABLE public.source_keep "
                "(id integer PRIMARY KEY, note text NOT NULL)"
            )
            raw.execute("INSERT INTO public.source_keep VALUES (1, 'preserve-me')")
        yield value


@pytest.fixture(autouse=True)
def clean_runtime(endpoint: Any) -> None:
    with psycopg.connect(_dsn(endpoint), autocommit=True) as raw:
        raw.execute("DROP TABLE IF EXISTS public.outside_guard")
        raw.execute(reset.TRUNCATE_SQL)


def _begin_repeatable(connection: Any) -> Any:
    transaction = connection.begin()
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    connection.exec_driver_sql("SET LOCAL search_path = public")
    return transaction


def _seed(connection: Any) -> None:
    connection.exec_driver_sql(
        """
        INSERT INTO public.agent_run
          (agent_run_id, thread_id, lot_id, chamber_id,
           requested_alarm_source, requested_alarm_id,
           representative_alarm_source, representative_alarm_id,
           status, autonomy_level, action, severity)
        VALUES
          ('RUN-0000000000000001', 'thread-cm47', 'LOT-CM47', 'EQP01-PM1',
           'TRACE', 'TA-CM47', 'TRACE', 'TA-CM47',
           'COMPLETED', 1, 'WARNING', 'MEDIUM')
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO public.action_history
          (action_id, lot_id, chamber_id, action_code, reason,
           approval_required, approval_status, created_at)
        VALUES
          ('ACT-0000000000000001', 'LOT-CM47', 'EQP01-PM1', 'WARNING',
           'runtime fixture', 'N', 'AUTO', now())
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO public.agent_run_action
          (agent_run_id, action_id, link_role, lot_id, chamber_id,
           trigger_alarm_source, trigger_alarm_id)
        VALUES
          ('RUN-0000000000000001', 'ACT-0000000000000001', 'CREATED',
           'LOT-CM47', 'EQP01-PM1', 'TRACE', 'TA-CM47')
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO public.checkpoints
          (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata)
        VALUES ('thread-cm47', '', 'cp-1', '{}'::jsonb, '{}'::jsonb)
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO public.checkpoint_blobs
          (thread_id, checkpoint_ns, channel, version, type, blob)
        VALUES ('thread-cm47', '', 'state', '1', 'bytes', decode('01', 'hex'))
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO public.checkpoint_writes
          (thread_id, checkpoint_ns, checkpoint_id, task_id, idx,
           channel, type, blob)
        VALUES
          ('thread-cm47', '', 'cp-1', 'task-1', 0,
           'state', 'bytes', decode('02', 'hex'))
        """
    )


def test_real_reset_clears_13_tables_and_preserves_checkpoint_schema(
    endpoint: Any,
) -> None:
    engine = _engine(endpoint)
    try:
        with engine.connect() as connection:
            transaction = _begin_repeatable(connection)
            _seed(connection)
            before_versions = (
                connection.exec_driver_sql(
                    "SELECT v FROM checkpoint_migrations ORDER BY v"
                )
                .scalars()
                .all()
            )
            result = reset.reset_runtime_data(
                connection, preflight=lambda _connection: None
            )
            transaction.commit()

        assert result.before_rows["action_history"] == 1
        assert result.before_rows["checkpoints"] == 1
        assert result.preserved_before_sha256 == result.preserved_after_sha256
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            post = reset.assert_target_zero(connection)
            after_versions = (
                connection.exec_driver_sql(
                    "SELECT v FROM checkpoint_migrations ORDER BY v"
                )
                .scalars()
                .all()
            )
            keep = connection.exec_driver_sql(
                "SELECT note FROM source_keep WHERE id=1"
            ).scalar_one()
        assert set(post.row_counts.values()) == {0}
        assert all(
            value == {"last_value": 1, "is_called": False}
            for value in post.sequence_state.values()
        )
        assert (
            before_versions
            == after_versions
            == list(checkpoint_contract.expected_versions())
        )
        assert keep == "preserve-me"
    finally:
        engine.dispose()


def test_full_fingerprint_detects_content_and_sequence_only_changes(
    endpoint: Any,
) -> None:
    engine = _engine(endpoint)
    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            before = evidence.snapshot_database_fingerprint(connection)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE source_keep SET note='same-row-count-new-value' WHERE id=1"
            )
            connection.exec_driver_sql(
                "SELECT setval('audit_log_audit_id_seq', 7, true)"
            )
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            after = evidence.snapshot_database_fingerprint(connection)
        assert before["sha256"] != after["sha256"]
    finally:
        with psycopg.connect(_dsn(endpoint), autocommit=True) as raw:
            raw.execute("UPDATE source_keep SET note='preserve-me' WHERE id=1")
            raw.execute("ALTER SEQUENCE audit_log_audit_id_seq RESTART WITH 1")
        engine.dispose()


def test_empty_reset_is_idempotent(endpoint: Any) -> None:
    engine = _engine(endpoint)
    try:
        digests: list[str] = []
        for _attempt in range(2):
            with engine.connect() as connection:
                transaction = _begin_repeatable(connection)
                result = reset.reset_runtime_data(
                    connection, preflight=lambda _connection: None
                )
                transaction.commit()
            digests.append(result.preserved_after_sha256)
        assert len(set(digests)) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("fixture_kind", ["orphan", "reused_only", "reference"])
def test_action_provenance_variants_block_without_mutation(
    endpoint: Any, fixture_kind: str
) -> None:
    engine = _engine(endpoint)
    try:
        with engine.begin() as connection:
            if fixture_kind == "orphan":
                connection.exec_driver_sql(
                    """
                    INSERT INTO action_history
                      (action_id, lot_id, chamber_id, action_code, reason,
                       approval_required, approval_status, created_at)
                    VALUES ('ACT-0000000000000003', 'LOT-ORPHAN', 'EQP01-PM1',
                            'WARNING', 'orphan', 'N', 'AUTO', now())
                    """
                )
            else:
                _seed(connection)
                if fixture_kind == "reused_only":
                    connection.exec_driver_sql(
                        "UPDATE agent_run_action SET link_role='REUSED'"
                    )
                else:
                    connection.exec_driver_sql(
                        """
                        INSERT INTO action_history
                          (action_id, lot_id, chamber_id, action_code, reason,
                           approval_required, approval_status, created_at)
                        VALUES ('REF-0000000000000001', 'LOT-REFERENCE',
                                'EQP01-PM1', 'WARNING', 'reference row',
                                'N', 'AUTO', now())
                        """
                    )
        with engine.connect() as connection:
            before = reset.target_row_counts(connection)
        with engine.connect() as connection:
            transaction = _begin_repeatable(connection)
            with pytest.raises(reset.NoMutationBlocked) as caught:
                reset.reset_runtime_data(connection, preflight=lambda _connection: None)
            transaction.rollback()
        assert caught.value.reason_code == "ACTION_PROVENANCE_MISMATCH"
        with engine.connect() as connection:
            assert reset.target_row_counts(connection) == before
    finally:
        engine.dispose()


def test_concurrent_reset_is_rejected_by_advisory_lock(endpoint: Any) -> None:
    engine = _engine(endpoint)
    try:
        with engine.connect() as owner, engine.connect() as contender:
            owner_transaction = _begin_repeatable(owner)
            reset.acquire_reset_lock(owner)
            contender_transaction = _begin_repeatable(contender)
            with pytest.raises(reset.NoMutationBlocked) as caught:
                reset.reset_runtime_data(contender, preflight=lambda _connection: None)
            assert caught.value.reason_code == "RESET_IN_PROGRESS"
            contender_transaction.rollback()
            owner_transaction.rollback()
    finally:
        engine.dispose()


def test_unexpected_foreign_key_rolls_the_whole_reset_back(endpoint: Any) -> None:
    engine = _engine(endpoint)
    try:
        with engine.connect() as connection:
            transaction = _begin_repeatable(connection)
            _seed(connection)
            connection.exec_driver_sql(
                "CREATE TABLE outside_guard "
                "(action_id varchar(20) PRIMARY KEY "
                "REFERENCES action_history(action_id))"
            )
            connection.exec_driver_sql(
                "INSERT INTO outside_guard VALUES ('ACT-0000000000000001')"
            )
            transaction.commit()

        with engine.connect() as connection:
            transaction = _begin_repeatable(connection)
            with pytest.raises(reset.DependencyFailure) as caught:
                reset.reset_runtime_data(connection, preflight=lambda _connection: None)
            assert caught.value.reason_code == "RESET_FAILED"
            transaction.rollback()
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM action_history"
                ).scalar_one()
                == 1
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM outside_guard"
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_preserved_drift_from_truncate_trigger_rolls_the_whole_reset_back(
    endpoint: Any,
) -> None:
    """preserved 전후 대조가 TRUNCATE의 간접 쓰기까지 commit 전에 막는다."""

    engine = _engine(endpoint)
    try:
        with engine.begin() as connection:
            _seed(connection)
            connection.exec_driver_sql(
                """
                CREATE FUNCTION public.cm47_mutate_preserved_on_truncate()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                  UPDATE public.source_keep
                  SET note = 'mutated-by-truncate'
                  WHERE id = 1;
                  RETURN NULL;
                END
                $$
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER cm47_mutate_preserved_on_truncate
                AFTER TRUNCATE ON public.action_history
                FOR EACH STATEMENT
                EXECUTE FUNCTION public.cm47_mutate_preserved_on_truncate()
                """
            )
        with engine.connect() as connection:
            before_rows = reset.target_row_counts(connection)
            before_sequences = reset.target_sequence_state(connection)

        with engine.connect() as connection:
            transaction = _begin_repeatable(connection)
            with pytest.raises(reset.NoMutationBlocked) as caught:
                reset.reset_runtime_data(
                    connection,
                    preflight=lambda _connection: None,
                )
            assert caught.value.reason_code == "PRESERVED_STATE_CHANGED"
            transaction.rollback()

        with engine.connect() as connection:
            assert reset.target_row_counts(connection) == before_rows
            assert reset.target_sequence_state(connection) == before_sequences
            assert (
                connection.exec_driver_sql(
                    "SELECT note FROM source_keep WHERE id=1"
                ).scalar_one()
                == "preserve-me"
            )
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                DROP TRIGGER IF EXISTS cm47_mutate_preserved_on_truncate
                ON public.action_history
                """
            )
            connection.exec_driver_sql(
                "DROP FUNCTION IF EXISTS public.cm47_mutate_preserved_on_truncate()"
            )
            connection.exec_driver_sql(
                "UPDATE public.source_keep SET note='preserve-me' WHERE id=1"
            )
        engine.dispose()


def test_idle_client_connection_blocks_before_truncate(endpoint: Any) -> None:
    engine = _engine(endpoint)
    idle = psycopg.connect(_dsn(endpoint), autocommit=True)
    try:
        with engine.connect() as connection:
            transaction = _begin_repeatable(connection)
            with pytest.raises(reset.NoMutationBlocked) as caught:
                reset.reset_runtime_data(connection, preflight=lambda _connection: None)
            assert caught.value.reason_code == "E2E_WRITER_ACTIVE"
            transaction.rollback()
    finally:
        idle.close()
        engine.dispose()


def test_writer_entering_after_initial_scan_is_caught_by_postcheck(
    endpoint: Any,
) -> None:
    engine = _engine(endpoint)
    writer_started = threading.Event()
    writer_done = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            with psycopg.connect(_dsn(endpoint)) as connection:
                writer_started.set()
                connection.execute(
                    """
                    INSERT INTO action_history
                      (action_id, lot_id, chamber_id, action_code, reason,
                       approval_required, approval_status, created_at)
                    VALUES
                      ('ACT-0000000000000002', 'LOT-RACE', 'EQP01-PM1',
                       'WARNING', 'late writer', 'N', 'AUTO', now())
                    """
                )
                connection.commit()
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)
        finally:
            writer_done.set()

    worker: threading.Thread | None = None

    def fault_hook(stage: str) -> None:
        nonlocal worker
        if stage == "before_commit":
            worker = threading.Thread(target=writer, daemon=True)
            worker.start()
            assert writer_started.wait(timeout=5)

    try:
        with engine.connect() as connection:
            transaction = _begin_repeatable(connection)
            _seed(connection)
            reset.reset_runtime_data(
                connection,
                preflight=lambda _connection: None,
                fault_hook=fault_hook,
            )
            transaction.commit()
        assert worker is not None
        assert writer_done.wait(timeout=5)
        worker.join(timeout=1)
        assert not errors
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            with pytest.raises(reset.DependencyFailure) as caught:
                reset.assert_target_zero(connection)
        assert caught.value.reason_code == "RESET_APPLIED_WRITER_REENTRY"
    finally:
        engine.dispose()


def _target(endpoint: Any) -> reset.db_target.BootstrapTarget:
    return reset.db_target.BootstrapTarget(
        host=endpoint.host,
        port=int(endpoint.port),
        username=endpoint.username,
        password=endpoint.password,
        database=reset.TARGET_DATABASE,
        profile=reset.TARGET_PROFILE,
    )


@pytest.mark.parametrize(
    ("fault_stage", "committed", "applied_exists"),
    [
        ("before_commit", False, False),
        ("after_commit_before_receipt", True, False),
        ("after_applied_receipt", True, True),
    ],
)
def test_fault_windows_preserve_commit_and_receipt_order(
    endpoint: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_stage: str,
    committed: bool,
    applied_exists: bool,
) -> None:
    """pre -> DB commit -> applied receipt의 세 경계를 실제 DB로 고정한다."""

    target = _target(endpoint)
    engine = _engine(endpoint)
    run_id = {"before_commit": "a", "after_commit_before_receipt": "b"}.get(
        fault_stage, "c"
    ) * 32
    pre = evidence.base_receipt("e2e_reset_pre", run_id)
    pre.update(
        {
            "target_database": reset.TARGET_DATABASE,
            "target_host_fingerprint_sha256": reset.db_target.host_fingerprint(
                target.host, target.port
            ),
        }
    )
    pre_path = evidence.receipt_path(tmp_path, run_id, "pre")
    evidence.write_exclusive_receipt(pre_path, pre)
    monkeypatch.setattr(reset, "validate_marker_preflight", lambda *_a, **_k: None)

    def fail_at(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(f"fault:{stage}")

    try:
        with engine.begin() as connection:
            _seed(connection)
        with pytest.raises(RuntimeError, match=f"fault:{fault_stage}"):
            reset.apply_reset(
                target,
                run_id=run_id,
                pre_receipt_path=pre_path,
                report_root=tmp_path,
                engine_factory=lambda _target: engine,
                preflight=lambda _connection: None,
                snapshotter=reset.preserved_snapshot,
                fault_hook=fail_at,
            )
        with engine.connect() as connection:
            action_count = connection.exec_driver_sql(
                "SELECT count(*) FROM action_history"
            ).scalar_one()
        assert action_count == (0 if committed else 1)
        assert evidence.receipt_path(tmp_path, run_id, "pre").exists()
        assert (
            evidence.receipt_path(tmp_path, run_id, "applied").exists()
            is applied_exists
        )
        assert not evidence.receipt_path(tmp_path, run_id, "post").exists()
    finally:
        engine.dispose()
