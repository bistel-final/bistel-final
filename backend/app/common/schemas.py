from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.ids import NonEmptyId


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


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    service: str = Field(min_length=1)
    timestamp: datetime


ReadinessFailureReason = Literal[
    "TIMEOUT",
    "CONNECTION_FAILED",
    "UNEXPECTED_RESPONSE",
]


class ReadinessDependency(ApiModel):
    status: Literal["up", "down"]
    latency_ms: int = Field(ge=0)
    reason: ReadinessFailureReason | None = None

    @model_validator(mode="after")
    def validate_reason(self) -> "ReadinessDependency":
        if self.status == "up" and self.reason is not None:
            raise ValueError("정상 의존성에는 실패 reason을 넣을 수 없습니다")
        if self.status == "down" and self.reason is None:
            raise ValueError("실패 의존성에는 정규화된 reason이 필요합니다")
        return self


class ReadinessDependencies(ApiModel):
    postgres: ReadinessDependency
    neo4j: ReadinessDependency
    n8n: ReadinessDependency


class ReadinessResponse(ApiModel):
    status: Literal["ready", "not_ready"]
    dependencies: ReadinessDependencies

    @model_validator(mode="after")
    def validate_status(self) -> "ReadinessResponse":
        dependency_states = {
            self.dependencies.postgres.status,
            self.dependencies.neo4j.status,
            self.dependencies.n8n.status,
        }
        expected = "ready" if dependency_states == {"up"} else "not_ready"
        if self.status != expected:
            raise ValueError("readiness 상태와 의존성 상태가 일치하지 않습니다")
        return self
