"""V5-C-7.1 Level 3 ReAct — 선택 스키마·가드·흔적·그래프 경로 회귀."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent import react
from app.agent.routing import ResolvedIncidentRoute, WaferRoute
from app.agent.routing_repository import RouteStep
from app.agent.state import LlmUsage
from app.common.enums import AlarmSource, RunStatus, ToolCallStatus
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    ChamberParameterHistoryToolResult,
    DocumentHit,
    DocumentSearchToolResult,
    HistoryBaseline,
    LotAggregate,
    MetrologyResultItem,
    MetrologyResultToolResult,
)
from tests.unit import test_agent_graph as harness

ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _candidates() -> react.ReactCandidates:
    return react.ReactCandidates(
        run_id="RUN-1",
        fdc=(
            react.FdcCandidate(
                candidate_id="F1",
                lot_hist_id="LH-REP",
                wafer_id="W1",
                wafer_ordinal=1,
                relation="CURRENT",
                step_id="CT-PHOTO",
                chamber_id="EQP01-PM1",
                track_in_at=NOW,
            ),
            react.FdcCandidate(
                candidate_id="F2",
                lot_hist_id="LH-2",
                wafer_id="W1",
                wafer_ordinal=1,
                relation="DOWNSTREAM",
                step_id="CT-ETCH",
                chamber_id="EQP04-PM1",
                track_in_at=NOW,
            ),
        ),
        history=(
            react.HistoryCandidate(
                candidate_id="H1",
                scope="CURRENT",
                chamber_id="EQP01-PM1",
                parameter_id="P1",
                step_no=1,
                before=NOW,
                current_lot_id="LOT001",
                incident_step_id="CT-PHOTO",
            ),
        ),
        metrology=(
            react.MetrologyCandidate(
                candidate_id="M1",
                lot_id="LOT001",
                step_id="CT-PHOTO",
                relation="CURRENT",
            ),
        ),
    )


def _usage() -> LlmUsage:
    return LlmUsage(
        model="fixture-model",
        prompt_version=react.REACT_PROMPT_VERSION,
        input_tokens=7,
        output_tokens=3,
    )


def _context(**overrides: Any) -> react.ReactContext:
    base = dict(
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        representative_alarm=ALARM,
        member_alarm_count=1,
        r03_present=False,
        candidates=_candidates(),
        fetched_fdc_candidate_ids=("F1",),
        observed_parameter_keys=(("P1", 1),),
        fdc_observations=("lot_hist=LH-REP wafer=1 flagged=[P1(ooc=2,oos=0)]",),
        equipment_observation=None,
        history_observations=(),
        metrology_observations=(),
        document_observations=(),
        remaining_tool_calls=5,
        remaining_steps=6,
        guard_rejections=0,
    )
    base.update(overrides)
    return react.ReactContext(**base)


def _selection(next_: str, **arguments: Any) -> react.ReactSelection:
    return react.ReactSelection(
        rationale_summary="다음 근거가 필요하다",
        next=next_,
        arguments=react.ReactArguments(**arguments),
        stop_reason=None,
    )


class ScriptedReactPort:
    """테스트 대본: 호출 순서대로 선택을 돌려주고, 끝나면 stop."""

    def __init__(
        self,
        *selections: react.ReactSelection,
        error_at: int | set[int] | None = None,
    ):
        self._selections = list(selections)
        self._error_at = error_at
        self.contexts: list[react.ReactContext] = []

    def __call__(self, context: react.ReactContext) -> react.ReactSelectionOutcome:
        self.contexts.append(context)
        index = len(self.contexts)
        if (isinstance(self._error_at, int) and index == self._error_at) or (
            isinstance(self._error_at, set) and index in self._error_at
        ):
            raise react.ReactSelectionError("REACT_STRUCTURE_INVALID", usage=_usage())
        selection = self._selections.pop(0) if self._selections else _selection("stop")
        return react.ReactSelectionOutcome(selection=selection, llm_usage=_usage())


def _level3_route() -> ResolvedIncidentRoute:
    route_step = RouteStep(
        lot_hist_id="LH-REP",
        lot_id="LOT001",
        wafer_id="LOT001W001",
        wafer_no=1,
        step_id="CT-PHOTO",
        area_id="PHOTO",
        equipment_id="EQP01",
        chamber_id="EQP01-PM1",
        recipe_id="RECIPE01",
        track_in_at=NOW,
        track_out_at=NOW,
    )
    base = harness._route()
    return ResolvedIncidentRoute(
        incident=base.incident,
        wafer_routes=(
            WaferRoute(
                wafer_id="LOT001W001",
                member_alarms=(ALARM,),
                steps=(route_step,),
            ),
        ),
        graph_evidence=base.graph_evidence,
        route_consistency=True,
        mismatches=(),
    )


class _InvestigationTools(harness._FakeTools):
    def chamber_parameter_history(self, _run_id: str, request: Any, **_kwargs: Any):
        self.calls.append(("history", request))
        current = LotAggregate(
            lot_id="LOT001",
            lot_mean=10.0,
            wafer_count=1,
            ooc_wafers=1,
            oos_wafers=0,
            evaluation_missing=0,
            track_in_from=NOW,
            track_in_to=NOW,
        )
        return ChamberParameterHistoryToolResult(
            ok=True,
            scope="CURRENT",
            chamber_id=request.chamber_id,
            parameter_id=request.parameter_id,
            step_no=request.step_no,
            current=current,
            baseline=HistoryBaseline(prior_lot_count=0),
            trend="INSUFFICIENT",
            comparison="CURRENT",
            sample_count=1,
        )

    def metrology_result(self, _run_id: str, request: Any):
        self.calls.append(("metrology", request))
        return MetrologyResultToolResult(
            ok=True,
            lot_id=request.lot_id,
            step_id=request.step_id,
            results=[
                MetrologyResultItem(
                    wafer_id="LOT001W001",
                    measure_type="CD_ADI",
                    measured_value=10.0,
                    spec_lower=9.0,
                    spec_upper=11.0,
                    alarm_result="PASS",
                    measured_at=NOW,
                )
            ],
            fail_count=0,
            disclaimer="계측 PASS/FAIL은 제품 품질 근거이며 Fault Mode 정답이 아니다",
        )


# ---------- 순수 함수 ----------


@pytest.mark.parametrize(
    ("selection", "context_overrides", "equipment_fetched", "expected"),
    [
        (_selection("stop"), {}, False, None),
        (_selection("get_fdc_summary", fdc_candidate_id="F2"), {}, False, None),
        (
            _selection("get_fdc_summary", fdc_candidate_id="F999"),
            {},
            False,
            "REACT_GUARD_CANDIDATE_UNKNOWN",
        ),
        (
            _selection("get_fdc_summary", fdc_candidate_id="F1"),
            {},
            False,
            "REACT_GUARD_TARGET_REPEATED",
        ),
        (_selection("search_documents", query="PH_FOCUS 스펙"), {}, False, None),
        (
            _selection("search_documents", query="   "),
            {},
            False,
            "REACT_GUARD_QUERY_EMPTY",
        ),
        (
            _selection("search_documents", query="x" * 201),
            {},
            False,
            "REACT_GUARD_QUERY_INVALID",
        ),
        (
            _selection("search_documents", query="q"),
            {},
            False,
            "REACT_GUARD_QUERY_REPEATED",
        ),
        (
            _selection("search_documents", query="drop <script>"),
            {},
            False,
            "REACT_GUARD_QUERY_INVALID",
        ),
        (_selection("get_equipment_context"), {}, False, None),
        (
            _selection("get_equipment_context"),
            {},
            True,
            "REACT_GUARD_EQUIPMENT_REPEATED",
        ),
        (
            _selection("search_documents", query="q"),
            {"remaining_tool_calls": 0},
            False,
            "REACT_GUARD_BUDGET_EXHAUSTED",
        ),
    ],
)
def test_guard_selection_enforces_allowlist_budget_and_repetition(
    selection: react.ReactSelection,
    context_overrides: dict[str, Any],
    equipment_fetched: bool,
    expected: str | None,
) -> None:
    context = _context(**context_overrides)
    history = (
        (
            SimpleNamespace(
                tool_name="search_documents",
                input={"query": "q", "model_code": None},
                status=ToolCallStatus.SUCCESS,
            ),
        )
        if expected == "REACT_GUARD_QUERY_REPEATED"
        else ()
    )
    assert (
        react.guard_selection(
            selection,
            context,
            equipment_fetched=equipment_fetched,
            tool_history=history,
        )
        == expected
    )


def test_failed_persisted_call_can_be_reselected() -> None:
    failed = SimpleNamespace(
        tool_name="search_documents",
        input={"query": "q", "model_code": None},
        status=ToolCallStatus.TIMEOUT,
    )
    assert (
        react.guard_selection(
            _selection("search_documents", query="q"),
            _context(),
            equipment_fetched=False,
            tool_history=(failed,),
        )
        is None
    )


def test_failed_tool_observation_is_visible_to_the_next_selection() -> None:
    context = react.build_context(
        run_id="RUN-1",
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        representative_alarm=ALARM,
        member_alarms=(ALARM,),
        route=harness._route(),
        candidates=_candidates(),
        fdc_results=(harness._fdc(),),
        equipment=None,
        documents=(),
        remaining_tool_calls=4,
        remaining_steps=5,
        guard_rejections=0,
        react_trace=(
            {
                "phase": "OBSERVED",
                "tool": "search_documents",
                "observation_summary": "실패 TIMEOUT",
            },
        ),
    )
    assert context.recent_tool_events == ("search_documents: 실패 TIMEOUT",)
    payload = json.loads(react.build_react_select_messages(context)[1]["content"])
    assert payload["observations"]["recent_tools"] == ["search_documents: 실패 TIMEOUT"]


def test_schema_has_no_send_action_and_selection_rejects_unknown_tool() -> None:
    schema = react.REACT_SELECT_SCHEMA["schema"]  # type: ignore[index]
    choices = schema["properties"]["next"]["enum"]  # type: ignore[index]
    assert "send_action" not in choices
    assert set(choices) == {*react.REACT_TOOLS, "stop"}
    with pytest.raises(ValidationError):
        react.ReactSelection.model_validate(
            {
                "rationale_summary": "t",
                "next": "send_action",
                "arguments": {},
                "stop_reason": None,
            }
        )


def test_select_next_step_maps_llm_structure_failure_and_records_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = SimpleNamespace(
        content=json.dumps({"rationale_summary": "t", "next": "fly", "arguments": {}}),
        model="fixture-model",
        prompt_tokens=11,
        completion_tokens=2,
    )
    monkeypatch.setattr(react.llm, "chat_with_usage", lambda *a, **k: completion)
    with pytest.raises(react.ReactSelectionError) as info:
        react.select_next_step(_context())
    assert info.value.code == "REACT_STRUCTURE_INVALID"
    assert info.value.usage_or_none is not None
    assert info.value.usage_or_none.input_tokens == 11

    good = SimpleNamespace(
        content=json.dumps(
            {
                "rationale_summary": "PH_FOCUS 이탈 스펙을 확인한다",
                "next": "search_documents",
                "arguments": {
                    "fdc_candidate_id": None,
                    "history_candidate_id": None,
                    "metrology_candidate_id": None,
                    "query": "PH_FOCUS 관리 범위",
                },
                "stop_reason": None,
            }
        ),
        model="fixture-model",
        prompt_tokens=11,
        completion_tokens=2,
    )
    monkeypatch.setattr(react.llm, "chat_with_usage", lambda *a, **k: good)
    outcome = react.select_next_step(_context())
    assert outcome.selection.next == "search_documents"
    assert outcome.llm_usage.prompt_version == react.REACT_PROMPT_VERSION


def test_prompt_and_trace_never_carry_raw_documents_or_secrets() -> None:
    context = _context(document_observations=("hits=2 [PH-9000 스펙#1. 개요]",))
    messages = react.build_react_select_messages(context)
    assert [m["role"] for m in messages] == ["system", "user"]
    payload = json.loads(messages[1]["content"])
    assert set(payload) == {
        "incident",
        "candidates",
        "observations",
        "budget",
    }
    entry = react.trace_entry(
        seq=1,
        selection=_selection("search_documents", query="q" * 500),
        usage=_usage(),
        phase="OBSERVED",
        observation_summary="o" * 1000,
        canonical_arguments={"query": "q" * 500, "model_code": None},
        argument_summary="문서 검색",
    )
    assert entry.rationale_summary is not None and len(entry.rationale_summary) <= 120
    assert (
        entry.observation_summary is not None and len(entry.observation_summary) <= 160
    )
    assert entry.argument_summary == "문서 검색"
    assert entry.argument_digest is not None and len(entry.argument_digest) == 64
    assert entry.tool == "search_documents"


def test_summaries_cover_ok_and_failed_results() -> None:
    assert react.summarize_documents(None) is None
    assert (
        react.summarize_documents(
            DocumentSearchToolResult(ok=False, reason="TIMEOUT: x")
        )
        is None
    )
    hit = DocumentHit(
        chunk_id="DOC:1",
        document_id="DOC",
        title="스펙",
        section="1",
        score=0.5,
        content="c",
    )
    text = react.summarize_documents(DocumentSearchToolResult(ok=True, hits=[hit]))
    assert text is not None and "hits=1" in text and "스펙#1" in text


# ---------- 그래프 경로 ----------


def _level3(monkeypatch: pytest.MonkeyPatch, port: Any, **kwargs: Any) -> Any:
    ports = harness._Ports()
    ports.react_select = port  # type: ignore[attr-defined]
    return harness._build(monkeypatch, ports=ports, **kwargs), ports


def test_level3_without_react_port_stays_not_implemented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, *_ = harness._build(monkeypatch, ports=harness._Ports())
    with pytest.raises(Exception, match="AUTONOMY_LEVEL_NOT_IMPLEMENTED"):
        harness._invoke(graph, level=3)


def test_level3_react_selects_tools_then_stops_and_records_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = ScriptedReactPort(
        _selection("search_documents", query="PH_FOCUS 관리 범위"),
        _selection("get_equipment_context"),
        _selection("stop"),
    )
    (graph, tools, ports, finishes, _), _ = _level3(monkeypatch, port)

    harness._invoke(graph, level=3)

    tool_names = [name for name, _ in tools.calls]
    assert tool_names[:1] == ["fdc"]
    assert tool_names.count("fdc") == 1
    assert "documents" in tool_names and "equipment" in tool_names
    assert "send_action" in tool_names  # 조치는 규칙대로 그대로 나간다(WARNING → EMAIL)
    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]
    evidence = finishes[0][1]["evidence"]
    assert evidence["autonomy_level"] == 3
    trace = evidence["react_trace"]
    assert [step["tool"] for step in trace] == [
        "search_documents",
        "get_equipment_context",
        "stop",
    ]
    assert trace[0]["observation_summary"] is not None
    assert trace[0]["argument_summary"] == "문서 검색"
    assert all(step["guard_code"] is None for step in trace)
    assert [step["phase"] for step in trace] == [
        "OBSERVED",
        "OBSERVED",
        "STOPPED",
    ]
    assert "generate_hypothesis" in ports.calls and "decide_action" in ports.calls
    # 선택 LLM 호출마다 컨텍스트 예산이 줄어드는지
    assert [c.remaining_steps for c in port.contexts] == [10, 9, 8]
    assert tools.llm_usage == [
        (10, 5)
    ], "selector usage를 hypothesis run 합계에 섞지 않는다"


def test_level3_executes_history_and_metrology_only_through_candidate_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = ScriptedReactPort(
        _selection("get_chamber_parameter_history", history_candidate_id="H1"),
        _selection("get_metrology_result", metrology_candidate_id="M1"),
        _selection("stop"),
    )
    tools = _InvestigationTools()
    (graph, _tools, _ports, finishes, _), _ = _level3(
        monkeypatch,
        port,
        tools=tools,
        level_route=_level3_route(),
    )

    harness._invoke(graph, level=3)

    assert [name for name, _ in tools.calls if name in {"history", "metrology"}] == [
        "history",
        "metrology",
    ]
    trace = finishes[0][1]["evidence"]["react_trace"]
    assert [step["tool"] for step in trace] == [
        "get_chamber_parameter_history",
        "get_metrology_result",
        "stop",
    ]
    assert "trend=INSUFFICIENT" in trace[0]["observation_summary"]
    assert "fail_count=0" in trace[1]["observation_summary"]
    assert "LOT001" not in trace[0]["argument_summary"]
    assert "CT-PHOTO" not in trace[1]["argument_summary"]


def test_level3_guard_rejections_stop_after_limit_without_failing_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = ScriptedReactPort(
        _selection("get_fdc_summary", fdc_candidate_id="F999"),
        _selection("get_fdc_summary", fdc_candidate_id="F999"),
        _selection("search_documents", query="never reached"),
    )
    (graph, tools, _ports, finishes, _), _ = _level3(monkeypatch, port)

    harness._invoke(graph, level=3)

    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]
    trace = finishes[0][1]["evidence"]["react_trace"]
    assert [step["guard_code"] for step in trace[:2]] == [
        "REACT_GUARD_CANDIDATE_UNKNOWN",
        "REACT_GUARD_CANDIDATE_UNKNOWN",
    ]
    assert trace[2]["phase"] == "STOPPED"
    assert trace[2]["stop_reason"] == "GUARD_LIMIT"
    assert trace[2]["degraded"] is True
    assert "documents" not in [name for name, _ in tools.calls]
    assert len(port.contexts) == 2


def test_level3_selection_failure_degrades_to_hypothesis_with_non_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = ScriptedReactPort(error_at={1, 2})
    (graph, _tools, ports, finishes, _), _ = _level3(monkeypatch, port)

    harness._invoke(graph, level=3)

    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]
    evidence = finishes[0][1]["evidence"]
    assert "REACT_STRUCTURE_INVALID" in evidence["error_codes"]
    assert [step["phase"] for step in evidence["react_trace"]] == [
        "REJECTED",
        "STOPPED",
    ]
    assert evidence["react_trace"][-1]["stop_reason"] == "REACT_STRUCTURE_INVALID"
    assert "REACT_DEGRADED_TO_HYPOTHESIS" in evidence["error_codes"]
    assert "generate_hypothesis" in ports.calls


def test_levels_one_and_two_leave_react_trace_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for level in (1, 2):
        graph, *_rest = harness._build(monkeypatch, ports=harness._Ports())
        finishes = _rest[2]
        harness._invoke(graph, level=level)
        evidence = finishes[0][1]["evidence"]
        assert evidence["react_trace"] == []
        assert evidence["autonomy_level"] == level
