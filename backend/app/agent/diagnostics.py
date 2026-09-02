"""Final 고정 데이터용 incident 종합 진단 계약 (`V5-C-5.2-1`).

이 모듈은 Tool 결과를 새 관측값처럼 보정하지 않는다. 이미 조회된 WAFER summary와
PostgreSQL route, Neo4j/RAG 근거만 결정론적으로 요약하며 조치 결정에는 관여하지 않는다.
공개 화면에 쓰는 모델도 이 파일의 redacted 구조를 그대로 사용한다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.routing import ResolvedIncidentRoute
from app.common.enums import ActionCode, AlarmType, FaultHypothesis
from app.common.ids import NonEmptyId
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    ParameterSummaryItem,
)

ANALYSIS_VERSION = "agent-diagnosis-v2"
DATASET_EPOCH = "fdc_final_20260818"
POST_ACTION_STATUS = "NOT_AVAILABLE_STATIC_DATASET"
POST_ACTION_MESSAGE = (
    "최종 정적 데이터셋에는 조치 이후 공정 관측값이 없어 효과를 평가할 수 없음"
)
CANONICAL_INCIDENT_KEYS: frozenset[tuple[str, str]] = frozenset(
    {
        ("LOT002", "EQP05-PM2"),
        ("LOT004", "EQP01-PM2"),
        ("LOT004", "EQP04-PM2"),
        ("LOT005", "EQP02-PM1"),
        ("LOT006", "EQP06-PM1"),
        ("LOT006", "EQP06-PM2"),
        ("LOT007", "EQP01-PM1"),
        ("LOT007", "EQP04-PM1"),
        ("LOT009", "EQP03-PM1"),
        ("LOT009", "EQP06-PM1"),
        ("LOT010", "EQP01-PM1"),
        ("LOT011", "EQP05-PM1"),
    }
)


class DiagnosticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BoundaryDeviation(DiagnosticModel):
    level: Literal["OOS", "OOC"]
    direction: Literal["LOWER", "UPPER", "BOTH", "UNKNOWN"]
    magnitude: float | None = Field(default=None, ge=0.0)


class WaferParameterObservation(DiagnosticModel):
    lot_hist_id: NonEmptyId
    wafer_id: NonEmptyId
    wafer_no: int = Field(ge=1)
    step_id: NonEmptyId
    recipe_step_no: int = Field(ge=1)
    recipe_step_name: str | None
    parameter_id: NonEmptyId
    parameter_name: str = Field(min_length=1)
    alarm_type: AlarmType
    point_count: int = Field(ge=0)
    ooc_point_count: int = Field(ge=0)
    oos_point_count: int = Field(ge=0)
    value_mean: float | None
    value_min: float | None
    value_max: float | None
    deviation: BoundaryDeviation | None


class ParameterPattern(DiagnosticModel):
    parameter_id: NonEmptyId
    affected_wafer_count: int = Field(ge=0)
    ooc_point_count: int = Field(ge=0)
    oos_point_count: int = Field(ge=0)
    maximum_deviation: float | None = Field(default=None, ge=0.0)
    directions: tuple[Literal["LOWER", "UPPER", "BOTH", "UNKNOWN"], ...]


class StepPattern(DiagnosticModel):
    recipe_step_no: int = Field(ge=1)
    recipe_step_name: str | None
    abnormal_parameter_count: int = Field(ge=0)
    affected_wafer_count: int = Field(ge=0)


class DirectScope(DiagnosticModel):
    lot_ids: tuple[NonEmptyId, ...]
    wafer_ids: tuple[NonEmptyId, ...]
    chamber_ids: tuple[NonEmptyId, ...]
    parameter_ids: tuple[NonEmptyId, ...]
    model_codes: tuple[NonEmptyId, ...]


class DiagnosticSourceIds(DiagnosticModel):
    alarm_refs: tuple[NonEmptyId, ...]
    lot_hist_ids: tuple[NonEmptyId, ...]
    parameter_ids: tuple[NonEmptyId, ...]
    relation_ids: tuple[NonEmptyId, ...]
    graph_revisions: tuple[NonEmptyId, ...]


class IncidentDiagnosticSnapshot(DiagnosticModel):
    analysis_version: Literal["agent-diagnosis-v2"] = ANALYSIS_VERSION
    dataset_epoch: Literal["fdc_final_20260818"] = DATASET_EPOCH
    lot_id: NonEmptyId
    chamber_id: NonEmptyId
    representative_alarm_ref: NonEmptyId
    member_alarm_count: int = Field(ge=1)
    target_wafer_count: int = Field(ge=1, le=3)
    observed_wafer_count: int = Field(ge=0, le=3)
    wafer_observations: tuple[WaferParameterObservation, ...]
    parameter_patterns: tuple[ParameterPattern, ...]
    step_patterns: tuple[StepPattern, ...]
    direct_scope: DirectScope
    data_gaps: tuple[str, ...]
    source_ids: DiagnosticSourceIds

    @model_validator(mode="after")
    def validate_coverage(self) -> IncidentDiagnosticSnapshot:
        if self.observed_wafer_count > self.target_wafer_count:
            raise ValueError("관측 WAFER 수가 진단 target보다 클 수 없습니다")
        if len(self.data_gaps) != len(set(self.data_gaps)):
            raise ValueError("data_gaps는 중복될 수 없습니다")
        return self


class AlternativeHypothesis(DiagnosticModel):
    summary: str = Field(min_length=1, max_length=1000)
    lower_rank_reason: str = Field(min_length=1, max_length=1000)


class DiagnosisBlock(DiagnosticModel):
    status: Literal["AVAILABLE", "EMPTY"]
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    predicted_fault_code: FaultHypothesis | None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    observations: tuple[str, ...]
    evidence_synthesis: str | None = Field(default=None, max_length=2000)
    cause_summary: str | None = Field(default=None, max_length=2000)
    alternative_hypotheses: tuple[AlternativeHypothesis, ...]
    verification_steps: tuple[str, ...]
    limitations: tuple[str, ...]
    diagnostic_coverage: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> DiagnosisBlock:
        if self.status == "EMPTY":
            if self.reason_code is None:
                raise ValueError("EMPTY diagnosis에는 reason_code가 필요합니다")
            populated = (
                self.predicted_fault_code,
                self.confidence,
                self.evidence_synthesis,
                self.cause_summary,
                self.diagnostic_coverage,
            )
            if any(item is not None for item in populated):
                raise ValueError("EMPTY diagnosis에는 판단 값을 넣을 수 없습니다")
            if any(
                (
                    self.observations,
                    self.alternative_hypotheses,
                    self.verification_steps,
                )
            ):
                raise ValueError("EMPTY diagnosis에는 판단 목록을 넣을 수 없습니다")
        elif (
            self.reason_code is not None
            or self.predicted_fault_code is None
            or self.confidence is None
            or self.cause_summary is None
        ):
            raise ValueError("AVAILABLE diagnosis의 필수 값이 없습니다")
        return self


class EvidenceAssessmentBlock(DiagnosticModel):
    status: Literal["SUFFICIENT", "PARTIAL", "CONFLICT", "EMPTY"]
    reason_codes: tuple[str, ...]
    available_sources: tuple[Literal["FDC", "POSTGRES_ROUTE", "GRAPH", "RAG"], ...]
    missing_sources: tuple[Literal["FDC", "POSTGRES_ROUTE", "GRAPH", "RAG"], ...]
    conflicting_source_ids: tuple[str, ...]


class ImpactScopeItem(DiagnosticModel):
    kind: Literal[
        "LOT",
        "WAFER",
        "CHAMBER",
        "PARAMETER",
        "PROCESS_STEP",
        "SIBLING_CHAMBER",
    ]
    source_id: NonEmptyId
    relation: str | None = None


class ImpactScopeBlock(DiagnosticModel):
    status: Literal["AVAILABLE", "EMPTY"]
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    direct: tuple[ImpactScopeItem, ...]
    check_required: tuple[ImpactScopeItem, ...]
    summary: str | None = Field(default=None, max_length=2000)
    graph_conflict: bool = False


class SimilarIncidentItem(DiagnosticModel):
    agent_run_id: NonEmptyId
    lot_id: NonEmptyId
    chamber_id: NonEmptyId
    score: int = Field(ge=0, le=100)
    parameter_jaccard: float = Field(ge=0.0, le=1.0)
    predicted_fault_code: FaultHypothesis
    recommended_action: ActionCode


class SimilarIncidentsBlock(DiagnosticModel):
    status: Literal["AVAILABLE", "EMPTY"]
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    label: Literal["고정 시연 데이터 내 비교 결과"] = "고정 시연 데이터 내 비교 결과"
    items: tuple[SimilarIncidentItem, ...]

    @model_validator(mode="after")
    def validate_status(self) -> SimilarIncidentsBlock:
        if self.status == "EMPTY":
            if self.reason_code != "NOT_ENOUGH_RUNTIME_HISTORY" or self.items:
                raise ValueError("EMPTY 유사 이력 계약이 올바르지 않습니다")
        elif self.reason_code is not None or not self.items:
            raise ValueError("AVAILABLE 유사 이력에는 item이 필요합니다")
        return self


class PostActionObservationBlock(DiagnosticModel):
    status: Literal["NOT_AVAILABLE_STATIC_DATASET"] = POST_ACTION_STATUS
    message: Literal[
        "최종 정적 데이터셋에는 조치 이후 공정 관측값이 없어 효과를 평가할 수 없음"
    ] = POST_ACTION_MESSAGE


def _deviation(parameter: ParameterSummaryItem) -> BoundaryDeviation | None:
    if parameter.oos_point_cnt > 0:
        level = "OOS"
        lower, upper = parameter.spec_lower, parameter.spec_upper
    elif parameter.ooc_point_cnt > 0:
        level = "OOC"
        lower, upper = parameter.ctrl_lower, parameter.ctrl_upper
    else:
        return None

    lower_delta = (
        None
        if lower is None or parameter.value_min is None
        else max(0.0, float(lower) - float(parameter.value_min))
    )
    upper_delta = (
        None
        if upper is None or parameter.value_max is None
        else max(0.0, float(parameter.value_max) - float(upper))
    )
    if lower_delta and upper_delta:
        direction = "BOTH"
    elif lower_delta:
        direction = "LOWER"
    elif upper_delta:
        direction = "UPPER"
    else:
        direction = "UNKNOWN"
    values = [value for value in (lower_delta, upper_delta) if value is not None]
    return BoundaryDeviation(
        level=level,
        direction=direction,
        magnitude=None if not values else max(values),
    )


def _successes(
    evidence: Sequence[FdcSummaryToolResult | None],
) -> tuple[FdcSummaryToolResult, ...]:
    return tuple(
        item
        for item in evidence
        if item is not None and item.ok and item.wafer is not None
    )


def build_diagnostic_snapshot(
    evidence: Sequence[FdcSummaryToolResult | None],
    route: ResolvedIncidentRoute,
    *,
    target_count: int | None = None,
    extra_data_gaps: Iterable[str] = (),
) -> IncidentDiagnosticSnapshot:
    """FDC summary 묶음을 exact 정렬의 불변 진단 snapshot으로 바꾼다."""

    successful = sorted(
        _successes(evidence),
        key=lambda item: (
            item.wafer.wafer_no,  # type: ignore[union-attr]
            item.wafer.lot_hist_id,  # type: ignore[union-attr]
        ),
    )
    observations: list[WaferParameterObservation] = []
    wafer_id_by_lot_hist_id = {
        step.lot_hist_id: wafer_route.wafer_id
        for wafer_route in route.wafer_routes
        for step in wafer_route.steps
    }
    parameter_buckets: dict[str, list[WaferParameterObservation]] = defaultdict(list)
    step_buckets: dict[tuple[int, str | None], list[WaferParameterObservation]] = (
        defaultdict(list)
    )
    for result in successful:
        wafer = result.wafer
        assert wafer is not None
        try:
            wafer_id = wafer_id_by_lot_hist_id[wafer.lot_hist_id]
        except KeyError as exc:
            # FDC Tool DTO에는 wafer_id가 없다. lot/번호로 식별자를 합성하면 실제
            # PostgreSQL route와 다른 ID가 진단·영향 범위에 들어갈 수 있으므로 닫는다.
            raise ValueError("DIAGNOSTIC_WAFER_ROUTE_MISMATCH") from exc
        for parameter in sorted(
            result.parameters,
            key=lambda item: (item.recipe_step_no, item.parameter_id),
        ):
            observation = WaferParameterObservation(
                lot_hist_id=wafer.lot_hist_id,
                wafer_id=wafer_id,
                wafer_no=wafer.wafer_no,
                step_id=wafer.step_id,
                recipe_step_no=parameter.recipe_step_no,
                recipe_step_name=parameter.recipe_step_name,
                parameter_id=parameter.parameter_id,
                parameter_name=parameter.parameter_name,
                alarm_type=parameter.alarm_type,
                point_count=parameter.point_cnt,
                ooc_point_count=parameter.ooc_point_cnt,
                oos_point_count=parameter.oos_point_cnt,
                value_mean=parameter.value_mean,
                value_min=parameter.value_min,
                value_max=parameter.value_max,
                deviation=_deviation(parameter),
            )
            observations.append(observation)
            parameter_buckets[observation.parameter_id].append(observation)
            step_buckets[
                (observation.recipe_step_no, observation.recipe_step_name)
            ].append(observation)

    parameter_patterns = tuple(
        ParameterPattern(
            parameter_id=parameter_id,
            affected_wafer_count=len(
                {
                    item.wafer_id
                    for item in items
                    if item.ooc_point_count > 0 or item.oos_point_count > 0
                }
            ),
            ooc_point_count=sum(item.ooc_point_count for item in items),
            oos_point_count=sum(item.oos_point_count for item in items),
            maximum_deviation=(
                max(
                    (
                        item.deviation.magnitude
                        for item in items
                        if item.deviation is not None
                        and item.deviation.magnitude is not None
                    ),
                    default=None,
                )
            ),
            directions=tuple(
                sorted(
                    {
                        item.deviation.direction
                        for item in items
                        if item.deviation is not None
                    }
                )
            ),
        )
        for parameter_id, items in sorted(parameter_buckets.items())
    )
    step_patterns = tuple(
        StepPattern(
            recipe_step_no=key[0],
            recipe_step_name=key[1],
            abnormal_parameter_count=len(
                {
                    item.parameter_id
                    for item in items
                    if item.ooc_point_count > 0 or item.oos_point_count > 0
                }
            ),
            affected_wafer_count=len(
                {
                    item.wafer_id
                    for item in items
                    if item.ooc_point_count > 0 or item.oos_point_count > 0
                }
            ),
        )
        for key, items in sorted(step_buckets.items())
    )
    gaps = list(extra_data_gaps)
    for item in evidence:
        if item is None:
            gaps.append("FDC_TOOL_UNAVAILABLE")
        elif not item.ok:
            reason_code = item.reason.partition(":")[0] or "FDC_TOOL_FAILED"
            gaps.append(reason_code)
            if reason_code in {"TIMEOUT", "DEPENDENCY_ERROR"}:
                # graph는 run 전체 공유 retry 1회를 먼저 소비한다. retry 이후에도
                # 일시 오류가 최종 결과로 남았다는 것은 추가 read 예산이 없다는 뜻이다.
                gaps.append("RETRY_BUDGET_EXHAUSTED")
    expected = target_count if target_count is not None else max(1, len(evidence))
    if len(successful) < expected:
        gaps.append("FDC_COVERAGE_PARTIAL")

    relation_ids = tuple(
        dict.fromkeys(
            relation_id
            for item in route.graph_evidence
            for relation_id in item.relation_ids
        )
    )
    graph_revisions = tuple(
        sorted(
            {
                item.graph_revision
                for item in route.graph_evidence
                if item.graph_revision is not None
            }
        )
    )
    wafer_ids = tuple(
        dict.fromkeys(observation.wafer_id for observation in observations)
    )
    parameter_ids = tuple(sorted(parameter_buckets))
    return IncidentDiagnosticSnapshot(
        lot_id=route.incident.lot_id,
        chamber_id=route.incident.chamber_id,
        representative_alarm_ref=route.incident.representative_alarm.to_token(),
        member_alarm_count=len(route.incident.member_alarms),
        target_wafer_count=expected,
        observed_wafer_count=len(successful),
        wafer_observations=tuple(observations),
        parameter_patterns=parameter_patterns,
        step_patterns=step_patterns,
        direct_scope=DirectScope(
            lot_ids=(route.incident.lot_id,),
            wafer_ids=wafer_ids,
            chamber_ids=(route.incident.chamber_id,),
            parameter_ids=parameter_ids,
            model_codes=tuple(
                sorted(
                    {
                        item.model_code
                        for item in route.graph_evidence
                        if item.model_code is not None
                    }
                )
            ),
        ),
        data_gaps=tuple(dict.fromkeys(gaps)),
        source_ids=DiagnosticSourceIds(
            alarm_refs=tuple(
                alarm.to_token() for alarm in route.incident.member_alarms
            ),
            lot_hist_ids=tuple(
                result.wafer.lot_hist_id  # type: ignore[union-attr]
                for result in successful
            ),
            parameter_ids=parameter_ids,
            relation_ids=relation_ids,
            graph_revisions=graph_revisions,
        ),
    )


def assess_evidence(
    snapshot: IncidentDiagnosticSnapshot,
    route: ResolvedIncidentRoute,
    graph: EquipmentContextToolResult | None,
    documents: DocumentSearchToolResult | None,
) -> EvidenceAssessmentBlock:
    available: list[str] = ["POSTGRES_ROUTE"]
    missing: list[str] = []
    reasons = list(snapshot.data_gaps)
    if snapshot.observed_wafer_count:
        available.append("FDC")
    else:
        missing.append("FDC")
        reasons.append("FDC_EVIDENCE_MISSING")
    if graph is not None and graph.ok:
        available.append("GRAPH")
    else:
        missing.append("GRAPH")
        reasons.append("GRAPH_EVIDENCE_MISSING")
        graph_reason = "" if graph is None else graph.reason.partition(":")[0]
        if graph_reason:
            reasons.append(f"GRAPH_{graph_reason}")
        if graph_reason in {"TIMEOUT", "DEPENDENCY_ERROR"}:
            reasons.append("RETRY_BUDGET_EXHAUSTED")
    if documents is not None and documents.ok and documents.hits:
        available.append("RAG")
    else:
        missing.append("RAG")
        reasons.append("RAG_EVIDENCE_MISSING")
        document_reason = (
            "" if documents is None else documents.reason.partition(":")[0]
        )
        if document_reason:
            reasons.append(f"RAG_{document_reason}")
        if document_reason in {"TIMEOUT", "DEPENDENCY_ERROR"}:
            reasons.append("RETRY_BUDGET_EXHAUSTED")
    conflicts = tuple(
        dict.fromkeys(
            source_id
            for mismatch in route.mismatches
            for source_id in (
                *mismatch.postgres_ids,
                *mismatch.graph_ids,
                *mismatch.relation_ids,
            )
            if source_id
        )
    )
    if not route.route_consistency:
        reasons.append("GRAPH_ROUTE_CONFLICT")
        status = "CONFLICT"
    elif missing or snapshot.data_gaps:
        status = "PARTIAL"
    else:
        status = "SUFFICIENT"
    return EvidenceAssessmentBlock(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons)),
        available_sources=tuple(available),
        missing_sources=tuple(missing),
        conflicting_source_ids=conflicts,
    )


def build_impact_scope(
    snapshot: IncidentDiagnosticSnapshot,
    route: ResolvedIncidentRoute,
    graph: EquipmentContextToolResult | None,
    *,
    summary: str | None = None,
) -> ImpactScopeBlock:
    direct = [
        *(
            ImpactScopeItem(kind="LOT", source_id=value)
            for value in snapshot.direct_scope.lot_ids
        ),
        *(
            ImpactScopeItem(kind="WAFER", source_id=value)
            for value in snapshot.direct_scope.wafer_ids
        ),
        *(
            ImpactScopeItem(kind="CHAMBER", source_id=value)
            for value in snapshot.direct_scope.chamber_ids
        ),
        *(
            ImpactScopeItem(kind="PARAMETER", source_id=value)
            for value in snapshot.direct_scope.parameter_ids
        ),
    ]
    check_required: list[ImpactScopeItem] = []
    for item in route.graph_evidence:
        check_required.extend(
            ImpactScopeItem(kind="PROCESS_STEP", source_id=value, relation="UPSTREAM")
            for value in item.upstream_process_step_ids
        )
        check_required.extend(
            ImpactScopeItem(kind="PROCESS_STEP", source_id=value, relation="DOWNSTREAM")
            for value in item.downstream_process_step_ids
        )
    if graph is not None and graph.ok:
        check_required.extend(
            ImpactScopeItem(kind="SIBLING_CHAMBER", source_id=value)
            for value in graph.sibling_chamber_ids
        )
        check_required.extend(
            ImpactScopeItem(kind="PARAMETER", source_id=value, relation="RELATED")
            for value in graph.parameter_ids
        )
    unique = {
        (item.kind, item.source_id, item.relation): item for item in check_required
    }
    return ImpactScopeBlock(
        status="AVAILABLE" if direct else "EMPTY",
        reason_code=None if direct else "DIRECT_SCOPE_MISSING",
        direct=tuple(direct),
        check_required=tuple(unique[key] for key in sorted(unique)),
        summary=summary,
        graph_conflict=not route.route_consistency,
    )


def abnormal_parameter_ids(snapshot: IncidentDiagnosticSnapshot) -> frozenset[str]:
    return frozenset(
        item.parameter_id
        for item in snapshot.parameter_patterns
        if item.ooc_point_count > 0 or item.oos_point_count > 0
    )


def parameter_jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 0.0
    return round(len(left & right) / len(left | right), 2)


def similar_incident_score(
    *,
    same_model: bool,
    parameter_similarity: float,
    same_fault: bool,
    same_action: bool,
) -> int:
    return (
        (30 if same_model else 0)
        + round(parameter_similarity * 30)
        + (25 if same_fault else 0)
        + (15 if same_action else 0)
    )


__all__ = [
    "ANALYSIS_VERSION",
    "CANONICAL_INCIDENT_KEYS",
    "DATASET_EPOCH",
    "POST_ACTION_MESSAGE",
    "POST_ACTION_STATUS",
    "AlternativeHypothesis",
    "DiagnosisBlock",
    "EvidenceAssessmentBlock",
    "ImpactScopeBlock",
    "IncidentDiagnosticSnapshot",
    "PostActionObservationBlock",
    "SimilarIncidentItem",
    "SimilarIncidentsBlock",
    "abnormal_parameter_ids",
    "assess_evidence",
    "build_diagnostic_snapshot",
    "build_impact_scope",
    "parameter_jaccard",
    "similar_incident_score",
]
