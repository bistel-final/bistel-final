"""V5-CM-5.3 five-dependency process isolation and recovery contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.ask import AgentAskUnavailable
from app.agent.public_schemas import AgentAskResponse
from app.agent.runtime_composition import get_agent_runtime
from app.common import db as db_module
from app.common import neo4j as neo4j_module
from app.common import readiness
from app.common.readiness import (
    CHECK_NAMES,
    READINESS_CACHE_TTL_SECONDS,
    ReadinessManager,
    ReadinessOrchestrator,
)
from app.main import app


class _FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance_past_cache(self) -> None:
        self.value += READINESS_CACHE_TTL_SECONDS


class _NoNetworkKafkaProbe:
    def measure(self) -> None:  # pragma: no cover - any call is a test failure
        raise AssertionError("Kafka probe must not run in offline fault isolation")


class _OfflineReadinessManager(ReadinessManager):
    """Keep production collect/cache while disabling background lifecycle I/O."""

    def start(self) -> None:
        # Production start submits RAG warmup and Kafka sampler.  Those paths have
        # separate tests; this fixture owns only request isolation and recovery.
        return None


@dataclass(slots=True)
class _Harness:
    client: TestClient
    clock: _FakeClock
    faults: set[str]
    io_calls: dict[str, int]


@pytest.fixture
def fault_isolation_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[set[str]], Any]:
    io_calls = {"engine": 0, "neo4j": 0, "http": 0, "kafka": 0}

    def forbidden(name: str) -> Callable[..., Any]:
        def call(*_args: Any, **_kwargs: Any) -> Any:
            io_calls[name] += 1
            raise AssertionError(f"external {name} factory was called")

        return call

    monkeypatch.setattr(db_module, "get_app_engine", forbidden("engine"))
    original_neo4j_factory = neo4j_module.get_neo4j_driver
    forbidden_neo4j = forbidden("neo4j")
    # close_neo4j_driver() intentionally inspects the cached factory at lifespan
    # shutdown. Preserve those cache methods while counting any actual factory call.
    forbidden_neo4j.cache_info = original_neo4j_factory.cache_info  # type: ignore[attr-defined]
    forbidden_neo4j.cache_clear = original_neo4j_factory.cache_clear  # type: ignore[attr-defined]
    monkeypatch.setattr(neo4j_module, "get_neo4j_driver", forbidden_neo4j)
    monkeypatch.setattr(readiness.urllib.request, "build_opener", forbidden("http"))

    @contextmanager
    def open_client(initial_faults: set[str]) -> Iterator[_Harness]:
        clock = _FakeClock()
        faults = set(initial_faults)
        executor = ThreadPoolExecutor(max_workers=len(CHECK_NAMES))
        manager = _OfflineReadinessManager(
            readiness_executor=executor,
            clock=clock,
            rag_warmup=lambda: None,
            kafka_probe_factory=lambda: (
                io_calls.__setitem__("kafka", io_calls["kafka"] + 1)
                or _NoNetworkKafkaProbe()
            ),
        )

        def provider(name: str) -> Callable[[], str | None]:
            return lambda: "DEPENDENCY_UNAVAILABLE" if name in faults else None

        manager._orchestrator = ReadinessOrchestrator(  # type: ignore[assignment]
            {name: provider(name) for name in CHECK_NAMES},
            executor,
            clock=clock,
        )
        original_factory = getattr(app.state, "readiness_manager_factory", None)
        app.state.readiness_manager_factory = lambda: manager
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                yield _Harness(client, clock, faults, io_calls)
        finally:
            app.dependency_overrides.clear()
            if original_factory is None:
                delattr(app.state, "readiness_manager_factory")
            else:
                app.state.readiness_manager_factory = original_factory
            assert app.dependency_overrides == {}
            assert io_calls == {"engine": 0, "neo4j": 0, "http": 0, "kafka": 0}

    return open_client


def _assert_fault_then_recovery(
    harness: _Harness,
    *,
    failed_checks: set[str],
) -> None:
    failed = harness.client.get("/health/ready")
    assert failed.status_code == 503
    payload = failed.json()
    assert payload["status"] == "NOT_READY"
    checks: Mapping[str, Mapping[str, Any]] = payload["checks"]
    assert set(checks) == set(CHECK_NAMES)
    for name, result in checks.items():
        if name in failed_checks:
            assert result["status"] == "FAIL"
            assert result["reason_code"] == "DEPENDENCY_UNAVAILABLE"
        else:
            assert result["status"] == "PASS"
            assert result["reason_code"] is None
    assert "dsn" not in failed.text.casefold()
    assert "password" not in failed.text.casefold()

    liveness = harness.client.get("/health")
    assert liveness.status_code == 200
    assert liveness.json() == {"status": "UP"}

    harness.faults.difference_update(failed_checks)
    harness.clock.advance_past_cache()
    recovered = harness.client.get("/health/ready")
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "READY"
    assert all(
        value["status"] == "PASS" for value in recovered.json()["checks"].values()
    )


def test_postgres_fault_keeps_process_alive_and_recovers(
    fault_isolation_client: Callable[[set[str]], Any],
) -> None:
    failed = {"postgresql_runtime", "reference_migration"}
    with fault_isolation_client(failed) as harness:
        _assert_fault_then_recovery(harness, failed_checks=failed)


def test_neo4j_fault_keeps_process_alive_and_recovers(
    fault_isolation_client: Callable[[set[str]], Any],
) -> None:
    with fault_isolation_client({"neo4j"}) as harness:
        _assert_fault_then_recovery(harness, failed_checks={"neo4j"})


class _MutableAskRuntime:
    def __init__(self) -> None:
        self.failing = True

    def ask_public(self, _question: str) -> AgentAskResponse:
        if self.failing:
            raise AgentAskUnavailable("credential=must-not-leak")
        return AgentAskResponse(
            title="Recovered",
            answer="The next independent request completed.",
            tools=[],
            predicted_fault_code=None,
            confidence=None,
            recommended_action=None,
            evidence_items=[],
            limitations=[],
            evidence=None,
            limit="",
        )


def test_llm_fault_returns_sanitized_503_then_recovers(
    fault_isolation_client: Callable[[set[str]], Any],
) -> None:
    runtime = _MutableAskRuntime()
    with fault_isolation_client(set()) as harness:
        app.dependency_overrides[get_agent_runtime] = lambda: runtime
        failed = harness.client.post(
            "/agent/ask", json={"question": "Explain LH-00181"}
        )
        assert failed.status_code == 503
        assert failed.json()["code"] == "DEPENDENCY_NOT_READY"
        assert "must-not-leak" not in failed.text
        assert "credential" not in failed.text.casefold()
        assert harness.client.get("/health").status_code == 200

        runtime.failing = False
        recovered = harness.client.post(
            "/agent/ask", json={"question": "Explain LH-00181"}
        )
        assert recovered.status_code == 200
        assert recovered.json()["title"] == "Recovered"
    assert app.dependency_overrides == {}


def test_n8n_fault_keeps_process_alive_and_recovers(
    fault_isolation_client: Callable[[set[str]], Any],
) -> None:
    with fault_isolation_client({"n8n"}) as harness:
        _assert_fault_then_recovery(harness, failed_checks={"n8n"})


def test_kafka_fault_keeps_process_alive_and_recovers(
    fault_isolation_client: Callable[[set[str]], Any],
) -> None:
    with fault_isolation_client({"kafka"}) as harness:
        _assert_fault_then_recovery(harness, failed_checks={"kafka"})
