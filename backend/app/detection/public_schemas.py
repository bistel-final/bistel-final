"""API v3 Detection core 공개 DTO.

내부 Detection 모델(`schemas.py`)과 분리해 deprecated React alias와 Agent의
nullable prediction projection이 계산·평가 모델로 역류하지 않게 한다.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.common.enums import (
    ActionCode,
    AlarmSource,
    DeliveryStatus,
    FaultHypothesis,
)
from app.common.ids import NonEmptyId
from app.common.schemas import ApiModel


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
    def validate_core_aliases(self) -> "AlarmItem":
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
    def validate_aliases(self) -> "ParameterItem":
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
