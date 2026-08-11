from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.common.enums import (
    ActionApprovalStatus,
    ActionCode,
    AgentRunStatus,
    ApprovalStatus,
    Decision,
    FaultCode,
    SendChannel,
    SendStatus,
    Severity,
    ToolCallStatus,
)
from app.common.ids import NonEmptyId
from app.common.schemas import ApiModel, IncidentRef, PageResponse
from app.common.tool_contracts import (
    DocumentHit,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
)


class AgentRunCreateRequest(ApiModel):
    alarm_id: str = Field(min_length=1, max_length=20)


class AgentRunAcceptedResponse(ApiModel):
    agent_run_id: NonEmptyId
    thread_id: NonEmptyId
    incident: IncidentRef
    requested_alarm_id: NonEmptyId
    representative_alarm_id: NonEmptyId
    status: AgentRunStatus


class IncidentAlarmEvidence(ApiModel):
    alarm_ids: list[NonEmptyId]
    lot_hist_ids: list[NonEmptyId]
    rule_ids: list[NonEmptyId]
    distinct_oos_wafer_count: int = Field(ge=0)
    distinct_ooc_wafer_count: int = Field(ge=0)
    has_r03_consec: bool
    sibling_alarm_counts: dict[NonEmptyId, int]

    @model_validator(mode="after")
    def validate_counts(self) -> "IncidentAlarmEvidence":
        if any(count < 0 for count in self.sibling_alarm_counts.values()):
            raise ValueError("형제 챔버 알람 수는 음수일 수 없습니다")
        return self


class BatchIncidentPlan(ApiModel):
    incident: IncidentRef
    representative_alarm_id: NonEmptyId
    alarm_ids: list[NonEmptyId]
    base_action_code: ActionCode | None = None
    final_action_code: ActionCode | None = None
    severity: Severity | None = None
    action_reason: str


class UpstreamEvidence(ApiModel):
    source: Literal["batch_plan", "action_history"]
    upstream_incident: IncidentRef
    downstream_incident: IncidentRef
    relationship: str = Field(min_length=1)
    same_wafer: bool
    action_id: NonEmptyId | None = None
    action_code: ActionCode | None = None


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
    batch_incident_plans: list[BatchIncidentPlan]
    upstream: list[UpstreamEvidence]
    errors: list[EvidenceError]


class DeliveryResult(ApiModel):
    action_id: NonEmptyId
    send_channel: SendChannel
    request_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    delivered_at: datetime
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


class ApprovalItem(ApiModel):
    approval_id: NonEmptyId
    agent_run_id: NonEmptyId
    action_id: NonEmptyId | None = None
    incident: IncidentRef
    equipment_id: NonEmptyId | None = None
    sensor_id: NonEmptyId | None = None
    rule_id: NonEmptyId | None = None
    action_code: ActionCode
    severity: Severity | None = None
    requested_at: datetime
    status: ApprovalStatus
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_comment: str | None = None


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
    send_status: SendStatus
    agent_run_status: AgentRunStatus
    decided_by: str = Field(min_length=1, max_length=40)
    decided_at: datetime
    decision_comment: str | None = None


class AgentRunItem(ApiModel):
    agent_run_id: NonEmptyId
    incident: IncidentRef
    equipment_id: NonEmptyId | None = None
    sensor_id: NonEmptyId | None = None
    recipe_step_name: str | None = None
    alarm_count: int = Field(ge=0)
    incident_first_at: datetime | None = None
    incident_last_at: datetime | None = None
    started_at: datetime
    ended_at: datetime | None = None
    status: AgentRunStatus
    fault_code: FaultCode | None = None
    recommended_action: ActionCode | None = None
    severity: Severity | None = None


class AgentRunPageResponse(PageResponse[AgentRunItem]):
    pass


class R03EvidenceRef(ApiModel):
    alarm_id: NonEmptyId
    lot_hist_id: NonEmptyId
    wafer_no: int = Field(ge=1)
    sensor_id: NonEmptyId
    recipe_step_name: str | None = None


class ActionItem(ApiModel):
    action_id: NonEmptyId
    # action_history.created_by_agent_run_id에 대응한다. 신규 생성 시 한 번만
    # 기록하며 같은 action_id를 재사용하는 수동 재실행에서는 바꾸지 않는다.
    created_by_agent_run_id: NonEmptyId | None = None
    incident: IncidentRef
    equipment_id: NonEmptyId | None = None
    sensor_id: NonEmptyId | None = None
    recipe_step_name: str | None = None
    action_code: ActionCode
    severity: Severity | None = None
    approval_status: ActionApprovalStatus | None = None
    send_status: SendStatus | None = None
    send_channel: SendChannel | None = None
    alarm_count: int = Field(ge=0)
    created_at: datetime | None = None


class ActionPageResponse(PageResponse[ActionItem]):
    pass


class ActionDetailResponse(ActionItem):
    trigger_alarm_lot_hist_id: NonEmptyId | None = None
    reason: str | None = None
    approval_required: bool
    approved_by: str | None = None
    approved_at: datetime | None = None
    send_started_at: datetime | None = None
    send_attempt_count: int = Field(ge=0)
    sent_at: datetime | None = None
    delivery: DeliveryResult | None = None


class AgentRunDetailResponse(ApiModel):
    agent_run_id: NonEmptyId
    requested_alarm_id: NonEmptyId
    representative_alarm_id: NonEmptyId
    alarm_ids: list[NonEmptyId]
    alarm_count: int = Field(ge=0)
    incident: IncidentRef
    equipment_id: NonEmptyId | None = None
    sensor_id: NonEmptyId | None = None
    recipe_step_name: str | None = None
    incident_first_at: datetime | None = None
    incident_last_at: datetime | None = None
    thread_id: NonEmptyId
    status: AgentRunStatus
    fault_code: FaultCode | None = None
    cause_summary: str | None = None
    recommended_action: ActionCode | None = None
    action_reason: str | None = None
    severity: Severity | None = None
    approval_required: bool
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: AgentEvidence | None = None
    llm_model: str = Field(min_length=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    started_at: datetime
    ended_at: datetime | None = None
    tool_calls: list[ToolCallItem]
    r03_fdc_evidence: R03EvidenceRef | None = None
    action: ActionDetailResponse | None = None
    approval: ApprovalItem | None = None
