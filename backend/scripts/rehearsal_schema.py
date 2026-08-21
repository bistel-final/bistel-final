"""Schema-only rehearsal handler for the final mentor data epoch."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

EXIT_MISMATCH = 1

EXPECTED_TABLES = frozenset(
    {
        "action_history",
        "dim_parameter",
        "evaluation",
        "fdc_trace",
        "lot_history",
        "metrology",
        "summary_alarm_history",
        "summary_data",
        "trace_alarm_history",
    }
)
EXPECTED_INDEXES = frozenset(
    {
        *(f"{table}_pkey" for table in EXPECTED_TABLES),
        "ix_evaluation_type",
        "ix_lot_history_cum",
        "ix_summary_data_key",
        "ix_trace_alarm_time",
    }
)
FORBIDDEN_SQL = re.compile(
    r"\b(?:DROP|TRUNCATE|DELETE|UPDATE|INSERT|COPY|GRANT|REVOKE|"
    r"CREATE\s+DATABASE|CREATE\s+ROLE|ALTER\s+ROLE|BEGIN|COMMIT)\b",
    re.IGNORECASE,
)
RELATIONS_SQL = """
SELECT c.relname AS object_name, c.relkind
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r','p','v','m','S','f','i','I')
ORDER BY c.relkind, c.relname
"""
EXTENSIONS_SQL = "SELECT extname FROM pg_extension ORDER BY extname"

ErrorFactory = Callable[[str, int], BaseException]
Handler = Callable[[Any, Any], None]
PostCheck = Callable[[Any, Any], None]


def _rows(result: Any) -> list[Mapping[str, Any]]:
    try:
        return list(result.mappings().all())
    except (AttributeError, TypeError) as exc:
        raise RuntimeError("invalid catalog result") from exc


def _without_comments(sql: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    while index < len(sql):
        current = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if in_string:
            output.append(current)
            if current == "'":
                if following == "'":
                    output.append(following)
                    index += 1
                else:
                    in_string = False
        elif current == "'":
            in_string = True
            output.append(current)
        elif current == "-" and following == "-":
            index += 2
            while index < len(sql) and sql[index] not in "\r\n":
                index += 1
            output.append("\n")
            continue
        elif current == "/" and following == "*":
            end = sql.find("*/", index + 2)
            if end < 0:
                raise ValueError("unclosed block comment")
            index = end + 2
            output.append(" ")
            continue
        else:
            output.append(current)
        index += 1
    if in_string:
        raise ValueError("unclosed string literal")
    return "".join(output)


def make_handlers(
    sql_bytes: bytes, error_factory: ErrorFactory
) -> tuple[Handler, PostCheck]:
    """Capture already-verified SQL bytes for composition in later load tasks."""

    try:
        sql_text = sql_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise error_factory("SCHEMA_FORBIDDEN_STATEMENT", EXIT_MISMATCH) from exc

    def fail(reason_code: str) -> None:
        raise error_factory(reason_code, EXIT_MISMATCH)

    def handler(connection: Any, _plan: Any) -> None:
        try:
            existing = _rows(connection.exec_driver_sql(RELATIONS_SQL))
        except RuntimeError:
            fail("MODE_CONTRACT_ERROR")
        if existing:
            fail("TARGET_NOT_FRESH")
        try:
            inspected_sql = _without_comments(sql_text)
        except ValueError:
            fail("SCHEMA_FORBIDDEN_STATEMENT")
        if FORBIDDEN_SQL.search(inspected_sql):
            fail("SCHEMA_FORBIDDEN_STATEMENT")
        connection.exec_driver_sql(sql_text)

    def postcheck(connection: Any, _plan: Any) -> None:
        try:
            rows = _rows(connection.exec_driver_sql(RELATIONS_SQL))
            extension_rows = _rows(connection.exec_driver_sql(EXTENSIONS_SQL))
        except RuntimeError:
            fail("MODE_CONTRACT_ERROR")
        tables = {
            str(row["object_name"]) for row in rows if str(row["relkind"]) in {"r", "p"}
        }
        indexes = {
            str(row["object_name"]) for row in rows if str(row["relkind"]) in {"i", "I"}
        }
        other = [row for row in rows if str(row["relkind"]) not in {"r", "p", "i", "I"}]
        extensions = {str(row["extname"]) for row in extension_rows}
        if (
            tables != EXPECTED_TABLES
            or indexes != EXPECTED_INDEXES
            or other
            or "vector" in extensions
        ):
            fail("MODE_CONTRACT_ERROR")

    return handler, postcheck
