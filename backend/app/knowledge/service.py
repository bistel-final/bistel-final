"""Knowledge application services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.common.tool_contracts import (
    AreaNode,
    ChamberNode,
    DocumentHit,
    EquipmentNode,
    GraphRelationRef,
    ParameterNode,
    ProcessStepNode,
)
from app.knowledge.document_search import embed_query
from app.knowledge.graph_query import EquipmentContextRow, GraphQueryRepository


class DocumentRepository(Protocol):
    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int,
        model_code: str | None,
    ) -> list[Any]:
        ...


class DocumentSearchService:
    """API와 Tool이 공유하는 문서 검색 application service."""

    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        model_code: str | None = None,
    ) -> list[DocumentHit]:
        query_vector = embed_query(query)
        rows = self._repository.search(
            query_vector,
            top_k=top_k,
            model_code=model_code,
        )
        return [
            DocumentHit(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                title=row.title,
                section=row.section,
                score=row.score,
                content=row.content,
                model_code=row.model_code,
            )
            for row in rows
        ]


class GraphContextRepository(Protocol):
    def get_equipment_context(self, chamber_id: str) -> EquipmentContextRow | None:
        ...


@dataclass(frozen=True)
class EquipmentContext:
    equipment: EquipmentNode
    area: AreaNode | None
    step: ProcessStepNode | None
    sibling_chambers: list[ChamberNode]
    adjacent_steps: list[ProcessStepNode]
    parameters: list[ParameterNode]
    relations: list[GraphRelationRef]
    graph_revision: str


class GraphService:
    """Graph context service shared by the API and Agent Tool."""

    def __init__(self, repository: GraphContextRepository | None = None) -> None:
        self._repository = repository or GraphQueryRepository()

    def get_equipment_context(self, chamber_id: str) -> EquipmentContext | None:
        row = self._repository.get_equipment_context(chamber_id)
        if row is None:
            return None

        area_id = _string_or_none(row.area, "area_id")
        step_id = _string_or_none(row.step, "step_id")
        model_code = _string(row.model, "model_code", row.equipment.get("model_code"))
        equipment_id = _string(row.equipment, "equipment_id")

        equipment = EquipmentNode(
            equipment_id=equipment_id,
            equipment_name=_string(
                row.equipment,
                "equipment_name",
                row.equipment.get("name") or equipment_id,
            ),
            model_code=model_code,
            area_id=area_id or _string(row.equipment, "area_id"),
            step_id=step_id,
        )
        return EquipmentContext(
            equipment=equipment,
            area=_area_node(row.area),
            step=_step_node(row.step),
            sibling_chambers=[
                _chamber_node(
                    item,
                    equipment_id=equipment_id,
                    model_code=model_code,
                    area_id=equipment.area_id,
                    step_id=step_id,
                )
                for item in row.sibling_chambers
            ],
            adjacent_steps=[
                item
                for item in (_step_node(step) for step in row.adjacent_steps)
                if item
            ],
            parameters=[
                ParameterNode(
                    parameter_id=_string(item, "parameter_id"),
                    parameter_name=_string(
                        item,
                        "parameter_name",
                        item.get("name") or item.get("parameter_id"),
                    ),
                    unit=_string_or_none(item, "unit"),
                )
                for item in row.parameters
            ],
            relations=[
                GraphRelationRef(
                    relation_id=_string(item, "relation_id"),
                    relation_type=_string(item, "relation_type"),
                    from_label=_string(item, "from_label"),
                    from_business_id=_string(item, "from_business_id"),
                    to_label=_string(item, "to_label"),
                    to_business_id=_string(item, "to_business_id"),
                )
                for item in row.relations
            ],
            graph_revision=row.graph_revision,
        )


def _area_node(value: dict[str, object] | None) -> AreaNode | None:
    if not value:
        return None
    return AreaNode(
        area_id=_string(value, "area_id"),
        area_name=_string_or_none(value, "area_name"),
    )


def _step_node(value: dict[str, object] | None) -> ProcessStepNode | None:
    if not value:
        return None
    step_id = _string(value, "step_id")
    return ProcessStepNode(
        step_id=step_id,
        step_name=_string(value, "step_name", value.get("name") or step_id),
        step_seq=_int_or_none(value, "step_seq"),
        layer=_string_or_none(value, "layer"),
    )


def _chamber_node(
    value: dict[str, object],
    *,
    equipment_id: str,
    model_code: str,
    area_id: str | None,
    step_id: str | None,
) -> ChamberNode:
    return ChamberNode(
        chamber_id=_string(value, "chamber_id"),
        equipment_id=equipment_id,
        chamber_no=_int_or_none(value, "chamber_no"),
        model_code=model_code,
        area_id=area_id,
        step_id=step_id,
    )


def _string(
    value: dict[str, object],
    key: str,
    default: object | None = None,
) -> str:
    item = value.get(key, default)
    if item is None:
        raise ValueError(f"graph context field is missing: {key}")
    return str(item)


def _string_or_none(value: dict[str, object] | None, key: str) -> str | None:
    if not value or value.get(key) is None:
        return None
    return str(value[key])


def _int_or_none(value: dict[str, object], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    return int(item)
