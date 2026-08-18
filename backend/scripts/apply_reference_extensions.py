"""Apply the profile-neutral 001 reference-extension migration safely.

The module is importable for unit tests and does not connect by default.  A
connection is opened only for ``--preflight`` or an explicitly confirmed
mutation mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - native Windows only
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX only
    _msvcrt = None

from db_target import (
    ALLOWED_DATABASES,
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
    validate_url_components,
)
from dotenv import load_dotenv
from manifest_v3 import atomic_save_json, scan_for_sensitive_values
from mutation_runtime import prepare_transaction
from schema_lock import advisory_lock_key
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
MIGRATION_PATH = (
    REPOSITORY_ROOT / "backend" / "migrations" / "001_reference_extensions.sql"
)
MARKER_ROOT = BOOTSTRAP_ROOT / "markers"
REPORT_ROOT = BOOTSTRAP_ROOT / "reports"

REFERENCE_TABLES = (
    "document",
    "document_chunk",
    "document_corpus",
    "nl_query_log",
    "r03_alarm_history",
)
REFERENCE_VIEW = "v_alarm_event"
REFERENCE_OBJECTS = frozenset((*REFERENCE_TABLES, REFERENCE_VIEW))
BASE_TABLES = frozenset(
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
PUBLIC_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
VIEW_COLUMNS = (
    "source",
    "alarm_id",
    "occurred_at",
    "area",
    "equipment_id",
    "chamber_id",
    "parameter_id",
    "recipe_id",
    "lot_id",
    "wafer_no",
    "recipe_step_no",
    "seq_no",
    "value",
    "alarm_type",
    "lot_hist_id",
)
VIEW_COLUMN_TYPES = (
    "character varying(10)",
    "character varying(24)",
    "timestamp without time zone",
    "character varying(10)",
    "character varying(20)",
    "character varying(24)",
    "character varying(20)",
    "character varying(20)",
    "character varying(20)",
    "smallint",
    "smallint",
    "smallint",
    "numeric(12,4)",
    "character varying(10)",
    "character varying(20)",
)
EXPECTED_TABLE_COLUMNS = {
    "r03_alarm_history": (
        ("alarm_id", "character varying(24)", False),
        ("occurred_at", "timestamp without time zone", False),
        ("lot_hist_id", "character varying(20)", False),
        ("lot_id", "character varying(20)", False),
        ("equipment_id", "character varying(20)", False),
        ("chamber_id", "character varying(24)", False),
        ("parameter_id", "character varying(20)", False),
        ("recipe_step_no", "smallint", False),
        ("trigger_wafer_no", "smallint", False),
        ("member_refs", "jsonb", False),
        ("policy_version", "character varying(20)", False),
    ),
    "document_corpus": (
        ("corpus_revision", "character varying(40)", False),
        ("status", "character varying(10)", False),
        ("embedding_model_code", "character varying(64)", False),
        ("embedding_dim", "integer", False),
        ("manifest_sha256", "character(64)", False),
        ("document_count", "integer", False),
        ("chunk_count", "integer", False),
        ("created_at", "timestamp with time zone", False),
        ("activated_at", "timestamp with time zone", True),
        ("retired_at", "timestamp with time zone", True),
    ),
    "document": (
        ("corpus_revision", "character varying(40)", False),
        ("doc_id", "character varying(64)", False),
        ("title", "text", False),
        ("doc_type", "character varying(20)", False),
        ("model_code", "character varying(40)", True),
        ("source_path", "text", False),
        ("version", "character varying(40)", True),
        ("content_sha256", "character(64)", False),
    ),
    "document_chunk": (
        ("corpus_revision", "character varying(40)", False),
        ("chunk_id", "character varying(64)", False),
        ("doc_id", "character varying(64)", False),
        ("chunk_seq", "integer", False),
        ("section_title", "text", True),
        ("content", "text", False),
        ("model_code", "character varying(40)", True),
        ("embedding", "vector(1024)", False),
    ),
    "nl_query_log": (
        ("nl_query_log_id", "bigint", False),
        ("asked_at", "timestamp with time zone", False),
        ("question", "text", False),
        ("generated_sql", "text", True),
        ("outcome", "character varying(20)", False),
        ("is_valid", "boolean", False),
        ("is_rejected", "boolean", False),
        ("reject_reason", "text", True),
        ("row_cnt", "integer", True),
        ("latency_ms", "integer", True),
        ("error_msg", "text", True),
    ),
}
EXPECTED_CONSTRAINT_COUNTS = {
    "r03_alarm_history": Counter({"p": 1, "u": 1, "f": 2, "c": 3}),
    "document_corpus": Counter({"p": 1, "c": 5}),
    "document": Counter({"p": 1, "f": 1, "c": 2}),
    "document_chunk": Counter({"p": 1, "u": 1, "f": 1, "c": 1}),
    "nl_query_log": Counter({"p": 1, "c": 4}),
}
KNOWN_CONSTRAINT_TYPES = frozenset({"p", "u", "f", "c"})

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CHANGE_REFERENCE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
FORBIDDEN_SQL = re.compile(
    r"\b(?:DROP|TRUNCATE|DELETE|UPDATE|INSERT|COPY|GRANT|REVOKE|"
    r"CREATE\s+DATABASE|CREATE\s+ROLE|ALTER\s+ROLE|BEGIN|COMMIT)\b",
    re.IGNORECASE,
)
LEGACY_SQL = re.compile(r"\bfdc_alarm\b", re.IGNORECASE)
IF_NOT_EXISTS = re.compile(r"\bIF\s+NOT\s+EXISTS\b", re.IGNORECASE)

OBJECTS_SQL = """/* reference-extensions:objects */
SELECT c.relname AS object_name, c.relkind
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
ORDER BY c.relname
"""
BASE_TABLES_SQL = """/* reference-extensions:base-tables */
SELECT c.relname AS table_name
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
  AND c.relname = ANY(%s)
ORDER BY c.relname
"""
EXTENSION_SQL = """/* reference-extensions:extension */
SELECT e.extname, n.nspname AS extension_schema, e.extversion
FROM pg_extension e
JOIN pg_namespace n ON n.oid = e.extnamespace
WHERE e.extname = 'vector'
"""
AVAILABLE_EXTENSION_SQL = """/* reference-extensions:available-extension */
SELECT default_version
FROM pg_available_extensions
WHERE name = 'vector'
"""
COLUMNS_SQL = """/* reference-extensions:columns */
SELECT c.relname AS object_name, a.attnum AS ordinal_position,
       a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS column_default
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""
CONSTRAINTS_SQL = """/* reference-extensions:constraints */
SELECT t.relname AS table_name, con.conname AS constraint_name,
       con.contype AS constraint_type,
       pg_get_constraintdef(con.oid, true) AS definition
FROM pg_constraint con
JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname = ANY(%s)
ORDER BY t.relname, con.conname
"""
INDEXES_SQL = """/* reference-extensions:indexes */
SELECT t.relname AS table_name, i.relname AS index_name,
       pg_get_indexdef(i.oid) AS definition,
       pg_get_expr(x.indpred, x.indrelid) AS predicate
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname = ANY(%s)
ORDER BY t.relname, i.relname
"""
VIEW_SQL = """/* reference-extensions:view */
SELECT pg_get_viewdef(c.oid, true) AS view_definition,
       v.is_updatable
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN information_schema.views v
  ON v.table_schema = n.nspname AND v.table_name = c.relname
WHERE n.nspname = 'public' AND c.relname = 'v_alarm_event'
"""
ACTION_COUNT_SQL = """/* reference-extensions:action-count */
SELECT count(*) AS row_count FROM public.action_history
"""
VIEW_STATS_SQL = """/* reference-extensions:view-stats */
SELECT
  (SELECT count(*) FROM v_alarm_event) AS view_count,
  (SELECT count(*) FROM trace_alarm_history) AS trace_count,
  (SELECT count(*) FROM summary_alarm_history) AS summary_count,
  (SELECT count(*) FROM r03_alarm_history) AS r03_count,
  (SELECT count(*) FROM v_alarm_event WHERE source = 'TRACE') AS view_trace_count,
  (SELECT count(*) FROM v_alarm_event WHERE source = 'SUMMARY') AS view_summary_count,
  (SELECT count(*) FROM v_alarm_event WHERE source = 'R03') AS view_r03_count,
  (SELECT count(*) FROM v_alarm_event WHERE lot_hist_id IS NULL) AS null_lot_hist_count,
  (SELECT count(*) FROM (
      SELECT source, alarm_id FROM v_alarm_event
      GROUP BY source, alarm_id HAVING count(*) > 1
   ) AS duplicate_refs) AS duplicate_ref_count,
  (SELECT count(*) FROM v_alarm_event
   WHERE source NOT IN ('TRACE','SUMMARY','R03')) AS invalid_source_count,
  (SELECT count(*) FROM v_alarm_event
   WHERE alarm_type NOT IN ('IN','OOC','OOS')) AS invalid_alarm_type_count,
  (SELECT count(*) FROM v_alarm_event
   WHERE occurred_at IS NULL OR area IS NULL OR equipment_id IS NULL
      OR chamber_id IS NULL OR parameter_id IS NULL OR lot_id IS NULL
      OR alarm_type IS NULL) AS required_null_count,
  (SELECT count(*) FROM (
      SELECT lot_id, wafer_no, chamber_id FROM lot_history
      GROUP BY lot_id, wafer_no, chamber_id HAVING count(*) > 1
   ) AS duplicate_lot_keys) AS duplicate_lot_key_count
"""
VIEW_TRIGGER_SQL = """/* reference-extensions:view-triggers */
SELECT count(*) AS trigger_count
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'v_alarm_event'
  AND NOT t.tgisinternal
"""
REFERENCE_SEQUENCE_SQL = """/* reference-extensions:sequence-count */
SELECT count(*) AS sequence_count
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'S'
  AND c.relname = 'nl_query_log_nl_query_log_id_seq'
"""


class ReferenceExtensionError(RuntimeError):
    exit_code = 2
    default_reason_code = "CONTRACT_INVALID"

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code or self.default_reason_code


class ReferenceStateError(ReferenceExtensionError):
    exit_code = 3
    default_reason_code = "SCHEMA_STATE_INVALID"


class ReferenceLockError(ReferenceExtensionError):
    exit_code = 4
    default_reason_code = "LOCK_UNAVAILABLE"


class _RehearsalRollback(Exception):
    """Internal sentinel that guarantees rehearsal transaction rollback."""


class ReferenceArtifactError(ReferenceExtensionError):
    exit_code = 5
    default_reason_code = "ARTIFACT_INVALID"


@dataclass(frozen=True)
class ReferenceInspection:
    state: str
    inventory: tuple[tuple[str, str], ...]
    extension: Mapping[str, Any] | None
    signature: Mapping[str, Any] | None
    schema_signature_sha256: str | None


@dataclass(frozen=True)
class PostcheckResult:
    signature: Mapping[str, Any]
    schema_signature_sha256: str
    vector_extension_version: str
    action_history_rows: int
    alarm_event_rows: int


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _result_rows(result: Any) -> list[Mapping[str, Any]]:
    try:
        return list(result.mappings().all())
    except (AttributeError, TypeError) as exc:
        raise ReferenceStateError(
            "PostgreSQL catalog 응답 형식이 잘못됐습니다"
        ) from exc


def _single_row(result: Any, *, label: str) -> Mapping[str, Any]:
    rows = _result_rows(result)
    if len(rows) != 1:
        raise ReferenceStateError(f"{label} 응답 행 수가 잘못됐습니다")
    return rows[0]


def _strip_sql_comments(sql: str) -> str:
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
                raise ReferenceExtensionError("SQL block comment가 닫히지 않았습니다")
            index = end + 2
            output.append(" ")
            continue
        else:
            output.append(current)
        index += 1
    if in_string:
        raise ReferenceExtensionError("SQL 문자열 literal이 닫히지 않았습니다")
    return "".join(output)


def split_sql_statements(sql: str) -> list[str]:
    body = _strip_sql_comments(sql)
    if match := FORBIDDEN_SQL.search(body):
        raise ReferenceExtensionError(
            f"001 SQL에 금지문이 있습니다: {match.group(0).upper()}"
        )
    if LEGACY_SQL.search(body):
        raise ReferenceExtensionError("001 SQL은 legacy fdc_alarm을 참조할 수 없습니다")
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    index = 0
    while index < len(body):
        character = body[index]
        following = body[index + 1] if index + 1 < len(body) else ""
        if character == "'":
            current.append(character)
            if in_string and following == "'":
                current.append(following)
                index += 1
            else:
                in_string = not in_string
        elif character == ";" and not in_string:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(character)
        index += 1
    if "".join(current).strip():
        raise ReferenceExtensionError("001 SQL 마지막 문장에 세미콜론이 없습니다")
    return statements


def load_and_validate_sql(path: Path = MIGRATION_PATH) -> tuple[str, list[str]]:
    try:
        sql = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReferenceExtensionError("001 migration SQL을 읽을 수 없습니다") from exc
    statements = split_sql_statements(sql)
    counts = {
        "extension": sum(
            statement.upper().startswith("CREATE EXTENSION") for statement in statements
        ),
        "table": sum(
            statement.upper().startswith("CREATE TABLE") for statement in statements
        ),
        "index": sum(
            statement.upper().startswith("CREATE UNIQUE INDEX")
            for statement in statements
        ),
        "view": sum(
            statement.upper().startswith("CREATE VIEW") for statement in statements
        ),
    }
    if counts != {"extension": 1, "table": 5, "index": 1, "view": 1}:
        raise ReferenceExtensionError("001 migration 객체 수가 계약과 다릅니다")
    occurrences = list(IF_NOT_EXISTS.finditer(_strip_sql_comments(sql)))
    if len(occurrences) != 1 or "CREATE EXTENSION IF NOT EXISTS" not in sql.upper():
        raise ReferenceExtensionError("IF NOT EXISTS는 vector extension에만 허용됩니다")
    if "action_history" in _strip_sql_comments(sql):
        raise ReferenceExtensionError(
            "001 migration은 action_history를 참조할 수 없습니다"
        )
    return sql, statements


def _migration_sha256(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


def acquire_advisory_lock(connection: Any, database: str) -> None:
    namespace, database_id = advisory_lock_key(database)
    result = connection.exec_driver_sql(
        "SELECT pg_try_advisory_xact_lock(%s, %s) AS acquired",
        (namespace, database_id),
    )
    row = _single_row(result, label="advisory lock")
    if row.get("acquired") is not True:
        raise ReferenceLockError("다른 프로세스가 같은 DB schema를 변경 중입니다")


def _extension(connection: Any) -> Mapping[str, Any] | None:
    rows = _result_rows(connection.exec_driver_sql(EXTENSION_SQL))
    if len(rows) > 1:
        raise ReferenceStateError("vector extension catalog가 중복됐습니다")
    return _json_safe(rows[0]) if rows else None


def _available_vector_version(connection: Any) -> str | None:
    rows = _result_rows(connection.exec_driver_sql(AVAILABLE_EXTENSION_SQL))
    if len(rows) > 1:
        raise ReferenceStateError("vector extension 가용 버전 응답이 중복됐습니다")
    if not rows:
        return None
    value = rows[0].get("default_version")
    return str(value) if value else None


def build_schema_signature(connection: Any) -> dict[str, Any]:
    extension = _extension(connection)
    object_names = sorted(REFERENCE_OBJECTS)
    columns = [
        _json_safe(dict(row))
        for row in _result_rows(
            connection.exec_driver_sql(COLUMNS_SQL, (object_names,))
        )
    ]
    constraints = [
        _json_safe(dict(row))
        for row in _result_rows(
            connection.exec_driver_sql(CONSTRAINTS_SQL, (list(REFERENCE_TABLES),))
        )
    ]
    indexes = [
        _json_safe(dict(row))
        for row in _result_rows(
            connection.exec_driver_sql(INDEXES_SQL, (list(REFERENCE_TABLES),))
        )
    ]
    view_rows = _result_rows(connection.exec_driver_sql(VIEW_SQL))
    if len(view_rows) != 1:
        raise ReferenceStateError("v_alarm_event 정의를 하나로 확인할 수 없습니다")
    embedding = next(
        (
            str(row["data_type"])
            for row in columns
            if row["object_name"] == "document_chunk"
            and row["column_name"] == "embedding"
        ),
        None,
    )
    return {
        "extension": extension,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "view": _json_safe(dict(view_rows[0])),
        "embedding_type": embedding,
    }


def inspect_database(connection: Any) -> ReferenceInspection:
    base_rows = _result_rows(
        connection.exec_driver_sql(BASE_TABLES_SQL, (sorted(BASE_TABLES),))
    )
    base_tables = {str(row["table_name"]) for row in base_rows}
    if base_tables != BASE_TABLES:
        return ReferenceInspection(
            "MISSING_BASE", (), _extension(connection), None, None
        )

    object_rows = _result_rows(
        connection.exec_driver_sql(OBJECTS_SQL, (sorted(REFERENCE_OBJECTS),))
    )
    inventory = tuple(
        sorted((str(row["object_name"]), str(row["relkind"])) for row in object_rows)
    )
    extension = _extension(connection)
    if not inventory:
        return ReferenceInspection("ABSENT", (), extension, None, None)
    expected_inventory = tuple(
        sorted([*((table, "r") for table in REFERENCE_TABLES), (REFERENCE_VIEW, "v")])
    )
    if inventory != expected_inventory:
        return ReferenceInspection("DRIFT", inventory, extension, None, None)
    signature = build_schema_signature(connection)
    return ReferenceInspection(
        "PRESENT",
        inventory,
        extension,
        signature,
        _canonical_hash(signature),
    )


def action_history_count(connection: Any) -> int:
    row = _single_row(
        connection.exec_driver_sql(ACTION_COUNT_SQL), label="action_history count"
    )
    value = int(row["row_count"])
    if value < 0:  # pragma: no cover - PostgreSQL count cannot be negative
        raise ReferenceStateError("action_history 행 수가 잘못됐습니다")
    return value


def reference_sequence_count(connection: Any) -> int:
    row = _single_row(
        connection.exec_driver_sql(REFERENCE_SEQUENCE_SQL),
        label="reference sequence count",
    )
    value = int(row["sequence_count"])
    if value < 0:  # pragma: no cover - PostgreSQL count cannot be negative
        raise ReferenceStateError("reference sequence 수가 잘못됐습니다")
    return value


def _validate_signature_contract(signature: Mapping[str, Any]) -> str:
    extension = signature.get("extension")
    if not isinstance(extension, Mapping):
        raise ReferenceStateError("vector extension이 등록되지 않았습니다")
    if (
        extension.get("extname") != "vector"
        or extension.get("extension_schema") != "public"
        or not extension.get("extversion")
    ):
        raise ReferenceStateError("vector extension name/schema/version이 다릅니다")
    if signature.get("embedding_type") != "vector(1024)":
        raise ReferenceStateError("document_chunk.embedding이 vector(1024)가 아닙니다")
    columns = signature.get("columns")
    if not isinstance(columns, list):
        raise ReferenceStateError("reference column signature가 잘못됐습니다")
    for table, expected in EXPECTED_TABLE_COLUMNS.items():
        actual = tuple(
            (
                str(row["column_name"]),
                str(row["data_type"]),
                bool(row["nullable"]),
            )
            for row in columns
            if row["object_name"] == table
        )
        if actual != expected:
            raise ReferenceStateError(f"{table} 컬럼 계약이 다릅니다")

    constraints = signature.get("constraints")
    if not isinstance(constraints, list):
        raise ReferenceStateError("reference constraint signature가 잘못됐습니다")
    for table, expected_counts in EXPECTED_CONSTRAINT_COUNTS.items():
        actual_counts = Counter(
            str(row["constraint_type"])
            for row in constraints
            if row["table_name"] == table
            and str(row["constraint_type"]) in KNOWN_CONSTRAINT_TYPES
        )
        if actual_counts != expected_counts:
            raise ReferenceStateError(f"{table} 제약 수·종류가 다릅니다")
    definitions = " ".join(str(row["definition"]).lower() for row in constraints)
    required_fragments = (
        "r03-[0-9a-f]{20}",
        "foreign key (lot_hist_id) references lot_history",
        "foreign key (parameter_id) references dim_parameter",
        "unique (lot_hist_id, parameter_id, recipe_step_no, policy_version)",
        "staging",
        "active",
        "retired",
        "embedding_dim = 1024",
        "spec",
        "manual",
        "troubleshoot",
        "foreign key (corpus_revision, doc_id) references document",
        "success",
        "policy_rejected",
        "validation_failed",
        "db_error",
    )
    if any(fragment not in definitions for fragment in required_fragments):
        raise ReferenceStateError("reference CHECK/FK/UNIQUE 정의가 다릅니다")

    indexes = signature.get("indexes")
    if not isinstance(indexes, list):
        raise ReferenceStateError("reference index signature가 잘못됐습니다")
    active_indexes = [
        row for row in indexes if row["index_name"] == "ux_document_corpus_active"
    ]
    if len(active_indexes) != 1:
        raise ReferenceStateError("ACTIVE 부분 고유 인덱스가 없습니다")
    active_index = active_indexes[0]
    if (
        "unique index" not in str(active_index["definition"]).lower()
        or "status" not in str(active_index.get("predicate", "")).lower()
        or "active" not in str(active_index.get("predicate", "")).lower()
    ):
        raise ReferenceStateError("ACTIVE 부분 고유 인덱스 정의가 다릅니다")
    return str(extension["extversion"])


def postcheck_database(connection: Any, *, action_rows_before: int) -> PostcheckResult:
    inspection = inspect_database(connection)
    if inspection.state != "PRESENT" or inspection.signature is None:
        raise ReferenceStateError("001 객체 적용 후 상태가 PRESENT가 아닙니다")
    extension_version = _validate_signature_contract(inspection.signature)
    action_rows_after = action_history_count(connection)
    if action_rows_after != action_rows_before:
        raise ReferenceStateError("001 적용 중 action_history 행 수가 변했습니다")

    stats = _single_row(
        connection.exec_driver_sql(VIEW_STATS_SQL), label="v_alarm_event postcheck"
    )
    integer_stats = {key: int(value) for key, value in stats.items()}
    expected_total = (
        integer_stats["trace_count"]
        + integer_stats["summary_count"]
        + integer_stats["r03_count"]
    )
    if integer_stats["view_count"] != expected_total:
        raise ReferenceStateError("v_alarm_event 전체 행 수가 source 합과 다릅니다")
    branch_pairs = (
        ("view_trace_count", "trace_count"),
        ("view_summary_count", "summary_count"),
        ("view_r03_count", "r03_count"),
    )
    if any(integer_stats[left] != integer_stats[right] for left, right in branch_pairs):
        raise ReferenceStateError("v_alarm_event source별 행 수가 원본과 다릅니다")
    zero_fields = (
        "null_lot_hist_count",
        "duplicate_ref_count",
        "invalid_source_count",
        "invalid_alarm_type_count",
        "required_null_count",
        "duplicate_lot_key_count",
    )
    if any(integer_stats[field] != 0 for field in zero_fields):
        raise ReferenceStateError("v_alarm_event 데이터 계약 검증에 실패했습니다")

    view_columns = [
        row
        for row in inspection.signature["columns"]
        if row["object_name"] == REFERENCE_VIEW
    ]
    actual_names = tuple(str(row["column_name"]) for row in view_columns)
    actual_types = tuple(str(row["data_type"]) for row in view_columns)
    if actual_names != VIEW_COLUMNS or actual_types != VIEW_COLUMN_TYPES:
        raise ReferenceStateError("v_alarm_event 컬럼 이름·순서·타입이 다릅니다")
    view_contract = inspection.signature["view"]
    if str(view_contract.get("is_updatable", "")).upper() != "NO":
        raise ReferenceStateError("v_alarm_event가 read-only View가 아닙니다")
    trigger_row = _single_row(
        connection.exec_driver_sql(VIEW_TRIGGER_SQL), label="v_alarm_event trigger"
    )
    if int(trigger_row["trigger_count"]) != 0:
        raise ReferenceStateError("v_alarm_event에 사용자 trigger가 있습니다")

    return PostcheckResult(
        signature=inspection.signature,
        schema_signature_sha256=str(inspection.schema_signature_sha256),
        vector_extension_version=extension_version,
        action_history_rows=action_rows_after,
        alarm_event_rows=integer_stats["view_count"],
    )


def public_privilege_violations(connection: Any) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    for object_name in sorted(REFERENCE_OBJECTS):
        qualified = f"public.{object_name}"
        for privilege in PUBLIC_PRIVILEGES:
            row = _single_row(
                connection.exec_driver_sql(
                    "SELECT has_table_privilege('public', %s, %s) AS granted",
                    (qualified, privilege),
                ),
                label="PUBLIC privilege",
            )
            if row.get("granted") is True:
                violations.append((object_name, privilege))
    return violations


def _acquire_file_lock(file: BinaryIO) -> None:
    if sys.platform == "win32":
        if _msvcrt is None:
            raise OSError("Windows lock backend unavailable")
        file.seek(0)
        if file.read(1) != b"\0":
            file.seek(0)
            file.write(b"\0")
            file.flush()
        file.seek(0)
        _msvcrt.locking(file.fileno(), _msvcrt.LK_LOCK, 1)
    else:
        if _fcntl is None:
            raise OSError("POSIX lock backend unavailable")
        _fcntl.flock(file.fileno(), _fcntl.LOCK_EX)


def _release_file_lock(file: BinaryIO) -> None:
    if sys.platform == "win32":
        if _msvcrt is None:
            raise OSError("Windows lock backend unavailable")
        file.seek(0)
        _msvcrt.locking(file.fileno(), _msvcrt.LK_UNLCK, 1)
    else:
        if _fcntl is None:
            raise OSError("POSIX lock backend unavailable")
        _fcntl.flock(file.fileno(), _fcntl.LOCK_UN)


@contextmanager
def _exclusive_artifact_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise ReferenceArtifactError("artifact lock에 symlink를 사용할 수 없습니다")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        lock_file: BinaryIO = os.fdopen(descriptor, "a+b")
        _acquire_file_lock(lock_file)
    except OSError as exc:
        raise ReferenceArtifactError("artifact lock을 사용할 수 없습니다") from exc
    try:
        yield
    finally:
        try:
            _release_file_lock(lock_file)
        finally:
            lock_file.close()


def _timezone_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReferenceArtifactError("artifact 시각은 timezone-aware여야 합니다")
    return value.isoformat()


def validate_change_reference(value: str | None) -> str:
    if not isinstance(value, str) or not CHANGE_REFERENCE_PATTERN.fullmatch(value):
        raise ReferenceExtensionError("change_reference 형식이 잘못됐습니다")
    return value


def marker_path(database: str, *, root: Path = MARKER_ROOT) -> Path:
    if database not in ALLOWED_DATABASES:
        raise ReferenceArtifactError("허용되지 않은 marker database입니다")
    return root / f"reference_extensions.{database}.json"


def _artifact_lock_path(database: str, *, root: Path) -> Path:
    return root / f".reference_extensions.{database}.lock"


def receipt_path(database: str, operation_id: str, *, root: Path = REPORT_ROOT) -> Path:
    if database not in ALLOWED_DATABASES:
        raise ReferenceArtifactError("허용되지 않은 receipt database입니다")
    try:
        parsed = uuid.UUID(operation_id)
    except (ValueError, AttributeError) as exc:
        raise ReferenceArtifactError(
            "receipt operation_id 형식이 잘못됐습니다"
        ) from exc
    return root / f"reference_extensions.{database}.{parsed}.json"


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise ReferenceArtifactError(f"{label}에 symlink를 사용할 수 없습니다")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceArtifactError(f"{label}를 안전하게 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise ReferenceArtifactError(f"{label} 최상위 값은 object여야 합니다")
    return payload


def _validate_artifact_common(
    payload: Mapping[str, Any], target: BootstrapTarget, *, migration_sha256: str
) -> None:
    if (
        payload.get("database") != target.database
        or payload.get("profile") != target.profile
        or payload.get("migration_sha256") != migration_sha256
    ):
        raise ReferenceArtifactError("artifact provenance가 현재 계약과 다릅니다")
    if not SHA256_PATTERN.fullmatch(str(payload.get("migration_sha256", ""))):
        raise ReferenceArtifactError("artifact migration SHA-256 형식이 잘못됐습니다")
    validate_change_reference(payload.get("change_reference"))
    scan_for_sensitive_values(payload)


def validate_marker(
    payload: Mapping[str, Any], target: BootstrapTarget, *, migration_sha256: str
) -> None:
    required = {
        "artifact_type",
        "format_version",
        "database",
        "profile",
        "status",
        "migration_sha256",
        "schema_signature_sha256",
        "vector_extension_version",
        "change_reference",
        "action_history_rows_before",
        "action_history_rows_after",
        "alarm_event_rows",
        "applied_at",
        "recorded_at",
    }
    if set(payload) != required:
        raise ReferenceArtifactError("marker key 집합이 잘못됐습니다")
    _validate_artifact_common(payload, target, migration_sha256=migration_sha256)
    if (
        payload.get("artifact_type") != "reference_extensions"
        or payload.get("format_version") != 1
        or payload.get("status") not in {"APPLIED", "VERIFIED_EXISTING"}
        or not SHA256_PATTERN.fullmatch(str(payload.get("schema_signature_sha256", "")))
        or not payload.get("vector_extension_version")
    ):
        raise ReferenceArtifactError("marker 계약 값이 잘못됐습니다")
    before = payload.get("action_history_rows_before")
    after = payload.get("action_history_rows_after")
    if not isinstance(before, int) or before < 0 or after != before:
        raise ReferenceArtifactError("marker action_history 불변 증거가 잘못됐습니다")
    alarm_rows = payload.get("alarm_event_rows")
    if not isinstance(alarm_rows, int) or alarm_rows < 0:
        raise ReferenceArtifactError("marker alarm_event_rows가 잘못됐습니다")
    for field in ("applied_at", "recorded_at"):
        try:
            parsed = datetime.fromisoformat(str(payload[field]))
        except ValueError as exc:
            raise ReferenceArtifactError("marker 시각 형식이 잘못됐습니다") from exc
        _timezone_text(parsed)


def load_marker(
    target: BootstrapTarget,
    *,
    migration_sha256: str,
    root: Path = MARKER_ROOT,
) -> dict[str, Any] | None:
    path = marker_path(target.database, root=root)
    if not path.exists():
        return None
    payload = _read_json(path, label="marker")
    validate_marker(payload, target, migration_sha256=migration_sha256)
    return payload


def save_marker(
    payload: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    migration_sha256: str,
    root: Path = MARKER_ROOT,
) -> None:
    validate_marker(payload, target, migration_sha256=migration_sha256)
    path = marker_path(target.database, root=root)
    with _exclusive_artifact_lock(_artifact_lock_path(target.database, root=root)):
        existing = load_marker(target, migration_sha256=migration_sha256, root=root)
        if existing is not None and existing != payload:
            raise ReferenceArtifactError("기존 marker와 새 기록이 충돌합니다")
        try:
            atomic_save_json(path, dict(payload))
        except OSError as exc:
            raise ReferenceArtifactError(
                "marker를 원자적으로 저장할 수 없습니다"
            ) from exc


def validate_receipt(
    payload: Mapping[str, Any], target: BootstrapTarget, *, migration_sha256: str
) -> None:
    _validate_artifact_common(payload, target, migration_sha256=migration_sha256)
    if (
        payload.get("artifact_type") != "reference_extensions_receipt"
        or payload.get("format_version") != 1
        or payload.get("status") not in {"STARTED", "COMMITTED", "ABORTED"}
    ):
        raise ReferenceArtifactError("receipt 계약 값이 잘못됐습니다")
    try:
        uuid.UUID(str(payload.get("operation_id")))
    except ValueError as exc:
        raise ReferenceArtifactError(
            "receipt operation_id 형식이 잘못됐습니다"
        ) from exc
    attempt = payload.get("attempt")
    before = payload.get("action_history_rows_before")
    if not isinstance(attempt, int) or attempt < 1:
        raise ReferenceArtifactError("receipt attempt가 잘못됐습니다")
    if not isinstance(before, int) or before < 0:
        raise ReferenceArtifactError(
            "receipt action_history_rows_before가 잘못됐습니다"
        )
    try:
        _timezone_text(datetime.fromisoformat(str(payload.get("started_at"))))
    except ValueError as exc:
        raise ReferenceArtifactError("receipt started_at 형식이 잘못됐습니다") from exc
    status = payload["status"]
    if status == "STARTED":
        expected = {
            "artifact_type",
            "format_version",
            "operation_id",
            "attempt",
            "database",
            "profile",
            "status",
            "migration_sha256",
            "change_reference",
            "started_at",
            "action_history_rows_before",
            "object_inventory_before",
            "vector_extension_before",
        }
    elif status == "COMMITTED":
        expected = {
            "artifact_type",
            "format_version",
            "operation_id",
            "attempt",
            "database",
            "profile",
            "status",
            "migration_sha256",
            "change_reference",
            "started_at",
            "committed_at",
            "action_history_rows_before",
            "action_history_rows_after",
            "object_inventory_before",
            "vector_extension_before",
            "schema_signature_sha256",
            "vector_extension_version",
            "alarm_event_rows",
        }
        if payload.get("action_history_rows_after") != before:
            raise ReferenceArtifactError(
                "receipt action_history 불변 증거가 잘못됐습니다"
            )
        if not SHA256_PATTERN.fullmatch(
            str(payload.get("schema_signature_sha256", ""))
        ):
            raise ReferenceArtifactError("receipt schema signature가 잘못됐습니다")
        try:
            _timezone_text(datetime.fromisoformat(str(payload.get("committed_at"))))
        except ValueError as exc:
            raise ReferenceArtifactError(
                "receipt committed_at 형식이 잘못됐습니다"
            ) from exc
    else:
        expected = {
            "artifact_type",
            "format_version",
            "operation_id",
            "attempt",
            "database",
            "profile",
            "status",
            "migration_sha256",
            "change_reference",
            "started_at",
            "aborted_at",
            "abort_reason",
            "action_history_rows_before",
            "object_inventory_before",
            "vector_extension_before",
        }
        try:
            _timezone_text(datetime.fromisoformat(str(payload.get("aborted_at"))))
        except ValueError as exc:
            raise ReferenceArtifactError(
                "receipt aborted_at 형식이 잘못됐습니다"
            ) from exc
    if set(payload) != expected:
        raise ReferenceArtifactError("receipt key 집합이 잘못됐습니다")


def _receipt_files(database: str, *, root: Path) -> list[Path]:
    if database not in ALLOWED_DATABASES:
        raise ReferenceArtifactError("허용되지 않은 receipt database입니다")
    if not root.exists():
        return []
    return sorted(root.glob(f"reference_extensions.{database}.*.json"))


def load_receipts(
    target: BootstrapTarget,
    *,
    migration_sha256: str,
    root: Path = REPORT_ROOT,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for path in _receipt_files(target.database, root=root):
        payload = _read_json(path, label="receipt")
        validate_receipt(payload, target, migration_sha256=migration_sha256)
        receipts.append(payload)
    return sorted(receipts, key=lambda item: (item["attempt"], item["started_at"]))


def save_receipt(
    payload: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    migration_sha256: str,
    root: Path = REPORT_ROOT,
) -> None:
    validate_receipt(payload, target, migration_sha256=migration_sha256)
    path = receipt_path(target.database, str(payload["operation_id"]), root=root)
    with _exclusive_artifact_lock(_artifact_lock_path(target.database, root=root)):
        try:
            atomic_save_json(path, dict(payload))
        except OSError as exc:
            raise ReferenceArtifactError(
                "receipt를 원자적으로 저장할 수 없습니다"
            ) from exc


def _matching_receipts(
    target: BootstrapTarget,
    *,
    migration_sha256: str,
    change_reference: str,
    root: Path,
) -> list[dict[str, Any]]:
    return [
        receipt
        for receipt in load_receipts(
            target, migration_sha256=migration_sha256, root=root
        )
        if receipt["change_reference"] == change_reference
    ]


def start_receipt(
    target: BootstrapTarget,
    *,
    migration_sha256: str,
    change_reference: str,
    action_rows_before: int,
    inspection: ReferenceInspection,
    root: Path = REPORT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = now or datetime.now(UTC)
    existing = _matching_receipts(
        target,
        migration_sha256=migration_sha256,
        change_reference=change_reference,
        root=root,
    )
    started = [receipt for receipt in existing if receipt["status"] == "STARTED"]
    if len(started) > 1:
        raise ReferenceArtifactError("미종결 STARTED receipt가 2건 이상입니다")
    if started:
        stale = dict(started[0])
        stale["status"] = "ABORTED"
        stale["aborted_at"] = _timezone_text(timestamp)
        stale["abort_reason"] = "SUPERSEDED_BEFORE_RETRY"
        save_receipt(stale, target, migration_sha256=migration_sha256, root=root)
    attempt = max((int(receipt["attempt"]) for receipt in existing), default=0) + 1
    payload = {
        "artifact_type": "reference_extensions_receipt",
        "format_version": 1,
        "operation_id": str(uuid.uuid4()),
        "attempt": attempt,
        "database": target.database,
        "profile": target.profile,
        "status": "STARTED",
        "migration_sha256": migration_sha256,
        "change_reference": change_reference,
        "started_at": _timezone_text(timestamp),
        "action_history_rows_before": action_rows_before,
        "object_inventory_before": [list(item) for item in inspection.inventory],
        "vector_extension_before": _json_safe(inspection.extension),
    }
    save_receipt(payload, target, migration_sha256=migration_sha256, root=root)
    return payload


def commit_receipt(
    receipt: Mapping[str, Any],
    result: PostcheckResult,
    target: BootstrapTarget,
    *,
    migration_sha256: str,
    root: Path = REPORT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = dict(receipt)
    payload.update(
        {
            "status": "COMMITTED",
            "committed_at": _timezone_text(now or datetime.now(UTC)),
            "action_history_rows_after": result.action_history_rows,
            "schema_signature_sha256": result.schema_signature_sha256,
            "vector_extension_version": result.vector_extension_version,
            "alarm_event_rows": result.alarm_event_rows,
        }
    )
    save_receipt(payload, target, migration_sha256=migration_sha256, root=root)
    return payload


def abort_receipt(
    receipt: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    migration_sha256: str,
    reason: str,
    root: Path = REPORT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    if receipt.get("status") != "STARTED":
        return dict(receipt)
    payload = dict(receipt)
    payload.update(
        {
            "status": "ABORTED",
            "aborted_at": _timezone_text(now or datetime.now(UTC)),
            "abort_reason": reason,
        }
    )
    save_receipt(payload, target, migration_sha256=migration_sha256, root=root)
    return payload


def build_marker(
    receipt: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    status: str,
    migration_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if receipt.get("status") != "COMMITTED":
        raise ReferenceArtifactError("COMMITTED receipt만 marker로 승격할 수 있습니다")
    if status not in {"APPLIED", "VERIFIED_EXISTING"}:
        raise ReferenceArtifactError("marker status가 잘못됐습니다")
    recorded_at = _timezone_text(now or datetime.now(UTC))
    payload = {
        "artifact_type": "reference_extensions",
        "format_version": 1,
        "database": target.database,
        "profile": target.profile,
        "status": status,
        "migration_sha256": migration_sha256,
        "schema_signature_sha256": receipt["schema_signature_sha256"],
        "vector_extension_version": receipt["vector_extension_version"],
        "change_reference": receipt["change_reference"],
        "action_history_rows_before": receipt["action_history_rows_before"],
        "action_history_rows_after": receipt["action_history_rows_after"],
        "alarm_event_rows": receipt["alarm_event_rows"],
        "applied_at": receipt["committed_at"],
        "recorded_at": recorded_at,
    }
    validate_marker(payload, target, migration_sha256=migration_sha256)
    return payload


def execute_schema(connection: Any, statements: Sequence[str]) -> None:
    for statement in statements:
        connection.exec_driver_sql(statement)


def _engine_for(target: BootstrapTarget) -> Engine:
    url = target.create_url()
    validate_url_components(url, target)
    return create_engine(
        url,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def _prepare_transaction(
    connection: Any, target: BootstrapTarget, *, readonly: bool
) -> None:
    prepare_transaction(
        connection,
        target,
        readonly=readonly,
        acquire_lock=acquire_advisory_lock,
    )


def run_preflight(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> ReferenceInspection:
    sql, _ = load_and_validate_sql()
    migration_sha = _migration_sha256(sql)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            inspection = inspect_database(connection)
            marker = load_marker(
                target, migration_sha256=migration_sha, root=marker_root
            )
            if inspection.state == "ABSENT" and marker is not None:
                raise ReferenceStateError(
                    "LOST_SCHEMA: marker가 있지만 001 객체가 없습니다"
                )
            if inspection.state == "PRESENT" and marker is not None:
                if (
                    marker["schema_signature_sha256"]
                    != inspection.schema_signature_sha256
                ):
                    raise ReferenceStateError(
                        "DRIFT: marker와 schema signature가 다릅니다"
                    )
            if inspection.state == "ABSENT" and inspection.extension is None:
                if _available_vector_version(connection) is None:
                    raise ReferenceStateError("vector extension을 사용할 수 없습니다")
            return inspection
    finally:
        engine.dispose()


def run_rehearsal(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> PostcheckResult:
    """Run the real apply/postcheck path and always roll the transaction back."""

    if target.database != "kosa_agent_e2e":
        raise ReferenceStateError("rehearse는 kosa_agent_e2e에서만 허용됩니다")
    sql, statements = load_and_validate_sql()
    migration_sha = _migration_sha256(sql)
    engine = engine_factory(target)
    result: PostcheckResult | None = None
    action_rows_before: int | None = None
    extension_before: Mapping[str, Any] | None = None
    try:
        try:
            with engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=False)
                inspection = inspect_database(connection)
                extension_before = inspection.extension
                marker = load_marker(
                    target, migration_sha256=migration_sha, root=marker_root
                )
                if inspection.state != "ABSENT" or marker is not None:
                    raise ReferenceStateError(
                        "rehearse는 marker와 001 객체가 없는 E2E DB에서만 허용됩니다"
                    )
                if reference_sequence_count(connection) != 0:
                    raise ReferenceStateError(
                        "rehearse 전 reference sequence가 없어야 합니다"
                    )
                action_rows_before = action_history_count(connection)
                execute_schema(connection, statements)
                result = postcheck_database(
                    connection, action_rows_before=action_rows_before
                )
                if reference_sequence_count(connection) != 1:
                    raise ReferenceStateError(
                        "rehearse 중 reference sequence가 정확히 1개여야 합니다"
                    )
                raise _RehearsalRollback
        except _RehearsalRollback:
            pass

        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            inspection_after = inspect_database(connection)
            if inspection_after.state != "ABSENT":
                raise ReferenceStateError("rehearse rollback 뒤 001 객체가 남았습니다")
            if inspection_after.extension != extension_before:
                raise ReferenceStateError(
                    "rehearse rollback 뒤 extension 상태가 바뀌었습니다"
                )
            if reference_sequence_count(connection) != 0:
                raise ReferenceStateError("rehearse rollback 뒤 sequence가 남았습니다")
            if (
                action_rows_before is None
                or action_history_count(connection) != action_rows_before
            ):
                raise ReferenceStateError(
                    "rehearse rollback 뒤 action_history가 바뀌었습니다"
                )
            if (
                load_marker(target, migration_sha256=migration_sha, root=marker_root)
                is not None
            ):
                raise ReferenceStateError(
                    "rehearse는 success marker를 만들 수 없습니다"
                )
        if result is None:  # pragma: no cover - guarded by rollback sentinel
            raise ReferenceStateError("rehearse postcheck 결과가 없습니다")
        return result
    finally:
        engine.dispose()


def _recover_receipt(
    target: BootstrapTarget,
    *,
    migration_sha256: str,
    change_reference: str,
    current_action_rows: int,
    result: PostcheckResult,
    receipt_root: Path,
) -> dict[str, Any]:
    receipts = _matching_receipts(
        target,
        migration_sha256=migration_sha256,
        change_reference=change_reference,
        root=receipt_root,
    )
    candidates = [
        receipt for receipt in receipts if receipt["status"] in {"STARTED", "COMMITTED"}
    ]
    if len(candidates) != 1:
        raise ReferenceArtifactError(
            "복구할 STARTED/COMMITTED receipt가 정확히 1건이어야 합니다"
        )
    receipt = candidates[0]
    if receipt["action_history_rows_before"] != current_action_rows:
        raise ReferenceStateError(
            "receipt 이후 action_history가 바뀌어 복구할 수 없습니다"
        )
    if receipt["status"] == "STARTED":
        return commit_receipt(
            receipt,
            result,
            target,
            migration_sha256=migration_sha256,
            root=receipt_root,
        )
    if (
        receipt["schema_signature_sha256"] != result.schema_signature_sha256
        or receipt["action_history_rows_after"] != current_action_rows
    ):
        raise ReferenceStateError("COMMITTED receipt와 현재 schema가 다릅니다")
    return receipt


def run_apply_or_recover(
    target: BootstrapTarget,
    *,
    recover_marker: bool,
    change_reference: str,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    receipt_root: Path = REPORT_ROOT,
) -> str:
    change_reference = validate_change_reference(change_reference)
    sql, statements = load_and_validate_sql()
    migration_sha = _migration_sha256(sql)
    engine = engine_factory(target)
    started_receipt: dict[str, Any] | None = None
    committed_receipt: dict[str, Any] | None = None
    database_transaction_committed = False
    marker_status = "VERIFIED_EXISTING" if recover_marker else "APPLIED"
    try:
        try:
            with engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=False)
                inspection = inspect_database(connection)
                marker = load_marker(
                    target, migration_sha256=migration_sha, root=marker_root
                )
                if inspection.state == "MISSING_BASE":
                    raise ReferenceStateError("MISSING_BASE: base 9 table이 필요합니다")
                if inspection.state == "DRIFT":
                    raise ReferenceStateError("DRIFT: 001 객체가 부분 적용됐습니다")
                if inspection.state == "ABSENT" and marker is not None:
                    raise ReferenceStateError(
                        "LOST_SCHEMA: marker가 있지만 001 객체가 없습니다"
                    )
                if inspection.state == "PRESENT" and marker is not None:
                    if recover_marker:
                        raise ReferenceStateError("정상 marker가 이미 존재합니다")
                    if (
                        marker["schema_signature_sha256"]
                        != inspection.schema_signature_sha256
                    ):
                        raise ReferenceStateError(
                            "DRIFT: marker와 schema signature가 다릅니다"
                        )
                    return "NO_OP"
                action_rows_before = action_history_count(connection)
                if recover_marker:
                    if inspection.state != "PRESENT" or marker is not None:
                        raise ReferenceStateError(
                            "marker 복구는 PRESENT + marker 없음에서만 허용됩니다"
                        )
                    result = postcheck_database(
                        connection, action_rows_before=action_rows_before
                    )
                    committed_receipt = _recover_receipt(
                        target,
                        migration_sha256=migration_sha,
                        change_reference=change_reference,
                        current_action_rows=action_rows_before,
                        result=result,
                        receipt_root=receipt_root,
                    )
                else:
                    if inspection.state == "PRESENT":
                        raise ReferenceStateError(
                            "receipt 없는 PRESENT는 복구할 수 없습니다"
                        )
                    if inspection.state != "ABSENT":
                        raise ReferenceStateError("001을 적용할 수 없는 DB 상태입니다")
                    started_receipt = start_receipt(
                        target,
                        migration_sha256=migration_sha,
                        change_reference=change_reference,
                        action_rows_before=action_rows_before,
                        inspection=inspection,
                        root=receipt_root,
                    )
                    execute_schema(connection, statements)
                    result = postcheck_database(
                        connection, action_rows_before=action_rows_before
                    )
            database_transaction_committed = True
            if committed_receipt is None:
                if started_receipt is None:  # pragma: no cover - guarded branches
                    raise ReferenceArtifactError(
                        "STARTED receipt가 준비되지 않았습니다"
                    )
                committed_receipt = commit_receipt(
                    started_receipt,
                    result,
                    target,
                    migration_sha256=migration_sha,
                    root=receipt_root,
                )
        except Exception:
            if (
                started_receipt is not None
                and committed_receipt is None
                and not database_transaction_committed
            ):
                try:
                    abort_receipt(
                        started_receipt,
                        target,
                        migration_sha256=migration_sha,
                        reason="TRANSACTION_ROLLED_BACK",
                        root=receipt_root,
                    )
                except ReferenceArtifactError:
                    pass
            raise

        marker_payload = build_marker(
            committed_receipt,
            target,
            status=marker_status,
            migration_sha256=migration_sha,
        )
        save_marker(
            marker_payload,
            target,
            migration_sha256=migration_sha,
            root=marker_root,
        )
        return marker_status
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", choices=sorted(ALLOWED_DATABASES), required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--rehearse", action="store_true")
    parser.add_argument("--recover-marker", action="store_true")
    parser.add_argument("--confirm-target", choices=sorted(ALLOWED_DATABASES))
    parser.add_argument("--change-ref")
    return parser


def resolve_mode(args: argparse.Namespace) -> str:
    selected_modes = sum(
        bool(mode)
        for mode in (
            args.dry_run,
            args.preflight,
            args.rehearse,
            args.recover_marker,
        )
    )
    if selected_modes > 1:
        raise ReferenceExtensionError(
            "dry-run·preflight·rehearse·recover-marker는 함께 쓸 수 없습니다"
        )
    if args.dry_run or args.preflight:
        if args.confirm_target or args.change_ref:
            raise ReferenceExtensionError(
                "dry-run/preflight는 mutation 옵션과 함께 쓸 수 없습니다"
            )
        return "dry-run" if args.dry_run else "preflight"
    if args.confirm_target is None:
        raise ReferenceExtensionError(
            "접속하지 않았습니다. 명시적인 실행 모드가 필요합니다"
        )
    if args.confirm_target != args.database:
        raise ReferenceExtensionError("--confirm-target과 --database가 다릅니다")
    if args.rehearse:
        if args.database != "kosa_agent_e2e":
            raise ReferenceExtensionError("rehearse는 kosa_agent_e2e에서만 허용됩니다")
        if args.change_ref is not None:
            raise ReferenceExtensionError("rehearse는 change-ref를 사용하지 않습니다")
        return "rehearse"
    validate_change_reference(args.change_ref)
    return "recover-marker" if args.recover_marker else "apply"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        mode = resolve_mode(args)
        load_and_validate_sql()
        if mode == "dry-run":
            print("DRY_RUN_OK objects=6 data_mutations=0")
            return 0
        load_dotenv(REPOSITORY_ROOT / ".env", override=False)
        target = load_bootstrap_target(args.database)
        if mode == "preflight":
            inspection = run_preflight(target)
            print(f"PREFLIGHT_OK database={target.database} state={inspection.state}")
        elif mode == "rehearse":
            result = run_rehearsal(target)
            print(
                "REHEARSAL_OK "
                f"database={target.database} "
                f"action_rows={result.action_history_rows} "
                f"alarm_rows={result.alarm_event_rows} "
                f"vector={result.vector_extension_version} "
                "rolled_back=true"
            )
        else:
            status = run_apply_or_recover(
                target,
                recover_marker=mode == "recover-marker",
                change_reference=args.change_ref,
            )
            print(f"REFERENCE_EXTENSIONS_OK database={target.database} status={status}")
        return 0
    except (ReferenceExtensionError, TargetValidationError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 2)
    except SQLAlchemyError:
        print(
            "ReferenceExtensionConnectionError: PostgreSQL 작업에 실패했습니다",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
