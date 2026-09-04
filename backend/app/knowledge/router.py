from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.repository import (
    ChamberGraphRepository,
    DocumentRepository,
    LotHistoryContextRepository,
)
from app.knowledge.schemas import (
    ChamberRelationResponse,
    DocumentDetailResponse,
    DocumentHit,
    DocumentSearchRequest,
)
from app.knowledge.service import (
    DocumentSearchService,
    DocumentService,
    GraphService,
    ProductionContextService,
)

router = APIRouter(tags=["Knowledge"])


# ==================
# 그래프 관계
# ==================
@router.get(
    "/relations/chambers/{chamber_id}",
    response_model=ChamberRelationResponse,
)
def get_chamber_relations(
    chamber_id: str,
    include_production_context: bool = False,
) -> ChamberRelationResponse | JSONResponse:
    """챔버 기준 구조 ontology와 선택적 PostgreSQL 생산 이력을 조회한다."""

    service = GraphService(ChamberGraphRepository())
    response = service.get_chamber_relations(chamber_id)
    if response is None:
        raise HTTPException(status_code=404, detail="chamber relation not found")
    if include_production_context:
        engine = pool_factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
        production_context = ProductionContextService(
            LotHistoryContextRepository(engine)
        )
        response = production_context.merge_chamber_history(
            response,
            chamber_id,
        )
    # 기본 응답은 기존 4개 필드를 그대로 유지하고, opt-in metadata만 조건부로 보낸다.
    if response.production_context is None:
        return JSONResponse(
            response.model_dump(mode="json", exclude={"production_context"})
        )
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
        doc_type=request.doc_type,
    )
    return [DocumentHit.from_tool_hit(hit) for hit in hits]


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
def get_document(document_id: str) -> DocumentDetailResponse:
    """Knowledge RAG 문서와 청크 본문을 순서대로 조회한다."""

    engine = pool_factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
    service = DocumentService(DocumentRepository(engine))
    document = service.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return document
