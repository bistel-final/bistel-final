"""Real Bash owner/FD and real v2 claim/terminal/seal, synthetic external IO only."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.release_artifacts import (
    EvidenceError,
    component_ref,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_lifecycle import classify_state, lifecycle_lock, read_lifecycle
from app.agent.release_prepared import parse_prepared
from app.agent.release_seal import verify_seal
from tests.unit.test_agent_release import AT, ATTEMPT, REV
from tests.unit.test_agent_release_aggregate import bundle  # noqa: F401
from tests.unit.test_agent_release_bundle_v2 import mock_bundle  # noqa: F401
from tests.unit.test_agent_release_evidence import delivery  # noqa: F401
from tests.unit.test_agent_release_mock import evidence as mock_evidence  # noqa: F401
from tests.unit.test_agent_release_round import template  # noqa: F401

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def shell_v2(mock_bundle, tmp_path):  # noqa: F811
    report = tmp_path / "reports"
    report.mkdir(mode=0o700)
    (report / "cm-5.2").mkdir(mode=0o700)
    a = report / "cm-5.2" / ATTEMPT
    mock_bundle["published_root"].rename(a)
    root = a / "robustness"
    for name in (
        "lifecycle-claim.resume_workload.json",
        "lifecycle-outcome.resume_workload.json",
        "lifecycle-claim.publish.json",
        "round1-completion.json",
    ):
        (root / name).unlink()
    events = tmp_path / "events.jsonl"
    wrapper = tmp_path / "host-python"
    wrapper.write_text(f"""#!{sys.executable}
import json, os, sys, runpy
from pathlib import Path
sys.path.insert(0, {str(REPO / 'backend')!r})
from app.agent import release_lifecycle as life, release_phase as phase
from app.agent import release_stage2_workload as work, release_stage2_publish as pub
from app.agent import release_stage2_recovery as recovery
from app.agent import release_stage2_inputs as inputs
from app.agent import release_retention as retention
life.owner_identity=phase.owner_identity=lambda pid: ('boot', 'start')
from app.agent.release_prepared import RUNTIME_BINDING_FIELDS, parse_prepared
from app.agent.release_artifacts import read_private, parse_json, EvidenceError
script=sys.argv.pop(1)
def event(name):
    with open(os.environ['TEST_EVENTS'], 'a') as f:
        f.write(json.dumps(dict(op=name, parent=os.getppid()))+'\\n')
    if name in os.environ.get('TEST_FAIL', '').split(','):
        raise EvidenceError('STAGE2_COMMAND_FAILED')
def bind(**kw):
    event('binding')
    a=kw['report_root'] / 'cm-5.2' / kw['attempt_id']
    p=parse_prepared(parse_json(read_private(a/'robustness','prepared-attempt.json')))
    observed={{k:p.model_dump()[k] for k in RUNTIME_BINDING_FIELDS}}
    return observed, p.approved_config_digest_allowlist[0]
def precondition(**kw):
    event('precondition')
    return True
def publish(**kw):
    event('publish')
    return dict(status='PUBLICATIONS_VERIFIED', post_freeze_callbacks=0)
def cleanup(p):
    event('cleanup')
    return dict(result='OK')
def restore(**kw):
    is_new=kw['prepared'].prev_attempt == {ATTEMPT!r}
    event('restore-new' if is_new else 'restore-old')
    return dict(result='OK')
work.live_binding=bind
work.execute_workload=lambda **kw: (event('execute') or dict(status='BATCH_CAPTURED'))
work.collect_workload=lambda **kw: (event('collect') or dict(status='ROUND_FROZEN'))
pub.precondition=precondition
pub.publish_evidence=publish
recovery.cleanup_e2e=cleanup
recovery.restore_level2=restore
inputs.preflight_arguments=lambda _: ['synthetic']
retention.verify_restored=lambda a: event('retention')
# Runtime/source/golden collectors have independent real-data projection tests.
# Here pre-recorded immutable sources test real terminal/seal joining and ownership.
phase.validate_log_prefix=lambda *a: None
original_begin=phase.begin_phase
def begin(**kw):
    kw['clock']=lambda: {AT!r}
    return original_begin(**kw)
phase.begin_phase=begin
original_finish=phase.finish_phase
def finish(**kw):
    kw['now']={AT!r}
    return original_finish(**kw)
phase.finish_phase=finish
if script.endswith('stage2_level3_phase.py'):
    event(sys.argv[1]+'-leaf')
runpy.run_path(script, run_name='__main__')
""")
    wrapper.chmod(0o700)
    env = dict(
        os.environ,
        CM52_HOST_PYTHON=str(wrapper),
        CM52_REPORT_ROOT=str(report),
        TEST_EVENTS=str(events),
    )
    env.pop("CM52_STAGE2_LOCK_FD", None)
    env.pop("CM52_STAGE2_PREPARED_SHA", None)

    def run(mode="resume", *, approval_record=None, **overrides):
        options = (
            [
                "--hold-after",
                "5d",
                "--resume-workload",
                "--prepared-attempt",
                str(root / "prepared-attempt.json"),
                "--approval-record",
                str(approval_record or root / "smtp-approval-grant.json"),
            ]
            if mode == "resume"
            else ["--resume-from", "6"]
        )
        return subprocess.run(
            [
                "bash",
                str(REPO / "deploy/compose/cm52_stage2.sh"),
                "--attempt-id",
                ATTEMPT,
                *options,
            ],
            env={**env, **overrides},
            capture_output=True,
            text=True,
            timeout=30,
        )

    return SimpleNamespace(run=run, root=root, a=a, report=report, events=events)


def operations(shell):
    return [json.loads(line) for line in shell.events.read_text().splitlines()]


def test_real_bash_resume_held_publish_sealed_v2(shell_v2):
    s = shell_v2
    first = s.run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert classify_state(read_lifecycle(s.root)) == "HELD"
    observed = operations(s)
    assert [v["op"] for v in observed] == [
        "begin-leaf",
        "binding",
        "execute-leaf",
        "execute",
        "collect-leaf",
        "collect",
        "held-leaf",
    ]
    owner = parse_json(read_private(s.root, "lifecycle-claim.resume_workload.json"))[
        "pid"
    ]
    assert {v["parent"] for v in observed} == {owner}
    again = s.run()
    assert again.returncode == 1
    assert operations(s) == observed
    second = s.run("publish")
    assert second.returncode == 0, second.stdout + second.stderr
    assert classify_state(read_lifecycle(s.root)) == "TERMINAL"
    aggregate, _ = verify_seal(
        root=s.root,
        published_root=s.a,
        expected_revision=REV,
        expected_attempt_id=ATTEMPT,
    )
    assert aggregate.robustness_verdict == aggregate.delivery_integrity == "PASS"
    after = operations(s)[len(observed) :]
    assert [v["op"] for v in after] == [
        "begin-leaf",
        "precondition",
        "publish-leaf",
        "publish",
        "cleanup-leaf",
        "cleanup",
        "retention",
        "restore-leaf",
        "restore-new",
        "finish-leaf",
    ]
    owner = parse_json(read_private(s.root, "lifecycle-claim.publish.json"))["pid"]
    assert {v["parent"] for v in after} == {owner}


@pytest.mark.parametrize(
    "failure", ["execute", "collect", "cleanup", "retention", "restore-old"]
)
def test_resume_failure_never_repeats_batch_and_restores_once(shell_v2, failure):
    s = shell_v2
    # Cleanup failures are reached after a workload failure.
    fail = failure if failure in ("execute", "collect") else "execute," + failure
    result = s.run(TEST_FAIL=fail)
    assert result.returncode == (2 if failure == "restore-old" else 1), (
        result.stdout + result.stderr
    )
    ops = [v["op"] for v in operations(s)]
    assert ops.count("execute") <= 1 and ops.count("collect") <= 1
    assert ops.count("cleanup") == ops.count("restore-old") == 1
    assert classify_state(read_lifecycle(s.root)) == "TERMINAL"
    assert not (s.root / "aggregate.json").exists()


def test_missing_grant_automatically_aborts_with_zero_workload(shell_v2):
    s = shell_v2
    (s.root / "smtp-approval-grant.json").unlink()
    result = s.run()
    assert result.returncode == 1, result.stdout + result.stderr
    assert "execute" not in [v["op"] for v in operations(s)]
    terminal = parse_json(read_private(s.root, "lifecycle-outcome.abort.json"))
    assert (
        terminal["external_effects"]["email_sent"]
        == terminal["external_effects"]["mes_sent"]
        == 0
    )
    assert terminal["reason_code"] == "EXTERNAL_EFFECT_APPROVAL_MISSING"


def test_noncanonical_grant_path_aborts_even_when_both_files_are_valid(shell_v2):
    from app.agent.release_artifacts import write_private_bytes

    s = shell_v2
    original = read_private(s.root, "smtp-approval-grant.json")
    write_private_bytes(s.root, "grant.json", original)
    result = s.run(approval_record=s.root / "grant.json")
    assert result.returncode == 1, result.stdout + result.stderr
    ops = [v["op"] for v in operations(s)]
    assert not {"binding", "execute", "collect"}.intersection(ops)
    terminal = parse_json(read_private(s.root, "lifecycle-outcome.abort.json"))
    assert terminal["reason_code"] == "EXTERNAL_EFFECT_APPROVAL_MISSING"
    assert terminal["external_effects"]["email_sent"] == 0
    assert terminal["external_effects"]["mes_sent"] == 0
    assert read_private(s.root, "smtp-approval-grant.json") == original
    assert not (s.root / "lifecycle-claim.resume_workload.json").exists()


def test_leaf_cli_wrong_parent_rejected_before_dispatch(shell_v2, tmp_path):
    s = shell_v2
    # Real subprocess and inherited EX FD. Only the operation is replaced by a
    # sentinel: deleting the CLI parent guard must reach it and fail this test.
    script = tmp_path / "leaf-parent-check.py"
    script.write_text(f"""import sys
sys.path.insert(0, {str(REPO / 'backend')!r})
from scripts import stage2_level3_phase as cli
events = []
cli.perform = lambda *args: (events.append('dispatch') or {{'status': 'DISPATCHED'}})
code = cli.main(sys.argv[1:])
assert events == [], events
raise SystemExit(code)
""")
    before = read_lifecycle(s.root)
    with lifecycle_lock(s.root) as fd:
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "prepare",
                "--repository",
                str(REPO),
                "--report-root",
                str(s.report),
                "--env-file",
                str(s.a / ".env.team"),
                "--attempt-id",
                ATTEMPT,
                "--mode",
                "prepare",
                "--lifecycle-lock-fd",
                str(fd),
                "--owner-pid",
                str(os.getpid() + 1000000),
            ],
            pass_fds=(fd,),
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "LIFECYCLE_PARENT_REQUIRED"
    assert result.stderr == ""
    assert read_lifecycle(s.root) == before
    assert not s.events.exists()


def test_authorized_checks_parent_even_with_matching_claim_identity(
    shell_v2, monkeypatch
):
    from app.agent import release_lifecycle as life
    from app.agent import release_phase as phase
    from app.agent.release_prepared import RUNTIME_BINDING_FIELDS
    from scripts import stage2_level3_phase as cli

    s = shell_v2
    owner = os.getppid()
    for module in (life, phase, cli):
        monkeypatch.setattr(module, "owner_identity", lambda _: ("boot", "start"))
    monkeypatch.setattr(phase, "validate_log_prefix", lambda *args: None)
    with lifecycle_lock(s.root) as fd:
        record = phase.begin_phase(
            root=s.root,
            mode="RESUME_WORKLOAD",
            lock_fd=fd,
            owner_pid=owner,
            clock=lambda: AT,
            grant_path=s.root / "smtp-approval-grant.json",
            read_runtime=lambda p: (
                {k: p.model_dump()[k] for k in RUNTIME_BINDING_FIELDS},
                p.approved_config_digest_allowlist[0],
            ),
        )
        assert record["workload_authorized"] is True
        write_private(s.a, "stage2-resume_workload-record.json", record)
        # Positive control reaches this exact authorization branch.
        cli.authorized(s.root, record, "resume_workload", owner)
        before = read_lifecycle(s.root)
        monkeypatch.setattr(cli.os, "getppid", lambda: owner + 1)
        with pytest.raises(EvidenceError, match="^LIFECYCLE_PARENT_REQUIRED$"):
            cli.authorized(s.root, record, "resume_workload", owner)
        assert read_lifecycle(s.root) == before
    assert not s.events.exists()


@pytest.mark.parametrize(
    "failure,code,restore",
    [
        ("precondition", "PUBLISH_PRECONDITION_FAILED", "restore-old"),
        ("publish", "ARTIFACT_PUBLISH_FAILED", "restore-old"),
        ("cleanup", "CLEANUP_FAILED", "restore-new"),
        ("retention", "CLEANUP_FAILED", "restore-new"),
        ("restore-new", "RESTORE_FAILED", "restore-new"),
    ],
)
def test_publish_failure_closes_without_seal_or_resend(
    shell_v2, failure, code, restore
):
    s = shell_v2
    assert s.run().returncode == 0
    count = len(operations(s))
    result = s.run("publish", TEST_FAIL=failure)
    assert result.returncode == (2 if code == "RESTORE_FAILED" else 1), (
        result.stdout + result.stderr
    )
    completion = parse_json(read_private(s.root, "round1-completion.json"))
    assert completion["final_status"] == "FAIL"
    assert completion["failure_code"] == code
    after = [row["op"] for row in operations(s)[count:]]
    assert after.count(restore) == 1
    assert "execute" not in after and "collect" not in after
    assert not (s.root / "MANIFEST.sha256").exists()


def test_no_decisions_capture_requires_held_and_is_not_an_action(
    shell_v2, monkeypatch, capsys
):
    from scripts import capture_stage2_no_decisions as cli

    s = shell_v2
    calls = []
    monkeypatch.setattr(
        cli, "current_runtime", lambda **kwargs: (s.a, None, None, None)
    )

    def capture(runtime, running, a, phase):
        calls.append(phase)
        parent = a / "evidence/artifacts/NO_DECISIONS"
        parent.mkdir(mode=0o700)
        write_private(a, "evidence/artifacts/NO_DECISIONS/db.json", dict(phase=phase))

    monkeypatch.setattr(cli, "snapshot", capture)
    args = [
        "--report-root",
        str(s.report),
        "--env-file",
        str(s.a / ".env.team"),
        "--attempt-id",
        ATTEMPT,
    ]
    assert cli.main(args) == 1 and calls == []
    assert s.run().returncode == 0
    before = read_lifecycle(s.root)
    assert cli.main(args) == 0 and calls == ["NO_DECISIONS"]
    assert read_lifecycle(s.root) == before
    assert "CAPTURED" in capsys.readouterr().out


def test_aggregate_and_seal_borrow_owner_lock(mock_bundle, tmp_path):  # noqa: F811
    from app.agent.release_aggregate import emit_aggregate
    from app.agent.release_seal import seal_bundle

    args = {
        k: v for k, v in mock_bundle.items() if k not in ("round1", "round1_completion")
    }
    repo = tmp_path / "repo"
    repo.mkdir()
    with lifecycle_lock(args["root"]) as fd:
        emit_aggregate(**args, repository=repo, lifecycle_lock_fd=fd)
        seal_bundle(**args, repository=repo, lifecycle_lock_fd=fd)
    assert verify_seal(**args)[0].delivery_integrity == "PASS"


def test_success_restore_requires_actual_publication_marker(shell_v2):
    from app.agent.release_stage2_publish import restore_context

    s = shell_v2
    with pytest.raises(EvidenceError):
        restore_context(s.a, published=True)
    prepared = parse_prepared(parse_json(read_private(s.root, "prepared-attempt.json")))
    write_private(
        s.a,
        "publish-result.json",
        dict(
            status="PUBLICATIONS_VERIFIED",
            attempt_id=ATTEMPT,
            R=REV,
            publications={
                n: component_ref(s.a, n).sha256
                for n in ("attempt.json", "golden-flow.json", "fault-5class.json")
            },
        ),
    )
    context = restore_context(s.a, published=True)
    assert context.prev_attempt == ATTEMPT
    assert context.prev_fault_sha256 == component_ref(s.a, "fault-5class.json").sha256
    assert (
        parse_prepared(parse_json(read_private(s.root, "prepared-attempt.json")))
        == prepared
    )
