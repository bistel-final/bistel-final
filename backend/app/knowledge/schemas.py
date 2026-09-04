from enum import StrEnum

from pydantic import Field

from app.common.ids import NonEmptyId
from app.common.schemas import ApiModel
from app.common.tool_contracts import DocumentHit as ToolDocumentHit


class DocumentType(StrEnum):
    SPEC = "SPEC"
    MANUAL = "MANUAL"
    TROUBLESHOOT = "TROUBLESHOOT"


# ==================
# 그래프 관계
# ==================
class GraphNode(ApiModel):
    id: NonEmptyId
    label: NonEmptyId
    business_id: NonEmptyId
    display_name: str = Field(min_length=1)
    properties: dict[str, object]


class GraphRelationship(ApiModel):
    id: NonEmptyId
    type: NonEmptyId
    source: NonEmptyId
    target: NonEmptyId


class ProductionContextMeta(ApiModel):
    returned_count: int = Field(ge=0)
    truncated: bool


class ChamberRelationResponse(ApiModel):
    root_node_id: NonEmptyId
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]
    graph_revision: NonEmptyId
    production_context: ProductionContextMeta | None = None


# ==================
# 문서 RAG
# ==================
class DocumentSearchRequest(ApiModel):
    query: str = Field(min_length=1, max_length=1000)
    model_code: NonEmptyId | None = None
    doc_type: DocumentType | None = None
    top_k: int = Field(default=4, ge=1, le=10)


class DocumentHit(ApiModel):
    chunk_id: NonEmptyId
    document_id: NonEmptyId
    doc_id: NonEmptyId
    title: str = Field(min_length=1)
    section: str | None = None
    score: float = Field(ge=-1.0, le=1.0)
    content: str = Field(min_length=1)
    model_code: NonEmptyId | None = None

    @classmethod
    def from_tool_hit(cls, hit: ToolDocumentHit) -> "DocumentHit":
        return cls(**hit.model_dump(), doc_id=hit.document_id)


# ==================
# 문서 RAG 상세 조회
# GET /documents/{document_id} 계약용 DTO다.
# ==================
class DocumentChunkItem(ApiModel):
    chunk_id: NonEmptyId
    chunk_seq: int = Field(ge=0)
    section_title: str | None = None
    content: str


class DocumentDetailResponse(ApiModel):
    # API의 document_id는 DB document.doc_id/document_chunk.doc_id에 대응한다.
    document_id: NonEmptyId
    title: str
    doc_type: DocumentType | None = None
    model_code: NonEmptyId | None = None
    source_path: str | None = None
    version: str | None = None
    chunks: list[DocumentChunkItem]
