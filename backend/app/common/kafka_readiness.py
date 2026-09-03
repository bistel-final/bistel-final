"""Kafka Admin-plane lag 측정과 순서 안전한 readiness tracker."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.common.kafka_config import KafkaClientConfig

ACTIONS_TOPIC = "fdc.actions"
RESULT_TOPIC = "fdc.actions.result"
REQUIRED_TOPICS = (ACTIONS_TOPIC, RESULT_TOPIC)
WF4_CONSUMER_GROUP = "kosa-fdc-wf4-writeback"
SAMPLING_PERIOD_SECONDS = 60.0
STALE_AFTER_SECONDS = SAMPLING_PERIOD_SECONDS * 2
LAG_STALE_AFTER_SECONDS = 300.0
KAFKA_API_TIMEOUT_SECONDS = 5.0


class KafkaReadinessError(RuntimeError):
    """Kafka metadata·offset 계약을 read-only로 확인할 수 없다."""


@dataclass(frozen=True, slots=True)
class KafkaLagMeasurement:
    lag: int
    partition_count: int


@dataclass(frozen=True, slots=True)
class KafkaLagSnapshot:
    sequence: int
    sampled_at: float
    lag: int | None
    positive_since: float | None
    reason_code: str | None


class KafkaLagTracker:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._next_sequence = 0
        self._snapshot: KafkaLagSnapshot | None = None

    def begin_measurement(self) -> int:
        with self._lock:
            self._next_sequence += 1
            return self._next_sequence

    def record(
        self,
        sequence: int,
        *,
        lag: int | None,
        reason_code: str | None = None,
        sampled_at: float | None = None,
    ) -> bool:
        when = self._clock() if sampled_at is None else sampled_at
        if sequence <= 0 or when < 0:
            raise ValueError("Kafka lag observation 순서·시각이 올바르지 않습니다")
        if reason_code is None:
            if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
                raise ValueError("Kafka lag는 0 이상 정수여야 합니다")
        elif lag is not None:
            raise ValueError("Kafka 오류 observation에는 lag를 넣을 수 없습니다")

        with self._lock:
            current = self._snapshot
            if current is not None and sequence <= current.sequence:
                return False
            positive_since: float | None = None
            if reason_code is None and lag is not None and lag > 0:
                if current is not None and current.lag is not None and current.lag > 0:
                    positive_since = current.positive_since
                if positive_since is None:
                    positive_since = when
            self._snapshot = KafkaLagSnapshot(
                sequence=sequence,
                sampled_at=when,
                lag=lag,
                positive_since=positive_since,
                reason_code=reason_code,
            )
            return True

    def snapshot(self) -> KafkaLagSnapshot | None:
        with self._lock:
            return self._snapshot

    def reason(
        self,
        *,
        worker_alive: bool,
        now: float | None = None,
    ) -> str | None:
        when = self._clock() if now is None else now
        snapshot = self.snapshot()
        if not worker_alive or snapshot is None:
            return "DEPENDENCY_UNAVAILABLE"
        if when - snapshot.sampled_at > STALE_AFTER_SECONDS:
            return "DEPENDENCY_UNAVAILABLE"
        if snapshot.reason_code is not None:
            return snapshot.reason_code
        if (
            snapshot.lag is not None
            and snapshot.lag > 0
            and snapshot.positive_since is not None
            and when - snapshot.positive_since >= LAG_STALE_AFTER_SECONDS
        ):
            return "KAFKA_LAG_STALE"
        return None


def _future_result(value: Any, timeout: float) -> Any:
    return value.result(timeout=timeout) if hasattr(value, "result") else value


def _offset_map(
    results: Mapping[Any, Any], *, timeout: float
) -> dict[tuple[str, int], int]:
    offsets: dict[tuple[str, int], int] = {}
    for partition, future in results.items():
        resolved = _future_result(future, timeout)
        offset = getattr(resolved, "offset", None)
        topic = getattr(partition, "topic", None)
        partition_id = getattr(partition, "partition", None)
        if (
            not isinstance(topic, str)
            or isinstance(partition_id, bool)
            or not isinstance(partition_id, int)
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise KafkaReadinessError("Kafka offset 응답이 올바르지 않습니다")
        offsets[(topic, partition_id)] = offset
    return offsets


class KafkaAdminProbe:
    def __init__(
        self,
        admin: Any,
        *,
        timeout_seconds: float = KAFKA_API_TIMEOUT_SECONDS,
    ) -> None:
        self._admin = admin
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str] | None = None,
    ) -> KafkaAdminProbe:
        config = KafkaClientConfig.from_mapping(values or os.environ)
        from confluent_kafka.admin import AdminClient

        return cls(
            AdminClient(
                {
                    **config.common_settings(),
                    "socket.timeout.ms": int(KAFKA_API_TIMEOUT_SECONDS * 1000),
                    "request.timeout.ms": int(KAFKA_API_TIMEOUT_SECONDS * 1000),
                }
            )
        )

    def measure(self) -> KafkaLagMeasurement:
        from confluent_kafka import ConsumerGroupTopicPartitions, TopicPartition
        from confluent_kafka.admin import OffsetSpec

        metadata = self._admin.list_topics(timeout=self._timeout_seconds)
        # 두 topic의 metadata는 모두 확인하되, committed/earliest/latest는 WF4 group이
        # 실제로 소비하는 result topic partition만 본다. actions topic은 WF4가 구독하지
        # 않아 committed offset이 없거나 -1로 돌아오므로, 이를 요구하면 어느 환경에서도
        # PASS가 나오지 않는다(공용 PC 실측 CONTRACT_MISMATCH).
        topic_partitions: list[Any] = []
        for topic in REQUIRED_TOPICS:
            topic_metadata = getattr(metadata, "topics", {}).get(topic)
            partitions = getattr(topic_metadata, "partitions", None)
            if not isinstance(partitions, Mapping) or not partitions:
                raise KafkaReadinessError("필수 Kafka topic metadata가 없습니다")
            for partition_id, partition_metadata in sorted(partitions.items()):
                if getattr(partition_metadata, "error", None):
                    raise KafkaReadinessError("Kafka partition metadata 오류입니다")
                if topic == RESULT_TOPIC:
                    topic_partitions.append(TopicPartition(topic, int(partition_id)))
        if not topic_partitions:
            raise KafkaReadinessError("result topic partition이 없습니다")

        request = [
            ConsumerGroupTopicPartitions(
                WF4_CONSUMER_GROUP,
                [TopicPartition(tp.topic, tp.partition) for tp in topic_partitions],
            )
        ]
        group_futures = self._admin.list_consumer_group_offsets(request)
        group_future = group_futures.get(WF4_CONSUMER_GROUP)
        if group_future is None:
            raise KafkaReadinessError("WF4 consumer group을 조회할 수 없습니다")
        group_result = _future_result(group_future, self._timeout_seconds)
        committed_items = getattr(group_result, "topic_partitions", None)
        if not isinstance(committed_items, list):
            raise KafkaReadinessError("WF4 committed offset 응답이 올바르지 않습니다")
        committed = {
            (item.topic, item.partition): item.offset for item in committed_items
        }

        earliest_request = {
            TopicPartition(tp.topic, tp.partition): OffsetSpec.earliest()
            for tp in topic_partitions
        }
        latest_request = {
            TopicPartition(tp.topic, tp.partition): OffsetSpec.latest()
            for tp in topic_partitions
        }
        earliest = _offset_map(
            self._admin.list_offsets(earliest_request),
            timeout=self._timeout_seconds,
        )
        latest = _offset_map(
            self._admin.list_offsets(latest_request),
            timeout=self._timeout_seconds,
        )
        expected = {(tp.topic, tp.partition) for tp in topic_partitions}
        if (
            set(committed) != expected
            or set(earliest) != expected
            or set(latest) != expected
        ):
            raise KafkaReadinessError("Kafka offset partition 집합이 다릅니다")

        lag = 0
        for key in sorted(expected):
            low = earliest[key]
            value = committed[key]
            high = latest[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or not low <= value <= high
            ):
                raise KafkaReadinessError(
                    "Kafka committed offset 범위가 올바르지 않습니다"
                )
            lag += high - value
        return KafkaLagMeasurement(lag=lag, partition_count=len(expected))
