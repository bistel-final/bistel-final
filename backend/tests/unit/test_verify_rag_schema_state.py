from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_rag_schema  # noqa: E402
import verify_rag_schema_state as verifier  # noqa: E402


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
    def __init__(
        self, *, public_privileges: list[dict[str, str]] | None = None
    ) -> None:
        self.statements: list[str] = []
        self.public_privileges = public_privileges or []

    def exec_driver_sql(self, sql: str, _parameters: Any = None) -> _Rows:
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if "table_name = 'document_corpus'" in sql:
            return _Rows([])
        if "information_schema.columns" in sql:
            return _Rows(
                [
                    {"table_name": table, "column_name": column}
                    for table, columns in {
                        "document": [
                            "doc_id",
                            "title",
                            "doc_type",
                            "model_code",
                            "source_path",
                            "version",
                            "created_at",
                        ],
                        "document_chunk": [
                            "chunk_id",
                            "doc_id",
                            "chunk_seq",
                            "section_title",
                            "content",
                            "token_cnt",
                            "embedding",
                            "metadata_json",
                        ],
                    }.items()
                    for column in columns
                ]
            )
        if "information_schema.tables" in sql:
            return _Rows([{"table_name": "document"}, {"table_name": "document_chunk"}])
        if "FROM pg_extension" in sql:
            return _Rows([{"extversion": "0.8.0"}])
        if "format_type(a.atttypid, a.atttypmod)" in sql:
            return _Rows(
                [
                    {
                        "table_name": table,
                        "column_name": column,
                        "data_type": data_type,
                    }
                    for table, columns in verifier.EXPECTED_RAG_COLUMN_TYPES.items()
                    for column, data_type in columns.items()
                ]
            )
        if "pg_get_constraintdef" in sql:
            return _Rows(
                [
                    {
                        "table_name": table,
                        "constraint_name": name,
                        "definition": definition,
                    }
                    for table, constraints in (
                        apply_rag_schema.RAG_CONSTRAINT_CONTRACT.items()
                    )
                    for name, definition in constraints.items()
                ]
            )
        if "pg_get_expr(d.adbin, d.adrelid)" in sql:
            return _Rows(
                [
                    {
                        "table_name": table,
                        "column_name": column,
                        "column_default": value,
                    }
                    for table, defaults in apply_rag_schema.RAG_DEFAULT_CONTRACT.items()
                    for column, value in defaults.items()
                ]
            )
        if "public_acl" in sql:
            return _Rows(self.public_privileges)
        if "n.nspname <> 'public'" in sql:
            return _Rows([])
        raise AssertionError(f"unexpected SQL: {normalized}")


def _target() -> SimpleNamespace:
    return SimpleNamespace(database="kosa_agent", profile="runtime")


def test_verify_connection_passes_for_matching_read_only_rag_schema() -> None:
    report = verifier.verify_connection(_Connection(), target=_target())

    assert report["status"] == "PASS"
    assert report["checks"]["vector_extension_present"] is True
    assert report["checks"]["legacy_document_corpus_absent"] is True
    assert report["checks"]["public_privilege_count"] == 0


def test_verify_connection_fails_when_public_privilege_remains() -> None:
    report = verifier.verify_connection(
        _Connection(
            public_privileges=[
                {
                    "object_type": "relation",
                    "object_name": "document",
                    "privilege_type": "SELECT",
                }
            ]
        ),
        target=_target(),
    )

    assert report["status"] == "FAIL"
    assert report["checks"]["public_privilege_count"] == 1
    with pytest.raises(verifier.RagSchemaStateError, match="public_privilege_count"):
        verifier.validate_rag_schema_state(report)


def test_verifier_sql_is_read_only() -> None:
    connection = _Connection()

    verifier.verify_connection(connection, target=_target())

    executed = "\n".join(connection.statements).upper()
    for forbidden in (
        "DROP ",
        "CREATE ",
        "ALTER ",
        "DELETE ",
        "INSERT ",
        "UPDATE ",
        "TRUNCATE ",
        "GRANT ",
        "REVOKE ",
    ):
        assert forbidden not in executed


def test_build_report_reflects_database_results() -> None:
    report = verifier.build_report(
        [
            {"database": "kosa_agent", "status": "PASS"},
            {"database": "kosa_text2sql", "status": "FAIL"},
        ]
    )

    assert report["artifact_type"] == "rag_schema_validation"
    assert report["task_id"] == "V5-B-1.1"
    assert report["status"] == "FAIL"


def test_cli_accepts_single_database_or_all() -> None:
    one = verifier.parse_args(["--database", "kosa_agent"])
    all_targets = verifier.parse_args(["--all"])

    assert one.database == "kosa_agent"
    assert all_targets.all is True
