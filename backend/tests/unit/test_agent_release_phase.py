"""Borrowed-lock Stage2 phases using real artifacts; never run external work."""

import os

import pytest

from app.agent import release_lifecycle
from app.agent import release_phase as subject
from app.agent.release_artifacts import (
    EvidenceError,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_lifecycle import (
    classify_state,
    lifecycle_lock,
    read_lifecycle,
)
from app.agent.release_prepared import RUNTIME_BINDING_FIELDS
from scripts.grant_smtp_send import issue_grant
from tests.unit.test_agent_release import (
    EXPIRES,
    LATER,
    S,
    grant_args,
    prepared_payload,
)
from tests.unit.test_agent_release_aggregate import bundle  # noqa: F401
from tests.unit.test_agent_release_evidence import delivery  # noqa: F401
from tests.unit.test_agent_release_round import template  # noqa: F401


@pytest.fixture(autouse=True)
def process_identity(monkeypatch):
    # OS identity is tested in release_lifecycle tests. Sandboxed macOS denies
    # sysctl/ps; inject only this leaf, not classification or lock ownership.
    monkeypatch.setattr(subject, "owner_identity", lambda pid: ("boot", "start"))
    monkeypatch.setattr(
        release_lifecycle, "owner_identity", lambda pid: ("boot", "start")
    )


@pytest.fixture
def prepared(tmp_path):
    tmp_path.chmod(0o700)
    root = tmp_path / "robustness"
    root.mkdir(mode=0o700)
    write_private(root, "prepared-attempt.json", prepared_payload())
    log = tmp_path / "stage2-log.jsonl"
    log.write_bytes(b"{}\n")
    log.chmod(0o600)
    return root


def begin(root, fd, mode="RESUME_WORKLOAD", **kwargs):
    return subject.begin_phase(
        root=root,
        mode=mode,
        lock_fd=fd,
        owner_pid=os.getppid(),
        clock=kwargs.pop("clock", lambda: LATER),
        grant_path=root / "smtp-approval-grant.json",
        read_runtime=kwargs.pop(
            "read_runtime",
            lambda p: ({k: p.model_dump()[k] for k in RUNTIME_BINDING_FIELDS}, S),
        ),
        **kwargs,
    )


def finish(root, fd, result, **kwargs):
    return subject.finish_phase(
        root=root,
        lock_fd=fd,
        owner_pid=os.getppid(),
        phase=result["phase"],
        issued_by=result["issued_by"],
        now=LATER,
        reason=result["reason_code"],
        primary=result["primary_failure_code"],
        cleanup_result=kwargs.pop("cleanup_result", "OK"),
        restore_result=kwargs.pop("restore_result", "OK"),
        **kwargs,
    )[1]


@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing", "EXTERNAL_EFFECT_APPROVAL_MISSING"),
        ("malformed", "EXTERNAL_EFFECT_APPROVAL_MISSING"),
        ("expired", "PREPARED_ATTEMPT_EXPIRED"),
        ("drift", "PREPARED_RUNTIME_DRIFT"),
        ("late_expiry", "PREPARED_ATTEMPT_EXPIRED"),
        ("log_drift", "PREPARED_RUNTIME_DRIFT"),
    ],
)
def test_denied_resume_claims_abort_before_workload(prepared, case, reason):
    if case not in {"missing", "malformed"}:
        issue_grant(prepared / "prepared-attempt.json", **grant_args())
    elif case == "malformed":
        write_private(prepared, "smtp-approval-grant.json", {"bad": True})
    if case == "log_drift":
        (prepared.parent / "stage2-log.jsonl").write_bytes(b"changed\n")
    times = iter([LATER, EXPIRES])
    options = {}
    if case == "expired":
        options["clock"] = lambda: EXPIRES
    if case == "late_expiry":
        options["clock"] = lambda: next(times)
    if case == "drift":
        options["read_runtime"] = lambda p: ({}, S)
    with lifecycle_lock(prepared) as fd:
        result = begin(prepared, fd, **options)
        assert result["phase"] == "ABORT"
        assert result["reason_code"] == reason
        assert result["workload_authorized"] is False
        assert not (prepared / "lifecycle-claim.resume_workload.json").exists()
        if case not in {"expired", "late_expiry"}:
            terminal = finish(prepared, fd, result)
            assert terminal.outcome == "ABORTED"
            assert terminal.external_effects.email_sent == 0
            assert classify_state(read_lifecycle(prepared)) == "TERMINAL"


def test_valid_resume_claim_is_single_use(prepared):
    issue_grant(prepared / "prepared-attempt.json", **grant_args())
    with lifecycle_lock(prepared) as fd:
        result = begin(prepared, fd)
        assert result["workload_authorized"] is True
        before = read_lifecycle(prepared)
        with pytest.raises(EvidenceError, match="TRANSITION_INVALID"):
            begin(prepared, fd)
        assert read_lifecycle(prepared) == before


def test_running_owner_cannot_be_recovered(prepared):
    with lifecycle_lock(prepared) as fd:
        begin(prepared, fd, "ABORT")
        before = read_lifecycle(prepared)
        with pytest.raises(EvidenceError):
            begin(prepared, fd, "RECOVER")
        assert read_lifecycle(prepared) == before


def test_recovery_reuses_claim_and_does_not_invent_zero_effects(prepared, monkeypatch):
    with lifecycle_lock(prepared) as fd:
        begin(prepared, fd, "ABORT")
        original = read_private(prepared, "lifecycle-claim.abort.json")
        monkeypatch.setattr(subject, "assert_stale_owner", lambda c: None)
        result = begin(prepared, fd, "RECOVER")
        assert not result["workload_authorized"]
        terminal = finish(prepared, fd, result)
        assert terminal.outcome == "FAILED"
        assert terminal.failure_code == "STALE_CLAIM_RECOVERED"
        assert terminal.external_effects.state == "INDETERMINATE"
        assert terminal.external_effects.email_sent is None
        assert read_private(prepared, "lifecycle-claim.abort.json") == original


@pytest.mark.parametrize(
    "cleanup,restore,code",
    [
        ("FAILED", "OK", "CLEANUP_FAILED"),
        ("OK", "FAILED", "RESTORE_FAILED"),
        ("FAILED", "FAILED", "RESTORE_FAILED"),
    ],
)
def test_abort_failure_precedence(prepared, cleanup, restore, code):
    with lifecycle_lock(prepared) as fd:
        result = begin(prepared, fd, "ABORT")
        value = finish(
            prepared, fd, result, cleanup_result=cleanup, restore_result=restore
        )
        assert value.outcome == "FAILED" and value.failure_code == code


@pytest.mark.parametrize("publish_ok", [True, False])
def test_publish_after_expiry_does_not_revalidate_grant(bundle, publish_ok):  # noqa: F811
    root = bundle["root"]
    # Remove only synthetic fixture terminals, never operational files.
    (root / "round1-completion.json").unlink()
    (root / "lifecycle-claim.publish.json").unlink()
    with lifecycle_lock(root) as fd:
        result = begin(
            root,
            fd,
            "PUBLISH",
            clock=lambda: EXPIRES,
            publish_precondition=lambda p: publish_ok,
            read_runtime=lambda p: pytest.fail("resume checks in publish"),
        )
        value = subject.finish_phase(
            root=root,
            lock_fd=fd,
            owner_pid=os.getppid(),
            phase="PUBLISH",
            issued_by="PUBLISH",
            now="2026-09-05T02:01:00Z",
            cleanup_result="OK",
            restore_result="OK",
            primary=result["primary_failure_code"],
            published_root=bundle["published_root"],
        )[1]
        assert value.final_status == ("PASS" if publish_ok else "FAIL")
        assert value.external_effects.email_sent == 7
        assert value.failure_code == (
            None if publish_ok else "PUBLISH_PRECONDITION_FAILED"
        )
        assert classify_state(read_lifecycle(root)) == "TERMINAL"


def test_caller_cannot_forge_positive_held_without_round(prepared):
    issue_grant(prepared / "prepared-attempt.json", **grant_args())
    with lifecycle_lock(prepared) as fd:
        result = begin(prepared, fd)
        with pytest.raises(EvidenceError):
            finish(
                prepared,
                fd,
                result,
                held=True,
                cleanup_result="NOT_ATTEMPTED",
                restore_result="NOT_ATTEMPTED",
            )
        assert classify_state(read_lifecycle(prepared)) == "UNRESOLVED"


@pytest.mark.parametrize("field", ["reset_attempt_id", "preflight_output_sha256"])
def test_publish_cannot_rebind_round_to_another_prepared_context(bundle, field):  # noqa: F811
    from tests.unit.test_agent_release_evidence import replace

    root = bundle["root"]
    (root / "round1-completion.json").unlink()
    (root / "lifecycle-claim.publish.json").unlink()
    value = parse_json(read_private(root, "round1.json"))
    value[field] = (
        "20260905T020000Z-aaaaaaaaaaaa" if field == "reset_attempt_id" else "f" * 64
    )
    replace(root, "round1.json", value)
    with lifecycle_lock(root) as fd:
        result = begin(root, fd, "PUBLISH", publish_precondition=lambda p: True)
        with pytest.raises(EvidenceError, match="^ROUND_PREPARED_BINDING_MISMATCH$"):
            finish(root, fd, result, published_root=bundle["published_root"])
        assert not (root / "round1-completion.json").exists()
