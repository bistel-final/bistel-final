"""LangGraph 실행 State와 내부 node port 계약 (`V5-C-2.1`).

공개 API DTO가 아니다. 그래프가 끝나기 직전에 :class:`CompletedAgentState`로
canonical 출력 channel을 명시적으로 검증하고, 실행 중에만 필요한 internal
channel은 출력에서 제거한다. ID·Enum·Tool payload는 ``app.common``의 정본을 그대로
재사용한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Literal,
    NotRequired,
    Protocol,
    TypedDict,
)

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.checkpoint import AgentCheckpointError, normalize_thread_id
from app.agent.diagnostics import (
    AlternativeHypothesis,
    EvidenceAssessmentBlock,
    ImpactScopeBlock,
    IncidentDiagnosticSnapshot,
)
from app.agent.investigation_models import (
    InvestigationEvidence,
    OriginAssessment,
    OriginClaim,
    ParameterFinding,
    ParameterFindingDraft,
)
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
    AGENT_TOOL_NAMES,
    AnomalySignal,
    ChamberParameterHistoryToolResult,
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    MetrologyResultToolResult,
)

if TYPE_CHECKING:
    from app.agent.rehydration import RehydrationSeed

MatchedRule = Literal["R03_PRESENT", "TRACE_OOS", "SUMMARY_OOC_ONLY", "NO_ALARM"]
ActionPolicyVersion = Literal["ACTION-POLICY-V1"]

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


class LlmUsage(StateModel):
    """실제 provider 응답에서 온 run 적재 단위."""

    model: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=40)
    input_tokens: int = Field(ge=0, le=2_147_483_647, strict=True)
    output_tokens: int = Field(ge=0, le=2_147_483_647, strict=True)

    @model_validator(mode="after")
    def _int32_total(self) -> LlmUsage:
        if self.input_tokens + self.output_tokens > 2_147_483_647:
            raise ValueError("LLM token 합계가 integer 범위를 넘었습니다")
        return self

    def plus(self, other: LlmUsage) -> LlmUsage:
        if self.model != other.model or self.prompt_version != other.prompt_version:
            raise ValueError("LLM usage provenance가 다릅니다")
        return LlmUsage(
            model=self.model,
            prompt_version=self.prompt_version,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class HypothesisContent(StateModel):
    """LLM이 만든 가설과 그 가설이 실제로 인용한 근거 ID."""

    predicted_fault_code: FaultHypothesis
    confidence: float = Field(ge=0.0, le=1.0)
    cause_summary: str = Field(min_length=1, max_length=2000)
    supporting_alarms: tuple[AlarmRef, ...] = ()
    supporting_chunk_ids: tuple[NonEmptyId, ...] = ()
    supporting_relation_ids: tuple[NonEmptyId, ...] = ()
    supporting_lot_hist_ids: tuple[NonEmptyId, ...] = ()
    supporting_parameter_ids: tuple[NonEmptyId, ...] = ()
    uncertainty: str = Field(max_length=1000)
    observations: tuple[str, ...] = Field(default=(), max_length=20)
    evidence_synthesis: str = Field(default="", max_length=2000)
    alternative_hypotheses: tuple[AlternativeHypothesis, ...] = Field(
        default=(), max_length=3
    )
    impact_summary: str = Field(default="", max_length=2000)
    verification_steps: tuple[str, ...] = Field(default=(), max_length=10)
    limitations: tuple[str, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def _unique_citations(self) -> HypothesisContent:
        collections = (
            tuple(item.to_token() for item in self.supporting_alarms),
            self.supporting_chunk_ids,
            self.supporting_relation_ids,
            self.supporting_lot_hist_ids,
            self.supporting_parameter_ids,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("가설 근거 ID를 중복할 수 없습니다")
        return self


class HypothesisDraftV3(HypothesisContent):
    """LLM strict 출력. 산술·compared 필드는 이 DTO에 들어올 수 없다."""

    parameter_findings_draft: tuple[ParameterFindingDraft, ...]
    origin_claim: OriginClaim


class Hypothesis(HypothesisContent):
    """검증된 draft에 코드 계산값을 더한 저장용 DTO. v1/v2 읽기도 허용한다."""

    parameter_findings: tuple[ParameterFinding, ...] = ()
    origin_assessment: OriginAssessment | None = None


class HypothesisOutcome(StateModel):
    """가설과 그 가설을 만드는 데 소비한 실제 LLM usage."""

    hypothesis: Hypothesis
    llm_usage: LlmUsage
    diagnostic_snapshot: IncidentDiagnosticSnapshot | None = None
    evidence_assessment: EvidenceAssessmentBlock | None = None
    impact_scope: ImpactScopeBlock | None = None


class ActionDecision(StateModel):
    """설계 §6.5 규칙표와 Common 파생값이 결속된 조치 결정."""

    action: ActionCode | None
    severity: Severity | None
    requires_approval: bool
    matched_rule: MatchedRule
    # V1 배포 전에 저장된 checkpoint에는 이 key가 없다. 당시 조치 규칙도 V1과
    # 같으므로 누락값만 V1로 복원하고, 명시된 미지원 version은 Literal이 거부한다.
    policy_version: ActionPolicyVersion = "ACTION-POLICY-V1"

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
    """예산 소비 ``agent_tool_call``에서 파생한 실행 시점 snapshot.

    감사 행 자체는 모두 유지하되 외부 효과 없는 성공 send_action no-call만 제외한다.
    """

    max_calls: int = Field(default=AGENT_MAX_TOOL_CALLS, ge=1)
    used: int = Field(ge=0)
    by_tool: Mapping[str, int] | None = None
    send_budget: int = Field(default=2, ge=0)
    send_used: int | None = Field(default=None, ge=0)
    pending_reservations: int | None = Field(default=None, ge=0)
    source: Literal["DB"] = "DB"

    @model_validator(mode="after")
    def _consistent_snapshot(self) -> ToolBudget:
        """legacy 요약과 신규 상세 snapshot을 구분해 구조만 검증한다."""

        if self.send_budget > self.max_calls:
            raise ValueError("전송 예약량이 전체 Tool 예산보다 클 수 없습니다")
        if self.by_tool is None:
            if self.send_used is not None or self.pending_reservations is not None:
                raise ValueError(
                    "legacy Tool 예산에는 상세 집계를 일부만 넣을 수 없습니다"
                )
            return self

        if self.send_used is None or self.pending_reservations is None:
            raise ValueError("상세 Tool 예산에는 전송·미종료 집계가 필요합니다")
        if any(name not in AGENT_TOOL_NAMES for name in self.by_tool):
            raise ValueError("Tool 예산에 등록되지 않은 Tool 이름이 있습니다")
        if any(
            not isinstance(count, int) or count < 0 for count in self.by_tool.values()
        ):
            raise ValueError("Tool별 호출 수는 0 이상의 정수여야 합니다")
        if sum(self.by_tool.values()) != self.used:
            raise ValueError("Tool별 호출 수 합계가 전체 호출 수와 다릅니다")
        if self.send_used != self.by_tool.get("send_action", 0):
            raise ValueError("send_action 호출 수가 Tool별 집계와 다릅니다")
        if self.pending_reservations > self.used:
            raise ValueError("미종료 예약 수가 전체 호출 수보다 클 수 없습니다")
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
    # public body에는 없고 POST orchestration이 Runnable config와 함께 주입한다.
    thread_id: NotRequired[str]


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
    # internal 5
    autonomy_level: int
    terminal_error: AgentError | None
    fdc_lot_hist_id: str
    fdc_lot_hist_ids: tuple[str, ...]
    fdc_evidence_set: tuple[FdcSummaryToolResult | None, ...]
    read_retry_used: int
    approval_decision: Decision | None
    pending_llm_usage: LlmUsage | None
    # Level 3 ReAct (V5-C-7.1): 선택 흔적·카운터·다중 문서 검색 결과. Level 1·2는 빈 값.
    react_trace: tuple[dict[str, Any], ...]
    react_steps: int
    react_guard_rejections: int
    react_pending: dict[str, Any] | None
    react_candidates: dict[str, Any]
    document_evidence_set: tuple[DocumentSearchToolResult | None, ...]
    history_evidence_set: tuple[ChamberParameterHistoryToolResult | None, ...]
    metrology_evidence_set: tuple[MetrologyResultToolResult | None, ...]


class CompletedAgentState(StateModel):
    """성공 종료 직전에 명시적으로 호출하는 canonical State 검증기."""

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
    # Level 3 ReAct 흔적(Level 1·2는 빈 tuple). finalize가 run evidence로 남긴다.
    react_trace: tuple[dict[str, Any], ...] = ()

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

        if self.hypothesis is None:
            raise ValueError("완료 State에는 원인 가설이 필요합니다")
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
            FdcSummaryToolResult | None | tuple[FdcSummaryToolResult | None, ...],
            EquipmentContextToolResult | None,
            DocumentSearchToolResult | None,
            ResolvedIncidentRoute,
            tuple[str, ...],
            InvestigationEvidence,
        ],
        HypothesisOutcome,
    ]
    decide_action: Callable[[ResolvedIncidentRoute], ActionDecision]
    persist_action: Callable[
        [NonEmptyId, ActionDecision, RehydrationSeed], PersistResult
    ]
    notify_email: Callable[[NonEmptyId], None]
    approval_email: Callable[[NonEmptyId, NonEmptyId, NonEmptyId], None]
    # checkpoint가 node 실행 전 죽으면 같은 port가 다시 호출된다. 따라서 이 port는
    # 승인 상태를 읽어 Decision으로 변환하는 것 외의 쓰기·외부 효과를 가져서는 안 된다.
    hitl_interrupt: Callable[[NonEmptyId], Decision]
    publish_mes: Callable[[NonEmptyId], None]
    writeback_result: Callable[[NonEmptyId], tuple[DeliveryPlan, ...]]
    cancel_mes: Callable[[NonEmptyId], tuple[DeliveryPlan, ...]]
    # V5-C-7.1 Level 3: ReactContext → ReactSelectionOutcome (app.agent.react). 순환
    # import를 피하기 위해 여기서는 Any로 둔다. Level 1·2 조립은 None을 넣어도 된다.
    react_select: Callable[[Any], Any] | None


__all__ = [
    "INITIAL_STATUS",
    "RULE_TO_ACTION",
    "ActionDecision",
    "ActionPolicyVersion",
    "AgentError",
    "AgentGraphInput",
    "AgentGraphState",
    "AgentNodePorts",
    "CompletedAgentState",
    "DeliveryPlan",
    "Hypothesis",
    "HypothesisOutcome",
    "LlmUsage",
    "PersistResult",
    "ToolBudget",
]
