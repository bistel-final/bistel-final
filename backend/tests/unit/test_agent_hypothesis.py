"""`V5-C-2.3` production 가설 port 단위 회귀."""

from __future__ import annotations

import json
from dataclasses import replace

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
        "cause_summary": "압력 이상 패턴이 관측되었습니다.",
        "supporting_alarms": [{"source": "TRACE", "alarm_id": "TA-01"}],
        "supporting_chunk_ids": ["CHUNK-1"],
        "supporting_relation_ids": ["REL-1"],
        "supporting_lot_hist_ids": [],
        "supporting_parameter_ids": [],
        "uncertainty": "이력 범위가 제한적입니다.",
        "observations": ["압력 이상 패턴 한 건이 관측되었습니다."],
        "evidence_synthesis": "FDC, Graph, 문서 근거를 함께 비교했습니다.",
        "alternative_hypotheses": [
            {
                "summary": "다른 원인 가능성이 있습니다.",
                "lower_rank_reason": "직접 근거가 상대적으로 부족합니다.",
            }
        ],
        "impact_summary": "현재 incident의 직접 범위를 우선 확인해야 합니다.",
        "verification_steps": ["인용된 근거를 다시 확인합니다."],
        "limitations": ["이력 범위가 제한적입니다."],
        "parameter_findings_draft": [],
        "origin_claim": {"scope": "UNDETERMINED", "basis_refs": []},
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
    assert "JSON_INVALID" in repr(messages[1])


def test_v2_requires_every_declared_output_key(monkeypatch) -> None:
    incomplete = json.loads(_content())
    incomplete.pop("verification_steps")
    responses = iter(
        [_completion(json.dumps(incomplete)), _completion(_content(), n=2)]
    )
    messages: list[list[dict[str, str]]] = []

    def chat(value, **kwargs):
        messages.append(value)
        assert kwargs == {"json_schema": subject.HYPOTHESIS_RESPONSE_SCHEMA}
        return next(responses)

    monkeypatch.setattr(subject.llm, "chat_with_usage", chat)
    outcome = generate_hypothesis(None, None, _docs(), _route())

    assert outcome.hypothesis.verification_steps == ("인용된 근거를 다시 확인합니다.",)
    assert "STRUCTURE_INVALID" in repr(messages[1])
    assert "missing.verification_steps" not in repr(messages[1])


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


def test_english_narrative_is_rejected_and_corrected_in_korean(monkeypatch) -> None:
    responses = iter(
        [
            _completion(_content(cause_summary="pressure pattern")),
            _completion(_content(), n=2),
        ]
    )
    messages: list[list[dict[str, str]]] = []

    def chat(value, **_kwargs):
        messages.append(value)
        return next(responses)

    monkeypatch.setattr(subject.llm, "chat_with_usage", chat)

    outcome = generate_hypothesis(None, None, _docs(), _route())

    assert outcome.hypothesis.cause_summary == "압력 이상 패턴이 관측되었습니다."
    assert "KOREAN_OUTPUT_REQUIRED" in messages[1][1]["content"]


def _hypothesis_content(**overrides):
    value = json.loads(_content(**overrides))
    value.pop("parameter_findings_draft")
    value.pop("origin_claim")
    return subject.Hypothesis.model_validate(value)


@pytest.mark.parametrize(
    "foreign",
    ["নির্দেশ", "निर्देश", "คำแนะนำ", "инструкция", "تعليمات", "確認します"],
)
def test_hangul_does_not_hide_unrelated_foreign_script_prose(foreign):
    result = _hypothesis_content(observations=[f"문서는 점검을 {foreign} 설명한다."])
    assert subject._korean_output_reason(result) == "KOREAN_OUTPUT_REQUIRED"


@pytest.mark.parametrize(
    "overrides",
    [
        {"cause_summary": "현재 P1을 নির্দেশ 점검한다."},
        {"uncertainty": "현재 P1을 নির্দেশ 점검한다."},
        {"observations": ["현재 P1을 নির্দেশ 점검한다."]},
        {"evidence_synthesis": "현재 P1을 নির্দেশ 점검한다."},
        {"impact_summary": "현재 P1을 নির্দেশ 점검한다."},
        {"verification_steps": ["현재 P1을 নির্দেশ 점검한다."]},
        {"limitations": ["현재 P1을 নির্দেশ 점검한다."]},
        {
            "alternative_hypotheses": [
                {
                    "summary": "현재 P1을 নির্দেশ 점검한다.",
                    "lower_rank_reason": "근거가 부족하다.",
                }
            ]
        },
        {
            "alternative_hypotheses": [
                {
                    "summary": "센서 가설이다.",
                    "lower_rank_reason": "현재 P1을 নির্দেশ 점검한다.",
                }
            ]
        },
    ],
)
def test_mixed_script_check_covers_every_user_facing_narrative(overrides):
    assert subject._korean_output_reason(_hypothesis_content(**overrides)) == (
        "KOREAN_OUTPUT_REQUIRED"
    )


@pytest.mark.parametrize(
    "text",
    [
        "P1의 μ=5 µm, σ=0.2, Δ=−2, Ω=10, ±3σ, 25℃를 비교한다.",
        "P1의 평균(mean) 5.0과 CD-P1 PASS·OOC/OOS 0/6을 비교한다.",
        "P_α의 10⁻³ m³/s와 ℓ·Å·K 및 χ²≤1, 參照값을 확인한다.",
    ],
)
def test_scientific_greek_units_symbols_latin_and_hanja_remain_accepted(text):
    assert (
        subject._korean_output_reason(_hypothesis_content(observations=[text])) is None
    )


def test_only_supplied_source_ids_are_exempt_not_model_citations_or_adjacent_prose():
    identifier = "P-নির্দেশ"
    copied = _hypothesis_content(
        observations=[f"{identifier}의 현재값은 5이다."],
        supporting_parameter_ids=[identifier],
    )
    assert subject._korean_output_reason(copied) == "KOREAN_OUTPUT_REQUIRED"
    assert (
        subject._korean_output_reason(copied, source_identifiers=(identifier,)) is None
    )
    mixed = copied.model_copy(
        update={"observations": (f"{identifier}의 결과는 নির্দেশ 점검을 요구한다.",)}
    )
    assert subject._korean_output_reason(mixed, source_identifiers=(identifier,)) == (
        "KOREAN_OUTPUT_REQUIRED"
    )


def test_mixed_bengali_observation_gets_one_whole_generation_correction_and_usage():
    observations = ["확인된 P1 관측이다."] * 4 + ["문서는 점검을 নির্দেশ 설명한다."]
    responses = iter(
        [_completion(_content(observations=observations)), _completion(_content(), n=2)]
    )
    messages = []

    def chat(value, **_kwargs):
        messages.append(value)
        return next(responses)

    outcome = generate_hypothesis(None, None, _docs(), _route(), completion_port=chat)
    assert len(messages) == 2
    assert "KOREAN_OUTPUT_REQUIRED" in messages[1][1]["content"]
    assert "নির্দেশ" not in repr(messages[1])
    assert outcome.hypothesis.observations == (
        "압력 이상 패턴 한 건이 관측되었습니다.",
    )
    assert (outcome.llm_usage.input_tokens, outcome.llm_usage.output_tokens) == (30, 12)


def test_repeated_mixed_script_fails_closed_after_all_rounds_with_safe_reason():
    calls = 0

    def chat(_messages, **_kwargs):
        nonlocal calls
        calls += 1
        return _completion(
            _content(limitations=["현재 निर्देश 근거는 부족하다."]), n=calls
        )

    with pytest.raises(HypothesisGenerationError) as error:
        generate_hypothesis(None, None, _docs(), _route(), completion_port=chat)
    # 초도 + 교정 라운드를 모두 소진한 뒤에만 실패한다(MAX_GENERATION_ROUNDS).
    assert calls == subject.MAX_GENERATION_ROUNDS
    assert error.value.last_rejection_reason == "KOREAN_OUTPUT_REQUIRED"
    assert error.value.usage_or_none.input_tokens == sum(
        10 * (index + 1) for index in range(calls)
    )
    assert "निर्देश" not in str(error.value)


def test_generation_preserves_verified_nonlatin_model_and_document_ids():
    route = _route()
    route = replace(
        route,
        graph_evidence=(replace(route.graph_evidence[0], model_code="MODEL-निर्देश"),),
    )
    docs = _docs()
    docs = docs.model_copy(
        update={"hits": [docs.hits[0].model_copy(update={"chunk_id": "CHUNK-নির্দেশ"})]}
    )
    text = "MODEL-निर्देश와 CHUNK-নির্দেশ의 관측을 비교한다."
    outcome = generate_hypothesis(
        None,
        None,
        docs,
        route,
        completion_port=lambda *_args, **_kwargs: _completion(
            _content(observations=[text], supporting_chunk_ids=["CHUNK-নির্দেশ"])
        ),
    )
    assert outcome.hypothesis.observations == (text,)
    assert outcome.llm_usage.input_tokens == 10


def test_source_document_prose_cannot_whitelist_mixed_language_output():
    docs = _docs()
    docs = docs.model_copy(
        update={"hits": [docs.hits[0].model_copy(update={"content": "নির্দেশ"})]}
    )
    with pytest.raises(HypothesisGenerationError) as error:
        generate_hypothesis(
            None,
            None,
            docs,
            _route(),
            completion_port=lambda *_args, **_kwargs: _completion(
                _content(observations=["문서는 নির্দেশ 점검을 설명한다."])
            ),
        )
    assert error.value.last_rejection_reason == "KOREAN_OUTPUT_REQUIRED"


def test_generation_preserves_verified_raw_nonlatin_alarm_id_without_source_prefix():
    alarm = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-নির্দেশ")
    route = _route()
    route = replace(
        route,
        incident=replace(
            route.incident,
            requested_alarm=alarm,
            representative_alarm=alarm,
            member_alarms=(alarm,),
        ),
    )
    text = "TA-নির্দেশ의 관측 범위를 비교한다."
    outcome = generate_hypothesis(
        None,
        None,
        _docs(),
        route,
        completion_port=lambda *_args, **_kwargs: _completion(
            _content(
                observations=[text],
                supporting_alarms=[alarm.model_dump(mode="json")],
            )
        ),
    )
    assert outcome.hypothesis.observations == (text,)
    assert outcome.hypothesis.supporting_alarms == (alarm,)
    assert outcome.llm_usage.input_tokens == 10


def test_invalid_responses_stop_after_last_round_and_keep_usage(
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
    # 라운드를 모두 소진한 뒤 멈추고 추가 호출은 없다.
    assert calls == subject.MAX_GENERATION_ROUNDS
    assert exc.value.code == "HYPOTHESIS_STRUCTURE_INVALID"
    assert exc.value.usage_or_none is not None
    assert exc.value.usage_or_none.input_tokens == sum(
        10 * (index + 1) for index in range(calls)
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"supporting_alarms": []},
        {"supporting_alarms": [{"source": "TRACE", "alarm_id": "OUTSIDE"}]},
        {"supporting_chunk_ids": []},
        {"supporting_chunk_ids": ["OUTSIDE"]},
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
