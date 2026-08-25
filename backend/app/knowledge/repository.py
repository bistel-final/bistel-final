"""Neo4j graph projection repository for API reads."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.common.neo4j import get_neo4j_driver
from app.knowledge.graph_revision import (
    graph_database_name,
    load_graph_revision,
)


@dataclass(frozen=True)
class ChamberGraphProjection:
    root_node_id: str
    nodes: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    graph_revision: str


class ChamberGraphRepository:
    """Read graph drawing projection from the verified Neo4j graph."""

    GRAPH_PROJECTION_QUERY = """
MATCH (c:Chamber {chamber_id: $chamber_id})-[part:PART_OF]->(e:Equipment)
MATCH (e)-[ofModel:OF_MODEL]->(m:EquipmentModel)
OPTIONAL MATCH (e)-[performs:PERFORMS]->(step:ProcessStep)
OPTIONAL MATCH (step)-[stepArea:IN_AREA]->(area:Area)
OPTIONAL MATCH (c)<-[measured:MEASURED_ON]-(parameter:Parameter)
OPTIONAL MATCH (sibling:Chamber)-[siblingPart:PART_OF]->(e)
  WHERE sibling.chamber_id <> c.chamber_id
OPTIONAL MATCH (previous:ProcessStep)-[previousRel:NEXT_STEP]->(step)
OPTIONAL MATCH (step)-[nextRel:NEXT_STEP]->(next:ProcessStep)
WITH
  c,
  [node IN [c, e, m, area, step] +
    collect(DISTINCT sibling) +
    collect(DISTINCT previous) +
    collect(DISTINCT next) +
    collect(DISTINCT parameter)
    WHERE node IS NOT NULL
    | {
        id: labels(node)[0] + ':' + coalesce(
          node.chamber_id,
          node.equipment_id,
          node.model_code,
          node.area_id,
          node.step_id,
          node.parameter_id
        ),
        label: labels(node)[0],
        business_id: coalesce(
          node.chamber_id,
          node.equipment_id,
          node.model_code,
          node.area_id,
          node.step_id,
          node.parameter_id
        ),
        display_name: coalesce(
          node.chamber_id,
          node.equipment_name,
          node.model_name,
          node.area_name,
          node.step_name,
          node.parameter_name,
          node.equipment_id,
          node.model_code,
          node.area_id,
          node.step_id,
          node.parameter_id
        ),
        properties: properties(node)
      }
  ] AS graph_nodes,
  collect(DISTINCT part) + collect(DISTINCT ofModel) + collect(DISTINCT performs) +
    collect(DISTINCT stepArea) + collect(DISTINCT measured) +
    collect(DISTINCT siblingPart) + collect(DISTINCT previousRel) +
    collect(DISTINCT nextRel) AS graph_relations
RETURN
  'Chamber:' + c.chamber_id AS root_node_id,
  graph_nodes AS nodes,
  [
    rel IN graph_relations
    WHERE rel IS NOT NULL
    | {
        id: rel.relation_id,
        type: type(rel),
        source: labels(startNode(rel))[0] + ':' + coalesce(
          startNode(rel).chamber_id,
          startNode(rel).equipment_id,
          startNode(rel).model_code,
          startNode(rel).area_id,
          startNode(rel).step_id,
          startNode(rel).parameter_id
        ),
        target: labels(endNode(rel))[0] + ':' + coalesce(
          endNode(rel).chamber_id,
          endNode(rel).equipment_id,
          endNode(rel).model_code,
          endNode(rel).area_id,
          endNode(rel).step_id,
          endNode(rel).parameter_id
        )
      }
  ] AS relationships
"""

    def __init__(
        self,
        *,
        driver_factory: Callable[[], Any] = get_neo4j_driver,
        graph_revision_loader: Callable[[], str] = lambda: load_graph_revision(),
        database: str | None = None,
    ) -> None:
        self._driver_factory = driver_factory
        self._graph_revision_loader = graph_revision_loader
        self._database = database or graph_database_name()

    def get_chamber_graph_projection(
        self,
        chamber_id: str,
    ) -> ChamberGraphProjection | None:
        driver = self._driver_factory()
        with driver.session(
            database=self._database,
            default_access_mode="READ",
        ) as session:
            record = session.run(
                self.GRAPH_PROJECTION_QUERY,
                {"chamber_id": chamber_id},
            ).single()
        if record is None:
            return None

        row = _record_to_mapping(record)
        root_node_id = row.get("root_node_id")
        if root_node_id is None:
            raise RuntimeError("Neo4j graph projection root_node_id가 없습니다")

        return ChamberGraphProjection(
            root_node_id=str(root_node_id),
            nodes=_clean_projection_items(row.get("nodes"), item_name="node"),
            relationships=_clean_projection_items(
                row.get("relationships"),
                item_name="relationship",
            ),
            graph_revision=self._graph_revision_loader(),
        )


# ============================
# Neo4j projection helper
# ============================
def _record_to_mapping(record: Any) -> Mapping[str, Any]:
    if isinstance(record, Mapping):
        return record
    if hasattr(record, "data"):
        data = record.data()
        if isinstance(data, Mapping):
            return data
    return dict(record)


def _clean_projection_items(value: Any, *, item_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []

    unique: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        cleaned = {str(key): field for key, field in item.items() if field is not None}
        item_id = cleaned.get("id")
        if item_id is None:
            raise RuntimeError(f"Neo4j graph projection {item_name} id가 없습니다")
        unique[str(item_id)] = cleaned
    return list(unique.values())
