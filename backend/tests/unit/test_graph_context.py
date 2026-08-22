from __future__ import annotations

from typing import Any

from app.common.tool_contracts import (
    AreaNode,
    ChamberNode,
    EquipmentNode,
    GraphRelationRef,
    ParameterNode,
    ProcessStepNode,
)
from app.knowledge.graph_query import EquipmentContextRow, GraphQueryRepository
from app.knowledge.service import GraphService


REVISION = "3474debee491ea5c699080109d748a4922ad0566a3b84568e9067053de2fa2eb"


class FakeGraphRepository:
    def get_equipment_context(self, chamber_id: str) -> EquipmentContextRow | None:
        if chamber_id == "missing":
            return None
        return EquipmentContextRow(
            chamber={"chamber_id": chamber_id, "chamber_no": 1},
            equipment={
                "equipment_id": "EQP01",
                "equipment_name": "Photo Scanner 01",
                "model_code": "PH-9000",
            },
            model={"model_code": "PH-9000", "model_name": "Photo Scanner"},
            area={"area_id": "photo", "area_name": "Photolithography"},
            step={"step_id": "CT-PHOTO", "step_name": "Coat Track", "step_seq": 1},
            sibling_chambers=[
                {"chamber_id": "EQP01-PM2", "chamber_no": 2},
            ],
            adjacent_steps=[
                {"step_id": "CT-ETCH", "step_name": "Etch", "step_seq": 2}
            ],
            parameters=[
                {
                    "parameter_id": "PH_FOCUS",
                    "parameter_name": "Focus",
                    "unit": "um",
                }
            ],
            relations=[
                {
                    "relation_id": "REL-1",
                    "relation_type": "PART_OF",
                    "from_label": "Chamber",
                    "from_business_id": "chamber_id=s:EQP01-PM1",
                    "to_label": "Equipment",
                    "to_business_id": "equipment_id=s:EQP01",
                }
            ],
            graph_revision=REVISION,
        )


def test_graph_service_maps_equipment_context_payload() -> None:
    result = GraphService(FakeGraphRepository()).get_equipment_context("EQP01-PM1")

    assert result is not None
    assert result.equipment == EquipmentNode(
        equipment_id="EQP01",
        equipment_name="Photo Scanner 01",
        model_code="PH-9000",
        area_id="photo",
        step_id="CT-PHOTO",
    )
    assert result.area == AreaNode(
        area_id="photo",
        area_name="Photolithography",
    )
    assert result.step == ProcessStepNode(
        step_id="CT-PHOTO",
        step_name="Coat Track",
        step_seq=1,
    )
    assert result.sibling_chambers == [
        ChamberNode(
            chamber_id="EQP01-PM2",
            equipment_id="EQP01",
            chamber_no=2,
            model_code="PH-9000",
            area_id="photo",
            step_id="CT-PHOTO",
        ),
    ]
    assert result.parameters == [
        ParameterNode(parameter_id="PH_FOCUS", parameter_name="Focus", unit="um")
    ]
    assert result.relations == [
        GraphRelationRef(
            relation_id="REL-1",
            relation_type="PART_OF",
            from_label="Chamber",
            from_business_id="chamber_id=s:EQP01-PM1",
            to_label="Equipment",
            to_business_id="equipment_id=s:EQP01",
        )
    ]
    assert result.graph_revision == REVISION


def test_graph_service_returns_none_for_missing_chamber() -> None:
    assert GraphService(FakeGraphRepository()).get_equipment_context("missing") is None


def test_graph_repository_query_is_read_only_and_not_full_graph_scan() -> None:
    query = GraphQueryRepository.CONTEXT_QUERY

    assert "MATCH (n)" not in query
    assert "MATCH (a)-[r]->(b)" not in query
    assert "DETACH DELETE" not in query
    assert "MERGE " not in query
    assert "CREATE " not in query
    assert "Chamber {chamber_id: $chamber_id}" in query


def test_graph_repository_maps_raw_neo4j_row() -> None:
    repository = GraphQueryRepository(
        driver_factory=lambda: _Driver(),
        graph_revision_loader=lambda: REVISION,
    )

    result = repository.get_equipment_context("EQP01-PM1")

    assert result is not None
    assert result.graph_revision == REVISION
    assert result.chamber["chamber_id"] == "EQP01-PM1"
    assert result.equipment["equipment_id"] == "EQP01"
    assert result.relations == [
        {
            "relation_id": "REL-x",
            "relation_type": "PART_OF",
            "from_label": "Chamber",
            "from_business_id": "chamber_id=s:EQP01-PM1",
            "to_label": "Equipment",
            "to_business_id": "equipment_id=s:EQP01",
        }
    ]


class _Driver:
    def session(self) -> "_Session":
        return _Session()


class _Session:
    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def run(self, query: str, parameters: dict[str, Any]) -> "_Result":
        assert query == GraphQueryRepository.CONTEXT_QUERY
        assert parameters == {"chamber_id": "EQP01-PM1"}
        return _Result()


class _Result:
    def single(self) -> dict[str, Any]:
        return {
            "chamber": {"chamber_id": "EQP01-PM1"},
            "equipment": {"equipment_id": "EQP01", "model_code": "PH-9000"},
            "model": {"model_code": "PH-9000"},
            "area": {"area_id": "photo"},
            "step": {"step_id": "CT-PHOTO", "step_name": "Photo"},
            "sibling_chambers": [],
            "adjacent_steps": [],
            "parameters": [],
            "relations": [
                {
                    "relation_id": "REL-x",
                    "relation_type": "PART_OF",
                    "from_label": "Chamber",
                    "from_properties": {"chamber_id": "EQP01-PM1"},
                    "to_label": "Equipment",
                    "to_properties": {"equipment_id": "EQP01"},
                }
            ],
        }
