from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from neo4j.exceptions import Neo4jError
from psycopg import errors as psycopg_errors
from sqlalchemy.exc import DBAPIError

from app.common.tool_deadlines import READ_TOOL_CALLER_DEADLINE_SECONDS
from app.common.tool_timeouts import (
    NEO4J_TRANSACTION_TIMEOUT_CODE,
    NEO4J_TRANSACTION_TIMEOUT_CODES,
    POSTGRES_STATEMENT_TIMEOUT_CODE,
    apply_postgres_statement_timeout,
    neo4j_timeout_error,
    postgres_timeout_error,
)


class _CapturingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement: object, params: dict[str, object]) -> None:
        self.calls.append((str(statement), params))


def test_caller_deadline_canonical_value_is_eight_seconds() -> None:
    assert READ_TOOL_CALLER_DEADLINE_SECONDS == 8.0


def test_postgres_timeout_is_transaction_local_and_parameterized() -> None:
    connection = _CapturingConnection()

    apply_postgres_statement_timeout(connection, timeout_seconds=0.125)  # type: ignore[arg-type]

    assert connection.calls == [
        (
            "SELECT set_config('statement_timeout', :timeout_ms, true)",
            {"timeout_ms": "125"},
        )
    ]


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan"), 0.0001])
def test_postgres_timeout_rejects_invalid_or_sub_millisecond_values(
    value: float,
) -> None:
    with pytest.raises(ValueError):
        apply_postgres_statement_timeout(  # type: ignore[arg-type]
            _CapturingConnection(),
            timeout_seconds=value,
        )


def test_postgres_timeout_maps_only_query_canceled_sqlstate() -> None:
    raw = psycopg_errors.QueryCanceled("cancelled raw detail")
    mapped = postgres_timeout_error(raw)

    assert mapped is not None
    assert mapped.reason_code == POSTGRES_STATEMENT_TIMEOUT_CODE
    assert str(mapped) == POSTGRES_STATEMENT_TIMEOUT_CODE

    wrapped = DBAPIError(
        "secret sql",
        {"secret": "value"},
        SimpleNamespace(sqlstate="57014"),
        False,
    )
    mapped_wrapped = postgres_timeout_error(wrapped)
    assert mapped_wrapped is not None
    assert mapped_wrapped.reason_code == POSTGRES_STATEMENT_TIMEOUT_CODE

    assert postgres_timeout_error(RuntimeError("57014 in a message")) is None
    non_timeout = DBAPIError(
        "secret sql",
        None,
        SimpleNamespace(sqlstate="42501"),
        False,
    )
    assert postgres_timeout_error(non_timeout) is None


@pytest.mark.parametrize("code", sorted(NEO4J_TRANSACTION_TIMEOUT_CODES))
def test_neo4j_timeout_maps_only_exact_allowlisted_codes(code: str) -> None:
    error = Neo4jError._hydrate_neo4j(code=code, message="secret query detail")

    mapped = neo4j_timeout_error(error)

    assert mapped is not None
    assert mapped.reason_code == NEO4J_TRANSACTION_TIMEOUT_CODE
    assert str(mapped) == NEO4J_TRANSACTION_TIMEOUT_CODE


@pytest.mark.parametrize(
    "code",
    [
        "Neo.ClientError.Security.Forbidden",
        "Neo.TransientError.Transaction.DeadlockDetected",
        "Neo.ClientError.Statement.SyntaxError",
    ],
)
def test_neo4j_non_timeout_errors_are_not_misclassified(code: str) -> None:
    error = Neo4jError._hydrate_neo4j(code=code, message="secret query detail")

    assert neo4j_timeout_error(error) is None
    assert neo4j_timeout_error(TimeoutError("not a server code")) is None


def test_reserved_tool_call_sentinel_has_no_automatic_recovery_writer() -> None:
    """CM-4.8: sentinel은 예약·exact finalize·예산 집계 외에 쓰지 않는다."""

    repository = Path(__file__).resolve().parents[2] / "app" / "agent" / "repository.py"
    source = repository.read_text(encoding="utf-8")
    tree = ast.parse(source)
    references: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if any(
            isinstance(child, ast.Name) and child.id == "RESERVED_ERROR_MSG"
            for child in ast.walk(node)
        ):
            references.add(node.name)

    assert references == {
        "reserve_tool_call",
        "finalize_tool_call",
        "count_tool_calls_for_budget",
    }
    assert "DELETE FROM agent_tool_call" not in source
