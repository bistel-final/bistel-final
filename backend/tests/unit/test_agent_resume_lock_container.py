"""`V5-C-3.3` session advisory resume lock의 실제 PostgreSQL 회귀."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
for candidate in (BACKEND_ROOT, BACKEND_ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import rehearsal_postgres as postgres  # noqa: E402

from app.agent import approval_store as subject  # noqa: E402

pytestmark = pytest.mark.container

TARGET_DATABASE = "kosa_agent_e2e"
THREAD_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(scope="module")
def runtime() -> tuple[Any, Any]:
    with postgres.one_off_postgres(database=TARGET_DATABASE) as endpoint:
        engine = create_engine(
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{TARGET_DATABASE}",
            pool_size=3,
        )
        try:
            yield endpoint, engine
        finally:
            engine.dispose()


def test_concurrent_resume_has_one_owner_and_one_busy_result(
    runtime: tuple[Any, Any],
) -> None:
    _endpoint, engine = runtime
    acquired = Event()
    release = Event()
    invocations: list[str] = []

    def owner() -> str:
        with subject._resume_mutex(engine.connect, THREAD_ID):
            invocations.append("invoke")
            acquired.set()
            assert release.wait(timeout=5)
        return "DONE"

    def contender() -> str:
        assert acquired.wait(timeout=5)
        try:
            with subject._resume_mutex(engine.connect, THREAD_ID):
                invocations.append("duplicate")
        except subject.HitlResumeError as exc:
            release.set()
            return exc.code
        return "UNEXPECTED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner_future = executor.submit(owner)
        contender_future = executor.submit(contender)
        results = sorted((owner_future.result(), contender_future.result()))

    assert results == ["DONE", "RESUME_ALREADY_RUNNING"]
    assert invocations == ["invoke"]


def test_body_exception_releases_lock_for_the_next_owner(
    runtime: tuple[Any, Any],
) -> None:
    _endpoint, engine = runtime
    with pytest.raises(RuntimeError, match="fixture"):
        with subject._resume_mutex(engine.connect, THREAD_ID):
            raise RuntimeError("fixture")
    with subject._resume_mutex(engine.connect, THREAD_ID):
        pass


def test_physical_owner_termination_releases_the_session_lock(
    runtime: tuple[Any, Any],
) -> None:
    endpoint, engine = runtime
    ready = Event()
    terminated = Event()
    pid_holder: list[int] = []

    def owner() -> str:
        try:
            with subject._resume_mutex(engine.connect, THREAD_ID) as connection:
                pid_holder.append(
                    int(
                        connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                    )
                )
                ready.set()
                assert terminated.wait(timeout=5)
        except subject.HitlResumeError as exc:
            return exc.code
        return "UNEXPECTED"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(owner)
        assert ready.wait(timeout=5)
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=TARGET_DATABASE,
            user=endpoint.username,
            password=endpoint.password,
            autocommit=True,
        ) as admin:
            assert admin.execute(
                "SELECT pg_terminate_backend(%s)", (pid_holder[0],)
            ).fetchone()[0]
        terminated.set()
        assert future.result() == "RESUME_LOCK_LEAKED"

    # terminated physical session이 들던 advisory lock은 server가 해제했다.
    with subject._resume_mutex(engine.connect, THREAD_ID):
        pass
