"""Owned local-only PostgreSQL lifecycle and label-free TEMP inventory restore.

No caller DSN, no shared database, no Docker pull. Every attempt gets a fresh
connection and temporary tables; SQL readback runs in a read-only transaction.
"""

import re
import subprocess
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.agent.release_artifacts import EvidenceError
from app.agent.u10_fixture_source import verify_source_projection


def local_only_command(argv, **kwargs):
    if list(argv[:3]) == ["docker", "pull", "--quiet"]:
        argv = ["docker", "image", "inspect", argv[3]]
    elif list(argv[:2]) == ["docker", "run"]:
        argv = [*argv[:2], "--pull=never", *argv[2:]]
    return subprocess.run(argv, **kwargs)


@contextmanager
def isolated_database(image_id: str):
    from scripts.rehearsal_postgres import one_off_postgres

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise EvidenceError("U10_POSTGRES_IMAGE_PIN_INVALID")
    with one_off_postgres(
        database="u10_counterfactual",
        image=image_id,
        command_runner=local_only_command,
    ) as endpoint:
        engine = create_engine(
            URL.create(
                "postgresql+psycopg",
                username=endpoint.username,
                password=endpoint.password,
                host=endpoint.host,
                port=endpoint.port,
                database=endpoint.database,
            ),
            connect_args={"connect_timeout": 5},
            poolclass=NullPool,
        )
        try:
            yield engine
        finally:
            engine.dispose()


@contextmanager
def restored_inventory(engine, source):
    """Internal seam; live callers receive engine only from isolated_database."""
    verify_source_projection(source)
    with engine.connect() as connection:
        if connection.dialect.name not in {"postgresql", "sqlite"}:
            raise EvidenceError("U10_DATABASE_DIALECT_INVALID")
        connection.execute(
            text(
                "CREATE TEMP TABLE lot_history (lot_hist_id TEXT PRIMARY KEY, "
                "lot_id TEXT NOT NULL, wafer_id TEXT NOT NULL, "
                "chamber_id TEXT NOT NULL, "
                "step_id TEXT NOT NULL, track_in_at TEXT NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TEMP TABLE metrology (metrology_id TEXT PRIMARY KEY, "
                "lot_id TEXT NOT NULL, wafer_id TEXT NOT NULL, step_id TEXT NOT NULL)"
            )
        )
        for table, columns in (
            (
                "lot_history",
                (
                    "lot_hist_id",
                    "lot_id",
                    "wafer_id",
                    "chamber_id",
                    "step_id",
                    "track_in_at",
                ),
            ),
            ("metrology", ("metrology_id", "lot_id", "wafer_id", "step_id")),
        ):
            statement = (
                f"INSERT INTO {table} ({','.join(columns)}) "
                f"VALUES ({','.join(':'+c for c in columns)})"
            )
            connection.execute(
                text(statement),
                [{k: row[k] for k in columns} for row in source["tables"][table]],
            )
        connection.commit()
        if connection.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        else:
            connection.execute(text("PRAGMA query_only = ON"))
        try:
            yield connection
        finally:
            connection.rollback()
