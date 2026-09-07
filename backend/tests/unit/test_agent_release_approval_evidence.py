"""Private synthetic producer/file barrier and independent DB↔WF2 joins."""

import json
import os
from copy import deepcopy

import pytest

from app.agent import release_approval_evidence as subject
from app.agent import release_database as database
from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    canonical_json,
    component_ref,
    read_private,
    write_private,
    write_private_bytes,
)
from app.agent.release_lifecycle import (
    claim_filename,
    lifecycle_lock,
    read_lifecycle,
    terminal_filename,
)
from scripts import record_release_approval_evidence as writer
from scripts import wait_release_approval_evidence as waiter
from scripts.verify_golden_flow import _parse_n8n, _parse_smtp
from tests.unit.test_agent_release import claim, outcome
from tests.unit.test_agent_release_database import engine, setup_bridge  # noqa: F401
from tests.unit.test_agent_release_entry import entry  # noqa: F401


def payload():
    return dict(
        format_version=1,
        executions=[
            dict(
                workflow="WF2",
                action_id=f"action-{i}",
                status="SUCCESS",
                execution_id=str(i),
            )
            for i in (1, 2, 3)
        ],
    )


def directories(root):
    for part in subject.APPROVAL_EXECUTIONS.split("/")[:-1]:
        root = root / part
        root.mkdir(mode=0o700)


@pytest.fixture
def evidence(tmp_path):
    tmp_path.chmod(0o700)
    directories(tmp_path)
    return tmp_path


def put(root, data=None):
    reference = write_private(root, subject.APPROVAL_EXECUTIONS, data or payload())
    write_private_bytes(root, subject.APPROVAL_READY, b"")
    return reference


def test_existing_n8n_format_not_smtp_receipt(evidence):
    reference = put(evidence)
    result = subject.read_approval_evidence(evidence, reference)
    assert _parse_n8n(result.model_dump()) == payload()
    with pytest.raises(ValueError):
        _parse_smtp(result.model_dump())


@pytest.mark.parametrize(
    "fault",
    [
        "short",
        "extra",
        "version_bool",
        "duplicate_action",
        "duplicate_id",
        "wrong_workflow",
        "failed",
        "receipt_id",
        "whitespace_id",
        "secret_field",
    ],
)
def test_malformed_or_wrong_source_not_an_execution_receipt(evidence, fault):
    data = payload()
    row = data["executions"][0]
    if fault == "short":
        data["executions"].pop()
    elif fault == "extra":
        data["executions"].append(deepcopy(row))
    elif fault == "version_bool":
        data["format_version"] = True
    elif fault == "duplicate_action":
        row["action_id"] = "action-2"
    elif fault == "duplicate_id":
        row["execution_id"] = "2"
    elif fault == "wrong_workflow":
        row["workflow"] = "WF3"
    elif fault == "failed":
        row["status"] = "FAILED"
    elif fault == "receipt_id":
        row["receipt_id"] = row.pop("execution_id")
    elif fault == "whitespace_id":
        row["execution_id"] = "id secret"
    else:
        row["password"] = "must-not-be-output"
    with pytest.raises(EvidenceError, match="^APPROVAL_EVIDENCE_INVALID$"):
        subject.read_approval_evidence(evidence, put(evidence, data))


@pytest.mark.parametrize(
    "fault", ["sha", "mode", "symlink", "hardlink", "parent", "path"]
)
def test_private_source_protection(evidence, fault):
    reference = put(evidence)
    path = evidence / subject.APPROVAL_EXECUTIONS
    if fault == "sha":
        path.write_bytes(path.read_bytes() + b" ")
    elif fault == "mode":
        path.chmod(0o644)
    elif fault == "symlink":
        path.rename(path.with_suffix(".moved"))
        path.symlink_to(path.with_suffix(".moved"))
    elif fault == "hardlink":
        os.link(path, path.with_suffix(".linked"))
    elif fault == "parent":
        path.parent.chmod(0o755)
    else:
        reference = Component(
            relative_path="smtp-approval.json", sha256=reference.sha256
        )
    with pytest.raises(EvidenceError):
        subject.read_approval_evidence(evidence, reference)


def test_only_missing_leaf_is_waited_for_then_pinned(evidence):
    now, sleeps, owners = [0.0], [], []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
        put(evidence)

    reference, value = subject.wait_approval_evidence(
        evidence,
        check_owner=lambda: owners.append(1),
        timeout_seconds=3,
        monotonic=lambda: now[0],
        sleep=sleep,
    )
    assert sleeps == [1] and len(owners) == 3
    assert reference == component_ref(evidence, subject.APPROVAL_EXECUTIONS)
    assert value.model_dump() == payload()


def test_partial_source_is_not_read_until_marker_last(evidence):
    path = evidence / subject.APPROVAL_EXECUTIONS
    path.write_bytes(b"{")
    path.chmod(0o600)
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds
        path.write_bytes(canonical_json(payload()))
        write_private_bytes(evidence, subject.APPROVAL_READY, b"")

    _, value = subject.wait_approval_evidence(
        evidence,
        check_owner=lambda: None,
        timeout_seconds=2,
        monotonic=lambda: now[0],
        sleep=sleep,
    )
    assert value.model_dump() == payload() and now[0] == 1


@pytest.mark.parametrize("fault", ["bytes", "permissions", "symlink", "hardlink"])
def test_bad_ready_marker_is_not_a_publication(evidence, fault):
    reference = put(evidence)
    ready = evidence / subject.APPROVAL_READY
    if fault == "bytes":
        ready.write_bytes(b"not-empty")
    elif fault == "permissions":
        ready.chmod(0o644)
    elif fault == "symlink":
        ready.rename(ready.with_suffix(".moved"))
        ready.symlink_to(ready.with_suffix(".moved"))
    else:
        os.link(ready, ready.with_suffix(".linked"))
    with pytest.raises(EvidenceError):
        subject.read_approval_evidence(evidence, reference)


def test_oversized_and_duplicate_key_inputs_fail_closed(evidence):
    reference = put(evidence)
    path = evidence / subject.APPROVAL_EXECUTIONS
    path.write_bytes(b" " * 65536 + canonical_json(payload()))
    with pytest.raises(EvidenceError, match="TOO_LARGE"):
        subject.read_approval_evidence(
            evidence, component_ref(evidence, subject.APPROVAL_EXECUTIONS)
        )
    path.write_bytes(b'{"format_version":1,"format_version":1,"executions":[]}')
    with pytest.raises(EvidenceError, match="JSON_INVALID"):
        subject.read_approval_evidence(
            evidence, component_ref(evidence, subject.APPROVAL_EXECUTIONS)
        )
    assert reference.relative_path == subject.APPROVAL_EXECUTIONS


@pytest.mark.parametrize("fault", ["partial", "parent_missing", "symlink", "owner"])
def test_bad_file_or_owner_never_polled(evidence, fault):
    path = evidence / subject.APPROVAL_EXECUTIONS
    if fault == "partial":
        path.write_bytes(b"{")
        path.chmod(0o600)
    elif fault == "parent_missing":
        path.parent.rmdir()
    elif fault == "symlink":
        path.symlink_to(evidence / "missing")
    if fault in {"partial", "symlink"}:
        write_private_bytes(evidence, subject.APPROVAL_READY, b"")

    def owner():
        if fault == "owner":
            raise EvidenceError("LIFECYCLE_PARENT_REQUIRED")

    with pytest.raises(EvidenceError):
        subject.wait_approval_evidence(
            evidence, check_owner=owner, sleep=lambda _: pytest.fail("must not wait")
        )


@pytest.mark.parametrize("arrive_at_deadline", [False, True])
def test_bounded_wait_timeout_never_accepts_late_file(evidence, arrive_at_deadline):
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds
        if now[0] == 2 and arrive_at_deadline:
            put(evidence)

    with pytest.raises(EvidenceError, match="TIMEOUT"):
        subject.wait_approval_evidence(
            evidence,
            check_owner=lambda: None,
            timeout_seconds=2,
            monotonic=lambda: now[0],
            sleep=sleep,
        )
    assert now[0] == 2


def test_owner_check_cannot_repin_modified_file(evidence):
    put(evidence)
    count = 0

    def owner():
        nonlocal count
        count += 1
        if count == 2:
            path = evidence / subject.APPROVAL_EXECUTIONS
            path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(EvidenceError, match="SHA_MISMATCH"):
        subject.wait_approval_evidence(evidence, check_owner=owner, timeout_seconds=0)


@pytest.mark.parametrize("value", [-1, 301, True, 1.5])
def test_invalid_wait_limit_before_owner_or_file(evidence, value):
    with pytest.raises(EvidenceError, match="WAIT_INVALID"):
        subject.wait_approval_evidence(
            evidence,
            timeout_seconds=value,
            check_owner=lambda: pytest.fail("owner called"),
        )


@pytest.mark.parametrize("values", [[1, 0], [float("nan")], [0, float("inf")]])
def test_clock_invalid_fails_without_wait(evidence, values):
    iterator = iter(values)
    with pytest.raises(EvidenceError, match="CLOCK_INVALID"):
        subject.wait_approval_evidence(
            evidence,
            check_owner=lambda: None,
            monotonic=lambda: next(iterator),
            sleep=lambda _: pytest.fail("wait"),
        )


@pytest.mark.parametrize("fault", ["wrong_action", "swapped_ids", "drift_during_http"])
def test_real_bridge_binds_pairs_and_source_bytes(engine, fault):  # noqa: F811
    server, args = setup_bridge(engine)
    path = engine.evidence_root / subject.APPROVAL_EXECUTIONS
    data = json.loads(path.read_bytes())
    if fault == "wrong_action":
        data["executions"][0]["action_id"] = "action-5"
    elif fault == "swapped_ids":
        one, two = data["executions"][:2]
        one["execution_id"], two["execution_id"] = (
            two["execution_id"],
            one["execution_id"],
        )
    if fault != "drift_during_http":
        path.write_bytes(canonical_json(data))
        args["approval_evidence"] = component_ref(
            engine.evidence_root, subject.APPROVAL_EXECUTIONS
        )
    else:

        def hook(request, payload):
            path.write_bytes(path.read_bytes() + b" ")
            return payload

        server.hook = hook
    codes = dict(
        wrong_action="ACTION_MISMATCH",
        swapped_ids="EXECUTION_MISMATCH",
        drift_during_http="SHA_MISMATCH",
    )
    with server.client() as api, pytest.raises(EvidenceError, match=codes[fault]):
        database.collect_delivery_receipts(engine, api, **args)
    if fault == "wrong_action":
        assert server.requests == []


def test_other_attempt_source_before_db_or_http(engine):  # noqa: F811
    server, args = setup_bridge(engine)
    args["approval_evidence_root"] = engine.evidence_root.with_name("other-attempt")
    with server.client() as api, pytest.raises(EvidenceError, match="ATTEMPT_MISMATCH"):
        database.collect_delivery_receipts(engine, api, **args)
    assert engine.events == [] and server.requests == []


@pytest.fixture
def live_owner(entry, monkeypatch):  # noqa: F811
    root = entry["prepared_path"].parent
    data = claim(read_lifecycle(root), "RESUME_WORKLOAD")
    data["pid"] = os.getppid()
    reference = write_private(root, claim_filename("RESUME_WORKLOAD"), data)
    directories(root.parent)
    for module in (writer, waiter):
        monkeypatch.setattr(module, "owner_identity", lambda pid: ("boot", "start"))
    args = {key: entry[key] for key in ("report_root", "repository", "attempt_id")}
    return root, reference, args


def test_separate_writer_and_borrowed_lock_barrier(live_owner):
    root, claim_ref, args = live_owner
    original = read_lifecycle(root)
    with lifecycle_lock(root) as fd:
        recorded = writer.record(
            **args,
            resume_claim_sha256=claim_ref.sha256,
            executions=[f"action-{i}={i}" for i in (1, 2, 3)],
        )
        result = waiter.barrier(**args, lock_fd=fd, timeout_seconds=0)
        assert result["approval_evidence"] == recorded["approval_evidence"]
        assert result["execution_count"] == 3
        assert result["qualification_authorized"] is False
        assert read_lifecycle(root) == original
        with pytest.raises(EvidenceError, match="LOCK_BUSY"), lifecycle_lock(root):
            pass
        with pytest.raises(EvidenceError, match="ARTIFACT_EXISTS"):
            writer.record(
                **args,
                resume_claim_sha256=claim_ref.sha256,
                executions=[f"action-{i}={i}" for i in (1, 2, 3)],
            )


def test_failed_producer_preserves_file_without_ready_marker(live_owner, monkeypatch):
    root, claim_ref, args = live_owner
    original = writer.write_private

    def changed(*a, **k):
        value = original(*a, **k)
        path = root / claim_filename("RESUME_WORKLOAD")
        path.write_bytes(path.read_bytes() + b" ")
        return value

    monkeypatch.setattr(writer, "write_private", changed)
    with pytest.raises(EvidenceError, match="CLAIM_MISMATCH"):
        writer.record(
            **args,
            resume_claim_sha256=claim_ref.sha256,
            executions=[f"action-{i}={i}" for i in (1, 2, 3)],
        )
    assert (root.parent / subject.APPROVAL_EXECUTIONS).exists()
    assert not (root.parent / subject.APPROVAL_READY).exists()


@pytest.mark.parametrize("fault", ["claim_sha", "dead_owner", "held", "parent_pid"])
def test_cli_owner_checks_before_file_acceptance(live_owner, monkeypatch, fault):
    root, claim_ref, args = live_owner
    if fault == "dead_owner":
        for module in (writer, waiter):
            monkeypatch.setattr(module, "owner_identity", lambda pid: ("boot", None))
    elif fault == "held":
        write_private(
            root, terminal_filename("RESUME_WORKLOAD"), outcome(read_lifecycle(root))
        )
    elif fault == "parent_pid":
        raw = json.loads(read_private(root, claim_filename("RESUME_WORKLOAD")))
        raw["pid"] += 1
        path = root / claim_filename("RESUME_WORKLOAD")
        path.write_bytes(canonical_json(raw))
        claim_ref = component_ref(root, claim_filename("RESUME_WORKLOAD"))
    if fault != "parent_pid":
        with pytest.raises(EvidenceError):
            writer.record(
                **args,
                resume_claim_sha256="f" * 64
                if fault == "claim_sha"
                else claim_ref.sha256,
                executions=[f"action-{i}={i}" for i in (1, 2, 3)],
            )
        assert not (root.parent / subject.APPROVAL_EXECUTIONS).exists()
    if fault != "claim_sha":
        with lifecycle_lock(root) as fd, pytest.raises(EvidenceError):
            waiter.barrier(**args, lock_fd=fd, timeout_seconds=0)


@pytest.mark.parametrize("module", [writer, waiter])
def test_cli_failure_does_not_echo_untrusted_arguments(module, capsys):
    assert module.main(["--secret=do-not-print"]) == 1
    output = capsys.readouterr()
    assert "do-not-print" not in output.out + output.err
    assert json.loads(output.out)["status"] == "FAIL"


@pytest.mark.parametrize("module", [writer, waiter])
def test_cli_wrong_repository_does_not_create_evidence(live_owner, module, tmp_path):
    root, claim_ref, args = live_owner
    args["repository"] = tmp_path
    with pytest.raises(EvidenceError, match="REPOSITORY_INVALID"):
        if module is writer:
            writer.record(
                **args,
                resume_claim_sha256=claim_ref.sha256,
                executions=[f"action-{i}={i}" for i in (1, 2, 3)],
            )
        else:
            module.barrier(**args, lock_fd=-1, timeout_seconds=0)
    assert not (root.parent / subject.APPROVAL_EXECUTIONS).exists()


@pytest.mark.parametrize("module,function", [(writer, "record"), (waiter, "barrier")])
def test_cli_interruption_is_sanitized(module, function, capsys, monkeypatch):
    def interrupted(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(module, function, interrupted)
    args = [
        "--repository",
        "/repo",
        "--report-root",
        "/reports",
        "--attempt-id",
        "synthetic",
    ]
    args += (
        ["--resume-claim-sha256", "a" * 64, "--execution", "action=1"]
        if module is writer
        else ["--lifecycle-lock-fd", "9"]
    )
    assert module.main(args) == 130
    assert (
        json.loads(capsys.readouterr().out)["code"] == "APPROVAL_EVIDENCE_INTERRUPTED"
    )
