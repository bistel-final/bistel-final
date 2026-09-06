"""Actual A/runtime/n8n validators with temporary Git and synthetic IO leaves."""

from datetime import UTC, datetime, timedelta

import pytest

from app.agent import release_capture as m
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_prepare import build_prepared, issue_prepared
from app.agent.release_runtime import ComposeRuntime, RuntimeSnapshot
from app.agent.u10_preflight_report import preflight_report
from tests.unit.test_agent_release import prepared_payload
from tests.unit.test_agent_release_context import projection
from tests.unit.test_agent_release_n8n import WF, Server
from tests.unit.test_agent_release_runtime import COMPOSE, Docker
from tests.unit.test_agent_u10_integrity import setup
from tests.unit.test_agent_u10_revision import git


@pytest.fixture
def rig(tmp_path, monkeypatch, request):
    args, containers, events, revision = setup(
        tmp_path, monkeypatch, negative=getattr(request, "param", False)
    )
    report = tmp_path / "reports"
    report.mkdir(mode=0o700)
    env = tmp_path / ".env.team"
    env.write_text("SYNTHETIC=true")
    env.chmod(0o600)
    fake = Docker(report)
    fake.fill()
    for cid, payload in containers.items():
        fake.payloads[cid].update(payload)
    adapter = ComposeRuntime(
        compose_directory=COMPOSE,
        env_file=env,
        revision=revision,
        image_ids=args["expected_image_ids"],
        report_root=report,
        uid=501,
        gid=20,
        run=fake.run,
        inspect_image=args["inspect"],
    )
    running = RuntimeSnapshot(
        revision=revision,
        phase="running",
        images={
            r: args["inspect"]("image", i)
            for r, i in args["expected_image_ids"].items()
        },
        containers={r: fake.payloads[c] for r, c in args["container_ids"].items()},
    )
    contexts = {r: projection() for r in ("backend", "runner")}
    context_calls = []

    def context(cid):
        context_calls.append(cid)
        role = next(r for r, c in args["container_ids"].items() if c == cid)
        return contexts[role]

    config = dict(
        n8n_workflow_versions={WF: "version-test"},
        smtp_host="smtp.example.invalid",
        smtp_port=587,
        smtp_from="Sender@example.invalid",
        recipient_allowlist=projection().recipients,
        wf2_callback_endpoint="http://backend:8000/internal/actions/{action_id}/delivery",
    )
    smtp_calls = []

    def smtp():
        smtp_calls.append("READ")
        return config

    now = datetime(2026, 9, 5, 1, 2, 4, tzinfo=UTC)
    server = Server()
    api = server.client()
    kwargs = {
        k: args[k]
        for k in (
            "repository",
            "artifact",
            "evaluation_receipt",
            "benchmark",
            "pinned_benchmark_sha256",
            "inspect",
            "read",
            "fetch",
        )
    }
    kwargs.update(
        runtime_adapter=adapter,
        running=running,
        previous=m.RestoreContext.model_validate(
            {
                k: v
                for k, v in prepared_payload().items()
                if k.startswith("prev_") or k == "running_rev"
            }
        ),
        api=api,
        workflow_id=WF,
        sample_execution_id="1",
        read_smtp_config=smtp,
        read_context=context,
        clock=lambda: now,
    )
    yield kwargs, fake, server, config, contexts, context_calls, smtp_calls, events
    api.close()


@pytest.mark.parametrize("rig", [False, True], indirect=True)
def test_one_preflight_and_historical_probe_no_workload_or_write(rig):
    args, fake, server, config, _, calls, smtp_calls, events = rig
    files = {p: p.read_bytes() for p in args["artifact"].parent.iterdir()}
    capture = m.collect_preparation(**args)
    assert capture.runtime == args["running"]
    assert capture.smtp_config.model_dump() == config
    assert calls == [
        args["running"].containers[r].container_id
        for r in ("backend", "runner", "backend", "runner")
    ]
    assert smtp_calls == ["READ", "READ"]
    assert [e[1] for e in events if e[0] == "http"] == [
        "/api/health/ready",
        "/",
        "/api/health",
    ]
    assert [r.url.path.rsplit("/", 2)[-2:] for r in server.requests] == [
        ["workflows", WF],
        ["executions", "1"],
        ["workflows", WF],
    ]
    assert all(r.method == "GET" for r in server.requests)
    assert not any(
        fake.actions(v) for v in ("create", "compose", "start", "exec", "stop")
    )
    assert {p: p.read_bytes() for p in args["artifact"].parent.iterdir()} == files
    report = preflight_report(
        capture.preflight,
        profile="e2e_level3",
        phase="pre_u9",
        checked_at=capture.preflight.checked_at,
        failed_checks=[],
    )
    assert report["robustness"] == report["delivery_integrity"] == "NOT_RUN"
    assert not report["allowed_actions"]["production_level3"]


@pytest.mark.parametrize(
    "key",
    [
        "smtp_host",
        "smtp_port",
        "smtp_from",
        "recipient_allowlist",
        "wf2_callback_endpoint",
        "n8n_workflow_versions",
    ],
)
def test_config_change_during_n8n_probe_rejected(rig, key):
    args, _, server, config, *_ = rig
    changes = dict(
        smtp_host="changed",
        smtp_port=465,
        smtp_from="Other@example.invalid",
        recipient_allowlist=["Other@example.invalid"],
        wf2_callback_endpoint="http://other/delivery",
        n8n_workflow_versions={WF: "v2"},
    )

    def mutate(request, value):
        if "/executions/" in request.url.path:
            config[key] = changes[key]
        return value

    server.hook = mutate
    with pytest.raises(EvidenceError, match="^PREPARATION_CAPTURE_DRIFT$"):
        m.collect_preparation(**args)


@pytest.mark.parametrize("field", ["recipients", "identity"])
def test_context_drift_on_both_roles_not_hidden_by_cross_role_match(rig, field):
    args, _, server, _, contexts, *_ = rig

    def mutate(request, value):
        if "/executions/" in request.url.path:
            for role, ctx in contexts.items():
                payload = ctx.model_dump()
                if field == "identity":
                    payload[field]["system_identifier"] = "98765"
                else:
                    payload[field] = ["Other@example.invalid"]
                contexts[role] = m.PreparationContext.model_validate(payload)
        return value

    server.hook = mutate
    with pytest.raises(EvidenceError, match="^PREPARATION_CAPTURE_DRIFT$"):
        m.collect_preparation(**args)


@pytest.mark.parametrize("role", ["backend", "frontend", "runner"])
def test_restart_during_probe_rejected(rig, role):
    args, fake, server, *_ = rig

    def mutate(request, value):
        if "/executions/" in request.url.path:
            fake.payloads[args["running"].containers[role].container_id][
                "started_at"
            ] = "2026-09-05T01:02:04Z"
        return value

    server.hook = mutate
    with pytest.raises(EvidenceError, match="^LEVEL3_RUNTIME_DRIFT$"):
        m.collect_preparation(**args)


def test_context_role_mismatch_prevents_preflight_probe(rig):
    args, _, server, _, contexts, _, _, events = rig
    payload = contexts["runner"].model_dump()
    payload["identity"]["host_alias"] = "other"
    contexts["runner"] = m.PreparationContext.model_validate(payload)
    with pytest.raises(EvidenceError, match="^PREPARATION_CAPTURE_CONTEXT_INVALID$"):
        m.collect_preparation(**args)
    assert server.requests == [] and not any(e[0] == "http" for e in events)


@pytest.mark.parametrize(
    "kind", ["missing", "exception", "secret", "workflow", "recipient"]
)
def test_unavailable_or_mismatched_smtp_never_falls_back(rig, kind):
    args, _, server, config, _, _, _, events = rig
    code = "PREPARATION_CAPTURE_SMTP_UNAVAILABLE"
    if kind == "missing":
        args["read_smtp_config"] = None
        code = "PREPARATION_CAPTURE_ARGUMENT_INVALID"
    elif kind == "exception":

        def fail():
            raise RuntimeError("private-config-secret")

        args["read_smtp_config"] = fail
    elif kind == "secret":
        config["password"] = "private-config-secret"
    elif kind == "workflow":
        config["n8n_workflow_versions"] = {"other": "version"}
    else:
        config["recipient_allowlist"] = ["Other@example.invalid"]
        code = "PREPARATION_CAPTURE_RECIPIENT_MISMATCH"
    with pytest.raises(EvidenceError, match=f"^{code}$"):
        m.collect_preparation(**args)
    assert server.requests == [] and not any(e[0] == "http" for e in events)


def test_retention_none_fails_without_sending_probe(rig):
    args, _, server, *_ = rig
    server.workflow["settings"]["saveDataSuccessExecution"] = "none"
    with pytest.raises(EvidenceError, match="^N8N_EVIDENCE_RETENTION_REQUIRED$"):
        m.collect_preparation(**args)
    assert len(server.requests) == 1


def test_dirty_repository_fails_before_external_reads(rig):
    args, fake, server, _, _, calls, smtp_calls, _ = rig
    (args["repository"] / "dirty").write_text("synthetic")
    with pytest.raises(EvidenceError):
        m.collect_preparation(**args)
    assert fake.calls == [] and server.requests == calls == smtp_calls == []


def test_git_changes_during_probe_rejected(rig):
    args, _, server, *_ = rig

    def mutate(request, value):
        if "/executions/" in request.url.path:
            git(args["repository"], "commit", "--allow-empty", "-qm", "synthetic drift")
        return value

    server.hook = mutate
    with pytest.raises(EvidenceError):
        m.collect_preparation(**args)


@pytest.mark.parametrize("file", ["artifact", "evaluation_receipt", "benchmark"])
def test_private_evaluation_changes_after_preflight_rejected(rig, file):
    args, _, server, *_ = rig

    def mutate(request, value):
        if "/executions/" in request.url.path:
            args[file].write_bytes(args[file].read_bytes() + b" ")
        return value

    server.hook = mutate
    with pytest.raises(EvidenceError):
        m.collect_preparation(**args)


def test_clock_backwards_after_probe_rejected(rig):
    args, *_ = rig
    now = datetime(2026, 9, 5, 1, 2, 4, 500000, tzinfo=UTC)
    times = iter([now] * 5 + [now - timedelta(microseconds=1)])
    args["clock"] = lambda: next(times)
    with pytest.raises(EvidenceError, match="^PREPARATION_CAPTURE_TIME_INVALID$"):
        m.collect_preparation(**args)


def test_prepare_ack_cannot_reuse_production_consent(rig):
    args, _, server, *_ = rig
    read = args["read"]

    def changed(*a):
        return {**read(*a), "demo_ack": "old-attempt", "ack_matches_receipt": True}

    args["read"] = changed
    with pytest.raises(EvidenceError, match="^PREPARATION_CAPTURE_PREFLIGHT_MISMATCH$"):
        m.collect_preparation(**args)
    assert server.requests == []


def test_actual_capture_can_feed_prepared_writer(rig, tmp_path):
    from app.agent.release_prepare import EPOCH
    from tests.unit.test_agent_release_prepare import RESET_ID, S

    args, *_ = rig
    capture = m.collect_preparation(**args)
    reset = dict(
        artifact_type="e2e_reset_final",
        format_version=1,
        task_id="V5-CM-4.7",
        dataset_epoch=EPOCH,
        run_id=RESET_ID,
        recorded_at="2026-09-05T00:59:00Z",
        status="PASS",
        reason="PASS",
        pre_receipt_sha256=S,
        applied_receipt_sha256=S,
        post_receipt_sha256=S,
        observer_before_sha256={"kosa_agent": S, "kosa_text2sql": S},
        observer_after_sha256={"kosa_agent": S, "kosa_text2sql": S},
    )
    baseline = dict(
        artifact_type="cm52_public_database_observer",
        format_version=1,
        dataset_epoch=EPOCH,
        recorded_at="2026-09-05T01:00:00Z",
        immutable={"kosa_agent": {"sha256": S}, "kosa_text2sql": {"sha256": S}},
        strict_kosa_agent={"sha256": S},
        text2sql_log={"row_count": 0, "max_id": 0, "sequence_last_value": 0},
    )
    pf = preflight_report(
        capture.preflight,
        profile="e2e_level3",
        phase="pre_u9",
        checked_at=capture.preflight.checked_at,
        failed_checks=[],
    )
    prepared = build_prepared(
        capture,
        attempt_id="20260905T010000Z-" + capture.runtime.revision[:12],
        revision=capture.runtime.revision,
        image_ids={r: c.image_id for r, c in capture.runtime.containers.items()},
        preflight_bytes=canonical_json(pf),
        reset_bytes=canonical_json(reset),
        reset_run_id=RESET_ID,
        baseline_bytes=canonical_json(baseline),
        log_bytes=canonical_json(dict(step="3b", status="PASS", detail="synthetic"))
        + b"\n",
        now="2026-09-05T01:03:00Z",
    )
    assert prepared.prepared_at == capture.captured_at
    assert prepared.db_identity == capture.db_identities["backend"]
    assert prepared.recipient.canonical_addresses == capture.recipients["backend"]
    assert prepared.max_external_emails == 7
    root = tmp_path / "reports"
    (root / "cm-5.2").mkdir(mode=0o700)
    attempt = root / "cm-5.2" / prepared.attempt_id
    attempt.mkdir(mode=0o700)
    (attempt / "robustness").mkdir(mode=0o700)
    capref = write_private(attempt, "preparation-capture.json", capture.model_dump())
    pfref = write_private(attempt, "e2e-level3-preflight.json", pf)
    write_private(attempt, "observer-baseline.json", baseline)
    write_private(
        attempt, "stage2-log.jsonl", dict(step="3b", status="PASS", detail="synthetic")
    )
    resetref = write_private(root, "reset-final.json", reset)
    report = issue_prepared(
        report_root=root,
        mounted_report_root=root,
        repository=args["repository"],
        attempt_id=prepared.attempt_id,
        revision=capture.runtime.revision,
        image_ids={r: c.image_id for r, c in capture.runtime.containers.items()},
        capture_sha256=capref.sha256,
        preflight_sha256=pfref.sha256,
        reset_receipt=resetref,
        reset_run_id=RESET_ID,
        clock=lambda: "2026-09-05T01:03:00Z",
    )
    assert not report["smtp_send_authorized"] and not report["deployment_authorized"]
    saved = parse_json(read_private(attempt, "robustness/prepared-attempt.json"))
    assert saved["prepared_at"] == capture.captured_at
    assert saved["db_identity"] == prepared.db_identity.model_dump()
    assert set(p.name for p in (attempt / "robustness").iterdir()) == {
        ".lifecycle.lock",
        "prepared-attempt.json",
    }
