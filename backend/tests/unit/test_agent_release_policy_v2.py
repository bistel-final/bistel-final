"""New evidence contracts stay separate from legacy historical bytes."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agent.release_artifacts import EvidenceError, resolve_component
from app.agent.release_delivery import EmailTargetV2, verify_delivery
from app.agent.release_prepared import PreparedAttempt, parse_prepared, parse_smtp_grant
from app.agent.release_round import RoundEvidenceV2, assess_round
from scripts.grant_smtp_send import issue_grant
from tests.unit.test_agent_release import AT, ATTEMPT, S, prepared_payload
from tests.unit.test_agent_release_evidence import delivery, replace  # noqa: F401
from tests.unit.test_agent_release_round import template  # noqa: F401


def prepared_v2():
    value = prepared_payload()
    value["schema_version"] = "level3-prepared-attempt-v2"
    value["preflight_snapshot_sha256"] = S
    value["effective_env"]["AGENT_ACTION_POLICY"] = "MOCK-NOTIFY-V1"
    value["n8n_evidence_probe"].update(
        wf3_execution_detail_retained=True,
        wf4_execution_detail_retained=True,
        callback_trail_writable=True,
    )
    return value


def test_prepared_dispatch_preserves_legacy_bytes_and_rejects_mixed_shapes():
    legacy = prepared_payload()
    assert (
        parse_prepared(legacy).model_dump()
        == PreparedAttempt.model_validate(legacy).model_dump()
    )
    value = prepared_v2()
    assert parse_prepared(value).effective_env.AGENT_ACTION_POLICY == "MOCK-NOTIFY-V1"
    value["schema_version"] = "level3-prepared-attempt-v1"
    with pytest.raises(ValidationError):
        parse_prepared(value)


@pytest.mark.parametrize(
    "field",
    [
        "wf3_execution_detail_retained",
        "wf4_execution_detail_retained",
        "callback_trail_writable",
    ],
)
def test_prepare_v2_requires_all_mock_observation_capabilities(field):
    value = prepared_v2()
    value["n8n_evidence_probe"][field] = False
    with pytest.raises(ValidationError):
        parse_prepared(value)


def test_v2_grant_issued_with_policy_and_seven_action_notify_acceptances(delivery):  # noqa: F811
    args, receipts, _ = deepcopy(delivery)
    root = args["root"]
    args["prepared_attempt"] = replace(root, "prepared-attempt.json", prepared_v2())
    (root / "smtp-approval-grant.json").unlink()
    prepared = parse_prepared(prepared_v2())
    result = issue_grant(
        root / "prepared-attempt.json",
        approver="방대혁",
        approval_reference="test-only",
        approved_at=AT,
        confirmation=f"SMTP_SEND_GRANT {ATTEMPT} {prepared.recipient.canonical_hash} 7",
    )
    from app.agent.release_artifacts import component_ref

    args["smtp_approval"] = component_ref(root, "smtp-approval-grant.json")
    grant = parse_smtp_grant(resolve_component(root, args["smtp_approval"]))
    assert grant.action_policy_version == "MOCK-NOTIFY-V1"
    assert result["max_external_emails"] == 7
    receipts.update(
        schema_version="level3-delivery-receipts-v2",
        capture_phase="POST_MOCK_CONVERGENCE",
    )
    receipts["hold_notify_execution_ids"] = receipts.pop("approval_execution_ids")
    for row in receipts["executions"]:
        row["email_kind"] = "ACTION_NOTIFY"
    args["delivery_receipts"] = replace(root, "delivery-receipts.round1.json", receipts)
    args["targets"] = [
        EmailTargetV2(**{**t.model_dump(), "email_kind": "ACTION_NOTIFY"})
        for t in args["targets"]
    ]
    assert verify_delivery(**args).provider_acceptances == 7
    old = grant.model_dump(exclude={"action_policy_version"})
    old["schema_version"] = "smtp-send-grant-v1"
    args["smtp_approval"] = replace(root, "smtp-approval-grant.json", old)
    with pytest.raises(EvidenceError, match="EXTERNAL_EFFECT_APPROVAL_MISSING"):
        verify_delivery(**args)


def test_round_v2_recomputes_twelve_completed_and_notify_email_targets(template):  # noqa: F811
    value = deepcopy(template)
    value.update(
        schema_version="level3-round1-v2",
        capture_phase="POST_MOCK_CONVERGENCE",
        action_policy_version="MOCK-NOTIFY-V1",
        approval_rows=0,
        mock_sources={"relative_path": "mock-sources.round1.json", "sha256": S},
        mock_results={"relative_path": "mock-results.round1.json", "sha256": S},
        kafka_before={"fdc.actions:0": 0, "fdc.actions.result:0": 10},
        kafka_after={"fdc.actions:0": 3, "fdc.actions.result:0": 13},
    )
    for run in value["runs"]:
        run.update(
            action_policy_version="MOCK-NOTIFY-V1",
            link_type="CREATED",
            status="COMPLETED",
        )
        for channel in run["deliveries"]:
            channel["status"] = "SENT"
    assessment, targets = assess_round(RoundEvidenceV2.model_validate(value))
    assert (
        assessment.robustness_verdict == assessment.delivery_snapshot_verdict == "PASS"
    )
    assert assessment.status_counts == {"COMPLETED": 12}
    assert len(targets) == 7 and {t.email_kind for t in targets} == {"ACTION_NOTIFY"}
    value["runs"][-1]["status"] = "WAITING_APPROVAL"
    assessment, _ = assess_round(RoundEvidenceV2.model_validate(value))
    assert assessment.robustness_verdict == "FAIL"
