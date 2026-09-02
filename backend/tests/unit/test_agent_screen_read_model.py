"""V5-C-5.2 Agent 화면 조립용 공개 조회 회귀."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent import public_read_model as subject
from app.agent import repository as repo
from app.agent import router as agent_router
from app.agent.diagnostics import (
    DiagnosticSourceIds,
    DirectScope,
    IncidentDiagnosticSnapshot,
    ParameterPattern,
)
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
        prompt_version="agent-hypothesis-v1",
        input_tokens=100,
        output_tokens=40,
        prediction_cause_summary="RFM 가능성이 가장 높습니다.",
        prediction_evidence={
            "schema_version": "agent-evidence-v1",
            "supporting_alarms": [{"source": "TRACE", "alarm_id": "TRACE-1"}],
            "supporting_chunk_ids": [],
            "supporting_relation_ids": [],
            "uncertainty": "추가 계측 확인이 필요합니다.",
        },
        prediction_llm_model="model",
        prediction_prompt_version="agent-hypothesis-v1",
        prediction_created_at=NOW,
    )


def _snapshot(
    *,
    lot_id: str,
    chamber_id: str,
    parameters: tuple[str, ...] = ("P1",),
    models: tuple[str, ...] = ("MODEL-1",),
) -> IncidentDiagnosticSnapshot:
    return IncidentDiagnosticSnapshot(
        lot_id=lot_id,
        chamber_id=chamber_id,
        representative_alarm_ref="TRACE:TRACE-1",
        member_alarm_count=1,
        target_wafer_count=1,
        observed_wafer_count=0,
        wafer_observations=(),
        parameter_patterns=tuple(
            ParameterPattern(
                parameter_id=parameter,
                affected_wafer_count=1,
                ooc_point_count=0,
                oos_point_count=1,
                maximum_deviation=1,
                directions=("UPPER",),
            )
            for parameter in parameters
        ),
        step_patterns=(),
        direct_scope=DirectScope(
            lot_ids=(lot_id,),
            wafer_ids=(),
            chamber_ids=(chamber_id,),
            parameter_ids=parameters,
            model_codes=models,
        ),
        data_gaps=(),
        source_ids=DiagnosticSourceIds(
            alarm_refs=("TRACE:TRACE-1",),
            lot_hist_ids=(),
            parameter_ids=parameters,
            relation_ids=(),
            graph_revisions=(),
        ),
    )


def _v2_run(
    run_id: str,
    *,
    lot_id: str,
    chamber_id: str,
    created_at: datetime,
    parameters: tuple[str, ...] = ("P1",),
    models: tuple[str, ...] = ("MODEL-1",),
    retry_of_run_id: str | None = None,
) -> repo.PublicAgentRunRecord:
    snapshot = _snapshot(
        lot_id=lot_id,
        chamber_id=chamber_id,
        parameters=parameters,
        models=models,
    )
    return replace(
        _run(),
        agent_run_id=run_id,
        created_at=created_at,
        chamber_id=chamber_id,
        status=RunStatus.COMPLETED,
        action_id=None,
        approval_id=None,
        prompt_version="agent-hypothesis-v2",
        prediction_evidence={
            "schema_version": "agent-evidence-v2",
            "diagnostic_snapshot": snapshot.model_dump(mode="json"),
        },
        prediction_prompt_version="agent-hypothesis-v2",
        lot_id=lot_id,
        retry_of_run_id=retry_of_run_id,
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
        created_at=NOW,
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
    assert payload["prediction"] == {
        "predicted_fault_code": "RFM",
        "confidence": 0.91,
        "cause_summary": "RFM 가능성이 가장 높습니다.",
        "supporting_alarms": [{"source": "TRACE", "alarm_id": "TRACE-1"}],
        "supporting_chunk_ids": [],
        "supporting_relation_ids": [],
        "uncertainty": "추가 계측 확인이 필요합니다.",
        "llm_model": "model",
        "prompt_version": "agent-hypothesis-v1",
        "input_tokens": 100,
        "output_tokens": 40,
        "generated_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert payload["action"]["deliveries"][0]["started_at"] is not None
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
        alarms=1,
        tools=1,
        action=1,
        approval=1,
    )


def test_v2_snapshot_projects_graph_evidence_when_level2_skips_redundant_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(lot_id="LOT004", chamber_id="EQP04-PM2")
    snapshot = snapshot.model_copy(
        update={
            "source_ids": snapshot.source_ids.model_copy(
                update={
                    "relation_ids": ("REL-1",),
                    "graph_revisions": ("REV-1",),
                }
            )
        }
    )
    monkeypatch.setattr(subject, "list_tool_calls", lambda *_args: [])

    items = subject._stored_tool_evidence(
        object(),
        "RUN-1",
        supporting_relation_ids=("REL-1",),
        diagnostic_snapshot=snapshot,
    )

    assert [item.model_dump(mode="json") for item in items] == [
        {
            "type": "GRAPH",
            "source_id": "REL-1",
            "title": "Graph relation REL-1",
            "excerpt": "relation=REL-1; revision=REV-1",
            "relation_id": "REL-1",
            "graph_revision": "REV-1",
        }
    ]


def test_v2_snapshot_does_not_project_relation_outside_stored_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(lot_id="LOT004", chamber_id="EQP04-PM2")
    snapshot = snapshot.model_copy(
        update={
            "source_ids": snapshot.source_ids.model_copy(
                update={
                    "relation_ids": ("REL-OTHER",),
                    "graph_revisions": ("REV-1",),
                }
            )
        }
    )
    monkeypatch.setattr(subject, "list_tool_calls", lambda *_args: [])

    assert (
        subject._stored_tool_evidence(
            object(),
            "RUN-1",
            supporting_relation_ids=("REL-1",),
            diagnostic_snapshot=snapshot,
        )
        == []
    )


def test_v1_detail_omits_unverifiable_graph_citation_and_marks_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = replace(
        _run(),
        prediction_evidence={
            **(_run().prediction_evidence or {}),
            "supporting_relation_ids": ["REL-LEGACY"],
        },
    )
    monkeypatch.setattr(subject, "get_agent_run_public", lambda *_args: legacy)
    monkeypatch.setattr(
        subject,
        "list_run_alarms",
        lambda *_args: [AlarmRef(source="TRACE", alarm_id="TRACE-1")],
    )
    monkeypatch.setattr(subject, "list_tool_calls", lambda *_args: [])
    monkeypatch.setattr(subject, "get_action_public", lambda *_args: _action())
    monkeypatch.setattr(subject, "get_approval_public", lambda *_args: _approval())

    detail = subject.load_public_agent_run_detail(object(), "RUN-1")

    assert detail.prediction is not None
    assert detail.prediction.supporting_relation_ids == []
    assert detail.evidence_assessment.status == "PARTIAL"
    assert detail.evidence_assessment.reason_codes == (
        "LEGACY_CITATION_PROVENANCE_NOT_AVAILABLE",
    )
    assert detail.evidence_assessment.missing_sources == ("GRAPH",)
    assert detail.evidence_assessment.available_sources == ("POSTGRES_ROUTE",)


def test_run_detail_rejects_non_list_prediction_citations_as_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corrupted = replace(
        _run(),
        prediction_evidence={
            "schema_version": "agent-evidence-v1",
            "supporting_alarms": "TRACE:TRACE-1",
            "supporting_chunk_ids": [],
            "supporting_relation_ids": [],
            "uncertainty": "",
        },
    )
    monkeypatch.setattr(subject, "get_agent_run_public", lambda *_args: corrupted)
    monkeypatch.setattr(
        subject,
        "list_run_alarms",
        lambda *_args: [AlarmRef(source="TRACE", alarm_id="TRACE-1")],
    )
    monkeypatch.setattr(subject, "list_tool_calls", lambda *_args: [])
    monkeypatch.setattr(subject, "get_action_public", lambda *_args: _action())
    monkeypatch.setattr(subject, "get_approval_public", lambda *_args: _approval())

    with pytest.raises(
        repo.RepositoryContractError,
        match="PUBLIC_PREDICTION_EVIDENCE_INVALID",
    ):
        subject.load_public_agent_run_detail(object(), "RUN-1")


def test_similar_incidents_use_first_completed_v2_run_and_exclude_current_and_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _v2_run(
        "RUN-CURRENT",
        lot_id="LOT004",
        chamber_id="EQP04-PM2",
        created_at=NOW,
        parameters=("P1", "P2"),
    )
    first = _v2_run(
        "RUN-FIRST",
        lot_id="LOT005",
        chamber_id="EQP02-PM1",
        created_at=NOW - timedelta(hours=3),
        parameters=("P1", "P2"),
    )
    duplicate_later = _v2_run(
        "RUN-LATER",
        lot_id="LOT005",
        chamber_id="EQP02-PM1",
        created_at=NOW - timedelta(hours=2),
        parameters=("P1", "P2"),
    )
    same_incident = _v2_run(
        "RUN-SAME-INCIDENT",
        lot_id="LOT004",
        chamber_id="EQP04-PM2",
        created_at=NOW - timedelta(hours=4),
    )
    retry = _v2_run(
        "RUN-RETRY",
        lot_id="LOT006",
        chamber_id="EQP06-PM1",
        created_at=NOW - timedelta(hours=1),
        retry_of_run_id="RUN-ORIGINAL",
    )
    legacy = replace(
        _v2_run(
            "RUN-V1",
            lot_id="LOT007",
            chamber_id="EQP01-PM1",
            created_at=NOW - timedelta(minutes=30),
        ),
        prompt_version="agent-hypothesis-v1",
    )
    outside_final_population = _v2_run(
        "RUN-OUTSIDE",
        lot_id="LOT999",
        chamber_id="EQP99-PM1",
        created_at=NOW - timedelta(minutes=10),
    )
    monkeypatch.setattr(
        subject,
        "list_agent_runs_public",
        lambda *_args, **_kwargs: [
            duplicate_later,
            retry,
            current,
            legacy,
            outside_final_population,
            first,
            same_incident,
        ],
    )

    result = subject._similar_incidents(
        object(),
        current,
        _snapshot(
            lot_id="LOT004",
            chamber_id="EQP04-PM2",
            parameters=("P1", "P2"),
        ),
    )

    assert result.status == "AVAILABLE"
    assert [item.agent_run_id for item in result.items] == ["RUN-FIRST"]
    assert result.items[0].score == 100


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
