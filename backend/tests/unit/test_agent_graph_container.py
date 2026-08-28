"""`V5-C-2.1`의 실제 PostgreSQL checkpoint 및 조립 E2E.

조립 E2E는 C 소유 DB 경계와 Tool 감사를 실제 PostgreSQL 16으로 검증한다. 기본 회귀는
결정론적 FDC fixture를 쓰고, production 조립 회귀는 A의 실제 FDC Tool만 격리 DB에 연결한
채 후속 C Task의 business port를 fixture로 둔다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
for candidate in (BACKEND_ROOT, BACKEND_ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import postgres_transition as transition  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402

from app.agent import action_store as action_store_module  # noqa: E402
from app.agent import approval_store as approval_store_module  # noqa: E402
from app.agent import checkpoint as ck  # noqa: E402
from app.agent import graph as graph_module  # noqa: E402
from app.agent import repository as repo  # noqa: E402
from app.agent.graph import (  # noqa: E402
    AgentGraphDependencies,
    build_agent_graph,
)
from app.agent.hypothesis import production_port  # noqa: E402
from app.agent.repository import reserve_tool_call  # noqa: E402
from app.agent.routing import GraphBoundary  # noqa: E402
from app.agent.run_guard import start_incident_run  # noqa: E402
from app.agent.state import (  # noqa: E402
    ActionDecision,
    DeliveryPlan,
    Hypothesis,
    HypothesisOutcome,
    LlmUsage,
    PersistResult,
    ToolBudget,
)
from app.agent.tools import (  # noqa: E402
    AuditedToolExecutor,
    ToolBoundary,
    ToolBudgetBlocked,
)
from app.common.enums import (  # noqa: E402
    ActionCode,
    AlarmSource,
    ApprovalStatus,
    Decision,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    Severity,
    ThresholdValidationStatus,
)
from app.common.llm import ChatCompletion  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402
from app.common.tool_contracts import (  # noqa: E402
    AnomalySignal,
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    ParameterSummaryItem,
    WaferContext,
)
from app.knowledge.schemas import (  # noqa: E402
    ChamberRelationResponse,
    GraphNode,
    GraphRelationship,
)

pytestmark = pytest.mark.container

REPOSITORY_ROOT = BACKEND_ROOT.parent
TARGET_DATABASE = "kosa_agent_e2e"
FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"
V5_SQL = (
    REPOSITORY_ROOT
    / "backend"
    / "migrations"
    / "v5"
    / ("001_reference_extensions_final.sql")
)
SQL_002 = REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
SQL_003 = (
    REPOSITORY_ROOT / "backend" / "migrations" / ("003_agent_run_severity_pair.sql")
)
T0 = datetime(2026, 8, 1, 10, 0, 0)
ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")

_WAFER_ALTER = "\n".join(
    f"ALTER TABLE {table} ALTER COLUMN wafer TYPE varchar(24) "
    f"USING wafer::varchar(24);"
    for table in transition.WAFER_ALTER_TABLES
)


@pytest.fixture(scope="module")
def runtime() -> tuple[Any, Any]:
    """base 9 + V5 Runtime + checkpoint를 한 PostgreSQL 16에 세운다."""

    with postgres.one_off_postgres(database=TARGET_DATABASE) as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=TARGET_DATABASE,
            user=endpoint.username,
            password=endpoint.password,
        ) as raw:
            cursor = raw.cursor()
            cursor.execute(
                (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
            )
            cursor.execute(_WAFER_ALTER)
            cursor.execute(V5_SQL.read_text(encoding="utf-8"))
            cursor.execute(SQL_002.read_text(encoding="utf-8"))
            cursor.execute(SQL_003.read_text(encoding="utf-8"))
            raw.commit()
        engine = create_engine(
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{TARGET_DATABASE}"
        )
        try:
            yield endpoint, engine
        finally:
            engine.dispose()


def _fdc_result(*, lot_hist_id: str = "LH-REP") -> FdcSummaryToolResult:
    anomaly = AnomalySignal(
        score=0.7,
        model_version="model-1",
        score_method="iforest",
        threshold_validation_status=ThresholdValidationStatus.UNVERIFIED,
    )
    return FdcSummaryToolResult(
        ok=True,
        wafer=WaferContext(
            lot_hist_id=lot_hist_id,
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


class _DirectRunner:
    def call(self, fn: Any, payload: dict[str, Any], *, seconds: float) -> Any:
        assert seconds > 0
        return fn(payload)


class _AssemblyPorts:
    """후속 C Task의 business 구현이 아닌 결정론적 조립 fixture."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_hypothesis(self, *_args: Any) -> HypothesisOutcome:
        self.calls.append("generate_hypothesis")
        return HypothesisOutcome(
            hypothesis=Hypothesis(
                predicted_fault_code=FaultHypothesis.OTH,
                confidence=0.5,
                cause_summary="fixture",
                supporting_alarms=(
                    AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01"),
                ),
                supporting_relation_ids=("REL-PART",),
                uncertainty="",
            ),
            llm_usage=LlmUsage(
                model="fixture-model",
                prompt_version=graph_module.PROMPT_VERSION,
                input_tokens=10,
                output_tokens=5,
            ),
        )

    def decide_action(self, _route: Any) -> ActionDecision:
        self.calls.append("decide_action")
        return ActionDecision(
            action=ActionCode.MONITORING,
            severity=Severity.LOW,
            requires_approval=False,
            matched_rule="SUMMARY_OOC_ONLY",
            policy_version="ACTION-POLICY-V1",
        )

    def persist_action(self, _run_id: str, _decision: ActionDecision) -> PersistResult:
        self.calls.append("persist_action")
        return PersistResult(action_id="ACT-FIXTURE")

    def notify_email(self, _action_id: str) -> None:
        self.calls.append("notify_email")
        return None

    def approval_email(
        self,
        _run_id: str,
        _action_id: str,
        _approval_id: str,
    ) -> None:
        self.calls.append("approval_email")
        return None

    def hitl_interrupt(self, _approval_id: str) -> Decision:
        self.calls.append("hitl_interrupt")
        return Decision.APPROVE

    def publish_mes(self, _action_id: str) -> None:
        self.calls.append("publish_mes")
        return None

    def writeback_result(self, _action_id: str) -> tuple[DeliveryPlan, ...]:
        self.calls.append("writeback_result")
        return ()

    def cancel_mes(self, _action_id: str) -> tuple[DeliveryPlan, ...]:
        self.calls.append("cancel_mes")
        return ()


class _HoldAssemblyPorts(_AssemblyPorts):
    def decide_action(self, _route: Any) -> ActionDecision:
        self.calls.append("decide_action")
        return ActionDecision(
            action=ActionCode.EQP_HOLD,
            severity=Severity.HIGH,
            requires_approval=True,
            matched_rule="R03_PRESENT",
            policy_version="ACTION-POLICY-V1",
        )

    def persist_action(self, _run_id: str, _decision: ActionDecision) -> PersistResult:
        self.calls.append("persist_action")
        return PersistResult(
            action_id="ACT-FIXTURE",
            approval_id="APR-FIXTURE",
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

    def writeback_result(self, _action_id: str) -> tuple[DeliveryPlan, ...]:
        self.calls.append("writeback_result")
        return (
            DeliveryPlan(
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.SENT,
            ),
            DeliveryPlan(
                channel=DeliveryChannel.MES_MOCK,
                status=DeliveryStatus.SENT,
            ),
        )


def _node(label: str, business_id: str) -> GraphNode:
    return GraphNode(
        id=f"{label}:{business_id}",
        label=label,
        business_id=business_id,
        display_name=business_id,
        properties={},
    )


def _relation(
    relation_id: str,
    kind: str,
    source: tuple[str, str],
    target: tuple[str, str],
) -> GraphRelationship:
    return GraphRelationship(
        id=relation_id,
        type=kind,
        source=f"{source[0]}:{source[1]}",
        target=f"{target[0]}:{target[1]}",
    )


def _routing_graph() -> Any:
    chamber = _node("Chamber", "EQP01-PM1")
    equipment = _node("Equipment", "EQP01")
    step = _node("ProcessStep", "CT-PHOTO")
    relations = [
        _relation(
            "REL-PART",
            "PART_OF",
            ("Chamber", "EQP01-PM1"),
            ("Equipment", "EQP01"),
        ),
        _relation(
            "REL-PERFORMS",
            "PERFORMS",
            ("Equipment", "EQP01"),
            ("ProcessStep", "CT-PHOTO"),
        ),
    ]
    context = EquipmentContextToolResult(
        ok=True,
        chamber_id="EQP01-PM1",
        equipment_id="EQP01",
        area="etch",
        model_code="MODEL-1",
        process_step_id="CT-PHOTO",
        graph_revision="rev-1",
    )
    projection = ChamberRelationResponse(
        root_node_id=chamber.id,
        nodes=[chamber, equipment, step],
        relationships=relations,
        graph_revision="rev-1",
    )
    return GraphBoundary(
        equipment_context=lambda chamber_id: context,
        chamber_relations=lambda chamber_id: projection,
    )


def _seed_runtime(engine: Any) -> None:
    with engine.begin() as connection:
        for table in (
            "agent_run",
            "action_history",
            "audit_log",
            "trace_alarm_history",
            "lot_history",
        ):
            connection.execute(text(f"TRUNCATE {table} CASCADE"))
        connection.execute(
            text(
                "INSERT INTO dim_parameter "
                "(parameter_id, parameter_name, unit, area, target_value, "
                " spec_lower, ctrl_lower, ctrl_upper, spec_upper, upper_only) "
                "VALUES ('PARAM01', 'Pressure', 'psi', 'etch', 10.0, "
                " 8.0, 9.0, 11.0, 12.0, false) "
                "ON CONFLICT (parameter_id) DO UPDATE SET "
                "parameter_name = EXCLUDED.parameter_name, unit = EXCLUDED.unit, "
                "area = EXCLUDED.area, target_value = EXCLUDED.target_value, "
                "spec_lower = EXCLUDED.spec_lower, ctrl_lower = EXCLUDED.ctrl_lower, "
                "ctrl_upper = EXCLUDED.ctrl_upper, spec_upper = EXCLUDED.spec_upper, "
                "upper_only = EXCLUDED.upper_only"
            )
        )
        connection.execute(
            text(
                "INSERT INTO lot_history "
                "(lot_hist_id, lot_id, wafer_no, wafer_id, chamber_id, step_id, "
                " area_id, equipment_id, recipe_id, track_in_at) VALUES "
                "('LH-REP', 'LOT001', 1, 'LOT001W001', 'EQP01-PM1', "
                " 'CT-PHOTO', 'etch', 'EQP01', 'RECIPE01', :occurred_at)"
            ),
            {"occurred_at": T0},
        )
        connection.execute(
            text(
                "INSERT INTO summary_data "
                "(lot_hist_id, area, equipment, chamber, parameter, recipe, lot, "
                " wafer, step_no, step_seq, value_mean, value_std, value_min, "
                " value_max, point_cnt) VALUES "
                "('LH-REP', 'etch', 'EQP01', 'EQP01-PM1', 'PARAM01', "
                " 'RECIPE01', 'LOT001', 1, 1, 1, 10.0, 0.5, 9.5, 10.5, 6)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO evaluation "
                "(lot_hist_id, area, equipment, chamber, parameter, recipe, lot, "
                " wafer, step_no, step_seq, point_cnt, ooc_point_cnt, "
                " oos_point_cnt, alarm_type) VALUES "
                "('LH-REP', 'etch', 'EQP01', 'EQP01-PM1', 'PARAM01', "
                " 'RECIPE01', 'LOT001', 1, 1, 1, 6, 0, 0, 'IN')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO trace_alarm_history "
                "(alarm_id, occurred_at, area, equipment, chamber, parameter, "
                " recipe, lot, wafer, step_no) VALUES "
                "('TA-01', :occurred_at, 'etch', 'EQP01', 'EQP01-PM1', "
                " 'PARAM01', 'RECIPE01', 'LOT001', 'LOT001W001', 1)"
            ),
            {"occurred_at": T0},
        )


def _dependencies(
    engine: Any,
    ports: _AssemblyPorts,
    *,
    fdc_summary: Callable[[dict[str, Any]], Any] | None = None,
) -> AgentGraphDependencies:
    equipment = EquipmentContextToolResult(
        ok=True,
        chamber_id="EQP01-PM1",
        equipment_id="EQP01",
        area="etch",
        model_code="MODEL-1",
        process_step_id="CT-PHOTO",
        graph_revision="rev-1",
    )
    boundary = ToolBoundary(
        fdc_summary=fdc_summary or (lambda payload: _fdc_result()),
        equipment_context=lambda payload: equipment,
        document_search=lambda payload: DocumentSearchToolResult(ok=True, hits=[]),
    )
    return AgentGraphDependencies(
        transactions=engine.begin,
        tools=AuditedToolExecutor(
            transactions=engine.begin,
            boundary=boundary,
            deadline_runner=_DirectRunner(),
        ),
        routing_graph=_routing_graph(),
        ports=ports,
    )


def _start_runtime_run(engine: Any) -> str:
    with engine.begin() as connection:
        started = start_incident_run(
            connection,
            AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01"),
            autonomy_level=2,
        )
    return started.run.agent_run_id


def _seed_reserved_calls(engine: Any, run_id: str, names: tuple[str, ...]) -> None:
    for index, name in enumerate(names, start=1):
        with engine.begin() as connection:
            reserve_tool_call(
                connection,
                agent_run_id=run_id,
                tool_name=name,
                input={"fixture": index},
            )


def _race_budget_reservation(
    executor: AuditedToolExecutor,
    run_id: str,
    tool_name: str,
) -> list[str]:
    barrier = Barrier(2)

    def attempt(index: int) -> str:
        barrier.wait()
        try:
            executor._reserve_within_budget(
                agent_run_id=run_id,
                tool_name=tool_name,
                request={"candidate": index},
            )
        except ToolBudgetBlocked as exc:
            return exc.code
        return "RESERVED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        return sorted(pool.map(attempt, (1, 2)))


@contextmanager
def _checkpoint_connection(endpoint: Any) -> Any:
    with psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=TARGET_DATABASE,
        user=endpoint.username,
        password=endpoint.password,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    ) as connection:
        yield connection


def test_actual_graph_resumes_after_hitl_from_a_new_postgres_saver(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, engine = runtime
    _seed_runtime(engine)
    thread_id = str(uuid4())

    def fixed_start(connection: Any, requested_alarm: AlarmRef, **kwargs: Any) -> Any:
        return start_incident_run(
            connection,
            requested_alarm,
            thread_id_factory=lambda: thread_id,
            **kwargs,
        )

    monkeypatch.setattr(graph_module, "start_incident_run", fixed_start)
    config = ck.build_thread_config(thread_id)
    first_ports = _HoldAssemblyPorts()
    first_ports.persist_action = action_store_module.production_port(  # type: ignore[method-assign]
        engine.begin
    )
    with _checkpoint_connection(endpoint) as connection:
        PostgresSaver(connection).setup()
        first_graph = build_agent_graph(
            _dependencies(engine, first_ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        interrupted = first_graph.invoke(
            {
                "requested_alarm": AlarmRef(
                    source=AlarmSource.TRACE,
                    alarm_id="TA-01",
                ),
                "autonomy_level": 2,
            },
            config=config,
        )
        checkpoint = first_graph.get_state(config).values

    assert interrupted["approval_decision"] is None
    assert interrupted["terminal_error"] is None
    assert "autonomy_level" in interrupted
    assert checkpoint.get("approval_decision") is None
    assert ToolBudget.model_validate(checkpoint["tool_budget"]).used == 2
    assert first_ports.calls[-1:] == ["approval_email"]
    assert first_ports.calls.count("hitl_interrupt") == 0
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM agent_run WHERE thread_id = :thread_id"),
                {"thread_id": thread_id},
            ).scalar_one()
            == RunStatus.WAITING_APPROVAL.value
        )

    approval_store_module.decision_port(engine.begin)(
        interrupted["approval_id"],
        Decision.APPROVE,
        "operator",
    )

    fresh_tools = _dependencies(engine, _AssemblyPorts()).tools
    for index in range(3):
        result = fresh_tools.document_search(
            checkpoint["run_id"],
            DocumentSearchToolInput(query=f"resume-{index}"),
        )
        assert result is not None and result.ok
    with pytest.raises(ToolBudgetBlocked) as blocked:
        fresh_tools.document_search(
            checkpoint["run_id"],
            DocumentSearchToolInput(query="resume-blocked"),
        )
    assert blocked.value.code == "TOOL_RETRY_EXHAUSTED"
    assert blocked.value.budget.used == 5

    second_ports = _HoldAssemblyPorts()
    second_ports.hitl_interrupt = approval_store_module.hitl_decision_port(  # type: ignore[method-assign]
        engine.begin
    )
    with _checkpoint_connection(endpoint) as connection:
        resumed_graph = build_agent_graph(
            _dependencies(engine, second_ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        resumed = approval_store_module.resume_after_approval(
            resumed_graph,
            engine.begin,
            engine.connect,
            thread_id,
        )

    with engine.connect() as connection:
        run = connection.execute(
            text(
                "SELECT status, latency_ms FROM agent_run "
                "WHERE thread_id = :thread_id"
            ),
            {"thread_id": thread_id},
        ).one()

    assert second_ports.calls == ["publish_mes", "writeback_result"]
    assert resumed["action_decision"].action is ActionCode.EQP_HOLD
    # 중단 checkpoint는 2건이지만 완료 State는 terminal transaction에서
    # DB 5건을 다시 읽는다.
    assert resumed["tool_budget"].used == 5
    assert Decision(resumed["approval_decision"]) is Decision.APPROVE
    assert run.status == RunStatus.COMPLETED.value
    assert run.latency_ms is not None and run.latency_ms >= 0


def test_recovery_catches_up_a_pre_persist_crash_without_a_new_run(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, engine = runtime
    _seed_runtime(engine)
    thread_id = str(uuid4())

    def fixed_start(connection: Any, requested_alarm: AlarmRef, **kwargs: Any) -> Any:
        return start_incident_run(
            connection,
            requested_alarm,
            thread_id_factory=lambda: thread_id,
            **kwargs,
        )

    monkeypatch.setattr(graph_module, "start_incident_run", fixed_start)
    config = ck.build_thread_config(thread_id)
    with _checkpoint_connection(endpoint) as connection:
        PostgresSaver(connection).setup()
        first = build_agent_graph(
            _dependencies(engine, _HoldAssemblyPorts()),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("decide_action",),
        )
        first.invoke(
            {"requested_alarm": ALARM, "autonomy_level": 2},
            config=config,
        )
        before = first.get_state(config)

    assert before.next == ("persist_action",)
    run_id = before.values["run_id"]
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM agent_run WHERE agent_run_id = :id"),
                {"id": run_id},
            ).scalar_one()
            == RunStatus.RUNNING.value
        )
        assert (
            connection.execute(text("SELECT count(*) FROM action_history")).scalar_one()
            == 0
        )

    ports = _HoldAssemblyPorts()
    ports.persist_action = action_store_module.production_port(engine.begin)  # type: ignore[method-assign]
    with _checkpoint_connection(endpoint) as connection:
        recovered_graph = build_agent_graph(
            _dependencies(engine, ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        recovered = approval_store_module.recover_hitl_run(
            recovered_graph,
            engine.begin,
            engine.connect,
            run_id,
        )

    assert recovered["run_id"] == run_id
    assert recovered["thread_id"] == thread_id
    assert ports.calls.count("approval_email") == 1
    assert ports.calls.count("hitl_interrupt") == 0
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status, (SELECT count(*) FROM action_history) AS actions "
                "FROM agent_run WHERE agent_run_id = :id"
            ),
            {"id": run_id},
        ).one()
    assert (row.status, row.actions) == (RunStatus.WAITING_APPROVAL.value, 1)


def test_recovery_catches_up_an_approval_email_checkpoint(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, engine = runtime
    _seed_runtime(engine)
    thread_id = str(uuid4())

    def fixed_start(connection: Any, requested_alarm: AlarmRef, **kwargs: Any) -> Any:
        return start_incident_run(
            connection,
            requested_alarm,
            thread_id_factory=lambda: thread_id,
            **kwargs,
        )

    monkeypatch.setattr(graph_module, "start_incident_run", fixed_start)
    config = ck.build_thread_config(thread_id)
    first_ports = _HoldAssemblyPorts()
    first_ports.persist_action = action_store_module.production_port(  # type: ignore[method-assign]
        engine.begin
    )
    with _checkpoint_connection(endpoint) as connection:
        PostgresSaver(connection).setup()
        first = build_agent_graph(
            _dependencies(engine, first_ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("persist_action",),
        )
        first.invoke(
            {"requested_alarm": ALARM, "autonomy_level": 2},
            config=config,
        )
        checkpoint = first.get_state(config)

    assert checkpoint.next == ("approval_email",)
    run_id = checkpoint.values["run_id"]
    email_calls: list[tuple[str, str]] = []
    ports = _HoldAssemblyPorts()
    ports.approval_email = approval_store_module.approval_email_port(  # type: ignore[method-assign]
        engine.begin,
        lambda action_id, approval_id: email_calls.append((action_id, approval_id)),
    )
    with _checkpoint_connection(endpoint) as connection:
        recovered_graph = build_agent_graph(
            _dependencies(engine, ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        recovered = approval_store_module.recover_hitl_run(
            recovered_graph,
            engine.begin,
            engine.connect,
            run_id,
        )

    assert recovered["run_id"] == run_id
    assert len(email_calls) == 1
    assert ports.calls.count("hitl_interrupt") == 0
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM agent_run WHERE agent_run_id = :id"),
                {"id": run_id},
            ).scalar_one()
            == RunStatus.WAITING_APPROVAL.value
        )


def test_recovery_replays_a_terminal_bundle_and_skips_the_late_email(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, engine = runtime
    _seed_runtime(engine)
    thread_id = str(uuid4())

    def fixed_start(connection: Any, requested_alarm: AlarmRef, **kwargs: Any) -> Any:
        return start_incident_run(
            connection,
            requested_alarm,
            thread_id_factory=lambda: thread_id,
            **kwargs,
        )

    monkeypatch.setattr(graph_module, "start_incident_run", fixed_start)
    config = ck.build_thread_config(thread_id)
    with _checkpoint_connection(endpoint) as connection:
        PostgresSaver(connection).setup()
        first = build_agent_graph(
            _dependencies(engine, _HoldAssemblyPorts()),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("decide_action",),
        )
        first.invoke(
            {"requested_alarm": ALARM, "autonomy_level": 2},
            config=config,
        )
        checkpoint = first.get_state(config)

    run_id = checkpoint.values["run_id"]
    decision = ActionDecision.model_validate(checkpoint.values["action_decision"])
    bundle = action_store_module.production_port(engine.begin)(run_id, decision)
    assert bundle.approval_id is not None
    approval_store_module.decision_port(engine.begin)(
        bundle.approval_id,
        Decision.APPROVE,
        "operator",
    )

    email_calls: list[tuple[str, str]] = []
    ports = _HoldAssemblyPorts()
    ports.persist_action = action_store_module.production_port(engine.begin)  # type: ignore[method-assign]
    ports.approval_email = approval_store_module.approval_email_port(  # type: ignore[method-assign]
        engine.begin,
        lambda action_id, approval_id: email_calls.append((action_id, approval_id)),
    )
    ports.hitl_interrupt = approval_store_module.hitl_decision_port(  # type: ignore[method-assign]
        engine.begin
    )
    with _checkpoint_connection(endpoint) as connection:
        recovered_graph = build_agent_graph(
            _dependencies(engine, ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        recovered = approval_store_module.recover_hitl_run(
            recovered_graph,
            engine.begin,
            engine.connect,
            run_id,
        )

    assert recovered["run_id"] == run_id
    assert Decision(recovered["approval_decision"]) is Decision.APPROVE
    assert email_calls == []
    assert ports.calls == ["publish_mes", "writeback_result"]
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM agent_run WHERE agent_run_id = :id"),
                {"id": run_id},
            ).scalar_one()
            == RunStatus.COMPLETED.value
        )


def test_recovery_without_a_checkpoint_is_fail_closed(
    runtime: tuple[Any, Any],
) -> None:
    endpoint, engine = runtime
    _seed_runtime(engine)
    run_id = _start_runtime_run(engine)
    bundle = action_store_module.production_port(engine.begin)(
        run_id,
        ActionDecision(
            action=ActionCode.EQP_HOLD,
            severity=Severity.HIGH,
            requires_approval=True,
            matched_rule="R03_PRESENT",
        ),
    )
    ports = _HoldAssemblyPorts()
    with _checkpoint_connection(endpoint) as connection:
        PostgresSaver(connection).setup()
        graph = build_agent_graph(
            _dependencies(engine, ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        with pytest.raises(approval_store_module.HitlResumeError) as caught:
            approval_store_module.recover_hitl_run(
                graph,
                engine.begin,
                engine.connect,
                run_id,
            )
    assert caught.value.code == "CHECKPOINT_MISSING"
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT r.status, p.status AS approval_status, "
                "(SELECT count(*) FROM action_history) AS actions "
                "FROM agent_run r JOIN approval_request p "
                "ON p.agent_run_id = r.agent_run_id "
                "WHERE r.agent_run_id = :id"
            ),
            {"id": run_id},
        ).one()
    assert bundle.approval_id is not None
    assert (row.status, row.approval_status, row.actions) == (
        RunStatus.WAITING_APPROVAL.value,
        ApprovalStatus.PENDING.value,
        1,
    )


def test_recovery_continues_after_resume_cas_committed_before_invoke(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, engine = runtime
    _seed_runtime(engine)
    thread_id = str(uuid4())

    def fixed_start(connection: Any, requested_alarm: AlarmRef, **kwargs: Any) -> Any:
        return start_incident_run(
            connection,
            requested_alarm,
            thread_id_factory=lambda: thread_id,
            **kwargs,
        )

    monkeypatch.setattr(graph_module, "start_incident_run", fixed_start)
    config = ck.build_thread_config(thread_id)
    first_ports = _HoldAssemblyPorts()
    first_ports.persist_action = action_store_module.production_port(  # type: ignore[method-assign]
        engine.begin
    )
    with _checkpoint_connection(endpoint) as connection:
        PostgresSaver(connection).setup()
        first = build_agent_graph(
            _dependencies(engine, first_ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        interrupted = first.invoke(
            {"requested_alarm": ALARM, "autonomy_level": 2},
            config=config,
        )

    run_id = interrupted["run_id"]
    approval_id = interrupted["approval_id"]
    approval_store_module.decision_port(engine.begin)(
        approval_id,
        Decision.APPROVE,
        "operator",
    )
    # 첫 소유자가 여기까지 commit하고 graph.invoke 전에 죽은 상황을 재현한다.
    with engine.begin() as connection:
        repo.resume_from_approval(
            connection,
            agent_run_id=run_id,
            approval_id=approval_id,
        )

    ports = _HoldAssemblyPorts()
    ports.hitl_interrupt = approval_store_module.hitl_decision_port(  # type: ignore[method-assign]
        engine.begin
    )
    with _checkpoint_connection(endpoint) as connection:
        recovered_graph = build_agent_graph(
            _dependencies(engine, ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        recovered = approval_store_module.recover_hitl_run(
            recovered_graph,
            engine.begin,
            engine.connect,
            run_id,
        )

    assert Decision(recovered["approval_decision"]) is Decision.APPROVE
    assert ports.calls == ["publish_mes", "writeback_result"]
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM agent_run WHERE agent_run_id = :id"),
                {"id": run_id},
            ).scalar_one()
            == RunStatus.COMPLETED.value
        )


def test_hitl_read_can_repeat_after_a_crash_before_node_checkpoint(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, engine = runtime
    _seed_runtime(engine)
    thread_id = str(uuid4())

    def fixed_start(connection: Any, requested_alarm: AlarmRef, **kwargs: Any) -> Any:
        return start_incident_run(
            connection,
            requested_alarm,
            thread_id_factory=lambda: thread_id,
            **kwargs,
        )

    monkeypatch.setattr(graph_module, "start_incident_run", fixed_start)
    config = ck.build_thread_config(thread_id)
    first_ports = _HoldAssemblyPorts()
    first_ports.persist_action = action_store_module.production_port(  # type: ignore[method-assign]
        engine.begin
    )
    with _checkpoint_connection(endpoint) as connection:
        PostgresSaver(connection).setup()
        first = build_agent_graph(
            _dependencies(engine, first_ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        interrupted = first.invoke(
            {"requested_alarm": ALARM, "autonomy_level": 2},
            config=config,
        )

    run_id = interrupted["run_id"]
    approval_id = interrupted["approval_id"]
    approval_store_module.decision_port(engine.begin)(
        approval_id,
        Decision.APPROVE,
        "operator",
    )
    with engine.begin() as connection:
        repo.resume_from_approval(
            connection,
            agent_run_id=run_id,
            approval_id=approval_id,
        )

    read_decision = approval_store_module.hitl_decision_port(engine.begin)
    read_calls = 0

    def crash_after_read(resolved_approval_id: str) -> Decision:
        nonlocal read_calls
        decision = read_decision(resolved_approval_id)
        read_calls += 1
        if read_calls == 1:
            # DB read는 끝났지만 LangGraph가 node 성공 checkpoint를 쓰기 전 죽는다.
            raise SystemExit("simulated process crash")
        return decision

    ports = _HoldAssemblyPorts()
    ports.hitl_interrupt = crash_after_read  # type: ignore[method-assign]
    with _checkpoint_connection(endpoint) as connection:
        recovered_graph = build_agent_graph(
            _dependencies(engine, ports),
            checkpointer=ck.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        with pytest.raises(SystemExit, match="simulated process crash"):
            approval_store_module.recover_hitl_run(
                recovered_graph,
                engine.begin,
                engine.connect,
                run_id,
            )
        after_crash = recovered_graph.get_state(config)
        assert after_crash.next == ("hitl_interrupt",)

        recovered = approval_store_module.recover_hitl_run(
            recovered_graph,
            engine.begin,
            engine.connect,
            run_id,
        )

    assert read_calls == 2
    assert Decision(recovered["approval_decision"]) is Decision.APPROVE
    assert ports.calls == ["publish_mes", "writeback_result"]
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM agent_run WHERE agent_run_id = :id"),
                {"id": run_id},
            ).scalar_one()
            == RunStatus.COMPLETED.value
        )


def test_concurrent_last_total_slot_is_reserved_exactly_once(
    runtime: tuple[Any, Any],
) -> None:
    _endpoint, engine = runtime
    _seed_runtime(engine)
    run_id = _start_runtime_run(engine)
    _seed_reserved_calls(
        engine,
        run_id,
        (
            "get_fdc_summary",
            "get_fdc_summary",
            "get_equipment_context",
            "get_equipment_context",
            "search_documents",
            "search_documents",
            "send_action",
        ),
    )

    outcomes = _race_budget_reservation(
        _dependencies(engine, _AssemblyPorts()).tools,
        run_id,
        "send_action",
    )

    with engine.connect() as connection:
        total = connection.execute(
            text("SELECT count(*) FROM agent_tool_call WHERE agent_run_id = :run_id"),
            {"run_id": run_id},
        ).scalar_one()
    assert outcomes == ["RESERVED", "TOOL_BUDGET_EXHAUSTED"]
    assert total == 8


def test_concurrent_last_non_send_slot_is_reserved_exactly_once(
    runtime: tuple[Any, Any],
) -> None:
    _endpoint, engine = runtime
    _seed_runtime(engine)
    run_id = _start_runtime_run(engine)
    _seed_reserved_calls(
        engine,
        run_id,
        (
            "get_fdc_summary",
            "get_fdc_summary",
            "get_equipment_context",
            "get_equipment_context",
            "search_documents",
        ),
    )

    outcomes = _race_budget_reservation(
        _dependencies(engine, _AssemblyPorts()).tools,
        run_id,
        "search_documents",
    )

    with engine.connect() as connection:
        total = connection.execute(
            text("SELECT count(*) FROM agent_tool_call WHERE agent_run_id = :run_id"),
            {"run_id": run_id},
        ).scalar_one()
    assert outcomes == ["RESERVED", "TOOL_BUDGET_RESERVED"]
    assert total == 6


def test_real_incident_run_route_and_tool_audit_complete_with_fake_ports(
    runtime: tuple[Any, Any],
) -> None:
    _endpoint, engine = runtime
    _seed_runtime(engine)

    graph = build_agent_graph(_dependencies(engine, _AssemblyPorts()))
    state = graph.invoke(
        {
            "requested_alarm": AlarmRef(
                source=AlarmSource.TRACE,
                alarm_id="TA-01",
            ),
            "autonomy_level": 2,
        }
    )

    with engine.connect() as connection:
        run = connection.execute(
            text(
                "SELECT status, input_tokens, output_tokens, latency_ms "
                "FROM agent_run"
            )
        ).one()
        calls = connection.execute(
            text(
                "SELECT tool_name, input, output, status, error_msg "
                "FROM agent_tool_call ORDER BY call_seq"
            )
        ).all()
        audit_count = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()

    assert run.status == RunStatus.COMPLETED.value
    assert (run.input_tokens, run.output_tokens) == (10, 5)
    assert run.latency_ms is not None and run.latency_ms >= 0
    assert [row.tool_name for row in calls] == ["get_fdc_summary", "search_documents"]
    assert all(row.status == "SUCCESS" and row.error_msg is None for row in calls)
    assert calls[0].input == {"lot_hist_id": "LH-REP"}
    assert calls[0].output["wafer"]["lot_hist_id"] == "LH-REP"
    assert state["route"].route_consistency is True
    assert state["tool_budget"].used == 2
    assert state["action_decision"].action is ActionCode.MONITORING
    assert audit_count == 3


def test_concurrent_same_run_persists_one_action_without_reused_link(
    runtime: tuple[Any, Any],
) -> None:
    """같은 RUNNING run의 두 호출은 advisory→row lock 아래 멱등 반환한다."""

    _endpoint, engine = runtime
    _seed_runtime(engine)
    run_id = _start_runtime_run(engine)
    decision = ActionDecision(
        action=ActionCode.WARNING,
        severity=Severity.MEDIUM,
        requires_approval=False,
        matched_rule="TRACE_OOS",
    )
    persist = action_store_module.production_port(engine.begin)
    barrier = Barrier(2)

    def attempt(_index: int) -> PersistResult:
        barrier.wait()
        return persist(run_id, decision)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (1, 2)))

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM action_history) AS actions, "
                "(SELECT count(*) FROM agent_run_action) AS links, "
                "(SELECT count(*) FROM agent_run_action "
                " WHERE link_role='REUSED') AS reused, "
                "(SELECT count(*) FROM action_delivery) AS deliveries"
            )
        ).one()

    assert results[0] == results[1]
    assert (counts.actions, counts.links, counts.reused, counts.deliveries) == (
        1,
        1,
        0,
        1,
    )


def test_failed_retry_reuses_the_created_action_on_postgres(
    runtime: tuple[Any, Any],
) -> None:
    _endpoint, engine = runtime
    _seed_runtime(engine)
    first_run_id = _start_runtime_run(engine)
    decision = ActionDecision(
        action=ActionCode.WARNING,
        severity=Severity.MEDIUM,
        requires_approval=False,
        matched_rule="TRACE_OOS",
    )
    persist = action_store_module.production_port(engine.begin)
    first = persist(first_run_id, decision)

    with engine.begin() as connection:
        current = repo.get_agent_run(connection, first_run_id)
        repo.finish_agent_run(
            connection,
            first_run_id,
            RunStatus.FAILED,
            evidence=current.evidence,
        )
    retry_run_id = _start_runtime_run(engine)
    second = persist(retry_run_id, decision)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT agent_run_id, action_id, link_role FROM agent_run_action "
                "ORDER BY linked_at, agent_run_id"
            )
        ).all()
        action_count = connection.execute(
            text("SELECT count(*) FROM action_history")
        ).scalar_one()

    assert first.action_id == second.action_id
    assert action_count == 1
    assert {row.link_role for row in rows} == {"CREATED", "REUSED"}
    assert len(rows) == 2


@pytest.mark.parametrize(
    "failure_point",
    ["approval", "first_delivery", "run_action", "provenance"],
)
def test_middle_failure_rolls_back_action_bundle_run_and_approval_audit(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    _endpoint, engine = runtime
    _seed_runtime(engine)
    run_id = _start_runtime_run(engine)
    decision = ActionDecision(
        action=ActionCode.EQP_HOLD,
        severity=Severity.HIGH,
        requires_approval=True,
        matched_rule="R03_PRESENT",
    )
    target_by_point = {
        "approval": "create_approval_request",
        "first_delivery": "insert_action_delivery",
        "run_action": "set_run_action",
        "provenance": "merge_run_action_provenance",
    }
    target = target_by_point[failure_point]
    original = getattr(action_store_module, target)
    calls = 0

    def fail_after_write(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if failure_point != "first_delivery" or calls == 1:
            raise repo.RepositoryContractError("INJECTED_FAILURE")
        return result

    monkeypatch.setattr(action_store_module, target, fail_after_write)
    with engine.connect() as connection:
        audit_before = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()

    with pytest.raises(repo.RepositoryContractError) as exc:
        action_store_module.production_port(engine.begin)(run_id, decision)

    with engine.connect() as connection:
        counts = {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table}")
            ).scalar_one()
            for table in (
                "action_history",
                "agent_run_action",
                "approval_request",
                "action_delivery",
            )
        }
        audit_after = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()
        run = connection.execute(
            text(
                "SELECT action, severity, evidence FROM agent_run "
                "WHERE agent_run_id = :run_id"
            ),
            {"run_id": run_id},
        ).one()

    assert exc.value.code == "INJECTED_FAILURE"
    assert counts == {
        "action_history": 0,
        "agent_run_action": 0,
        "approval_request": 0,
        "action_delivery": 0,
    }
    assert audit_after == audit_before
    assert run.action is None and run.severity is None
    assert not run.evidence or "action_provenance" not in run.evidence


def test_production_action_port_persists_hold_bundle_before_interrupt(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """후속 delivery port는 fixture로 두고 실제 action 저장 port만 graph에 태운다."""

    endpoint, engine = runtime
    _seed_runtime(engine)
    thread_id = str(uuid4())

    def fixed_start(connection: Any, requested_alarm: AlarmRef, **kwargs: Any) -> Any:
        return start_incident_run(
            connection,
            requested_alarm,
            thread_id_factory=lambda: thread_id,
            **kwargs,
        )

    monkeypatch.setattr(graph_module, "start_incident_run", fixed_start)
    ports = _HoldAssemblyPorts()
    ports.persist_action = action_store_module.production_port(engine.begin)  # type: ignore[method-assign]
    with _checkpoint_connection(endpoint) as checkpoint_connection:
        PostgresSaver(checkpoint_connection).setup()
        graph = build_agent_graph(
            _dependencies(engine, ports),
            checkpointer=ck.build_postgres_saver(checkpoint_connection),
            interrupt_after=("approval_email",),
        )

        state = graph.invoke(
            {
                "requested_alarm": AlarmRef(
                    source=AlarmSource.TRACE,
                    alarm_id="TA-01",
                ),
                "autonomy_level": 2,
            },
            config=ck.build_thread_config(thread_id),
        )

    with engine.connect() as connection:
        action = connection.execute(
            text("SELECT action_code, approval_status, reason " "FROM action_history")
        ).one()
        approval = connection.execute(
            text("SELECT approval_id, status FROM approval_request")
        ).one()
        deliveries = connection.execute(
            text(
                "SELECT channel, status, request_hash FROM action_delivery "
                "ORDER BY channel"
            )
        ).all()
        run = connection.execute(
            text("SELECT status, action, severity, evidence FROM agent_run")
        ).one()

    assert action.action_code == ActionCode.EQP_HOLD.value
    assert action.approval_status == "PENDING"
    assert "{" not in action.reason and "ACTION-POLICY" not in action.reason
    assert state["approval_id"] == approval.approval_id
    assert approval.status == "PENDING"
    assert [(row.channel, row.status) for row in deliveries] == [
        (DeliveryChannel.EMAIL.value, DeliveryStatus.WAITING.value),
        (DeliveryChannel.MES_MOCK.value, DeliveryStatus.BLOCKED.value),
    ]
    assert all(len(row.request_hash) == 64 for row in deliveries)
    assert run.status == RunStatus.WAITING_APPROVAL.value
    assert ports.calls.count("hitl_interrupt") == 0
    assert (run.action, run.severity) == (
        ActionCode.EQP_HOLD.value,
        Severity.HIGH.value,
    )
    assert run.evidence["action_provenance"] == {
        "schema": "action-provenance-v1",
        "action_policy_version": "ACTION-POLICY-V1",
        "member_alarms": [{"source": "TRACE", "alarm_id": "TA-01"}],
    }


def test_production_boundary_binds_the_actual_fdc_structured_tool() -> None:
    """무거운 Detection import를 container 묶음에만 둔다."""

    from app.detection.tools import get_fdc_summary

    boundary = ToolBoundary.production()
    assert boundary.fdc_summary.__self__ is get_fdc_summary


def test_production_fdc_tool_runs_in_the_real_graph_against_postgres(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """production 경계의 실제 FDC Tool만 격리 DB에 태운 조립 회귀."""

    from app.detection import tools as detection_tools

    _endpoint, engine = runtime
    _seed_runtime(engine)
    monkeypatch.setattr(detection_tools, "get_readonly_engine", lambda: engine)

    production = ToolBoundary.production()
    graph = build_agent_graph(
        _dependencies(
            engine,
            _AssemblyPorts(),
            fdc_summary=production.fdc_summary,
        )
    )
    state = graph.invoke(
        {
            "requested_alarm": AlarmRef(
                source=AlarmSource.TRACE,
                alarm_id="TA-01",
            ),
            "autonomy_level": 2,
        }
    )

    with engine.connect() as connection:
        run_status = connection.execute(
            text("SELECT status FROM agent_run")
        ).scalar_one()
        calls = connection.execute(
            text(
                "SELECT tool_name, output, status, error_msg "
                "FROM agent_tool_call ORDER BY call_seq"
            )
        ).all()

    fdc_call = calls[0]
    assert run_status == RunStatus.COMPLETED.value
    assert [row.tool_name for row in calls] == [
        "get_fdc_summary",
        "search_documents",
    ]
    assert fdc_call.status == "SUCCESS" and fdc_call.error_msg is None
    assert fdc_call.output["wafer"] == {
        "lot_hist_id": "LH-REP",
        "lot_id": "LOT001",
        "wafer_no": 1,
        "chamber_id": "EQP01-PM1",
        "equipment_id": "EQP01",
        "step_id": "CT-PHOTO",
        "recipe_id": "RECIPE01",
    }
    assert fdc_call.output["parameters"][0]["parameter_id"] == "PARAM01"
    assert fdc_call.output["parameters"][0]["point_cnt"] == 6
    # 로컬에 versioned model artifact가 있으면 signal이 채워질 수 있다. 이 E2E는
    # nullable score 값이 아니라 실제 summary Tool 배선과 action 비의존을 고정한다.
    assert "anomaly" in fdc_call.output
    assert state["action_decision"].action is ActionCode.MONITORING


def test_real_hypothesis_adapter_runs_in_the_real_node_and_persists_usage(
    runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM HTTP 경계만 mock하고 실제 adapter·graph·PostgreSQL을 결합한다."""

    from app.agent import hypothesis as hypothesis_module

    _endpoint, engine = runtime
    _seed_runtime(engine)
    content = json.dumps(
        {
            "predicted_fault_code": "OTH",
            "confidence": 0.6,
            "cause_summary": "pressure pattern",
            "supporting_alarms": [{"source": "TRACE", "alarm_id": "TA-01"}],
            "supporting_chunk_ids": [],
            "supporting_relation_ids": ["REL-PART"],
            "uncertainty": "limited history",
        }
    )
    monkeypatch.setattr(
        hypothesis_module.llm,
        "chat_with_usage",
        lambda messages: ChatCompletion(
            content=content,
            model="provider-actual-model",
            prompt_tokens=31,
            completion_tokens=12,
        ),
    )
    ports = _AssemblyPorts()
    ports.generate_hypothesis = production_port()  # type: ignore[method-assign]
    graph = build_agent_graph(_dependencies(engine, ports))

    state = graph.invoke(
        {
            "requested_alarm": AlarmRef(
                source=AlarmSource.TRACE,
                alarm_id="TA-01",
            ),
            "autonomy_level": 2,
        }
    )

    with engine.connect() as connection:
        run = connection.execute(
            text(
                "SELECT status, llm_model, prompt_version, input_tokens, "
                "output_tokens FROM agent_run"
            )
        ).one()
        prediction = connection.execute(
            text("SELECT llm_model, prompt_version, evidence " "FROM agent_prediction")
        ).one()
    assert run.status == RunStatus.COMPLETED.value
    assert run.llm_model == prediction.llm_model == "provider-actual-model"
    assert (
        run.prompt_version == prediction.prompt_version == graph_module.PROMPT_VERSION
    )
    assert (run.input_tokens, run.output_tokens) == (31, 12)
    assert prediction.evidence["schema_version"] == "agent-evidence-v1"
    assert state["hypothesis"].supporting_relation_ids == ("REL-PART",)
