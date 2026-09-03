"""V5-C-7.1 Level 3 ReAct — 선택 스키마·가드·흔적·그래프 경로 회귀."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent import react
from app.agent.state import LlmUsage
from app.common.enums import AlarmSource, RunStatus
from app.common.schemas import AlarmRef
from app.common.tool_contracts import DocumentHit, DocumentSearchToolResult
from tests.unit import test_agent_graph as harness

ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")


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
        allowed_lot_hist_ids=("LH-REP", "LH-2"),
        fetched_lot_hist_ids=("LH-REP",),
        fdc_observations=("lot_hist=LH-REP wafer=1 flagged=[P1(ooc=2,oos=0)]",),
        equipment_observation=None,
        document_observations=(),
        remaining_tool_calls=5,
        remaining_steps=6,
        guard_rejections=0,
    )
    base.update(overrides)
    return react.ReactContext(**base)


def _selection(next_: str, **arguments: Any) -> react.ReactSelection:
    return react.ReactSelection(
        thought="다음 근거가 필요하다",
        next=next_,
        arguments=react.ReactArguments(**arguments),
        stop_reason=None,
    )


class ScriptedReactPort:
    """테스트 대본: 호출 순서대로 선택을 돌려주고, 끝나면 stop."""

    def __init__(self, *selections: react.ReactSelection, error_at: int | None = None):
        self._selections = list(selections)
        self._error_at = error_at
        self.contexts: list[react.ReactContext] = []

    def __call__(self, context: react.ReactContext) -> react.ReactSelectionOutcome:
        self.contexts.append(context)
        index = len(self.contexts)
        if self._error_at is not None and index == self._error_at:
            raise react.ReactSelectionError("REACT_STRUCTURE_INVALID", usage=_usage())
        selection = self._selections.pop(0) if self._selections else _selection("stop")
        return react.ReactSelectionOutcome(selection=selection, llm_usage=_usage())


# ---------- 순수 함수 ----------


@pytest.mark.parametrize(
    ("selection", "context_overrides", "equipment_fetched", "expected"),
    [
        (_selection("stop"), {}, False, None),
        (_selection("get_fdc_summary", lot_hist_id="LH-2"), {}, False, None),
        (
            _selection("get_fdc_summary", lot_hist_id="LH-999"),
            {},
            False,
            "REACT_GUARD_TARGET_NOT_ALLOWED",
        ),
        (
            _selection("get_fdc_summary", lot_hist_id="LH-REP"),
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
    assert (
        react.guard_selection(selection, context, equipment_fetched=equipment_fetched)
        == expected
    )


def test_schema_has_no_send_action_and_selection_rejects_unknown_tool() -> None:
    schema = react.REACT_SELECT_SCHEMA["schema"]  # type: ignore[index]
    choices = schema["properties"]["next"]["enum"]  # type: ignore[index]
    assert "send_action" not in choices
    assert set(choices) == {*react.REACT_TOOLS, "stop"}
    with pytest.raises(ValidationError):
        react.ReactSelection.model_validate(
            {
                "thought": "t",
                "next": "send_action",
                "arguments": {},
                "stop_reason": None,
            }
        )


def test_select_next_step_maps_llm_structure_failure_and_records_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = SimpleNamespace(
        content=json.dumps({"thought": "t", "next": "fly", "arguments": {}}),
        model="fixture-model",
        usage=SimpleNamespace(input_tokens=11, output_tokens=2),
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
                "thought": "PH_FOCUS 이탈 스펙을 확인한다",
                "next": "search_documents",
                "arguments": {"lot_hist_id": None, "query": "PH_FOCUS 관리 범위"},
                "stop_reason": None,
            }
        ),
        model="fixture-model",
        usage=SimpleNamespace(input_tokens=11, output_tokens=2),
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
        "allowed_lot_hist_ids",
        "fetched_lot_hist_ids",
        "observations",
        "budget",
    }
    entry = react.trace_entry(
        seq=1,
        selection=_selection("search_documents", query="q" * 500),
        usage=_usage(),
        observation="o" * 1000,
        guard=None,
    )
    assert len(entry.thought) <= 200
    assert entry.observation is not None and len(entry.observation) <= 300
    assert entry.arguments_summary is not None and len(entry.arguments_summary) <= 200
    assert entry.arguments_digest is not None and len(entry.arguments_digest) == 64
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
    assert trace[0]["observation"] is not None
    assert trace[0]["arguments_summary"].startswith("query=")
    assert all(step["guard"] is None for step in trace)
    assert "generate_hypothesis" in ports.calls and "decide_action" in ports.calls
    # 선택 LLM 호출마다 컨텍스트 예산이 줄어드는지
    assert [c.remaining_steps for c in port.contexts] == [6, 5, 4]


def test_level3_guard_rejections_stop_after_limit_without_failing_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = ScriptedReactPort(
        _selection("get_fdc_summary", lot_hist_id="LH-NOPE"),
        _selection("get_fdc_summary", lot_hist_id="LH-NOPE"),
        _selection("search_documents", query="never reached"),
    )
    (graph, tools, _ports, finishes, _), _ = _level3(monkeypatch, port)

    harness._invoke(graph, level=3)

    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]
    trace = finishes[0][1]["evidence"]["react_trace"]
    assert [step["guard"] for step in trace] == [
        "REACT_GUARD_TARGET_NOT_ALLOWED",
        "REACT_GUARD_TARGET_NOT_ALLOWED",
    ]
    assert "documents" not in [name for name, _ in tools.calls]
    assert len(port.contexts) == 2


def test_level3_selection_failure_degrades_to_hypothesis_with_non_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = ScriptedReactPort(error_at=1)
    (graph, _tools, ports, finishes, _), _ = _level3(monkeypatch, port)

    harness._invoke(graph, level=3)

    assert [status for status, _ in finishes] == [RunStatus.COMPLETED.value]
    evidence = finishes[0][1]["evidence"]
    assert "REACT_STRUCTURE_INVALID" in evidence["error_codes"]
    assert evidence["react_trace"] == []
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
