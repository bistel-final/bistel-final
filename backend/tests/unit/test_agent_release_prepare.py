"""Private synthetic prepared issuance with real A validators and temporary Git."""

import json
import os
import stat
import subprocess
import sys

import pytest

from app.agent import release_prepare as m
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    component_ref,
    digest,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_budget import budget_policy, profile_fields
from app.agent.release_lifecycle import classify_state, lifecycle_lock, read_lifecycle
from app.agent.release_prepared import PreparedAttempt, validate_log_prefix
from app.agent.u10_integrity import verify_preflight_integrity
from app.agent.u10_preflight_report import preflight_report
from scripts.grant_smtp_send import issue_grant
from tests.unit.test_agent_release import prepared_payload
from tests.unit.test_agent_release_runtime import IDS, Docker
from tests.unit.test_agent_u10_integrity import setup
from tests.unit.test_agent_u10_revision import git

S = "1" * 64
RESET_ID = "2" * 32
CAPTURED = "2026-09-05T01:02:06Z"
NOW = "2026-09-05T01:03:00Z"


@pytest.fixture
def bundle(tmp_path, monkeypatch, request):
    args, _, _, revision = setup(
        tmp_path, monkeypatch, negative=getattr(request, "param", False)
    )
    pf = verify_preflight_integrity(**args)
    report = tmp_path / "reports"
    report.mkdir(mode=0o700)
    (report / "cm-5.2").mkdir(mode=0o700)
    attempt_id = "20260905T010000Z-" + revision[:12]
    attempt = report / "cm-5.2" / attempt_id
    attempt.mkdir(mode=0o700)
    root = attempt / "robustness"
    root.mkdir(mode=0o700)
    fake = Docker(report)
    fake.fill()
    bindings = pf.deployment.image_bindings
    containers = {}
    for role, cid in IDS.items():
        c = fake.payloads[cid]
        c.update(
            image_id=bindings.images[role].image_id,
            running=True,
            status="running",
            started_at=bindings.containers[role].started_at,
        )
        containers[role] = c
    prepared = prepared_payload()
    context = {
        k: v for k, v in prepared.items() if k.startswith("prev_") or k == "running_rev"
    }
    addresses = ["Team@example.invalid"]
    capture = {
        "schema_version": "level3-preparation-capture-v1",
        "preflight": pf.model_dump(),
        "runtime": {
            "revision": revision,
            "phase": "running",
            "images": {r: v.model_dump() for r, v in bindings.images.items()},
            "containers": containers,
        },
        "db_identities": {r: prepared["db_identity"] for r in ("backend", "runner")},
        "recipients": {r: addresses for r in ("backend", "runner")},
        "smtp_config": {
            "n8n_workflow_versions": {"wf2-test": "version-test"},
            "smtp_host": "smtp.example.invalid",
            "smtp_port": 587,
            "smtp_from": "Sender@example.invalid",
            "recipient_allowlist": addresses,
            "wf2_callback_endpoint": "http://backend:8000/internal/actions/callback",
        },
        "n8n_evidence_probe": prepared["n8n_evidence_probe"],
        "previous": context,
        "captured_at": CAPTURED,
    }
    reset = {
        "artifact_type": "e2e_reset_final",
        "format_version": 1,
        "task_id": "V5-CM-4.7",
        "dataset_epoch": m.EPOCH,
        "run_id": RESET_ID,
        "recorded_at": "2026-09-05T00:59:00+00:00",
        "status": "PASS",
        "reason": "PASS",
        "pre_receipt_sha256": S,
        "applied_receipt_sha256": S,
        "post_receipt_sha256": S,
        "observer_before_sha256": {"kosa_agent": S, "kosa_text2sql": S},
        "observer_after_sha256": {"kosa_agent": S, "kosa_text2sql": S},
        "connector_ledger": [],
    }
    baseline = {
        "artifact_type": "cm52_public_database_observer",
        "format_version": 1,
        "dataset_epoch": m.EPOCH,
        "recorded_at": "2026-09-05T01:00:00+00:00",
        "immutable": {"kosa_agent": {"sha256": S}, "kosa_text2sql": {"sha256": S}},
        "strict_kosa_agent": {"sha256": S},
        "text2sql_log": {"row_count": 0, "max_id": 0, "sequence_last_value": 0},
    }
    capref = write_private(attempt, m.CAPTURE, capture)
    p = preflight_report(
        pf,
        profile=pf.profile,
        phase=pf.phase,
        checked_at=pf.checked_at,
        failed_checks=[],
    )
    pfref = write_private(attempt, m.PREFLIGHT, p)
    write_private(attempt, m.BASELINE, baseline)
    write_private(
        attempt, m.LOG, {"step": "3b", "status": "PASS", "detail": "identity-readiness"}
    )
    reset_ref = write_private(report, "reset-final.json", reset)
    kwargs = dict(
        report_root=report,
        mounted_report_root=report,
        repository=args["repository"],
        attempt_id=attempt_id,
        revision=revision,
        image_ids={r: v.image_id for r, v in bindings.images.items()},
        capture_sha256=capref.sha256,
        preflight_sha256=pfref.sha256,
        reset_receipt=reset_ref,
        reset_run_id=RESET_ID,
        clock=lambda: NOW,
    )
    return kwargs, attempt, root, capture


def replace(path, value):
    path.write_bytes(canonical_json(value) + b"\n")


def reseal_capture(bundle, mutate):
    args, attempt, _, _ = bundle
    capture = parse_json(read_private(attempt, m.CAPTURE))
    mutate(capture)
    replace(attempt / m.CAPTURE, capture)
    args["capture_sha256"] = component_ref(attempt, m.CAPTURE).sha256


def test_wide_readback_is_preserved_by_prepared_writer(bundle):
    def bind(capture):
        for value in capture["preflight"]["deployment"]["runtime"][
            "readbacks"
        ].values():
            value.update(
                **profile_fields("PRODUCTION_WIDE_V1"),
                budget_policy=budget_policy("PRODUCTION_WIDE_V1"),
            )

    reseal_capture(bundle, bind)
    args, _, root, _ = bundle
    m.issue_prepared(**args)
    prepared = PreparedAttempt.model_validate(
        parse_json(read_private(root, m.PREPARED))
    )
    assert prepared.effective_env.investigation_budget_profile == "PRODUCTION_WIDE_V1"
    assert prepared.effective_env.level3_total == 26


def test_issue_derives_hashes_ttl_context_and_can_feed_manual_grant(bundle):
    args, attempt, root, capture = bundle
    before = {p: p.read_bytes() for p in attempt.iterdir() if p.is_file()}
    report = m.issue_prepared(**args)
    assert set(p.name for p in root.iterdir()) == {m.PREPARED, ".lifecycle.lock"}
    raw = read_private(root, m.PREPARED)
    prepared = PreparedAttempt.model_validate(parse_json(raw))
    assert report["prepared_sha256"] == digest(raw)
    assert (
        prepared.prepared_at == CAPTURED
        and prepared.expires_at == "2026-09-05T01:32:06Z"
    )
    assert prepared.observer_baseline_sha256 == digest(before[attempt / m.BASELINE])
    assert prepared.reset_final_receipt_sha256 == args["reset_receipt"].sha256
    assert prepared.e2e_level3_preflight_output_sha256 == args["preflight_sha256"]
    assert prepared.approved_config_digest_allowlist == [
        m.config_digest(capture["smtp_config"])
    ]
    assert prepared.running_rev == capture["previous"]["running_rev"]
    assert stat.S_IMODE((root / m.PREPARED).stat().st_mode) == 0o600
    assert classify_state(read_lifecycle(root)) == "PREPARED"
    validate_log_prefix(prepared, attempt)
    assert all(p.read_bytes() == value for p, value in before.items())
    assert report["warning"] == "production is DOWN"
    assert report["smtp_send_authorized"] is report["deployment_authorized"] is False
    assert report["next_modes"] == ["--resume-workload", "--abort-prepared"]
    assert "Team@" not in json.dumps(report) and "smtp.example" not in json.dumps(
        report
    )
    grant = issue_grant(
        root / m.PREPARED,
        approver="방대혁",
        approval_reference="synthetic-test-only",
        confirmation=(
            f"SMTP_SEND_GRANT {args['attempt_id']} "
            f"{prepared.recipient.canonical_hash} 7"
        ),
        approved_at=NOW,
    )
    assert grant["grant_type"] == "SMTP_SEND_GRANT"


def test_repeat_does_not_overwrite_or_renew_lease(bundle):
    args, _, root, _ = bundle
    m.issue_prepared(**args)
    before = (root / m.PREPARED).read_bytes()
    with pytest.raises(EvidenceError, match="ALREADY_ISSUED"):
        m.issue_prepared(**args)
    assert (root / m.PREPARED).read_bytes() == before


@pytest.mark.parametrize("bundle", [True], indirect=True)
def test_negative_u10_research_verdict_is_not_a_preparation_gate(bundle):
    args, _, root, capture = bundle
    assert "NOT_ESTABLISHED" in capture["preflight"]["evaluation"]["agent_verdict"]
    assert m.issue_prepared(**args)["status"] == "PREPARED"
    assert (root / m.PREPARED).exists()


@pytest.mark.parametrize("part", ["cm-5.2", "attempt", "robustness"])
def test_intermediate_symlink_cannot_redirect_lock_or_prepared(bundle, tmp_path, part):
    args, attempt, root, _ = bundle
    selected = (
        args["report_root"] / "cm-5.2"
        if part == "cm-5.2"
        else attempt
        if part == "attempt"
        else root
    )
    moved = tmp_path / "moved"
    selected.rename(moved)
    selected.symlink_to(moved, target_is_directory=True)
    with pytest.raises(EvidenceError):
        m.issue_prepared(**args)
    assert not list(moved.rglob(m.PREPARED))
    assert not list(moved.rglob(".lifecycle.lock"))


def cli_command(args):
    argv = []
    for flag, value in (
        ("report-root", args["report_root"]),
        ("mounted-report-root", args["mounted_report_root"]),
        ("repository", args["repository"]),
        ("attempt-id", args["attempt_id"]),
        ("expect-revision", args["revision"]),
        ("capture-sha256", args["capture_sha256"]),
        ("preflight-sha256", args["preflight_sha256"]),
        ("reset-receipt", args["reset_receipt"].relative_path),
        ("reset-receipt-sha256", args["reset_receipt"].sha256),
        ("reset-run-id", args["reset_run_id"]),
    ):
        argv += ["--" + flag, str(value)]
    for role, value in args["image_ids"].items():
        argv += ["--image-id", f"{role}={value}"]
    # Synthetic clock only in this test interpreter; there is no CLI time bypass.
    code = """
from datetime import datetime, UTC
from app.agent import release_prepare as m
class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 5, 1, 3, tzinfo=UTC)
m.datetime = Clock
from scripts.emit_prepared_attempt import main
raise SystemExit(main())
"""
    return [sys.executable, "-c", code, *argv]


def test_real_cli_and_two_process_no_clobber_race(bundle):
    args, _, root, _ = bundle
    processes = [
        subprocess.Popen(
            cli_command(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for _ in range(2)
    ]
    results = [p.communicate(timeout=30) for p in processes]
    assert sorted(p.returncode for p in processes) == [0, 1]
    reports = [json.loads(out) for out, err in results if not err]
    assert len(reports) == 2
    assert sorted(r["status"] for r in reports) == ["FAIL", "PREPARED"]
    assert next(r for r in reports if r["status"] == "FAIL")["code"] in {
        "LIFECYCLE_LOCK_BUSY",
        "PREPARATION_ALREADY_ISSUED",
    }
    assert all(
        "Team@" not in out + err and "smtp.example" not in out + err
        for out, err in results
    )
    assert set(p.name for p in root.iterdir()) == {m.PREPARED, ".lifecycle.lock"}


@pytest.mark.parametrize(
    "name",
    [
        "smtp-approval-grant.json",
        "lifecycle-claim.abort.json",
        "MANIFEST.sha256",
        "unknown.json",
    ],
)
def test_preexisting_bundle_state_cannot_be_adopted(bundle, name):
    args, _, root, _ = bundle
    write_private(root, name, {})
    with pytest.raises(EvidenceError, match="ALREADY_ISSUED"):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


@pytest.mark.parametrize("pin", ["capture_sha256", "preflight_sha256", "reset_run_id"])
def test_independent_input_pin_mismatch(bundle, pin):
    args, _, root, _ = bundle
    args[pin] = "f" * (32 if pin == "reset_run_id" else 64)
    with pytest.raises(ValueError):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


@pytest.mark.parametrize("role", ["backend", "frontend", "runner"])
def test_independent_image_pin_mismatch(bundle, role):
    args, _, root, _ = bundle
    args["image_ids"][role] = "sha256:" + "f" * 64
    with pytest.raises(EvidenceError, match="RUNTIME_BINDING"):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


@pytest.mark.parametrize(
    "case",
    [
        "db",
        "recipient",
        "config_recipient",
        "config_secret",
        "probe",
        "stopped",
        "container_image",
        "budget",
        "autonomy",
        "ack",
        "preflight_profile",
        "preflight_head",
        "capture_time",
        "restore_null",
        "readiness",
    ],
)
def test_resealed_capture_semantic_mismatch(bundle, case):
    def mutate(c):
        if case == "db":
            c["db_identities"]["runner"]["system_identifier"] = "67890"
        elif case == "recipient":
            c["recipients"]["runner"] = ["Other@example.invalid"]
        elif case == "config_recipient":
            c["smtp_config"]["recipient_allowlist"] = ["Other@example.invalid"]
        elif case == "config_secret":
            c["smtp_config"]["password"] = "synthetic-secret"
        elif case == "probe":
            c["n8n_evidence_probe"]["returns_recipient"] = False
        elif case == "stopped":
            c["runtime"]["containers"]["runner"]["running"] = False
        elif case == "container_image":
            c["runtime"]["containers"]["runner"]["image_id"] = "sha256:" + "f" * 64
        elif case in ("budget", "autonomy", "ack"):
            r = c["preflight"]["deployment"]["runtime"]["readbacks"]["runner"]
            if case == "budget":
                r["budget_policy"]["level3_total"] = 12
            elif case == "autonomy":
                r["autonomy_level"] = 2
            else:
                r["demo_ack"] = "old-ack"
        elif case == "preflight_profile":
            c["preflight"]["profile"] = "production_level2"
        elif case == "preflight_head":
            c["preflight"]["head"] = "f" * 40
        elif case == "capture_time":
            c["captured_at"] = "2026-09-05T00:30:00Z"
        elif case == "readiness":
            ready = c["preflight"]["deployment"]["readiness"]["backend_readiness"]
            ready["status"] = "NOT_READY"
            ready["checks"]["n8n"].update(
                status="FAIL", reason_code="CONTRACT_MISMATCH"
            )
        else:
            c["previous"]["prev_fault_sha256"] = S

    reseal_capture(bundle, mutate)
    args, _, root, _ = bundle
    with pytest.raises(ValueError):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


@pytest.mark.parametrize(
    "now", ["2026-09-05T01:32:06Z", "2026-09-05T01:32:07Z", "2026-09-05T01:02:05Z"]
)
def test_stale_transport_does_not_gain_new_thirty_minutes(bundle, now):
    args, _, root, _ = bundle
    args["clock"] = lambda: now
    with pytest.raises(EvidenceError, match="EXPIRED"):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


@pytest.mark.parametrize("step", ["4", "5d", "hold", "cleanup"])
def test_workload_or_hold_log_cannot_be_prepared(bundle, step):
    args, attempt, root, _ = bundle
    with (attempt / m.LOG).open("ab") as stream:
        stream.write(
            canonical_json({"step": step, "status": "PASS", "detail": ""}) + b"\n"
        )
    with pytest.raises(EvidenceError, match="LOG_INVALID"):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


@pytest.mark.parametrize(
    "case",
    [
        "reset_failed",
        "reset_epoch",
        "reset_observer",
        "baseline_epoch",
        "baseline_counter",
        "baseline_time",
    ],
)
def test_reset_and_baseline_envelope_rejection(bundle, case):
    args, attempt, root, _ = bundle
    path = (
        args["report_root"] / "reset-final.json"
        if case.startswith("reset")
        else attempt / m.BASELINE
    )
    value = parse_json(path.read_bytes())
    if case == "reset_failed":
        value["status"] = "APPLIED_BLOCKED"
    elif case.endswith("epoch"):
        value["dataset_epoch"] = "old"
    elif case == "reset_observer":
        value["observer_after_sha256"]["kosa_agent"] = "f" * 64
    elif case == "baseline_counter":
        value["text2sql_log"]["max_id"] = True
    else:
        value["recorded_at"] = "2026-09-05T00:00:00Z"
    replace(path, value)
    args["reset_receipt"] = component_ref(args["report_root"], "reset-final.json")
    with pytest.raises(ValueError):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


@pytest.mark.parametrize("tampered", [False, True])
def test_bound_restore_bytes_are_required_and_preserved(bundle, tampered):
    args, _, root, _ = bundle
    previous_id = "20260904T000000Z-" + "b" * 12
    previous_root = args["report_root"] / "cm-5.2" / previous_id
    previous_root.mkdir(mode=0o700)
    refs = {
        name: write_private(previous_root, name, {"synthetic": name})
        for name in ("fault-5class.json", "golden-flow.json")
    }

    def mutate(c):
        c["previous"].update(
            prev_state="bound",
            prev_attempt=previous_id,
            prev_rev="b" * 40,
            prev_fault_path=f"/reports/cm-5.2/{previous_id}/fault-5class.json",
            prev_golden_path=f"/reports/cm-5.2/{previous_id}/golden-flow.json",
            prev_fault_sha256=refs["fault-5class.json"].sha256,
            prev_golden_sha256=refs["golden-flow.json"].sha256,
        )

    reseal_capture(bundle, mutate)
    if tampered:
        replace(previous_root / "fault-5class.json", {"changed": True})
        with pytest.raises(EvidenceError, match="RESTORE_BINDING_INVALID"):
            m.issue_prepared(**args)
        assert not (root / m.PREPARED).exists()
        return
    m.issue_prepared(**args)
    prepared = PreparedAttempt.model_validate(
        parse_json(read_private(root, m.PREPARED))
    )
    assert prepared.prev_state == "bound" and prepared.prev_attempt == previous_id
    assert all(component_ref(previous_root, name) == ref for name, ref in refs.items())


def test_resealed_preflight_output_must_equal_a_observation(bundle):
    args, attempt, root, _ = bundle
    value = parse_json(read_private(attempt, m.PREFLIGHT))
    value["allowed_actions"]["production_level3"] = True
    replace(attempt / m.PREFLIGHT, value)
    args["preflight_sha256"] = component_ref(attempt, m.PREFLIGHT).sha256
    with pytest.raises(EvidenceError, match="PREFLIGHT_OUTPUT_INVALID"):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


def test_log_drift_during_emission_blocks_publication(bundle):
    args, attempt, root, _ = bundle

    def clock():
        with (attempt / m.LOG).open("ab") as stream:
            stream.write(b"\n")
        return NOW

    args["clock"] = clock
    with pytest.raises(EvidenceError, match="INPUT_DRIFT"):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


def test_lock_race_loser_does_not_issue(bundle):
    args, _, root, _ = bundle
    with lifecycle_lock(root):
        with pytest.raises(EvidenceError, match="LOCK_BUSY"):
            m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


@pytest.mark.parametrize("case", ["dirty", "branch"])
def test_local_clean_main_is_required_without_mutating_repository(bundle, case):
    args, _, root, _ = bundle
    if case == "dirty":
        (args["repository"] / "unexpected.txt").write_text("synthetic")
    else:
        git(args["repository"], "switch", "-c", "test-branch")
    with pytest.raises(EvidenceError, match="U10_(WORKTREE_NOT_CLEAN|MAIN_REQUIRED)"):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


@pytest.mark.parametrize("mode", [0o644, 0o400])
def test_private_capture_mode_required(bundle, mode):
    args, attempt, root, _ = bundle
    (attempt / m.CAPTURE).chmod(mode)
    with pytest.raises(EvidenceError):
        m.issue_prepared(**args)
    assert not (root / m.PREPARED).exists()


def test_cli_errors_do_not_echo_private_values():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/emit_prepared_attempt.py",
            "--password",
            "synthetic-secret@example.invalid",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["smtp_send_authorized"] is False
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert not result.stderr


def test_import_does_not_start_docker_network_or_runtime():
    code = """
import sys
def audit(event, args):
    if event.startswith(('socket.', 'subprocess.')):
        raise RuntimeError(event)
sys.addaudithook(audit)
import app.agent.release_prepare
assert 'app.agent.runtime' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
