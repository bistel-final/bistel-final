from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.common import readiness
from app.common.db import DB_CONNECT_TIMEOUT_SECONDS
from app.common.kafka_config import KafkaClientConfig, KafkaConfigError
from app.common.kafka_readiness import KafkaAdminProbe, KafkaLagTracker
from app.common.readiness import (
    CHECK_NAMES,
    POSTGRES_LOCK_TIMEOUT_SECONDS,
    POSTGRES_STATEMENT_TIMEOUT_SECONDS,
    READINESS_DEADLINE_SECONDS,
    RagWarmupController,
    ReadinessManager,
    ReadinessOrchestrator,
)
from app.common.readiness_markers import (
    READINESS_MARKER_FILENAMES,
    MarkerBundle,
)
from app.common.schemas import (
    ReadinessCheck,
    ReadinessChecks,
    ReadinessResponse,
)
from app.main import app

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _response(*, failed: str | None = None) -> ReadinessResponse:
    checks = {
        name: ReadinessCheck(
            status="FAIL" if name == failed else "PASS",
            reason_code="TIMEOUT" if name == failed else None,
            latency_ms=1,
        )
        for name in CHECK_NAMES
    }
    return ReadinessResponse(
        status="NOT_READY" if failed else "READY",
        dataset_epoch="fdc_final_20260818",
        checks=ReadinessChecks(**checks),
    )


class _FakeManager:
    def __init__(self, result: ReadinessResponse) -> None:
        self.result = result
        self.start_calls = 0
        self.collect_calls = 0
        self.close_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def collect(self) -> ReadinessResponse:
        self.collect_calls += 1
        return self.result

    def close(self) -> None:
        self.close_calls += 1


class _FailingStartManager(_FakeManager):
    def start(self) -> None:
        self.start_calls += 1
        raise RuntimeError("startup dependency failure")


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [(_response(), 200), (_response(failed="neo4j"), 503)],
)
def test_readiness_route_returns_exact_contract(
    result: ReadinessResponse,
    expected_status: int,
) -> None:
    manager = _FakeManager(result)
    app.state.readiness_manager_factory = lambda: manager
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        del app.state.readiness_manager_factory

    assert response.status_code == expected_status
    assert response.json() == result.model_dump(mode="json")
    assert set(response.json()["checks"]) == set(CHECK_NAMES)
    assert manager.start_calls == manager.collect_calls == manager.close_calls == 1


def test_health_is_process_only_after_lifespan_start() -> None:
    manager = _FakeManager(_response())
    app.state.readiness_manager_factory = lambda: manager
    try:
        with TestClient(app) as client:
            before = manager.collect_calls
            response = client.get("/health")
            after = manager.collect_calls
    finally:
        del app.state.readiness_manager_factory

    assert response.status_code == 200
    assert response.json() == {"status": "UP"}
    assert (before, after) == (0, 0)


def test_background_start_failure_does_not_block_health() -> None:
    manager = _FailingStartManager(_response())
    app.state.readiness_manager_factory = lambda: manager
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        del app.state.readiness_manager_factory

    assert response.status_code == 200
    assert response.json() == {"status": "UP"}
    assert manager.start_calls == manager.close_calls == 1
    assert manager.collect_calls == 0


def test_readiness_dto_rejects_reason_status_mismatch_and_wrong_owner() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        ReadinessCheck(status="PASS", reason_code="TIMEOUT", latency_ms=1)

    passed = ReadinessCheck(status="PASS", reason_code=None, latency_ms=1)
    with pytest.raises(ValidationError, match="허용되지 않은"):
        ReadinessChecks(
            postgresql_runtime=passed,
            reference_migration=passed,
            neo4j=passed,
            rag=passed,
            n8n=ReadinessCheck(
                status="FAIL",
                reason_code="RAG_MODEL_NOT_READY",
                latency_ms=1,
            ),
            kafka=passed,
        )


def test_orchestrator_starts_all_six_checks_concurrently() -> None:
    barrier = threading.Barrier(len(CHECK_NAMES))

    def provider() -> None:
        barrier.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=6) as executor:
        result = ReadinessOrchestrator(
            {name: provider for name in CHECK_NAMES},
            executor,
            deadline_seconds=2,
        ).collect()

    assert result.status == "READY"
    assert all(getattr(result.checks, name).status == "PASS" for name in CHECK_NAMES)


def test_orchestrator_global_deadline_returns_all_checks_without_late_pollution() -> (
    None
):
    release = threading.Event()

    def blocked() -> None:
        release.wait(timeout=2)

    providers = {
        name: (blocked if name == "rag" else lambda: None) for name in CHECK_NAMES
    }
    executor = ThreadPoolExecutor(max_workers=6)
    try:
        started = time.monotonic()
        result = ReadinessOrchestrator(
            providers,
            executor,
            deadline_seconds=0.05,
        ).collect()
        elapsed = time.monotonic() - started
        release.set()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    assert elapsed < 0.5
    assert result.status == "NOT_READY"
    assert result.checks.rag.reason_code == "TIMEOUT"
    assert set(result.checks.model_dump()) == set(CHECK_NAMES)


def test_manager_collect_shares_inflight_result_and_short_cache() -> None:
    clock = [10.0]

    class CountingOrchestrator:
        def __init__(self) -> None:
            self.calls = 0
            self.started = threading.Event()
            self.release = threading.Event()

        def collect(self) -> ReadinessResponse:
            self.calls += 1
            self.started.set()
            assert self.release.wait(timeout=2)
            return _response()

    orchestrator = CountingOrchestrator()
    manager = ReadinessManager(
        clock=lambda: clock[0],
        rag_warmup=lambda: None,
    )
    manager._orchestrator = orchestrator  # type: ignore[assignment]
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(manager.collect) for _ in range(4)]
            assert orchestrator.started.wait(timeout=1)
            orchestrator.release.set()
            results = [future.result(timeout=2) for future in futures]

        assert orchestrator.calls == 1
        assert all(result == results[0] for result in results)
        assert manager.collect() == results[0]
        assert orchestrator.calls == 1

        clock[0] += readiness.READINESS_CACHE_TTL_SECONDS
        assert manager.collect() == results[0]
        assert orchestrator.calls == 2
    finally:
        manager.close()


def test_kafka_readiness_uses_sampler_snapshot_without_admin_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ReadinessManager(
        clock=lambda: 10.0,
        rag_warmup=lambda: None,
    )
    sequence = manager._tracker.begin_measurement()
    assert manager._tracker.record(sequence, lag=0)
    manager._sampler_future = SimpleNamespace(done=lambda: False)  # type: ignore[assignment]

    def forbidden() -> None:
        raise AssertionError("request path must not perform a Kafka admin probe")

    monkeypatch.setattr(manager, "_measure_kafka", forbidden)
    try:
        assert manager._kafka_readiness() is None
    finally:
        manager._sampler_future = None
        manager.close()


def test_postgresql_check_budget_fits_global_readiness_deadline() -> None:
    assert POSTGRES_LOCK_TIMEOUT_SECONDS < POSTGRES_STATEMENT_TIMEOUT_SECONDS
    assert (
        DB_CONNECT_TIMEOUT_SECONDS + POSTGRES_STATEMENT_TIMEOUT_SECONDS
        < READINESS_DEADLINE_SECONDS
    )


def test_rag_warmup_is_single_flight_and_retries_transient_failure() -> None:
    calls = 0

    def warmup() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")

    with ThreadPoolExecutor(max_workers=1) as executor:
        controller = RagWarmupController(
            executor,
            warmup,
            cooldown_seconds=0,
        )
        controller.start()
        while controller.reason() == "RAG_MODEL_NOT_READY" and calls < 2:
            time.sleep(0.005)
        deadline = time.monotonic() + 1
        while controller.reason() is not None and time.monotonic() < deadline:
            time.sleep(0.005)

    assert calls == 2
    assert controller.reason() is None


def test_kafka_lag_tracker_rejects_late_observation_and_tracks_stale_lag() -> None:
    tracker = KafkaLagTracker(clock=lambda: 0.0)
    old = tracker.begin_measurement()
    newest = tracker.begin_measurement()

    assert tracker.record(newest, lag=0, sampled_at=10.0)
    assert not tracker.record(old, lag=8, sampled_at=5.0)
    assert tracker.reason(worker_alive=True, now=10.0) is None

    positive = tracker.begin_measurement()
    assert tracker.record(positive, lag=2, sampled_at=20.0)
    for sampled_at in (100.0, 200.0, 300.0):
        sequence = tracker.begin_measurement()
        assert tracker.record(sequence, lag=2, sampled_at=sampled_at)
    assert tracker.reason(worker_alive=True, now=319.9) is None
    assert tracker.reason(worker_alive=True, now=320.0) == "KAFKA_LAG_STALE"
    recovered = tracker.begin_measurement()
    assert tracker.record(recovered, lag=0, sampled_at=321.0)
    assert tracker.reason(worker_alive=True, now=321.0) is None


def test_packaged_readiness_markers_are_exact_and_source_synchronized() -> None:
    bundle = MarkerBundle()

    bundle.validate_source_sync()
    bundle.validate_postgresql_chain()
    bundle.validate_reference_chain()

    assert tuple(sorted(path.name for path in bundle.root.iterdir())) == tuple(
        sorted(READINESS_MARKER_FILENAMES)
    )
    dockerfile = (REPOSITORY_ROOT / "backend" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "COPY backend/app ./app" in dockerfile


def test_n8n_readiness_fails_closed_before_network_for_invalid_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("N8N_BASE_URL", "http://user:secret@n8n.local:5678/path")
    called = False

    def forbidden() -> object:
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr(readiness.urllib.request, "build_opener", forbidden)

    with pytest.raises(readiness.ReadinessContractError, match="origin"):
        readiness._n8n_readiness()
    assert not called


def test_kafka_config_is_file_only_and_secret_safe(tmp_path: Path) -> None:
    user = tmp_path / "user"
    password = tmp_path / "password"
    user.write_text("kosa_fdc_client\n", encoding="utf-8")
    password.write_text("safe-password-value\n", encoding="utf-8")
    values = {
        "KAFKA_BOOTSTRAP_INTERNAL": "kafka:9092",
        "KAFKA_CLIENT_USER_FILE": str(user),
        "KAFKA_CLIENT_PASSWORD_FILE": str(password),
    }

    config = KafkaClientConfig.from_mapping(values)

    assert config.username == "kosa_fdc_client"
    assert config.password == "safe-password-value"
    assert "safe-password-value" not in repr(config)
    with pytest.raises(KafkaConfigError, match="평문"):
        KafkaClientConfig.from_mapping({**values, "KAFKA_CLIENT_PASSWORD": "forbidden"})


@pytest.mark.parametrize(
    ("values", "expected_reason"),
    [
        ({}, "NOT_CONFIGURED"),
        ({"KAFKA_BOOTSTRAP_INTERNAL": "invalid bootstrap"}, "CONTRACT_MISMATCH"),
    ],
)
def test_kafka_config_reason_distinguishes_missing_from_malformed(
    values: dict[str, str],
    expected_reason: str,
) -> None:
    with pytest.raises(KafkaConfigError) as caught:
        KafkaClientConfig.from_mapping(values)

    assert readiness._reason_for_exception(caught.value) == expected_reason


class _Resolved:
    def __init__(self, value: object) -> None:
        self._value = value

    def result(self, timeout: float) -> object:
        assert timeout == 5.0
        return self._value


class _FakeKafkaAdmin:
    def __init__(self) -> None:
        self.list_offsets_calls = 0
        self.mutations = 0

    def list_topics(self, *, timeout: float) -> object:
        assert timeout == 5.0
        partition = SimpleNamespace(error=None)
        return SimpleNamespace(
            topics={
                "fdc.actions": SimpleNamespace(partitions={0: partition}),
                "fdc.actions.result": SimpleNamespace(partitions={0: partition}),
            }
        )

    def list_consumer_group_offsets(self, request: list[object]) -> dict[str, object]:
        partitions = request[0].topic_partitions
        committed = [
            SimpleNamespace(topic=item.topic, partition=item.partition, offset=4)
            for item in partitions
        ]
        return {
            "kosa-fdc-wf4-writeback": _Resolved(
                SimpleNamespace(topic_partitions=committed)
            )
        }

    def list_offsets(self, request: dict[object, object]) -> dict[object, object]:
        self.list_offsets_calls += 1
        offset = 0 if self.list_offsets_calls == 1 else 10
        return {
            partition: _Resolved(SimpleNamespace(offset=offset))
            for partition in request
        }


def test_kafka_probe_uses_two_admin_offset_planes_without_mutation() -> None:
    admin = _FakeKafkaAdmin()

    measurement = KafkaAdminProbe(admin).measure()

    assert measurement.partition_count == 2
    assert measurement.lag == 12
    assert admin.list_offsets_calls == 2
    assert admin.mutations == 0
