"""Adopt or load the evaluation-only ``action_history`` Mock fixture.

The command mutates only ``kosa_text2sql.action_history`` and only when that
table is empty.  Existing rows must match the registered corrected bundle
exactly.  Completion is proven by a tracked marker; ignored receipts are used
only to recover an interrupted first adoption.
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
import load_corrected_base as corrected_loader
import manifest_v3
import verify_bootstrap_state as bootstrap_verifier
from build_corrected_dataset import _exclusive_lock
from db_target import BootstrapTarget
from master_cypher import canonical_sha256
from sqlalchemy.engine import Engine
from value_normalization import (
    VALUE_NORMALIZATION_VERSION,
    column_type_registry,
    normalize_csv_row,
    normalize_db_row,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
MANIFEST_ROOT = BOOTSTRAP_ROOT / "manifests"
MARKER_ROOT = BOOTSTRAP_ROOT / "markers"
REPORT_ROOT = BOOTSTRAP_ROOT / "reports"

TARGET_DATABASE = "kosa_text2sql"
TARGET_PROFILE = "evaluation"
BOOTSTRAP_STAGE = "evaluation_mock"
EXPECTED_ACTION_ROWS = 48
FIXTURE_TYPE = "MOCK"
STATEMENT_TIMEOUT = "120s"
REFERENCE_TABLES = corrected_loader.REFERENCE_IMMUTABLE_TABLES
FORBIDDEN_DML_TABLES = frozenset(
    {*corrected_loader.LOAD_TABLES, *REFERENCE_TABLES, "nl_query_log"}
)
VALID_STATES = frozenset({"MISSING_BASE", "EMPTY", "ADOPTED", "DRIFT"})
VALID_RECEIPT_STATUS = frozenset({"STARTED", "COMMITTED", "ABORTED"})
VALID_ABORT_REASONS = frozenset(
    {"TRANSACTION_ROLLED_BACK", "PREVIOUS_TRANSACTION_NOT_COMMITTED"}
)


class EvaluationMockError(manifest_v3.VerificationError):
    """Evaluation Mock input or operation contract failed."""


class EvaluationMockStateError(manifest_v3.ArtifactMismatchError):
    """The live database is not in a safe fixture transition state."""


class EvaluationMockArtifactError(manifest_v3.ManifestSchemaError):
    """A receipt or completion marker is malformed or inconsistent."""


class _RehearsalRollback(RuntimeError):
    pass


@dataclass(frozen=True)
class ManifestContext:
    base: corrected_loader.InputContext
    manifest: dict[str, Any]
    manifest_sha256: str
    expected_rows: tuple[dict[str, Any], ...]
    expected_hash: str
    action_entry: dict[str, Any]


@dataclass(frozen=True)
class DatabaseState:
    name: str
    action_history_rows: int
    action_history_hash: str
    reference_rows: dict[str, int]
    reference_hashes: dict[str, str]
    alarm_event_rows: int
    schema_signature_sha256: str
    mismatched_tables: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityContext:
    adoption_input_identity: dict[str, Any]
    adoption_input_identity_sha256: str
    fixture_identity: dict[str, Any]
    fixture_identity_sha256: str


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationMockArtifactError(f"{label} JSON을 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise EvaluationMockArtifactError(f"{label} 최상위 값은 object여야 합니다")
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def _action_columns() -> list[str]:
    return [column.name for column in base_schema.BASE_COLUMNS["action_history"]]


def _action_types() -> dict[str, str]:
    return column_type_registry(base_schema.BASE_COLUMNS["action_history"])


def _expected_action_rows(
    base: corrected_loader.InputContext,
) -> tuple[dict[str, Any], ...]:
    table = base.bundle.tables.get("action_history")
    if table is None or list(table.columns) != _action_columns():
        raise EvaluationMockArtifactError(
            "active corrected action_history column 계약이 다릅니다"
        )
    rows = tuple(normalize_csv_row(row, _action_types()) for row in table.rows)
    if len(rows) != EXPECTED_ACTION_ROWS:
        raise EvaluationMockArtifactError(
            "active corrected action_history는 정확히 48행이어야 합니다"
        )
    return rows


def build_evaluation_mock_manifest(
    base: corrected_loader.InputContext,
) -> dict[str, Any]:
    rows = _expected_action_rows(base)
    payload = corrected_loader.build_corrected_base_manifest(TARGET_PROFILE, base)
    payload["bootstrap_stage"] = BOOTSTRAP_STAGE
    payload["tables"]["action_history"] = {
        "columns": _action_columns(),
        "verification_policy": "immutable_content",
        "row_count": EXPECTED_ACTION_ROWS,
        "content_hash": manifest_v3.hash_canonical_rows(rows),
        "fixture_type": FIXTURE_TYPE,
    }
    epoch = manifest_v3.load_dataset_epoch()
    manifest_v3.validate_manifest_schema(
        payload,
        expected_artifact_type="db_bootstrap",
        expected_profile=TARGET_PROFILE,
        expected_stage=BOOTSTRAP_STAGE,
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    return payload


def _registered_manifest_matches_active_contract(
    registered: Mapping[str, Any], candidate: Mapping[str, Any]
) -> bool:
    """Keep future reference rows while pinning this Task's owned contract."""

    if set(registered) != set(candidate):
        return False
    if any(registered[key] != candidate[key] for key in candidate if key != "tables"):
        return False
    registered_tables = registered.get("tables")
    candidate_tables = candidate.get("tables")
    if not isinstance(registered_tables, Mapping) or not isinstance(
        candidate_tables, Mapping
    ):
        return False
    if set(registered_tables) != set(candidate_tables):
        return False
    owned_tables = set(candidate_tables) - set(REFERENCE_TABLES)
    return all(
        registered_tables[table] == candidate_tables[table] for table in owned_tables
    )


def _load_manifest_context(*, require_registered: bool) -> ManifestContext:
    base = corrected_loader._load_input_context()
    rows = _expected_action_rows(base)
    candidate = build_evaluation_mock_manifest(base)
    path = MANIFEST_ROOT / "evaluation.evaluation_mock.json"
    manifest = candidate
    if require_registered:
        manifest = _read_json(path, label="evaluation Mock manifest")
        epoch = manifest_v3.load_dataset_epoch()
        manifest_v3.validate_manifest_schema(
            manifest,
            expected_artifact_type="db_bootstrap",
            expected_profile=TARGET_PROFILE,
            expected_stage=BOOTSTRAP_STAGE,
            expected_archive_sha256=epoch["archive"]["sha256"],
        )
        if not _registered_manifest_matches_active_contract(manifest, candidate):
            raise EvaluationMockArtifactError(
                "등록 evaluation Mock manifest의 action/base 계약이 "
                "active corrected bundle과 다릅니다"
            )
    action_entry = dict(manifest["tables"]["action_history"])
    return ManifestContext(
        base=base,
        manifest=manifest,
        manifest_sha256=canonical_sha256(manifest),
        expected_rows=rows,
        expected_hash=manifest_v3.hash_canonical_rows(rows),
        action_entry=action_entry,
    )


def register_manifest(*, confirm: bool) -> str:
    context = _load_manifest_context(require_registered=False)
    path = MANIFEST_ROOT / "evaluation.evaluation_mock.json"
    existing = (
        _read_json(path, label="evaluation Mock manifest") if path.exists() else None
    )
    if existing is not None:
        try:
            epoch = manifest_v3.load_dataset_epoch()
            manifest_v3.validate_manifest_schema(
                existing,
                expected_artifact_type="db_bootstrap",
                expected_profile=TARGET_PROFILE,
                expected_stage=BOOTSTRAP_STAGE,
                expected_archive_sha256=epoch["archive"]["sha256"],
            )
        except manifest_v3.VerificationError:
            pass
        else:
            if _registered_manifest_matches_active_contract(existing, context.manifest):
                return "NO_OP"
    if not confirm:
        return "PREVIEW"
    try:
        manifest_v3.atomic_save_json(path, context.manifest)
    except OSError as exc:
        raise EvaluationMockArtifactError(
            "evaluation Mock manifest를 원자 저장할 수 없습니다"
        ) from exc
    return "REGISTERED"


def _result_rows(result: Any) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in result.mappings().all()]
    except (AttributeError, TypeError, ValueError) as exc:
        raise EvaluationMockStateError("DB query 응답 형식이 잘못됐습니다") from exc


def _scalar(result: Any) -> Any:
    try:
        return result.scalar_one()
    except (AttributeError, LookupError, TypeError) as exc:
        raise EvaluationMockStateError("DB scalar 응답 형식이 잘못됐습니다") from exc


def _read_table(
    connection: Any, table: str, entry: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    columns = list(entry["columns"])
    selected = ", ".join(f'"{column}"' for column in columns)
    rows = _result_rows(connection.exec_driver_sql(f'SELECT {selected} FROM "{table}"'))
    types = bootstrap_verifier._expected_column_types(table)
    return tuple(normalize_db_row(row, types) for row in rows)


def _registered_reference_marker(target: BootstrapTarget) -> dict[str, Any]:
    sql, _ = reference_extensions.load_and_validate_sql()
    migration_sha = reference_extensions._migration_sha256(sql)
    marker = reference_extensions.load_marker(target, migration_sha256=migration_sha)
    if marker is None:
        raise EvaluationMockStateError("MISSING_BASE: 001 marker가 없습니다")
    return marker


def classify_database_state(
    connection: Any,
    target: BootstrapTarget,
    context: ManifestContext,
    *,
    reference_marker_root: Path = reference_extensions.MARKER_ROOT,
) -> DatabaseState:
    if target.database != TARGET_DATABASE or target.profile != TARGET_PROFILE:
        raise EvaluationMockStateError("evaluation Mock 대상 DB가 아닙니다")
    try:
        inspection = corrected_loader._preflight_001(
            connection, target, marker_root=reference_marker_root
        )
    except corrected_loader.CorrectedBaseError as exc:
        raise EvaluationMockStateError(
            "MISSING_BASE: 001/base 계약이 다릅니다"
        ) from exc

    mismatches: list[str] = []
    for table in corrected_loader.LOAD_TABLES:
        entry = context.manifest["tables"][table]
        actual = _read_table(connection, table, entry)
        if (
            len(actual) != entry["row_count"]
            or manifest_v3.hash_canonical_rows(actual) != entry["content_hash"]
        ):
            mismatches.append(table)

    reference_rows: dict[str, int] = {}
    reference_hashes: dict[str, str] = {}
    for table in REFERENCE_TABLES:
        entry = context.manifest["tables"][table]
        actual = _read_table(connection, table, entry)
        actual_hash = manifest_v3.hash_canonical_rows(actual)
        reference_rows[table] = len(actual)
        reference_hashes[table] = actual_hash
        if len(actual) != entry["row_count"] or actual_hash != entry["content_hash"]:
            mismatches.append(table)

    action_entry = context.action_entry
    action_rows = _read_table(connection, "action_history", action_entry)
    action_hash = manifest_v3.hash_canonical_rows(action_rows)
    reference = reference_extensions.postcheck_database(
        connection, action_rows_before=len(action_rows)
    )
    if reference.schema_signature_sha256 != inspection.schema_signature_sha256:
        mismatches.append("reference_signature")
    if mismatches:
        return DatabaseState(
            "MISSING_BASE",
            len(action_rows),
            action_hash,
            reference_rows,
            reference_hashes,
            reference.alarm_event_rows,
            reference.schema_signature_sha256,
            tuple(sorted(mismatches)),
        )
    if not action_rows:
        name = "EMPTY"
    elif (
        len(action_rows) == EXPECTED_ACTION_ROWS
        and action_hash == context.expected_hash
    ):
        name = "ADOPTED"
    else:
        name = "DRIFT"
    return DatabaseState(
        name,
        len(action_rows),
        action_hash,
        reference_rows,
        reference_hashes,
        reference.alarm_event_rows,
        reference.schema_signature_sha256,
    )


def build_identity_context(
    target: BootstrapTarget,
    context: ManifestContext,
    state: DatabaseState,
    *,
    reference_marker_root: Path = reference_extensions.MARKER_ROOT,
) -> IdentityContext:
    sql, _ = reference_extensions.load_and_validate_sql()
    migration_sha = reference_extensions._migration_sha256(sql)
    marker = reference_extensions.load_marker(
        target, migration_sha256=migration_sha, root=reference_marker_root
    )
    if marker is None:
        raise EvaluationMockStateError("MISSING_BASE: 001 marker가 없습니다")
    base_identity = context.base.input_identity
    adoption = {
        "evaluation_mock_manifest_sha256": context.manifest_sha256,
        "reference_extensions_marker_sha256": canonical_sha256(marker),
        "schema_signature_sha256": state.schema_signature_sha256,
        "corrected_manifest_sha256": base_identity["corrected_manifest_sha256"],
        "corrected_registration_marker_sha256": base_identity[
            "corrected_registration_marker_sha256"
        ],
        "build_id": base_identity["build_id"],
        "generator_revision": base_identity["generator_revision"],
        "generator_sha256": base_identity["generator_sha256"],
        "correction_version": base_identity["correction_version"],
    }
    fixture = {
        "artifact_type": "evaluation_mock_fixture_identity",
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "correction_version": context.manifest["correction_version"],
        "value_normalization_version": context.manifest["value_normalization_version"],
        "schema_signature_sha256": state.schema_signature_sha256,
        "action_history": context.action_entry,
    }
    manifest_v3.scan_for_sensitive_values(adoption)
    manifest_v3.scan_for_sensitive_values(fixture)
    return IdentityContext(
        adoption,
        canonical_sha256(adoption),
        fixture,
        canonical_sha256(fixture),
    )


def _insert_action_rows(connection: Any, context: ManifestContext) -> int:
    columns = _action_columns()
    names = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join(f"%({column})s" for column in columns)
    rows = [dict(row) for row in context.expected_rows]
    result = connection.exec_driver_sql(
        f'INSERT INTO "action_history" ({names}) VALUES ({placeholders})', rows
    )
    rowcount = getattr(result, "rowcount", len(rows))
    if rowcount not in {-1, EXPECTED_ACTION_ROWS}:
        raise EvaluationMockStateError("action_history 적재 행 수가 다릅니다")
    return EXPECTED_ACTION_ROWS


def postcheck_database(
    connection: Any,
    target: BootstrapTarget,
    context: ManifestContext,
    before: DatabaseState,
    *,
    reference_marker_root: Path = reference_extensions.MARKER_ROOT,
) -> DatabaseState:
    after = classify_database_state(
        connection,
        target,
        context,
        reference_marker_root=reference_marker_root,
    )
    if after.name != "ADOPTED":
        raise EvaluationMockStateError(
            "postcheck에서 Mock fixture가 ADOPTED가 아닙니다"
        )
    if (
        after.reference_rows != before.reference_rows
        or after.reference_hashes != before.reference_hashes
        or after.alarm_event_rows != before.alarm_event_rows
        or after.schema_signature_sha256 != before.schema_signature_sha256
    ):
        raise EvaluationMockStateError(
            "Mock 적용 중 base/reference/View가 변경됐습니다"
        )
    return after


def _prepare_transaction(
    connection: Any, target: BootstrapTarget, *, readonly: bool
) -> None:
    corrected_loader.prepare_transaction(
        connection,
        target,
        readonly=readonly,
        isolation_level="REPEATABLE READ",
        acquire_lock=reference_extensions.acquire_advisory_lock,
    )
    connection.exec_driver_sql(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")


def _engine_for(target: BootstrapTarget) -> Engine:
    return corrected_loader._engine_for(target)


def receipt_path(operation_id: str, *, root: Path = REPORT_ROOT) -> Path:
    try:
        uuid.UUID(operation_id)
    except ValueError as exc:
        raise EvaluationMockArtifactError(
            "receipt operation_id 형식이 잘못됐습니다"
        ) from exc
    return root / f"evaluation_mock.{TARGET_DATABASE}.{operation_id}.json"


def marker_path(*, root: Path = MARKER_ROOT) -> Path:
    return root / f"evaluation_mock.{TARGET_DATABASE}.json"


def _artifact_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _validate_timestamp(value: Any, *, field: str) -> None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise EvaluationMockArtifactError(f"{field} 시각 형식이 잘못됐습니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvaluationMockArtifactError(f"{field} 시각은 timezone-aware여야 합니다")


def _artifact_common(
    identities: IdentityContext, *, change_reference: str
) -> dict[str, Any]:
    return {
        "database": TARGET_DATABASE,
        "profile": TARGET_PROFILE,
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "bootstrap_stage": BOOTSTRAP_STAGE,
        "adoption_input_identity_sha256": identities.adoption_input_identity_sha256,
        "fixture_identity_sha256": identities.fixture_identity_sha256,
        "value_normalization_version": VALUE_NORMALIZATION_VERSION,
        "change_reference": change_reference,
    }


def validate_receipt(payload: Mapping[str, Any]) -> None:
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
        "adoption_input_identity_sha256",
        "fixture_identity_sha256",
        "value_normalization_version",
        "change_reference",
        "started_at",
        "state_before",
        "action_history_rows_before",
        "reference_rows_before",
        "reference_hashes_before",
    }
    status = payload.get("status")
    if status == "COMMITTED":
        expected = common | {
            "committed_at",
            "action_history_rows_after",
            "action_history_hash_after",
            "reference_rows_after",
            "reference_hashes_after",
            "inserted_rows",
            "alarm_event_rows",
            "schema_signature_sha256",
        }
    elif status == "ABORTED":
        expected = common | {"aborted_at", "abort_reason"}
    else:
        expected = common
    if set(payload) != expected:
        raise EvaluationMockArtifactError("receipt key 집합이 잘못됐습니다")
    if (
        payload.get("artifact_type") != "evaluation_mock_receipt"
        or payload.get("format_version") != 1
        or status not in VALID_RECEIPT_STATUS
        or payload.get("database") != TARGET_DATABASE
        or payload.get("profile") != TARGET_PROFILE
        or payload.get("dataset_epoch") != manifest_v3.DATASET_EPOCH
        or payload.get("bootstrap_stage") != BOOTSTRAP_STAGE
        or payload.get("value_normalization_version") != VALUE_NORMALIZATION_VERSION
        or payload.get("state_before") not in VALID_STATES
    ):
        raise EvaluationMockArtifactError("receipt 계약 값이 잘못됐습니다")
    for field in ("adoption_input_identity_sha256", "fixture_identity_sha256"):
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(payload.get(field, ""))):
            raise EvaluationMockArtifactError(f"receipt {field} 형식이 잘못됐습니다")
    try:
        uuid.UUID(str(payload.get("operation_id")))
    except ValueError as exc:
        raise EvaluationMockArtifactError(
            "receipt operation_id 형식이 잘못됐습니다"
        ) from exc
    if not reference_extensions.CHANGE_REFERENCE_PATTERN.fullmatch(
        str(payload.get("change_reference", ""))
    ):
        raise EvaluationMockArtifactError("receipt change reference가 잘못됐습니다")
    if not isinstance(payload.get("attempt"), int) or payload["attempt"] < 1:
        raise EvaluationMockArtifactError("receipt attempt가 잘못됐습니다")
    action_rows_before = payload.get("action_history_rows_before")
    if (
        not isinstance(action_rows_before, int)
        or isinstance(action_rows_before, bool)
        or action_rows_before < 0
    ):
        raise EvaluationMockArtifactError("receipt action row 수가 잘못됐습니다")
    for field in ("reference_rows_before", "reference_hashes_before"):
        value = payload.get(field)
        if not isinstance(value, Mapping) or set(value) != set(REFERENCE_TABLES):
            raise EvaluationMockArtifactError(f"receipt {field}가 잘못됐습니다")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in payload["reference_rows_before"].values()
    ) or any(
        not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(value))
        for value in payload["reference_hashes_before"].values()
    ):
        raise EvaluationMockArtifactError("receipt reference snapshot이 잘못됐습니다")
    _validate_timestamp(payload.get("started_at"), field="started_at")
    if status == "COMMITTED":
        _validate_timestamp(payload.get("committed_at"), field="committed_at")
        if (
            payload.get("action_history_rows_after") != EXPECTED_ACTION_ROWS
            or not manifest_v3.HEX_SHA256_PATTERN.fullmatch(
                str(payload.get("action_history_hash_after", ""))
            )
            or payload.get("reference_rows_after")
            != payload.get("reference_rows_before")
            or payload.get("reference_hashes_after")
            != payload.get("reference_hashes_before")
            or payload.get("inserted_rows") not in {0, EXPECTED_ACTION_ROWS}
            or not isinstance(payload.get("alarm_event_rows"), int)
            or isinstance(payload.get("alarm_event_rows"), bool)
            or payload["alarm_event_rows"] < 0
            or not manifest_v3.HEX_SHA256_PATTERN.fullmatch(
                str(payload.get("schema_signature_sha256", ""))
            )
        ):
            raise EvaluationMockArtifactError("COMMITTED receipt 증거가 잘못됐습니다")
        expected_inserted = (
            EXPECTED_ACTION_ROWS if payload["state_before"] == "EMPTY" else 0
        )
        if (
            payload["state_before"] not in {"EMPTY", "ADOPTED"}
            or payload["inserted_rows"] != expected_inserted
        ):
            raise EvaluationMockArtifactError(
                "COMMITTED receipt 전이 증거가 잘못됐습니다"
            )
    elif status == "ABORTED":
        _validate_timestamp(payload.get("aborted_at"), field="aborted_at")
        if payload.get("abort_reason") not in VALID_ABORT_REASONS:
            raise EvaluationMockArtifactError("receipt abort reason이 잘못됐습니다")
    manifest_v3.scan_for_sensitive_values(payload)


def validate_marker(payload: Mapping[str, Any]) -> None:
    expected = {
        "artifact_type",
        "format_version",
        "database",
        "profile",
        "status",
        "dataset_epoch",
        "correction_version",
        "bootstrap_stage",
        "fixture_identity_sha256",
        "manifest_sha256_at_adoption",
        "corrected_bundle_identity_sha256",
        "schema_signature_sha256",
        "action_history_rows",
        "action_history_hash",
        "fixture_type",
        "alarm_event_rows",
        "receipt_sha256",
        "change_reference",
        "applied_at",
        "recorded_at",
    }
    if set(payload) != expected:
        raise EvaluationMockArtifactError("marker key 집합이 잘못됐습니다")
    if (
        payload.get("artifact_type") != "evaluation_mock"
        or payload.get("format_version") != 1
        or payload.get("database") != TARGET_DATABASE
        or payload.get("profile") != TARGET_PROFILE
        or payload.get("status") not in {"APPLIED", "VERIFIED_EXISTING"}
        or payload.get("dataset_epoch") != manifest_v3.DATASET_EPOCH
        or payload.get("bootstrap_stage") != BOOTSTRAP_STAGE
        or payload.get("action_history_rows") != EXPECTED_ACTION_ROWS
        or payload.get("fixture_type") != FIXTURE_TYPE
        or not isinstance(payload.get("alarm_event_rows"), int)
        or isinstance(payload.get("alarm_event_rows"), bool)
        or payload["alarm_event_rows"] < 0
    ):
        raise EvaluationMockArtifactError("marker 계약 값이 잘못됐습니다")
    for field in (
        "fixture_identity_sha256",
        "manifest_sha256_at_adoption",
        "corrected_bundle_identity_sha256",
        "schema_signature_sha256",
        "action_history_hash",
        "receipt_sha256",
    ):
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(payload.get(field, ""))):
            raise EvaluationMockArtifactError(f"marker {field} 형식이 잘못됐습니다")
    if (
        not isinstance(payload.get("correction_version"), str)
        or not payload["correction_version"]
    ):
        raise EvaluationMockArtifactError("marker correction version이 잘못됐습니다")
    if not reference_extensions.CHANGE_REFERENCE_PATTERN.fullmatch(
        str(payload.get("change_reference", ""))
    ):
        raise EvaluationMockArtifactError("marker change reference가 잘못됐습니다")
    for field in ("applied_at", "recorded_at"):
        _validate_timestamp(payload.get(field), field=field)
    manifest_v3.scan_for_sensitive_values(payload)


def _save_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    manifest_v3.scan_for_sensitive_values(payload)
    try:
        with _exclusive_lock(_artifact_lock_path(path)):
            manifest_v3.atomic_save_json(path, payload)
            if _read_json(path, label=str(payload.get("artifact_type"))) != dict(
                payload
            ):
                raise EvaluationMockArtifactError("저장 artifact 재검증에 실패했습니다")
    except OSError as exc:
        raise EvaluationMockArtifactError("artifact를 원자 저장할 수 없습니다") from exc


def _receipt_files(*, root: Path = REPORT_ROOT) -> list[Path]:
    return (
        sorted(root.glob(f"evaluation_mock.{TARGET_DATABASE}.*.json"))
        if root.exists()
        else []
    )


def _load_receipts(*, root: Path = REPORT_ROOT) -> list[dict[str, Any]]:
    receipts = []
    for path in _receipt_files(root=root):
        payload = _read_json(path, label="evaluation Mock receipt")
        validate_receipt(payload)
        receipts.append(payload)
    return sorted(receipts, key=lambda item: (item["attempt"], item["started_at"]))


def _start_receipt(
    identities: IdentityContext,
    state: DatabaseState,
    *,
    change_reference: str,
    root: Path,
) -> dict[str, Any]:
    existing = _load_receipts(root=root)
    payload = {
        "artifact_type": "evaluation_mock_receipt",
        "format_version": 1,
        "operation_id": str(uuid.uuid4()),
        "attempt": max((item["attempt"] for item in existing), default=0) + 1,
        "status": "STARTED",
        **_artifact_common(identities, change_reference=change_reference),
        "started_at": datetime.now(UTC).isoformat(),
        "state_before": state.name,
        "action_history_rows_before": state.action_history_rows,
        "reference_rows_before": state.reference_rows,
        "reference_hashes_before": state.reference_hashes,
    }
    validate_receipt(payload)
    _save_artifact(receipt_path(payload["operation_id"], root=root), payload)
    return payload


def _commit_receipt(
    receipt: Mapping[str, Any],
    state: DatabaseState,
    *,
    inserted_rows: int,
    root: Path,
) -> dict[str, Any]:
    payload = {
        **receipt,
        "status": "COMMITTED",
        "committed_at": datetime.now(UTC).isoformat(),
        "action_history_rows_after": state.action_history_rows,
        "action_history_hash_after": state.action_history_hash,
        "reference_rows_after": state.reference_rows,
        "reference_hashes_after": state.reference_hashes,
        "inserted_rows": inserted_rows,
        "alarm_event_rows": state.alarm_event_rows,
        "schema_signature_sha256": state.schema_signature_sha256,
    }
    validate_receipt(payload)
    _save_artifact(receipt_path(payload["operation_id"], root=root), payload)
    return payload


def _abort_receipt(
    receipt: Mapping[str, Any], *, reason: str, root: Path
) -> dict[str, Any]:
    if receipt.get("status") != "STARTED":
        return dict(receipt)
    payload = {
        **receipt,
        "status": "ABORTED",
        "aborted_at": datetime.now(UTC).isoformat(),
        "abort_reason": reason,
    }
    validate_receipt(payload)
    _save_artifact(receipt_path(payload["operation_id"], root=root), payload)
    return payload


def _build_marker(
    receipt: Mapping[str, Any],
    context: ManifestContext,
    identities: IdentityContext,
) -> dict[str, Any]:
    if receipt.get("status") != "COMMITTED":
        raise EvaluationMockArtifactError("COMMITTED receipt만 marker로 승격합니다")
    now = datetime.now(UTC).isoformat()
    payload = {
        "artifact_type": "evaluation_mock",
        "format_version": 1,
        "database": TARGET_DATABASE,
        "profile": TARGET_PROFILE,
        "status": (
            "VERIFIED_EXISTING" if receipt["state_before"] == "ADOPTED" else "APPLIED"
        ),
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "correction_version": context.manifest["correction_version"],
        "bootstrap_stage": BOOTSTRAP_STAGE,
        "fixture_identity_sha256": identities.fixture_identity_sha256,
        "manifest_sha256_at_adoption": context.manifest_sha256,
        "corrected_bundle_identity_sha256": context.base.input_identity_sha256,
        "schema_signature_sha256": receipt["schema_signature_sha256"],
        "action_history_rows": receipt["action_history_rows_after"],
        "action_history_hash": receipt["action_history_hash_after"],
        "fixture_type": FIXTURE_TYPE,
        "alarm_event_rows": receipt["alarm_event_rows"],
        "receipt_sha256": canonical_sha256(receipt),
        "change_reference": receipt["change_reference"],
        "applied_at": receipt["committed_at"],
        "recorded_at": now,
    }
    validate_marker(payload)
    return payload


def _validate_marker_against_state(
    marker: Mapping[str, Any],
    state: DatabaseState,
    identities: IdentityContext,
    context: ManifestContext,
) -> None:
    validate_marker(marker)
    if (
        marker["fixture_identity_sha256"] != identities.fixture_identity_sha256
        or marker["schema_signature_sha256"] != state.schema_signature_sha256
        or marker["action_history_rows"] != state.action_history_rows
        or marker["action_history_hash"] != state.action_history_hash
        or marker["action_history_hash"] != context.expected_hash
    ):
        raise EvaluationMockArtifactError("Mock marker가 현재 fixture/DB와 다릅니다")


def verify_completion_marker(
    connection: Any,
    target: BootstrapTarget,
    registered_manifest: Mapping[str, Any],
    *,
    marker_root: Path = MARKER_ROOT,
    reference_marker_root: Path = reference_extensions.MARKER_ROOT,
) -> dict[str, Any]:
    context = _load_manifest_context(require_registered=True)
    if dict(registered_manifest) != context.manifest:
        raise EvaluationMockArtifactError(
            "verifier manifest와 Mock manifest가 다릅니다"
        )
    state = classify_database_state(
        connection,
        target,
        context,
        reference_marker_root=reference_marker_root,
    )
    if state.name != "ADOPTED":
        raise EvaluationMockStateError("Mock fixture가 ADOPTED 상태가 아닙니다")
    identities = build_identity_context(
        target,
        context,
        state,
        reference_marker_root=reference_marker_root,
    )
    marker = _read_json(marker_path(root=marker_root), label="evaluation Mock marker")
    _validate_marker_against_state(marker, state, identities, context)
    return marker


def run_preflight(
    target: BootstrapTarget,
    context: ManifestContext,
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
    context: ManifestContext,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
) -> DatabaseState:
    engine = engine_factory(target)
    before: DatabaseState | None = None
    result: DatabaseState | None = None
    try:
        try:
            with engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=False)
                before = classify_database_state(connection, target, context)
                if before.name == "EMPTY":
                    _insert_action_rows(connection, context)
                elif before.name != "ADOPTED":
                    raise EvaluationMockStateError(
                        "rehearse는 EMPTY 또는 ADOPTED DB에서만 허용됩니다"
                    )
                result = postcheck_database(connection, target, context, before)
                raise _RehearsalRollback
        except _RehearsalRollback:
            pass
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            after = classify_database_state(connection, target, context)
            if before is None or after.name != before.name:
                raise EvaluationMockStateError(
                    "rehearse rollback 뒤 DB 상태가 다릅니다"
                )
        if result is None:
            raise EvaluationMockStateError("rehearse postcheck 결과가 없습니다")
        return result
    finally:
        engine.dispose()


def _matching_recovery_receipts(
    identities: IdentityContext, *, change_reference: str, root: Path
) -> list[dict[str, Any]]:
    return [
        receipt
        for receipt in _load_receipts(root=root)
        if receipt["status"] in {"STARTED", "COMMITTED"}
        and receipt["adoption_input_identity_sha256"]
        == identities.adoption_input_identity_sha256
        and receipt["fixture_identity_sha256"] == identities.fixture_identity_sha256
        and receipt["change_reference"] == change_reference
    ]


def _validate_committed_receipt_against_state(
    receipt: Mapping[str, Any],
    state: DatabaseState,
    context: ManifestContext,
    identities: IdentityContext,
) -> None:
    if receipt.get("status") != "COMMITTED":
        raise EvaluationMockArtifactError("COMMITTED receipt가 아닙니다")
    if (
        receipt["fixture_identity_sha256"] != identities.fixture_identity_sha256
        or receipt["action_history_rows_after"] != state.action_history_rows
        or receipt["action_history_hash_after"] != state.action_history_hash
        or receipt["action_history_hash_after"] != context.expected_hash
        or receipt["reference_rows_after"] != state.reference_rows
        or receipt["reference_hashes_after"] != state.reference_hashes
        or receipt["alarm_event_rows"] != state.alarm_event_rows
        or receipt["schema_signature_sha256"] != state.schema_signature_sha256
    ):
        raise EvaluationMockArtifactError("COMMITTED receipt가 현재 DB와 다릅니다")


def run_apply_or_recover(
    target: BootstrapTarget,
    context: ManifestContext,
    *,
    recover_artifact: bool,
    change_reference: str,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
    reference_marker_root: Path = reference_extensions.MARKER_ROOT,
    after_database_commit: Callable[[], None] | None = None,
    after_receipt_commit: Callable[[], None] | None = None,
) -> str:
    if not reference_extensions.CHANGE_REFERENCE_PATTERN.fullmatch(change_reference):
        raise EvaluationMockArtifactError("change reference 형식이 잘못됐습니다")
    engine = engine_factory(target)
    started: dict[str, Any] | None = None
    committed: dict[str, Any] | None = None
    result: DatabaseState | None = None
    identities: IdentityContext | None = None
    inserted_rows = 0
    database_committed = False
    completion_path = marker_path(root=marker_root)
    try:
        try:
            with engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=False)
                state = classify_database_state(
                    connection,
                    target,
                    context,
                    reference_marker_root=reference_marker_root,
                )
                if state.name == "MISSING_BASE":
                    raise EvaluationMockStateError(
                        "MISSING_BASE: base/reference stage가 다릅니다"
                    )
                if state.name == "DRIFT":
                    raise EvaluationMockStateError(
                        "DRIFT: action_history가 제공 Mock과 다릅니다"
                    )
                identities = build_identity_context(
                    target,
                    context,
                    state,
                    reference_marker_root=reference_marker_root,
                )
                if completion_path.exists():
                    if state.name != "ADOPTED":
                        raise EvaluationMockStateError(
                            "LOST_DATA: Mock marker와 DB가 다릅니다"
                        )
                    result = postcheck_database(
                        connection,
                        target,
                        context,
                        state,
                        reference_marker_root=reference_marker_root,
                    )
                    marker = _read_json(completion_path, label="evaluation Mock marker")
                    _validate_marker_against_state(marker, result, identities, context)
                    return "NO_OP"
                if recover_artifact:
                    if state.name != "ADOPTED":
                        raise EvaluationMockStateError(
                            "artifact 복구는 ADOPTED DB에서만 허용됩니다"
                        )
                    candidates = _matching_recovery_receipts(
                        identities, change_reference=change_reference, root=report_root
                    )
                    if len(candidates) != 1:
                        raise EvaluationMockArtifactError(
                            "복구할 STARTED/COMMITTED receipt가 정확히 1건이어야 합니다"
                        )
                    committed = candidates[0]
                    result = postcheck_database(
                        connection,
                        target,
                        context,
                        state,
                        reference_marker_root=reference_marker_root,
                    )
                else:
                    stale = _matching_recovery_receipts(
                        identities, change_reference=change_reference, root=report_root
                    )
                    if stale:
                        if (
                            len(stale) == 1
                            and stale[0]["status"] == "STARTED"
                            and state.name == "EMPTY"
                        ):
                            _abort_receipt(
                                stale[0],
                                reason="PREVIOUS_TRANSACTION_NOT_COMMITTED",
                                root=report_root,
                            )
                        else:
                            raise EvaluationMockArtifactError(
                                "미완료 receipt가 있어 --recover-artifact가 필요합니다"
                            )
                    started = _start_receipt(
                        identities,
                        state,
                        change_reference=change_reference,
                        root=report_root,
                    )
                    if state.name == "EMPTY":
                        inserted_rows = _insert_action_rows(connection, context)
                    elif state.name != "ADOPTED":
                        raise EvaluationMockStateError("적용할 수 없는 DB 상태입니다")
                    result = postcheck_database(
                        connection,
                        target,
                        context,
                        state,
                        reference_marker_root=reference_marker_root,
                    )
            database_committed = True
            if after_database_commit is not None:
                after_database_commit()
            if committed is None:
                if started is None or result is None:
                    raise EvaluationMockArtifactError(
                        "receipt/postcheck가 준비되지 않았습니다"
                    )
                committed = _commit_receipt(
                    started, result, inserted_rows=inserted_rows, root=report_root
                )
            elif committed["status"] == "STARTED":
                if result is None:
                    raise EvaluationMockArtifactError("복구 postcheck가 없습니다")
                committed = _commit_receipt(
                    committed, result, inserted_rows=0, root=report_root
                )
            else:
                if result is None or identities is None:
                    raise EvaluationMockArtifactError("복구 검증 상태가 없습니다")
                _validate_committed_receipt_against_state(
                    committed, result, context, identities
                )
            if after_receipt_commit is not None:
                after_receipt_commit()
            if identities is None:
                raise EvaluationMockArtifactError(
                    "fixture identity가 준비되지 않았습니다"
                )
            marker = _build_marker(committed, context, identities)
            _save_artifact(completion_path, marker)
            return "RECOVERED" if recover_artifact else "APPLIED"
        except Exception:
            if started is not None and not database_committed:
                try:
                    _abort_receipt(
                        started, reason="TRANSACTION_ROLLED_BACK", root=report_root
                    )
                except EvaluationMockArtifactError:
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
    parser.add_argument("--database", choices=[TARGET_DATABASE])
    parser.add_argument("--confirm-target")
    parser.add_argument("--change-ref")
    parser.add_argument("--confirm", action="store_true")
    return parser


def _validate_cli(args: argparse.Namespace) -> None:
    if args.register_manifests:
        if args.database or args.confirm_target or args.change_ref:
            raise EvaluationMockError("manifest 등록에 DB 옵션을 사용할 수 없습니다")
        return
    if args.confirm:
        raise EvaluationMockError("--confirm은 --register-manifests 전용입니다")
    if args.database != TARGET_DATABASE:
        raise EvaluationMockError("DB 모드는 kosa_text2sql만 허용합니다")
    if args.preflight:
        if args.confirm_target or args.change_ref:
            raise EvaluationMockError("preflight에 mutation 옵션을 사용할 수 없습니다")
        return
    if args.confirm_target != TARGET_DATABASE:
        raise EvaluationMockError("mutation에는 정확한 --confirm-target이 필요합니다")
    if args.rehearse:
        if args.change_ref:
            raise EvaluationMockError("rehearse에는 change-ref를 쓰지 않습니다")
        return
    if not args.change_ref:
        raise EvaluationMockError("apply/recover에는 --change-ref가 필요합니다")


def main(argv: Sequence[str] | None = None) -> int:
    """V5-CM-1.5에서 폐기된 공개 실행 경로를 차단한다."""
    del argv
    import retired_pipelines

    return retired_pipelines.block("load_evaluation_mock")


if __name__ == "__main__":
    sys.exit(main())
