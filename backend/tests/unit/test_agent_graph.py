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

from app.agent import graph as subject
from app.agent import incident_repository, routing_repository
from app.agent.graph import (
    CANONICAL_NODES,
    INTERNAL_NODES,
    AgentGraphDependencies,
    AgentGraphInputError,
    build_agent_graph,
)
from app.agent.incident import ResolvedIncident
from app.agent.repository import RepositoryConflict, ToolBudgetCounts
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
)
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    ParameterSummaryItem,
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
        self._fdc_ok = fdc_ok
        self._equipment_ok = equipment_ok

    def budget(self, run_id: str) -> ToolBudget:
        return ToolBudget(used=len(self.calls))

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

    def __post_init__(self) -> None:
        self.calls: list[str] = []

    def generate_hypothesis(self, fdc: Any, graph: Any, docs: Any, route: Any) -> Any:
        self.calls.append("generate_hypothesis")
        return Hypothesis(
            predicted_fault_code=FaultHypothesis.OTH,
            confidence=0.5,
            cause_summary="fixture",
            supporting_chunk_ids=("MISSING",) if self.invalid_citation else (),
            uncertainty="",
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
        )

    def persist_action(self, run_id: str, decision: ActionDecision) -> PersistResult:
        self.calls.append("persist_action")
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

    def approval_email(self, action_id: str, approval_id: str) -> None:
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
) -> tuple[Any, _FakeTools, _Ports | None, list[tuple[str, Any]], list[str]]:
    route = level_route or _route()
    tool_set = tools or _FakeTools()
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
    )
    monkeypatch.setattr(subject, "start_incident_run", lambda *args, **kwargs: started)
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
        finish_events.append((status.value, kwargs))
        if finish_error is not None:
            raise finish_error
        return SimpleNamespace()

    def get_run(connection: Any, run_id: str) -> Any:
        transaction_events.append("get_run")
        return SimpleNamespace(started_at=RUN_STARTED_AT)

    monkeypatch.setattr(subject, "finish_agent_run", finish)
    monkeypatch.setattr(subject, "get_agent_run", get_run)
    graph = build_agent_graph(
        AgentGraphDependencies(
            transactions=transactions,
            tools=tool_set,  # type: ignore[arg-type]
            routing_graph=GraphBoundary(lambda value: None, lambda value: None),
            ports=ports,
            now=(lambda: RUN_STARTED_AT) if now is None else now,
        ),
        interrupt_after=interrupt_after,
    )
    return graph, tool_set, ports, finish_events, transaction_events


def _invoke(graph: Any, level: int = 1) -> dict[str, Any]:
    return graph.invoke({"requested_alarm": ALARM, "autonomy_level": level})


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


def test_graph_has_exactly_fourteen_canonical_and_one_internal_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, *_ = _build(monkeypatch)
    names = set(graph.get_graph().nodes) - {"__start__", "__end__"}
    assert names == set(CANONICAL_NODES) | set(INTERNAL_NODES)
    assert len(CANONICAL_NODES) == 14
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
    assert [name for name, _ in tools.calls] == ["fdc", "equipment", "documents"]
    assert ports.calls == [
        "generate_hypothesis",
        "decide_action",
        "persist_action",
        "notify_email",
    ]
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]
    assert state["action_id"] == "ACT-1"
    assert state["tool_budget"].used == 3
    assert set(state) == set(subject.CompletedAgentState.model_fields)
    assert not {
        "autonomy_level",
        "terminal_error",
        "fdc_lot_hist_id",
        "approval_decision",
    } & set(state)


def test_level_two_skips_only_redundant_equipment_context_and_keeps_model_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, tools, _, finishes, _ = _build(monkeypatch, ports=_Ports())
    _invoke(graph, level=2)
    assert [name for name, _ in tools.calls] == ["fdc", "documents"]
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]
    document_input = tools.calls[-1][1]
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

    assert len(tools_one.calls) == 3 and len(tools_two.calls) == 2
    assert [status for status, _ in finishes_one] == [RunStatus.COMPLETED.value]
    assert [status for status, _ in finishes_two] == [RunStatus.COMPLETED.value]


def test_interrupt_result_keeps_internal_channels_for_resume_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _, ports, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(action=ActionCode.EQP_HOLD, decision=Decision.APPROVE),
        interrupt_after=("hitl_interrupt",),
    )

    state = _invoke(graph)

    assert ports is not None and ports.calls[-2:] == [
        "approval_email",
        "hitl_interrupt",
    ]
    assert state["approval_decision"] is Decision.APPROVE
    assert state["terminal_error"] is None
    assert "autonomy_level" in state
    assert finishes == []


def test_latency_reads_started_at_inside_the_terminal_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _, _, _, transactions = _build(monkeypatch, ports=_Ports())
    _invoke(graph)
    assert transactions[-4:] == ["begin", "get_run", "finish", "commit"]


@pytest.mark.parametrize("route", [_route(consistent=False), _route(covered=False)])
def test_level_two_keeps_equipment_when_route_evidence_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
    route: ResolvedIncidentRoute,
) -> None:
    graph, tools, *_ = _build(monkeypatch, level_route=route, ports=_Ports())
    _invoke(graph, level=2)
    assert [name for name, _ in tools.calls] == ["fdc", "equipment", "documents"]


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
    graph, _, _, finishes, _ = _build(monkeypatch, ports=ports)
    _invoke(graph)
    assert ports.calls[-(len(tail) + 2) :] == [
        "approval_email",
        "hitl_interrupt",
        *tail,
    ]
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]


def test_failed_read_tools_do_not_skip_document_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _FakeTools(fdc_ok=False, equipment_ok=False)
    graph, _, _, finishes, _ = _build(monkeypatch, tools=tools, ports=_Ports())
    state = _invoke(graph)
    assert [name for name, _ in tools.calls] == ["fdc", "equipment", "documents"]
    assert [error.code for error in state["errors"]] == ["TIMEOUT", "DEPENDENCY_ERROR"]
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
    graph, _, _, finishes, _ = _build(
        monkeypatch,
        ports=_Ports(invalid_citation=True),
    )
    state = _invoke(graph)
    assert [status for status, _ in finishes] == [RunStatus.FAILED.value]
    assert state["terminal_error"].code == "STATE_CONTRACT_ERROR"


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
