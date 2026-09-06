"""Stage2-owned preparation leaves. Never sends a batch or issues SMTP consent.

Bash holds the inherited lock and invokes prepare/cleanup/restore. Intent and
observed immutable IDs remain private after failure; no speculative ID cleanup.
"""

import os
from contextlib import contextmanager
from types import SimpleNamespace

from app.agent.release_artifacts import (
    EvidenceError,
    component_ref,
    parse_json,
    read_private,
    write_private,
    write_private_bytes,
)
from app.agent.release_capture import collect_preparation
from app.agent.release_context import docker_context
from app.agent.release_n8n_probe import CallbackObserver, read_smtp_snapshot
from app.agent.release_prepare import (
    CAPTURE,
    PREFLIGHT,
    RestoreContext,
    issue_prepared,
)
from app.agent.release_prepared import RuntimeImages
from app.agent.release_production import ProductionPorts
from app.agent.release_runtime import ComposeRuntime, RuntimeSnapshot
from app.agent.release_stage2_inputs import (
    n8n_settings,
    preflight_arguments,
    preparation_inputs,
    u10_inputs,
)
from app.agent.release_stage2_recovery import cleanup_e2e, e2e_inventory, restore_level2
from app.agent.u10_preflight_report import preflight_report


@contextmanager
def observation_client(recipients):
    values, ids, samples = n8n_settings()
    with CallbackObserver(
        values["N8N_BASE_URL"],
        values["N8N_USERNAME"],
        values["N8N_PASSWORD"],
        allow_temporary_workflow=True,
        allow_insecure_http=os.environ.get("CM52_ALLOW_INSECURE_N8N_HTTP") == "true",
    ) as observer:
        session = observer.session
        pins = {
            w: dict(
                workflow_id=identifier,
                version=session.workflow(identifier)["versionId"],
            )
            for w, identifier in ids.items()
        }

        def smtp():
            return read_smtp_snapshot(
                session=session,
                observer=observer,
                workflow_ids={w.lower(): identifier for w, identifier in ids.items()},
                recipients=recipients,
            ).model_dump()

        yield session, pins, samples, smtp


def adapter(*, repository, report_root, env_file, revision, images, attempt_id):
    return ComposeRuntime(
        compose_directory=repository / "deploy/compose",
        env_file=env_file,
        revision=revision,
        image_ids=images,
        report_root=report_root,
        uid=os.getuid(),
        gid=os.getgid(),
        action_policy="MOCK-NOTIFY-V1",
        trail_run_id=attempt_id,
    )


def _previous(ports, attempt):
    fault = ports.values.get("AGENT_FAULT_EVAL_ARTIFACT_PATH") or None
    golden = ports.values.get("AGENT_GOLDEN_FLOW_SUMMARY_PATH") or None
    if bool(fault) != bool(golden):
        raise EvidenceError("PREV_STATE_INVALID")
    if fault:
        import re

        match = re.fullmatch(
            r"/reports/cm-5\.2/([0-9]{8}T[0-9]{6}Z-[0-9a-f]{12})/fault-5class\.json",
            fault,
        )
        if match is None or golden != f"/reports/cm-5.2/{match[1]}/golden-flow.json":
            raise EvidenceError("PREV_STATE_INVALID")
        fref = component_ref(ports.report_root, fault.removeprefix("/reports/"))
        gref = component_ref(ports.report_root, golden.removeprefix("/reports/"))
        revision = parse_json(read_private(ports.report_root, fref.relative_path))[
            "code_revision"
        ]
    return RestoreContext(
        prev_state="bound" if fault else "empty",
        prev_fault_path=fault,
        prev_golden_path=golden,
        prev_fault_sha256=fref.sha256 if fault else None,
        prev_golden_sha256=gref.sha256 if fault else None,
        prev_rev=revision if fault else None,
        prev_attempt=match[1] if fault else None,
        running_rev=attempt.revision,
    )


def prepare(*, repository, report_root, env_file, attempt_id, lock_fd):
    from app.agent.release_lifecycle import verify_lifecycle_lock

    relative = f"cm-5.2/{attempt_id}/robustness"
    verify_lifecycle_lock(report_root, lock_fd, relative_directory=relative)
    attempt, images, reset, reset_id = preparation_inputs(
        repository=repository, report_root=report_root, attempt_id=attempt_id
    )
    n8n_settings()  # Missing credentials/explicit probe authorization: no team down.
    trail = repository / "deploy/compose/trail"
    trail.mkdir(mode=0o700, exist_ok=True)
    import stat

    info = trail.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
        or trail.resolve() != trail
    ):
        raise EvidenceError("MOCK_CALLBACK_TRAIL_UNAVAILABLE")
    a = report_root / "cm-5.2" / attempt_id
    if (a / "prepare-intent.json").exists() or (a / "stage2-log.jsonl").exists():
        raise EvidenceError("PREPARATION_ALREADY_STARTED")
    ports = ProductionPorts(
        repository=repository,
        env_file=env_file,
        report_root=report_root,
        artifact=None,
        published_root=a,
        revision=attempt.revision,
        attempt_id=attempt_id,
        image_ids={r: images[r] for r in ("backend", "frontend")},
        preflight_arguments=preflight_arguments(repository),
    )
    ports.validate_inputs(restore_only=True)
    if (
        ports.active_runs() != 0
        or ports.preflight(2).get("integrity") != "PASS"
        or e2e_inventory()
    ):
        raise EvidenceError("PREPARATION_LEVEL2_REQUIRED")
    previous = _previous(ports, attempt)
    running_adapter = adapter(
        repository=repository,
        report_root=report_root,
        env_file=env_file,
        revision=attempt.revision,
        images=images,
        attempt_id=attempt_id,
    )
    write_private(
        a,
        "prepare-intent.json",
        dict(
            attempt_id=attempt_id,
            R=attempt.revision,
            previous=previous.model_dump(),
            images={
                r: dict(image_id=i, label_revision=attempt.revision)
                for r, i in images.items()
            },
        ),
    )
    ports.command(ports.compose + ["down"], timeout=180)
    created = running_adapter.create()
    write_private(a, "prepare-created.json", created)
    running_adapter._command(
        running_adapter.compose
        + ["up", "-d", "--no-build", "--pull", "never", "--wait", "kafka", "mes-mock"],
        timeout=180,
    )
    running = running_adapter.start(created)
    write_private(a, "prepare-running.json", running)
    cid = running.containers["backend"].container_id
    recipients = docker_context(cid).recipients

    def trail_probe():
        raw = running_adapter._command(
            [
                "docker",
                "exec",
                cid,
                "python",
                "scripts/read_mock_release_sources.py",
                "trail-probe",
            ]
        )
        return parse_json(raw) == {"callback_trail_writable": True}

    with observation_client(recipients) as (api, workflows, samples, smtp):
        capture = collect_preparation(
            runtime_adapter=running_adapter,
            running=running,
            repository=repository,
            **u10_inputs(repository),
            previous=previous,
            api=api,
            workflow_id=workflows["WF2"]["workflow_id"],
            sample_execution_id=samples["WF2"],
            read_smtp_config=smtp,
            preflight_snapshot=a / "evidence/artifacts/PREFLIGHT/db-snapshot.json",
            mock_workflows={w: workflows[w] for w in ("WF3", "WF4")},
            mock_samples={w: samples[w] for w in ("WF3", "WF4")},
            read_trail_probe=trail_probe,
        )
    capture_ref = write_private(a, CAPTURE, capture)
    pf = capture.preflight
    preflight_ref = write_private(
        a,
        PREFLIGHT,
        preflight_report(
            pf,
            profile=pf.profile,
            phase=pf.phase,
            checked_at=pf.checked_at,
            failed_checks=[],
        ),
    )
    write_private_bytes(
        a,
        "stage2-log.jsonl",
        b'{"step":"3b","status":"PASS","detail":"pinned-runtime-preflight-probe"}\n',
    )
    return issue_prepared(
        report_root=report_root,
        mounted_report_root=report_root,
        repository=repository,
        attempt_id=attempt_id,
        revision=attempt.revision,
        image_ids=images,
        capture_sha256=capture_ref.sha256,
        preflight_sha256=preflight_ref.sha256,
        reset_receipt=reset,
        reset_run_id=reset_id,
        lifecycle_lock_fd=lock_fd,
    )


def failed_preparation_context(a):
    value = parse_json(read_private(a, "prepare-intent.json"))
    previous = RestoreContext.model_validate(value["previous"])
    images = RuntimeImages.model_validate(value["images"])
    observed = None
    for name in ("prepare-running.json", "prepare-created.json"):
        if (a / name).exists():
            observed = RuntimeSnapshot.model_validate(parse_json(read_private(a, name)))
            if observed.images != images or observed.revision != value["R"]:
                raise EvidenceError("PREPARATION_RUNTIME_BINDING_INVALID")
            break
    # A failed/ambiguous create with no complete observed IDs cannot authorize
    # speculative removal. Preserve intent; report cleanup failure for the owner.
    return SimpleNamespace(
        **previous.model_dump(),
        images=images,
        R=value["R"],
        attempt_id=value["attempt_id"],
        containers=SimpleNamespace(**observed.containers) if observed else None,
    )


def cleanup_prepare(a):
    context = failed_preparation_context(a)
    if context.containers is None and e2e_inventory():
        raise EvidenceError("PREPARATION_CLEANUP_PINS_UNAVAILABLE")
    result = cleanup_e2e(context)
    from app.agent.release_retention import verify_restored

    verify_restored(a)
    return result


def restore_prepare(*, a, repository, report_root, env_file):
    return restore_level2(
        repository=repository,
        report_root=report_root,
        env_file=env_file,
        prepared=failed_preparation_context(a),
        preflight_arguments=preflight_arguments(repository),
    )
