"""V4-CM-1.7 source/corrected/DB/Neo4j 통합 read-only verifier.

검증 명령은 PostgreSQL과 Neo4j를 변경하지 않는다. 저장소 artifact 쓰기는
``--register-corrected --confirm``과 ``--all``/명시적 ``--report``에만 있다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import apply_agent_runtime as agent_runtime
import apply_reference_extensions as reference_extensions
import bootstrap_base_schema as base_schema
import bootstrap_neo4j_graph as neo4j_bootstrap
import build_corrected_dataset as corrected_builder
import manifest_v3
from db_target import (
    ALLOWED_DATABASES,
    DATABASE_PROFILE,
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
    validate_url_components,
)
from dotenv import load_dotenv
from master_cypher import GraphManifestError, canonical_sha256
from neo4j.exceptions import DriverError, Neo4jError
from neo4j_target import Neo4jTargetError
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from value_normalization import (
    VALUE_NORMALIZATION_VERSION,
    column_type_registry,
    logical_type,
    normalize_db_row,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
CORRECTED_MARKER_PATH = BOOTSTRAP_ROOT / "markers" / "corrected.v1.json"
CORRECTED_LOCK_PATH = BOOTSTRAP_ROOT / "markers" / ".corrected.v1.lock"
REPORT_ROOT = BOOTSTRAP_ROOT / "reports"
NEO4J_DATABASE = "neo4j"

EXIT_OK = manifest_v3.EXIT_OK
EXIT_MISMATCH = manifest_v3.EXIT_MISMATCH
EXIT_USAGE = manifest_v3.EXIT_USAGE
EXIT_CONFIRM_REQUIRED = manifest_v3.EXIT_CONFIRM_REQUIRED
EXIT_METADATA = manifest_v3.EXIT_METADATA
EXIT_SCHEMA = manifest_v3.EXIT_SCHEMA
EXIT_NOT_REGISTERED = manifest_v3.EXIT_NOT_REGISTERED
EXIT_UNVERIFIABLE = 7

EXPECTED_STAGES = {
    "kosa_agent": "runtime_clean",
    "kosa_agent_e2e": "runtime_clean",
    "kosa_text2sql": "evaluation_mock",
}
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_REGISTERED = "NOT_REGISTERED"
STATUS_UNVERIFIABLE = "UNVERIFIABLE"
STATUS_BY_EXIT = {
    EXIT_OK: STATUS_PASS,
    EXIT_MISMATCH: STATUS_FAIL,
    EXIT_NOT_REGISTERED: STATUS_NOT_REGISTERED,
    EXIT_UNVERIFIABLE: STATUS_UNVERIFIABLE,
}

PK_COLUMNS: dict[str, tuple[str, ...]] = {
    "action_history": ("action_id",),
    "dim_parameter": ("parameter_id",),
    "evaluation": ("lot_hist_id", "parameter", "step_no"),
    "fdc_trace": ("lot_hist_id", "parameter_id", "seq_no"),
    "lot_history": ("lot_hist_id",),
    "metrology": ("metrology_id",),
    "summary_alarm_history": ("alarm_id",),
    "summary_data": ("lot_hist_id", "parameter", "step_no"),
    "trace_alarm_history": ("alarm_id",),
}
UNCHANGED_TABLES = frozenset(
    {
        "action_history",
        "evaluation",
        "lot_history",
        "metrology",
        "summary_data",
        "trace_alarm_history",
    }
)
CHANGED_TABLES = frozenset({"fdc_trace", "summary_alarm_history"})
NEW_TABLES = frozenset({"dim_parameter"})
MARKER_KEYS = {
    "artifact_type",
    "format_version",
    "status",
    "dataset_epoch",
    "correction_version",
    "source_archive_sha256",
    "source_manifest_sha256",
    "corrected_manifest_sha256",
    "receipt_sha256",
    "build_id",
    "generator_sha256",
    "table_count",
    "verification",
    "registered_at",
}
MARKER_VERIFICATION_KEYS = {
    "action_history_rows",
    "pk_duplicate_count",
    "fk_violation_count",
}
IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
READ_ONLY_PREFIXES = (
    "SELECT ",
    "SET TRANSACTION READ ONLY",
    "SET LOCAL ",
)


class UnverifiableError(manifest_v3.VerificationError):
    exit_code = EXIT_UNVERIFIABLE
    code = "UNVERIFIABLE"

    def __init__(self, message: str, *, reason_code: str = "UNVERIFIABLE") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class AcceptanceMismatchError(manifest_v3.ArtifactMismatchError):
    def __init__(self, message: str, *, details: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.details = dict(details)


@dataclass(frozen=True)
class CheckResult:
    target: str
    status: str
    exit_code: int
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "target": self.target,
            "status": self.status,
            "exit_code": self.exit_code,
            "details": self.details,
        }
        manifest_v3.scan_for_sensitive_values(payload)
        return payload


@dataclass(frozen=True)
class ActiveBundle:
    receipt: dict[str, Any]
    receipt_sha256: str
    report: dict[str, Any]
    tables: dict[str, corrected_builder.TableData]


def _read_json(path: Path, *, missing: type[Exception]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise missing("등록 artifact가 없습니다") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise manifest_v3.ManifestSchemaError(
            "등록 JSON을 안전하게 읽을 수 없습니다"
        ) from exc
    if not isinstance(payload, dict):
        raise manifest_v3.ManifestSchemaError("등록 JSON 최상위 값은 object여야 합니다")
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise manifest_v3.ManifestSchemaError(
            "artifact byte hash를 계산할 수 없습니다"
        ) from exc
    return digest.hexdigest()


def _archive_path(value: Path | None, environ: Mapping[str, str]) -> Path:
    if value is not None:
        return value.expanduser()
    package = environ.get("MENTOR_PACKAGE_DIR", "").strip()
    if not package:
        raise UnverifiableError(
            "등록 ZIP 위치가 설정되지 않았습니다",
            reason_code="MISSING_CONFIGURATION",
        )
    path = Path(package).expanduser()
    return path / "kosa_0813.zip" if path.is_dir() else path


def _load_active_bundle(
    *, output_root: Path = corrected_builder.DEFAULT_OUTPUT_ROOT
) -> ActiveBundle:
    context = corrected_builder.build_context(
        output_root=output_root,
        allowed_root=REPOSITORY_ROOT,
    )
    state = corrected_builder._load_active_state(context)
    if state is None:
        raise manifest_v3.NotRegisteredError("active corrected build가 없습니다")
    corrected_builder._validate_active_target(state, context)
    receipt, receipt_sha = corrected_builder._validate_build(state.build_path)
    try:
        report = json.loads(
            (state.build_path / "correction-report.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise manifest_v3.ManifestSchemaError(
            "correction report를 읽을 수 없습니다"
        ) from exc
    tables: dict[str, corrected_builder.TableData] = {}
    for table in sorted(receipt["tables"]):
        expected_columns = report["tables"][table]["columns_after"]
        try:
            raw = (state.build_path / "postgres" / f"{table}.csv").read_bytes()
        except OSError as exc:
            raise manifest_v3.ManifestSchemaError(
                "corrected CSV를 읽을 수 없습니다"
            ) from exc
        columns, rows = manifest_v3.parse_csv_bytes(
            raw,
            table=table,
            expected_columns=expected_columns,
        )
        tables[table] = corrected_builder.TableData(tuple(columns), tuple(rows))
    return ActiveBundle(receipt, receipt_sha, report, tables)


def _corrected_candidate(
    bundle: ActiveBundle, epoch: Mapping[str, Any]
) -> dict[str, Any]:
    expected_stages = corrected_builder._stage_identity(
        corrected_builder.CORRECTION_STAGES
    )
    expected_components = corrected_builder.build_generator_components(
        corrected_builder.CORRECTION_STAGES
    )
    if bundle.receipt.get("applied_stages") != expected_stages:
        raise manifest_v3.ManifestMetadataError(
            "active build correction stage identity가 현재 코드와 다릅니다"
        )
    if (
        bundle.receipt.get("generator_revision") != corrected_builder.GENERATOR_REVISION
        or bundle.receipt.get("generator_components") != expected_components
        or bundle.receipt.get("generator_sha256")
        != corrected_builder._generator_sha256(expected_components)
    ):
        raise manifest_v3.ManifestMetadataError(
            "active build generator identity가 현재 코드와 다릅니다"
        )
    candidate = {
        "format_version": manifest_v3.MANIFEST_FORMAT_VERSION,
        "artifact_type": "corrected_files",
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "source_archive_sha256": epoch["archive"]["sha256"],
        "correction_version": corrected_builder.CORRECTION_VERSION,
        "hash_algorithm": manifest_v3.HASH_ALGORITHM,
        "tables": corrected_builder.build_corrected_table_entries(bundle.tables),
    }
    manifest_v3.validate_manifest_schema(
        candidate,
        expected_artifact_type="corrected_files",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    return candidate


def _key_duplicate_count(
    rows: Sequence[Mapping[str, str]], columns: Sequence[str]
) -> int:
    values = [tuple(row[column] for column in columns) for row in rows]
    return len(values) - len(set(values))


def _fk_violation_count(tables: Mapping[str, corrected_builder.TableData]) -> int:
    lot_ids = {row["lot_hist_id"] for row in tables["lot_history"].rows}
    parameter_ids = {row["parameter_id"] for row in tables["dim_parameter"].rows}
    violations = 0
    for table in ("fdc_trace", "summary_data", "evaluation", "metrology"):
        violations += sum(
            row["lot_hist_id"] not in lot_ids for row in tables[table].rows
        )
    violations += sum(
        row["parameter_id"] not in parameter_ids for row in tables["fdc_trace"].rows
    )
    return violations


def _validate_file_roles(
    source_manifest: Mapping[str, Any], bundle: ActiveBundle
) -> None:
    classified = UNCHANGED_TABLES | CHANGED_TABLES | NEW_TABLES
    if (
        UNCHANGED_TABLES & CHANGED_TABLES
        or UNCHANGED_TABLES & NEW_TABLES
        or CHANGED_TABLES & NEW_TABLES
        or classified != set(bundle.tables)
    ):
        raise manifest_v3.ManifestSchemaError(
            "corrected table의 unchanged/changed/new 분류가 완전하지 않습니다"
        )
    source_tables = source_manifest.get("tables")
    if not isinstance(source_tables, Mapping):
        raise manifest_v3.ManifestSchemaError("source manifest tables가 잘못됐습니다")
    for table in UNCHANGED_TABLES:
        source_entry = source_tables.get(table)
        if not isinstance(source_entry, Mapping):
            raise manifest_v3.ArtifactMismatchError(
                "보정 비대상 table이 source manifest에 없습니다"
            )
        actual = {
            "columns": list(bundle.tables[table].columns),
            "row_count": len(bundle.tables[table].rows),
            "content_hash": manifest_v3.hash_canonical_rows(bundle.tables[table].rows),
        }
        expected = {
            key: source_entry[key] for key in ("columns", "row_count", "content_hash")
        }
        if actual != expected:
            raise manifest_v3.ArtifactMismatchError(
                "보정 비대상 table이 source와 달라졌습니다"
            )
    for table in CHANGED_TABLES:
        source_entry = source_tables.get(table)
        if not isinstance(source_entry, Mapping):
            raise manifest_v3.ArtifactMismatchError(
                "보정 대상 table이 source manifest에 없습니다"
            )
        corrected = bundle.tables[table]
        if list(corrected.columns) != source_entry.get("columns") or len(
            corrected.rows
        ) != source_entry.get("row_count"):
            raise manifest_v3.ArtifactMismatchError(
                "보정 대상 table의 column 또는 row 수가 source와 다릅니다"
            )
        if manifest_v3.hash_canonical_rows(corrected.rows) == source_entry.get(
            "content_hash"
        ):
            raise manifest_v3.ArtifactMismatchError(
                "보정 대상 table이 source와 동일합니다"
            )
    if any(table in source_tables for table in NEW_TABLES):
        raise manifest_v3.ArtifactMismatchError(
            "corrected 신규 table이 source manifest에 이미 있습니다"
        )


def _file_contract_details(
    source_manifest: Mapping[str, Any], bundle: ActiveBundle
) -> dict[str, Any]:
    _validate_file_roles(source_manifest, bundle)
    pk_duplicates = sum(
        _key_duplicate_count(bundle.tables[table].rows, PK_COLUMNS[table])
        for table in sorted(PK_COLUMNS)
    )
    fk_violations = _fk_violation_count(bundle.tables)
    if pk_duplicates or fk_violations:
        raise manifest_v3.ArtifactMismatchError("corrected PK/FK 검증에 실패했습니다")
    evaluation_counts = Counter(
        row["alarm_type"] for row in bundle.tables["evaluation"].rows
    )
    metrology_counts = Counter(
        row["alarm_result"] for row in bundle.tables["metrology"].rows
    )
    references = {
        "trace_alarm_rows": len(bundle.tables["trace_alarm_history"].rows),
        "summary_alarm_rows": len(bundle.tables["summary_alarm_history"].rows),
        "evaluation": {key: evaluation_counts[key] for key in ("IN", "OOC", "OOS")},
        "metrology": {key: metrology_counts[key] for key in ("PASS", "FAIL")},
        "dim_parameter_rows": len(bundle.tables["dim_parameter"].rows),
        "action_history_rows": len(bundle.tables["action_history"].rows),
    }
    expected_references = {
        "trace_alarm_rows": 126,
        "summary_alarm_rows": 47,
        "evaluation": {"IN": 4542, "OOC": 216, "OOS": 42},
        "metrology": {"PASS": 39, "FAIL": 9},
        "dim_parameter_rows": 8,
        "action_history_rows": 48,
    }
    if references != expected_references:
        raise manifest_v3.ArtifactMismatchError(
            "corrected reference output이 계약과 다릅니다"
        )
    return {
        "table_count": len(bundle.tables),
        "unchanged_table_count": len(UNCHANGED_TABLES),
        "changed_table_count": len(CHANGED_TABLES),
        "new_table_count": len(NEW_TABLES),
        "pk_duplicate_count": pk_duplicates,
        "fk_violation_count": fk_violations,
        "reference": references,
    }


def _validate_registered_marker(
    marker: Mapping[str, Any],
    *,
    manifest_payload: Mapping[str, Any],
    bundle: ActiveBundle,
    source_manifest_sha: str,
) -> None:
    if set(marker) != MARKER_KEYS or set(marker.get("verification", {})) != (
        MARKER_VERIFICATION_KEYS
    ):
        raise manifest_v3.ManifestSchemaError(
            "corrected registration marker key 집합이 잘못됐습니다"
        )
    expected = {
        "artifact_type": "corrected_registration",
        "format_version": 1,
        "status": "REGISTERED",
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "correction_version": corrected_builder.CORRECTION_VERSION,
        "source_archive_sha256": manifest_payload["source_archive_sha256"],
        "source_manifest_sha256": source_manifest_sha,
        "corrected_manifest_sha256": canonical_sha256(manifest_payload),
        "receipt_sha256": bundle.receipt_sha256,
        "build_id": bundle.receipt["build_id"],
        "generator_sha256": bundle.receipt["generator_sha256"],
        "table_count": len(bundle.tables),
        "verification": {
            "action_history_rows": 48,
            "pk_duplicate_count": 0,
            "fk_violation_count": 0,
        },
    }
    if any(marker.get(key) != value for key, value in expected.items()):
        raise manifest_v3.ManifestMetadataError(
            "corrected registration marker provenance가 다릅니다"
        )
    try:
        recorded = datetime.fromisoformat(str(marker["registered_at"]))
    except ValueError as exc:
        raise manifest_v3.ManifestSchemaError(
            "corrected marker 시각 형식이 잘못됐습니다"
        ) from exc
    if recorded.tzinfo is None or recorded.utcoffset() is None:
        raise manifest_v3.ManifestSchemaError(
            "corrected marker 시각은 timezone-aware여야 합니다"
        )
    manifest_v3.scan_for_sensitive_values(marker)


def _marker_candidate(
    manifest_payload: Mapping[str, Any],
    bundle: ActiveBundle,
    source_manifest_sha: str,
    *,
    registered_at: datetime | None = None,
) -> dict[str, Any]:
    now = registered_at or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise manifest_v3.ManifestSchemaError(
            "corrected marker 시각은 timezone-aware여야 합니다"
        )
    marker = {
        "artifact_type": "corrected_registration",
        "format_version": 1,
        "status": "REGISTERED",
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "correction_version": corrected_builder.CORRECTION_VERSION,
        "source_archive_sha256": manifest_payload["source_archive_sha256"],
        "source_manifest_sha256": source_manifest_sha,
        "corrected_manifest_sha256": canonical_sha256(manifest_payload),
        "receipt_sha256": bundle.receipt_sha256,
        "build_id": bundle.receipt["build_id"],
        "generator_sha256": bundle.receipt["generator_sha256"],
        "table_count": len(bundle.tables),
        "verification": {
            "action_history_rows": 48,
            "pk_duplicate_count": 0,
            "fk_violation_count": 0,
        },
        "registered_at": now.isoformat(),
    }
    _validate_registered_marker(
        marker,
        manifest_payload=manifest_payload,
        bundle=bundle,
        source_manifest_sha=source_manifest_sha,
    )
    return marker


def register_corrected(
    *,
    confirm: bool,
    manifest_path: Path = manifest_v3.CORRECTED_MANIFEST_PATH,
    marker_path: Path = CORRECTED_MARKER_PATH,
    output_root: Path = corrected_builder.DEFAULT_OUTPUT_ROOT,
    after_manifest_replace: Callable[[], None] | None = None,
) -> CheckResult:
    epoch = manifest_v3.load_dataset_epoch()
    source = _read_json(
        manifest_v3.SOURCE_MANIFEST_PATH, missing=manifest_v3.NotRegisteredError
    )
    manifest_v3.validate_manifest_schema(
        source,
        expected_artifact_type="source_files",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    source_sha = canonical_sha256(source)
    bundle = _load_active_bundle(output_root=output_root)
    candidate = _corrected_candidate(bundle, epoch)
    details = _file_contract_details(source, bundle)
    marker = _marker_candidate(candidate, bundle, source_sha)

    with corrected_builder._exclusive_lock(CORRECTED_LOCK_PATH):
        existing_manifest = (
            _read_json(manifest_path, missing=manifest_v3.NotRegisteredError)
            if manifest_path.exists()
            else None
        )
        existing_marker = (
            _read_json(marker_path, missing=manifest_v3.NotRegisteredError)
            if marker_path.exists()
            else None
        )
        if existing_marker is not None:
            marker_manifest_sha = existing_marker.get("corrected_manifest_sha256")
            if (
                marker_manifest_sha == canonical_sha256(candidate)
                and existing_manifest is not None
                and canonical_sha256(existing_manifest) != marker_manifest_sha
            ):
                raise manifest_v3.ManifestMetadataError(
                    "marker가 가리키는 corrected manifest 내용이 다릅니다"
                )
        if existing_marker is not None and existing_manifest == candidate:
            try:
                _validate_registered_marker(
                    existing_marker,
                    manifest_payload=candidate,
                    bundle=bundle,
                    source_manifest_sha=source_sha,
                )
            except manifest_v3.ManifestMetadataError:
                # Marker-last 중단 뒤 남은 이전 marker는 confirm으로 교체한다.
                pass
            else:
                return CheckResult(
                    "corrected-registration",
                    STATUS_PASS,
                    EXIT_OK,
                    {**details, "registration": "NO_OP"},
                )
        if not confirm:
            changed = []
            if existing_manifest is None:
                changed.append("corrected manifest 신규")
            else:
                changed.extend(
                    manifest_v3.compare_manifests(existing_manifest, candidate)
                )
            if existing_marker is None:
                changed.append("registration marker 신규 또는 복구")
            return CheckResult(
                "corrected-registration",
                STATUS_NOT_REGISTERED,
                EXIT_CONFIRM_REQUIRED,
                {"registration": "PREVIEW", "change_count": len(changed)},
            )
        manifest_v3.atomic_save_json(manifest_path, candidate)
        if after_manifest_replace is not None:
            after_manifest_replace()
        manifest_v3.atomic_save_json(marker_path, marker)
    return CheckResult(
        "corrected-registration",
        STATUS_PASS,
        EXIT_OK,
        {**details, "registration": "REGISTERED"},
    )


def verify_files(
    *,
    archive_path: Path,
    manifest_path: Path = manifest_v3.CORRECTED_MANIFEST_PATH,
    marker_path: Path = CORRECTED_MARKER_PATH,
    output_root: Path = corrected_builder.DEFAULT_OUTPUT_ROOT,
) -> CheckResult:
    epoch = manifest_v3.load_dataset_epoch()
    rebuilt_source = manifest_v3.build_source_manifest(archive_path)
    source = _read_json(
        manifest_v3.SOURCE_MANIFEST_PATH, missing=manifest_v3.NotRegisteredError
    )
    manifest_v3.validate_manifest_schema(
        source,
        expected_artifact_type="source_files",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    if source != rebuilt_source:
        raise manifest_v3.ArtifactMismatchError(
            "source ZIP이 등록 source manifest와 다릅니다"
        )
    registered = _read_json(manifest_path, missing=manifest_v3.NotRegisteredError)
    manifest_v3.validate_manifest_schema(
        registered,
        expected_artifact_type="corrected_files",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    bundle = _load_active_bundle(output_root=output_root)
    candidate = _corrected_candidate(bundle, epoch)
    if registered != candidate:
        raise manifest_v3.ArtifactMismatchError(
            "active corrected data가 등록 manifest와 다릅니다"
        )
    marker = _read_json(marker_path, missing=manifest_v3.NotRegisteredError)
    _validate_registered_marker(
        marker,
        manifest_payload=registered,
        bundle=bundle,
        source_manifest_sha=canonical_sha256(source),
    )
    details = _file_contract_details(source, bundle)
    return CheckResult("files", STATUS_PASS, EXIT_OK, details)


def _sql(connection: Any, statement: str, parameters: Any = None) -> Any:
    normalized = " ".join(statement.strip().split()).upper()
    if ";" in normalized or not normalized.startswith(READ_ONLY_PREFIXES):
        raise manifest_v3.VerificationError(
            "read-only verifier가 허용하지 않는 SQL을 거부했습니다"
        )
    if parameters is None:
        return connection.exec_driver_sql(statement)
    return connection.exec_driver_sql(statement, parameters)


def _rows(result: Any) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in result.mappings().all()]
    except (AttributeError, TypeError, ValueError) as exc:
        raise UnverifiableError(
            "DB query 응답 형식이 잘못됐습니다",
            reason_code="INVALID_RESPONSE",
        ) from exc


def _scalar(result: Any) -> Any:
    try:
        return result.scalar_one()
    except (AttributeError, LookupError, TypeError) as exc:
        raise UnverifiableError(
            "DB scalar 응답 형식이 잘못됐습니다",
            reason_code="INVALID_RESPONSE",
        ) from exc


def _expected_column_types(table: str) -> dict[str, str]:
    if table in base_schema.BASE_COLUMNS:
        return column_type_registry(base_schema.BASE_COLUMNS[table])
    if table in reference_extensions.EXPECTED_TABLE_COLUMNS:
        columns = reference_extensions.EXPECTED_TABLE_COLUMNS[table]
        return {name: logical_type(data_type) for name, data_type, _nullable in columns}
    if table in agent_runtime.EXPECTED_TABLE_COLUMNS:
        columns = agent_runtime.EXPECTED_TABLE_COLUMNS[table]
        return {column.name: logical_type(column.data_type) for column in columns}
    raise manifest_v3.ManifestSchemaError(f"{table}: logical type registry가 없습니다")


def _mismatch_details(
    *,
    target: BootstrapTarget,
    stage: str,
    inventory: str,
    table_count: int,
    action_history_rows: int | None,
    table: str | None = None,
    mismatch_kind: str | None = None,
    expected_row_count: int | None = None,
    actual_row_count: int | None = None,
    expected_policy: str | None = None,
    mismatches: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "profile": target.profile,
        "expected_stage": stage,
        "inventory": inventory,
        "table_count": table_count,
        "action_history_rows": action_history_rows,
    }
    optional = {
        "table": table,
        "mismatch_kind": mismatch_kind,
        "expected_row_count": expected_row_count,
        "actual_row_count": actual_row_count,
        "expected_policy": expected_policy,
    }
    details.update({key: value for key, value in optional.items() if value is not None})
    if mismatches is not None:
        details["mismatches"] = [dict(mismatch) for mismatch in mismatches]
    return details


def _table_mismatch(
    table: str,
    mismatch_kind: str,
    *,
    expected_row_count: int | None = None,
    actual_row_count: int | None = None,
    expected_policy: str | None = None,
) -> dict[str, Any]:
    mismatch: dict[str, Any] = {
        "table": table,
        "mismatch_kind": mismatch_kind,
    }
    optional = {
        "expected_row_count": expected_row_count,
        "actual_row_count": actual_row_count,
        "expected_policy": expected_policy,
    }
    mismatch.update(
        {key: value for key, value in optional.items() if value is not None}
    )
    return mismatch


def _engine_for(target: BootstrapTarget) -> Engine:
    url = target.create_url()
    validate_url_components(url, target)
    return create_engine(
        url,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def _validate_read_identity(connection: Any, target: BootstrapTarget) -> None:
    row = _rows(
        _sql(
            connection,
            """
            SELECT current_database() AS database_name,
                   EXISTS (
                       SELECT 1 FROM pg_namespace WHERE nspname = 'public'
                   ) AS public_exists,
                   has_schema_privilege(current_user, 'public', 'USAGE') AS can_use
            """,
        )
    )
    if len(row) != 1 or row[0].get("database_name") != target.database:
        raise UnverifiableError(
            "연결된 PostgreSQL database가 target과 다릅니다",
            reason_code="TARGET_IDENTITY_MISMATCH",
        )
    if row[0].get("public_exists") is not True or row[0].get("can_use") is not True:
        raise UnverifiableError(
            "public schema 읽기 권한이 없습니다",
            reason_code="NO_SCHEMA_USAGE",
        )


def _table_names(connection: Any) -> set[str]:
    return {
        str(row["table_name"])
        for row in _rows(
            _sql(
                connection,
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                """,
            )
        )
    }


def _inventory_state(
    actual_tables: set[str], expected_tables: set[str], row_counts: Mapping[str, int]
) -> str:
    if not actual_tables:
        return "NO_SCHEMA"
    if actual_tables != expected_tables:
        return "UNKNOWN"
    return (
        "BASE_SCHEMA"
        if all(value == 0 for value in row_counts.values())
        else "EARLY_DATA"
    )


def verify_database(
    database: str,
    stage: str,
    *,
    environ: Mapping[str, str] | None = None,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
) -> CheckResult:
    if database not in ALLOWED_DATABASES:
        raise manifest_v3.VerificationError("허용되지 않은 PostgreSQL database입니다")
    profile = DATABASE_PROFILE[database]
    manifest_path = manifest_v3.resolve_bootstrap_manifest_path(profile, stage)
    registered = _read_json(manifest_path, missing=manifest_v3.NotRegisteredError)
    epoch = manifest_v3.load_dataset_epoch()
    manifest_v3.validate_manifest_schema(
        registered,
        expected_artifact_type="db_bootstrap",
        expected_profile=profile,
        expected_stage=stage,
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    if registered["value_normalization_version"] != VALUE_NORMALIZATION_VERSION:
        raise manifest_v3.ManifestMetadataError(
            "DB manifest value normalization version이 다릅니다"
        )
    target = load_bootstrap_target(database, environ=environ)
    expected_tables = set(registered["tables"])
    if any(not IDENTIFIER_PATTERN.fullmatch(table) for table in expected_tables):
        raise manifest_v3.ManifestSchemaError("DB manifest table 식별자가 잘못됐습니다")
    if any(
        not IDENTIFIER_PATTERN.fullmatch(column)
        for entry in registered["tables"].values()
        for column in entry["columns"]
    ):
        raise manifest_v3.ManifestSchemaError(
            "DB manifest column 식별자가 잘못됐습니다"
        )
    completion_marker: dict[str, Any] | None = None
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _sql(connection, "SET TRANSACTION READ ONLY")
            _sql(connection, "SET LOCAL search_path = public")
            _sql(connection, "SET LOCAL statement_timeout = '30s'")
            _validate_read_identity(connection, target)
            actual_tables = _table_names(connection)
            relevant = actual_tables & expected_tables
            row_counts = {
                table: int(_scalar(_sql(connection, f'SELECT count(*) FROM "{table}"')))
                for table in sorted(relevant)
                if IDENTIFIER_PATTERN.fullmatch(table)
            }
            inventory = _inventory_state(actual_tables, expected_tables, row_counts)
            if actual_tables != expected_tables:
                raise AcceptanceMismatchError(
                    "DB object 집합이 stage 계약과 다릅니다",
                    details=_mismatch_details(
                        target=target,
                        stage=stage,
                        inventory=inventory,
                        table_count=len(actual_tables),
                        action_history_rows=row_counts.get("action_history"),
                        mismatch_kind="TABLE_INVENTORY",
                    ),
                )
            mismatches: list[dict[str, Any]] = []
            for table in sorted(expected_tables):
                privilege = _scalar(
                    _sql(
                        connection,
                        "SELECT has_table_privilege(current_user, %s, 'SELECT')",
                        (f"public.{table}",),
                    )
                )
                if privilege is not True:
                    raise UnverifiableError(
                        "DB table SELECT 권한이 없습니다",
                        reason_code="NO_SELECT_GRANT",
                    )
                column_rows = _rows(
                    _sql(
                        connection,
                        """
                        SELECT a.attname AS column_name,
                               format_type(a.atttypid, a.atttypmod) AS data_type
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        JOIN pg_attribute a ON a.attrelid = c.oid
                        WHERE n.nspname = 'public' AND c.relname = %s
                          AND c.relkind IN ('r','p')
                          AND a.attnum > 0 AND NOT a.attisdropped
                        ORDER BY a.attnum
                        """,
                        (table,),
                    )
                )
                columns = [str(row["column_name"]) for row in column_rows]
                expected = registered["tables"][table]
                if any(not IDENTIFIER_PATTERN.fullmatch(column) for column in columns):
                    raise manifest_v3.ManifestSchemaError(
                        "DB manifest column 식별자가 잘못됐습니다"
                    )
                if columns != expected["columns"]:
                    mismatches.append(
                        _table_mismatch(
                            table,
                            "COLUMN_ORDER",
                            expected_policy=expected["verification_policy"],
                        )
                    )
                    continue
                actual_types = {
                    str(row["column_name"]): logical_type(str(row["data_type"]))
                    for row in column_rows
                }
                expected_types = _expected_column_types(table)
                if actual_types != expected_types:
                    mismatches.append(
                        _table_mismatch(
                            table,
                            "COLUMN_TYPE",
                            expected_policy=expected["verification_policy"],
                        )
                    )
                    continue
                if expected["verification_policy"] == "schema_only":
                    continue
                selected = ", ".join(f'"{column}"' for column in columns)
                values = [
                    normalize_db_row(row, expected_types)
                    for row in _rows(
                        _sql(connection, f'SELECT {selected} FROM "{table}"')
                    )
                ]
                if len(values) != expected["row_count"]:
                    mismatches.append(
                        _table_mismatch(
                            table,
                            "ROW_COUNT",
                            expected_row_count=expected["row_count"],
                            actual_row_count=len(values),
                            expected_policy=expected["verification_policy"],
                        )
                    )
                elif (
                    manifest_v3.hash_canonical_rows(values) != expected["content_hash"]
                ):
                    mismatches.append(
                        _table_mismatch(
                            table,
                            "CONTENT_HASH",
                            expected_row_count=expected["row_count"],
                            actual_row_count=len(values),
                            expected_policy=expected["verification_policy"],
                        )
                    )
            if stage != "base_schema":
                try:
                    reference_extensions.postcheck_database(
                        connection,
                        action_rows_before=row_counts.get("action_history", 0),
                    )
                except reference_extensions.ReferenceExtensionError:
                    mismatches.append({"mismatch_kind": "REFERENCE_SIGNATURE_OR_VIEW"})
            if stage == "evaluation_mock":
                # Lazy import avoids a module cycle: the mutation runner reuses this
                # verifier's registered corrected-bundle primitives.
                import load_evaluation_mock as evaluation_mock

                try:
                    completion_marker = evaluation_mock.verify_completion_marker(
                        connection,
                        target,
                        registered,
                    )
                except (
                    evaluation_mock.EvaluationMockArtifactError,
                    evaluation_mock.EvaluationMockStateError,
                ):
                    # Completion evidence is an acceptance mismatch, not a manifest
                    # parser crash.  Keep the reason normalized so reports never
                    # expose filesystem paths or underlying exception messages.
                    mismatches.append({"mismatch_kind": "FIXTURE_MARKER"})
            if stage == "runtime_clean":
                try:
                    runtime_sql, _ = agent_runtime.load_and_validate_sql()
                    migration_sha = agent_runtime.migration_sha256(runtime_sql)
                    runtime_result = agent_runtime.postcheck_database(
                        connection,
                        alarm_rows_before=agent_runtime.alarm_event_count(connection),
                    )
                    runtime_marker = agent_runtime.load_marker(
                        target,
                        migration_sha=migration_sha,
                    )
                    if (
                        runtime_marker is None
                        or runtime_marker["schema_signature_sha256"]
                        != runtime_result.schema_signature_sha256
                    ):
                        raise agent_runtime.AgentRuntimeArtifactError(
                            "runtime marker가 schema와 다릅니다"
                        )
                except agent_runtime.AgentRuntimeError:
                    mismatches.append({"mismatch_kind": "RUNTIME_MARKER"})
            if mismatches:
                raise AcceptanceMismatchError(
                    "DB acceptance 계약이 다릅니다",
                    details=_mismatch_details(
                        target=target,
                        stage=stage,
                        inventory=inventory,
                        table_count=len(actual_tables),
                        action_history_rows=row_counts.get("action_history"),
                        mismatches=mismatches,
                    ),
                )
    except SQLAlchemyError as exc:
        raise UnverifiableError(
            "PostgreSQL read-only 검증을 수행할 수 없습니다",
            reason_code="CONNECT_OR_QUERY_FAILED",
        ) from exc
    finally:
        engine.dispose()
    details = {
        "profile": target.profile,
        "expected_stage": stage,
        "inventory": inventory,
        "table_count": len(expected_tables),
        "action_history_rows": row_counts.get("action_history", 0),
    }
    if completion_marker is not None:
        details.update(
            {
                "fixture_type": completion_marker["fixture_type"],
                "fixture_marker_status": completion_marker["status"],
            }
        )
    return CheckResult(
        database,
        STATUS_PASS,
        EXIT_OK,
        details,
    )


def verify_neo4j(
    *,
    archive_path: Path,
    environ: Mapping[str, str] | None = None,
    marker_root: Path = neo4j_bootstrap.MARKER_ROOT,
    state_reader: Callable[..., Any] = neo4j_bootstrap.read_current_state,
) -> CheckResult:
    try:
        context = neo4j_bootstrap.load_context(
            archive_path,
            NEO4J_DATABASE,
            environ=environ,
        )
    except GraphManifestError as exc:
        raise manifest_v3.ManifestMetadataError(
            "Neo4j graph artifact metadata가 잘못됐습니다"
        ) from exc
    try:
        marker = neo4j_bootstrap.load_marker(NEO4J_DATABASE, root=marker_root)
    except neo4j_bootstrap.MarkerError as exc:
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j marker가 유효하지 않습니다"
        ) from exc
    if marker is None:
        raise UnverifiableError(
            "Neo4j success marker가 없습니다",
            reason_code="MISSING_SUCCESS_MARKER",
        )
    try:
        neo4j_bootstrap.validate_marker_for_context(marker, context)
    except neo4j_bootstrap.MarkerError as exc:
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j marker가 현재 target/artifact와 다릅니다"
        ) from exc
    if not neo4j_bootstrap.marker_is_readiness_success(marker):
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j marker가 readiness success 상태가 아닙니다"
        )
    try:
        snapshot, schema_fingerprint = state_reader(
            context.target,
            require_supported_schema=True,
        )
    except neo4j_bootstrap.GraphStateError as exc:
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j live graph 구조가 계약과 다릅니다"
        ) from exc
    except Neo4jTargetError as exc:
        raise UnverifiableError(
            "Neo4j read-only 검증을 수행할 수 없습니다",
            reason_code="TARGET_VALIDATION_FAILED",
        ) from exc
    except (DriverError, Neo4jError) as exc:
        raise UnverifiableError(
            "Neo4j read-only 검증을 수행할 수 없습니다",
            reason_code="CONNECT_OR_QUERY_FAILED",
        ) from exc
    graph_fingerprint = neo4j_bootstrap.snapshot_fingerprint(snapshot)
    label_counts = Counter(node.label for node in snapshot.nodes)
    type_counts = Counter(item.relation_type for item in snapshot.relationships)
    relation_id_count = sum(
        item.relation_id is not None for item in snapshot.relationships
    )
    manifest = context.manifest
    expected = {
        "node_count": manifest["node_count"],
        "relationship_count": manifest["relationship_count"],
        "relation_id_count": manifest["relationship_count"],
        "relation_id_duplicates": 0,
        "label_distribution": manifest["label_distribution"],
        "relationship_type_distribution": manifest["relationship_type_distribution"],
        "graph_fingerprint": manifest["expected_graph_fingerprint_sha256"],
        "schema_fingerprint": marker.get("schema_fingerprint_sha256"),
    }
    actual = {
        "node_count": snapshot.node_count,
        "relationship_count": snapshot.relationship_count,
        "relation_id_count": relation_id_count,
        "relation_id_duplicates": snapshot.relation_id_duplicates,
        "label_distribution": dict(sorted(label_counts.items())),
        "relationship_type_distribution": dict(sorted(type_counts.items())),
        "graph_fingerprint": graph_fingerprint,
        "schema_fingerprint": schema_fingerprint,
    }
    forbidden = {"Sensor", "LOCATED_IN", "UPSTREAM_OF", "USED_IN"}
    if actual != expected or forbidden & (set(label_counts) | set(type_counts)):
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j live graph가 등록 manifest/marker와 다릅니다"
        )
    if marker["actual_graph_fingerprint_sha256"] != graph_fingerprint:
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j live graph fingerprint가 marker와 다릅니다"
        )
    return CheckResult(
        "neo4j",
        STATUS_PASS,
        EXIT_OK,
        {
            "database": NEO4J_DATABASE,
            "marker_status": marker["status"],
            "node_count": snapshot.node_count,
            "relationship_count": snapshot.relationship_count,
            "relation_id_count": relation_id_count,
            "relation_id_duplicates": snapshot.relation_id_duplicates,
        },
    )


def _failure_result(target: str, exc: Exception) -> CheckResult:
    exit_code = getattr(exc, "exit_code", EXIT_USAGE)
    if exit_code == EXIT_NOT_REGISTERED:
        status = STATUS_NOT_REGISTERED
    elif exit_code == EXIT_UNVERIFIABLE:
        status = STATUS_UNVERIFIABLE
    else:
        status = STATUS_FAIL
    details = {
        "reason_code": getattr(
            exc,
            "reason_code",
            getattr(exc, "code", type(exc).__name__),
        )
    }
    provided = getattr(exc, "details", None)
    if isinstance(provided, Mapping):
        details.update(provided)
    return CheckResult(
        target,
        status,
        exit_code,
        details,
    )


def aggregate_exit_code(results: Sequence[CheckResult]) -> int:
    codes = {result.exit_code for result in results}
    if EXIT_MISMATCH in codes:
        return EXIT_MISMATCH
    if any(
        code not in {EXIT_OK, EXIT_NOT_REGISTERED, EXIT_UNVERIFIABLE} for code in codes
    ):
        return EXIT_MISMATCH
    if EXIT_NOT_REGISTERED in codes:
        return EXIT_NOT_REGISTERED
    if EXIT_UNVERIFIABLE in codes:
        return EXIT_UNVERIFIABLE
    return EXIT_OK


def _report_payload(results: Sequence[CheckResult]) -> dict[str, Any]:
    exit_code = aggregate_exit_code(results)
    payload = {
        "artifact_type": "bootstrap_verification_report",
        "format_version": 1,
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "overall_status": STATUS_BY_EXIT.get(exit_code, STATUS_FAIL),
        "exit_code": exit_code,
        "targets": [result.as_dict() for result in results],
        "verified_at": datetime.now(UTC).isoformat(),
    }
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def _save_report(path: Path, results: Sequence[CheckResult]) -> dict[str, Any]:
    payload = _report_payload(results)
    manifest_v3.atomic_save_json(path, payload)
    return payload


def _target_unverifiable(exc: Exception) -> UnverifiableError:
    message = str(exc)
    reason_code = (
        "MISSING_CONFIGURATION"
        if "설정이 비어" in message or "설정되지 않았" in message
        else "TARGET_VALIDATION_FAILED"
    )
    return UnverifiableError(
        "검증 target을 안전하게 확인할 수 없습니다",
        reason_code=reason_code,
    )


def run_all(
    *,
    archive_path: Path,
    environ: Mapping[str, str],
) -> tuple[list[CheckResult], int]:
    checks: list[tuple[str, Callable[[], CheckResult]]] = [
        ("files", lambda: verify_files(archive_path=archive_path)),
        *[
            (
                database,
                lambda database=database: verify_database(
                    database,
                    EXPECTED_STAGES[database],
                    environ=environ,
                ),
            )
            for database in EXPECTED_STAGES
        ],
        (
            "neo4j",
            lambda: verify_neo4j(archive_path=archive_path, environ=environ),
        ),
    ]
    results = []
    for target, check in checks:
        try:
            results.append(check())
        except manifest_v3.VerificationError as exc:
            if exc.exit_code not in {
                EXIT_MISMATCH,
                EXIT_NOT_REGISTERED,
                EXIT_UNVERIFIABLE,
            }:
                raise
            results.append(_failure_result(target, exc))
        except (
            TargetValidationError,
            Neo4jTargetError,
            neo4j_bootstrap.Neo4jBootstrapError,
        ) as exc:
            results.append(_failure_result(target, _target_unverifiable(exc)))
        except SQLAlchemyError:
            results.append(
                _failure_result(
                    target,
                    UnverifiableError(
                        "target read-only 검증을 수행할 수 없습니다",
                        reason_code="CONNECT_OR_QUERY_FAILED",
                    ),
                )
            )
        except agent_runtime.AgentRuntimeError as exc:
            results.append(_failure_result(target, exc))
    return results, aggregate_exit_code(results)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--register-corrected", action="store_true")
    modes.add_argument("--files-only", action="store_true")
    modes.add_argument("--database", choices=sorted(ALLOWED_DATABASES))
    modes.add_argument("--neo4j", action="store_true")
    modes.add_argument("--all", action="store_true")
    parser.add_argument("--stage")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def _validate_cli(args: argparse.Namespace) -> None:
    if args.database and not args.stage:
        raise manifest_v3.VerificationError("--database에는 --stage가 필요합니다")
    if args.stage and not args.database:
        raise manifest_v3.VerificationError("--stage는 --database와 함께 사용합니다")
    if args.confirm and not args.register_corrected:
        raise manifest_v3.VerificationError(
            "--confirm은 corrected 등록에서만 사용합니다"
        )
    if args.register_corrected and args.report:
        raise manifest_v3.VerificationError(
            "corrected 등록과 report 저장을 함께 수행하지 않습니다"
        )


def _print_result(result: CheckResult) -> None:
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    args = _parser().parse_args(argv)
    try:
        _validate_cli(args)
        environ = dict(os.environ)
        if args.register_corrected:
            result = register_corrected(confirm=args.confirm)
            _print_result(result)
            return result.exit_code
        if args.files_only:
            archive = _archive_path(args.archive, environ)
            result = verify_files(archive_path=archive)
            results = [result]
        elif args.database:
            result = verify_database(
                args.database,
                args.stage,
                environ=environ,
            )
            results = [result]
        elif args.neo4j:
            archive = _archive_path(args.archive, environ)
            result = verify_neo4j(archive_path=archive, environ=environ)
            results = [result]
        else:
            archive = _archive_path(args.archive, environ)
            results, exit_code = run_all(archive_path=archive, environ=environ)
            path = args.report or REPORT_ROOT / (
                datetime.now(UTC).strftime("bootstrap-%Y%m%dT%H%M%SZ.json")
            )
            report_payload = _save_report(path, results)
            print(json.dumps(report_payload, ensure_ascii=False, sort_keys=True))
            return exit_code
        if args.report:
            _save_report(args.report, results)
        _print_result(results[0])
        return results[0].exit_code
    except manifest_v3.VerificationError as exc:
        _print_result(_failure_result("command", exc))
        return exc.exit_code
    except (
        TargetValidationError,
        Neo4jTargetError,
        neo4j_bootstrap.Neo4jBootstrapError,
    ) as exc:
        error = _target_unverifiable(exc)
        _print_result(_failure_result("command", error))
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main())
