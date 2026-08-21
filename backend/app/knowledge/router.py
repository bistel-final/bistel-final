from fastapi import APIRouter

from app.common.tool_contracts import DocumentHit
from app.knowledge.repository import DocumentSearchRepository
from app.knowledge.schemas import DocumentSearchRequest
from app.knowledge.service import DocumentSearchService

router = APIRouter(tags=["Knowledge"])


@router.post("/documents/search", response_model=list[DocumentHit])
def search_documents(request: DocumentSearchRequest) -> list[DocumentHit]:
    """Knowledge RAG 문서를 유사도 순으로 검색한다."""

    service = DocumentSearchService(DocumentSearchRepository())
    return service.search(
        request.query,
        top_k=request.top_k,
        model_code=request.model_code,
    )
