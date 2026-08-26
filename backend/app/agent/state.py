"""LangGraph 실행 State와 내부 node port 계약 (`V5-C-2.1`).

공개 API DTO가 아니다. 그래프가 끝나기 직전에 :class:`CompletedAgentState`로 20개
canonical channel을 명시적으로 검증하고, 실행 중에만 필요한 네 channel은 출력에서
제거한다. ID·Enum·Tool payload는 ``app.common``의 정본을 그대로 재사용한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final, Literal, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.checkpoint import AgentCheckpointError, normalize_thread_id
from app.agent.routing import ResolvedIncidentRoute
from app.common.config import AGENT_MAX_TOOL_CALLS
from app.common.enums import (
    ActionCode,
    Decision,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    Severity,
    requires_approval,
    resolve_delivery_channels,
    resolve_severity,
)
from app.common.ids import NonEmptyId
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    AnomalySignal,
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
)

MatchedRule = Literal["R03_PRESENT", "TRACE_OOS", "SUMMARY_OOC_ONLY", "NO_ALARM"]

RULE_TO_ACTION: Final[Mapping[MatchedRule, ActionCode | None]] = {
    "R03_PRESENT": ActionCode.EQP_HOLD,
    "TRACE_OOS": ActionCode.WARNING,
    "SUMMARY_OOC_ONLY": ActionCode.MONITORING,
    "NO_ALARM": None,
}

INITIAL_STATUS: Final[Mapping[DeliveryChannel, DeliveryStatus]] = {
    DeliveryChannel.EMAIL: DeliveryStatus.WAITING,
    DeliveryChannel.MES_MOCK: DeliveryStatus.BLOCKED,
}


class StateModel(BaseModel):
    """checkpoint 왕복에도 같은 검증을 적용하는 내부 DTO 기반형."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        arbitrary_types_allowed=True,
    )


class Hypothesis(StateModel):
    """LLM이 만든 가설과 그 가설이 실제로 인용한 근거 ID."""

    predicted_fault_code: FaultHypothesis
    confidence: float = Field(ge=0.0, le=1.0)
    cause_summary: str = Field(min_length=1, max_length=2000)
    supporting_alarms: tuple[AlarmRef, ...] = ()
    supporting_chunk_ids: tuple[NonEmptyId, ...] = ()
    supporting_relation_ids: tuple[NonEmptyId, ...] = ()
    uncertainty: str = Field(max_length=1000)

    @model_validator(mode="after")
    def _unique_citations(self) -> Hypothesis:
        collections = (
            tuple(item.to_token() for item in self.supporting_alarms),
            self.supporting_chunk_ids,
            self.supporting_relation_ids,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("가설 근거 ID를 중복할 수 없습니다")
        return self


class ActionDecision(StateModel):
    """설계 §6.5 규칙표와 Common 파생값이 결속된 조치 결정."""

    action: ActionCode | None
    severity: Severity | None
    requires_approval: bool
    matched_rule: MatchedRule

    @model_validator(mode="after")
    def _exact_match(self) -> ActionDecision:
        expected = RULE_TO_ACTION[self.matched_rule]
        if expected is not self.action:
            raise ValueError("matched_rule과 action이 설계 §6.5 표와 다릅니다")
        if self.action is None:
            if self.severity is not None or self.requires_approval:
                raise ValueError("action 없음이면 severity·승인도 없어야 합니다")
            return self
        if self.severity is not resolve_severity(self.action):
            raise ValueError("severity가 Common 파생값과 다릅니다")
        if self.requires_approval != requires_approval(self.action):
            raise ValueError("requires_approval이 Common 파생값과 다릅니다")
        return self


class DeliveryPlan(StateModel):
    """delivery identity의 channel 부분.

    전체 identity는 ``(action_id, channel)``이다.
    """

    channel: DeliveryChannel
    status: DeliveryStatus


class ToolBudget(StateModel):
    """``agent_tool_call`` 행 수에서 파생한 실행 시점의 예산 snapshot."""

    max_calls: int = Field(default=AGENT_MAX_TOOL_CALLS, ge=1)
    used: int = Field(ge=0)
    source: Literal["DB"] = "DB"

    @model_validator(mode="after")
    def _within_budget(self) -> ToolBudget:
        if self.used > self.max_calls:
            raise ValueError("사용한 Tool 호출 수가 예산을 초과했습니다")
        return self


class AgentError(StateModel):
    """예외 원문을 포함하지 않는 실행 오류."""

    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    node: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    terminal: bool


class PersistResult(StateModel):
    """``persist_action``이 발급한 ID와 초기 delivery 계획."""

    action_id: NonEmptyId
    approval_id: NonEmptyId | None = None
    deliveries: tuple[DeliveryPlan, ...] = ()

    def assert_matches(self, decision: ActionDecision) -> None:
        if decision.action is None:
            raise ValueError("action 없음이면 PersistResult가 존재할 수 없습니다")
        if (self.approval_id is not None) != requires_approval(decision.action):
            raise ValueError("approval_id 유무가 action 정책과 다릅니다")
        channels = tuple(plan.channel for plan in self.deliveries)
        if channels != resolve_delivery_channels(decision.action):
            raise ValueError("delivery channel 집합이 Common 파생값과 다릅니다")
        if len(channels) != len(set(channels)):
            raise ValueError("delivery channel을 중복할 수 없습니다")
        for plan in self.deliveries:
            if plan.status is not INITIAL_STATUS[plan.channel]:
                raise ValueError("초기 delivery 상태가 계약과 다릅니다")


class AgentGraphInput(TypedDict):
    requested_alarm: AlarmRef
    autonomy_level: int


class AgentGraphState(TypedDict, total=False):
    # canonical output 20
    run_id: str
    thread_id: str
    retry_of_run_id: str | None
    requested_alarm: AlarmRef
    representative_alarm: AlarmRef
    member_alarms: tuple[AlarmRef, ...]
    lot_id: str
    chamber_id: str
    route: ResolvedIncidentRoute
    fdc_evidence: FdcSummaryToolResult | None
    optional_anomaly_evidence: AnomalySignal | None
    graph_evidence: EquipmentContextToolResult | None
    document_evidence: DocumentSearchToolResult | None
    hypothesis: Hypothesis | None
    action_decision: ActionDecision | None
    action_id: str | None
    approval_id: str | None
    deliveries: tuple[DeliveryPlan, ...]
    tool_budget: ToolBudget
    errors: tuple[AgentError, ...]
    # internal 4
    autonomy_level: int
    terminal_error: AgentError | None
    fdc_lot_hist_id: str
    approval_decision: Decision | None


class CompletedAgentState(StateModel):
    """성공 종료 직전에 명시적으로 호출하는 canonical 20-channel 검증기."""

    run_id: NonEmptyId
    thread_id: NonEmptyId
    retry_of_run_id: NonEmptyId | None
    requested_alarm: AlarmRef
    representative_alarm: AlarmRef
    member_alarms: tuple[AlarmRef, ...]
    lot_id: NonEmptyId
    chamber_id: NonEmptyId
    route: ResolvedIncidentRoute
    fdc_evidence: FdcSummaryToolResult | None
    optional_anomaly_evidence: AnomalySignal | None
    graph_evidence: EquipmentContextToolResult | None
    document_evidence: DocumentSearchToolResult | None
    hypothesis: Hypothesis | None
    action_decision: ActionDecision | None
    action_id: NonEmptyId | None
    approval_id: NonEmptyId | None
    deliveries: tuple[DeliveryPlan, ...]
    tool_budget: ToolBudget
    errors: tuple[AgentError, ...]

    @model_validator(mode="after")
    def _validate_complete(self) -> CompletedAgentState:
        try:
            normalize_thread_id(self.thread_id)
        except AgentCheckpointError as exc:
            raise ValueError("thread_id가 canonical UUID가 아닙니다") from exc

        incident = self.route.incident
        if self.requested_alarm != incident.requested_alarm:
            raise ValueError("requested_alarm이 route incident와 다릅니다")
        if self.representative_alarm != incident.representative_alarm:
            raise ValueError("representative_alarm이 route incident와 다릅니다")
        # PostgresSaver의 msgpack round-trip은 dataclass 안쪽 tuple을 list로 복원할 수
        # 있다. 순서·값 계약을 비교하되 컨테이너 종류(identity)는 정본으로 취급하지
        # 않는다.
        if self.member_alarms != tuple(incident.member_alarms):
            raise ValueError("member_alarms가 route incident와 다릅니다")
        if self.lot_id != incident.lot_id or self.chamber_id != incident.chamber_id:
            raise ValueError("incident key가 route와 다릅니다")

        anomaly = None if self.fdc_evidence is None else self.fdc_evidence.anomaly
        if (self.optional_anomaly_evidence is None) != (anomaly is None):
            raise ValueError("anomaly evidence는 양쪽에 함께 존재해야 합니다")
        if self.optional_anomaly_evidence != anomaly:
            raise ValueError("anomaly evidence 값이 FDC 결과와 다릅니다")

        channels = tuple(plan.channel for plan in self.deliveries)
        if len(channels) != len(set(channels)):
            raise ValueError("deliveries에는 같은 channel을 중복할 수 없습니다")

        if self.action_decision is None:
            raise ValueError("완료 State에는 action_decision이 필요합니다")
        if self.action_decision.action is None:
            if (
                any(value is not None for value in (self.action_id, self.approval_id))
                or self.deliveries
            ):
                raise ValueError("action 없음이면 영속화 결과도 없어야 합니다")
        else:
            if self.action_id is None:
                raise ValueError("action이 있으면 action_id가 필요합니다")
            if (self.approval_id is not None) != self.action_decision.requires_approval:
                raise ValueError("approval_id 유무가 결정과 다릅니다")
            if channels != resolve_delivery_channels(self.action_decision.action):
                raise ValueError("delivery channel 순서·집합이 결정과 다릅니다")

        self._validate_hypothesis_citations()
        if any(error.terminal for error in self.errors):
            raise ValueError("성공 State에 terminal error를 넣을 수 없습니다")
        return self

    def _validate_hypothesis_citations(self) -> None:
        if self.hypothesis is None:
            return
        alarm_tokens = {alarm.to_token() for alarm in self.member_alarms}
        cited_alarm_tokens = {
            alarm.to_token() for alarm in self.hypothesis.supporting_alarms
        }
        if not cited_alarm_tokens <= alarm_tokens:
            raise ValueError("가설이 incident 밖의 alarm을 인용했습니다")

        chunk_ids = (
            set()
            if self.document_evidence is None
            else {hit.chunk_id for hit in self.document_evidence.hits}
        )
        if not set(self.hypothesis.supporting_chunk_ids) <= chunk_ids:
            raise ValueError("가설이 조회하지 않은 document chunk를 인용했습니다")

        relation_ids = {
            relation_id
            for item in self.route.graph_evidence
            for relation_id in item.relation_ids
        }
        if not set(self.hypothesis.supporting_relation_ids) <= relation_ids:
            raise ValueError("가설이 조회하지 않은 graph relation을 인용했습니다")


class AgentNodePorts(Protocol):
    """C 후속 Task가 구현할 9개 node port."""

    generate_hypothesis: Callable[
        [
            FdcSummaryToolResult | None,
            EquipmentContextToolResult | None,
            DocumentSearchToolResult | None,
            ResolvedIncidentRoute,
        ],
        Hypothesis,
    ]
    decide_action: Callable[[ResolvedIncidentRoute], ActionDecision]
    persist_action: Callable[[NonEmptyId, ActionDecision], PersistResult]
    notify_email: Callable[[NonEmptyId], None]
    approval_email: Callable[[NonEmptyId, NonEmptyId], None]
    hitl_interrupt: Callable[[NonEmptyId], Decision]
    publish_mes: Callable[[NonEmptyId], None]
    writeback_result: Callable[[NonEmptyId], tuple[DeliveryPlan, ...]]
    cancel_mes: Callable[[NonEmptyId], tuple[DeliveryPlan, ...]]


__all__ = [
    "INITIAL_STATUS",
    "RULE_TO_ACTION",
    "ActionDecision",
    "AgentError",
    "AgentGraphInput",
    "AgentGraphState",
    "AgentNodePorts",
    "CompletedAgentState",
    "DeliveryPlan",
    "Hypothesis",
    "PersistResult",
    "ToolBudget",
]
