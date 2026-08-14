from datetime import date, datetime
from typing import Any

from pydantic import Field, model_validator

from app.common.enums import (
    ActionCode,
    AlarmSource,
    AlarmType,
    ApprovalStatus,
    RunStatus,
)
from app.common.ids import NonEmptyId
from app.common.schemas import AlarmRef, ApiModel, IncidentRef, PageResponse
from app.common.tool_contracts import AnomalySignal


class AlarmItem(ApiModel):
    """TRACE·SUMMARY·R03를 같은 화면 계약으로 노출하는 source-aware 알람."""

    alarm: AlarmRef
    occurred_at: datetime
    area: NonEmptyId
    equipment_id: NonEmptyId
    chamber_id: NonEmptyId
    parameter_id: NonEmptyId
    parameter_name: str | None = None
    recipe_id: NonEmptyId | None = None
    lot_hist_id: NonEmptyId | None = None
    lot_id: NonEmptyId
    wafer_no: int | None = Field(default=None, ge=1)
    recipe_step_no: int | None = Field(default=None, ge=1)
    recipe_step_name: str | None = None
    alarm_type: AlarmType
    value: float | None = None
    limit_type: str | None = None
    limit_value: float | None = None
    source_detail: dict[str, Any] = Field(default_factory=dict)
    incident: IncidentRef
    action_id: NonEmptyId | None = None
    action_code: ActionCode | None = None
    approval_status: ApprovalStatus | None = None
    latest_agent_run_id: NonEmptyId | None = None
    agent_run_status: RunStatus | None = None


class AlarmPageResponse(PageResponse[AlarmItem]):
    pass


class HierarchyNode(ApiModel):
    area_id: NonEmptyId
    equipment_id: NonEmptyId
    model_code: NonEmptyId | None = None
    chambers: list[NonEmptyId]


class DailyTrendItem(ApiModel):
    date: date
    trace_oos_count: int = Field(ge=0)
    summary_ooc_count: int = Field(ge=0)
    r03_count: int = Field(ge=0)


class TopParameterItem(ApiModel):
    parameter_id: NonEmptyId
    alarm_count: int = Field(ge=0)
    chamber_ids: list[NonEmptyId]


class ChamberAlarmCount(ApiModel):
    chamber_id: NonEmptyId
    alarm_count: int = Field(ge=0)


class EquipmentCountItem(ApiModel):
    equipment_id: NonEmptyId
    area_id: NonEmptyId | None = None
    alarm_count: int = Field(ge=0)
    chambers: list[ChamberAlarmCount]


class DashboardSummaryResponse(ApiModel):
    date_range: list[date]
    area: NonEmptyId
    hierarchy: list[HierarchyNode]
    parameter_catalog: list[NonEmptyId]
    alarm_count: int = Field(ge=0)
    trace_oos_count: int = Field(ge=0)
    summary_ooc_count: int = Field(ge=0)
    r03_count: int = Field(ge=0)
    source_counts: dict[AlarmSource, int]
    daily_trend: list[DailyTrendItem]
    top_parameters: list[TopParameterItem]
    equipment_counts: list[EquipmentCountItem]
    recent_alarms: list[AlarmItem]

    @model_validator(mode="after")
    def validate_summary(self) -> "DashboardSummaryResponse":
        if len(self.date_range) != 2:
            raise ValueError("date_range는 [시작일, 종료일]이어야 합니다")
        if self.date_range[0] > self.date_range[1]:
            raise ValueError("date_range 시작일은 종료일보다 늦을 수 없습니다")
        if any(count < 0 for count in self.source_counts.values()):
            raise ValueError("source별 알람 수는 음수일 수 없습니다")
        if sum(self.source_counts.values()) != self.alarm_count:
            raise ValueError("source_counts 합계는 alarm_count와 일치해야 합니다")
        return self


class ParameterLimits(ApiModel):
    unit: str | None = None
    spec_lower: float | None = None
    ctrl_lower: float | None = None
    target: float | None = None
    ctrl_upper: float | None = None
    spec_upper: float | None = None
    # upper_only는 한계값 null 여부로 추정하지 않고 corrected metadata를 따른다.
    upper_only: bool


class TraceCatalogArea(ApiModel):
    area_id: NonEmptyId


class TraceCatalogEquipment(ApiModel):
    equipment_id: NonEmptyId
    area_id: NonEmptyId
    model_code: NonEmptyId | None = None
    chambers: list[NonEmptyId]


class TraceCatalogParameter(ParameterLimits):
    parameter_id: NonEmptyId
    parameter_name: str


class TraceCatalogRecipe(ApiModel):
    recipe_id: NonEmptyId
    step_id: NonEmptyId


class TraceCatalogLot(ApiModel):
    lot_id: NonEmptyId
    wafer_nos: list[int]

    @model_validator(mode="after")
    def validate_wafer_numbers(self) -> "TraceCatalogLot":
        if any(wafer_no < 1 for wafer_no in self.wafer_nos):
            raise ValueError("wafer_no는 1 이상이어야 합니다")
        return self


class TraceCatalogResponse(ApiModel):
    areas: list[TraceCatalogArea]
    equipments: list[TraceCatalogEquipment]
    parameters: list[TraceCatalogParameter]
    recipes: list[TraceCatalogRecipe]
    lots: list[TraceCatalogLot]
    # React 다중 Trace 화면용 선택 adapter도 Tool과 같은 provenance 계약을 쓴다.
    anomaly: AnomalySignal | None = None


class TraceSearchRequest(ApiModel):
    area: NonEmptyId | None = None
    equipment_id: NonEmptyId | None = None
    chamber_id: NonEmptyId | None = None
    parameter_ids: list[NonEmptyId] = Field(min_length=1)
    recipe_id: NonEmptyId | None = None
    lot_id: NonEmptyId | None = None
    wafer_nos: list[int] = Field(default_factory=list)
    from_: datetime | None = Field(
        default=None,
        validation_alias="from",
        serialization_alias="from",
    )
    to: datetime | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "TraceSearchRequest":
        if any(wafer_no < 1 for wafer_no in self.wafer_nos):
            raise ValueError("wafer_no는 1 이상이어야 합니다")
        if len(self.parameter_ids) != len(set(self.parameter_ids)):
            raise ValueError("parameter_ids에는 중복 값을 넣을 수 없습니다")
        if len(self.wafer_nos) != len(set(self.wafer_nos)):
            raise ValueError("wafer_nos에는 중복 값을 넣을 수 없습니다")
        if self.from_ is not None and self.to is not None and self.from_ >= self.to:
            raise ValueError("from은 to보다 빨라야 합니다")
        return self


class TracePoint(ApiModel):
    seq_no: int = Field(ge=0)
    recipe_step_no: int | None = Field(default=None, ge=1)
    recipe_step_name: str | None = None
    measured_at: datetime | None = None
    value: float


class TraceWaferSeries(ApiModel):
    lot_hist_id: NonEmptyId
    lot_id: NonEmptyId
    wafer_no: int = Field(ge=1)
    chamber_id: NonEmptyId
    equipment_id: NonEmptyId
    recipe_id: NonEmptyId | None = None
    parameter_id: NonEmptyId
    occurred_at: datetime | None = None
    points: list[TracePoint]


class MeasuredStepStat(ApiModel):
    lot_hist_id: NonEmptyId
    parameter_id: NonEmptyId
    recipe_step_no: int = Field(ge=1)
    recipe_step_name: str | None = None
    value_mean: float | None = None
    value_std: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    point_cnt: int = Field(ge=0)
    ooc_point_cnt: int = Field(ge=0)
    oos_point_cnt: int = Field(ge=0)
    alarm_type: AlarmType


class TraceSearchResponse(ApiModel):
    wafers: list[TraceWaferSeries]
    limits: dict[NonEmptyId, ParameterLimits]
    measured_step_stats: list[MeasuredStepStat]
    total: int = Field(ge=0)
