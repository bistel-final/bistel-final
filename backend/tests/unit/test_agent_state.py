"""`V5-C-2.1` State·DTO 불변식."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.incident import ResolvedIncident
from app.agent.routing import GraphRouteEvidence, ResolvedIncidentRoute
from app.agent.state import (
    INITIAL_STATUS,
    RULE_TO_ACTION,
    ActionDecision,
    AgentError,
    CompletedAgentState,
    DeliveryPlan,
    Hypothesis,
    PersistResult,
    ToolBudget,
)
from app.common.enums import (
    ActionCode,
    AlarmSource,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    Severity,
    ThresholdValidationStatus,
)
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    AnomalySignal,
    DocumentHit,
    DocumentSearchToolResult,
    FdcSummaryToolResult,
    ParameterSummaryItem,
    WaferContext,
)

ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")


def _route() -> ResolvedIncidentRoute:
    incident = ResolvedIncident(
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        requested_alarm=ALARM,
        representative_alarm=ALARM,
        member_alarms=(ALARM,),
    )
    return ResolvedIncidentRoute(
        incident=incident,
        wafer_routes=(),
        graph_evidence=(
            GraphRouteEvidence(
                chamber_id="EQP01-PM1",
                equipment_id="EQP01",
                model_code="MODEL-1",
                process_step_id="CT-PHOTO",
                upstream_process_step_ids=(),
                downstream_process_step_ids=(),
                relation_ids=("REL-1",),
                graph_revision="rev-1",
            ),
        ),
        route_consistency=True,
        mismatches=(),
    )


def _anomaly() -> AnomalySignal:
    return AnomalySignal(
        score=0.7,
        model_version="model-1",
        score_method="iforest",
        threshold_validation_status=ThresholdValidationStatus.UNVERIFIED,
    )


def _fdc(anomaly: AnomalySignal | None = None) -> FdcSummaryToolResult:
    return FdcSummaryToolResult(
        ok=True,
        wafer=WaferContext(
            lot_hist_id="LH-1",
            lot_id="LOT001",
            wafer_no=1,
            chamber_id="EQP01-PM1",
            equipment_id="EQP01",
            step_id="CT-PHOTO",
        ),
        parameters=[
            ParameterSummaryItem(
                parameter_id="P-1",
                parameter_name="Pressure",
                recipe_step_no=1,
                point_cnt=1,
                ooc_point_cnt=0,
                oos_point_cnt=0,
                alarm_type="IN",
            )
        ],
        anomaly=anomaly,
    )


def _completed(**overrides: object) -> dict[str, object]:
    route = _route()
    values: dict[str, object] = {
        "run_id": "RUN-1",
        "thread_id": str(uuid4()),
        "retry_of_run_id": None,
        "requested_alarm": ALARM,
        "representative_alarm": ALARM,
        "member_alarms": (ALARM,),
        "lot_id": "LOT001",
        "chamber_id": "EQP01-PM1",
        "route": route,
        "fdc_evidence": _fdc(),
        "optional_anomaly_evidence": None,
        "graph_evidence": None,
        "document_evidence": DocumentSearchToolResult(ok=True, hits=[]),
        "hypothesis": Hypothesis(
            predicted_fault_code=FaultHypothesis.OTH,
            confidence=0.5,
            cause_summary="fixture",
            supporting_alarms=(ALARM,),
            uncertainty="",
        ),
        "action_decision": ActionDecision(
            action=ActionCode.MONITORING,
            severity=Severity.LOW,
            requires_approval=False,
            matched_rule="SUMMARY_OOC_ONLY",
        ),
        "action_id": "ACT-1",
        "approval_id": None,
        "deliveries": (),
        "tool_budget": ToolBudget(used=3),
        "errors": (),
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("rule", "action", "severity", "approval"),
    [
        ("R03_PRESENT", ActionCode.EQP_HOLD, Severity.HIGH, True),
        ("TRACE_OOS", ActionCode.WARNING, Severity.MEDIUM, False),
        ("SUMMARY_OOC_ONLY", ActionCode.MONITORING, Severity.LOW, False),
        ("NO_ALARM", None, None, False),
    ],
)
def test_action_decision_exactly_matches_the_rule_table(
    rule: str,
    action: ActionCode | None,
    severity: Severity | None,
    approval: bool,
) -> None:
    decision = ActionDecision(
        action=action,
        severity=severity,
        requires_approval=approval,
        matched_rule=rule,
    )
    assert decision.action is RULE_TO_ACTION[rule]


def test_a_rule_cannot_be_paired_with_another_action() -> None:
    with pytest.raises(ValidationError):
        ActionDecision(
            action=ActionCode.MONITORING,
            severity=Severity.LOW,
            requires_approval=False,
            matched_rule="R03_PRESENT",
        )


def test_no_action_has_no_severity_or_approval() -> None:
    with pytest.raises(ValidationError):
        ActionDecision(
            action=None,
            severity=Severity.LOW,
            requires_approval=False,
            matched_rule="NO_ALARM",
        )


def test_persist_result_uses_common_delivery_order_and_initial_status() -> None:
    decision = ActionDecision(
        action=ActionCode.EQP_HOLD,
        severity=Severity.HIGH,
        requires_approval=True,
        matched_rule="R03_PRESENT",
    )
    result = PersistResult(
        action_id="ACT-1",
        approval_id="APR-1",
        deliveries=tuple(
            DeliveryPlan(channel=channel, status=INITIAL_STATUS[channel])
            for channel in (DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK)
        ),
    )
    result.assert_matches(decision)


def test_no_action_rejects_any_persist_result() -> None:
    decision = ActionDecision(
        action=None,
        severity=None,
        requires_approval=False,
        matched_rule="NO_ALARM",
    )
    with pytest.raises(ValueError):
        PersistResult(action_id="ACT-1").assert_matches(decision)


@pytest.mark.parametrize("value", ["", "   "])
def test_persist_ids_are_non_empty(value: str) -> None:
    with pytest.raises(ValidationError):
        PersistResult(action_id=value)


def test_completed_state_accepts_checkpoint_equal_anomaly_objects() -> None:
    left = _anomaly()
    right = AnomalySignal.model_validate(left.model_dump(mode="json"))
    assert left is not right
    CompletedAgentState.model_validate(
        _completed(fdc_evidence=_fdc(left), optional_anomaly_evidence=right)
    )


def test_completed_state_rejects_different_anomaly_values() -> None:
    with pytest.raises(ValidationError):
        CompletedAgentState.model_validate(
            _completed(
                fdc_evidence=_fdc(_anomaly()),
                optional_anomaly_evidence=AnomalySignal(
                    score=0.8,
                    model_version="model-1",
                    score_method="iforest",
                    threshold_validation_status=(ThresholdValidationStatus.UNVERIFIED),
                ),
            )
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("autonomy_level", 2),
        ("terminal_error", None),
        ("fdc_lot_hist_id", "LH-1"),
        ("approval_decision", "APPROVE"),
    ],
)
def test_completed_state_rejects_internal_channels(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CompletedAgentState.model_validate(_completed(**{name: value}))


def test_completed_state_rejects_noncanonical_thread_id() -> None:
    with pytest.raises(ValidationError):
        CompletedAgentState.model_validate(_completed(thread_id="not-a-uuid"))


def test_hypothesis_citations_must_exist_in_the_same_state() -> None:
    hypothesis = Hypothesis(
        predicted_fault_code=FaultHypothesis.OTH,
        confidence=0.5,
        cause_summary="근거가 제한적입니다",
        supporting_alarms=(ALARM,),
        supporting_chunk_ids=("CHUNK-1",),
        supporting_relation_ids=("REL-1",),
        uncertainty="추가 확인 필요",
    )
    document = DocumentSearchToolResult(
        ok=True,
        hits=[
            DocumentHit(
                chunk_id="CHUNK-1",
                document_id="DOC-1",
                title="guide",
                score=0.8,
                content="content",
            )
        ],
    )
    CompletedAgentState.model_validate(
        _completed(hypothesis=hypothesis, document_evidence=document)
    )


def test_hypothesis_cannot_invent_a_chunk_id() -> None:
    hypothesis = Hypothesis(
        predicted_fault_code=FaultHypothesis.OTH,
        confidence=0.5,
        cause_summary="근거가 제한적입니다",
        supporting_chunk_ids=("CHUNK-MISSING",),
        uncertainty="",
    )
    with pytest.raises(ValidationError):
        CompletedAgentState.model_validate(_completed(hypothesis=hypothesis))


def test_delivery_channels_are_unique() -> None:
    duplicate = (
        DeliveryPlan(channel=DeliveryChannel.EMAIL, status=DeliveryStatus.WAITING),
        DeliveryPlan(channel=DeliveryChannel.EMAIL, status=DeliveryStatus.WAITING),
    )
    with pytest.raises(ValidationError):
        CompletedAgentState.model_validate(_completed(deliveries=duplicate))


def test_agent_error_cannot_contain_exception_text() -> None:
    with pytest.raises(ValidationError):
        AgentError(code="postgres://secret", node="collect_fdc", terminal=True)


def test_legacy_tool_budget_keeps_unknown_detail_instead_of_inventing_counts() -> None:
    budget = ToolBudget.model_validate({"max_calls": 8, "used": 3, "source": "DB"})
    assert budget.source == "DB"
    assert budget.by_tool is None
    assert budget.send_used is None
    assert budget.pending_reservations is None


def test_detailed_tool_budget_enforces_only_snapshot_structure() -> None:
    budget = ToolBudget(
        used=9,
        by_tool={"get_fdc_summary": 7, "send_action": 2},
        send_used=2,
        pending_reservations=1,
    )
    assert budget.used == 9  # 정책 초과 관측값도 State로 표현한다.


@pytest.mark.parametrize(
    "overrides",
    [
        {"by_tool": {"get_fdc_summary": 2}, "send_used": 0},
        {
            "by_tool": {"unknown_tool": 1},
            "send_used": 0,
            "pending_reservations": 0,
        },
        {
            "by_tool": {"send_action": 1},
            "send_used": 0,
            "pending_reservations": 0,
        },
        {
            "by_tool": {"get_fdc_summary": 1},
            "send_used": 0,
            "pending_reservations": 2,
        },
    ],
)
def test_tool_budget_rejects_incomplete_or_inconsistent_detail(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ToolBudget(used=1, **overrides)
