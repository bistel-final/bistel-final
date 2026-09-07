"""Production budget wiring: real graph, local ledger, no LLM or side effects."""

import asyncio

import pytest

from app.agent.graph import CompiledAgentGraph
from app.agent.state import ToolBudget
from app.common.enums import RunStatus
from tests.unit import test_agent_investigation_budget as fixture


class ProductionTools(fixture._WideTools):
    def __init__(self, *, profile="PRODUCTION_WIDE_V1", **kwargs):
        super().__init__(**kwargs)
        self.profile = profile

    def budget(self, run_id):
        return ToolBudget(
            **super().budget(run_id).model_dump(),
            investigation_budget_profile=self.profile,
        )


@pytest.mark.parametrize(
    "errors,reads,selectors,reason",
    [
        (None, 24, 23, "BUDGET_EXHAUSTED"),
        ({1, 3, 5, 7, 9, 11}, 23, 28, "STEP_CAP"),
    ],
)
def test_new_production_run_uses_stored_wide_profile_without_engine_override(
    monkeypatch, errors, reads, selectors, reason
):
    port = fixture.fixture.ScriptedReactPort(
        *fixture._full_investigation(), error_at=errors
    )
    graph, tools, _, finishes, _ = fixture._build(
        monkeypatch, port, profile=None, tools=ProductionTools()
    )
    # No experimental profile and no custom recursion_limit at this call site.
    graph.invoke({"requested_alarm": fixture.fixture.ALARM, "autonomy_level": 3})
    assert len([name for name, _ in tools.calls if name != "send_action"]) == reads
    assert len(port.contexts) == selectors
    assert port.contexts[0].remaining_tool_calls == 23
    assert port.contexts[0].remaining_steps == 28
    assert all(context.max_tool_attempts == 8 for context in port.contexts)
    assert finishes[0][0] == RunStatus.COMPLETED.value
    assert finishes[0][1]["evidence"]["react_trace"][-1]["stop_reason"] == reason


@pytest.mark.parametrize("status", ["SUCCESS", "ERROR", "TIMEOUT"])
def test_production_per_tool_eight_includes_failed_and_timed_out_reads(
    monkeypatch, status
):
    port = fixture.fixture.ScriptedReactPort(
        *(
            fixture.fixture._selection("search_documents", query=f"P{i} 관리 기준")
            for i in range(1, 11)
        )
    )
    graph, tools, _, finishes, _ = fixture._build(
        monkeypatch,
        port,
        profile=None,
        tools=ProductionTools(documents_status=status),
    )
    graph.invoke({"requested_alarm": fixture.fixture.ALARM, "autonomy_level": 3})
    assert sum(name == "documents" for name, _ in tools.calls) == 8
    assert port.contexts[-1].tool_attempts["search_documents"] == 8
    assert port.contexts[-1].remaining_tool_calls == 15
    assert finishes[0][1]["evidence"]["react_trace"][-1]["stop_reason"] == "GUARD_LIMIT"


def test_llm_can_stop_early_without_filling_the_larger_budget(monkeypatch):
    port = fixture.fixture.ScriptedReactPort(fixture.fixture._selection("stop"))
    graph, tools, _, finishes, _ = fixture._build(
        monkeypatch, port, profile=None, tools=ProductionTools()
    )
    graph.invoke({"requested_alarm": fixture.fixture.ALARM, "autonomy_level": 3})
    assert len([name for name, _ in tools.calls if name != "send_action"]) == 1
    assert finishes[0][1]["evidence"]["react_trace"][-1]["stop_reason"] == "LLM_STOP"


@pytest.mark.parametrize(
    "profile,read_cap,send_budget,error_code",
    [
        (None, 24, 2, "INVESTIGATION_BUDGET_MISMATCH"),
        ("PRODUCTION_WIDE_V1", 8, 2, "STATE_CONTRACT_ERROR"),
        ("PRODUCTION_WIDE_V1", 24, 1, "STATE_CONTRACT_ERROR"),
    ],
)
def test_unbound_or_inconsistent_expansion_fails_before_first_read(
    monkeypatch, profile, read_cap, send_budget, error_code
):
    port = fixture.fixture.ScriptedReactPort(fixture.fixture._selection("stop"))
    graph, tools, _, finishes, _ = fixture._build(
        monkeypatch,
        port,
        profile=None,
        tools=ProductionTools(
            profile=profile, read_cap=read_cap, send_budget=send_budget
        ),
    )
    graph.invoke({"requested_alarm": fixture.fixture.ALARM, "autonomy_level": 3})
    assert tools.calls == [] and port.contexts == []
    assert finishes[0][0] == RunStatus.FAILED.value
    assert finishes[0][1]["evidence"]["code"] == error_code


def test_unmarked_legacy_run_keeps_eight_reads_and_four_per_tool(monkeypatch):
    port = fixture.fixture.ScriptedReactPort(
        *(
            fixture.fixture._selection("search_documents", query=f"P{i} 관리 기준")
            for i in range(1, 7)
        )
    )
    graph, tools, _, finishes, _ = fixture._build(
        monkeypatch, port, profile=None, tools=ProductionTools(profile=None, read_cap=8)
    )
    graph.invoke({"requested_alarm": fixture.fixture.ALARM, "autonomy_level": 3})
    assert port.contexts[0].remaining_tool_calls == 7
    assert port.contexts[0].remaining_steps == 10
    assert all(context.max_tool_attempts == 4 for context in port.contexts)
    assert sum(name == "documents" for name, _ in tools.calls) == 4
    assert finishes[0][1]["evidence"]["react_trace"][-1]["stop_reason"] == "GUARD_LIMIT"


@pytest.mark.parametrize("positional", [False, True])
@pytest.mark.parametrize("explicit_limit", [None, 5, 150])
def test_engine_ceiling_preserves_explicit_limits_and_thread_configuration(
    positional, explicit_limit
):
    config = {"configurable": {"thread_id": "saved-thread"}}
    if explicit_limit is not None:
        config["recursion_limit"] = explicit_limit
    before = dict(config)

    class Recorder:
        def invoke(self, data, config):
            assert config["configurable"] == before["configurable"]
            assert config["recursion_limit"] == (explicit_limit or 100)
            return {"result": "ok"}

        async def ainvoke(self, data, config):
            return self.invoke(data, config)

    wrapper = CompiledAgentGraph(Recorder(), project_completed=False)
    args, kwargs = (({}, config), {}) if positional else (({},), {"config": config})
    assert wrapper.invoke(*args, **kwargs) == {"result": "ok"}
    assert asyncio.run(wrapper.ainvoke(*args, **kwargs)) == {"result": "ok"}
    assert config == before
