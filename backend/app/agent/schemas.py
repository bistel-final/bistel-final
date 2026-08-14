from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.common.enums import (
    ActionCode,
    ApprovalStatus,
    Decision,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    Severity,
    ToolCallStatus,
    resolve_severity,
)
from app.common.ids import NonEmptyId
from app.common.schemas import AlarmRef, ApiModel, IncidentRef, PageResponse
from app.common.tool_contracts import (
    DocumentHit,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    GraphRelationRef,
)


class AgentRunCreateRequest(ApiModel):
    alarm: AlarmRef


class AgentRunAcceptedResponse(ApiModel):
    agent_run_id: NonEmptyId
    thread_id: NonEmptyId
    incident: IncidentRef
    requested_alarm: AlarmRef
    representative_alarm: AlarmRef
    status: RunStatus


class IncidentAlarmEvidence(ApiModel):
    alarm_refs: list[AlarmRef] = Field(min_length=1)
    lot_hist_ids: list[NonEmptyId]
    trace_oos_refs: list[AlarmRef]
    summary_ooc_refs: list[AlarmRef]
    r03_refs: list[AlarmRef]
    sibling_alarm_counts: dict[NonEmptyId, int]

    @model_validator(mode="after")
    def validate_evidence(self) -> "IncidentAlarmEvidence":
        if any(count < 0 for count in self.sibling_alarm_counts.values()):
            raise ValueError("형제 챔버 알람 수는 음수일 수 없습니다")
        known = {alarm.to_token() for alarm in self.alarm_refs}
        grouped = self.trace_oos_refs + self.summary_ooc_refs + self.r03_refs
        if any(alarm.to_token() not in known for alarm in grouped):
            raise ValueError("source별 근거는 alarm_refs에 포함되어야 합니다")
        return self


class RouteEvidence(ApiModel):
    alarm: AlarmRef
    lot_hist_id: NonEmptyId
    relation: GraphRelationRef
    graph_revision: NonEmptyId
    route_consistency: bool


class EvidenceError(ApiModel):
    stage: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool


class AgentEvidence(ApiModel):
    representative_fdc: FdcSummaryToolResult | None = None
    r03_fdc: FdcSummaryToolResult | None = None
    incident: IncidentAlarmEvidence
    equipment_context: EquipmentContextToolResult | None = None
    document_hits: list[DocumentHit]
    routes: list[RouteEvidence]
    errors: list[EvidenceError]


class ActionDeliveryItem(ApiModel):
    action_id: NonEmptyId
    channel: DeliveryChannel
    status: DeliveryStatus
    request_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    attempt_count: int = Field(ge=0)
    provider_message_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None
    result: dict[str, Any] | None = None


class ToolCallItem(ApiModel):
    tool_call_id: NonEmptyId
    call_seq: int = Field(ge=1)
    tool_name: str = Field(min_length=1)
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    status: ToolCallStatus
    latency_ms: int | None = Field(default=None, ge=0)
    called_at: datetime
    error_msg: str | None = None


ApprovalQueueStatus = Literal[
    ApprovalStatus.PENDING,
    ApprovalStatus.APPROVED,
    ApprovalStatus.REJECTED,
    ApprovalStatus.EXPIRED,
]


class ApprovalItem(ApiModel):
    approval_id: NonEmptyId
    agent_run_id: NonEmptyId
    action_id: NonEmptyId
    trigger_alarm: AlarmRef
    incident: IncidentRef
    equipment_id: NonEmptyId | None = None
    parameter_id: NonEmptyId | None = None
    action_code: ActionCode
    severity: Severity
    requested_at: datetime
    status: ApprovalQueueStatus
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_comment: str | None = None

    @model_validator(mode="after")
    def validate_approval_target(self) -> "ApprovalItem":
        if self.action_code is not ActionCode.EQP_HOLD:
            raise ValueError("approval_request는 EQP_HOLD에만 생성할 수 있습니다")
        if self.severity is not Severity.HIGH:
            raise ValueError("EQP_HOLD approval severity는 HIGH여야 합니다")
        return self


class ApprovalPageResponse(PageResponse[ApprovalItem]):
    pass


class ApprovalDecisionRequest(ApiModel):
    decision: Decision
    decided_by: str = Field(min_length=1, max_length=40)
    decision_comment: str | None = Field(default=None, max_length=1000)


ApprovalDecisionStatus = Literal[
    ApprovalStatus.APPROVED,
    ApprovalStatus.REJECTED,
]


class ApprovalDecisionResponse(ApiModel):
    approval_id: NonEmptyId
    action_id: NonEmptyId
    approval_status: ApprovalDecisionStatus
    agent_run_status: RunStatus
    deliveries: list[ActionDeliveryItem]
    decided_by: str = Field(min_length=1, max_length=40)
    decided_at: datetime
    decision_comment: str | None = None


ReviewDisposition = Literal["ACCEPTED", "CORRECTED", "UNDETERMINED"]
ReviewLabelSource = Literal["HUMAN_REVIEW", "MENTOR_REVIEW", "HIDDEN_GOLD"]


class AgentRunItem(ApiModel):
    agent_run_id: NonEmptyId
    incident: IncidentRef
    requested_alarm: AlarmRef
    representative_alarm: AlarmRef
    equipment_id: NonEmptyId | None = None
    parameter_id: NonEmptyId | None = None
    recipe_step_name: str | None = None
    alarm_count: int = Field(ge=1)
    incident_first_at: datetime | None = None
    incident_last_at: datetime | None = None
    started_at: datetime
    ended_at: datetime | None = None
    status: RunStatus
    predicted_fault_code: FaultHypothesis | None = None
    prediction_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reviewed_fault_code: FaultHypothesis | None = None
    review_disposition: ReviewDisposition | None = None
    label_source: ReviewLabelSource | None = None
    ground_truth_available: bool = False
    recommended_action: ActionCode | None = None
    severity: Severity | None = None

    @model_validator(mode="after")
    def validate_review_fields(self) -> "AgentRunItem":
        review_fields = (
            self.reviewed_fault_code,
            self.review_disposition,
            self.label_source,
        )
        if self.ground_truth_available and self.label_source != "HIDDEN_GOLD":
            raise ValueError("ground_truth_available=true에는 HIDDEN_GOLD가 필요합니다")
        if not self.ground_truth_available and self.label_source == "HIDDEN_GOLD":
            raise ValueError("HIDDEN_GOLD에는 ground_truth_available=true가 필요합니다")
        has_review = any(value is not None for value in review_fields)
        if has_review and (
            self.review_disposition is None or self.label_source is None
        ):
            raise ValueError(
                "검토 필드가 있으면 review_disposition과 label_source가 필요합니다"
            )
        if self.review_disposition == "CORRECTED" and self.reviewed_fault_code is None:
            raise ValueError("CORRECTED 검토에는 reviewed_fault_code가 필요합니다")
        return self


class AgentRunPageResponse(PageResponse[AgentRunItem]):
    pass


class R03EvidenceRef(ApiModel):
    alarm: AlarmRef
    lot_hist_id: NonEmptyId
    wafer_no: int = Field(ge=1)
    parameter_id: NonEmptyId
    recipe_step_name: str | None = None


class ActionItem(ApiModel):
    action_id: NonEmptyId
    incident: IncidentRef
    trigger_alarm: AlarmRef
    equipment_id: NonEmptyId | None = None
    parameter_id: NonEmptyId | None = None
    recipe_step_name: str | None = None
    action_code: ActionCode
    severity: Severity
    approval_status: ApprovalStatus
    alarm_count: int = Field(ge=1)
    created_at: datetime
    deliveries: list[ActionDeliveryItem]

    @model_validator(mode="after")
    def validate_delivery_channels(self) -> "ActionItem":
        channels = [delivery.channel for delivery in self.deliveries]
        if len(channels) != len(set(channels)):
            raise ValueError("deliveries에는 같은 channel을 중복할 수 없습니다")
        expected = {
            ActionCode.MONITORING: set(),
            ActionCode.WARNING: {DeliveryChannel.EMAIL},
            ActionCode.EQP_HOLD: {
                DeliveryChannel.EMAIL,
                DeliveryChannel.MES_MOCK,
            },
        }[self.action_code]
        if set(channels) != expected:
            raise ValueError("action_code와 delivery channel 구성이 일치하지 않습니다")
        if self.severity is not resolve_severity(self.action_code):
            raise ValueError("action_code와 severity가 일치하지 않습니다")
        if self.action_code is ActionCode.EQP_HOLD:
            if self.approval_status is ApprovalStatus.AUTO:
                raise ValueError("EQP_HOLD approval_status는 AUTO일 수 없습니다")
        elif self.approval_status is not ApprovalStatus.AUTO:
            raise ValueError("자동 조치 approval_status는 AUTO여야 합니다")
        return self


class ActionPageResponse(PageResponse[ActionItem]):
    pass


class ActionDetailResponse(ActionItem):
    reason: str | None = None
    approval_required: bool
    approved_by: str | None = None
    approved_at: datetime | None = None


class AgentRunDetailResponse(AgentRunItem):
    retry_of_run_id: NonEmptyId | None = None
    alarms: list[AlarmRef] = Field(min_length=1)
    thread_id: NonEmptyId
    cause_summary: str | None = None
    action_reason: str | None = None
    approval_required: bool
    evidence: AgentEvidence | None = None
    llm_model: str | None = Field(default=None, min_length=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    tool_calls: list[ToolCallItem]
    r03_fdc_evidence: R03EvidenceRef | None = None
    action: ActionDetailResponse | None = None
    approval: ApprovalItem | None = None

    @model_validator(mode="after")
    def validate_alarm_provenance(self) -> "AgentRunDetailResponse":
        alarm_tokens = {alarm.to_token() for alarm in self.alarms}
        if self.requested_alarm.to_token() not in alarm_tokens:
            raise ValueError("requested_alarm은 alarms에 포함되어야 합니다")
        if self.representative_alarm.to_token() not in alarm_tokens:
            raise ValueError("representative_alarm은 alarms에 포함되어야 합니다")
        if len(alarm_tokens) != len(self.alarms):
            raise ValueError("alarms에는 같은 AlarmRef를 중복할 수 없습니다")
        if self.alarm_count != len(self.alarms):
            raise ValueError("alarm_count는 alarms 길이와 일치해야 합니다")
        return self
