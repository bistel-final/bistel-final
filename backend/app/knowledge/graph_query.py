"""Neo4j graph context read repository."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.common.neo4j import get_neo4j_driver
from app.knowledge.graph_revision import (
    graph_database_name,
    load_graph_revision,
)

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


@dataclass(frozen=True)
class EquipmentContextRow:
    chamber: dict[str, Any]
    equipment: dict[str, Any]
    model: dict[str, Any]
    area: dict[str, Any] | None
    step: dict[str, Any] | None
    sibling_chambers: list[dict[str, Any]]
    adjacent_steps: list[dict[str, Any]]
    parameters: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    graph_revision: str


class GraphQueryRepository:
    """Read chamber-centered context from the verified Neo4j graph."""

    CONTEXT_QUERY = """
MATCH (c:Chamber {chamber_id: $chamber_id})-[part:PART_OF]->(e:Equipment)
MATCH (e)-[ofModel:OF_MODEL]->(m:EquipmentModel)
OPTIONAL MATCH (e)-[performs:PERFORMS]->(step:ProcessStep)
OPTIONAL MATCH (m)-[modelArea:IN_AREA]->(area:Area)
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
  collect(DISTINCT properties(sibling)) AS sibling_chambers,
  collect(DISTINCT properties(previous)) + collect(DISTINCT properties(next))
    AS adjacent_steps,
  collect(DISTINCT properties(parameter)) AS parameters,
  collect(DISTINCT part) + collect(DISTINCT ofModel) + collect(DISTINCT performs) +
    collect(DISTINCT modelArea) + collect(DISTINCT measured) +
    collect(DISTINCT siblingPart) + collect(DISTINCT previousRel) +
    collect(DISTINCT nextRel) AS graph_relations
RETURN
  properties(c) AS chamber,
  properties(e) AS equipment,
  properties(m) AS model,
  properties(area) AS area,
  properties(step) AS step,
  sibling_chambers,
  adjacent_steps,
  parameters,
  [
    rel IN graph_relations
    WHERE rel IS NOT NULL
    | {
        relation_id: rel.relation_id,
        relation_type: type(rel),
        from_label: labels(startNode(rel))[0],
        from_properties: properties(startNode(rel)),
        to_label: labels(endNode(rel))[0],
        to_properties: properties(endNode(rel))
      }
  ] AS relations
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

    def get_equipment_context(self, chamber_id: str) -> EquipmentContextRow | None:
        driver = self._driver_factory()
        with driver.session(
            database=self._database,
            default_access_mode="READ",
        ) as session:
            record = session.run(
                self.CONTEXT_QUERY,
                {"chamber_id": chamber_id},
            ).single()
        if record is None:
            return None

        row = _record_to_mapping(record)
        return EquipmentContextRow(
            chamber=_clean_map(row.get("chamber")),
            equipment=_clean_map(row.get("equipment")),
            model=_clean_map(row.get("model")),
            area=_optional_map(row.get("area")),
            step=_optional_map(row.get("step")),
            sibling_chambers=_clean_maps(row.get("sibling_chambers")),
            adjacent_steps=_clean_maps(row.get("adjacent_steps")),
            parameters=_clean_maps(row.get("parameters")),
            relations=_relation_refs(row.get("relations")),
            graph_revision=self._graph_revision_loader(),
        )


def _record_to_mapping(record: Any) -> Mapping[str, Any]:
    if isinstance(record, Mapping):
        return record
    if hasattr(record, "data"):
        data = record.data()
        if isinstance(data, Mapping):
            return data
    return dict(record)


def _clean_map(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items() if item is not None}


def _optional_map(value: Any) -> dict[str, Any] | None:
    cleaned = _clean_map(value)
    return cleaned or None


def _clean_maps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    unique: dict[str, dict[str, Any]] = {}
    for item in value:
        cleaned = _clean_map(item)
        if cleaned:
            unique[json.dumps(cleaned, ensure_ascii=False, sort_keys=True)] = cleaned
    return list(unique.values())


def _relation_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    refs: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        if not item.get("relation_id"):
            raise RuntimeError("Neo4j relationship에 relation_id가 없습니다")
        if not item.get("relation_type"):
            raise RuntimeError("Neo4j relationship type을 확인할 수 없습니다")
        if not item.get("from_label") or not item.get("to_label"):
            raise RuntimeError("Neo4j relationship endpoint label이 없습니다")
        if not item.get("from_properties") or not item.get("to_properties"):
            raise RuntimeError("Neo4j relationship endpoint business key가 없습니다")
        from_label = str(item.get("from_label", ""))
        to_label = str(item.get("to_label", ""))
        ref = {
            "relation_id": item["relation_id"],
            "relation_type": item["relation_type"],
            "from_label": from_label,
            "from_business_id": _business_id(
                from_label,
                _clean_map(item.get("from_properties")),
            ),
            "to_label": to_label,
            "to_business_id": _business_id(
                to_label,
                _clean_map(item.get("to_properties")),
            ),
        }
        refs[str(ref["relation_id"])] = ref
    return list(refs.values())


def _business_id(label: str, properties: Mapping[str, Any]) -> str:
    keys = BUSINESS_KEYS[label]
    return "+".join(f"{key}={_business_value(properties[key])}" for key in sorted(keys))


def _business_value(value: Any) -> str:
    if isinstance(value, bool):
        raise RuntimeError("bool business key는 허용하지 않습니다")
    if isinstance(value, int):
        return f"i:{value:d}"
    return f"s:{_escape_business_value(str(value))}"


def _escape_business_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for delimiter in ("|", ":", "+", "="):
        escaped = escaped.replace(delimiter, f"\\{delimiter}")
    return escaped
