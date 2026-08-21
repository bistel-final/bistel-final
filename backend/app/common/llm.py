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


#: 재시도 대상 status — rate limit 과 서버 일시 오류만. 그 외 4xx 는 재시도 무의미.
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_RETRY_BACKOFF_BASE_SEC = 1.0


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


def chat(messages: list[dict[str, str]]) -> str:
    """chat completions 한 번을 호출해 첫 응답 텍스트를 반환한다.

    messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
    실패는 전부 이 모듈의 예외 3종으로 정규화된다. 원문 예외·URL·key 는
    메시지에 싣지 않는다.
    """
    base_url, api_key = _resolve_endpoint()

    max_retries = _retry_max()
    attempt = 0
    while True:
        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": LLM_MODEL_MAIN,
                    "messages": messages,
                    "temperature": LLM_TEMPERATURE,
                    "max_tokens": LLM_MAX_TOKENS,
                },
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
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmDependencyError("LLM 응답 형식을 해석할 수 없다.") from exc

    if not isinstance(content, str) or not content.strip():
        raise LlmDependencyError("LLM 응답이 비어 있다.")

    return content
