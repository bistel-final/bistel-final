"""공통 LLM 클라이언트.

OpenAI 호환 chat completions 계약 하나로 세 경로를 모두 지원한다.
- provider=ollama (기본): 로컬 Ollama 의 /v1 호환 endpoint. API key 불필요
- provider=openai: LLM_API_KEY 필수, base URL 은 공식 endpoint 기본값
- 그 외 provider: LLM_BASE_URL + LLM_API_KEY (학원 게이트웨이 등
  OpenAI 호환 프록시)

설계 원칙
- 미준비는 오류가 아니다: 키·서버 미설정은 LlmNotReadyError 로 던지고
  호출부(Tool)가 {ok:false, reason:"LLM_NOT_READY: ..."} 로 변환한다.
  기본 5화면·필수 경로는 LLM 없이도 정상 동작해야 한다(WBS §D 주의).
- 비밀 비노출: API key·base URL·원문 예외를 로그·예외 메시지에 싣지 않는다.
- 재현성: model·temperature 는 config 로만 주입한다. 호출부가 임의 값을
  덮어쓰지 않는다.

C(LangGraph)와 공유를 전제로 app/common 에 둔다. provider 확정(팀 결정)은
.env 교체만으로 반영된다.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.common.config import (
    LLM_MAX_TOKENS,
    LLM_MODEL_MAIN,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SEC,
    OLLAMA_BASE_URL,
)


class LlmNotReadyError(RuntimeError):
    """LLM 을 호출할 수 없는 설정 상태. 요청 오류가 아니라 준비 문제다."""


class LlmTimeoutError(RuntimeError):
    """LLM 응답이 LLM_TIMEOUT_SEC 안에 오지 않았다."""


class LlmDependencyError(RuntimeError):
    """LLM 서버가 오류를 반환했거나 응답을 해석할 수 없다."""

    def __init__(
        self,
        message: str,
        *,
        usage: LlmResponseUsage | None = None,
    ) -> None:
        super().__init__(message)
        self._usage = usage

    @property
    def usage_or_none(self) -> LlmResponseUsage | None:
        """응답에서 검증을 마친 실제 usage가 있으면 반환한다."""

        return self._usage


_INT32_MAX = 2_147_483_647


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    """provider가 실제로 반환한 모델·token을 포함한 응답."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True, slots=True)
class LlmResponseUsage:
    """본문 검증 실패 시에도 보존할 provider 실제 model·token."""

    model: str
    prompt_tokens: int
    completion_tokens: int


#: 재시도 대상 status — rate limit 과 서버 일시 오류만. 그 외 4xx 는 재시도 무의미.
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_RETRY_BACKOFF_BASE_SEC = 1.0


def _is_reasoning_model(model: str) -> bool:
    """OpenAI 추론 계열 판별 — 채팅 파라미터 계약이 다르다.

    gpt-5* (5.x 가족: sol/terra/luna, mini/nano 포함)·o1·o3·o4 는
    max_completion_tokens 를 쓰고 temperature 를 거부한다.
    gpt-4o 계열·ollama·openai-compatible 은 기존 경로.
    """
    name = (model or "").strip().lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


def _retry_max() -> int:
    """최대 재시도 회수. env(LLM_RETRY_MAX)로 조정, 기본 2회."""
    raw = (os.getenv("LLM_RETRY_MAX") or "").strip()
    try:
        return max(0, int(raw)) if raw else 2
    except ValueError:
        return 2


def _resolve_endpoint() -> tuple[str, str]:
    """(base_url, api_key) 를 결정한다. 미준비면 LlmNotReadyError."""
    provider = LLM_PROVIDER.strip().lower()

    if provider == "ollama":
        # Ollama 는 /v1 에서 OpenAI 호환 API 를 제공하고 key 를 무시한다.
        return OLLAMA_BASE_URL.rstrip("/") + "/v1", "ollama"

    api_key = (os.getenv("LLM_API_KEY") or "").strip()
    base_url = (os.getenv("LLM_BASE_URL") or "").strip()

    if provider == "openai" and not base_url:
        base_url = "https://api.openai.com/v1"

    if not api_key:
        raise LlmNotReadyError(f"LLM_API_KEY 가 설정되지 않았다 (provider={provider}).")
    if not base_url:
        raise LlmNotReadyError(
            f"LLM_BASE_URL 이 설정되지 않았다 (provider={provider})."
        )

    return base_url.rstrip("/"), api_key


def configured_model() -> str:
    """원격 호출 없이 provider 설정과 저장할 모델 식별자를 검증한다."""

    _resolve_endpoint()
    model = LLM_MODEL_MAIN.strip()
    if not model or len(model) > 64:
        raise LlmNotReadyError("LLM model 설정이 올바르지 않다.")
    return model


def preflight_model() -> str:
    """run INSERT 전에 설정 모델이 provider에 실제 존재하는지 확인한다.

    생성 요청에서만 ``/models``를 읽으며 import·API process startup에는 영향을 주지
    않는다. 응답·URL·key·원문 예외는 반환하거나 로그에 넣지 않는다.
    """

    base_url, api_key = _resolve_endpoint()
    model = configured_model()
    try:
        response = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=float(min(5, LLM_TIMEOUT_SEC)),
        )
    except httpx.HTTPError as exc:
        raise LlmNotReadyError("LLM 모델 목록을 확인할 수 없다.") from exc
    if response.status_code != 200:
        raise LlmNotReadyError("LLM 모델 목록을 확인할 수 없다.")
    try:
        payload = response.json()
        items = payload["data"]
        model_ids = {
            item["id"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise LlmNotReadyError("LLM 모델 목록 형식이 올바르지 않다.") from exc
    if model not in model_ids:
        raise LlmNotReadyError("설정한 LLM 모델이 준비되지 않았다.")
    return model


def _request(
    messages: list[dict[str, str]],
    *,
    json_object: bool = False,
    json_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """OpenAI 호환 응답 JSON을 받는 공통 HTTP 경계."""
    if json_object and json_schema is not None:
        raise ValueError("JSON object mode와 JSON schema를 동시에 요청할 수 없습니다.")
    base_url, api_key = _resolve_endpoint()
    provider = LLM_PROVIDER.strip().lower()

    max_retries = _retry_max()
    attempt = 0
    while True:
        try:
            request_body: dict[str, Any] = {
                "model": LLM_MODEL_MAIN,
                "messages": messages,
            }
            if _is_reasoning_model(LLM_MODEL_MAIN):
                # GPT-5 계열(gpt-5*, o-series)은 max_tokens 대신 max_completion_tokens,
                # temperature 는 기본값 외 거부(보내면 400). 추론 강도는 SQL/Cypher
                # 한 줄 생성에 과하지 않게 낮게 — LLM_REASONING_EFFORT(기본 low)로 제어.
                request_body["max_completion_tokens"] = LLM_MAX_TOKENS
                request_body["reasoning_effort"] = (
                    os.getenv("LLM_REASONING_EFFORT", "low").strip() or "low"
                )
            else:
                request_body["temperature"] = LLM_TEMPERATURE
                request_body["max_tokens"] = LLM_MAX_TOKENS
            if json_schema is not None:
                # OpenAI 외 provider의 /v1 호환 endpoint는 json_schema를 거부하거나
                # 무시할 수 있다. 그 경로에서는 JSON object만 요청하고 호출부의
                # Pydantic·citation 검증과 1회 교정으로 구조를 fail-closed 확정한다.
                request_body["response_format"] = (
                    {
                        "type": "json_schema",
                        "json_schema": json_schema,
                    }
                    if provider == "openai"
                    else {"type": "json_object"}
                )
            elif json_object:
                request_body["response_format"] = {"type": "json_object"}
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=request_body,
                timeout=LLM_TIMEOUT_SEC,
            )
        except httpx.TimeoutException as exc:
            raise LlmTimeoutError(
                f"LLM 응답이 {LLM_TIMEOUT_SEC}초 안에 오지 않았다."
            ) from exc
        except httpx.HTTPError as exc:
            # 연결 거부(서버 미기동) 포함. 접속 정보를 메시지에 싣지 않는다.
            raise LlmNotReadyError(
                f"LLM 서버에 연결할 수 없다 (provider={LLM_PROVIDER})."
            ) from exc

        # rate limit·서버 일시 오류는 지수 backoff 후 재시도한다.
        # timeout 은 재시도하지 않는다 — 상한 지연이 불어난다.
        if response.status_code in _RETRYABLE_STATUS and attempt < max_retries:
            time.sleep(_RETRY_BACKOFF_BASE_SEC * (2**attempt))
            attempt += 1
            continue
        break

    if response.status_code != 200:
        raise LlmDependencyError(
            f"LLM 서버가 오류를 반환했다 (status={response.status_code})."
        )

    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise LlmDependencyError("LLM 응답 형식을 해석할 수 없다.") from exc
    if not isinstance(payload, dict):
        raise LlmDependencyError("LLM 응답 형식을 해석할 수 없다.")
    return payload


def _content(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmDependencyError("LLM 응답 형식을 해석할 수 없다.") from exc

    if not isinstance(content, str) or not content.strip():
        raise LlmDependencyError("LLM 응답이 비어 있다.")
    return content


def _usage_token(usage: object, field: str) -> int:
    if not isinstance(usage, dict):
        raise LlmDependencyError("LLM usage 형식을 해석할 수 없다.")
    value = usage.get(field)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > _INT32_MAX
    ):
        raise LlmDependencyError("LLM usage 형식을 해석할 수 없다.")
    return value


def chat_with_usage(
    messages: list[dict[str, str]],
    *,
    json_object: bool = False,
    json_schema: dict[str, Any] | None = None,
) -> ChatCompletion:
    """응답 본문과 provider 실제 model·usage를 엄격하게 반환한다."""

    payload = _request(
        messages,
        json_object=json_object,
        json_schema=json_schema,
    )
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip() or len(model.strip()) > 64:
        raise LlmDependencyError("LLM model 형식을 해석할 수 없다.")
    prompt_tokens = _usage_token(payload.get("usage"), "prompt_tokens")
    completion_tokens = _usage_token(payload.get("usage"), "completion_tokens")
    if not 0 < prompt_tokens + completion_tokens <= _INT32_MAX:
        raise LlmDependencyError("LLM usage 합계가 허용 범위를 넘었다.")
    usage = LlmResponseUsage(
        model=model.strip(),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    try:
        content = _content(payload)
    except LlmDependencyError as exc:
        raise LlmDependencyError(str(exc), usage=usage) from exc
    return ChatCompletion(
        content=content,
        model=usage.model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def chat(messages: list[dict[str, str]]) -> str:
    """기존 public 계약: 첫 응답 텍스트만 반환한다."""

    return _content(_request(messages))


__all__ = [
    "ChatCompletion",
    "LlmDependencyError",
    "LlmNotReadyError",
    "LlmResponseUsage",
    "LlmTimeoutError",
    "chat",
    "chat_with_usage",
    "configured_model",
    "preflight_model",
]
