"""Readiness가 신뢰하는 immutable runtime marker bundle."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

DATASET_EPOCH = "fdc_final_20260818"
RUNTIME_DATABASE = "kosa_agent"
PACKAGE_ROOT = Path(__file__).resolve().parent / "readiness_marker_bundle"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

# loader·동기화 테스트·Docker 회귀가 공유하는 단일 immutable allowlist다.
READINESS_MARKER_SOURCES: Mapping[str, str] = MappingProxyType(
    {
        "dataset-epoch.json": "infra/bootstrap/dataset-epoch.json",
        "runtime.runtime_checkpointed.json": (
            "infra/bootstrap/manifests/runtime.runtime_checkpointed.json"
        ),
        "postgres_profile.kosa_agent.json": (
            "infra/bootstrap/markers/postgres_profile.kosa_agent.json"
        ),
        "agent_severity_guard_final.kosa_agent.json": (
            "infra/bootstrap/markers/agent_severity_guard_final.kosa_agent.json"
        ),
        "checkpoint_setup_final.kosa_agent.json": (
            "infra/bootstrap/markers/checkpoint_setup_final.kosa_agent.json"
        ),
        "role_matrix_core.kosa_agent.json": (
            "infra/bootstrap/markers/role_matrix_core.kosa_agent.json"
        ),
        "role_matrix_checkpoint.kosa_agent.json": (
            "infra/bootstrap/markers/role_matrix_checkpoint.kosa_agent.json"
        ),
        "agent_runtime_final.kosa_agent.json": (
            "infra/bootstrap/markers/agent_runtime_final.kosa_agent.json"
        ),
        "runtime.runtime_clean.json": (
            "infra/bootstrap/manifests/runtime.runtime_clean.json"
        ),
        "neo4j_graph.neo4j.json": ("infra/bootstrap/markers/neo4j_graph.neo4j.json"),
    }
)
READINESS_MARKER_FILENAMES = tuple(READINESS_MARKER_SOURCES)

POSTGRES_RUNTIME_FILENAMES = (
    "dataset-epoch.json",
    "runtime.runtime_checkpointed.json",
    "postgres_profile.kosa_agent.json",
    "agent_severity_guard_final.kosa_agent.json",
    "checkpoint_setup_final.kosa_agent.json",
    "role_matrix_core.kosa_agent.json",
    "role_matrix_checkpoint.kosa_agent.json",
)
REFERENCE_MIGRATION_FILENAMES = (
    "agent_runtime_final.kosa_agent.json",
    "runtime.runtime_clean.json",
)
NEO4J_FILENAME = "neo4j_graph.neo4j.json"


class MarkerBundleError(RuntimeError):
    """Packaged marker가 final runtime 계보 계약과 다르다."""


class MarkerBundleNotConfiguredError(MarkerBundleError):
    """필수 packaged marker 또는 bundle 자체가 없다."""


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    return value


def canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        _normalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def raw_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MarkerBundle:
    def __init__(self, root: Path = PACKAGE_ROOT) -> None:
        self.root = root

    def assert_exact_inventory(self) -> None:
        try:
            actual = {path.name for path in self.root.iterdir() if path.is_file()}
        except OSError as exc:
            raise MarkerBundleNotConfiguredError(
                "readiness marker bundle이 없습니다"
            ) from exc
        expected = set(READINESS_MARKER_FILENAMES)
        if actual != expected:
            if expected - actual and not actual - expected:
                raise MarkerBundleNotConfiguredError("필수 readiness marker가 없습니다")
            raise MarkerBundleError("readiness marker bundle inventory가 다릅니다")

    def load(self, filename: str) -> dict[str, Any]:
        if filename not in READINESS_MARKER_SOURCES:
            raise MarkerBundleError("허용되지 않은 readiness marker입니다")
        path = self.root / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise MarkerBundleNotConfiguredError(
                "필수 readiness marker가 없습니다"
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MarkerBundleError("readiness marker를 읽을 수 없습니다") from exc
        if not isinstance(payload, dict):
            raise MarkerBundleError("readiness marker는 object여야 합니다")
        return payload

    def validate_postgresql_chain(self) -> dict[str, dict[str, Any]]:
        self.assert_exact_inventory()
        payloads = {name: self.load(name) for name in POSTGRES_RUNTIME_FILENAMES}
        epoch = payloads["dataset-epoch.json"]
        manifest = payloads["runtime.runtime_checkpointed.json"]
        profile = payloads["postgres_profile.kosa_agent.json"]
        severity = payloads["agent_severity_guard_final.kosa_agent.json"]
        checkpoint = payloads["checkpoint_setup_final.kosa_agent.json"]
        role_core = payloads["role_matrix_core.kosa_agent.json"]
        role_checkpoint = payloads["role_matrix_checkpoint.kosa_agent.json"]

        if (
            epoch.get("dataset_epoch") != DATASET_EPOCH
            or manifest.get("dataset_epoch") != DATASET_EPOCH
            or manifest.get("profile") != "runtime"
            or manifest.get("bootstrap_stage") != "runtime_checkpointed"
            or profile.get("database") != RUNTIME_DATABASE
            or profile.get("profile") != "runtime"
            or profile.get("dataset_epoch") != DATASET_EPOCH
        ):
            raise MarkerBundleError("PostgreSQL runtime epoch/profile 계약이 다릅니다")

        for marker, artifact_type, status in (
            (severity, "agent_severity_guard_final", {"APPLIED", "VERIFIED_EXISTING"}),
            (checkpoint, "checkpoint_setup_final", {"APPLIED", "VERIFIED_EXISTING"}),
            (role_core, "role_matrix_core", {"APPLIED", "VERIFIED_EXISTING"}),
            (
                role_checkpoint,
                "role_matrix_checkpoint",
                {"APPLIED", "VERIFIED_EXISTING"},
            ),
        ):
            if (
                marker.get("artifact_type") != artifact_type
                or marker.get("database") != RUNTIME_DATABASE
                or marker.get("dataset_epoch") != DATASET_EPOCH
                or marker.get("status") not in status
            ):
                raise MarkerBundleError("PostgreSQL runtime marker 계약이 다릅니다")

        expected_hashes = {
            "agent_severity_guard_final.kosa_agent.json": raw_file_sha256(
                self.root / "agent_severity_guard_final.kosa_agent.json"
            ),
            "checkpoint_setup_final.kosa_agent.json": raw_file_sha256(
                self.root / "checkpoint_setup_final.kosa_agent.json"
            ),
            "role_matrix_core.kosa_agent.json": raw_file_sha256(
                self.root / "role_matrix_core.kosa_agent.json"
            ),
        }
        core_predecessors = role_core.get("predecessor_artifacts")
        checkpoint_predecessors = role_checkpoint.get("predecessor_artifacts")
        if not isinstance(core_predecessors, dict) or any(
            core_predecessors.get(name) != expected_hashes[name]
            for name in (
                "agent_severity_guard_final.kosa_agent.json",
                "checkpoint_setup_final.kosa_agent.json",
            )
        ):
            raise MarkerBundleError("role core predecessor hash가 다릅니다")
        if not isinstance(checkpoint_predecessors, dict) or any(
            checkpoint_predecessors.get(name) != expected_hashes[name]
            for name in (
                "role_matrix_core.kosa_agent.json",
                "checkpoint_setup_final.kosa_agent.json",
            )
        ):
            raise MarkerBundleError("role checkpoint predecessor hash가 다릅니다")
        return payloads

    def validate_reference_chain(self) -> dict[str, dict[str, Any]]:
        self.assert_exact_inventory()
        marker = self.load("agent_runtime_final.kosa_agent.json")
        manifest = self.load("runtime.runtime_clean.json")
        migrations = manifest.get("applied_migrations")
        if (
            marker.get("artifact_type") != "agent_runtime_final"
            or marker.get("database") != RUNTIME_DATABASE
            or marker.get("dataset_epoch") != DATASET_EPOCH
            or marker.get("status") not in {"APPLIED", "VERIFIED_EXISTING"}
            or marker.get("manifest_sha256") != canonical_json_sha256(manifest)
            or manifest.get("dataset_epoch") != DATASET_EPOCH
            or manifest.get("bootstrap_stage") != "runtime_clean"
            or not isinstance(migrations, list)
            or not migrations
            or migrations[0] != "v5_001_reference_extensions_final"
        ):
            raise MarkerBundleError("reference successor 계보가 다릅니다")
        return {
            "agent_runtime_final.kosa_agent.json": marker,
            "runtime.runtime_clean.json": manifest,
        }

    def validate_source_sync(self) -> None:
        self.assert_exact_inventory()
        for filename, source in READINESS_MARKER_SOURCES.items():
            source_path = REPOSITORY_ROOT / source
            packaged_path = self.root / filename
            try:
                source_payload = json.loads(source_path.read_text(encoding="utf-8"))
                packaged_payload = json.loads(packaged_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise MarkerBundleError(
                    "marker source sync를 확인할 수 없습니다"
                ) from exc
            if canonical_json_sha256(source_payload) != canonical_json_sha256(
                packaged_payload
            ):
                raise MarkerBundleError("packaged marker가 저장소 원본과 다릅니다")
