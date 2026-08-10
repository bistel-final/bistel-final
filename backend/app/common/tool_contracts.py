from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.enums import ChartType, Judgement
from app.common.ids import NonEmptyId

# 실패 reason 은 아래 접두어 중 하나로 시작한다. 임의 접두어를 만들지 않는다.
REASON_PREFIXES: tuple[str, ...] = (
    "NOT_FOUND:",
    "TIMEOUT:",
    "MODEL_NOT_READY:",
    "LLM_NOT_READY:",
    "DEPENDENCY_ERROR:",
    "POLICY_REJECTED:",
    "IDEMPOTENCY_CONFLICT:",
)

_CONTRACT_FIELDS = ("ok", "reason")


def _tool_id() -> Any:
    return Field(min_length=1, max_length=20)


class ToolModel(BaseModel):
    # 공백만 있는 식별자가 통과하지 않도록 검증 전에 strip 한다.
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
                raise ValueError("성공 결과의 reason 은 빈 문자열이어야 합니다")

            for name in self.required_on_success:
                if self._is_empty(getattr(self, name)):
                    raise ValueError(f"성공 결과에 필수 값이 없습니다: {name}")

            return self

        if not self.reason.startswith(REASON_PREFIXES):
            allowed = " ".join(REASON_PREFIXES)
            raise ValueError(f"실패 reason 접두어는 다음 중 하나여야 합니다: {allowed}")

        # 실패 결과는 모든 payload 필드가 비어 있어야 한다.
        # 빈 문자열·0 처럼 falsy 하지만 null 이 아닌 값도 거부한다.
        for name in payload:
            value = getattr(self, name)

            if isinstance(value, bool):
                if value is not False:
                    raise ValueError(
                        f"실패 결과의 bool 필드는 False 여야 합니다: {name}"
                    )
            elif isinstance(value, list):
                if value:
                    raise ValueError(f"실패 결과의 목록 필드는 비어야 합니다: {name}")
            elif value is not None:
                raise ValueError(f"실패 결과의 필드는 None 이어야 합니다: {name}")

        return self

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if isinstance(value, bool):
            return value is False

        # strip 후 빈 문자열도 값이 없는 것으로 본다.
        if isinstance(value, str):
            return value == ""

        return value is None or value == []


# ---------------------------------------------------------------------
# 공유 payload — REST DTO 와 Tool 결과가 함께 사용한다
# ---------------------------------------------------------------------
class WaferContext(ToolModel):
    lot_hist_id: NonEmptyId
    lot_id: NonEmptyId
    wafer_no: int
    chamber_id: NonEmptyId
    equipment_id: NonEmptyId
    step_id: NonEmptyId
    recipe_id: NonEmptyId | None = None


class SensorSummaryItem(ToolModel):
    sensor_id: NonEmptyId
    sensor_name: str
    recipe_step_no: int
    recipe_step_name: str | None = None
    value_mean: float | None = None
    value_std: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    spec_lower: float | None = None
    ctrl_lower: float | None = None
    target: float | None = None
    ctrl_upper: float | None = None
    spec_upper: float | None = None
    judgement: Judgement


class AreaNode(ToolModel):
    area_id: NonEmptyId
    area_name: str | None = None


class ProcessStepNode(ToolModel):
    step_id: NonEmptyId
    step_name: str
    step_seq: int | None = None
    layer: str | None = None


class EquipmentNode(ToolModel):
    equipment_id: NonEmptyId
    equipment_name: str
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


class DocumentHit(ToolModel):
    chunk_id: NonEmptyId
    document_id: NonEmptyId
    title: str
    section: str | None = None
    score: float = Field(ge=-1.0, le=1.0)
    content: str
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


# ---------------------------------------------------------------------
# Tool 1 — get_fdc_summary  (구현 A, 사용 C)
# ---------------------------------------------------------------------
class FdcSummaryToolInput(ToolModel):
    lot_hist_id: str = _tool_id()


class FdcSummaryToolResult(ToolResult):
    # anomaly_score 는 WAFER 당 정확히 1개다. 없으면 성공이 아니다.
    required_on_success: ClassVar[tuple[str, ...]] = (
        "wafer",
        "sensors",
        "anomaly_score",
        "anomaly_threshold",
    )

    wafer: WaferContext | None = None
    sensors: list[SensorSummaryItem] = Field(default_factory=list)
    anomaly_score: float | None = Field(default=None, ge=0.0, le=1.0)
    anomaly_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    is_anomaly: bool = False

    @model_validator(mode="after")
    def _validate_anomaly_flag(self) -> "FdcSummaryToolResult":
        # 설계 5.3: is_anomaly 는 모델의 predict() 가 아니라 정규화 점수 비교로 정한다.
        # 경계값(score == threshold)은 이상으로 판정한다.
        if not self.ok:
            return self

        expected = self.anomaly_score >= self.anomaly_threshold
        if self.is_anomaly != expected:
            raise ValueError("is_anomaly 가 점수·임계값과 일치하지 않습니다")

        return self


# ---------------------------------------------------------------------
# Tool 2 — get_equipment_context  (구현 B, 사용 C)
# ---------------------------------------------------------------------
class EquipmentContextToolInput(ToolModel):
    chamber_id: str = _tool_id()


class EquipmentContextToolResult(ToolResult):
    # 상하류·형제 챔버는 없을 수 있다. ETC-01-C1 은 downstream 이 없다.
    required_on_success: ClassVar[tuple[str, ...]] = ("equipment",)

    equipment: EquipmentNode | None = None
    area: AreaNode | None = None
    step: ProcessStepNode | None = None
    sibling_chambers: list[ChamberNode] = Field(default_factory=list)
    upstream: list[EquipmentNode] = Field(default_factory=list)
    downstream: list[EquipmentNode] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Tool 3 — search_documents  (구현 B, 사용 C)
# ---------------------------------------------------------------------
class DocumentSearchToolInput(ToolModel):
    query: str = Field(min_length=1, max_length=1000)
    model_code: NonEmptyId | None = None
    top_k: int = Field(default=4, ge=1, le=10)


class DocumentSearchToolResult(ToolResult):
    # 검색 결과 0건은 오류가 아니므로 필수 값을 두지 않는다.
    hits: list[DocumentHit] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Tool 4 — send_action  (구현·사용 C)
# ---------------------------------------------------------------------
class SendActionToolInput(ToolModel):
    action_id: str = _tool_id()
    agent_run_id: str = _tool_id()


class SendActionToolResult(ToolResult):
    # 실패 시 action_id 도 None 이다. 호출자는 Input 과 agent_tool_call.input_json
    # 으로 이미 알고 있으므로 결과에서 되돌려줄 필요가 없다.
    required_on_success: ClassVar[tuple[str, ...]] = ("action_id", "sent")

    action_id: str | None = Field(default=None, min_length=1, max_length=20)
    sent: bool = False


# ---------------------------------------------------------------------
# Tool 5 — generate_analysis_plan  (구현·사용 D, 독립 실행)
#   LangGraph 가 호출하지 않으므로 AGENT_MAX_TOOL_CALLS 예산에 포함하지 않는다.
# ---------------------------------------------------------------------
class AnalysisPlanToolInput(ToolModel):
    question: str = Field(min_length=1, max_length=1000)


class AnalysisPlanToolResult(ToolResult):
    # group_by 는 비어 있을 수 있다(전체 집계).
    required_on_success: ClassVar[tuple[str, ...]] = ("sql", "metric", "visualization")

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

# LangGraph 가 호출하며 agent_tool_call 에 기록하는 Tool.
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
