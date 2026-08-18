"""Adopt or load the registered corrected base data into PostgreSQL profiles.

The command is read-only unless an explicit target confirmation is supplied.
It never mutates ``action_history`` or any table owned by the reference
extension migration.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import apply_reference_extensions as reference_extensions
import bootstrap_base_schema as base_schema
import manifest_v3
import verify_bootstrap_state as bootstrap_verifier
from build_corrected_dataset import _exclusive_lock
from db_target import (
    ALLOWED_DATABASES,
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
    validate_url_components,
)
from dotenv import load_dotenv
from master_cypher import canonical_sha256
from mutation_runtime import prepare_transaction
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from value_normalization import (
    VALUE_NORMALIZATION_VERSION,
    ValueNormalizationError,
    column_type_registry,
    normalize_csv_row,
    normalize_db_row,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
MANIFEST_ROOT = BOOTSTRAP_ROOT / "manifests"
MARKER_ROOT = BOOTSTRAP_ROOT / "markers"
REPORT_ROOT = BOOTSTRAP_ROOT / "reports"

CORRECTION_VERSION = "corrected-base-v1"
EMPTY_ROWS_SHA256 = base_schema.EMPTY_ROWS_SHA256
LOAD_TABLES = (
    "dim_parameter",
    "lot_history",
    "fdc_trace",
    "summary_data",
    "evaluation",
    "trace_alarm_history",
    "summary_alarm_history",
    "metrology",
)
REFERENCE_IMMUTABLE_TABLES = (
    "r03_alarm_history",
    "document_corpus",
    "document",
    "document_chunk",
)
FORBIDDEN_DML_TABLES = frozenset(
    {"action_history", *REFERENCE_IMMUTABLE_TABLES, "nl_query_log"}
)
FIXUP_PARAMETER_IDS = ("ET_ESC", "PH_DEV", "PH_PEB")
EXPECTED_REFERENCE_OUTPUT = {
    "trace_alarm_rows": 126,
    "summary_alarm_rows": 47,
    "evaluation": {"IN": 4542, "OOC": 216, "OOS": 42},
    "metrology": {"FAIL": 9, "PASS": 39},
    "dim_parameter_rows": 8,
}
STATEMENT_TIMEOUT = "120s"
VALID_STATES = frozenset({"MISSING_001", "EMPTY", "ADOPTED", "NEEDS_FIXUP", "DRIFT"})
VALID_RECEIPT_STATUS = frozenset({"STARTED", "COMMITTED", "ABORTED"})
VALID_ABORT_REASONS = frozenset(
    {"TRANSACTION_ROLLED_BACK", "PREVIOUS_TRANSACTION_NOT_COMMITTED"}
)


class CorrectedBaseError(RuntimeError):
    """Corrected base input, state, or mutation contract failed."""


class CorrectedBaseStateError(CorrectedBaseError):
    """Live database does not match a safe transition state."""


class CorrectedBaseArtifactError(CorrectedBaseError):
    """Receipt, marker, or alignment artifact is invalid."""


class _RehearsalRollback(RuntimeError):
    pass


@dataclass(frozen=True)
class InputContext:
    bundle: bootstrap_verifier.ActiveBundle
    input_identity: dict[str, Any]
    input_identity_sha256: str
    expected_rows: dict[str, tuple[dict[str, Any], ...]]
    expected_hashes: dict[str, str]


@dataclass(frozen=True)
class DatabaseState:
    name: str
    action_history_rows: int
    reference_rows: dict[str, int]
    mismatched_tables: tuple[str, ...] = ()
    fixup_values: dict[str, str] | None = None


@dataclass(frozen=True)
class PostcheckResult:
    action_history_rows: int
    alarm_event_rows: int
    reference_rows: dict[str, int]
    table_row_counts: dict[str, int]
    table_hashes: dict[str, str]
    schema_signature_sha256: str


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorrectedBaseArtifactError(f"{label} JSON을 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise CorrectedBaseArtifactError(f"{label} 최상위 값은 object여야 합니다")
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def _column_types(table: str) -> dict[str, str]:
    try:
        return column_type_registry(base_schema.BASE_COLUMNS[table])
    except KeyError as exc:
        raise CorrectedBaseError(f"{table}: base column registry가 없습니다") from exc


def _load_input_context() -> InputContext:
    epoch = manifest_v3.load_dataset_epoch()
    corrected_manifest = _read_json(
        manifest_v3.CORRECTED_MANIFEST_PATH, label="corrected manifest"
    )
    manifest_v3.validate_manifest_schema(
        corrected_manifest,
        expected_artifact_type="corrected_files",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    source_manifest = _read_json(
        manifest_v3.SOURCE_MANIFEST_PATH, label="source manifest"
    )
    manifest_v3.validate_manifest_schema(
        source_manifest,
        expected_artifact_type="source_files",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    marker = _read_json(
        bootstrap_verifier.CORRECTED_MARKER_PATH,
        label="corrected registration marker",
    )
    bundle = bootstrap_verifier._load_active_bundle()
    bootstrap_verifier._validate_registered_marker(
        marker,
        manifest_payload=corrected_manifest,
        bundle=bundle,
        source_manifest_sha=canonical_sha256(source_manifest),
    )

    expected_rows: dict[str, tuple[dict[str, Any], ...]] = {}
    expected_hashes: dict[str, str] = {}
    table_identity: dict[str, Any] = {}
    for table in LOAD_TABLES:
        table_data = bundle.tables.get(table)
        if table_data is None:
            raise CorrectedBaseArtifactError(
                f"{table}: active corrected table이 없습니다"
            )
        expected_columns = [column.name for column in base_schema.BASE_COLUMNS[table]]
        if list(table_data.columns) != expected_columns:
            raise CorrectedBaseArtifactError(
                f"{table}: active corrected column 계약이 다릅니다"
            )
        types = _column_types(table)
        normalized = tuple(normalize_csv_row(row, types) for row in table_data.rows)
        expected_rows[table] = normalized
        expected_hashes[table] = manifest_v3.hash_canonical_rows(normalized)
        table_identity[table] = {
            "columns": expected_columns,
            "row_count": len(normalized),
            "content_hash": expected_hashes[table],
        }

    identity = {
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "source_archive_sha256": corrected_manifest["source_archive_sha256"],
        "corrected_manifest_sha256": canonical_sha256(corrected_manifest),
        "corrected_registration_marker_sha256": canonical_sha256(marker),
        "corrected_build_receipt_sha256": bundle.receipt_sha256,
        "build_id": bundle.receipt["build_id"],
        "generator_revision": bundle.receipt["generator_revision"],
        "generator_sha256": bundle.receipt["generator_sha256"],
        "correction_version": bundle.receipt["correction_version"],
        "value_normalization_version": VALUE_NORMALIZATION_VERSION,
        "tables": table_identity,
    }
    manifest_v3.scan_for_sensitive_values(identity)
    return InputContext(
        bundle=bundle,
        input_identity=identity,
        input_identity_sha256=canonical_sha256(identity),
        expected_rows=expected_rows,
        expected_hashes=expected_hashes,
    )


def build_corrected_base_manifest(
    profile: str, context: InputContext
) -> dict[str, Any]:
    if profile not in manifest_v3.PROFILE_APPLIES_TO:
        raise CorrectedBaseError("지원하지 않는 profile입니다")
    tables: dict[str, Any] = {}
    for table, columns in sorted(base_schema.BASE_COLUMNS.items()):
        entry = {"columns": [column.name for column in columns]}
        if table == "action_history":
            entry.update(
                {
                    "verification_policy": "bootstrap_empty",
                    "row_count": 0,
                    "content_hash": EMPTY_ROWS_SHA256,
                }
            )
        else:
            entry.update(
                {
                    "verification_policy": "immutable_content",
                    "row_count": len(context.expected_rows[table]),
                    "content_hash": context.expected_hashes[table],
                }
            )
        tables[table] = entry
    for table, columns in sorted(reference_extensions.EXPECTED_TABLE_COLUMNS.items()):
        entry: dict[str, Any] = {"columns": [column[0] for column in columns]}
        if table == "nl_query_log":
            entry["verification_policy"] = "schema_only"
        else:
            entry.update(
                {
                    "verification_policy": "bootstrap_empty",
                    "row_count": 0,
                    "content_hash": EMPTY_ROWS_SHA256,
                }
            )
        tables[table] = entry
    epoch = manifest_v3.load_dataset_epoch()
    contract = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[(profile, "corrected_base")]
    payload = {
        "format_version": manifest_v3.MANIFEST_FORMAT_VERSION,
        "artifact_type": "db_bootstrap",
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "source_archive_sha256": epoch["archive"]["sha256"],
        "correction_version": CORRECTION_VERSION,
        "hash_algorithm": manifest_v3.HASH_ALGORITHM,
        "value_normalization_version": VALUE_NORMALIZATION_VERSION,
        "profile": profile,
        "applies_to": list(manifest_v3.PROFILE_APPLIES_TO[profile]),
        "bootstrap_stage": "corrected_base",
        "schema_stage": contract.schema_stage,
        "applied_migrations": list(contract.applied_migrations),
        "tables": tables,
    }
    manifest_v3.validate_manifest_schema(
        payload,
        expected_artifact_type="db_bootstrap",
        expected_profile=profile,
        expected_stage="corrected_base",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    return payload


def register_manifests(*, confirm: bool, context: InputContext | None = None) -> str:
    context = context or _load_input_context()
    candidates = {
        profile: build_corrected_base_manifest(profile, context)
        for profile in ("runtime", "evaluation")
    }
    changed = {
        profile: path
        for profile, path in (
            ("runtime", MANIFEST_ROOT / "runtime.corrected_base.json"),
            ("evaluation", MANIFEST_ROOT / "evaluation.corrected_base.json"),
        )
        if not path.exists()
        or _read_json(path, label=f"{profile} manifest") != candidates[profile]
    }
    if not changed:
        return "NO_OP"
    if not confirm:
        return "PREVIEW"
    try:
        for profile, path in changed.items():
            manifest_v3.atomic_save_json(path, candidates[profile])
    except OSError as exc:
        raise CorrectedBaseArtifactError(
            "corrected base manifest를 원자 저장할 수 없습니다"
        ) from exc
    return "REGISTERED"


def _result_rows(result: Any) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in result.mappings().all()]
    except (AttributeError, TypeError, ValueError) as exc:
        raise CorrectedBaseStateError("DB query 응답 형식이 잘못됐습니다") from exc


def _scalar(result: Any) -> Any:
    try:
        return result.scalar_one()
    except (AttributeError, LookupError, TypeError) as exc:
        raise CorrectedBaseStateError("DB scalar 응답 형식이 잘못됐습니다") from exc


def _read_table(connection: Any, table: str) -> tuple[dict[str, Any], ...]:
    columns = [column.name for column in base_schema.BASE_COLUMNS[table]]
    selected = ", ".join(f'"{column}"' for column in columns)
    rows = _result_rows(connection.exec_driver_sql(f'SELECT {selected} FROM "{table}"'))
    types = _column_types(table)
    return tuple(normalize_db_row(row, types) for row in rows)


def _read_reference_counts(connection: Any) -> dict[str, int]:
    return {
        table: int(
            _scalar(connection.exec_driver_sql(f'SELECT count(*) FROM "{table}"'))
        )
        for table in REFERENCE_IMMUTABLE_TABLES
    }


def _preflight_001(
    connection: Any,
    target: BootstrapTarget,
    *,
    marker_root: Path = reference_extensions.MARKER_ROOT,
) -> reference_extensions.ReferenceInspection:
    sql, _ = reference_extensions.load_and_validate_sql()
    migration_sha = reference_extensions._migration_sha256(sql)
    inspection = reference_extensions.inspect_database(connection)
    marker = reference_extensions.load_marker(
        target, migration_sha256=migration_sha, root=marker_root
    )
    if inspection.state != "PRESENT" or marker is None:
        raise CorrectedBaseStateError(
            "MISSING_001: reference extension이 준비되지 않았습니다"
        )
    if marker["schema_signature_sha256"] != inspection.schema_signature_sha256:
        raise CorrectedBaseStateError(
            "MISSING_001: marker와 schema signature가 다릅니다"
        )
    try:
        base_signature, index_counts = base_schema.build_actual_signature(connection)
    except base_schema.DatabaseStateError as exc:
        raise CorrectedBaseStateError(
            "base schema catalog를 검증할 수 없습니다"
        ) from exc
    expected_index_counts = (
        base_schema.EXPECTED_EXPLICIT_INDEX_COUNT,
        base_schema.EXPECTED_CONSTRAINT_INDEX_COUNT,
        base_schema.EXPECTED_TOTAL_INDEX_COUNT,
    )
    if (
        base_signature != base_schema.EXPECTED_SIGNATURE
        or index_counts != expected_index_counts
    ):
        raise CorrectedBaseStateError("base schema PK·FK·CHECK·index 계약이 다릅니다")
    return inspection


def _fixup_candidate(
    actual: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]]
) -> dict[str, str] | None:
    actual_by_id = {str(row["parameter_id"]): dict(row) for row in actual}
    expected_by_id = {str(row["parameter_id"]): dict(row) for row in expected}
    if set(actual_by_id) != set(expected_by_id):
        return None
    differences: dict[str, set[str]] = {}
    for parameter_id in sorted(expected_by_id):
        changed = {
            column
            for column in expected_by_id[parameter_id]
            if actual_by_id[parameter_id].get(column)
            != expected_by_id[parameter_id].get(column)
        }
        if changed:
            differences[parameter_id] = changed
    if differences != {
        parameter_id: {"parameter_name"} for parameter_id in FIXUP_PARAMETER_IDS
    }:
        return None
    return {
        parameter_id: str(expected_by_id[parameter_id]["parameter_name"])
        for parameter_id in FIXUP_PARAMETER_IDS
    }


def classify_database_state(
    connection: Any,
    target: BootstrapTarget,
    context: InputContext,
    *,
    marker_root: Path = reference_extensions.MARKER_ROOT,
) -> DatabaseState:
    _preflight_001(connection, target, marker_root=marker_root)
    action_rows = int(
        _scalar(connection.exec_driver_sql("SELECT count(*) FROM action_history"))
    )
    reference_rows = _read_reference_counts(connection)
    actual = {table: _read_table(connection, table) for table in LOAD_TABLES}
    if all(not rows for rows in actual.values()):
        return DatabaseState("EMPTY", action_rows, reference_rows)
    mismatches = tuple(
        table
        for table in LOAD_TABLES
        if manifest_v3.hash_canonical_rows(actual[table])
        != context.expected_hashes[table]
    )
    if not mismatches:
        return DatabaseState("ADOPTED", action_rows, reference_rows)
    if mismatches == ("dim_parameter",):
        fixup = _fixup_candidate(
            actual["dim_parameter"], context.expected_rows["dim_parameter"]
        )
        if fixup is not None:
            return DatabaseState(
                "NEEDS_FIXUP", action_rows, reference_rows, mismatches, fixup
            )
    return DatabaseState("DRIFT", action_rows, reference_rows, mismatches)


def _insert_corrected_rows(connection: Any, context: InputContext) -> int:
    inserted = 0
    for table in LOAD_TABLES:
        columns = list(context.expected_rows[table][0])
        names = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join(f"%({column})s" for column in columns)
        rows = [dict(row) for row in context.expected_rows[table]]
        result = connection.exec_driver_sql(
            f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', rows
        )
        rowcount = getattr(result, "rowcount", len(rows))
        if rowcount not in {-1, len(rows)}:
            raise CorrectedBaseStateError(f"{table}: 적재 행 수가 기대와 다릅니다")
        inserted += len(rows)
    return inserted


def _apply_fixup(connection: Any, fixup: Mapping[str, str]) -> int:
    if tuple(fixup) != FIXUP_PARAMETER_IDS:
        raise CorrectedBaseStateError("dim_parameter 보정 key가 계약과 다릅니다")
    changed = 0
    for parameter_id, parameter_name in fixup.items():
        result = connection.exec_driver_sql(
            """
            UPDATE dim_parameter
               SET parameter_name = %s
             WHERE parameter_id = %s AND parameter_name IS DISTINCT FROM %s
            """,
            (parameter_name, parameter_id, parameter_name),
        )
        changed += int(getattr(result, "rowcount", 0))
    if changed != 3:
        raise CorrectedBaseStateError("dim_parameter 보정 행 수가 정확히 3이 아닙니다")
    return changed


def _reference_output(connection: Any) -> dict[str, Any]:
    evaluation_rows = _result_rows(
        connection.exec_driver_sql(
            "SELECT alarm_type, count(*) AS row_count "
            "FROM evaluation GROUP BY alarm_type"
        )
    )
    metrology_rows = _result_rows(
        connection.exec_driver_sql(
            "SELECT alarm_result, count(*) AS row_count "
            "FROM metrology GROUP BY alarm_result"
        )
    )
    return {
        "trace_alarm_rows": int(
            _scalar(
                connection.exec_driver_sql("SELECT count(*) FROM trace_alarm_history")
            )
        ),
        "summary_alarm_rows": int(
            _scalar(
                connection.exec_driver_sql("SELECT count(*) FROM summary_alarm_history")
            )
        ),
        "evaluation": {
            str(row["alarm_type"]): int(row["row_count"]) for row in evaluation_rows
        },
        "metrology": {
            str(row["alarm_result"]): int(row["row_count"]) for row in metrology_rows
        },
        "dim_parameter_rows": int(
            _scalar(connection.exec_driver_sql("SELECT count(*) FROM dim_parameter"))
        ),
    }


def postcheck_database(
    connection: Any,
    target: BootstrapTarget,
    context: InputContext,
    before: DatabaseState,
) -> PostcheckResult:
    after = classify_database_state(connection, target, context)
    if after.name != "ADOPTED":
        raise CorrectedBaseStateError(
            "postcheck에서 corrected base가 ADOPTED가 아닙니다"
        )
    if after.action_history_rows != before.action_history_rows:
        raise CorrectedBaseStateError("action_history가 변경됐습니다")
    if after.reference_rows != before.reference_rows:
        raise CorrectedBaseStateError("reference table이 변경됐습니다")
    outputs = _reference_output(connection)
    if outputs != EXPECTED_REFERENCE_OUTPUT:
        raise CorrectedBaseStateError("reference output 기준값이 다릅니다")
    reference = reference_extensions.postcheck_database(
        connection, action_rows_before=before.action_history_rows
    )
    row_counts = {table: len(context.expected_rows[table]) for table in LOAD_TABLES}
    return PostcheckResult(
        action_history_rows=after.action_history_rows,
        alarm_event_rows=reference.alarm_event_rows,
        reference_rows=after.reference_rows,
        table_row_counts=row_counts,
        table_hashes=dict(context.expected_hashes),
        schema_signature_sha256=reference.schema_signature_sha256,
    )


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
        isolation_level="REPEATABLE READ",
        acquire_lock=reference_extensions.acquire_advisory_lock,
    )
    connection.exec_driver_sql(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")


def _artifact_common(
    target: BootstrapTarget, context: InputContext, *, change_reference: str
) -> dict[str, Any]:
    return {
        "database": target.database,
        "profile": target.profile,
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "bootstrap_stage": "corrected_base",
        "input_identity_sha256": context.input_identity_sha256,
        "build_id": context.input_identity["build_id"],
        "value_normalization_version": VALUE_NORMALIZATION_VERSION,
        "change_reference": change_reference,
    }


def receipt_path(database: str, operation_id: str, *, root: Path = REPORT_ROOT) -> Path:
    if database not in ALLOWED_DATABASES:
        raise CorrectedBaseArtifactError("허용되지 않은 receipt database입니다")
    try:
        uuid.UUID(operation_id)
    except ValueError as exc:
        raise CorrectedBaseArtifactError(
            "receipt operation_id 형식이 잘못됐습니다"
        ) from exc
    return root / f"corrected_base.{database}.{operation_id}.json"


def marker_path(database: str, *, root: Path = MARKER_ROOT) -> Path:
    if database not in ALLOWED_DATABASES:
        raise CorrectedBaseArtifactError("허용되지 않은 marker database입니다")
    return root / f"corrected_base.{database}.json"


def alignment_report_path(database: str, *, root: Path = REPORT_ROOT) -> Path:
    if database != "kosa_text2sql":
        raise CorrectedBaseArtifactError("alignment report는 evaluation DB 전용입니다")
    return root / f"alignment_report.{database}.json"


def _artifact_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _validate_timestamp(value: Any, *, field: str) -> None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise CorrectedBaseArtifactError(f"{field} 시각 형식이 잘못됐습니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CorrectedBaseArtifactError(f"{field} 시각은 timezone-aware여야 합니다")


def validate_receipt(payload: Mapping[str, Any], target: BootstrapTarget) -> None:
    common = {
        "artifact_type",
        "format_version",
        "operation_id",
        "attempt",
        "status",
        "database",
        "profile",
        "dataset_epoch",
        "bootstrap_stage",
        "input_identity_sha256",
        "build_id",
        "value_normalization_version",
        "change_reference",
        "started_at",
        "state_before",
        "action_history_rows_before",
        "reference_rows_before",
    }
    status = payload.get("status")
    if status == "COMMITTED":
        expected = common | {
            "committed_at",
            "action_history_rows_after",
            "reference_rows_after",
            "inserted_rows",
            "fixed_rows",
            "alarm_event_rows",
            "schema_signature_sha256",
        }
    elif status == "ABORTED":
        expected = common | {"aborted_at", "abort_reason"}
    else:
        expected = common
    if set(payload) != expected:
        raise CorrectedBaseArtifactError("receipt key 집합이 잘못됐습니다")
    if (
        payload.get("artifact_type") != "corrected_base_receipt"
        or payload.get("format_version") != 1
        or status not in VALID_RECEIPT_STATUS
        or payload.get("database") != target.database
        or payload.get("profile") != target.profile
        or payload.get("dataset_epoch") != manifest_v3.DATASET_EPOCH
        or payload.get("bootstrap_stage") != "corrected_base"
        or payload.get("value_normalization_version") != VALUE_NORMALIZATION_VERSION
    ):
        raise CorrectedBaseArtifactError("receipt 계약 값이 잘못됐습니다")
    for field in ("input_identity_sha256", "build_id"):
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(payload.get(field, ""))):
            raise CorrectedBaseArtifactError(f"receipt {field} 형식이 잘못됐습니다")
    try:
        uuid.UUID(str(payload.get("operation_id")))
    except ValueError as exc:
        raise CorrectedBaseArtifactError(
            "receipt operation_id 형식이 잘못됐습니다"
        ) from exc
    if payload.get("state_before") not in VALID_STATES:
        raise CorrectedBaseArtifactError("receipt state_before가 잘못됐습니다")
    if not reference_extensions.CHANGE_REFERENCE_PATTERN.fullmatch(
        str(payload.get("change_reference", ""))
    ):
        raise CorrectedBaseArtifactError("receipt change reference가 잘못됐습니다")
    if not isinstance(payload.get("attempt"), int) or payload["attempt"] < 1:
        raise CorrectedBaseArtifactError("receipt attempt가 잘못됐습니다")
    for field in ("action_history_rows_before",):
        value = payload.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CorrectedBaseArtifactError(f"receipt {field}가 잘못됐습니다")
    reference_before = payload.get("reference_rows_before")
    if (
        not isinstance(reference_before, Mapping)
        or set(reference_before) != set(REFERENCE_IMMUTABLE_TABLES)
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in reference_before.values()
        )
    ):
        raise CorrectedBaseArtifactError("receipt reference_rows_before가 잘못됐습니다")
    _validate_timestamp(payload.get("started_at"), field="started_at")
    if status == "COMMITTED":
        _validate_timestamp(payload.get("committed_at"), field="committed_at")
        for field in (
            "action_history_rows_after",
            "inserted_rows",
            "fixed_rows",
            "alarm_event_rows",
        ):
            value = payload.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise CorrectedBaseArtifactError(f"receipt {field}가 잘못됐습니다")
        if (
            payload["action_history_rows_after"]
            != payload["action_history_rows_before"]
        ):
            raise CorrectedBaseArtifactError(
                "receipt action_history 불변 증거가 다릅니다"
            )
        if payload.get("reference_rows_after") != reference_before:
            raise CorrectedBaseArtifactError("receipt reference 불변 증거가 다릅니다")
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(
            str(payload.get("schema_signature_sha256", ""))
        ):
            raise CorrectedBaseArtifactError("receipt schema signature가 잘못됐습니다")
    elif status == "ABORTED":
        _validate_timestamp(payload.get("aborted_at"), field="aborted_at")
        if payload.get("abort_reason") not in VALID_ABORT_REASONS:
            raise CorrectedBaseArtifactError("receipt abort reason이 잘못됐습니다")
    manifest_v3.scan_for_sensitive_values(payload)


def _save_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    manifest_v3.scan_for_sensitive_values(payload)
    try:
        with _exclusive_lock(_artifact_lock_path(path)):
            manifest_v3.atomic_save_json(path, payload)
            loaded = _read_json(path, label=payload.get("artifact_type", "artifact"))
            if loaded != dict(payload):
                raise CorrectedBaseArtifactError(
                    "저장한 artifact 재검증에 실패했습니다"
                )
    except OSError as exc:
        raise CorrectedBaseArtifactError("artifact를 원자 저장할 수 없습니다") from exc


def _receipt_files(database: str, *, root: Path = REPORT_ROOT) -> list[Path]:
    return (
        sorted(root.glob(f"corrected_base.{database}.*.json")) if root.exists() else []
    )


def _load_receipts(
    target: BootstrapTarget, *, root: Path = REPORT_ROOT
) -> list[dict[str, Any]]:
    receipts = []
    for path in _receipt_files(target.database, root=root):
        payload = _read_json(path, label="corrected base receipt")
        validate_receipt(payload, target)
        receipts.append(payload)
    return sorted(receipts, key=lambda item: (item["attempt"], item["started_at"]))


def _start_receipt(
    target: BootstrapTarget,
    context: InputContext,
    state: DatabaseState,
    *,
    change_reference: str,
    root: Path = REPORT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    existing = _load_receipts(target, root=root)
    payload = {
        "artifact_type": "corrected_base_receipt",
        "format_version": 1,
        "operation_id": str(uuid.uuid4()),
        "attempt": max((item["attempt"] for item in existing), default=0) + 1,
        "status": "STARTED",
        **_artifact_common(target, context, change_reference=change_reference),
        "started_at": (now or datetime.now(UTC)).isoformat(),
        "state_before": state.name,
        "action_history_rows_before": state.action_history_rows,
        "reference_rows_before": state.reference_rows,
    }
    validate_receipt(payload, target)
    _save_artifact(
        receipt_path(target.database, payload["operation_id"], root=root), payload
    )
    return payload


def _commit_receipt(
    receipt: Mapping[str, Any],
    target: BootstrapTarget,
    result: PostcheckResult,
    *,
    inserted_rows: int,
    fixed_rows: int,
    root: Path = REPORT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = {
        **receipt,
        "status": "COMMITTED",
        "committed_at": (now or datetime.now(UTC)).isoformat(),
        "action_history_rows_after": result.action_history_rows,
        "reference_rows_after": result.reference_rows,
        "inserted_rows": inserted_rows,
        "fixed_rows": fixed_rows,
        "alarm_event_rows": result.alarm_event_rows,
        "schema_signature_sha256": result.schema_signature_sha256,
    }
    validate_receipt(payload, target)
    _save_artifact(
        receipt_path(target.database, payload["operation_id"], root=root), payload
    )
    return payload


def _abort_receipt(
    receipt: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    reason: str,
    root: Path = REPORT_ROOT,
) -> dict[str, Any]:
    if receipt.get("status") != "STARTED":
        return dict(receipt)
    payload = {
        **receipt,
        "status": "ABORTED",
        "aborted_at": datetime.now(UTC).isoformat(),
        "abort_reason": reason,
    }
    validate_receipt(payload, target)
    _save_artifact(
        receipt_path(target.database, payload["operation_id"], root=root), payload
    )
    return payload


def _profile_artifact_path(
    target: BootstrapTarget, *, marker_root: Path, report_root: Path
) -> Path:
    return (
        marker_path(target.database, root=marker_root)
        if target.profile == "runtime"
        else alignment_report_path(target.database, root=report_root)
    )


def _build_profile_artifact(
    receipt: Mapping[str, Any], target: BootstrapTarget, context: InputContext
) -> dict[str, Any]:
    if receipt.get("status") != "COMMITTED":
        raise CorrectedBaseArtifactError(
            "COMMITTED receipt만 완료 artifact로 승격합니다"
        )
    recorded_at = datetime.now(UTC).isoformat()
    if target.profile == "runtime":
        status = (
            "VERIFIED_EXISTING" if receipt["state_before"] == "ADOPTED" else "APPLIED"
        )
        payload = {
            "artifact_type": "corrected_base",
            "format_version": 1,
            **_artifact_common(
                target, context, change_reference=str(receipt["change_reference"])
            ),
            "status": status,
            "receipt_sha256": canonical_sha256(receipt),
            "action_history_rows": receipt["action_history_rows_after"],
            "alarm_event_rows": receipt["alarm_event_rows"],
            "applied_at": receipt["committed_at"],
            "recorded_at": recorded_at,
        }
    else:
        payload = {
            "artifact_type": "corrected_base_alignment",
            "format_version": 1,
            **_artifact_common(
                target, context, change_reference=str(receipt["change_reference"])
            ),
            "data_alignment_status": "COMPLETED",
            "stage_acceptance_status": "PENDING",
            "next_stage_task": "V4-CM-2.3",
            "observed_action_count": receipt["action_history_rows_after"],
            "receipt_sha256": canonical_sha256(receipt),
            "recorded_at": recorded_at,
        }
    validate_profile_artifact(payload, target)
    return payload


def validate_profile_artifact(
    payload: Mapping[str, Any], target: BootstrapTarget
) -> None:
    common = {
        "artifact_type",
        "format_version",
        "database",
        "profile",
        "dataset_epoch",
        "bootstrap_stage",
        "input_identity_sha256",
        "build_id",
        "value_normalization_version",
        "change_reference",
        "receipt_sha256",
        "recorded_at",
    }
    if target.profile == "runtime":
        expected = common | {
            "status",
            "action_history_rows",
            "alarm_event_rows",
            "applied_at",
        }
        valid = (
            payload.get("artifact_type") == "corrected_base"
            and payload.get("status") in {"APPLIED", "VERIFIED_EXISTING"}
            and payload.get("action_history_rows") == 0
        )
        _validate_timestamp(payload.get("applied_at"), field="applied_at")
    else:
        expected = common | {
            "data_alignment_status",
            "stage_acceptance_status",
            "next_stage_task",
            "observed_action_count",
        }
        valid = (
            payload.get("artifact_type") == "corrected_base_alignment"
            and payload.get("data_alignment_status") == "COMPLETED"
            and payload.get("stage_acceptance_status") == "PENDING"
            and payload.get("next_stage_task") == "V4-CM-2.3"
            and payload.get("observed_action_count") == 48
        )
    if (
        set(payload) != expected
        or not valid
        or payload.get("format_version") != 1
        or payload.get("database") != target.database
        or payload.get("profile") != target.profile
        or payload.get("dataset_epoch") != manifest_v3.DATASET_EPOCH
        or payload.get("bootstrap_stage") != "corrected_base"
        or payload.get("value_normalization_version") != VALUE_NORMALIZATION_VERSION
        or not reference_extensions.CHANGE_REFERENCE_PATTERN.fullmatch(
            str(payload.get("change_reference", ""))
        )
    ):
        raise CorrectedBaseArtifactError("profile 완료 artifact 계약이 잘못됐습니다")
    for field in ("input_identity_sha256", "build_id", "receipt_sha256"):
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(payload.get(field, ""))):
            raise CorrectedBaseArtifactError(
                f"profile 완료 artifact {field} 형식이 잘못됐습니다"
            )
    _validate_timestamp(payload.get("recorded_at"), field="recorded_at")
    if target.profile == "runtime":
        for field in ("action_history_rows", "alarm_event_rows"):
            value = payload.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise CorrectedBaseArtifactError(
                    f"profile 완료 artifact {field}가 잘못됐습니다"
                )
    manifest_v3.scan_for_sensitive_values(payload)


def _validate_existing_artifact_provenance(
    artifact: Mapping[str, Any],
    target: BootstrapTarget,
    context: InputContext,
    state: DatabaseState,
    result: PostcheckResult,
    *,
    report_root: Path,
) -> None:
    validate_profile_artifact(artifact, target)
    if artifact["input_identity_sha256"] != context.input_identity_sha256:
        raise CorrectedBaseArtifactError("완료 artifact input identity가 다릅니다")
    candidates = [
        receipt
        for receipt in _load_receipts(target, root=report_root)
        if receipt["status"] == "COMMITTED"
        and canonical_sha256(receipt) == artifact["receipt_sha256"]
    ]
    if len(candidates) != 1:
        raise CorrectedBaseArtifactError(
            "완료 artifact의 COMMITTED receipt가 정확히 1건이어야 합니다"
        )
    receipt = candidates[0]
    if (
        receipt["input_identity_sha256"] != context.input_identity_sha256
        or receipt["change_reference"] != artifact["change_reference"]
        or receipt["action_history_rows_after"] != state.action_history_rows
        or receipt["schema_signature_sha256"] != result.schema_signature_sha256
    ):
        raise CorrectedBaseArtifactError(
            "완료 artifact provenance가 현재 DB와 다릅니다"
        )


def _save_profile_artifact(
    payload: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    marker_root: Path,
    report_root: Path,
) -> None:
    validate_profile_artifact(payload, target)
    _save_artifact(
        _profile_artifact_path(
            target, marker_root=marker_root, report_root=report_root
        ),
        payload,
    )


def run_preflight(
    target: BootstrapTarget,
    context: InputContext,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
) -> DatabaseState:
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            return classify_database_state(connection, target, context)
    finally:
        engine.dispose()


def run_rehearsal(
    target: BootstrapTarget,
    context: InputContext,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
) -> PostcheckResult:
    if target.database != "kosa_agent_e2e":
        raise CorrectedBaseStateError("rehearse는 kosa_agent_e2e에서만 허용됩니다")
    engine = engine_factory(target)
    before: DatabaseState | None = None
    result: PostcheckResult | None = None
    try:
        try:
            with engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=False)
                before = classify_database_state(connection, target, context)
                if before.name != "EMPTY":
                    raise CorrectedBaseStateError(
                        "rehearse는 EMPTY E2E DB에서만 허용됩니다"
                    )
                _insert_corrected_rows(connection, context)
                result = postcheck_database(connection, target, context, before)
                raise _RehearsalRollback
        except _RehearsalRollback:
            pass
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            after = classify_database_state(connection, target, context)
            if before is None or after.name != before.name:
                raise CorrectedBaseStateError("rehearse rollback 뒤 DB 상태가 다릅니다")
        if result is None:
            raise CorrectedBaseStateError("rehearse postcheck 결과가 없습니다")
        return result
    finally:
        engine.dispose()


def _matching_recovery_receipts(
    target: BootstrapTarget,
    context: InputContext,
    *,
    change_reference: str,
    root: Path,
) -> list[dict[str, Any]]:
    return [
        receipt
        for receipt in _load_receipts(target, root=root)
        if receipt["status"] in {"STARTED", "COMMITTED"}
        and receipt["input_identity_sha256"] == context.input_identity_sha256
        and receipt["change_reference"] == change_reference
    ]


def run_apply_or_recover(
    target: BootstrapTarget,
    context: InputContext,
    *,
    recover_artifact: bool,
    change_reference: str,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
    after_database_commit: Callable[[], None] | None = None,
    after_receipt_commit: Callable[[], None] | None = None,
) -> str:
    if not reference_extensions.CHANGE_REFERENCE_PATTERN.fullmatch(change_reference):
        raise CorrectedBaseArtifactError("change reference 형식이 잘못됐습니다")
    engine = engine_factory(target)
    started: dict[str, Any] | None = None
    committed: dict[str, Any] | None = None
    result: PostcheckResult | None = None
    inserted_rows = 0
    fixed_rows = 0
    database_committed = False
    artifact_path = _profile_artifact_path(
        target, marker_root=marker_root, report_root=report_root
    )
    try:
        try:
            with engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=False)
                state = classify_database_state(connection, target, context)
                if state.name == "DRIFT":
                    raise CorrectedBaseStateError(
                        "DRIFT: corrected base와 다른 값이 있습니다"
                    )
                if artifact_path.exists():
                    artifact = _read_json(artifact_path, label="profile artifact")
                    if state.name != "ADOPTED":
                        raise CorrectedBaseStateError(
                            "LOST_DATA 또는 artifact provenance 불일치"
                        )
                    result = postcheck_database(connection, target, context, state)
                    _validate_existing_artifact_provenance(
                        artifact,
                        target,
                        context,
                        state,
                        result,
                        report_root=report_root,
                    )
                    return "NO_OP"
                if recover_artifact:
                    if state.name != "ADOPTED":
                        raise CorrectedBaseStateError(
                            "artifact 복구는 ADOPTED DB에서만 허용됩니다"
                        )
                    candidates = _matching_recovery_receipts(
                        target,
                        context,
                        change_reference=change_reference,
                        root=report_root,
                    )
                    if len(candidates) != 1:
                        raise CorrectedBaseArtifactError(
                            "복구할 STARTED/COMMITTED receipt가 정확히 1건이어야 합니다"
                        )
                    committed = candidates[0]
                    result = postcheck_database(connection, target, context, state)
                    expected_action_rows = (
                        committed.get("action_history_rows_after")
                        if committed["status"] == "COMMITTED"
                        else committed["action_history_rows_before"]
                    )
                    if expected_action_rows != state.action_history_rows:
                        raise CorrectedBaseStateError(
                            "복구 receipt와 현재 action_history가 다릅니다"
                        )
                    if (
                        committed["status"] == "COMMITTED"
                        and committed["schema_signature_sha256"]
                        != result.schema_signature_sha256
                    ):
                        raise CorrectedBaseStateError(
                            "복구 receipt와 현재 schema signature가 다릅니다"
                        )
                else:
                    stale = _matching_recovery_receipts(
                        target,
                        context,
                        change_reference=change_reference,
                        root=report_root,
                    )
                    if stale:
                        if (
                            len(stale) == 1
                            and stale[0]["status"] == "STARTED"
                            and state.name in {"EMPTY", "NEEDS_FIXUP"}
                        ):
                            _abort_receipt(
                                stale[0],
                                target,
                                reason="PREVIOUS_TRANSACTION_NOT_COMMITTED",
                                root=report_root,
                            )
                        else:
                            raise CorrectedBaseArtifactError(
                                "미완료 receipt가 있어 --recover-artifact가 필요합니다"
                            )
                    started = _start_receipt(
                        target,
                        context,
                        state,
                        change_reference=change_reference,
                        root=report_root,
                    )
                    if state.name == "EMPTY":
                        inserted_rows = _insert_corrected_rows(connection, context)
                    elif state.name == "NEEDS_FIXUP":
                        fixed_rows = _apply_fixup(connection, state.fixup_values or {})
                    elif state.name != "ADOPTED":
                        raise CorrectedBaseStateError("적용할 수 없는 DB 상태입니다")
                    result = postcheck_database(connection, target, context, state)
            database_committed = True
            if after_database_commit is not None:
                after_database_commit()
            if committed is None:
                if started is None or result is None:
                    raise CorrectedBaseArtifactError(
                        "적용 receipt/postcheck가 준비되지 않았습니다"
                    )
                committed = _commit_receipt(
                    started,
                    target,
                    result,
                    inserted_rows=inserted_rows,
                    fixed_rows=fixed_rows,
                    root=report_root,
                )
            elif committed["status"] == "STARTED":
                if result is None:
                    raise CorrectedBaseArtifactError(
                        "복구 postcheck가 준비되지 않았습니다"
                    )
                committed = _commit_receipt(
                    committed,
                    target,
                    result,
                    inserted_rows=0,
                    fixed_rows=0,
                    root=report_root,
                )
            if after_receipt_commit is not None:
                after_receipt_commit()
            artifact = _build_profile_artifact(committed, target, context)
            _save_profile_artifact(
                artifact,
                target,
                marker_root=marker_root,
                report_root=report_root,
            )
            return "RECOVERED" if recover_artifact else "APPLIED"
        except Exception:
            if started is not None and not database_committed:
                try:
                    _abort_receipt(
                        started,
                        target,
                        reason="TRANSACTION_ROLLED_BACK",
                        root=report_root,
                    )
                except CorrectedBaseArtifactError:
                    pass
            raise
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--rehearse", action="store_true")
    modes.add_argument("--recover-artifact", action="store_true")
    modes.add_argument("--register-manifests", action="store_true")
    parser.add_argument("--database", choices=sorted(ALLOWED_DATABASES))
    parser.add_argument("--confirm-target")
    parser.add_argument("--change-ref")
    parser.add_argument("--confirm", action="store_true")
    return parser


def _validate_cli(args: argparse.Namespace) -> None:
    if args.register_manifests:
        if args.database or args.confirm_target or args.change_ref:
            raise CorrectedBaseError(
                "manifest 등록에 DB 적용 옵션을 사용할 수 없습니다"
            )
        return
    if not args.database:
        raise CorrectedBaseError("DB 모드에는 --database가 필요합니다")
    if args.preflight:
        if args.confirm_target or args.change_ref or args.confirm:
            raise CorrectedBaseError("preflight에 mutation 옵션을 사용할 수 없습니다")
        return
    if args.confirm:
        raise CorrectedBaseError("--confirm은 --register-manifests 전용입니다")
    if args.confirm_target != args.database:
        raise CorrectedBaseError("mutation에는 정확한 --confirm-target이 필요합니다")
    if args.rehearse:
        if args.database != "kosa_agent_e2e" or args.change_ref:
            raise CorrectedBaseError(
                "rehearse는 E2E 전용이며 change-ref를 쓰지 않습니다"
            )
        return
    if not args.change_ref:
        raise CorrectedBaseError("apply/recover에는 --change-ref가 필요합니다")


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    args = _parser().parse_args(argv)
    try:
        _validate_cli(args)
        context = _load_input_context()
        if args.register_manifests:
            result = register_manifests(confirm=args.confirm, context=context)
        else:
            target = load_bootstrap_target(args.database)
            if args.preflight:
                state = run_preflight(target, context)
                result = state.name
            elif args.rehearse:
                run_rehearsal(target, context)
                result = "REHEARSED"
            else:
                result = run_apply_or_recover(
                    target,
                    context,
                    recover_artifact=args.recover_artifact,
                    change_reference=args.change_ref,
                )
        print(json.dumps({"status": result}, ensure_ascii=False, sort_keys=True))
        return 0 if result != "PREVIEW" else manifest_v3.EXIT_CONFIRM_REQUIRED
    except (
        CorrectedBaseError,
        manifest_v3.VerificationError,
        TargetValidationError,
        SQLAlchemyError,
        ValueNormalizationError,
    ) as exc:
        print(
            json.dumps(
                {"status": "FAILED", "reason_code": type(exc).__name__},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return getattr(exc, "exit_code", 2)


if __name__ == "__main__":
    sys.exit(main())
