"""Real v3/selector provider wiring with per-HTTP consent, no import-time IO.

Reuses production parsing/correction/usage accounting. Endpoint, model, request
temperature/seed/schema and credentials are checked again before EVERY HTTP
request, including transport retries. No credential or prompt is retained.
"""

import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import urlsplit

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_comparison import LlmConfiguration
from app.agent.u10_observer import EffectObserver
from app.agent.u10_preparation import RuntimePorts


def validate_runtime_configuration(config, binding, authorize):
    """Read-only preflight shared with every HTTP entry; no DNS or claim IO."""
    from app.agent import prompts, react
    from app.common import llm

    config = LlmConfiguration.model_validate(config.model_dump())
    if authorize(binding) is not True:
        raise EvidenceError("U10_DATA_EXPORT_NOT_AUTHORIZED")
    if (
        digest(canonical_json(config)) != binding.llm_config_sha256
        or llm.LLM_MODEL_MAIN != config.hypothesis_model_revision
        or llm.LLM_MODEL_MAIN != config.selector_model_revision
        or config.hypothesis_prompt_version != prompts.PROMPT_VERSION
        or config.selector_prompt_version != react.REACT_PROMPT_VERSION
    ):
        raise EvidenceError("LLM_CONFIG_MISMATCH")
    if config.request_policy == "U10-LUNA-REASONING-V1":
        if (
            not llm._is_reasoning_model(llm.LLM_MODEL_MAIN)
            or (os.getenv("LLM_REASONING_EFFORT", "low").strip() or "low")
            != config.reasoning_effort
            or llm.LLM_MAX_TOKENS != config.max_completion_tokens
        ):
            raise EvidenceError("LLM_CONFIG_MISMATCH")
        # Runtime temperature is irrelevant only when no temperature is sent.
        # The approved config explicitly records temperature/seed as null.
    elif (
        config.seed > 2_147_483_647
        or llm.LLM_TEMPERATURE != 0
        or llm._is_reasoning_model(llm.LLM_MODEL_MAIN)
    ):
        raise EvidenceError("LLM_CONFIG_MISMATCH")
    llm._resolve_endpoint()  # Check credential readiness without emitting values.


class RealProvider:
    def __init__(self, llm_config, binding, authorize):
        from app.common import llm

        self.config = LlmConfiguration.model_validate(llm_config.model_dump())
        self.binding, self.authorize = binding, authorize
        self._llm = llm
        self._endpoint, self._key = llm._resolve_endpoint()
        self.observations = []
        self._check_config()
        parsed = urlsplit(self._endpoint)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http"
                    and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
                )
            )
        ):
            raise EvidenceError("U10_PROVIDER_ENDPOINT_INVALID")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.addresses = {
            (address[4][0], address[4][1])
            for address in socket.getaddrinfo(
                parsed.hostname, port, type=socket.SOCK_STREAM
            )
        }

    def _check_config(self):
        llm = self._llm
        validate_runtime_configuration(self.config, self.binding, self.authorize)
        if llm._resolve_endpoint() != (self._endpoint, self._key):
            raise EvidenceError("LLM_CONFIG_MISMATCH")

    @contextmanager
    def scope(self, key):
        import httpx

        from app.agent import hypothesis, react
        from app.agent.tools import ThreadDeadlineRunner

        self._check_config()
        observer = EffectObserver(self.addresses)

        def post(url, *, headers, json, timeout):
            self._check_config()
            if (
                url != self._endpoint + "/chat/completions"
                or headers != {"Authorization": "Bearer " + self._key}
                or json.get("model") != self.config.hypothesis_model_revision
            ):
                raise EvidenceError("LLM_CONFIG_MISMATCH")
            if self.config.request_policy == "U10-LUNA-REASONING-V1":
                if (
                    any(k in json for k in ("temperature", "seed", "max_tokens"))
                    or json.get("reasoning_effort") != self.config.reasoning_effort
                    or type(json.get("max_completion_tokens")) is not int
                    or json["max_completion_tokens"]
                    != self.config.max_completion_tokens
                ):
                    raise EvidenceError("LLM_CONFIG_MISMATCH")
            elif json.get("temperature") != 0 or json.get("seed") != self.config.seed:
                raise EvidenceError("LLM_CONFIG_MISMATCH")
            with observer.provider_request():
                # No proxy environment or redirects can silently change egress.
                return httpx.post(
                    url,
                    headers=headers,
                    json=json,
                    timeout=timeout,
                    trust_env=False,
                    follow_redirects=False,
                )

        def complete(messages, *, json_schema, seed=None):
            if seed != self.config.seed:
                raise EvidenceError("LLM_CONFIG_MISMATCH")
            expected = (
                hypothesis.HYPOTHESIS_RESPONSE_SCHEMA,
                react.REACT_SELECT_SCHEMA,
            )
            if json_schema not in expected:
                raise EvidenceError("U10_PROVIDER_SCHEMA_INVALID")
            result = self._llm.chat_with_usage(
                messages,
                json_schema=json_schema,
                seed=seed,
                request_port=post,
            )
            if json_schema == react.REACT_SELECT_SCHEMA:
                try:
                    selected = json.loads(result.content)
                except (TypeError, ValueError):
                    selected = None  # Production parser records the invalid structure.
                observer.selected(selected)
            return result

        def generate(**kwargs):
            return hypothesis.generate_hypothesis(**kwargs, completion_port=complete)

        def select(context, *, seed):
            return react.select_next_step(context, seed=seed, completion_port=complete)

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="u10-read")
        with observer.active():
            try:
                yield RuntimePorts(
                    ThreadDeadlineRunner(executor),
                    generate,
                    select,
                    observer.observe,
                )
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
        observer.verify_closed()
        self.observations.append(
            {
                "fixture_id": key.fixture_id,
                "attempt_no": key.attempt_no,
                "policy": key.policy,
                "execution_order": key.execution_order,
                "provider_requests": observer.provider_requests,
                "observed_connections": observer.connections,
                "blocked_effect_attempts": observer.blocked,
                "send_action_selected": observer.send_selected,
            }
        )
