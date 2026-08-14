"""V4-CM-1.2 corrected copy 빌드 파이프라인.

원본 ZIP과 멘토 Generator는 읽기 전용으로 유지한다. 실제 보정 규칙은 후속 Task가
``CORRECTION_STAGES``에 등록하며, 이 모듈은 입력 guard·결정론적 출력·immutable build와
active pointer 교체만 담당한다.
"""

from __future__ import annotations

import argparse
import csv
import errno
import fcntl
import hashlib
import inspect
import json
import os
import re
import shutil
import sys
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TextIO

import manifest_v3 as manifest_v3_module
from manifest_v3 import (
    DATASET_EPOCH,
    DATASET_EPOCH_PATH,
    EXIT_CONFIRM_REQUIRED,
    EXIT_MISMATCH,
    EXIT_OK,
    HASH_ALGORITHM,
    HEX_SHA256_PATTERN,
    REPOSITORY_ROOT,
    SOURCE_EXPECTED_COLUMNS,
    SOURCE_MANIFEST_PATH,
    SOURCE_TABLE_FILES,
    ArtifactMismatchError,
    ManifestMetadataError,
    ManifestSchemaError,
    NotRegisteredError,
    VerificationError,
    atomic_save_json,
    hash_canonical_rows,
    load_dataset_epoch,
    parse_csv_bytes,
    scan_for_sensitive_values,
    validate_manifest_schema,
    verify_archive_inventory,
)

CORRECTION_VERSION = "v1"
GENERATOR_REVISION = "corrected-builder-v1"
RECEIPT_FORMAT_VERSION = 1
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "data" / "corrected"
LOCK_FILENAME = ".active.lock"

ACTIVE_KEYS = {
    "format_version",
    "correction_version",
    "build_id",
    "receipt_sha256",
}
RECEIPT_KEYS = {
    "format_version",
    "artifact_type",
    "dataset_epoch",
    "source_archive_sha256",
    "correction_version",
    "generator_revision",
    "generator_sha256",
    "generator_components",
    "registration_status",
    "hash_algorithm",
    "build_id",
    "applied_stages",
    "tables",
}
RECEIPT_TABLE_KEYS = {
    "file_id",
    "row_count",
    "content_hash",
    "file_sha256",
}
REPORT_KEYS = {
    "format_version",
    "artifact_type",
    "dataset_epoch",
    "correction_version",
    "build_id",
    "tables",
}
REPORT_TABLE_KEYS = {
    "row_count_before",
    "row_count_after",
    "rows_added",
    "rows_removed",
    "cells_changed",
    "columns_before",
    "columns_after",
    "stage_ids",
}
STAGE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")


@dataclass(frozen=True)
class TableData:
    """순서가 있는 column과 문자열 row를 보존하는 table 값."""

    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


Dataset = dict[str, TableData]
TablePatch = dict[str, TableData]


@dataclass(frozen=True)
class StageSpec:
    """후속 correction stage의 읽기·쓰기 범위 계약."""

    stage_id: str
    version: str
    reads: frozenset[str]
    writes: frozenset[str]
    transform: Callable[[Dataset], TablePatch]


CORRECTION_STAGES: tuple[StageSpec, ...] = ()


@dataclass(frozen=True)
class BuildContext:
    output_root: Path
    allowed_root: Path
    version_root: Path
    staging_root: Path
    builds_root: Path
    active_path: Path
    lock_path: Path


@dataclass(frozen=True)
class ActiveState:
    payload: dict[str, Any]
    fingerprint: str
    build_path: Path


@dataclass(frozen=True)
class BuildIdentity:
    epoch: dict[str, Any]
    source: Dataset
    corrected: Dataset
    touched: dict[str, list[str]]
    components: list[dict[str, str]]
    generator_sha256: str
    build_id: str


class ActivePointerError(NotRegisteredError):
    """active pointer나 그 대상 build가 안전하지 않을 때의 종료 코드 6 오류."""


def _require_exact_keys(
    payload: Mapping[str, Any], expected: set[str], *, context: str
) -> None:
    actual = set(payload)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    detail = []
    if missing:
        detail.append(f"누락={missing}")
    if extra:
        detail.append(f"추가={extra}")
    raise ManifestSchemaError(f"{context} key 집합 오류: {'; '.join(detail)}")


def _require_nonnegative_int(value: Any, *, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ManifestSchemaError(f"{context}는 0 이상 정수여야 합니다")
    return value


def _require_nonempty_string(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestSchemaError(f"{context}는 비어 있지 않은 문자열이어야 합니다")
    return value


def _read_json_object(path: Path, *, context: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NotRegisteredError(f"{context} 파일이 등록되지 않았습니다") from exc
    except json.JSONDecodeError as exc:
        raise ManifestSchemaError(f"{context} JSON 형식이 잘못됐습니다") from exc
    except OSError as exc:
        raise ManifestSchemaError(
            f"{context} 파일을 안전하게 읽을 수 없습니다"
        ) from exc
    if not isinstance(payload, dict):
        raise ManifestSchemaError(f"{context} 최상위 값은 object여야 합니다")
    return payload


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ManifestSchemaError("파일 hash를 안전하게 계산할 수 없습니다") from exc
    return digest.hexdigest()


def _canonical_json_hash(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(serialized)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _existing_symlink_between(path: Path, root: Path) -> bool:
    """root 아래 현재 존재하는 component 중 symlink가 있는지 확인한다."""
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError:
        return True
    current = root.absolute()
    if current.exists() and current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def build_context(*, output_root: Path, allowed_root: Path) -> BuildContext:
    """호출자가 정한 trusted root 아래로 출력 범위를 제한한다."""
    resolved_allowed = allowed_root.resolve(strict=False)
    resolved_output = output_root.resolve(strict=False)
    if not _is_within(resolved_output, resolved_allowed):
        raise VerificationError("output_root가 allowed_root 밖입니다")
    if _existing_symlink_between(output_root, allowed_root):
        raise VerificationError("output 경로에 symlink를 사용할 수 없습니다")
    version_root = resolved_output / CORRECTION_VERSION
    return BuildContext(
        output_root=resolved_output,
        allowed_root=resolved_allowed,
        version_root=version_root,
        staging_root=version_root / ".staging",
        builds_root=version_root / "builds",
        active_path=version_root / "active.json",
        lock_path=version_root / LOCK_FILENAME,
    )


def _validate_archive_location(archive_path: Path, context: BuildContext) -> None:
    resolved_archive = archive_path.resolve(strict=False)
    if _is_within(resolved_archive, context.output_root):
        raise VerificationError("등록 ZIP을 corrected output 아래에 둘 수 없습니다")


def _validate_table_data(table: str, data: TableData) -> None:
    if not isinstance(data, TableData):
        raise ManifestSchemaError(f"{table}: TableData 형식이 아닙니다")
    if not data.columns or len(data.columns) != len(set(data.columns)):
        raise ManifestSchemaError(f"{table}: column 계약이 비었거나 중복됐습니다")
    if any(not isinstance(column, str) or not column for column in data.columns):
        raise ManifestSchemaError(
            f"{table}: column은 비어 있지 않은 문자열이어야 합니다"
        )
    expected_keys = set(data.columns)
    for row in data.rows:
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise ManifestSchemaError(f"{table}: row column 집합이 다릅니다")
        for value in row.values():
            if not isinstance(value, str):
                raise ManifestSchemaError(f"{table}: cell은 문자열이어야 합니다")
            if unicodedata.normalize("NFC", value) != value:
                raise ManifestSchemaError(f"{table}: cell이 NFC가 아닙니다")


def _copy_table(data: TableData) -> TableData:
    return TableData(data.columns, tuple(dict(row) for row in data.rows))


def _copy_dataset(dataset: Mapping[str, TableData]) -> Dataset:
    return {table: _copy_table(data) for table, data in dataset.items()}


def _table_fingerprint(data: TableData) -> str:
    return _canonical_json_hash(
        {
            "columns": list(data.columns),
            "content_hash": hash_canonical_rows(data.rows),
        }
    )


def _validate_stage_registry(stages: Sequence[StageSpec]) -> None:
    seen: set[str] = set()
    for stage in stages:
        if not STAGE_ID_PATTERN.fullmatch(stage.stage_id):
            raise ManifestSchemaError("stage_id 형식이 잘못됐습니다")
        _require_nonempty_string(stage.version, context=f"{stage.stage_id}.version")
        if stage.stage_id in seen:
            raise ManifestSchemaError("stage_id가 중복됐습니다")
        seen.add(stage.stage_id)
        if not stage.writes:
            raise ManifestSchemaError(f"{stage.stage_id}: writes가 비어 있습니다")
        if any(not isinstance(table, str) or not table for table in stage.reads):
            raise ManifestSchemaError(f"{stage.stage_id}: reads가 잘못됐습니다")
        if any(not isinstance(table, str) or not table for table in stage.writes):
            raise ManifestSchemaError(f"{stage.stage_id}: writes가 잘못됐습니다")


def run_stages(
    source: Mapping[str, TableData], stages: Sequence[StageSpec]
) -> tuple[Dataset, dict[str, list[str]]]:
    """선언된 순서대로 restricted copy를 넘기고 patch만 병합한다."""
    _validate_stage_registry(stages)
    dataset = _copy_dataset(source)
    touched: dict[str, list[str]] = {table: [] for table in dataset}
    for table, data in dataset.items():
        _validate_table_data(table, data)

    for stage in stages:
        missing_reads = set(stage.reads) - set(dataset)
        if missing_reads:
            raise ManifestSchemaError(
                f"{stage.stage_id}: 읽을 table이 없습니다: {sorted(missing_reads)}"
            )
        before = {table: _table_fingerprint(data) for table, data in dataset.items()}
        allowed = set(stage.reads | stage.writes) & set(dataset)
        restricted = {table: _copy_table(dataset[table]) for table in allowed}
        restricted_before = {
            table: _table_fingerprint(data) for table, data in restricted.items()
        }
        patch = stage.transform(restricted)
        if not isinstance(patch, dict):
            raise ManifestSchemaError(f"{stage.stage_id}: TablePatch가 아닙니다")
        if set(restricted) != set(restricted_before):
            raise ManifestSchemaError(
                f"{stage.stage_id}: 전달된 dataset mapping을 직접 변경했습니다"
            )
        mutated_without_patch = {
            table
            for table, fingerprint in restricted_before.items()
            if _table_fingerprint(restricted[table]) != fingerprint
            and table not in patch
        }
        if mutated_without_patch:
            raise ManifestSchemaError(
                f"{stage.stage_id}: in-place 변경 table을 patch로 반환하지 않았습니다: "
                f"{sorted(mutated_without_patch)}"
            )
        undeclared = set(patch) - set(stage.writes)
        if undeclared:
            raise ManifestSchemaError(
                f"{stage.stage_id}: writes 밖 table을 반환했습니다: "
                f"{sorted(undeclared)}"
            )
        for table, data in patch.items():
            _validate_table_data(table, data)
            dataset[table] = _copy_table(data)
            touched.setdefault(table, []).append(stage.stage_id)

        for table, fingerprint in before.items():
            if table not in dataset:
                raise ManifestSchemaError(
                    f"{stage.stage_id}: table 삭제는 허용하지 않습니다"
                )
            if (
                table not in stage.writes
                and _table_fingerprint(dataset[table]) != fingerprint
            ):
                raise ManifestSchemaError(
                    f"{stage.stage_id}: 선언하지 않은 table을 변경했습니다: {table}"
                )
    return dataset, touched


def _load_source_dataset(
    archive_path: Path,
    *,
    epoch_path: Path,
    source_manifest_path: Path,
) -> tuple[dict[str, Any], Dataset]:
    epoch = load_dataset_epoch(epoch_path)
    source_manifest = _read_json_object(source_manifest_path, context="source manifest")
    validate_manifest_schema(
        source_manifest,
        expected_artifact_type="source_files",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    contents = verify_archive_inventory(archive_path, epoch)
    dataset: Dataset = {}
    for table, file_id in SOURCE_TABLE_FILES.items():
        if file_id not in contents:
            raise ArtifactMismatchError(f"source CSV가 inventory에 없습니다: {table}")
        columns, rows = parse_csv_bytes(
            contents[file_id],
            table=table,
            expected_columns=SOURCE_EXPECTED_COLUMNS[table],
        )
        entry = {
            "file_id": file_id,
            "columns": columns,
            "row_count": len(rows),
            "content_hash": hash_canonical_rows(rows),
        }
        if entry != source_manifest["tables"][table]:
            raise ArtifactMismatchError(f"source manifest table이 다릅니다: {table}")
        dataset[table] = TableData(tuple(columns), tuple(rows))
    return epoch, dataset


def _component_entry(path: Path, *, repository_root: Path) -> dict[str, str]:
    resolved = path.resolve(strict=True)
    resolved_root = repository_root.resolve(strict=True)
    try:
        logical_id = resolved.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise ManifestSchemaError("generator component가 저장소 밖입니다") from exc
    return {"logical_id": logical_id, "sha256": _sha256_file(resolved)}


def build_generator_components(
    stages: Sequence[StageSpec],
    *,
    repository_root: Path = REPOSITORY_ROOT,
    builder_path: Path | None = None,
    manifest_path: Path | None = None,
) -> list[dict[str, str]]:
    paths = {
        (builder_path or Path(__file__)).resolve(),
        (manifest_path or Path(manifest_v3_module.__file__)).resolve(),
    }
    for stage in stages:
        source_path = inspect.getsourcefile(stage.transform)
        if source_path is None:
            raise ManifestSchemaError(
                f"{stage.stage_id}: stage source를 찾을 수 없습니다"
            )
        paths.add(Path(source_path).resolve())
    components = [
        _component_entry(path, repository_root=repository_root) for path in paths
    ]
    return sorted(components, key=lambda item: item["logical_id"])


def _generator_sha256(components: Sequence[Mapping[str, str]]) -> str:
    return _canonical_json_hash(list(components))


def _stage_identity(stages: Sequence[StageSpec]) -> list[dict[str, str]]:
    return [
        {"stage_id": stage.stage_id, "stage_version": stage.version} for stage in stages
    ]


def compute_build_id(
    *,
    source_archive_sha256: str,
    generator_revision: str,
    generator_sha256: str,
    stages: Sequence[StageSpec],
) -> str:
    return _canonical_json_hash(
        {
            "source_archive_sha256": source_archive_sha256,
            "correction_version": CORRECTION_VERSION,
            "generator_revision": generator_revision,
            "generator_sha256": generator_sha256,
            "hash_algorithm": HASH_ALGORITHM,
            "applied_stages": _stage_identity(stages),
        }
    )


def _row_payload(row: Mapping[str, str]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _table_report(
    before: TableData | None,
    after: TableData,
    *,
    stage_ids: Sequence[str],
) -> dict[str, Any]:
    after_counter = Counter(_row_payload(row) for row in after.rows)
    if before is None:
        return {
            "row_count_before": None,
            "row_count_after": len(after.rows),
            "rows_added": len(after.rows),
            "rows_removed": 0,
            "cells_changed": 0,
            "columns_before": None,
            "columns_after": list(after.columns),
            "stage_ids": list(stage_ids),
        }
    before_counter = Counter(_row_payload(row) for row in before.rows)
    rows_added = sum((after_counter - before_counter).values())
    rows_removed = sum((before_counter - after_counter).values())
    common_columns = [column for column in before.columns if column in after.columns]
    cells_changed = 0
    for before_row, after_row in zip(before.rows, after.rows, strict=False):
        cells_changed += sum(
            before_row[column] != after_row[column] for column in common_columns
        )
    return {
        "row_count_before": len(before.rows),
        "row_count_after": len(after.rows),
        "rows_added": rows_added,
        "rows_removed": rows_removed,
        "cells_changed": cells_changed,
        "columns_before": list(before.columns),
        "columns_after": list(after.columns),
        "stage_ids": list(stage_ids),
    }


def build_correction_report(
    source: Mapping[str, TableData],
    corrected: Mapping[str, TableData],
    *,
    touched: Mapping[str, Sequence[str]],
    build_id: str,
) -> dict[str, Any]:
    report = {
        "format_version": 1,
        "artifact_type": "correction_report",
        "dataset_epoch": DATASET_EPOCH,
        "correction_version": CORRECTION_VERSION,
        "build_id": build_id,
        "tables": {
            table: _table_report(
                source.get(table),
                corrected[table],
                stage_ids=touched.get(table, ()),
            )
            for table in sorted(corrected)
        },
    }
    scan_for_sensitive_values(report)
    validate_correction_report(report)
    return report


def build_corrected_table_entries(dataset: Mapping[str, TableData]) -> dict[str, Any]:
    """V4-CM-1.7이 corrected manifest를 조립할 때 사용하는 candidate entry."""
    return {
        table: {
            "file_id": f"corrected/{CORRECTION_VERSION}/postgres/{table}.csv",
            "columns": list(data.columns),
            "row_count": len(data.rows),
            "content_hash": hash_canonical_rows(data.rows),
        }
        for table, data in sorted(dataset.items())
    }


def _write_csv(path: Path, data: TableData) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=list(data.columns),
                extrasaction="raise",
                lineterminator="\n",
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writeheader()
            writer.writerows(data.rows)
            destination.flush()
            os.fsync(destination.fileno())
    except (OSError, csv.Error, ValueError) as exc:
        raise ManifestSchemaError("corrected CSV를 안전하게 쓸 수 없습니다") from exc
    return _sha256_file(path)


def _build_receipt(
    dataset: Mapping[str, TableData],
    *,
    source_archive_sha256: str,
    generator_revision: str,
    generator_components: Sequence[Mapping[str, str]],
    generator_sha256: str,
    build_id: str,
    stages: Sequence[StageSpec],
    file_hashes: Mapping[str, str],
) -> dict[str, Any]:
    receipt = {
        "format_version": RECEIPT_FORMAT_VERSION,
        "artifact_type": "corrected_build_receipt",
        "dataset_epoch": DATASET_EPOCH,
        "source_archive_sha256": source_archive_sha256,
        "correction_version": CORRECTION_VERSION,
        "generator_revision": generator_revision,
        "generator_sha256": generator_sha256,
        "generator_components": list(generator_components),
        "registration_status": "UNREGISTERED",
        "hash_algorithm": HASH_ALGORITHM,
        "build_id": build_id,
        "applied_stages": _stage_identity(stages),
        "tables": {
            table: {
                "file_id": f"corrected/{CORRECTION_VERSION}/postgres/{table}.csv",
                "row_count": len(data.rows),
                "content_hash": hash_canonical_rows(data.rows),
                "file_sha256": file_hashes[table],
            }
            for table, data in sorted(dataset.items())
        },
    }
    scan_for_sensitive_values(receipt)
    validate_build_receipt(receipt)
    return receipt


def validate_build_receipt(receipt: Mapping[str, Any]) -> None:
    _require_exact_keys(receipt, RECEIPT_KEYS, context="build receipt")
    if receipt["format_version"] != RECEIPT_FORMAT_VERSION:
        raise ManifestMetadataError("build receipt format_version이 다릅니다")
    if receipt["artifact_type"] != "corrected_build_receipt":
        raise ManifestMetadataError("build receipt artifact_type이 다릅니다")
    if receipt["dataset_epoch"] != DATASET_EPOCH:
        raise ManifestMetadataError("build receipt dataset_epoch가 다릅니다")
    if receipt["correction_version"] != CORRECTION_VERSION:
        raise ManifestMetadataError("build receipt correction_version이 다릅니다")
    if receipt["registration_status"] != "UNREGISTERED":
        raise ManifestMetadataError("build receipt registration_status가 다릅니다")
    if receipt["hash_algorithm"] != HASH_ALGORITHM:
        raise ManifestMetadataError("build receipt hash_algorithm이 다릅니다")
    for field in ("source_archive_sha256", "generator_sha256", "build_id"):
        if not isinstance(receipt[field], str) or not HEX_SHA256_PATTERN.fullmatch(
            receipt[field]
        ):
            raise ManifestSchemaError(f"build receipt {field} 형식이 잘못됐습니다")
    _require_nonempty_string(
        receipt["generator_revision"], context="generator_revision"
    )
    components = receipt["generator_components"]
    if not isinstance(components, list) or len(components) < 2:
        raise ManifestSchemaError("generator_components가 부족합니다")
    logical_ids: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            raise ManifestSchemaError("generator component는 object여야 합니다")
        _require_exact_keys(component, {"logical_id", "sha256"}, context="component")
        logical_id = _require_nonempty_string(
            component["logical_id"], context="component.logical_id"
        )
        if logical_id.startswith("/") or ".." in PurePosixPath(logical_id).parts:
            raise ManifestSchemaError("generator logical_id가 안전하지 않습니다")
        if not HEX_SHA256_PATTERN.fullmatch(component["sha256"]):
            raise ManifestSchemaError("generator component hash가 잘못됐습니다")
        logical_ids.append(logical_id)
    if logical_ids != sorted(logical_ids) or len(logical_ids) != len(set(logical_ids)):
        raise ManifestSchemaError("generator component 순서·중복이 잘못됐습니다")
    if _generator_sha256(components) != receipt["generator_sha256"]:
        raise ManifestSchemaError("generator_sha256가 component와 다릅니다")

    applied_stages = receipt["applied_stages"]
    if not isinstance(applied_stages, list):
        raise ManifestSchemaError("applied_stages는 배열이어야 합니다")
    seen: set[str] = set()
    for stage in applied_stages:
        if not isinstance(stage, dict):
            raise ManifestSchemaError("applied stage는 object여야 합니다")
        _require_exact_keys(
            stage, {"stage_id", "stage_version"}, context="applied stage"
        )
        stage_id = _require_nonempty_string(stage["stage_id"], context="stage_id")
        _require_nonempty_string(stage["stage_version"], context="stage_version")
        if not STAGE_ID_PATTERN.fullmatch(stage_id) or stage_id in seen:
            raise ManifestSchemaError("applied stage ID가 잘못됐습니다")
        seen.add(stage_id)

    expected_build_id = _canonical_json_hash(
        {
            "source_archive_sha256": receipt["source_archive_sha256"],
            "correction_version": receipt["correction_version"],
            "generator_revision": receipt["generator_revision"],
            "generator_sha256": receipt["generator_sha256"],
            "hash_algorithm": receipt["hash_algorithm"],
            "applied_stages": applied_stages,
        }
    )
    if expected_build_id != receipt["build_id"]:
        raise ManifestSchemaError("build_id가 receipt provenance와 다릅니다")

    tables = receipt["tables"]
    if not isinstance(tables, dict) or not tables:
        raise ManifestSchemaError("build receipt tables가 비어 있습니다")
    for table, entry in tables.items():
        if not isinstance(table, str) or not table or not isinstance(entry, dict):
            raise ManifestSchemaError("build receipt table entry가 잘못됐습니다")
        _require_exact_keys(entry, RECEIPT_TABLE_KEYS, context=f"{table} receipt")
        if entry["file_id"] != f"corrected/{CORRECTION_VERSION}/postgres/{table}.csv":
            raise ManifestSchemaError(f"{table}: corrected file_id가 다릅니다")
        _require_nonnegative_int(entry["row_count"], context=f"{table}.row_count")
        for field in ("content_hash", "file_sha256"):
            if not isinstance(entry[field], str) or not HEX_SHA256_PATTERN.fullmatch(
                entry[field]
            ):
                raise ManifestSchemaError(f"{table}.{field} 형식이 잘못됐습니다")
    scan_for_sensitive_values(receipt)


def validate_correction_report(report: Mapping[str, Any]) -> None:
    _require_exact_keys(report, REPORT_KEYS, context="correction report")
    if (
        report["format_version"] != 1
        or report["artifact_type"] != "correction_report"
        or report["dataset_epoch"] != DATASET_EPOCH
        or report["correction_version"] != CORRECTION_VERSION
    ):
        raise ManifestMetadataError("correction report metadata가 다릅니다")
    if not isinstance(report["build_id"], str) or not HEX_SHA256_PATTERN.fullmatch(
        report["build_id"]
    ):
        raise ManifestSchemaError("correction report build_id가 잘못됐습니다")
    tables = report["tables"]
    if not isinstance(tables, dict) or not tables:
        raise ManifestSchemaError("correction report tables가 비어 있습니다")
    for table, entry in tables.items():
        if not isinstance(entry, dict):
            raise ManifestSchemaError(f"{table}: report entry가 잘못됐습니다")
        _require_exact_keys(entry, REPORT_TABLE_KEYS, context=f"{table} report")
        before_count = entry["row_count_before"]
        if before_count is not None:
            _require_nonnegative_int(before_count, context=f"{table}.row_count_before")
        for field in ("row_count_after", "rows_added", "rows_removed", "cells_changed"):
            _require_nonnegative_int(entry[field], context=f"{table}.{field}")
        before_columns = entry["columns_before"]
        after_columns = entry["columns_after"]
        if before_columns is not None and not _valid_columns(before_columns):
            raise ManifestSchemaError(f"{table}.columns_before가 잘못됐습니다")
        if not _valid_columns(after_columns):
            raise ManifestSchemaError(f"{table}.columns_after가 잘못됐습니다")
        stage_ids = entry["stage_ids"]
        if not isinstance(stage_ids, list) or len(stage_ids) != len(set(stage_ids)):
            raise ManifestSchemaError(f"{table}.stage_ids가 잘못됐습니다")
        if any(not STAGE_ID_PATTERN.fullmatch(value) for value in stage_ids):
            raise ManifestSchemaError(f"{table}.stage_ids 형식이 잘못됐습니다")
    scan_for_sensitive_values(report)


def _valid_columns(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and len(value) == len(set(value))
        and all(isinstance(column, str) and column for column in value)
    )


def _validate_active_payload(payload: Mapping[str, Any]) -> None:
    try:
        _require_exact_keys(payload, ACTIVE_KEYS, context="active pointer")
    except ManifestSchemaError as exc:
        raise ActivePointerError(str(exc)) from exc
    if payload["format_version"] != 1:
        raise ActivePointerError("active pointer format_version이 다릅니다")
    if payload["correction_version"] != CORRECTION_VERSION:
        raise ActivePointerError("active pointer correction_version이 다릅니다")
    for field in ("build_id", "receipt_sha256"):
        if not isinstance(payload[field], str) or not HEX_SHA256_PATTERN.fullmatch(
            payload[field]
        ):
            raise ActivePointerError(f"active pointer {field} 형식이 잘못됐습니다")


def _resolve_active_build(payload: Mapping[str, Any], context: BuildContext) -> Path:
    _validate_active_payload(payload)
    candidate = context.builds_root / payload["build_id"]
    if candidate.is_symlink():
        raise ActivePointerError("active build에 symlink를 사용할 수 없습니다")
    resolved_builds = context.builds_root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    if resolved_candidate.parent != resolved_builds:
        raise ActivePointerError("active build 경로가 builds root 밖입니다")
    return resolved_candidate


def _load_active_state(context: BuildContext) -> ActiveState | None:
    if not context.active_path.exists():
        return None
    if context.active_path.is_symlink():
        raise ActivePointerError("active pointer에 symlink를 사용할 수 없습니다")
    try:
        raw = context.active_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivePointerError("active pointer를 안전하게 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise ActivePointerError("active pointer 최상위 값은 object여야 합니다")
    build_path = _resolve_active_build(payload, context)
    return ActiveState(payload, _sha256_bytes(raw), build_path)


def _validate_build(
    build_path: Path,
    *,
    expected_receipt: Mapping[str, Any] | None = None,
    expected_report: Mapping[str, Any] | None = None,
    expected_build_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    if build_path.is_symlink() or not build_path.is_dir():
        raise ActivePointerError("build 디렉터리가 없거나 symlink입니다")
    expected_root_entries = {"postgres", "correction-report.json", "build-receipt.json"}
    try:
        root_entries = {entry.name for entry in build_path.iterdir()}
    except OSError as exc:
        raise ActivePointerError("build 디렉터리를 읽을 수 없습니다") from exc
    if root_entries != expected_root_entries:
        raise ActivePointerError("build 파일 집합이 계약과 다릅니다")

    receipt_path = build_path / "build-receipt.json"
    report_path = build_path / "correction-report.json"
    receipt = _read_json_object(receipt_path, context="build receipt")
    report = _read_json_object(report_path, context="correction report")
    validate_build_receipt(receipt)
    validate_correction_report(report)
    required_build_id = expected_build_id or build_path.name
    if (
        receipt["build_id"] != required_build_id
        or report["build_id"] != required_build_id
    ):
        raise ActivePointerError("build ID가 예상값과 다릅니다")
    if set(receipt["tables"]) != set(report["tables"]):
        raise ActivePointerError("receipt와 report table 집합이 다릅니다")
    if expected_receipt is not None and receipt != expected_receipt:
        raise ManifestSchemaError("동일 build_id의 receipt 내용이 다릅니다")
    if expected_report is not None and report != expected_report:
        raise ManifestSchemaError("동일 build_id의 report 내용이 다릅니다")

    postgres_root = build_path / "postgres"
    if postgres_root.is_symlink() or not postgres_root.is_dir():
        raise ActivePointerError("postgres 출력 디렉터리가 안전하지 않습니다")
    expected_files = {f"{table}.csv" for table in receipt["tables"]}
    try:
        actual_files = {entry.name for entry in postgres_root.iterdir()}
    except OSError as exc:
        raise ActivePointerError("postgres 출력 파일을 읽을 수 없습니다") from exc
    if actual_files != expected_files:
        raise ActivePointerError("corrected CSV 파일 집합이 다릅니다")
    for table, entry in receipt["tables"].items():
        csv_path = postgres_root / f"{table}.csv"
        if csv_path.is_symlink():
            raise ActivePointerError("corrected CSV에 symlink를 사용할 수 없습니다")
        try:
            payload = csv_path.read_bytes()
        except OSError as exc:
            raise ActivePointerError("corrected CSV를 읽을 수 없습니다") from exc
        if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
            raise ManifestSchemaError("corrected CSV 쓰기 형식이 다릅니다")
        columns = report["tables"][table]["columns_after"]
        parsed_columns, rows = parse_csv_bytes(
            payload, table=table, expected_columns=columns
        )
        if parsed_columns != columns or len(rows) != entry["row_count"]:
            raise ManifestSchemaError("corrected CSV row/column이 receipt와 다릅니다")
        if hash_canonical_rows(rows) != entry["content_hash"]:
            raise ManifestSchemaError("corrected CSV content hash가 다릅니다")
        if _sha256_bytes(payload) != entry["file_sha256"]:
            raise ManifestSchemaError("corrected CSV byte hash가 다릅니다")
    return receipt, _sha256_file(receipt_path)


def _validate_active_target(state: ActiveState, context: BuildContext) -> None:
    _, receipt_hash = _validate_build(state.build_path)
    if receipt_hash != state.payload["receipt_sha256"]:
        raise ActivePointerError("active pointer receipt hash가 다릅니다")
    if state.payload["build_id"] != state.build_path.name:
        raise ActivePointerError("active pointer build ID가 다릅니다")


def _create_staging(context: BuildContext) -> Path:
    context.staging_root.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        staging = context.staging_root / uuid.uuid4().hex
        try:
            staging.mkdir(mode=0o700)
            return staging
        except FileExistsError:
            continue
        except OSError as exc:
            raise ManifestSchemaError("staging 디렉터리를 만들 수 없습니다") from exc
    raise ManifestSchemaError("고유 staging 디렉터리를 만들 수 없습니다")


def _safe_remove_staging(staging: Path, context: BuildContext) -> None:
    if not staging.exists():
        return
    if (
        staging.is_symlink()
        or staging.parent.resolve() != context.staging_root.resolve()
    ):
        raise VerificationError("허용되지 않은 staging 삭제를 거부했습니다")
    try:
        uuid.UUID(hex=staging.name)
    except ValueError as exc:
        raise VerificationError("staging 이름이 UUID가 아닙니다") from exc
    try:
        shutil.rmtree(staging)
    except OSError as exc:
        raise VerificationError("staging 정리에 실패했습니다") from exc


@contextmanager
def _exclusive_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise VerificationError("lock 파일에 symlink를 사용할 수 없습니다")
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        lock_file: TextIO = os.fdopen(descriptor, "a+", encoding="utf-8")
    except OSError as exc:
        raise VerificationError("active lock을 열 수 없습니다") from exc
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise VerificationError("active lock을 사용할 수 없습니다") from exc
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def _write_staging(
    staging: Path,
    *,
    source: Mapping[str, TableData],
    corrected: Mapping[str, TableData],
    touched: Mapping[str, Sequence[str]],
    source_archive_sha256: str,
    generator_revision: str,
    generator_components: Sequence[Mapping[str, str]],
    generator_sha256: str,
    build_id: str,
    stages: Sequence[StageSpec],
) -> tuple[dict[str, Any], dict[str, Any]]:
    file_hashes = {
        table: _write_csv(staging / "postgres" / f"{table}.csv", data)
        for table, data in sorted(corrected.items())
    }
    report = build_correction_report(
        source, corrected, touched=touched, build_id=build_id
    )
    receipt = _build_receipt(
        corrected,
        source_archive_sha256=source_archive_sha256,
        generator_revision=generator_revision,
        generator_components=generator_components,
        generator_sha256=generator_sha256,
        build_id=build_id,
        stages=stages,
        file_hashes=file_hashes,
    )
    atomic_save_json(staging / "correction-report.json", report)
    atomic_save_json(staging / "build-receipt.json", receipt)
    _validate_build(
        staging,
        expected_receipt=receipt,
        expected_report=report,
        expected_build_id=build_id,
    )
    return receipt, report


def _promote_build(
    staging: Path,
    *,
    context: BuildContext,
    build_id: str,
    receipt: Mapping[str, Any],
    report: Mapping[str, Any],
) -> Path:
    context.builds_root.mkdir(parents=True, exist_ok=True)
    target = context.builds_root / build_id
    if target.exists():
        _validate_build(target, expected_receipt=receipt, expected_report=report)
        _safe_remove_staging(staging, context)
        return target
    try:
        staging.rename(target)
        return target
    except OSError as exc:
        if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY} and not target.exists():
            raise ManifestSchemaError("staging build를 promote할 수 없습니다") from exc
        _validate_build(target, expected_receipt=receipt, expected_report=report)
        _safe_remove_staging(staging, context)
        return target


def _active_payload(build_id: str, receipt_sha256: str) -> dict[str, Any]:
    payload = {
        "format_version": 1,
        "correction_version": CORRECTION_VERSION,
        "build_id": build_id,
        "receipt_sha256": receipt_sha256,
    }
    _validate_active_payload(payload)
    return payload


def _state_fingerprint(state: ActiveState | None) -> str | None:
    return state.fingerprint if state is not None else None


def _print_change_summary(
    state: ActiveState,
    identity: BuildIdentity,
    *,
    generator_revision: str,
    stages: Sequence[StageSpec],
) -> None:
    """비밀값 없이 active→candidate 변경 근거를 출력한다."""
    receipt, _ = _validate_build(state.build_path)
    changed_identity_fields = [
        field
        for field, old, new in (
            (
                "source_archive_sha256",
                receipt["source_archive_sha256"],
                identity.epoch["archive"]["sha256"],
            ),
            ("generator_revision", receipt["generator_revision"], generator_revision),
            (
                "generator_sha256",
                receipt["generator_sha256"],
                identity.generator_sha256,
            ),
            ("applied_stages", receipt["applied_stages"], _stage_identity(stages)),
        )
        if old != new
    ]
    candidate_tables = build_corrected_table_entries(identity.corrected)
    old_tables = receipt["tables"]
    changed_tables = sorted(
        table
        for table in set(old_tables) | set(candidate_tables)
        if table not in old_tables
        or table not in candidate_tables
        or old_tables[table]["row_count"] != candidate_tables[table]["row_count"]
        or old_tables[table]["content_hash"] != candidate_tables[table]["content_hash"]
    )
    print(f"active build_id: {state.payload['build_id']}")
    print(f"candidate build_id: {identity.build_id}")
    print(
        "변경 identity: "
        + (", ".join(changed_identity_fields) if changed_identity_fields else "없음")
    )
    print("변경 table: " + (", ".join(changed_tables) if changed_tables else "없음"))


def _activate_build(
    *,
    context: BuildContext,
    start_state: ActiveState | None,
    build_path: Path,
    expected_build_id: str,
) -> None:
    with _exclusive_lock(context.lock_path):
        current_state = _load_active_state(context)
        if _state_fingerprint(current_state) != _state_fingerprint(start_state):
            if current_state is not None:
                _validate_active_target(current_state, context)
                if current_state.payload["build_id"] == expected_build_id:
                    return
            raise ArtifactMismatchError("active pointer가 실행 중 변경됐습니다")

        _, receipt_hash = _validate_build(build_path)
        payload = _active_payload(expected_build_id, receipt_hash)
        try:
            atomic_save_json(context.active_path, payload)
        except OSError as exc:
            raise ActivePointerError("active pointer를 저장할 수 없습니다") from exc
        reloaded = _load_active_state(context)
        if reloaded is None or reloaded.payload != payload:
            raise ActivePointerError("active pointer 재로딩 검증에 실패했습니다")
        _validate_active_target(reloaded, context)


def _current_identity(
    *,
    archive_path: Path,
    context: BuildContext,
    epoch_path: Path,
    source_manifest_path: Path,
    stages: Sequence[StageSpec],
    generator_revision: str,
) -> BuildIdentity:
    _validate_archive_location(archive_path, context)
    epoch, source = _load_source_dataset(
        archive_path,
        epoch_path=epoch_path,
        source_manifest_path=source_manifest_path,
    )
    corrected, touched = run_stages(source, stages)
    components = build_generator_components(stages)
    generator_sha256 = _generator_sha256(components)
    build_id = compute_build_id(
        source_archive_sha256=epoch["archive"]["sha256"],
        generator_revision=generator_revision,
        generator_sha256=generator_sha256,
        stages=stages,
    )
    return BuildIdentity(
        epoch=epoch,
        source=source,
        corrected=corrected,
        touched=touched,
        components=components,
        generator_sha256=generator_sha256,
        build_id=build_id,
    )


def execute(
    *,
    archive_path: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    allowed_root: Path = DEFAULT_OUTPUT_ROOT,
    epoch_path: Path = DATASET_EPOCH_PATH,
    source_manifest_path: Path = SOURCE_MANIFEST_PATH,
    stages: Sequence[StageSpec] = CORRECTION_STAGES,
    generator_revision: str = GENERATOR_REVISION,
    confirm: bool = False,
    check: bool = False,
) -> int:
    """corrected build를 검증·생성한다. DB·Neo4j·n8n에는 접속하지 않는다."""
    if check and confirm:
        raise VerificationError("--check와 --confirm은 함께 사용할 수 없습니다")
    context = build_context(output_root=output_root, allowed_root=allowed_root)
    start_state = _load_active_state(context)
    if start_state is not None:
        _validate_active_target(start_state, context)

    identity = _current_identity(
        archive_path=archive_path,
        context=context,
        epoch_path=epoch_path,
        source_manifest_path=source_manifest_path,
        stages=stages,
        generator_revision=generator_revision,
    )

    if check:
        if start_state is None:
            raise ActivePointerError("active pointer가 등록되지 않았습니다")
        if start_state.payload["build_id"] != identity.build_id:
            print("active build가 현재 입력·코드·stage와 다릅니다")
            return EXIT_MISMATCH
        print("active corrected build가 현재 입력·코드·stage와 일치합니다")
        return EXIT_OK

    if start_state is not None and start_state.payload["build_id"] == identity.build_id:
        print("corrected build가 이미 active 상태입니다")
        return EXIT_OK
    if start_state is not None and not confirm:
        print("active corrected build 변경이 필요합니다")
        _print_change_summary(
            start_state,
            identity,
            generator_revision=generator_revision,
            stages=stages,
        )
        print("pointer를 바꾸지 않았습니다. 승인하려면 --confirm을 추가하세요")
        return EXIT_CONFIRM_REQUIRED

    staging = _create_staging(context)
    try:
        receipt, report = _write_staging(
            staging,
            source=identity.source,
            corrected=identity.corrected,
            touched=identity.touched,
            source_archive_sha256=identity.epoch["archive"]["sha256"],
            generator_revision=generator_revision,
            generator_components=identity.components,
            generator_sha256=identity.generator_sha256,
            build_id=identity.build_id,
            stages=stages,
        )
        build_path = _promote_build(
            staging,
            context=context,
            build_id=identity.build_id,
            receipt=receipt,
            report=report,
        )
        _activate_build(
            context=context,
            start_state=start_state,
            build_path=build_path,
            expected_build_id=identity.build_id,
        )
    finally:
        if staging.exists():
            original_error_active = sys.exc_info()[0] is not None
            try:
                _safe_remove_staging(staging, context)
            except VerificationError:
                if not original_error_active:
                    raise
                print(
                    "WARNING: staging 정리에 실패했지만 원래 오류를 유지합니다",
                    file=sys.stderr,
                )

    if _sha256_file(archive_path) != identity.epoch["archive"]["sha256"]:
        raise ArtifactMismatchError("실행 후 원본 ZIP SHA-256이 달라졌습니다")
    print("corrected build를 생성하고 active pointer를 교체했습니다")
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="corrected dataset build verifier")
    parser.add_argument("--archive", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--confirm", action="store_true")
    mode.add_argument("--check", action="store_true")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return execute(
        archive_path=args.archive,
        confirm=args.confirm,
        check=args.check,
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except VerificationError as exc:
        print(f"ERROR [{exc.code}]: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
