"""V5-C-7.1 isolated development budgets; no live model/database/delivery.

The production graph, selector guard and Tool DTOs run against a local ledger.
This verifies budget wiring, not live-model quality or production deployment.
"""

from dataclasses import FrozenInstanceError, replace

import pytest

from app.agent import experiment, react
from app.agent import graph as subject
from app.agent.investigation_budget import (
    DEVELOPMENT_WIDE,
    STANDARD,
    InvestigationBudget,
)
from app.agent.state import ToolBudget
from app.common.enums import RunStatus, ToolCallStatus
from app.common.tool_contracts import DocumentSearchToolResult
from tests.unit import test_agent_react as fixture
from tests.unit import test_agent_selector_adaptive as adaptive


class _WideTools(adaptive._MeasuredTools):
    def __init__(self, *, read_cap=24, send_budget=2, documents_status="SUCCESS"):
        super().__init__()
        self.read_cap = read_cap
        self.send_budget = send_budget
        self.documents_status = documents_status

    def budget(self, run_id):
        return ToolBudget(
            max_calls=self.read_cap + self.send_budget,
            send_budget=self.send_budget,
            used=len(self.calls),
        )

    def fdc_summary(self, run_id, request):
        result = super().fdc_summary(run_id, request)
        return result.model_copy(
            update={
                "parameters": [
                    result.parameters[0].model_copy(
                        update={"parameter_id": f"P{index}"}
                    )
                    for index in range(1, 9)
                ]
            }
        )

    def document_search(self, run_id, request):
        if self.documents_status == "SUCCESS":
            return super().document_search(run_id, request)
        self.calls.append(("documents", request))
        return DocumentSearchToolResult(
            ok=False,
            reason=(
                "TIMEOUT: fixture"
                if self.documents_status == "TIMEOUT"
                else "DEPENDENCY_ERROR: fixture"
            ),
        )

    def history(self, run_id):
        rows = super().history(run_id)
        for row in rows:
            if row.tool_name == "search_documents":
                row.status = ToolCallStatus(self.documents_status)
        return rows


def _build(monkeypatch, port, *, profile=DEVELOPMENT_WIDE, tools=None):
    original = fixture.harness.AgentGraphDependencies
    monkeypatch.setattr(
        fixture.harness,
        "AgentGraphDependencies",
        lambda **kwargs: original(**kwargs, experimental_investigation_budget=profile),
    )
    monkeypatch.setattr(fixture, "NOW", fixture.NOW.replace(tzinfo=None))
    return fixture._level3(
        monkeypatch,
        port,
        tools=_WideTools() if tools is None else tools,
        level_route=adaptive._route_with_adjacent(count=7),
    )[0]


def _invoke(graph):
    return graph.invoke(
        {"requested_alarm": fixture.ALARM, "autonomy_level": 3},
        config={"recursion_limit": 100},
    )


def _full_investigation():
    return [
        *(
            fixture._selection("get_fdc_summary", fdc_candidate_id=f"F{index}")
            for index in range(2, 9)
        ),
        *(
            fixture._selection(
                "get_chamber_parameter_history", history_candidate_id=f"H{index}"
            )
            for index in range(1, 9)
        ),
        *(
            fixture._selection("search_documents", query=f"P{index} 관리 기준")
            for index in range(1, 9)
        ),
    ]


def test_named_profiles_are_immutable_and_do_not_change_runtime_defaults():
    assert (STANDARD.read_cap, STANDARD.selector_cap, STANDARD.same_tool_cap) == (
        8,
        10,
        4,
    )
    assert (
        DEVELOPMENT_WIDE.read_cap,
        DEVELOPMENT_WIDE.selector_cap,
        DEVELOPMENT_WIDE.same_tool_cap,
    ) == (24, 28, 8)
    assert STANDARD.guard_rejection_cap == DEVELOPMENT_WIDE.guard_rejection_cap == 2
    assert STANDARD.send_budget == DEVELOPMENT_WIDE.send_budget == 2
    with pytest.raises(FrozenInstanceError):
        DEVELOPMENT_WIDE.read_cap = 100
    with pytest.raises(ValueError, match="INVESTIGATION_BUDGET_PROFILE_INVALID"):
        replace(DEVELOPMENT_WIDE, read_cap=100)
    with pytest.raises(ValueError, match="INVESTIGATION_BUDGET_PROFILE_INVALID"):
        InvestigationBudget("STANDARD", 8, 10, True)
    assert react.REACT_MAX_STEPS == 10
    assert (
        subject.AgentGraphDependencies.__dataclass_fields__[
            "experimental_investigation_budget"
        ].default
        is None
    )


@pytest.mark.parametrize(
    "errors,expected_reads,expected_selectors,reason",
    [
        (None, 24, 23, "BUDGET_EXHAUSTED"),
        ({1, 3, 5, 7, 9, 11}, 23, 28, "STEP_CAP"),
    ],
)
def test_wide_graph_honors_24_reads_and_28_selector_steps(
    monkeypatch, errors, expected_reads, expected_selectors, reason
):
    port = fixture.ScriptedReactPort(*_full_investigation(), error_at=errors)
    graph, tools, _, finishes, _ = _build(monkeypatch, port)
    _invoke(graph)
    reads = [name for name, _ in tools.calls if name != "send_action"]
    assert len(reads) == expected_reads
    assert len(port.contexts) == expected_selectors
    assert len(reads) > 8 and len(port.contexts) > 10
    assert all(context.max_tool_attempts == 8 for context in port.contexts)
    assert port.contexts[0].remaining_tool_calls == 23
    assert port.contexts[0].remaining_steps == 28
    assert finishes[0][0] == RunStatus.COMPLETED.value
    trace = finishes[0][1]["evidence"]["react_trace"]
    assert trace[-1]["stop_reason"] == reason
    assert {step["guard_code"] for step in trace if step["guard_code"]} == (
        {"REACT_SCHEMA_INVALID"} if errors else set()
    )


@pytest.mark.parametrize("status", ["SUCCESS", "ERROR", "TIMEOUT"])
def test_wide_per_tool_eight_counts_failures_and_guards_ninth(monkeypatch, status):
    port = fixture.ScriptedReactPort(
        *(
            fixture._selection("search_documents", query=f"P{index} 관리 기준")
            for index in range(1, 11)
        )
    )
    graph, tools, _, finishes, _ = _build(
        monkeypatch, port, tools=_WideTools(documents_status=status)
    )
    _invoke(graph)
    assert sum(name == "documents" for name, _ in tools.calls) == 8
    assert port.contexts[-1].tool_attempts["search_documents"] == 8
    assert port.contexts[-1].remaining_tool_calls == 15
    assert finishes[0][1]["evidence"]["react_trace"][-1]["stop_reason"] == "GUARD_LIMIT"


@pytest.mark.parametrize("read_cap,send_budget", [(8, 2), (24, 1)])
def test_mismatched_ledger_fails_before_first_tool_or_model(
    monkeypatch, read_cap, send_budget
):
    port = fixture.ScriptedReactPort(fixture._selection("stop"))
    graph, tools, _, finishes, _ = _build(
        monkeypatch, port, tools=_WideTools(read_cap=read_cap, send_budget=send_budget)
    )
    _invoke(graph)
    assert tools.calls == [] and port.contexts == []
    assert finishes[0][0] == RunStatus.FAILED.value
    assert finishes[0][1]["evidence"]["code"] == "INVESTIGATION_BUDGET_MISMATCH"


def test_ledger_drift_after_selection_blocks_next_tool(monkeypatch):
    tools = _WideTools()
    calls = []

    def select(context):
        calls.append(context)
        tools.read_cap = 8
        return react.ReactSelectionOutcome(
            selection=fixture._selection("search_documents", query="P1 관리 기준"),
            llm_usage=fixture._usage(),
        )

    graph, _, _, finishes, _ = _build(monkeypatch, select, tools=tools)
    _invoke(graph)
    assert [name for name, _ in tools.calls] == ["fdc"]
    assert len(calls) == 1
    assert finishes[0][0] == RunStatus.FAILED.value
    assert finishes[0][1]["evidence"]["code"] == "INVESTIGATION_BUDGET_MISMATCH"


def test_explicit_none_preserves_existing_remaining_and_per_tool_cap(monkeypatch):
    port = fixture.ScriptedReactPort(
        *(
            fixture._selection("search_documents", query=f"P{index} 관리 기준")
            for index in range(1, 7)
        )
    )
    graph, tools, _, finishes, _ = _build(
        monkeypatch, port, profile=None, tools=_WideTools(read_cap=8)
    )
    _invoke(graph)
    assert port.contexts[0].remaining_steps == 10
    assert port.contexts[0].remaining_tool_calls == 7
    assert all(context.max_tool_attempts == 4 for context in port.contexts)
    assert sum(name == "documents" for name, _ in tools.calls) == 4
    assert finishes[0][1]["evidence"]["react_trace"][-1]["stop_reason"] == "GUARD_LIMIT"


@pytest.mark.parametrize("profile", [None, STANDARD, DEVELOPMENT_WIDE])
def test_experiment_factory_passes_profile_and_keeps_effect_seam(monkeypatch, profile):
    built = []

    def build(dependencies, **kwargs):
        built.append((dependencies, kwargs))
        return object()

    monkeypatch.setattr(experiment, "build_agent_graph", build)
    experiment.build_level_graph(
        3,
        selector_port=lambda context: None,
        hypothesis_port=lambda state: None,
        clock=lambda: fixture.NOW,
        tools=_WideTools(),
        transactions=lambda: None,
        routing_graph=None,
        configured_llm_model="fixture-model",
        experimental_investigation_budget=profile,
    )
    dependencies, kwargs = built[0]
    assert dependencies.experimental_investigation_budget is profile
    assert kwargs["interrupt_after"] == ("decide_action",)
    with pytest.raises(RuntimeError, match="EXPERIMENT_EXTERNAL_EFFECT_FORBIDDEN"):
        dependencies.ports.persist_action(None)


def test_development_profile_cannot_be_injected_into_level_two():
    with pytest.raises(ValueError, match="INVESTIGATION_BUDGET_LEVEL_INVALID"):
        experiment.build_level_graph(
            2,
            selector_port=None,
            hypothesis_port=lambda state: None,
            clock=lambda: fixture.NOW,
            tools=_WideTools(),
            transactions=lambda: None,
            routing_graph=None,
            configured_llm_model="fixture-model",
            experimental_investigation_budget=DEVELOPMENT_WIDE,
        )
