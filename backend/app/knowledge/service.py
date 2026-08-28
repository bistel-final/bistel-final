"""Knowledge application 서비스."""

from __future__ import annotations

from app.common.tool_contracts import (
    DocumentHit,
    EquipmentContextToolResult,
)
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.embedding import embed_query
from app.knowledge.graph_query import GraphQueryRepository
from app.knowledge.repository import ChamberGraphRepository, DocumentRepository
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
    ) -> list[DocumentHit]:
        normalized_model_code = (
            model_code.strip().upper() if model_code and model_code.strip() else None
        )

        query_vector = embed_query(query)
        rows = self._repository.search(
            query_vector,
            top_k=top_k,
            model_code=normalized_model_code,
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
