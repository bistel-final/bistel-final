"""V5-C-5.1 묶음 1 — 공개 DTO·목록 read model 단위 회귀."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent import public_read_model as read_model
from app.agent import repository as repo
from app.agent import router as agent_router
from app.agent import runtime_composition as runtime_module
from app.agent.public_schemas import PublicAgentRunItem, PublicApprovalItem
from app.agent.runtime_composition import (
    READ_TOOL_CALLER_DEADLINE_SECONDS,
    AgentRuntime,
    DecidedPublicApproval,
    RuntimeResources,
    StartedPublicRun,
)
from app.common.enums import (
    ActionCode,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    ToolCallStatus,
)
from app.common.schemas import AlarmRef

NOW = datetime(2026, 8, 4, 7, 0, 30, tzinfo=UTC)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append((statement, params))
        return _Result(self.rows)


def _run_record() -> repo.PublicAgentRunRecord:
    return repo.PublicAgentRunRecord(
        agent_run_id="RUN-0000000000000001",
        created_at=NOW,
        requested_alarm=AlarmRef(source="R03", alarm_id="R03-1"),
        chamber_id="EQP04-PM2",
        predicted_fault_code=FaultHypothesis.RFM,
        confidence=0.84,
        recommended_action=ActionCode.EQP_HOLD,
        status=RunStatus.WAITING_APPROVAL,
        action_id="ACT-0000000000000001",
        approval_id="APR-0000000000000001",
        tools=(
            repo.PublicToolCallRecord(
                tool_name="get_fdc_summary",
                status=ToolCallStatus.SUCCESS,
            ),
        ),
        deliveries=(
            repo.PublicDeliveryRecord(
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.SENT,
            ),
            repo.PublicDeliveryRecord(
                channel=DeliveryChannel.MES_MOCK,
                status=DeliveryStatus.BLOCKED,
            ),
        ),
        latency_ms=920,
        llm_model="configured-model",
    )


def _approval_record(
    *, status: ApprovalStatus = ApprovalStatus.PENDING
) -> repo.PublicApprovalRecord:
    decided = status is not ApprovalStatus.PENDING
    return repo.PublicApprovalRecord(
        approval_id="APR-0000000000000001",
        agent_run_id="RUN-0000000000000001",
        action_id="ACT-0000000000000001",
        created_at=NOW,
        lot_id="LOT004",
        equipment_id="EQP04",
        chamber_id="EQP04-PM2",
        predicted_fault_code=FaultHypothesis.RFM,
        action_code=ActionCode.EQP_HOLD,
        reason="R03_CONSEC: consecutive OOS",
        status=status,
        decided_by="operator" if decided else None,
        decided_at=NOW if decided else None,
        decision_comment="checked" if decided else None,
    )


def test_public_run_json_has_exact_allowlist_and_canonical_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        read_model,
        "list_agent_runs_public",
        lambda *_args, **_kwargs: [_run_record()],
    )

    item = read_model.list_public_agent_runs(object(), date_from=None, date_to=None)[0]
    payload = item.model_dump(mode="json")

    assert set(payload) == {
        "agent_run_id",
        "created_at",
        "alarm_source",
        "alarm_id",
        "chamber_id",
        "chamber",
        "predicted_fault_code",
        "fault_code",
        "fault_name",
        "fault_color",
        "confidence",
        "recommended_action",
        "status",
        "action_id",
        "approval_id",
        "tools",
        "deliveries",
        "latency_ms",
        "llm_model",
    }
    assert set(payload["tools"][0]) == {
        "tool_name",
        "status",
        "result_summary",
        "n",
        "s",
    }
    assert set(payload["deliveries"][0]) == {"channel", "status"}
    assert payload["chamber"] == payload["chamber_id"]
    assert payload["fault_code"] == payload["predicted_fault_code"]
    assert payload["created_at"].endswith("+09:00")
    assert [delivery["channel"] for delivery in payload["deliveries"]] == [
        "EMAIL",
        "MES",
    ]
    serialized = str(payload)
    for forbidden in (
        "MES_MOCK",
        "input",
        "output",
        "request_hash",
        "provider_message_id",
        "HIDDEN_GOLD",
        "reviewed_fault_code",
    ):
        assert forbidden not in serialized


def test_public_run_dto_rejects_extra_internal_fields() -> None:
    payload = read_model._public_run(_run_record()).model_dump(mode="python")
    with pytest.raises(ValidationError):
        PublicAgentRunItem(**payload, output={"dsn": "secret"})


def test_tool_summary_is_allowlisted_and_never_uses_raw_error() -> None:
    record = repo.PublicToolCallRecord(
        tool_name="get_equipment_context",
        status=ToolCallStatus.ERROR,
    )
    item = read_model._public_tool(record)
    assert item.result_summary == "Equipment context unavailable"

    with pytest.raises(repo.RepositoryContractError) as caught:
        read_model._public_tool(
            repo.PublicToolCallRecord(
                tool_name="arbitrary-secret-tool",
                status=ToolCallStatus.SUCCESS,
            )
        )
    assert caught.value.code == "PUBLIC_TOOL_NAME_INVALID"


def test_date_pair_uses_kst_half_open_boundaries() -> None:
    lower, upper = read_model.public_run_date_bounds(date(2026, 8, 4), date(2026, 8, 5))
    assert lower is not None and upper is not None
    assert lower.isoformat() == "2026-08-04T00:00:00+09:00"
    assert upper.isoformat() == "2026-08-06T00:00:00+09:00"

    with pytest.raises(read_model.PublicDateRangeError):
        read_model.public_run_date_bounds(date(2026, 8, 4), None)
    with pytest.raises(read_model.PublicDateRangeError):
        read_model.public_run_date_bounds(date(2026, 8, 5), date(2026, 8, 4))
    with pytest.raises(read_model.PublicDateRangeError):
        read_model.public_run_date_bounds(date.max, date.max)


def _run_db_row(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "agent_run_id": "RUN-0000000000000001",
        "created_at": NOW,
        "requested_alarm_source": "R03",
        "requested_alarm_id": "R03-1",
        "chamber_id": "EQP04-PM2",
        "predicted_fault_code": "RFM",
        "confidence": Decimal("0.840"),
        "recommended_action": "EQP_HOLD",
        "status": "WAITING_APPROVAL",
        "action_id": "ACT-0000000000000001",
        "stored_action_code": "EQP_HOLD",
        "approval_id": "APR-0000000000000001",
        "approval_agent_run_id": "RUN-0000000000000001",
        "tools": [{"tool_name": "search_documents", "status": "SUCCESS"}],
        "deliveries": [{"channel": "MES_MOCK", "status": "BLOCKED"}],
        "latency_ms": 920,
        "active_timing": None,
        "observed_at": NOW,
        "llm_model": "configured-model",
    }
    values.update(overrides)
    if "agent_run_id" in overrides and "approval_agent_run_id" not in overrides:
        values["approval_agent_run_id"] = overrides["agent_run_id"]
    return SimpleNamespace(**values)


def test_run_repository_is_one_query_without_raw_tool_data() -> None:
    connection = _Connection([_run_db_row()])

    records = repo.list_agent_runs_public(connection, date_from=NOW, date_to=NOW)

    assert len(connection.calls) == 1
    sql = str(connection.calls[0][0])
    assert "ORDER BY r.started_at DESC, r.agent_run_id DESC" in sql
    assert "tool.input" not in sql
    assert "tool.output" not in sql
    assert "tool.error_msg" not in sql
    assert "agent_prediction_review" not in sql
    assert "active_started_at" not in sql
    assert "LIMIT :limit" in sql
    assert connection.calls[0][1]["limit"] == repo.PUBLIC_AGENT_RUN_LIMIT
    assert records[0].confidence == 0.84


def test_run_repository_omits_only_nullable_or_malformed_legacy_rows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = repo.public_read_omission_counts()
    connection = _Connection(
        [
            _run_db_row(),
            _run_db_row(
                agent_run_id="RUN-0000000000000002",
                latency_ms=None,
            ),
            _run_db_row(
                agent_run_id="RUN-0000000000000003",
                llm_model=None,
            ),
            _run_db_row(
                agent_run_id="RUN-0000000000000004",
                status="RUNNING",
                latency_ms=100,
                active_timing={
                    "schema": repo.ACTIVE_TIMING_SCHEMA,
                    "active_started_at": "not-a-timestamp",
                },
            ),
            _run_db_row(
                agent_run_id="RUN-0000000000000005",
                status="RUNNING",
                latency_ms=100,
                active_timing={"schema": repo.ACTIVE_TIMING_SCHEMA},
            ),
        ]
    )

    records = repo.list_agent_runs_public(connection, date_from=None, date_to=None)

    assert [record.agent_run_id for record in records] == ["RUN-0000000000000001"]
    assert "ACTIVE_TIMING_SUBTOTAL_INVALID" in caplog.text
    assert "INVALID_LLM_MODEL" in caplog.text
    assert "ACTIVE_TIMING_INVALID" in caplog.text
    assert "ACTIVE_TIMING_START_MISSING" in caplog.text
    assert "RUN-0000000000000002" not in caplog.text
    after = repo.public_read_omission_counts()
    assert after.get(("agent_run", "ACTIVE_TIMING_SUBTOTAL_INVALID"), 0) == (
        before.get(("agent_run", "ACTIVE_TIMING_SUBTOTAL_INVALID"), 0) + 1
    )
    assert after.get(("agent_run", "INVALID_LLM_MODEL"), 0) == (
        before.get(("agent_run", "INVALID_LLM_MODEL"), 0) + 1
    )
    assert after.get(("agent_run", "ACTIVE_TIMING_START_MISSING"), 0) == (
        before.get(("agent_run", "ACTIVE_TIMING_START_MISSING"), 0) + 1
    )


def _approval_db_row(equipment_ids: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        approval_id="APR-0000000000000001",
        agent_run_id="RUN-0000000000000001",
        action_id="ACT-0000000000000001",
        created_at=NOW,
        lot_id="LOT004",
        chamber_id="EQP04-PM2",
        predicted_fault_code="RFM",
        action_code="EQP_HOLD",
        reason="R03_CONSEC",
        status="PENDING",
        decided_by=None,
        decided_at=None,
        decision_comment=None,
        linked_agent_run_id="RUN-0000000000000001",
        linked_action_id="ACT-0000000000000001",
        linked_lot_id="LOT004",
        linked_chamber_id="EQP04-PM2",
        action_lot_id="LOT004",
        action_chamber_id="EQP04-PM2",
        equipment_ids=equipment_ids,
    )


@pytest.mark.parametrize("equipment_ids", [[], ["EQP04", "EQP05"]])
def test_approval_repository_requires_exactly_one_real_equipment(
    equipment_ids: list[str],
) -> None:
    connection = _Connection([_approval_db_row(equipment_ids)])

    assert repo.list_approvals_public(connection) == []
    assert len(connection.calls) == 1


def test_approval_repository_fails_closed_on_cross_entity_identity() -> None:
    row = _approval_db_row(["EQP04"])
    row.linked_action_id = "ACT-OTHER"

    assert repo.list_approvals_public(_Connection([row])) == []


def test_approval_repository_omits_bad_row_without_hiding_healthy_row() -> None:
    healthy = _approval_db_row(["EQP04"])
    invalid = _approval_db_row(["EQP04", "EQP05"])

    records = repo.list_approvals_public(_Connection([healthy, invalid]))

    assert [record.approval_id for record in records] == ["APR-0000000000000001"]


def test_rejected_approval_aliases_copy_canonical_decision_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        read_model,
        "list_approvals_public",
        lambda _connection: [_approval_record(status=ApprovalStatus.REJECTED)],
    )

    payload = read_model.list_public_approvals(object())[0].model_dump(mode="json")
    assert set(payload) == set(PublicApprovalItem.model_fields)
    assert payload["status"] == "REJECTED"
    assert payload["approved_by"] == payload["decided_by"] == "operator"
    assert payload["approved_at"] == payload["decided_at"]
    assert payload["created_at"].endswith("+09:00")
    assert payload["decided_at"].endswith("+09:00")
    assert payload["fault_code"] == payload["predicted_fault_code"] == "RFM"


def test_public_approval_dto_rejects_pending_decision_fields() -> None:
    payload = read_model._public_approval(_approval_record()).model_dump(mode="python")
    payload["decided_by"] = "operator"
    with pytest.raises(ValidationError, match="PENDING"):
        PublicApprovalItem(**payload)


def _test_app(connection: Any) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.include_router(agent_router.router)
    app.dependency_overrides[agent_router.get_db_connection] = lambda: connection
    return app, TestClient(app, raise_server_exceptions=False)


def test_run_route_rejects_unpaired_dates_before_query() -> None:
    connection = _Connection([])
    _app, client = _test_app(connection)

    response = client.get("/agent/runs?date_from=2026-08-04")

    assert response.status_code == 422
    assert connection.calls == []


def test_read_routes_return_bare_arrays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_router,
        "list_public_agent_runs",
        lambda *_args, **_kwargs: [read_model._public_run(_run_record())],
    )
    monkeypatch.setattr(
        agent_router,
        "list_public_approvals",
        lambda *_args, **_kwargs: [read_model._public_approval(_approval_record())],
    )
    _app, client = _test_app(object())

    runs = client.get("/agent/runs")
    approvals = client.get("/approvals")

    assert runs.status_code == approvals.status_code == 200
    assert isinstance(runs.json(), list)
    assert isinstance(approvals.json(), list)


def test_repository_unavailable_maps_to_sanitized_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: Any, **_kwargs: Any) -> list[PublicAgentRunItem]:
        raise repo.RepositoryUnavailable("DATABASE_UNAVAILABLE", "dsn=secret")

    monkeypatch.setattr(agent_router, "list_public_agent_runs", unavailable)
    _app, client = _test_app(object())

    response = client.get("/agent/runs")

    assert response.status_code == 503
    assert "secret" not in response.text


def test_repository_contract_maps_to_sanitized_500_and_logs_code(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def invalid(*_args: Any, **_kwargs: Any) -> list[PublicAgentRunItem]:
        raise repo.RepositoryContractError("PUBLIC_RUN_DTO_INVALID", "row=secret")

    monkeypatch.setattr(agent_router, "list_public_agent_runs", invalid)
    _app, client = _test_app(object())

    response = client.get("/agent/runs")

    assert response.status_code == 500
    assert "secret" not in response.text
    assert "PUBLIC_RUN_DTO_INVALID" in caplog.text


class _FakePublicRuntime:
    def __init__(self) -> None:
        self.background: list[tuple[str, str]] = []
        self.decisions: list[tuple[str, str]] = []

    def start_run(self, alarm: AlarmRef) -> StartedPublicRun:
        return StartedPublicRun(
            agent_run_id="RUN-0000000000000009",
            alarm=alarm,
            thread_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        )

    def continue_run(self, thread_id: str, run_id: str) -> None:
        self.background.append((thread_id, run_id))

    def decide_approval_public(self, approval_id: str, payload: Any) -> Any:
        self.decisions.append((approval_id, payload.decision.value))
        return DecidedPublicApproval(
            item=read_model.to_public_approval(
                _approval_record(status=ApprovalStatus.APPROVED)
            ),
            thread_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            agent_run_id="RUN-0000000000000001",
        )

    def resume_decided(self, thread_id: str, run_id: str) -> None:
        self.background.append((thread_id, run_id))

    def fail_registered_run(self, _run_id: str) -> None:
        raise AssertionError("registration should not fail")


def test_post_run_accepts_only_source_aware_alarm_and_registers_background() -> None:
    runtime = _FakePublicRuntime()
    app, client = _test_app(object())
    app.dependency_overrides[agent_router.get_agent_runtime] = lambda: runtime

    accepted = client.post(
        "/agent/runs",
        json={"alarm": {"source": "TRACE", "alarm_id": "TRACE-1"}},
    )
    legacy = client.post("/agent/runs", json={"alarm_id": "TRACE-1"})

    assert accepted.status_code == 202
    assert accepted.json() == {
        "agent_run_id": "RUN-0000000000000009",
        "status": "RUNNING",
        "alarm": {"source": "TRACE", "alarm_id": "TRACE-1"},
    }
    assert runtime.background == [
        (
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "RUN-0000000000000009",
        )
    ]
    assert legacy.status_code == 422


def test_post_approval_returns_public_item_then_registers_resume() -> None:
    runtime = _FakePublicRuntime()
    app, client = _test_app(object())
    app.dependency_overrides[agent_router.get_agent_runtime] = lambda: runtime

    response = client.post(
        "/approvals/APR-0000000000000001/decision",
        json={"decision": "APPROVED", "decided_by": "operator"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"
    assert runtime.decisions == [("APR-0000000000000001", "APPROVED")]
    assert runtime.background == [
        (
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "RUN-0000000000000001",
        )
    ]


def test_public_openapi_contains_bundle_two_posts() -> None:
    app, _client = _test_app(object())
    schema = app.openapi()
    assert schema["paths"]["/agent/runs"]["post"]["responses"].get("202")
    assert "/approvals/{approval_id}/decision" in schema["paths"]


class _RuntimeGraph:
    def __init__(self, *, lose_response: bool = False, checkpoint: bool = True) -> None:
        self.lose_response = lose_response
        self.checkpoint = checkpoint
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self.values: dict[str, Any] = {}
        self.next: tuple[str, ...] = ()

    def invoke(self, payload: Any, *, config: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((payload, config))
        thread_id = config["configurable"]["thread_id"]
        if self.checkpoint:
            self.values = {
                "run_id": "RUN-0000000000000009",
                "thread_id": thread_id,
            }
            self.next = ("collect_fdc",)
        if self.lose_response:
            raise RuntimeError("driver response lost: secret")
        return dict(self.values)

    def get_state(self, _config: dict[str, Any]) -> Any:
        return SimpleNamespace(values=self.values, next=self.next)


class _Closable:
    def __init__(self, *, shutdown_error: bool = False) -> None:
        self.closed = 0
        self.shutdowns = 0
        self.shutdown_error = shutdown_error

    def close(self) -> None:
        self.closed += 1

    def shutdown(self, **_kwargs: Any) -> None:
        self.shutdowns += 1
        if self.shutdown_error:
            raise RuntimeError("shutdown failed")


def _runtime_resources(graph: _RuntimeGraph) -> RuntimeResources:
    @contextmanager
    def transactions() -> Any:
        yield object()

    return RuntimeResources(
        graph=graph,
        transactions=transactions,
        resume_connections=transactions,
        checkpoint_pool=_Closable(),
        deadline_executor=_Closable(),  # type: ignore[arg-type]
        llm_model="configured-model",
    )


def test_production_read_tool_caller_deadline_is_eight_seconds() -> None:
    """DB statement timeout과 별개인 C-2.2 caller 계약을 조립 seam에서 고정한다."""

    executor = _Closable()
    tools = runtime_module._production_tool_executor(
        transactions=lambda: None,
        boundary=SimpleNamespace(),
        executor=executor,  # type: ignore[arg-type]
    )

    assert READ_TOOL_CALLER_DEADLINE_SECONDS == 8.0
    assert tools.deadline_seconds == 8.0


@pytest.mark.parametrize("lose_response", [False, True])
def test_runtime_binds_one_thread_and_recovers_checkpoint_response_loss(
    monkeypatch: pytest.MonkeyPatch,
    lose_response: bool,
) -> None:
    graph = _RuntimeGraph(lose_response=lose_response)
    resources = _runtime_resources(graph)
    stored = SimpleNamespace(
        agent_run_id="RUN-0000000000000009",
        status=RunStatus.RUNNING,
    )
    monkeypatch.setattr(
        runtime_module,
        "get_agent_run_by_thread_exact",
        lambda *_args: stored,
    )
    runtime = AgentRuntime(
        factory=lambda _model: resources,
        llm_preflight=lambda: "configured-model",
        autonomy_level=2,
    )

    started = runtime.start_run(AlarmRef(source="TRACE", alarm_id="TRACE-1"))

    assert started.agent_run_id == "RUN-0000000000000009"
    assert len(graph.calls) == 1
    payload, config = graph.calls[0]
    assert payload["thread_id"] == config["configurable"]["thread_id"]
    assert started.thread_id == payload["thread_id"]


def test_initial_checkpoint_failure_marks_exact_thread_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _RuntimeGraph(lose_response=True, checkpoint=False)
    resources = _runtime_resources(graph)
    run = SimpleNamespace(
        agent_run_id="RUN-0000000000000009",
        status=RunStatus.RUNNING,
        evidence={},
    )
    finished: list[tuple[str, RunStatus]] = []
    monkeypatch.setattr(
        runtime_module,
        "get_agent_run_by_thread_exact",
        lambda *_args: run,
    )
    monkeypatch.setattr(
        runtime_module,
        "merge_run_action_provenance",
        lambda *_args, **_kwargs: run,
    )
    monkeypatch.setattr(
        runtime_module,
        "finish_agent_run_with_active_latency",
        lambda _connection, run_id, status, **_kwargs: finished.append(
            (run_id, status)
        ),
    )
    runtime = AgentRuntime(
        factory=lambda _model: resources,
        llm_preflight=lambda: "configured-model",
    )

    with pytest.raises(RuntimeError, match="response lost"):
        runtime.start_run(AlarmRef(source="TRACE", alarm_id="TRACE-1"))

    assert finished == [("RUN-0000000000000009", RunStatus.FAILED)]


def test_runtime_preflight_failure_never_builds_resources() -> None:
    built: list[str] = []
    runtime = AgentRuntime(
        factory=lambda model: built.append(model),  # type: ignore[arg-type,return-value]
        llm_preflight=lambda: (_ for _ in ()).throw(RuntimeError("secret")),
    )

    with pytest.raises(runtime_module.AgentRuntimeError) as caught:
        runtime.preflight()

    assert caught.value.code == "LLM_NOT_READY"
    assert built == []


def test_new_run_preflight_warms_embedding_before_resource_build() -> None:
    graph = _RuntimeGraph()
    resources = _runtime_resources(graph)
    order: list[str] = []
    runtime = AgentRuntime(
        factory=lambda _model: order.append("resources") or resources,
        llm_preflight=lambda: order.append("llm") or "configured-model",
        embedding_preflight=lambda: order.append("embedding"),
    )

    assert runtime.preflight() is resources
    assert order == ["llm", "embedding", "resources"]


def test_embedding_preflight_failure_never_builds_resources() -> None:
    built: list[str] = []
    runtime = AgentRuntime(
        factory=lambda model: built.append(model),  # type: ignore[arg-type,return-value]
        llm_preflight=lambda: "configured-model",
        embedding_preflight=lambda: (_ for _ in ()).throw(RuntimeError("secret")),
    )

    with pytest.raises(runtime_module.AgentRuntimeError) as caught:
        runtime.preflight()

    assert caught.value.code == "RAG_MODEL_NOT_READY"
    assert built == []


def test_resume_resource_build_does_not_require_remote_llm_preflight() -> None:
    graph = _RuntimeGraph()
    resources = _runtime_resources(graph)
    remote_calls: list[str] = []
    runtime = AgentRuntime(
        factory=lambda _model: resources,
        llm_preflight=lambda: remote_calls.append("remote") or "configured-model",
        model_config=lambda: "configured-model",
        embedding_preflight=lambda: remote_calls.append("embedding"),
    )

    assert runtime.resources() is resources
    assert remote_calls == []


def test_runtime_close_is_idempotent_and_pool_closes_after_executor_error() -> None:
    graph = _RuntimeGraph()
    pool = _Closable()
    executor = _Closable(shutdown_error=True)

    @contextmanager
    def transactions() -> Any:
        yield object()

    resources = RuntimeResources(
        graph=graph,
        transactions=transactions,
        resume_connections=transactions,
        checkpoint_pool=pool,
        deadline_executor=executor,  # type: ignore[arg-type]
        llm_model="configured-model",
    )
    runtime = AgentRuntime(
        factory=lambda _model: resources,
        llm_preflight=lambda: "configured-model",
    )
    runtime.preflight()

    with pytest.raises(RuntimeError, match="shutdown failed"):
        runtime.close()
    runtime.close()

    assert executor.shutdowns == 1
    assert pool.closed == 1


def test_runtime_close_prevents_late_resource_or_ask_rebuild() -> None:
    built: list[str] = []
    asked: list[str] = []
    runtime = AgentRuntime(
        factory=lambda model: built.append(model),  # type: ignore[arg-type,return-value]
        ask_factory=lambda: asked.append("ask"),  # type: ignore[arg-type,return-value]
        model_config=lambda: "configured-model",
    )
    runtime.close()

    for call in (runtime.resources, lambda: runtime.ask_public("question")):
        with pytest.raises(runtime_module.AgentRuntimeError) as caught:
            call()
        assert caught.value.code == "AGENT_RUNTIME_CLOSED"

    assert built == []
    assert asked == []
