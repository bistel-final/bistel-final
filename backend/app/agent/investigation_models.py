"""V5-C-7.1 가설 v3의 draft/final 경계와 코드 소유 조사 상태."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.common.tool_contracts import (
    ChamberParameterHistoryToolResult,
    MetrologyResultToolResult,
)


class InvestigationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


OriginScope = Literal[
    "UPSTREAM", "DOWNSTREAM", "CURRENT_CHAMBER", "EQUIPMENT_COMMON", "UNDETERMINED"
]
ComparisonStatus = Literal["NOT_AVAILABLE", "NOT_CHECKED", "CHECKED"]


class ComparisonMatrix(InvestigationModel):
    upstream: ComparisonStatus = "NOT_AVAILABLE"
    downstream: ComparisonStatus = "NOT_AVAILABLE"
    sibling: ComparisonStatus = "NOT_AVAILABLE"
    history: ComparisonStatus = "NOT_AVAILABLE"
    metrology: ComparisonStatus = "NOT_AVAILABLE"


class OriginBasisRef(InvestigationModel):
    namespace: Literal["ALARM", "CHUNK", "RELATION", "LOT_HIST", "PARAMETER"]
    id: str = Field(min_length=1, max_length=160)


class OriginClaim(InvestigationModel):
    scope: OriginScope
    basis_refs: tuple[OriginBasisRef, ...] = Field(max_length=40)


class OriginAssessment(InvestigationModel):
    scope: OriginScope
    basis: tuple[OriginBasisRef, ...]
    compared: ComparisonMatrix


class ParameterFindingDraft(InvestigationModel):
    parameter_id: str = Field(min_length=1, max_length=20)
    lot_hist_ids: tuple[str, ...] = Field(min_length=1, max_length=100)


class ParameterFinding(InvestigationModel):
    parameter_id: str = Field(min_length=1, max_length=20)
    step_no: int = Field(ge=1)
    direction: Literal["ABOVE", "BELOW", "BOTH"]
    excursion_ratio: float = Field(gt=0, allow_inf_nan=False)
    wafer_scope: Literal["SINGLE", "PARTIAL", "ALL"]
    lot_hist_ids: tuple[str, ...]


class SuccessfulInvestigationCall(InvestigationModel):
    tool_name: str
    input: dict[str, Any]


class InvestigationEvidence(InvestigationModel):
    """予約 테이블 SUCCESS 행과 관측 DTO. LLM 출력에서 받지 않는다."""

    successful_calls: tuple[SuccessfulInvestigationCall, ...] = ()
    history: tuple[ChamberParameterHistoryToolResult, ...] = ()
    metrology: tuple[MetrologyResultToolResult, ...] = ()
