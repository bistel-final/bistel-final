"""상실된 HITL checkpoint를 DB 정본으로 재수화하는 계약 (`V5-C-3.4`).

``rehydration_snapshot``은 ``persist_action`` 직전 State의 비파생 값을 보존한다.
복구 시 snapshot만 믿지 않고 run·prediction·action bundle·Tool 호출 수와 다시 결속한 뒤
canonical 20 channel과 내부 5 channel을 모두 검증한다. 외부 Tool·LLM·새 run/action
생성은 이 모듈의 입력에도 없다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import ValidationError, model_validator
from sqlalchemy.engine import Connection

from app.agent.incident import ResolvedIncident
from app.agent.repository import (
    ACTION_PROVENANCE_KEY,
    ACTION_PROVENANCE_SCHEMA,
    REHYDRATION_SNAPSHOT_KEY,
    AgentRunRow,
    count_tool_calls,
    find_run_action,
    get_action_bundle,
    get_agent_run,
    get_prediction_or_none,
    list_run_alarms,
)
from app.agent.routing import (
    GraphRouteEvidence,
    ResolvedIncidentRoute,
    RouteMismatch,
    WaferRoute,
)
from app.agent.routing_repository import RouteStep
from app.agent.state import (
    ActionDecision,
    AgentError,
    AgentGraphState,
    CompletedAgentState,
    DeliveryPlan,
    Hypothesis,
    StateModel,
    ToolBudget,
)
from app.common.enums import ActionCode, RunStatus, Severity
from app.common.ids import NonEmptyId
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    AnomalySignal,
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
)

REHYDRATION_SNAPSHOT_SCHEMA: Final = "rehydration-snapshot-v1"
REHYDRATION_AUDIT_KEY: Final = "checkpoint_rehydration"


class RehydrationError(RuntimeError):
    """원문·DB 정보를 노출하지 않는 재수화 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class IncidentSnapshotModel(StateModel):
    lot_id: NonEmptyId
    chamber_id: NonEmptyId
    requested_alarm: AlarmRef
    representative_alarm: AlarmRef
    member_alarms: tuple[AlarmRef, ...]


class RouteStepSnapshotModel(StateModel):
    lot_hist_id: NonEmptyId
    lot_id: NonEmptyId
    wafer_id: NonEmptyId
    wafer_no: int | None
    step_id: NonEmptyId
    area_id: NonEmptyId | None
    equipment_id: NonEmptyId
    chamber_id: NonEmptyId
    recipe_id: NonEmptyId | None
    track_in_at: datetime
    track_out_at: datetime | None


class WaferRouteSnapshotModel(StateModel):
    wafer_id: NonEmptyId
    member_alarms: tuple[AlarmRef, ...]
    steps: tuple[RouteStepSnapshotModel, ...]


class GraphRouteEvidenceSnapshotModel(StateModel):
    chamber_id: NonEmptyId
    equipment_id: NonEmptyId | None
    model_code: NonEmptyId | None
    process_step_id: NonEmptyId | None
    upstream_process_step_ids: tuple[NonEmptyId, ...]
    downstream_process_step_ids: tuple[NonEmptyId, ...]
    relation_ids: tuple[NonEmptyId, ...]
    graph_revision: NonEmptyId | None


class RouteMismatchSnapshotModel(StateModel):
    code: NonEmptyId
    wafer_id: str
    from_lot_hist_id: str
    to_lot_hist_id: str | None
    postgres_ids: tuple[str, ...]
    graph_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]


class RouteSnapshotModel(StateModel):
    incident: IncidentSnapshotModel
    wafer_routes: tuple[WaferRouteSnapshotModel, ...]
    graph_evidence: tuple[GraphRouteEvidenceSnapshotModel, ...]
    route_consistency: bool
    mismatches: tuple[RouteMismatchSnapshotModel, ...]


def route_to_snapshot(route: ResolvedIncidentRoute) -> RouteSnapshotModel:
    """dataclass route를 JSONB에 안전한 전용 DTO로 바꾼다."""

    incident = route.incident
    return RouteSnapshotModel(
        incident=IncidentSnapshotModel(
            lot_id=incident.lot_id,
            chamber_id=incident.chamber_id,
            requested_alarm=incident.requested_alarm,
            representative_alarm=incident.representative_alarm,
            member_alarms=tuple(incident.member_alarms),
        ),
        wafer_routes=tuple(
            WaferRouteSnapshotModel(
                wafer_id=wafer.wafer_id,
                member_alarms=tuple(wafer.member_alarms),
                steps=tuple(
                    RouteStepSnapshotModel.model_validate(step, from_attributes=True)
                    for step in wafer.steps
                ),
            )
            for wafer in route.wafer_routes
        ),
        graph_evidence=tuple(
            GraphRouteEvidenceSnapshotModel.model_validate(item, from_attributes=True)
            for item in route.graph_evidence
        ),
        route_consistency=route.route_consistency,
        mismatches=tuple(
            RouteMismatchSnapshotModel.model_validate(item, from_attributes=True)
            for item in route.mismatches
        ),
    )


def snapshot_to_route(snapshot: RouteSnapshotModel) -> ResolvedIncidentRoute:
    """JSONB round-trip DTO를 canonical dataclass hierarchy로 복원한다."""

    value = RouteSnapshotModel.model_validate(snapshot)
    incident = value.incident
    return ResolvedIncidentRoute(
        incident=ResolvedIncident(
            lot_id=incident.lot_id,
            chamber_id=incident.chamber_id,
            requested_alarm=incident.requested_alarm,
            representative_alarm=incident.representative_alarm,
            member_alarms=tuple(incident.member_alarms),
        ),
        wafer_routes=tuple(
            WaferRoute(
                wafer_id=wafer.wafer_id,
                member_alarms=tuple(wafer.member_alarms),
                steps=tuple(
                    RouteStep(**step.model_dump(mode="python")) for step in wafer.steps
                ),
            )
            for wafer in value.wafer_routes
        ),
        graph_evidence=tuple(
            GraphRouteEvidence(**item.model_dump(mode="python"))
            for item in value.graph_evidence
        ),
        route_consistency=value.route_consistency,
        mismatches=tuple(
            RouteMismatch(**item.model_dump(mode="python")) for item in value.mismatches
        ),
    )


class RehydrationSeed(StateModel):
    """graph가 action UoW로 넘기는 persist 직전 비파생 State."""

    route: ResolvedIncidentRoute
    fdc_evidence: FdcSummaryToolResult | None
    optional_anomaly_evidence: AnomalySignal | None
    graph_evidence: EquipmentContextToolResult | None
    document_evidence: DocumentSearchToolResult | None
    errors: tuple[AgentError, ...]
    tool_budget: ToolBudget
    fdc_lot_hist_id: NonEmptyId

    @model_validator(mode="after")
    def _consistent(self) -> RehydrationSeed:
        anomaly = None if self.fdc_evidence is None else self.fdc_evidence.anomaly
        if self.optional_anomaly_evidence != anomaly:
            raise ValueError("anomaly evidence가 FDC 결과와 다릅니다")
        if any(item.terminal for item in self.errors):
            raise ValueError("재수화 seed에 terminal error를 넣을 수 없습니다")
        if any(item.node == "approval_email" for item in self.errors):
            raise ValueError("persist 직전 seed에는 approval email 오류가 없습니다")
        if (
            self.fdc_evidence is not None
            and self.fdc_evidence.ok
            and self.fdc_evidence.wafer is not None
            and self.fdc_evidence.wafer.lot_hist_id != self.fdc_lot_hist_id
        ):
            raise ValueError("FDC lot_hist_id가 seed와 다릅니다")
        return self

    @classmethod
    def from_state(cls, state: AgentGraphState) -> RehydrationSeed:
        return cls(
            route=state["route"],
            fdc_evidence=state.get("fdc_evidence"),
            optional_anomaly_evidence=state.get("optional_anomaly_evidence"),
            graph_evidence=state.get("graph_evidence"),
            document_evidence=state.get("document_evidence"),
            errors=tuple(state.get("errors", ())),
            tool_budget=state["tool_budget"],
            fdc_lot_hist_id=state["fdc_lot_hist_id"],
        )


class RehydrationSnapshot(StateModel):
    """action bundle과 같은 commit으로 보존되는 JSONB 계약."""

    schema_version: Literal["rehydration-snapshot-v1"] = REHYDRATION_SNAPSHOT_SCHEMA
    route: RouteSnapshotModel
    fdc_evidence: FdcSummaryToolResult | None
    optional_anomaly_evidence: AnomalySignal | None
    graph_evidence: EquipmentContextToolResult | None
    document_evidence: DocumentSearchToolResult | None
    errors: tuple[AgentError, ...]
    tool_budget: ToolBudget
    fdc_lot_hist_id: NonEmptyId
    action_id: NonEmptyId
    approval_id: NonEmptyId
    deliveries: tuple[DeliveryPlan, ...]

    @classmethod
    def from_seed(
        cls,
        seed: RehydrationSeed,
        *,
        action_id: str,
        approval_id: str | None,
        deliveries: tuple[DeliveryPlan, ...],
    ) -> RehydrationSnapshot:
        if approval_id is None:
            raise RehydrationError("REHYDRATE_BUNDLE_MISMATCH")
        resolved = RehydrationSeed.model_validate(seed)
        return cls(
            route=route_to_snapshot(resolved.route),
            fdc_evidence=resolved.fdc_evidence,
            optional_anomaly_evidence=resolved.optional_anomaly_evidence,
            graph_evidence=resolved.graph_evidence,
            document_evidence=resolved.document_evidence,
            errors=resolved.errors,
            tool_budget=resolved.tool_budget,
            fdc_lot_hist_id=resolved.fdc_lot_hist_id,
            action_id=action_id,
            approval_id=approval_id,
            deliveries=deliveries,
        )

    def as_evidence(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def load_snapshot(evidence: Mapping[str, Any] | None) -> RehydrationSnapshot:
    raw = None if evidence is None else evidence.get(REHYDRATION_SNAPSHOT_KEY)
    if not isinstance(raw, Mapping):
        raise RehydrationError("REHYDRATE_SNAPSHOT_MISSING")
    if raw.get("schema_version") != REHYDRATION_SNAPSHOT_SCHEMA:
        raise RehydrationError("REHYDRATE_SNAPSHOT_VERSION_UNSUPPORTED")
    try:
        return RehydrationSnapshot.model_validate(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise RehydrationError("REHYDRATE_SNAPSHOT_MISSING") from exc


def prediction_to_hypothesis(row: Any) -> Hypothesis:
    evidence = row.evidence
    if evidence.get("schema_version") != "agent-evidence-v1":
        raise RehydrationError("REHYDRATE_PREDICTION_MISMATCH")
    try:
        return Hypothesis(
            predicted_fault_code=row.predicted_fault_code,
            confidence=row.confidence,
            cause_summary=row.cause_summary,
            supporting_alarms=tuple(
                AlarmRef.model_validate(item)
                for item in evidence.get("supporting_alarms", ())
            ),
            supporting_chunk_ids=tuple(evidence.get("supporting_chunk_ids", ())),
            supporting_relation_ids=tuple(evidence.get("supporting_relation_ids", ())),
            uncertainty=evidence.get("uncertainty", ""),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise RehydrationError("REHYDRATE_PREDICTION_MISMATCH") from exc


def _action_decision(run: AgentRunRow, evidence: Mapping[str, Any]) -> ActionDecision:
    provenance = evidence.get(ACTION_PROVENANCE_KEY)
    if not isinstance(provenance, Mapping):
        raise RehydrationError("REHYDRATE_SNAPSHOT_MISSING")
    if provenance.get("schema") != ACTION_PROVENANCE_SCHEMA:
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH")
    policy = provenance.get("action_policy_version")
    if not isinstance(policy, str) or not policy:
        raise RehydrationError("REHYDRATE_SNAPSHOT_MISSING")
    if run.action is not ActionCode.EQP_HOLD or run.severity is not Severity.HIGH:
        raise RehydrationError("REHYDRATE_BUNDLE_MISMATCH")
    try:
        return ActionDecision(
            action=run.action,
            severity=run.severity,
            requires_approval=True,
            matched_rule="R03_PRESENT",
            policy_version=policy,
        )
    except (ValidationError, ValueError) as exc:
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH") from exc


def _alarm_tokens(values: tuple[AlarmRef, ...] | list[AlarmRef]) -> set[str]:
    return {item.to_token() for item in values}


def build_rehydrated_state(
    connection: Connection,
    run_id: str,
) -> dict[str, Any]:
    """한 DB snapshot에서 25-channel payload를 조립하고 전체 계약을 검증한다."""

    run = get_agent_run(connection, run_id)
    if run.status is not RunStatus.WAITING_APPROVAL:
        raise RehydrationError("REHYDRATE_RUN_NOT_WAITING")
    evidence: Mapping[str, Any] = run.evidence or {}
    snapshot = load_snapshot(evidence)
    route = snapshot_to_route(snapshot.route)
    incident = route.incident
    fdc_anomaly = (
        None if snapshot.fdc_evidence is None else snapshot.fdc_evidence.anomaly
    )
    if snapshot.optional_anomaly_evidence != fdc_anomaly:
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH")
    if (
        snapshot.fdc_evidence is not None
        and snapshot.fdc_evidence.ok
        and snapshot.fdc_evidence.wafer is not None
        and snapshot.fdc_evidence.wafer.lot_hist_id != snapshot.fdc_lot_hist_id
    ):
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH")
    stored_members = tuple(list_run_alarms(connection, run_id))
    if (
        incident.lot_id != run.lot_id
        or incident.chamber_id != run.chamber_id
        or incident.requested_alarm != run.requested_alarm
        or incident.representative_alarm != run.representative_alarm
        or _alarm_tokens(incident.member_alarms) != _alarm_tokens(stored_members)
    ):
        raise RehydrationError("REHYDRATE_SNAPSHOT_IDENTITY_MISMATCH")

    provenance = evidence.get(ACTION_PROVENANCE_KEY)
    if not isinstance(provenance, Mapping):
        raise RehydrationError("REHYDRATE_SNAPSHOT_MISSING")
    try:
        provenance_members = tuple(
            AlarmRef.model_validate(item)
            for item in provenance.get("member_alarms", ())
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH") from exc
    if _alarm_tokens(provenance_members) != _alarm_tokens(stored_members):
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH")

    prediction = get_prediction_or_none(connection, run_id)
    if prediction is None:
        raise RehydrationError("REHYDRATE_PREDICTION_MISMATCH")
    if (
        prediction.llm_model != run.llm_model
        or prediction.prompt_version != run.prompt_version
    ):
        raise RehydrationError("REHYDRATE_PREDICTION_MISMATCH")
    hypothesis = prediction_to_hypothesis(prediction)
    decision = _action_decision(run, evidence)

    link = find_run_action(connection, run_id)
    if link is None or link.action_id != snapshot.action_id:
        raise RehydrationError("REHYDRATE_BUNDLE_MISMATCH")
    bundle = get_action_bundle(connection, link.action_id)
    if (
        bundle.action_code is not ActionCode.EQP_HOLD
        or bundle.approval_id != snapshot.approval_id
        or bundle.approval_agent_run_id != run_id
        or tuple(bundle.delivery_channels)
        != tuple(item.channel for item in snapshot.deliveries)
    ):
        raise RehydrationError("REHYDRATE_BUNDLE_MISMATCH")
    if count_tool_calls(connection, run_id) != snapshot.tool_budget.used:
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH")

    payload: dict[str, Any] = {
        "run_id": run.agent_run_id,
        "thread_id": run.thread_id,
        "retry_of_run_id": run.retry_of_run_id,
        "requested_alarm": run.requested_alarm,
        "representative_alarm": run.representative_alarm,
        # agent_run_alarm에는 발생 시각이 없어 C-1.1 원래 순서를 재구성할 수 없다.
        # DB는 identity 집합을 검증하고 checkpoint 순서는 snapshot route가 보존한다.
        "member_alarms": tuple(incident.member_alarms),
        "lot_id": run.lot_id,
        "chamber_id": run.chamber_id,
        "route": route,
        "fdc_evidence": snapshot.fdc_evidence,
        "optional_anomaly_evidence": snapshot.optional_anomaly_evidence,
        "graph_evidence": snapshot.graph_evidence,
        "document_evidence": snapshot.document_evidence,
        "hypothesis": hypothesis,
        "action_decision": decision,
        "action_id": snapshot.action_id,
        "approval_id": snapshot.approval_id,
        "deliveries": snapshot.deliveries,
        "tool_budget": snapshot.tool_budget,
        "errors": snapshot.errors,
        "autonomy_level": run.autonomy_level,
        "terminal_error": None,
        "fdc_lot_hist_id": snapshot.fdc_lot_hist_id,
        "approval_decision": None,
        "pending_llm_usage": None,
    }
    validate_rehydrated_payload(payload)
    return payload


def validate_rehydrated_payload(values: Mapping[str, Any]) -> None:
    """canonical 20 + 내부 5를 재개 가능한 모양으로 검증한다."""

    canonical = {name: values.get(name) for name in CompletedAgentState.model_fields}
    try:
        CompletedAgentState.model_validate(canonical)
    except (ValidationError, TypeError, ValueError) as exc:
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH") from exc
    if values.get("autonomy_level") not in (1, 2):
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH")
    if values.get("terminal_error") is not None:
        raise RehydrationError("REHYDRATE_CHECKPOINT_UNVERIFIED")
    if (
        not isinstance(values.get("fdc_lot_hist_id"), str)
        or not str(values["fdc_lot_hist_id"]).strip()
    ):
        raise RehydrationError("REHYDRATE_PROVENANCE_MISMATCH")
    if values.get("approval_decision") is not None:
        raise RehydrationError("REHYDRATE_CHECKPOINT_UNVERIFIED")
    if values.get("pending_llm_usage") is not None:
        raise RehydrationError("REHYDRATE_CHECKPOINT_UNVERIFIED")


def canonical_payload(values: Mapping[str, Any]) -> str:
    """tuple/list·dataclass round-trip에 무관한 semantic comparison projection."""

    validate_rehydrated_payload(values)
    route = route_to_snapshot(values["route"])
    projection = {
        "run_id": values.get("run_id"),
        "thread_id": values.get("thread_id"),
        "retry_of_run_id": values.get("retry_of_run_id"),
        "requested_alarm": AlarmRef.model_validate(
            values.get("requested_alarm")
        ).model_dump(mode="json"),
        "representative_alarm": AlarmRef.model_validate(
            values.get("representative_alarm")
        ).model_dump(mode="json"),
        "member_alarms": [
            AlarmRef.model_validate(item).model_dump(mode="json")
            for item in values.get("member_alarms", ())
        ],
        "lot_id": values.get("lot_id"),
        "chamber_id": values.get("chamber_id"),
        "route": route.model_dump(mode="json"),
        "fdc_evidence": _dump(values.get("fdc_evidence"), FdcSummaryToolResult),
        "optional_anomaly_evidence": _dump(
            values.get("optional_anomaly_evidence"), AnomalySignal
        ),
        "graph_evidence": _dump(
            values.get("graph_evidence"), EquipmentContextToolResult
        ),
        "document_evidence": _dump(
            values.get("document_evidence"), DocumentSearchToolResult
        ),
        "hypothesis": _dump(values.get("hypothesis"), Hypothesis),
        "action_decision": _dump(values.get("action_decision"), ActionDecision),
        "action_id": values.get("action_id"),
        "approval_id": values.get("approval_id"),
        "deliveries": [
            DeliveryPlan.model_validate(item).model_dump(mode="json")
            for item in values.get("deliveries", ())
        ],
        "tool_budget": ToolBudget.model_validate(values.get("tool_budget")).model_dump(
            mode="json"
        ),
        "errors": [
            AgentError.model_validate(item).model_dump(mode="json")
            for item in values.get("errors", ())
        ],
        "autonomy_level": values.get("autonomy_level"),
        "terminal_error": None,
        "fdc_lot_hist_id": values.get("fdc_lot_hist_id"),
        "approval_decision": None,
        "pending_llm_usage": None,
    }
    return json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _dump(value: Any, model: type[StateModel] | type[Any]) -> Any:
    if value is None:
        return None
    return model.model_validate(value).model_dump(mode="json")


__all__ = [
    "REHYDRATION_AUDIT_KEY",
    "REHYDRATION_SNAPSHOT_KEY",
    "REHYDRATION_SNAPSHOT_SCHEMA",
    "RehydrationError",
    "RehydrationSeed",
    "RehydrationSnapshot",
    "RouteSnapshotModel",
    "build_rehydrated_state",
    "canonical_payload",
    "load_snapshot",
    "prediction_to_hypothesis",
    "route_to_snapshot",
    "snapshot_to_route",
    "validate_rehydrated_payload",
]
