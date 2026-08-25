"""Neo4j graph projection repository for API reads."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.common.neo4j import get_neo4j_driver
from app.knowledge.exceptions import GraphProjectionShapeError
from app.knowledge.graph_revision import (
    graph_database_name,
    load_graph_revision,
)

GRAPH_NODE_LABELS = frozenset(
    {"Area", "Chamber", "Equipment", "EquipmentModel", "Parameter", "ProcessStep"}
)
GRAPH_RELATIONSHIP_TYPES = frozenset(
    {"IN_AREA", "MEASURED_ON", "NEXT_STEP", "OF_MODEL", "PART_OF", "PERFORMS"}
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
  e,
  m,
  area,
  step,
  collect(DISTINCT sibling) AS siblings,
  collect(DISTINCT previous) AS previous_steps,
  collect(DISTINCT next) AS next_steps,
  collect(DISTINCT parameter) AS parameters,
  collect(DISTINCT part) + collect(DISTINCT ofModel) + collect(DISTINCT performs) +
    collect(DISTINCT stepArea) + collect(DISTINCT measured) +
    collect(DISTINCT siblingPart) + collect(DISTINCT previousRel) +
    collect(DISTINCT nextRel) AS graph_relations
WITH
  c,
  [node IN [c, e, m, area, step] + siblings + previous_steps + next_steps + parameters
    WHERE node IS NOT NULL
    | {
        id: labels(node)[0] + ':' + CASE labels(node)[0]
          WHEN 'Chamber' THEN node.chamber_id
          WHEN 'Equipment' THEN node.equipment_id
          WHEN 'EquipmentModel' THEN node.model_code
          WHEN 'Area' THEN node.area_id
          WHEN 'ProcessStep' THEN node.step_id
          WHEN 'Parameter' THEN node.parameter_id
        END,
        label: labels(node)[0],
        business_id: CASE labels(node)[0]
          WHEN 'Chamber' THEN node.chamber_id
          WHEN 'Equipment' THEN node.equipment_id
          WHEN 'EquipmentModel' THEN node.model_code
          WHEN 'Area' THEN node.area_id
          WHEN 'ProcessStep' THEN node.step_id
          WHEN 'Parameter' THEN node.parameter_id
        END,
        display_name: CASE labels(node)[0]
          WHEN 'Chamber' THEN node.chamber_id
          WHEN 'Equipment' THEN coalesce(node.equipment_name, node.equipment_id)
          WHEN 'EquipmentModel' THEN coalesce(node.model_name, node.model_code)
          WHEN 'Area' THEN coalesce(node.area_name, node.area_id)
          WHEN 'ProcessStep' THEN coalesce(node.step_name, node.step_id)
          WHEN 'Parameter' THEN node.parameter_id
        END,
        properties: properties(node)
      }
  ] AS graph_nodes,
  graph_relations
RETURN
  'Chamber:' + c.chamber_id AS root_node_id,
  graph_nodes AS nodes,
  [
    rel IN graph_relations
    WHERE rel IS NOT NULL
    | {
        id: rel.relation_id,
        type: type(rel),
        source: CASE type(rel)
          WHEN 'PART_OF' THEN 'Chamber:' + startNode(rel).chamber_id
          WHEN 'OF_MODEL' THEN 'Equipment:' + startNode(rel).equipment_id
          WHEN 'PERFORMS' THEN 'Equipment:' + startNode(rel).equipment_id
          WHEN 'IN_AREA' THEN 'ProcessStep:' + startNode(rel).step_id
          WHEN 'MEASURED_ON' THEN 'Parameter:' + startNode(rel).parameter_id
          WHEN 'NEXT_STEP' THEN 'ProcessStep:' + startNode(rel).step_id
        END,
        target: CASE type(rel)
          WHEN 'PART_OF' THEN 'Equipment:' + endNode(rel).equipment_id
          WHEN 'OF_MODEL' THEN 'EquipmentModel:' + endNode(rel).model_code
          WHEN 'PERFORMS' THEN 'ProcessStep:' + endNode(rel).step_id
          WHEN 'IN_AREA' THEN 'Area:' + endNode(rel).area_id
          WHEN 'MEASURED_ON' THEN 'Chamber:' + endNode(rel).chamber_id
          WHEN 'NEXT_STEP' THEN 'ProcessStep:' + endNode(rel).step_id
        END
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
            raise GraphProjectionShapeError(
                details={"reason_code": "MISSING_ROOT_NODE_ID"}
            )

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
        _validate_projection_contract(cleaned, item_name=item_name)
        item_id = cleaned.get("id")
        if item_id is None:
            raise GraphProjectionShapeError(
                details={
                    "reason_code": "MISSING_PROJECTION_ITEM_ID",
                    "item": item_name,
                }
            )
        unique[str(item_id)] = cleaned
    items = list(unique.values())
    if item_name == "node":
        return sorted(
            items,
            key=lambda item: (
                str(item.get("label", "")),
                str(item.get("business_id", "")),
                str(item.get("id", "")),
            ),
        )
    if item_name == "relationship":
        return sorted(
            items,
            key=lambda item: (
                str(item.get("type", "")),
                str(item.get("source", "")),
                str(item.get("target", "")),
                str(item.get("id", "")),
            ),
        )
    return items


def _validate_projection_contract(
    item: Mapping[str, Any],
    *,
    item_name: str,
) -> None:
    if item_name == "node":
        label = item.get("label")
        if label not in GRAPH_NODE_LABELS:
            raise GraphProjectionShapeError(
                details={
                    "reason_code": "UNSUPPORTED_NODE_LABEL",
                    "label": str(label),
                }
            )
        return

    if item_name == "relationship":
        relationship_type = item.get("type")
        if relationship_type not in GRAPH_RELATIONSHIP_TYPES:
            raise GraphProjectionShapeError(
                details={
                    "reason_code": "UNSUPPORTED_RELATIONSHIP_TYPE",
                    "type": str(relationship_type),
                }
            )
