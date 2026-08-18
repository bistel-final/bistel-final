"""Build the deterministic, non-destructive Neo4j seed artifact.

The registered ``master.cypher`` is never executed.  This module verifies the
archive and member hashes, parses the deliberately small source grammar, drops
the single destructive statement, injects stable relationship identifiers and
builds the expected graph fingerprint without connecting to Neo4j.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
DATASET_EPOCH_PATH = BOOTSTRAP_ROOT / "dataset-epoch.json"
CORRECTED_CYPHER_PATH = BOOTSTRAP_ROOT / "master_graph.cypher"
GRAPH_MANIFEST_PATH = BOOTSTRAP_ROOT / "manifests" / "neo4j.graph.json"

DATASET_EPOCH = "kosa_0813"
SOURCE_MEMBER_PATH = "kosa_0813/클린데이터셋/neo4j/master.cypher"
SOURCE_ARCHIVE_SHA256 = (
    "8bbe0bdd646290e2da300db0c293d6775927f61a35375721d4b042b239803c96"
)
SOURCE_MEMBER_SHA256 = (
    "b565035859a808d1a15a74f8df616bb43afd7ae2a2565839f7910ad07a89c35d"
)
CORRECTION_VERSION = "graph-v1"
HASH_ALGORITHM = "sha256-canonical-json-nfc-codepoint-v1"
RELATION_ID_ALGORITHM_VERSION = "rel-id-v1"
BUSINESS_KEY_CONTRACT_VERSION = "bk-v1"
GRAPH_MANIFEST_FORMAT_VERSION = 1
GRAPH_MANIFEST_ARTIFACT_TYPE = "neo4j_graph_manifest"

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

EXPECTED_NODE_COUNT = 38
EXPECTED_RELATIONSHIP_COUNT = 81
EXPECTED_LABEL_DISTRIBUTION = {
    "Area": 2,
    "Chamber": 12,
    "Equipment": 6,
    "EquipmentModel": 2,
    "Parameter": 8,
    "ProcessStep": 2,
    "Recipe": 2,
    "RecipeStep": 4,
}
EXPECTED_RELATIONSHIP_TYPE_DISTRIBUTION = {
    "IN_AREA": 4,
    "MEASURED_ON": 48,
    "NEXT_STEP": 1,
    "OF_MODEL": 6,
    "PART_OF": 12,
    "PERFORMS": 6,
    "STEP_OF": 4,
}

GRAPH_MANIFEST_KEYS = frozenset(
    {
        "format_version",
        "artifact_type",
        "dataset_epoch",
        "correction_version",
        "source_archive_sha256",
        "source_member_sha256",
        "corrected_cypher_sha256",
        "expected_graph_fingerprint_sha256",
        "expected_legacy_fingerprint_sha256",
        "hash_algorithm",
        "relation_id_algorithm_version",
        "business_key_contract_version",
        "node_count",
        "relationship_count",
        "label_distribution",
        "relationship_type_distribution",
    }
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
NAME_PATTERN = r"[A-Za-z][A-Za-z0-9_]*"
NODE_PREFIX = re.compile(
    rf"^MERGE \((?P<alias>{NAME_PATTERN})?:(?P<label>{NAME_PATTERN}) "
    r"\{(?P<properties>[^{}]*)\}\)"
)
MATCH_NODE = re.compile(
    rf"\((?P<alias>{NAME_PATTERN}):(?P<label>{NAME_PATTERN}) "
    r"\{(?P<properties>[^{}]*)\}\)"
)
RELATION_SUFFIX = re.compile(
    rf"MERGE \((?P<from_alias>{NAME_PATTERN})\)-"
    rf"\[:(?P<type>{NAME_PATTERN})\]->\((?P<to_alias>{NAME_PATTERN})\)$"
)
SET_BLOCK = re.compile(r"\sSET\s+(?P<body>.+?)\sWITH\s")
FORBIDDEN_SEED = re.compile(
    r"\b(?:DETACH\s+DELETE|DELETE|REMOVE|DROP|LOAD\s+CSV|"
    r"CREATE\s+(?:INDEX|CONSTRAINT)|CALL\s+(?:db|apoc)\.)\b",
    re.IGNORECASE,
)


class MasterCypherError(RuntimeError):
    """Source, grammar, graph or artifact contract mismatch."""


class SourceGuardError(MasterCypherError):
    """The archive is not the registered dataset epoch."""


class CypherGrammarError(MasterCypherError):
    """The source is outside the reviewed strict grammar."""


class GraphContractError(MasterCypherError):
    """The parsed graph violates bk-v1 or rel-id-v1."""


class GraphManifestError(MasterCypherError):
    """The Neo4j graph manifest is malformed or stale."""


@dataclass(frozen=True)
class NodeSpec:
    label: str
    properties: dict[str, str | int]

    @property
    def business_id(self) -> str:
        return serialize_business_id(self.label, self.properties)


@dataclass(frozen=True)
class RelationshipSpec:
    relation_type: str
    from_node: NodeSpec
    to_node: NodeSpec
    relation_id: str
    properties: dict[str, str | int]
    source_statement_index: int

    @property
    def canonical_tuple(self) -> str:
        return relation_tuple(
            self.relation_type,
            self.from_node,
            self.to_node,
        )


@dataclass(frozen=True)
class ParsedMasterCypher:
    destructive_statement: str
    seed_statements: tuple[str, ...]
    corrected_statements: tuple[str, ...]
    nodes: tuple[NodeSpec, ...]
    relationships: tuple[RelationshipSpec, ...]

    @property
    def corrected_text(self) -> str:
        return "\n".join(self.corrected_statements) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {
            _normalize(str(key)): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, tuple | list):
        return [_normalize(item) for item in value]
    return value


def canonical_json_bytes(payload: Any) -> bytes:
    normalized = _normalize(payload)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    return sha256_bytes(canonical_json_bytes(payload))


def _split_csv(text: str) -> list[str]:
    values: list[str] = []
    start = 0
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            if quoted and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif char == "," and not quoted:
            values.append(text[start:index].strip())
            start = index + 1
        index += 1
    if quoted:
        raise CypherGrammarError("닫히지 않은 문자열 literal이 있습니다")
    values.append(text[start:].strip())
    return values


def _parse_value(raw: str) -> str | int:
    value = raw.strip()
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value):
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return unicodedata.normalize("NFC", value[1:-1].replace("''", "'"))
    raise CypherGrammarError("source property는 str 또는 int literal만 허용합니다")


def parse_properties(raw: str) -> dict[str, str | int]:
    result: dict[str, str | int] = {}
    if not raw.strip():
        return result
    for part in _split_csv(raw):
        match = re.fullmatch(rf"(?P<key>{NAME_PATTERN})\s*:\s*(?P<value>.+)", part)
        if match is None:
            raise CypherGrammarError("property map 형식이 잘못됐습니다")
        key = unicodedata.normalize("NFC", match.group("key"))
        if key in result:
            raise CypherGrammarError("property key가 중복됐습니다")
        result[key] = _parse_value(match.group("value"))
    return result


def _parse_set_properties(statement: str, alias: str) -> dict[str, str | int]:
    match = SET_BLOCK.search(statement)
    if match is None:
        return {}
    result: dict[str, str | int] = {}
    for part in _split_csv(match.group("body")):
        item = re.fullmatch(
            rf"(?P<alias>{NAME_PATTERN})\.(?P<key>{NAME_PATTERN})\s*=\s*(?P<value>.+)",
            part,
        )
        if item is None or item.group("alias") != alias:
            raise CypherGrammarError("SET은 현재 MERGE node property만 허용합니다")
        result[item.group("key")] = _parse_value(item.group("value"))
    return result


def _strip_string_literals(statement: str) -> str:
    output: list[str] = []
    quoted = False
    index = 0
    while index < len(statement):
        char = statement[index]
        if char == "'":
            if quoted and index + 1 < len(statement) and statement[index + 1] == "'":
                output.extend("  ")
                index += 2
                continue
            quoted = not quoted
            output.append(" ")
        else:
            output.append(" " if quoted else char)
        index += 1
    if quoted:
        raise CypherGrammarError("닫히지 않은 문자열 literal이 있습니다")
    return "".join(output)


def _escape_business_value(value: str) -> str:
    escaped = unicodedata.normalize("NFC", value).replace("\\", "\\\\")
    for delimiter in ("|", ":", "+", "="):
        escaped = escaped.replace(delimiter, f"\\{delimiter}")
    return escaped


def serialize_business_value(value: Any) -> str:
    if isinstance(value, bool):
        raise GraphContractError("bool business key는 허용하지 않습니다")
    if isinstance(value, int):
        return f"i:{value:d}"
    if isinstance(value, str):
        return f"s:{_escape_business_value(value)}"
    raise GraphContractError("business key는 str 또는 int만 허용합니다")


def _split_unescaped(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    start = 0
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == delimiter:
            parts.append(value[start:index])
            start = index + 1
    if escaped:
        raise GraphContractError("business ID escape가 끝나지 않았습니다")
    parts.append(value[start:])
    return parts


def _unescape_business_value(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "\\":
            output.append(value[index])
            index += 1
            continue
        index += 1
        if index >= len(value) or value[index] not in "\\|:+=":
            raise GraphContractError("지원하지 않는 business ID escape입니다")
        output.append(value[index])
        index += 1
    return unicodedata.normalize("NFC", "".join(output))


def parse_business_id(value: str) -> dict[str, str | int]:
    """Reverse a bk-v1 value; used to lock the escape contract in tests."""

    result: dict[str, str | int] = {}
    for component in _split_unescaped(value, "+"):
        pair = _split_unescaped(component, "=")
        if len(pair) != 2 or not pair[0]:
            raise GraphContractError("business ID component 형식이 잘못됐습니다")
        key, tagged = pair
        if key in result or len(tagged) < 2 or tagged[1] != ":":
            raise GraphContractError("business ID type tag 형식이 잘못됐습니다")
        tag, raw = tagged[0], tagged[2:]
        if tag == "s":
            result[key] = _unescape_business_value(raw)
        elif tag == "i" and re.fullmatch(r"(?:0|-?[1-9][0-9]*)", raw):
            result[key] = int(raw)
        else:
            raise GraphContractError("business ID type/value가 잘못됐습니다")
    return result


def serialize_business_id(label: str, properties: Mapping[str, Any]) -> str:
    keys = BUSINESS_KEYS.get(label)
    if keys is None:
        raise GraphContractError("bk-v1에 없는 label입니다")
    missing = [key for key in keys if key not in properties]
    if missing:
        raise GraphContractError("business key property가 없습니다")
    return "+".join(
        f"{key}={serialize_business_value(properties[key])}" for key in sorted(keys)
    )


def relation_tuple(relation_type: str, from_node: NodeSpec, to_node: NodeSpec) -> str:
    return (
        f"{relation_type}|{from_node.label}:{from_node.business_id}|"
        f"{to_node.label}:{to_node.business_id}"
    )


def build_relation_id(
    relation_type: str, from_node: NodeSpec, to_node: NodeSpec
) -> str:
    value = relation_tuple(relation_type, from_node, to_node)
    return "REL-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _node_identity(node: NodeSpec) -> tuple[str, str]:
    return node.label, node.business_id


def _find_node(
    nodes: Sequence[NodeSpec], label: str, match_properties: Mapping[str, Any]
) -> NodeSpec:
    candidates = [
        node
        for node in nodes
        if node.label == label
        and all(
            node.properties.get(key) == value for key, value in match_properties.items()
        )
    ]
    if len(candidates) != 1:
        raise GraphContractError(
            "MATCH endpoint를 business node 하나로 확정할 수 없습니다"
        )
    return candidates[0]


def _inject_relation_id(statement: str, relation: RelationshipSpec) -> str:
    token = f"[:{relation.relation_type}]"
    replacement = (
        f"[:{relation.relation_type} {{relation_id:'{relation.relation_id}'}}]"
    )
    if statement.count(token) != 1:
        raise CypherGrammarError("relationship token을 하나로 확정할 수 없습니다")
    return statement.replace(token, replacement, 1)


def split_statements(source: str) -> tuple[str, ...]:
    statements: list[str] = []
    for raw_line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _strip_line_comment(raw_line).strip()
        if not line:
            continue
        if not line.endswith(";"):
            raise CypherGrammarError(
                "등록 source는 한 줄 한 문장과 세미콜론을 요구합니다"
            )
        statements.append(line)
    return tuple(statements)


def _strip_line_comment(line: str) -> str:
    """Remove ``//`` outside a Cypher string without interpreting its text."""

    quoted = False
    index = 0
    while index < len(line) - 1:
        char = line[index]
        if char == "'":
            if quoted and index + 1 < len(line) and line[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif not quoted and char == "/" and line[index + 1] == "/":
            return line[:index]
        index += 1
    return line


def parse_master_cypher(source: str) -> ParsedMasterCypher:
    statements = split_statements(source)
    if len(statements) != 94:
        raise CypherGrammarError("등록 source 문장 수가 94가 아닙니다")
    destructive = statements[0]
    if destructive != "MATCH (n) DETACH DELETE n;":
        raise CypherGrammarError("선두 destructive 문이 등록 계약과 다릅니다")
    seed_statements = statements[1:]

    nodes: list[NodeSpec] = []
    initial_nodes: dict[int, tuple[str | None, NodeSpec]] = {}
    for statement_index, statement_with_semicolon in enumerate(seed_statements):
        statement = statement_with_semicolon[:-1]
        visible = _strip_string_literals(statement)
        if FORBIDDEN_SEED.search(visible):
            raise CypherGrammarError("seed에 금지된 Cypher 문이 있습니다")
        if not (statement.startswith("MERGE ") or statement.startswith("MATCH ")):
            raise CypherGrammarError("등록 source grammar 밖의 시작 구문입니다")
        prefix = NODE_PREFIX.match(statement)
        if prefix is not None:
            alias = prefix.group("alias")
            properties = parse_properties(prefix.group("properties"))
            if alias:
                for key, value in _parse_set_properties(statement, alias).items():
                    if key in properties and properties[key] != value:
                        raise CypherGrammarError("MERGE와 SET property가 충돌합니다")
                    properties[key] = value
            node = NodeSpec(prefix.group("label"), properties)
            # Accessing the property validates label/key/type immediately.
            _ = node.business_id
            initial_nodes[statement_index] = (alias, node)
            nodes.append(node)

    if len(nodes) != EXPECTED_NODE_COUNT:
        raise GraphContractError("source node 수가 38이 아닙니다")
    identities = [_node_identity(node) for node in nodes]
    if len(set(identities)) != len(identities):
        raise GraphContractError("business key가 중복된 node가 있습니다")

    relationships: list[RelationshipSpec] = []
    corrected: list[str] = []
    for statement_index, statement_with_semicolon in enumerate(seed_statements):
        statement = statement_with_semicolon[:-1]
        relation_match = RELATION_SUFFIX.search(statement)
        if relation_match is None:
            if "-[" in statement or "]->" in statement:
                raise CypherGrammarError("지원하지 않는 relationship grammar입니다")
            corrected.append(statement_with_semicolon)
            continue

        aliases: dict[str, NodeSpec] = {}
        initial = initial_nodes.get(statement_index)
        if initial is not None and initial[0] is not None:
            aliases[initial[0]] = initial[1]
        for match in MATCH_NODE.finditer(statement):
            alias = match.group("alias")
            if alias in aliases:
                continue
            aliases[alias] = _find_node(
                nodes,
                match.group("label"),
                parse_properties(match.group("properties")),
            )
        try:
            from_node = aliases[relation_match.group("from_alias")]
            to_node = aliases[relation_match.group("to_alias")]
        except KeyError as exc:
            raise CypherGrammarError(
                "relationship endpoint alias를 해석할 수 없습니다"
            ) from exc
        relation_type = relation_match.group("type")
        relation_id = build_relation_id(relation_type, from_node, to_node)
        relation = RelationshipSpec(
            relation_type=relation_type,
            from_node=from_node,
            to_node=to_node,
            relation_id=relation_id,
            properties={"relation_id": relation_id},
            source_statement_index=statement_index,
        )
        relationships.append(relation)
        corrected.append(_inject_relation_id(statement, relation) + ";")

    if len(relationships) != EXPECTED_RELATIONSHIP_COUNT:
        raise GraphContractError("source relationship 수가 81이 아닙니다")
    relation_ids = [relation.relation_id for relation in relationships]
    tuples = [relation.canonical_tuple for relation in relationships]
    if len(set(relation_ids)) != len(relation_ids) or len(set(tuples)) != len(tuples):
        raise GraphContractError("명시적 edge key 없는 병렬 relationship이 있습니다")

    label_distribution = dict(sorted(Counter(node.label for node in nodes).items()))
    type_distribution = dict(
        sorted(Counter(item.relation_type for item in relationships).items())
    )
    if label_distribution != EXPECTED_LABEL_DISTRIBUTION:
        raise GraphContractError("source label 분포가 등록 계약과 다릅니다")
    if type_distribution != EXPECTED_RELATIONSHIP_TYPE_DISTRIBUTION:
        raise GraphContractError("source relationship type 분포가 등록 계약과 다릅니다")

    return ParsedMasterCypher(
        destructive_statement=destructive,
        seed_statements=tuple(seed_statements),
        corrected_statements=tuple(corrected),
        nodes=tuple(nodes),
        relationships=tuple(relationships),
    )


def graph_payload(
    nodes: Sequence[NodeSpec],
    relationships: Sequence[RelationshipSpec],
    *,
    include_relation_id: bool,
) -> dict[str, Any]:
    node_items = [
        {
            "label": node.label,
            "business_id": node.business_id,
            "properties": dict(sorted(node.properties.items())),
        }
        for node in nodes
    ]
    relation_items: list[dict[str, Any]] = []
    for relation in relationships:
        properties = dict(sorted(relation.properties.items()))
        item: dict[str, Any] = {
            "type": relation.relation_type,
            "from_label": relation.from_node.label,
            "from_business_id": relation.from_node.business_id,
            "to_label": relation.to_node.label,
            "to_business_id": relation.to_node.business_id,
            "properties": properties if include_relation_id else {},
        }
        if include_relation_id:
            item["relation_id"] = relation.relation_id
        relation_items.append(item)
    node_items.sort(key=lambda item: canonical_json_bytes(item).decode("utf-8"))
    relation_items.sort(key=lambda item: canonical_json_bytes(item).decode("utf-8"))
    return {"nodes": node_items, "relationships": relation_items}


def graph_fingerprint(parsed: ParsedMasterCypher, *, legacy: bool = False) -> str:
    return canonical_sha256(
        graph_payload(
            parsed.nodes,
            parsed.relationships,
            include_relation_id=not legacy,
        )
    )


def load_registered_source(
    archive_path: Path,
    *,
    epoch_path: Path = DATASET_EPOCH_PATH,
) -> tuple[bytes, dict[str, Any]]:
    if not archive_path.is_file() or archive_path.is_symlink():
        raise SourceGuardError("등록 source ZIP을 안전하게 읽을 수 없습니다")
    try:
        epoch = json.loads(epoch_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceGuardError("dataset epoch 등록부를 읽을 수 없습니다") from exc
    if not isinstance(epoch, dict):
        raise SourceGuardError("dataset epoch 등록부 형식이 잘못됐습니다")
    archive = epoch.get("archive")
    inventory = epoch.get("file_inventory")
    if (
        epoch.get("dataset_epoch") != DATASET_EPOCH
        or not isinstance(archive, dict)
        or archive.get("sha256") != SOURCE_ARCHIVE_SHA256
        or not isinstance(inventory, list)
    ):
        raise SourceGuardError("dataset epoch가 현재 source 계약과 다릅니다")
    registered = [
        item
        for item in inventory
        if isinstance(item, dict) and item.get("path") == SOURCE_MEMBER_PATH
    ]
    if len(registered) != 1 or registered[0].get("sha256") != SOURCE_MEMBER_SHA256:
        raise SourceGuardError("master.cypher 등록값이 없거나 다릅니다")
    if sha256_file(archive_path) != SOURCE_ARCHIVE_SHA256:
        raise SourceGuardError("source archive SHA-256이 등록값과 다릅니다")
    try:
        with zipfile.ZipFile(archive_path) as archive_file:
            member = archive_file.read(SOURCE_MEMBER_PATH)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise SourceGuardError("source archive member를 읽을 수 없습니다") from exc
    if sha256_bytes(member) != SOURCE_MEMBER_SHA256:
        raise SourceGuardError("master.cypher member SHA-256이 등록값과 다릅니다")
    return member, epoch


def parse_registered_archive(
    archive_path: Path, *, epoch_path: Path = DATASET_EPOCH_PATH
) -> ParsedMasterCypher:
    member, _ = load_registered_source(archive_path, epoch_path=epoch_path)
    try:
        source = member.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceGuardError("master.cypher는 UTF-8이어야 합니다") from exc
    return parse_master_cypher(source)


def build_graph_manifest(parsed: ParsedMasterCypher) -> dict[str, Any]:
    corrected_bytes = parsed.corrected_text.encode("utf-8")
    payload = {
        "format_version": GRAPH_MANIFEST_FORMAT_VERSION,
        "artifact_type": GRAPH_MANIFEST_ARTIFACT_TYPE,
        "dataset_epoch": DATASET_EPOCH,
        "correction_version": CORRECTION_VERSION,
        "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "source_member_sha256": SOURCE_MEMBER_SHA256,
        "corrected_cypher_sha256": sha256_bytes(corrected_bytes),
        "expected_graph_fingerprint_sha256": graph_fingerprint(parsed),
        "expected_legacy_fingerprint_sha256": graph_fingerprint(parsed, legacy=True),
        "hash_algorithm": HASH_ALGORITHM,
        "relation_id_algorithm_version": RELATION_ID_ALGORITHM_VERSION,
        "business_key_contract_version": BUSINESS_KEY_CONTRACT_VERSION,
        "node_count": len(parsed.nodes),
        "relationship_count": len(parsed.relationships),
        "label_distribution": dict(
            sorted(Counter(node.label for node in parsed.nodes).items())
        ),
        "relationship_type_distribution": dict(
            sorted(Counter(item.relation_type for item in parsed.relationships).items())
        ),
    }
    validate_graph_manifest(payload)
    return payload


def validate_graph_manifest(payload: Mapping[str, Any]) -> None:
    if set(payload) != GRAPH_MANIFEST_KEYS:
        raise GraphManifestError("Neo4j graph manifest key 집합이 잘못됐습니다")
    fixed = {
        "format_version": GRAPH_MANIFEST_FORMAT_VERSION,
        "artifact_type": GRAPH_MANIFEST_ARTIFACT_TYPE,
        "dataset_epoch": DATASET_EPOCH,
        "correction_version": CORRECTION_VERSION,
        "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "source_member_sha256": SOURCE_MEMBER_SHA256,
        "hash_algorithm": HASH_ALGORITHM,
        "relation_id_algorithm_version": RELATION_ID_ALGORITHM_VERSION,
        "business_key_contract_version": BUSINESS_KEY_CONTRACT_VERSION,
        "node_count": EXPECTED_NODE_COUNT,
        "relationship_count": EXPECTED_RELATIONSHIP_COUNT,
        "label_distribution": EXPECTED_LABEL_DISTRIBUTION,
        "relationship_type_distribution": EXPECTED_RELATIONSHIP_TYPE_DISTRIBUTION,
    }
    if any(payload.get(key) != value for key, value in fixed.items()):
        raise GraphManifestError("Neo4j graph manifest 고정값이 다릅니다")
    for key in (
        "corrected_cypher_sha256",
        "expected_graph_fingerprint_sha256",
        "expected_legacy_fingerprint_sha256",
    ):
        if not isinstance(payload.get(key), str) or not SHA256_PATTERN.fullmatch(
            payload[key]
        ):
            raise GraphManifestError("Neo4j graph manifest SHA-256 형식이 잘못됐습니다")
    for distribution_key in (
        "label_distribution",
        "relationship_type_distribution",
    ):
        distribution = payload[distribution_key]
        if not isinstance(distribution, dict) or list(distribution) != sorted(
            distribution
        ):
            raise GraphManifestError(
                "Neo4j graph manifest 분포 key 순서가 잘못됐습니다"
            )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in distribution.values()
        ):
            raise GraphManifestError("Neo4j graph manifest 분포 값이 잘못됐습니다")


def graph_manifest_sha256(payload: Mapping[str, Any]) -> str:
    validate_graph_manifest(payload)
    return sha256_bytes(canonical_json_bytes(payload))


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise MasterCypherError("생성 artifact에 symlink를 사용할 수 없습니다")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise MasterCypherError(
            "Neo4j artifact를 원자적으로 저장할 수 없습니다"
        ) from exc
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def write_generated_artifacts(
    parsed: ParsedMasterCypher,
    *,
    cypher_path: Path = CORRECTED_CYPHER_PATH,
    manifest_path: Path = GRAPH_MANIFEST_PATH,
) -> dict[str, Any]:
    manifest = build_graph_manifest(parsed)
    corrected = parsed.corrected_text.encode("utf-8")
    _atomic_write(cypher_path, corrected)
    _atomic_write(manifest_path, canonical_json_bytes(manifest) + b"\n")
    if cypher_path.read_bytes() != corrected:
        raise MasterCypherError("corrected Cypher 원자 저장 검증에 실패했습니다")
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_graph_manifest(saved)
    if saved != manifest:
        raise MasterCypherError("graph manifest 원자 저장 검증에 실패했습니다")
    return manifest


def validate_generated_artifacts(
    parsed: ParsedMasterCypher,
    *,
    cypher_path: Path = CORRECTED_CYPHER_PATH,
    manifest_path: Path = GRAPH_MANIFEST_PATH,
) -> dict[str, Any]:
    expected_manifest = build_graph_manifest(parsed)
    try:
        corrected = cypher_path.read_bytes()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraphManifestError("Neo4j 생성 artifact를 읽을 수 없습니다") from exc
    if corrected != parsed.corrected_text.encode("utf-8"):
        raise GraphManifestError("corrected Cypher가 source 생성 결과와 다릅니다")
    validate_graph_manifest(payload)
    if payload != expected_manifest:
        raise GraphManifestError("Neo4j graph manifest가 생성 결과와 다릅니다")
    return payload
