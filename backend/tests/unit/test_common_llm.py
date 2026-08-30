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
