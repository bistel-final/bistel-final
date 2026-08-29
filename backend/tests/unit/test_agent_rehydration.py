"""`V5-C-3.4` snapshot·25-channel 재수화 단위 계약."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import approval_store
from app.agent import rehydration as subject
from app.agent.incident import ResolvedIncident
from app.agent.repository import ActionBundle
from app.agent.routing import (
    GraphRouteEvidence,
    ResolvedIncidentRoute,
    RouteMismatch,
    WaferRoute,
)
from app.agent.routing_repository import RouteStep
from app.agent.state import (
    AgentError,
    DeliveryPlan,
    Hypothesis,
    ToolBudget,
)
from app.common.enums import (
    ActionCode,
    AlarmSource,
    AlarmType,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    Severity,
    ToolCallStatus,
)
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    FdcSummaryToolResult,
    ParameterSummaryItem,
    WaferContext,
)

ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")
THREAD_ID = "11111111-2222-3333-4444-555555555555"
OTHER_ALARM = AlarmRef(source=AlarmSource.SUMMARY, alarm_id="SA-OTHER")


def _route() -> ResolvedIncidentRoute:
    step = RouteStep(
        lot_hist_id="LH-1",
        lot_id="LOT-1",
        wafer_id="W-1",
        wafer_no=1,
        step_id="STEP-1",
        area_id="PHOTO",
        equipment_id="EQP01",
        chamber_id="EQP01-PM1",
        recipe_id="RECIPE01",
        track_in_at=datetime(2026, 8, 1, tzinfo=UTC),
        track_out_at=None,
    )
    return ResolvedIncidentRoute(
        incident=ResolvedIncident(
            lot_id="LOT-1",
            chamber_id="EQP01-PM1",
            requested_alarm=ALARM,
            representative_alarm=ALARM,
            member_alarms=(ALARM,),
        ),
        wafer_routes=(WaferRoute("W-1", (ALARM,), (step,)),),
        graph_evidence=(
            GraphRouteEvidence(
                chamber_id="EQP01-PM1",
                equipment_id="EQP01",
                model_code="PH-9000",
                process_step_id="STEP-1",
                upstream_process_step_ids=(),
                downstream_process_step_ids=("STEP-2",),
                relation_ids=("REL-1",),
                graph_revision="graph-v1",
            ),
        ),
        route_consistency=False,
        mismatches=(
            RouteMismatch(
                code="NEXT_STEP_MISSING",
                wafer_id="W-1",
                from_lot_hist_id="LH-1",
                to_lot_hist_id=None,
                postgres_ids=("STEP-2",),
                graph_ids=(),
                relation_ids=("REL-1",),
            ),
        ),
    )


def _seed() -> subject.RehydrationSeed:
    return subject.RehydrationSeed(
        route=_route(),
        fdc_evidence=None,
        optional_anomaly_evidence=None,
        graph_evidence=None,
        document_evidence=None,
        errors=(
            AgentError(code="TOOL_BUDGET_EXCEEDED", node="collect_fdc", terminal=False),
        ),
        tool_budget=ToolBudget(used=0),
        fdc_lot_hist_id="LH-1",
    )


def _fdc_result() -> FdcSummaryToolResult:
    return FdcSummaryToolResult(
        ok=True,
        wafer=WaferContext(
            lot_hist_id="LH-1",
            lot_id="LOT-1",
            wafer_no=1,
            chamber_id="EQP01-PM1",
            equipment_id="EQP01",
            step_id="STEP-1",
        ),
        parameters=[
            ParameterSummaryItem(
                parameter_id="P-1",
                parameter_name="Pressure",
                recipe_step_no=1,
                point_cnt=1,
                ooc_point_cnt=0,
                oos_point_cnt=0,
                alarm_type=AlarmType.IN,
            )
        ],
    )


def _snapshot() -> subject.RehydrationSnapshot:
    return subject.RehydrationSnapshot.from_seed(
        _seed(),
        action_id="ACT-1",
        approval_id="APR-1",
        deliveries=(
            DeliveryPlan(
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.WAITING,
            ),
            DeliveryPlan(
                channel=DeliveryChannel.MES_MOCK,
                status=DeliveryStatus.BLOCKED,
            ),
        ),
    )


def _tool_call(
    tool_name: str,
    *,
    status: ToolCallStatus = ToolCallStatus.SUCCESS,
    error_msg: str | None = None,
    output: dict[str, Any] | None = None,
    latency_ms: int | None = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        tool_name=tool_name,
        status=status,
        error_msg=error_msg,
        output={} if output is None else output,
        latency_ms=latency_ms,
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence: dict[str, Any] | None = None,
) -> SimpleNamespace:
    snapshot = _snapshot()
    run_evidence = {
        subject.REHYDRATION_SNAPSHOT_KEY: snapshot.as_evidence(),
        "action_provenance": {
            "schema": "action-provenance-v1",
            "action_policy_version": "ACTION-POLICY-V1",
            "member_alarms": [ALARM.model_dump(mode="json")],
        },
    }
    if evidence is not None:
        run_evidence = evidence
    run = SimpleNamespace(
        agent_run_id="RUN-1",
        thread_id=THREAD_ID,
        retry_of_run_id=None,
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
        status=RunStatus.WAITING_APPROVAL,
        autonomy_level=2,
        requested_alarm=ALARM,
        representative_alarm=ALARM,
        action=ActionCode.EQP_HOLD,
        severity=Severity.HIGH,
        llm_model="fixture-model",
        prompt_version="agent-hypothesis-v1",
        evidence=run_evidence,
    )
    prediction = SimpleNamespace(
        predicted_fault_code=FaultHypothesis.OTH,
        confidence=0.5,
        cause_summary="fixture",
        evidence={
            "schema_version": "agent-evidence-v1",
            "supporting_alarms": [ALARM.model_dump(mode="json")],
            "supporting_chunk_ids": [],
            "supporting_relation_ids": ["REL-1"],
            "uncertainty": "",
        },
        llm_model="fixture-model",
        prompt_version="agent-hypothesis-v1",
    )
    bundle = ActionBundle(
        action_id="ACT-1",
        action_code=ActionCode.EQP_HOLD,
        approval_id="APR-1",
        approval_status=ApprovalStatus.PENDING,
        approval_agent_run_id="RUN-1",
        delivery_channels=(DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK),
    )
    state = SimpleNamespace(
        run=run,
        prediction=prediction,
        bundle=bundle,
        member_alarms=[ALARM],
        tool_calls=[],
    )
    monkeypatch.setattr(subject, "get_agent_run", lambda *_a: state.run)
    monkeypatch.setattr(subject, "list_run_alarms", lambda *_a: state.member_alarms)
    monkeypatch.setattr(subject, "get_prediction_or_none", lambda *_a: state.prediction)
    monkeypatch.setattr(
        subject,
        "find_run_action",
        lambda *_a: SimpleNamespace(action_id="ACT-1"),
    )
    monkeypatch.setattr(
        subject,
        "get_action_bundle",
        lambda *_a: state.bundle,
    )
    monkeypatch.setattr(subject, "list_tool_calls", lambda *_a: state.tool_calls)
    return state


def test_route_snapshot_round_trip_preserves_the_domain_hierarchy() -> None:
    restored = subject.snapshot_to_route(subject.route_to_snapshot(_route()))
    assert restored == _route()
    assert isinstance(restored.wafer_routes[0].steps[0], RouteStep)
    assert restored.graph_evidence[0].relation_ids == ("REL-1",)


def test_snapshot_json_has_no_runtime_secret_or_label_fields() -> None:
    payload = _snapshot().as_evidence()
    flattened = str(payload).lower()
    assert payload["schema_version"] == subject.REHYDRATION_SNAPSHOT_SCHEMA
    assert "password" not in flattened
    assert "dsn" not in flattened
    assert "fault_code" not in flattened
    assert "approval_email" not in flattened


def test_build_rehydrates_all_25_channels_without_tool_or_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch)
    payload = subject.build_rehydrated_state(object(), "RUN-1")
    assert len(payload) == 25
    assert payload["graph_evidence"] is None  # Level 2 skip 보존
    assert payload["errors"][0].code == "TOOL_BUDGET_EXCEEDED"
    assert payload["terminal_error"] is None
    assert payload["approval_decision"] is None
    assert payload["pending_llm_usage"] is None
    assert payload["hypothesis"] == Hypothesis.model_validate(payload["hypothesis"])


def test_canonical_projection_ignores_tuple_list_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch)
    payload = subject.build_rehydrated_state(object(), "RUN-1")
    round_tripped = deepcopy(payload)
    round_tripped["member_alarms"] = list(round_tripped["member_alarms"])
    round_tripped["deliveries"] = list(round_tripped["deliveries"])
    assert subject.canonical_payload(round_tripped) == subject.canonical_payload(
        payload
    )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda evidence: evidence.pop(subject.REHYDRATION_SNAPSHOT_KEY),
            "REHYDRATE_SNAPSHOT_MISSING",
        ),
        (
            lambda evidence: evidence[subject.REHYDRATION_SNAPSHOT_KEY].__setitem__(
                "schema_version", "future-v2"
            ),
            "REHYDRATE_SNAPSHOT_VERSION_UNSUPPORTED",
        ),
        (
            lambda evidence: evidence["action_provenance"].__setitem__(
                "member_alarms", []
            ),
            "REHYDRATE_PROVENANCE_MISMATCH",
        ),
    ],
)
def test_invalid_db_inputs_fail_before_checkpoint_write(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
    code: str,
) -> None:
    evidence = {
        subject.REHYDRATION_SNAPSHOT_KEY: _snapshot().as_evidence(),
        "action_provenance": {
            "schema": "action-provenance-v1",
            "action_policy_version": "ACTION-POLICY-V1",
            "member_alarms": [ALARM.model_dump(mode="json")],
        },
    }
    mutate(evidence)
    _wire(monkeypatch, evidence=evidence)
    with pytest.raises(subject.RehydrationError) as caught:
        subject.build_rehydrated_state(object(), "RUN-1")
    assert caught.value.code == code


@pytest.mark.parametrize(
    "mutate",
    [
        lambda state: setattr(state.run, "lot_id", "LOT-OTHER"),
        lambda state: setattr(state.run, "chamber_id", "EQP02-PM1"),
        lambda state: setattr(state.run, "requested_alarm", OTHER_ALARM),
        lambda state: setattr(state.run, "representative_alarm", OTHER_ALARM),
        lambda state: setattr(state, "member_alarms", [OTHER_ALARM]),
    ],
    ids=("lot", "chamber", "requested", "representative", "members"),
)
def test_snapshot_identity_mismatch_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
) -> None:
    state = _wire(monkeypatch)
    mutate(state)
    with pytest.raises(subject.RehydrationError) as caught:
        subject.build_rehydrated_state(object(), "RUN-1")
    assert caught.value.code == "REHYDRATE_SNAPSHOT_IDENTITY_MISMATCH"


def test_fdc_input_identity_mismatch_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _wire(monkeypatch)
    snapshot = state.run.evidence[subject.REHYDRATION_SNAPSHOT_KEY]
    snapshot["fdc_evidence"] = _fdc_result().model_dump(mode="json")
    snapshot["fdc_lot_hist_id"] = "LH-OTHER"
    with pytest.raises(subject.RehydrationError) as caught:
        subject.build_rehydrated_state(object(), "RUN-1")
    assert caught.value.code == "REHYDRATE_PROVENANCE_MISMATCH"


def test_non_send_tool_after_snapshot_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _wire(monkeypatch)
    state.tool_calls = [_tool_call("search_documents")]
    with pytest.raises(subject.RehydrationError) as caught:
        subject.build_rehydrated_state(object(), "RUN-1")
    assert caught.value.code == "REHYDRATE_PROVENANCE_MISMATCH"


def test_post_snapshot_send_action_audit_restores_the_current_db_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _wire(monkeypatch)
    state.tool_calls = [_tool_call("send_action")]

    payload = subject.build_rehydrated_state(object(), "RUN-1")

    assert payload["tool_budget"] == ToolBudget(
        used=1,
        by_tool={"send_action": 1},
        send_used=1,
        pending_reservations=0,
    )


def test_post_snapshot_send_action_tail_cannot_exceed_the_send_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _wire(monkeypatch)
    state.tool_calls = [_tool_call("send_action") for _ in range(3)]

    with pytest.raises(subject.RehydrationError) as caught:
        subject.build_rehydrated_state(object(), "RUN-1")

    assert caught.value.code == "REHYDRATE_PROVENANCE_MISMATCH"


def test_snapshot_tool_prefix_identity_is_still_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _wire(monkeypatch)
    raw_snapshot = state.run.evidence[subject.REHYDRATION_SNAPSHOT_KEY]
    raw_snapshot["tool_budget"] = ToolBudget(
        used=1,
        by_tool={"get_fdc_summary": 1},
        send_used=0,
        pending_reservations=0,
    ).model_dump(mode="json")
    state.tool_calls = [_tool_call("send_action")]

    with pytest.raises(subject.RehydrationError) as caught:
        subject.build_rehydrated_state(object(), "RUN-1")

    assert caught.value.code == "REHYDRATE_PROVENANCE_MISMATCH"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda state: setattr(state, "prediction", None),
            "REHYDRATE_PREDICTION_MISMATCH",
        ),
        (
            lambda state: setattr(
                state,
                "bundle",
                replace(state.bundle, approval_agent_run_id="RUN-OTHER"),
            ),
            "REHYDRATE_BUNDLE_MISMATCH",
        ),
        (
            lambda state: setattr(state.run, "status", RunStatus.RUNNING),
            "REHYDRATE_RUN_NOT_WAITING",
        ),
    ],
    ids=("prediction", "bundle", "run-status"),
)
def test_remaining_db_fail_closed_branches_have_negative_regressions(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
    expected: str,
) -> None:
    state = _wire(monkeypatch)
    mutate(state)
    with pytest.raises(subject.RehydrationError) as caught:
        subject.build_rehydrated_state(object(), "RUN-1")
    assert caught.value.code == expected


class _CheckpointGraph:
    def __init__(self, *, mode: str = "success") -> None:
        self.mode = mode
        self.values: dict[str, Any] = {}
        self.next: tuple[str, ...] = ()
        self.update_calls = 0

    def update_state(
        self, _config: Any, payload: dict[str, Any], *, as_node: str
    ) -> None:
        assert as_node == "persist_action"
        self.update_calls += 1
        if self.mode == "missing":
            raise RuntimeError("write acknowledgement lost")
        self.values = dict(payload)
        self.next = ("approval_email",)
        if self.mode == "mismatch":
            self.values["approval_id"] = "APR-other"
        if self.mode == "ack_lost":
            raise RuntimeError("write acknowledgement lost")

    def get_state(self, _config: Any) -> Any:
        return SimpleNamespace(values=self.values, next=self.next)


@contextmanager
def _transactions() -> Any:
    yield object()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("missing", "REHYDRATE_WRITE_UNCERTAIN"),
        ("mismatch", "REHYDRATE_CHECKPOINT_UNVERIFIED"),
    ],
)
def test_checkpoint_write_postcondition_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected: str,
) -> None:
    graph = _CheckpointGraph(mode=mode)
    payload = {
        "run_id": "RUN-1",
        "thread_id": THREAD_ID,
        "approval_id": "APR-1",
        "terminal_error": None,
    }
    monkeypatch.setattr(approval_store, "build_rehydrated_state", lambda *_a: payload)
    monkeypatch.setattr(approval_store, "canonical_payload", lambda _value: "same")
    with pytest.raises(approval_store.HitlResumeError) as caught:
        approval_store._rehydrate_locked(
            graph,
            _transactions,
            run_id="RUN-1",
            thread_id=THREAD_ID,
        )
    assert caught.value.code == expected


def test_checkpoint_write_ack_loss_is_reclassified_by_exact_postcondition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _CheckpointGraph(mode="ack_lost")
    payload = {
        "run_id": "RUN-1",
        "thread_id": THREAD_ID,
        "approval_id": "APR-1",
        "terminal_error": None,
    }
    recorded: list[str] = []
    monkeypatch.setattr(approval_store, "build_rehydrated_state", lambda *_a: payload)
    monkeypatch.setattr(approval_store, "canonical_payload", lambda _value: "same")
    monkeypatch.setattr(
        approval_store,
        "_record_rehydration_success",
        lambda _transactions, run_id: recorded.append(run_id),
    )
    approval_store._rehydrate_locked(
        graph,
        _transactions,
        run_id="RUN-1",
        thread_id=THREAD_ID,
    )
    assert recorded == ["RUN-1"]


def test_rehydration_audit_appends_repeated_recovery_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(evidence=None)

    def merge(*_args: Any, terminal_evidence: dict[str, Any], **_kwargs: Any) -> None:
        run.evidence = {**(run.evidence or {}), **terminal_evidence}

    monkeypatch.setattr(approval_store, "get_agent_run", lambda *_a: run)
    monkeypatch.setattr(approval_store, "merge_run_action_provenance", merge)

    approval_store._record_rehydration_success(_transactions, "RUN-1")
    first_timestamp = run.evidence[subject.REHYDRATION_AUDIT_KEY]["events"][0]
    approval_store._record_rehydration_success(_transactions, "RUN-1")

    audit = run.evidence[subject.REHYDRATION_AUDIT_KEY]
    assert audit["schema_version"] == subject.REHYDRATION_SNAPSHOT_SCHEMA
    assert len(audit["events"]) == 2
    assert audit["events"][0] == first_timestamp


def test_active_terminal_checkpoint_is_drift_not_rehydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(thread_id=THREAD_ID, status=RunStatus.WAITING_APPROVAL)
    graph = _CheckpointGraph()
    graph.values = {"run_id": "RUN-1", "thread_id": THREAD_ID}
    rehydrated: list[str] = []
    monkeypatch.setattr(approval_store, "get_agent_run", lambda *_a: run)

    @contextmanager
    def mutex(*_args: Any) -> Any:
        yield object()

    monkeypatch.setattr(approval_store, "_resume_mutex", mutex)
    monkeypatch.setattr(
        approval_store,
        "_rehydrate_locked",
        lambda *_a, **_k: rehydrated.append("called"),
    )
    with pytest.raises(approval_store.HitlResumeError) as caught:
        approval_store.recover_hitl_run(
            graph,
            _transactions,
            lambda: None,
            "RUN-1",
        )
    assert caught.value.code == "CHECKPOINT_PHASE_INVALID"
    assert rehydrated == []


@pytest.mark.parametrize("status", [RunStatus.COMPLETED, RunStatus.FAILED])
def test_terminal_run_never_reaches_rehydration(
    monkeypatch: pytest.MonkeyPatch,
    status: RunStatus,
) -> None:
    run = SimpleNamespace(thread_id=THREAD_ID, status=status)
    rehydrated: list[str] = []
    monkeypatch.setattr(approval_store, "get_agent_run", lambda *_a: run)
    monkeypatch.setattr(
        approval_store,
        "_rehydrate_locked",
        lambda *_a, **_k: rehydrated.append("called"),
    )
    with pytest.raises(approval_store.HitlResumeError) as caught:
        approval_store.recover_hitl_run(
            object(),
            _transactions,
            lambda: None,
            "RUN-1",
        )
    assert caught.value.code == "RUN_NOT_ACTIVE"
    assert rehydrated == []


def test_missing_checkpoint_on_running_run_does_not_rehydrate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(thread_id=THREAD_ID, status=RunStatus.RUNNING)
    graph = _CheckpointGraph()
    rehydrated: list[str] = []
    monkeypatch.setattr(approval_store, "get_agent_run", lambda *_a: run)

    @contextmanager
    def mutex(*_args: Any) -> Any:
        yield object()

    monkeypatch.setattr(approval_store, "_resume_mutex", mutex)
    monkeypatch.setattr(
        approval_store,
        "_rehydrate_locked",
        lambda *_a, **_k: rehydrated.append("called"),
    )
    with pytest.raises(approval_store.HitlResumeError) as caught:
        approval_store.recover_hitl_run(
            graph,
            _transactions,
            lambda: None,
            "RUN-1",
        )
    assert caught.value.code == "REHYDRATE_RUN_NOT_WAITING"
    assert rehydrated == []
