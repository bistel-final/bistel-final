"""C-6.2가 DB 연결 전에 고정하는 named provenance 두 파일."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import manifest_v3

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
RUNTIME_PROVENANCE_PATH: Final = (
    REPOSITORY_ROOT / "infra/bootstrap/markers/agent_runtime_final.kosa_agent_e2e.json"
)
EVALUATION_PROVENANCE_PATH: Final = (
    REPOSITORY_ROOT / "infra/bootstrap/manifests/evaluation.evaluation_reference.json"
)
DATASET_EPOCH: Final = "fdc_final_20260818"
SOURCE_ARCHIVE_SHA256: Final = (
    "e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3"
)

_RUNTIME_MARKER_KEYS: Final = {
    "artifact_type",
    "format_version",
    "task_id",
    "database",
    "profile",
    "status",
    "dataset_epoch",
    "source_archive_sha256",
    "bootstrap_stage",
    "migration_id",
    "migration_sha256",
    "manifest_sha256",
    "schema_signature_sha256",
    "action_history_rows",
    "change_reference",
    "applied_at",
    "recorded_at",
}


class ProvenanceInvalid(ValueError):
    """named provenance가 최종 epoch·profile 계약과 다르다."""


@dataclass(frozen=True, slots=True)
class StaticProvenance:
    runtime_sha256: str
    evaluation_sha256: str


def _load(path: Path) -> tuple[Mapping[str, Any], str]:
    if path.is_symlink():
        raise ProvenanceInvalid("EVIDENCE_INVALID")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProvenanceInvalid("EVIDENCE_INVALID") from exc
    if not isinstance(payload, Mapping):
        raise ProvenanceInvalid("EVIDENCE_INVALID")
    try:
        manifest_v3.scan_for_sensitive_values(payload)
    except Exception as exc:  # noqa: BLE001 - 외부에는 안정 reason만 노출한다.
        raise ProvenanceInvalid("EVIDENCE_INVALID") from exc
    return payload, hashlib.sha256(raw).hexdigest()


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _validate_runtime(payload: Mapping[str, Any]) -> None:
    if set(payload) != _RUNTIME_MARKER_KEYS:
        raise ProvenanceInvalid("EVIDENCE_INVALID")
    expected = {
        "artifact_type": "agent_runtime_final",
        "format_version": 1,
        "task_id": "V5-CM-3.2",
        "database": "kosa_agent_e2e",
        "profile": "runtime",
        "dataset_epoch": DATASET_EPOCH,
        "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "bootstrap_stage": "runtime_clean",
        "migration_id": "002_agent_runtime_clean",
        "action_history_rows": 0,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ProvenanceInvalid("EVIDENCE_INVALID")
    if payload.get("status") not in {"APPLIED", "VERIFIED_EXISTING"}:
        raise ProvenanceInvalid("EVIDENCE_INVALID")
    if any(
        not _sha256(payload.get(key))
        for key in (
            "migration_sha256",
            "manifest_sha256",
            "schema_signature_sha256",
        )
    ):
        raise ProvenanceInvalid("EVIDENCE_INVALID")


def _validate_evaluation(payload: Mapping[str, Any]) -> None:
    try:
        manifest_v3.validate_manifest_schema(
            payload,
            expected_artifact_type="db_bootstrap",
            expected_profile="evaluation",
            expected_stage="evaluation_reference",
            expected_archive_sha256=SOURCE_ARCHIVE_SHA256,
        )
    except Exception as exc:  # noqa: BLE001 - validator 세부를 안정 reason으로 사상한다.
        raise ProvenanceInvalid("EVIDENCE_INVALID") from exc
    if (
        payload.get("format_version") != 3
        or payload.get("dataset_epoch") != DATASET_EPOCH
        or payload.get("applies_to") != ["kosa_text2sql"]
    ):
        raise ProvenanceInvalid("EVIDENCE_INVALID")
    tables = payload.get("tables")
    if not isinstance(tables, Mapping):
        raise ProvenanceInvalid("EVIDENCE_INVALID")
    if (
        not isinstance(tables.get("lot_history"), Mapping)
        or tables["lot_history"].get("row_count") != 600
        or not isinstance(tables.get("metrology"), Mapping)
        or tables["metrology"].get("row_count") != 48
    ):
        raise ProvenanceInvalid("EVIDENCE_INVALID")


def load_static_provenance(
    *,
    runtime_path: Path = RUNTIME_PROVENANCE_PATH,
    evaluation_path: Path = EVALUATION_PROVENANCE_PATH,
) -> StaticProvenance:
    """두 파일을 schema·epoch·target까지 검증하고 원문 SHA를 돌려준다."""

    runtime, runtime_sha256 = _load(runtime_path)
    evaluation, evaluation_sha256 = _load(evaluation_path)
    _validate_runtime(runtime)
    _validate_evaluation(evaluation)
    return StaticProvenance(runtime_sha256, evaluation_sha256)


__all__ = [
    "EVALUATION_PROVENANCE_PATH",
    "ProvenanceInvalid",
    "RUNTIME_PROVENANCE_PATH",
    "StaticProvenance",
    "load_static_provenance",
]
