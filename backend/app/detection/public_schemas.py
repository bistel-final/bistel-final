"""API v3 Detection 공개 DTO.

이 모듈은 서로 목적이 다른 **두 DTO 계열**을 담는다. 내부 Detection 모델
(`schemas.py`)과는 분리해 deprecated React alias와 Agent의 nullable prediction
projection이 계산·평가 모델로 역류하지 않게 한다.

1. **호환 필수 계약** (`AlarmItem`·`TracePoint`·`ParameterItem`)
   멘토 기준 public 필수 `/alarms`·`/trace`·`/parameters`의 응답이다. API v3
   §2.7 "최종 참고 React 호환 projection"이 고정한 deprecated alias
   (`equipment`·`chamber`·`lot`·`wafer`·`parameter`·`step_no`·`fault`·`notify`·
   `mes`·`LSL`/`UCL` 등)를 canonical field와 **함께** 노출하며, alias는 전부
   canonical 값에서 1:1로 파생한다(`model_validator`가 강제한다).

2. **화면 확장** (`ScreenAlarmItem` 이하)
   API v3 §5.2 선택 확장 5개(`/dashboard/summary`·`/alarms/paged`·
   `/alarms/{source}/{alarm_id}`·`/traces/catalog`·`/traces/search`)의 응답이다.
   화면 1(알람 대시보드)·화면 2(알람 히스토리)가 실제로 렌더링하는 필드
   (진행 중인 조치·승인 상태·같은 incident 알람 묶음)를 그대로 노출한다.

두 계열을 한 DTO로 합치지 않는 이유: compat 계약(§7 "canonical field에서만
파생")이 화면 전용 join(조치·승인·Agent run)에 오염되면 안 된다. 계열별로
파생 규칙과 제거 시점이 다르므로 class는 끝까지 분리해 둔다.

명명 규칙(화면 확장 계열):
- `sensor_id`는 물리 컬럼 `parameter_id`의 화면 alias다(화면 1·2 시안이 최초
  작성될 때부터 쓰인 이름이라 걷어내지 않는다 — 실측 컬럼 자체는 바뀌지 않는다).
- `rule_id`는 저장된 `rule_code`(TRACE_OOS/SUMMARY_OOC/R03_CONSEC)를 화면 배지
  표기(R01_OOS/R02_OOC/R03_CONSEC)로 1:1 재라벨링한 값이다 — source가 정하므로
  창작이 아니다. 실제 스키마에는 R01/R02 세부 rule 구분이 없다(설계서 v2.1 4.2·4.3
  — SUMMARY는 동적 관리한계 하나, TRACE는 spec 이탈 하나뿐).
- `recipe_step_name`은 물리 스키마 어디에도 없다(`repository.py` 830행대 주석).
  Tool(`get_fdc_summary`)·compat 필수 API는 그래서 항상 None을 반환하지만, 화면은
  step 축을 문자열 키로 묶어야 해서(`TraceModel.decorateWafers`) `STEP{no}`
  형태의 순수 라벨을 만든다 — 측정값이 아니라 순서 표시이므로 NFR-19 대상이 아니다.
- `hit_cnt`·`detail`의 평균·최소·최대값은 전부 `evaluation`·`summary_data`
  base table의 실측 집계에서 가져온다. 값을 만들지 않고, 구할 수 없으면 필드를
  생략한다(TraceModel.jsx 상단 주석 "값 창작 금지"와 같은 원칙).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from app.common.enums import (
    ActionCode,
    AlarmSource,
    ApprovalStatus,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    Severity,
)
from app.common.ids import NonEmptyId
from app.common.schemas import ApiModel, IncidentRef, PageResponse

# ---------------------------------------------------------------------
# 1. 호환 필수 계약 — GET /alarms · /trace · /parameters (API v3 §2.7)
# ---------------------------------------------------------------------


class AlarmItem(ApiModel):
    """API v3 core TRACE·SUMMARY·R03 평면 알람."""

    source: AlarmSource
    alarm_id: NonEmptyId
    occurred_at: datetime
    area: Literal["Photo", "Etch"]
    equipment_id: NonEmptyId
    equipment: NonEmptyId
    chamber_id: NonEmptyId
    chamber: NonEmptyId
    recipe_id: NonEmptyId
    recipe: NonEmptyId
    lot_id: NonEmptyId
    lot: NonEmptyId
    wafer_id: NonEmptyId
    wafer: NonEmptyId
    parameter_id: NonEmptyId
    parameter: NonEmptyId
    recipe_step_no: int = Field(ge=1)
    step_no: int = Field(ge=1)
    seq_no: int | None = Field(default=None, ge=0)
    alarm_type: Literal["OOC", "OOS"]
    value: float | None = None
    rule_code: Literal["TRACE_OOS", "SUMMARY_OOC", "R03_CONSEC"]
    predicted_fault_code: FaultHypothesis | None = None
    fault: FaultHypothesis | None = None
    action_code: ActionCode | None = None
    notify_status: Literal["WAITING", "SENDING", "SENT", "FAILED", "UNKNOWN"] | None = (
        None
    )
    notify: bool
    mes_status: DeliveryStatus | None = None
    mes: str
    statistic_type: str | None = None
    cl: float | None = None
    ucl: float | None = None
    lcl: float | None = None

    @model_validator(mode="after")
    def validate_core_aliases(self) -> AlarmItem:
        aliases = (
            (self.equipment, self.equipment_id),
            (self.chamber, self.chamber_id),
            (self.recipe, self.recipe_id),
            (self.lot, self.lot_id),
            (self.wafer, self.wafer_id),
            (self.parameter, self.parameter_id),
            (self.step_no, self.recipe_step_no),
            (self.fault, self.predicted_fault_code),
        )
        if any(alias != canonical for alias, canonical in aliases):
            raise ValueError("호환 alias가 canonical 필드와 다릅니다")
        if self.notify != (self.notify_status == "SENT"):
            raise ValueError("notify는 notify_status=SENT일 때만 true입니다")
        expected_mes = "" if self.mes_status is None else self.mes_status.value
        if self.mes != expected_mes:
            raise ValueError("mes alias가 mes_status와 다릅니다")
        expected_rule = {
            AlarmSource.TRACE: "TRACE_OOS",
            AlarmSource.SUMMARY: "SUMMARY_OOC",
            AlarmSource.R03: "R03_CONSEC",
        }[self.source]
        if self.rule_code != expected_rule:
            raise ValueError("source와 rule_code가 다릅니다")
        if self.source is AlarmSource.R03 and (
            self.alarm_type != "OOS" or self.value is not None
        ):
            raise ValueError("R03는 OOS·value=null이어야 합니다")
        return self


class TracePoint(ApiModel):
    seq_no: int = Field(ge=0)
    recipe_step_no: int = Field(ge=1)
    measured_at: datetime
    value: float


class ParameterItem(ApiModel):
    """API v3 core parameter 기준정보와 1-revision 호환 alias."""

    parameter_id: NonEmptyId
    parameter_name: str = Field(min_length=1)
    name: str = Field(min_length=1)
    area: Literal["Photo", "Etch"]
    unit: str | None = None
    spec_lower: float | None = None
    LSL: float | None = None
    ctrl_lower: float | None = None
    LCL: float | None = None
    target_value: float
    TARGET: float
    ctrl_upper: float
    UCL: float
    spec_upper: float
    USL: float
    upper_only: bool

    @model_validator(mode="after")
    def validate_aliases(self) -> ParameterItem:
        aliases = (
            (self.name, self.parameter_name),
            (self.LSL, self.spec_lower),
            (self.LCL, self.ctrl_lower),
            (self.TARGET, self.target_value),
            (self.UCL, self.ctrl_upper),
            (self.USL, self.spec_upper),
        )
        if any(alias != canonical for alias, canonical in aliases):
            raise ValueError("parameter 호환 alias가 canonical 값과 다릅니다")
        return self


# ---------------------------------------------------------------------
# 2. 화면 확장 — API v3 §5.2 선택 확장 5개 (화면 1·2 전용)
# ---------------------------------------------------------------------


Area = Literal["Photo", "Etch"]
Judgement = Literal["OOS", "OOC"]
RuleId = Literal["R01_OOS", "R02_OOC", "R03_CONSEC"]

RULE_ID_BY_SOURCE: dict[AlarmSource, RuleId] = {
    AlarmSource.TRACE: "R01_OOS",
    AlarmSource.SUMMARY: "R02_OOC",
    AlarmSource.R03: "R03_CONSEC",
}


class ScreenAlarmItem(ApiModel):
    """화면 1·2 alarm row — TRACE·SUMMARY·R03을 같은 모양으로 노출한다."""

    alarm_id: NonEmptyId
    source: AlarmSource
    occurred_at: datetime
    area: Area
    equipment_id: NonEmptyId
    chamber_id: NonEmptyId
    sensor_id: NonEmptyId
    lot_hist_id: NonEmptyId | None = None
    lot_id: NonEmptyId
    wafer_no: int | None = Field(default=None, ge=1)
    recipe_step_no: int = Field(ge=1)
    recipe_step_name: str | None = None
    rule_id: RuleId
    judgement: Judgement
    hit_cnt: int | None = Field(default=None, ge=0)
    detail: str
    incident: IncidentRef
    action_id: NonEmptyId | None = None
    action_code: ActionCode | None = None
    approval_status: ApprovalStatus | None = None
    latest_agent_run_id: NonEmptyId | None = None
    agent_run_status: RunStatus | None = None

    @model_validator(mode="after")
    def validate_rule_id(self) -> ScreenAlarmItem:
        expected = RULE_ID_BY_SOURCE[self.source]
        if self.rule_id != expected:
            raise ValueError("source와 rule_id가 다릅니다")
        expected_judgement: Judgement = (
            "OOS" if self.source is not AlarmSource.SUMMARY else "OOC"
        )
        if self.judgement != expected_judgement:
            raise ValueError("source와 judgement가 다릅니다")
        if self.incident.lot_id != self.lot_id:
            raise ValueError("incident.lot_id는 lot_id와 같아야 합니다")
        if self.incident.chamber_id != self.chamber_id:
            raise ValueError("incident.chamber_id는 chamber_id와 같아야 합니다")
        return self


class AlarmPageEnvelope(PageResponse[ScreenAlarmItem]):
    pass


class HierarchyNode(ApiModel):
    area_id: Area
    equipment_id: NonEmptyId
    chambers: list[NonEmptyId]


class PendingApprovalItem(ApiModel):
    approval_id: NonEmptyId
    action_id: NonEmptyId
    agent_run_id: NonEmptyId
    incident: IncidentRef
    equipment_id: NonEmptyId
    action_code: ActionCode
    severity: Severity
    requested_at: datetime


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
    area_id: Area
    alarm_count: int = Field(ge=0)
    chambers: list[ChamberAlarmCount]


class DashboardSummaryResponse(ApiModel):
    """`GET /dashboard/summary` 응답 — 화면 1의 hierarchy·KPI·추이 원본."""

    reference_date: date
    date_range: list[date]
    area: Area | None
    hierarchy: list[HierarchyNode]
    sensor_catalog: list[NonEmptyId]
    alarm_count: int = Field(ge=0)
    oos_count: int = Field(ge=0)
    ooc_count: int = Field(ge=0)
    daily_trend: list[DailyTrendItem]
    top_sensors: list[TopSensorItem]
    equipment_counts: list[EquipmentCountItem]
    pending_approvals: list[PendingApprovalItem]
    recent_alarms: list[ScreenAlarmItem]

    @model_validator(mode="after")
    def validate_counts(self) -> DashboardSummaryResponse:
        if self.oos_count + self.ooc_count != self.alarm_count:
            raise ValueError("oos_count + ooc_count는 alarm_count와 같아야 합니다")
        return self


class ParameterLimit(ApiModel):
    unit: str | None = None
    spec_lower: float | None = None
    ctrl_lower: float | None = None
    target: float | None = None
    ctrl_upper: float
    spec_upper: float
    upper_only: bool


class TraceCatalogArea(ApiModel):
    area_id: Area


class TraceCatalogEquipment(ApiModel):
    equipment_id: NonEmptyId
    area_id: Area
    chambers: list[NonEmptyId]


class TraceCatalogSensor(ParameterLimit):
    sensor_id: NonEmptyId
    sensor_name: str


class TraceCatalogRecipe(ApiModel):
    recipe_id: NonEmptyId
    area_id: Area


class TraceCatalogLot(ApiModel):
    lot_id: NonEmptyId
    wafer_nos: list[int]


class TraceCatalogResponse(ApiModel):
    """`GET /traces/catalog` 응답 — 화면 2 조회 선택지 + 센서별 한계선."""

    areas: list[TraceCatalogArea]
    equipments: list[TraceCatalogEquipment]
    sensors: list[TraceCatalogSensor]
    recipes: list[TraceCatalogRecipe]
    lots: list[TraceCatalogLot]


class TraceSearchRequest(ApiModel):
    """`POST /traces/search` 요청 — 다중 웨이퍼·다중 센서 복합 조회."""

    area: Area | None = None
    equipment_id: NonEmptyId | None = None
    chamber_id: NonEmptyId | None = None
    sensor_ids: list[NonEmptyId] | None = None
    recipe_id: NonEmptyId | None = None
    lot_id: NonEmptyId | None = None
    wafer_nos: list[int] | None = None
    from_: datetime | None = Field(
        default=None, validation_alias="from", serialization_alias="from"
    )
    to: datetime | None = None

    @model_validator(mode="after")
    def validate_range(self) -> TraceSearchRequest:
        if self.from_ is not None and self.to is not None and self.from_ >= self.to:
            raise ValueError("from은 to보다 빨라야 합니다")
        if self.wafer_nos is not None and any(w < 1 for w in self.wafer_nos):
            raise ValueError("wafer_no는 1 이상이어야 합니다")
        return self


class TraceSearchPoint(ApiModel):
    seq_no: int = Field(ge=0)
    recipe_step_no: int = Field(ge=1)
    recipe_step_name: str | None = None
    measured_at: datetime
    value: float


class TraceSearchWafer(ApiModel):
    lot_hist_id: NonEmptyId
    lot_id: NonEmptyId
    wafer_no: int = Field(ge=1)
    chamber_id: NonEmptyId
    equipment_id: NonEmptyId
    sensor_id: NonEmptyId
    occurred_at: datetime | None = None
    points: list[TraceSearchPoint]
    missing_steps: list[str] = Field(default_factory=list)


class TraceSearchResponse(ApiModel):
    wafers: list[TraceSearchWafer]
    limits: dict[str, TraceCatalogSensor]
    measured_step_stats: dict[str, dict] = Field(default_factory=dict)
    total: int = Field(ge=0)


class AlarmDetailResponse(ScreenAlarmItem):
    """`GET /alarms/{source}/{alarm_id}` 응답 — 화면 2 상세 패널 원본.

    필드 구성은 목록 항목(`ScreenAlarmItem`)과 같다 — source-aware 단건 조회라서
    추가 필드가 필요 없다. 별도 이름을 두는 이유는 API 명세 v3 §5.2 표의 성공
    응답 타입(`AlarmDetailResponse`)과 코드를 1:1로 맞추기 위해서다.
    """
