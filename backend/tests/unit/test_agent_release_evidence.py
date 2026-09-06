"""Bundle C component contracts: synthetic private bytes, no live evidence."""

import json
import subprocess
import sys

import pytest

from app.agent import release_completion as completion_module
from app.agent import release_delivery as delivery_module
from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    canonical_json,
    component_ref,
    digest,
    write_private,
)
from app.agent.release_completion import verify_completion
from app.agent.release_delivery import EmailTarget, verify_delivery
from tests.unit.test_agent_release import (
    AT,
    ATTEMPT,
    EXPIRES,
    REV,
    S,
    claim,
    completion,
    outcome,
    prepared_payload,
    ref,
)


def replace(root, name, value):
    """Test-only tampering; live evidence writers remain no-clobber."""
    (root / name).write_bytes(canonical_json(value) + b"\n")
    return component_ref(root, name)


@pytest.fixture
def delivery(tmp_path):
    root = tmp_path / "robustness"
    root.mkdir(mode=0o700)
    prepared = prepared_payload()
    prepared_ref = write_private(root, "prepared-attempt.json", prepared)
    grant = dict(
        schema_version="smtp-send-grant-v1",
        grant_type="SMTP_SEND_GRANT",
        attempt_id=ATTEMPT,
        prepared_attempt=prepared_ref.model_dump(),
        approval_reference="test-only",
        approver="방대혁",
        recipient_canonical_addresses=prepared["recipient"]["canonical_addresses"],
        recipient_canonical_hash=prepared["recipient"]["canonical_hash"],
        recipient_hash_version=2,
        max_external_emails=7,
        approved_at=AT,
    )
    grant_ref = write_private(root, "smtp-approval-grant.json", grant)
    targets = [
        EmailTarget(
            action_id=f"action-{i}",
            action_code="WARNING" if i < 4 else "EQP_HOLD",
            email_kind="WARNING_NOTIFY" if i < 4 else "APPROVAL_REQUEST",
            request_hash=digest(f"test-key-{i}".encode()),
        )
        for i in range(7)
    ]
    receipts = dict(
        schema_version="level3-delivery-receipts-v1",
        attempt_id=ATTEMPT,
        capture_phase="PRE_HITL",
        recipient_hash_version=2,
        smtp_config_digest=S,
        callbacks=[
            dict(
                action_id=t.action_id,
                request_hash=t.request_hash,
                channel="EMAIL",
                status="SENT",
                provider_message_id=f"smtp-{i}",
                completed_at=AT,
            )
            for i, t in enumerate(targets)
        ],
        executions=[
            dict(
                action_id=t.action_id,
                request_hash=t.request_hash,
                channel="EMAIL",
                email_kind=t.email_kind,
                n8n_execution_id=f"execution-{i}",
                n8n_status="success",
                provider_message_id=f"smtp-{i}",
                recipients=["Team@EXAMPLE.invalid"],
                recipient_hash=prepared["recipient"]["canonical_hash"],
                observed_at=AT,
            )
            for i, t in enumerate(targets)
        ],
        approval_execution_ids=[f"execution-{i}" for i in range(4, 7)],
    )
    receipts_ref = write_private(root, "delivery-receipts.round1.json", receipts)
    return (
        dict(
            root=root,
            delivery_receipts=receipts_ref,
            prepared_attempt=prepared_ref,
            smtp_approval=grant_ref,
            targets=targets,
            evaluated_revision=REV,
            expected_attempt_id=ATTEMPT,
            resume_at=AT,
            captured_at=AT,
        ),
        receipts,
        grant,
    )


def test_seven_acceptances_independent_callback_execution_and_grant(delivery):
    args, _, _ = delivery
    before = {p.name: p.read_bytes() for p in args["root"].iterdir()}
    first = verify_delivery(**args)
    assert verify_delivery(**args) == first
    assert first.provider_acceptances == 7
    assert first.recipient_count == 1 and first.delivery_integrity == "PASS"
    assert "Team@" not in canonical_json(first).decode()
    assert {p.name: p.read_bytes() for p in args["root"].iterdir()} == before


@pytest.mark.parametrize(
    "kind,code",
    [
        ("missing_execution", "DELIVERY_POPULATION_INVALID"),
        ("duplicate_callback", "DELIVERY_POPULATION_INVALID"),
        ("duplicate_key", "DELIVERY_POPULATION_INVALID"),
        ("wrong_action", "DELIVERY_POPULATION_INVALID"),
        ("duplicate_execution", "DELIVERY_POPULATION_INVALID"),
        ("missing_message", "DELIVERY_PROVIDER_ACCEPTANCE_INVALID"),
        ("duplicate_message", "DELIVERY_PROVIDER_ACCEPTANCE_INVALID"),
        ("wrong_message", "DELIVERY_PROVIDER_ACCEPTANCE_INVALID"),
        ("not_terminal", "DELIVERY_PROVIDER_ACCEPTANCE_INVALID"),
        ("callback_failed", "DELIVERY_PROVIDER_ACCEPTANCE_INVALID"),
        ("callback_unknown", "DELIVERY_PROVIDER_ACCEPTANCE_INVALID"),
        ("wrong_kind", "DELIVERY_PROVIDER_ACCEPTANCE_INVALID"),
        ("wrong_recipient", "DELIVERY_RECIPIENT_MISMATCH"),
        ("wrong_hash", "DELIVERY_RECIPIENT_MISMATCH"),
        ("local_part_case", "DELIVERY_RECIPIENT_MISMATCH"),
        ("wrong_config", "DELIVERY_CONFIG_MISMATCH"),
        ("missing_approval", "DELIVERY_APPROVAL_RECEIPT_MISMATCH"),
        ("duplicate_approval", "DELIVERY_APPROVAL_RECEIPT_MISMATCH"),
        ("warning_as_approval", "DELIVERY_APPROVAL_RECEIPT_MISMATCH"),
        ("before_resume", "DELIVERY_TIME_INVALID"),
        ("after_capture", "DELIVERY_TIME_INVALID"),
        ("wrong_attempt", "DELIVERY_BINDING_MISMATCH"),
        ("wrong_hash_version", "DELIVERY_EVIDENCE_SCHEMA_INVALID"),
        ("wrong_channel", "DELIVERY_EVIDENCE_SCHEMA_INVALID"),
        ("extra_secret", "DELIVERY_EVIDENCE_SCHEMA_INVALID"),
    ],
)
def test_resealed_delivery_corruption_is_rejected(delivery, kind, code):
    args, data, _ = delivery
    row, callback = data["executions"][0], data["callbacks"][0]
    if kind == "missing_execution":
        data["executions"].pop()
    elif kind == "duplicate_callback":
        data["callbacks"][-1] = callback.copy()
    elif kind == "duplicate_key":
        data["executions"][-1] = row.copy()
    elif kind == "wrong_action":
        row["action_id"] = "other-action"
    elif kind == "duplicate_execution":
        row["n8n_execution_id"] = data["executions"][1]["n8n_execution_id"]
    elif kind == "missing_message":
        row["provider_message_id"] = None
    elif kind == "duplicate_message":
        row["provider_message_id"] = data["executions"][1]["provider_message_id"]
    elif kind == "wrong_message":
        callback["provider_message_id"] = "other-message"
    elif kind == "not_terminal":
        row["n8n_status"] = "running"
    elif kind.startswith("callback_"):
        callback["status"] = kind.removeprefix("callback_").upper()
    elif kind == "wrong_kind":
        row["email_kind"] = "APPROVAL_REQUEST"
    elif kind in {"wrong_recipient", "local_part_case"}:
        row["recipients"] = [
            "other@example.invalid"
            if kind == "wrong_recipient"
            else "team@example.invalid"
        ]
    elif kind == "wrong_hash":
        row["recipient_hash"] = "0" * 64
    elif kind == "wrong_config":
        data["smtp_config_digest"] = "0" * 64
    elif kind == "missing_approval":
        data["approval_execution_ids"].pop()
    elif kind == "duplicate_approval":
        data["approval_execution_ids"][0] = data["approval_execution_ids"][1]
    elif kind == "warning_as_approval":
        data["approval_execution_ids"][0] = row["n8n_execution_id"]
    elif kind == "before_resume":
        callback["completed_at"] = "2026-09-05T00:59:59Z"
    elif kind == "after_capture":
        row["observed_at"] = "2026-09-05T01:00:01Z"
    elif kind == "wrong_attempt":
        data["attempt_id"] = "20260905T020000Z-aaaaaaaaaaaa"
    elif kind == "wrong_hash_version":
        data["recipient_hash_version"] = 1
    elif kind == "wrong_channel":
        callback["channel"] = "MES"
    else:
        data["secret"] = "must-not-leak"
    args["delivery_receipts"] = replace(
        args["root"], "delivery-receipts.round1.json", data
    )
    with pytest.raises(EvidenceError, match=f"^{code}$"):
        verify_delivery(**args)


@pytest.mark.parametrize("kind", ["count", "duplicate", "distribution", "key"])
def test_round_targets_must_be_seven_unique_four_warning_three_hold(delivery, kind):
    args, _, _ = delivery
    targets = args["targets"]
    if kind == "count":
        targets.pop()
    elif kind == "duplicate":
        targets[-1] = targets[0]
    else:
        payload = targets[-1].model_dump()
        if kind == "distribution":
            payload.update(action_code="WARNING", email_kind="WARNING_NOTIFY")
        else:
            payload["request_hash"] = targets[0].request_hash
        targets[-1] = EmailTarget.model_validate(payload)
    with pytest.raises(EvidenceError, match="^DELIVERY_TARGET_POPULATION_INVALID$"):
        verify_delivery(**args)


@pytest.mark.parametrize("kind", ["recipient", "prepared", "time", "expires", "R"])
def test_grant_and_revision_are_not_self_asserted_by_delivery(delivery, kind):
    args, _, grant = delivery
    if kind == "recipient":
        from app.agent.release_prepared import recipient_hash

        grant["recipient_canonical_addresses"] = ["other@example.invalid"]
        grant["recipient_canonical_hash"] = recipient_hash(
            grant["recipient_canonical_addresses"]
        )
    elif kind == "prepared":
        grant["prepared_attempt"]["sha256"] = "0" * 64
    elif kind == "time":
        grant["approved_at"] = "2026-09-05T01:00:01Z"
    elif kind == "expires":
        args["resume_at"] = EXPIRES
        args["captured_at"] = EXPIRES
    else:
        args["evaluated_revision"] = "b" * 40
    args["smtp_approval"] = replace(args["root"], "smtp-approval-grant.json", grant)
    with pytest.raises(EvidenceError):
        verify_delivery(**args)


@pytest.mark.parametrize("kind", ["delete", "bytes", "symlink", "mode"])
def test_delivery_file_boundary(delivery, kind):
    args, _, _ = delivery
    path = args["root"] / "delivery-receipts.round1.json"
    if kind == "delete":
        path.unlink()
    elif kind == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif kind == "symlink":
        other = args["root"] / "other.json"
        path.rename(other)
        path.symlink_to(other)
    else:
        path.chmod(0o644)
    with pytest.raises(EvidenceError):
        verify_delivery(**args)


def test_delivery_rechecks_files_after_cross_binding(delivery, monkeypatch):
    args, _, _ = delivery
    original = delivery_module.resolve_component
    calls = []

    def resolve(root, reference):
        calls.append(reference.relative_path)
        if len(calls) == 4:
            path = root / "delivery-receipts.round1.json"
            path.write_bytes(path.read_bytes() + b" ")
        return original(root, reference)

    monkeypatch.setattr(delivery_module, "resolve_component", resolve)
    with pytest.raises(EvidenceError, match="^COMPONENT_SHA_MISMATCH$"):
        verify_delivery(**args)


@pytest.fixture
def completed(tmp_path):
    root, published = tmp_path / "robustness", tmp_path / "published"
    root.mkdir(mode=0o700)
    published.mkdir(mode=0o700)
    # Publication semantics belong to CM52's validators. This unit checks bytes.
    publications = {
        name: write_private(published, name, {"test_only": name})
        for name in ("attempt.json", "golden-flow.json", "fault-5class.json")
    }
    files = {
        "prepared-attempt.json": canonical_json(prepared_payload()),
        "round1.json": b"{}",
    }
    files["lifecycle-claim.resume_workload.json"] = canonical_json(
        claim(files, "RESUME_WORKLOAD")
    )
    files["lifecycle-outcome.resume_workload.json"] = canonical_json(outcome(files))
    # Publish long after grant expiry: only the historic resume consumes approval.
    publish = claim(files, "PUBLISH")
    publish["claimed_at"] = "2026-09-05T02:00:00Z"
    files["lifecycle-claim.publish.json"] = canonical_json(publish)
    data = completion(files)
    data["completed_at"] = "2026-09-05T02:01:00Z"
    for field, name in (
        ("attempt_artifact_sha256", "attempt.json"),
        ("golden_flow_sha256", "golden-flow.json"),
        ("fault_5class_sha256", "fault-5class.json"),
    ):
        data[field] = publications[name].sha256
    files["round1-completion.json"] = canonical_json(data)
    for name, raw in files.items():
        path = root / name
        path.write_bytes(raw)
        path.chmod(0o600)
    return dict(
        root=root,
        published_root=published,
        round1=Component(**ref(files, "round1.json")),
        round1_completion=Component(**ref(files, "round1-completion.json")),
        prepared_attempt=Component(**ref(files, "prepared-attempt.json")),
        evaluated_revision=REV,
        expected_attempt_id=ATTEMPT,
        captured_at=AT,
    ), files


def test_completion_transitive_lineage_and_published_bytes(completed):
    args, files = completed
    result = verify_completion(**args)
    assert result.completion_integrity == "PASS" and result.attempt_id == ATTEMPT
    assert verify_completion(**args) == result
    assert {p.name: p.read_bytes() for p in args["root"].iterdir()} == files


@pytest.mark.parametrize(
    "name",
    [
        "lifecycle-claim.resume_workload.json",
        "lifecycle-outcome.resume_workload.json",
        "lifecycle-claim.publish.json",
        "round1-completion.json",
        "round1.json",
    ],
)
def test_missing_transitive_component_rejected(completed, name):
    args, _ = completed
    (args["root"] / name).unlink()
    with pytest.raises(EvidenceError):
        verify_completion(**args)


@pytest.mark.parametrize(
    "kind",
    [
        "phase",
        "outcome",
        "claim_hash",
        "attempt",
        "failure",
        "round",
        "capture_time",
        "R",
        "expected_attempt",
        "publish_outcome",
        "extra_claim",
    ],
)
def test_completion_resealed_mismatch(completed, kind):
    args, files = completed
    data = json.loads(files["round1-completion.json"])
    if kind in {"phase", "outcome", "claim_hash"}:
        name = "lifecycle-outcome.resume_workload.json"
        payload = json.loads(files[name])
        if kind == "phase":
            payload["phase"] = "ABORT"
        elif kind == "outcome":
            payload["outcome"] = "ABORTED"
        else:
            payload["claim"]["sha256"] = "0" * 64
        data["lifecycle_outcome_resume_workload"] = replace(
            args["root"], name, payload
        ).model_dump()
    elif kind == "attempt":
        data["cm52_attempt_id"] = "20260905T020000Z-aaaaaaaaaaaa"
    elif kind == "failure":
        data.update(
            final_status="FAIL",
            primary_failure_code="ARTIFACT_PUBLISH_FAILED",
            failure_code="ARTIFACT_PUBLISH_FAILED",
        )
    elif kind == "round":
        data["round1"]["sha256"] = "0" * 64
    elif kind == "capture_time":
        args["captured_at"] = "2026-09-05T01:00:01Z"
    elif kind == "R":
        args["evaluated_revision"] = "b" * 40
    elif kind == "expected_attempt":
        args["expected_attempt_id"] = "20260905T020000Z-aaaaaaaaaaaa"
    elif kind == "publish_outcome":
        write_private(args["root"], "lifecycle-outcome.publish.json", {})
    else:
        write_private(args["root"], "lifecycle-claim.abort.json", claim(files, "ABORT"))
    args["round1_completion"] = replace(args["root"], "round1-completion.json", data)
    with pytest.raises(EvidenceError):
        verify_completion(**args)


@pytest.mark.parametrize(
    "name", ["attempt.json", "golden-flow.json", "fault-5class.json"]
)
def test_completion_recounts_publication_sha(completed, name):
    args, _ = completed
    path = args["published_root"] / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(EvidenceError, match="^COMPLETION_PUBLICATION_MISMATCH$"):
        verify_completion(**args)


def test_completion_detects_publication_drift(completed, monkeypatch):
    args, _ = completed
    original = completion_module.read_private
    reads = []

    def read(root, name):
        reads.append(name)
        if len(reads) == 4:
            path = root / name
            path.write_bytes(path.read_bytes() + b" ")
        return original(root, name)

    monkeypatch.setattr(completion_module, "read_private", read)
    with pytest.raises(EvidenceError, match="^COMPLETION_EVIDENCE_DRIFT$"):
        verify_completion(**args)


def test_component_modules_import_without_runtime_services():
    code = """
import sys
from app.agent import release_delivery, release_completion
for module in ('httpx', 'sqlalchemy', 'app.common.config', 'app.common.db'):
    assert module not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
