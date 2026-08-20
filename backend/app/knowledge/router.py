from fastapi import APIRouter

from app.knowledge.repository import DocumentSearchRepository
from app.knowledge.schemas import DocumentSearchRequest, DocumentSearchResponse
from app.knowledge.service import DocumentSearchService

router = APIRouter(tags=["Knowledge"])


@router.post("/documents/search", response_model=DocumentSearchResponse)
def search_documents(request: DocumentSearchRequest) -> DocumentSearchResponse:
    """Knowledge RAG 문서를 유사도 순으로 검색한다."""

    service = DocumentSearchService(DocumentSearchRepository())
    hits = service.search(
        request.query,
        top_k=request.top_k,
        model_code=request.model_code,
    )
    return DocumentSearchResponse(query=request.query, hits=hits, count=len(hits))
