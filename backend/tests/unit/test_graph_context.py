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
from app.knowledge.graph_query import (
    EquipmentContextRow,
    GraphQueryRepository,
    load_graph_revision,
)
from app.knowledge.service import EquipmentContext, GraphService
from app.knowledge.tools import get_equipment_context as get_equipment_context_tool

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
    driver = _Driver()
    repository = GraphQueryRepository(
        driver_factory=lambda: driver,
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
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
    assert driver.session_options == {
        "database": "neo4j",
        "default_access_mode": "READ",
    }


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
    repository = GraphQueryRepository(
        driver_factory=lambda: _Driver(relation_id=None),
        graph_revision_loader=lambda: REVISION,
        database="neo4j",
    )

    try:
        repository.get_equipment_context("EQP01-PM1")
    except RuntimeError as exc:
        assert "relation_id" in str(exc)
    else:
        raise AssertionError("missing relation_id must fail fast")


def test_get_equipment_context_tool_returns_common_success_contract(
    monkeypatch: Any,
) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> EquipmentContext:
            assert chamber_id == "EQP01-PM1"
            return EquipmentContext(
                equipment=EquipmentNode(
                    equipment_id="EQP01",
                    equipment_name="Photo Scanner 01",
                    model_code="PH-9000",
                    area_id="photo",
                    step_id="CT-PHOTO",
                ),
                area=AreaNode(area_id="photo", area_name="Photolithography"),
                step=ProcessStepNode(step_id="CT-PHOTO", step_name="Photo"),
                sibling_chambers=[],
                adjacent_steps=[],
                parameters=[],
                relations=[
                    GraphRelationRef(
                        relation_id="REL-1",
                        relation_type="PART_OF",
                        from_label="Chamber",
                        from_business_id="chamber_id=s:EQP01-PM1",
                        to_label="Equipment",
                        to_business_id="equipment_id=s:EQP01",
                    )
                ],
                graph_revision=REVISION,
            )

    monkeypatch.setattr("app.knowledge.tools.GraphService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "EQP01-PM1"})

    assert result.ok is True
    assert result.reason == ""
    assert result.equipment is not None
    assert result.equipment.equipment_id == "EQP01"
    assert result.graph_revision == REVISION
    assert result.relations[0].relation_id == "REL-1"


def test_get_equipment_context_tool_returns_not_found(monkeypatch: Any) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> None:
            assert chamber_id == "missing"
            return None

    monkeypatch.setattr("app.knowledge.tools.GraphService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "missing"})

    assert result.ok is False
    assert result.reason == "NOT_FOUND: chamber_id=missing"
    assert result.equipment is None
    assert result.graph_revision is None
    assert result.relations == []


def test_get_equipment_context_tool_returns_timeout(monkeypatch: Any) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> None:
            raise TimeoutError("neo4j read timed out")

    monkeypatch.setattr("app.knowledge.tools.GraphService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "EQP01-PM1"})

    assert result.ok is False
    assert result.reason == "TIMEOUT: neo4j read timed out"
    assert result.equipment is None


def test_get_equipment_context_tool_returns_dependency_failure(
    monkeypatch: Any,
) -> None:
    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_equipment_context(self, chamber_id: str) -> None:
            raise RuntimeError("marker invalid")

    monkeypatch.setattr("app.knowledge.tools.GraphService", FakeService)

    result = get_equipment_context_tool.invoke({"chamber_id": "EQP01-PM1"})

    assert result.ok is False
    assert result.reason == "DEPENDENCY_ERROR: marker invalid"
    assert result.equipment is None


class _Driver:
    def __init__(self, relation_id: str | None = "REL-x") -> None:
        self.relation_id = relation_id
        self.session_options: dict[str, Any] | None = None

    def session(self, **options: Any) -> _Session:
        self.session_options = options
        return _Session(self.relation_id)


class _Session:
    def __init__(self, relation_id: str | None) -> None:
        self.relation_id = relation_id

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def run(self, query: str, parameters: dict[str, Any]) -> _Result:
        assert query == GraphQueryRepository.CONTEXT_QUERY
        assert parameters == {"chamber_id": "EQP01-PM1"}
        return _Result(self.relation_id)


class _Result:
    def __init__(self, relation_id: str | None) -> None:
        self.relation_id = relation_id

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
                    "relation_id": self.relation_id,
                    "relation_type": "PART_OF",
                    "from_label": "Chamber",
                    "from_properties": {"chamber_id": "EQP01-PM1"},
                    "to_label": "Equipment",
                    "to_properties": {"equipment_id": "EQP01"},
                }
            ],
        }


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
