from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.enums import (
    AlarmType,
    ChartType,
    DeliveryChannel,
    DeliveryStatus,
    IncidentModelSignalStatus,
    ThresholdValidationStatus,
)
from app.common.ids import NonEmptyId

# 실패 reason은 아래 접두어 중 하나로 시작한다. 임의 접두어를 만들지 않는다.
REASON_PREFIXES: tuple[str, ...] = (
    "NOT_FOUND:",
    "TIMEOUT:",
    "MODEL_NOT_READY:",
    "LLM_NOT_READY:",
    "GRAPH_SHAPE_ERROR:",
    "DEPENDENCY_ERROR:",
    "POLICY_REJECTED:",
    "IDEMPOTENCY_CONFLICT:",
)

_CONTRACT_FIELDS = ("ok", "reason")


def _tool_id() -> Any:
    return Field(min_length=1, max_length=20)


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolResult(ToolModel):
    # 성공 시 반드시 값이 있어야 하는 필드. 나머지는 있어도 없어도 된다.
    required_on_success: ClassVar[tuple[str, ...]] = ()

    ok: bool
    reason: str = ""

    @model_validator(mode="after")
    def _validate_contract(self) -> "ToolResult":
        payload = [n for n in type(self).model_fields if n not in _CONTRACT_FIELDS]

        if self.ok:
            if self.reason != "":
                raise ValueError("성공 결과의 reason은 빈 문자열이어야 합니다")

            for name in self.required_on_success:
                if self._is_empty(getattr(self, name)):
                    raise ValueError(f"성공 결과에 필수 값이 없습니다: {name}")

            return self

        if not self.reason.startswith(REASON_PREFIXES):
            allowed = " ".join(REASON_PREFIXES)
            raise ValueError(f"실패 reason 접두어는 다음 중 하나여야 합니다: {allowed}")

        # 실패 결과는 모든 payload 필드가 비어 있어야 한다.
        for name in payload:
            value = getattr(self, name)

            if isinstance(value, bool):
                if value is not False:
                    raise ValueError(
                        f"실패 결과의 bool 필드는 False여야 합니다: {name}"
                    )
            elif isinstance(value, list):
                if value:
                    raise ValueError(f"실패 결과의 목록 필드는 비어야 합니다: {name}")
            elif value is not None:
                raise ValueError(f"실패 결과의 필드는 None이어야 합니다: {name}")

        return self

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if isinstance(value, bool):
            return value is False
        if isinstance(value, str):
            return value == ""
        return value is None or value == []


# ---------------------------------------------------------------------
# 공유 payload — REST DTO와 Tool 결과가 함께 사용한다
# ---------------------------------------------------------------------
class WaferContext(ToolModel):
    lot_hist_id: NonEmptyId
    lot_id: NonEmptyId
    wafer_no: int = Field(ge=1)
    chamber_id: NonEmptyId
    equipment_id: NonEmptyId
    step_id: NonEmptyId
    recipe_id: NonEmptyId | None = None


class ParameterSummaryItem(ToolModel):
    parameter_id: NonEmptyId
    parameter_name: str = Field(min_length=1)
    unit: str | None = None
    recipe_step_no: int = Field(ge=1)
    recipe_step_name: str | None = None
    value_mean: float | None = None
    value_std: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    point_cnt: int = Field(ge=0)
    ooc_point_cnt: int = Field(ge=0)
    oos_point_cnt: int = Field(ge=0)
    spec_lower: float | None = None
    ctrl_lower: float | None = None
    target: float | None = None
    ctrl_upper: float | None = None
    spec_upper: float | None = None
    alarm_type: AlarmType


class AreaNode(ToolModel):
    area_id: NonEmptyId
    area_name: str | None = None


class ProcessStepNode(ToolModel):
    step_id: NonEmptyId
    step_name: str = Field(min_length=1)
    step_seq: int | None = None
    layer: str | None = None


class EquipmentNode(ToolModel):
    equipment_id: NonEmptyId
    equipment_name: str = Field(min_length=1)
    model_code: NonEmptyId
    area_id: NonEmptyId
    step_id: NonEmptyId | None = None


class ChamberNode(ToolModel):
    chamber_id: NonEmptyId
    equipment_id: NonEmptyId
    chamber_no: int | None = None
    model_code: NonEmptyId | None = None
    area_id: NonEmptyId | None = None
    step_id: NonEmptyId | None = None


class ParameterNode(ToolModel):
    parameter_id: NonEmptyId
    parameter_name: str = Field(min_length=1)
    unit: str | None = None


class GraphRelationRef(ToolModel):
    relation_id: NonEmptyId
    relation_type: NonEmptyId
    from_label: NonEmptyId
    from_business_id: NonEmptyId
    to_label: NonEmptyId
    to_business_id: NonEmptyId


class DocumentHit(ToolModel):
    chunk_id: NonEmptyId
    document_id: NonEmptyId
    title: str = Field(min_length=1)
    section: str | None = None
    score: float = Field(ge=-1.0, le=1.0)
    content: str = Field(min_length=1)
    model_code: NonEmptyId | None = None


class MetricPlan(ToolModel):
    type: Literal[
        "count", "sum", "mean", "median", "std", "min", "max", "percentile", "ratio"
    ]
    column: str | None = None
    p: float | None = Field(default=None, ge=0.0, le=100.0)


class VisualizationPlan(ToolModel):
    chart_type: ChartType
    x: str | None = None
    y: str | None = None


class AnomalySignal(ToolModel):
    """모델 provenance와 threshold 용도를 분리한 선택적 판정보조 신호."""

    score: float = Field(ge=0.0, le=1.0)
    model_version: NonEmptyId
    score_method: NonEmptyId
    display_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    is_anomaly: bool | None = None
    action_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    threshold_version: NonEmptyId | None = None
    threshold_validation_status: ThresholdValidationStatus

    @model_validator(mode="after")
    def _validate_threshold_contract(self) -> "AnomalySignal":
        has_display_threshold = self.display_threshold is not None
        has_display_flag = self.is_anomaly is not None
        if has_display_threshold != has_display_flag:
            raise ValueError(
                "display_threshold와 is_anomaly는 함께 제공하거나 함께 생략해야 합니다"
            )
        if has_display_threshold and self.is_anomaly != (
            self.score >= self.display_threshold
        ):
            raise ValueError("is_anomaly가 score·display_threshold와 일치하지 않습니다")

        has_action_threshold = self.action_threshold is not None
        has_threshold_version = self.threshold_version is not None
        if has_action_threshold != has_threshold_version:
            raise ValueError(
                "action_threshold와 threshold_version은 함께 제공해야 합니다"
            )
        if (
            has_display_threshold
            and has_action_threshold
            and self.action_threshold < self.display_threshold
        ):
            raise ValueError(
                "action_threshold는 display_threshold보다 작을 수 없습니다"
            )
        if (
            self.threshold_validation_status is ThresholdValidationStatus.VERIFIED
            and not has_action_threshold
        ):
            raise ValueError(
                "VERIFIED threshold에는 action_threshold와 "
                "threshold_version이 필요합니다"
            )
        return self


class IncidentModelSignal(ToolModel):
    """incident 전체 member를 검증·집계한 action 정책 입력 계약.

    WAFER 단위 ``AnomalySignal``은 표시·근거 DTO이고 조치 가능 여부를 말하지 않는다.
    실제 집계와 version allowlist 판정은 A batch service의 후속 구현 책임이다.
    """

    enabled: bool
    status: IncidentModelSignalStatus
    incident_score: float | None = Field(default=None, ge=0.0, le=1.0)
    display_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    action_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_member_count: int = Field(ge=1)
    valid_member_count: int = Field(ge=0)
    max_score_lot_hist_id: NonEmptyId | None = None
    model_version: NonEmptyId | None = None
    score_method: NonEmptyId | None = None
    threshold_version: NonEmptyId | None = None
    action_policy_version: NonEmptyId
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _validate_incident_signal(self) -> "IncidentModelSignal":
        if self.valid_member_count > self.expected_member_count:
            raise ValueError(
                "valid_member_count는 expected_member_count를 초과할 수 없습니다"
            )

        action_fields = (
            "incident_score",
            "display_threshold",
            "action_threshold",
            "max_score_lot_hist_id",
            "model_version",
            "score_method",
            "threshold_version",
        )

        if self.status is IncidentModelSignalStatus.READY:
            if not self.enabled:
                raise ValueError("READY signal은 enabled=true여야 합니다")
            if self.reason != "":
                raise ValueError("READY signal의 reason은 빈 문자열이어야 합니다")
            if (
                self.expected_member_count <= 0
                or self.expected_member_count != self.valid_member_count
            ):
                raise ValueError(
                    "READY signal은 expected_member_count == "
                    "valid_member_count > 0인 full coverage여야 합니다"
                )
            missing = [name for name in action_fields if getattr(self, name) is None]
            if missing:
                raise ValueError(
                    "READY signal에 완전한 incident score·threshold·provenance가 "
                    f"필요합니다: {', '.join(missing)}"
                )
            if self.action_threshold < self.display_threshold:
                raise ValueError(
                    "action_threshold는 display_threshold보다 작을 수 없습니다"
                )
            return self

        if self.reason == "":
            raise ValueError("DISABLED·UNAVAILABLE signal에는 reason이 필요합니다")

        if self.status is IncidentModelSignalStatus.DISABLED:
            if self.enabled:
                raise ValueError("DISABLED signal은 enabled=false여야 합니다")
        elif not self.enabled:
            raise ValueError("UNAVAILABLE signal은 enabled=true여야 합니다")

        populated = [name for name in action_fields if getattr(self, name) is not None]
        if populated:
            raise ValueError(
                "DISABLED·UNAVAILABLE signal에는 action 적용 가능한 score·threshold·"
                f"provenance를 넣을 수 없습니다: {', '.join(populated)}"
            )
        return self


# ---------------------------------------------------------------------
# Tool 1 — get_fdc_summary  (구현 A, 사용 C)
# ---------------------------------------------------------------------
class FdcSummaryToolInput(ToolModel):
    lot_hist_id: str = _tool_id()


class FdcSummaryToolResult(ToolResult):
    required_on_success: ClassVar[tuple[str, ...]] = ("wafer", "parameters")

    wafer: WaferContext | None = None
    parameters: list[ParameterSummaryItem] = Field(default_factory=list)
    anomaly: AnomalySignal | None = None


# ---------------------------------------------------------------------
# Tool 2 — get_equipment_context  (구현 B, 사용 C)
# ---------------------------------------------------------------------
class EquipmentContextToolInput(ToolModel):
    chamber_id: str = _tool_id()


class EquipmentContextToolResult(ToolResult):
    required_on_success: ClassVar[tuple[str, ...]] = (
        "chamber_id",
        "equipment_id",
        "area",
        "model_code",
        "process_step_id",
        "graph_revision",
    )

    chamber_id: NonEmptyId | None = None
    equipment_id: NonEmptyId | None = None
    sibling_chamber_ids: list[NonEmptyId] = Field(default_factory=list)
    area: NonEmptyId | None = None
    model_code: NonEmptyId | None = None
    process_step_id: NonEmptyId | None = None
    upstream_process_step_ids: list[NonEmptyId] = Field(default_factory=list)
    downstream_process_step_ids: list[NonEmptyId] = Field(default_factory=list)
    parameter_ids: list[NonEmptyId] = Field(default_factory=list)
    graph_revision: NonEmptyId | None = None


# ---------------------------------------------------------------------
# Tool 3 — search_documents  (구현 B, 사용 C)
# ---------------------------------------------------------------------
class DocumentSearchToolInput(ToolModel):
    query: str = Field(min_length=1, max_length=1000)
    model_code: NonEmptyId | None = None
    top_k: int = Field(default=4, ge=1, le=10)


class DocumentSearchToolResult(ToolResult):
    # 검색 결과 0건은 오류가 아니다.
    hits: list[DocumentHit] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Tool 4 — send_action  (구현·사용 C)
# ---------------------------------------------------------------------
class SendActionToolInput(ToolModel):
    action_id: str = _tool_id()


class ChannelDeliveryResult(ToolModel):
    channel: DeliveryChannel
    status: DeliveryStatus
    sent: bool
    duplicate: bool

    @model_validator(mode="after")
    def _validate_effect_flags(self) -> "ChannelDeliveryResult":
        if self.sent and self.duplicate:
            raise ValueError("sent와 duplicate은 동시에 true일 수 없습니다")
        if self.sent and self.status is not DeliveryStatus.SENT:
            raise ValueError("sent=true이면 status는 SENT여야 합니다")
        return self


class SendActionToolResult(ToolResult):
    required_on_success: ClassVar[tuple[str, ...]] = ("action_id", "deliveries")

    action_id: NonEmptyId | None = None
    deliveries: list[ChannelDeliveryResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_channels(self) -> "SendActionToolResult":
        channels = [delivery.channel for delivery in self.deliveries]
        if len(channels) != len(set(channels)):
            raise ValueError("deliveries에는 같은 channel을 중복할 수 없습니다")
        return self


# ---------------------------------------------------------------------
# Tool 5 — generate_analysis_plan  (구현·사용 D, 독립 실행)
# ---------------------------------------------------------------------
class AnalysisPlanToolInput(ToolModel):
    question: str = Field(min_length=1, max_length=1000)


class AnalysisPlanToolResult(ToolResult):
    required_on_success: ClassVar[tuple[str, ...]] = (
        "sql",
        "metric",
        "visualization",
    )

    sql: str | None = Field(default=None, min_length=1)
    metric: MetricPlan | None = None
    group_by: list[str] = Field(default_factory=list)
    visualization: VisualizationPlan | None = None


TOOL_RESULT_MODELS: dict[str, type[ToolResult]] = {
    "get_fdc_summary": FdcSummaryToolResult,
    "get_equipment_context": EquipmentContextToolResult,
    "search_documents": DocumentSearchToolResult,
    "send_action": SendActionToolResult,
    "generate_analysis_plan": AnalysisPlanToolResult,
}

# LangGraph가 호출하며 agent_tool_call에 기록하는 Tool.
AGENT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "get_fdc_summary",
        "get_equipment_context",
        "search_documents",
        "send_action",
    }
)


def fail(model: type[ToolResult], reason: str) -> ToolResult:
    return model(ok=False, reason=reason)
