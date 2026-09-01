"""Agent 평가 artifact의 aggregate-only 공개 DTO."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.agent.golden_flow import GoldenPhase, PhaseStatus
from app.common.schemas import ApiModel


class EvaluationCountMetric(ApiModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float = Field(ge=0.0, le=1.0)


class EvaluationClassMetric(ApiModel):
    support: int = Field(ge=0)
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)


class AgentFaultClassification(ApiModel):
    population_count: int = Field(ge=0)
    accuracy: EvaluationCountMetric
    unclassified_count: int = Field(ge=0)
    macro_f1_5class: float = Field(ge=0.0, le=1.0)
    observed_class_macro_f1: float = Field(ge=0.0, le=1.0)
    by_class: dict[str, EvaluationClassMetric]


class AgentEvaluationVersions(ApiModel):
    dataset_epoch: str = Field(min_length=1)
    model_version: str | None
    prompt_version: str | None
    policy_version: str | None


class AgentEvaluationExclusion(ApiModel):
    reason: Literal["NO_INJECTED_FAULT", "AMBIGUOUS_LABEL"]
    count: int = Field(ge=0)
    meaning: str = Field(min_length=1)


class AgentFaultEvaluation(ApiModel):
    versions: AgentEvaluationVersions
    structured_prediction: EvaluationCountMetric
    evidence_valid_run: EvaluationCountMetric
    rule_action_agreement: EvaluationCountMetric
    classification: AgentFaultClassification
    exclusions: list[AgentEvaluationExclusion]
    metrology_observed_count: int = Field(ge=0)
    metrology_total_lot_hist_count: int = Field(ge=0)
    hard_gate_passed: bool
    hard_gate_reasons: list[str]
    public_fault_ground_truth_available: Literal[True]
    production_ground_truth_available: Literal[False]
    label_source: Literal["SYNTHETIC_GENERATOR"]
    usage_scope: Literal["EVALUATION_ONLY"]
    production_performance_disclaimer: str = Field(min_length=1)


class AgentGoldenPhase(ApiModel):
    phase: GoldenPhase
    status: PhaseStatus
    reasons: list[str]
    metrics: dict[str, Any]


class AgentGoldenEvaluation(ApiModel):
    dataset_epoch: str = Field(min_length=1)
    status: PhaseStatus
    phases: list[AgentGoldenPhase]

    @model_validator(mode="after")
    def validate_phase_order_and_status(self) -> AgentGoldenEvaluation:
        if [item.phase for item in self.phases] != list(GoldenPhase):
            raise ValueError("golden phase는 정확한 7단 순서여야 합니다")
        derived = (
            PhaseStatus.FAIL
            if any(item.status is PhaseStatus.FAIL for item in self.phases)
            else (
                PhaseStatus.EVIDENCE_INCOMPLETE
                if any(
                    item.status is PhaseStatus.EVIDENCE_INCOMPLETE
                    for item in self.phases
                )
                else PhaseStatus.PASS
            )
        )
        if self.status is not derived:
            raise ValueError("golden 전체 상태가 phase 상태와 다릅니다")
        return self


EvaluationEmptyReason = Literal[
    "NOT_CONFIGURED",
    "NOT_FOUND",
    "CONTRACT_VIOLATION",
]


class AgentEvaluationResponse(ApiModel):
    fault_5class: AgentFaultEvaluation | None
    golden_flow: AgentGoldenEvaluation | None
    fault_5class_empty_reason: EvaluationEmptyReason | None
    golden_flow_empty_reason: EvaluationEmptyReason | None

    @model_validator(mode="after")
    def validate_independent_empty_states(self) -> AgentEvaluationResponse:
        pairs = (
            (self.fault_5class, self.fault_5class_empty_reason),
            (self.golden_flow, self.golden_flow_empty_reason),
        )
        if any((value is None) == (reason is None) for value, reason in pairs):
            raise ValueError("평가 값과 Empty 사유는 정확히 하나만 있어야 합니다")
        return self


__all__ = [
    "AgentEvaluationResponse",
    "AgentFaultEvaluation",
    "AgentGoldenEvaluation",
]
