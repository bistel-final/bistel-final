"""PUBLISH leaves: historical evidence verification plus one empty second batch.

No missing baseline is recaptured. The backend-only artifact readback recreation
is recorded separately; it cannot mutate the frozen first-round container pins.
"""

from app.agent.release_artifacts import (
    EvidenceError,
    component_ref,
    digest,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_golden import GoldenEvidenceV2, verify_mock_golden
from app.agent.release_mock import instant
from app.agent.release_prepared import parse_prepared
from app.agent.release_round import verify_round
from app.agent.release_runtime import RuntimeSnapshot
from app.agent.release_stage2_workload import (
    capture_runner,
    current_runtime,
    snapshot,
    source,
)


def precondition(**args):
    a, prepared, running, runtime = current_runtime(**args)
    runtime.verify_running(running)
    verify_round(a / "robustness", component_ref(a / "robustness", "round1.json"))
    for name in (
        "BATCH_BASELINE/db-snapshot.json",
        "MOCK_RESULTS/db-snapshot.json",
        "NO_DECISIONS/db.json",
    ):
        read_private(a, "evidence/artifacts/" + name)
    # Neither TTL nor a new SMTP consent applies to historical publication.
    return True


def publish_evidence(**args):
    a, prepared, running, runtime = current_runtime(**args)
    precondition(**args)
    for name in (
        "evidence/mock-notify-evidence.json",
        "golden-flow.json",
        "fault-5class.json",
        "publish-recreate-intent.json",
    ):
        if (a / name).exists():
            raise EvidenceError("PUBLISH_ALREADY_STARTED")
    # Read the empty plan before the only publish batch invocation. Do not let
    # unexpected pending work turn a verification step into another SMTP run.
    raw = runtime.exec_runner(
        running,
        ["python", "scripts/run_pending_incidents.py", "--database", "kosa_agent_e2e"],
    )
    plan = parse_json(raw)
    from scripts.verify_golden_flow import _validate_plan

    _validate_plan(plan)
    if plan["selected"] or plan["rejected"] or plan["incomplete"]:
        raise EvidenceError("PUBLISH_PENDING_WORK_DETECTED")
    second = capture_runner(
        runtime,
        running,
        root=a,
        name="evidence/second-batch.jsonl",
        arguments=[
            "scripts/run_pending_incidents.py",
            "--database",
            "kosa_agent_e2e",
            "--once",
        ],
        timeout=120,
    )
    snapshot(runtime, running, a, "SECOND_BATCH")
    phases = {
        p: component_ref(
            a,
            "evidence/artifacts/"
            + p
            + ("/db.json" if p == "NO_DECISIONS" else "/db-snapshot.json"),
        )
        for p in (
            "PREFLIGHT",
            "BATCH_BASELINE",
            "MOCK_RESULTS",
            "NO_DECISIONS",
            "SECOND_BATCH",
        )
    }
    manifest = GoldenEvidenceV2(
        schema_version="mock-notify-golden-evidence-v1",
        protocol="MOCK-NOTIFY-V1",
        attempt_id=a.name,
        R=prepared.R,
        source_manifest_sha256=digest(
            (
                args["repository"] / "infra/bootstrap/source-manifest-v4.json"
            ).read_bytes()
        ),
        round1_sha256=component_ref(a / "robustness", "round1.json").sha256,
        preflight_pending=component_ref(a, "evidence/artifacts/PREFLIGHT/pending.json"),
        preflight_offsets=component_ref(a, "evidence/artifacts/PREFLIGHT/kafka.json"),
        snapshots=phases,
        second_batch=second,
        isolated={
            p: dict(
                test_ref="tests/unit/test_agent_mock_notify.py", revision=prepared.R
            )
            for p in ("UNKNOWN", "MANUAL_RETRY")
        },
    )
    ref = write_private(a, "evidence/mock-notify-evidence.json", manifest)
    summary, _, _ = verify_mock_golden(
        root=a, evidence=ref, expected_attempt_id=a.name, expected_revision=prepared.R
    )
    golden = write_private(a, "golden-flow.json", summary)
    ca = f"/reports/cm-5.2/{a.name}"
    runtime.exec_runner(
        running,
        [
            "python",
            "scripts/evaluate_fault_5class.py",
            "--agent-database",
            "kosa_agent_e2e",
            "--golden-evidence",
            ca + "/evidence/mock-notify-evidence.json",
            "--output",
            ca + "/fault-5class.json",
        ],
        timeout=300,
    )
    fault = component_ref(a, "fault-5class.json")
    fault_value = parse_json(read_private(a, "fault-5class.json"))
    if (
        fault_value.get("hard_gate_passed") is not True
        or fault_value.get("code_revision") != prepared.R
        or fault_value.get("policy_version") != "MOCK-NOTIFY-V1"
    ):
        raise EvidenceError("PUBLISH_FAULT_EVALUATION_FAILED")
    frozen = parse_json(read_private(a / "robustness", "mock-sources.round1.json"))
    from datetime import UTC, datetime

    # Record the exact allowed transition before the sole backend recreation.
    write_private(
        a,
        "publish-recreate-intent.json",
        dict(
            original_runtime_sha256=component_ref(a, "prepare-running.json").sha256,
            fault_sha256=fault.sha256,
            golden_sha256=golden.sha256,
            attempt_id=a.name,
            R=prepared.R,
        ),
    )
    runtime.env.update(
        CM52_PUBLISHED_FAULT=ca + "/fault-5class.json",
        CM52_PUBLISHED_GOLDEN=ca + "/golden-flow.json",
        CM52_PUBLISHED_TRAIL_ID=a.name + "_published",
    )
    runtime._command(
        runtime.compose
        + [
            "-f",
            str(args["repository"] / "deploy/compose/docker-compose.e2e-artifacts.yml"),
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--force-recreate",
            "--no-deps",
            "--wait",
            "backend",
        ],
        timeout=180,
    )
    new = runtime._stable(runtime._ids(), "running")
    if (
        any(new.containers[r] != running.containers[r] for r in ("frontend", "runner"))
        or new.images != running.images
    ):
        raise EvidenceError("PUBLISH_RUNTIME_DRIFT")
    write_private(a, "publish-running.json", new)
    runtime._command(
        [
            "docker",
            "exec",
            new.containers["backend"].container_id,
            "python",
            "scripts/preflight_agent_evaluation_artifacts.py",
            "--fault",
            ca + "/fault-5class.json",
            "--golden",
            ca + "/golden-flow.json",
            "--expect-fault-sha",
            fault.sha256,
            "--expect-golden-sha",
            golden.sha256,
            "--expect-revision",
            prepared.R,
            "--attempt-id",
            a.name,
        ],
        timeout=90,
    )
    from app.agent.release_stage2_recovery import verify_evaluation_api

    verify_evaluation_api("bound")
    # Existing public DB observer verifies the same baseline and exact three UI queries.
    from app.agent.release_stage2_recovery import docker_command

    docker_command(
        [
            str(args["repository"] / ".venv/bin/python"),
            str(args["repository"] / "backend/scripts/observe_public_databases.py"),
            "verify",
            "--baseline",
            str(a / "observer-baseline.json"),
            "--expected-digests",
            str(a / "analytics-digests.json"),
            "--output",
            str(a / "observer-final.json"),
        ],
        timeout=180,
    )
    docker_command(
        [
            str(args["repository"] / ".venv/bin/python"),
            str(args["repository"] / "deploy/compose/scan_cm52_artifacts.py"),
            "--root",
            str(a),
            "--env-file",
            str(args["env_file"]),
            "--publications-only",
        ],
        timeout=90,
    )
    # Read the original trail AFTER its writer has been replaced; also count
    # callbacks to the artifact-readback backend, with an explicit cutoff.
    cutoff = datetime.now(UTC).isoformat()
    trail = []
    for extra in (("--historical-trail-run-id", a.name), ()):
        trail.extend(
            source(
                runtime,
                new,
                "trail",
                "--started-at",
                frozen["window"]["started_at"],
                "--frozen-at",
                cutoff,
                *extra,
            )
        )
    late = sum(
        r["channel"] == "MES_MOCK"
        and instant(r["ts"]) > instant(frozen["window"]["frozen_at"])
        for r in trail
    )
    write_private(
        a,
        "post-freeze-callbacks.json",
        dict(
            count=late,
            observed_through=cutoff,
            original_run_id=a.name,
            published_run_id=a.name + "_published",
        ),
    )
    return dict(status="PUBLICATIONS_VERIFIED", post_freeze_callbacks=late)


def cleanup_context(a, *, allow_published):
    prepared = parse_prepared(
        parse_json(read_private(a / "robustness", "prepared-attempt.json"))
    )
    if not allow_published or not (a / "publish-recreate-intent.json").exists():
        return prepared
    # An ambiguous recreation without a completed observation is not permission
    # to delete whatever a mutable service name currently resolves to.
    intent = parse_json(read_private(a, "publish-recreate-intent.json"))
    original = RuntimeSnapshot.model_validate(
        parse_json(read_private(a, "prepare-running.json"))
    )
    new = RuntimeSnapshot.model_validate(
        parse_json(read_private(a, "publish-running.json"))
    )
    if (
        intent["original_runtime_sha256"]
        != component_ref(a, "prepare-running.json").sha256
        or (intent["attempt_id"], intent["R"]) != (a.name, prepared.R)
        or original.prepared_containers() != prepared.containers
        or original.revision != prepared.R
        or new.revision != prepared.R
        or set(new.containers) != {"backend", "frontend", "runner"}
        or new.images != prepared.images
        or any(
            new.containers[r] != original.containers[r] for r in ("frontend", "runner")
        )
    ):
        raise EvidenceError("PUBLISH_RUNTIME_DRIFT")
    return prepared.model_copy(update={"containers": new.prepared_containers()})


def restore_context(a, *, published):
    prepared = cleanup_context(a, allow_published=True)
    if not published:
        return prepared
    # This marker is written only after all actual publication checks return.
    marker = parse_json(read_private(a, "publish-result.json"))
    bindings = {
        name: component_ref(a, name).sha256
        for name in (
            "attempt.json",
            "golden-flow.json",
            "fault-5class.json",
        )
    }
    if (
        marker.get("status") != "PUBLICATIONS_VERIFIED"
        or marker.get("attempt_id") != prepared.attempt_id
        or marker.get("R") != prepared.R
        or marker.get("publications") != bindings
    ):
        raise EvidenceError("PUBLISH_RESULT_BINDING_INVALID")
    ca = f"/reports/cm-5.2/{a.name}"
    return prepared.model_copy(
        update=dict(
            prev_state="bound",
            prev_fault_path=ca + "/fault-5class.json",
            prev_golden_path=ca + "/golden-flow.json",
            prev_fault_sha256=bindings["fault-5class.json"],
            prev_golden_sha256=bindings["golden-flow.json"],
            prev_rev=prepared.R,
            prev_attempt=a.name,
        )
    )
