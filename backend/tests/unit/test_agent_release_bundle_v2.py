"""Full synthetic v2 transitive recount. No live source/authenticity claims."""

from copy import deepcopy

import pytest

from app.agent.golden_summary import MOCK_PHASES, validate_golden_summary
from app.agent.release_aggregate import assess_aggregate, emit_aggregate
from app.agent.release_artifacts import (
    component_ref,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_mock import MockTarget, emit_mock_results
from app.agent.release_round import RoundEvidenceV2, build_round
from app.agent.release_seal import seal_bundle, verify_seal
from tests.unit.test_agent_release import AT, ATTEMPT, REV
from tests.unit.test_agent_release_aggregate import bundle  # noqa: F401
from tests.unit.test_agent_release_evidence import delivery, replace  # noqa: F401
from tests.unit.test_agent_release_mock import evidence as mock_evidence  # noqa: F401
from tests.unit.test_agent_release_policy_v2 import prepared_v2
from tests.unit.test_agent_release_round import template  # noqa: F401


@pytest.fixture
def mock_bundle(bundle, mock_evidence):  # noqa: F811
    args = dict(bundle)
    published = args["published_root"]
    root = published / "robustness"
    args["root"].rename(root)
    args["root"] = root

    def read(name):
        return parse_json(read_private(root, name))

    from scripts.fault_evaluation_population import _load_oracle

    preflight = {
        k: [] for k in ("runs", "actions", "approvals", "deliveries", "tools", "audits")
    }
    preflight["r03_incidents"] = [
        dict(lot_id=lot, chamber_id=chamber)
        for lot, chamber in sorted(
            i.key for i in _load_oracle().incidents if "R03" in i.alarm_sources
        )
    ]
    for relative in ("evidence", "evidence/artifacts", "evidence/artifacts/PREFLIGHT"):
        (published / relative).mkdir(mode=0o700)
    preflight_ref = write_private(
        published, "evidence/artifacts/PREFLIGHT/db-snapshot.json", preflight
    )
    prepared_value = prepared_v2()
    prepared_value["preflight_snapshot_sha256"] = preflight_ref.sha256
    prepared = replace(root, "prepared-attempt.json", prepared_value)
    grant = read("smtp-approval-grant.json")
    grant.update(
        schema_version="smtp-send-grant-v2",
        action_policy_version="MOCK-NOTIFY-V1",
        prepared_attempt=prepared.model_dump(),
    )
    grant_ref = replace(root, "smtp-approval-grant.json", grant)
    receipt = read("delivery-receipts.round1.json")
    receipt.update(
        schema_version="level3-delivery-receipts-v2",
        capture_phase="POST_MOCK_CONVERGENCE",
    )
    receipt["hold_notify_execution_ids"] = receipt.pop("approval_execution_ids")
    for row in receipt["executions"]:
        row["email_kind"] = "ACTION_NOTIFY"
    receipt_ref = replace(root, "delivery-receipts.round1.json", receipt)
    evidence = read("round1.json")
    evidence.pop("batch_summary")
    evidence.update(
        schema_version="level3-round1-v2",
        capture_phase="POST_MOCK_CONVERGENCE",
        action_policy_version="MOCK-NOTIFY-V1",
        approval_rows=0,
        prepared_attempt=prepared.model_dump(),
        smtp_approval=grant_ref.model_dump(),
        delivery_receipts=receipt_ref.model_dump(),
    )
    holds = []
    for run in evidence["runs"]:
        run.update(
            status="COMPLETED",
            action_policy_version="MOCK-NOTIFY-V1",
            link_type="CREATED",
        )
        for channel in run["deliveries"]:
            channel["status"] = "SENT"
        if run["action_code"] == "EQP_HOLD":
            holds.append(run)
    sources = deepcopy(mock_evidence["sources"])
    sources["attempt_id"] = ATTEMPT
    sources["window"].update(started_at=AT, frozen_at=AT)
    targets = []
    from app.common.mes_identity import event_id_for

    for i, run in enumerate(holds):
        action, key = (
            run["action_id"],
            next(d["request_hash"] for d in run["deliveries"] if d["channel"] == "MES"),
        )
        targets.append(
            MockTarget(
                action_id=action,
                request_hash=key,
                created_run_id=run["run_id"],
                incident_key={
                    k: run["route"]["incident"][k] for k in ("lot_id", "chamber_id")
                },
                action_code="EQP_HOLD",
                action_policy_version="MOCK-NOTIFY-V1",
                link_type="CREATED",
            )
        )
        for collection in (
            "kafka_records",
            "callback_trail",
            "db_mes_rows",
            "n8n_executions",
        ):
            for row in sources[collection]:
                if row["action_id"] != f"action-{i}":
                    continue
                row["action_id"] = action
                for time_field in ("timestamp", "ts", "started_at", "completed_at"):
                    if time_field in row:
                        row[time_field] = AT
                if "request_hash" in row:
                    row["request_hash"] = key
                if "event_id" in row:
                    row["event_id"] = event_id_for(action, key)
                    row["key"] = action
    source_ref = write_private(root, "mock-sources.round1.json", sources)
    results_ref = emit_mock_results(
        root=root, sources=source_ref, targets=targets, expected_attempt_id=ATTEMPT
    )
    evidence.update(
        mock_sources=source_ref.model_dump(),
        mock_results=results_ref.model_dump(),
        kafka_before={"fdc.actions:0": 10, "fdc.actions.result:0": 20},
        kafka_after={"fdc.actions:0": 13, "fdc.actions.result:0": 23},
    )
    args["round1"] = replace(
        root, "round1.json", build_round(RoundEvidenceV2.model_validate(evidence))
    )
    for name in (
        "lifecycle-claim.resume_workload.json",
        "lifecycle-claim.publish.json",
    ):
        row = read(name)
        row["prepared_attempt"] = prepared.model_dump()
        replace(root, name, row)
    held = read("lifecycle-outcome.resume_workload.json")
    held["schema_version"] = "level3-lifecycle-outcome-v2"
    held["claim"] = component_ref(
        root, "lifecycle-claim.resume_workload.json"
    ).model_dump()
    held["external_effects"]["mes_sent"] = held["external_effects"].pop("mes_blocked")
    held_ref = replace(root, "lifecycle-outcome.resume_workload.json", held)
    golden = parse_json(read_private(published, "golden-flow.json"))
    golden["protocol"] = "MOCK-NOTIFY-V1"
    golden["phases"] = [
        dict(
            phase=phase,
            status="PASS" if i < 5 else "NOT_LIVE",
            reasons=[],
            metrics={},
            **(
                {
                    "evidence": {
                        "test_ref": "tests/unit/test_agent_mock_notify.py",
                        "revision": REV,
                    }
                }
                if i >= 5
                else {}
            ),
        )
        for i, phase in enumerate(MOCK_PHASES)
    ]
    validate_golden_summary(golden)
    replace(published, "golden-flow.json", golden)
    fault = parse_json(read_private(published, "fault-5class.json"))
    fault["policy_version"] = "MOCK-NOTIFY-V1"
    replace(published, "fault-5class.json", fault)
    completion = read("round1-completion.json")
    completion.update(
        schema_version="level3-round1-completion-v2",
        post_freeze_callbacks=0,
        round1=args["round1"].model_dump(),
        lifecycle_outcome_resume_workload=held_ref.model_dump(),
        lifecycle_claim_resume_workload=component_ref(
            root, "lifecycle-claim.resume_workload.json"
        ).model_dump(),
        lifecycle_claim_publish=component_ref(
            root, "lifecycle-claim.publish.json"
        ).model_dump(),
    )
    completion["external_effects"]["mes_sent"] = completion["external_effects"].pop(
        "mes_blocked"
    )
    for field, name in (
        ("attempt_artifact_sha256", "attempt.json"),
        ("golden_flow_sha256", "golden-flow.json"),
        ("fault_5class_sha256", "fault-5class.json"),
    ):
        completion[field] = component_ref(published, name).sha256
    args["round1_completion"] = replace(root, "round1-completion.json", completion)
    return args


def test_full_mock_bundle_recount_and_seal(mock_bundle, tmp_path):
    result = assess_aggregate(**mock_bundle)
    assert result.schema_version == "level3-aggregate-v2"
    assert result.robustness_verdict == result.delivery_integrity == "PASS"
    assert result.batch_summary.status_counts == {"COMPLETED": 12}
    repo = tmp_path / "repo"
    repo.mkdir()
    args = {
        k: v for k, v in mock_bundle.items() if k not in {"round1", "round1_completion"}
    }
    emit_aggregate(**args, repository=repo)
    seal_bundle(**args, repository=repo)
    assert verify_seal(**args)[0] == result
