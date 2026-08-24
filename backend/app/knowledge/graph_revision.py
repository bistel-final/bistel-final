"""Neo4j graph revision marker validation."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.common.config import REPOSITORY_ROOT

GRAPH_MARKER_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "markers"
GRAPH_REVISION_KEY = "actual_graph_fingerprint_sha256"
DATASET_EPOCH = "fdc_final_20260818"
SOURCE_MEMBER_SHA256 = (
    "51604707c9a0f3bc97b21773b7bd43d0049f2dacf322042c36f090ec63c74eea"
)
EXPECTED_NODE_COUNT = 44
EXPECTED_RELATIONSHIP_COUNT = 85
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SUCCESS_STATUSES = frozenset(
    {"APPLIED", "REPLACED", "ADOPTED_EXISTING", "VERIFIED_EXISTING"}
)


def graph_database_name() -> str:
    return os.getenv("NEO4J_DATABASE", "neo4j")


def load_graph_revision(
    *,
    database: str | None = None,
    marker_root: Path = GRAPH_MARKER_ROOT,
) -> str:
    database_name = database or graph_database_name()
    marker_path = marker_root / f"neo4j_graph.{database_name}.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Neo4j graph success marker를 읽을 수 없습니다") from exc

    validate_graph_marker(marker, database=database_name)
    return str(marker[GRAPH_REVISION_KEY])


def validate_graph_marker(marker: Mapping[str, Any], *, database: str) -> None:
    for key in (
        "source_member_sha256",
        "expected_graph_fingerprint_sha256",
        GRAPH_REVISION_KEY,
    ):
        value = marker.get(key)
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            raise RuntimeError("Neo4j graph marker SHA-256 형식이 잘못됐습니다")

    revision = marker.get(GRAPH_REVISION_KEY)
    if (
        marker.get("dataset_epoch") != DATASET_EPOCH
        or marker.get("database") != database
        or marker.get("source_member_sha256") != SOURCE_MEMBER_SHA256
        or marker.get("status") not in SUCCESS_STATUSES
        or marker.get("node_count") != EXPECTED_NODE_COUNT
        or marker.get("relationship_count") != EXPECTED_RELATIONSHIP_COUNT
        or marker.get("relation_id_duplicates") != 0
        or marker.get("expected_graph_fingerprint_sha256") != revision
    ):
        raise RuntimeError("Neo4j graph marker가 success 계약과 다릅니다")
