"""Agent Tool용 Neo4j compact graph context 질의."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from neo4j import Query

from app.common.config import TOOL_DB_TIMEOUT_SEC
from app.common.neo4j import get_neo4j_driver
from app.common.tool_timeouts import neo4j_timeout_error
from app.knowledge.graph_revision import (
    graph_database_name,
    load_graph_revision,
)


class GraphQueryRepository:
    """검증된 Neo4j 그래프에서 Tool용 compact 장비 context를 조회한다."""

    TOOL_CONTEXT_QUERY = """
MATCH (c:Chamber {chamber_id: $chamber_id})-[:PART_OF]->(e:Equipment)
MATCH (e)-[:OF_MODEL]->(m:EquipmentModel)
OPTIONAL MATCH (e)-[:PERFORMS]->(step:ProcessStep)
OPTIONAL MATCH (step)-[:IN_AREA]->(area:Area)
OPTIONAL MATCH (c)<-[:MEASURED_ON]-(parameter:Parameter)
OPTIONAL MATCH (sibling:Chamber)-[:PART_OF]->(e)
  WHERE sibling.chamber_id <> c.chamber_id
OPTIONAL MATCH (previous:ProcessStep)-[:NEXT_STEP]->(step)
OPTIONAL MATCH (step)-[:NEXT_STEP]->(next:ProcessStep)
RETURN
  c.chamber_id AS chamber_id,
  e.equipment_id AS equipment_id,
  collect(DISTINCT sibling.chamber_id) AS sibling_chamber_ids,
  area.area_id AS area,
  m.model_code AS model_code,
  step.step_id AS process_step_id,
  collect(DISTINCT previous.step_id) AS upstream_process_step_ids,
  collect(DISTINCT next.step_id) AS downstream_process_step_ids,
  collect(DISTINCT parameter.parameter_id) AS parameter_ids
"""

    def __init__(
        self,
        *,
        driver_factory: Callable[[], Any] = get_neo4j_driver,
        graph_revision_loader: Callable[[], str] = lambda: load_graph_revision(),
        database: str | None = None,
        timeout_seconds: float = TOOL_DB_TIMEOUT_SEC,
    ) -> None:
        self._driver_factory = driver_factory
        self._graph_revision_loader = graph_revision_loader
        self._database = database or graph_database_name()
        self._timeout_seconds = timeout_seconds

    def get_equipment_context_payload(self, chamber_id: str) -> dict[str, Any] | None:
        driver = self._driver_factory()
        with driver.session(
            database=self._database,
            default_access_mode="READ",
        ) as session:
            try:
                record = session.run(
                    Query(
                        self.TOOL_CONTEXT_QUERY,
                        timeout=self._timeout_seconds,
                    ),
                    {"chamber_id": chamber_id},
                ).single()
            except Exception as exc:
                if timeout := neo4j_timeout_error(exc):
                    raise timeout from None
                raise
        if record is None:
            return None

        row = _record_to_mapping(record)
        return {
            "chamber_id": row.get("chamber_id"),
            "equipment_id": row.get("equipment_id"),
            "sibling_chamber_ids": _clean_scalar_list(row.get("sibling_chamber_ids")),
            "area": row.get("area"),
            "model_code": row.get("model_code"),
            "process_step_id": row.get("process_step_id"),
            "upstream_process_step_ids": _clean_scalar_list(
                row.get("upstream_process_step_ids")
            ),
            "downstream_process_step_ids": _clean_scalar_list(
                row.get("downstream_process_step_ids")
            ),
            "parameter_ids": _clean_scalar_list(row.get("parameter_ids")),
            "graph_revision": self._graph_revision_loader(),
        }


# ============================
# Neo4j record 정리 헬퍼
# ============================
def _record_to_mapping(record: Any) -> Mapping[str, Any]:
    if isinstance(record, Mapping):
        return record
    if hasattr(record, "data"):
        data = record.data()
        if isinstance(data, Mapping):
            return data
    return dict(record)


def _clean_scalar_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return sorted({str(item) for item in value if item is not None})
