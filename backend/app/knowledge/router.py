from fastapi import APIRouter, HTTPException

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.repository import ChamberGraphRepository
from app.knowledge.schemas import (
    ChamberRelationResponse,
    DocumentHit,
    DocumentSearchRequest,
)
from app.knowledge.service import DocumentSearchService, GraphService

router = APIRouter(tags=["Knowledge"])


# ==================
# 그래프 관계
# ==================
@router.get(
    "/relations/chambers/{chamber_id}",
    response_model=ChamberRelationResponse,
)
def get_chamber_relations(chamber_id: str) -> ChamberRelationResponse:
    """챔버 기준 Neo4j 그래프 projection을 조회한다."""

    service = GraphService(ChamberGraphRepository())
    response = service.get_chamber_relations(chamber_id)
    if response is None:
        raise HTTPException(status_code=404, detail="chamber relation not found")
    return response


# ==================
# 문서 RAG
# ==================
@router.post("/documents/search", response_model=list[DocumentHit])
def search_documents(request: DocumentSearchRequest) -> list[DocumentHit]:
    """Knowledge RAG 문서를 유사도 순으로 검색한다."""

    engine = pool_factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
    service = DocumentSearchService(DocumentSearchRepository(engine))
    hits = service.search(
        request.query,
        top_k=request.top_k,
        model_code=request.model_code,
    )
    return [DocumentHit.from_tool_hit(hit) for hit in hits]
