from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, URL

from app.common.config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    READONLY_PASSWORD,
    READONLY_USER,
)


def create_postgres_url(username: str, password: str) -> URL:
    return URL.create(
        drivername="postgresql+psycopg",
        username=username,
        password=password,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
    )


engine = create_engine(
    create_postgres_url(
        username=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    ),
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"connect_timeout": 5},
)

readonly_engine = create_engine(
    create_postgres_url(
        username=READONLY_USER,
        password=READONLY_PASSWORD,
    ),
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"connect_timeout": 5},
)


def get_db_connection() -> Iterator[Connection]:
    with engine.connect() as connection:
        yield connection


def get_readonly_connection() -> Iterator[Connection]:
    with readonly_engine.connect() as connection:
        yield connection


def dispose_engines() -> None:
    engine.dispose()
    readonly_engine.dispose()
