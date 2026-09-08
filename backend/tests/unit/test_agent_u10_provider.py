"""Real provider/observer seams with HTTP mocked: no data export or live effects."""

import json
import os
import socket
import subprocess
import sys
import threading
from dataclasses import replace

import httpx
import pytest

from app.agent.react import REACT_PROMPT_VERSION
from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_batch import AttemptKey, BatchBinding
from app.agent.u10_comparison import LlmConfiguration
from app.agent.u10_observer import EffectObserver
from app.agent.u10_provider import RealProvider
from app.common import llm
from tests.unit.test_agent_hypothesis import _content
from tests.unit.test_agent_u10_hypothesis import observed

KEY = AttemptKey("CF-1", 1, "REACT_V2", 2)


def config():
    return LlmConfiguration(
        hypothesis_model_revision="actual-model",
        selector_model_revision="actual-model",
        hypothesis_prompt_version="agent-hypothesis-v3-ko5",
        selector_prompt_version=REACT_PROMPT_VERSION,
        temperature=0.0,
        seed=13,
    )


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "actual-model")
    monkeypatch.setattr(llm, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(llm, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(
        llm, "_resolve_endpoint", lambda: ("https://provider.test/v1", "test-key")
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))
        ],
    )
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    monkeypatch.setenv("LLM_RETRY_MAX", "1")


def luna_config():
    return LlmConfiguration.model_validate(
        {
            **config().model_dump(),
            "hypothesis_model_revision": "gpt-5.6-luna",
            "selector_model_revision": "gpt-5.6-luna",
            "temperature": None,
            "seed": None,
            "request_policy": "U10-LUNA-REASONING-V1",
            "reasoning_effort": "low",
            "max_completion_tokens": 1500,
        }
    )


@pytest.fixture
def luna_settings(settings, monkeypatch):
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "gpt-5.6-luna")
    monkeypatch.setattr(llm, "LLM_TEMPERATURE", 0.1)
    monkeypatch.setattr(llm, "LLM_MAX_TOKENS", 1500)
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")


def provider(authorize=lambda _: True, cfg=None):
    cfg = config() if cfg is None else cfg
    binding = BatchBinding(
        "a" * 40, "b" * 64, digest(canonical_json(cfg)), "c" * 64, "d" * 64
    )
    return RealProvider(cfg, binding, authorize)


@pytest.mark.parametrize(
    "old_selector_version",
    ["agent-react-v2-ko1", "agent-react-v2-ko2", "agent-react-v2-ko3"],
)
def test_old_prompts_remain_readable_but_cannot_enter_new_live_execution(
    settings, old_selector_version
):
    from app.agent import prompts, react
    from app.agent.u10_provider import validate_runtime_configuration

    cfg = config()
    assert cfg.hypothesis_prompt_version == prompts.PROMPT_VERSION
    assert cfg.selector_prompt_version == react.REACT_PROMPT_VERSION
    binding = BatchBinding(
        "a" * 40, "b" * 64, digest(canonical_json(cfg)), "c" * 64, "d" * 64
    )
    validate_runtime_configuration(cfg, binding, lambda _: True)
    old_selector = LlmConfiguration.model_validate(
        {**cfg.model_dump(), "selector_prompt_version": old_selector_version}
    )
    old_selector_binding = replace(
        binding, llm_config_sha256=digest(canonical_json(old_selector))
    )
    with pytest.raises(EvidenceError, match="LLM_CONFIG_MISMATCH"):
        validate_runtime_configuration(
            old_selector, old_selector_binding, lambda _: True
        )
    for old_version in ("agent-hypothesis-v3-ko1", "agent-hypothesis-v3-ko2"):
        old = LlmConfiguration.model_validate(
            {**cfg.model_dump(), "hypothesis_prompt_version": old_version}
        )
        old_binding = replace(binding, llm_config_sha256=digest(canonical_json(old)))
        with pytest.raises(EvidenceError, match="LLM_CONFIG_MISMATCH"):
            validate_runtime_configuration(old, old_binding, lambda _: True)


def test_selector_module_drift_blocks_admission(settings, monkeypatch):
    from app.agent import react
    from app.agent.u10_provider import validate_runtime_configuration

    cfg = config()
    binding = BatchBinding(
        "a" * 40, "b" * 64, digest(canonical_json(cfg)), "c" * 64, "d" * 64
    )
    monkeypatch.setattr(react, "REACT_PROMPT_VERSION", "agent-react-new-version")
    with pytest.raises(EvidenceError, match="LLM_CONFIG_MISMATCH"):
        validate_runtime_configuration(cfg, binding, lambda _: True)


def completion(content, model="actual-model"):
    return httpx.Response(
        200,
        json={
            "model": model,
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            "choices": [{"message": {"content": content}}],
        },
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://provider.test/v1",
        "https://u:p@provider.test",
        "https://provider.test/v1?redirect=unsafe",
        "https://provider.test/v1#unsafe",
    ],
)
def test_invalid_endpoint_is_rejected_before_dns(settings, monkeypatch, endpoint):
    dns = []
    monkeypatch.setattr(llm, "_resolve_endpoint", lambda: (endpoint, "test-key"))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns.append(a) or [])
    with pytest.raises(EvidenceError, match="^U10_PROVIDER_ENDPOINT_INVALID$"):
        provider()
    assert dns == []


def stop_payload():
    return json.dumps(
        {
            "rationale_summary": "근거를 확인했습니다.",
            "next": "stop",
            "arguments": {
                k: None
                for k in (
                    "fdc_candidate_id",
                    "history_candidate_id",
                    "metrology_candidate_id",
                    "query",
                )
            },
        }
    )


def test_real_selector_transport_and_usage(settings, monkeypatch):
    posts = []

    def post(url, **kwargs):
        posts.append(kwargs)
        return completion(stop_payload())

    monkeypatch.setattr(httpx, "post", post)
    p = provider()
    state = observed()
    with p.scope(KEY) as ports:
        result = ports.select(state.build_context(), seed=13)
        assert result.selection.next == "stop"
        assert (
            result.llm_usage.input_tokens == 10 and result.llm_usage.output_tokens == 4
        )
        safety, effects = ports.observe_effects()
        assert effects == 0 and sum(safety.model_dump().values()) == 0
    assert len(posts) == 1
    assert posts[0]["trust_env"] is False and posts[0]["follow_redirects"] is False
    assert posts[0]["json"]["temperature"] == 0 and posts[0]["json"]["seed"] == 13


@pytest.mark.parametrize("mode", ["selector", "hypothesis", "retry", "correction"])
def test_luna_actual_request_shape_and_usage(luna_settings, monkeypatch, mode):
    posts, state = [], observed()
    alarm = (
        state.hypothesis_inputs()["route"]
        .incident.member_alarms[0]
        .model_dump(mode="json")
    )
    valid = _content(
        supporting_alarms=[alarm], supporting_chunk_ids=[], supporting_relation_ids=[]
    )

    def post(url, **kwargs):
        body = kwargs["json"]
        assert body["model"] == "gpt-5.6-luna"
        assert not {"temperature", "seed", "max_tokens"} & body.keys()
        assert body["reasoning_effort"] == "low"
        assert body["max_completion_tokens"] == 1500
        posts.append(body)
        if mode == "retry" and len(posts) == 1:
            return httpx.Response(503)
        content = "{}" if mode == "correction" and len(posts) == 1 else valid
        return completion(
            stop_payload() if mode in {"selector", "retry"} else content, "gpt-5.6-luna"
        )

    monkeypatch.setattr(httpx, "post", post)
    p = provider(cfg=luna_config())
    with p.scope(KEY) as ports:
        result = (
            ports.select(state.build_context(), seed=None)
            if mode in {"selector", "retry"}
            else ports.generate(**state.hypothesis_inputs(), seed=None)
        )
    assert result.llm_usage.input_tokens == (20 if mode == "correction" else 10)
    assert p.observations[0]["provider_requests"] == (
        2 if mode in {"retry", "correction"} else 1
    )
    assert p.observations[0]["blocked_effect_attempts"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature", 0),
        ("seed", 0),
        ("max_tokens", 1500),
        ("reasoning_effort", "high"),
        ("max_completion_tokens", 1501),
    ],
)
def test_luna_tampered_wire_parameters_never_reach_http(
    luna_settings, monkeypatch, field, value
):
    original = llm.chat_with_usage

    def changed(messages, *, request_port, **kwargs):
        def port(url, **request):
            request["json"][field] = value
            return request_port(url, **request)

        return original(messages, request_port=port, **kwargs)

    monkeypatch.setattr(llm, "chat_with_usage", changed)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: pytest.fail("HTTP called"))
    p = provider(cfg=luna_config())
    with pytest.raises(EvidenceError, match="^LLM_CONFIG_MISMATCH$"):
        with p.scope(KEY) as ports:
            ports.select(observed().build_context(), seed=None)


@pytest.mark.parametrize("change", ["effort", "tokens", "grant"])
@pytest.mark.parametrize("mode", ["retry", "correction"])
def test_luna_drift_revocation_blocks_retry_and_correction(
    luna_settings, monkeypatch, change, mode
):
    approved, calls = [True], []
    p = provider(lambda _: approved[0], cfg=luna_config())

    def post(*args, **kwargs):
        calls.append(1)
        if change == "effort":
            monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
        elif change == "tokens":
            monkeypatch.setattr(llm, "LLM_MAX_TOKENS", 1501)
        else:
            approved[0] = False
        return (
            httpx.Response(503) if mode == "retry" else completion("{}", "gpt-5.6-luna")
        )

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(
        EvidenceError, match="U10_DATA_EXPORT_NOT_AUTHORIZED|LLM_CONFIG_MISMATCH"
    ):
        with p.scope(KEY) as ports:
            if mode == "retry":
                ports.select(observed().build_context(), seed=None)
            else:
                ports.generate(**observed().hypothesis_inputs(), seed=None)
    assert calls == [1]


def test_luna_old_temperature_zero_grant_still_rejected_before_dns(
    luna_settings, monkeypatch
):
    monkeypatch.setattr(llm, "LLM_TEMPERATURE", 0)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: pytest.fail("DNS called")
    )
    old = config().model_copy(
        update={
            "hypothesis_model_revision": "gpt-5.6-luna",
            "selector_model_revision": "gpt-5.6-luna",
        }
    )
    with pytest.raises(EvidenceError, match="^LLM_CONFIG_MISMATCH$"):
        provider(cfg=old)


@pytest.mark.parametrize("mode", ["retry", "correction"])
def test_revocation_before_each_transport_or_correction(settings, monkeypatch, mode):
    approved, posts = [True], []
    p = provider(lambda _: approved[0])
    state = observed()

    def post(*args, **kwargs):
        posts.append(1)
        approved[0] = False
        return httpx.Response(503) if mode == "retry" else completion("{}")

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(EvidenceError, match="U10_DATA_EXPORT_NOT_AUTHORIZED"):
        with p.scope(KEY) as ports:
            if mode == "retry":
                ports.select(state.build_context(), seed=13)
            else:
                ports.generate(**state.hypothesis_inputs(), seed=13)
    assert posts == [1]


@pytest.mark.parametrize(
    "change", ["model", "temperature", "endpoint", "binding", "seed"]
)
def test_configuration_drift_before_request(settings, monkeypatch, change):
    p = provider()
    state = observed()
    monkeypatch.setattr(
        httpx, "post", lambda *a, **kw: pytest.fail("HTTP must not run")
    )
    with pytest.raises(EvidenceError, match="LLM_CONFIG_MISMATCH"):
        with p.scope(KEY) as ports:
            if change == "model":
                monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "changed")
            elif change == "temperature":
                monkeypatch.setattr(llm, "LLM_TEMPERATURE", 0.5)
            elif change == "endpoint":
                monkeypatch.setattr(
                    llm, "_resolve_endpoint", lambda: ("https://other.test", "key")
                )
            elif change == "binding":
                p.binding = replace(p.binding, llm_config_sha256="0" * 64)
            ports.select(state.build_context(), seed=14 if change == "seed" else 13)


def test_no_consent_means_no_dns(settings, monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: pytest.fail("DNS called")
    )
    with pytest.raises(EvidenceError, match="U10_DATA_EXPORT_NOT_AUTHORIZED"):
        provider(lambda _: False)


@pytest.mark.parametrize("event", ["connect", "file", "process", "mkdir", "sendto"])
def test_observer_blocks_real_io_before_effect(tmp_path, event):
    observer = EffectObserver({("192.0.2.1", 443)})
    with observer.active():
        with pytest.raises(EvidenceError, match="U10_UNEXPECTED_EFFECT_BLOCKED"):
            if event == "connect":
                with socket.socket() as sock:
                    sock.connect(("127.0.0.1", 9))
            elif event == "file":
                (tmp_path / "forbidden").write_text("no")
            elif event == "process":
                subprocess.run([sys.executable, "-c", "pass"], check=True)
            elif event == "mkdir":
                os.mkdir(tmp_path / "forbidden")
            else:
                with socket.socket(type=socket.SOCK_DGRAM) as sock:
                    sock.sendto(b"no", ("127.0.0.1", 9))
        assert observer.observe()[1] == 1
    assert not (tmp_path / "forbidden").exists()
    with pytest.raises(EvidenceError):
        observer.verify_closed()


def test_allowlisted_connect_only_during_provider_request_and_no_reuse():
    observer = EffectObserver({("192.0.2.1", 443)})
    with observer.active():
        with observer.provider_request():
            sys.audit("socket.connect", None, ("192.0.2.1", 443))
        assert observer.connections == 1 and observer.provider_requests == 1
    observer.verify_closed()
    with pytest.raises(EvidenceError):
        with observer.active():
            pytest.fail("reuse")


def test_send_selection_is_not_reported_as_safe(settings, monkeypatch):
    from app.agent.react import ReactSelectionError

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: completion(stop_payload().replace('"stop"', '"send_action"')),
    )
    state, p = observed(), provider()
    with pytest.raises(EvidenceError, match="U10_UNEXPECTED_EFFECT_BLOCKED"):
        with p.scope(KEY) as ports:
            with pytest.raises(ReactSelectionError):
                ports.select(state.build_context(), seed=13)
            assert ports.observe_effects()[0].send_action_selected == 1


def test_uninstalled_audit_hook_fails_before_scope_entry():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from app.agent.u10_observer import EffectObserver
from app.agent.release_artifacts import EvidenceError
sys.addaudithook = lambda _: None
try:
    with EffectObserver({('127.0.0.1', 9)}).active():
        raise AssertionError('unobserved scope opened')
except EvidenceError as exc:
    assert str(exc) == 'U10_OBSERVER_UNAVAILABLE'
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_successful_correction_accumulates_actual_usage(settings, monkeypatch):
    state, p, calls = observed(), provider(), []
    alarm = (
        state.hypothesis_inputs()["route"]
        .incident.member_alarms[0]
        .model_dump(mode="json")
    )
    valid = _content(
        supporting_alarms=[alarm], supporting_chunk_ids=[], supporting_relation_ids=[]
    )

    def post(*args, **kwargs):
        calls.append(1)
        return completion("{}" if len(calls) == 1 else valid)

    monkeypatch.setattr(httpx, "post", post)
    with p.scope(KEY) as ports:
        result = ports.generate(**state.hypothesis_inputs(), seed=13)
        assert (result.llm_usage.input_tokens, result.llm_usage.output_tokens) == (
            20,
            8,
        )
    assert p.observations[0]["provider_requests"] == 2


def test_late_read_worker_is_joined_under_same_effect_fence(settings, tmp_path):
    p, release = provider(), threading.Event()

    def late(_):
        release.wait(1)
        (tmp_path / "late-write").write_text("must never happen")

    with pytest.raises(EvidenceError, match="U10_UNEXPECTED_EFFECT_BLOCKED"):
        with p.scope(KEY) as ports:
            with pytest.raises(TimeoutError):
                ports.deadline.call(late, {}, seconds=0.001)
            release.set()
    assert not (tmp_path / "late-write").exists() and p.observations == []
