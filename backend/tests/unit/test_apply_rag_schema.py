from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_rag_schema as runner  # noqa: E402


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def one(self) -> dict[str, Any]:
        if len(self.rows) != 1:
            raise LookupError("expected one row")
        return self.rows[0]


class _Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def exec_driver_sql(self, sql: str, _parameters: Any = None) -> _Rows:
        self.statements.append(" ".join(sql.split()))
        if "information_schema.tables" in sql and "IN ('document_corpus'" in sql:
            return _Rows(
                [
                    {"table_name": "document_corpus"},
                    {"table_name": "document"},
                    {"table_name": "document_chunk"},
                ]
            )
        if "count(*)" in sql:
            return _Rows([{"row_count": 7}])
        return _Rows([])


def test_schema_sql_touches_only_rag_tables() -> None:
    sql = runner.RAG_SCHEMA_SQL

    assert "DROP TABLE IF EXISTS document_chunk" in sql
    assert "DROP TABLE IF EXISTS document" in sql
    assert "DROP TABLE IF EXISTS document_corpus" in sql
    assert "CREATE TABLE document (" in sql
    assert "CREATE TABLE document_chunk (" in sql
    for forbidden in [
        "lot_history",
        "trace_alarm_history",
        "summary_alarm_history",
        "summary_data",
        "action_history",
        "agent_run",
        "r03_alarm_history",
        "nl_query_log",
        "v_alarm_event",
    ]:
        assert forbidden not in sql


def test_apply_executes_rag_schema_statements_only() -> None:
    connection = _Connection()

    runner.apply_rag_schema(connection)

    executed = "\n".join(connection.statements)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in executed
    assert "DROP TABLE IF EXISTS document_chunk" in executed
    assert "CREATE TABLE document_chunk" in executed
    assert "action_history" not in executed


def test_inspect_counts_only_rag_objects() -> None:
    counts = runner.inspect_rag_objects(_Connection())

    assert counts == {
        "document_chunk": 7,
        "document": 7,
        "document_corpus": 7,
    }


def test_cli_requires_confirm_target_to_match_database() -> None:
    args = runner.parse_args(
        ["--database", "kosa_agent", "--confirm-target", "kosa_agent"]
    )
    assert args.database == "kosa_agent"

    with pytest.raises(runner.RagSchemaError):
        runner.parse_args(
            ["--database", "kosa_agent", "--confirm-target", "kosa_agent_e2e"]
        )


def test_target_allowlist_excludes_text2sql() -> None:
    with pytest.raises(runner.TargetValidationError):
        runner.validate_rag_target("kosa_text2sql")
