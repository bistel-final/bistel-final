"""V5-C-7.1 / C: offline MOCK-NOTIFY-V1 request/result/writeback recount.

The collector owns completeness/authenticity of the captured window. This module
never contacts Kafka, n8n or a database, sends an action, or authorizes deployment.
Targets must come from the independently verified round's CREATED HOLD actions.
Stored counts and join summaries are not accepted as source evidence.
"""

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, ValidationError, model_validator

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    Sha256,
    canonical_json,
    resolve_component,
    write_private,
)
from app.agent.release_delivery import Identifier
from app.agent.release_prepared import Attempt
from app.common.mes_identity import event_id_for

POLICY = "MOCK-NOTIFY-V1"
SOURCES_NAME = "mock-sources.round1.json"
RESULTS_NAME = "mock-results.round1.json"
Topic = Literal["fdc.actions", "fdc.actions.result"]
Status = Literal["WAITING", "SENDING", "SENT", "FAILED", "UNKNOWN"]


def instant(value: str) -> datetime:
    """Keep trail's fractional UTC timestamps; do not silently drop timezone."""
    try:
        parsed = datetime.fromisoformat(value)
        if "T" not in value or parsed.utcoffset() != timedelta(0):
            raise ValueError
        return parsed
    except ValueError:
        raise ValueError("MOCK_TIME_INVALID") from None


def _time(value: str) -> str:
    instant(value)
    return value


Timestamp = Annotated[str, Field(max_length=40), AfterValidator(_time)]


class IncidentKey(EvidenceModel):
    lot_id: Identifier
    chamber_id: Identifier


class MockTarget(EvidenceModel):
    action_id: Identifier
    request_hash: Sha256
    created_run_id: Identifier
    incident_key: IncidentKey
    action_code: Literal["EQP_HOLD"]
    action_policy_version: Literal["MOCK-NOTIFY-V1"]
    link_type: Literal["CREATED"]


class TopicWindow(EvidenceModel):
    partition: int = Field(ge=0)
    offset_before: int = Field(ge=0)
    offset_after: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.offset_after < self.offset_before:
            raise ValueError("MOCK_OFFSET_WINDOW_INVALID")
        return self


class MockWindow(EvidenceModel):
    started_at: Timestamp
    frozen_at: Timestamp
    actions_topic: TopicWindow
    result_topic: TopicWindow

    @model_validator(mode="after")
    def ordered(self):
        if instant(self.started_at) > instant(self.frozen_at):
            raise ValueError("MOCK_TIME_INVALID")
        return self


class KafkaProjection(EvidenceModel):
    topic: Topic
    partition: int = Field(ge=0)
    offset: int = Field(ge=0)
    key: Identifier
    action_id: Identifier
    request_hash: Sha256
    # Request carries event_id. Result wire format has only action_id/hash;
    # its collector derives this field with the same event_id_for contract.
    event_id: Annotated[str, Field(pattern=r"^MES:[0-9a-f]{64}$")]
    status: Literal["SENT", "FAILED"] | None
    timestamp: Timestamp

    @model_validator(mode="after")
    def shape(self):
        if (self.topic == "fdc.actions") != (self.status is None):
            raise ValueError("MOCK_RECORD_SHAPE_INVALID")
        return self


class CallbackProjection(EvidenceModel):
    # Exact backend trail fields. event_id is NOT present on this source.
    ts: Timestamp
    action_id: Identifier
    channel: Literal["EMAIL", "MES_MOCK"]
    status: Status | None
    duplicate: bool | None
    http_status: int = Field(ge=100, le=599)


class DbMesProjection(EvidenceModel):
    action_id: Identifier
    request_hash: Sha256
    channel: Literal["MES_MOCK"]
    status: Status
    provider_message_id: Identifier | None
    attempt_count: int = Field(ge=0)
    started_at: Timestamp | None
    completed_at: Timestamp | None


class N8nProjection(EvidenceModel):
    workflow: Literal["WF3", "WF4"]
    execution_id: Identifier
    action_id: Identifier
    status: Literal["success", "error", "running", "waiting", "canceled", "new"]
    started_at: Timestamp


class MockSources(EvidenceModel):
    schema_version: Literal["level3-mock-sources-v1"]
    attempt_id: Attempt
    window: MockWindow
    kafka_records: list[KafkaProjection] = Field(max_length=1000)
    callback_trail: list[CallbackProjection] = Field(max_length=1000)
    db_mes_rows: list[DbMesProjection] = Field(max_length=100)
    n8n_executions: list[N8nProjection] = Field(max_length=1000)


class DbTerminal(EvidenceModel):
    status: Literal["SENT"]
    provider_message_id: Identifier
    started_at: Timestamp
    completed_at: Timestamp


class MockResult(EvidenceModel):
    action_id: Identifier
    request_hash: Sha256
    event_id: str
    created_run_id: Identifier
    incident_key: IncidentKey
    actions_offset: int
    result_offset: int
    wf3_execution_id: Identifier
    wf4_execution_id: Identifier
    db_terminal: DbTerminal
    callback_count: int = Field(ge=1)


class MockResults(EvidenceModel):
    schema_version: Literal["level3-mock-results-v1"]
    attempt_id: Attempt
    actions_topic: TopicWindow
    result_topic: TopicWindow
    results: list[MockResult] = Field(max_length=3)
    policy_external_mes: int = Field(ge=0)
    # Only source/index locators: no arbitrary payloads or secret-bearing text.
    unmatched_records: list[str]


class MockAssessment(EvidenceModel):
    integrity: Literal["PASS", "FAIL"]
    failed_checks: list[str]
    ignored_outside_window: int
    summary: MockResults


def assess_mock_sources(
    *, sources, targets, expected_attempt_id: str
) -> MockAssessment:
    """Recount a complete captured window. Negative observations remain inspectable."""
    try:
        sources = MockSources.model_validate(
            sources.model_dump() if isinstance(sources, MockSources) else sources
        )
        targets = [
            MockTarget.model_validate(
                t.model_dump() if isinstance(t, MockTarget) else t
            )
            for t in targets
        ]
    except ValidationError:
        raise EvidenceError("MOCK_SOURCES_SCHEMA_INVALID") from None
    if sources.attempt_id != expected_attempt_id:
        raise EvidenceError("MOCK_ATTEMPT_MISMATCH")
    if (
        len(targets) != 3
        or len({t.action_id for t in targets}) != 3
        or len({t.created_run_id for t in targets}) != 3
        or len({(t.incident_key.lot_id, t.incident_key.chamber_id) for t in targets})
        != 3
    ):
        raise EvidenceError("MOCK_TARGET_POPULATION_INVALID")
    expected = {t.action_id: t for t in targets}
    errors, unmatched, ignored = set(), [], 0
    records = {
        action: {"fdc.actions": [], "fdc.actions.result": []} for action in expected
    }
    callbacks = {action: [] for action in expected}
    db_rows = {action: [] for action in expected}
    executions = {action: {"WF3": [], "WF4": []} for action in expected}
    start, end = instant(sources.window.started_at), instant(sources.window.frozen_at)

    def in_time(value):
        return start <= instant(value) <= end

    seen_offsets, events, observed_counts = set(), {}, Counter()
    for index, row in enumerate(sources.kafka_records):
        window = (
            sources.window.actions_topic
            if row.topic == "fdc.actions"
            else sources.window.result_topic
        )
        locator = f"kafka_records/{index}"
        if row.partition != window.partition:
            unmatched.append(locator)
            errors.add("MOCK_PARTITION_MISMATCH")
            continue
        if not window.offset_before <= row.offset < window.offset_after:
            ignored += 1
            continue
        position = (row.topic, row.partition, row.offset)
        if position in seen_offsets:
            errors.add("MOCK_RECORD_DUPLICATE")
        seen_offsets.add(position)
        observed_counts[row.topic] += 1
        events.setdefault(row.action_id, set()).add(row.event_id)
        target = expected.get(row.action_id)
        if (
            target is None
            or row.request_hash != target.request_hash
            or row.event_id != event_id_for(row.action_id, row.request_hash)
            or row.key != row.action_id
        ):
            unmatched.append(locator)
            continue
        if not in_time(row.timestamp):
            errors.add("MOCK_RECORD_TIME_INVALID")
        records[row.action_id][row.topic].append(row)
    if any(len(values) != 1 for values in events.values()):
        errors.add("MOCK_EVENT_AMBIGUOUS")
    for topic, window in (
        ("fdc.actions", sources.window.actions_topic),
        ("fdc.actions.result", sources.window.result_topic),
    ):
        if window.offset_after - window.offset_before != 3:
            errors.add("MOCK_KAFKA_DELTA")
        positions = {offset for name, _, offset in seen_offsets if name == topic}
        # Cardinality + in-range uniqueness proves coverage without allocating
        # a possibly attacker-sized range from supplied offsets.
        if len(positions) != window.offset_after - window.offset_before:
            errors.add("MOCK_WINDOW_INCOMPLETE")
        if observed_counts[topic] != 3:
            errors.add("MOCK_KAFKA_DELTA")

    for index, row in enumerate(sources.callback_trail):
        if row.channel != "MES_MOCK":
            continue
        if not in_time(row.ts):
            ignored += 1
            continue
        if row.action_id not in expected:
            unmatched.append(f"callback_trail/{index}")
            continue
        callbacks[row.action_id].append(row)
    for index, row in enumerate(sources.db_mes_rows):
        if row.action_id not in expected:
            unmatched.append(f"db_mes_rows/{index}")
        else:
            db_rows[row.action_id].append(row)
    execution_ids = set()
    for index, row in enumerate(sources.n8n_executions):
        if not in_time(row.started_at):
            ignored += 1
            continue
        if row.execution_id in execution_ids:
            errors.add("MOCK_EXECUTION_DUPLICATE")
        execution_ids.add(row.execution_id)
        if row.action_id not in expected:
            unmatched.append(f"n8n_executions/{index}")
        else:
            executions[row.action_id][row.workflow].append(row)

    results = []
    for action_id, target in sorted(expected.items()):
        request, result = (
            records[action_id]["fdc.actions"],
            records[action_id]["fdc.actions.result"],
        )
        db, trail = db_rows[action_id], callbacks[action_id]
        wf3, wf4 = executions[action_id]["WF3"], executions[action_id]["WF4"]
        if any(len(rows) != 1 for rows in (request, result, db, wf3, wf4)):
            errors.add("MOCK_JOIN_COMPLETE")
            continue
        db, request, result, wf3, wf4 = db[0], request[0], result[0], wf3[0], wf4[0]
        if db.status in ("FAILED", "UNKNOWN"):
            errors.add(f"MOCK_RESULT_{db.status}")
        if result.status == "FAILED":
            errors.add("MOCK_RESULT_FAILED")
        if (
            db.status != "SENT"
            or result.status != "SENT"
            or db.request_hash != target.request_hash
            or not db.provider_message_id
            or db.provider_message_id
            not in {
                f"kafka:{result.topic}:{result.partition}:{result.offset}",
                f"kafka:{result.topic}:{action_id}:{target.request_hash}",
            }
            or db.started_at is None
            or db.completed_at is None
            or db.attempt_count != 1
            or wf3.status != "success"
            or wf4.status != "success"
        ):
            errors.add("MOCK_WRITEBACK_INCOMPLETE")
            continue
        if (
            not in_time(db.started_at)
            or not in_time(db.completed_at)
            or instant(db.started_at) > instant(db.completed_at)
            or instant(request.timestamp) > instant(result.timestamp)
        ):
            errors.add("MOCK_RESULT_TIME_INVALID")
        if (
            not trail
            or any(
                r.status != "SENT"
                or r.duplicate is None
                or not 200 <= r.http_status < 300
                for r in trail
            )
            or sum(r.duplicate is False for r in trail) != 1
        ):
            errors.add("MOCK_CALLBACK_INCOMPLETE")
            continue
        results.append(
            MockResult(
                action_id=action_id,
                request_hash=target.request_hash,
                event_id=event_id_for(action_id, target.request_hash),
                created_run_id=target.created_run_id,
                incident_key=target.incident_key,
                actions_offset=request.offset,
                result_offset=result.offset,
                wf3_execution_id=wf3.execution_id,
                wf4_execution_id=wf4.execution_id,
                db_terminal=DbTerminal(
                    status="SENT",
                    provider_message_id=db.provider_message_id,
                    started_at=db.started_at,
                    completed_at=db.completed_at,
                ),
                callback_count=len(trail),
            )
        )
    if unmatched:
        errors.add("POLICY_EXTERNAL_MES")
    if len(results) != 3:
        errors.add("MOCK_JOIN_COMPLETE")
    return MockAssessment(
        integrity="FAIL" if errors else "PASS",
        failed_checks=sorted(errors),
        ignored_outside_window=ignored,
        summary=MockResults(
            schema_version="level3-mock-results-v1",
            attempt_id=expected_attempt_id,
            actions_topic=sources.window.actions_topic,
            result_topic=sources.window.result_topic,
            results=results,
            policy_external_mes=len(unmatched),
            unmatched_records=unmatched,
        ),
    )


def verify_mock_results(
    *,
    root: Path,
    sources: Component,
    results: Component,
    targets,
    expected_attempt_id: str,
) -> MockAssessment:
    """Resolve both SHA pins, independently recount, compare, then reread bytes."""
    if sources.relative_path != SOURCES_NAME or results.relative_path != RESULTS_NAME:
        raise EvidenceError("MOCK_COMPONENT_INVALID")
    original = resolve_component(root, sources)
    declared = resolve_component(root, results)
    assessed = assess_mock_sources(
        sources=original, targets=targets, expected_attempt_id=expected_attempt_id
    )
    if assessed.integrity != "PASS" or canonical_json(declared) != canonical_json(
        assessed.summary
    ):
        raise EvidenceError("MOCK_SOURCES_MISMATCH")
    for reference in (sources, results):
        resolve_component(root, reference)
    return assessed


def emit_mock_results(
    *, root: Path, sources: Component, targets, expected_attempt_id: str
) -> Component:
    """Emit only a fully joined result. Sources are already frozen by collector."""
    if sources.relative_path != SOURCES_NAME:
        raise EvidenceError("MOCK_COMPONENT_INVALID")
    assessed = assess_mock_sources(
        sources=resolve_component(root, sources),
        targets=targets,
        expected_attempt_id=expected_attempt_id,
    )
    if assessed.integrity != "PASS":
        raise EvidenceError("MOCK_SOURCES_MISMATCH")
    resolve_component(root, sources)
    reference = write_private(root, RESULTS_NAME, assessed.summary)
    verify_mock_results(
        root=root,
        sources=sources,
        results=reference,
        targets=targets,
        expected_attempt_id=expected_attempt_id,
    )
    return reference
