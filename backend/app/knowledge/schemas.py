from enum import StrEnum

from pydantic import Field

from app.common.ids import NonEmptyId
from app.common.schemas import ApiModel
from app.common.tool_contracts import (
    AreaNode,
    ChamberNode,
    DocumentHit,
    EquipmentNode,
    GraphRelationRef,
    ParameterNode,
    ProcessStepNode,
)


class DocumentType(StrEnum):
    SPEC = "SPEC"
    MANUAL = "MANUAL"
    TROUBLESHOOT = "TROUBLESHOOT"


class ChamberRelationResponse(ApiModel):
    chamber: ChamberNode
    equipment: EquipmentNode
    area: AreaNode | None = None
    step: ProcessStepNode | None = None
    sibling_chambers: list[ChamberNode]
    adjacent_steps: list[ProcessStepNode]
    parameters: list[ParameterNode]
    relations: list[GraphRelationRef]
    graph_revision: NonEmptyId


class EquipmentRelationResponse(ApiModel):
    equipment: EquipmentNode
    chambers: list[ChamberNode]
    area: AreaNode | None = None
    step: ProcessStepNode | None = None
    adjacent_steps: list[ProcessStepNode]
    parameters: list[ParameterNode]
    relations: list[GraphRelationRef]
    graph_revision: NonEmptyId


class DocumentSearchRequest(ApiModel):
    query: str = Field(min_length=1, max_length=1000)
    model_code: NonEmptyId | None = None
    top_k: int = Field(default=4, ge=1, le=10)


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
