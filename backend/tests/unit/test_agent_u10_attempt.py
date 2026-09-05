"""Single ReAct attempt integration; all IO ports are local test functions."""

import subprocess
import sys
from dataclasses import replace

import pytest

from app.agent.hypothesis import HypothesisGenerationError
from app.agent.react import ReactSelectionError
from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_attempt import execute_react_attempt
from app.agent.u10_comparison import (
    EvidenceIds,
    Fixture,
    Inventory,
    LlmConfiguration,
    Safety,
)
from tests.unit.test_agent_graph import _fdc
from tests.unit.test_agent_react import _usage as selector_usage
from tests.unit.test_agent_u10_comparison import ids
from tests.unit.test_agent_u10_hypothesis import docs, generated, usage
from tests.unit.test_agent_u10_observations import context
from tests.unit.test_agent_u10_react_execution import outcome
from tests.unit.test_agent_u10_read_adapter import Immediate, ports
from tests.unit.test_agent_u10_read_execution import Clock


def setup(state=None):
    state = state or context()
    inventory = Inventory(
        current_wafers=1,
        adjacent={"relation": "NONE", "wafers": 0},
        sibling_chamber_id=None,
        history_prior_lots=0,
        metrology_samples=0,
        documents=True,
    )
    fixture = Fixture(
        fixture_id="CF-1",
        initial_snapshot_sha256="a" * 64,
        initial_evidence_ids=state.initial_evidence_ids(),
        candidate_inventory=inventory,
        expected_compared=inventory.dimensions(),
        required_evidence_ids=ids("LOT_HIST:LH-REP", "PARAMETER:PARAM-1"),
        oracle_required_dimensions=[],
    )
    config = LlmConfiguration(
        hypothesis_model_revision="actual-model",
        selector_model_revision="fixture-model",
        hypothesis_prompt_version="agent-hypothesis-v3-ko1",
        selector_prompt_version="agent-react-v2-ko1",
        temperature=0.0,
        seed=13,
    )
    return dict(
        fixture=fixture,
        attempt_no=1,
        verified_snapshot_sha256="a" * 64,
        context=state,
        llm=config,
        read_ports=ports(lambda _: _fdc()),
        deadline=Immediate(),
        generate=generated,
        observe_effects=lambda: (
            Safety(send_action_selected=0, hitl_bypass=0, pre_approval_mes=0),
            0,
        ),
        clock_ns=Clock(),
    )


def run(**overrides):
    params = setup()
    choices = iter([outcome("get_fdc_summary", fdc_candidate_id="F1"), outcome("stop")])
    params["select"] = lambda ctx, *, seed: next(choices)
    params.update(overrides)
    return execute_react_attempt(**params)


def test_read_hypothesis_action_and_record_are_connected(monkeypatch):
    from app.agent import decision

    events = []
    actual = decision.decide_action

    def decide(route):
        events.append("action")
        return actual(route)

    monkeypatch.setattr(decision, "decide_action", decide)
    choices = iter([outcome("get_fdc_summary", fdc_candidate_id="F1"), outcome("stop")])

    def select(ctx, *, seed):
        assert seed == 13
        assert (
            "oracle" not in ctx.model_dump()
            and "required_evidence_ids" not in ctx.model_dump()
        )
        events.append("select")
        return next(choices)

    def read(_):
        events.append("read")
        return _fdc()

    def generate(**inputs):
        events.append("hypothesis")
        assert inputs["seed"] == 13
        value = generated(**inputs)
        value.hypothesis.supporting_lot_hist_ids = ("LH-REP",)
        value.hypothesis.supporting_parameter_ids = ("PARAM-1",)
        return value

    def effects():
        events.append("effects")
        return Safety(send_action_selected=0, hitl_bypass=0, pre_approval_mes=0), 0

    result = run(
        select=select,
        read_ports=ports(read),
        generate=generate,
        observe_effects=effects,
    )
    a = result.attempt
    assert events == ["select", "read", "select", "hypothesis", "action", "effects"]
    assert a.completion and a.action == "WARNING" and a.execution_order == 2
    assert a.read_attempts == a.successful_reads == 1
    assert a.selector_calls == 2 and a.selector_tokens.total() == 20
    assert a.hypothesis_tokens.total() == 14
    assert a.tool_latency_ms == 2 and a.selector_latency_ms == 4
    assert a.end_to_end_latency_ms >= 8
    assert a.cited_evidence_ids.values == ["LOT_HIST:LH-REP", "PARAMETER:PARAM-1"]
    assert set(a.available_evidence_ids.values) == set(
        a.initial_evidence_ids.values
    ) | set(a.cited_evidence_ids.values)
    # Production route availability must NOT override the factual U10 inventory.
    assert (
        result.hypothesis.outcome.hypothesis.origin_assessment.compared.metrology
        == "NOT_CHECKED"
    )
    assert (
        a.compared.metrology == "NOT_AVAILABLE" and a.compared.history == "NOT_CHECKED"
    )
    assert a.llm_config_sha256 == digest(canonical_json(setup()["llm"]))
    result.reads.calls[0].evidence_ids.values.clear()
    assert a.calls[0].evidence_ids.values


@pytest.mark.parametrize(
    "key,value,code",
    [
        ("verified_snapshot_sha256", "b" * 64, "SNAPSHOT_MISMATCH"),
        ("attempt_no", True, "U10_ATTEMPT_ID_INVALID"),
        ("attempt_no", 3, "U10_ATTEMPT_ID_INVALID"),
    ],
)
def test_bad_binding_fails_before_selector(key, value, code):
    with pytest.raises(EvidenceError, match=code):
        run(**{key: value}, select=lambda *a, **kw: pytest.fail("selected"))


def test_initial_evidence_mismatch_and_dirty_context_are_rejected():
    params = setup()
    params["fixture"].initial_evidence_ids.values.clear()
    with pytest.raises(ValueError):
        run(fixture=params["fixture"])
    valid_other = setup()["fixture"].model_copy(
        update={
            "initial_evidence_ids": EvidenceIds.model_validate(ids("ALARM:OTHER")),
        }
    )
    with pytest.raises(EvidenceError, match="INITIAL_EVIDENCE_MISMATCH"):
        run(fixture=valid_other, select=lambda *a, **kw: pytest.fail("selected"))
    state = context()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc())
    with pytest.raises(EvidenceError, match="U10_ATTEMPT_CONTEXT_NOT_FRESH"):
        run(context=state, select=lambda *a, **kw: pytest.fail("selected"))


def test_second_pair_order_is_react_first():
    assert run(attempt_no=2).attempt.execution_order == 3


@pytest.mark.parametrize(
    "code", ["REACT_STRUCTURE_INVALID", "LLM_TIMEOUT", "LLM_DEPENDENCY"]
)
def test_abnormal_selector_stop_is_incomplete_even_with_successful_hypothesis(code):
    selections = []

    def select(ctx, *, seed):
        selections.append(ctx.structure_retry)
        if len(selections) == 1:
            return outcome("get_fdc_summary", fdc_candidate_id="F1")
        assert ctx.fetched_fdc_candidate_ids == ("F1",)
        raise ReactSelectionError(code, usage=selector_usage())

    result = run(select=select)
    expected = (
        [False, False, True] if code == "REACT_STRUCTURE_INVALID" else [False, False]
    )
    assert selections == expected
    assert result.reads.stop_reason == code
    assert result.attempt.successful_reads == 1
    assert (
        result.hypothesis.error_code is None and result.hypothesis.outcome is not None
    )
    assert result.attempt.hypothesis_tokens.total() == 14
    assert result.attempt.selector_tokens.total() == 10 * len(expected)
    assert result.attempt.action == "WARNING"
    assert result.attempt.completion is False


def test_read_failures_retry_and_no_fdc_means_incomplete_not_free_llm():
    result = run(
        read_ports=ports(lambda _: _fdc(ok=False)),
        generate=lambda **_: pytest.fail("generated"),
    )
    assert [c.status for c in result.attempt.calls] == ["TIMEOUT", "TIMEOUT"]
    assert result.attempt.read_attempts == 2 and result.attempt.successful_reads == 0
    assert not result.attempt.completion and result.attempt.action is None
    assert (
        result.attempt.hypothesis == []
        and result.attempt.hypothesis_tokens.total() == 0
    )
    assert result.attempt.available_evidence_ids == result.attempt.initial_evidence_ids


def test_partial_hypothesis_usage_keeps_failed_attempt():
    def fail(**_):
        raise HypothesisGenerationError("LLM_TIMEOUT", usage=usage())

    result = run(generate=fail)
    assert not result.attempt.completion and result.attempt.action is None
    assert result.attempt.hypothesis_tokens.total() == 14
    assert result.attempt.cited_evidence_ids.values == []


def test_missing_hypothesis_usage_cannot_issue_attempt():
    def fail(**_):
        raise HypothesisGenerationError("LLM_TIMEOUT")

    with pytest.raises(EvidenceError, match="METRIC_PRECONDITION_INVALID"):
        run(generate=fail)


def test_fourth_same_tool_failure_with_unfinished_retry_is_incomplete():
    from app.common.tool_contracts import DocumentSearchToolResult

    choices = iter(
        [
            outcome("get_fdc_summary", fdc_candidate_id="F1"),
            *(outcome("search_documents", query=f"FDC 조회 {i}") for i in range(4)),
        ]
    )
    queries = []

    def documents(payload):
        queries.append(payload["query"])
        if len(queries) == 4:
            return DocumentSearchToolResult(ok=False, reason="TIMEOUT:test")
        return docs((f"C{len(queries)}", 0.5))

    result = run(
        select=lambda ctx, *, seed: next(choices),
        read_ports=replace(ports(lambda _: _fdc()), document_search=documents),
    )
    assert len(queries) == 4 and result.attempt.read_attempts == 5
    assert result.reads.stop_reason == "BUDGET_EXHAUSTED"
    assert result.attempt.calls[-1].status == "TIMEOUT"
    assert result.attempt.calls[-1].retry == 0
    assert result.hypothesis.outcome is not None and not result.attempt.completion


def test_effects_are_observed_not_replaced_by_zero():
    result = run(
        observe_effects=lambda: (
            Safety(send_action_selected=1, hitl_bypass=2, pre_approval_mes=3),
            4,
        )
    )
    assert result.attempt.external_effects == 4
    assert result.attempt.safety.model_dump() == {
        "send_action_selected": 1,
        "hitl_bypass": 2,
        "pre_approval_mes": 3,
    }


def test_selector_model_pin_and_seed_apply_before_hypothesis():
    def select(ctx, *, seed):
        assert seed == 13
        selected = outcome("stop")
        selected.llm_usage.model = "other"
        return selected

    with pytest.raises(EvidenceError, match="LLM_CONFIG_MISMATCH"):
        run(select=select, generate=lambda **_: pytest.fail("generated"))


def test_unavailable_slot_cannot_be_claimed_checked():
    from app.agent.u10_comparison import ReadCall, derive_compared

    call = ReadCall(
        slot="METROLOGY",
        tool="get_metrology_result",
        selection=1,
        retry=0,
        input_digest="a" * 64,
        status="SUCCESS",
        latency_ms=1,
        evidence_ids=ids(),
    )
    assert (
        derive_compared(setup()["fixture"].candidate_inventory, [call]).metrology
        == "NOT_AVAILABLE"
    )


def test_import_is_lazy():
    code = """
import sys
import app.agent.u10_attempt
assert 'app.agent.graph' not in sys.modules
assert 'app.agent.hypothesis' not in sys.modules
assert 'app.common.llm' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
