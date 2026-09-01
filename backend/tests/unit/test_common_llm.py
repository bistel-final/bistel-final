"""공통 LLM usage 응답 계약 회귀."""

from __future__ import annotations

import httpx
import pytest

from app.common import llm


def _response(
    *,
    content: object = "answer",
    model: object = "provider-model",
    usage: object = None,
) -> httpx.Response:
    if usage is None:
        usage = {"prompt_tokens": 7, "completion_tokens": 3}
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "model": model,
            "usage": usage,
        },
    )


def test_chat_with_usage_uses_provider_model_not_request_model(monkeypatch) -> None:
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "requested-model")
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: _response())
    result = llm.chat_with_usage([{"role": "user", "content": "q"}])
    assert result.model == "provider-model"
    assert (result.prompt_tokens, result.completion_tokens) == (7, 3)


def test_ollama_downgrades_requested_json_schema_to_json_object(monkeypatch) -> None:
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    requests: list[dict[str, object]] = []

    def post(*args, **kwargs):
        requests.append(kwargs["json"])
        return _response()

    monkeypatch.setattr(llm.httpx, "post", post)

    llm.chat_with_usage([{"role": "user", "content": "q"}])
    llm.chat_with_usage(
        [{"role": "user", "content": "q"}],
        json_object=True,
    )
    schema = {
        "name": "answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    }
    llm.chat_with_usage(
        [{"role": "user", "content": "q"}],
        json_schema=schema,
    )

    assert "response_format" not in requests[0]
    assert requests[1]["response_format"] == {"type": "json_object"}
    assert requests[2]["response_format"] == {"type": "json_object"}


def test_openai_preserves_requested_json_schema(monkeypatch) -> None:
    monkeypatch.setattr(llm, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(
        llm, "LLM_MODEL_MAIN", "gpt-4o-mini"
    )  # 기존(비추론) 경로로 고정 — 환경 독립
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    requests: list[dict[str, object]] = []

    def post(*args, **kwargs):
        requests.append(kwargs["json"])
        return _response()

    monkeypatch.setattr(llm.httpx, "post", post)
    schema = {
        "name": "answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    }

    llm.chat_with_usage(
        [{"role": "user", "content": "q"}],
        json_schema=schema,
    )

    assert requests == [
        {
            "model": llm.LLM_MODEL_MAIN,
            "messages": [{"role": "user", "content": "q"}],
            "temperature": llm.LLM_TEMPERATURE,
            "max_tokens": llm.LLM_MAX_TOKENS,
            "response_format": {"type": "json_schema", "json_schema": schema},
        }
    ]


def test_reasoning_model_uses_gpt5_parameter_contract(monkeypatch) -> None:
    """gpt-5 계열: max_completion_tokens + reasoning_effort,
    temperature/max_tokens 없음 (보내면 400)."""
    monkeypatch.setattr(llm, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "gpt-5.6-luna")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    requests: list[dict[str, object]] = []

    def post(*args, **kwargs):
        requests.append(kwargs["json"])
        return _response()

    monkeypatch.setattr(llm.httpx, "post", post)
    llm.chat_with_usage([{"role": "user", "content": "q"}])

    body = requests[0]
    assert body["model"] == "gpt-5.6-luna"
    assert body["max_completion_tokens"] == llm.LLM_MAX_TOKENS
    assert body["reasoning_effort"] == "low"
    assert "temperature" not in body and "max_tokens" not in body


def test_legacy_model_keeps_existing_parameter_contract(monkeypatch) -> None:
    """gpt-4o 계열·ollama 경로는 바이트 단위로 기존 그대로 — C파트 Agent 호출 무영향."""
    monkeypatch.setattr(llm, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "gpt-4o-mini")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    requests: list[dict[str, object]] = []

    def post(*args, **kwargs):
        requests.append(kwargs["json"])
        return _response()

    monkeypatch.setattr(llm.httpx, "post", post)
    llm.chat_with_usage([{"role": "user", "content": "q"}])

    body = requests[0]
    assert body["temperature"] == llm.LLM_TEMPERATURE
    assert body["max_tokens"] == llm.LLM_MAX_TOKENS
    assert "max_completion_tokens" not in body and "reasoning_effort" not in body


def test_chat_with_usage_rejects_ambiguous_response_formats(monkeypatch) -> None:
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    with pytest.raises(ValueError, match="동시"):
        llm.chat_with_usage(
            [{"role": "user", "content": "q"}],
            json_object=True,
            json_schema={"name": "answer", "strict": True, "schema": {}},
        )


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"prompt_tokens": True, "completion_tokens": 1},
        {"prompt_tokens": -1, "completion_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": 2_147_483_648},
        {"prompt_tokens": 2_147_483_647, "completion_tokens": 1},
        {"prompt_tokens": 0, "completion_tokens": 0},
    ],
)
def test_usage_contract_fails_closed(monkeypatch, usage: object) -> None:
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *args, **kwargs: _response(usage=usage),
    )
    with pytest.raises(llm.LlmDependencyError):
        llm.chat_with_usage([{"role": "user", "content": "q"}])


@pytest.mark.parametrize("content", ["", "   ", None])
def test_invalid_content_preserves_valid_provider_usage(
    monkeypatch, content: object
) -> None:
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *args, **kwargs: _response(content=content),
    )
    with pytest.raises(llm.LlmDependencyError) as exc:
        llm.chat_with_usage([{"role": "user", "content": "q"}])
    assert exc.value.usage_or_none == llm.LlmResponseUsage(
        model="provider-model",
        prompt_tokens=7,
        completion_tokens=3,
    )


def test_legacy_chat_does_not_require_new_usage_fields(monkeypatch) -> None:
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "legacy"}}]},
        ),
    )
    assert llm.chat([{"role": "user", "content": "q"}]) == "legacy"


def test_preflight_returns_only_a_model_present_in_provider_list(monkeypatch) -> None:
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "configured-model")
    monkeypatch.setattr(
        llm.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            json={"data": [{"id": "configured-model"}]},
        ),
    )

    assert llm.preflight_model() == "configured-model"


def test_preflight_fails_closed_when_configured_model_is_absent(monkeypatch) -> None:
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "configured-model")
    monkeypatch.setattr(
        llm.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            json={"data": [{"id": "another-model"}]},
        ),
    )

    with pytest.raises(llm.LlmNotReadyError):
        llm.preflight_model()
