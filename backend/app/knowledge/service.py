"""Knowledge application 서비스."""

from __future__ import annotations

from app.common.tool_contracts import (
    DocumentHit,
    EquipmentContextToolResult,
)
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.embedding import embed_query
from app.knowledge.graph_query import GraphQueryRepository
from app.knowledge.repository import (
    ChamberGraphRepository,
    DocumentRepository,
    LotHistoryContextRepository,
)
from app.knowledge.schemas import (
    ChamberRelationResponse,
    DocumentChunkItem,
    DocumentDetailResponse,
)


# ==================
# 문서 RAG
# ==================
class DocumentSearchService:
    """API와 Tool이 공유하는 문서 검색 서비스."""

    def __init__(self, repository: DocumentSearchRepository) -> None:
        self._repository = repository

    def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        model_code: str | None = None,
        doc_type: str | None = None,
    ) -> list[DocumentHit]:
        normalized_model_code = (
            model_code.strip().upper() if model_code and model_code.strip() else None
        )
        normalized_doc_type = (
            doc_type.strip().upper() if doc_type and doc_type.strip() else None
        )

        query_vector = embed_query(query)
        rows = self._repository.search(
            query_vector,
            top_k=top_k,
            model_code=normalized_model_code,
            doc_type=normalized_doc_type,
        )
        return [DocumentHit.model_validate(row) for row in rows]


class DocumentService:
    """Knowledge 문서 상세 조회 서비스."""

    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    def get_document(self, document_id: str) -> DocumentDetailResponse | None:
        document = self._repository.get_document_meta(document_id)
        if document is None:
            return None
        chunks = self._repository.list_document_chunks(document_id)
        return DocumentDetailResponse(
            document_id=str(document["doc_id"]),
            title=str(document["title"]),
            doc_type=document["doc_type"],
            model_code=document["model_code"],
            source_path=document["source_path"],
            version=document["version"],
            chunks=[
                DocumentChunkItem(
                    chunk_id=str(chunk["chunk_id"]),
                    chunk_seq=int(chunk["chunk_seq"]),
                    section_title=chunk["section_title"],
                    content=str(chunk["content"]),
                )
                for chunk in chunks
            ],
        )


class GraphService:
    """API 그래프 context 조회 서비스."""

    def __init__(self, repository: ChamberGraphRepository | None = None) -> None:
        self._repository = repository or ChamberGraphRepository()

    def get_chamber_relations(self, chamber_id: str) -> ChamberRelationResponse | None:
        projection = self._repository.get_chamber_graph_projection(chamber_id)
        if projection is None:
            return None

        return ChamberRelationResponse.model_validate(
            {
                "root_node_id": projection.root_node_id,
                "nodes": projection.nodes,
                "relationships": projection.relationships,
                "graph_revision": projection.graph_revision,
            }
        )


class ProductionContextService:
    """LOT/WAFER의 실제 이력을 읽어 화면 관계 노드로 projection한다.

    Agent의 routing 판단이나 Neo4j ontology를 바꾸지 않는다. ``lot_hist_id``는
    wafer의 특정 공정 처리 건을 식별하므로 같은 wafer가 다른 step을 거친 기록도
    안전하게 구분된다.
    """

    def __init__(self, repository: LotHistoryContextRepository) -> None:
        self._repository = repository

    def merge_chamber_history(
        self,
        graph: ChamberRelationResponse,
        chamber_id: str,
    ) -> ChamberRelationResponse:
        rows = self._repository.list_chamber_history(chamber_id)
        nodes = {node.id: node.model_dump() for node in graph.nodes}
        relationships = {relationship.id: relationship.model_dump() for relationship in graph.relationships}

        for row in rows:
            lot_id = str(row["lot_id"])
            lot_hist_id = str(row["lot_hist_id"])
            lot_node_id = f"Lot:{lot_id}"
            wafer_node_id = f"Wafer:{lot_hist_id}"
            nodes.setdefault(
                lot_node_id,
                {
                    "id": lot_node_id,
                    "label": "Lot",
                    "business_id": lot_id,
                    "display_name": lot_id,
                    "properties": {"lot_id": lot_id, "source_system": "POSTGRES_LOT_HISTORY"},
                },
            )
            nodes[wafer_node_id] = {
                "id": wafer_node_id,
                "label": "Wafer",
                "business_id": str(row["wafer_id"]),
                "display_name": str(row["wafer_id"]),
                "properties": {
                    "lot_hist_id": lot_hist_id,
                    "lot_id": lot_id,
                    "wafer_id": str(row["wafer_id"]),
                    "wafer_no": row["wafer_no"],
                    "step_id": row["step_id"],
                    "recipe_id": row["recipe_id"],
                    "track_in_at": row["track_in_at"],
                    "track_out_at": row["track_out_at"],
                    "chamber_wafer_cum": row["chamber_wafer_cum"],
                    "source_system": "POSTGRES_LOT_HISTORY",
                },
            }
            relationships[f"PG-CONTAINS-{lot_hist_id}"] = {
                "id": f"PG-CONTAINS-{lot_hist_id}",
                "type": "CONTAINS",
                "source": lot_node_id,
                "target": wafer_node_id,
            }
            relationships[f"PG-PROCESSED-IN-{lot_hist_id}"] = {
                "id": f"PG-PROCESSED-IN-{lot_hist_id}",
                "type": "PROCESSED_IN",
                "source": wafer_node_id,
                "target": f"Chamber:{chamber_id}",
            }

        return ChamberRelationResponse.model_validate(
            {
                "root_node_id": graph.root_node_id,
                "nodes": list(nodes.values()),
                "relationships": list(relationships.values()),
                "graph_revision": graph.graph_revision,
            }
        )


class EquipmentContextService:
    """Agent Tool 전용 compact 그래프 context 조회 서비스."""

    def __init__(self, repository: GraphQueryRepository | None = None) -> None:
        self._repository = repository or GraphQueryRepository()

    def get_equipment_context(
        self,
        chamber_id: str,
    ) -> EquipmentContextToolResult | None:
        payload = self._repository.get_equipment_context_payload(chamber_id)
        if payload is None:
            return None

        return EquipmentContextToolResult.model_validate({"ok": True, **payload})
