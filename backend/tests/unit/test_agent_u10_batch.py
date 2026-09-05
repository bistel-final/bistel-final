"""Batch integration with test-only snapshots/read/generator/approval ports."""

import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace

import pytest

from app.agent import u10_batch as subject
from app.agent.hypothesis import HypothesisGenerationError
from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_comparison import Benchmark, Safety, validate_artifact
from app.agent.u10_read_execution import fixed_policy_sha256
from tests.unit.test_agent_u10_comparison import benchmark_payload, seal_benchmark
from tests.unit.test_agent_u10_fixed_attempt import parameters
from tests.unit.test_agent_u10_hypothesis import usage
from tests.unit.test_agent_u10_react_execution import outcome


def inputs(*, tweak=lambda key, params: None):
    template, _ = parameters()
    payload = benchmark_payload()
    payload["fixed_policy_sha256"] = fixed_policy_sha256()
    payload["fixtures"] = [
        {**template["fixture"].model_dump(), "fixture_id": f"CF-{i}"}
        for i in range(1, 9)
    ]
    seal_benchmark(payload)
    benchmark = Benchmark.model_validate(payload)
    events, contexts, generated = [], [], []

    def authorize(binding):
        events.append(("authorize", len(contexts) + 1))
        assert binding.attempt_count == 32
        assert binding.benchmark_sha256 == digest(canonical_json(benchmark))
        return True  # Test authority only, never a real data-export grant.

    @contextmanager
    def prepare(key):
        events.append(("prepare", key.execution_order))
        assert set(asdict(key)) == {
            "fixture_id",
            "attempt_no",
            "policy",
            "execution_order",
        }
        params, _ = parameters()
        actual = params["generate"]

        def generate(**kwargs):
            generated.append((key.policy, kwargs["seed"]))
            assert "required_evidence_ids" not in kwargs and "oracle" not in kwargs
            return actual(**kwargs)

        params["generate"] = generate
        choices = iter(
            [outcome("get_fdc_summary", fdc_candidate_id="F1"), outcome("stop")]
        )
        params["select"] = lambda ctx, *, seed: next(choices)
        tweak(key, params)
        contexts.append(params["context"])
        try:
            yield subject.AttemptEnvironment(
                **{
                    name: params[name]
                    for name in subject.AttemptEnvironment.__dataclass_fields__
                }
            )
        finally:
            events.append(("close", key.execution_order))

    return (
        dict(
            benchmark=benchmark,
            llm=template["llm"],
            evaluated_revision="a" * 40,
            expected_benchmark_sha256=digest(canonical_json(benchmark)),
            expected_tool_contract_sha256=benchmark.tool_contract_sha256,
            authorize=authorize,
            prepare=prepare,
        ),
        events,
        contexts,
        generated,
    )


def test_exact_interleave_resources_seed_and_final_verifier():
    params, events, contexts, generated = inputs()
    artifact = subject.execute_batch(**params)
    expected = [
        (f"CF-{i}", n, p)
        for i in range(1, 9)
        for n, policies in [
            (1, ("FIXED_POLICY_V21", "REACT_V2")),
            (2, ("REACT_V2", "FIXED_POLICY_V21")),
        ]
        for p in policies
    ]
    assert [
        (a.fixture_id, a.attempt_no, a.policy) for a in artifact.attempts
    ] == expected
    assert [a.execution_order for a in artifact.attempts] == list(range(1, 33))
    assert events == [
        (event, i) for i in range(1, 33) for event in ("authorize", "prepare", "close")
    ]
    assert len({id(c) for c in contexts}) == 32
    assert len(generated) == 32 and {seed for _, seed in generated} == {13}
    assert (
        sum(
            a.selector_calls
            for a in artifact.attempts
            if a.policy == "FIXED_POLICY_V21"
        )
        == 0
    )
    assert all(a.completion for a in artifact.attempts)
    assert (
        validate_artifact(artifact.model_dump(), params["benchmark"].model_dump())
        == artifact.result
    )
    assert artifact.result["agent_verdict"] == "AGENT_JUSTIFICATION_NOT_ESTABLISHED_V21"
    assert artifact.EXPERIMENT_ONLY and artifact.PRODUCTION_PERFORMANCE_NOT_CLAIMED


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("expected_benchmark_sha256", "0" * 64, "BENCHMARK_SHA_MISMATCH"),
        ("expected_tool_contract_sha256", "0" * 64, "TOOL_CONTRACT_MISMATCH"),
        ("evaluated_revision", "abc", "U10_REVISION_INVALID"),
    ],
)
def test_bad_pins_do_not_authorize_or_prepare(field, value, code):
    params, events, _, _ = inputs()
    params[field] = value
    with pytest.raises(EvidenceError, match=code):
        subject.execute_batch(**params)
    assert events == []


def test_old_fixed_policy_pin_rejected_before_any_work():
    params, events, _, _ = inputs()
    params["benchmark"] = params["benchmark"].model_copy(
        update={"fixed_policy_sha256": "0" * 64}
    )
    params["expected_benchmark_sha256"] = digest(canonical_json(params["benchmark"]))
    with pytest.raises(EvidenceError, match="FIXED_POLICY_MISMATCH"):
        subject.execute_batch(**params)
    assert events == []


@pytest.mark.parametrize("answer", [False, None, 1, "approved"])
def test_approval_must_explicitly_pass_before_resource_scope(answer):
    params, events, _, _ = inputs()
    params["authorize"] = lambda binding: answer
    with pytest.raises(EvidenceError, match="U10_DATA_EXPORT_NOT_AUTHORIZED"):
        subject.execute_batch(**params)
    assert events == []


def test_revoked_authority_stops_before_next_resource_scope():
    params, events, _, _ = inputs()
    answers = iter([True, False])
    params["authorize"] = lambda binding: next(answers)
    with pytest.raises(EvidenceError, match="U10_DATA_EXPORT_NOT_AUTHORIZED"):
        subject.execute_batch(**params)
    assert events == [("prepare", 1), ("close", 1)]


def test_snapshot_drift_closes_scope_and_does_not_run_next_attempt():
    def tweak(key, params):
        if key.execution_order == 2:
            params["verified_snapshot_sha256"] = "b" * 64

    params, events, _, generated = inputs(tweak=tweak)
    with pytest.raises(EvidenceError, match="SNAPSHOT_MISMATCH"):
        subject.execute_batch(**params)
    assert events[-1] == ("close", 2) and len(generated) == 1


def test_reused_context_is_rejected_and_cleaned():
    first = []

    def tweak(key, params):
        if not first:
            first.append(params["context"])
        else:
            params["context"] = first[0]

    params, events, _, generated = inputs(tweak=tweak)
    with pytest.raises(EvidenceError, match="U10_ATTEMPT_CONTEXT_REUSED"):
        subject.execute_batch(**params)
    assert events[-1] == ("close", 2) and len(generated) == 1


def test_measured_failure_is_retained_not_filtered_or_retried():
    def tweak(key, params):
        if key.execution_order == 2:

            def fail(**_):
                raise HypothesisGenerationError("LLM_TIMEOUT", usage=usage())

            params["generate"] = fail

    params, events, _, _ = inputs(tweak=tweak)
    artifact = subject.execute_batch(**params)
    assert len(artifact.attempts) == 32 and len(events) == 96
    assert [a.execution_order for a in artifact.attempts if not a.completion] == [2]
    assert artifact.attempts[1].hypothesis_tokens.total() == 14
    assert artifact.result["agent_verdict"] == "AGENT_JUSTIFICATION_NOT_ESTABLISHED_V21"


@pytest.mark.parametrize(
    "field",
    ["external_effects", "send_action_selected", "hitl_bypass", "pre_approval_mes"],
)
def test_unexpected_effect_halts_batch_after_cleanup(field):
    def tweak(key, params):
        safety = dict(send_action_selected=0, hitl_bypass=0, pre_approval_mes=0)
        if field != "external_effects":
            safety[field] = 1
        params["observe_effects"] = lambda: (
            Safety(**safety),
            int(field == "external_effects"),
        )

    params, events, _, _ = inputs(tweak=tweak)
    with pytest.raises(EvidenceError, match="U10_UNEXPECTED_EXTERNAL_EFFECT"):
        subject.execute_batch(**params)
    assert events == [("authorize", 1), ("prepare", 1), ("close", 1)]


def test_row_identity_tampering_is_rejected_before_next_scope(monkeypatch):
    actual = subject.execute_fixed_attempt

    def tampered(**kwargs):
        result = actual(**kwargs)
        return replace(
            result, attempt=result.attempt.model_copy(update={"execution_order": 2})
        )

    monkeypatch.setattr(subject, "execute_fixed_attempt", tampered)
    params, events, _, _ = inputs()
    with pytest.raises(EvidenceError, match="U10_ATTEMPT_BINDING_MISMATCH"):
        subject.execute_batch(**params)
    assert events[-1] == ("close", 1)


def test_row_llm_config_tampering_is_rejected_before_next_scope(monkeypatch):
    actual = subject.execute_fixed_attempt

    def tampered(**kwargs):
        result = actual(**kwargs)
        return replace(
            result,
            attempt=result.attempt.model_copy(update={"llm_config_sha256": "0" * 64}),
        )

    monkeypatch.setattr(subject, "execute_fixed_attempt", tampered)
    params, events, _, _ = inputs()
    with pytest.raises(EvidenceError, match="^LLM_CONFIG_MISMATCH$"):
        subject.execute_batch(**params)
    assert events == [("authorize", 1), ("prepare", 1), ("close", 1)]


@pytest.mark.parametrize(
    "field", ["hypothesis_model_revision", "selector_model_revision"]
)
@pytest.mark.parametrize("model", ["x" * 65, " model", "model "])
def test_invalid_model_revision_is_rejected_before_authorization(field, model):
    params, events, _, _ = inputs()
    params["llm"] = params["llm"].model_copy(update={field: model})
    with pytest.raises(EvidenceError, match="^LLM_CONFIG_MISMATCH$"):
        subject.execute_batch(**params)
    assert events == []


@pytest.mark.parametrize("env", [None, {}])
def test_wrong_environment_type_is_rejected_and_scope_is_closed(env):
    params, events, _, _ = inputs()

    @contextmanager
    def prepare(key):
        events.append(("prepare", key.execution_order))
        try:
            yield env
        finally:
            events.append(("close", key.execution_order))

    params["prepare"] = prepare
    with pytest.raises(EvidenceError, match="^U10_ATTEMPT_ENVIRONMENT_INVALID$"):
        subject.execute_batch(**params)
    assert events == [("authorize", 1), ("prepare", 1), ("close", 1)]


@pytest.mark.parametrize(
    "order,field", [(1, "bound_inputs"), (1, "document_context"), (2, "select")]
)
def test_missing_policy_port_closes_scope_before_generation(order, field):
    def tweak(key, params):
        if key.execution_order == order:
            params[field] = None

    params, events, _, generated = inputs(tweak=tweak)
    with pytest.raises(EvidenceError, match="U10_ATTEMPT_ENVIRONMENT_INVALID"):
        subject.execute_batch(**params)
    assert events[-1] == ("close", order) and len(generated) == order - 1


def test_cleanup_failure_prevents_next_attempt():
    params, events, _, generated = inputs()
    actual = params["prepare"]

    @contextmanager
    def failing_cleanup(key):
        with actual(key) as env:
            yield env
        raise RuntimeError("cleanup failed")

    params["prepare"] = failing_cleanup
    with pytest.raises(RuntimeError, match="cleanup failed"):
        subject.execute_batch(**params)
    assert events == [("authorize", 1), ("prepare", 1), ("close", 1)]
    assert len(generated) == 1


def test_missing_attempt_is_rejected_by_population_verifier(monkeypatch):
    plan = list(subject.execution_plan())[:-1]
    monkeypatch.setattr(subject, "execution_plan", lambda: iter(plan))
    params, events, _, _ = inputs()
    with pytest.raises(EvidenceError, match="ATTEMPT_POPULATION_INVALID"):
        subject.execute_batch(**params)
    assert events[-1] == ("close", 31)


def test_final_result_is_independently_recalculated(monkeypatch):
    actual = subject.evaluate

    def tampered(*args):
        result = actual(*args)
        result["agent_verdict"] = "FORGED"
        return result

    monkeypatch.setattr(subject, "evaluate", tampered)
    params, events, _, _ = inputs()
    with pytest.raises(EvidenceError, match="VERDICT_RECALCULATION_MISMATCH"):
        subject.execute_batch(**params)
    assert events[-1] == ("close", 32)


def test_import_does_not_load_providers():
    code = """
import sys
import app.agent.u10_batch
assert 'app.agent.graph' not in sys.modules
assert 'app.agent.hypothesis' not in sys.modules
assert 'app.common.llm' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
