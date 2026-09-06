"""Read-only synthetic source adapters; no shared Kafka/n8n/SMTP operations."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agent.release_artifacts import EvidenceError, canonical_json
from app.agent.release_mock import TopicWindow
from app.agent.release_mock_capture import (
    project_kafka_record,
    project_mes_execution,
    read_callback_trail,
    read_kafka_window,
    workflow_pin,
)
from app.common.mes_identity import event_id_for
from tests.unit.test_agent_release_n8n import node

AT = "2026-09-05T01:00:00Z"
KEY = "a" * 64


@pytest.mark.parametrize("workflow", ["WF3", "WF4"])
def test_projects_actual_distinct_workflow_node_contracts(workflow):
    field = "schema_ok" if workflow == "WF3" else "valid"
    name = "Validate MES Payload" if workflow == "WF3" else "Validate MES Result"
    raw = dict(
        id="1",
        workflowId="wf",
        workflowData=dict(id="wf", versionId="v"),
        startedAt=AT,
        stoppedAt=AT,
        status="success",
        data={
            "resultData": {
                "runData": {
                    name: node({field: True, "payload": {"action_id": "action"}})
                }
            }
        },
    )
    args = dict(
        workflow=workflow,
        workflow_id="wf",
        version="v",
        execution_id="1",
        observed_at=AT,
    )
    result = project_mes_execution(raw, **args)
    assert result.action_id == "action"
    assert set(result.model_dump()) == {
        "workflow",
        "execution_id",
        "action_id",
        "status",
        "started_at",
    }
    bad = deepcopy(raw)
    bad["retryOf"] = "previous"
    with pytest.raises(EvidenceError):
        project_mes_execution(bad, **args)
    bad = deepcopy(raw)
    bad["data"]["resultData"]["runData"][name] *= 2
    with pytest.raises(EvidenceError):
        project_mes_execution(bad, **args)


@pytest.mark.parametrize("workflow", ["WF3", "WF4"])
@pytest.mark.parametrize(
    "setting", ["saveDataSuccessExecution", "saveDataErrorExecution"]
)
@pytest.mark.parametrize("retained", ["none", None])
def test_retention_must_be_explicit_for_both_outcomes(workflow, setting, retained):
    raw = dict(
        id="wf",
        versionId="v",
        active=True,
        settings=dict(saveDataSuccessExecution="all", saveDataErrorExecution="all"),
    )
    raw["settings"][setting] = retained
    with pytest.raises(EvidenceError, match="N8N_EXECUTION_RETENTION_REQUIRED"):
        workflow_pin(
            SimpleNamespace(workflow=lambda _: raw),
            workflow=workflow,
            workflow_id="wf",
            version="v",
        )


@pytest.mark.parametrize("topic", ["fdc.actions", "fdc.actions.result"])
def test_result_derives_event_without_wire_event_id(topic):
    data = dict(action_id="a", request_hash=KEY)
    if topic == "fdc.actions":
        data["event_id"] = event_id_for("a", KEY)
    else:
        data.update(status="SENT", error_code=None)
    result = project_kafka_record(
        topic=topic,
        partition=0,
        offset=1,
        key=b"a",
        value=canonical_json(data),
        timestamp_ms=1788570000000,
    )
    assert result.event_id == event_id_for("a", KEY)
    with pytest.raises(EvidenceError):
        project_kafka_record(
            topic=topic,
            partition=0,
            offset=1,
            key=b"other",
            value=canonical_json(data),
            timestamp_ms=1788570000000,
        )


@pytest.mark.parametrize("change", ["partial", "extra", "symlink", "mode", "outside"])
def test_trail_never_silently_drops_invalid_records(tmp_path, change):
    row = dict(
        ts=AT,
        action_id="a",
        channel="MES_MOCK",
        status="SENT",
        duplicate=False,
        http_status=200,
    )
    if change == "extra":
        row["event_id"] = "invented"
    if change == "outside":
        row["ts"] = "2026-09-05T01:00:01Z"
    path = tmp_path / "trail.jsonl"
    path.write_bytes(canonical_json(row) + (b"" if change == "partial" else b"\n"))
    path.chmod(0o644 if change == "mode" else 0o600)
    if change == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    with pytest.raises(EvidenceError):
        read_callback_trail(path, started_at=AT, frozen_at=AT)


def test_trail_retains_email_and_duplicate_callback_rows(tmp_path):
    path = tmp_path / "trail.jsonl"
    rows = [
        dict(
            ts=AT,
            action_id="a",
            channel=channel,
            status="SENT",
            duplicate=duplicate,
            http_status=200,
        )
        for channel, duplicate in [
            ("EMAIL", False),
            ("MES_MOCK", False),
            ("MES_MOCK", True),
        ]
    ]
    path.write_bytes(b"".join(canonical_json(r) + b"\n" for r in rows))
    path.chmod(0o600)
    assert [
        r.model_dump() for r in read_callback_trail(path, started_at=AT, frozen_at=AT)
    ] == rows


def test_kafka_window_rejects_gap_without_group_commit():
    events = []
    consumer = SimpleNamespace(
        assign=lambda ps: events.append(ps),
        poll=lambda timeout: SimpleNamespace(
            error=lambda: None,
            topic=lambda: "fdc.actions",
            partition=lambda: 0,
            offset=lambda: 11,
        ),
    )
    with pytest.raises(EvidenceError, match="MOCK_KAFKA_WINDOW_INCOMPLETE"):
        read_kafka_window(
            consumer,
            topic="fdc.actions",
            window=TopicWindow(partition=0, offset_before=10, offset_after=13),
            topic_partition=lambda *args: args,
            monotonic=lambda: 0,
        )
    assert events == [[("fdc.actions", 0, 10)]]
