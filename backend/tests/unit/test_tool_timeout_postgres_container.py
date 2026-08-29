"""PostgreSQL server-side timeout termination contract on an isolated container."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rehearsal_postgres as postgres  # noqa: E402

from app.common.tool_timeouts import DependencyTimeoutError  # noqa: E402
from app.knowledge.document_search import DocumentSearchRepository  # noqa: E402

pytestmark = pytest.mark.container

MARKER = "cm48_registration_timeout_marker_76f0d9"


def test_registration_query_is_canceled_and_pool_state_is_clean() -> None:
    """RAG order의 registration 구간도 local timeout 안에서 실제 취소된다."""

    with postgres.one_off_postgres(
        database="cm48_timeout",
        image=postgres.POSTGRES_RAG_IMAGE,
    ) as endpoint:
        engine = create_engine(
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{endpoint.database}",
            pool_size=1,
            max_overflow=1,
        )

        def slow_registration(raw_connection: object) -> None:
            with raw_connection.cursor() as cursor:  # type: ignore[union-attr]
                cursor.execute(f"SELECT pg_sleep(3) /* {MARKER} */")

        repository = DocumentSearchRepository(
            engine,
            timeout_seconds=0.15,
            vector_registrar=slow_registration,
        )
        started = time.monotonic()
        try:
            with pytest.raises(DependencyTimeoutError) as raised:
                repository.search([0.1])
            elapsed = time.monotonic() - started

            assert raised.value.reason_code == "DB_STATEMENT_TIMEOUT"
            assert 0.08 <= elapsed < 2.0

            with engine.connect() as connection:
                assert connection.execute(text("SELECT 1")).scalar_one() == 1
                assert (
                    connection.execute(text("SHOW statement_timeout")).scalar_one()
                    == "0"
                )
                active = connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_stat_activity
                        WHERE pid <> pg_backend_pid()
                          AND state = 'active'
                          AND query LIKE :marker
                        """
                    ),
                    {"marker": f"%{MARKER}%"},
                ).scalar_one()
                assert active == 0
        finally:
            engine.dispose()
