from fastapi import APIRouter

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.schemas import DocumentHit, DocumentSearchRequest
from app.knowledge.service import DocumentSearchService

router = APIRouter(tags=["Knowledge"])


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
