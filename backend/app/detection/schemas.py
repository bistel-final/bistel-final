from datetime import date, datetime

from pydantic import Field, model_validator

from app.agent.schemas import ApprovalItem
from app.common.enums import (
    ActionApprovalStatus,
    ActionCode,
    AgentRunStatus,
    Judgement,
)
from app.common.ids import NonEmptyId
from app.common.schemas import ApiModel, IncidentRef, PageResponse
from app.common.tool_contracts import SensorSummaryItem, WaferContext


class AlarmItem(ApiModel):
    alarm_id: NonEmptyId
    lot_hist_id: NonEmptyId
    lot_id: NonEmptyId
    wafer_no: int | None = Field(default=None, ge=1)
    chamber_id: NonEmptyId | None = None
    equipment_id: NonEmptyId | None = None
    sensor_id: NonEmptyId | None = None
    recipe_step_no: int | None = Field(default=None, ge=1)
    recipe_step_name: str | None = None
    rule_id: NonEmptyId | None = None
    judgement: Judgement | None = None
    hit_cnt: int | None = Field(default=None, ge=0)
    detail: str | None = None
    occurred_at: datetime
    incident: IncidentRef
    action_id: NonEmptyId | None = None
    action_code: ActionCode | None = None
    approval_status: ActionApprovalStatus | None = None
    latest_agent_run_id: NonEmptyId | None = None
    agent_run_status: AgentRunStatus | None = None


class AlarmPageResponse(PageResponse[AlarmItem]):
    pass


class HierarchyNode(ApiModel):
    area_id: NonEmptyId
    equipment_id: NonEmptyId
    model_code: NonEmptyId | None = None
    chambers: list[NonEmptyId]


class DailyTrendItem(ApiModel):
    date: date
    oos_count: int = Field(ge=0)
    ooc_count: int = Field(ge=0)
    has_r03_consec: bool


class TopSensorItem(ApiModel):
    sensor_id: NonEmptyId
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
    reference_date: date | None = None
    area: NonEmptyId | None = None
    date_range: list[date]
    hierarchy: list[HierarchyNode]
    sensor_catalog: list[NonEmptyId]
    alarm_count: int = Field(ge=0)
    oos_count: int = Field(ge=0)
    ooc_count: int = Field(ge=0)
    daily_trend: list[DailyTrendItem]
    top_sensors: list[TopSensorItem]
    equipment_counts: list[EquipmentCountItem]
    pending_approvals: list["ApprovalItem"]
    recent_alarms: list[AlarmItem]

    @model_validator(mode="after")
    def validate_date_range(self) -> "DashboardSummaryResponse":
        if len(self.date_range) not in {0, 2}:
            raise ValueError("date_range는 빈 배열 또는 [시작일, 종료일]이어야 합니다")
        if len(self.date_range) == 2 and self.date_range[0] > self.date_range[1]:
            raise ValueError("date_range 시작일은 종료일보다 늦을 수 없습니다")
        return self


class FdcSummaryResponse(ApiModel):
    wafer: WaferContext
    sensors: list[SensorSummaryItem] = Field(min_length=1)
    anomaly_score: float = Field(ge=0.0, le=1.0)
    anomaly_threshold: float = Field(ge=0.0, le=1.0)
    is_anomaly: bool

    @model_validator(mode="after")
    def validate_anomaly_flag(self) -> "FdcSummaryResponse":
        if self.is_anomaly != (self.anomaly_score >= self.anomaly_threshold):
            raise ValueError("is_anomaly가 점수·임계값과 일치하지 않습니다")
        return self


class SensorLimits(ApiModel):
    unit: str | None = None
    spec_lower: float | None = None
    ctrl_lower: float | None = None
    target: float | None = None
    ctrl_upper: float | None = None
    spec_upper: float | None = None
    upper_only: bool


class TraceCatalogArea(ApiModel):
    area_id: NonEmptyId


class TraceCatalogEquipment(ApiModel):
    equipment_id: NonEmptyId
    area_id: NonEmptyId
    model_code: NonEmptyId | None = None
    chambers: list[NonEmptyId]


class TraceCatalogSensor(SensorLimits):
    sensor_id: NonEmptyId
    sensor_name: str


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


class TraceCatalogAnomaly(ApiModel):
    threshold: float = Field(ge=0.0, le=1.0)


class TraceCatalogResponse(ApiModel):
    areas: list[TraceCatalogArea]
    equipments: list[TraceCatalogEquipment]
    sensors: list[TraceCatalogSensor]
    recipes: list[TraceCatalogRecipe]
    lots: list[TraceCatalogLot]
    anomaly: TraceCatalogAnomaly


class TraceSearchRequest(ApiModel):
    area: NonEmptyId | None = None
    equipment_id: NonEmptyId | None = None
    chamber_id: NonEmptyId | None = None
    sensor_ids: list[NonEmptyId] = Field(min_length=1)
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
        if len(self.sensor_ids) != len(set(self.sensor_ids)):
            raise ValueError("sensor_ids에는 중복 값을 넣을 수 없습니다")
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
    sensor_id: NonEmptyId
    occurred_at: datetime | None = None
    points: list[TracePoint]


class MeasuredStepStat(ApiModel):
    lot_hist_id: NonEmptyId
    sensor_id: NonEmptyId
    recipe_step_no: int = Field(ge=1)
    recipe_step_name: str | None = None
    value_mean: float | None = None
    value_std: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    point_cnt: int = Field(ge=0)
    ooc_point_cnt: int = Field(ge=0)
    oos_point_cnt: int = Field(ge=0)
    judgement: Judgement


class TraceSearchResponse(ApiModel):
    wafers: list[TraceWaferSeries]
    limits: dict[NonEmptyId, SensorLimits]
    measured_step_stats: list[MeasuredStepStat]
    total: int = Field(ge=0)
