"""V5-C-5.1 묶음 3 — read-only Agent Ask 계약."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent import ask as ask_module
from app.agent import router as agent_router
from app.agent.ask import (
    AgentAskContractError,
    AgentAskService,
    AgentAskUnavailable,
    AskReadTools,
    AskSynthesis,
    StructuredAskReadTools,
    extract_ask_identifiers,
    synthesize_ask_response,
)
from app.agent.public_schemas import (
    AgentAskResponse,
    DocumentAskEvidence,
    GraphAskEvidence,
    MetrologyAskEvidence,
)
from app.agent.runtime_composition import AgentRuntime
from app.common import llm
from app.common.tool_contracts import (
    DocumentHit,
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolInput,
    EquipmentContextToolResult,
    FdcSummaryToolInput,
    FdcSummaryToolResult,
    ParameterSummaryItem,
    WaferContext,
    fail,
)


def _fdc_success() -> FdcSummaryToolResult:
    return FdcSummaryToolResult(
        ok=True,
        wafer=WaferContext(
            lot_hist_id="LH-00181",
            lot_id="LOT004",
            wafer_no=6,
            chamber_id="EQP04-PM2",
            equipment_id="EQP04",
            step_id="CT-ETCH",
            recipe_id="RECIPE03",
        ),
        parameters=[
            ParameterSummaryItem(
                parameter_id="ET_REFL",
                parameter_name="RF reflected power",
                recipe_step_no=2,
                point_cnt=3,
                ooc_point_cnt=1,
                oos_point_cnt=2,
                alarm_type="OOS",
            )
        ],
    )


def _equipment_success() -> EquipmentContextToolResult:
    return EquipmentContextToolResult(
        ok=True,
        chamber_id="EQP04-PM2",
        equipment_id="EQP04",
        sibling_chamber_ids=["EQP04-PM1"],
        area="Etch",
        model_code="ET-7500",
        process_step_id="CT-ETCH",
        graph_revision="a" * 64,
    )


def _documents_success(*, empty: bool = False) -> DocumentSearchToolResult:
    hits = (
        []
        if empty
        else [
            DocumentHit(
                chunk_id="DOC-TROUBLE-FDC:cs1:0001",
                document_id="DOC-TROUBLE-FDC",
                title="FDC troubleshooting guide",
                section=None,
                score=0.91,
                content="RFM diagnosis uses reflected power and matching evidence.",
                model_code="ET-7500",
            )
        ]
    )
    return DocumentSearchToolResult(ok=True, hits=hits)


class _ReadTools:
    def __init__(
        self,
        *,
        fdc: FdcSummaryToolResult | None = None,
        equipment: EquipmentContextToolResult | None = None,
        documents: DocumentSearchToolResult | None = None,
    ) -> None:
        self.fdc = fdc or _fdc_success()
        self.equipment = equipment or _equipment_success()
        self.documents = documents or _documents_success()
        self.calls: list[object] = []

    def get_fdc_summary(self, request: FdcSummaryToolInput) -> FdcSummaryToolResult:
        self.calls.append(request)
        return self.fdc

    def get_equipment_context(
        self, request: EquipmentContextToolInput
    ) -> EquipmentContextToolResult:
        self.calls.append(request)
        return self.equipment

    def search_documents(
        self, request: DocumentSearchToolInput
    ) -> DocumentSearchToolResult:
        self.calls.append(request)
        return self.documents


def _synthesis(
    _question: str,
    evidence: tuple[Any, ...],
) -> AskSynthesis:
    return AskSynthesis(
        title="EQP04-PM2 anomaly analysis",
        answer="The returned FDC and document evidence support an RFM hypothesis.",
        predicted_fault_code="RFM",
        confidence=0.84,
        recommended_action="EQP_HOLD",
        evidence_source_ids=[item.source_id for item in evidence],
    )


def test_identifier_extraction_uses_only_explicit_supported_formats() -> None:
    found = extract_ask_identifiers(
        "Check lh-00181 from lot004 on eqp04-pm2 using et-7500"
    )

    assert found.lot_hist_id == "LH-00181"
    assert found.lot_id == "LOT004"
    assert found.chamber_id == "EQP04-PM2"
    assert found.model_code == "ET-7500"
    assert not extract_ask_identifiers("Why was that chamber held?").any_recognized
    # lot ID를 lot_hist_id로 추측하거나 chamber prefix에서 model을 만들지 않는다.
    lot_only = extract_ask_identifiers("Please inspect LOT004")
    assert lot_only.lot_hist_id is None
    assert lot_only.chamber_id is None
    assert lot_only.model_code is None


def test_no_identifier_skips_all_tools_and_llm() -> None:
    tools = _ReadTools()
    synthesis_calls: list[str] = []
    service = AgentAskService(
        tools=tools,
        synthesizer=lambda question, _evidence: synthesis_calls.append(question),  # type: ignore[arg-type,return-value]
    )

    response = service.ask("Why was that equipment held?")

    assert tools.calls == []
    assert synthesis_calls == []
    assert response.evidence_items == []
    assert response.predicted_fault_code is None
    assert response.confidence is None
    assert response.recommended_action is None


def test_lot_id_never_becomes_fdc_input_and_is_reported_as_a_limitation() -> None:
    tools = _ReadTools()
    response = AgentAskService(tools=tools, synthesizer=_synthesis).ask(
        "Explain LOT004"
    )

    assert [type(call) for call in tools.calls] == [DocumentSearchToolInput]
    assert any("lot_hist_id" in item for item in response.limitations)


def test_runtime_lazily_builds_ask_without_graph_or_checkpoint_resources() -> None:
    builds: list[str] = []
    service = AgentAskService(tools=_ReadTools(), synthesizer=_synthesis)
    runtime = AgentRuntime(
        factory=lambda _model: (_ for _ in ()).throw(
            AssertionError("Ask must not build graph resources")
        ),
        ask_factory=lambda: builds.append("ask") or service,
        model_config=lambda: (_ for _ in ()).throw(
            AssertionError("Ask must not require run LLM preflight")
        ),
    )

    first = runtime.ask_public("Explain ET-7500")
    second = runtime.ask_public("Explain ET-7500")

    assert first.title == second.title == "EQP04-PM2 anomaly analysis"
    assert builds == ["ask"]


def test_selected_tools_use_real_input_dtos_and_only_cited_evidence() -> None:
    tools = _ReadTools()
    service = AgentAskService(tools=tools, synthesizer=_synthesis)

    response = service.ask("Why did LH-00181 on EQP04-PM2 fail?")
    payload = response.model_dump(mode="json")

    assert [type(call) for call in tools.calls] == [
        FdcSummaryToolInput,
        EquipmentContextToolInput,
        DocumentSearchToolInput,
    ]
    assert tools.calls[0].lot_hist_id == "LH-00181"  # type: ignore[union-attr]
    assert tools.calls[1].chamber_id == "EQP04-PM2"  # type: ignore[union-attr]
    assert tools.calls[2].model_code == "ET-7500"  # type: ignore[union-attr]
    assert [item["type"] for item in payload["evidence_items"]] == [
        "TRACE",
        "DOCUMENT",
    ]
    assert payload["evidence"] == {
        "doc_id": "DOC-TROUBLE-FDC",
        "document_id": "DOC-TROUBLE-FDC",
        "chunk_id": "DOC-TROUBLE-FDC:cs1:0001",
        "section": None,
    }
    assert "GRAPH" not in {item["type"] for item in payload["evidence_items"]}
    for forbidden in (
        "ground_truth",
        "alarm_result",
        "HIDDEN_GOLD",
        "MES_MOCK",
        "input",
        "output",
        "request_hash",
    ):
        assert forbidden not in str(payload)


def test_public_response_has_exact_chat_key_allowlist() -> None:
    response = AgentAskService(tools=_ReadTools(), synthesizer=_synthesis).ask(
        "Explain ET-7500"
    )
    payload = response.model_dump(mode="json")

    assert set(payload) == {
        "title",
        "answer",
        "tools",
        "predicted_fault_code",
        "confidence",
        "recommended_action",
        "evidence_items",
        "limitations",
        "evidence",
        "limit",
    }
    assert set(payload["tools"][0]) == {
        "tool_name",
        "status",
        "result_summary",
        "name",
        "result",
    }
    assert "n" not in payload["tools"][0]
    assert "s" not in payload["tools"][0]
    assert "fault_code" not in payload


def test_evidence_union_requires_exact_type_specific_provenance() -> None:
    with pytest.raises(ValidationError):
        GraphAskEvidence(
            type="GRAPH",
            source_id="REL-1",
            title="graph",
            excerpt="context",
            graph_revision="a" * 64,
        )
    with pytest.raises(ValidationError):
        DocumentAskEvidence(
            type="DOCUMENT",
            source_id="CHUNK-1",
            title="doc",
            excerpt="evidence",
            document_id="DOC-1",
            chunk_id="CHUNK-1",
        )
    with pytest.raises(ValidationError):
        MetrologyAskEvidence(
            type="METROLOGY",
            source_id="MET-1",
            title="metrology",
            excerpt="measurement",
            alarm_result="FAIL",  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    ("fdc_result", "document_result", "raises"),
    [
        (
            fail(FdcSummaryToolResult, "TIMEOUT: fdc"),
            fail(DocumentSearchToolResult, "DEPENDENCY_ERROR: rag"),
            True,
        ),
        (
            fail(FdcSummaryToolResult, "NOT_FOUND: lot_hist_id=LH-99999"),
            fail(DocumentSearchToolResult, "DEPENDENCY_ERROR: rag"),
            False,
        ),
    ],
)
def test_tool_soft_hard_matrix_is_deterministic(
    fdc_result: FdcSummaryToolResult,
    document_result: DocumentSearchToolResult,
    raises: bool,
) -> None:
    service = AgentAskService(
        tools=_ReadTools(fdc=fdc_result, documents=document_result),
        synthesizer=_synthesis,
    )

    if raises:
        with pytest.raises(AgentAskUnavailable):
            service.ask("Inspect LH-99999")
    else:
        response = service.ask("Inspect LH-99999")
        assert response.evidence_items == []
        assert response.predicted_fault_code is None
        assert len(response.limitations) == 2


def test_usable_evidence_keeps_200_when_another_tool_fails() -> None:
    tools = _ReadTools(
        equipment=fail(
            EquipmentContextToolResult,
            "DEPENDENCY_ERROR: graph",
        )
    )
    response = AgentAskService(tools=tools, synthesizer=_synthesis).ask(
        "Inspect LH-00181 and EQP04-PM2"
    )

    assert response.evidence_items
    assert any("get_equipment_context" in item for item in response.limitations)
    assert [item.status.value for item in response.tools] == [
        "SUCCESS",
        "ERROR",
        "SUCCESS",
    ]


def test_duplicate_tool_evidence_id_fails_before_llm() -> None:
    duplicate = _documents_success()
    duplicate.hits.append(duplicate.hits[0].model_copy())
    synthesis_calls: list[str] = []
    service = AgentAskService(
        tools=_ReadTools(documents=duplicate),
        synthesizer=lambda question, _evidence: synthesis_calls.append(question),  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(AgentAskContractError, match="ASK_EVIDENCE_ID_DUPLICATED"):
        service.ask("Explain ET-7500")
    assert synthesis_calls == []


def test_structured_synthesis_retries_invalid_citation_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: Iterator[str] = iter(
        [
            '{"title":"x","answer":"x","predicted_fault_code":null,'
            '"confidence":null,"recommended_action":null,'
            '"evidence_source_ids":["UNKNOWN"]}',
            '{"title":"x","answer":"x","predicted_fault_code":"RFM",'
            '"confidence":0.8,"recommended_action":"WARNING",'
            '"evidence_source_ids":["DOC:cs1:0001"]}',
        ]
    )
    monkeypatch.setattr(ask_module.llm, "chat", lambda _messages: next(replies))
    evidence = (
        DocumentAskEvidence(
            type="DOCUMENT",
            source_id="DOC:cs1:0001",
            title="doc",
            excerpt="evidence",
            document_id="DOC",
            chunk_id="DOC:cs1:0001",
            section=None,
        ),
    )

    result = synthesize_ask_response("Explain ET-7500", evidence)

    assert result.evidence_source_ids == ["DOC:cs1:0001"]


def test_structured_synthesis_maps_provider_failure_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ask_module.llm,
        "chat",
        lambda _messages: (_ for _ in ()).throw(llm.LlmNotReadyError("secret")),
    )
    evidence = (
        DocumentAskEvidence(
            type="DOCUMENT",
            source_id="DOC:cs1:0001",
            title="doc",
            excerpt="evidence",
            document_id="DOC",
            chunk_id="DOC:cs1:0001",
            section=None,
        ),
    )

    with pytest.raises(AgentAskUnavailable, match="ASK_LLM_UNAVAILABLE"):
        synthesize_ask_response("Explain ET-7500", evidence)


class _ToolStub:
    def __init__(self, result: object) -> None:
        self.result = result
        self.inputs: list[dict[str, object]] = []

    def invoke(self, payload: dict[str, object]) -> object:
        self.inputs.append(payload)
        return self.result


def test_structured_adapter_invokes_only_three_read_tools_with_dto_payloads() -> None:
    fdc = _ToolStub(_fdc_success())
    equipment = _ToolStub(_equipment_success())
    documents = _ToolStub(_documents_success())
    adapter = StructuredAskReadTools(fdc, equipment, documents)

    adapter.get_fdc_summary(FdcSummaryToolInput(lot_hist_id="LH-00181"))
    adapter.get_equipment_context(EquipmentContextToolInput(chamber_id="EQP04-PM2"))
    adapter.search_documents(
        DocumentSearchToolInput(query="ET-7500", model_code="ET-7500", top_k=4)
    )

    assert fdc.inputs == [{"lot_hist_id": "LH-00181"}]
    assert equipment.inputs == [{"chamber_id": "EQP04-PM2"}]
    assert documents.inputs == [
        {"query": "ET-7500", "model_code": "ET-7500", "top_k": 4}
    ]
    assert {name for name in AskReadTools.__dict__ if not name.startswith("_")} == {
        "get_fdc_summary",
        "get_equipment_context",
        "search_documents",
    }
    source = inspect.getsource(ask_module.AgentAskService)
    assert "AuditedToolExecutor" not in source
    assert "app.agent.repository" not in inspect.getsource(ask_module)


class _AskRuntime:
    def ask_public(
        self,
        question: str,
        *,
        context_evidence: tuple[object, ...] = (),
    ) -> AgentAskResponse:
        return AgentAskService(tools=_ReadTools(), synthesizer=_synthesis).ask(
            question,
            context_evidence=context_evidence,  # type: ignore[arg-type]
        )


def _client(runtime: object) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.include_router(agent_router.router)
    app.dependency_overrides[agent_router.get_agent_runtime] = lambda: runtime
    return app, TestClient(app, raise_server_exceptions=False)


def test_post_ask_route_and_openapi_complete_five_public_agent_endpoints() -> None:
    app, client = _client(_AskRuntime())

    response = client.post("/agent/ask", json={"question": "Explain ET-7500"})
    invalid = client.post("/agent/ask", json={"question": " "})

    assert response.status_code == 200
    assert invalid.status_code == 422
    paths = app.openapi()["paths"]
    assert {method.upper() for method in paths["/agent/runs"]} >= {"GET", "POST"}
    assert "post" in paths["/agent/ask"]
    assert "get" in paths["/approvals"]
    assert "post" in paths["/approvals/{approval_id}/decision"]


def test_post_ask_maps_dependency_failure_to_sanitized_503() -> None:
    class _UnavailableRuntime:
        def ask_public(self, _question: str) -> AgentAskResponse:
            raise AgentAskUnavailable("dsn=secret")

    with pytest.raises(Exception) as caught:
        agent_router.ask_agent(
            agent_router.AgentAskRequest(question="Explain ET-7500"),
            _UnavailableRuntime(),  # type: ignore[arg-type]
        )

    assert getattr(caught.value, "status_code", None) == 503
    assert "secret" not in str(caught.value)


def test_run_context_ask_uses_saved_public_detail_before_read_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = SimpleNamespace(
        agent_run_id="RUN-1",
        chamber_id="EQP01-PM1",
        diagnosis=SimpleNamespace(
            cause_summary="RFM 가능성이 가장 높습니다.",
            evidence_synthesis="FDC와 문서 근거가 일치합니다.",
            observations=("세 WAFER에서 같은 이상이 반복되었습니다.",),
            verification_steps=("RF match를 확인합니다.",),
            limitations=("조치 후 관측값은 없습니다.",),
        ),
        impact_scope=SimpleNamespace(direct=()),
        evidence_items=(),
    )

    class _Connection:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    class _Engine:
        def connect(self) -> _Connection:
            return _Connection()

    monkeypatch.setattr(agent_router, "get_app_engine", lambda: _Engine())
    monkeypatch.setattr(
        agent_router,
        "load_public_agent_run_detail",
        lambda _connection, run_id: detail if run_id == "RUN-1" else None,
    )

    response = agent_router.ask_agent(
        agent_router.AgentAskRequest(
            question="Explain this stored run",
            agent_run_id="RUN-1",
        ),
        _AskRuntime(),  # type: ignore[arg-type]
    )

    assert [item.type for item in response.evidence_items] == ["AGENT_RUN"]
    assert response.evidence_items[0].source_id == "RUN-1"
    assert "FDC와 문서 근거" in response.evidence_items[0].excerpt


def test_run_context_rejects_an_explicit_identifier_outside_saved_evidence() -> None:
    detail = SimpleNamespace(
        chamber_id="EQP01-PM1",
        impact_scope=SimpleNamespace(direct=(SimpleNamespace(source_id="LOT001"),)),
        evidence_items=(),
    )

    with pytest.raises(Exception) as caught:
        agent_router._validate_ask_context_identifiers("Explain LOT999", detail)

    assert getattr(caught.value, "code", None).value == "VALIDATION_ERROR"
    assert caught.value.details == {"reason": "AGENT_RUN_CONTEXT_IDENTIFIER_CONFLICT"}


def test_response_rejects_prediction_without_evidence() -> None:
    with pytest.raises(ValidationError, match="근거가 없으면"):
        AgentAskResponse(
            title="unsupported",
            answer="unsupported",
            tools=[],
            predicted_fault_code="RFM",
            confidence=0.8,
            recommended_action="WARNING",
            evidence_items=[],
            limitations=[],
            evidence=None,
            limit="",
        )


def test_invalid_llm_contract_is_sanitized_internal_error() -> None:
    class _InvalidRuntime:
        def ask_public(self, _question: str) -> AgentAskResponse:
            raise AgentAskContractError("raw provider response=secret")

    _app, client = _client(_InvalidRuntime())
    response = client.post("/agent/ask", json={"question": "Explain ET-7500"})

    assert response.status_code == 500
    assert "secret" not in response.text
