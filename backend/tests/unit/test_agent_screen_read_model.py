"""V5-C-5.2 Agent 화면 조립용 공개 조회 회귀."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent import public_read_model as subject
from app.agent import repository as repo
from app.agent import router as agent_router
from app.agent.public_schemas import (
    ActionDetailResponse,
    ActionItem,
    AgentRunDetailResponse,
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

NOW = datetime(2026, 8, 29, 13, 0, tzinfo=UTC)
ACTION_WALL_TIME = datetime(2026, 8, 29, 22, 0)


def _run() -> repo.PublicAgentRunRecord:
    return repo.PublicAgentRunRecord(
        agent_run_id="RUN-1",
        created_at=NOW,
        requested_alarm=AlarmRef(source="TRACE", alarm_id="TRACE-1"),
        chamber_id="EQP01-PM1",
        predicted_fault_code=FaultHypothesis.RFM,
        confidence=0.91,
        recommended_action=ActionCode.EQP_HOLD,
        status=RunStatus.WAITING_APPROVAL,
        action_id="ACT-1",
        approval_id="APR-1",
        tools=(
            repo.PublicToolCallRecord(
                tool_name="get_fdc_summary", status=ToolCallStatus.SUCCESS
            ),
        ),
        deliveries=(
            repo.PublicDeliveryRecord(
                channel=DeliveryChannel.EMAIL, status=DeliveryStatus.SENT
            ),
            repo.PublicDeliveryRecord(
                channel=DeliveryChannel.MES_MOCK, status=DeliveryStatus.BLOCKED
            ),
        ),
        latency_ms=100,
        llm_model="model",
    )


def _action() -> repo.PublicActionRecord:
    return repo.PublicActionRecord(
        action_id="ACT-1",
        agent_run_id="RUN-1",
        action_code=ActionCode.EQP_HOLD,
        lot_id="LOT001",
        equipment_id="EQP01",
        chamber_id="EQP01-PM1",
        reason="R03_CONSEC",
        approval_status=ApprovalStatus.PENDING,
        deliveries=(
            repo.PublicActionDeliveryRecord(
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.SENT,
                started_at=NOW,
                completed_at=NOW,
            ),
            repo.PublicActionDeliveryRecord(
                channel=DeliveryChannel.MES_MOCK,
                status=DeliveryStatus.BLOCKED,
                started_at=None,
                completed_at=None,
            ),
        ),
        created_at=ACTION_WALL_TIME,
    )


def _approval() -> repo.PublicApprovalRecord:
    return repo.PublicApprovalRecord(
        approval_id="APR-1",
        agent_run_id="RUN-1",
        action_id="ACT-1",
        created_at=NOW,
        lot_id="LOT001",
        equipment_id="EQP01",
        chamber_id="EQP01-PM1",
        predicted_fault_code=FaultHypothesis.RFM,
        action_code=ActionCode.EQP_HOLD,
        reason="R03_CONSEC",
        status=ApprovalStatus.PENDING,
        decided_by=None,
        decided_at=None,
        decision_comment=None,
    )


def test_action_projection_has_exact_aliases_and_hides_internal_channel() -> None:
    payload = subject._public_action_detail(_action()).model_dump(mode="json")

    assert set(payload) == set(ActionDetailResponse.model_fields)
    assert payload["agent_run_id"] == payload["created_by_agent_run_id"]
    assert payload["equipment_id"] == payload["equipment"]
    assert [item["channel"] for item in payload["deliveries"]] == ["EMAIL", "MES"]
    assert payload["created_at"] == "2026-08-29T22:00:00+09:00"
    assert payload["deliveries"][0]["started_at"] == payload["created_at"]
    assert payload["deliveries"][0]["completed_at"] == payload["created_at"]
    serialized = str(payload)
    for forbidden in (
        "MES_MOCK",
        "request_hash",
        "provider_message_id",
        "last_error",
        "result",
    ):
        assert forbidden not in serialized


def test_public_action_schema_rejects_delivery_matrix_drift() -> None:
    payload = subject._public_action(_action()).model_dump()
    payload["deliveries"] = payload["deliveries"][:1]

    with pytest.raises(ValueError, match="delivery channel 행렬"):
        ActionItem.model_validate(payload)


def test_run_detail_uses_only_valid_stored_outputs_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: Counter[str] = Counter()

    def counted(name: str, value: object):
        def call(*_args: object) -> object:
            calls[name] += 1
            return value

        return call

    monkeypatch.setattr(subject, "get_agent_run_public", counted("run", _run()))
    monkeypatch.setattr(subject, "get_action_public", counted("action", _action()))
    monkeypatch.setattr(
        subject, "get_approval_public", counted("approval", _approval())
    )
    monkeypatch.setattr(
        subject,
        "list_run_alarms",
        counted("alarms", [AlarmRef(source="TRACE", alarm_id="TRACE-1")]),
    )
    monkeypatch.setattr(
        subject,
        "get_prediction_or_none",
        counted(
            "prediction",
            SimpleNamespace(
                evidence={
                    "supporting_alarms": [
                        {"source": "TRACE", "alarm_id": "TRACE-1"},
                        {"source": "TRACE", "alarm_id": ""},
                        {"source": "R03", "alarm_id": "R03-1"},
                    ],
                    "supporting_relation_ids": ["REL-1", None],
                },
            ),
        ),
    )
    monkeypatch.setattr(
        subject,
        "list_tool_calls",
        counted(
            "tools",
            [
                SimpleNamespace(
                    status=ToolCallStatus.SUCCESS,
                    tool_name="get_equipment_context",
                    output={"graph_revision": "secret-only-invalid"},
                ),
                SimpleNamespace(
                    status=ToolCallStatus.ERROR,
                    tool_name="search_documents",
                    output={"raw": "dsn=secret"},
                ),
            ],
        ),
    )

    detail = subject.load_public_agent_run_detail(object(), "RUN-1")
    payload = detail.model_dump(mode="json")

    assert set(payload) == set(AgentRunDetailResponse.model_fields)
    assert [item["source_id"] for item in payload["evidence_items"]] == [
        "TRACE:TRACE-1",
        "R03:R03-1",
    ]
    assert "secret" not in str(payload)
    assert payload["action"]["action_id"] == payload["action_id"]
    assert payload["approval"]["approval_id"] == payload["approval_id"]
    assert set(payload["action"]) == {
        "action_id",
        "agent_run_id",
        "action_code",
        "reason",
        "approval_status",
        "deliveries",
    }
    assert set(payload["approval"]) == {
        "approval_id",
        "action_id",
        "agent_run_id",
        "status",
        "decided_by",
        "decided_at",
        "decision_comment",
    }
    assert calls == Counter(
        run=1,
        prediction=1,
        alarms=1,
        tools=1,
        action=1,
        approval=1,
    )


def test_public_routes_are_bare_and_not_found_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(agent_router.router)
    app.dependency_overrides[agent_router.get_db_connection] = lambda: object()
    monkeypatch.setattr(
        agent_router,
        "list_public_actions",
        lambda *_args, **_kwargs: [subject._public_action(_action())],
    )
    monkeypatch.setattr(
        agent_router,
        "load_public_action_detail",
        lambda *_args: (_ for _ in ()).throw(
            repo.RepositoryNotFound("ACTION_NOT_FOUND", "dsn=secret")
        ),
    )
    client = TestClient(app, raise_server_exceptions=False)

    actions = client.get("/actions?action_code=EQP_HOLD")
    missing = client.get("/actions/ACT-MISSING")

    assert actions.status_code == 200
    assert isinstance(actions.json(), list)
    assert missing.status_code == 404
    assert "secret" not in missing.text


def test_public_action_contract_failure_is_sanitized_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(agent_router.router)
    app.dependency_overrides[agent_router.get_db_connection] = lambda: object()
    monkeypatch.setattr(
        agent_router,
        "list_public_actions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            repo.RepositoryContractError("PUBLIC_ACTION_INVALID", "dsn=secret")
        ),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/actions")

    assert response.status_code == 503
    assert "secret" not in response.text


def test_repository_action_filter_and_sort_are_in_sql() -> None:
    class Result:
        def all(self) -> list[Any]:
            return []

    class Connection:
        def __init__(self) -> None:
            self.statement: Any = None
            self.params: dict[str, Any] = {}

        def execute(self, statement: Any, params: dict[str, Any]) -> Result:
            self.statement = statement
            self.params = params
            return Result()

    connection = Connection()
    assert repo.list_actions_public(connection, action_code=ActionCode.WARNING) == []
    assert connection.params == {"action_code": "WARNING"}
    sql = str(connection.statement)
    assert "action.created_at DESC, action.action_id DESC" in sql
    assert "provider_message_id" not in sql
    assert "request_hash" not in sql


def test_repository_run_filters_are_bound_and_server_side() -> None:
    class Result:
        def all(self) -> list[Any]:
            return []

    class Connection:
        def __init__(self) -> None:
            self.statement: Any = None
            self.params: dict[str, Any] = {}

        def execute(self, statement: Any, params: dict[str, Any]) -> Result:
            self.statement = statement
            self.params = params
            return Result()

    connection = Connection()
    assert (
        repo.list_agent_runs_public(
            connection,
            date_from=None,
            date_to=None,
            status=RunStatus.WAITING_APPROVAL,
            predicted_fault_code=FaultHypothesis.OTH,
        )
        == []
    )
    assert connection.params["run_status"] == "WAITING_APPROVAL"
    assert connection.params["fault_code"] == "OTH"
    sql = str(connection.statement)
    assert "r.status = CAST(:run_status AS text)" in sql
    assert "p.predicted_fault_code = CAST(:fault_code AS text)" in sql


def test_openapi_exposes_the_three_optional_public_components() -> None:
    app = FastAPI()
    app.include_router(agent_router.router)
    schema = app.openapi()

    expected = {
        "/agent/runs/{run_id}": (
            "AgentRunDetailResponse",
            {"200", "404", "422", "503"},
        ),
        "/actions": ("ActionItem", {"200", "422", "503"}),
        "/actions/{action_id}": ("ActionDetailResponse", {"200", "404", "422", "503"}),
    }
    for path, (component, statuses) in expected.items():
        operation = schema["paths"][path]["get"]
        response_schema = operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert component in str(response_schema)
        assert set(operation["responses"]) == statuses

    run_parameters = {
        parameter["name"]
        for parameter in schema["paths"]["/agent/runs"]["get"]["parameters"]
    }
    assert run_parameters == {
        "date_from",
        "date_to",
        "status",
        "predicted_fault_code",
    }

    detail_fields = set(
        schema["components"]["schemas"]["AgentRunDetailResponse"]["properties"]
    )
    assert {"evidence_items", "tools", "approval", "action"} <= detail_fields
