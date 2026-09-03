"""Process liveness와 분리된 final dependency readiness 오케스트레이션."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from app.common.db import DB_CONNECT_TIMEOUT_SECONDS
from app.common.graph_readiness import GraphReadinessError, verify_graph_readiness
from app.common.kafka_config import KafkaContractError, KafkaNotConfiguredError
from app.common.kafka_readiness import (
    SAMPLING_PERIOD_SECONDS,
    KafkaAdminProbe,
    KafkaLagTracker,
    KafkaReadinessError,
)
from app.common.postgres_readiness import (
    PostgresReadinessError,
    verify_postgresql_runtime,
    verify_reference_migration,
)
from app.common.rag_readiness import (
    RagReadinessError,
    load_marker,
    verify_rag_readiness,
)
from app.common.readiness_markers import (
    DATASET_EPOCH,
    MarkerBundle,
    MarkerBundleError,
    MarkerBundleNotConfiguredError,
)
from app.common.schemas import (
    ReadinessCheck,
    ReadinessChecks,
    ReadinessFailureReason,
    ReadinessResponse,
)

logger = logging.getLogger(__name__)

CHECK_NAMES = (
    "postgresql_runtime",
    "reference_migration",
    "neo4j",
    "rag",
    "n8n",
    "kafka",
)
READINESS_WORKERS = len(CHECK_NAMES)
READINESS_DEADLINE_SECONDS = 10.0
DEPENDENCY_TIMEOUT_SECONDS = 5.0
RAG_RETRY_COOLDOWN_SECONDS = 60.0
READINESS_CACHE_TTL_SECONDS = 3.0
POSTGRES_STATEMENT_TIMEOUT_SECONDS = 3.0
POSTGRES_LOCK_TIMEOUT_SECONDS = 2.0

if (
    DB_CONNECT_TIMEOUT_SECONDS + POSTGRES_STATEMENT_TIMEOUT_SECONDS
    >= READINESS_DEADLINE_SECONDS
):
    raise RuntimeError(
        "PostgreSQL readiness budget은 global deadline보다 작아야 합니다"
    )


class ReadinessNotConfiguredError(RuntimeError):
    """의존성 설정 또는 필수 local artifact가 없다."""


class ReadinessContractError(RuntimeError):
    """의존성 설정·응답이 고정 계약과 다르다."""


@dataclass(frozen=True, slots=True)
class ReadinessCheckResult:
    status: Literal["PASS", "FAIL"]
    reason_code: ReadinessFailureReason | None
    latency_ms: int

    def to_schema(self) -> ReadinessCheck:
        return ReadinessCheck(
            status=self.status,
            reason_code=self.reason_code,
            latency_ms=self.latency_ms,
        )


CheckProvider = Callable[[], ReadinessFailureReason | None]


def _reason_for_exception(exc: BaseException) -> ReadinessFailureReason:
    if isinstance(
        exc,
        ReadinessNotConfiguredError
        | KafkaNotConfiguredError
        | MarkerBundleNotConfiguredError,
    ):
        return "NOT_CONFIGURED"
    if isinstance(
        exc,
        ReadinessContractError
        | KafkaContractError
        | MarkerBundleError
        | PostgresReadinessError
        | GraphReadinessError
        | RagReadinessError
        | KafkaReadinessError
        | json.JSONDecodeError,
    ):
        return "CONTRACT_MISMATCH"
    if isinstance(exc, TimeoutError | socket.timeout):
        return "TIMEOUT"
    return "DEPENDENCY_UNAVAILABLE"


def _run_provider(
    name: str,
    provider: CheckProvider,
    *,
    clock: Callable[[], float],
) -> ReadinessCheckResult:
    started = clock()
    try:
        reason = provider()
        if reason is None:
            return ReadinessCheckResult("PASS", None, _latency_ms(started, clock()))
        return ReadinessCheckResult(
            "FAIL",
            reason,
            _latency_ms(started, clock()),
        )
    except Exception as exc:
        reason = _reason_for_exception(exc)
        logger.warning("readiness check failed check=%s reason=%s", name, reason)
        return ReadinessCheckResult("FAIL", reason, _latency_ms(started, clock()))


def _latency_ms(started: float, finished: float) -> int:
    return max(0, int(round((finished - started) * 1000)))


class ReadinessOrchestrator:
    def __init__(
        self,
        providers: Mapping[str, CheckProvider],
        executor: Executor,
        *,
        deadline_seconds: float = READINESS_DEADLINE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if set(providers) != set(CHECK_NAMES):
            raise ValueError("readiness provider 집합이 exact 6종과 다릅니다")
        self._providers = dict(providers)
        self._executor = executor
        self._deadline_seconds = deadline_seconds
        self._clock = clock

    def collect(self) -> ReadinessResponse:
        started = self._clock()
        futures = {
            self._executor.submit(
                _run_provider,
                name,
                self._providers[name],
                clock=self._clock,
            ): name
            for name in CHECK_NAMES
        }
        done, pending = wait(futures, timeout=self._deadline_seconds)
        results: dict[str, ReadinessCheckResult] = {}
        for future in done:
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:
                results[name] = ReadinessCheckResult(
                    "FAIL",
                    "DEPENDENCY_UNAVAILABLE",
                    _latency_ms(started, self._clock()),
                )
        deadline_latency = _latency_ms(started, self._clock())
        for future in pending:
            future.cancel()
            results[futures[future]] = ReadinessCheckResult(
                "FAIL",
                "TIMEOUT",
                deadline_latency,
            )
        checks = ReadinessChecks(
            **{name: results[name].to_schema() for name in CHECK_NAMES}
        )
        status = (
            "READY"
            if all(result.status == "PASS" for result in results.values())
            else "NOT_READY"
        )
        return ReadinessResponse(
            status=status,
            dataset_epoch=DATASET_EPOCH,
            checks=checks,
        )


class RagWarmupController:
    """무거운 RAG 준비를 process-wide single-flight로 수행한다."""

    def __init__(
        self,
        executor: Executor,
        warmup: Callable[[], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        cooldown_seconds: float = RAG_RETRY_COOLDOWN_SECONDS,
    ) -> None:
        self._executor = executor
        self._warmup = warmup
        self._clock = clock
        self._cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._future: Future[None] | None = None
        self._last_failure_at: float | None = None
        self._last_reason: ReadinessFailureReason | None = None
        self._non_retryable = False

    def start(self) -> None:
        with self._lock:
            if self._future is None:
                self._future = self._executor.submit(self._warmup)

    def reason(self) -> ReadinessFailureReason | None:
        now = self._clock()
        with self._lock:
            future = self._future
            if future is None:
                self._future = self._executor.submit(self._warmup)
                return "RAG_MODEL_NOT_READY"
            if not future.done():
                return "RAG_MODEL_NOT_READY"
            try:
                future.result()
            except Exception as exc:
                reason = _reason_for_exception(exc)
                if self._last_failure_at is None:
                    self._last_failure_at = now
                    self._last_reason = reason
                    self._non_retryable = reason in {
                        "NOT_CONFIGURED",
                        "CONTRACT_MISMATCH",
                    }
                if (
                    not self._non_retryable
                    and now - self._last_failure_at >= self._cooldown_seconds
                ):
                    self._future = self._executor.submit(self._warmup)
                    self._last_failure_at = None
                    self._last_reason = None
                    return "RAG_MODEL_NOT_READY"
                return self._last_reason or reason
            return None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _n8n_readiness() -> None:
    raw = os.getenv("N8N_BASE_URL", "").strip()
    if not raw:
        raise ReadinessNotConfiguredError("N8N_BASE_URL이 없습니다")
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError as exc:
        raise ReadinessContractError("N8N_BASE_URL 형식이 잘못됐습니다") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ReadinessContractError(
            "N8N_BASE_URL은 credential 없는 origin이어야 합니다"
        )
    base = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    request = urllib.request.Request(
        f"{base}/healthz/readiness",
        method="GET",
        headers={"Accept": "application/json"},
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=DEPENDENCY_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ReadinessContractError("n8n readiness가 exact 200이 아닙니다")
            response.read(4097)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400 or 400 <= exc.code < 500:
            raise ReadinessContractError("n8n readiness HTTP 계약이 다릅니다") from exc
        raise RuntimeError("n8n readiness dependency 오류") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError | socket.timeout):
            raise TimeoutError from exc
        raise RuntimeError("n8n readiness 연결 오류") from exc


def _postgres_runtime_readiness(bundle: MarkerBundle) -> None:
    payloads = bundle.validate_postgresql_chain()
    from app.common.db import get_app_engine

    with get_app_engine().connect() as connection, connection.begin():
        connection.exec_driver_sql("SET LOCAL statement_timeout = '3000ms'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '2000ms'")
        from app.common import config as runtime_config
        from app.common.readiness_markers import expected_runtime_database

        verify_postgresql_runtime(
            connection,
            payloads["runtime.runtime_checkpointed.json"],
            expected_database=expected_runtime_database(
                getattr(runtime_config, "POSTGRES_DB", None)
            ),
        )


def _reference_migration_readiness(bundle: MarkerBundle) -> None:
    bundle.validate_reference_chain()
    from app.common.db import get_app_engine

    with get_app_engine().connect() as connection, connection.begin():
        connection.exec_driver_sql("SET LOCAL statement_timeout = '3000ms'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '2000ms'")
        verify_reference_migration(connection)


def _neo4j_readiness(bundle: MarkerBundle) -> None:
    marker = bundle.load("neo4j_graph.neo4j.json")
    from app.common.neo4j import get_neo4j_driver

    verify_graph_readiness(
        get_neo4j_driver(),
        marker,
        timeout_seconds=DEPENDENCY_TIMEOUT_SECONDS,
    )


def _rag_warmup() -> None:
    raw_path = os.getenv("EMBEDDING_MODEL_PATH", "backend/model-cache/bge-m3").strip()
    if not raw_path:
        raise ReadinessNotConfiguredError("EMBEDDING_MODEL_PATH가 없습니다")
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    if not path.is_dir():
        raise ReadinessNotConfiguredError("embedding model cache가 없습니다")

    from app.common.db import get_app_engine
    from app.knowledge.document_search import DocumentSearchRepository
    from app.knowledge.embedding import warm_embedding_model
    from app.knowledge.service import DocumentSearchService

    warm_embedding_model()
    engine = get_app_engine()
    with engine.connect() as connection:
        verify_rag_readiness(connection)
        database = str(
            connection.exec_driver_sql("SELECT current_database()").scalar_one()
        )
    marker = load_marker(database)
    smoke = marker["search_smoke"]
    if not isinstance(smoke, list) or len(smoke) != 3:
        raise ReadinessContractError("RAG smoke가 exact 3건이 아닙니다")
    service = DocumentSearchService(
        DocumentSearchRepository(engine, timeout_seconds=DEPENDENCY_TIMEOUT_SECONDS)
    )
    for case in smoke:
        if not isinstance(case, Mapping):
            raise ReadinessContractError("RAG smoke 형식이 잘못됐습니다")
        query = case.get("query")
        model_code = case.get("model_code")
        expected = case.get("expected_document_id")
        if not all(
            isinstance(value, str) and value for value in (query, model_code, expected)
        ):
            raise ReadinessContractError("RAG smoke 입력이 잘못됐습니다")
        hits = service.search(query, top_k=4, model_code=model_code)
        if expected not in {hit.document_id for hit in hits}:
            raise ReadinessContractError("RAG smoke 기대 문서를 찾지 못했습니다")


class ReadinessManager:
    """lifespan이 소유하는 readiness·warmup·sampler 실행 자원."""

    def __init__(
        self,
        *,
        readiness_executor: ThreadPoolExecutor | None = None,
        warmup_executor: ThreadPoolExecutor | None = None,
        sampler_executor: ThreadPoolExecutor | None = None,
        bundle: MarkerBundle | None = None,
        kafka_probe_factory: Callable[
            [], KafkaAdminProbe
        ] = KafkaAdminProbe.from_environment,
        clock: Callable[[], float] = time.monotonic,
        sampler_period_seconds: float = SAMPLING_PERIOD_SECONDS,
        cache_ttl_seconds: float = READINESS_CACHE_TTL_SECONDS,
        rag_warmup: Callable[[], None] = _rag_warmup,
    ) -> None:
        if not 0 < cache_ttl_seconds < sampler_period_seconds:
            raise ValueError(
                "readiness cache TTL은 0보다 크고 sampler 주기보다 작아야 합니다"
            )
        self._readiness_executor = readiness_executor or ThreadPoolExecutor(
            max_workers=READINESS_WORKERS,
            thread_name_prefix="readiness",
        )
        self._warmup_executor = warmup_executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="rag-warmup",
        )
        self._sampler_executor = sampler_executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="kafka-sampler",
        )
        self._bundle = bundle or MarkerBundle()
        self._kafka_probe_factory = kafka_probe_factory
        self._clock = clock
        self._sampler_period_seconds = sampler_period_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._collect_condition = threading.Condition()
        self._collecting = False
        self._cached_at: float | None = None
        self._cached_result: ReadinessResponse | None = None
        self._tracker = KafkaLagTracker(clock=clock)
        self._stop = threading.Event()
        self._sampler_future: Future[None] | None = None
        self._rag = RagWarmupController(
            self._warmup_executor,
            rag_warmup,
            clock=clock,
        )
        providers: dict[str, CheckProvider] = {
            "postgresql_runtime": lambda: _postgres_runtime_readiness(self._bundle),
            "reference_migration": lambda: _reference_migration_readiness(self._bundle),
            "neo4j": lambda: _neo4j_readiness(self._bundle),
            "rag": self._rag.reason,
            "n8n": _n8n_readiness,
            "kafka": self._kafka_readiness,
        }
        self._orchestrator = ReadinessOrchestrator(
            providers,
            self._readiness_executor,
            clock=clock,
        )

    def start(self) -> None:
        self._rag.start()
        if self._sampler_future is None:
            self._sampler_future = self._sampler_executor.submit(self._sample_loop)

    def collect(self) -> ReadinessResponse:
        with self._collect_condition:
            while True:
                now = self._clock()
                if (
                    self._cached_result is not None
                    and self._cached_at is not None
                    and now - self._cached_at < self._cache_ttl_seconds
                ):
                    return self._cached_result
                if not self._collecting:
                    self._collecting = True
                    break
                self._collect_condition.wait()

        try:
            result = self._orchestrator.collect()
        except BaseException:
            with self._collect_condition:
                self._collecting = False
                self._collect_condition.notify_all()
            raise

        with self._collect_condition:
            self._cached_at = self._clock()
            self._cached_result = result
            self._collecting = False
            self._collect_condition.notify_all()
        return result

    def _measure_kafka(self) -> None:
        sequence = self._tracker.begin_measurement()
        try:
            measurement = self._kafka_probe_factory().measure()
        except Exception as exc:
            self._tracker.record(
                sequence,
                lag=None,
                reason_code=_reason_for_exception(exc),
            )
            raise
        self._tracker.record(sequence, lag=measurement.lag)

    def _kafka_readiness(self) -> ReadinessFailureReason | None:
        return self._tracker.reason(worker_alive=self._sampler_alive())

    def _sampler_alive(self) -> bool:
        return self._sampler_future is not None and not self._sampler_future.done()

    def _sample_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._measure_kafka()
            except Exception as exc:
                reason = _reason_for_exception(exc)
                logger.warning("Kafka lag sampling failed reason=%s", reason)
            if self._stop.wait(self._sampler_period_seconds):
                return

    def close(self) -> None:
        self._stop.set()
        if self._sampler_future is not None:
            try:
                self._sampler_future.result(timeout=DEPENDENCY_TIMEOUT_SECONDS + 1)
            except Exception:
                pass
        self._sampler_executor.shutdown(wait=False, cancel_futures=True)
        self._warmup_executor.shutdown(wait=False, cancel_futures=True)
        self._readiness_executor.shutdown(wait=False, cancel_futures=True)


def create_readiness_manager() -> ReadinessManager:
    """설정·연결 검증을 app import가 아닌 lifespan 이후로 미룬다."""

    return ReadinessManager()
