"""U10 uses real v3 generation with an in-memory provider, never external IO."""

import json
import subprocess
import sys

import pytest

from app.agent import hypothesis as production
from app.agent.hypothesis import HypothesisGenerationError
from app.agent.hypothesis_v3 import comparison_matrix
from app.agent.release_artifacts import EvidenceError
from app.agent.state import HypothesisOutcome, LlmUsage
from app.agent.u10_hypothesis import execute_hypothesis, production_hypothesis_port
from app.agent.u10_read_adapter import ReadAdapter
from app.common import tool_contracts as dto
from app.common.llm import LlmTimeoutError
from tests.unit.test_agent_graph import _equipment, _fdc
from tests.unit.test_agent_hypothesis import _completion, _content
from tests.unit.test_agent_react import NOW
from tests.unit.test_agent_u10_evidence import hypothesis
from tests.unit.test_agent_u10_observations import context, history_call, history_result
from tests.unit.test_agent_u10_read_adapter import Immediate, ports
from tests.unit.test_agent_u10_read_execution import Clock


def observed():
    state = context()
    ReadAdapter(state, ports(lambda _: _fdc()), Immediate())(
        "get_fdc_summary", {"lot_hist_id": "LH-REP"}
    )
    return state


def usage(**changes):
    return LlmUsage.model_validate(
        {
            "model": "actual-model",
            "prompt_version": "agent-hypothesis-v3-ko1",
            "input_tokens": 10,
            "output_tokens": 4,
            **changes,
        }
    )


def generated(**inputs):
    return HypothesisOutcome(
        hypothesis=hypothesis(
            origin_assessment={
                "scope": "UNDETERMINED",
                "basis": [],
                "compared": comparison_matrix(inputs["route"], inputs["investigation"]),
            }
        ),
        llm_usage=usage(),
    )


def run(state, generate=generated, **kwargs):
    return execute_hypothesis(
        state,
        generate,
        expected_model="actual-model",
        seed=13,
        clock_ns=Clock(),
        **kwargs,
    )


def docs(*hits):
    return dto.DocumentSearchToolResult(
        ok=True,
        hits=[
            dto.DocumentHit(
                chunk_id=key,
                document_id="DOC1",
                title="제목",
                content="검증된 관찰",
                score=score,
                model_code="MODEL-1",
            )
            for key, score in hits
        ],
    )


def test_successful_reads_only_and_detached_inputs():
    state = context()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc(ok=False))
    assert state.hypothesis_inputs()["investigation"].successful_calls == ()
    assert state.hypothesis_inputs()["fdc_evidence"] == ()
    for _ in range(2):
        state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc())
    request, internal = history_call(state)
    state.record("get_chamber_parameter_history", request, history_result(), internal)
    inputs = state.hypothesis_inputs()
    assert set(inputs) == {
        "fdc_evidence",
        "graph_evidence",
        "document_evidence",
        "route",
        "investigation",
    }
    calls = inputs["investigation"].successful_calls
    assert [c.tool_name for c in calls] == [
        "get_fdc_summary",
        "get_chamber_parameter_history",
    ]
    assert calls[1].input == request and "_context" not in calls[1].input
    assert len(inputs["investigation"].history) == 1
    assert (
        comparison_matrix(inputs["route"], inputs["investigation"]).history == "CHECKED"
    )
    inputs["fdc_evidence"][0].parameters.clear()
    calls[1].input.clear()
    assert state.hypothesis_inputs()["fdc_evidence"][0].parameters
    assert (
        state.hypothesis_inputs()["investigation"].successful_calls[1].input == request
    )


def test_documents_share_production_dedup_score_and_ten_hit_limit():
    state = observed()
    for query, result in [
        ("FDC 첫 검색", docs(*[(f"C{i}", i / 20) for i in range(8)])),
        (
            "FDC 다음 검색",
            docs(("C0", 0.99), *[(f"C{i}", i / 20) for i in range(8, 14)]),
        ),
    ]:
        state.record(
            "search_documents", {"query": query, "model_code": "MODEL-1"}, result
        )
    result = state.hypothesis_inputs()["document_evidence"]
    assert len(result.hits) == 10 and result.hits[0].chunk_id == "C0"
    assert result.hits[0].score == 0.99
    assert len({h.chunk_id for h in result.hits}) == 10
    result.hits.clear()
    assert len(state.hypothesis_inputs()["document_evidence"].hits) == 10


def test_no_fdc_does_not_call_generator_or_invent_usage():
    result = run(context(), lambda **_: pytest.fail("generator called"))
    assert result.error_code == "HYPOTHESIS_EVIDENCE_INSUFFICIENT"
    assert result.outcome is result.usage is result.cited_evidence_ids is None
    assert result.latency_ms == 0
    with pytest.raises(EvidenceError, match="METRIC_PRECONDITION_INVALID"):
        result.measured_tokens()


def test_real_production_generation_seed_prompt_citations_and_retry_usage(monkeypatch):
    state = observed()
    state.record("get_equipment_context", {"chamber_id": "EQP01-PM1"}, _equipment())
    request, internal = history_call(state)
    state.record("get_chamber_parameter_history", request, history_result(), internal)
    metrology = dto.MetrologyResultToolResult(
        ok=True,
        lot_id="LOT001",
        step_id="CT-PHOTO",
        fail_count=0,
        disclaimer="계측 관찰",
        results=[
            dto.MetrologyResultItem(
                wafer_id="LOT001W001",
                measure_type="CD",
                measured_value=1.0,
                alarm_result="PASS",
                measured_at=NOW,
            )
        ],
    )
    state.record(
        "get_metrology_result", {"lot_id": "LOT001", "step_id": "CT-PHOTO"}, metrology
    )
    inputs = state.hypothesis_inputs()
    assert inputs["graph_evidence"] == _equipment()
    assert inputs["investigation"].metrology == (metrology,)
    captured = []
    alarm = state.hypothesis_inputs()["route"].incident.representative_alarm
    content = _content(
        supporting_alarms=[alarm.model_dump(mode="json")],
        supporting_chunk_ids=[],
        supporting_relation_ids=[],
        supporting_lot_hist_ids=["LH-REP"],
        supporting_parameter_ids=["PARAM-1"],
    )

    def chat(messages, **kwargs):
        captured.append((messages, kwargs))
        return _completion("{}" if len(captured) == 1 else content)

    monkeypatch.setattr(production.llm, "chat_with_usage", chat)
    assert production_hypothesis_port() is production.generate_hypothesis
    result = run(state, production_hypothesis_port())
    assert result.error_code is None and len(captured) == 2
    assert result.outcome.hypothesis.origin_assessment.compared.history == "CHECKED"
    assert result.outcome.hypothesis.origin_assessment.compared.metrology == "CHECKED"
    assert result.measured_tokens().model_dump() == {"input": 20, "output": 8}
    assert result.latency_ms == 2
    assert result.cited_evidence_ids.values == sorted(
        [
            "ALARM:" + alarm.to_token(),
            "LOT_HIST:LH-REP",
            "PARAMETER:PARAM-1",
        ]
    )
    assert all(kwargs["seed"] == 13 for _, kwargs in captured)
    messages = json.dumps(captured, ensure_ascii=False)
    assert "oracle" not in messages and "required_evidence_ids" not in messages
    assert "candidate_inventory" not in messages
    assert (
        "compared" not in production.HYPOTHESIS_RESPONSE_SCHEMA["schema"]["properties"]
    )


def test_production_timeout_after_first_response_preserves_partial_usage(monkeypatch):
    calls = []

    def chat(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return _completion("{}")
        raise LlmTimeoutError("private provider url")

    monkeypatch.setattr(production.llm, "chat_with_usage", chat)
    result = run(observed(), production_hypothesis_port())
    assert result.error_code == "LLM_TIMEOUT" and result.outcome is None
    assert result.measured_tokens().model_dump() == {"input": 10, "output": 4}
    assert result.cited_evidence_ids is None and "private" not in repr(result)


@pytest.mark.parametrize(
    "error", [RuntimeError("credential"), HypothesisGenerationError("LLM_NOT_READY")]
)
def test_unknown_or_unmeasured_failure_keeps_usage_missing(error):
    def fail(**_):
        raise error

    result = run(observed(), fail)
    assert result.usage is None and "credential" not in repr(result)
    with pytest.raises(EvidenceError, match="METRIC_PRECONDITION_INVALID"):
        result.measured_tokens()


@pytest.mark.parametrize(
    "change", [{"model": "different"}, {"prompt_version": "agent-hypothesis-v2-ko1"}]
)
@pytest.mark.parametrize("failed", [False, True])
def test_usage_pins_apply_to_success_and_failure(change, failed):
    def generate(**inputs):
        if failed:
            raise HypothesisGenerationError("LLM_TIMEOUT", usage=usage(**change))
        return generated(**inputs).model_copy(update={"llm_usage": usage(**change)})

    with pytest.raises(EvidenceError, match="LLM_CONFIG_MISMATCH"):
        run(observed(), generate)


@pytest.mark.parametrize("bad", ["dict", "v2", "compared"])
def test_invalid_outcome_is_not_accepted_as_v3(bad):
    def generate(**inputs):
        value = generated(**inputs)
        if bad == "dict":
            return value.model_dump()
        if bad == "v2":
            value.hypothesis.origin_assessment = None
        else:
            value.hypothesis.origin_assessment.compared.history = "CHECKED"
        return value

    with pytest.raises(EvidenceError, match="U10_HYPOTHESIS_RESULT_INVALID"):
        run(observed(), generate)


def test_port_cannot_mutate_context_or_return_shared_state():
    state, returned = observed(), []

    def generate(**inputs):
        value = generated(**inputs)
        inputs["fdc_evidence"][0].parameters.clear()
        returned.append(value)
        return value

    result = run(state, generate)
    returned[0].llm_usage.input_tokens = 999
    assert result.measured_tokens().input == 10
    assert state.results("get_fdc_summary")[0].parameters


@pytest.mark.parametrize(
    "kwargs",
    [
        {"seed": True},
        {"seed": -1},
        {"expected_model": ""},
        {"expected_model": " actual-model"},
        {"expected_model": "x" * 65},
    ],
)
def test_invalid_config_fails_before_generation(kwargs):
    params = {"expected_model": "actual-model", "seed": 13, **kwargs}
    with pytest.raises(EvidenceError, match="U10_HYPOTHESIS_CONFIG_INVALID"):
        execute_hypothesis(observed(), lambda **_: pytest.fail("called"), **params)


def test_backwards_clock_is_rejected():
    values = iter([2, 1])
    with pytest.raises(EvidenceError, match="MONOTONIC_CLOCK_INVALID"):
        execute_hypothesis(
            observed(),
            generated,
            expected_model="actual-model",
            seed=13,
            clock_ns=lambda: next(values),
        )


def test_v2_prompt_tree_is_rejected_before_generation(monkeypatch):
    from app.agent import prompts

    monkeypatch.setattr(prompts, "PROMPT_VERSION", "agent-hypothesis-v2-ko1")
    with pytest.raises(EvidenceError, match="^U10_HYPOTHESIS_CONFIG_INVALID$"):
        run(observed(), lambda **_: pytest.fail("generator called"))


def test_import_is_lazy():
    code = """
import sys
import app.agent.u10_hypothesis
assert 'app.agent.hypothesis' not in sys.modules
assert 'app.agent.graph' not in sys.modules
assert 'app.common.llm' not in sys.modules
assert 'app.config' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
