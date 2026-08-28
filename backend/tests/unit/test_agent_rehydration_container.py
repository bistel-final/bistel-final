"""`V5-C-3.4` checkpoint 상실 복구의 격리 PostgreSQL 16 회귀."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import text

from app.agent import action_store, approval_store, checkpoint
from app.agent import graph as graph_module
from app.agent.graph import build_agent_graph
from app.agent.run_guard import start_incident_run
from app.agent.state import Hypothesis, HypothesisOutcome, LlmUsage
from app.common.enums import Decision, FaultHypothesis, RunStatus
from app.common.schemas import AlarmRef
from app.common.tool_contracts import DocumentHit, DocumentSearchToolResult
from tests.unit import test_agent_graph_container as graph_container

pytestmark = pytest.mark.container


@pytest.fixture(scope="module")
def rehydration_runtime() -> Iterator[tuple[Any, Any]]:
    """기존 조립 fixture의 DB 형상을 별도 module fixture로 재사용한다."""

    with graph_container._runtime_context() as resources:
        yield resources


class _CountingGraph:
    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self.invoke_count = 0

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        self.invoke_count += 1
        return self.graph.invoke(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.graph, name)


class _BlockingUpdateGraph:
    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self.entered = Event()
        self.release = Event()
        self.update_calls = 0

    def update_state(self, *args: Any, **kwargs: Any) -> Any:
        self.update_calls += 1
        self.entered.set()
        assert self.release.wait(timeout=5)
        return self.graph.update_state(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.graph, name)


def test_snapshot_merge_failure_rolls_back_the_entire_action_transaction(
    rehydration_runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _endpoint, engine = rehydration_runtime
    graph_container._seed_runtime(engine)
    run_id = graph_container._start_runtime_run(engine)
    decision = graph_container.ActionDecision(
        action=graph_container.ActionCode.EQP_HOLD,
        severity=graph_container.Severity.HIGH,
        requires_approval=True,
        matched_rule="R03_PRESENT",
    )
    original = action_store.merge_run_action_provenance

    def fail_after_snapshot_merge(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise graph_container.repo.RepositoryContractError("INJECTED_FAILURE")

    monkeypatch.setattr(
        action_store,
        "merge_run_action_provenance",
        fail_after_snapshot_merge,
    )
    with pytest.raises(graph_container.repo.RepositoryContractError) as caught:
        action_store.production_port(engine.begin)(
            run_id,
            decision,
            graph_container._minimal_rehydration_seed(),
        )

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
        run = connection.execute(
            text(
                "SELECT action, severity, evidence FROM agent_run "
                "WHERE agent_run_id = :run_id"
            ),
            {"run_id": run_id},
        ).one()
    assert caught.value.code == "INJECTED_FAILURE"
    assert counts == {
        "action_history": 0,
        "agent_run_action": 0,
        "approval_request": 0,
        "action_delivery": 0,
    }
    assert run.action is None and run.severity is None
    assert not run.evidence or not {
        "action_provenance",
        "rehydration_snapshot",
    }.intersection(run.evidence)


@pytest.mark.parametrize(
    ("decision", "citation_kind"),
    [
        (None, "relation"),
        (Decision.APPROVE, "relation"),
        (Decision.REJECT, "relation"),
        (Decision.APPROVE, "chunk"),
    ],
)
def test_missing_checkpoint_is_rehydrated_from_the_persisted_snapshot(
    rehydration_runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    decision: Decision | None,
    citation_kind: str,
) -> None:
    endpoint, engine = rehydration_runtime
    graph_container._seed_runtime(engine)
    thread_id = str(uuid4())
    start_calls: list[str] = []

    def fixed_start(connection: Any, requested_alarm: AlarmRef, **kwargs: Any) -> Any:
        start_calls.append(requested_alarm.to_token())
        return start_incident_run(
            connection,
            requested_alarm,
            thread_id_factory=lambda: thread_id,
            **kwargs,
        )

    monkeypatch.setattr(graph_module, "start_incident_run", fixed_start)
    config = checkpoint.build_thread_config(thread_id)
    first_ports = graph_container._HoldAssemblyPorts()
    first_ports.persist_action = action_store.production_port(  # type: ignore[method-assign]
        engine.begin
    )
    document_search = None
    if citation_kind == "chunk":

        def generate_chunk_hypothesis(*_args: Any) -> HypothesisOutcome:
            first_ports.calls.append("generate_hypothesis")
            return HypothesisOutcome(
                hypothesis=Hypothesis(
                    predicted_fault_code=FaultHypothesis.OTH,
                    confidence=0.5,
                    cause_summary="chunk citation fixture",
                    supporting_alarms=(graph_container.ALARM,),
                    supporting_chunk_ids=("CHUNK-1",),
                    uncertainty="",
                ),
                llm_usage=LlmUsage(
                    model="fixture-model",
                    prompt_version=graph_module.PROMPT_VERSION,
                    input_tokens=10,
                    output_tokens=5,
                ),
            )

        first_ports.generate_hypothesis = generate_chunk_hypothesis  # type: ignore[method-assign]

        def chunk_document_search(_payload: dict[str, Any]) -> DocumentSearchToolResult:
            return DocumentSearchToolResult(
                ok=True,
                hits=[
                    DocumentHit(
                        chunk_id="CHUNK-1",
                        document_id="DOC-1",
                        title="Fixture",
                        score=0.9,
                        content="checkpoint recovery guidance",
                        model_code="MODEL-1",
                    )
                ],
            )

        document_search = chunk_document_search
    with graph_container._checkpoint_connection(endpoint) as connection:
        PostgresSaver(connection).setup()
        first_graph = build_agent_graph(
            graph_container._dependencies(
                engine,
                first_ports,
                document_search=document_search,
            ),
            checkpointer=checkpoint.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        interrupted = first_graph.invoke(
            {"requested_alarm": graph_container.ALARM, "autonomy_level": 2},
            config=config,
        )
    run_id = interrupted["run_id"]
    approval_id = interrupted["approval_id"]
    assert approval_id is not None

    if decision is not None:
        approval_store.decision_port(engine.begin)(
            approval_id,
            decision,
            "operator",
        )

    with engine.begin() as connection:
        for table in ("checkpoint_writes", "checkpoints", "checkpoint_blobs"):
            connection.execute(
                text(f"DELETE FROM {table} WHERE thread_id = :thread_id"),
                {"thread_id": thread_id},
            )

    recovery_ports = graph_container._HoldAssemblyPorts()
    email_calls: list[tuple[str, str]] = []
    recovery_ports.approval_email = approval_store.approval_email_port(  # type: ignore[method-assign]
        engine.begin,
        lambda action_id, current_approval_id: email_calls.append(
            (action_id, current_approval_id)
        ),
    )
    recovery_ports.hitl_interrupt = approval_store.hitl_decision_port(  # type: ignore[method-assign]
        engine.begin
    )
    cancel_mes = approval_store.cancel_mes_port(engine.begin)

    def record_cancel(action_id: str) -> Any:
        recovery_ports.calls.append("cancel_mes")
        return cancel_mes(action_id)

    recovery_ports.cancel_mes = record_cancel  # type: ignore[method-assign]
    with graph_container._checkpoint_connection(endpoint) as connection:
        recovered_graph = _CountingGraph(
            build_agent_graph(
                graph_container._dependencies(engine, recovery_ports),
                checkpointer=checkpoint.build_postgres_saver(connection),
                interrupt_after=("approval_email",),
            )
        )
        recovered = approval_store.recover_hitl_run(
            recovered_graph,
            engine.begin,
            engine.connect,
            run_id,
        )

    assert recovered["run_id"] == run_id
    assert recovered["thread_id"] == thread_id
    assert start_calls == [graph_container.ALARM.to_token()]
    if decision is None:
        assert recovered_graph.invoke_count == 1
        assert email_calls == [(recovered["action_id"], approval_id)]
        assert recovery_ports.calls == []
        expected_status = RunStatus.WAITING_APPROVAL.value
    elif decision is Decision.APPROVE:
        assert recovered_graph.invoke_count == 2
        assert email_calls == []
        assert recovery_ports.calls == ["publish_mes", "writeback_result"]
        expected_status = RunStatus.COMPLETED.value
    else:
        assert recovered_graph.invoke_count == 2
        assert email_calls == []
        assert recovery_ports.calls == ["cancel_mes"]
        expected_status = RunStatus.COMPLETED.value
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT evidence, status, "
                "(SELECT count(*) FROM agent_tool_call "
                " WHERE agent_run_id = :run_id) AS tool_calls, "
                "(SELECT count(*) FROM action_history) AS actions "
                "FROM agent_run WHERE agent_run_id = :run_id"
            ),
            {"run_id": run_id},
        ).one()
    assert (row.tool_calls, row.actions) == (2, 1)
    assert row.status == expected_status
    audit = row.evidence["checkpoint_rehydration"]
    assert audit["schema_version"] == "rehydration-snapshot-v1"
    assert len(audit["events"]) == 1
    if citation_kind == "chunk":
        assert recovered["hypothesis"].supporting_chunk_ids == ("CHUNK-1",)
    else:
        assert recovered["hypothesis"].supporting_relation_ids == ("REL-PART",)


def test_concurrent_rehydration_and_regular_resume_share_one_postgres_owner(
    rehydration_runtime: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, engine = rehydration_runtime
    graph_container._seed_runtime(engine)
    thread_id = str(uuid4())

    def fixed_start(connection: Any, requested_alarm: AlarmRef, **kwargs: Any) -> Any:
        return start_incident_run(
            connection,
            requested_alarm,
            thread_id_factory=lambda: thread_id,
            **kwargs,
        )

    monkeypatch.setattr(graph_module, "start_incident_run", fixed_start)
    config = checkpoint.build_thread_config(thread_id)
    first_ports = graph_container._HoldAssemblyPorts()
    first_ports.persist_action = action_store.production_port(  # type: ignore[method-assign]
        engine.begin
    )
    with graph_container._checkpoint_connection(endpoint) as connection:
        PostgresSaver(connection).setup()
        first_graph = build_agent_graph(
            graph_container._dependencies(engine, first_ports),
            checkpointer=checkpoint.build_postgres_saver(connection),
            interrupt_after=("approval_email",),
        )
        interrupted = first_graph.invoke(
            {"requested_alarm": graph_container.ALARM, "autonomy_level": 2},
            config=config,
        )
    run_id = interrupted["run_id"]

    with engine.begin() as connection:
        for table in ("checkpoint_writes", "checkpoints", "checkpoint_blobs"):
            connection.execute(
                text(f"DELETE FROM {table} WHERE thread_id = :thread_id"),
                {"thread_id": thread_id},
            )

    recovery_ports = graph_container._HoldAssemblyPorts()
    with graph_container._checkpoint_connection(endpoint) as connection:
        blocking = _BlockingUpdateGraph(
            build_agent_graph(
                graph_container._dependencies(engine, recovery_ports),
                checkpointer=checkpoint.build_postgres_saver(connection),
                interrupt_after=("approval_email",),
            )
        )

        def owner() -> str:
            recovered = approval_store.recover_hitl_run(
                blocking,
                engine.begin,
                engine.connect,
                run_id,
            )
            return recovered["run_id"]

        def regular_resume_contender() -> str:
            assert blocking.entered.wait(timeout=5)
            try:
                approval_store.resume_after_approval(
                    blocking,
                    engine.begin,
                    engine.connect,
                    thread_id,
                )
            except approval_store.HitlResumeError as exc:
                return exc.code
            finally:
                blocking.release.set()
            return "UNEXPECTED"

        with ThreadPoolExecutor(max_workers=2) as executor:
            owner_future = executor.submit(owner)
            contender_future = executor.submit(regular_resume_contender)
            results = (owner_future.result(), contender_future.result())

    assert set(results) == {"RESUME_ALREADY_RUNNING", run_id}
    assert blocking.update_calls == 1
    assert recovery_ports.calls == ["approval_email"]
    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM action_history) AS actions, "
                "(SELECT count(*) FROM agent_tool_call "
                " WHERE agent_run_id = :run_id) AS tool_calls"
            ),
            {"run_id": run_id},
        ).one()
    assert (counts.actions, counts.tool_calls) == (1, 2)
