"""Stage2 round collection after the single authorized pending-batch execution.

Concrete ports read immutable container IDs and independent n8n details. The
caller owns the lifecycle lock and cleanup. This code never sends or retries an
action; convergence polling only reads current external-effect observations.
"""

import time
from datetime import UTC, datetime

from app.agent.release_artifacts import EvidenceError, component_ref, write_private
from app.agent.release_database import bind_email_targets, parse_mock_database_transport
from app.agent.release_delivery import DeliveryReceiptsV2, EmailCallback
from app.agent.release_mock import (
    SOURCES_NAME,
    MockSources,
    MockTarget,
    emit_mock_results,
    instant,
)
from app.agent.release_mock_capture import collect_mes_executions, workflow_pin
from app.agent.release_n8n import collect_acceptances
from app.agent.release_prepared import config_digest, parse_prepared
from app.agent.release_round import (
    RoundEvidenceV2,
    budget_policy_sha256,
    build_round,
    fixture_sha256,
    verify_round,
)
from app.agent.release_run_capture import CapturedRunsV2


def collect_round(
    *,
    root,
    attempt_root,
    prepared,
    resume_at,
    kafka_before,
    read_database,
    read_runs,
    read_offsets,
    read_records,
    read_trail,
    api,
    workflows,
    read_smtp_config,
    capture_mock_snapshot,
    monotonic=time.monotonic,
    sleep=time.sleep,
    clock=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"),
):
    prepared = parse_prepared(prepared.model_dump())
    if prepared.schema_version != "level3-prepared-attempt-v2":
        raise EvidenceError("CAPTURE_POLICY_INVALID")
    if set(workflows) != {"WF2", "WF3", "WF4"}:
        raise EvidenceError("MOCK_WORKFLOW_INVALID")
    # Workflows must remain explicitly retained throughout the observation.
    pins = {w: workflow_pin(api, workflow=w, **workflows[w]) for w in ("WF3", "WF4")}
    deadline = monotonic() + 180
    while True:
        database = parse_mock_database_transport(read_database())
        if database.identity != prepared.db_identity:
            raise EvidenceError("CAPTURE_DB_IDENTITY_MISMATCH")
        if any(
            d.status in {"FAILED", "UNKNOWN", "BLOCKED", "CANCELED"}
            for d in database.deliveries
        ) or any(r.status == "FAILED" for r in database.runs):
            raise EvidenceError("MOCK_CONVERGENCE_FAILED")
        if (
            len(database.runs) == 12
            and all(r.status == "COMPLETED" for r in database.runs)
            and len(database.deliveries) == 10
            and all(d.status == "SENT" for d in database.deliveries)
        ):
            break
        if monotonic() >= deadline:
            write_private(attempt_root, "partial-mock-database.json", database)
            raise EvidenceError("MOCK_CONVERGENCE_TIMEOUT")
        sleep(min(2, max(0, deadline - monotonic())))
    capture_mock_snapshot()
    captured = CapturedRunsV2.model_validate(read_runs())
    run_actions = {r.run_id: r.action_id for r in captured.runs}
    targets = bind_email_targets(
        database, expected_run_actions=run_actions, resume_at=resume_at
    )
    after = read_offsets()
    frozen_at = clock()
    records = read_records(kafka_before, after)
    trail = read_trail(resume_at, frozen_at)
    write_private(attempt_root, "partial-mock-database.json", database)
    write_private(attempt_root, "partial-mock-kafka.json", records)
    write_private(attempt_root, "partial-mock-callbacks.json", trail)
    config = read_smtp_config()
    config_sha = config_digest(config)
    if config_sha not in prepared.approved_config_digest_allowlist:
        raise EvidenceError("CAPTURE_SMTP_CONFIG_MISMATCH")
    if any(
        config["n8n_workflow_versions"].get(pin["workflow_id"]) != pin["version"]
        for pin in workflows.values()
    ):
        raise EvidenceError("CAPTURE_WORKFLOW_PIN_MISSING")
    deadline = monotonic() + 300
    while True:
        try:
            executions = collect_mes_executions(
                api,
                workflows={w: workflows[w] for w in ("WF3", "WF4")},
                started_at=resume_at,
                observed_at=clock(),
            )
            emails = collect_acceptances(
                api,
                workflow_id=workflows["WF2"]["workflow_id"],
                workflow_version=workflows["WF2"]["version"],
                targets=targets,
                resume_at=resume_at,
                clock=clock,
            )
            if len(executions) != 6:
                raise EvidenceError("MOCK_EXECUTION_POPULATION_INVALID")
            break
        except EvidenceError:
            if monotonic() >= deadline:
                raise EvidenceError("MOCK_EXECUTION_EVIDENCE_TIMEOUT") from None
            sleep(min(2, max(0, deadline - monotonic())))
    if config_digest(read_smtp_config()) != config_sha or any(
        workflow_pin(api, workflow=w, **workflows[w]) != pin for w, pin in pins.items()
    ):
        raise EvidenceError("CAPTURE_SMTP_CONFIG_DRIFT")
    if (
        parse_mock_database_transport(read_database()) != database
        or read_offsets() != after
        or read_trail(resume_at, clock()) != trail
        or CapturedRunsV2.model_validate(read_runs()) != captured
    ):
        raise EvidenceError("MOCK_CAPTURE_DRIFT")
    mock_targets = [
        MockTarget(
            action_id=r.action_id,
            request_hash=next(
                d.request_hash for d in r.deliveries if d.channel == "MES"
            ),
            created_run_id=r.run_id,
            incident_key={k: r.route["incident"][k] for k in ("lot_id", "chamber_id")},
            action_code="EQP_HOLD",
            action_policy_version="MOCK-NOTIFY-V1",
            link_type="CREATED",
        )
        for r in captured.runs
        if r.action_code == "EQP_HOLD"
    ]
    sources = MockSources(
        schema_version="level3-mock-sources-v1",
        attempt_id=prepared.attempt_id,
        window=dict(
            started_at=resume_at,
            frozen_at=frozen_at,
            actions_topic=dict(
                partition=0,
                offset_before=kafka_before["fdc.actions:0"],
                offset_after=after["fdc.actions:0"],
            ),
            result_topic=dict(
                partition=0,
                offset_before=kafka_before["fdc.actions.result:0"],
                offset_after=after["fdc.actions.result:0"],
            ),
        ),
        kafka_records=records,
        callback_trail=trail,
        n8n_executions=executions,
        db_mes_rows=[
            d.model_dump(mode="json")
            for d in database.deliveries
            if d.channel == "MES_MOCK"
        ],
    )
    source_ref = write_private(root, SOURCES_NAME, sources)
    result_ref = emit_mock_results(
        root=root,
        sources=source_ref,
        targets=mock_targets,
        expected_attempt_id=prepared.attempt_id,
    )
    holds = {r.action_id for r in mock_targets}
    receipts = DeliveryReceiptsV2(
        schema_version="level3-delivery-receipts-v2",
        attempt_id=prepared.attempt_id,
        capture_phase="POST_MOCK_CONVERGENCE",
        recipient_hash_version=2,
        smtp_config_digest=config_sha,
        executions=emails,
        hold_notify_execution_ids=sorted(
            r.n8n_execution_id for r in emails if r.action_id in holds
        ),
        callbacks=[
            EmailCallback(
                action_id=d.action_id,
                request_hash=d.request_hash,
                channel="EMAIL",
                status=d.status,
                provider_message_id=d.provider_message_id,
                completed_at=d.completed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                if d.completed_at
                else None,
            )
            for d in database.deliveries
            if d.channel == "EMAIL"
        ],
    )
    delivery = write_private(root, "delivery-receipts.round1.json", receipts)
    model = captured.model
    evidence = RoundEvidenceV2(
        schema_version="level3-round1-v2",
        capture_phase="POST_MOCK_CONVERGENCE",
        action_policy_version="MOCK-NOTIFY-V1",
        approval_rows=len(database.approvals),
        R=prepared.R,
        reset_attempt_id=prepared.attempt_id,
        images=prepared.images,
        dataset_epoch="fdc_final_20260818",
        fixture_sha256=fixture_sha256(),
        budget_policy_sha256=budget_policy_sha256(),
        llm=model.llm,
        model_endpoint_sha256=model.endpoint_sha256,
        model_config_digest=model.model_config_digest,
        preflight_output_sha256=prepared.e2e_level3_preflight_output_sha256,
        prepared_attempt=component_ref(root, "prepared-attempt.json"),
        smtp_approval=component_ref(root, "smtp-approval-grant.json"),
        delivery_receipts=delivery,
        captured_at=instant(clock()).strftime("%Y-%m-%dT%H:%M:%SZ"),
        kafka_before=kafka_before,
        kafka_after=after,
        runs=captured.runs,
        mock_sources=source_ref,
        mock_results=result_ref,
    )
    reference = write_private(root, "round1.json", build_round(evidence))
    verify_round(root, reference)
    return reference
