"""Synthetic HTTP only; never call local deployments or external providers."""

import subprocess
import sys

import httpx
import pytest

from app.agent import u10_readiness as subject
from app.agent.release_artifacts import EvidenceError, canonical_json

CHECKS = (
    "postgresql_runtime",
    "reference_migration",
    "neo4j",
    "rag",
    "n8n",
    "kafka",
)
PATHS = ("/api/health/ready", "/", "/api")


def ready():
    return {
        "status": "READY",
        "dataset_epoch": "fdc_final_20260818",
        "checks": {
            name: {"status": "PASS", "reason_code": None, "latency_ms": 0}
            for name in CHECKS
        },
    }


def verify(payload=None, *, body=None):
    if body is None:
        body = canonical_json(ready() if payload is None else payload)
    return subject.verify_readiness(
        fetch=lambda path: subject.ProbeResponse(
            200, body if path == PATHS[0] else b"private-html"
        )
    )


def test_success_uses_gateway_paths_and_returns_no_enable_or_profile_claim():
    calls = []

    def fetch(path):
        calls.append(path)
        return subject.ProbeResponse(200, canonical_json(ready()))

    result = subject.verify_readiness(fetch=fetch).model_dump(mode="json")
    assert calls == list(PATHS)
    assert result == {
        "gateway_origin": "http://127.0.0.1:8080",
        "backend_readiness": ready(),
        "frontend_status": 200,
        "api_status": 200,
    }


@pytest.mark.parametrize("name", CHECKS)
def test_each_dependency_failure_rejects_even_with_http_200(name):
    payload = ready()
    payload["checks"][name].update(status="FAIL", reason_code="TIMEOUT")
    payload["status"] = "NOT_READY"
    with pytest.raises(EvidenceError, match="^U10_READINESS_NOT_READY$"):
        verify(payload)


@pytest.mark.parametrize("name", CHECKS)
def test_each_missing_check_rejected(name):
    payload = ready()
    del payload["checks"][name]
    with pytest.raises(EvidenceError, match="^U10_READINESS_RESPONSE_INVALID$"):
        verify(payload)


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(dataset_epoch="kosa_0813"),
        lambda p: p.update(status="NOT_READY"),
        lambda p: p.update(private="secret"),
        lambda p: p["checks"].update(extra=p["checks"]["rag"]),
        lambda p: p["checks"]["rag"].update(status="FAIL", reason_code="TIMEOUT"),
        lambda p: p["checks"]["rag"].update(reason_code="TIMEOUT"),
        lambda p: p["checks"]["rag"].update(latency_ms=True),
        lambda p: p["checks"]["rag"].update(latency_ms="1"),
        lambda p: p["checks"]["rag"].update(latency_ms=-1),
        lambda p: p["checks"]["rag"].update(private="secret"),
    ],
)
def test_readiness_schema_is_strict_and_status_consistent(change):
    payload = ready()
    change(payload)
    with pytest.raises(EvidenceError, match="^U10_READINESS_RESPONSE_INVALID$"):
        verify(payload)


@pytest.mark.parametrize(
    "body",
    [b"not-json", b"[]", b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff"],
)
def test_invalid_json_is_sanitized(body):
    with pytest.raises(EvidenceError, match="^U10_READINESS_RESPONSE_INVALID$"):
        verify(body=body)


def test_valid_readiness_at_limit_passes_and_one_extra_byte_fails():
    body = canonical_json(ready())
    body += b" " * (16384 - len(body))
    assert verify(body=body).backend_readiness.status == "READY"
    with pytest.raises(EvidenceError, match="^U10_READINESS_RESPONSE_INVALID$"):
        verify(body=body + b" ")


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("status", [301, 404, 503])
def test_each_endpoint_requires_exact_200_and_stops_at_failure(path, status):
    calls = []

    def fetch(current):
        calls.append(current)
        return subject.ProbeResponse(
            status if current == path else 200, canonical_json(ready())
        )

    with pytest.raises(EvidenceError, match="^U10_READINESS_HTTP_STATUS_INVALID$"):
        subject.verify_readiness(fetch=fetch)
    assert calls == list(PATHS[: PATHS.index(path) + 1])


@pytest.mark.parametrize(
    "response",
    [
        None,
        {"status_code": 200},
        subject.ProbeResponse(True),
        subject.ProbeResponse("200"),
        subject.ProbeResponse(200, "private-body"),
    ],
)
def test_injected_transport_boundary_is_strict(response):
    with pytest.raises(EvidenceError, match="^U10_READINESS_RESPONSE_INVALID$"):
        subject.verify_readiness(fetch=lambda _: response)


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("private-url"), OSError("private-path")]
)
def test_transport_error_is_sanitized(error):
    def fetch(_):
        raise error

    with pytest.raises(EvidenceError, match="^U10_READINESS_HTTP_FAILED$"):
        subject.verify_readiness(fetch=fetch)


def mock_client(monkeypatch, handler):
    original = httpx.Client
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(subject.httpx, "Client", factory)
    return options


def test_default_transport_gets_only_local_gateway_without_proxy_or_redirect(
    monkeypatch,
):
    requests = []

    def handler(request):
        requests.append((request.method, str(request.url)))
        return httpx.Response(200, json=ready())

    options = mock_client(monkeypatch, handler)
    result = subject.verify_readiness(fetch=subject.fetch_gateway)
    assert result.backend_readiness.status == "READY"
    assert requests == [("GET", "http://127.0.0.1:8080" + path) for path in PATHS]
    assert (
        options
        == [{"trust_env": False, "follow_redirects": False, "timeout": 15.0}] * 3
    )


@pytest.mark.parametrize("path", ["https://example.com", "/api/other", "//other", None])
def test_default_transport_rejects_non_allowlisted_path_before_client(
    monkeypatch, path
):
    monkeypatch.setattr(subject.httpx, "Client", lambda **_: pytest.fail("HTTP called"))
    with pytest.raises(EvidenceError, match="^U10_READINESS_PATH_INVALID$"):
        subject.fetch_gateway(path)


def test_default_transport_does_not_follow_redirect(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://example.com/private"})

    mock_client(monkeypatch, handler)
    with pytest.raises(EvidenceError, match="^U10_READINESS_HTTP_STATUS_INVALID$"):
        subject.verify_readiness(fetch=subject.fetch_gateway)
    assert calls == ["http://127.0.0.1:8080/api/health/ready"]


def test_default_transport_caps_valid_json_before_return(monkeypatch):
    body = canonical_json(ready())
    body += b" " * (16385 - len(body))
    mock_client(monkeypatch, lambda _: httpx.Response(200, content=body))
    with pytest.raises(EvidenceError, match="^U10_READINESS_RESPONSE_INVALID$"):
        subject.fetch_gateway(PATHS[0])


def test_default_transport_sanitizes_connection_failure(monkeypatch):
    def handler(_):
        raise httpx.ConnectError("private-url")

    mock_client(monkeypatch, handler)
    with pytest.raises(EvidenceError, match="^U10_READINESS_HTTP_FAILED$"):
        subject.fetch_gateway(PATHS[0])


@pytest.mark.parametrize("path,status", [("/", 200), ("/api", 200), (PATHS[0], 503)])
def test_default_transport_closes_unused_body_without_reading(
    monkeypatch, path, status
):
    class UnreadBody(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            pytest.fail("non-readiness/error body must not be read")
            yield b"private-body"

        def close(self):
            self.closed = True

    stream = UnreadBody()
    mock_client(monkeypatch, lambda _: httpx.Response(status, stream=stream))
    assert subject.fetch_gateway(path) == subject.ProbeResponse(status)
    assert stream.closed


def test_oversize_stream_stops_reading_and_closes(monkeypatch):
    class OversizeBody(httpx.SyncByteStream):
        closed = False
        chunks = 0

        def __iter__(self):
            for _ in range(6):
                self.chunks += 1
                yield b" " * 4096

        def close(self):
            self.closed = True

    stream = OversizeBody()
    mock_client(monkeypatch, lambda _: httpx.Response(200, stream=stream))
    with pytest.raises(EvidenceError, match="^U10_READINESS_RESPONSE_INVALID$"):
        subject.fetch_gateway(PATHS[0])
    assert stream.chunks == 5 and stream.closed


def test_import_does_not_load_providers_or_perform_io():
    code = """
import sys, socket, subprocess, httpx
def forbidden(*args, **kwargs): raise AssertionError('IO on import')
socket.socket = forbidden
subprocess.run = forbidden
httpx.Client = forbidden
import app.agent.u10_readiness
assert 'app.agent.graph' not in sys.modules
assert 'app.common.config' not in sys.modules
assert 'app.common.db' not in sys.modules
assert 'app.common.llm' not in sys.modules
assert 'app.common.readiness' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
