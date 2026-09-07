"""V5-C-7.1 selector control-flow contracts, not evidence of live-model quality.

Synthetic P1 observations drive a local completion double through the real
selector messages, parser, guards and production graph. No benchmark fixture,
evaluation label, live provider, database or delivery service is used here.
"""

import json
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agent import react
from app.agent.hypothesis import generate_hypothesis
from app.common.enums import RunStatus, ToolCallStatus
from app.common.llm import ChatCompletion
from app.common.tool_contracts import DocumentHit, DocumentSearchToolResult
from tests.unit import test_agent_hypothesis as hypothesis_fixture
from tests.unit import test_agent_react as fixture


def _route_with_adjacent(count=1):
    route = fixture._level3_route()
    wafer = route.wafer_routes[0]
    steps = wafer.steps + tuple(
        replace(
            wafer.steps[0],
            lot_hist_id=f"LH-NEXT-{index}",
            step_id=f"STEP-NEXT-{index}",
            chamber_id="EQP04-PM1",
            equipment_id="EQP04",
        )
        for index in range(1, count + 1)
    )
    return replace(route, wafer_routes=(replace(wafer, steps=steps),))


class _MeasuredTools(fixture._InvestigationTools):
    def __init__(self, mean=12.0):
        super().__init__()
        self.mean = mean

    def fdc_summary(self, run_id, request):
        result = super().fdc_summary(run_id, request)
        parameter = result.parameters[0].model_copy(
            update={
                "parameter_id": "P1",
                "parameter_name": "P1",
                "value_mean": self.mean,
                "value_min": self.mean,
                "value_max": self.mean,
                "ctrl_lower": 1.0,
                "ctrl_upper": 9.0,
                "spec_lower": 0.0,
                "spec_upper": 10.0,
                "point_cnt": 4,
                "ooc_point_cnt": 4 if self.mean < 1 or self.mean > 9 else 0,
                "oos_point_cnt": 4 if self.mean < 0 or self.mean > 10 else 0,
                "alarm_type": "OOS" if self.mean < 0 or self.mean > 10 else "IN",
            }
        )
        wafer = result.wafer.model_copy(update={"lot_hist_id": request.lot_hist_id})
        if request.lot_hist_id != "LH-REP":
            wafer = wafer.model_copy(
                update={
                    "chamber_id": "EQP04-PM1",
                    "equipment_id": "EQP04",
                    "step_id": "STEP-NEXT-" + request.lot_hist_id.rsplit("-", 1)[1],
                }
            )
        return result.model_copy(update={"wafer": wafer, "parameters": [parameter]})

    def document_search(self, run_id, request):
        self.calls.append(("documents", request))
        return DocumentSearchToolResult(
            ok=True,
            hits=[
                DocumentHit(
                    chunk_id="DOC-P1-CHECK",
                    document_id="DOC-P1",
                    title="파라미터 점검",
                    section="관측 비교",
                    score=0.8,
                    content="P1 현재값과 과거값의 차이를 점검 근거로 비교한다.",
                    model_code=request.model_code,
                )
            ],
        )

    def history(self, run_id):
        # The production boundary persists every read. Extend the base fake's
        # audit ledger for its two investigation ports as well.
        names = {
            "history": "get_chamber_parameter_history",
            "metrology": "get_metrology_result",
        }
        return super().history(run_id) + tuple(
            SimpleNamespace(
                tool_name=names[name],
                input=request.model_dump(mode="json"),
                status=ToolCallStatus.SUCCESS,
            )
            for name, request in self.calls
            if name in names
        )


def _rows(payload, kind):
    table = payload["candidates"][kind]
    return [dict(zip(table["columns"], row, strict=True)) for row in table["rows"]]


def _candidate_arguments(payload, tool):
    if tool == "search_documents":
        return {"query": "P1 관측값 관리 기준"}
    if tool in {"get_equipment_context", "stop"}:
        return {}
    kind, key = {
        "get_fdc_summary": ("fdc", "fdc_candidate_id"),
        "get_chamber_parameter_history": ("history", "history_candidate_id"),
        "get_metrology_result": ("metrology", "metrology_candidate_id"),
    }[tool]
    candidate = next(row for row in _rows(payload, kind) if not row["observed"])
    return {key: candidate["id"]}


def _selection_port(decide, observed):
    def complete(messages, **kwargs):
        assert kwargs["json_schema"] == react.REACT_SELECT_SCHEMA
        assert [message["role"] for message in messages] == ["system", "user"]
        payload = json.loads(messages[1]["content"])
        observed.append(payload)
        tool = decide(payload)
        assert tool in payload["budget"]["available_tools"]
        response = react.ReactSelection(
            rationale_summary="P1 관측과 조회 가능 범위를 바탕으로 선택한다",
            next=tool,
            arguments=react.ReactArguments(**_candidate_arguments(payload, tool)),
            stop_reason="관측 근거와 추가 조회 가치를 확인해 종료한다"
            if tool == "stop"
            else None,
        )
        return ChatCompletion(
            content=response.model_dump_json(),
            model="fixture-model",
            prompt_tokens=7,
            completion_tokens=3,
        )

    return lambda context: react.select_next_step(context, completion_port=complete)


@pytest.mark.parametrize(
    "mean,expected",
    [
        (-2.0, "get_chamber_parameter_history"),
        (5.0, "get_metrology_result"),
        (12.0, "search_documents"),
        (14.0, "get_equipment_context"),
        (20.0, "get_fdc_summary"),
    ],
)
def test_production_graph_observation_content_changes_next_tool(
    monkeypatch, mean, expected
):
    monkeypatch.setattr(fixture, "NOW", fixture.NOW.replace(tzinfo=None))
    seen = []

    def decide(payload):
        if payload["observations"]["recent_tools"]:
            return "stop"
        # A deliberately simple test controller, not an asserted process policy:
        # it reads the actual P1 value, never the case parameter.
        text = payload["observations"]["fdc"][0]
        value = float(re.search(r"P1\(step=1,mean=([^,]+)", text).group(1))
        if value < 0:
            return "get_chamber_parameter_history"
        if value <= 10:
            return "get_metrology_result"
        if value <= 13:
            return "search_documents"
        if value <= 15:
            return "get_equipment_context"
        return "get_fdc_summary"

    (graph, tools, _, finishes, _), _ = fixture._level3(
        monkeypatch,
        _selection_port(decide, seen),
        tools=_MeasuredTools(mean),
        level_route=_route_with_adjacent(),
    )
    fixture.harness._invoke(graph, level=3)

    assert len(seen) == 2
    assert finishes[0][0] == RunStatus.COMPLETED.value
    trace = finishes[0][1]["evidence"]["react_trace"]
    assert [(entry["tool"], entry["phase"]) for entry in trace] == [
        (expected, "OBSERVED"),
        ("stop", "STOPPED"),
    ]
    assert all(entry["guard_code"] is None for entry in trace)
    assert len([name for name, _ in tools.calls if name != "send_action"]) == 2


def test_observed_history_and_document_reach_real_hypothesis_after_autonomous_stop(
    monkeypatch,
):
    monkeypatch.setattr(fixture, "NOW", fixture.NOW.replace(tzinfo=None))
    seen, hypothesis_inputs = [], []

    def decide(payload):
        observations = payload["observations"]
        if not observations["history"]:
            assert "P1(step=1,mean=12" in observations["fdc"][0]
            return "get_chamber_parameter_history"
        if not observations["documents"]:
            assert payload["checked_dimensions"]["history"] == "CHECKED"
            return "search_documents"
        assert "P1 현재값과 과거값" in observations["documents"][0]["excerpt"]
        assert observations["document_queries"] == ["P1 관측값 관리 기준"]
        assert payload["budget"]["remaining_tool_calls"] > 0
        return "stop"

    def complete_hypothesis(messages, **kwargs):
        evidence = json.loads(messages[1]["content"].split("\n", 1)[1])
        hypothesis_inputs.append(evidence)
        sources = evidence["diagnostic_snapshot"]["source_ids"]
        return hypothesis_fixture._completion(
            hypothesis_fixture._content(
                cause_summary="P1 관측과 문서를 바탕으로 점검이 필요합니다.",
                supporting_alarms=evidence["route"]["incident"]["member_alarms"],
                supporting_chunk_ids=[
                    hit["chunk_id"] for hit in evidence["document"]["hits"]
                ],
                supporting_relation_ids=[],
                supporting_lot_hist_ids=sources["lot_hist_ids"],
                supporting_parameter_ids=sources["parameter_ids"],
                evidence_synthesis="P1 현재 관측과 과거 집계 및 문서를 비교했습니다.",
            ),
            model="fixture-model",
        )

    (graph, tools, ports, finishes, _), _ = fixture._level3(
        monkeypatch,
        _selection_port(decide, seen),
        tools=_MeasuredTools(),
        level_route=fixture._level3_route(),
    )

    def actual_hypothesis(fdc, graph, docs, route, gaps, investigation):
        assert seen[-1]["observations"]["documents"][0]["chunk_id"] == "DOC-P1-CHECK"
        ports.calls.append("generate_hypothesis")
        return generate_hypothesis(
            fdc,
            graph,
            docs,
            route,
            gaps,
            investigation,
            completion_port=complete_hypothesis,
        )

    monkeypatch.setattr(ports, "generate_hypothesis", actual_hypothesis)
    state = fixture.harness._invoke(graph, level=3)

    assert len(seen) == 3 and len(hypothesis_inputs) == 1
    evidence = hypothesis_inputs[0]
    assert evidence["investigation"]["history"][0]["parameter_id"] == "P1"
    assert evidence["investigation"]["compared"]["history"] == "CHECKED"
    assert evidence["document"]["hits"][0]["chunk_id"] == "DOC-P1-CHECK"
    assert finishes[0][0] == RunStatus.COMPLETED.value
    assert state["hypothesis"].supporting_chunk_ids == ("DOC-P1-CHECK",)
    assert state["hypothesis"].supporting_parameter_ids == ("P1",)
    trace = finishes[0][1]["evidence"]["react_trace"]
    assert [(step["tool"], step["phase"]) for step in trace] == [
        ("get_chamber_parameter_history", "OBSERVED"),
        ("search_documents", "OBSERVED"),
        ("stop", "STOPPED"),
    ]
    assert all(step["guard_code"] is None for step in trace)
    assert ports.calls.index("generate_hypothesis") < ports.calls.index("decide_action")
    assert [name for name, _ in tools.calls if name != "send_action"] == [
        "fdc",
        "history",
        "documents",
    ]


@pytest.mark.parametrize("alternative", ["search_documents", "stop"])
def test_tool_local_exhaustion_preserves_other_choices_in_production_graph(
    monkeypatch, alternative
):
    monkeypatch.setattr(fixture, "NOW", fixture.NOW.replace(tzinfo=None))
    seen = []

    def decide(payload):
        budget = payload["budget"]
        remaining = budget["remaining_by_tool"]["get_fdc_summary"]
        if remaining:
            return "get_fdc_summary"
        assert budget["remaining_tool_calls"] > 0
        assert "get_fdc_summary" not in budget["available_tools"]
        assert any(not row["observed"] for row in _rows(payload, "fdc"))
        if payload["observations"]["documents"]:
            return "stop"
        return alternative

    (graph, tools, _, finishes, _), _ = fixture._level3(
        monkeypatch,
        _selection_port(decide, seen),
        tools=_MeasuredTools(),
        level_route=_route_with_adjacent(count=4),
    )
    fixture.harness._invoke(graph, level=3)

    assert [item["budget"]["remaining_by_tool"]["get_fdc_summary"] for item in seen][
        :4
    ] == [3, 2, 1, 0]
    assert sum(name == "fdc" for name, _ in tools.calls) == 4
    assert sum(name == "documents" for name, _ in tools.calls) == (
        alternative == "search_documents"
    )
    assert finishes[0][0] == RunStatus.COMPLETED.value
    trace = finishes[0][1]["evidence"]["react_trace"]
    assert all(step["guard_code"] is None for step in trace)
    assert trace[-1]["tool"] == "stop" and trace[-1]["phase"] == "STOPPED"
    assert not any(step["stop_reason"] == "GUARD_LIMIT" for step in trace)
