"""Synthetic evidence only: no live DB, workflow, Kafka or email operations."""

from copy import deepcopy
from datetime import datetime

import pytest

from app.agent import release_mock as subject
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    component_ref,
    digest,
    write_private,
)
from app.common.mes_identity import event_id_for

ATTEMPT = "20260906T000000Z-012345abcdef"
START = "2026-09-06T00:00:00Z"
AT = "2026-09-06T00:00:01.123456+00:00"
END = "2026-09-06T00:00:03Z"


@pytest.fixture
def evidence():
    targets = [
        dict(
            action_id=f"action-{i}",
            request_hash=digest(f"request-{i}".encode()),
            created_run_id=f"run-{i}",
            incident_key=dict(lot_id=f"LOT{i}", chamber_id=f"CH{i}"),
            action_code="EQP_HOLD",
            action_policy_version="MOCK-NOTIFY-V1",
            link_type="CREATED",
        )
        for i in range(3)
    ]
    sources = dict(
        schema_version="level3-mock-sources-v1",
        attempt_id=ATTEMPT,
        window=dict(
            started_at=START,
            frozen_at=END,
            actions_topic=dict(partition=0, offset_before=10, offset_after=13),
            result_topic=dict(partition=0, offset_before=20, offset_after=23),
        ),
        kafka_records=[],
        callback_trail=[],
        db_mes_rows=[],
        n8n_executions=[],
    )
    for i, target in enumerate(targets):
        action, request_hash = target["action_id"], target["request_hash"]
        for topic, offset, status in (
            ("fdc.actions", 10 + i, None),
            ("fdc.actions.result", 20 + i, "SENT"),
        ):
            sources["kafka_records"].append(
                dict(
                    topic=topic,
                    partition=0,
                    offset=offset,
                    key=action,
                    action_id=action,
                    request_hash=request_hash,
                    event_id=event_id_for(action, request_hash),
                    status=status,
                    timestamp=AT,
                )
            )
        sources["callback_trail"].append(
            dict(
                ts=AT,
                action_id=action,
                channel="MES_MOCK",
                status="SENT",
                duplicate=False,
                http_status=200,
            )
        )
        sources["db_mes_rows"].append(
            dict(
                action_id=action,
                request_hash=request_hash,
                channel="MES_MOCK",
                status="SENT",
                provider_message_id=f"kafka:fdc.actions.result:0:{20+i}",
                attempt_count=1,
                started_at=START,
                completed_at=AT,
            )
        )
        for workflow in ("WF3", "WF4"):
            sources["n8n_executions"].append(
                dict(
                    workflow=workflow,
                    execution_id=f"{workflow}-{i}",
                    action_id=action,
                    status="success",
                    started_at=AT,
                )
            )
    return dict(sources=sources, targets=targets, expected_attempt_id=ATTEMPT)


def test_normal_recounts_three_independent_joins(evidence):
    result = subject.assess_mock_sources(**evidence)
    assert result.integrity == "PASS"
    assert result.failed_checks == []
    assert result.summary.policy_external_mes == 0
    assert len(result.summary.results) == 3
    assert result.summary.results[0].actions_offset == 10
    assert result.summary.results[0].result_offset == 20
    assert result.summary.results[0].callback_count == 1
    assert result.summary.results[0].created_run_id == "run-0"
    assert datetime.fromisoformat(AT).microsecond == 123456


@pytest.mark.parametrize(
    "collection,index,field,value,code",
    [
        ("kafka_records", 1, "action_id", "other", "POLICY_EXTERNAL_MES"),
        ("kafka_records", 1, "event_id", "MES:" + "f" * 64, "POLICY_EXTERNAL_MES"),
        ("kafka_records", 1, "key", "other", "POLICY_EXTERNAL_MES"),
        ("kafka_records", 1, "request_hash", "f" * 64, "POLICY_EXTERNAL_MES"),
        ("kafka_records", 1, "partition", 1, "MOCK_PARTITION_MISMATCH"),
        ("kafka_records", 1, "status", "FAILED", "MOCK_RESULT_FAILED"),
        (
            "kafka_records",
            1,
            "timestamp",
            "2026-09-06T00:00:10Z",
            "MOCK_RECORD_TIME_INVALID",
        ),
        ("db_mes_rows", 0, "status", "UNKNOWN", "MOCK_RESULT_UNKNOWN"),
        ("db_mes_rows", 0, "status", "FAILED", "MOCK_RESULT_FAILED"),
        ("db_mes_rows", 0, "status", "SENDING", "MOCK_WRITEBACK_INCOMPLETE"),
        ("db_mes_rows", 0, "provider_message_id", "wrong", "MOCK_WRITEBACK_INCOMPLETE"),
        ("db_mes_rows", 0, "request_hash", "e" * 64, "MOCK_WRITEBACK_INCOMPLETE"),
        ("db_mes_rows", 0, "attempt_count", 2, "MOCK_WRITEBACK_INCOMPLETE"),
        ("db_mes_rows", 0, "completed_at", None, "MOCK_WRITEBACK_INCOMPLETE"),
        ("db_mes_rows", 0, "started_at", END, "MOCK_RESULT_TIME_INVALID"),
        ("callback_trail", 0, "action_id", "other", "POLICY_EXTERNAL_MES"),
        ("callback_trail", 0, "http_status", 500, "MOCK_CALLBACK_INCOMPLETE"),
        ("callback_trail", 0, "duplicate", None, "MOCK_CALLBACK_INCOMPLETE"),
        ("callback_trail", 0, "status", "FAILED", "MOCK_CALLBACK_INCOMPLETE"),
        ("n8n_executions", 0, "status", "error", "MOCK_WRITEBACK_INCOMPLETE"),
        ("n8n_executions", 0, "action_id", "other", "POLICY_EXTERNAL_MES"),
        ("n8n_executions", 0, "execution_id", "WF4-0", "MOCK_EXECUTION_DUPLICATE"),
    ],
)
def test_single_source_mutations_fail(evidence, collection, index, field, value, code):
    evidence["sources"][collection][index][field] = value
    result = subject.assess_mock_sources(**evidence)
    assert result.integrity == "FAIL"
    assert code in result.failed_checks


@pytest.mark.parametrize(
    "collection", ["kafka_records", "db_mes_rows", "callback_trail", "n8n_executions"]
)
def test_missing_independent_source_cannot_be_replaced_by_db_sent_count(
    evidence, collection
):
    evidence["sources"][collection].pop()
    assert subject.assess_mock_sources(**evidence).integrity == "FAIL"


@pytest.mark.parametrize(
    "collection", ["kafka_records", "db_mes_rows", "n8n_executions"]
)
def test_duplicate_source_row_fails(evidence, collection):
    evidence["sources"][collection].append(deepcopy(evidence["sources"][collection][0]))
    assert subject.assess_mock_sources(**evidence).integrity == "FAIL"


def test_duplicate_callback_is_counted_but_not_another_logical_terminal(evidence):
    row = deepcopy(evidence["sources"]["callback_trail"][0])
    row["duplicate"] = True
    evidence["sources"]["callback_trail"].append(row)
    result = subject.assess_mock_sources(**evidence)
    assert result.integrity == "PASS"
    assert result.summary.results[0].callback_count == 2
    row["duplicate"] = False
    assert (
        "MOCK_CALLBACK_INCOMPLETE"
        in subject.assess_mock_sources(**evidence).failed_checks
    )


def test_same_count_with_duplicate_offset_does_not_hide_a_missing_record(evidence):
    evidence["sources"]["kafka_records"][2]["offset"] = 10
    result = subject.assess_mock_sources(**evidence)
    assert {"MOCK_WINDOW_INCOMPLETE", "MOCK_RECORD_DUPLICATE"} <= set(
        result.failed_checks
    )


def test_two_events_for_one_action_rejected(evidence):
    row = evidence["sources"]["kafka_records"][1]
    row["request_hash"] = "a" * 64
    row["event_id"] = event_id_for(row["action_id"], row["request_hash"])
    assert (
        "MOCK_EVENT_AMBIGUOUS" in subject.assess_mock_sources(**evidence).failed_checks
    )


def test_window_bounds_and_post_freeze_callback_are_ignored_without_mutating_input(
    evidence,
):
    row = deepcopy(evidence["sources"]["kafka_records"][0])
    row["offset"] = 13  # next offset: excluded, not the last captured record
    evidence["sources"]["kafka_records"].append(row)
    row = deepcopy(row)
    row["offset"] = 9
    evidence["sources"]["kafka_records"].append(row)
    callback = deepcopy(evidence["sources"]["callback_trail"][0])
    callback.update(ts="2026-09-06T00:00:04Z", duplicate=True)
    evidence["sources"]["callback_trail"].append(callback)
    before = deepcopy(evidence)
    result = subject.assess_mock_sources(**evidence)
    assert result.integrity == "PASS"
    assert result.ignored_outside_window == 3
    assert result.summary.results[0].callback_count == 1
    assert evidence == before


def test_email_trail_not_counted_as_mes(evidence):
    callback = deepcopy(evidence["sources"]["callback_trail"][0])
    callback.update(channel="EMAIL", action_id="warning")
    evidence["sources"]["callback_trail"].append(callback)
    assert subject.assess_mock_sources(**evidence).integrity == "PASS"


@pytest.mark.parametrize(
    "field,value",
    [
        ("action_policy_version", "ACTION-POLICY-V1"),
        ("link_type", "REUSED"),
        ("action_code", "WARNING"),
    ],
)
def test_only_new_policy_created_hold_targets_accepted(evidence, field, value):
    evidence["targets"][0][field] = value
    with pytest.raises(EvidenceError, match="MOCK_SOURCES_SCHEMA_INVALID"):
        subject.assess_mock_sources(**evidence)


def test_attempt_mismatch(evidence):
    evidence["expected_attempt_id"] = "20260906T000000Z-aaaaaaaaaaaa"
    with pytest.raises(EvidenceError, match="MOCK_ATTEMPT_MISMATCH"):
        subject.assess_mock_sources(**evidence)


def test_target_population_duplicate(evidence):
    evidence["targets"][1] = deepcopy(evidence["targets"][0])
    with pytest.raises(EvidenceError, match="MOCK_TARGET_POPULATION_INVALID"):
        subject.assess_mock_sources(**evidence)


@pytest.mark.parametrize("change", ["extra", "bool", "time", "backward", "v1"])
def test_strict_schema_and_times(evidence, change):
    sources = evidence["sources"]
    if change == "extra":
        sources["policy_external_mes"] = 0  # caller's claimed verdict isn't evidence
    elif change == "bool":
        sources["window"]["actions_topic"]["partition"] = True
    elif change == "time":
        sources["callback_trail"][0]["ts"] = "2026-09-06T00:00:01"
    elif change == "backward":
        sources["window"]["actions_topic"]["offset_after"] = 9
    else:
        sources["schema_version"] = "level3-mock-sources-v0"
    with pytest.raises(EvidenceError, match="MOCK_SOURCES_SCHEMA_INVALID"):
        subject.assess_mock_sources(**evidence)


@pytest.fixture
def frozen(tmp_path, evidence):
    root = tmp_path / "robustness"
    root.mkdir(mode=0o700)
    source_ref = write_private(root, subject.SOURCES_NAME, evidence["sources"])
    args = dict(
        root=root,
        sources=source_ref,
        targets=evidence["targets"],
        expected_attempt_id=ATTEMPT,
    )
    return args


def test_no_clobber_emission_and_verification(frozen):
    result = subject.emit_mock_results(**frozen)
    path = frozen["root"] / subject.RESULTS_NAME
    before = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert subject.verify_mock_results(**frozen, results=result).integrity == "PASS"
    with pytest.raises((EvidenceError, FileExistsError)):
        subject.emit_mock_results(**frozen)
    assert path.read_bytes() == before


def test_forged_summary_rejected_even_with_fresh_sha(frozen):
    reference = subject.emit_mock_results(**frozen)
    from app.agent.release_artifacts import resolve_component

    value = resolve_component(frozen["root"], reference)
    value["results"][0]["callback_count"] = 900
    path = frozen["root"] / subject.RESULTS_NAME
    path.write_bytes(canonical_json(value))
    with pytest.raises(EvidenceError, match="MOCK_SOURCES_MISMATCH"):
        subject.verify_mock_results(
            **frozen, results=component_ref(frozen["root"], subject.RESULTS_NAME)
        )


def test_source_tamper_and_missing_file_rejected(frozen):
    reference = subject.emit_mock_results(**frozen)
    path = frozen["root"] / subject.SOURCES_NAME
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(EvidenceError):
        subject.verify_mock_results(**frozen, results=reference)
    path.unlink()
    with pytest.raises((EvidenceError, FileNotFoundError)):
        subject.verify_mock_results(**frozen, results=reference)


def test_failed_observation_never_emits_pass_summary(tmp_path, evidence):
    tmp_path.chmod(0o700)
    evidence["sources"]["db_mes_rows"][0]["status"] = "UNKNOWN"
    source = write_private(tmp_path, subject.SOURCES_NAME, evidence["sources"])
    with pytest.raises(EvidenceError, match="MOCK_SOURCES_MISMATCH"):
        subject.emit_mock_results(
            root=tmp_path,
            sources=source,
            targets=evidence["targets"],
            expected_attempt_id=ATTEMPT,
        )
    assert not (tmp_path / subject.RESULTS_NAME).exists()


def test_swapped_component_paths_rejected(frozen):
    reference = subject.emit_mock_results(**frozen)
    with pytest.raises(EvidenceError, match="MOCK_COMPONENT_INVALID"):
        subject.verify_mock_results(
            **{**frozen, "sources": reference}, results=reference
        )
