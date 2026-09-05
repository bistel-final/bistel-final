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
        hypothesis_prompt_version="agent-hypothesis-v3-ko1",
        selector_prompt_version="agent-react-v2-ko1",
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


def provider(authorize=lambda _: True):
    cfg = config()
    binding = BatchBinding(
        "a" * 40, "b" * 64, digest(canonical_json(cfg)), "c" * 64, "d" * 64
    )
    return RealProvider(cfg, binding, authorize)


def completion(content):
    return httpx.Response(
        200,
        json={
            "model": "actual-model",
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
