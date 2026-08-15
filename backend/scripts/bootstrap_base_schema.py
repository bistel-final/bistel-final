"""Safely bootstrap the empty kosa_0813 PostgreSQL base schema.

Implementation and unit tests never connect by default.  A connection is made
only for an explicit ``--preflight`` or target-confirmed mutation mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
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
    set_and_validate_public_search_path,
    validate_connected_identity,
    validate_url_components,
)
from dotenv import load_dotenv
from manifest_v3 import (
    DATASET_EPOCH,
    DATASET_EPOCH_PATH,
    HASH_ALGORITHM,
    MANIFEST_FORMAT_VERSION,
    PROFILE_APPLIES_TO,
    REPOSITORY_ROOT,
    atomic_save_json,
    load_dataset_epoch,
    scan_for_sensitive_values,
    validate_manifest_schema,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
BASE_SCHEMA_SQL_PATH = BOOTSTRAP_ROOT / "001_base_schema.sql"
MANIFEST_ROOT = BOOTSTRAP_ROOT / "manifests"
MARKER_ROOT = BOOTSTRAP_ROOT / "markers"
SOURCE_MEMBER_PATH = "kosa_0813/클린데이터셋/03_schema_clean.sql"
SOURCE_MEMBER_SHA256 = (
    "bf6cc620065850a0e15e052179a1ba25b9fc3bec30966ca2480d77fb27212d9b"
)
CORRECTION_VERSION = "base-schema-v1"
EMPTY_ROWS_SHA256 = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"

# Immutable coordination contract. Changing it breaks cross-version locking.
BASE_SCHEMA_LOCK_NAMESPACE = 1_111_905_090  # 0x42465342 = "BFSB"
DATABASE_LOCK_ID = {
    "kosa_agent": 1,
    "kosa_agent_e2e": 2,
    "kosa_text2sql": 3,
}

EXPECTED_TABLE_COUNT = 9
EXPECTED_EXPLICIT_INDEX_COUNT = 4
EXPECTED_CONSTRAINT_INDEX_COUNT = 9
EXPECTED_TOTAL_INDEX_COUNT = 13

FORBIDDEN_SQL = re.compile(
    r"\b(?:DROP|TRUNCATE|DELETE|UPDATE|INSERT|COPY|GRANT|REVOKE|"
    r"CREATE\s+DATABASE|CREATE\s+ROLE|ALTER\s+ROLE|BEGIN|COMMIT)\b",
    re.IGNORECASE,
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SQL_WHITESPACE = re.compile(r"\s+")
SQL_CAST = re.compile(
    r"::(?:character varying|text|boolean|smallint|integer|numeric(?:\(\d+,\d+\))?)",
    re.IGNORECASE,
)
SQL_LITERAL = re.compile(r"'((?:''|[^'])*)'")

OBJECTS_SQL = """/* base-schema:objects */
SELECT c.relname AS object_name, c.relkind
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S')
ORDER BY c.relname
"""
COLUMNS_SQL = """/* base-schema:columns */
SELECT c.relname AS table_name, a.attnum AS ordinal_position,
       a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS column_default
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""
CONSTRAINTS_SQL = """/* base-schema:constraints */
SELECT t.relname AS table_name, con.contype,
       ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
             ORDER BY k.ord) AS columns,
       rt.relname AS reference_table,
       ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
             ORDER BY k.ord) AS reference_columns,
       con.confupdtype AS update_action, con.confdeltype AS delete_action,
       pg_get_constraintdef(con.oid, true) AS definition
FROM pg_constraint con
JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
LEFT JOIN pg_class rt ON rt.oid = con.confrelid
WHERE n.nspname = 'public' AND con.contype IN ('p','u','f','c')
ORDER BY t.relname, con.contype, con.oid
"""
INDEXES_SQL = """/* base-schema:indexes */
SELECT t.relname AS table_name, i.relname AS index_name,
       am.amname AS method, x.indisunique AS is_unique,
       x.indisprimary AS is_primary, con.oid IS NOT NULL AS is_constraint,
       ARRAY(SELECT a.attname FROM unnest(x.indkey) WITH ORDINALITY k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
             WHERE k.attnum > 0 ORDER BY k.ord) AS columns
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_am am ON am.oid = i.relam
LEFT JOIN pg_constraint con
       ON con.conindid = i.oid AND con.contype IN ('p','u','x')
WHERE n.nspname = 'public' AND t.relkind IN ('r','p')
ORDER BY t.relname, i.relname
"""


class BootstrapError(RuntimeError):
    exit_code = 2


class DatabaseStateError(BootstrapError):
    exit_code = 3


class AdvisoryLockError(BootstrapError):
    exit_code = 4


class MarkerError(BootstrapError):
    exit_code = 5


class _RollbackProbe(RuntimeError):
    pass


@dataclass(frozen=True)
class ColumnContract:
    name: str
    data_type: str
    nullable: bool = True
    default: str | None = None


def _columns(*specs: tuple[str, str, bool, str | None]) -> list[ColumnContract]:
    return [ColumnContract(*spec) for spec in specs]


BASE_COLUMNS: dict[str, list[ColumnContract]] = {
    "action_history": _columns(
        ("action_id", "character varying(20)", False, None),
        ("lot_id", "character varying(20)", True, None),
        ("recipe_step_name", "character varying(40)", True, None),
        ("equipment_id", "character varying(20)", True, None),
        ("chamber_id", "character varying(24)", True, None),
        ("trigger_alarm_lot_hist_id", "character varying(20)", True, None),
        ("action_code", "character varying(20)", True, None),
        ("reason", "text", True, None),
        ("approval_required", "character(1)", True, None),
        ("approval_status", "character varying(12)", True, None),
        ("approved_by", "character varying(40)", True, None),
        ("approved_at", "timestamp without time zone", True, None),
        ("notify_status", "character varying(12)", True, None),
        ("notify_at", "timestamp without time zone", True, None),
        ("mes_status", "character varying(12)", True, None),
        ("mes_at", "timestamp without time zone", True, None),
        ("created_at", "timestamp without time zone", True, None),
    ),
    "dim_parameter": _columns(
        ("parameter_id", "character varying(20)", False, None),
        ("parameter_name", "character varying(60)", True, None),
        ("unit", "character varying(20)", True, None),
        ("area", "character varying(10)", True, None),
        ("target_value", "numeric(12,4)", True, None),
        ("spec_lower", "numeric(12,4)", True, None),
        ("ctrl_lower", "numeric(12,4)", True, None),
        ("ctrl_upper", "numeric(12,4)", True, None),
        ("spec_upper", "numeric(12,4)", True, None),
        ("upper_only", "boolean", True, "false"),
    ),
    "evaluation": _columns(
        ("lot_hist_id", "character varying(20)", False, None),
        ("area", "character varying(10)", True, None),
        ("equipment", "character varying(20)", True, None),
        ("chamber", "character varying(24)", True, None),
        ("parameter", "character varying(20)", False, None),
        ("recipe", "character varying(20)", True, None),
        ("lot", "character varying(20)", True, None),
        ("wafer", "smallint", True, None),
        ("step_no", "smallint", False, None),
        ("step_seq", "smallint", True, None),
        ("point_cnt", "smallint", True, None),
        ("ooc_point_cnt", "smallint", True, None),
        ("oos_point_cnt", "smallint", True, None),
        ("alarm_type", "character varying(10)", True, None),
    ),
    "fdc_trace": _columns(
        ("lot_hist_id", "character varying(20)", False, None),
        ("parameter_id", "character varying(20)", False, None),
        ("seq_no", "smallint", False, None),
        ("recipe_step_no", "smallint", True, None),
        ("step_seq", "smallint", True, None),
        ("measured_at", "timestamp without time zone", True, None),
        ("value", "numeric(12,4)", True, None),
    ),
    "lot_history": _columns(
        ("lot_hist_id", "character varying(20)", False, None),
        ("lot_id", "character varying(20)", False, None),
        ("wafer_no", "smallint", False, None),
        ("wafer_id", "character varying(24)", True, None),
        ("device_id", "character varying(20)", True, None),
        ("step_id", "character varying(20)", True, None),
        ("area_id", "character varying(10)", True, None),
        ("equipment_id", "character varying(20)", True, None),
        ("chamber_id", "character varying(24)", True, None),
        ("recipe_id", "character varying(20)", True, None),
        ("track_in_at", "timestamp without time zone", True, None),
        ("track_out_at", "timestamp without time zone", True, None),
        ("duration_sec", "integer", True, None),
        ("chamber_wafer_cum", "integer", True, None),
        ("lot_seq", "integer", True, None),
        ("fault_code", "character varying(10)", True, None),
    ),
    "metrology": _columns(
        ("metrology_id", "character varying(20)", False, None),
        ("lot_hist_id", "character varying(20)", True, None),
        ("lot_id", "character varying(20)", True, None),
        ("wafer_no", "smallint", True, None),
        ("wafer_id", "character varying(24)", True, None),
        ("step_id", "character varying(20)", True, None),
        ("measure_type", "character varying(20)", True, None),
        ("unit", "character varying(20)", True, None),
        ("measured_value", "numeric(12,4)", True, None),
        ("spec_center", "numeric(12,4)", True, None),
        ("spec_lower", "numeric(12,4)", True, None),
        ("spec_upper", "numeric(12,4)", True, None),
        ("alarm_result", "character varying(10)", True, None),
        ("measured_at", "timestamp without time zone", True, None),
    ),
    "summary_alarm_history": _columns(
        ("alarm_id", "character varying(20)", False, None),
        ("occurred_at", "timestamp without time zone", True, None),
        ("area", "character varying(10)", True, None),
        ("equipment", "character varying(20)", True, None),
        ("chamber", "character varying(24)", True, None),
        ("parameter", "character varying(20)", True, None),
        ("recipe", "character varying(20)", True, None),
        ("lot", "character varying(20)", True, None),
        ("wafer", "smallint", True, None),
        ("step_no", "smallint", True, None),
        ("step_seq", "smallint", True, None),
        ("statistic_type", "character varying(10)", True, None),
        ("stat_value", "numeric(12,4)", True, None),
        ("cl", "numeric(12,4)", True, None),
        ("ucl", "numeric(12,4)", True, None),
        ("lcl", "numeric(12,4)", True, None),
        ("limit_type", "character varying(4)", True, None),
        ("alarm_type", "character varying(10)", True, "OOC"),
    ),
    "summary_data": _columns(
        ("lot_hist_id", "character varying(20)", False, None),
        ("area", "character varying(10)", True, None),
        ("equipment", "character varying(20)", True, None),
        ("chamber", "character varying(24)", True, None),
        ("parameter", "character varying(20)", False, None),
        ("recipe", "character varying(20)", True, None),
        ("lot", "character varying(20)", True, None),
        ("wafer", "smallint", True, None),
        ("step_no", "smallint", False, None),
        ("step_seq", "smallint", True, None),
        ("value_mean", "numeric(12,4)", True, None),
        ("value_std", "numeric(12,4)", True, None),
        ("value_min", "numeric(12,4)", True, None),
        ("value_max", "numeric(12,4)", True, None),
        ("point_cnt", "smallint", True, None),
    ),
    "trace_alarm_history": _columns(
        ("alarm_id", "character varying(20)", False, None),
        ("occurred_at", "timestamp without time zone", True, None),
        ("area", "character varying(10)", True, None),
        ("equipment", "character varying(20)", True, None),
        ("chamber", "character varying(24)", True, None),
        ("parameter", "character varying(20)", True, None),
        ("recipe", "character varying(20)", True, None),
        ("lot", "character varying(20)", True, None),
        ("wafer", "smallint", True, None),
        ("step_no", "smallint", True, None),
        ("step_seq", "smallint", True, None),
        ("seq_no", "smallint", True, None),
        ("value", "numeric(12,4)", True, None),
        ("limit_type", "character varying(4)", True, None),
        ("limit_value", "numeric(12,4)", True, None),
        ("alarm_type", "character varying(10)", True, "OOS"),
    ),
}

PRIMARY_KEYS = {
    "action_history": ["action_id"],
    "dim_parameter": ["parameter_id"],
    "evaluation": ["lot_hist_id", "parameter", "step_no"],
    "fdc_trace": ["lot_hist_id", "parameter_id", "seq_no"],
    "lot_history": ["lot_hist_id"],
    "metrology": ["metrology_id"],
    "summary_alarm_history": ["alarm_id"],
    "summary_data": ["lot_hist_id", "parameter", "step_no"],
    "trace_alarm_history": ["alarm_id"],
}
FOREIGN_KEYS = {
    "evaluation": [(["lot_hist_id"], "lot_history", ["lot_hist_id"])],
    "fdc_trace": [
        (["lot_hist_id"], "lot_history", ["lot_hist_id"]),
        (["parameter_id"], "dim_parameter", ["parameter_id"]),
    ],
    "summary_data": [(["lot_hist_id"], "lot_history", ["lot_hist_id"])],
}
CHECKS = {
    "evaluation": [(["alarm_type"], ["IN", "OOC", "OOS"])],
    "metrology": [(["alarm_result"], ["FAIL", "PASS"])],
    "summary_alarm_history": [(["limit_type"], ["LCL", "UCL"])],
    "trace_alarm_history": [(["limit_type"], ["LSL", "USL"])],
}
EXPLICIT_INDEXES = {
    "ix_evaluation_type": ("evaluation", ["alarm_type"]),
    "ix_lot_history_cum": ("lot_history", ["chamber_id", "chamber_wafer_cum"]),
    "ix_summary_data_key": ("summary_data", ["chamber", "parameter", "step_no"]),
    "ix_trace_alarm_time": ("trace_alarm_history", ["occurred_at"]),
}


@dataclass(frozen=True)
class Inspection:
    state: str
    signature: dict[str, Any] | None
    row_counts: dict[str, int]
    explicit_index_count: int = 0
    constraint_index_count: int = 0
    total_index_count: int = 0


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def build_expected_signature() -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for table in sorted(BASE_COLUMNS):
        constraints: list[dict[str, Any]] = [
            {"type": "PRIMARY_KEY", "columns": PRIMARY_KEYS[table]}
        ]
        for columns, reference_table, reference_columns in FOREIGN_KEYS.get(table, []):
            constraints.append(
                {
                    "type": "FOREIGN_KEY",
                    "columns": columns,
                    "reference_table": reference_table,
                    "reference_columns": reference_columns,
                    "update_action": "NO ACTION",
                    "delete_action": "NO ACTION",
                }
            )
        for columns, values in CHECKS.get(table, []):
            constraints.append(
                {"type": "CHECK", "columns": columns, "allowed_values": values}
            )
        indexes = [
            {
                "name": name,
                "method": "btree",
                "unique": False,
                "columns": columns,
            }
            for name, (index_table, columns) in sorted(EXPLICIT_INDEXES.items())
            if index_table == table
        ]
        tables[table] = {
            "columns": [asdict(column) for column in BASE_COLUMNS[table]],
            "constraints": sorted(constraints, key=_canonical_hash),
            "indexes": indexes,
        }
    return {"tables": tables}


EXPECTED_SIGNATURE = build_expected_signature()
EXPECTED_SIGNATURE_SHA256 = _canonical_hash(EXPECTED_SIGNATURE)


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
                raise BootstrapError("SQL block comment가 닫히지 않았습니다")
            index = end + 2
            output.append(" ")
            continue
        else:
            output.append(current)
        index += 1
    if in_string:
        raise BootstrapError("SQL 문자열 literal이 닫히지 않았습니다")
    return "".join(output)


def split_sql_statements(sql: str) -> list[str]:
    body = _strip_sql_comments(sql)
    if match := FORBIDDEN_SQL.search(body):
        raise BootstrapError(
            f"base schema SQL에 금지문이 있습니다: {match.group(0).upper()}"
        )
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
    trailing = "".join(current).strip()
    if trailing:
        raise BootstrapError("base schema SQL 마지막 문장에 세미콜론이 없습니다")
    return statements


def load_and_validate_sql(path: Path = BASE_SCHEMA_SQL_PATH) -> tuple[str, list[str]]:
    try:
        sql = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BootstrapError("base schema SQL을 읽을 수 없습니다") from exc
    statements = split_sql_statements(sql)
    counts = {
        "CREATE TABLE": sum(s.upper().startswith("CREATE TABLE") for s in statements),
        "CREATE INDEX": sum(s.upper().startswith("CREATE INDEX") for s in statements),
        "COMMENT ON": sum(s.upper().startswith("COMMENT ON") for s in statements),
    }
    if counts != {"CREATE TABLE": 9, "CREATE INDEX": 4, "COMMENT ON": 11}:
        raise BootstrapError("base schema SQL statement 개수가 계약과 다릅니다")
    return sql, statements


def build_base_manifest(profile: str) -> dict[str, Any]:
    if profile not in PROFILE_APPLIES_TO:
        raise BootstrapError("지원하지 않는 bootstrap profile입니다")
    epoch = load_dataset_epoch(DATASET_EPOCH_PATH)
    tables = {
        table: {
            "columns": [column.name for column in columns],
            "verification_policy": "bootstrap_empty",
            "row_count": 0,
            "content_hash": EMPTY_ROWS_SHA256,
        }
        for table, columns in sorted(BASE_COLUMNS.items())
    }
    manifest = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "artifact_type": "db_bootstrap",
        "dataset_epoch": DATASET_EPOCH,
        "source_archive_sha256": epoch["archive"]["sha256"],
        "correction_version": CORRECTION_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "profile": profile,
        "applies_to": list(PROFILE_APPLIES_TO[profile]),
        "bootstrap_stage": "base_schema",
        "schema_stage": "base",
        "applied_migrations": [],
        "tables": tables,
    }
    validate_manifest_schema(
        manifest,
        expected_artifact_type="db_bootstrap",
        expected_profile=profile,
        expected_stage="base_schema",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    return manifest


def _result_rows(result: Any) -> list[Mapping[str, Any]]:
    try:
        return list(result.mappings().all())
    except (AttributeError, TypeError) as exc:
        raise DatabaseStateError("PostgreSQL catalog 응답 형식이 잘못됐습니다") from exc


def _name_array(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return [item for item in value[1:-1].split(",") if item]
    raise DatabaseStateError("PostgreSQL catalog column 배열 형식이 잘못됐습니다")


def _normalize_default(value: Any) -> str | None:
    if value is None:
        return None
    normalized = SQL_WHITESPACE.sub(" ", str(value).strip())
    normalized = SQL_CAST.sub("", normalized)
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    literal = SQL_LITERAL.fullmatch(normalized)
    if literal:
        return literal.group(1).replace("''", "'")
    return normalized.lower()


ACTION_NAMES = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}


def build_actual_signature(
    connection: Any,
) -> tuple[dict[str, Any], tuple[int, int, int]]:
    tables = {
        table: {"columns": [], "constraints": [], "indexes": []}
        for table in BASE_COLUMNS
    }
    for row in _result_rows(connection.exec_driver_sql(COLUMNS_SQL)):
        table = str(row["table_name"])
        if table not in tables:
            raise DatabaseStateError("예상 밖 public table column이 있습니다")
        tables[table]["columns"].append(
            {
                "name": str(row["column_name"]),
                "data_type": str(row["data_type"]).lower(),
                "nullable": bool(row["nullable"]),
                "default": _normalize_default(row.get("column_default")),
            }
        )

    for row in _result_rows(connection.exec_driver_sql(CONSTRAINTS_SQL)):
        table = str(row["table_name"])
        columns = _name_array(row.get("columns"))
        kind = row["contype"]
        if kind == "p":
            contract = {"type": "PRIMARY_KEY", "columns": columns}
        elif kind == "u":
            contract = {"type": "UNIQUE", "columns": columns}
        elif kind == "f":
            contract = {
                "type": "FOREIGN_KEY",
                "columns": columns,
                "reference_table": str(row["reference_table"]),
                "reference_columns": _name_array(row.get("reference_columns")),
                "update_action": ACTION_NAMES.get(str(row["update_action"]), "UNKNOWN"),
                "delete_action": ACTION_NAMES.get(str(row["delete_action"]), "UNKNOWN"),
            }
        elif kind == "c":
            values = sorted(
                {
                    value.replace("''", "'")
                    for value in SQL_LITERAL.findall(str(row["definition"]))
                }
            )
            contract = {"type": "CHECK", "columns": columns, "allowed_values": values}
        else:  # pragma: no cover - query already restricts kinds
            raise DatabaseStateError("지원하지 않는 constraint type입니다")
        tables[table]["constraints"].append(contract)

    explicit_count = 0
    constraint_count = 0
    total_count = 0
    for row in _result_rows(connection.exec_driver_sql(INDEXES_SQL)):
        total_count += 1
        if bool(row["is_constraint"]):
            constraint_count += 1
            continue
        explicit_count += 1
        table = str(row["table_name"])
        tables[table]["indexes"].append(
            {
                "name": str(row["index_name"]),
                "method": str(row["method"]),
                "unique": bool(row["is_unique"]),
                "columns": _name_array(row.get("columns")),
            }
        )

    for contract in tables.values():
        contract["constraints"] = sorted(contract["constraints"], key=_canonical_hash)
        contract["indexes"] = sorted(contract["indexes"], key=lambda item: item["name"])
    return {"tables": tables}, (explicit_count, constraint_count, total_count)


def inspect_database(connection: Any) -> Inspection:
    objects = _result_rows(connection.exec_driver_sql(OBJECTS_SQL))
    if not objects:
        return Inspection("ABSENT", None, {})
    actual_tables = {
        str(row["object_name"]) for row in objects if row["relkind"] in {"r", "p"}
    }
    non_tables = [row for row in objects if row["relkind"] not in {"r", "p"}]
    if actual_tables != set(BASE_COLUMNS) or non_tables:
        return Inspection("CONFLICT", None, {})

    signature, index_counts = build_actual_signature(connection)
    row_counts: dict[str, int] = {}
    for table in sorted(BASE_COLUMNS):
        result = connection.exec_driver_sql(
            f'SELECT count(*) AS row_count FROM public."{table}"'
        )
        rows = _result_rows(result)
        if len(rows) != 1:
            raise DatabaseStateError("base table row count 응답이 잘못됐습니다")
        row_counts[table] = int(rows[0]["row_count"])

    exact = (
        signature == EXPECTED_SIGNATURE
        and all(count == 0 for count in row_counts.values())
        and index_counts
        == (
            EXPECTED_EXPLICIT_INDEX_COUNT,
            EXPECTED_CONSTRAINT_INDEX_COUNT,
            EXPECTED_TOTAL_INDEX_COUNT,
        )
    )
    return Inspection(
        "EXACT_EMPTY" if exact else "CONFLICT",
        signature,
        row_counts,
        *index_counts,
    )


def acquire_advisory_lock(connection: Any, database: str) -> None:
    result = connection.exec_driver_sql(
        "SELECT pg_try_advisory_xact_lock(%s, %s) AS acquired",
        (BASE_SCHEMA_LOCK_NAMESPACE, DATABASE_LOCK_ID[database]),
    )
    rows = _result_rows(result)
    if len(rows) != 1 or rows[0].get("acquired") is not True:
        raise AdvisoryLockError("다른 프로세스가 같은 DB bootstrap을 진행 중입니다")


def execute_schema(connection: Any, statements: Sequence[str]) -> None:
    for statement in statements:
        connection.exec_driver_sql(statement)


def marker_path(database: str, *, root: Path = MARKER_ROOT) -> Path:
    if database not in ALLOWED_DATABASES:
        raise MarkerError("허용되지 않은 marker database입니다")
    return root / f"base_schema.{database}.json"


def _marker_lock_path(database: str, *, root: Path) -> Path:
    return root / f".base_schema.{database}.lock"


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
def _exclusive_marker_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise MarkerError("marker lock에 symlink를 사용할 수 없습니다")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        lock_file: BinaryIO = os.fdopen(descriptor, "a+b")
        _acquire_file_lock(lock_file)
    except OSError as exc:
        raise MarkerError("marker lock을 사용할 수 없습니다") from exc
    try:
        yield
    finally:
        try:
            _release_file_lock(lock_file)
        finally:
            lock_file.close()


def _sql_sha256(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


def build_marker(
    target: BootstrapTarget,
    *,
    status: str,
    sql_sha256: str,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    if status not in {"APPLIED", "VERIFIED_EXISTING"}:
        raise MarkerError("marker status가 잘못됐습니다")
    now = recorded_at or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise MarkerError("marker 시각은 timezone-aware여야 합니다")
    payload: dict[str, Any] = {
        "database": target.database,
        "profile": target.profile,
        "source_member_sha256": SOURCE_MEMBER_SHA256,
        "corrected_sql_sha256": sql_sha256,
        "schema_signature_sha256": EXPECTED_SIGNATURE_SHA256,
        "status": status,
        "recorded_at": now.isoformat(),
        "verification": {
            "table_count": EXPECTED_TABLE_COUNT,
            "explicit_index_count": EXPECTED_EXPLICIT_INDEX_COUNT,
            "constraint_index_count": EXPECTED_CONSTRAINT_INDEX_COUNT,
            "total_index_count": EXPECTED_TOTAL_INDEX_COUNT,
            "action_history_rows": 0,
        },
    }
    if status == "APPLIED":
        payload["applied_at"] = now.isoformat()
    scan_for_sensitive_values(payload)
    return payload


def validate_marker(
    payload: Mapping[str, Any], target: BootstrapTarget, *, sql_sha256: str
) -> None:
    required = {
        "database",
        "profile",
        "source_member_sha256",
        "corrected_sql_sha256",
        "schema_signature_sha256",
        "status",
        "recorded_at",
        "verification",
    }
    if payload.get("status") == "APPLIED":
        required.add("applied_at")
    if set(payload) != required:
        raise MarkerError("marker key 집합이 잘못됐습니다")
    if (
        payload["database"] != target.database
        or payload["profile"] != target.profile
        or payload["source_member_sha256"] != SOURCE_MEMBER_SHA256
        or payload["corrected_sql_sha256"] != sql_sha256
        or payload["schema_signature_sha256"] != EXPECTED_SIGNATURE_SHA256
        or payload["status"] not in {"APPLIED", "VERIFIED_EXISTING"}
    ):
        raise MarkerError("marker provenance가 현재 계약과 다릅니다")
    expected_verification = {
        "table_count": 9,
        "explicit_index_count": 4,
        "constraint_index_count": 9,
        "total_index_count": 13,
        "action_history_rows": 0,
    }
    if payload["verification"] != expected_verification:
        raise MarkerError("marker 검증값이 현재 계약과 다릅니다")
    for field in ("recorded_at", "applied_at"):
        if field not in payload:
            continue
        try:
            parsed = datetime.fromisoformat(str(payload[field]))
        except ValueError as exc:
            raise MarkerError("marker 시각 형식이 잘못됐습니다") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise MarkerError("marker 시각은 timezone-aware여야 합니다")
    scan_for_sensitive_values(payload)


def load_marker(
    target: BootstrapTarget, *, sql_sha256: str, root: Path = MARKER_ROOT
) -> dict[str, Any] | None:
    path = marker_path(target.database, root=root)
    if not path.exists():
        return None
    if path.is_symlink():
        raise MarkerError("marker에 symlink를 사용할 수 없습니다")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarkerError("marker를 안전하게 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise MarkerError("marker 최상위 값은 object여야 합니다")
    validate_marker(payload, target, sql_sha256=sql_sha256)
    return payload


def save_marker(
    payload: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    sql_sha256: str,
    root: Path = MARKER_ROOT,
) -> None:
    validate_marker(payload, target, sql_sha256=sql_sha256)
    path = marker_path(target.database, root=root)
    with _exclusive_marker_lock(_marker_lock_path(target.database, root=root)):
        existing = load_marker(target, sql_sha256=sql_sha256, root=root)
        if existing is not None and existing != payload:
            raise MarkerError("기존 marker와 새 적용 기록이 충돌합니다")
        try:
            atomic_save_json(path, payload)
        except OSError as exc:
            raise MarkerError("marker를 원자적으로 저장할 수 없습니다") from exc


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
    if readonly:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    validate_connected_identity(connection, target)
    set_and_validate_public_search_path(connection)
    acquire_advisory_lock(connection, target.database)


def run_preflight(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> Inspection:
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            inspection = inspect_database(connection)
            sql, _ = load_and_validate_sql()
            marker = load_marker(target, sql_sha256=_sql_sha256(sql), root=marker_root)
            if marker is not None and inspection.state != "EXACT_EMPTY":
                raise DatabaseStateError("marker와 실제 DB schema 상태가 다릅니다")
            return inspection
    finally:
        engine.dispose()


def run_apply_or_recover(
    target: BootstrapTarget,
    *,
    recover_marker: bool,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> str:
    sql, statements = load_and_validate_sql()
    sql_sha = _sql_sha256(sql)
    marker_payload: dict[str, Any] | None = None
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=False)
            marker = load_marker(target, sql_sha256=sql_sha, root=marker_root)
            inspection = inspect_database(connection)
            if marker is not None:
                if inspection.state != "EXACT_EMPTY":
                    raise DatabaseStateError("marker와 실제 DB schema 상태가 다릅니다")
                if recover_marker:
                    raise DatabaseStateError("정상 marker가 이미 존재합니다")
                return "NO_OP"
            if recover_marker:
                if inspection.state != "EXACT_EMPTY":
                    raise DatabaseStateError(
                        "marker 복구는 exact empty schema에서만 허용됩니다"
                    )
                marker_payload = build_marker(
                    target, status="VERIFIED_EXISTING", sql_sha256=sql_sha
                )
            else:
                if inspection.state == "EXACT_EMPTY":
                    raise DatabaseStateError(
                        "exact schema에 marker가 없습니다. "
                        "--recover-marker가 필요합니다"
                    )
                if inspection.state != "ABSENT":
                    raise DatabaseStateError(
                        "partial/conflict/nonempty DB에는 적용할 수 없습니다"
                    )
                execute_schema(connection, statements)
                verified = inspect_database(connection)
                if verified.state != "EXACT_EMPTY":
                    raise DatabaseStateError("base schema 적용 후 검증에 실패했습니다")
                marker_payload = build_marker(
                    target, status="APPLIED", sql_sha256=sql_sha
                )
        if marker_payload is None:  # pragma: no cover - all branches return or set
            raise MarkerError("marker 생성 상태가 잘못됐습니다")
        save_marker(
            marker_payload,
            target,
            sql_sha256=sql_sha,
            root=marker_root,
        )
        return str(marker_payload["status"])
    finally:
        engine.dispose()


def run_rollback_verification(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> None:
    if target.database != "kosa_agent_e2e":
        raise DatabaseStateError("rollback 검증은 kosa_agent_e2e에서만 허용됩니다")
    sql, statements = load_and_validate_sql()
    sql_sha = _sql_sha256(sql)
    engine = engine_factory(target)
    try:
        try:
            with engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=False)
                if (
                    load_marker(target, sql_sha256=sql_sha, root=marker_root)
                    is not None
                ):
                    raise DatabaseStateError(
                        "marker가 있는 DB에서는 rollback 검증을 할 수 없습니다"
                    )
                if inspect_database(connection).state != "ABSENT":
                    raise DatabaseStateError(
                        "객체가 없는 E2E DB에서만 rollback 검증할 수 있습니다"
                    )
                execute_schema(connection, statements)
                raise _RollbackProbe
        except _RollbackProbe:
            pass

        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            if inspect_database(connection).state != "ABSENT":
                raise DatabaseStateError(
                    "transaction rollback 뒤 base 객체가 남았습니다"
                )
    finally:
        engine.dispose()


def validate_registered_manifests() -> None:
    epoch = load_dataset_epoch(DATASET_EPOCH_PATH)
    for profile in ("runtime", "evaluation"):
        path = MANIFEST_ROOT / f"{profile}.base_schema.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BootstrapError("base schema manifest를 읽을 수 없습니다") from exc
        if payload != build_base_manifest(profile):
            raise BootstrapError("base schema manifest가 결정론적 계약과 다릅니다")
        validate_manifest_schema(
            payload,
            expected_artifact_type="db_bootstrap",
            expected_profile=profile,
            expected_stage="base_schema",
            expected_archive_sha256=epoch["archive"]["sha256"],
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", choices=sorted(ALLOWED_DATABASES), required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--recover-marker", action="store_true")
    parser.add_argument("--verify-rollback", action="store_true")
    parser.add_argument("--confirm-target", choices=sorted(ALLOWED_DATABASES))
    return parser


def resolve_mode(args: argparse.Namespace) -> str:
    if args.dry_run or args.preflight:
        if args.confirm_target or args.recover_marker or args.verify_rollback:
            raise BootstrapError(
                "dry-run/preflight는 mutation 옵션과 함께 쓸 수 없습니다"
            )
        return "dry-run" if args.dry_run else "preflight"
    if args.recover_marker and args.verify_rollback:
        raise BootstrapError("recover-marker와 verify-rollback은 함께 쓸 수 없습니다")
    if args.confirm_target is None:
        raise BootstrapError("접속하지 않았습니다. 명시적인 실행 모드가 필요합니다")
    if args.confirm_target != args.database:
        raise BootstrapError("--confirm-target과 --database가 다릅니다")
    if args.recover_marker:
        return "recover-marker"
    if args.verify_rollback:
        return "verify-rollback"
    return "apply"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        mode = resolve_mode(args)
        load_dotenv(REPOSITORY_ROOT / ".env", override=False)
        target = load_bootstrap_target(args.database)
        load_and_validate_sql()
        validate_registered_manifests()
        if mode == "dry-run":
            print(
                f"DRY_RUN_OK database={target.database} "
                f"profile={target.profile} objects=9 rows=0"
            )
        elif mode == "preflight":
            state = run_preflight(target).state
            print(f"PREFLIGHT_OK database={target.database} state={state}")
        elif mode == "recover-marker":
            status = run_apply_or_recover(target, recover_marker=True)
            print(f"BOOTSTRAP_OK database={target.database} status={status}")
        elif mode == "verify-rollback":
            run_rollback_verification(target)
            print(f"ROLLBACK_OK database={target.database} objects=0")
        else:
            status = run_apply_or_recover(target, recover_marker=False)
            print(f"BOOTSTRAP_OK database={target.database} status={status}")
        return 0
    except (BootstrapError, TargetValidationError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 2)
    except SQLAlchemyError:
        # Driver 예외에는 host·계정·statement가 섞일 수 있으므로 원문과 traceback을
        # 출력하지 않는다. 상세 진단은 비밀을 제거한 별도 운영 로그의 책임이다.
        print(
            "BootstrapConnectionError: PostgreSQL bootstrap 작업에 실패했습니다",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
