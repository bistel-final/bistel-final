"""Destructive-safe loader for the registered kosa_0813 Neo4j graph.

No mode connects implicitly.  The raw Cypher is never sent to Neo4j; all
mutations use the reviewed, deterministic seed produced by ``master_cypher``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

from dotenv import load_dotenv
from master_cypher import (
    BOOTSTRAP_ROOT,
    BUSINESS_KEY_CONTRACT_VERSION,
    BUSINESS_KEYS,
    DATASET_EPOCH,
    RELATION_ID_ALGORITHM_VERSION,
    REPOSITORY_ROOT,
    SOURCE_ARCHIVE_SHA256,
    SOURCE_MEMBER_SHA256,
    GraphManifestError,
    NodeSpec,
    ParsedMasterCypher,
    canonical_json_bytes,
    canonical_sha256,
    graph_manifest_sha256,
    parse_registered_archive,
    serialize_business_value,
    sha256_bytes,
    sha256_file,
    validate_generated_artifacts,
)
from neo4j_target import (
    Neo4jBootstrapTarget,
    Neo4jTargetError,
    load_neo4j_bootstrap_target,
    validate_connected_database,
    validate_database_name,
)

MARKER_ROOT = BOOTSTRAP_ROOT / "markers"
BACKUP_SCHEMA_VERSION = "neo4j-logical-v2"
RESTORE_ALGORITHM_VERSION = "neo4j-logical-restore-v2"
PREFLIGHT_TTL = timedelta(hours=24)
APPROVAL_REF_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SUCCESS_STATUSES = frozenset(
    {"APPLIED", "REPLACED", "ADOPTED_EXISTING", "VERIFIED_EXISTING"}
)
ALL_MARKER_STATUSES = SUCCESS_STATUSES | {"RESTORED"}
SNAPSHOT_BUSINESS_KEYS = {**BUSINESS_KEYS, "Sensor": ("sensor_id",)}

PREFLIGHT_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "database",
        "target_fingerprint_sha256",
        "existing_graph_fingerprint_sha256",
        "schema_fingerprint_sha256",
        "node_count",
        "relationship_count",
        "source_member_sha256",
        "corrected_cypher_sha256",
        "recorded_at",
    }
)
BACKUP_MANIFEST_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "database",
        "target_fingerprint_sha256",
        "backup_file_sha256",
        "backup_graph_fingerprint_sha256",
        "schema_fingerprint_sha256",
        "backup_schema_version",
        "node_count",
        "relationship_count",
        "recorded_at",
    }
)
RESTORE_RECEIPT_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "database",
        "target_fingerprint_sha256",
        "backup_manifest_sha256",
        "backup_file_sha256",
        "backup_graph_fingerprint_sha256",
        "schema_fingerprint_sha256",
        "restore_algorithm_version",
        "verified_at",
    }
)
BACKUP_FILE_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "backup_schema_version",
        "database",
        "target_fingerprint_sha256",
        "schema_fingerprint_sha256",
        "created_at",
        "nodes",
        "relationships",
    }
)
MARKER_COMMON_KEYS = frozenset(
    {
        "dataset_epoch",
        "database",
        "target_fingerprint_sha256",
        "source_archive_sha256",
        "source_member_sha256",
        "corrected_manifest_sha256",
        "corrected_cypher_sha256",
        "expected_graph_fingerprint_sha256",
        "actual_graph_fingerprint_sha256",
        "relation_id_algorithm_version",
        "business_key_contract_version",
        "status",
        "recorded_at",
        "node_count",
        "relationship_count",
        "relation_id_duplicates",
    }
)
MARKER_REQUIRED = {
    "APPLIED": frozenset({"applied_at"}),
    "REPLACED": frozenset(
        {
            "applied_at",
            "backup_file_sha256",
            "backup_graph_fingerprint_sha256",
            "schema_fingerprint_sha256",
            "approval_ref",
            "preflight_receipt_sha256",
            "backup_manifest_sha256",
            "restore_receipt_sha256",
        }
    ),
    "ADOPTED_EXISTING": frozenset({"adopted_at", "approval_ref"}),
    "VERIFIED_EXISTING": frozenset(),
    "RESTORED": frozenset(
        {
            "restored_at",
            "backup_file_sha256",
            "backup_graph_fingerprint_sha256",
            "schema_fingerprint_sha256",
            "backup_manifest_sha256",
            "restore_receipt_sha256",
            "approval_ref",
            "pre_restore_graph_fingerprint_sha256",
            "post_restore_graph_fingerprint_sha256",
        }
    ),
}
MARKER_OPTIONAL_UNION = frozenset().union(*MARKER_REQUIRED.values())

NODE_QUERY = """MATCH (n)
RETURN labels(n) AS labels, properties(n) AS properties
"""
RELATIONSHIP_QUERY = """MATCH (a)-[r]->(b)
RETURN labels(a) AS from_labels, properties(a) AS from_properties,
       type(r) AS relationship_type, properties(r) AS relationship_properties,
       labels(b) AS to_labels, properties(b) AS to_properties
"""
DELETE_QUERY = "MATCH (n) DETACH DELETE n"
INDEX_QUERY = """SHOW INDEXES
YIELD name, type, entityType, labelsOrTypes, properties, owningConstraint
RETURN name, type, entityType, labelsOrTypes, properties, owningConstraint
"""
CONSTRAINT_QUERY = """SHOW CONSTRAINTS
YIELD name, type, entityType, labelsOrTypes, properties, ownedIndex
RETURN name, type, entityType, labelsOrTypes, properties, ownedIndex
"""


class Neo4jBootstrapError(RuntimeError):
    exit_code = 2


class GraphStateError(Neo4jBootstrapError):
    exit_code = 3


class EvidenceError(Neo4jBootstrapError):
    exit_code = 4


class MarkerError(Neo4jBootstrapError):
    exit_code = 5


class BackupError(Neo4jBootstrapError):
    exit_code = 6


@dataclass(frozen=True)
class SnapshotNode:
    label: str
    properties: dict[str, str | int | float]

    @property
    def business_id(self) -> str:
        keys = SNAPSHOT_BUSINESS_KEYS.get(self.label)
        if keys is None or any(key not in self.properties for key in keys):
            raise GraphStateError("snapshot business key를 복원할 수 없습니다")
        return "+".join(
            f"{key}={serialize_business_value(self.properties[key])}"
            for key in sorted(keys)
        )


@dataclass(frozen=True)
class RelationshipSnapshot:
    relation_type: str
    from_node: NodeSpec | SnapshotNode
    to_node: NodeSpec | SnapshotNode
    properties: dict[str, str | int | float]

    @property
    def relation_id(self) -> str | None:
        value = self.properties.get("relation_id")
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class GraphSnapshot:
    nodes: tuple[NodeSpec | SnapshotNode, ...]
    relationships: tuple[RelationshipSnapshot, ...]

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def relationship_count(self) -> int:
        return len(self.relationships)

    @property
    def relation_id_duplicates(self) -> int:
        values = [item.relation_id for item in self.relationships]
        present = [value for value in values if value is not None]
        return len(present) - len(set(present))


@dataclass(frozen=True)
class LoaderContext:
    target: Neo4jBootstrapTarget
    parsed: ParsedMasterCypher
    manifest: dict[str, Any]
    corrected_manifest_sha256: str


def _record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    try:
        return dict(record.data())
    except (AttributeError, TypeError, ValueError) as exc:
        raise GraphStateError("Neo4j query 응답 형식이 잘못됐습니다") from exc


def _rows(result: Any) -> list[dict[str, Any]]:
    try:
        return [_record_to_dict(record) for record in result]
    except TypeError as exc:
        raise GraphStateError("Neo4j query 결과를 순회할 수 없습니다") from exc


def _supported_properties(value: Any) -> dict[str, str | int | float]:
    if not isinstance(value, Mapping):
        raise GraphStateError("Neo4j property payload가 object가 아닙니다")
    result: dict[str, str | int | float] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise GraphStateError("Neo4j property key가 문자열이 아닙니다")
        if isinstance(item, bool) or not isinstance(item, str | int | float):
            raise GraphStateError("지원하지 않는 Neo4j property type입니다")
        if isinstance(item, float) and not math.isfinite(item):
            raise GraphStateError("Neo4j float property가 finite 값이 아닙니다")
        result[key] = item
    return result


def _single_label(value: Any) -> str:
    if not isinstance(value, list | tuple) or len(value) != 1:
        raise GraphStateError("노드는 bk-v1 label 하나만 가져야 합니다")
    label = value[0]
    if not isinstance(label, str) or label not in SNAPSHOT_BUSINESS_KEYS:
        raise GraphStateError("snapshot allowlist 밖의 node label입니다")
    return label


def snapshot_from_rows(
    node_rows: Sequence[Mapping[str, Any]],
    relationship_rows: Sequence[Mapping[str, Any]],
) -> GraphSnapshot:
    nodes: list[NodeSpec | SnapshotNode] = []
    identities: dict[tuple[str, str], NodeSpec | SnapshotNode] = {}
    for row in node_rows:
        node = SnapshotNode(
            _single_label(row.get("labels")),
            _supported_properties(row.get("properties")),
        )
        identity = (node.label, node.business_id)
        if identity in identities:
            raise GraphStateError("Neo4j business key가 중복됐습니다")
        identities[identity] = node
        nodes.append(node)

    relationships: list[RelationshipSnapshot] = []
    for row in relationship_rows:
        from_node = SnapshotNode(
            _single_label(row.get("from_labels")),
            _supported_properties(row.get("from_properties")),
        )
        to_node = SnapshotNode(
            _single_label(row.get("to_labels")),
            _supported_properties(row.get("to_properties")),
        )
        for endpoint in (from_node, to_node):
            identity = (endpoint.label, endpoint.business_id)
            if (
                identity not in identities
                or identities[identity].properties != endpoint.properties
            ):
                raise GraphStateError(
                    "relationship endpoint를 node에 복원할 수 없습니다"
                )
        relation_type = row.get("relationship_type")
        if not isinstance(relation_type, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]*", relation_type
        ):
            raise GraphStateError("relationship type 형식이 잘못됐습니다")
        relationships.append(
            RelationshipSnapshot(
                relation_type,
                identities[(from_node.label, from_node.business_id)],
                identities[(to_node.label, to_node.business_id)],
                _supported_properties(row.get("relationship_properties")),
            )
        )
    relation_tuples = [
        (
            item.relation_type,
            item.from_node.label,
            item.from_node.business_id,
            item.to_node.label,
            item.to_node.business_id,
        )
        for item in relationships
    ]
    if len(relation_tuples) != len(set(relation_tuples)):
        raise GraphStateError("명시적 edge key 없는 병렬 relationship이 있습니다")
    return GraphSnapshot(tuple(nodes), tuple(relationships))


def snapshot_payload(
    snapshot: GraphSnapshot, *, legacy: bool = False
) -> dict[str, Any]:
    node_items = [
        {
            "label": node.label,
            "business_id": node.business_id,
            "properties": dict(sorted(node.properties.items())),
        }
        for node in snapshot.nodes
    ]
    relation_items: list[dict[str, Any]] = []
    for relation in snapshot.relationships:
        properties = dict(sorted(relation.properties.items()))
        properties.pop("relation_id", None) if legacy else None
        item: dict[str, Any] = {
            "type": relation.relation_type,
            "from_label": relation.from_node.label,
            "from_business_id": relation.from_node.business_id,
            "to_label": relation.to_node.label,
            "to_business_id": relation.to_node.business_id,
            "properties": properties,
        }
        if not legacy and relation.relation_id is not None:
            item["relation_id"] = relation.relation_id
        relation_items.append(item)
    node_items.sort(key=lambda item: canonical_json_bytes(item).decode("utf-8"))
    relation_items.sort(key=lambda item: canonical_json_bytes(item).decode("utf-8"))
    return {"nodes": node_items, "relationships": relation_items}


def snapshot_fingerprint(snapshot: GraphSnapshot, *, legacy: bool = False) -> str:
    return canonical_sha256(snapshot_payload(snapshot, legacy=legacy))


def expected_snapshot(parsed: ParsedMasterCypher) -> GraphSnapshot:
    relationships = tuple(
        RelationshipSnapshot(
            item.relation_type,
            item.from_node,
            item.to_node,
            dict(item.properties),
        )
        for item in parsed.relationships
    )
    return GraphSnapshot(parsed.nodes, relationships)


def inspect_graph(query_runner: Any) -> GraphSnapshot:
    return snapshot_from_rows(
        _rows(query_runner.run(NODE_QUERY)),
        _rows(query_runner.run(RELATIONSHIP_QUERY)),
    )


def _schema_identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise GraphStateError(f"Neo4j {field} 형식이 잘못됐습니다")
    return value


def _schema_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list | tuple) or not value:
        raise GraphStateError(f"Neo4j {field} 목록이 잘못됐습니다")
    return [_schema_identifier(item, field=field) for item in value]


def supported_schema_payload(query_runner: Any) -> dict[str, Any]:
    """Return a deterministic fingerprint payload for schema kept in-place.

    Logical graph replace never drops schema.  Therefore only Neo4j lookup indexes
    and RANGE indexes owned by NODE UNIQUENESS constraints are supported.  A
    standalone user index or any other constraint still requires an official dump.
    """

    constraints: list[dict[str, Any]] = []
    constraint_by_name: dict[str, dict[str, Any]] = {}
    for row in _rows(query_runner.run(CONSTRAINT_QUERY)):
        name = _schema_identifier(row.get("name"), field="constraint name")
        constraint_type = str(row.get("type", "")).upper()
        entity_type = str(row.get("entityType", "")).upper()
        labels = _schema_string_list(row.get("labelsOrTypes"), field="constraint label")
        properties = _schema_string_list(
            row.get("properties"), field="constraint property"
        )
        owned_index = _schema_identifier(row.get("ownedIndex"), field="owned index")
        if constraint_type != "UNIQUENESS" or entity_type != "NODE":
            raise GraphStateError(
                "지원하지 않는 constraint가 있어 공식 Neo4j dump가 필요합니다"
            )
        item = {
            "name": name,
            "type": constraint_type,
            "entity_type": entity_type,
            "labels_or_types": labels,
            "properties": properties,
            "owned_index": owned_index,
        }
        if name in constraint_by_name:
            raise GraphStateError("Neo4j constraint 이름이 중복됐습니다")
        constraint_by_name[name] = item
        constraints.append(item)

    indexes: list[dict[str, Any]] = []
    owned_index_names: set[str] = set()
    for row in _rows(query_runner.run(INDEX_QUERY)):
        name = _schema_identifier(row.get("name"), field="index name")
        index_type = str(row.get("type", "")).upper()
        entity_type = str(row.get("entityType", "")).upper()
        owner = row.get("owningConstraint")
        if index_type == "LOOKUP":
            if owner is not None:
                raise GraphStateError("LOOKUP index에 constraint owner가 있습니다")
            item = {
                "name": name,
                "type": index_type,
                "entity_type": entity_type,
                "labels_or_types": None,
                "properties": None,
                "owning_constraint": None,
            }
        elif index_type == "RANGE":
            if not isinstance(owner, str):
                raise GraphStateError(
                    "독립 사용자 index가 있어 공식 Neo4j dump가 필요합니다"
                )
            owner_name = _schema_identifier(owner, field="owning constraint")
            if owner_name not in constraint_by_name:
                raise GraphStateError(
                    "독립 사용자 index가 있어 공식 Neo4j dump가 필요합니다"
                )
            labels = _schema_string_list(row.get("labelsOrTypes"), field="index label")
            properties = _schema_string_list(
                row.get("properties"), field="index property"
            )
            constraint = constraint_by_name[owner_name]
            if (
                constraint["owned_index"] != name
                or constraint["entity_type"] != entity_type
                or constraint["labels_or_types"] != labels
                or constraint["properties"] != properties
            ):
                raise GraphStateError("constraint와 backing index가 일치하지 않습니다")
            owned_index_names.add(name)
            item = {
                "name": name,
                "type": index_type,
                "entity_type": entity_type,
                "labels_or_types": labels,
                "properties": properties,
                "owning_constraint": owner_name,
            }
        else:
            raise GraphStateError(
                "지원하지 않는 index가 있어 공식 Neo4j dump가 필요합니다"
            )
        indexes.append(item)

    expected_owned = {item["owned_index"] for item in constraints}
    if owned_index_names != expected_owned:
        raise GraphStateError("constraint backing index가 누락됐습니다")
    indexes.sort(key=lambda item: canonical_json_bytes(item))
    constraints.sort(key=lambda item: canonical_json_bytes(item))
    return {"indexes": indexes, "constraints": constraints}


def validate_supported_schema(query_runner: Any) -> str:
    return canonical_sha256(supported_schema_payload(query_runner))


def graph_state(
    snapshot: GraphSnapshot,
    manifest: Mapping[str, Any],
    *,
    marker: Mapping[str, Any] | None,
) -> str:
    if snapshot.node_count == 0 and snapshot.relationship_count == 0:
        if marker is not None:
            raise GraphStateError("marker는 있지만 graph가 비어 있습니다")
        return "EMPTY"
    actual = snapshot_fingerprint(snapshot)
    expected = manifest["expected_graph_fingerprint_sha256"]
    if actual == expected:
        if marker is None:
            return "EXACT_WITHOUT_MARKER"
        validate_marker(marker)
        if marker["status"] == "RESTORED":
            return "EXACT_WITH_RECOVERY_MARKER"
        if not marker_is_readiness_success(marker):
            raise GraphStateError("marker가 readiness success 계약을 만족하지 않습니다")
        return "EXACT_WITH_MARKER"
    all_missing = all(item.relation_id is None for item in snapshot.relationships)
    if (
        all_missing
        and snapshot_fingerprint(snapshot, legacy=True)
        == manifest["expected_legacy_fingerprint_sha256"]
    ):
        return "LEGACY_EXACT"
    if marker is not None:
        raise GraphStateError("marker와 실제 Neo4j graph가 다릅니다")
    return "CONFLICT"


def _parse_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError(f"{field} 시각 형식이 잘못됐습니다")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{field} 시각 형식이 잘못됐습니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceError(f"{field} 시각은 timezone-aware여야 합니다")
    return parsed.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_text(value: datetime | None = None) -> str:
    moment = value or utc_now()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise EvidenceError("artifact 시각은 timezone-aware여야 합니다")
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validate_approval_ref(value: str | None) -> str:
    if not isinstance(value, str) or not APPROVAL_REF_PATTERN.fullmatch(value):
        raise EvidenceError("approval_ref 형식이 잘못됐습니다")
    return value


def _validate_sha(payload: Mapping[str, Any], key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise EvidenceError(f"{key} SHA-256 형식이 잘못됐습니다")


def validate_receipt(payload: Mapping[str, Any], artifact_type: str) -> None:
    key_sets = {
        "neo4j_preflight_receipt": PREFLIGHT_KEYS,
        "neo4j_backup_manifest": BACKUP_MANIFEST_KEYS,
        "neo4j_restore_verification_receipt": RESTORE_RECEIPT_KEYS,
    }
    required = key_sets.get(artifact_type)
    if required is None or set(payload) != required:
        raise EvidenceError("Neo4j receipt key 집합이 잘못됐습니다")
    if (
        payload.get("artifact_type") != artifact_type
        or payload.get("format_version") != 2
    ):
        raise EvidenceError("Neo4j receipt metadata가 잘못됐습니다")
    validate_database_name(str(payload.get("database", "")))
    for key in required:
        if key.endswith("sha256"):
            _validate_sha(payload, key)
    if artifact_type == "neo4j_preflight_receipt":
        _parse_utc(payload.get("recorded_at"), field="recorded_at")
        for count in (payload.get("node_count"), payload.get("relationship_count")):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise EvidenceError("preflight count가 잘못됐습니다")
    elif artifact_type == "neo4j_backup_manifest":
        _parse_utc(payload.get("recorded_at"), field="recorded_at")
        if payload.get("backup_schema_version") != BACKUP_SCHEMA_VERSION:
            raise EvidenceError("backup schema version이 잘못됐습니다")
        for count in (payload.get("node_count"), payload.get("relationship_count")):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise EvidenceError("backup count가 잘못됐습니다")
    else:
        _parse_utc(payload.get("verified_at"), field="verified_at")
        if payload.get("restore_algorithm_version") != RESTORE_ALGORITHM_VERSION:
            raise EvidenceError("restore algorithm version이 잘못됐습니다")


def _read_json(
    path: Path, *, error_type: type[Neo4jBootstrapError] = EvidenceError
) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise error_type("artifact를 안전하게 읽을 수 없습니다")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise error_type("artifact JSON을 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise error_type("artifact 최상위 값은 object여야 합니다")
    return payload


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_backup_path(path: Path, backup_root: Path) -> Path:
    resolved_root = backup_root.expanduser().resolve()
    resolved = path.expanduser().resolve()
    repository = REPOSITORY_ROOT.resolve()
    if resolved_root == repository or _is_within(resolved_root, repository):
        raise BackupError("backup root는 저장소 밖이어야 합니다")
    if not _is_within(resolved, resolved_root) or resolved == resolved_root:
        raise BackupError("artifact 경로는 backup root 하위여야 합니다")
    return resolved


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
def exclusive_file_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise BackupError("lock에 symlink를 사용할 수 없습니다")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        lock_file: BinaryIO = os.fdopen(descriptor, "a+b")
        _acquire_file_lock(lock_file)
    except OSError as exc:
        raise BackupError("artifact lock을 사용할 수 없습니다") from exc
    try:
        yield
    finally:
        try:
            _release_file_lock(lock_file)
        finally:
            lock_file.close()


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise BackupError("artifact에 symlink를 사용할 수 없습니다")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise BackupError("artifact를 원자적으로 저장할 수 없습니다") from exc
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def save_external_artifact(
    path: Path, payload: Mapping[str, Any], backup_root: Path
) -> None:
    destination = validate_backup_path(path, backup_root)
    with exclusive_file_lock(backup_root.resolve() / ".neo4j-bootstrap.lock"):
        atomic_write_bytes(destination, canonical_json_bytes(payload) + b"\n")


def marker_path(database: str, *, root: Path = MARKER_ROOT) -> Path:
    validate_database_name(database)
    return root / f"neo4j_graph.{database}.json"


def validate_marker(payload: Mapping[str, Any]) -> None:
    status = payload.get("status")
    if status not in ALL_MARKER_STATUSES:
        raise MarkerError("Neo4j marker status가 잘못됐습니다")
    expected_keys = MARKER_COMMON_KEYS | MARKER_REQUIRED[status]
    if set(payload) != expected_keys:
        raise MarkerError("Neo4j marker status별 key 집합이 잘못됐습니다")
    if payload.get("dataset_epoch") != DATASET_EPOCH:
        raise MarkerError("Neo4j marker dataset epoch가 다릅니다")
    validate_database_name(str(payload.get("database", "")))
    for key in expected_keys:
        if key.endswith("sha256"):
            value = payload.get(key)
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                raise MarkerError("Neo4j marker SHA-256 형식이 잘못됐습니다")
    for key in ("recorded_at", "applied_at", "adopted_at", "restored_at"):
        if key in payload:
            try:
                _parse_utc(payload[key], field=key)
            except EvidenceError as exc:
                raise MarkerError(str(exc)) from exc
    if (
        payload.get("source_archive_sha256") != SOURCE_ARCHIVE_SHA256
        or payload.get("source_member_sha256") != SOURCE_MEMBER_SHA256
    ):
        raise MarkerError("Neo4j marker source provenance가 다릅니다")
    if (
        payload.get("relation_id_algorithm_version") != RELATION_ID_ALGORITHM_VERSION
        or payload.get("business_key_contract_version") != BUSINESS_KEY_CONTRACT_VERSION
    ):
        raise MarkerError("Neo4j marker algorithm version이 다릅니다")
    if any(
        isinstance(payload.get(key), bool)
        or not isinstance(payload.get(key), int)
        or payload[key] < 0
        for key in ("node_count", "relationship_count", "relation_id_duplicates")
    ):
        raise MarkerError("Neo4j marker count가 잘못됐습니다")
    if status in SUCCESS_STATUSES and payload.get(
        "expected_graph_fingerprint_sha256"
    ) != payload.get("actual_graph_fingerprint_sha256"):
        raise MarkerError("success marker의 expected/actual fingerprint가 다릅니다")
    if status == "RESTORED" and payload.get(
        "actual_graph_fingerprint_sha256"
    ) != payload.get("post_restore_graph_fingerprint_sha256"):
        raise MarkerError("RESTORED marker의 actual/post fingerprint가 다릅니다")
    if "approval_ref" in payload:
        try:
            validate_approval_ref(payload["approval_ref"])
        except EvidenceError as exc:
            raise MarkerError(str(exc)) from exc


def marker_is_readiness_success(payload: Mapping[str, Any]) -> bool:
    try:
        validate_marker(payload)
    except MarkerError:
        return False
    return payload["status"] in SUCCESS_STATUSES


def validate_marker_for_context(
    payload: Mapping[str, Any], context: LoaderContext
) -> None:
    validate_marker(payload)
    expected = {
        "database": context.target.database,
        "target_fingerprint_sha256": context.target.target_fingerprint_sha256,
        "corrected_manifest_sha256": context.corrected_manifest_sha256,
        "corrected_cypher_sha256": context.manifest["corrected_cypher_sha256"],
        "expected_graph_fingerprint_sha256": context.manifest[
            "expected_graph_fingerprint_sha256"
        ],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise MarkerError("Neo4j marker가 현재 target/artifact 계약과 다릅니다")


def load_marker(database: str, *, root: Path = MARKER_ROOT) -> dict[str, Any] | None:
    path = marker_path(database, root=root)
    if not path.exists():
        return None
    payload = _read_json(path, error_type=MarkerError)
    validate_marker(payload)
    return payload


def save_marker(
    payload: Mapping[str, Any],
    *,
    root: Path = MARKER_ROOT,
    allow_replace: bool = False,
) -> None:
    validate_marker(payload)
    path = marker_path(str(payload["database"]), root=root)
    lock_path = root / f".neo4j_graph.{payload['database']}.lock"
    with exclusive_file_lock(lock_path):
        if path.exists() and not allow_replace:
            existing = _read_json(path, error_type=MarkerError)
            if existing != payload:
                raise MarkerError("기존 Neo4j marker와 새 기록이 충돌합니다")
        try:
            atomic_write_bytes(path, canonical_json_bytes(payload) + b"\n")
        except BackupError as exc:
            raise MarkerError("Neo4j marker를 저장할 수 없습니다") from exc


def build_marker(
    context: LoaderContext,
    snapshot: GraphSnapshot,
    status: str,
    *,
    now: datetime | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in ALL_MARKER_STATUSES:
        raise MarkerError("Neo4j marker status가 잘못됐습니다")
    moment = utc_text(now)
    actual = snapshot_fingerprint(snapshot)
    payload: dict[str, Any] = {
        "dataset_epoch": DATASET_EPOCH,
        "database": context.target.database,
        "target_fingerprint_sha256": context.target.target_fingerprint_sha256,
        "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "source_member_sha256": SOURCE_MEMBER_SHA256,
        "corrected_manifest_sha256": context.corrected_manifest_sha256,
        "corrected_cypher_sha256": context.manifest["corrected_cypher_sha256"],
        "expected_graph_fingerprint_sha256": context.manifest[
            "expected_graph_fingerprint_sha256"
        ],
        "actual_graph_fingerprint_sha256": actual,
        "relation_id_algorithm_version": RELATION_ID_ALGORITHM_VERSION,
        "business_key_contract_version": BUSINESS_KEY_CONTRACT_VERSION,
        "status": status,
        "recorded_at": moment,
        "node_count": snapshot.node_count,
        "relationship_count": snapshot.relationship_count,
        "relation_id_duplicates": snapshot.relation_id_duplicates,
    }
    timestamp_key = {
        "APPLIED": "applied_at",
        "REPLACED": "applied_at",
        "ADOPTED_EXISTING": "adopted_at",
        "RESTORED": "restored_at",
    }.get(status)
    if timestamp_key:
        payload[timestamp_key] = moment
    if extra:
        payload.update(extra)
    validate_marker(payload)
    return payload


def _backup_payload(
    snapshot: GraphSnapshot,
    target: Neo4jBootstrapTarget,
    *,
    schema_fingerprint_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not SHA256_PATTERN.fullmatch(schema_fingerprint_sha256):
        raise BackupError("schema fingerprint 형식이 잘못됐습니다")
    payload = snapshot_payload(snapshot)
    return {
        "artifact_type": "neo4j_logical_backup",
        "format_version": 2,
        "backup_schema_version": BACKUP_SCHEMA_VERSION,
        "database": target.database,
        "target_fingerprint_sha256": target.target_fingerprint_sha256,
        "schema_fingerprint_sha256": schema_fingerprint_sha256,
        "created_at": utc_text(now),
        "nodes": payload["nodes"],
        "relationships": payload["relationships"],
    }


def snapshot_from_backup(payload: Mapping[str, Any]) -> GraphSnapshot:
    if set(payload) != BACKUP_FILE_KEYS:
        raise BackupError("backup file key 집합이 잘못됐습니다")
    if (
        payload.get("artifact_type") != "neo4j_logical_backup"
        or payload.get("format_version") != 2
        or payload.get("backup_schema_version") != BACKUP_SCHEMA_VERSION
    ):
        raise BackupError("backup file metadata가 잘못됐습니다")
    try:
        _parse_utc(payload.get("created_at"), field="created_at")
    except EvidenceError as exc:
        raise BackupError(str(exc)) from exc
    validate_database_name(str(payload.get("database", "")))
    _validate_sha(payload, "target_fingerprint_sha256")
    _validate_sha(payload, "schema_fingerprint_sha256")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise BackupError("backup node 목록이 잘못됐습니다")
    node_rows: list[dict[str, Any]] = []
    identity_to_node: dict[tuple[str, str], Mapping[str, Any]] = {}
    node_keys = {"label", "business_id", "properties"}
    for item in nodes:
        if not isinstance(item, Mapping) or set(item) != node_keys:
            raise BackupError("backup node schema가 잘못됐습니다")
        label = item.get("label")
        business_id = item.get("business_id")
        if not isinstance(label, str) or not isinstance(business_id, str):
            raise BackupError("backup node identity가 잘못됐습니다")
        identity = (label, business_id)
        if identity in identity_to_node:
            raise BackupError("backup node identity가 중복됐습니다")
        identity_to_node[identity] = item
        node_rows.append({"labels": [label], "properties": item["properties"]})
    relationship_rows: list[dict[str, Any]] = []
    relationships = payload.get("relationships")
    if not isinstance(relationships, list):
        raise BackupError("backup relationship 목록이 잘못됐습니다")
    try:
        for item in relationships:
            if not isinstance(item, Mapping):
                raise BackupError("backup relationship schema가 잘못됐습니다")
            base_keys = {
                "type",
                "from_label",
                "from_business_id",
                "to_label",
                "to_business_id",
                "properties",
            }
            properties = item.get("properties")
            if not isinstance(properties, Mapping):
                raise BackupError("backup relationship property가 잘못됐습니다")
            expected_keys = base_keys | (
                {"relation_id"} if "relation_id" in properties else set()
            )
            if set(item) != expected_keys:
                raise BackupError("backup relationship key 집합이 잘못됐습니다")
            if "relation_id" in item and item["relation_id"] != properties.get(
                "relation_id"
            ):
                raise BackupError("backup relationship ID가 서로 다릅니다")
            from_item = identity_to_node[(item["from_label"], item["from_business_id"])]
            to_item = identity_to_node[(item["to_label"], item["to_business_id"])]
            relationship_rows.append(
                {
                    "from_labels": [from_item["label"]],
                    "from_properties": from_item["properties"],
                    "relationship_type": item["type"],
                    "relationship_properties": item["properties"],
                    "to_labels": [to_item["label"]],
                    "to_properties": to_item["properties"],
                }
            )
    except (KeyError, TypeError) as exc:
        raise BackupError("backup endpoint mapping이 잘못됐습니다") from exc
    try:
        return snapshot_from_rows(node_rows, relationship_rows)
    except GraphStateError as exc:
        raise BackupError(str(exc)) from exc


def _backup_file_for_manifest(manifest_path: Path) -> Path:
    suffix = ".manifest.json"
    if not manifest_path.name.endswith(suffix):
        raise BackupError("backup manifest 파일명은 .manifest.json으로 끝나야 합니다")
    return manifest_path.with_name(manifest_path.name[: -len(suffix)] + ".json")


def create_backup_artifacts(
    snapshot: GraphSnapshot,
    target: Neo4jBootstrapTarget,
    *,
    schema_fingerprint_sha256: str,
    backup_root: Path,
    now: datetime | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    moment = now or utc_now()
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = backup_root / "backups"
    backup_path = directory / f"neo4j_graph.{target.database}.{stamp}.json"
    manifest_path = directory / f"neo4j_graph.{target.database}.{stamp}.manifest.json"
    backup_payload = _backup_payload(
        snapshot,
        target,
        schema_fingerprint_sha256=schema_fingerprint_sha256,
        now=moment,
    )
    backup_bytes = canonical_json_bytes(backup_payload) + b"\n"
    backup_file_sha = sha256_bytes(backup_bytes)
    backup_graph_sha = snapshot_fingerprint(snapshot)
    manifest = {
        "artifact_type": "neo4j_backup_manifest",
        "format_version": 2,
        "database": target.database,
        "target_fingerprint_sha256": target.target_fingerprint_sha256,
        "backup_file_sha256": backup_file_sha,
        "backup_graph_fingerprint_sha256": backup_graph_sha,
        "schema_fingerprint_sha256": schema_fingerprint_sha256,
        "backup_schema_version": BACKUP_SCHEMA_VERSION,
        "node_count": snapshot.node_count,
        "relationship_count": snapshot.relationship_count,
        "recorded_at": utc_text(moment),
    }
    validate_receipt(manifest, "neo4j_backup_manifest")
    validate_backup_path(backup_path, backup_root)
    validate_backup_path(manifest_path, backup_root)
    with exclusive_file_lock(backup_root.resolve() / ".neo4j-bootstrap.lock"):
        if backup_path.exists() or manifest_path.exists():
            raise BackupError("같은 시각의 Neo4j backup artifact가 이미 존재합니다")
        atomic_write_bytes(backup_path, backup_bytes)
        atomic_write_bytes(manifest_path, canonical_json_bytes(manifest) + b"\n")
    return backup_path, manifest_path, manifest


def load_backup_bundle(
    manifest_path: Path,
    backup_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], GraphSnapshot, Path]:
    manifest_path = validate_backup_path(manifest_path, backup_root)
    manifest = _read_json(manifest_path, error_type=BackupError)
    try:
        validate_receipt(manifest, "neo4j_backup_manifest")
    except EvidenceError as exc:
        raise BackupError(str(exc)) from exc
    backup_path = validate_backup_path(
        _backup_file_for_manifest(manifest_path), backup_root
    )
    backup_payload = _read_json(backup_path, error_type=BackupError)
    if (
        backup_payload.get("database") != manifest["database"]
        or backup_payload.get("target_fingerprint_sha256")
        != manifest["target_fingerprint_sha256"]
        or backup_payload.get("schema_fingerprint_sha256")
        != manifest["schema_fingerprint_sha256"]
    ):
        raise BackupError("backup file과 manifest target이 다릅니다")
    backup_bytes = canonical_json_bytes(backup_payload) + b"\n"
    if sha256_bytes(backup_bytes) != manifest["backup_file_sha256"]:
        raise BackupError("backup file SHA-256이 manifest와 다릅니다")
    snapshot = snapshot_from_backup(backup_payload)
    if snapshot_fingerprint(snapshot) != manifest["backup_graph_fingerprint_sha256"]:
        raise BackupError("backup graph fingerprint가 manifest와 다릅니다")
    if (
        snapshot.node_count != manifest["node_count"]
        or snapshot.relationship_count != manifest["relationship_count"]
    ):
        raise BackupError("backup graph count가 manifest와 다릅니다")
    return manifest, backup_payload, snapshot, backup_path


def build_preflight_receipt(
    context: LoaderContext,
    snapshot: GraphSnapshot,
    *,
    schema_fingerprint_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not SHA256_PATTERN.fullmatch(schema_fingerprint_sha256):
        raise EvidenceError("schema fingerprint 형식이 잘못됐습니다")
    payload = {
        "artifact_type": "neo4j_preflight_receipt",
        "format_version": 2,
        "database": context.target.database,
        "target_fingerprint_sha256": context.target.target_fingerprint_sha256,
        "existing_graph_fingerprint_sha256": snapshot_fingerprint(snapshot),
        "schema_fingerprint_sha256": schema_fingerprint_sha256,
        "node_count": snapshot.node_count,
        "relationship_count": snapshot.relationship_count,
        "source_member_sha256": SOURCE_MEMBER_SHA256,
        "corrected_cypher_sha256": context.manifest["corrected_cypher_sha256"],
        "recorded_at": utc_text(now),
    }
    validate_receipt(payload, "neo4j_preflight_receipt")
    return payload


def build_restore_receipt(
    manifest_path: Path,
    backup_root: Path,
    target: Neo4jBootstrapTarget,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    manifest, _, _, _ = load_backup_bundle(manifest_path, backup_root)
    if (
        manifest["database"] != target.database
        or manifest["target_fingerprint_sha256"] != target.target_fingerprint_sha256
    ):
        raise EvidenceError("backup manifest target이 현재 target과 다릅니다")
    payload = {
        "artifact_type": "neo4j_restore_verification_receipt",
        "format_version": 2,
        "database": target.database,
        "target_fingerprint_sha256": target.target_fingerprint_sha256,
        "backup_manifest_sha256": sha256_file(manifest_path),
        "backup_file_sha256": manifest["backup_file_sha256"],
        "backup_graph_fingerprint_sha256": manifest["backup_graph_fingerprint_sha256"],
        "schema_fingerprint_sha256": manifest["schema_fingerprint_sha256"],
        "restore_algorithm_version": RESTORE_ALGORITHM_VERSION,
        "verified_at": utc_text(now),
    }
    validate_receipt(payload, "neo4j_restore_verification_receipt")
    return payload


def validate_replace_evidence(
    context: LoaderContext,
    *,
    expected_existing_fingerprint: str,
    preflight_path: Path,
    backup_manifest_path: Path,
    restore_receipt_path: Path,
    approval_ref: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_approval_ref(approval_ref)
    backup_root = Path(context.target.backup_root)
    paths = [preflight_path, backup_manifest_path, restore_receipt_path]
    for path in paths:
        validate_backup_path(path, backup_root)
    preflight = _read_json(preflight_path)
    backup_manifest = _read_json(backup_manifest_path)
    restore_receipt = _read_json(restore_receipt_path)
    validate_receipt(preflight, "neo4j_preflight_receipt")
    validate_receipt(backup_manifest, "neo4j_backup_manifest")
    validate_receipt(restore_receipt, "neo4j_restore_verification_receipt")
    reference_now = (now or utc_now()).astimezone(UTC)
    recorded = _parse_utc(preflight["recorded_at"], field="recorded_at")
    if reference_now < recorded or reference_now - recorded > PREFLIGHT_TTL:
        raise EvidenceError("preflight receipt TTL 24시간이 지났습니다")
    if not SHA256_PATTERN.fullmatch(expected_existing_fingerprint):
        raise EvidenceError("expected existing fingerprint 형식이 잘못됐습니다")
    fixed_pairs = (
        (preflight["database"], context.target.database),
        (
            preflight["target_fingerprint_sha256"],
            context.target.target_fingerprint_sha256,
        ),
        (
            preflight["existing_graph_fingerprint_sha256"],
            expected_existing_fingerprint,
        ),
        (preflight["source_member_sha256"], SOURCE_MEMBER_SHA256),
        (
            preflight["corrected_cypher_sha256"],
            context.manifest["corrected_cypher_sha256"],
        ),
        (backup_manifest["database"], context.target.database),
        (
            backup_manifest["target_fingerprint_sha256"],
            context.target.target_fingerprint_sha256,
        ),
        (
            backup_manifest["backup_graph_fingerprint_sha256"],
            expected_existing_fingerprint,
        ),
        (
            backup_manifest["schema_fingerprint_sha256"],
            preflight["schema_fingerprint_sha256"],
        ),
        (backup_manifest["node_count"], preflight["node_count"]),
        (
            backup_manifest["relationship_count"],
            preflight["relationship_count"],
        ),
        (restore_receipt["database"], context.target.database),
        (
            restore_receipt["target_fingerprint_sha256"],
            context.target.target_fingerprint_sha256,
        ),
        (
            restore_receipt["backup_manifest_sha256"],
            sha256_file(backup_manifest_path),
        ),
        (
            restore_receipt["backup_file_sha256"],
            backup_manifest["backup_file_sha256"],
        ),
        (
            restore_receipt["backup_graph_fingerprint_sha256"],
            backup_manifest["backup_graph_fingerprint_sha256"],
        ),
        (
            restore_receipt["schema_fingerprint_sha256"],
            backup_manifest["schema_fingerprint_sha256"],
        ),
    )
    if any(actual != expected for actual, expected in fixed_pairs):
        raise EvidenceError("replace evidence 교차 검증값이 다릅니다")
    load_backup_bundle(backup_manifest_path, backup_root)
    return {
        "backup_file_sha256": backup_manifest["backup_file_sha256"],
        "backup_graph_fingerprint_sha256": backup_manifest[
            "backup_graph_fingerprint_sha256"
        ],
        "schema_fingerprint_sha256": backup_manifest["schema_fingerprint_sha256"],
        "approval_ref": approval_ref,
        "preflight_receipt_sha256": sha256_file(preflight_path),
        "backup_manifest_sha256": sha256_file(backup_manifest_path),
        "restore_receipt_sha256": sha256_file(restore_receipt_path),
    }


def _driver_for(target: Neo4jBootstrapTarget) -> Any:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise Neo4jBootstrapError("Neo4j driver가 설치되지 않았습니다") from exc
    return GraphDatabase.driver(
        target.uri,
        auth=(target.username, target.password),
        connection_timeout=5,
        connection_acquisition_timeout=5,
        max_transaction_retry_time=0,
    )


def _execute_read(session: Any, callback: Callable[[Any], Any]) -> Any:
    if hasattr(session, "execute_read"):
        return session.execute_read(callback)
    return callback(session)


def _execute_write(session: Any, callback: Callable[[Any], Any]) -> Any:
    if hasattr(session, "execute_write"):
        return session.execute_write(callback)
    return callback(session)


def _open_session(driver: Any, database: str, *, read_only: bool = False) -> Any:
    options: dict[str, Any] = {"database": database}
    if read_only:
        options["default_access_mode"] = "READ"
    return driver.session(**options)


def read_current_snapshot(
    target: Neo4jBootstrapTarget,
    *,
    driver_factory: Callable[[Neo4jBootstrapTarget], Any] = _driver_for,
    require_supported_schema: bool = False,
) -> GraphSnapshot:
    snapshot, _ = read_current_state(
        target,
        driver_factory=driver_factory,
        require_supported_schema=require_supported_schema,
    )
    return snapshot


def read_current_state(
    target: Neo4jBootstrapTarget,
    *,
    driver_factory: Callable[[Neo4jBootstrapTarget], Any] = _driver_for,
    require_supported_schema: bool = True,
) -> tuple[GraphSnapshot, str | None]:
    driver = driver_factory(target)
    try:
        with _open_session(driver, target.database, read_only=True) as session:
            validate_connected_database(session, target.database)

            def read(tx: Any) -> tuple[GraphSnapshot, str | None]:
                schema_fingerprint = (
                    validate_supported_schema(tx) if require_supported_schema else None
                )
                return inspect_graph(tx), schema_fingerprint

            return _execute_read(session, read)
    finally:
        driver.close()


def _run_seed(tx: Any, parsed: ParsedMasterCypher) -> None:
    for statement in parsed.corrected_statements:
        tx.run(statement)


def _adopt_relationships(tx: Any, parsed: ParsedMasterCypher) -> None:
    for index, relation in enumerate(parsed.relationships):
        from_keys = SNAPSHOT_BUSINESS_KEYS[relation.from_node.label]
        to_keys = SNAPSHOT_BUSINESS_KEYS[relation.to_node.label]
        from_pattern = ", ".join(f"{key}: $f_{key}" for key in from_keys)
        to_pattern = ", ".join(f"{key}: $t_{key}" for key in to_keys)
        query = (
            f"MATCH (a:{relation.from_node.label} {{{from_pattern}}})-"
            f"[r:{relation.relation_type}]->"
            f"(b:{relation.to_node.label} {{{to_pattern}}}) "
            "WHERE r.relation_id IS NULL SET r.relation_id = $relation_id "
            "RETURN count(r) AS updated"
        )
        params = {
            **{f"f_{key}": relation.from_node.properties[key] for key in from_keys},
            **{f"t_{key}": relation.to_node.properties[key] for key in to_keys},
            "relation_id": relation.relation_id,
        }
        rows = _rows(tx.run(query, **params))
        if len(rows) != 1 or rows[0].get("updated") != 1:
            raise GraphStateError(f"relationship backfill 결과가 잘못됐습니다: {index}")


def _restore_snapshot(tx: Any, snapshot: GraphSnapshot) -> None:
    for node in sorted(snapshot.nodes, key=lambda item: (item.label, item.business_id)):
        tx.run(
            f"CREATE (n:{node.label}) SET n = $properties",
            properties=node.properties,
        )
    for relation in sorted(
        snapshot.relationships,
        key=lambda item: (
            item.relation_type,
            item.from_node.business_id,
            item.to_node.business_id,
        ),
    ):
        from_keys = SNAPSHOT_BUSINESS_KEYS[relation.from_node.label]
        to_keys = SNAPSHOT_BUSINESS_KEYS[relation.to_node.label]
        from_pattern = ", ".join(f"{key}: $f_{key}" for key in from_keys)
        to_pattern = ", ".join(f"{key}: $t_{key}" for key in to_keys)
        query = (
            f"MATCH (a:{relation.from_node.label} {{{from_pattern}}}), "
            f"(b:{relation.to_node.label} {{{to_pattern}}}) "
            f"CREATE (a)-[r:{relation.relation_type}]->(b) "
            "SET r = $properties"
        )
        tx.run(
            query,
            **{
                **{f"f_{key}": relation.from_node.properties[key] for key in from_keys},
                **{f"t_{key}": relation.to_node.properties[key] for key in to_keys},
                "properties": relation.properties,
            },
        )


def mutate_graph(
    context: LoaderContext,
    mode: str,
    *,
    expected_existing_fingerprint: str | None = None,
    expected_schema_fingerprint: str | None = None,
    restore_snapshot: GraphSnapshot | None = None,
    driver_factory: Callable[[Neo4jBootstrapTarget], Any] = _driver_for,
) -> GraphSnapshot:
    driver = driver_factory(context.target)
    try:
        with _open_session(driver, context.target.database) as session:
            validate_connected_database(session, context.target.database)

            def mutate(tx: Any) -> GraphSnapshot:
                validate_connected_database(tx, context.target.database)
                current = inspect_graph(tx)
                if mode == "apply-empty":
                    if current.node_count or current.relationship_count:
                        raise GraphStateError(
                            "empty graph에서만 apply-empty를 허용합니다"
                        )
                    _run_seed(tx, context.parsed)
                elif mode == "adopt-existing":
                    if snapshot_fingerprint(current, legacy=True) != context.manifest[
                        "expected_legacy_fingerprint_sha256"
                    ] or any(
                        relation.relation_id is not None
                        for relation in current.relationships
                    ):
                        raise GraphStateError(
                            "legacy expected graph만 adopt할 수 있습니다"
                        )
                    _adopt_relationships(tx, context.parsed)
                elif mode == "replace":
                    actual_schema_fingerprint = validate_supported_schema(tx)
                    if actual_schema_fingerprint != expected_schema_fingerprint:
                        raise GraphStateError(
                            "transaction 시점 schema fingerprint가 다릅니다"
                        )
                    if snapshot_fingerprint(current) != expected_existing_fingerprint:
                        raise GraphStateError(
                            "transaction 시점 graph fingerprint가 다릅니다"
                        )
                    tx.run(DELETE_QUERY)
                    _run_seed(tx, context.parsed)
                elif mode == "restore-backup":
                    actual_schema_fingerprint = validate_supported_schema(tx)
                    if actual_schema_fingerprint != expected_schema_fingerprint:
                        raise GraphStateError(
                            "transaction 시점 schema fingerprint가 다릅니다"
                        )
                    if snapshot_fingerprint(current) != expected_existing_fingerprint:
                        raise GraphStateError(
                            "transaction 시점 graph fingerprint가 다릅니다"
                        )
                    if restore_snapshot is None:
                        raise BackupError("restore할 snapshot이 없습니다")
                    tx.run(DELETE_QUERY)
                    _restore_snapshot(tx, restore_snapshot)
                else:  # pragma: no cover - caller validates modes
                    raise Neo4jBootstrapError("지원하지 않는 mutation mode입니다")
                actual = inspect_graph(tx)
                expected = (
                    snapshot_fingerprint(restore_snapshot)
                    if mode == "restore-backup" and restore_snapshot is not None
                    else context.manifest["expected_graph_fingerprint_sha256"]
                )
                if snapshot_fingerprint(actual) != expected:
                    raise GraphStateError(
                        "mutation 후 graph fingerprint 검증에 실패했습니다"
                    )
                return actual

            return _execute_write(session, mutate)
    finally:
        driver.close()


def _resolve_archive(value: str | None, environ: Mapping[str, str]) -> Path:
    if value:
        return Path(value).expanduser()
    package = environ.get("MENTOR_PACKAGE_DIR", "").strip()
    if not package:
        raise Neo4jBootstrapError("--archive 또는 MENTOR_PACKAGE_DIR가 필요합니다")
    path = Path(package).expanduser()
    if path.is_dir():
        path = path / "kosa_0813.zip"
    return path


def load_context(
    archive_path: Path,
    database: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> LoaderContext:
    source = os.environ if environ is None else environ
    parsed = parse_registered_archive(archive_path)
    manifest = validate_generated_artifacts(parsed)
    target = load_neo4j_bootstrap_target(database, environ=source)
    return LoaderContext(
        target=target,
        parsed=parsed,
        manifest=manifest,
        corrected_manifest_sha256=graph_manifest_sha256(manifest),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database")
    parser.add_argument("--confirm-target")
    parser.add_argument("--archive")
    modes = parser.add_mutually_exclusive_group()
    for name in (
        "dry-run",
        "preflight",
        "apply-empty",
        "adopt-existing",
        "recover-marker",
        "backup",
        "verify-backup",
        "replace",
        "restore-backup",
    ):
        modes.add_argument(f"--{name}", action="store_true")
    parser.add_argument("--receipt-out")
    parser.add_argument("--expected-existing-fingerprint")
    parser.add_argument("--expected-current-fingerprint")
    parser.add_argument("--preflight-receipt")
    parser.add_argument("--backup-manifest")
    parser.add_argument("--restore-receipt")
    parser.add_argument("--approval-ref")
    return parser


def resolve_mode(args: argparse.Namespace) -> str:
    modes = [
        name
        for name in (
            "dry_run",
            "preflight",
            "apply_empty",
            "adopt_existing",
            "recover_marker",
            "backup",
            "verify_backup",
            "replace",
            "restore_backup",
        )
        if getattr(args, name)
    ]
    if not modes:
        raise Neo4jBootstrapError(
            "접속하지 않았습니다. 명시적인 실행 모드가 필요합니다"
        )
    if len(modes) != 1:
        raise Neo4jBootstrapError("실행 모드는 하나만 선택해야 합니다")
    mode = modes[0].replace("_", "-")
    if mode == "verify-backup":
        if not args.backup_manifest or not args.receipt_out:
            raise Neo4jBootstrapError("verify-backup 필수 인자가 없습니다")
        return mode
    if not args.database:
        raise Neo4jBootstrapError("이 모드는 --database가 필요합니다")
    validate_database_name(args.database)
    if mode != "dry-run":
        if args.confirm_target != args.database:
            raise Neo4jBootstrapError("--confirm-target과 --database가 다릅니다")
    if mode in {"preflight"} and not args.receipt_out:
        raise Neo4jBootstrapError("preflight는 --receipt-out이 필요합니다")
    if mode == "adopt-existing":
        validate_approval_ref(args.approval_ref)
    if mode == "replace":
        required = (
            args.expected_existing_fingerprint,
            args.preflight_receipt,
            args.backup_manifest,
            args.restore_receipt,
            args.approval_ref,
        )
        if not all(required):
            raise Neo4jBootstrapError("replace 증거 인자가 모두 필요합니다")
        validate_approval_ref(args.approval_ref)
    if mode == "restore-backup":
        required = (
            args.backup_manifest,
            args.restore_receipt,
            args.expected_current_fingerprint,
            args.approval_ref,
        )
        if not all(required):
            raise Neo4jBootstrapError("restore-backup 증거 인자가 모두 필요합니다")
        validate_approval_ref(args.approval_ref)
    return mode


def _context_for_verify_backup(
    args: argparse.Namespace, environ: Mapping[str, str]
) -> tuple[Neo4jBootstrapTarget, Path, Path]:
    backup_root_raw = environ.get("NEO4J_BOOTSTRAP_BACKUP_ROOT", "").strip()
    if not backup_root_raw:
        raise Neo4jTargetError(
            "Neo4j bootstrap 설정이 비어 있습니다: NEO4J_BOOTSTRAP_BACKUP_ROOT"
        )
    backup_root = Path(backup_root_raw)
    manifest_path = validate_backup_path(Path(args.backup_manifest), backup_root)
    manifest = _read_json(manifest_path, error_type=BackupError)
    database = str(manifest.get("database", ""))
    target = load_neo4j_bootstrap_target(database, environ=environ)
    receipt_out = validate_backup_path(Path(args.receipt_out), backup_root)
    return target, manifest_path, receipt_out


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    driver_factory: Callable[[Neo4jBootstrapTarget], Any] = _driver_for,
    marker_root: Path = MARKER_ROOT,
    now: datetime | None = None,
) -> int:
    args = _parser().parse_args(argv)
    mode = resolve_mode(args)
    source = os.environ if environ is None else environ

    if mode == "verify-backup":
        target, manifest_path, receipt_out = _context_for_verify_backup(args, source)
        backup_root = Path(target.backup_root)
        receipt = build_restore_receipt(manifest_path, backup_root, target, now=now)
        save_external_artifact(receipt_out, receipt, backup_root)
        print(
            f"BACKUP_VERIFIED database={target.database} "
            f"target_fingerprint_sha256={target.target_fingerprint_sha256}"
        )
        return 0

    archive_path = _resolve_archive(args.archive, source)
    context = load_context(archive_path, args.database, environ=source)
    backup_root = Path(context.target.backup_root)
    if mode == "dry-run":
        print(
            f"DRY_RUN_OK database={context.target.database} nodes=38 relationships=81 "
            f"target_fingerprint_sha256={context.target.target_fingerprint_sha256}"
        )
        return 0

    if mode == "preflight":
        snapshot, schema_fingerprint = read_current_state(
            context.target, driver_factory=driver_factory, require_supported_schema=True
        )
        assert schema_fingerprint is not None
        receipt = build_preflight_receipt(
            context,
            snapshot,
            schema_fingerprint_sha256=schema_fingerprint,
            now=now,
        )
        output = validate_backup_path(Path(args.receipt_out), backup_root)
        save_external_artifact(output, receipt, backup_root)
        local_marker = load_marker(context.target.database, root=marker_root)
        if local_marker is not None:
            validate_marker_for_context(local_marker, context)
        state = graph_state(
            snapshot,
            context.manifest,
            marker=local_marker,
        )
        print(
            f"PREFLIGHT_OK database={context.target.database} "
            f"state={state} "
            f"target_fingerprint_sha256={context.target.target_fingerprint_sha256}"
        )
        return 0

    if mode == "backup":
        snapshot, schema_fingerprint = read_current_state(
            context.target, driver_factory=driver_factory, require_supported_schema=True
        )
        assert schema_fingerprint is not None
        backup_path, manifest_path, _ = create_backup_artifacts(
            snapshot,
            context.target,
            schema_fingerprint_sha256=schema_fingerprint,
            backup_root=backup_root,
            now=now,
        )
        print(
            f"BACKUP_OK database={context.target.database} "
            f"backup_file={backup_path.name} backup_manifest={manifest_path.name} "
            f"target_fingerprint_sha256={context.target.target_fingerprint_sha256}"
        )
        return 0

    current_marker = load_marker(context.target.database, root=marker_root)
    if current_marker is not None:
        validate_marker_for_context(current_marker, context)
    if mode == "recover-marker":
        snapshot = read_current_snapshot(context.target, driver_factory=driver_factory)
        if (
            snapshot_fingerprint(snapshot)
            != context.manifest["expected_graph_fingerprint_sha256"]
        ):
            raise GraphStateError("expected graph에서만 marker를 복구할 수 있습니다")
        if current_marker is not None and current_marker.get("status") != "RESTORED":
            raise MarkerError("정상 Neo4j marker가 이미 존재합니다")
        marker = build_marker(context, snapshot, "VERIFIED_EXISTING", now=now)
        save_marker(marker, root=marker_root, allow_replace=current_marker is not None)
        print(f"NEO4J_OK database={context.target.database} status=VERIFIED_EXISTING")
        return 0

    if mode == "apply-empty":
        if current_marker is not None:
            raise MarkerError("marker가 있는 target에는 apply-empty를 할 수 없습니다")
        snapshot = mutate_graph(context, mode, driver_factory=driver_factory)
        marker = build_marker(context, snapshot, "APPLIED", now=now)
        save_marker(marker, root=marker_root)
        print(f"NEO4J_OK database={context.target.database} status=APPLIED")
        return 0

    if mode == "adopt-existing":
        if current_marker is not None:
            raise MarkerError(
                "marker가 있는 target에는 adopt-existing을 할 수 없습니다"
            )
        snapshot = mutate_graph(context, mode, driver_factory=driver_factory)
        marker = build_marker(
            context,
            snapshot,
            "ADOPTED_EXISTING",
            now=now,
            extra={"approval_ref": args.approval_ref},
        )
        save_marker(marker, root=marker_root)
        print(f"NEO4J_OK database={context.target.database} status=ADOPTED_EXISTING")
        return 0

    if mode == "replace":
        evidence = validate_replace_evidence(
            context,
            expected_existing_fingerprint=args.expected_existing_fingerprint,
            preflight_path=Path(args.preflight_receipt),
            backup_manifest_path=Path(args.backup_manifest),
            restore_receipt_path=Path(args.restore_receipt),
            approval_ref=args.approval_ref,
            now=now,
        )
        snapshot = mutate_graph(
            context,
            mode,
            expected_existing_fingerprint=args.expected_existing_fingerprint,
            expected_schema_fingerprint=evidence["schema_fingerprint_sha256"],
            driver_factory=driver_factory,
        )
        marker = build_marker(context, snapshot, "REPLACED", now=now, extra=evidence)
        save_marker(marker, root=marker_root, allow_replace=True)
        print(f"NEO4J_OK database={context.target.database} status=REPLACED")
        return 0

    if mode == "restore-backup":
        validate_approval_ref(args.approval_ref)
        manifest_path = validate_backup_path(Path(args.backup_manifest), backup_root)
        restore_path = validate_backup_path(Path(args.restore_receipt), backup_root)
        backup_manifest, _, backup_snapshot, _ = load_backup_bundle(
            manifest_path, backup_root
        )
        if (
            backup_manifest["database"] != context.target.database
            or backup_manifest["target_fingerprint_sha256"]
            != context.target.target_fingerprint_sha256
        ):
            raise EvidenceError("backup manifest target이 현재 target과 다릅니다")
        receipt = _read_json(restore_path)
        validate_receipt(receipt, "neo4j_restore_verification_receipt")
        expected_pairs = (
            (receipt["database"], context.target.database),
            (
                receipt["target_fingerprint_sha256"],
                context.target.target_fingerprint_sha256,
            ),
            (receipt["backup_manifest_sha256"], sha256_file(manifest_path)),
            (
                receipt["backup_file_sha256"],
                backup_manifest["backup_file_sha256"],
            ),
            (
                receipt["backup_graph_fingerprint_sha256"],
                backup_manifest["backup_graph_fingerprint_sha256"],
            ),
            (
                receipt["schema_fingerprint_sha256"],
                backup_manifest["schema_fingerprint_sha256"],
            ),
        )
        if any(actual != expected for actual, expected in expected_pairs):
            raise EvidenceError("restore evidence 교차 검증값이 다릅니다")
        if not isinstance(
            args.expected_current_fingerprint, str
        ) or not SHA256_PATTERN.fullmatch(args.expected_current_fingerprint):
            raise EvidenceError("expected current fingerprint 형식이 잘못됐습니다")
        snapshot = mutate_graph(
            context,
            mode,
            expected_existing_fingerprint=args.expected_current_fingerprint,
            expected_schema_fingerprint=backup_manifest["schema_fingerprint_sha256"],
            restore_snapshot=backup_snapshot,
            driver_factory=driver_factory,
        )
        marker = build_marker(
            context,
            snapshot,
            "RESTORED",
            now=now,
            extra={
                "backup_file_sha256": backup_manifest["backup_file_sha256"],
                "backup_graph_fingerprint_sha256": backup_manifest[
                    "backup_graph_fingerprint_sha256"
                ],
                "schema_fingerprint_sha256": backup_manifest[
                    "schema_fingerprint_sha256"
                ],
                "backup_manifest_sha256": sha256_file(manifest_path),
                "restore_receipt_sha256": sha256_file(restore_path),
                "approval_ref": args.approval_ref,
                "pre_restore_graph_fingerprint_sha256": (
                    args.expected_current_fingerprint
                ),
                "post_restore_graph_fingerprint_sha256": snapshot_fingerprint(snapshot),
            },
        )
        save_marker(marker, root=marker_root, allow_replace=True)
        print(f"NEO4J_OK database={context.target.database} status=RESTORED")
        return 0

    raise Neo4jBootstrapError("지원하지 않는 mode입니다")


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    try:
        return run(argv)
    except (
        GraphManifestError,
        Neo4jBootstrapError,
        Neo4jTargetError,
    ) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 2)
    except Exception as exc:
        # Neo4j driver exceptions may contain URI, credentials or raw query text.
        if exc.__class__.__module__.startswith("neo4j"):
            print(
                "Neo4jConnectionError: Neo4j bootstrap 작업에 실패했습니다",
                file=sys.stderr,
            )
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
