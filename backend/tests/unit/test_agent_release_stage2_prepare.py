"""Preparation owner wiring, real v2 prepared writer, synthetic service ports."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.agent import release_stage2_prepare as m
from app.agent.release_artifacts import (
    EvidenceError,
    component_ref,
    read_private,
    write_private,
)
from app.agent.release_lifecycle import classify_state, lifecycle_lock, read_lifecycle
from app.agent.release_prepare import (
    PreparationCaptureV2,
    RestoreContext,
    issue_prepared,
)
from tests.unit.test_agent_release_prepare import NOW, bundle  # noqa: F401


def test_observation_client_preserves_declared_retention_source(monkeypatch):
    class Session:
        def workflow(self, identifier):
            value = {"versionId": "version-" + identifier}
            if identifier in {"wf3", "wf4"}:
                value["_evidence_retention_source"] = {
                    "declared": {
                        "saveDataSuccessExecution": "DEFAULT",
                        "saveDataErrorExecution": None,
                    }
                }
            return value

    class Observer:
        session = Session()

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(m, "CallbackObserver", Observer)
    monkeypatch.setattr(
        m,
        "n8n_settings",
        lambda: (
            {
                "N8N_BASE_URL": "https://example.invalid",
                "N8N_USERNAME": "user",
                "N8N_PASSWORD": "synthetic",
            },
            {"WF2": "wf2", "WF3": "wf3", "WF4": "wf4"},
            {"WF2": "2", "WF3": "3", "WF4": "4"},
        ),
    )
    with m.observation_client(["team@example.invalid"]) as (
        _,
        pins,
        samples,
        _,
        original,
    ):
        assert pins["WF3"] == {"workflow_id": "wf3", "version": "version-wf3"}
        assert samples == {"WF2": "2", "WF3": "3", "WF4": "4"}
        assert original == {
            workflow: {
                "saveDataSuccessExecution": "DEFAULT",
                "saveDataErrorExecution": None,
            }
            for workflow in ("WF3", "WF4")
        }


@pytest.fixture
def prepared_ports(bundle, monkeypatch):  # noqa: F811
    args, a, root, value = bundle
    repository = args["repository"]
    (repository / "deploy/compose").mkdir(parents=True)
    for name in (
        "preparation-capture.json",
        "e2e-level3-preflight.json",
        "stage2-log.jsonl",
    ):
        (a / name).unlink()
    for name in ("evidence", "evidence/artifacts", "evidence/artifacts/PREFLIGHT"):
        (a / name).mkdir(mode=0o700)
    pref = write_private(
        a,
        "evidence/artifacts/PREFLIGHT/db-snapshot.json",
        {
            k: []
            for k in (
                "runs",
                "actions",
                "deliveries",
                "approvals",
                "tools",
                "audits",
                "r03_incidents",
            )
        },
    )
    value["schema_version"] = "level3-preparation-capture-v2"
    value["preflight_snapshot_sha256"] = pref.sha256
    value["n8n_original_retention"] = {
        workflow: {
            "saveDataSuccessExecution": "DEFAULT",
            "saveDataErrorExecution": "DEFAULT",
        }
        for workflow in ("WF3", "WF4")
    }
    value["n8n_evidence_probe"].update(
        wf3_execution_detail_retained=True,
        wf4_execution_detail_retained=True,
        callback_trail_writable=True,
    )
    for rb in value["preflight"]["deployment"]["runtime"]["readbacks"].values():
        rb.update(
            schema_version="agent-runtime-readback-v2", action_policy="MOCK-NOTIFY-V1"
        )
    capture = PreparationCaptureV2.model_validate(value)
    events = []

    class Ports:
        compose = ["docker", "compose", "team"]
        values = {}

        def __init__(self, **kwargs):
            pass

        def validate_inputs(self, **kwargs):
            events.append("validate")

        def active_runs(self):
            events.append("active")
            return 0

        def preflight(self, level):
            events.append("level2")
            return dict(integrity="PASS")

        def command(self, command, **kwargs):
            events.append(command[-1])

    class Runtime:
        compose = ["docker", "compose", "e2e"]

        def create(self):
            events.append("create")
            return capture.runtime

        def start(self, created):
            events.append("start")
            return capture.runtime

        def _command(self, argv, **kwargs):
            events.append("infra" if argv[-1] == "mes-mock" else "trail-probe")
            return b'{"callback_trail_writable":true}'

    @contextmanager
    def observer(recipients):
        events.append("observer")
        yield (
            None,
            {w: dict(workflow_id=w, version="v") for w in ("WF2", "WF3", "WF4")},
            dict(WF2="2", WF3="3", WF4="4"),
            lambda: {},
            value["n8n_original_retention"],
        )

    def collect(**kwargs):
        assert (
            kwargs["preflight_snapshot"]
            == a / "evidence/artifacts/PREFLIGHT/db-snapshot.json"
        )
        assert kwargs["read_trail_probe"]() is True
        events.append("capture")
        return capture

    monkeypatch.setattr(m, "ProductionPorts", Ports)
    monkeypatch.setattr(m, "adapter", lambda **kwargs: Runtime())
    monkeypatch.setattr(m, "e2e_inventory", lambda: [])
    monkeypatch.setattr(m, "n8n_settings", lambda: None)
    monkeypatch.setattr(m, "preflight_arguments", lambda _: ["synthetic"])
    monkeypatch.setattr(m, "u10_inputs", lambda _: {})
    monkeypatch.setattr(
        m,
        "preparation_inputs",
        lambda **kwargs: (
            SimpleNamespace(revision=args["revision"]),
            args["image_ids"],
            args["reset_receipt"],
            args["reset_run_id"],
        ),
    )
    monkeypatch.setattr(
        m, "_previous", lambda *args: RestoreContext.model_validate(value["previous"])
    )
    monkeypatch.setattr(
        m,
        "docker_context",
        lambda _: SimpleNamespace(recipients=["Team@example.invalid"]),
    )
    monkeypatch.setattr(m, "observation_client", observer)
    monkeypatch.setattr(m, "collect_preparation", collect)
    monkeypatch.setattr(
        m,
        "issue_prepared",
        lambda **kwargs: issue_prepared(**kwargs, clock=lambda: NOW),
    )
    call = dict(
        repository=repository,
        report_root=args["report_root"],
        env_file=repository / ".env.team",
        attempt_id=args["attempt_id"],
    )
    return call, a, root, events


def test_prepare_creates_pins_but_no_batch_or_grant(prepared_ports):
    args, a, root, events = prepared_ports
    with lifecycle_lock(root) as fd:
        result = m.prepare(**args, lock_fd=fd)
    assert result["status"] == "PREPARED"
    assert classify_state(read_lifecycle(root)) == "PREPARED"
    assert events == [
        "validate",
        "active",
        "level2",
        "down",
        "create",
        "infra",
        "start",
        "observer",
        "trail-probe",
        "capture",
    ]
    assert not (root / "smtp-approval-grant.json").exists()
    assert not list(root.glob("lifecycle-claim.*"))
    assert b"level3-prepared-attempt-v2" in read_private(root, "prepared-attempt.json")


def test_prepare_failure_preserves_exact_cleanup_pins(prepared_ports, monkeypatch):
    args, a, root, events = prepared_ports

    def fail(**kw):
        raise EvidenceError("N8N_EXECUTION_RETENTION_REQUIRED")

    monkeypatch.setattr(m, "collect_preparation", fail)
    with lifecycle_lock(root) as fd:
        with pytest.raises(EvidenceError, match="N8N_EXECUTION_RETENTION_REQUIRED"):
            m.prepare(**args, lock_fd=fd)
    context = m.failed_preparation_context(a)
    assert context.containers.backend.container_id
    assert "start" in events
    assert not (root / "prepared-attempt.json").exists()
    assert component_ref(a, "prepare-intent.json")


def test_missing_n8n_settings_rejected_before_any_service_change(
    prepared_ports, monkeypatch
):
    args, a, root, events = prepared_ports

    def fail():
        raise EvidenceError("N8N_OPERATOR_ENV_REQUIRED")

    monkeypatch.setattr(m, "n8n_settings", fail)
    with lifecycle_lock(root) as fd:
        with pytest.raises(EvidenceError, match="^N8N_OPERATOR_ENV_REQUIRED$"):
            m.prepare(**args, lock_fd=fd)
    assert events == []
    assert not (a / "prepare-intent.json").exists()
    assert not (root / "prepared-attempt.json").exists()


def test_failed_prepare_cleanup_reports_retention_without_becoming_cleanup_failure(
    tmp_path, monkeypatch
):
    from app.agent import release_retention

    context = SimpleNamespace(containers=SimpleNamespace())
    monkeypatch.setattr(m, "failed_preparation_context", lambda _: context)
    monkeypatch.setattr(m, "e2e_inventory", lambda: [])
    monkeypatch.setattr(
        m,
        "cleanup_e2e",
        lambda _: {"result": "OK", "basis": "synthetic-scoped-cleanup"},
    )

    def fail(_):
        raise EvidenceError("N8N_RETENTION_RESTORE_REQUIRED")

    monkeypatch.setattr(release_retention, "verify_restored", fail)
    result = m.cleanup_prepare(tmp_path)
    assert result["result"] == "OK"
    assert result["retention_restore"] == {
        "status": "NOT_VERIFIED",
        "reason_code": "N8N_RETENTION_RESTORE_REQUIRED",
        "observation": None,
    }
    assert read_private(tmp_path, "prepare-cleanup.json")
