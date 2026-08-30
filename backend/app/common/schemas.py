from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.enums import AlarmSource
from app.common.ids import (
    NonEmptyId,
    format_alarm_ref_token,
    parse_alarm_ref_token,
)


class ApiModel(BaseModel):
    """REST 요청·응답 모델의 공통 엄격성 계약."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


ItemT = TypeVar("ItemT")


class PageResponse(ApiModel, Generic[ItemT]):
    items: list[ItemT]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=100)


class IncidentRef(ApiModel):
    lot_id: NonEmptyId
    chamber_id: NonEmptyId


class AlarmRef(ApiModel):
    """서로 다른 저장·파생 알람 ID 공간을 구분하는 공통 식별자."""

    source: AlarmSource
    alarm_id: NonEmptyId

    def to_token(self) -> str:
        return format_alarm_ref_token(self.source, self.alarm_id)

    @classmethod
    def from_token(cls, token: str) -> "AlarmRef":
        source, alarm_id = parse_alarm_ref_token(token)
        return cls(source=source, alarm_id=alarm_id)


class HealthResponse(ApiModel):
    status: Literal["UP"]


ReadinessFailureReason = Literal[
    "NOT_CONFIGURED",
    "CONTRACT_MISMATCH",
    "DEPENDENCY_UNAVAILABLE",
    "RAG_MODEL_NOT_READY",
    "KAFKA_LAG_STALE",
    "TIMEOUT",
]


class ReadinessCheck(ApiModel):
    status: Literal["PASS", "FAIL"]
    reason_code: ReadinessFailureReason | None
    latency_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_reason(self) -> "ReadinessCheck":
        if self.status == "PASS" and self.reason_code is not None:
            raise ValueError("PASS check의 reason_code는 null이어야 합니다")
        if self.status == "FAIL" and self.reason_code is None:
            raise ValueError("FAIL check에는 reason_code가 필요합니다")
        return self


class ReadinessChecks(ApiModel):
    postgresql_runtime: ReadinessCheck
    reference_migration: ReadinessCheck
    neo4j: ReadinessCheck
    rag: ReadinessCheck
    n8n: ReadinessCheck
    kafka: ReadinessCheck

    @model_validator(mode="after")
    def validate_reason_ownership(self) -> "ReadinessChecks":
        common = {
            "NOT_CONFIGURED",
            "CONTRACT_MISMATCH",
            "DEPENDENCY_UNAVAILABLE",
            "TIMEOUT",
        }
        allowed = {
            "postgresql_runtime": common,
            "reference_migration": common,
            "neo4j": common,
            "rag": common | {"RAG_MODEL_NOT_READY"},
            "n8n": common,
            "kafka": common | {"KAFKA_LAG_STALE"},
        }
        for name, reasons in allowed.items():
            reason = getattr(self, name).reason_code
            if reason is not None and reason not in reasons:
                raise ValueError(f"{name} check에 허용되지 않은 reason_code입니다")
        return self


class ReadinessResponse(ApiModel):
    status: Literal["READY", "NOT_READY"]
    dataset_epoch: Literal["fdc_final_20260818"]
    checks: ReadinessChecks

    @model_validator(mode="after")
    def validate_status(self) -> "ReadinessResponse":
        expected = (
            "READY"
            if all(
                getattr(self.checks, name).status == "PASS"
                for name in ReadinessChecks.model_fields
            )
            else "NOT_READY"
        )
        if self.status != expected:
            raise ValueError("readiness 상태와 check 상태가 일치하지 않습니다")
        return self
