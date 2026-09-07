"""Actual Bash/exec/lock/terminal paths; only OS identity and service leaves fake.

No shared Docker/DB/API/SMTP operations. Adapter tests below use explicit fake
Docker subprocess outputs, never the host daemon.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.agent import release_stage2_recovery as subject
from app.agent.release_artifacts import EvidenceError, canonical_json, write_private
from app.agent.release_fence import FENCE_NAME
from app.agent.release_lifecycle import classify_state, lifecycle_lock, read_lifecycle
from app.agent.release_prepared import PreparedAttempt
from app.agent.release_production import ProductionPorts, update_env
from tests.unit.test_agent_release import ATTEMPT, REV, claim, prepared_payload
from tests.unit.test_agent_release_aggregate import bundle  # noqa: F401
from tests.unit.test_agent_release_entry import entry  # noqa: F401
from tests.unit.test_agent_release_evidence import delivery  # noqa: F401
from tests.unit.test_agent_release_round import template  # noqa: F401

REPO = Path(__file__).resolve().parents[3]
STAGE2 = REPO / "deploy/compose/cm52_stage2.sh"


@pytest.fixture
def shell(entry, tmp_path):  # noqa: F811 - shared pytest fixture
    events = tmp_path / "events.jsonl"
    wrapper = tmp_path / "host-python"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys,runpy\n"
        f"sys.path.insert(0,{str(REPO / 'backend')!r})\n"
        "from app.agent import release_lifecycle as life, release_phase as phase\n"
        "from app.agent import release_stage2_recovery as ports\n"
        "identity=lambda pid: ('boot', None if pid==99999999 else 'start')\n"
        "life.owner_identity=phase.owner_identity=identity\n"
        "script=sys.argv.pop(1)\n"
        "def event(name):\n"
        "    with open(os.environ['TEST_EVENTS'],'a') as f:\n"
        "        f.write(json.dumps({'op':name,'parent':os.getppid()})+'\\n')\n"
        "def cleanup(p):\n"
        "    event('cleanup')\n"
        "    if os.environ.get('TEST_CLEANUP')=='INTERRUPTED':\n"
        "        raise KeyboardInterrupt\n"
        "    if os.environ.get('TEST_CLEANUP')=='FAILED':\n"
        "        raise RuntimeError('private-secret-do-not-output')\n"
        "    return {'result':os.environ.get('TEST_CLEANUP','OK')}\n"
        "def restore(**kw):\n"
        "    event('restore')\n"
        "    if os.environ.get('TEST_RESTORE')=='FAILED':\n"
        "        raise RuntimeError('private-secret-do-not-output')\n"
        "    return {'result':'OK'}\n"
        "ports.cleanup_e2e=cleanup\n"
        "ports.restore_level2=restore\n"
        "if os.environ.get('TEST_FAIL_FINISH')=='1':\n"
        "    def fail_finish(**kw): raise OSError('private-secret-do-not-output')\n"
        "    phase.finish_phase=fail_finish\n"
        "if script.endswith('stage2_recovery_phase.py'): event(sys.argv[1]+'-leaf')\n"
        "runpy.run_path(script,run_name='__main__')\n"
    )
    wrapper.chmod(0o700)
    environment = {
        **os.environ,
        "CM52_HOST_PYTHON": str(wrapper),
        "CM52_REPORT_ROOT": str(entry["report_root"]),
        "TEST_EVENTS": str(events),
        **{
            f"CM52_U10_{k}": "synthetic"
            for k in ("ARTIFACT", "EVALUATION_RECEIPT", "BENCHMARK", "BENCHMARK_SHA256")
        },
    }
    environment.pop("CM52_STAGE2_LOCK_FD", None)
    environment.pop("CM52_STAGE2_PREPARED_SHA", None)

    def run(mode="abort", **overrides):
        return subprocess.run(
            [
                "bash",
                str(STAGE2),
                "--attempt-id",
                ATTEMPT,
                "--" + mode + "-prepared",
                str(entry["prepared_path"]),
            ],
            env={**environment, **overrides},
            capture_output=True,
            text=True,
            timeout=20,
        )

    return SimpleNamespace(
        run=run,
        root=entry["prepared_path"].parent,
        events=events,
        report=entry["report_root"],
    )


@pytest.mark.parametrize(
    "cleanup,restore,code,rc",
    [
        ("OK", "OK", None, 0),
        ("NOT_ATTEMPTED", "OK", None, 0),
        ("FAILED", "OK", "CLEANUP_FAILED", 1),
        ("OK", "FAILED", "RESTORE_FAILED", 2),
        ("FAILED", "FAILED", "RESTORE_FAILED", 2),
    ],
)
def test_bash_abort_owns_same_lock_pid_until_terminal(
    shell, cleanup, restore, code, rc
):
    result = shell.run(TEST_CLEANUP=cleanup, TEST_RESTORE=restore)
    assert result.returncode == rc, result.stdout + result.stderr
    files = read_lifecycle(shell.root)
    assert classify_state(files) == "TERMINAL"
    terminal = json.loads(files["lifecycle-outcome.abort.json"])
    assert terminal["failure_code"] == code
    assert terminal["cleanup_result"] == cleanup
    assert terminal["restore_result"] == restore
    assert terminal["reason_code"] == "OPERATOR_ABORT"
    assert terminal["external_effects"]["email_sent"] == 0
    events = [json.loads(line) for line in shell.events.read_text().splitlines()]
    assert [e["op"] for e in events] == [
        "begin-leaf",
        "cleanup-leaf",
        "cleanup",
        "restore-leaf",
        "restore",
        "finish-leaf",
    ]
    owner = json.loads(files["lifecycle-claim.abort.json"])["pid"]
    assert {e["parent"] for e in events} == {owner}
    assert "private-secret" not in result.stdout + result.stderr
    assert not list(shell.report.rglob("stage2-log.jsonl"))
    before = {p.name: p.read_bytes() for p in shell.root.iterdir()}
    repeated = shell.run()
    assert repeated.returncode == 1
    assert "LIFECYCLE_TRANSITION_INVALID" in repeated.stdout + repeated.stderr
    assert {p.name: p.read_bytes() for p in shell.root.iterdir()} == before
    assert len(shell.events.read_text().splitlines()) == 6


@pytest.mark.parametrize("phase", ["ABORT", "RESUME_WORKLOAD"])
@pytest.mark.parametrize(
    "cleanup,restore,code",
    [
        ("OK", "OK", "STALE_CLAIM_RECOVERED"),
        ("FAILED", "OK", "CLEANUP_FAILED"),
        ("OK", "FAILED", "RESTORE_FAILED"),
        ("FAILED", "FAILED", "RESTORE_FAILED"),
        ("NOT_ATTEMPTED", "OK", "STALE_CLAIM_RECOVERED"),
    ],
)
def test_bash_recover_reuses_stale_claim_and_preserves_unknown_effects(
    shell, phase, cleanup, restore, code
):
    files = read_lifecycle(shell.root)
    value = claim(files, phase)
    value["pid"] = 99999999
    filename = "lifecycle-claim." + phase.lower() + ".json"
    write_private(shell.root, filename, value)
    original = (shell.root / filename).read_bytes()
    result = shell.run("recover", TEST_CLEANUP=cleanup, TEST_RESTORE=restore)
    assert result.returncode == (2 if restore == "FAILED" else 1), (
        result.stdout + result.stderr
    )
    terminal = json.loads(
        (shell.root / ("lifecycle-outcome." + phase.lower() + ".json")).read_bytes()
    )
    assert terminal["primary_failure_code"] == "STALE_CLAIM_RECOVERED"
    assert terminal["failure_code"] == code and terminal["issued_by"] == "RECOVER"
    assert terminal["external_effects"]["state"] == "INDETERMINATE"
    assert terminal["external_effects"]["email_sent"] is None
    assert (shell.root / filename).read_bytes() == original
    assert len(list(shell.root.glob("lifecycle-claim.*"))) == 1
    assert not (shell.root / "round1-completion.json").exists()


def test_active_owner_recovery_does_no_cleanup(shell):
    value = claim(read_lifecycle(shell.root), "RESUME_WORKLOAD")
    write_private(shell.root, "lifecycle-claim.resume_workload.json", value)
    result = shell.run("recover")
    assert result.returncode == 1
    assert "LIFECYCLE_OWNER_ACTIVE" in result.stderr
    assert [json.loads(x)["op"] for x in shell.events.read_text().splitlines()] == [
        "begin-leaf"
    ]
    assert classify_state(read_lifecycle(shell.root)) == "UNRESOLVED"


def test_contending_bash_does_not_wait_or_cleanup(shell):
    with lifecycle_lock(shell.root):
        result = shell.run()
    assert result.returncode == 1
    assert "LIFECYCLE_LOCK_BUSY" in result.stdout
    assert not shell.events.exists()
    assert classify_state(read_lifecycle(shell.root)) == "PREPARED"


def test_forged_inherited_fd_is_denied_before_claim(shell):
    result = shell.run(CM52_STAGE2_LOCK_FD="90", CM52_STAGE2_PREPARED_SHA="1" * 64)
    assert result.returncode == 1
    assert "LIFECYCLE" in result.stderr
    assert classify_state(read_lifecycle(shell.root)) == "PREPARED"


def test_interrupted_cleanup_still_attempts_restore_once(shell):
    result = shell.run(TEST_CLEANUP="INTERRUPTED")
    assert result.returncode == 1, result.stdout + result.stderr
    terminal = json.loads((shell.root / "lifecycle-outcome.abort.json").read_bytes())
    assert terminal["failure_code"] == "CLEANUP_FAILED"
    assert terminal["restore_result"] == "OK"
    operations = [json.loads(x)["op"] for x in shell.events.read_text().splitlines()]
    assert operations.count("cleanup") == operations.count("restore") == 1


def test_terminal_write_failure_preserves_claim_and_never_repeats_cleanup(shell):
    result = shell.run(TEST_FAIL_FINISH="1")
    assert result.returncode == 2
    assert "LIFECYCLE_TERMINAL_WRITE_FAILED" in result.stderr
    assert "private-secret" not in result.stdout + result.stderr
    assert classify_state(read_lifecycle(shell.root)) == "UNRESOLVED"
    operations = [json.loads(x)["op"] for x in shell.events.read_text().splitlines()]
    assert operations.count("cleanup") == operations.count("restore") == 1
    assert not list(shell.root.glob("lifecycle-outcome.*"))


def test_missing_restore_inputs_records_failure_not_a_false_restoration(shell):
    result = shell.run(CM52_U10_ARTIFACT="")
    assert result.returncode == 2
    assert "STAGE2_RESTORE_PREFLIGHT_INPUTS_REQUIRED" in result.stderr
    terminal = json.loads((shell.root / "lifecycle-outcome.abort.json").read_bytes())
    assert terminal["failure_code"] == "RESTORE_FAILED"
    operations = [json.loads(x)["op"] for x in shell.events.read_text().splitlines()]
    assert operations.count("cleanup") == 1 and "restore" not in operations


@pytest.fixture
def docker():
    prepared = PreparedAttempt.model_validate(prepared_payload())
    state = {
        getattr(prepared.containers, role).container_id: {
            "id": getattr(prepared.containers, role).container_id,
            "image": getattr(prepared.images, role).image_id,
            "running": True,
            "project": subject.PROJECT,
            "service": service,
            "oneoff": "False",
        }
        for role, service in subject.SERVICES.items()
    }
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[1] == "ps":
            return "\n".join(state).encode()
        if "inspect" in argv:
            return canonical_json(state[argv[-1]])
        if argv[1] == "stop":
            for cid in argv[4:]:
                state[cid]["running"] = False
            return b""
        if argv[1:3] == ["container", "rm"]:
            for cid in argv[3:]:
                del state[cid]
            return b""
        raise AssertionError(argv)

    return SimpleNamespace(prepared=prepared, state=state, calls=calls, run=run)


def test_cleanup_targets_only_pinned_ids_and_retains_volumes(docker):
    result = subject.cleanup_e2e(docker.prepared, run=docker.run)
    assert result["result"] == "OK" and not docker.state
    commands = [a for a in docker.calls if "stop" in a or "rm" in a]
    assert len(commands) == 2
    assert all("--force" not in a and "--volumes" not in a for a in commands)
    assert subject.cleanup_e2e(docker.prepared, run=docker.run) == {
        "result": "NOT_ATTEMPTED",
        "basis": "CLEANUP_E2E_ABSENT",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("project", "bistel-team"),
        ("service", "unrelated"),
        ("oneoff", "True"),
        ("id", "f" * 64),
        ("image", "sha256:" + "e" * 64),
        ("running", 1),
    ],
)
def test_cleanup_rejects_drift_before_any_stop(docker, field, value):
    next(iter(docker.state.values()))[field] = value
    with pytest.raises(EvidenceError):
        subject.cleanup_e2e(docker.prepared, run=docker.run)
    assert not any("stop" in a or "rm" in a for a in docker.calls)


def test_restore_refuses_live_e2e_before_env_or_service_access(docker, tmp_path):
    with pytest.raises(EvidenceError, match="STAGE2_RESTORE_E2E_REMAINS"):
        subject.restore_level2(
            repository=REPO,
            report_root=tmp_path,
            env_file=tmp_path / "absent",
            prepared=docker.prepared,
            preflight_arguments=[],
            run=docker.run,
        )
    assert len(docker.calls) == 1


def test_restore_env_pair_is_atomic_and_preserves_unrelated_bytes(tmp_path):
    path = tmp_path / ".env.team"
    before = b"# keep\r\nPRIVATE=secret\r\nAGENT_LEVEL3_ENABLED=true\r\n"
    path.write_bytes(before)
    path.chmod(0o600)
    fault = f"/reports/cm-5.2/{ATTEMPT}/fault-5class.json"
    golden = f"/reports/cm-5.2/{ATTEMPT}/golden-flow.json"
    after = update_env(
        path, before, level=2, attempt=ATTEMPT, artifact_paths=(fault, golden)
    )
    assert after.startswith(
        b"# keep\r\nPRIVATE=secret\r\nAGENT_LEVEL3_ENABLED=false\r\n"
    )
    assert f"AGENT_FAULT_EVAL_ARTIFACT_PATH={fault}\n".encode() in after
    assert f"AGENT_GOLDEN_FLOW_SUMMARY_PATH={golden}\n".encode() in after
    assert path.read_bytes() == after
    with pytest.raises(EvidenceError):
        update_env(path, after, level=2, attempt=ATTEMPT, artifact_paths=(fault, ""))
    assert path.read_bytes() == after


@pytest.mark.parametrize(
    "cleanup,restore,code",
    [
        ("OK", "OK", "PUBLISH_PHASE_RECOVERED"),
        ("FAILED", "OK", "CLEANUP_FAILED"),
        ("OK", "FAILED", "RESTORE_FAILED"),
        ("FAILED", "FAILED", "RESTORE_FAILED"),
        ("NOT_ATTEMPTED", "OK", "PUBLISH_PHASE_RECOVERED"),
    ],
)
def test_stale_publish_issues_only_failed_completion_after_cleanup(
    shell,
    bundle,  # noqa: F811 - shared pytest fixture
    cleanup,
    restore,
    code,  # noqa: F811
):
    for path in bundle["root"].iterdir():
        if path.name not in {"prepared-attempt.json", "round1-completion.json"}:
            value = json.loads(path.read_bytes())
            if path.name == "lifecycle-claim.publish.json":
                value["pid"] = 99999999
            write_private(shell.root, path.name, value)
    hashes = {}
    for path in bundle["published_root"].iterdir():
        reference = write_private(
            shell.root.parent, path.name, json.loads(path.read_bytes())
        )
        hashes[path.name] = reference.sha256
    result = shell.run("recover", TEST_CLEANUP=cleanup, TEST_RESTORE=restore)
    assert result.returncode == (2 if restore == "FAILED" else 1), (
        result.stdout + result.stderr
    )
    terminal = json.loads((shell.root / "round1-completion.json").read_bytes())
    assert terminal["final_status"] == "FAIL"
    assert terminal["primary_failure_code"] == "PUBLISH_PHASE_RECOVERED"
    assert terminal["failure_code"] == code
    assert terminal["attempt_artifact_sha256"] == hashes["attempt.json"]
    assert terminal["golden_flow_sha256"] == hashes["golden-flow.json"]
    assert terminal["fault_5class_sha256"] == hashes["fault-5class.json"]
    assert terminal["external_effects"]["state"] == "INDETERMINATE"
    assert not list(shell.root.glob("*abort*"))
    assert not (shell.root / "aggregate.json").exists()
    assert not (shell.root / "MANIFEST.sha256").exists()


@pytest.fixture
def restoration(tmp_path):
    report = tmp_path / "reports"
    report.mkdir(mode=0o700)
    env_file = tmp_path / ".env.team"
    env_file.write_text(
        f"SOURCE_REVISION={REV}\nAGENT_EVAL_REPORTS_DIR={report}\n"
        "POSTGRES_DB=kosa_agent\nAPP_DB_USER=kosa_app\nSECRET=preserve-this\n"
        "AGENT_AUTONOMY_LEVEL=3\nAGENT_LEVEL3_ENABLED=true\n"
        f"AGENT_LEVEL3_DEMO_ACK={ATTEMPT}\n"
    )
    env_file.chmod(0o600)
    r = SimpleNamespace(
        calls=[],
        failure=None,
        quiet=0,
        env_pair=["", ""],
        preflight={
            "integrity": "PASS",
            "profile": "production_level2",
            "evaluated_revision": REV,
        },
        report=report,
        env_file=env_file,
    )

    def process(argv, **kwargs):
        r.calls.append((argv, kwargs))
        stdout = b""
        if "read_release_quiescence.py" in " ".join(argv):
            stdout = canonical_json(
                {"schema_version": "level3-quiescence-v1", "active_runs": r.quiet}
            )
        elif argv[1] == "ps":
            stdout = ("1" * 64).encode()
        elif "json.dumps([os.environ" in " ".join(argv):
            stdout = canonical_json(r.env_pair)
        elif "preflight_agent_evaluation_artifacts.py" in " ".join(argv):
            if r.failure == "previous":
                raise OSError("secret")
        elif "up" in argv and r.failure == "up":
            raise OSError("secret")
        return SimpleNamespace(stdout=stdout, returncode=0)

    def factory(**kwargs):
        ports = ProductionPorts(**kwargs, run=process)

        def preflight(level):
            assert level == 2
            r.calls.append((["preflight", level], {}))
            return r.preflight

        ports.preflight = preflight
        return ports

    def execute(prepared=None):
        def verify_api(state):
            r.calls.append((["api", state], {}))
            if r.failure == "api":
                raise EvidenceError("STAGE2_RESTORE_EVALUATION_API_FAILED")

        return subject.restore_level2(
            repository=REPO,
            report_root=report,
            env_file=env_file,
            prepared=prepared or PreparedAttempt.model_validate(prepared_payload()),
            preflight_arguments=["synthetic-A-input"],
            ports_factory=factory,
            inspect_image=lambda kind, identifier: {
                "image_id": identifier,
                "label_revision": REV,
            },
            run=lambda argv, **kwargs: b"",
            verify_api=verify_api,
        )

    r.execute = execute
    return r


def test_restore_uses_fixed_images_quiescence_then_recreate_then_verified_reopen(
    restoration, monkeypatch
):
    r = restoration
    monkeypatch.setenv("AGENT_AUTONOMY_LEVEL", "unsafe-host-value")
    monkeypatch.setenv("APP_DB_USER", "owner-must-not-win")
    assert r.execute()["result"] == "OK"
    state = json.loads((r.report / FENCE_NAME).read_bytes())
    assert state["state"] == "OPEN" and state["level"] == 2
    commands = [c for c, _ in r.calls]
    assert "read_release_quiescence.py" in " ".join(commands[0])
    assert commands[0][commands[0].index("run") :] == [
        "run",
        "--rm",
        "--no-deps",
        "--pull",
        "never",
        "-T",
        "--entrypoint",
        "python",
        "backend",
        "-B",
        "/workspace/backend/scripts/read_release_quiescence.py",
    ]
    up = [a for a in commands if "up" in a]
    assert len(up) == 2
    assert up[0][-2:] == ["kafka", "mes-mock"]
    assert up[1][-2:] == ["backend", "frontend"]
    assert all("--no-build" in a and "never" in a for a in up)
    for argv, kwargs in r.calls:
        if argv[0] == "docker":
            assert "APP_DB_USER" not in kwargs["env"]
            assert "AGENT_AUTONOMY_LEVEL" not in kwargs["env"]
            assert kwargs["env"]["CM52_PIN_BACKEND_IMAGE"] == "sha256:" + "1" * 64
    assert b"SECRET=preserve-this\n" in r.env_file.read_bytes()
    assert b"AGENT_AUTONOMY_LEVEL=2\n" in r.env_file.read_bytes()


@pytest.mark.parametrize(
    "fault", ["active", "bool", "up", "integrity", "revision", "env_pair", "api"]
)
def test_restore_failure_never_reopens_fence(restoration, fault):
    r = restoration
    before = r.env_file.read_bytes()
    if fault in {"active", "bool"}:
        r.quiet = 1 if fault == "active" else False
    elif fault == "up":
        r.failure = "up"
    elif fault == "integrity":
        r.preflight["integrity"] = "FAIL"
    elif fault == "revision":
        r.preflight["evaluated_revision"] = "b" * 40
    elif fault == "env_pair":
        r.env_pair = ["unbound", ""]
    elif fault == "api":
        r.failure = "api"
    with pytest.raises(EvidenceError):
        r.execute()
    assert json.loads((r.report / FENCE_NAME).read_bytes())["state"] == "CLOSED"
    if fault in {"active", "bool"}:
        assert r.env_file.read_bytes() == before
        assert not any("up" in argv for argv, _ in r.calls)


def test_restore_bound_previous_pair_checks_sha_before_mutation_and_after_recreation(
    restoration,
):
    r = restoration
    (r.report / "cm-5.2").mkdir(mode=0o700)
    previous_root = r.report / "cm-5.2" / ATTEMPT
    previous_root.mkdir(mode=0o700)
    fault_ref = write_private(
        previous_root, "fault-5class.json", {"synthetic": "fault"}
    )
    golden_ref = write_private(
        previous_root, "golden-flow.json", {"synthetic": "golden"}
    )
    r.env_pair = [
        f"/reports/cm-5.2/{ATTEMPT}/{name}"
        for name in ("fault-5class.json", "golden-flow.json")
    ]
    prepared = PreparedAttempt.model_validate(
        {
            **prepared_payload(),
            "prev_state": "bound",
            "prev_attempt": ATTEMPT,
            "prev_rev": REV,
            "prev_fault_path": r.env_pair[0],
            "prev_golden_path": r.env_pair[1],
            "prev_fault_sha256": fault_ref.sha256,
            "prev_golden_sha256": golden_ref.sha256,
        }
    )
    assert r.execute(prepared)["result"] == "OK"
    verifier = [
        a
        for a, _ in r.calls
        if "preflight_agent_evaluation_artifacts.py" in " ".join(map(str, a))
    ]
    assert (
        len(verifier) == 1
        and fault_ref.sha256 in verifier[0]
        and golden_ref.sha256 in verifier[0]
    )
    before = r.env_file.read_bytes()
    (previous_root / "golden-flow.json").write_bytes(b"changed")
    r.calls.clear()
    with pytest.raises(EvidenceError, match="RESTORE_BINDING_INVALID"):
        r.execute(prepared)
    assert not r.calls and r.env_file.read_bytes() == before


@pytest.mark.parametrize(
    "fault", [None, "redirect", "status", "oversized", "malformed", "reason"]
)
def test_restore_api_is_real_schema_fixed_get_without_redirect(fault):
    calls = []
    body = {
        "fault_5class": None,
        "golden_flow": None,
        "fault_5class_empty_reason": "NOT_CONFIGURED",
        "golden_flow_empty_reason": "NOT_CONFIGURED",
    }
    if fault == "reason":
        body["fault_5class_empty_reason"] = "ARTIFACT_INVALID"

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert str(request.url) == "http://127.0.0.1:8080/api/agent/evaluations"
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "https://example.invalid"})
        if fault == "status":
            return httpx.Response(500)
        if fault == "oversized":
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
        if fault == "malformed":
            return httpx.Response(200, content=b"private-malformed-body")
        return httpx.Response(200, json=body)

    if fault is None:
        subject.verify_evaluation_api(
            "empty", transport=httpx.MockTransport(handler), timeout_seconds=0
        )
    else:
        with pytest.raises(
            EvidenceError, match="^STAGE2_RESTORE_EVALUATION_API_FAILED$"
        ):
            subject.verify_evaluation_api(
                "empty", transport=httpx.MockTransport(handler), timeout_seconds=0
            )
    assert len(calls) == 1


def test_restore_api_accepts_bound_response_serialized_by_public_dto(tmp_path):
    from app.agent.evaluation_read_model import load_agent_evaluations
    from tests.unit.test_agent_evaluation_read_model import (
        _fault_artifact,
        _golden_artifact,
    )

    fault_path = tmp_path / "fault.json"
    golden_path = tmp_path / "golden.json"
    fault_path.write_text(json.dumps(_fault_artifact()), encoding="utf-8")
    golden_path.write_text(json.dumps(_golden_artifact()), encoding="utf-8")
    body = load_agent_evaluations(
        fault_path=str(fault_path), golden_path=str(golden_path)
    ).model_dump(mode="json")
    assert body["fault_5class"] is not None and body["golden_flow"] is not None

    def handler(_request):
        return httpx.Response(200, json=body)

    subject.verify_evaluation_api(
        "bound", transport=httpx.MockTransport(handler), timeout_seconds=0
    )


def test_restore_api_retries_transient_startup_failures_within_bound():
    calls = []
    now = [0.0]
    body = {
        "fault_5class": None,
        "golden_flow": None,
        "fault_5class_empty_reason": "NOT_CONFIGURED",
        "golden_flow_empty_reason": "NOT_CONFIGURED",
    }

    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) < 3 else 200, json=body)

    def sleep(seconds):
        now[0] += seconds

    subject.verify_evaluation_api(
        "empty",
        transport=httpx.MockTransport(handler),
        timeout_seconds=5,
        retry_interval=1,
        monotonic=lambda: now[0],
        sleep=sleep,
    )
    assert len(calls) == 3 and now[0] == 2


def test_restore_api_retry_stops_at_deadline():
    calls = []
    now = [0.0]

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    def sleep(seconds):
        now[0] += seconds

    with pytest.raises(EvidenceError, match="^STAGE2_RESTORE_EVALUATION_API_FAILED$"):
        subject.verify_evaluation_api(
            "empty",
            transport=httpx.MockTransport(handler),
            timeout_seconds=2,
            retry_interval=1,
            monotonic=lambda: now[0],
            sleep=sleep,
        )
    assert len(calls) == 3 and now[0] == 2
