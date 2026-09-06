"""Bounded, read-only projections for the four independent MES Mock sources.

No Kafka group commits, workflow execution, callback replay or DB writes. Raw
payloads stay in memory. Complete offset windows are captured, never filtered to
the desired three actions. The offline join determines success.
"""

import os
import stat
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.agent.release_artifacts import EvidenceError, canonical_json, parse_json
from app.agent.release_mock import (
    CallbackProjection,
    KafkaProjection,
    N8nProjection,
    TopicWindow,
    instant,
)
from app.agent.release_n8n import _id, _inventory, _node, _timestamp
from app.agent.release_prepared import N8nEvidenceProbeV2
from app.common.mes_identity import event_id_for


def _require(ok, code):
    if not ok:
        raise EvidenceError(code)


def workflow_pin(api, *, workflow, workflow_id, version):
    _require(workflow in {"WF3", "WF4"}, "MOCK_WORKFLOW_INVALID")
    raw = api.workflow(_id(workflow_id))
    _require(
        raw.get("id") == workflow_id
        and raw.get("versionId") == version
        and raw.get("active") is True,
        "N8N_EVIDENCE_WORKFLOW_MISMATCH",
    )
    settings = raw.get("settings", {})
    _require(
        all(
            settings.get(k) == "all"
            for k in ("saveDataSuccessExecution", "saveDataErrorExecution")
        ),
        "N8N_EXECUTION_RETENTION_REQUIRED",
    )
    nodes = raw.get("nodes", [])
    required = {"WF3": "Validate MES Payload", "WF4": "Validate MES Result"}[workflow]
    _require(
        sum(
            n.get("name") == required and n.get("type") == "n8n-nodes-base.code"
            for n in nodes
        )
        == 1,
        "MOCK_WORKFLOW_INVALID",
    )
    return canonical_json(raw)


def project_mes_execution(
    raw, *, workflow, workflow_id, version, execution_id, observed_at
):
    try:
        _require(
            raw["id"] == execution_id
            and raw["workflowId"] == workflow_id
            and raw["workflowData"]["id"] == workflow_id
            and raw["workflowData"]["versionId"] == version
            and raw.get("retryOf") is None
            and raw.get("retrySuccessId") is None,
            "N8N_EVIDENCE_EXECUTION_MISMATCH",
        )
        _require(
            _timestamp(raw["startedAt"])
            <= _timestamp(raw["stoppedAt"])
            <= _timestamp(observed_at),
            "N8N_EVIDENCE_TIME_INVALID",
        )
        data = raw["data"]["resultData"]["runData"]
        name = "Validate MES Payload" if workflow == "WF3" else "Validate MES Result"
        envelope = _node(data, name)
        validity = "schema_ok" if workflow == "WF3" else "valid"
        _require(envelope.get(validity) is True, "MOCK_EXECUTION_PAYLOAD_INVALID")
        payload = envelope["payload"]
        return N8nProjection(
            workflow=workflow,
            execution_id=execution_id,
            action_id=payload["action_id"],
            status=raw["status"],
            started_at=raw["startedAt"],
        )
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("MOCK_EXECUTION_INVALID") from None


def collect_mes_executions(api, *, workflows, started_at, observed_at):
    """Two closed inventories; unexpected executions remain in the population."""
    _require(set(workflows) == {"WF3", "WF4"}, "MOCK_WORKFLOW_INVALID")
    result = []
    for workflow, pin in workflows.items():
        _require(set(pin) == {"workflow_id", "version"}, "MOCK_WORKFLOW_INVALID")
        before = workflow_pin(api, workflow=workflow, **pin)
        inventory = _inventory(api, pin["workflow_id"], started_at)

        def collect(inventory=inventory, workflow=workflow, pin=pin):
            rows = []
            for identifier, metadata in sorted(inventory.items()):
                raw = api.execution(identifier)
                _require(
                    all(raw.get(k) == v for k, v in metadata.items()),
                    "N8N_EVIDENCE_EXECUTION_DRIFT",
                )
                rows.append(
                    project_mes_execution(
                        raw,
                        workflow=workflow,
                        **pin,
                        execution_id=identifier,
                        observed_at=observed_at,
                    )
                )
            return rows

        rows = collect()
        _require(rows == collect(), "N8N_EVIDENCE_EXECUTION_DRIFT")
        _require(
            inventory == _inventory(api, pin["workflow_id"], started_at),
            "N8N_EVIDENCE_INVENTORY_DRIFT",
        )
        _require(
            before == workflow_pin(api, workflow=workflow, **pin),
            "N8N_EVIDENCE_WORKFLOW_DRIFT",
        )
        result.extend(rows)
    return result


def probe_mock_evidence(
    api, *, email_probe, workflows, samples, read_trail_probe, observed_at
):
    """Existing successful execution details only; no probe mail or MES request."""
    _require(set(workflows) == set(samples) == {"WF3", "WF4"}, "MOCK_WORKFLOW_INVALID")
    for workflow, pin in workflows.items():
        before = workflow_pin(api, workflow=workflow, **pin)
        sample = project_mes_execution(
            api.execution(_id(samples[workflow])),
            workflow=workflow,
            **pin,
            execution_id=samples[workflow],
            observed_at=observed_at,
        )
        _require(sample.status == "success", "N8N_EVIDENCE_PROBE_FAILED")
        _require(
            before == workflow_pin(api, workflow=workflow, **pin),
            "N8N_EVIDENCE_WORKFLOW_DRIFT",
        )
    _require(read_trail_probe() is True, "MOCK_CALLBACK_TRAIL_UNAVAILABLE")
    return N8nEvidenceProbeV2(
        **email_probe.model_dump(),
        wf3_execution_detail_retained=True,
        wf4_execution_detail_retained=True,
        callback_trail_writable=True,
    )


def read_callback_trail(path: Path, *, started_at, frozen_at):
    """Read an append-only application trail without truncation or silent drops."""
    fd = None
    try:
        _require(
            path.is_absolute() and path.resolve(strict=True) == path,
            "MOCK_CALLBACK_TRAIL_UNAVAILABLE",
        )
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(fd)
        _require(
            stat.S_ISREG(before.st_mode)
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_nlink == 1
            and before.st_size <= 512 * 1000,
            "MOCK_CALLBACK_TRAIL_UNAVAILABLE",
        )
        raw = b""
        while len(raw) <= 512 * 1000:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            raw += chunk
        after = os.fstat(fd)
        _require(
            (before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns),
            "MOCK_CALLBACK_TRAIL_DRIFT",
        )
        _require(not raw or raw.endswith(b"\n"), "MOCK_CALLBACK_TRAIL_PARTIAL")
        result = []
        for line in raw.splitlines():
            _require(0 < len(line) <= 512, "MOCK_CALLBACK_TRAIL_INVALID")
            row = CallbackProjection.model_validate(parse_json(line))
            _require(
                instant(started_at) <= instant(row.ts) <= instant(frozen_at),
                "MOCK_CALLBACK_WINDOW_MISMATCH",
            )
            result.append(row)
        return result
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("MOCK_CALLBACK_TRAIL_UNAVAILABLE") from None
    finally:
        if fd is not None:
            os.close(fd)


def project_kafka_record(*, topic, partition, offset, key, value, timestamp_ms):
    try:
        _require(
            type(key) is bytes and type(value) is bytes and len(value) <= 64 * 1024,
            "MOCK_KAFKA_RECORD_INVALID",
        )
        data = parse_json(value)
        action, request_hash = data["action_id"], data["request_hash"]
        event = event_id_for(action, request_hash)
        _require(
            topic in {"fdc.actions", "fdc.actions.result"} and key.decode() == action,
            "MOCK_KAFKA_RECORD_INVALID",
        )
        if topic == "fdc.actions":
            _require(data.get("event_id") == event, "MOCK_KAFKA_EVENT_MISMATCH")
        _require(
            type(timestamp_ms) is int and timestamp_ms >= 0, "MOCK_KAFKA_TIME_INVALID"
        )
        return KafkaProjection(
            topic=topic,
            partition=partition,
            offset=offset,
            key=key.decode(),
            action_id=action,
            request_hash=request_hash,
            event_id=event,
            status=None if topic == "fdc.actions" else data["status"],
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat(),
        )
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("MOCK_KAFKA_RECORD_INVALID") from None


def read_kafka_window(
    consumer, *, topic, window: TopicWindow, topic_partition, monotonic, timeout=30
):
    """Explicit assign/seek only; supplied consumer must have auto commit disabled.

    The adapter factory owns that configuration. Never subscribe/join a live
    consumer group. All offsets in [before, after) must appear exactly once.
    """
    _require(
        topic in {"fdc.actions", "fdc.actions.result"}
        and window.offset_after - window.offset_before <= 1000,
        "MOCK_KAFKA_WINDOW_INVALID",
    )
    partition = topic_partition(topic, window.partition, window.offset_before)
    consumer.assign([partition])
    deadline = monotonic() + timeout
    rows = []
    expected = window.offset_before
    while expected < window.offset_after:
        _require(monotonic() < deadline, "MOCK_KAFKA_WINDOW_INCOMPLETE")
        msg = consumer.poll(min(1, max(0, deadline - monotonic())))
        if msg is None:
            continue
        _require(
            not msg.error()
            and msg.topic() == topic
            and msg.partition() == window.partition
            and msg.offset() == expected,
            "MOCK_KAFKA_WINDOW_INCOMPLETE",
        )
        rows.append(
            project_kafka_record(
                topic=topic,
                partition=msg.partition(),
                offset=msg.offset(),
                key=msg.key(),
                value=msg.value(),
                timestamp_ms=msg.timestamp()[1],
            )
        )
        expected += 1
    return rows


class KafkaEvidenceReader:
    """Dedicated manual-assignment consumer; never writes group offsets."""

    def __init__(self, values=None, *, factory=None, topic_partition=None):
        from app.common.kafka_config import KafkaClientConfig

        config = KafkaClientConfig.from_mapping(
            os.environ if values is None else values
        )
        if factory is None:
            from confluent_kafka import Consumer, TopicPartition

            factory, topic_partition = Consumer, TopicPartition
        self.partition = topic_partition
        self.consumer = factory(
            {
                **config.common_settings(),
                "group.id": "cm52-evidence-" + uuid.uuid4().hex,
                "enable.auto.commit": False,
                "enable.auto.offset.store": False,
                "auto.offset.reset": "error",
                "enable.partition.eof": False,
                "log_level": 0,
            }
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.consumer.close()

    def offsets(self):
        try:
            result = {}
            for topic in ("fdc.actions", "fdc.actions.result"):
                metadata = self.consumer.list_topics(topic=topic, timeout=5)
                entry = metadata.topics[topic]
                _require(
                    not entry.error
                    and set(entry.partitions) == {0}
                    and not entry.partitions[0].error,
                    "MOCK_KAFKA_PARTITION_INVALID",
                )
                low, high = self.consumer.get_watermark_offsets(
                    self.partition(topic, 0), timeout=5, cached=False
                )
                _require(
                    type(low) is int and type(high) is int and 0 <= low <= high,
                    "MOCK_KAFKA_OFFSETS_INVALID",
                )
                result[f"{topic}:0"] = high
            return result
        except EvidenceError:
            raise
        except Exception:
            raise EvidenceError("MOCK_KAFKA_READ_FAILED") from None

    def records(self, before, after):
        _require(
            set(before) == set(after) == {"fdc.actions:0", "fdc.actions.result:0"},
            "MOCK_KAFKA_WINDOW_INVALID",
        )
        try:
            result = []
            for topic in ("fdc.actions", "fdc.actions.result"):
                result.extend(
                    read_kafka_window(
                        self.consumer,
                        topic=topic,
                        window=TopicWindow(
                            partition=0,
                            offset_before=before[f"{topic}:0"],
                            offset_after=after[f"{topic}:0"],
                        ),
                        topic_partition=self.partition,
                        monotonic=time.monotonic,
                    )
                )
            return result
        except EvidenceError:
            raise
        except Exception:
            raise EvidenceError("MOCK_KAFKA_READ_FAILED") from None
