"""Actual Stage2 workload/evidence leaves under the Bash owner's claim.

Only the fixed pending batch command sends actions. All collection is read-only;
the separate SMTP grant and live drift checks are consumed by begin_phase first.
"""

import os
import re
import subprocess

from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    component_parent,
    component_ref,
    parse_json,
    read_private,
    write_private_bytes,
)
from app.agent.release_collection import collect_round
from app.agent.release_prepare import parse_capture
from app.agent.release_prepared import parse_prepared
from app.agent.release_resume import observe_resume
from app.agent.release_stage2_prepare import adapter, observation_client


def current_runtime(*, repository, report_root, env_file, attempt_id):
    a = report_root / "cm-5.2" / attempt_id
    prepared = parse_prepared(
        parse_json(read_private(a / "robustness", "prepared-attempt.json"))
    )
    capture = parse_capture(parse_json(read_private(a, "preparation-capture.json")))
    if (
        prepared.schema_version != "level3-prepared-attempt-v2"
        or capture.runtime.revision != prepared.R
        or prepared.attempt_id != attempt_id
        or capture.runtime.images != prepared.images
        or capture.runtime.prepared_containers() != prepared.containers
    ):
        raise EvidenceError("PREPARED_RUNTIME_DRIFT")
    runtime = adapter(
        repository=repository,
        report_root=report_root,
        env_file=env_file,
        revision=prepared.R,
        images={
            r: getattr(prepared.images, r).image_id
            for r in ("backend", "frontend", "runner")
        },
        attempt_id=attempt_id,
    )
    return a, prepared, capture.runtime, runtime


def live_binding(**args):
    a, prepared, running, runtime = current_runtime(**args)
    with observation_client(prepared.recipient.canonical_addresses) as (_, _, _, smtp):
        return observe_resume(
            runtime_adapter=runtime,
            running=running,
            repository=args["repository"],
            read_smtp_config=smtp,
        )


def source(runtime, running, operation, *arguments):
    runtime.verify_running(running)
    cid = running.containers["backend"].container_id
    raw = runtime._command(
        [
            "docker",
            "exec",
            cid,
            "python",
            "scripts/read_mock_release_sources.py",
            operation,
            *arguments,
        ],
        timeout=90,
    )
    return parse_json(raw)


def capture_runner(runtime, running, *, root, name, arguments, timeout=3600):
    """O_EXCL stdout survives timeout/nonzero exit; never run again in this claim."""
    runtime.verify_running(running)
    cid = running.containers["runner"].container_id
    argv = [
        "docker",
        "exec",
        "--user",
        runtime.user,
        "--workdir",
        "/workspace/backend",
        cid,
        "python",
        *arguments,
    ]
    with component_parent(root, name) as (directory, leaf):
        fd = os.open(
            leaf,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            try:
                result = subprocess.run(
                    argv,
                    env=dict(runtime.env),
                    stdout=fd,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    check=False,
                )
                if result.returncode or os.fstat(fd).st_size > 4 * 1024 * 1024:
                    raise EvidenceError("STAGE2_COMMAND_FAILED")
            except (OSError, subprocess.TimeoutExpired):
                raise EvidenceError("STAGE2_COMMAND_FAILED") from None
        finally:
            os.fsync(fd)
            os.close(fd)
    runtime.verify_running(running)
    return component_ref(root, name)


def snapshot(runtime, running, a, phase):
    parent = a / "evidence/artifacts" / phase
    parent.mkdir(mode=0o700, exist_ok=True)
    filename = "db.json" if phase == "NO_DECISIONS" else "db-snapshot.json"
    output = f"/reports/cm-5.2/{a.name}/evidence/artifacts/{phase}/{filename}"
    runtime.exec_runner(
        running,
        [
            "python",
            "scripts/capture_mock_golden_snapshot.py",
            "--phase",
            phase,
            "--output",
            output,
        ],
        timeout=90,
    )


def execute_workload(**args):
    a, prepared, running, runtime = current_runtime(**args)
    ids = os.environ.get("CM52_ANALYTICS_QUERY_IDS", "")
    if re.fullmatch(r"[0-9]+,[0-9]+,[0-9]+", ids) is None:
        raise EvidenceError("ANALYTICS_QUERY_IDS_REQUIRED")
    runtime.exec_runner(
        running,
        [
            "python",
            "scripts/e2e_analytics_questions.py",
            "--ids",
            ids,
            "--output",
            f"/reports/cm-5.2/{a.name}/analytics-digests.json",
        ],
        timeout=180,
    )
    before = source(runtime, running, "offsets")
    expected = parse_json(read_private(a, "evidence/artifacts/PREFLIGHT/kafka.json"))
    if before != expected:
        raise EvidenceError("GOLDEN_MOCK_BEFORE_OFFSETS_MISMATCH")
    raw = runtime.exec_runner(
        running,
        ["python", "scripts/run_pending_incidents.py", "--database", "kosa_agent_e2e"],
    )
    from app.agent.diagnostics import CANONICAL_INCIDENT_KEYS
    from scripts.verify_golden_flow import _validate_plan

    plan = parse_json(raw)
    _validate_plan(plan)
    if (
        len(plan["selected"]) != 12
        or {(r["lot_id"], r["chamber_id"]) for r in plan["selected"]}
        != CANONICAL_INCIDENT_KEYS
        or plan["rejected"]
        or plan["incomplete"]
    ):
        raise EvidenceError("STAGE2_PENDING_POPULATION_INVALID")
    write_private_bytes(a, "pending-plan.json", raw)
    # Existing output (including partial output) prevents a second invocation.
    capture_runner(
        runtime,
        running,
        root=a,
        name="pending-run.jsonl",
        arguments=[
            "scripts/run_pending_incidents.py",
            "--database",
            "kosa_agent_e2e",
            "--once",
        ],
    )
    final = parse_json(read_private(a, "pending-run.jsonl").splitlines()[-1])
    if any(
        type(final.get(k)) is not int or final[k] != v
        for k, v in dict(
            attempted=12, succeeded=12, failed=0, skipped=0, new_runs_observed=12
        ).items()
    ):
        raise EvidenceError("STAGE2_BATCH_POSTCONDITION_FAILED")
    snapshot(runtime, running, a, "BATCH_BASELINE")
    return dict(status="BATCH_CAPTURED", run_count=12)


def collect_workload(*, resume_at, **args):
    a, prepared, running, runtime = current_runtime(**args)
    before = parse_json(read_private(a, "evidence/artifacts/PREFLIGHT/kafka.json"))
    with observation_client(prepared.recipient.canonical_addresses) as (
        api,
        workflows,
        _,
        smtp,
    ):
        ref = collect_round(
            root=a / "robustness",
            attempt_root=a,
            prepared=prepared,
            resume_at=resume_at,
            kafka_before=before,
            read_database=lambda: source(runtime, running, "database"),
            read_runs=lambda: parse_json(
                runtime.exec_runner(
                    running, ["python", "scripts/read_release_runs.py"], timeout=120
                )
            ),
            read_offsets=lambda: source(runtime, running, "offsets"),
            read_records=lambda first, last: source(
                runtime,
                running,
                "records",
                "--before",
                canonical_json(first).decode(),
                "--after",
                canonical_json(last).decode(),
            ),
            read_trail=lambda start, end: source(
                runtime, running, "trail", "--started-at", start, "--frozen-at", end
            ),
            api=api,
            workflows=workflows,
            read_smtp_config=smtp,
            capture_mock_snapshot=lambda: snapshot(runtime, running, a, "MOCK_RESULTS"),
        )
    runtime.exec_runner(
        running,
        [
            "python",
            "scripts/emit_diagnostic_targets.py",
            "--agent-database",
            "kosa_agent_e2e",
            "--attempt-id",
            a.name,
            "--output",
            f"/reports/cm-5.2/{a.name}/diagnostic-targets.json",
        ],
        timeout=120,
    )
    return dict(status="ROUND_FROZEN", round1=ref.model_dump())
