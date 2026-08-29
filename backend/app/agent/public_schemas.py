"""API v3 Agent 화면 전용 공개 DTO.

기존 ``app.agent.schemas``는 Runtime 상세·내부 전송 정보를 함께 표현한다. 목록 API가
그 모델을 재사용하면 Tool input/output, delivery hash 같은 내부 필드가 공개 응답에 섞일
수 있으므로 이 모듈은 호환 화면에 필요한 최소 projection만 별도로 소유한다.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from app.common.enums import (
    ActionCode,
    AlarmSource,
    DeliveryStatus,
    FaultHypothesis,
    PublicApprovalStatus,
    PublicDeliveryChannel,
    RunStatus,
    ToolCallStatus,
)
from app.common.ids import NonEmptyId
from app.common.schemas import ApiModel


class PublicToolCallItem(ApiModel):
    """Auto Analysis용 compact Tool projection.

    ``n``·``s``는 최종 참고 React의 한시적 alias다. Chat의 ``name``·``result``나
    Runtime 저장 payload는 이 DTO로 표현할 수 없다.
    """

    tool_name: str = Field(min_length=1)
    status: ToolCallStatus
    result_summary: str = Field(min_length=1)
    n: str = Field(min_length=1)
    s: ToolCallStatus

    @model_validator(mode="after")
    def validate_aliases(self) -> "PublicToolCallItem":
        if self.n != self.tool_name or self.s is not self.status:
            raise ValueError("Tool compatibility alias가 canonical 값과 다릅니다")
        return self


class PublicDeliveryItem(ApiModel):
    channel: PublicDeliveryChannel
    status: DeliveryStatus


class PublicAgentRunItem(ApiModel):
    agent_run_id: NonEmptyId
    created_at: datetime
    alarm_source: AlarmSource
    alarm_id: NonEmptyId
    chamber_id: NonEmptyId
    chamber: NonEmptyId
    predicted_fault_code: FaultHypothesis | None
    fault_code: FaultHypothesis | None
    # UI metadata mapping은 아직 정본에 없다. 임의 label/color 생성을 타입에서 막는다.
    fault_name: None
    fault_color: None
    confidence: float | None = Field(ge=0.0, le=1.0)
    recommended_action: ActionCode | None
    status: RunStatus
    action_id: NonEmptyId | None
    approval_id: NonEmptyId | None
    tools: list[PublicToolCallItem]
    deliveries: list[PublicDeliveryItem]
    latency_ms: int = Field(ge=0)
    llm_model: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_projection(self) -> "PublicAgentRunItem":
        if self.chamber != self.chamber_id:
            raise ValueError("chamber alias가 chamber_id와 다릅니다")
        if self.fault_code is not self.predicted_fault_code:
            raise ValueError("fault_code alias가 predicted_fault_code와 다릅니다")
        if (self.predicted_fault_code is None) != (self.confidence is None):
            raise ValueError("예측 코드와 confidence는 함께 존재해야 합니다")
        if self.approval_id is not None and self.action_id is None:
            raise ValueError("approval_id에는 action_id가 필요합니다")
        return self


class PublicApprovalItem(ApiModel):
    approval_id: NonEmptyId
    agent_run_id: NonEmptyId
    action_id: NonEmptyId
    created_at: datetime
    lot_id: NonEmptyId
    lot: NonEmptyId
    equipment_id: NonEmptyId
    equipment: NonEmptyId
    chamber_id: NonEmptyId
    chamber: NonEmptyId
    predicted_fault_code: FaultHypothesis
    fault_code: FaultHypothesis
    action_code: ActionCode
    reason: str = Field(min_length=1)
    status: PublicApprovalStatus
    decided_by: str | None
    decided_at: datetime | None
    decision_comment: str | None
    approved_by: str | None
    approved_at: datetime | None

    @model_validator(mode="after")
    def validate_projection(self) -> "PublicApprovalItem":
        if self.lot != self.lot_id:
            raise ValueError("lot alias가 lot_id와 다릅니다")
        if self.equipment != self.equipment_id:
            raise ValueError("equipment alias가 equipment_id와 다릅니다")
        if self.chamber != self.chamber_id:
            raise ValueError("chamber alias가 chamber_id와 다릅니다")
        if self.fault_code is not self.predicted_fault_code:
            raise ValueError("fault_code alias가 predicted_fault_code와 다릅니다")
        if self.action_code is not ActionCode.EQP_HOLD:
            raise ValueError("공개 승인 목록에는 EQP_HOLD만 올 수 있습니다")

        if self.status is PublicApprovalStatus.PENDING:
            decision_values = (
                self.decided_by,
                self.decided_at,
                self.decision_comment,
                self.approved_by,
                self.approved_at,
            )
            if any(value is not None for value in decision_values):
                raise ValueError("PENDING 승인에는 결정 정보가 없어야 합니다")
        else:
            if self.decided_by is None or self.decided_at is None:
                raise ValueError("결정된 승인에는 결정자와 결정 시각이 필요합니다")
            if (
                self.approved_by != self.decided_by
                or self.approved_at != self.decided_at
            ):
                raise ValueError(
                    "승인 compatibility alias가 canonical 결정값과 다릅니다"
                )
        return self


class AgentAskRequest(ApiModel):
    question: str = Field(min_length=1, max_length=1000)


class AskToolItem(ApiModel):
    """Chat 전용 Tool projection.

    Auto Analysis의 ``n``·``s``와 Runtime payload는 이 타입으로 표현할 수 없다.
    """

    tool_name: str = Field(min_length=1)
    status: ToolCallStatus
    result_summary: str = Field(min_length=1)
    name: str = Field(min_length=1)
    result: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_aliases(self) -> "AskToolItem":
        if self.name != self.tool_name or self.result != self.result_summary:
            raise ValueError("Chat Tool alias가 canonical 값과 다릅니다")
        return self


class AlarmAskEvidence(ApiModel):
    type: Literal["ALARM"]
    source_id: NonEmptyId
    title: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class TraceAskEvidence(ApiModel):
    type: Literal["TRACE"]
    source_id: NonEmptyId
    title: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class GraphAskEvidence(ApiModel):
    type: Literal["GRAPH"]
    source_id: NonEmptyId
    title: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    relation_id: NonEmptyId
    graph_revision: NonEmptyId


class DocumentAskEvidence(ApiModel):
    type: Literal["DOCUMENT"]
    source_id: NonEmptyId
    title: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    document_id: NonEmptyId
    chunk_id: NonEmptyId
    section: str | None

    @model_validator(mode="after")
    def validate_source_identity(self) -> "DocumentAskEvidence":
        if self.source_id != self.chunk_id:
            raise ValueError("DOCUMENT source_id는 chunk_id와 같아야 합니다")
        return self


class MetrologyAskEvidence(ApiModel):
    type: Literal["METROLOGY"]
    source_id: NonEmptyId
    title: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


AskEvidenceItem = Annotated[
    AlarmAskEvidence
    | TraceAskEvidence
    | GraphAskEvidence
    | DocumentAskEvidence
    | MetrologyAskEvidence,
    Field(discriminator="type"),
]
ASK_EVIDENCE_ADAPTER = TypeAdapter(AskEvidenceItem)


class AskDocumentEvidenceAlias(ApiModel):
    doc_id: NonEmptyId
    document_id: NonEmptyId
    chunk_id: NonEmptyId
    section: str | None

    @model_validator(mode="after")
    def validate_document_alias(self) -> "AskDocumentEvidenceAlias":
        if self.doc_id != self.document_id:
            raise ValueError("doc_id alias가 document_id와 다릅니다")
        return self


class AgentAskResponse(ApiModel):
    title: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    tools: list[AskToolItem]
    predicted_fault_code: FaultHypothesis | None
    confidence: float | None = Field(ge=0.0, le=1.0)
    recommended_action: ActionCode | None
    evidence_items: list[AskEvidenceItem]
    limitations: list[str]
    evidence: AskDocumentEvidenceAlias | None
    limit: str

    @model_validator(mode="after")
    def validate_compatibility_projection(self) -> "AgentAskResponse":
        source_ids = [item.source_id for item in self.evidence_items]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("evidence source_id는 중복될 수 없습니다")

        first_document = next(
            (
                item
                for item in self.evidence_items
                if isinstance(item, DocumentAskEvidence)
            ),
            None,
        )
        if first_document is None:
            if self.evidence is not None:
                raise ValueError("DOCUMENT 근거가 없으면 evidence alias는 null입니다")
        else:
            expected = (
                first_document.document_id,
                first_document.chunk_id,
                first_document.section,
            )
            actual = (
                (
                    self.evidence.document_id,
                    self.evidence.chunk_id,
                    self.evidence.section,
                )
                if self.evidence is not None
                else None
            )
            if actual != expected:
                raise ValueError("evidence alias가 첫 DOCUMENT 근거와 다릅니다")

        if self.limit != "; ".join(self.limitations):
            raise ValueError("limit alias가 limitations와 다릅니다")
        if not self.evidence_items and any(
            value is not None
            for value in (
                self.predicted_fault_code,
                self.confidence,
                self.recommended_action,
            )
        ):
            raise ValueError("근거가 없으면 판단 필드는 모두 null이어야 합니다")
        return self


__all__ = [
    "ASK_EVIDENCE_ADAPTER",
    "AgentAskRequest",
    "AgentAskResponse",
    "AlarmAskEvidence",
    "AskDocumentEvidenceAlias",
    "AskEvidenceItem",
    "AskToolItem",
    "DocumentAskEvidence",
    "GraphAskEvidence",
    "MetrologyAskEvidence",
    "PublicAgentRunItem",
    "PublicApprovalItem",
    "PublicDeliveryItem",
    "PublicToolCallItem",
    "TraceAskEvidence",
]
