"""V5-C-7.1 private evidence tests. No shared deployments or effects."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent import release_lifecycle as life
from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    canonical_json,
    component_ref,
    digest,
    parse_json,
    read_private,
    resolve_component,
    validate_report_root,
    write_private,
)
from app.agent.release_prepared import (
    RUNTIME_BINDING_FIELDS,
    PreparedAttempt,
    Recipient,
    SmtpGrant,
    canonical_recipients,
    config_digest,
    recipient_hash,
    validate_grant,
    validate_log_prefix,
    validate_runtime,
)
from scripts.grant_smtp_send import issue_grant

POLICY = json.loads(
    (Path(__file__).parents[1] / "fixtures/v5_c_7_1/lifecycle_policy.json").read_text()
)
REV = "a" * 40
ATTEMPT = "20260905T010000Z-aaaaaaaaaaaa"
AT = "2026-09-05T01:00:00Z"
LATER = "2026-09-05T01:01:00Z"
EXPIRES = "2026-09-05T01:30:00Z"
S = "1" * 64


def prepared_payload():
    return {
        "schema_version": "level3-prepared-attempt-v1",
        "attempt_id": ATTEMPT,
        "R": REV,
        "images": {
            role: {"image_id": "sha256:" + str(i) * 64, "label_revision": REV}
            for i, role in enumerate(("backend", "frontend", "runner"), 1)
        },
        "containers": {
            role: {"container_id": str(i) * 64, "started_at": AT}
            for i, role in enumerate(("backend", "frontend", "runner"), 1)
        },
        "effective_env": {
            "AGENT_AUTONOMY_LEVEL": 3,
            "AGENT_LEVEL3_ENABLED": True,
            "AGENT_LEVEL3_DEMO_ACK": "",
            "level12_total": 8,
            "level3_total": 10,
            "send": 2,
            "same_tool_attempts": 4,
            "selector_steps": 10,
        },
        "db_identity": {
            "host_alias": "postgres",
            "database": "kosa_agent_e2e",
            "current_database": "kosa_agent_e2e",
            "system_identifier": "12345",
        },
        "n8n_evidence_probe": {
            "execution_data_retained": True,
            "returns_action_id": True,
            "returns_recipient": True,
            "verdict": "PASS",
        },
        "recipient": {
            "canonical_addresses": ["Team@example.invalid"],
            "canonical_hash": recipient_hash(["Team@example.invalid"]),
            "recipient_hash_version": 2,
            "count": 1,
        },
        "reset_final_receipt_sha256": S,
        "observer_baseline_sha256": S,
        "approved_config_digest_allowlist": [S],
        "max_external_emails": 7,
        "e2e_level3_preflight_output_sha256": S,
        "prepared_at": AT,
        "expires_at": EXPIRES,
        "prev_state": "empty",
        "prev_fault_path": None,
        "prev_golden_path": None,
        "prev_fault_sha256": None,
        "prev_golden_sha256": None,
        "prev_rev": None,
        "prev_attempt": None,
        "running_rev": "b" * 40,
        "stage2_log": {
            "relative_path": "stage2-log.jsonl",
            "prefix_bytes": 3,
            "prefix_sha256": digest(b"{}\n"),
        },
        "last_ok_step": "3b",
    }


def ref(files, name):
    return {"relative_path": name, "sha256": digest(files[name])}


def claim(files, phase):
    return {
        "schema_version": "level3-lifecycle-claim-v1",
        "phase": phase,
        "prepared_attempt": ref(files, "prepared-attempt.json"),
        "invocation_id": "11111111-1111-1111-1111-111111111111",
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "boot_id": "boot",
        "process_start_time": "start",
        "claimed_at": AT,
    }


def outcome(files, phase="RESUME_WORKLOAD", kind="HELD"):
    return {
        "schema_version": "level3-lifecycle-outcome-v1",
        "phase": phase,
        "outcome": kind,
        "issued_by": phase,
        "claim": ref(files, life.claim_filename(phase)),
        "reason_code": "OPERATOR_ABORT" if phase == "ABORT" else None,
        "primary_failure_code": None,
        "failure_code": None,
        "recovery_started_workload": False,
        "external_effects": {
            "state": "KNOWN",
            "email_sent": 0 if phase == "ABORT" else 7,
            "mes_blocked": 0 if phase == "ABORT" else 3,
            "basis": "OBSERVED",
        },
        "cleanup_result": "NOT_ATTEMPTED" if kind == "HELD" else "OK",
        "restore_result": "NOT_ATTEMPTED" if kind == "HELD" else "OK",
        "finished_at": AT,
    }


def completion(files):
    return {
        "schema_version": "level3-round1-completion-v1",
        "cm52_attempt_id": ATTEMPT,
        "round1": ref(files, "round1.json"),
        "final_status": "PASS",
        "lifecycle_claim_resume_workload": ref(
            files, life.claim_filename("RESUME_WORKLOAD")
        ),
        "lifecycle_outcome_resume_workload": ref(
            files, life.terminal_filename("RESUME_WORKLOAD")
        ),
        "lifecycle_claim_publish": ref(files, life.claim_filename("PUBLISH")),
        "issued_by": "PUBLISH",
        "reason_code": None,
        "primary_failure_code": None,
        "failure_code": None,
        "cleanup_result": "OK",
        "restore_result": "OK",
        "external_effects": {
            "state": "KNOWN",
            "email_sent": 7,
            "mes_blocked": 3,
            "basis": "SNAPSHOT",
        },
        "attempt_artifact_sha256": S,
        "golden_flow_sha256": S,
        "fault_5class_sha256": S,
        "completed_at": LATER,
    }


def files_for(state):
    files = {"prepared-attempt.json": canonical_json(prepared_payload())}
    if state == "PREPARED":
        return files
    files[life.claim_filename("RESUME_WORKLOAD")] = canonical_json(
        claim(files, "RESUME_WORKLOAD")
    )
    if state == "UNRESOLVED":
        return files
    files[life.terminal_filename("RESUME_WORKLOAD")] = canonical_json(outcome(files))
    if state == "HELD":
        return files
    files["round1.json"] = b"{}"
    files[life.claim_filename("PUBLISH")] = canonical_json(claim(files, "PUBLISH"))
    files["round1-completion.json"] = canonical_json(completion(files))
    return files


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "robustness"
    root.mkdir(mode=0o700)
    write_private(root, "prepared-attempt.json", prepared_payload())
    return root


@pytest.mark.parametrize("state", POLICY["transitions"])
@pytest.mark.parametrize("mode", ["RESUME_WORKLOAD", "PUBLISH", "ABORT", "RECOVER"])
def test_shared_transition_fixture_no_mutation(state, mode):
    files = files_for(state)
    before = deepcopy(files)
    assert life.classify_state(files) == state
    if mode in POLICY["transitions"][state]:
        assert life.authorize_transition(state, mode) == "ALLOW"
    else:
        with pytest.raises(EvidenceError, match="LIFECYCLE_TRANSITION_INVALID"):
            life.authorize_transition(state, mode)
    assert files == before


@pytest.mark.parametrize("case", POLICY["failures"])
@pytest.mark.parametrize("result", POLICY["results"])
def test_failure_priority_shared_fixture(case, result):
    primary = case["primary_failure_code"]
    expected = primary if result["failure"] == "PRIMARY" else result["failure"]
    assert (
        life.resolve_failure_code(
            case["issued_by"],
            primary,
            result["cleanup_result"],
            result["restore_result"],
        )
        == expected
    )
    files = files_for("TERMINAL")
    phase = case["phase"]
    if phase == "ABORT":
        files[life.claim_filename(phase)] = canonical_json(claim(files, phase))
    model = life.RoundCompletion if phase == "PUBLISH" else life.LifecycleOutcome
    payload = (
        completion(files) if phase == "PUBLISH" else outcome(files, phase, "FAILED")
    )
    payload.update(
        issued_by=case["issued_by"],
        primary_failure_code=primary,
        failure_code=expected,
        cleanup_result=result["cleanup_result"],
        restore_result=result["restore_result"],
    )
    if phase == "PUBLISH":
        payload["final_status"] = "FAIL"
    if case["issued_by"] == "RECOVER":
        payload["reason_code"] = None
        payload["external_effects"] = {
            "state": "INDETERMINATE",
            "email_sent": None,
            "mes_blocked": None,
            "basis": "OWNER_DIED_DURING_WORKLOAD",
        }
    if case["issued_by"] == "ABORT" and expected is None:
        payload["outcome"] = "ABORTED"
    assert model.model_validate(payload).failure_code == expected
    payload["failure_code"] = (
        "CLEANUP_FAILED" if expected != "CLEANUP_FAILED" else "RESTORE_FAILED"
    )
    with pytest.raises(ValidationError, match="FAILURE_CODE_MISMATCH"):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason_code", "OPERATOR_ABORT"),
        ("primary_failure_code", "ARTIFACT_PUBLISH_FAILED"),
        ("issued_by", "RECOVER"),
        ("cleanup_result", "FAILED"),
        ("attempt_artifact_sha256", None),
        ("lifecycle_claim_publish", None),
    ],
)
def test_pass_completion_cannot_hide_failure(field, value):
    payload = completion(files_for("TERMINAL"))
    payload[field] = value
    with pytest.raises(ValidationError):
        life.RoundCompletion.model_validate(payload)


@pytest.mark.parametrize("kind", ["HELD", "PASS"])
@pytest.mark.parametrize(
    "field,value",
    [("email_sent", 6), ("email_sent", 8), ("mes_blocked", 2), ("mes_blocked", 4)],
)
def test_held_and_pass_require_exact_external_effect_counts(kind, field, value):
    if kind == "HELD":
        payload = outcome(files_for("HELD"))
        model, error = life.LifecycleOutcome, "LIFECYCLE_HELD_INVALID"
    else:
        payload = completion(files_for("TERMINAL"))
        model, error = life.RoundCompletion, "COMPLETION_PASS_INVALID"
    model.model_validate(payload)  # The unmodified 7/3 fixture is valid.
    payload["external_effects"][field] = value
    with pytest.raises(ValidationError, match=error):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "state,email,mes",
    [
        ("KNOWN", None, 0),
        ("KNOWN", 0, None),
        ("INDETERMINATE", 0, None),
        ("INDETERMINATE", None, 0),
    ],
)
def test_unknown_effects_are_not_zero(state, email, mes):
    with pytest.raises(ValidationError, match="EXTERNAL_EFFECTS_NULL_MATRIX"):
        life.ExternalEffects(
            state=state, email_sent=email, mes_blocked=mes, basis="UNKNOWN"
        )


def test_not_attempted_requires_basis():
    payload = completion(files_for("TERMINAL"))
    payload.update(
        final_status="FAIL",
        primary_failure_code="PUBLISH_PRECONDITION_FAILED",
        failure_code="PUBLISH_PRECONDITION_FAILED",
        cleanup_result="NOT_ATTEMPTED",
        restore_result="NOT_ATTEMPTED",
    )
    with pytest.raises(ValidationError, match="CLEANUP_BASIS_REQUIRED"):
        life.RoundCompletion.model_validate(payload)
    payload["external_effects"]["basis"] = "CLEANUP_E2E_ABSENT"
    with pytest.raises(ValidationError, match="RESTORE_BASIS_REQUIRED"):
        life.RoundCompletion.model_validate(payload)
    payload["external_effects"]["basis"] += " RESTORE_ALREADY_TARGET"
    assert (
        life.RoundCompletion.model_validate(payload).failure_code
        == "PUBLISH_PRECONDITION_FAILED"
    )


@pytest.mark.parametrize(
    "phase,kind", [("ABORT", "HELD"), ("RESUME_WORKLOAD", "ABORTED")]
)
def test_cross_phase_outcome_rejected(phase, kind):
    files = files_for("PREPARED")
    files[life.claim_filename(phase)] = canonical_json(claim(files, phase))
    with pytest.raises(ValidationError, match="LIFECYCLE_PHASE_MISMATCH"):
        life.LifecycleOutcome.model_validate(outcome(files, phase, kind))


def test_forbidden_publish_outcome_and_multiple_unresolved():
    files = files_for("PREPARED")
    files["lifecycle-outcome.publish.json"] = b"{}"
    with pytest.raises(EvidenceError, match="LIFECYCLE_PHASE_INVALID"):
        life.classify_state(files)
    files.pop("lifecycle-outcome.publish.json")
    for phase in ("ABORT", "RESUME_WORKLOAD"):
        files[life.claim_filename(phase)] = canonical_json(claim(files, phase))
    with pytest.raises(EvidenceError, match="LIFECYCLE_MULTIPLE_UNRESOLVED"):
        life.classify_state(files)


@pytest.mark.parametrize(
    "name",
    [
        "prepared-attempt.json",
        "lifecycle-claim.resume_workload.json",
        "lifecycle-outcome.resume_workload.json",
        "lifecycle-claim.publish.json",
        "round1.json",
    ],
)
def test_terminal_missing_or_replaced_components(name):
    files = files_for("TERMINAL")
    files[name] = b'{"tampered":true}'
    with pytest.raises((EvidenceError, ValidationError)):
        life.classify_state(files)
    files = files_for("TERMINAL")
    files.pop(name)
    with pytest.raises((EvidenceError, ValidationError)):
        life.classify_state(files)


@pytest.mark.parametrize("state", ["PREPARED", "UNRESOLVED", "HELD"])
@pytest.mark.parametrize("name", ["aggregate.json", "MANIFEST.sha256"])
def test_seal_forbidden_before_pass_completion(state, name):
    files = files_for(state)
    files[name] = b"{}"
    with pytest.raises(EvidenceError, match="LIFECYCLE_SEAL_FORBIDDEN"):
        life.classify_state(files)


def test_abort_then_resume_rejected():
    files = files_for("PREPARED")
    files[life.claim_filename("ABORT")] = canonical_json(claim(files, "ABORT"))
    files[life.terminal_filename("ABORT")] = canonical_json(
        outcome(files, "ABORT", "ABORTED")
    )
    assert life.classify_state(files) == "TERMINAL"
    files[life.claim_filename("RESUME_WORKLOAD")] = canonical_json(
        claim(files, "RESUME_WORKLOAD")
    )
    with pytest.raises(EvidenceError, match="LIFECYCLE_TRANSITION_INVALID"):
        life.classify_state(files)


def test_runtime_binding_and_log_prefix(bundle):
    prepared = PreparedAttempt.model_validate(prepared_payload())
    expected = prepared.model_dump(mode="json")
    observed = {key: expected[key] for key in RUNTIME_BINDING_FIELDS}
    validate_runtime(prepared, observed, observed_config_digest=S)
    for key in RUNTIME_BINDING_FIELDS:
        changed = deepcopy(observed)
        changed[key] = {}
        with pytest.raises(EvidenceError, match="PREPARED_RUNTIME_DRIFT"):
            validate_runtime(prepared, changed, observed_config_digest=S)
    log = bundle / "stage2-log.jsonl"
    log.write_bytes(b"{}\n{}\n")
    log.chmod(0o600)
    validate_log_prefix(prepared, bundle)
    log.write_bytes(b"[]\n{}\n")
    with pytest.raises(EvidenceError, match="PREPARED_LOG_PREFIX_MISMATCH"):
        validate_log_prefix(prepared, bundle)


def smtp_config_payload():
    return {
        "n8n_workflow_versions": {"WF2": "v2", "WF4": "v1"},
        "smtp_host": "smtp.example.invalid",
        "smtp_port": 587,
        "smtp_from": "FDC@example.invalid",
        "recipient_allowlist": ["Team@example.invalid"],
        "wf2_callback_endpoint": "https://backend.example.invalid/internal/delivery",
    }


def test_config_digest_canonical_fixture():
    payload = smtp_config_payload()
    expected_bytes = (
        b'{"n8n_workflow_versions":{"WF2":"v2","WF4":"v1"},'
        b'"recipient_allowlist":["Team@example.invalid"],'
        b'"smtp_from":"FDC@example.invalid","smtp_host":"smtp.example.invalid",'
        b'"smtp_port":587,"wf2_callback_endpoint":'
        b'"https://backend.example.invalid/internal/delivery"}'
    )
    assert config_digest(payload) == digest(expected_bytes)
    reordered = dict(reversed(list(payload.items())))
    reordered["n8n_workflow_versions"] = {"WF4": "v1", "WF2": "v2"}
    reordered["recipient_allowlist"] = [" Team@EXAMPLE.INVALID "]
    assert config_digest(reordered) == config_digest(payload)
    assert payload == smtp_config_payload()  # Do not mutate the live snapshot.


@pytest.mark.parametrize(
    "field,value",
    [
        ("n8n_workflow_versions", {"WF2": "v3", "WF4": "v1"}),
        ("n8n_workflow_versions", {"WF2-other": "v2", "WF4": "v1"}),
        ("smtp_host", "changed.example.invalid"),
        ("smtp_port", 465),
        ("smtp_from", "Other@example.invalid"),
        ("recipient_allowlist", ["team@example.invalid"]),
        ("wf2_callback_endpoint", "https://other.example.invalid/callback"),
    ],
)
def test_live_config_drift_cannot_echo_the_prepared_allowlist(field, value):
    config = smtp_config_payload()
    original = config_digest(config)
    payload = prepared_payload()
    # Multiple explicitly approved variants are allowed, but not arbitrary drift.
    payload["approved_config_digest_allowlist"] = [S, original]
    prepared = PreparedAttempt.model_validate(payload)
    observed = {key: payload[key] for key in RUNTIME_BINDING_FIELDS}
    assert "approved_config_digest_allowlist" not in observed
    validate_runtime(prepared, observed, observed_config_digest=original)
    config[field] = value
    with pytest.raises(EvidenceError, match="PREPARED_RUNTIME_DRIFT"):
        validate_runtime(
            prepared, observed, observed_config_digest=config_digest(config)
        )


@pytest.mark.parametrize("value", [None, [], [S], "", "A" * 64, "z" * 64])
def test_runtime_config_digest_requires_a_canonical_sha(value):
    payload = prepared_payload()
    prepared = PreparedAttempt.model_validate(payload)
    observed = {key: payload[key] for key in RUNTIME_BINDING_FIELDS}
    with pytest.raises(EvidenceError, match="PREPARED_RUNTIME_DRIFT"):
        validate_runtime(prepared, observed, observed_config_digest=value)


@pytest.mark.parametrize(
    "field,value",
    [("password", "do-not-log"), ("smtp_port", True), ("n8n_workflow_versions", {})],
)
def test_config_digest_rejects_secrets_or_invalid_projection(field, value):
    payload = smtp_config_payload()
    payload[field] = value
    with pytest.raises(EvidenceError) as exc:
        config_digest(payload)
    assert str(exc.value) == "CONFIG_DIGEST_PAYLOAD_INVALID"


def grant_args():
    recipients = recipient_hash(["Team@example.invalid"])
    return {
        "approver": "방대혁",
        "approval_reference": "team-confirmed SMTP_SEND_GRANT",
        "confirmation": f"SMTP_SEND_GRANT {ATTEMPT} {recipients} 7",
        "approved_at": LATER,
    }


def test_grant_manual_no_clobber_redacted(bundle):
    assert not (bundle / "smtp-approval-grant.json").exists()
    result = issue_grant(bundle / "prepared-attempt.json", **grant_args())
    assert "Team@example.invalid" not in json.dumps(result)
    assert result["warning"] == "production is DOWN"
    grant = SmtpGrant.model_validate(
        parse_json(read_private(bundle, "smtp-approval-grant.json"))
    )
    validate_grant(
        bundle,
        PreparedAttempt.model_validate(prepared_payload()),
        grant,
        resume_at=LATER,
    )
    before = read_private(bundle, "smtp-approval-grant.json")
    with pytest.raises(EvidenceError, match="ARTIFACT_EXISTS"):
        issue_grant(bundle / "prepared-attempt.json", **grant_args())
    assert read_private(bundle, "smtp-approval-grant.json") == before
    assert life.classify_state(life.read_lifecycle(bundle)) == "PREPARED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("confirmation", "APPROVE_19.9H"),
        ("confirmation", "DATA_EXPORT_APPROVED"),
        ("approver", "unknown"),
        ("approved_at", "2026-09-05T00:59:00Z"),
        ("approved_at", EXPIRES),
    ],
)
def test_wrong_authority_or_time_creates_no_grant(bundle, field, value):
    args = grant_args()
    args[field] = value
    with pytest.raises((EvidenceError, ValidationError)):
        issue_grant(bundle / "prepared-attempt.json", **args)
    assert not (bundle / "smtp-approval-grant.json").exists()


def test_recipient_v2_preserves_local_part():
    assert canonical_recipients([" b@EXAMPLE.invalid ", "A@Example.INVALID"]) == [
        "A@example.invalid",
        "b@example.invalid",
    ]
    assert recipient_hash(["A@example.invalid"]) != recipient_hash(
        ["a@example.invalid"]
    )
    with pytest.raises(EvidenceError, match="RECIPIENT_INVALID"):
        canonical_recipients(["a@Example.invalid", "a@example.invalid"])
    value = prepared_payload()["recipient"]
    for changes in (
        {"recipient_hash_version": 1},
        {"canonical_addresses": ["Team@EXAMPLE.invalid"]},
        {"count": 2},
        {"canonical_hash": "0" * 64},
    ):
        with pytest.raises(ValidationError):
            Recipient.model_validate({**value, **changes})


def test_grant_reuse_sha_and_expiry_boundary(bundle):
    issue_grant(bundle / "prepared-attempt.json", **grant_args())
    grant = SmtpGrant.model_validate(
        parse_json(read_private(bundle, "smtp-approval-grant.json"))
    )
    prepared = PreparedAttempt.model_validate(prepared_payload())
    with pytest.raises(EvidenceError, match="PREPARED_ATTEMPT_EXPIRED"):
        validate_grant(bundle, prepared, grant, resume_at=EXPIRES)
    changed = prepared_payload()
    changed["recipient"]["canonical_addresses"] = ["Other@example.invalid"]
    changed["recipient"]["canonical_hash"] = recipient_hash(["Other@example.invalid"])
    with pytest.raises(EvidenceError, match="EXTERNAL_EFFECT_APPROVAL_MISSING"):
        validate_grant(
            bundle, PreparedAttempt.model_validate(changed), grant, resume_at=LATER
        )
    bad = grant.model_dump(mode="json")
    bad["prepared_attempt"]["sha256"] = "0" * 64
    with pytest.raises(EvidenceError, match="COMPONENT_SHA_MISMATCH"):
        validate_grant(bundle, prepared, SmtpGrant.model_validate(bad), resume_at=LATER)


@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", "a/../x", "a//x", "./x", "a\nb", "a\\b"]
)
def test_component_locator_rejects_escape(path):
    with pytest.raises((EvidenceError, ValidationError)):
        Component(relative_path=path, sha256=S)


@pytest.mark.parametrize("mode", [0o644, 0o400, 0o777])
def test_private_exact_mode(bundle, mode):
    (bundle / "prepared-attempt.json").chmod(mode)
    with pytest.raises(EvidenceError, match="COMPONENT_FILE_INVALID"):
        read_private(bundle, "prepared-attempt.json")


def test_no_follow_leaf_parent_root(bundle, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    write_private(outside, "data.json", {"safe": 1})
    (bundle / "linked").symlink_to(outside, target_is_directory=True)
    (bundle / "leaf.json").symlink_to(outside / "data.json")
    for name in ("linked/data.json", "leaf.json"):
        with pytest.raises(EvidenceError):
            read_private(bundle, name)
    linked = tmp_path / "linked_root"
    linked.symlink_to(bundle, target_is_directory=True)
    with pytest.raises(EvidenceError):
        read_private(linked, "prepared-attempt.json")


def test_sha_duplicate_json_and_constants(bundle):
    good = component_ref(bundle, "prepared-attempt.json")
    assert resolve_component(bundle, good)["attempt_id"] == ATTEMPT
    with pytest.raises(EvidenceError, match="COMPONENT_SHA_MISMATCH"):
        resolve_component(
            bundle, Component(relative_path=good.relative_path, sha256="0" * 64)
        )
    for raw in (b'{"a":1,"a":2}', b'{"v":NaN}', b'{"v":Infinity}'):
        with pytest.raises(EvidenceError, match="COMPONENT_JSON_INVALID"):
            parse_json(raw)


def test_report_root_outside_repo_mount_same(tmp_path):
    repo, protected = tmp_path / "repo", tmp_path / "private"
    repo.mkdir(mode=0o700)
    protected.mkdir(mode=0o700)
    inside = repo / "reports"
    inside.mkdir(mode=0o700)
    assert validate_report_root(protected, protected, repo) == protected.resolve()
    with pytest.raises(EvidenceError, match="LEVEL3_REPORT_ROOT_INSIDE_REPO"):
        validate_report_root(inside, inside, repo)
    link = tmp_path / "reentry"
    link.symlink_to(inside, target_is_directory=True)
    with pytest.raises(EvidenceError, match="REPORT_ROOT_SYMLINK_REENTRY"):
        validate_report_root(link, link, repo)
    with pytest.raises(EvidenceError, match="REPORT_ROOT_MOUNT_MISMATCH"):
        validate_report_root(protected, inside, repo)


def test_actual_cross_process_lock_and_release(bundle):
    code = (
        "from pathlib import Path; "
        "from app.agent.release_lifecycle import lifecycle_lock; "
        f"lock=lifecycle_lock(Path({str(bundle)!r})); "
        "lock.__enter__(); lock.__exit__(None,None,None)"
    )
    with life.lifecycle_lock(bundle):
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=5
        )
        assert result.returncode != 0
        assert "LIFECYCLE_LOCK_BUSY" in result.stderr
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "boot,start,allowed",
    [
        ("boot", "start", False),
        ("boot", None, True),
        ("reboot", "start", True),
        ("boot", "reused-pid", True),
    ],
)
def test_owner_identity_prevents_pid_reuse(monkeypatch, boot, start, allowed):
    value = life.LifecycleClaim.model_validate(
        claim(files_for("PREPARED"), "RESUME_WORKLOAD")
    )
    monkeypatch.setattr(life, "owner_identity", lambda _pid: (boot, start))
    if allowed:
        life.assert_stale_owner(value)
    else:
        with pytest.raises(EvidenceError, match="LIFECYCLE_OWNER_ACTIVE"):
            life.assert_stale_owner(value)


def test_unknown_host_cannot_prove_owner_dead():
    value = claim(files_for("PREPARED"), "RESUME_WORKLOAD")
    value["host"] = "different-host.invalid"
    with pytest.raises(EvidenceError, match="LIFECYCLE_OWNER_UNVERIFIABLE"):
        life.assert_stale_owner(life.LifecycleClaim.model_validate(value))


@pytest.mark.parametrize("locale", ["ko_KR.UTF-8", "fr_FR.UTF-8"])
def test_darwin_owner_identity_uses_c_locale(monkeypatch, locale):
    monkeypatch.setattr(life.sys, "platform", "darwin")
    monkeypatch.setenv("LC_ALL", locale)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    calls = []

    def run(args, **kwargs):
        calls.append(args[0])
        assert kwargs["env"]["LC_ALL"] == "C"
        assert kwargs["env"]["PATH"] == "/usr/bin:/bin"
        return SimpleNamespace(
            returncode=0, stdout="boot" if args[0] == "sysctl" else "start"
        )

    monkeypatch.setattr(life.subprocess, "run", run)
    value = life.LifecycleClaim.model_validate(
        claim(files_for("PREPARED"), "RESUME_WORKLOAD")
    )
    with pytest.raises(EvidenceError, match="LIFECYCLE_OWNER_ACTIVE"):
        life.assert_stale_owner(value)
    assert calls == ["sysctl", "ps"]


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("effective_env", "AGENT_LEVEL3_ENABLED", 1),
        ("effective_env", "level3_total", 10.0),
        ("n8n_evidence_probe", "returns_recipient", 1),
        ("recipient", "recipient_hash_version", 2.0),
    ],
)
def test_receipt_literals_do_not_coerce_json_types(section, field, value):
    payload = prepared_payload()
    payload[section][field] = value
    with pytest.raises(ValidationError, match="EVIDENCE_LITERAL_TYPE_INVALID"):
        PreparedAttempt.model_validate(payload)


def test_generated_contract_and_readonly_inspection(bundle, capsys):
    from scripts import render_level3_lifecycle_contract as contract
    from scripts import validate_level3_lifecycle as inspect

    assert contract.main(["--check"]) == 0
    capsys.readouterr()
    before = set(bundle.iterdir())
    assert inspect.main(["--bundle-root", str(bundle)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "PREPARED"
    assert report["inspection_only"] is True
    assert report["warning"] == "production is DOWN"
    assert "Team@example.invalid" not in json.dumps(report)
    assert set(bundle.iterdir()) == before  # read-only: even lock creation is forbidden


def test_failed_effect_evidence_can_record_a_real_cap_violation():
    payload = outcome(files_for("HELD"), kind="FAILED")
    payload.update(
        primary_failure_code="STAGE2_STEP_FAILED", failure_code="STAGE2_STEP_FAILED"
    )
    payload["external_effects"]["email_sent"] = 8
    assert (
        life.LifecycleOutcome.model_validate(payload).external_effects.email_sent == 8
    )


@pytest.mark.parametrize(
    "filename", ["lifecycle-claim.json", "lifecycle-claim.unknown.json"]
)
def test_unknown_phase_files_do_not_look_prepared(bundle, filename):
    files = files_for("PREPARED")
    files[filename] = b"{}"
    with pytest.raises(EvidenceError, match="LIFECYCLE_PHASE_INVALID"):
        life.classify_state(files)
    write_private(bundle, filename, {})
    with pytest.raises(EvidenceError, match="LIFECYCLE_PHASE_INVALID"):
        life.read_lifecycle(bundle)
