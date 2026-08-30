"""`V5-C-2.3` production 가설 port 단위 회귀."""

from __future__ import annotations

import json

import pytest

from app.agent import hypothesis as subject
from app.agent.hypothesis import HypothesisGenerationError, generate_hypothesis
from app.agent.incident import ResolvedIncident
from app.agent.routing import GraphRouteEvidence, ResolvedIncidentRoute
from app.common.enums import AlarmSource
from app.common.llm import (
    ChatCompletion,
    LlmDependencyError,
    LlmNotReadyError,
    LlmResponseUsage,
    LlmTimeoutError,
)
from app.common.schemas import AlarmRef
from app.common.tool_contracts import DocumentHit, DocumentSearchToolResult

ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")


def _route() -> ResolvedIncidentRoute:
    return ResolvedIncidentRoute(
        incident=ResolvedIncident(
            lot_id="LOT-1",
            chamber_id="EQP-1-PM1",
            requested_alarm=ALARM,
            representative_alarm=ALARM,
            member_alarms=(ALARM,),
        ),
        wafer_routes=(),
        graph_evidence=(
            GraphRouteEvidence(
                chamber_id="EQP-1-PM1",
                equipment_id="EQP-1",
                model_code="MODEL-1",
                process_step_id="STEP-1",
                upstream_process_step_ids=(),
                downstream_process_step_ids=(),
                relation_ids=("REL-1",),
                graph_revision="rev-1",
            ),
        ),
        route_consistency=True,
        mismatches=(),
    )


def _docs() -> DocumentSearchToolResult:
    return DocumentSearchToolResult(
        ok=True,
        hits=[
            DocumentHit(
                chunk_id="CHUNK-1",
                document_id="DOC-1",
                title="Guide",
                score=0.8,
                content="safe evidence",
            )
        ],
    )


def _content(**overrides: object) -> str:
    payload: dict[str, object] = {
        "predicted_fault_code": "OTH",
        "confidence": 0.6,
        "cause_summary": "pressure pattern",
        "supporting_alarms": [{"source": "TRACE", "alarm_id": "TA-01"}],
        "supporting_chunk_ids": ["CHUNK-1"],
        "supporting_relation_ids": ["REL-1"],
        "uncertainty": "limited history",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _completion(
    content: str, *, model: str = "actual-model", n: int = 1
) -> ChatCompletion:
    return ChatCompletion(
        content=content,
        model=model,
        prompt_tokens=10 * n,
        completion_tokens=4 * n,
    )


def test_success_returns_structured_hypothesis_and_actual_usage(monkeypatch) -> None:
    monkeypatch.setattr(
        subject.llm,
        "chat_with_usage",
        lambda messages, **_kwargs: _completion(_content()),
    )
    outcome = generate_hypothesis(None, None, _docs(), _route())
    assert outcome.hypothesis.predicted_fault_code.value == "OTH"
    assert outcome.llm_usage.model == "actual-model"
    assert (outcome.llm_usage.input_tokens, outcome.llm_usage.output_tokens) == (10, 4)


def test_invalid_first_response_uses_exactly_one_correction_and_sums_usage(
    monkeypatch,
) -> None:
    responses = iter([_completion("not-json"), _completion(_content(), n=2)])
    messages: list[list[dict[str, str]]] = []

    def chat(value, **kwargs):
        messages.append(value)
        assert kwargs == {"json_schema": subject.HYPOTHESIS_RESPONSE_SCHEMA}
        return next(responses)

    monkeypatch.setattr(subject.llm, "chat_with_usage", chat)
    outcome = generate_hypothesis(None, None, _docs(), _route())
    assert len(messages) == 2
    assert outcome.llm_usage.input_tokens == 30
    assert outcome.llm_usage.output_tokens == 12
    assert "not-json" not in repr(messages[1])
    assert "STRUCTURE_INVALID" in repr(messages[1])


def test_single_json_fence_is_accepted_without_a_correction_round(monkeypatch) -> None:
    calls = 0

    def chat(messages, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs == {"json_schema": subject.HYPOTHESIS_RESPONSE_SCHEMA}
        return _completion(f"```json\n{_content()}\n```")

    monkeypatch.setattr(subject.llm, "chat_with_usage", chat)
    outcome = generate_hypothesis(None, None, _docs(), _route())
    assert calls == 1
    assert outcome.hypothesis.predicted_fault_code.value == "OTH"


def test_second_invalid_response_stops_without_third_call_and_keeps_usage(
    monkeypatch,
) -> None:
    calls = 0

    def chat(messages, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs == {"json_schema": subject.HYPOTHESIS_RESPONSE_SCHEMA}
        return _completion("{}", n=calls)

    monkeypatch.setattr(subject.llm, "chat_with_usage", chat)
    with pytest.raises(HypothesisGenerationError) as exc:
        generate_hypothesis(None, None, _docs(), _route())
    assert calls == 2
    assert exc.value.code == "HYPOTHESIS_STRUCTURE_INVALID"
    assert exc.value.usage_or_none is not None
    assert exc.value.usage_or_none.input_tokens == 30


@pytest.mark.parametrize(
    "overrides",
    [
        {"supporting_alarms": []},
        {"supporting_alarms": [{"source": "TRACE", "alarm_id": "OUTSIDE"}]},
        {"supporting_chunk_ids": []},
        {"supporting_chunk_ids": ["OUTSIDE"]},
        {"supporting_relation_ids": []},
        {"supporting_relation_ids": ["OUTSIDE"]},
    ],
)
def test_required_and_allowlisted_citations_fail_closed(monkeypatch, overrides) -> None:
    monkeypatch.setattr(
        subject.llm,
        "chat_with_usage",
        lambda messages, **_kwargs: _completion(_content(**overrides)),
    )
    with pytest.raises(HypothesisGenerationError) as exc:
        generate_hypothesis(None, None, _docs(), _route())
    assert exc.value.code == "HYPOTHESIS_STRUCTURE_INVALID"


def test_citation_correction_names_the_failed_identifier_class(monkeypatch) -> None:
    responses = iter(
        [
            _completion(_content(supporting_chunk_ids=["DOC-1"])),
            _completion(_content(), n=2),
        ]
    )
    messages: list[list[dict[str, str]]] = []

    def chat(value, **_kwargs):
        messages.append(value)
        return next(responses)

    monkeypatch.setattr(subject.llm, "chat_with_usage", chat)

    outcome = generate_hypothesis(None, None, _docs(), _route())

    assert outcome.hypothesis.supporting_chunk_ids == ("CHUNK-1",)
    assert "DOCUMENT_CITATION_OUTSIDE_EVIDENCE" in messages[1][1]["content"]
    assert "document_id" in messages[1][1]["content"]


def test_correction_transport_failure_preserves_first_success_usage(
    monkeypatch,
) -> None:
    responses = iter([_completion("not-json"), LlmTimeoutError("secret")])

    def chat(messages, **kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(subject.llm, "chat_with_usage", chat)
    with pytest.raises(HypothesisGenerationError) as exc:
        generate_hypothesis(None, None, _docs(), _route())
    assert exc.value.code == "LLM_TIMEOUT"
    assert exc.value.usage_or_none is not None
    assert exc.value.usage_or_none.input_tokens == 10
    assert "secret" not in repr(exc.value)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LlmNotReadyError("secret"), "LLM_NOT_READY"),
        (LlmTimeoutError("secret"), "LLM_TIMEOUT"),
        (LlmDependencyError("secret"), "LLM_DEPENDENCY"),
    ],
)
def test_common_llm_errors_map_to_exact_sanitized_codes(
    monkeypatch, error: Exception, code: str
) -> None:
    def fail(messages, **kwargs):
        raise error

    monkeypatch.setattr(subject.llm, "chat_with_usage", fail)
    with pytest.raises(HypothesisGenerationError) as exc:
        generate_hypothesis(None, None, _docs(), _route())
    assert exc.value.code == code
    assert exc.value.usage_or_none is None
    assert "secret" not in repr(exc.value)


def test_invalid_content_dependency_preserves_current_response_usage(
    monkeypatch,
) -> None:
    error = LlmDependencyError(
        "secret",
        usage=LlmResponseUsage(
            model="actual-model",
            prompt_tokens=11,
            completion_tokens=5,
        ),
    )

    def fail(messages, **kwargs):
        raise error

    monkeypatch.setattr(subject.llm, "chat_with_usage", fail)
    with pytest.raises(HypothesisGenerationError) as exc:
        generate_hypothesis(None, None, _docs(), _route())
    assert exc.value.code == "LLM_DEPENDENCY"
    assert exc.value.usage_or_none is not None
    assert (
        exc.value.usage_or_none.input_tokens,
        exc.value.usage_or_none.output_tokens,
    ) == (
        11,
        5,
    )
    assert "secret" not in repr(exc.value)


def test_correction_content_failure_adds_current_response_usage(
    monkeypatch,
) -> None:
    responses = iter(
        [
            _completion("not-json"),
            LlmDependencyError(
                "secret",
                usage=LlmResponseUsage(
                    model="actual-model",
                    prompt_tokens=11,
                    completion_tokens=5,
                ),
            ),
        ]
    )

    def chat(messages, **kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(subject.llm, "chat_with_usage", chat)
    with pytest.raises(HypothesisGenerationError) as exc:
        generate_hypothesis(None, None, _docs(), _route())
    assert exc.value.code == "LLM_DEPENDENCY"
    assert exc.value.usage_or_none is not None
    assert (
        exc.value.usage_or_none.input_tokens,
        exc.value.usage_or_none.output_tokens,
    ) == (
        21,
        9,
    )


def test_prompt_size_error_keeps_its_terminal_code(monkeypatch) -> None:
    calls = 0

    def build(*args, **kwargs):
        raise subject.HypothesisPromptError("HYPOTHESIS_PROMPT_TOO_LARGE")

    def chat(messages, **kwargs):
        nonlocal calls
        calls += 1
        return _completion(_content())

    monkeypatch.setattr(subject, "build_hypothesis_messages", build)
    monkeypatch.setattr(subject.llm, "chat_with_usage", chat)
    with pytest.raises(HypothesisGenerationError) as exc:
        generate_hypothesis(None, None, _docs(), _route())
    assert exc.value.code == "HYPOTHESIS_PROMPT_TOO_LARGE"
    assert calls == 0


def test_programming_type_error_is_not_misclassified_as_prompt_blocked(
    monkeypatch,
) -> None:
    def fail(*args, **kwargs):
        raise TypeError("programming defect")

    monkeypatch.setattr(subject, "build_hypothesis_messages", fail)
    with pytest.raises(TypeError, match="programming defect"):
        generate_hypothesis(None, None, _docs(), _route())


def test_model_change_between_rounds_is_dependency_failure(monkeypatch) -> None:
    responses = iter(
        [
            _completion("not-json", model="model-a"),
            _completion(_content(), model="model-b"),
        ]
    )
    monkeypatch.setattr(
        subject.llm,
        "chat_with_usage",
        lambda messages, **_kwargs: next(responses),
    )
    with pytest.raises(HypothesisGenerationError) as exc:
        generate_hypothesis(None, None, _docs(), _route())
    assert exc.value.code == "LLM_DEPENDENCY"
    assert exc.value.usage_or_none is not None
    assert exc.value.usage_or_none.model == "model-a"


def test_production_port_is_the_only_callable_factory_product() -> None:
    assert subject.production_port() is subject.generate_hypothesis


def test_hypothesis_response_schema_is_exact_and_strict() -> None:
    schema = subject.HYPOTHESIS_RESPONSE_SCHEMA
    assert schema["name"] == "agent_hypothesis"
    assert schema["strict"] is True
    body = schema["schema"]
    assert body["additionalProperties"] is False
    assert set(body["required"]) == set(body["properties"])
    alarm = body["properties"]["supporting_alarms"]["items"]
    assert alarm["additionalProperties"] is False
    assert set(alarm["required"]) == {"source", "alarm_id"}
