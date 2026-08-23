"""Safely apply the runtime-only 002 Agent schema migration.

The supported mutation path is this runner.  The SQL file intentionally keeps
only target/empty-data guards; transaction-scoped write exclusion is owned by
the runner so direct ``psql -f`` execution is not supported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apply_reference_extensions import (
    BASE_TABLES,
    REFERENCE_TABLES,
    REFERENCE_VIEW,
    ReferenceExtensionError,
    _canonical_hash,
    _engine_for,
    _exclusive_artifact_lock,
    _json_safe,
    _result_rows,
    _single_row,
    _timezone_text,
    acquire_advisory_lock,
    validate_change_reference,
)
from apply_reference_extensions import (
    load_marker as load_reference_marker,
)
from apply_reference_extensions import (
    postcheck_database as postcheck_reference_database,
)
from db_target import (
    ALLOWED_DATABASES,
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
)
from dotenv import load_dotenv
from manifest_v3 import (
    VerificationError,
    atomic_save_json,
    resolve_bootstrap_manifest_path,
    scan_for_sensitive_values,
)
from mutation_runtime import (
    MutationRuntimeError,
    prepare_transaction,
    resolve_exclusive_mode,
)
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
MIGRATION_PATH = (
    REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
)
MARKER_ROOT = BOOTSTRAP_ROOT / "markers"
REPORT_ROOT = BOOTSTRAP_ROOT / "reports"

RUNTIME_DATABASES = frozenset({"kosa_agent", "kosa_agent_e2e"})
RUNTIME_TABLES = (
    "action_delivery",
    "agent_prediction",
    "agent_prediction_review",
    "agent_run",
    "agent_run_action",
    "agent_run_alarm",
    "agent_tool_call",
    "approval_request",
    "audit_log",
)
EXPECTED_ALL_TABLES = frozenset({*BASE_TABLES, *REFERENCE_TABLES, *RUNTIME_TABLES})
PARTIAL_INDEX_VALUES = {
    "ux_agent_run_incident_active": {"RUNNING", "WAITING_APPROVAL"},
    "ux_agent_run_action_created": {"CREATED"},
    "ux_agent_run_action_incident": {"CREATED"},
    "ux_agent_run_alarm_representative": set(),
}
EXPECTED_INDEX_NAMES = frozenset(
    {
        "action_delivery_pkey",
        "agent_prediction_pkey",
        "agent_prediction_review_pkey",
        "agent_run_pkey",
        "agent_run_action_pkey",
        "agent_run_alarm_pkey",
        "agent_tool_call_pkey",
        "agent_tool_call_agent_run_id_call_seq_key",
        "approval_request_pkey",
        "approval_request_action_id_key",
        "audit_log_pkey",
        *PARTIAL_INDEX_VALUES,
    }
)
EXPECTED_INDEX_COLUMNS = {
    "action_delivery_pkey": ("action_id", "channel"),
    "agent_prediction_pkey": ("agent_run_id",),
    "agent_prediction_review_pkey": ("review_id",),
    "agent_run_pkey": ("agent_run_id",),
    "agent_run_action_pkey": ("agent_run_id",),
    "agent_run_alarm_pkey": ("agent_run_id", "alarm_source", "alarm_id"),
    "agent_tool_call_pkey": ("tool_call_id",),
    "agent_tool_call_agent_run_id_call_seq_key": ("agent_run_id", "call_seq"),
    "approval_request_pkey": ("approval_id",),
    "approval_request_action_id_key": ("action_id",),
    "audit_log_pkey": ("audit_id",),
    "ux_agent_run_incident_active": ("lot_id", "chamber_id"),
    "ux_agent_run_action_created": ("action_id",),
    "ux_agent_run_action_incident": ("lot_id", "chamber_id"),
    "ux_agent_run_alarm_representative": ("agent_run_id",),
}
EXPECTED_SEQUENCE_NAMES = frozenset(
    {"agent_prediction_review_review_id_seq", "audit_log_audit_id_seq"}
)
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")


@dataclass(frozen=True)
class ColumnContract:
    name: str
    data_type: str
    nullable: bool
    default: str | None = None


def _column(
    name: str, data_type: str, nullable: bool, default: str | None = None
) -> ColumnContract:
    return ColumnContract(name, data_type, nullable, default)


EXPECTED_TABLE_COLUMNS: dict[str, tuple[ColumnContract, ...]] = {
    "agent_run": (
        _column("agent_run_id", "character varying(20)", False),
        _column("thread_id", "character varying(36)", False),
        _column("retry_of_run_id", "character varying(20)", True),
        _column("lot_id", "character varying(20)", False),
        _column("chamber_id", "character varying(24)", False),
        _column("requested_alarm_source", "character varying(10)", False),
        _column("requested_alarm_id", "character varying(24)", False),
        _column("representative_alarm_source", "character varying(10)", False),
        _column("representative_alarm_id", "character varying(24)", False),
        _column("status", "character varying(20)", False),
        _column("autonomy_level", "smallint", False),
        _column("action", "character varying(20)", True),
        _column("severity", "character varying(10)", True),
        _column("llm_model", "character varying(64)", True),
        _column("prompt_version", "character varying(40)", True),
        _column("evidence", "jsonb", True),
        _column("input_tokens", "integer", True),
        _column("output_tokens", "integer", True),
        _column("latency_ms", "integer", True),
        _column("started_at", "timestamp with time zone", False, "now()"),
        _column("ended_at", "timestamp with time zone", True),
    ),
    "agent_run_alarm": (
        _column("agent_run_id", "character varying(20)", False),
        _column("alarm_source", "character varying(10)", False),
        _column("alarm_id", "character varying(24)", False),
        _column("is_representative", "boolean", False, "false"),
    ),
    "agent_prediction": (
        _column("agent_run_id", "character varying(20)", False),
        _column("predicted_fault_code", "character varying(10)", False),
        _column("confidence", "numeric(4,3)", False),
        _column("cause_summary", "text", False),
        _column("evidence", "jsonb", False),
        _column("llm_model", "character varying(64)", False),
        _column("prompt_version", "character varying(40)", False),
        _column("created_at", "timestamp with time zone", False, "now()"),
    ),
    "agent_prediction_review": (
        _column(
            "review_id",
            "bigint",
            False,
            "nextval('agent_prediction_review_review_id_seq'::regclass)",
        ),
        _column("agent_run_id", "character varying(20)", False),
        _column("reviewed_fault_code", "character varying(10)", True),
        _column("disposition", "character varying(16)", False),
        _column("label_source", "character varying(16)", False),
        _column("reviewer", "character varying(40)", False),
        _column("reviewed_at", "timestamp with time zone", False, "now()"),
        _column("comment", "text", True),
    ),
    "agent_run_action": (
        _column("agent_run_id", "character varying(20)", False),
        _column("action_id", "character varying(20)", False),
        _column("link_role", "character varying(8)", False),
        _column("lot_id", "character varying(20)", False),
        _column("chamber_id", "character varying(24)", False),
        _column("trigger_alarm_source", "character varying(10)", False),
        _column("trigger_alarm_id", "character varying(24)", False),
        _column("linked_at", "timestamp with time zone", False, "now()"),
    ),
    "agent_tool_call": (
        _column("tool_call_id", "character varying(29)", False),
        _column("agent_run_id", "character varying(20)", False),
        _column("call_seq", "integer", False),
        _column("tool_name", "character varying(40)", False),
        _column("input", "jsonb", True),
        _column("output", "jsonb", True),
        _column("status", "character varying(10)", False),
        _column("latency_ms", "integer", True),
        _column("called_at", "timestamp with time zone", False, "now()"),
        _column("error_msg", "text", True),
    ),
    "approval_request": (
        _column("approval_id", "character varying(20)", False),
        _column("action_id", "character varying(20)", False),
        _column("agent_run_id", "character varying(20)", False),
        _column(
            "status", "character varying(12)", False, "'PENDING'::character varying"
        ),
        _column("requested_at", "timestamp with time zone", False, "now()"),
        _column("decided_by", "character varying(40)", True),
        _column("decided_at", "timestamp with time zone", True),
        _column("decision_comment", "character varying(1000)", True),
    ),
    "action_delivery": (
        _column("action_id", "character varying(20)", False),
        _column("channel", "character varying(10)", False),
        _column("status", "character varying(10)", False),
        _column("request_hash", "character(64)", False),
        _column("attempt_count", "integer", False, "0"),
        _column("provider_message_id", "text", True),
        _column("started_at", "timestamp with time zone", True),
        _column("completed_at", "timestamp with time zone", True),
        _column("last_error", "text", True),
        _column("result", "jsonb", True),
    ),
    "audit_log": (
        _column(
            "audit_id", "bigint", False, "nextval('audit_log_audit_id_seq'::regclass)"
        ),
        _column("occurred_at", "timestamp with time zone", False, "now()"),
        _column("actor_type", "character varying(10)", False),
        _column("actor_id", "character varying(40)", True),
        _column("event_type", "character varying(32)", False),
        _column("entity_type", "character varying(16)", False),
        _column("entity_id", "character varying(20)", False),
        _column("before_json", "jsonb", True),
        _column("after_json", "jsonb", True),
        _column("detail", "text", True),
    ),
}

EXPECTED_CONSTRAINT_COUNTS = {
    "agent_run": Counter({"p": 1, "f": 1, "c": 19}),
    "agent_run_alarm": Counter({"p": 1, "f": 1, "c": 2}),
    "agent_prediction": Counter({"p": 1, "f": 1, "c": 5}),
    "agent_prediction_review": Counter({"p": 1, "f": 1, "c": 5}),
    "agent_run_action": Counter({"p": 1, "f": 2, "c": 6}),
    "agent_tool_call": Counter({"p": 1, "u": 1, "f": 1, "c": 5}),
    "approval_request": Counter({"p": 1, "u": 1, "f": 2, "c": 4}),
    "action_delivery": Counter({"p": 1, "f": 1, "c": 7}),
    "audit_log": Counter({"p": 1, "c": 4}),
}

TABLES_SQL = """/* agent-runtime:tables */
SELECT c.relname AS object_name, c.relkind
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
ORDER BY c.relname
"""
COLUMNS_SQL = """/* agent-runtime:columns */
SELECT c.relname AS table_name, a.attnum AS ordinal_position,
       a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS column_default
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""
CONSTRAINTS_SQL = """/* agent-runtime:constraints */
SELECT t.relname AS table_name, con.conname AS constraint_name,
       con.contype AS constraint_type, pg_get_constraintdef(con.oid, true) AS definition
FROM pg_constraint con JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname = ANY(%s)
ORDER BY t.relname, con.conname
"""
INDEXES_SQL = """/* agent-runtime:indexes */
SELECT t.relname AS table_name, i.relname AS index_name,
       pg_get_indexdef(i.oid) AS definition,
       pg_get_expr(x.indpred, x.indrelid) AS predicate
FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname = ANY(%s)
ORDER BY t.relname, i.relname
"""
SEQUENCES_SQL = """/* agent-runtime:sequences */
SELECT seq.relname AS sequence_name
FROM pg_class seq
JOIN pg_namespace n ON n.oid = seq.relnamespace
JOIN pg_depend dep ON dep.objid = seq.oid AND dep.deptype IN ('a', 'i')
JOIN pg_class owner_table ON owner_table.oid = dep.refobjid
WHERE n.nspname = 'public' AND seq.relkind = 'S'
  AND owner_table.relname = ANY(%s)
ORDER BY seq.relname
"""
ACTION_COUNT_SQL = "SELECT count(*) AS row_count FROM public.action_history"
ALARM_COUNT_SQL = f"SELECT count(*) AS row_count FROM public.{REFERENCE_VIEW}"
ALL_TABLES_SQL = """SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"""

FORBIDDEN_SQL = re.compile(
    r"\b(?:DROP|TRUNCATE|DELETE|UPDATE|INSERT|COPY|ALTER)\b", re.I
)
IF_NOT_EXISTS = re.compile(r"\bIF\s+NOT\s+EXISTS\b", re.I)
LEGACY_ALARM = re.compile(r"\bfdc_alarm\b", re.I)


class AgentRuntimeError(RuntimeError):
    exit_code = 2
    default_reason_code = "CONTRACT_INVALID"

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code or self.default_reason_code


class AgentRuntimeStateError(AgentRuntimeError):
    exit_code = 3
    default_reason_code = "SCHEMA_STATE_INVALID"


class AgentRuntimeArtifactError(AgentRuntimeError):
    exit_code = 5
    default_reason_code = "ARTIFACT_INVALID"


class _RehearsalRollback(Exception):
    pass


@dataclass(frozen=True)
class RuntimeInspection:
    state: str
    inventory: tuple[tuple[str, str], ...]
    signature: Mapping[str, Any] | None
    schema_signature_sha256: str | None


@dataclass(frozen=True)
class RuntimePostcheck:
    signature: Mapping[str, Any]
    schema_signature_sha256: str
    action_history_rows: int
    alarm_event_rows: int


def _strip_comments(sql: str) -> str:
    return re.sub(r"/\*.*?\*/|--[^\r\n]*", " ", sql, flags=re.S)


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL while preserving single-quoted and dollar-quoted bodies."""

    body = _strip_comments(sql)
    statements: list[str] = []
    current: list[str] = []
    single = False
    dollar_tag: str | None = None
    index = 0
    while index < len(body):
        if dollar_tag is not None:
            if body.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
                continue
            current.append(body[index])
            index += 1
            continue
        character = body[index]
        if character == "'":
            current.append(character)
            if single and index + 1 < len(body) and body[index + 1] == "'":
                current.append("'")
                index += 2
                continue
            single = not single
            index += 1
            continue
        if not single and character == "$":
            match = re.match(r"\$[A-Za-z_0-9]*\$", body[index:])
            if match:
                dollar_tag = match.group(0)
                current.append(dollar_tag)
                index += len(dollar_tag)
                continue
        if character == ";" and not single:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(character)
        index += 1
    if single or dollar_tag is not None:
        raise AgentRuntimeError("002 SQL literal이 닫히지 않았습니다")
    if "".join(current).strip():
        raise AgentRuntimeError("002 SQL 마지막 문장에 세미콜론이 없습니다")
    return statements


def load_and_validate_sql(path: Path = MIGRATION_PATH) -> tuple[str, list[str]]:
    try:
        sql = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentRuntimeError("002 migration SQL을 읽을 수 없습니다") from exc
    body = _strip_comments(sql)
    if (
        FORBIDDEN_SQL.search(body)
        or IF_NOT_EXISTS.search(body)
        or LEGACY_ALARM.search(body)
    ):
        raise AgentRuntimeError(
            "002 migration에 금지된 DML/legacy/완화 구문이 있습니다"
        )
    if "LOCK TABLE" in body.upper():
        raise AgentRuntimeError("002 SQL은 transaction lock을 직접 소유할 수 없습니다")
    statements = split_sql_statements(sql)
    counts = Counter(
        "do"
        if statement.upper().startswith("DO ")
        else "table"
        if statement.upper().startswith("CREATE TABLE")
        else "index"
        if statement.upper().startswith("CREATE UNIQUE INDEX")
        else "other"
        for statement in statements
    )
    if counts != Counter({"table": 9, "index": 4, "do": 1}):
        raise AgentRuntimeError("002 migration 객체 수가 계약과 다릅니다")
    normalized = " ".join(body.lower().split())
    if (
        "current_database() not in ('kosa_agent', 'kosa_agent_e2e')" not in normalized
        or "select count(*) from action_history" not in normalized
    ):
        raise AgentRuntimeError("002 SQL 선두 target/action guard가 없습니다")
    return sql, statements


def migration_sha256(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


def _require_runtime_target(target: BootstrapTarget) -> None:
    if target.database not in RUNTIME_DATABASES or target.profile != "runtime":
        raise AgentRuntimeStateError(
            "002는 runtime profile에만 적용할 수 있습니다",
            reason_code="PROFILE_NOT_ALLOWED",
        )


def _prepare_transaction(
    connection: Any, target: BootstrapTarget, *, readonly: bool
) -> None:
    _require_runtime_target(target)
    prepare_transaction(
        connection,
        target,
        readonly=readonly,
        acquire_lock=acquire_advisory_lock,
    )


def lock_action_history(connection: Any) -> None:
    connection.exec_driver_sql("LOCK TABLE action_history IN SHARE MODE")


def action_history_count(connection: Any) -> int:
    row = _single_row(
        connection.exec_driver_sql(ACTION_COUNT_SQL), label="action count"
    )
    return int(row["row_count"])


def alarm_event_count(connection: Any) -> int:
    row = _single_row(connection.exec_driver_sql(ALARM_COUNT_SQL), label="alarm count")
    return int(row["row_count"])


def _actual_table_set(connection: Any) -> set[str]:
    return {
        str(row["table_name"])
        for row in _result_rows(connection.exec_driver_sql(ALL_TABLES_SQL))
    }


def validate_prerequisites(connection: Any, target: BootstrapTarget) -> tuple[int, int]:
    _require_runtime_target(target)
    actual = _actual_table_set(connection)
    reference_tables = frozenset({*BASE_TABLES, *REFERENCE_TABLES})
    if not reference_tables.issubset(actual) or actual - reference_tables - set(
        RUNTIME_TABLES
    ):
        raise AgentRuntimeStateError(
            "corrected/reference schema가 runtime 계약과 다릅니다",
            reason_code="MISSING_BASE",
        )
    rows = action_history_count(connection)
    if rows != 0:
        raise AgentRuntimeStateError(
            "runtime action_history가 비어 있지 않습니다",
            reason_code="ACTION_PRESENT",
        )
    reference = postcheck_reference_database(connection, action_rows_before=0)
    return rows, reference.alarm_event_rows


def build_schema_signature(connection: Any) -> dict[str, Any]:
    tables = list(RUNTIME_TABLES)
    return {
        "columns": [
            _json_safe(dict(row))
            for row in _result_rows(connection.exec_driver_sql(COLUMNS_SQL, (tables,)))
        ],
        "constraints": [
            _json_safe(dict(row))
            for row in _result_rows(
                connection.exec_driver_sql(CONSTRAINTS_SQL, (tables,))
            )
        ],
        "indexes": [
            _json_safe(dict(row))
            for row in _result_rows(connection.exec_driver_sql(INDEXES_SQL, (tables,)))
        ],
        "sequences": [
            _json_safe(dict(row))
            for row in _result_rows(
                connection.exec_driver_sql(SEQUENCES_SQL, (tables,))
            )
        ],
    }


def _validate_signature_contract(signature: Mapping[str, Any]) -> str:
    columns = signature.get("columns")
    if not isinstance(columns, list):
        raise AgentRuntimeStateError("runtime column signature가 잘못됐습니다")
    for table, expected in EXPECTED_TABLE_COLUMNS.items():
        actual = tuple(
            ColumnContract(
                str(row["column_name"]),
                str(row["data_type"]),
                bool(row["nullable"]),
                str(row["column_default"])
                if row.get("column_default") is not None
                else None,
            )
            for row in columns
            if row["table_name"] == table
        )
        if actual != expected:
            raise AgentRuntimeStateError(f"{table} 컬럼 계약이 다릅니다")
    constraints = signature.get("constraints")
    if not isinstance(constraints, list):
        raise AgentRuntimeStateError("runtime constraint signature가 잘못됐습니다")
    for table, expected in EXPECTED_CONSTRAINT_COUNTS.items():
        actual = Counter(
            str(row["constraint_type"])
            for row in constraints
            if row["table_name"] == table
            and str(row["constraint_type"]) in {"p", "u", "f", "c"}
        )
        if actual != expected:
            raise AgentRuntimeStateError(f"{table} 제약 수·종류가 다릅니다")
    definitions = " ".join(str(row["definition"]).lower() for row in constraints)
    required = (
        "foreign key (retry_of_run_id) references agent_run",
        "foreign key (action_id) references action_history",
        "pending",
        "approved",
        "rejected",
        "expired",
        "blocked",
        "waiting",
        "sending",
        "sent",
        "failed",
        "canceled",
        "unknown",
        "hypothesis_generated",
    )
    if (
        any(fragment not in definitions for fragment in required)
        or "'auto'" in definitions
    ):
        raise AgentRuntimeStateError("runtime CHECK/FK 값 집합이 다릅니다")
    indexes = signature.get("indexes")
    if not isinstance(indexes, list):
        raise AgentRuntimeStateError("runtime index signature가 잘못됐습니다")
    index_names = {str(row["index_name"]) for row in indexes}
    if index_names != EXPECTED_INDEX_NAMES:
        raise AgentRuntimeStateError("runtime index allowlist가 다릅니다")
    definitions_by_index = {
        str(row["index_name"]): str(row["definition"]).lower() for row in indexes
    }
    for name, columns_for_index in EXPECTED_INDEX_COLUMNS.items():
        rendered = f"({', '.join(columns_for_index)})"
        if rendered not in definitions_by_index[name]:
            raise AgentRuntimeStateError(f"{name} 대상 컬럼이 다릅니다")
    predicates = {
        str(row["index_name"]): str(row.get("predicate") or "").lower()
        for row in indexes
        if row["index_name"] in PARTIAL_INDEX_VALUES
    }
    if set(predicates) != set(PARTIAL_INDEX_VALUES):
        raise AgentRuntimeStateError("runtime partial index가 누락됐습니다")
    for name, expected_values in PARTIAL_INDEX_VALUES.items():
        actual_values = set(re.findall(r"'([A-Z_]+)'", predicates[name].upper()))
        if actual_values != expected_values:
            raise AgentRuntimeStateError(f"{name} predicate가 다릅니다")
    if "is_representative" not in predicates["ux_agent_run_alarm_representative"]:
        raise AgentRuntimeStateError(
            "ux_agent_run_alarm_representative predicate가 다릅니다"
        )
    sequences = signature.get("sequences")
    if (
        not isinstance(sequences, list)
        or {str(row["sequence_name"]) for row in sequences} != EXPECTED_SEQUENCE_NAMES
    ):
        raise AgentRuntimeStateError("runtime sequence allowlist가 다릅니다")
    return _canonical_hash(signature)


def inspect_database(connection: Any) -> RuntimeInspection:
    rows = _result_rows(connection.exec_driver_sql(TABLES_SQL, (list(RUNTIME_TABLES),)))
    inventory = tuple(
        sorted((str(row["object_name"]), str(row["relkind"])) for row in rows)
    )
    if not inventory:
        return RuntimeInspection("ABSENT", (), None, None)
    expected = tuple(sorted((table, "r") for table in RUNTIME_TABLES))
    if inventory != expected:
        return RuntimeInspection("DRIFT", inventory, None, None)
    signature = build_schema_signature(connection)
    try:
        signature_hash = _validate_signature_contract(signature)
    except AgentRuntimeStateError:
        return RuntimeInspection("DRIFT", inventory, signature, None)
    return RuntimeInspection("PRESENT", inventory, signature, signature_hash)


def _privilege_violations(connection: Any) -> list[tuple[str, str, str]]:
    violations: list[tuple[str, str, str]] = []
    for table in RUNTIME_TABLES:
        for privilege in TABLE_PRIVILEGES:
            row = _single_row(
                connection.exec_driver_sql(
                    "SELECT has_table_privilege('public', %s, %s) AS allowed",
                    (f"public.{table}", privilege),
                ),
                label="table privilege",
            )
            if row["allowed"] is True:
                violations.append(("table", table, privilege))
    for sequence in EXPECTED_SEQUENCE_NAMES:
        for privilege in SEQUENCE_PRIVILEGES:
            row = _single_row(
                connection.exec_driver_sql(
                    "SELECT has_sequence_privilege('public', %s, %s) AS allowed",
                    (f"public.{sequence}", privilege),
                ),
                label="sequence privilege",
            )
            if row["allowed"] is True:
                violations.append(("sequence", sequence, privilege))
    return violations


def postcheck_database(connection: Any, *, alarm_rows_before: int) -> RuntimePostcheck:
    inspection = inspect_database(connection)
    if (
        inspection.state != "PRESENT"
        or inspection.signature is None
        or inspection.schema_signature_sha256 is None
    ):
        raise AgentRuntimeStateError("002 schema postcheck에 실패했습니다")
    action_rows = action_history_count(connection)
    alarm_rows = alarm_event_count(connection)
    if action_rows != 0 or alarm_rows != alarm_rows_before:
        raise AgentRuntimeStateError("002가 base/action/View 불변식을 위반했습니다")
    if _actual_table_set(connection) != EXPECTED_ALL_TABLES:
        raise AgentRuntimeStateError("runtime table 전체 allowlist가 다릅니다")
    if violations := _privilege_violations(connection):
        raise AgentRuntimeStateError(
            f"PUBLIC 권한이 남았습니다: {len(violations)}건",
            reason_code="PUBLIC_PRIVILEGE_DETECTED",
        )
    return RuntimePostcheck(
        inspection.signature,
        inspection.schema_signature_sha256,
        action_rows,
        alarm_rows,
    )


def execute_schema(connection: Any, statements: Sequence[str]) -> None:
    for statement in statements:
        # ``exec_driver_sql`` passes literal percent signs straight to psycopg,
        # which then mistakes the ``RAISE EXCEPTION ... %`` format marker in
        # the guard block for a DBAPI placeholder.  TextClause compilation
        # escapes the literal for the active PostgreSQL paramstyle.
        connection.execute(text(statement))


def marker_path(database: str, *, root: Path = MARKER_ROOT) -> Path:
    if database not in RUNTIME_DATABASES:
        raise AgentRuntimeArtifactError("runtime marker database가 허용되지 않았습니다")
    return root / f"runtime_clean.{database}.json"


def receipt_path(database: str, operation_id: str, *, root: Path = REPORT_ROOT) -> Path:
    try:
        uuid.UUID(operation_id)
    except ValueError as exc:
        raise AgentRuntimeArtifactError(
            "runtime receipt operation id가 잘못됐습니다"
        ) from exc
    return root / f"agent_runtime.{database}.{operation_id}.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentRuntimeArtifactError("runtime artifact를 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise AgentRuntimeArtifactError("runtime artifact는 object여야 합니다")
    scan_for_sensitive_values(payload)
    return payload


def _artifact_identity(target: BootstrapTarget) -> dict[str, Any]:
    manifest = _read_json(resolve_bootstrap_manifest_path("runtime", "runtime_clean"))
    reference_sql = (
        REPOSITORY_ROOT / "backend" / "migrations" / "001_reference_extensions.sql"
    ).read_text(encoding="utf-8")
    reference = load_reference_marker(
        target,
        migration_sha256=hashlib.sha256(reference_sql.encode()).hexdigest(),
    )
    if reference is None:
        raise AgentRuntimeArtifactError("001 marker가 없습니다")
    return {
        "manifest_sha256": _canonical_hash(manifest),
        "reference_marker_sha256": _canonical_hash(reference),
    }


def _marker_candidate(
    target: BootstrapTarget,
    result: RuntimePostcheck,
    *,
    migration_sha: str,
    change_reference: str,
    status: str,
    applied_at: str | None = None,
) -> dict[str, Any]:
    manifest = _read_json(resolve_bootstrap_manifest_path("runtime", "runtime_clean"))
    now = _timezone_text(datetime.now(UTC))
    return {
        "artifact_type": "runtime_clean",
        "format_version": 1,
        "database": target.database,
        "profile": target.profile,
        "status": status,
        "migration_sha256": migration_sha,
        "schema_signature_sha256": result.schema_signature_sha256,
        "dataset_epoch": manifest["dataset_epoch"],
        "correction_version": manifest["correction_version"],
        "value_normalization_version": manifest["value_normalization_version"],
        "change_reference": change_reference,
        "action_history_rows": result.action_history_rows,
        "applied_at": applied_at or now,
        "recorded_at": now,
    }


def validate_marker(
    payload: Mapping[str, Any], target: BootstrapTarget, *, migration_sha: str
) -> None:
    expected_keys = {
        "artifact_type",
        "format_version",
        "database",
        "profile",
        "status",
        "migration_sha256",
        "schema_signature_sha256",
        "dataset_epoch",
        "correction_version",
        "value_normalization_version",
        "change_reference",
        "action_history_rows",
        "applied_at",
        "recorded_at",
    }
    if (
        set(payload) != expected_keys
        or payload.get("artifact_type") != "runtime_clean"
        or payload.get("format_version") != 1
    ):
        raise AgentRuntimeArtifactError("runtime marker key/value 계약이 다릅니다")
    if (
        payload.get("database") != target.database
        or payload.get("profile") != "runtime"
        or payload.get("migration_sha256") != migration_sha
    ):
        raise AgentRuntimeArtifactError("runtime marker provenance가 다릅니다")
    if (
        payload.get("status") not in {"APPLIED", "VERIFIED_EXISTING"}
        or payload.get("action_history_rows") != 0
    ):
        raise AgentRuntimeArtifactError("runtime marker 상태가 다릅니다")
    validate_change_reference(str(payload.get("change_reference")))
    scan_for_sensitive_values(payload)


def load_marker(
    target: BootstrapTarget, *, migration_sha: str, root: Path = MARKER_ROOT
) -> dict[str, Any] | None:
    path = marker_path(target.database, root=root)
    if not path.exists():
        return None
    payload = _read_json(path)
    validate_marker(payload, target, migration_sha=migration_sha)
    return payload


def save_marker(
    payload: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    migration_sha: str,
    root: Path = MARKER_ROOT,
) -> None:
    validate_marker(payload, target, migration_sha=migration_sha)
    path = marker_path(target.database, root=root)
    lock_path = root / f".runtime_clean.{target.database}.lock"
    with _exclusive_artifact_lock(lock_path):
        existing = load_marker(target, migration_sha=migration_sha, root=root)
        if existing is not None and existing != payload:
            raise AgentRuntimeArtifactError("기존 runtime marker와 충돌합니다")
        atomic_save_json(path, dict(payload))


def _receipt_files(database: str, *, root: Path) -> list[Path]:
    return (
        sorted(root.glob(f"agent_runtime.{database}.*.json")) if root.exists() else []
    )


def _load_receipts(target: BootstrapTarget, *, root: Path) -> list[dict[str, Any]]:
    receipts = [_read_json(path) for path in _receipt_files(target.database, root=root)]
    return sorted(
        receipts,
        key=lambda item: (int(item.get("attempt", 0)), str(item.get("started_at", ""))),
    )


def _save_receipt(
    payload: Mapping[str, Any], target: BootstrapTarget, *, root: Path
) -> None:
    scan_for_sensitive_values(payload)
    atomic_save_json(
        receipt_path(target.database, str(payload["operation_id"]), root=root),
        dict(payload),
    )


def _start_receipt(
    target: BootstrapTarget,
    *,
    migration_sha: str,
    change_reference: str,
    adoption_identity: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    existing = [
        item
        for item in _load_receipts(target, root=root)
        if item.get("migration_sha256") == migration_sha
        and item.get("change_reference") == change_reference
    ]
    now = _timezone_text(datetime.now(UTC))
    for item in existing:
        if item.get("status") == "STARTED":
            stale = dict(item)
            stale.update(
                status="ABORTED", aborted_at=now, abort_reason="SUPERSEDED_BEFORE_RETRY"
            )
            _save_receipt(stale, target, root=root)
    payload = {
        "artifact_type": "agent_runtime_receipt",
        "format_version": 1,
        "operation_id": str(uuid.uuid4()),
        "attempt": max((int(item.get("attempt", 0)) for item in existing), default=0)
        + 1,
        "database": target.database,
        "profile": target.profile,
        "status": "STARTED",
        "migration_sha256": migration_sha,
        "change_reference": change_reference,
        "adoption_identity": dict(adoption_identity),
        "started_at": now,
        "action_history_rows_before": 0,
    }
    _save_receipt(payload, target, root=root)
    return payload


def _finish_receipt(
    receipt: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    result: RuntimePostcheck | None,
    root: Path,
    reason: str | None = None,
) -> dict[str, Any]:
    payload = dict(receipt)
    if result is None:
        payload.update(
            status="ABORTED",
            aborted_at=_timezone_text(datetime.now(UTC)),
            abort_reason=reason or "APPLY_FAILED",
        )
    else:
        payload.update(
            status="COMMITTED",
            committed_at=_timezone_text(datetime.now(UTC)),
            action_history_rows_after=result.action_history_rows,
            schema_signature_sha256=result.schema_signature_sha256,
        )
    _save_receipt(payload, target, root=root)
    return payload


def run_preflight(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> RuntimeInspection:
    _require_runtime_target(target)
    sql, _ = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            validate_prerequisites(connection, target)
            inspection = inspect_database(connection)
            marker = load_marker(target, migration_sha=migration_sha, root=marker_root)
            if marker is not None and inspection.state != "PRESENT":
                raise AgentRuntimeStateError(
                    "LOST_SCHEMA: marker와 runtime schema가 다릅니다",
                    reason_code="LOST_SCHEMA",
                )
            if (
                inspection.state == "PRESENT"
                and marker is not None
                and marker["schema_signature_sha256"]
                != inspection.schema_signature_sha256
            ):
                raise AgentRuntimeStateError(
                    "runtime marker와 schema signature가 다릅니다", reason_code="DRIFT"
                )
            return inspection
    finally:
        engine.dispose()


def run_rehearsal(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> RuntimePostcheck:
    if target.database != "kosa_agent_e2e":
        raise AgentRuntimeStateError("rehearse는 kosa_agent_e2e에서만 허용됩니다")
    sql, statements = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    engine = engine_factory(target)
    result: RuntimePostcheck | None = None
    try:
        try:
            with engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=False)
                lock_action_history(connection)
                _, alarm_before = validate_prerequisites(connection, target)
                if (
                    inspect_database(connection).state != "ABSENT"
                    or load_marker(
                        target, migration_sha=migration_sha, root=marker_root
                    )
                    is not None
                ):
                    raise AgentRuntimeStateError(
                        "rehearse는 runtime schema/marker가 없는 "
                        "E2E DB에서만 허용됩니다"
                    )
                execute_schema(connection, statements)
                result = postcheck_database(connection, alarm_rows_before=alarm_before)
                raise _RehearsalRollback
        except _RehearsalRollback:
            pass
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            validate_prerequisites(connection, target)
            if inspect_database(connection).state != "ABSENT":
                raise AgentRuntimeStateError(
                    "rehearse rollback 뒤 runtime 객체가 남았습니다"
                )
        if result is None:
            raise AgentRuntimeStateError("rehearse 결과가 없습니다")
        return result
    finally:
        engine.dispose()


def _exact_marker(
    marker: Mapping[str, Any], inspection: RuntimeInspection, *, migration_sha: str
) -> bool:
    return (
        marker.get("migration_sha256") == migration_sha
        and marker.get("schema_signature_sha256") == inspection.schema_signature_sha256
    )


def run_apply(
    target: BootstrapTarget,
    *,
    change_reference: str,
    recover_artifact: bool = False,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
) -> tuple[str, RuntimePostcheck]:
    _require_runtime_target(target)
    # **`V5-CM-1.6` fail-closed.** 이 runner는 구 corrected 계보 위에서만 성립했다.
    #
    # active corrected marker가 이미 history로 격리돼 현행 apply는 실제 final 환경에서
    # 성공할 수 없다. 그 우연한 실패를 **engine 생성 전** 명시적 reason으로 바꾼다.
    # V5 재기준화(mutation·receipt·marker)는 `V5-CM-3.2` 소관이다(계획 §7.2).
    raise AgentRuntimeStateError(
        "Runtime migration은 V5-CM-3.2에서 재기준화된다",
        reason_code="FINAL_RUNTIME_MIGRATION_NOT_WIRED",
    )
    change_reference = validate_change_reference(change_reference)
    sql, statements = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    adoption = _artifact_identity(target)
    engine = engine_factory(target)
    receipt: dict[str, Any] | None = None
    result: RuntimePostcheck | None = None
    alarm_before = 0
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=False)
            lock_action_history(connection)
            _, alarm_before = validate_prerequisites(connection, target)
            inspection = inspect_database(connection)
            marker = load_marker(target, migration_sha=migration_sha, root=marker_root)
            if marker is not None:
                if inspection.state != "PRESENT" or not _exact_marker(
                    marker, inspection, migration_sha=migration_sha
                ):
                    raise AgentRuntimeStateError(
                        "runtime marker와 schema가 다릅니다", reason_code="LOST_SCHEMA"
                    )
                result = postcheck_database(connection, alarm_rows_before=alarm_before)
                return "NO_OP", result
            if inspection.state == "DRIFT":
                raise AgentRuntimeStateError(
                    "부분 runtime schema를 자동 보정하지 않습니다", reason_code="DRIFT"
                )
            if inspection.state == "PRESENT":
                if not recover_artifact:
                    raise AgentRuntimeArtifactError(
                        "외부 생성 runtime schema는 자동 채택하지 않습니다"
                    )
                candidates = [
                    item
                    for item in _load_receipts(target, root=report_root)
                    if item.get("migration_sha256") == migration_sha
                    and item.get("adoption_identity") == adoption
                    and item.get("status") in {"STARTED", "COMMITTED"}
                ]
                if len(candidates) != 1:
                    raise AgentRuntimeArtifactError(
                        "복구 receipt 후보는 정확히 1건이어야 합니다"
                    )
                result = postcheck_database(connection, alarm_rows_before=alarm_before)
                receipt = candidates[0]
            elif inspection.state == "ABSENT":
                if recover_artifact:
                    raise AgentRuntimeArtifactError("복구할 runtime schema가 없습니다")
                receipt = _start_receipt(
                    target,
                    migration_sha=migration_sha,
                    change_reference=change_reference,
                    adoption_identity=adoption,
                    root=report_root,
                )
                execute_schema(connection, statements)
                result = postcheck_database(connection, alarm_rows_before=alarm_before)
            else:
                raise AgentRuntimeStateError("runtime schema 상태가 잘못됐습니다")
        if result is None or receipt is None:
            raise AgentRuntimeStateError("runtime apply 결과가 없습니다")
        if receipt.get("status") != "COMMITTED":
            receipt = _finish_receipt(receipt, target, result=result, root=report_root)
        marker = _marker_candidate(
            target,
            result,
            migration_sha=migration_sha,
            change_reference=change_reference,
            status="VERIFIED_EXISTING" if recover_artifact else "APPLIED",
            applied_at=str(receipt.get("committed_at")),
        )
        save_marker(marker, target, migration_sha=migration_sha, root=marker_root)
        return "RECOVERED" if recover_artifact else "APPLIED", result
    except Exception:
        if receipt is not None and receipt.get("status") == "STARTED":
            _finish_receipt(receipt, target, result=None, root=report_root)
        raise
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", choices=sorted(ALLOWED_DATABASES))
    parser.add_argument("--confirm-target")
    parser.add_argument("--change-ref")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--rehearse", action="store_true")
    parser.add_argument("--recover-artifact", action="store_true")
    return parser


def resolve_mode(args: argparse.Namespace) -> str:
    return resolve_exclusive_mode(
        {
            "preflight": args.preflight,
            "rehearse": args.rehearse,
            "recover": args.recover_artifact,
        },
        default_mode="apply",
        mutually_exclusive_message="runtime apply mode는 하나만 선택해야 합니다",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    try:
        mode = resolve_mode(args)
        if args.database is None:
            raise AgentRuntimeError("--database가 필요합니다")
        if args.database not in RUNTIME_DATABASES:
            raise AgentRuntimeStateError(
                "evaluation DB에는 002를 적용할 수 없습니다",
                reason_code="PROFILE_NOT_ALLOWED",
            )
        target = load_bootstrap_target(args.database)
        if mode == "preflight":
            state = run_preflight(target)
            print(f"RUNTIME_PREFLIGHT database={target.database} state={state.state}")
            return 0
        if args.confirm_target != target.database:
            raise AgentRuntimeError("--confirm-target이 대상 database와 다릅니다")
        if mode == "rehearse":
            result = run_rehearsal(target)
            print(
                f"RUNTIME_REHEARSAL_OK database={target.database} tables=9 "
                f"action_rows={result.action_history_rows}"
            )
            return 0
        if not args.change_ref:
            raise AgentRuntimeError("apply/recover에는 --change-ref가 필요합니다")
        status, result = run_apply(
            target, change_reference=args.change_ref, recover_artifact=mode == "recover"
        )
        print(
            f"RUNTIME_{status} database={target.database} tables=9 "
            f"action_rows={result.action_history_rows}"
        )
        return 0
    except (
        AgentRuntimeError,
        MutationRuntimeError,
        ReferenceExtensionError,
        VerificationError,
        TargetValidationError,
    ) as exc:
        fallback_reason = (
            "TARGET_VALIDATION_FAILED"
            if isinstance(exc, TargetValidationError)
            else "CONTRACT_INVALID"
        )
        reason = getattr(
            exc,
            "reason_code",
            getattr(exc, "code", fallback_reason),
        )
        print(
            f"RUNTIME_FAIL database={args.database or 'none'} reason={reason}",
            file=sys.stderr,
        )
        return getattr(exc, "exit_code", 2)
    except SQLAlchemyError:
        print(
            f"RUNTIME_FAIL database={args.database or 'none'} "
            "reason=CONNECT_OR_QUERY_FAILED",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
