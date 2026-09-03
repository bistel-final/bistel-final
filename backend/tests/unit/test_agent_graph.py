"""`V5-C-2.1` canonical graph 위상·분기·실패 회귀."""

from __future__ import annotations

import ast
import inspect
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent import decision as decision_module
from app.agent import graph as subject
from app.agent import incident_repository, routing_repository
from app.agent.approval_store import HitlResumeError
from app.agent.graph import (
    CANONICAL_NODES,
    INTERNAL_NODES,
    AgentGraphDependencies,
    AgentGraphInputError,
    build_agent_graph,
)
from app.agent.hypothesis import HypothesisGenerationError
from app.agent.incident import ResolvedIncident
from app.agent.repository import (
    PredictionRow,
    RepositoryConflict,
    RepositoryContractError,
    ToolBudgetCounts,
)
from app.agent.routing import (
    GraphBoundary,
    GraphRouteEvidence,
    IncidentRoute,
    ResolvedIncidentRoute,
    RouteSnapshot,
)
from app.agent.state import (
    ActionDecision,
    DeliveryPlan,
    Hypothesis,
    HypothesisOutcome,
    LlmUsage,
    PersistResult,
    ToolBudget,
)
from app.agent.tools import ToolBudgetBlocked
from app.common.enums import (
    ActionCode,
    AlarmSource,
    Decision,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    Severity,
    ToolCallStatus,
)
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    ChannelDeliveryResult,
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    ParameterSummaryItem,
    SendActionToolResult,
    WaferContext,
)

ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")
RUN_STARTED_AT = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)


def _incident() -> ResolvedIncident:
    return ResolvedIncident(
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        requested_alarm=ALARM,
        representative_alarm=ALARM,
        member_alarms=(ALARM,),
    )


def _route(*, consistent: bool = True, covered: bool = True) -> ResolvedIncidentRoute:
    evidence = ()
    if covered:
        evidence = (
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
        )
    return ResolvedIncidentRoute(
        incident=_incident(),
        wafer_routes=(),
        graph_evidence=evidence,
        route_consistency=consistent,
        mismatches=(),
    )


def _fdc(*, ok: bool = True) -> FdcSummaryToolResult:
    if not ok:
        return FdcSummaryToolResult(ok=False, reason="TIMEOUT: fixture")
    return FdcSummaryToolResult(
        ok=True,
        wafer=WaferContext(
            lot_hist_id="LH-REP",
            lot_id="LOT001",
            wafer_no=1,
            chamber_id="EQP01-PM1",
            equipment_id="EQP01",
            step_id="CT-PHOTO",
        ),
        parameters=[
            ParameterSummaryItem(
                parameter_id="PARAM-1",
                parameter_name="Pressure",
                recipe_step_no=1,
                point_cnt=1,
                ooc_point_cnt=0,
                oos_point_cnt=1,
                alarm_type="OOS",
            )
        ],
    )


def _equipment(*, ok: bool = True) -> EquipmentContextToolResult:
    if not ok:
        return EquipmentContextToolResult(ok=False, reason="DEPENDENCY_ERROR: fixture")
    return EquipmentContextToolResult(
        ok=True,
        chamber_id="EQP01-PM1",
        equipment_id="EQP01",
        area="PHOTO",
        model_code="MODEL-1",
        process_step_id="CT-PHOTO",
        graph_revision="rev-1",
    )


class _FakeTools:
    def __init__(self, *, fdc_ok: bool = True, equipment_ok: bool = True) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.budget_connections: list[Any] = []
        self.finish_connections: list[Any] = []
        self.usage_connections: list[Any] = []
        self.llm_usage: list[tuple[int, int]] = []
        self._fdc_ok = fdc_ok
        self._equipment_ok = equipment_ok
        self.action: ActionCode | None = ActionCode.WARNING
        self.send_count = 0
        self.send_failure_reason: str | None = None

    def budget(self, run_id: str) -> ToolBudget:
        return ToolBudget(used=len(self.calls))

    def budget_from_connection(
        self,
        connection: Any,
        run_id: str,
    ) -> ToolBudget:
        self.budget_connections.append(connection)
        return self.budget(run_id)

    def history(self, run_id: str) -> tuple[Any, ...]:
        names = {
            "fdc": "get_fdc_summary",
            "equipment": "get_equipment_context",
            "documents": "search_documents",
            "send_action": "send_action",
        }
        rows = []
        for name, request in self.calls:
            canonical = names.get(name)
            if canonical is None:
                continue
            ok = not (
                (name == "fdc" and not self._fdc_ok)
                or (name == "equipment" and not self._equipment_ok)
            )
            rows.append(
                SimpleNamespace(
                    tool_name=canonical,
                    input=request.model_dump(mode="json"),
                    status=(ToolCallStatus.SUCCESS if ok else ToolCallStatus.ERROR),
                )
            )
        return tuple(rows)

    def fdc_summary(self, run_id: str, request: Any) -> FdcSummaryToolResult:
        self.calls.append(("fdc", request))
        return _fdc(ok=self._fdc_ok)

    def equipment_context(
        self, run_id: str, request: Any
    ) -> EquipmentContextToolResult:
        self.calls.append(("equipment", request))
        return _equipment(ok=self._equipment_ok)

    def document_search(self, run_id: str, request: Any) -> DocumentSearchToolResult:
        self.calls.append(("documents", request))
        return DocumentSearchToolResult(ok=True, hits=[])

    def send_action(self, run_id: str, request: Any) -> SendActionToolResult:
        self.calls.append(("send_action", request))
        self.send_count += 1
        if self.send_failure_reason is not None:
            return SendActionToolResult(ok=False, reason=self.send_failure_reason)
        if self.action is ActionCode.EQP_HOLD:
            mes_status = (
                DeliveryStatus.BLOCKED if self.send_count == 1 else DeliveryStatus.SENT
            )
            return SendActionToolResult(
                ok=True,
                action_id=request.action_id,
                effect_attempted=True,
                effect_channel=(
                    DeliveryChannel.EMAIL
                    if self.send_count == 1
                    else DeliveryChannel.MES_MOCK
                ),
                deliveries=[
                    ChannelDeliveryResult(
                        channel=DeliveryChannel.EMAIL,
                        status=DeliveryStatus.SENT,
                        sent=self.send_count == 1,
                        duplicate=self.send_count > 1,
                    ),
                    ChannelDeliveryResult(
                        channel=DeliveryChannel.MES_MOCK,
                        status=mes_status,
                        sent=self.send_count > 1,
                        duplicate=False,
                    ),
                ],
            )
        return SendActionToolResult(
            ok=True,
            action_id=request.action_id,
            effect_attempted=True,
            effect_channel=DeliveryChannel.EMAIL,
            deliveries=[
                ChannelDeliveryResult(
                    channel=DeliveryChannel.EMAIL,
                    status=DeliveryStatus.SENT,
                    sent=True,
                    duplicate=False,
                )
            ],
        )


class _BudgetBlockedTools(_FakeTools):
    def fdc_summary(self, run_id: str, request: Any) -> FdcSummaryToolResult:
        self.calls.append(("fdc-blocked", request))
        raise ToolBudgetBlocked(
            "TOOL_BUDGET_EXHAUSTED",
            ToolBudgetCounts(
                total=8,
                by_tool={
                    "get_fdc_summary": 3,
                    "get_equipment_context": 3,
                    "send_action": 2,
                },
                pending_reservations=1,
            ),
        )


@dataclass
class _Ports:
    action: ActionCode | None = ActionCode.WARNING
    decision: Decision = Decision.APPROVE
    invalid_citation: bool = False
    generation_error: bool = False

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self.persisted_decisions: list[ActionDecision] = []
        self.hypothesis_extra_data_gaps: tuple[str, ...] = ()

    def generate_hypothesis(
        self,
        fdc: Any,
        graph: Any,
        docs: Any,
        route: Any,
        extra_data_gaps: tuple[str, ...],
    ) -> Any:
        self.calls.append("generate_hypothesis")
        self.hypothesis_extra_data_gaps = extra_data_gaps
        if self.generation_error:
            raise HypothesisGenerationError(
                "LLM_TIMEOUT",
                usage=LlmUsage(
                    model="fixture-model",
                    prompt_version=subject.PROMPT_VERSION,
                    input_tokens=10,
                    output_tokens=5,
                ),
            )
        return HypothesisOutcome(
            hypothesis=Hypothesis(
                predicted_fault_code=FaultHypothesis.OTH,
                confidence=0.5,
                cause_summary="fixture",
                supporting_chunk_ids=("MISSING",) if self.invalid_citation else (),
                uncertainty="",
            ),
            llm_usage=LlmUsage(
                model="fixture-model",
                prompt_version=subject.PROMPT_VERSION,
                input_tokens=10,
                output_tokens=5,
            ),
        )

    def decide_action(self, route: Any) -> ActionDecision:
        self.calls.append("decide_action")
        table = {
            ActionCode.WARNING: (Severity.MEDIUM, False, "TRACE_OOS"),
            ActionCode.MONITORING: (Severity.LOW, False, "SUMMARY_OOC_ONLY"),
            ActionCode.EQP_HOLD: (Severity.HIGH, True, "R03_PRESENT"),
            None: (None, False, "NO_ALARM"),
        }
        severity, approval, rule = table[self.action]
        return ActionDecision(
            action=self.action,
            severity=severity,
            requires_approval=approval,
            matched_rule=rule,
            policy_version="ACTION-POLICY-V1",
        )

    def persist_action(
        self, run_id: str, decision: ActionDecision, _rehydration_seed: Any
    ) -> PersistResult:
        self.calls.append("persist_action")
        self.persisted_decisions.append(decision)
        if decision.action is ActionCode.EQP_HOLD:
            return PersistResult(
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
        if decision.action is ActionCode.WARNING:
            return PersistResult(
                action_id="ACT-1",
                deliveries=(
                    DeliveryPlan(
                        channel=DeliveryChannel.EMAIL,
                        status=DeliveryStatus.WAITING,
                    ),
                ),
            )
        return PersistResult(action_id="ACT-1")

    def notify_email(self, action_id: str) -> None:
        self.calls.append("notify_email")

    def approval_email(
        self,
        run_id: str,
        action_id: str,
        approval_id: str,
    ) -> None:
        self.calls.append("approval_email")

    def hitl_interrupt(self, approval_id: str) -> Decision:
        self.calls.append("hitl_interrupt")
        return self.decision

    def publish_mes(self, action_id: str) -> None:
        self.calls.append("publish_mes")

    def writeback_result(self, action_id: str) -> tuple[DeliveryPlan, ...]:
        self.calls.append("writeback_result")
        return (
            DeliveryPlan(channel=DeliveryChannel.EMAIL, status=DeliveryStatus.SENT),
            DeliveryPlan(
                channel=DeliveryChannel.MES_MOCK,
                status=DeliveryStatus.SENT,
            ),
        )

    def cancel_mes(self, action_id: str) -> tuple[DeliveryPlan, ...]:
        self.calls.append("cancel_mes")
        return (
            DeliveryPlan(channel=DeliveryChannel.EMAIL, status=DeliveryStatus.SENT),
            DeliveryPlan(
                channel=DeliveryChannel.MES_MOCK,
                status=DeliveryStatus.CANCELED,
            ),
        )


class _ProductionDecisionPorts(_Ports):
    def decide_action(self, route: ResolvedIncidentRoute) -> ActionDecision:
        self.calls.append("decide_action")
        return decision_module.production_port()(route)


class _EmailFailurePorts(_Ports):
    def __init__(self, error: Exception) -> None:
        super().__init__(action=ActionCode.EQP_HOLD)
        self.error: Exception | None = error

    def approval_email(
        self,
        run_id: str,
        action_id: str,
        approval_id: str,
    ) -> None:
        self.calls.append("approval_email")
        if self.error is not None:
            raise self.error


class _PendingHitlPorts(_Ports):
    def __init__(self) -> None:
        super().__init__(action=ActionCode.EQP_HOLD)

    def hitl_interrupt(self, approval_id: str) -> Decision:
        self.calls.append("hitl_interrupt")
        raise RepositoryConflict("APPROVAL_STILL_PENDING")


def _build(
    monkeypatch: pytest.MonkeyPatch,
    *,
    level_route: ResolvedIncidentRoute | None = None,
    tools: _FakeTools | None = None,
    ports: _Ports | None = None,
    combine_error: Exception | None = None,
    now: Any | None = None,
    finish_error: Exception | None = None,
    interrupt_after: tuple[str, ...] | None = None,
    lot_hist_id_of_member: dict[tuple[AlarmSource, str], str] | None = None,
    transaction_entry_error: Exception | None = None,
    usage_record_error_once: Exception | None = None,
    usage_record_error_always: Exception | None = None,
    existing_prediction: PredictionRow | None = None,
    run_evidence: dict[str, Any] | None = None,
    durable_interrupt: bool = True,
    require_bound_thread: bool = False,
    configured_llm_model: str | None = None,
    start_calls: list[dict[str, Any]] | None = None,
    diagnostic_wafer_refs: tuple[tuple[str, str], ...] = (),
) -> tuple[Any, _FakeTools, _Ports | None, list[tuple[str, Any]], list[str]]:
    route = level_route or _route()
    tool_set = tools or _FakeTools()
    tool_set.action = None if ports is None else ports.action
    finish_events: list[tuple[str, Any]] = []
    transaction_events: list[str] = []

    @contextmanager
    def transactions() -> Any:
        transaction_events.append("begin")
        if transaction_entry_error is not None:
            raise transaction_entry_error
        yield object()
        transaction_events.append("commit")

    run = SimpleNamespace(
        agent_run_id="RUN-1",
        thread_id=str(uuid4()),
        retry_of_run_id=None,
        status=RunStatus.RUNNING,
        llm_model=(
            None if existing_prediction is None else existing_prediction.llm_model
        ),
        prompt_version=subject.PROMPT_VERSION,
        input_tokens=None if existing_prediction is None else 10,
        output_tokens=None if existing_prediction is None else 5,
        latency_ms=0,
        evidence=dict(run_evidence or {}),
        started_at=RUN_STARTED_AT,
    )
    started = SimpleNamespace(run=run, incident=_incident())
    snapshot = RouteSnapshot(
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        member_keys=((AlarmSource.TRACE, "TA-01"),),
        wafer_of_member={(AlarmSource.TRACE, "TA-01"): "LOT001W001"},
        lot_hist_id_of_member=(
            {(AlarmSource.TRACE, "TA-01"): "LH-REP"}
            if lot_hist_id_of_member is None
            else lot_hist_id_of_member
        ),
        steps=(),
        diagnostic_wafer_refs=diagnostic_wafer_refs,
    )

    def start(*_args: Any, **kwargs: Any) -> Any:
        if start_calls is not None:
            start_calls.append(dict(kwargs))
        factory = kwargs.get("thread_id_factory")
        if callable(factory):
            run.thread_id = factory()
        return started

    monkeypatch.setattr(subject, "start_incident_run", start)
    monkeypatch.setattr(
        subject,
        "read_route_snapshot",
        lambda connection, incident: IncidentRoute(
            incident=incident, snapshot=snapshot
        ),
    )

    def combine(bound: Any, *, graph: Any) -> ResolvedIncidentRoute:
        if combine_error is not None:
            raise combine_error
        return route

    monkeypatch.setattr(subject, "combine_route", combine)

    def finish(connection: Any, run_id: str, status: RunStatus, **kwargs: Any) -> Any:
        transaction_events.append("finish")
        tool_set.finish_connections.append(connection)
        finish_events.append((status.value, kwargs))
        if finish_error is not None:
            raise finish_error
        return SimpleNamespace()

    prediction: PredictionRow | None = existing_prediction
    usage_record_calls = 0

    def get_run(connection: Any, run_id: str) -> Any:
        transaction_events.append("get_run")
        return run

    def get_prediction(_connection: Any, _run_id: str) -> PredictionRow | None:
        return prediction

    def lock_run(_connection: Any, _run_id: str) -> Any:
        return run

    def insert(
        _connection: Any,
        *,
        agent_run_id: str,
        predicted_fault_code: FaultHypothesis,
        confidence: float,
        cause_summary: str,
        evidence: dict[str, Any],
        llm_model: str,
        prompt_version: str,
        **_kwargs: Any,
    ) -> PredictionRow:
        nonlocal prediction
        prediction = PredictionRow(
            agent_run_id=agent_run_id,
            predicted_fault_code=predicted_fault_code,
            confidence=confidence,
            cause_summary=cause_summary,
            evidence=evidence,
            llm_model=llm_model,
            prompt_version=prompt_version,
            created_at=RUN_STARTED_AT,
        )
        return prediction

    def record_usage(
        connection: Any,
        _run_id: str,
        *,
        llm_model: str,
        prompt_version: str,
        input_tokens: int,
        output_tokens: int,
    ) -> Any:
        nonlocal usage_record_calls
        usage_record_calls += 1
        tool_set.usage_connections.append(connection)
        if usage_record_error_always is not None:
            raise usage_record_error_always
        if usage_record_calls == 1 and usage_record_error_once is not None:
            raise usage_record_error_once
        run.llm_model = llm_model
        run.prompt_version = prompt_version
        run.input_tokens = (run.input_tokens or 0) + input_tokens
        run.output_tokens = (run.output_tokens or 0) + output_tokens
        tool_set.llm_usage.append((input_tokens, output_tokens))
        return run

    monkeypatch.setattr(subject, "finish_agent_run", finish)
    monkeypatch.setattr(subject, "get_agent_run", get_run)
    monkeypatch.setattr(subject, "get_prediction_or_none", get_prediction)
    monkeypatch.setattr(subject, "lock_agent_run", lock_run)
    monkeypatch.setattr(subject, "insert_prediction", insert)
    monkeypatch.setattr(subject, "record_run_llm_usage", record_usage)

    def merge_provenance(
        _connection: Any,
        _run_id: str,
        *,
        terminal_evidence: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> Any:
        run.evidence = {**run.evidence, **(terminal_evidence or {})}
        return run

    monkeypatch.setattr(
        subject,
        "merge_run_action_provenance",
        merge_provenance,
    )
    graph = build_agent_graph(
        AgentGraphDependencies(
            transactions=transactions,
            tools=tool_set,  # type: ignore[arg-type]
            routing_graph=GraphBoundary(lambda value: None, lambda value: None),
            ports=ports,
            configured_llm_model=configured_llm_model,
            require_bound_thread=require_bound_thread,
            now=(lambda: RUN_STARTED_AT) if now is None else now,
        ),
        checkpointer=(MemorySaver() if interrupt_after and durable_interrupt else None),
        interrupt_after=interrupt_after,
    )
    return graph, tool_set, ports, finish_events, transaction_events


def _invoke(graph: Any, level: int = 1) -> dict[str, Any]:
    return graph.invoke(
        {"requested_alarm": ALARM, "autonomy_level": level},
        config={"configurable": {"thread_id": "11111111-2222-3333-4444-555555555555"}},
    )


@pytest.mark.parametrize("module", [incident_repository, routing_repository])
def test_read_repositories_use_the_shared_public_sql_boundary(module: Any) -> None:
    tree = ast.parse(inspect.getsource(module))
    function_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "_execute" not in function_names
    assert "_translate" not in imported_names
    assert "execute_read_all" in imported_names


def test_graph_has_exactly_sixteen_canonical_and_one_internal_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, *_ = _build(monkeypatch)
    names = set(graph.get_graph().nodes) - {"__start__", "__end__"}
    assert names == set(CANONICAL_NODES) | set(INTERNAL_NODES)
    # V5-C-7.1: Level 3 ReAct 노드 2개(react_select·react_tool) 추가 → 16
    assert len(CANONICAL_NODES) == 16
    assert INTERNAL_NODES == ("fail_run",)


def test_graph_edges_are_the_reviewed_canonical_and_failure_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, *_ = _build(monkeypatch)
    actual = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    canonical = {
        ("__start__", "load_incident"),
        ("load_incident", "collect_fdc"),
        ("collect_fdc", "collect_equipment"),
        ("collect_fdc", "collect_documents"),
        ("collect_fdc", "react_select"),
        ("react_select", "react_tool"),
        ("react_select", "generate_hypothesis"),
        ("react_tool", "react_select"),
        ("collect_equipment", "collect_documents"),
        ("collect_documents", "generate_hypothesis"),
        ("generate_hypothesis", "decide_action"),
        ("decide_action", "persist_action"),
        ("decide_action", "finalize"),
        ("persist_action", "finalize"),
        ("persist_action", "notify_email"),
        ("persist_action", "approval_email"),
        ("notify_email", "finalize"),
        ("approval_email", "hitl_interrupt"),
        ("hitl_interrupt", "publish_mes"),
        ("hitl_interrupt", "cancel_mes"),
        ("publish_mes", "writeback_result"),
        ("writeback_result", "finalize"),
        ("cancel_mes", "finalize"),
        ("finalize", "__end__"),
        ("fail_run", "__end__"),
    }
    failure_edges = {(node, "fail_run") for node in CANONICAL_NODES}
    assert actual == canonical | failure_edges


def test_level_one_warning_calls_all_read_tools_and_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = _Ports(action=ActionCode.WARNING)
    graph, tools, _, finishes, _ = _build(monkeypatch, ports=ports)
    state = _invoke(graph, level=1)
    assert [name for name, _ in tools.calls] == [
        "fdc",
        "equipment",
        "documents",
        "send_action",
    ]
    assert ports.calls == [
        "generate_hypothesis",
        "decide_action",
        "persist_action",
    ]
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]
    assert state["action_id"] == "ACT-1"
    assert state["tool_budget"].used == 4
    assert set(state) == set(subject.CompletedAgentState.model_fields)
    assert tools.llm_usage == [(10, 5)]
    assert not {
        "autonomy_level",
        "terminal_error",
        "fdc_lot_hist_id",
        "approval_decision",
        "pending_llm_usage",
    } & set(state)


def test_more_than_three_fdc_targets_are_bounded_and_mark_diagnosis_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TargetAwareTools(_FakeTools):
        def fdc_summary(self, run_id: str, request: Any) -> FdcSummaryToolResult:
            self.calls.append(("fdc", request))
            result = _fdc(ok=True)
            assert result.wafer is not None
            return result.model_copy(
                update={
                    "wafer": result.wafer.model_copy(
                        update={"lot_hist_id": request.lot_hist_id}
                    )
                }
            )

    ports = _Ports(action=ActionCode.WARNING)
    graph, tools, _, finishes, _ = _build(
        monkeypatch,
        ports=ports,
        tools=_TargetAwareTools(),
        diagnostic_wafer_refs=(
            ("LH-10", "W10"),
            ("LH-2", "W2"),
            ("LH-1", "W1"),
            ("LH-3", "W3"),
        ),
    )

    state = _invoke(graph)

    assert [request.lot_hist_id for name, request in tools.calls if name == "fdc"] == [
        "LH-1",
        "LH-2",
        "LH-3",
    ]
    assert ports.hypothesis_extra_data_gaps == ("FDC_TARGET_BUDGET_EXCEEDED",)
    assert any(
        error.code == "FDC_TARGET_BUDGET_EXCEEDED" and not error.terminal
        for error in state["errors"]
    )
    assert state.get("terminal_error") is None, state
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]


def test_production_decision_reaches_persist_port_with_exact_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = _ProductionDecisionPorts()
    expected = decision_module.decide_action(_route())
    graph, _, _, finishes, _ = _build(monkeypatch, ports=ports)

    state = _invoke(graph)

    assert len(ports.persisted_decisions) == 1
    assert ports.persisted_decisions[0].model_dump(mode="json") == (
        expected.model_dump(mode="json")
    )
    assert state["action_decision"].model_dump(mode="json") == (
        expected.model_dump(mode="json")
    )
    assert ports.persisted_decisions[0].policy_version == "ACTION-POLICY-V1"
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]


def test_level_two_skips_only_redundant_equipment_context_and_keeps_model_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, _, finishes, _ = _build(monkeypatch, ports=_Ports())
    _invoke(graph, level=2)
    assert [name for name, _ in tools.calls] == [
        "fdc",
        "documents",
        "send_action",
    ]
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]
    document_input = next(value for name, value in tools.calls if name == "documents")
    assert document_input.model_code == "MODEL-1"


def test_level_one_and_two_compare_completion_and_actual_tool_call_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_one, tools_one, _, finishes_one, _ = _build(
        monkeypatch,
        ports=_Ports(),
    )
    _invoke(graph_one, level=1)

    graph_two, tools_two, _, finishes_two, _ = _build(
        monkeypatch,
        ports=_Ports(),
    )
    _invoke(graph_two, level=2)

    assert len(tools_one.calls) == 4 and len(tools_two.calls) == 3
    assert [status for status, _ in finishes_one] == [RunStatus.COMPLETED.value]
    assert [status for status, _ in finishes_two] == [RunStatus.COMPLETED.value]


def test_approval_email_interrupt_keeps_internal_channels_for_resume_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, ports, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(action=ActionCode.EQP_HOLD, decision=Decision.APPROVE),
        interrupt_after=("approval_email",),
    )

    state = _invoke(graph)

    assert ports is not None and ports.calls[-1:] == ["persist_action"]
    assert [name for name, _ in tools.calls][-1:] == ["send_action"]
    assert ports.calls.count("hitl_interrupt") == 0
    assert state["approval_decision"] is None
    assert state["terminal_error"] is None
    assert "autonomy_level" in state
    assert finishes == []


def test_hitl_interrupt_configuration_requires_a_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="HITL_CHECKPOINTER_REQUIRED"):
        _build(
            monkeypatch,
            ports=_Ports(action=ActionCode.EQP_HOLD),
            interrupt_after=("approval_email",),
            durable_interrupt=False,
        )


def test_send_action_failure_is_nonterminal_and_still_interrupts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = _Ports(action=ActionCode.EQP_HOLD)
    tools = _FakeTools()
    tools.send_failure_reason = "TIMEOUT: fixture"
    graph, _, _, finishes, _ = _build(
        monkeypatch,
        tools=tools,
        ports=ports,
        interrupt_after=("approval_email",),
    )
    state = _invoke(graph)
    assert [error.code for error in state["errors"]][-1:] == ["TIMEOUT"]
    assert state["errors"][-1].terminal is False
    assert state["terminal_error"] is None
    assert finishes == []


def test_legacy_approval_email_port_is_not_called_by_delivery_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = _EmailFailurePorts(RuntimeError("raw fixture message"))
    graph, tools, _, finishes, _ = _build(
        monkeypatch,
        ports=ports,
        interrupt_after=("approval_email",),
    )
    state = _invoke(graph)
    assert state["terminal_error"] is None
    assert ports.calls.count("approval_email") == 0
    assert [name for name, _ in tools.calls].count("send_action") == 1
    assert finishes == []


def test_pending_hitl_direct_invoke_preserves_checkpoint_without_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = _PendingHitlPorts()
    graph, _, _, finishes, _ = _build(
        monkeypatch,
        ports=ports,
        interrupt_after=("approval_email",),
    )
    config = {"configurable": {"thread_id": "11111111-2222-3333-4444-555555555555"}}
    _invoke(graph)

    with pytest.raises(HitlResumeError) as caught:
        graph.invoke(None, config=config)
    assert caught.value.code == "APPROVAL_STILL_PENDING"
    snapshot = graph.get_state(config)
    assert snapshot.next == ("hitl_interrupt",)
    assert snapshot.values.get("terminal_error") is None
    assert finishes == []


def test_latency_reads_started_at_inside_the_terminal_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, _, _, transactions = _build(monkeypatch, ports=_Ports())
    _invoke(graph)
    assert transactions[-4:] == ["begin", "get_run", "finish", "commit"]
    assert tools.budget_connections[-1] is tools.finish_connections[-1]


def test_finalize_preserves_action_provenance_in_terminal_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = {
        "schema": "action-provenance-v1",
        "action_policy_version": "ACTION-POLICY-V1",
        "member_alarms": [{"source": "TRACE", "alarm_id": "TA-01"}],
    }
    graph, _, _, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(),
        run_evidence={"action_provenance": provenance},
    )

    _invoke(graph)

    evidence = finishes[0][1]["evidence"]
    assert evidence["action_provenance"] == provenance
    assert evidence["route_consistency"] is True
    # 완료 run에도 인용 대조 기준(graph relation 정본)이 남아야 한다 — 정렬·중복 없음.
    relation_ids = evidence["graph_relation_ids"]
    assert isinstance(relation_ids, list)
    assert relation_ids == sorted(set(relation_ids))
    assert all(isinstance(item, str) and item for item in relation_ids)


def test_failed_finish_paths_share_one_provenance_preserving_helper() -> None:
    tree = ast.parse(inspect.getsource(subject.build_agent_graph))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_finish_failed"
    ]
    assert len(calls) == 2


def test_failed_finish_preserves_existing_action_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = {
        "schema": "action-provenance-v1",
        "action_policy_version": "ACTION-POLICY-V1",
        "member_alarms": [{"source": "TRACE", "alarm_id": "TA-01"}],
    }
    graph, _, _, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(generation_error=True),
        run_evidence={"action_provenance": provenance},
    )

    _invoke(graph)

    assert finishes[0][0] == RunStatus.FAILED.value
    evidence = finishes[0][1]["evidence"]
    assert evidence["action_provenance"] == provenance
    assert evidence["code"] == "LLM_TIMEOUT"


@pytest.mark.parametrize("route", [_route(consistent=False), _route(covered=False)])
def test_level_two_keeps_equipment_when_route_evidence_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
    route: ResolvedIncidentRoute,
) -> None:
    graph, tools, *_ = _build(monkeypatch, level_route=route, ports=_Ports())
    _invoke(graph, level=2)
    assert [name for name, _ in tools.calls] == [
        "fdc",
        "equipment",
        "documents",
        "send_action",
    ]


def test_no_action_skips_persistence_and_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = _Ports(action=None)
    graph, _, _, finishes, _ = _build(monkeypatch, ports=ports)
    state = _invoke(graph)
    assert ports.calls == ["generate_hypothesis", "decide_action"]
    assert state["action_id"] is None
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]


@pytest.mark.parametrize(
    ("decision", "tail"),
    [
        (Decision.APPROVE, ["publish_mes", "writeback_result"]),
        (Decision.REJECT, ["cancel_mes"]),
    ],
)
def test_hold_follows_the_internal_approval_branch(
    monkeypatch: pytest.MonkeyPatch,
    decision: Decision,
    tail: list[str],
) -> None:
    ports = _Ports(action=ActionCode.EQP_HOLD, decision=decision)
    graph, tools, _, finishes, _ = _build(monkeypatch, ports=ports)
    _invoke(graph)
    expected_ports = [
        "hitl_interrupt",
        *[item for item in tail if item != "publish_mes"],
    ]
    assert ports.calls[-len(expected_ports) :] == expected_ports
    expected_sends = 2 if decision is Decision.APPROVE else 1
    assert [name for name, _ in tools.calls].count("send_action") == expected_sends
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]


def test_failed_read_tools_do_not_skip_document_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _FakeTools(fdc_ok=False, equipment_ok=False)
    graph, _, _, finishes, _ = _build(monkeypatch, tools=tools, ports=_Ports())
    state = _invoke(graph)
    assert [name for name, _ in tools.calls] == [
        "fdc",
        "fdc",
        "equipment",
        "documents",
        "send_action",
    ]
    assert [error.code for error in state["errors"]] == [
        "TIMEOUT",
        "TIMEOUT",
        "DEPENDENCY_ERROR",
    ]
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]


def test_budget_block_is_nonterminal_and_reaches_completed_run_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _BudgetBlockedTools()
    graph, _, _, finishes, _ = _build(monkeypatch, tools=tools, ports=_Ports())
    state = _invoke(graph)

    assert "terminal_error" not in state
    assert state["fdc_evidence"] is None
    assert [error.code for error in state["errors"]] == ["TOOL_BUDGET_EXHAUSTED"]
    assert finishes[0][1]["evidence"]["error_codes"] == ["TOOL_BUDGET_EXHAUSTED"]
    assert [name for name, _ in tools.calls] == [
        "fdc-blocked",
        "equipment",
        "documents",
        "send_action",
    ]


def test_safe_node_keeps_budget_block_nonterminal_for_future_tool_nodes() -> None:
    counts = ToolBudgetCounts(
        total=8,
        by_tool={"get_fdc_summary": 4, "send_action": 2, "search_documents": 2},
        pending_reservations=0,
    )

    def blocked(_state: Any) -> dict[str, Any]:
        raise ToolBudgetBlocked("TOOL_BUDGET_EXHAUSTED", counts)

    result = subject._safe_node("send_action", blocked)({"errors": ()})

    assert "terminal_error" not in result
    assert result["tool_budget"].used == 8
    assert [(error.code, error.node, error.terminal) for error in result["errors"]] == [
        ("TOOL_BUDGET_EXHAUSTED", "send_action", False)
    ]


def test_document_query_contains_only_existing_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, *_ = _build(monkeypatch, ports=_Ports())
    _invoke(graph)
    query = next(value for name, value in tools.calls if name == "documents").query
    assert "TRACE" in query
    assert "TA-01" in query
    assert "CT-PHOTO" in query
    assert "PARAM-1" in query
    for forbidden in ("FAULTS", "fault_code", "NRM"):
        assert forbidden not in query


def test_representative_alarm_lot_hist_id_is_the_fdc_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, *_ = _build(monkeypatch, ports=_Ports())
    _invoke(graph)
    assert tools.calls[0][1].lot_hist_id == "LH-REP"


def test_route_dependency_failure_marks_the_started_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, _, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(),
        combine_error=RuntimeError("neo4j://secret"),
    )
    state = _invoke(graph)
    assert tools.calls == []
    assert [status for status, _ in finishes] == [RunStatus.FAILED.value]
    assert state["terminal_error"].code == "NODE_EXECUTION_ERROR"
    assert finishes[0][1]["evidence"] == {"code": "NODE_EXECUTION_ERROR"}


def test_missing_representative_lot_hist_id_is_sanitized_after_run_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, _, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(),
        lot_hist_id_of_member={},
    )

    state = _invoke(graph)

    assert tools.calls == []
    assert state["terminal_error"].code == "ROUTE_INCIDENT_MISMATCH"
    assert [status for status, _ in finishes] == [RunStatus.FAILED.value]


def test_fail_run_persistence_error_never_escapes_or_hides_the_root_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _, _, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(invalid_citation=True),
        finish_error=RuntimeError("postgresql://secret"),
    )

    state = _invoke(graph)

    assert [status for status, _ in finishes] == [RunStatus.FAILED.value]
    assert state["terminal_error"].code == "STATE_CONTRACT_ERROR"
    assert state["errors"][-1].node == "fail_run"
    assert state["errors"][-1].code == "NODE_EXECUTION_ERROR"
    assert "secret" not in repr(state)


def test_entry_transaction_failure_is_sanitized_before_run_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, _, finishes, transactions = _build(
        monkeypatch,
        ports=_Ports(),
        transaction_entry_error=RuntimeError("postgresql://user:secret@db"),
    )

    with pytest.raises(AgentGraphInputError) as exc:
        _invoke(graph)

    assert exc.value.code == "NODE_EXECUTION_ERROR"
    assert "secret" not in repr(exc.value)
    assert tools.calls == []
    assert finishes == []
    assert transactions == ["begin"]


def test_completed_run_is_not_overwritten_when_commit_ack_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _, _, finishes, _ = _build(monkeypatch, ports=_Ports())
    stored_status = RunStatus.RUNNING

    def finish_after_ack_loss(
        connection: Any,
        run_id: str,
        status: RunStatus,
        **kwargs: Any,
    ) -> Any:
        nonlocal stored_status
        finishes.append((status.value, kwargs))
        if status is RunStatus.COMPLETED:
            stored_status = RunStatus.COMPLETED
            raise RuntimeError("commit ack lost: postgresql://secret")
        assert stored_status is RunStatus.COMPLETED
        raise RepositoryConflict("RUN_NOT_ACTIVE")

    monkeypatch.setattr(subject, "finish_agent_run", finish_after_ack_loss)

    state = _invoke(graph)

    assert stored_status is RunStatus.COMPLETED
    assert [status for status, _ in finishes] == [
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
    ]
    assert state["terminal_error"].code == "NODE_EXECUTION_ERROR"
    assert state["errors"][-1].code == "RUN_NOT_ACTIVE"
    assert "secret" not in repr(state)


def test_finalize_contract_failure_never_commits_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, _, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(invalid_citation=True),
    )
    state = _invoke(graph)
    assert [status for status, _ in finishes] == [RunStatus.FAILED.value]
    assert state["terminal_error"].code == "STATE_CONTRACT_ERROR"
    assert tools.llm_usage == [(10, 5)], "downstream 실패가 token을 재가산했다"


def test_generation_failure_usage_is_recorded_once_in_failed_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, _, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(generation_error=True),
    )
    state = _invoke(graph)
    assert state["terminal_error"].code == "LLM_TIMEOUT"
    assert state["pending_llm_usage"].input_tokens == 10
    assert tools.llm_usage == [(10, 5)]
    assert [status for status, _ in finishes] == [RunStatus.FAILED.value]


def test_post_llm_save_failure_moves_usage_to_failed_uow_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, _, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(),
        usage_record_error_once=RepositoryConflict("SAVE_CONFLICT"),
    )
    state = _invoke(graph)
    assert state["terminal_error"].code == "SAVE_CONFLICT"
    assert state["pending_llm_usage"].output_tokens == 5
    assert tools.llm_usage == [(10, 5)]
    assert [status for status, _ in finishes] == [RunStatus.FAILED.value]


@pytest.mark.parametrize(
    ("usage_error", "expected_code"),
    [
        (RepositoryConflict("PREDICTION_CONFLICT"), "PREDICTION_CONFLICT"),
        (RepositoryContractError("RUN_TOKEN_OVERFLOW"), "RUN_TOKEN_OVERFLOW"),
    ],
)
def test_failed_usage_record_never_blocks_failed_terminal_transition(
    monkeypatch: pytest.MonkeyPatch,
    usage_error: Exception,
    expected_code: str,
) -> None:
    graph, tools, _, finishes, transactions = _build(
        monkeypatch,
        ports=_Ports(),
        usage_record_error_always=usage_error,
    )

    state = _invoke(graph)

    assert state["terminal_error"].code == expected_code
    assert [(item.code, item.node) for item in state["errors"]][-2:] == [
        (expected_code, "generate_hypothesis"),
        (expected_code, "fail_run"),
    ]
    assert [status for status, _ in finishes] == [RunStatus.FAILED.value]
    assert len(tools.usage_connections) == 2
    assert all(
        tools.finish_connections[0] is not connection
        for connection in tools.usage_connections
    )
    assert transactions[-3:] == ["get_run", "finish", "commit"]


def test_sequential_replay_restores_prediction_without_llm_or_token_readd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = PredictionRow(
        agent_run_id="RUN-1",
        predicted_fault_code=FaultHypothesis.OTH,
        confidence=0.5,
        cause_summary="stored",
        evidence={
            "schema_version": "agent-evidence-v1",
            "supporting_alarms": [{"source": "TRACE", "alarm_id": "TA-01"}],
            "supporting_chunk_ids": [],
            "supporting_relation_ids": [],
            "uncertainty": "",
        },
        llm_model="fixture-model",
        prompt_version=subject.PROMPT_VERSION,
        created_at=RUN_STARTED_AT,
    )
    ports = _Ports()
    graph, tools, _, finishes, _ = _build(
        monkeypatch,
        ports=ports,
        existing_prediction=existing,
    )
    state = _invoke(graph)
    assert "generate_hypothesis" not in ports.calls
    assert state["hypothesis"].cause_summary == "stored"
    assert tools.llm_usage == []
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]


def test_unwired_ports_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, _, _, finishes, _ = _build(monkeypatch, ports=None)
    state = _invoke(graph)
    assert [status for status, _ in finishes] == [RunStatus.FAILED.value]
    assert state["terminal_error"].code == "PORT_NOT_WIRED"


def test_level_three_is_rejected_before_any_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _, _, finishes, transactions = _build(monkeypatch, ports=_Ports())
    with pytest.raises(AgentGraphInputError) as exc:
        _invoke(graph, level=3)
    assert exc.value.code == "AUTONOMY_LEVEL_NOT_IMPLEMENTED"
    assert finishes == []
    assert transactions == []


def test_production_thread_is_bound_to_input_config_and_start_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    graph, *_ = _build(
        monkeypatch,
        ports=_Ports(),
        interrupt_after=("load_incident", "approval_email"),
        require_bound_thread=True,
        configured_llm_model="configured-model",
        start_calls=calls,
    )
    thread_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

    graph.invoke(
        {
            "requested_alarm": ALARM,
            "autonomy_level": 2,
            "thread_id": thread_id,
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})

    assert len(calls) == 1
    assert calls[0]["thread_id_factory"]() == thread_id
    assert calls[0]["llm_model"] == "configured-model"
    assert tuple(snapshot.next) == ("collect_fdc",)


def test_production_thread_mismatch_fails_before_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    graph, _, _, _, transactions = _build(
        monkeypatch,
        ports=_Ports(),
        interrupt_after=("load_incident", "approval_email"),
        require_bound_thread=True,
        configured_llm_model="configured-model",
        start_calls=calls,
    )

    with pytest.raises(AgentGraphInputError) as caught:
        graph.invoke(
            {
                "requested_alarm": ALARM,
                "autonomy_level": 2,
                "thread_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            },
            config={
                "configurable": {"thread_id": "11111111-2222-3333-4444-555555555555"}
            },
        )

    assert caught.value.code == "THREAD_BINDING_MISMATCH"
    assert calls == []
    assert transactions == []


@pytest.mark.parametrize("invalid_level", [True, 1.0, "1", 0, None])
def test_non_integer_or_out_of_range_levels_are_rejected_before_db(
    monkeypatch: pytest.MonkeyPatch,
    invalid_level: Any,
) -> None:
    graph, _, _, finishes, transactions = _build(monkeypatch, ports=_Ports())
    with pytest.raises(AgentGraphInputError) as exc:
        graph.invoke({"requested_alarm": ALARM, "autonomy_level": invalid_level})
    assert exc.value.code == "AUTONOMY_LEVEL_INVALID"
    assert finishes == []
    assert transactions == []


@pytest.mark.parametrize(
    "payload",
    [
        {"autonomy_level": 1},
        {"requested_alarm": {"source": "TRACE"}, "autonomy_level": 1},
    ],
)
def test_missing_or_malformed_alarm_is_rejected_before_db(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    graph, _, _, finishes, transactions = _build(monkeypatch, ports=_Ports())
    with pytest.raises(AgentGraphInputError) as exc:
        graph.invoke(payload)
    assert exc.value.code == "REQUESTED_ALARM_INVALID"
    assert finishes == []
    assert transactions == []
