from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import Query

from app.common.tool_contracts import (
    EquipmentContextToolResult,
)
from app.common.tool_timeouts import DependencyTimeoutError
from app.knowledge.exceptions import GraphProjectionShapeError
from app.knowledge.graph_query import (
    GraphQueryRepository,
)
from app.knowledge.graph_revision import load_graph_revision
from app.knowledge.repository import (
    ChamberGraphProjection,
    ChamberGraphRepository,
    LotHistoryContextRepository,
)
from app.knowledge.router import router as knowledge_router
from app.knowledge.schemas import ChamberRelationResponse
from app.knowledge.service import (
    EquipmentContextService,
    GraphService,
    ProductionContextService,
)
from app.knowledge.tools import get_equipment_context as get_equipment_context_tool

REVISION = "3474debee491ea5c699080109d748a4922ad0566a3b84568e9067053de2fa2eb"

TOOL_CONTEXT_FIXTURES: dict[str, dict[str, object]] = {
    "EQP01-PM1": {
        "chamber_id": "EQP01-PM1",
        "equipment_id": "EQP01",
        "sibling_chamber_ids": ["EQP01-PM2"],
        "area": "Photo",
        "model_code": "PH-9000",
        "process_step_id": "CT-PHOTO",
        "upstream_process_step_ids": [],
        "downstream_process_step_ids": ["CT-ETCH"],
        "parameter_ids": ["PH_DEV", "PH_DOSE", "PH_FOCUS", "PH_PEB"],
        "graph_revision": REVISION,
    },
    "EQP04-PM2": {
        "chamber_id": "EQP04-PM2",
        "equipment_id": "EQP04",
        "sibling_chamber_ids": ["EQP04-PM1"],
        "area": "Etch",
        "model_code": "ET-7500",
        "process_step_id": "CT-ETCH",
        "upstream_process_step_ids": ["CT-PHOTO"],
        "downstream_process_step_ids": [],
        "parameter_ids": ["ET_CF4", "ET_ESC", "ET_PRES", "ET_REFL"],
        "graph_revision": REVISION,
    },
}

CHAMBER_GRAPH_FIXTURE = ChamberGraphProjection(
    root_node_id="Chamber:EQP04-PM2",
    nodes=[
        {
            "id": "Chamber:EQP04-PM2",
            "label": "Chamber",
            "business_id": "EQP04-PM2",
            "display_name": "EQP04-PM2",
            "properties": {"chamber_id": "EQP04-PM2", "chamber_no": 2},
        },
        {
            "id": "Equipment:EQP04",
            "label": "Equipment",
            "business_id": "EQP04",
            "display_name": "Dry Etcher 04",
            "properties": {"equipment_id": "EQP04", "model_code": "ET-7500"},
        },
        {
            "id": "ProcessStep:CT-ETCH",
            "label": "ProcessStep",
            "business_id": "CT-ETCH",
            "display_name": "Etch",
            "properties": {"step_id": "CT-ETCH", "step_name": "Etch"},
        },
    ],
    relationships=[
        {
            "id": "REL-PART-04-2",
            "type": "PART_OF",
            "source": "Chamber:EQP04-PM2",
            "target": "Equipment:EQP04",
        },
        {
            "id": "REL-PERFORMS-04",
            "type": "PERFORMS",
            "source": "Equipment:EQP04",
            "target": "ProcessStep:CT-ETCH",
        },
    ],
    graph_revision=REVISION,
)


class FakeGraphRepository:
    def get_chamber_graph_projection(
        self,
        chamber_id: str,
    ) -> ChamberGraphProjection | None:
        if chamber_id == "missing":
            return None
        return ChamberGraphProjection(
            root_node_id=f"Chamber:{chamber_id}",
            nodes=[
                {
                    "id": f"Chamber:{chamber_id}",
                    "label": "Chamber",
                    "business_id": chamber_id,
                    "display_name": chamber_id,
                    "properties": {"chamber_id": chamber_id, "chamber_no": 1},
                },
                {
                    "id": "Equipment:EQP01",
                    "label": "Equipment",
                    "business_id": "EQP01",
                    "display_name": "Photo Scanner 01",
                    "properties": {"equipment_id": "EQP01"},
                },
            ],
            relationships=[
                {
                    "id": "REL-1",
                    "type": "PART_OF",
                    "source": f"Chamber:{chamber_id}",
                    "target": "Equipment:EQP01",
                },
            ],
            graph_revision=REVISION,
        )

    def get_equipment_context_payload(
        self, chamber_id: str
    ) -> dict[str, object] | None:
        if chamber_id == "missing":
            return None
        return {
            "chamber_id": chamber_id,
            "equipment_id": "EQP01",
            "sibling_chamber_ids": ["EQP01-PM2"],
            "area": "photo",
            "model_code": "PH-9000",
            "process_step_id": "CT-PHOTO",
            "upstream_process_step_ids": [],
            "downstream_process_step_ids": ["CT-ETCH"],
            "parameter_ids": ["PH_FOCUS"],
            "graph_revision": REVISION,
        }


def test_graph_service_maps_chamber_relation_graph_projection() -> None:
    result = GraphService(FakeGraphRepository()).get_chamber_relations("EQP01-PM1")

    assert result is not None
    assert result.root_node_id == "Chamber:EQP01-PM1"
    assert result.graph_revision == REVISION
    assert [node.id for node in result.nodes] == [
        "Chamber:EQP01-PM1",
        "Equipment:EQP01",
    ]
    assert [relationship.model_dump() for relationship in result.relationships] == [
        {
            "id": "REL-1",
            "type": "PART_OF",
            "source": "Chamber:EQP01-PM1",
            "target": "Equipment:EQP01",
        }
    ]


def test_graph_service_returns_none_for_missing_chamber() -> None:
    assert GraphService(FakeGraphRepository()).get_chamber_relations("missing") is None


def test_equipment_context_service_returns_compact_tool_context() -> None:
    result = EquipmentContextService(FakeGraphRepository()).get_equipment_context(
        "EQP01-PM1"
    )

    assert result is not None
    assert result.ok is True
    assert result.chamber_id == "EQP01-PM1"
    assert result.equipment_id == "EQP01"
    assert result.sibling_chamber_ids == ["EQP01-PM2"]
    assert result.area == "photo"
    assert result.model_code == "PH-9000"
    assert result.process_step_id == "CT-PHOTO"
    assert result.upstream_process_step_ids == []
    assert result.downstream_process_step_ids == ["CT-ETCH"]
    assert result.parameter_ids == ["PH_FOCUS"]
    assert result.graph_revision == REVISION


def test_equipment_context_service_matches_final_compact_fixtures() -> None:
    class FixtureRepository:
        def get_equipment_context_payload(
            self,
            chamber_id: str,
        ) -> dict[str, object] | None:
            return TOOL_CONTEXT_FIXTURES.get(chamber_id)

    service = EquipmentContextService(FixtureRepository())

    for chamber_id, expected in TOOL_CONTEXT_FIXTURES.items():
        result = service.get_equipment_context(chamber_id)

        assert result is not None
        assert result.model_dump() == {"ok": True, "reason": "", **expected}


def test_equipment_context_service_uses_next_step_direction_for_process_flow() -> None:
    class EtchGraphRepository:
        def get_equipment_context_payload(
            self,
            chamber_id: str,
        ) -> dict[str, object]:
            return {
                "chamber_id": chamber_id,
                "equipment_id": "EQP04",
                "sibling_chamber_ids": ["EQP04-PM1"],
                "area": "Etch",
                "model_code": "ET-7500",
                "process_step_id": "CT-ETCH",
                "upstream_process_step_ids": ["CT-PHOTO"],
                "downstream_process_step_ids": [],
                "parameter_ids": ["ET_CF4"],
                "graph_revision": REVISION,
            }

    result = EquipmentContextService(EtchGraphRepository()).get_equipment_context(
        "EQP04-PM2"
    )

    assert result is not None
    assert result.process_step_id == "CT-ETCH"
    assert result.upstream_process_step_ids == ["CT-PHOTO"]
    assert result.downstream_process_step_ids == []


def test_chamber_relations_api_returns_graph_projection(monkeypatch: Any) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_chamber_relations(
            self,
            chamber_id: str,
        ) -> ChamberRelationResponse:
            assert chamber_id == "EQP04-PM2"
            return ChamberRelationResponse.model_validate(
                {
                    "root_node_id": CHAMBER_GRAPH_FIXTURE.root_node_id,
                    "nodes": CHAMBER_GRAPH_FIXTURE.nodes,
                    "relationships": CHAMBER_GRAPH_FIXTURE.relationships,
                    "graph_revision": CHAMBER_GRAPH_FIXTURE.graph_revision,
                }
            )

    app = FastAPI()
    app.include_router(knowledge_router)
    monkeypatch.setattr("app.knowledge.router.GraphService", FakeService)

    response = TestClient(app).get("/relations/chambers/EQP04-PM2")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "root_node_id": "Chamber:EQP04-PM2",
        "nodes": CHAMBER_GRAPH_FIXTURE.nodes,
        "relationships": CHAMBER_GRAPH_FIXTURE.relationships,
        "graph_revision": REVISION,
    }
    assert "relations" not in body
    assert "relation_ids" not in body


def test_chamber_relations_api_returns_404_for_missing_chamber(
    monkeypatch: Any,
) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_chamber_relations(self, chamber_id: str) -> None:
            assert chamber_id == "missing"
            return None

    app = FastAPI()
    app.include_router(knowledge_router)
    monkeypatch.setattr("app.knowledge.router.GraphService", FakeService)

    response = TestClient(app).get("/relations/chambers/missing")

    assert response.status_code == 404


def test_chamber_relations_api_adds_production_context_only_when_requested(
    monkeypatch: Any,
) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_chamber_relations(self, chamber_id: str) -> ChamberRelationResponse:
            assert chamber_id == "EQP04-PM2"
            return ChamberRelationResponse.model_validate(
                {
                    "root_node_id": CHAMBER_GRAPH_FIXTURE.root_node_id,
                    "nodes": CHAMBER_GRAPH_FIXTURE.nodes,
                    "relationships": CHAMBER_GRAPH_FIXTURE.relationships,
                    "graph_revision": CHAMBER_GRAPH_FIXTURE.graph_revision,
                }
            )

    calls: list[object] = []

    class FakePoolFactory:
        def get_engine(self, database: object, role: object) -> object:
            calls.append((database, role))
            return object()

    class FakeHistoryRepository:
        def __init__(self, engine: object) -> None:
            calls.append(engine)

    class FakeProductionContextService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def merge_chamber_history(
            self, graph: ChamberRelationResponse, chamber_id: str
        ) -> ChamberRelationResponse:
            assert chamber_id == "EQP04-PM2"
            return ChamberRelationResponse.model_validate(
                {
                    **graph.model_dump(),
                    "nodes": [
                        *graph.model_dump()["nodes"],
                        {
                            "id": "Lot:LOT-01",
                            "label": "Lot",
                            "business_id": "LOT-01",
                            "display_name": "LOT-01",
                            "properties": {"lot_id": "LOT-01"},
                        },
                    ],
                }
            )

    monkeypatch.setattr("app.knowledge.router.GraphService", FakeService)
    monkeypatch.setattr("app.knowledge.router.pool_factory", FakePoolFactory())
    monkeypatch.setattr(
        "app.knowledge.router.LotHistoryContextRepository", FakeHistoryRepository
    )
    monkeypatch.setattr(
        "app.knowledge.router.ProductionContextService", FakeProductionContextService
    )
    app = FastAPI()
    app.include_router(knowledge_router)

    basic = TestClient(app).get("/relations/chambers/EQP04-PM2")
    assert basic.status_code == 200
    assert not calls
    assert all(node["label"] != "Lot" for node in basic.json()["nodes"])

    production = TestClient(app).get(
        "/relations/chambers/EQP04-PM2?include_production_context=true"
    )
    assert production.status_code == 200
    assert len(calls) == 2
    assert any(node["label"] == "Lot" for node in production.json()["nodes"])


def test_production_context_projects_lot_and_wafer_without_mutating_graph() -> None:
    class FakeGraphRepository:
        def get_chamber_graph_projection(
            self, chamber_id: str
        ) -> ChamberGraphProjection:
            assert chamber_id == "EQP04-PM2"
            return CHAMBER_GRAPH_FIXTURE

    class FakeHistoryRepository:
        def list_chamber_history(self, chamber_id: str) -> list[dict[str, object]]:
            assert chamber_id == "EQP04-PM2"
            return [
                {
                    "lot_hist_id": "LH-0042",
                    "lot_id": "LOT-01",
                    "wafer_id": "WAFER-01",
                    "wafer_no": 1,
                    "step_id": "CT-ETCH",
                    "recipe_id": "RECIPE02",
                    "track_in_at": "2026-08-18T09:00:00+09:00",
                    "track_out_at": "2026-08-18T09:02:00+09:00",
                    "chamber_wafer_cum": 42,
                }
            ]

    graph = GraphService(FakeGraphRepository()).get_chamber_relations("EQP04-PM2")
    assert graph is not None
    result = ProductionContextService(FakeHistoryRepository()).merge_chamber_history(
        graph, "EQP04-PM2"
    )

    lots = [node for node in result.nodes if node.label == "Lot"]
    wafers = [node for node in result.nodes if node.label == "Wafer"]
    assert lots[0].id == "Lot:LOT-01"
    assert wafers[0].id == "Wafer:LH-0042"
    assert wafers[0].properties["source_system"] == "POSTGRES_LOT_HISTORY"
    assert "fault_code" not in wafers[0].properties
    assert {relationship.type for relationship in result.relationships} >= {
        "CONTAINS",
        "PROCESSED_IN",
    }
    assert graph.graph_revision == result.graph_revision


def test_lot_history_context_query_is_read_only_and_excludes_fault_label() -> None:
    query = str(LotHistoryContextRepository.CHAMBER_CONTEXT_QUERY)

    assert "FROM lot_history" in query
    assert "WHERE chamber_id = :chamber_id" in query
    assert "fault_code" not in query
    assert all(
        token not in query.upper()
        for token in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ")
    )


def test_graph_repository_query_is_read_only_and_not_full_graph_scan() -> None:
    query = ChamberGraphRepository.GRAPH_PROJECTION_QUERY

    assert "MATCH (n)" not in query
    assert "MATCH (a)-[r]->(b)" not in query
    assert "DETACH DELETE" not in query
    assert "MERGE " not in query
    assert "CREATE " not in query
    assert "Chamber {chamber_id: $chamber_id}" in query


def test_graph_repository_uses_process_step_area_not_model_area() -> None:
    query = ChamberGraphRepository.GRAPH_PROJECTION_QUERY

    assert "(step)-[stepArea:IN_AREA]->(area:Area)" in query
    assert "(m)-[modelArea:IN_AREA]->(area:Area)" not in query


def test_graph_repository_tool_query_returns_compact_directional_payload() -> None:
    query = GraphQueryRepository.TOOL_CONTEXT_QUERY

    assert "previous:ProcessStep)-[:NEXT_STEP]->(step)" in query
    assert "(step)-[:NEXT_STEP]->(next:ProcessStep)" in query
    assert "previous.step_id) AS upstream_process_step_ids" in query
    assert "next.step_id) AS downstream_process_step_ids" in query
    assert "relation_id" not in query


def test_graph_repository_returns_api_graph_projection() -> None:
    driver = _Driver(
        graph_record={
            "root_node_id": "Chamber:EQP01-PM1",
            "nodes": [
                {
                    "id": "Equipment:EQP01",
                    "label": "Equipment",
                    "business_id": "EQP01",
                    "display_name": "EQP01",
                    "properties": {"equipment_id": "EQP01"},
                },
                {
                    "id": "Chamber:EQP01-PM1",
                    "label": "Chamber",
                    "business_id": "EQP01-PM1",
                    "display_name": "EQP01-PM1",
                    "properties": {"chamber_id": "EQP01-PM1"},
                },
                {
                    "id": "Area:Photo",
                    "label": "Area",
                    "business_id": "Photo",
                    "display_name": "Photo",
                    "properties": {"area_id": "Photo"},
                },
            ],
            "relationships": [
                {
                    "id": "REL-z",
                    "type": "PERFORMS",
                    "source": "Equipment:EQP01",
                    "target": "ProcessStep:CT-PHOTO",
                },
                {
                    "id": "REL-x",
                    "type": "PART_OF",
                    "source": "Chamber:EQP01-PM1",
                    "target": "Equipment:EQP01",
                },
            ],
        }
    )
    repository = ChamberGraphRepository(
        driver_factory=lambda: driver,
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
    )

    result = repository.get_chamber_graph_projection("EQP01-PM1")

    assert result is not None
    assert result.graph_revision == REVISION
    assert result.root_node_id == "Chamber:EQP01-PM1"
    assert result.nodes == [
        {
            "id": "Area:Photo",
            "label": "Area",
            "business_id": "Photo",
            "display_name": "Photo",
            "properties": {"area_id": "Photo"},
        },
        {
            "id": "Chamber:EQP01-PM1",
            "label": "Chamber",
            "business_id": "EQP01-PM1",
            "display_name": "EQP01-PM1",
            "properties": {"chamber_id": "EQP01-PM1"},
        },
        {
            "id": "Equipment:EQP01",
            "label": "Equipment",
            "business_id": "EQP01",
            "display_name": "EQP01",
            "properties": {"equipment_id": "EQP01"},
        },
    ]
    assert result.relationships == [
        {
            "id": "REL-x",
            "type": "PART_OF",
            "source": "Chamber:EQP01-PM1",
            "target": "Equipment:EQP01",
        },
        {
            "id": "REL-z",
            "type": "PERFORMS",
            "source": "Equipment:EQP01",
            "target": "ProcessStep:CT-PHOTO",
        },
    ]
    assert driver.session_options == {
        "database": "neo4j",
        "default_access_mode": "READ",
    }


def test_graph_repository_maps_tool_payload_row() -> None:
    driver = _Driver(
        tool_record={
            "chamber_id": "EQP04-PM2",
            "equipment_id": "EQP04",
            "sibling_chamber_ids": ["EQP04-PM1", None],
            "area": "Etch",
            "model_code": "ET-7500",
            "process_step_id": "CT-ETCH",
            "upstream_process_step_ids": ["CT-PHOTO"],
            "downstream_process_step_ids": [],
            "parameter_ids": ["ET_REFL", "ET_CF4", None],
        }
    )
    repository = GraphQueryRepository(
        driver_factory=lambda: driver,
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
    )

    result = repository.get_equipment_context_payload("EQP04-PM2")

    assert result == {
        "chamber_id": "EQP04-PM2",
        "equipment_id": "EQP04",
        "sibling_chamber_ids": ["EQP04-PM1"],
        "area": "Etch",
        "model_code": "ET-7500",
        "process_step_id": "CT-ETCH",
        "upstream_process_step_ids": ["CT-PHOTO"],
        "downstream_process_step_ids": [],
        "parameter_ids": ["ET_CF4", "ET_REFL"],
        "graph_revision": REVISION,
    }
    assert driver.session_options == {
        "database": "neo4j",
        "default_access_mode": "READ",
    }
    query = driver.queries[-1]
    assert isinstance(query, Query)
    assert query.text == GraphQueryRepository.TOOL_CONTEXT_QUERY
    assert query.timeout == 5.0


def test_graph_repository_maps_only_server_transaction_timeout_code() -> None:
    from neo4j.exceptions import Neo4jError

    timeout_error = Neo4jError._hydrate_neo4j(
        code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
        message="secret cypher detail",
    )
    driver = _Driver(run_error=timeout_error)
    repository = GraphQueryRepository(
        driver_factory=lambda: driver,
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
    )

    with pytest.raises(DependencyTimeoutError) as raised:
        repository.get_equipment_context_payload("EQP04-PM2")

    assert raised.value.reason_code == "NEO4J_TRANSACTION_TIMEOUT"
    assert "secret" not in str(raised.value)


def test_graph_repository_does_not_map_non_timeout_neo4j_code() -> None:
    from neo4j.exceptions import Neo4jError

    dependency_error = Neo4jError._hydrate_neo4j(
        code="Neo.TransientError.Transaction.DeadlockDetected",
        message="secret cypher detail",
    )
    repository = GraphQueryRepository(
        driver_factory=lambda: _Driver(run_error=dependency_error),
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
    )

    with pytest.raises(type(dependency_error)):
        repository.get_equipment_context_payload("EQP04-PM2")


def test_load_graph_revision_validates_marker_and_does_not_cache(tmp_path: Any) -> None:
    marker_root = tmp_path
    marker_path = marker_root / "neo4j_graph.neo4j.json"
    marker_path.write_text(_marker(REVISION), encoding="utf-8")

    assert load_graph_revision(marker_root=marker_root) == REVISION

    changed = "a" * 64
    marker_path.write_text(_marker(changed), encoding="utf-8")
    assert load_graph_revision(marker_root=marker_root) == changed


def test_load_graph_revision_rejects_invalid_marker(tmp_path: Any) -> None:
    marker_path = tmp_path / "neo4j_graph.neo4j.json"
    marker_path.write_text(
        _marker(REVISION, status="RESTORED"),
        encoding="utf-8",
    )

    try:
        load_graph_revision(marker_root=tmp_path)
    except RuntimeError as exc:
        assert "success" in str(exc)
    else:
        raise AssertionError("invalid marker must be rejected")


def test_relation_id_is_required_for_returned_relationships() -> None:
    repository = ChamberGraphRepository(
        driver_factory=lambda: _Driver(
            graph_record={
                "root_node_id": "Chamber:EQP01-PM1",
                "nodes": [
                    {
                        "id": "Chamber:EQP01-PM1",
                        "label": "Chamber",
                        "business_id": "EQP01-PM1",
                        "display_name": "EQP01-PM1",
                        "properties": {},
                    }
                ],
                "relationships": [
                    {
                        "type": "PART_OF",
                        "source": "Chamber:EQP01-PM1",
                        "target": "Equipment:EQP01",
                    }
                ],
            }
        ),
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
    )

    try:
        repository.get_chamber_graph_projection("EQP01-PM1")
    except GraphProjectionShapeError as exc:
        assert exc.details == {
            "reason_code": "MISSING_PROJECTION_ITEM_ID",
            "item": "relationship",
        }
    else:
        raise AssertionError("relationship id 누락은 즉시 실패해야 합니다")


def test_graph_projection_requires_root_node_id() -> None:
    repository = ChamberGraphRepository(
        driver_factory=lambda: _Driver(
            graph_record={
                "nodes": [],
                "relationships": [],
            }
        ),
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
    )

    try:
        repository.get_chamber_graph_projection("EQP01-PM1")
    except GraphProjectionShapeError as exc:
        assert exc.details == {"reason_code": "MISSING_ROOT_NODE_ID"}
    else:
        raise AssertionError("root_node_id 누락은 즉시 실패해야 합니다")


def test_graph_projection_rejects_unsupported_node_label() -> None:
    repository = ChamberGraphRepository(
        driver_factory=lambda: _Driver(
            graph_record={
                "root_node_id": "Chamber:EQP01-PM1",
                "nodes": [
                    {
                        "id": "Recipe:RECIPE01",
                        "label": "Recipe",
                        "business_id": "RECIPE01",
                        "display_name": "Recipe 01",
                        "properties": {},
                    }
                ],
                "relationships": [],
            }
        ),
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
    )

    try:
        repository.get_chamber_graph_projection("EQP01-PM1")
    except GraphProjectionShapeError as exc:
        assert exc.details == {
            "reason_code": "UNSUPPORTED_NODE_LABEL",
            "label": "Recipe",
        }
    else:
        raise AssertionError("지원하지 않는 node label은 즉시 실패해야 합니다")


def test_graph_projection_rejects_unsupported_relationship_type() -> None:
    repository = ChamberGraphRepository(
        driver_factory=lambda: _Driver(
            graph_record={
                "root_node_id": "Chamber:EQP01-PM1",
                "nodes": [],
                "relationships": [
                    {
                        "id": "REL-recipe",
                        "type": "STEP_OF",
                        "source": "RecipeStep:RECIPE01+1",
                        "target": "Recipe:RECIPE01",
                    }
                ],
            }
        ),
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
    )

    try:
        repository.get_chamber_graph_projection("EQP01-PM1")
    except GraphProjectionShapeError as exc:
        assert exc.details == {
            "reason_code": "UNSUPPORTED_RELATIONSHIP_TYPE",
            "type": "STEP_OF",
        }
    else:
        raise AssertionError("지원하지 않는 relationship type은 즉시 실패해야 합니다")


def test_get_equipment_context_tool_returns_common_success_contract(
    monkeypatch: Any,
) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> EquipmentContextToolResult:
            assert chamber_id == "EQP01-PM1"
            return EquipmentContextToolResult(
                ok=True,
                chamber_id="EQP01-PM1",
                equipment_id="EQP01",
                area="photo",
                model_code="PH-9000",
                process_step_id="CT-PHOTO",
                downstream_process_step_ids=["CT-ETCH"],
                parameter_ids=["PH_FOCUS"],
                graph_revision=REVISION,
            )

    monkeypatch.setattr("app.knowledge.tools.EquipmentContextService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "EQP01-PM1"})

    assert result.ok is True
    assert result.reason == ""
    assert result.chamber_id == "EQP01-PM1"
    assert result.equipment_id == "EQP01"
    assert result.model_code == "PH-9000"
    assert result.process_step_id == "CT-PHOTO"
    assert result.upstream_process_step_ids == []
    assert result.downstream_process_step_ids == ["CT-ETCH"]
    assert result.parameter_ids == ["PH_FOCUS"]
    assert result.graph_revision == REVISION


def test_get_equipment_context_tool_returns_not_found(monkeypatch: Any) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> None:
            assert chamber_id == "missing"
            return None

    monkeypatch.setattr("app.knowledge.tools.EquipmentContextService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "missing"})

    assert result.ok is False
    assert result.reason == "NOT_FOUND: chamber_id=missing"
    assert result.chamber_id is None
    assert result.graph_revision is None


def test_get_equipment_context_tool_returns_timeout(monkeypatch: Any) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> None:
            raise TimeoutError("neo4j read timed out")

    monkeypatch.setattr("app.knowledge.tools.EquipmentContextService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "EQP01-PM1"})

    assert result.ok is False
    assert result.reason == "TIMEOUT: neo4j read timed out"
    assert result.chamber_id is None


def test_get_equipment_context_tool_maps_dependency_timeout_reason_code(
    monkeypatch: Any,
) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> None:
            error = DependencyTimeoutError("NEO4J_TRANSACTION_TIMEOUT")
            error.args = ("bolt://neo4j:secret@localhost:7687",)
            raise error

    monkeypatch.setattr("app.knowledge.tools.EquipmentContextService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "EQP01-PM1"})

    assert result.ok is False
    assert result.reason == "TIMEOUT: NEO4J_TRANSACTION_TIMEOUT"
    assert "bolt://" not in result.reason
    assert "secret" not in result.reason
    assert result.chamber_id is None


def test_get_equipment_context_tool_returns_dependency_failure(
    monkeypatch: Any,
) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> None:
            raise RuntimeError(
                "MATCH (c:Chamber {chamber_id: $chamber_id}) "
                "bolt://neo4j:secret@localhost:7687 marker invalid"
            )

    monkeypatch.setattr("app.knowledge.tools.EquipmentContextService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "EQP01-PM1"})

    assert result.ok is False
    assert result.reason == "DEPENDENCY_ERROR: 장비 그래프 context 의존성 오류"
    assert "MATCH" not in result.reason
    assert "bolt://" not in result.reason
    assert "secret" not in result.reason
    assert result.chamber_id is None


def test_get_equipment_context_tool_sanitizes_graph_shape_failure(
    monkeypatch: Any,
) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> EquipmentContextToolResult:
            assert chamber_id == "EQP99-PM1"
            return EquipmentContextToolResult(
                ok=True,
                chamber_id="EQP99-PM1",
                equipment_id="EQP99",
                model_code="XX-1000",
                graph_revision=REVISION,
            )

    monkeypatch.setattr("app.knowledge.tools.EquipmentContextService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "EQP99-PM1"})

    assert result.ok is False
    assert (
        result.reason == "GRAPH_SHAPE_ERROR: 장비 graph context 필수 값이 누락됐습니다"
    )
    assert "EQP99" not in result.reason
    assert result.chamber_id is None


class _Driver:
    def __init__(
        self,
        graph_record: dict[str, Any] | None = None,
        tool_record: dict[str, Any] | None = None,
        run_error: Exception | None = None,
    ) -> None:
        self.graph_record = graph_record
        self.tool_record = tool_record
        self.run_error = run_error
        self.session_options: dict[str, Any] | None = None
        self.queries: list[object] = []

    def session(self, **options: Any) -> _Session:
        self.session_options = options
        return _Session(self, self.graph_record, self.tool_record)


class _Session:
    def __init__(
        self,
        driver: _Driver,
        graph_record: dict[str, Any] | None,
        tool_record: dict[str, Any] | None,
    ) -> None:
        self.driver = driver
        self.graph_record = graph_record
        self.tool_record = tool_record

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def run(self, query: object, parameters: dict[str, Any]) -> _PayloadResult:
        self.driver.queries.append(query)
        if self.driver.run_error is not None:
            raise self.driver.run_error
        query_text = query.text if isinstance(query, Query) else query
        if query_text == ChamberGraphRepository.GRAPH_PROJECTION_QUERY:
            assert parameters == {"chamber_id": "EQP01-PM1"}
            return _PayloadResult(self.graph_record)
        if query_text == GraphQueryRepository.TOOL_CONTEXT_QUERY:
            assert parameters == {"chamber_id": "EQP04-PM2"}
            return _PayloadResult(self.tool_record)
        raise AssertionError(f"unexpected query: {query}")


class _PayloadResult:
    def __init__(self, record: dict[str, Any] | None) -> None:
        self.record = record

    def single(self) -> dict[str, Any] | None:
        return self.record


def _marker(revision: str, *, status: str = "APPLIED") -> str:
    return (
        "{"
        f'"dataset_epoch":"fdc_final_20260818",'
        f'"database":"neo4j",'
        f'"source_member_sha256":"51604707c9a0f3bc97b21773b7bd43d0049f2dacf322042c36f090ec63c74eea",'
        f'"status":"{status}",'
        f'"node_count":44,'
        f'"relationship_count":85,'
        f'"relation_id_duplicates":0,'
        f'"expected_graph_fingerprint_sha256":"{revision}",'
        f'"actual_graph_fingerprint_sha256":"{revision}"'
        "}"
    )
