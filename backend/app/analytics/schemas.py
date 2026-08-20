from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from app.common.audit import AuditEvent
from app.common.enums import ActorType
from app.common.schemas import ApiModel, PageResponse
from app.common.tool_contracts import MetricPlan, VisualizationPlan


class AnalysisQueryRequest(ApiModel):
    question: str = Field(min_length=1, max_length=1000)


class GroupedMetricResult(ApiModel):
    group: dict[str, Any]
    value: int | float | None = None


class AnalysisQueryResponse(ApiModel):
    """자연어 질의의 성공·정책 거부를 함께 표현하는 HTTP 200 계약."""

    question: str
    generated_sql: str | None = None
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int = Field(ge=0)
    metric: MetricPlan | None = None
    metric_result: int | float | list[GroupedMetricResult] | None = None
    group_by: list[str]
    visualization: VisualizationPlan | None = None
    is_valid: bool
    is_rejected: bool
    reject_reason: str | None = None
    error_msg: str | None = None
    latency_ms: int = Field(ge=0)
    nl_query_log_id: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_outcome(self) -> "AnalysisQueryResponse":
        if self.row_count != len(self.rows):
            raise ValueError("row_count는 rows 길이와 일치해야 합니다")

        if self.is_rejected:
            if self.is_valid:
                raise ValueError("정책 거부 응답은 is_valid=false여야 합니다")
            if not self.reject_reason:
                raise ValueError("정책 거부 응답에는 reject_reason이 필요합니다")
            if any((self.columns, self.rows, self.group_by)) or self.row_count != 0:
                raise ValueError(
                    "정책 거부 응답의 결과 배열과 row_count는 비어야 합니다"
                )
            if any((self.generated_sql, self.metric, self.visualization)):
                raise ValueError(
                    "정책 거부 응답의 generated_sql·metric·visualization은 "
                    "null이어야 합니다"
                )
            return self

        if self.reject_reason is not None:
            raise ValueError("거부되지 않은 응답에는 reject_reason을 넣을 수 없습니다")
        if self.is_valid and (
            self.generated_sql is None
            or self.metric is None
            or self.visualization is None
        ):
            raise ValueError(
                "유효한 응답에는 generated_sql·metric·visualization이 필요합니다"
            )
        return self


class NlQueryOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    POLICY_REJECTED = "POLICY_REJECTED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    DB_ERROR = "DB_ERROR"


class NlQueryLogItem(ApiModel):
    nl_query_log_id: int = Field(ge=1)
    asked_at: datetime
    question: str
    generated_sql: str | None = None
    outcome: NlQueryOutcome
    is_valid: bool
    is_rejected: bool
    reject_reason: str | None = None
    row_cnt: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    error_msg: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "NlQueryLogItem":
        reject_reason_is_null = self.reject_reason is None
        error_is_null = self.error_msg is None
        has_reject_reason = bool(self.reject_reason)
        has_error = bool(self.error_msg)
        rules = {
            NlQueryOutcome.SUCCESS: (
                self.is_valid
                and not self.is_rejected
                and reject_reason_is_null
                and error_is_null
                and self.row_cnt is not None
            ),
            NlQueryOutcome.POLICY_REJECTED: (
                not self.is_valid
                and self.is_rejected
                and has_reject_reason
                and error_is_null
                and self.row_cnt is None
            ),
            NlQueryOutcome.VALIDATION_FAILED: (
                not self.is_valid
                and not self.is_rejected
                and reject_reason_is_null
                and has_error
                and self.row_cnt is None
            ),
            NlQueryOutcome.DB_ERROR: (
                self.is_valid
                and not self.is_rejected
                and reject_reason_is_null
                and has_error
                and self.row_cnt is None
            ),
        }
        if not rules[self.outcome]:
            raise ValueError("Text2SQL outcome과 결과 필드가 일치하지 않습니다")
        return self


class NlQueryHistoryResponse(PageResponse[NlQueryLogItem]):
    pass


class SqlValidateRequest(ApiModel):
    sql: str = Field(min_length=1, max_length=20000)


class ValidationCheck(ApiModel):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    ok: bool


class SqlValidateResponse(ApiModel):
    valid: bool
    normalized_sql: str | None = None
    reason: str
    checks: list[ValidationCheck] | None = None


class EvaluationItem(ApiModel):
    case_type: Literal["GOLD", "DEFENSE"]
    case_id: str = Field(min_length=1)
    question: str | None = None
    passed: bool
    generated_sql: str | None = None
    attempt_count: int = Field(ge=0)
    expected_result: Any | None = None
    actual_result: Any | None = None
    expected_visualization: VisualizationPlan | None = None
    actual_visualization: VisualizationPlan | None = None
    reason: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)


class EvaluationResponse(ApiModel):
    run_id: str = Field(min_length=1)
    executed_at: datetime
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float
    prompt_version: str = Field(min_length=1)
    correct: int = Field(ge=0)
    total: int = Field(ge=0)
    accuracy: float = Field(ge=0.0, le=1.0)
    defense_passed: int = Field(ge=0)
    defense_total: int = Field(ge=0)
    items: list[EvaluationItem]

    @model_validator(mode="after")
    def validate_counts(self) -> "EvaluationResponse":
        if self.correct > self.total:
            raise ValueError("correct는 total보다 클 수 없습니다")
        if self.defense_passed > self.defense_total:
            raise ValueError("defense_passed는 defense_total보다 클 수 없습니다")
        return self


class EvaluationListResponse(PageResponse[EvaluationResponse]):
    pass


class AuditLogItem(ApiModel):
    audit_id: int = Field(ge=1)
    occurred_at: datetime
    actor_type: ActorType
    actor_id: str | None = None
    event_type: AuditEvent
    entity_type: str | None = None
    entity_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    detail: str | None = None


class AuditLogResponse(ApiModel):
    items: list[AuditLogItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=100)
    event_types: list[AuditEvent]
    event_type_counts: dict[AuditEvent, int]

    @model_validator(mode="after")
    def validate_event_counts(self) -> "AuditLogResponse":
        if any(count < 0 for count in self.event_type_counts.values()):
            raise ValueError("감사 이벤트 수는 음수일 수 없습니다")
        return self
