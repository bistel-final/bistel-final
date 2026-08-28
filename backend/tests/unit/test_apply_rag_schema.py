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


def test_schema_targets_match_load_targets_after_b_1_4() -> None:
    """`V5-B-1.4` 이후 schema·load 대상은 모두 세 DB다.

    `kosa_text2sql`도 RAG 운영 검증 대상이 되었으므로 schema target을 더 넓은
    과거 계약으로 고정하면 안 된다.
    """

    import load_rag_documents
    import postgres_transition

    assert runner.ALLOWED_RAG_DATABASES == postgres_transition.B_SCHEMA_TARGETS
    assert (
        load_rag_documents.ALLOWED_RAG_DATABASES
        == postgres_transition.B_LOADED_RAG_TARGETS
    )
    assert load_rag_documents.ALLOWED_RAG_DATABASES == runner.ALLOWED_RAG_DATABASES
    assert runner.ALLOWED_RAG_DATABASES == frozenset(
        {"kosa_agent", "kosa_agent_e2e", "kosa_text2sql"}
    )


def test_every_schema_target_parses() -> None:
    """allowlist에 있는 target은 CLI가 전부 받는다."""

    for database in sorted(runner.ALLOWED_RAG_DATABASES):
        args = runner.parse_args(
            ["--database", database, "--confirm-target", database]
        )
        assert args.database == database


@pytest.mark.parametrize("flag", ["--database", "--confirm-target"])
def test_an_unknown_database_is_refused_by_each_flag(flag: str) -> None:
    """**두 flag를 따로 본다.** 한쪽만 allowlist를 잃어도 다른 쪽이 가려버린다."""

    argv = ["--database", "kosa_agent", "--confirm-target", "kosa_agent"]
    argv[argv.index(flag) + 1] = "kosa"
    with pytest.raises(SystemExit):
        runner.parse_args(argv)


def test_an_unknown_database_is_refused_at_runtime() -> None:
    with pytest.raises(runner.TargetValidationError):
        runner.validate_rag_target("kosa")


def test_the_refusal_message_lists_the_actual_allowlist() -> None:
    """문구를 손으로 적으면 allowlist와 또 어긋난다."""

    with pytest.raises(runner.TargetValidationError) as caught:
        runner.validate_rag_target("kosa")
    message = str(caught.value)
    for database in runner.ALLOWED_RAG_DATABASES:
        assert database in message
