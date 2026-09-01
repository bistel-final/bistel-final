"""Neo4j final graph marker와 live fingerprint의 runtime-safe 검증."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from neo4j import unit_of_work

DATASET_EPOCH = "fdc_final_20260818"
EXPECTED_NODE_COUNT = 44
EXPECTED_RELATIONSHIP_COUNT = 85
EXPECTED_LABEL_DISTRIBUTION = {
    "Area": 2,
    "Chamber": 12,
    "Equipment": 6,
    "EquipmentModel": 2,
    "Parameter": 8,
    "ProcessStep": 2,
    "Recipe": 4,
    "RecipeStep": 8,
}
EXPECTED_RELATIONSHIP_TYPE_DISTRIBUTION = {
    "IN_AREA": 4,
    "MEASURED_ON": 48,
    "NEXT_STEP": 1,
    "OF_MODEL": 6,
    "PART_OF": 12,
    "PERFORMS": 6,
    "STEP_OF": 8,
}
BUSINESS_KEYS: dict[str, tuple[str, ...]] = {
    "Area": ("area_id",),
    "Recipe": ("recipe_id",),
    "RecipeStep": ("recipe_id", "recipe_step_no"),
    "ProcessStep": ("step_id",),
    "EquipmentModel": ("model_code",),
    "Equipment": ("equipment_id",),
    "Chamber": ("chamber_id",),
    "Parameter": ("parameter_id",),
}
NODE_QUERY = """MATCH (n)
RETURN labels(n) AS labels, properties(n) AS properties
"""
RELATIONSHIP_QUERY = """MATCH (a)-[r]->(b)
RETURN labels(a) AS from_labels, properties(a) AS from_properties,
       type(r) AS relationship_type, properties(r) AS relationship_properties,
       labels(b) AS to_labels, properties(b) AS to_properties
"""


class GraphReadinessError(RuntimeError):
    """Neo4j marker 또는 live graph가 final 계약과 다르다."""


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


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        _normalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _escape_business_value(value: str) -> str:
    escaped = unicodedata.normalize("NFC", value).replace("\\", "\\\\")
    for delimiter in ("|", ":", "+", "="):
        escaped = escaped.replace(delimiter, f"\\{delimiter}")
    return escaped


def serialize_business_value(value: Any) -> str:
    if isinstance(value, bool):
        raise GraphReadinessError("bool business key는 허용되지 않습니다")
    if isinstance(value, int):
        return f"i:{value:d}"
    if isinstance(value, str):
        return f"s:{_escape_business_value(value)}"
    raise GraphReadinessError("business key type이 올바르지 않습니다")


@dataclass(frozen=True)
class SnapshotNode:
    label: str
    properties: dict[str, str | int | float]

    @property
    def business_id(self) -> str:
        keys = BUSINESS_KEYS.get(self.label)
        if keys is None or any(key not in self.properties for key in keys):
            raise GraphReadinessError("snapshot business key를 복원할 수 없습니다")
        return "+".join(
            f"{key}={serialize_business_value(self.properties[key])}"
            for key in sorted(keys)
        )


@dataclass(frozen=True)
class RelationshipSnapshot:
    relation_type: str
    from_node: SnapshotNode
    to_node: SnapshotNode
    properties: dict[str, str | int | float]

    @property
    def relation_id(self) -> str | None:
        value = self.properties.get("relation_id")
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class GraphSnapshot:
    nodes: tuple[SnapshotNode, ...]
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


def _record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    try:
        return dict(record.data())
    except (AttributeError, TypeError, ValueError) as exc:
        raise GraphReadinessError("Neo4j 응답 형식이 올바르지 않습니다") from exc


def _rows(result: Any) -> list[dict[str, Any]]:
    try:
        return [_record_to_dict(record) for record in result]
    except TypeError as exc:
        raise GraphReadinessError("Neo4j 결과를 순회할 수 없습니다") from exc


def _properties(value: Any) -> dict[str, str | int | float]:
    if not isinstance(value, Mapping):
        raise GraphReadinessError("Neo4j property payload가 object가 아닙니다")
    result: dict[str, str | int | float] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise GraphReadinessError("Neo4j property key가 문자열이 아닙니다")
        if isinstance(item, bool) or not isinstance(item, str | int | float):
            raise GraphReadinessError("지원하지 않는 Neo4j property type입니다")
        if isinstance(item, float) and not math.isfinite(item):
            raise GraphReadinessError("Neo4j property가 finite 값이 아닙니다")
        result[key] = item
    return result


def _label(value: Any) -> str:
    if not isinstance(value, list | tuple) or len(value) != 1:
        raise GraphReadinessError("Neo4j node label 수가 계약과 다릅니다")
    label = value[0]
    if not isinstance(label, str) or label not in BUSINESS_KEYS:
        raise GraphReadinessError("Neo4j node label이 allowlist 밖입니다")
    return label


def snapshot_from_rows(
    node_rows: Sequence[Mapping[str, Any]],
    relationship_rows: Sequence[Mapping[str, Any]],
) -> GraphSnapshot:
    nodes: list[SnapshotNode] = []
    identities: dict[tuple[str, str], SnapshotNode] = {}
    for row in node_rows:
        node = SnapshotNode(
            _label(row.get("labels")), _properties(row.get("properties"))
        )
        identity = (node.label, node.business_id)
        if identity in identities:
            raise GraphReadinessError("Neo4j business key가 중복됐습니다")
        identities[identity] = node
        nodes.append(node)

    relationships: list[RelationshipSnapshot] = []
    identities_seen: set[tuple[str, str, str, str, str]] = set()
    for row in relationship_rows:
        from_node = SnapshotNode(
            _label(row.get("from_labels")), _properties(row.get("from_properties"))
        )
        to_node = SnapshotNode(
            _label(row.get("to_labels")), _properties(row.get("to_properties"))
        )
        for endpoint in (from_node, to_node):
            identity = (endpoint.label, endpoint.business_id)
            if (
                identity not in identities
                or identities[identity].properties != endpoint.properties
            ):
                raise GraphReadinessError("relationship endpoint를 복원할 수 없습니다")
        relation_type = row.get("relationship_type")
        if (
            not isinstance(relation_type, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", relation_type) is None
        ):
            raise GraphReadinessError("relationship type 형식이 올바르지 않습니다")
        identity = (
            relation_type,
            from_node.label,
            from_node.business_id,
            to_node.label,
            to_node.business_id,
        )
        if identity in identities_seen:
            raise GraphReadinessError(
                "명시적 edge key 없는 병렬 relationship이 있습니다"
            )
        identities_seen.add(identity)
        relationships.append(
            RelationshipSnapshot(
                relation_type,
                identities[(from_node.label, from_node.business_id)],
                identities[(to_node.label, to_node.business_id)],
                _properties(row.get("relationship_properties")),
            )
        )
    return GraphSnapshot(tuple(nodes), tuple(relationships))


def snapshot_payload(snapshot: Any, *, legacy: bool = False) -> dict[str, Any]:
    """Bootstrap snapshot과 runtime snapshot 모두에 쓰는 canonical payload."""

    nodes = [
        {
            "label": node.label,
            "business_id": node.business_id,
            "properties": dict(sorted(node.properties.items())),
        }
        for node in snapshot.nodes
    ]
    relationships: list[dict[str, Any]] = []
    for relation in snapshot.relationships:
        properties = dict(sorted(relation.properties.items()))
        if legacy:
            properties.pop("relation_id", None)
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
        relationships.append(item)
    nodes.sort(key=lambda item: canonical_json_bytes(item).decode("utf-8"))
    relationships.sort(key=lambda item: canonical_json_bytes(item).decode("utf-8"))
    return {"nodes": nodes, "relationships": relationships}


def snapshot_fingerprint(snapshot: Any, *, legacy: bool = False) -> str:
    return canonical_sha256(snapshot_payload(snapshot, legacy=legacy))


def read_live_snapshot(
    driver: Any, *, database: str, timeout_seconds: float
) -> GraphSnapshot:
    with driver.session(database=database, default_access_mode="READ") as session:

        @unit_of_work(timeout=timeout_seconds)
        def read(tx: Any) -> GraphSnapshot:
            return snapshot_from_rows(
                _rows(tx.run(NODE_QUERY)),
                _rows(tx.run(RELATIONSHIP_QUERY)),
            )

        return session.execute_read(read)


def verify_graph_readiness(
    driver: Any,
    marker: Mapping[str, Any],
    *,
    database: str = "neo4j",
    timeout_seconds: float = 5.0,
) -> None:
    try:
        # config 필수 env를 요구하는 모듈이라 순수 수집 경계(bootstrap script
        # collection)에 끌어들이지 않도록 호출 시점에 import한다.
        from app.knowledge.graph_revision import validate_graph_marker

        validate_graph_marker(marker, database=database)
    except RuntimeError as exc:
        raise GraphReadinessError("Neo4j marker 계약이 다릅니다") from exc
    snapshot = read_live_snapshot(
        driver,
        database=database,
        timeout_seconds=timeout_seconds,
    )
    label_counts = dict(sorted(Counter(node.label for node in snapshot.nodes).items()))
    type_counts = dict(
        sorted(Counter(item.relation_type for item in snapshot.relationships).items())
    )
    relation_id_count = sum(
        item.relation_id is not None for item in snapshot.relationships
    )
    fingerprint = snapshot_fingerprint(snapshot)
    if (
        snapshot.node_count != EXPECTED_NODE_COUNT
        or snapshot.relationship_count != EXPECTED_RELATIONSHIP_COUNT
        or snapshot.relation_id_duplicates != 0
        or relation_id_count != EXPECTED_RELATIONSHIP_COUNT
        or label_counts != EXPECTED_LABEL_DISTRIBUTION
        or type_counts != EXPECTED_RELATIONSHIP_TYPE_DISTRIBUTION
        or marker.get("actual_graph_fingerprint_sha256") != fingerprint
    ):
        raise GraphReadinessError("Neo4j live graph가 marker/fingerprint와 다릅니다")
